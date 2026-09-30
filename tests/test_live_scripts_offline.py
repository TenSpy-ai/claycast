"""The read-only live scripts under tests/live/, checked offline: every one byte-compiles, and
the blank-semantics probe's DRY RUN (its default — no --live) prints the whole probe set with no
cookie, no network and no ClayClient (it exits before constructing one). The probe has to send
two deliberately INVALID time nodes to Clay — timeUnit "year" and no timeUnit — which af_field()
refuses to build since the timeUnit contract landed; they are raw dict copies of a valid node,
and a regression there used to crash the probe before it printed anything."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

LIVE = Path(__file__).resolve().parent / "live"
PY = sys.executable
PROBE = str(LIVE / "blank_semantics_probe.py")


def _env_without_cookie():
    """No CLAY_SESSION in the child: the dry run must not need one."""
    return {k: v for k, v in os.environ.items() if k != "CLAY_SESSION"}


@pytest.mark.parametrize("script", sorted(p.name for p in LIVE.glob("*.py")))
def test_live_scripts_byte_compile(script):
    r = subprocess.run([PY, "-m", "py_compile", str(LIVE / script)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr


def test_blank_semantics_probe_dry_run_prints_every_probe_without_a_cookie():
    r = subprocess.run(
        [PY, PROBE, "--entity", "ACCOUNT", "--text-field", "audf_xxx", "--bool-field", "audf_yyy",
         "--date-field", "audf_zzz", "--value", "Retail", "--workspace", "12345"],
        capture_output=True, text=True, timeout=60, env=_env_without_cookie(),
    )
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["dry_run"] is True and out["impossible_value"].startswith("__claycast_probe_")
    probes = out["probes"]
    for name in ("T", "E", "NE", "NEQX", "PIN_NEQ", "COMP_NEQ", "EQE", "TB", "FB", "EB", "PIN_F",
                 "WL_DAY", "WL_YEAR", "WL_NOUNIT"):
        assert name in probes, name
    assert probes["T"] is None                                   # the whole entity
    assert probes["E"]["operator"] == "Empty" and probes["E"]["key"] == "audf_xxx"
    assert probes["FB"]["operator"] == "False" and probes["FB"]["key"] == "audf_yyy"
    assert probes["WL_DAY"]["timeUnit"] == "day" and probes["WL_DAY"]["value"] == 30
    assert probes["WL_YEAR"]["timeUnit"] == "year" and probes["WL_YEAR"]["value"] == 1
    assert "timeUnit" not in probes["WL_NOUNIT"] and probes["WL_NOUNIT"]["operator"] == "WithinLast"
    assert probes["WL_NOUNIT"]["value"] == 30 and probes["WL_NOUNIT"]["key"] == "audf_zzz"
    assert probes["WL_DAY"]["timeUnit"] == "day"                 # the copies did not touch the valid node
    assert "A3_EQV_ON_BLANK" in probes and probes["CASE_EQ_UPPER"]["value"] == "RETAIL"   # --value adds the A2/A3 checks


def test_blank_semantics_probe_requires_a_workspace():
    r = subprocess.run([PY, PROBE, "--text-field", "audf_xxx", "--bool-field", "audf_yyy"],
                       capture_output=True, text=True, timeout=60, env=_env_without_cookie())
    assert r.returncode == 2 and "--workspace" in r.stderr
