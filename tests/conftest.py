"""Shared fixtures for the offline suite.

`SCRIPTS` is the repo's scripts/ directory, resolved relative to this file, and clay_client is
loaded from there. Clients are built without __init__ (which would hit /me) and wired to a
FakeSession that records every HTTP call and answers from a route table — no cookie, no
network, no credits.
"""
import importlib.util
import json
import os
import re
import sys
import uuid
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load_module(name, scripts_dir, filename="clay_client.py"):
    scripts_dir = str(scripts_dir)
    spec = importlib.util.spec_from_file_location(name, os.path.join(scripts_dir, filename))
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, scripts_dir)
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(scripts_dir)
    return mod


cc = load_module("clay_client_under_test", SCRIPTS)


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        return json.loads(json.dumps(self._payload))  # deep copy, JSON-safe


class FakeSession:
    """Records (method, path, params, json) and answers from routes: list of
    (METHOD, path_regex, payload_or_callable)."""

    def __init__(self, routes=None):
        self.routes = list(routes or [])
        self.calls = []

    def _do(self, method, url, params=None, json=None, **_):
        path = url.split("/v3", 1)[1] if "/v3" in url else url
        self.calls.append({"method": method, "path": path, "params": params, "json": json})
        for m, rx, payload in self.routes:
            if m == method and re.fullmatch(rx, path):
                body = payload(self.calls[-1]) if callable(payload) else payload
                return FakeResponse(body)
        raise AssertionError(f"unexpected {method} {path}")

    def get(self, url, **kw):
        return self._do("GET", url, **kw)

    def post(self, url, **kw):
        return self._do("POST", url, **kw)

    def patch(self, url, **kw):
        return self._do("PATCH", url, **kw)

    def put(self, url, **kw):
        return self._do("PUT", url, **kw)

    def delete(self, url, **kw):
        return self._do("DELETE", url, **kw)


def make_client(module, routes=None, ws=12345):
    c = module.ClayClient.__new__(module.ClayClient)
    c.session = FakeSession(routes)
    c.workspace_id = ws
    c.user_id = 1
    return c


@pytest.fixture
def client():
    return lambda routes=None: make_client(cc, routes)


def all_nodes(ast):
    yield ast
    for child in ast.get("items", []) or []:
        yield from all_nodes(child)
    if isinstance(ast.get("condition"), dict):
        yield from all_nodes(ast["condition"])


def is_uuid(s):
    try:
        uuid.UUID(str(s))
        return True
    except ValueError:
        return False
