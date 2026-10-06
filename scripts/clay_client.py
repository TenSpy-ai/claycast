"""
Clay Internal API Client
Authenticates using your existing Chrome session — no login required.

Usage:
    from clay_client import ClayClient
    clay = ClayClient()
    tables = clay.list_tables()
    clay.create_column(table_id, {...})
"""

from __future__ import annotations

import copy
import csv
import datetime
import io
import json
import math
import os
import random
import re
import string
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

BASE = "https://api.clay.com/v3"
_RECORD_ID_CHARS = string.ascii_letters + string.digits
_VALID_FIELD_TYPES = {
    "text", "number", "boolean", "url", "date", "email", "image",
    "select", "checkbox", "formula", "enrichment", "waterfall", "claygent",
    "multiselect", "phone", "currency", "percent", "action", "source",
}
_SYSTEM_FIELD_IDS = {
    "f_created_at", "f_updated_at", "f_created_by", "f_auto_increment_id",
    "f_status", "f_is_archived",
}
_RUNNABLE_FIELD_TYPES = {"action", "enrichment", "source", "waterfall", "claygent"}
_PRESET_INPUT_RE = re.compile(r"\{\{Input_(\d+)\}\}")
_CPJ_TYPE_SETTINGS = {
    "people": {
        "name": "Find people",
        "iconType": "PersonWithMagnifyingGlass",
        "actionKey": "find-lists-of-people-with-mixrank-source",
        "actionPackageId": "e251a70e-46d7-4f3a-b3ef-a211ad3d8bd2",
        "previewActionKey": "find-lists-of-people-with-mixrank-source-preview",
        "previewTextPath": "name",
        "defaultPreviewText": "Clay Profile",
        "recordsPath": "people",
        "idPath": "profile_id",
        "scheduleConfig": {"runSettings": "once"},
        "dedupeOnUniqueIds": True,
        "hasEvaluatedInputs": False,
    },
    "companies": {
        "name": "Find companies",
        "iconType": "BuildingWithMagnifyingGlass",
        "actionKey": "find-lists-of-companies-with-mixrank-source",
        "actionPackageId": "e251a70e-46d7-4f3a-b3ef-a211ad3d8bd2",
        "previewActionKey": "find-lists-of-companies-with-mixrank-source-preview",
        "previewTextPath": "name",
        "defaultPreviewText": "Profile",
        "recordsPath": "companies",
        "idPath": "linkedin_company_id",
        "scheduleConfig": {"runSettings": "once"},
        "hasEvaluatedInputs": False,
    },
}
_CPJ_COMPANY_SIZE_OPTIONS_LEGACY = [
    {"id": "7b919ff8-7ef0-4941-9fed-f9097df7613b", "text": "Self-employed", "color": "yellow"},
    {"id": "f90357fa-92c4-434a-8c2d-485a339bed7b", "text": "2-10 employees", "color": "blue"},
    {"id": "77b51387-d5e4-40ad-9297-16a92c326849", "text": "11-50 employees", "color": "green"},
    {"id": "52ef7a55-750c-407a-8c40-11602ea56267", "text": "51-200 employees", "color": "red"},
    {"id": "7ceb4eb1-dd8c-41c8-94d3-b67144d5e2ea", "text": "201-500 employees", "color": "violet"},
    {"id": "7d7eb46c-42f9-4364-b9e4-8d710516781f", "text": "501-1,000 employees", "color": "grey"},
    {"id": "d9e14351-1cfd-4ada-b3d9-fc9d5263e240", "text": "1,001-5,000 employees", "color": "orange"},
    {"id": "3c62100d-989e-472a-a5a4-f0c09727e2f7", "text": "5,001-10,000 employees", "color": "pink"},
    {"id": "2466c9fb-45ef-415f-816f-4ecd6f346d52", "text": "10,001+ employees", "color": "yellow"},
]
_CPJ_BASIC_FIELDS = {
    "people": [
        {"name": "First Name", "dataType": "text", "formulaText": "{{source}}.first_name"},
        {"name": "Last Name", "dataType": "text", "formulaText": "{{source}}.last_name"},
        {"name": "Full Name", "dataType": "text", "formulaText": "{{source}}.name"},
        {
            "name": "Job Title",
            "dataType": "text",
            "formulaText": "{{source}}.matched_experience.job_title || {{source}}.latest_experience_title",
        },
        {"name": "Location", "dataType": "text", "formulaText": "{{source}}.location_name"},
        {"name": "Company Domain", "dataType": "url", "formulaText": "{{source}}.domain"},
        {
            "name": "LinkedIn Profile",
            "dataType": "url",
            "formulaText": "{{source}}.url",
            "isDedupeField": True,
        },
    ],
    "companies": [
        {"name": "Name", "dataType": "text", "formulaText": "{{source}}.name"},
        {"name": "Description", "dataType": "text", "formulaText": "{{source}}.description"},
        {"name": "Primary Industry", "dataType": "text", "formulaText": "{{source}}.industry"},
        {"name": "Size", "dataType": "text", "formulaText": "{{source}}.size"},
        {"name": "Type", "dataType": "text", "formulaText": "{{source}}.type"},
        {"name": "Location", "dataType": "text", "formulaText": "{{source}}.location"},
        {"name": "Country", "dataType": "text", "formulaText": "{{source}}.country"},
        {"name": "Domain", "dataType": "url", "formulaText": "{{source}}.domain"},
        {
            "name": "LinkedIn URL",
            "dataType": "url",
            "formulaText": "{{source}}.linkedin_url",
            "isDedupeField": True,
        },
    ],
}
_CPJ_ASSIGNED_FIELD_ID = {
    "people": "f_people_search",
    "companies": "f_companies_search",
}
_CPJ_CLIENT_TABLE_TYPE = {
    "people": "people",
    "companies": "company",
}
_AUDIENCE_CSV_LEAD_FIELDS = ("name", "first_name", "last_name", "title")


def companies_basic_fields_with_select_size() -> list[dict]:
    """Return the legacy Companies starter fields with a select-typed Size.

    This is an explicit opt-in compatibility helper for callers who want the
    old chip-style Size column and accept the risk of frontend-captured option
    UUIDs. ClayCast does not use this payload by default.
    """
    fields = copy.deepcopy(_CPJ_BASIC_FIELDS["companies"])
    for field in fields:
        if field.get("name") == "Size":
            field["dataType"] = "select"
            field["options"] = copy.deepcopy(_CPJ_COMPANY_SIZE_OPTIONS_LEGACY)
            break
    return fields


def _legacy_companies_basic_fields() -> list[dict]:
    """Backward-compatible alias for the explicit legacy Companies helper."""
    return companies_basic_fields_with_select_size()


def _to_iso(value: datetime.datetime | str) -> str:
    """
    Normalize an aware datetime or ISO string to UTC `...Z`.

    Naive datetimes / strings without timezone are rejected so callers never
    silently export the wrong date range.
    """
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("Timestamp string cannot be empty")
        if text.endswith("Z"):
            return text
        try:
            parsed = datetime.datetime.fromisoformat(text)
        except ValueError as e:
            raise ValueError(f"Invalid ISO timestamp {value!r}") from e
    elif isinstance(value, datetime.datetime):
        parsed = value
    else:
        raise ValueError(f"Expected datetime or ISO string, got {type(value).__name__}")

    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise ValueError(
            "Naive datetimes are not allowed. Pass an ISO string with timezone "
            "or a timezone-aware datetime."
        )
    return parsed.astimezone(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _encode_array_filter(key: str, values) -> list[tuple[str, str]]:
    """Encode Clay's indexed bracket-array query params like `ownerIds[0]=123`."""
    if values is None:
        return []
    return [(f"{key}[{i}]", str(value)) for i, value in enumerate(values)]


def _pivot_audience_rows_to_csv(rows: list[dict]) -> tuple[list[str], list[dict]]:
    """
    Pivot audience rows (each with entity.fields[]) into CSV-ready flat rows.

    Column ordering is deterministic: `name`, `first_name`, `last_name`,
    `title` first when present, then alphabetical by field id.
    """
    columns_seen: set[str] = set()
    flat_rows: list[dict] = []
    extra_keys = ("_signals", "_activities", "_custom_objects")

    for row in rows:
        flat: dict[str, Any] = {}
        fields = (row.get("entity") or {}).get("fields") or []
        for field in fields:
            field_id = field.get("field_id")
            if not field_id:
                continue
            columns_seen.add(field_id)
            flat[field_id] = field.get("value")
        for key in extra_keys:
            if key in row:
                columns_seen.add(key)
                flat[key] = json.dumps(row[key], ensure_ascii=False)
        flat_rows.append(flat)

    lead = [field_id for field_id in _AUDIENCE_CSV_LEAD_FIELDS if field_id in columns_seen]
    rest = sorted(field_id for field_id in columns_seen if field_id not in set(lead))
    return lead + rest, flat_rows


def _read_clay_session_from_env_file(path) -> str | None:
    """
    Return the CLAY_SESSION value from a KEY=VALUE .env file, or None if the
    file is readable but has no CLAY_SESSION line. PermissionError is surfaced
    (wrapped in RuntimeError with the offending path) so misconfigured perms
    never silently fall back to the next .env on the walk-up path.
    """
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                if key.strip() == "CLAY_SESSION":
                    val = val.strip().strip('"').strip("'")
                    if val:
                        return val
    except PermissionError as e:
        raise RuntimeError(
            f"Permission denied reading {path}: {e}. Fix file perms or remove "
            "CLAY_SESSION from it — refusing to skip silently."
        ) from e
    except FileNotFoundError:
        return None
    return None


def _find_project_root(start: str | None = None) -> Path | None:
    """
    Resolve the nearest project root by walking up from `start` (default: cwd)
    until the first ancestor containing `.git`.

    Boundaries match ClayCast's auth-loader safety rules:
      * stop at `$HOME`
      * stop at filesystem root
      * resolve symlinks before walking

    Returns the project-root `Path`, or None if no `.git` ancestor is found
    before hitting a boundary.
    """
    start_path = Path(start or os.getcwd()).resolve()
    home = Path.home().resolve()
    root = Path(start_path.anchor or "/").resolve()

    for d in (start_path, *start_path.parents):
        if (d / ".git").exists():
            return d
        if d == home or d == root:
            return None
    return None


def _find_env_with_session(start: str | None = None) -> tuple[str, str] | None:
    """
    Walk up from `start` (default: cwd) looking for the nearest .env that
    contains CLAY_SESSION=. Returns (path, value) on hit, or None.

    Boundaries — the walk is bounded so a stray cwd cannot silently consume an
    unrelated .env:
      * STOP at $HOME (refuse to read ~/.env — personal cookie leakage risk).
      * STOP at the filesystem root `/`.
      * STOP at the first ancestor that contains a `.git` entry (project root
        sentinel). If that dir's .env lacks CLAY_SESSION, the walk ends —
        no leakage beyond the project.

    Symlinks in the start path are resolved via Path.resolve() so a symlinked
    project walks its real-filesystem ancestry.
    """
    start_path = Path(start or os.getcwd()).resolve()
    home = Path.home().resolve()
    root = Path(start_path.anchor or "/").resolve()
    project_root = _find_project_root(str(start_path))

    for d in (start_path, *start_path.parents):
        if d == home or d == root:
            return None

        env = d / ".env"
        if env.is_file():
            val = _read_clay_session_from_env_file(env)
            if val is not None:
                return (str(env), val)

        if project_root is not None and d == project_root:
            return None

    return None


def _load_claysession() -> str:
    """
    Resolve the Clay session cookie.

    Lookup order:
      1. process env var CLAY_SESSION
      2. nearest project .env with a CLAY_SESSION= line, discovered by walking
         up from the current working directory (bounded — see
         _find_env_with_session for the stop rules)
    """
    if os.environ.get("CLAY_SESSION"):
        return os.environ["CLAY_SESSION"]

    found = _find_env_with_session()
    if found:
        return found[1]

    raise RuntimeError(
        "CLAY_SESSION not found. Set it via (a) process env CLAY_SESSION=..., "
        "or (b) a .env file with CLAY_SESSION=s%3A... in your project root "
        "(the nearest ancestor directory with a .git folder). The loader "
        "refuses to read .env from $HOME or / for safety. "
        "See references/cookie-setup.md."
    )

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json",
    "x-clay-frontend-version": "v20260227_221530Z_165b5326da",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "referer": "https://app.clay.com/",
    "origin": "https://app.clay.com",
}


def _chunk_list(lst, size):
    """Yield successive chunks of `size` from `lst`."""
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


def _gen_record_id() -> str:
    """Generate a Clay-style record id: `r_` + 12 random alphanumeric chars.
    Collision space is 62^12 ≈ 3.2e21; safe for bulk-create upserts."""
    return "r_" + "".join(random.choice(_RECORD_ID_CHARS) for _ in range(12))


def _default_type_settings(field_type: str, type_settings: dict | None = None) -> dict:
    """
    Return a Clay-compatible `typeSettings` dict for common field types.
    Caller-supplied `type_settings` wins over defaults.
    """
    normalized = "boolean" if field_type == "checkbox" else field_type
    if normalized not in _VALID_FIELD_TYPES:
        raise ValueError(f"Unsupported field type {field_type!r}")

    merged = copy.deepcopy(type_settings) if isinstance(type_settings, dict) else {}
    merged.setdefault("dataTypeSettings", {})
    dts = merged["dataTypeSettings"]
    dts.setdefault("type", normalized if normalized not in {"formula", "action", "source"} else "text")

    if normalized in {"select", "multiselect"}:
        dts.setdefault("options", [])
    elif normalized == "currency":
        dts.setdefault("currencyCode", "USD")
    elif normalized == "percent":
        dts.setdefault("decimalPlaces", 2)
    return merged


def extract_cell_value(cell):
    """
    Extract the user-facing value from a Clay cell dict.

    Cell payload shapes observed (2026-04-23 probes + G3 investigation):
      - Plain text cell:     `{"value": <scalar>}`
      - Formula cell:        `{"value": <scalar>, "metadata": {"status": "..."}}`
      - Action/enrichment:   `{"value": "<preview>", "metadata": {...},
                              "externalContent": {"fullValue": <json|dict>, ...}}`
      - Writer-documented:   `{"fullValue": {"text": ..., "structured_data": ...}, ...}`
        (not observed in probes; kept as fallback per writer:130-141)

    Resolution order — richest-available wins:
      1. externalContent.fullValue (full action/HTTP response)
      2. fullValue.text
      3. fullValue.structured_data
      4. scalar fullValue
      5. scalar value
    Returns None for an empty/None cell.
    """
    if cell is None:
        return None
    if not isinstance(cell, dict):
        return cell

    ext = cell.get("externalContent") or {}
    if isinstance(ext, dict) and "fullValue" in ext:
        return ext.get("fullValue")

    full = cell.get("fullValue")
    if isinstance(full, dict):
        if full.get("text") is not None:
            return full.get("text")
        if full.get("structured_data") is not None:
            return full.get("structured_data")
    if full is not None:
        return full

    if "value" in cell:
        return cell.get("value")
    return None


def build_salesforce_user_soql(emails=None, names=None, fields=None,
                               sandbox=True) -> str:
    """
    Build a SOQL query string for looking up Salesforce Users by email and/or
    name, returned as a JS string-EXPRESSION ready to drop into
    `create_action_column(inputs={"soql_query": <this>})`.

    Encodes the two traps documented in references/action-registry.md
    (Salesforce SOQL gotchas):

    - sandbox=True (default): emails matched with `LIKE 'addr%'` to survive the
      `.invalid` / `.<sandbox>` suffix Salesforce appends to every User email in
      a sandbox org. Set sandbox=False for a production org to use exact `=`.
      Rule of thumb: if the auth-account name contains 'test'/'sandbox'/'--',
      it's a sandbox.
    - fields defaults to safe DIRECT fields only. It deliberately omits
      relationship hops (`UserRole.Name`) and the `UserRoleId`/`ProfileId` ids,
      which are commonly FLS-blocked on read-only API users and raise
      INVALID_FIELD. Resolve role/profile names with a separate object query
      (`SELECT Id, Name FROM UserRole WHERE Id IN (...)`) only if those ids are
      readable.

    Returns a quoted literal (no per-row field interpolation). For a per-row
    query keyed on a field, build the expression yourself, e.g.
    `'"... WHERE Email LIKE \\'" + {{f_email}} + "%\\'"'`.
    """
    fields = fields or ["Id", "Name", "Email", "Title",
                        "Department", "Division", "IsActive"]
    select = ", ".join(fields)
    clauses = []
    for e in (emails or []):
        safe = str(e).replace("'", "")
        clauses.append(f"Email LIKE '{safe}%'" if sandbox else f"Email = '{safe}'")
    if names:
        joined = ",".join("'" + str(n).replace("'", "") + "'" for n in names)
        clauses.append(f"Name IN ({joined})")
    if not clauses:
        raise ValueError("build_salesforce_user_soql: provide emails and/or names")
    where = " OR ".join(clauses)
    query = f"SELECT {select} FROM User WHERE {where}"
    # Wrap as a JS string literal so it is a valid formula expression.
    return '"' + query.replace('"', '\\"') + '"'


def rewrite_preset_placeholders(preset_inputs_binding: dict, mapping: dict) -> dict:
    """
    Rewrite `{{Input_N}}` placeholders in a preset's inputsBinding payload to
    real Clay field references. Returns a new dict; does not mutate the input.

    Accepted mapping keys:
      - `"Input_1"`
      - `"{{Input_1}}"`
      - `"1"`

    Mapping values are inserted verbatim (typically `"{{@Column}}"` or
    `"{{f_xxx}}"`). If any `{{Input_N}}` placeholder remains after rewriting,
    raises `ValueError` naming the missing placeholders.
    """
    if not isinstance(preset_inputs_binding, dict):
        raise ValueError("rewrite_preset_placeholders: preset_inputs_binding must be a dict")
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError("rewrite_preset_placeholders: mapping must be a non-empty dict")

    normalized: dict[str, str] = {}
    for raw_key, raw_value in mapping.items():
        key = str(raw_key).strip()
        m = re.fullmatch(r"\{\{Input_(\d+)\}\}", key) or re.fullmatch(r"Input_(\d+)", key) or re.fullmatch(r"(\d+)", key)
        if not m:
            raise ValueError(
                "rewrite_preset_placeholders: mapping keys must be 'Input_N', "
                "'{{Input_N}}', or 'N'"
            )
        normalized[f"Input_{m.group(1)}"] = str(raw_value)

    def _rewrite(value):
        if isinstance(value, dict):
            return {k: _rewrite(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_rewrite(v) for v in value]
        if isinstance(value, str):
            def repl(match):
                token = f"Input_{match.group(1)}"
                return normalized.get(token, match.group(0))
            return _PRESET_INPUT_RE.sub(repl, value)
        return value

    rewritten = _rewrite(copy.deepcopy(preset_inputs_binding))
    remaining = sorted({f"{{{{Input_{n}}}}}" for n in _PRESET_INPUT_RE.findall(json.dumps(rewritten, ensure_ascii=False))})
    if remaining:
        raise ValueError(
            "rewrite_preset_placeholders: missing mappings for "
            + ", ".join(remaining)
        )
    return rewritten
    if not isinstance(cell, dict):
        return cell
    ext = cell.get("externalContent")
    if isinstance(ext, dict) and "fullValue" in ext:
        return ext["fullValue"]
    fv = cell.get("fullValue")
    if isinstance(fv, dict):
        if fv.get("text") is not None:
            return fv["text"]
        if fv.get("structured_data") is not None:
            return fv["structured_data"]
    if fv is not None:
        return fv
    return cell.get("value")


def _cell_has_error(value) -> bool:
    """Best-effort recursive Clay cell error detection."""
    if value is None:
        return False
    if isinstance(value, list):
        return any(_cell_has_error(v) for v in value)
    if not isinstance(value, dict):
        return False

    if value.get("error") is True or value.get("isError") is True:
        return True
    for key in ("runStatus", "status", "state"):
        status = value.get(key)
        if isinstance(status, str) and status.upper() in {"FAILED", "ERROR", "STOPPED"}:
            return True
    if value.get("errorMessage") or value.get("error_message") or value.get("message"):
        msg = value.get("errorMessage") or value.get("error_message") or value.get("message")
        if isinstance(msg, str) and "error" in msg.lower():
            return True

    md = value.get("metadata")
    if isinstance(md, dict) and _cell_has_error(md):
        return True
    ext = value.get("externalContent")
    if isinstance(ext, dict) and _cell_has_error(ext):
        return True
    fv = value.get("fullValue")
    if isinstance(fv, (dict, list)) and _cell_has_error(fv):
        return True
    nested = value.get("value")
    if isinstance(nested, (dict, list)) and _cell_has_error(nested):
        return True
    return False


def _status_from_counts(summary: dict) -> str:
    if summary.get("running"):
        return "running"
    if summary.get("pending"):
        return "queued"
    if summary.get("failed") and not summary.get("success"):
        return "failed"
    if summary.get("success") or summary.get("failed"):
        return "completed" if not summary.get("failed") else "partial"
    return "unknown"


def _normalize_run_status(raw: dict, *, total_records: int | None = None) -> dict:
    """
    Normalize `fieldrun` and workspace `runstatus` payloads to one shape:
      {
        "fields": [
          {
            "field_id": str,
            "status": str,
            "progress_percent": float,
            "records_processed": int | None,
            "success": int,
            "failed": int,
            "running": int,
            "pending": int,
            "error": Any,
          }, ...
        ]
      }
    """
    fields: list[dict] = []

    if not isinstance(raw, dict):
        return {"fields": fields}

    if isinstance(raw.get("statusCountsByField"), dict):
        for fid, items in raw["statusCountsByField"].items():
            summary = {"success": 0, "failed": 0, "running": 0, "pending": 0}
            for item in items or []:
                status = str(item.get("status", "")).upper()
                count = int(item.get("count", 0) or 0)
                if status == "SUCCESS":
                    summary["success"] += count
                elif status in {"FAILED", "ERROR", "STOPPED"}:
                    summary["failed"] += count
                elif status in {"RUNNING", "IN_PROGRESS", "PROCESSING"}:
                    summary["running"] += count
                elif status in {"PENDING", "QUEUED", "WAITING"}:
                    summary["pending"] += count
            done = summary["success"] + summary["failed"]
            progress = 0
            if total_records:
                progress = min(100.0, round(done / max(1, total_records) * 100, 1))
            fields.append({
                "field_id": fid,
                "status": _status_from_counts(summary),
                "progress_percent": progress,
                "records_processed": done,
                **summary,
                "error": None,
            })
        return {"fields": fields}

    raw_fields = raw.get("fields", raw.get("fieldStatuses", []))
    if isinstance(raw_fields, dict):
        raw_fields = [{"fieldId": fid, **(obj if isinstance(obj, dict) else {"status": obj})}
                      for fid, obj in raw_fields.items()]

    for item in raw_fields or []:
        fid = item.get("id") or item.get("fieldId")
        if not fid:
            continue
        status = str(item.get("status", item.get("runStatus", "unknown"))).lower()
        mapped = {
            "complete": "completed",
            "done": "completed",
            "success": "completed",
            "pending": "queued",
            "waiting": "queued",
            "in_progress": "running",
            "processing": "running",
            "error": "failed",
            "stopped": "failed",
        }.get(status, status)
        success = int(item.get("success", 0) or 0)
        failed = int(item.get("failed", 0) or 0)
        running = int(item.get("running", 0) or 0)
        pending = int(item.get("pending", 0) or 0)
        progress = item.get("progress", item.get("progressPercent", 0)) or 0
        records_processed = item.get("recordsProcessed")
        if records_processed is None and (success or failed):
            records_processed = success + failed
        fields.append({
            "field_id": fid,
            "status": mapped,
            "progress_percent": progress,
            "records_processed": records_processed,
            "success": success,
            "failed": failed,
            "running": running,
            "pending": pending,
            "error": item.get("error"),
        })
    return {"fields": fields}


def _build_match_index(records, match_field_id):
    """
    Build {match_value → record_id} from a list of raw records.
    Last-seen-wins when the same match_value appears on multiple rows — this
    matches the writer's behavior at writer:120-128. Callers that care about
    duplicate existing rows should pre-check and raise.
    """
    idx = {}
    for r in records:
        cells = r.get("cells", {})
        cell = cells.get(match_field_id, {})
        mv = extract_cell_value(cell)
        if mv is not None and mv != "":
            idx[mv] = r.get("id")
    return idx



import re as _re_module

_BODY_EXPR_TOKENS = (
    ("||", "logical OR"), ("??", "nullish coalescing"), ("==", "equality"),
    ("!=", "inequality"), ("JSON.stringify", "JSON.stringify"),
    ("Number(", "Number()"), ("new Date", "new Date"), (">=", "comparison"), ("<=", "comparison"),
)

# A field reference plus any path accessors that belong to the TOKEN itself:
#   {{f_x}}            {{f_x}}?.key        {{f_x}}.key
#   {{f_x}}?.[0]       {{f_x}}?.a?.b       {{f_x}}[0].name
# Clay's own UI writes nested references this way and renders them as a single
# field pill, so `?.` and `.` inside a reference are NOT expressions.
_FIELD_REF_RE = _re_module.compile(
    r"\{\{[^{}]+\}\}(?:\s*\??\.\s*[A-Za-z_$][\w$]*|\s*\??\[[^\]]*\])*"
)


def _strip_field_refs(body: str) -> str:
    """Remove `{{field}}` tokens together with their path accessors.

    Path accessors (`?.key`, `.key`, `?.[0]`, `[0]`) are part of the reference
    token as Clay's UI writes it, not operators acting on the concatenation --
    so they must be removed before scanning for real expressions, or a perfectly
    canonical body like
        "..." + Clay.formatForJSON({{f_x}}?.normalizedUrl) + "..."
    would be flagged by the bare `?.` it contains.
    """
    return _FIELD_REF_RE.sub("@REF@", body)


def _find_body_expression(body: str):
    """Return (token, label) for the first non-concatenation expression in an
    http-api-v2 body formulaText, or None if the body is a pure concatenation.

    Any expression in a body makes the column's inputs render BLANK in the Clay
    UI while the column still runs correctly -- verified 2026-08-19 across ||
    (inside and outside Clay.formatForJSON), ??, ternaries, == and arithmetic.
    Safe content is: string literals, `+` concatenation, `{{field}}` references
    (with their path accessors), and Clay.formatForJSON({{field}}) calls.
    """
    scan = _strip_field_refs(body)
    for tok, label in _BODY_EXPR_TOKENS:
        if tok in scan:
            return tok, label
    # ternary: a '?' surviving the field-ref strip, paired with a ':'
    for i, ch in enumerate(scan):
        if ch == "?" and scan[i:i + 2] not in ("?.", "??") and scan[i - 1:i + 1] != "??":
            if ":" in scan[i:]:
                return "? :", "ternary"
    return None


def _body_expression_error(where: str, body: str, tok: str, label: str) -> str:
    return (
        "%s: the http-api-v2 'body' contains a %s expression (%r).\n"
        "ANY expression in a body makes the column's inputs UNREADABLE in the Clay "
        "UI - the column still runs and sends the correct payload, and Clay's API "
        "reports no settingsError, but opening it in Clay shows a blank body "
        "(verified 2026-08-19: ||, ??, ternary, == and arithmetic all render blank).\n"
        "Fix: keep the body a pure concatenation of string literals, '+', bare "
        "{{field}} refs and Clay.formatForJSON({{field}}) calls. Move every "
        "expression into its own formula column and reference that column."
        % (where, label, tok)
    )


def format_json_body(mapping: dict) -> str:
    """Build an `http-api-v2` **body** binding the way Clay's own UI writes it.

    `body` is schema type `longtext` (unlike the object-typed `queryString` /
    `headers`), so the canonical binding is a `formulaText` expression that
    concatenates a JSON string, wrapping every interpolated field reference in
    the `Clay.formatForJSON()` formula helper (which escapes quotes, newlines
    and control characters). Verified 2026-07-24 against a UI-created column.

    A `formulaMap` body is also accepted by the API and does send valid JSON,
    but it is NOT what the UI produces: a column built that way round-trips
    differently from a human-built one, and raw values containing a quote or
    newline can break the payload.

    A body must stay a PURE concatenation. A `||` anywhere inside it makes the
    column's inputs unreadable in the Clay UI (blank body) even though the column
    still runs correctly -- verified 2026-08-19 by controlled A/B. This function
    raises `ValueError` on any value containing `||`; put fallbacks in their own
    formula column and reference that column instead.

    Values are rendered by type:
      - `"{{f_xxx}}"` (a lone field reference) -> `Clay.formatForJSON({{f_xxx}})`, quoted
      - `bool`                                  -> bare `true` / `false`
      - `int` / `float`                         -> bare number
      - anything else                           -> JSON string literal

    Usage:
        clay.create_action_column(t_id, "Call Webhook",
            action_key="http-api-v2", package_id="4299091f-...",
            inputs={"method": '"POST"', "url": '"https://..."',
                    "headers": {"Content-Type": '"application/json"'},
                    "body": format_json_body({
                        "company":  "{{f_domain}}",   # field -> formatForJSON
                        "segment":  "Enterprise Retail",  # string literal
                        "allow_ai": True,             # bare boolean
                    }),
                    ...},   # plus every other param as None -- see create_action_column
            data_type="json", view_id=v_id)
    """
    import re as _re

    pieces: list[tuple[str, str]] = []
    literal = "{\n"
    items = list(mapping.items())
    for idx, (key, value) in enumerate(items):
        tail = "," if idx < len(items) - 1 else ""
        if isinstance(value, str) and not _re.fullmatch(r"\{\{[^{}]+\}\}", value.strip()):
            _hit = _find_body_expression(value)
            if _hit:
                raise ValueError(
                    _body_expression_error("format_json_body() value for %r" % key,
                                           value, _hit[0], _hit[1])
                )
        if isinstance(value, str) and _re.fullmatch(r"\{\{[^{}]+\}\}", value.strip()):
            literal += '  "%s": "' % key
            pieces.append(("lit", literal))
            pieces.append(("expr", "Clay.formatForJSON(%s)" % value.strip()))
            literal = '"%s\n' % tail
        elif isinstance(value, bool):
            literal += '  "%s": %s%s\n' % (key, "true" if value else "false", tail)
        elif isinstance(value, (int, float)):
            literal += '  "%s": %s%s\n' % (key, value, tail)
        else:
            escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
            literal += '  "%s": "%s"%s\n' % (key, escaped, tail)
    literal += "}"
    pieces.append(("lit", literal))

    def _enc(raw: str) -> str:
        return '"' + raw.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'

    return " + ".join(_enc(v) if kind == "lit" else v for kind, v in pieces if not (kind == "lit" and v == ""))

# ── Audiences: filter AST builder ────────────────────────────────────────────
# Audiences segments are saved as a filter AST (`filterAst` on POST/PATCH /audiences/segments;
# the official `clay` CLI calls the same object `filter`). These helpers build the exact node
# shapes the Clay UI writes, so segments built from code stay readable and editable in the UI:
#   GroupOp  {"type": "GroupOp", "combinationMode": "And"|"Or", "items": [...]}
#   BinOp    {"type": "BinOp", "key": <fieldId>, "dataPath": ["<entity>_entity_field_values", "field", <fieldId>],
#             "operator": ..., "value"?: ..., "entityType": "ACCOUNT"|"CONTACT"}
#   activity {"type": "BinOp", "key": "<fieldId>::<activityTypeId>",
#             "dataPath": ["activities", "fields", <fieldId>, <activityTypeId>], "operator": ..., "value"?: ...}
#   ColOp    {"type": "ColOp", "dataPath": ["activities", <activityTypeId>], "operator": "AnyItems"|"NoItems",
#             "entityType": ..., "condition": <GroupOp of BinOps that must hold on the SAME activity>}
# Verified live 2026-09-29/30 (workspace 12345): each shape counts via POST /audiences/count and
# saves via POST/PATCH /audiences/segments.
#
# Two traps these helpers encode:
#   1. Sibling activity conditions are evaluated independently ("type = X" AND "date within 30d"
#      as two rows = "ever had type X" AND "any activity within 30d"). Conditions that must
#      describe the same activity go inside one ColOp — but the Clay UI renders a ColOp row as
#      "deleted field", so prefer af_activity() when a segment needs only ONE activity condition.
#   2. An exclusion segment and the "everything else" segment must be exact complements, which
#      requires every rule to use an operator with a single-operator negation (Equal/NotEqual,
#      Contain/NotContain, True/False, Empty/NotEmpty, WithinLast/NotWithinLast,
#      WithinNext/NotWithinNext). af_exclusion_pair() enforces that and decides blank cells per
#      field: a blank counts as "matches none" (not excluded) unless the field has an ("Empty",)
#      rule, which excludes it. NotEqual and NotContain (verified live 2026-09-29) and False match
#      blank cells; NotWithinLast/NotWithinNext are assumed to (unmeasured — the probed date field
#      had no blanks) and are pinned the same way, so on the excluded side the text/date negatives
#      are pinned under And(NotEmpty, ...); boolean rules are exact without a pin because a blank
#      boolean IS False; a ("NotEmpty",) rule makes the included side
#      And(Empty). The partition rests on four assumptions, verified live 2026-09-29 (one
#      production workspace, ACCOUNT entity, read-only counts):
#        A1 Empty and NotEmpty are exact complements (Empty + NotEmpty = total); Equal "" matches
#           blank cells, so "" is refused as a rule value.
#        A2 An operator and its negation are exact complements on populated cells (Equal/NotEqual
#           are case-insensitive).
#        A3 Positive operators (Equal, Contain, True, WithinLast, WithinNext) never match a blank.
#        A4 Nested And/Or groups evaluate as ordinary conjunction/disjunction.
#      UI: verified 2026-09-30 in the segment editor — the pinned And(NotEmpty, Or(...)) and the
#      guarded Or(Empty, And(...)) render as ordinary editable rows and groups (no "deleted
#      field"), the editor's count equals the API count, and the saved AST round-trips with its
#      nesting intact.

AUDIENCE_ENTITY_PATHS = {"ACCOUNT": "account_entity_field_values", "CONTACT": "contact_entity_field_values"}
# The negation map: operator -> the operator that is its exact complement on a POPULATED cell
# (Empty/NotEmpty are complements on every cell). It is NOT the rule-table allow-list by itself:
# af_none_of()/af_exclusion_pair() accept exactly its keys, while af_any_of() also passes other
# operators through (ContainAny, like af_owner_in). The WithinLast-family entries need `timeUnit`,
# which a rule carries as its third element — (op, value, time_unit), see af_rule().
AUDIENCE_NEGATED_OPERATOR = {
    "Equal": "NotEqual", "NotEqual": "Equal",
    "Contain": "NotContain", "NotContain": "Contain",
    "True": "False", "False": "True",
    "Empty": "NotEmpty", "NotEmpty": "Empty",
    "WithinLast": "NotWithinLast", "NotWithinLast": "WithinLast",
    "WithinNext": "NotWithinNext", "NotWithinNext": "WithinNext",
}
# Operators that take a numeric value plus "timeUnit". Verified live 2026-09-29: day/week/month are
# accepted, "year" is rejected (400) and a time node WITHOUT timeUnit is a server error (500) — so
# every builder here refuses to emit one (_af_time_unit).
AUDIENCE_TIME_OPERATORS = frozenset({"WithinLast", "NotWithinLast", "WithinNext", "NotWithinNext"})
AUDIENCE_TIME_UNITS = ("day", "week", "month")
# Negations of positive operators that, unlike the positive operators, also match a BLANK cell.
# Verified live 2026-09-29 (one production workspace, ACCOUNT entity): NotEqual and NotContain
# matched every blank text cell and False matched every blank boolean; NotWithinLast/NotWithinNext
# could not be measured (the probed date field had no blanks) and are treated the same way. A bare
# one on the excluded side of a pair would put blank records on BOTH sides, so af_any_of() pins the
# group under NotEmpty — the pin is the correctness mechanism, not insurance.
AUDIENCE_BLANK_UNKNOWN_OPERATORS = frozenset({"NotEqual", "NotContain", "False", "NotWithinLast", "NotWithinNext"})
# Boolean fields: an unset checkbox is stored blank and Clay's False matches it — True + False = total
# on all 5 boolean fields checked (verified live 2026-09-29, one production workspace). So True/False
# rules use the plain unpinned pair — excluded Or(rules), included And(negations) with no Empty guard
# — and a NotEmpty pin would make a ("False",) rule match nothing (And(NotEmpty, False) matched no
# record live). Flip this only if a workspace is measured the other way.
AUDIENCE_BOOLEAN_BLANK_IS_FALSE = True
_AF_NO_VALUE_OPERATORS = frozenset({"Empty", "NotEmpty", "True", "False"})
_AF_BOOLEAN_OPERATORS = frozenset({"True", "False"})
_AF_SCALAR_VALUE_OPERATORS = frozenset({"Equal", "NotEqual", "Contain", "NotContain"})
_AF_RULE_DICT_KEYS = frozenset({"operator", "op", "value", "time_unit", "timeUnit"})


def _af_entity(entity_type: str) -> str:
    et = str(entity_type).upper()
    if et not in AUDIENCE_ENTITY_PATHS:
        raise ValueError(f"entity_type must be ACCOUNT or CONTACT, got {entity_type!r}")
    return et


def _audience_segment_entity(method: str, verb: str, segment_id: str, segment: dict, explicit: str | None) -> str:
    """Entity type for a fetched segment: its own `entityType` when it carries one (it must be
    ACCOUNT or CONTACT and must agree with `explicit`, the caller's already-normalised
    `entity_type`), otherwise `explicit`; neither -> ValueError asking for one. `method` and
    `verb` name the caller in the messages ("count_audience_records", "counted")."""
    seg_et = segment.get("entityType")
    if seg_et is None:
        if explicit is None:
            raise ValueError(f"{method}: segment {segment_id!r} has no entityType; pass entity_type=")
        return explicit
    seg_et = str(seg_et).upper()
    if seg_et not in AUDIENCE_ENTITY_PATHS:
        raise ValueError(
            f"{method}: segment {segment_id!r} has entityType {seg_et!r}; "
            f"only ACCOUNT and CONTACT segments can be {verb}"
        )
    if explicit is not None and explicit != seg_et:
        raise ValueError(f"{method}: segment {segment_id!r} is {seg_et}, but entity_type={explicit!r} was passed")
    return seg_et


def af_and(*items: dict) -> dict:
    """GroupOp with combinationMode And."""
    return {"type": "GroupOp", "combinationMode": "And", "items": list(items)}


def af_or(*items: dict) -> dict:
    """GroupOp with combinationMode Or."""
    return {"type": "GroupOp", "combinationMode": "Or", "items": list(items)}


def _af_time_unit(operator: str, time_unit: Any, *, where: str) -> str | None:
    """Enforce the timeUnit contract for one node and return the unit to stamp (None for a
    non-time operator). Verified live 2026-09-29: a WithinLast-family node without timeUnit is a
    server error (500) and timeUnit "year" is rejected (400) — both are ValueErrors here, as is a
    time_unit on any other operator (Clay would silently carry it)."""
    units = "/".join(AUDIENCE_TIME_UNITS)
    if operator in AUDIENCE_TIME_OPERATORS:
        if time_unit is None:
            raise ValueError(f"{where}: {operator} needs time_unit ({units}); a time node without timeUnit is a Clay server error")
        if time_unit not in AUDIENCE_TIME_UNITS:
            raise ValueError(f"{where}: time_unit must be one of {units}, got {time_unit!r}")
        return time_unit
    if time_unit is not None:
        raise ValueError(f"{where}: time_unit only applies to {'/'.join(sorted(AUDIENCE_TIME_OPERATORS))}, got {time_unit!r} on {operator!r}")
    return None


def af_field(entity_type: str, field_id: str, operator: str, value: Any = None, *, time_unit: str | None = None) -> dict:
    """BinOp on an Audiences field of a company (ACCOUNT) or person (CONTACT).

    `field_id` is a built-in id (`org_name`, `domain`, `sfdc_owner_id`, `email`, `title`, ...) or a
    custom `audf_...` id. A people segment may test company fields by passing entity_type="ACCOUNT"
    (Clay evaluates them on the linked company). `value` is omitted for Empty/NotEmpty/True/False;
    WithinLast/NotWithinLast/WithinNext/NotWithinNext take a number plus time_unit
    ("day"|"week"|"month") and raise ValueError without it (a unit-less time node is a Clay server
    error, verified live 2026-09-29) or when a time_unit is given for any other operator;
    ContainAny takes a list.
    """
    et = _af_entity(entity_type)
    unit = _af_time_unit(operator, time_unit, where="af_field")
    node = {"type": "BinOp", "key": field_id, "dataPath": [AUDIENCE_ENTITY_PATHS[et], "field", field_id], "operator": operator, "entityType": et}
    if value is not None:
        # a list (ContainAny) is copied, so editing the node never leaks into the caller's rule
        # table; a tuple/set becomes the JSON-able list Clay expects
        node["value"] = list(value) if isinstance(value, (list, tuple, set, frozenset)) else value
    if unit is not None:
        node["timeUnit"] = unit
    return node


def af_activity(activity_type_id: str, field_id: str, operator: str, value: Any = None, *, time_unit: str | None = None) -> dict:
    """UI-native single condition on an imported activity (e.g. a CRM custom object).

    `activity_type_id` is the `acttyp_...` id (from `activities get/summary` or the Audiences settings);
    `field_id` is "title", "created_at" or a custom `actf_...` field on that activity type. Renders and
    edits in the Clay UI. Two of these as siblings do NOT bind to the same activity — see
    af_activity_same_event(). Same time_unit contract as af_field() (ValueError without a unit on
    a WithinLast-family operator, or with one on any other).
    """
    unit = _af_time_unit(operator, time_unit, where="af_activity")
    node = {"type": "BinOp", "key": f"{field_id}::{activity_type_id}", "dataPath": ["activities", "fields", field_id, activity_type_id], "operator": operator}
    if value is not None:
        node["value"] = value
    if unit is not None:
        node["timeUnit"] = unit
    return node


def af_activity_timestamp(entity_type: str, operator: str, value: Any = None, *, time_unit: str | None = None) -> dict:
    """Recency of an activity, for use INSIDE af_activity_same_event() (dataPath ["activities",
    "activity_timestamp"]). Same time_unit contract as af_field() (ValueError without a unit on a
    WithinLast-family operator, or with one on any other)."""
    unit = _af_time_unit(operator, time_unit, where="af_activity_timestamp")
    node = {"type": "BinOp", "dataPath": ["activities", "activity_timestamp"], "operator": operator, "entityType": _af_entity(entity_type)}
    if value is not None:
        node["value"] = value
    if unit is not None:
        node["timeUnit"] = unit
    return node


def af_activity_same_event(activity_type_id: str, entity_type: str, *conditions: dict, operator: str = "AnyItems") -> dict:
    """ColOp: the record has (AnyItems) / lacks (NoItems) an activity of this type on which ALL
    `conditions` hold at once. Use for "type X within 30 days" style rules. The Clay UI cannot
    render this node (it shows "deleted field") — the segment still evaluates correctly.
    """
    et = _af_entity(entity_type)
    if operator not in {"AnyItems", "NoItems"}:
        raise ValueError("operator must be AnyItems or NoItems (AllItems is not supported by Clay)")
    items = [dict(c, entityType=et) for c in conditions]
    return {"type": "ColOp", "dataPath": ["activities", activity_type_id], "operator": operator, "entityType": et, "condition": af_and(*items)}


def af_owner_in(owner_ids: list[str], entity_type: str = "ACCOUNT") -> dict:
    """Company owned by any of these Salesforce users (18-char User IDs). ContainAny on the
    built-in `sfdc_owner_id` is exact for IDs and renders as ONE row in the UI (verified equal to
    an Or of 125 Equal rows)."""
    return af_field(entity_type, "sfdc_owner_id", "ContainAny", list(owner_ids))


def af_rule(rule: Any) -> tuple[str, Any, str | None]:
    """Normalise one exclusion-pair rule to (operator, value, time_unit) and validate it — pure, so
    a rule table can be checked before any HTTP call. Accepted forms: (op,), (op, value),
    (op, value, time_unit), a list in place of a tuple, or a dict {"operator"|"op", "value",
    "time_unit"|"timeUnit"}. Per operator: Empty/NotEmpty/True/False take no value and no unit;
    WithinLast/NotWithinLast/WithinNext/NotWithinNext take a finite number >= 0 plus a unit in
    AUDIENCE_TIME_UNITS (a unit-less time node is a server error, verified live 2026-09-29);
    Equal/NotEqual/Contain/NotContain take one scalar value — not None, not "" (Equal "" matches
    blank cells, verified live 2026-09-29: write ("Empty",) instead) and not a list (one rule per
    value; ContainAny has no exact negation); any other operator takes a value and no unit
    (af_any_of() passes it through, af_none_of() rejects it). Everything else is a ValueError."""
    if isinstance(rule, dict):
        unknown = set(rule) - _AF_RULE_DICT_KEYS
        if unknown:
            raise ValueError(f"rule {rule!r}: unknown keys {sorted(unknown)}; a rule dict has operator (or op), value, time_unit (or timeUnit)")
        if "operator" in rule and "op" in rule:
            raise ValueError(f"rule {rule!r}: give 'operator' or 'op', not both")
        if "time_unit" in rule and "timeUnit" in rule:
            raise ValueError(f"rule {rule!r}: give 'time_unit' or 'timeUnit', not both")
        if "operator" not in rule and "op" not in rule:
            raise ValueError(f"rule {rule!r} needs an 'operator' key")
        op = rule["operator"] if "operator" in rule else rule["op"]
        value = rule.get("value")
        time_unit = rule["time_unit"] if "time_unit" in rule else rule.get("timeUnit")
    elif isinstance(rule, (tuple, list)):
        if not 1 <= len(rule) <= 3:
            raise ValueError(f"a rule is (op,), (op, value) or (op, value, time_unit), got {rule!r}")
        op = rule[0]
        value = rule[1] if len(rule) > 1 else None
        time_unit = rule[2] if len(rule) > 2 else None
    else:
        raise ValueError(f"a rule is (op,), (op, value), (op, value, time_unit) or a dict, got {rule!r}")
    if not isinstance(op, str) or not op:
        raise ValueError(f"rule {rule!r}: operator must be a non-empty string")
    unit = _af_time_unit(op, time_unit, where=f"rule {rule!r}")
    if op in _AF_NO_VALUE_OPERATORS:
        if value is not None:
            raise ValueError(f"rule {rule!r}: {op} takes no value; write ({op!r},)")
        return op, None, None
    if op in AUDIENCE_TIME_OPERATORS:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(f"rule {rule!r}: {op} needs a finite number >= 0 as value, e.g. ({op!r}, 30, 'day')")
        return op, value, unit
    if value is None:
        raise ValueError(f"rule {rule!r}: {op} needs a value")
    if isinstance(value, str) and value == "":
        raise ValueError(f"rule {rule!r}: an empty-string value is ambiguous on blank cells (Equal \"\" matches them); use ('Empty',) or ('NotEmpty',)")
    if op in _AF_SCALAR_VALUE_OPERATORS and isinstance(value, (list, tuple, set, frozenset, dict)):
        raise ValueError(f"rule {rule!r}: {op} takes one scalar value; write one rule per value (ContainAny has no exact negation)")
    return op, value, None


def _af_rules(field_id: Any, rules: Any) -> list[tuple[str, Any, str | None]]:
    """One field's rules through af_rule(): validated, identical rules collapsed (first occurrence
    kept; identical means the same operator, value TYPE, value and unit — 1 == True == 1.0 in
    Python, but ("Equal", 1), ("Equal", True) and ("Equal", 1.0) are three rules), never empty;
    every error names the field."""
    if not isinstance(field_id, str) or not field_id:
        raise ValueError(f"field id must be a non-empty string, got {field_id!r}")
    if isinstance(rules, (str, bytes, dict)) or not hasattr(rules, "__iter__"):
        raise ValueError(f"field {field_id!r}: rules must be a list of rules, got {rules!r}")
    norm: list[tuple[str, Any, str | None]] = []
    seen: list[tuple] = []   # (op, value type name, value, unit) — a list, not a set: values may be lists
    for rule in rules:
        try:
            parts = af_rule(rule)
        except ValueError as e:
            raise ValueError(f"field {field_id!r}: {e}") from None
        key = (parts[0], type(parts[1]).__name__, parts[1], parts[2])
        if key not in seen:
            seen.append(key)
            norm.append(parts)
    if not norm:
        raise ValueError(f"field {field_id!r} has no rules; drop the field from the table instead")
    return norm


def _af_check_negatable(field_id: str, norm: list[tuple[str, Any, str | None]]) -> None:
    """Every rule of an exclusion pair needs an operator with a single-operator negation."""
    for op, _, _ in norm:
        if op not in AUDIENCE_NEGATED_OPERATOR:
            raise ValueError(
                f"field {field_id!r}: operator {op!r} is unknown or has no exact negation; "
                f"exclusion tables accept only {', '.join(sorted(AUDIENCE_NEGATED_OPERATOR))}"
            )


def _af_any_of(et: str, field_id: str, norm: list[tuple[str, Any, str | None]]) -> dict:
    group = af_or(*[af_field(et, field_id, op, value, time_unit=tu) for op, value, tu in norm])
    ops = {op for op, _, _ in norm}
    if ops & _AF_BOOLEAN_OPERATORS and AUDIENCE_BOOLEAN_BLANK_IS_FALSE:
        return group  # a blank boolean IS False: the bare rules already decide blank cells exactly
    if "Empty" not in ops and ops & AUDIENCE_BLANK_UNKNOWN_OPERATORS:
        # NotEqual/NotContain match blank cells (verified live 2026-09-29; NotWithinLast/NotWithinNext
        # are assumed to): without this pin a blank record would be excluded here AND included by the
        # Empty guard of _af_none_of().
        return af_and(af_field(et, field_id, "NotEmpty"), group)
    return group


def _af_none_of(et: str, field_id: str, norm: list[tuple[str, Any, str | None]]) -> dict:
    negated = [af_field(et, field_id, AUDIENCE_NEGATED_OPERATOR[op], value, time_unit=tu) for op, value, tu in norm]
    ops = {op for op, _, _ in norm}
    if ops & _AF_BOOLEAN_OPERATORS and AUDIENCE_BOOLEAN_BLANK_IS_FALSE:
        return af_and(*negated)  # no Empty guard: True/False already partition blank cells
    if "Empty" in ops:
        return af_and(*negated)  # the Empty rule excludes blanks; NotEmpty (its negation) is in here
    if "NotEmpty" in ops:
        return af_and(af_field(et, field_id, "Empty"))  # every populated record is excluded
    return af_or(af_field(et, field_id, "Empty"), af_and(*negated))


def af_any_of(entity_type: str, field_id: str, rules: list) -> dict:
    """Or of one BinOp per rule — the 'excluded' side of a pair for one field. A rule is (op,),
    (op, value), (op, value, time_unit) or a dict (see af_rule(); identical rules collapse, an
    empty list raises). Any operator is passed through (ContainAny, like af_owner_in). Blank cells:
    NotEqual and NotContain (verified live 2026-09-29) and False match blank cells;
    NotWithinLast/NotWithinNext are assumed to (unmeasured — the probed date field had no blanks)
    and are pinned the same way, so a field with one of those and no ("Empty",) rule is pinned —
    And(NotEmpty, Or(rules)), one
    extra UI row — and a blank is never excluded; that pin is what keeps the pair exact, not
    insurance. Exception: True/False rules are emitted bare, because a blank boolean IS False and
    a NotEmpty pin would make ("False",) match nothing (AUDIENCE_BOOLEAN_BLANK_IS_FALSE)."""
    et = _af_entity(entity_type)
    return _af_any_of(et, field_id, _af_rules(field_id, rules))


def af_none_of(entity_type: str, field_id: str, rules: list) -> dict:
    """The exact complement of af_any_of() for the same rules. Every operator must be a key of
    AUDIENCE_NEGATED_OPERATOR (ValueError otherwise, naming the field and the allowed operators).
    Without an ("Empty",) rule: blank OR none of the rules match -> Or(Empty, And(negated)). With
    one, the blank is excluded by that rule, so the guard is dropped -> And(negated), where
    NotEmpty is the negation of the Empty rule. A ("NotEmpty",) rule excludes every populated
    record, so the complement is And(Empty). True/False rules: And(negated) with no guard (a blank
    boolean is False)."""
    et = _af_entity(entity_type)
    norm = _af_rules(field_id, rules)
    _af_check_negatable(field_id, norm)
    return _af_none_of(et, field_id, norm)


def af_exclusion_pair(entity_type: str, rule_table: list[tuple[str, list]]) -> tuple[list[dict], list[dict]]:
    """From [(field_id, rules), ...] return (excluded_items, included_items): the record is excluded
    if ANY field matches any of its rules (Or the first list), included if EVERY field matches none
    (And the second) — a blank cell counts as "matches none" unless that field has an ("Empty",)
    rule — or, on a boolean field, a ("False",) rule, since a blank checkbox is False — which
    excludes it. A rule is (op,), (op, value) or (op, value, time_unit) — see af_rule()
    for the grammar — using only the operators in AUDIENCE_NEGATED_OPERATOR. The whole table is
    validated before anything is built (entity type, (field_id, rules) entries, every rule; an
    empty table or a field with no rules is a ValueError). Fields with a negative operator are
    pinned under NotEmpty on the excluded side, boolean rules are exact unpinned, and a
    ("NotEmpty",) rule makes the included side And(Empty). Count both plus the total to prove they
    partition the entity — see ClayClient.verify_audience_filter_complement()."""
    et = _af_entity(entity_type)
    if isinstance(rule_table, (str, bytes, dict)) or not hasattr(rule_table, "__iter__"):
        raise ValueError(f"rule_table must be a list of (field_id, rules) entries, got {rule_table!r}")
    entries = list(rule_table)
    if not entries:
        raise ValueError("rule_table is empty; an exclusion pair needs at least one (field_id, rules) entry")
    normalised = []
    for entry in entries:
        if not isinstance(entry, (tuple, list)) or len(entry) != 2:
            raise ValueError(f"rule_table entry {entry!r}: expected (field_id, rules)")
        field_id, rules = entry
        norm = _af_rules(field_id, rules)
        _af_check_negatable(field_id, norm)
        normalised.append((field_id, norm))
    excluded = [_af_any_of(et, f, norm) for f, norm in normalised]
    included = [_af_none_of(et, f, norm) for f, norm in normalised]
    return excluded, included


def af_with_ids(node: dict) -> dict:
    """Return a copy of the AST with a UUID `id` on EVERY node (GroupOp, BinOp, ColOp and each
    ColOp condition). The internal segments endpoint validates them — a node without an `id`
    fails with 400 "filterAst.items.N - Invalid input" (verified 2026-09-30); the public API
    documents the same ids as optional editor bookkeeping. Existing ids are kept."""
    out = dict(node)
    out.setdefault("id", str(uuid.uuid4()))
    if isinstance(out.get("items"), list):
        out["items"] = [af_with_ids(x) for x in out["items"]]
    if isinstance(out.get("condition"), dict):
        out["condition"] = af_with_ids(out["condition"])
    return out


def af_root(ast: dict) -> dict:
    """Wrap a node in a root GroupOp (Clay rejects a bare BinOp/ColOp) and stamp UUID ids on every
    node, which is what the UI editor writes and what the internal API requires."""
    root = ast if ast.get("type") == "GroupOp" else af_and(ast)
    return af_with_ids(root)


# ── Audiences: Salesforce import mapping — module-level helpers ───────────────
# The five import-level sync settings the mapping PATCH replaces. They live in the import's
# `importMetadata` (verified live 2026-09-29 — there and nowhere else, not at the import's top
# level). Four are booleans, the fifth is a dict or None.
_SF_IMPORT_SYNC_BOOL_KEYS = (
    "isImportSyncEnabled",
    "isExportSyncEnabled",
    "isCreateNewRecordsEnabled",
    "isTaskSyncEnabled",
)
_SF_IMPORT_SYNC_KEYS = _SF_IMPORT_SYNC_BOOL_KEYS + ("createNewRecordsIdMapping",)
# importMetadata key -> the matching update_salesforce_import_field_mapping() keyword argument.
_SF_IMPORT_SYNC_KWARGS = {
    "isImportSyncEnabled": "is_import_sync_enabled",
    "isExportSyncEnabled": "is_export_sync_enabled",
    "isCreateNewRecordsEnabled": "is_create_new_records_enabled",
    "createNewRecordsIdMapping": "create_new_records_id_mapping",
    "isTaskSyncEnabled": "is_task_sync_enabled",
}
# "Not passed" marker for the five sync-flag kwargs of update_salesforce_import_field_mapping().
# They have no usable default on purpose (the PATCH replaces them on the import), and a sentinel
# rather than None lets the method tell an omitted flag from an explicit None.
_REQUIRED_SYNC_FLAG: Any = object()


class AudienceFieldsOrphanedError(RuntimeError):
    """add_salesforce_import_fields() created Audiences fields but did not map them.

    Raised after the first write (POST /audiences/field) when the second — the mapping PATCH —
    was not sent or did not succeed, so `audf_…` fields exist that nothing fills. A RuntimeError
    subclass: a post-write contract violation, like the rest of the module.

    Attributes: `import_id`, `entity_type` (ACCOUNT / CONTACT), `workspace_id`,
    `created_fields` (the create response), `created_field_ids`, `sync_flags` (the five sync
    settings keyed like importMetadata), `mapping`, `pairing_verified` and `mapped_after_failure`.

    * `pairing_verified=True`: every created field echoed the requested displayName in request
      order, so `mapping` is the exact list the PATCH should carry (existing pairs + new pairs,
      or the freshly re-read pairs + new pairs when the import changed underneath) and
      re-sending it is safe — the PATCH is a full REPLACE, so the retry is idempotent. One
      exception: if the import was not found (or its mapping was malformed) when re-read before
      the PATCH, `mapping` is None and the message says so — check the import first:
          clay.update_salesforce_import_field_mapping(err.import_id, err.mapping,
              entity_type=err.entity_type, **err.sync_flag_kwargs())
    * `pairing_verified=False`: the create response could not be paired with the request (wrong
      shape, wrong count, or a displayName that did not match), `mapping` is None, and the
      created ids must NOT be mapped by position — check each field's displayName in the UI or
      delete the orphans.
    * `mapped_after_failure`: after a failed PATCH the import is re-read once (best effort):
      True = every created id is now in `fieldMapping.fieldMappings`, False = not all are,
      None = the re-read did not succeed. Only a confirmed False justifies a delete.

    claycast has no wrapper for deleting an Audiences field (that endpoint was never captured);
    the official CLI `clay audiences fields delete <audf_id> --entity-type people|companies`
    (soft, idempotent) or the UI does it. Picklable (`__reduce__`), so it crosses process pools.
    """

    def __init__(
        self,
        message: str,
        *,
        import_id: str | None = None,
        entity_type: str | None = None,
        workspace_id: int | str | None = None,
        created_fields: list[dict] | None = None,
        mapping: list[dict] | None = None,
        pairing_verified: bool = False,
        sync_flags: dict | None = None,
        mapped_after_failure: bool | None = None,
    ):
        super().__init__(message)
        self.import_id = import_id
        self.entity_type = entity_type
        self.workspace_id = workspace_id
        self.created_fields = list(created_fields or [])
        self.created_field_ids = [f.get("id") if isinstance(f, dict) else None for f in self.created_fields]
        self.mapping = mapping
        self.pairing_verified = pairing_verified
        self.sync_flags = dict(sync_flags or {})
        self.mapped_after_failure = mapped_after_failure

    def sync_flag_kwargs(self) -> dict:
        """`sync_flags` translated to update_salesforce_import_field_mapping() keyword arguments
        (isImportSyncEnabled -> is_import_sync_enabled, …), so the retry in the message runs as
        written."""
        return {_SF_IMPORT_SYNC_KWARGS[k]: self.sync_flags[k] for k in _SF_IMPORT_SYNC_KEYS if k in self.sync_flags}

    def __reduce__(self):
        return (self.__class__, (str(self),), self.__dict__)

    def __setstate__(self, state):
        self.__dict__.update(state)


def _check_salesforce_sync_overrides(overrides: dict | None, *, where: str) -> dict:
    """Shape-check a `sync_flags` dict before any request: it must be a dict keyed like
    importMetadata — isImportSyncEnabled / isExportSyncEnabled / isCreateNewRecordsEnabled /
    isTaskSyncEnabled (True or False) and createNewRecordsIdMapping (a dict or None). Unknown keys
    (snake_case typos) and wrong types raise ValueError that names sync_flags, not the import.
    Returns a copy; None -> {}."""
    if overrides is None:
        return {}
    if not isinstance(overrides, dict):
        raise ValueError(
            f"{where}: sync_flags must be a dict keyed like importMetadata "
            f"({', '.join(_SF_IMPORT_SYNC_KEYS)}), got {type(overrides).__name__}"
        )
    unknown = sorted(str(k) for k in overrides if k not in _SF_IMPORT_SYNC_KEYS)
    if unknown:
        raise ValueError(
            f"{where}: unknown sync_flags key(s) {', '.join(unknown)}; "
            f"expected any of {', '.join(_SF_IMPORT_SYNC_KEYS)}"
        )
    for key in _SF_IMPORT_SYNC_BOOL_KEYS:
        if key in overrides and not isinstance(overrides[key], bool):
            raise ValueError(f"{where}: sync_flags[{key!r}] must be True or False, got {overrides[key]!r}")
    if "createNewRecordsIdMapping" in overrides:
        val = overrides["createNewRecordsIdMapping"]
        if val is not None and not isinstance(val, dict):
            raise ValueError(
                f"{where}: sync_flags['createNewRecordsIdMapping'] must be a dict or None, got {val!r}"
            )
    return dict(overrides)


def _salesforce_import_sync_flags(
    import_id: str, meta: dict, overrides: dict | None, *, where: str = "add_salesforce_import_fields"
) -> dict:
    """The five sync settings for the mapping PATCH, read from the import's importMetadata with
    `overrides` (a sync_flags dict, shape-checked here) winning.

    Fails closed. A setting that is absent or null in importMetadata and not overridden raises
    ValueError naming every such key and the exact `sync_flags={...}` remedy — never a default,
    because the PATCH replaces the settings and a guessed value would switch a sync on or off.
    Problems are attributed to their source: "importMetadata is missing …" / "has sync
    setting(s) of an unexpected type" versus "sync_flags[k] must be True or False, got …".
    isCreateNewRecordsEnabled=True with createNewRecordsIdMapping=None is refused unless
    importMetadata itself already holds exactly that (the key PRESENT with null): an absent key is
    not a null, so supplying None for a missing key does not get past the check either — for an
    enabled import the remedy names <the import's id-mapping dict> instead. Verified live
    2026-09-29: an ACCOUNT import carried all five keys; a CONTACT import had no
    isTaskSyncEnabled (the UI's replay sent False) — the operator supplies it via sync_flags."""
    overrides = _check_salesforce_sync_overrides(overrides, where=where)
    flags: dict = {}
    wrong: list[str] = []
    for key in _SF_IMPORT_SYNC_BOOL_KEYS:
        val = meta.get(key)
        if val is None:
            continue  # absent or null: missing unless overridden
        if isinstance(val, bool):
            flags[key] = val
        else:
            wrong.append(f"{key}={val!r} (expected True or False)")
    if "createNewRecordsIdMapping" in meta:
        val = meta["createNewRecordsIdMapping"]
        if val is None or isinstance(val, dict):
            flags["createNewRecordsIdMapping"] = val
        else:
            wrong.append(f"createNewRecordsIdMapping={val!r} (expected a dict or None)")
    wrong = [w for w in wrong if w.split("=", 1)[0] not in overrides]  # an override replaces a bad value
    wrong_keys = [w.split("=", 1)[0] for w in wrong]
    flags.update(overrides)
    missing = [k for k in _SF_IMPORT_SYNC_KEYS if k not in flags and k not in wrong_keys]
    if missing or wrong:
        problems = []
        if missing:
            problems.append(f"is missing sync setting(s) {', '.join(missing)}")
        if wrong:
            problems.append(f"has sync setting(s) of an unexpected type: {', '.join(wrong)}")
        # None is a sensible createNewRecordsIdMapping example only while create-new-records is off;
        # with it on, None would just trip the refusal below, so the example names the real remedy
        id_mapping_example = (
            "<the import's id-mapping dict>" if flags.get("isCreateNewRecordsEnabled") is True else "None"
        )
        example = ", ".join(
            f'"{k}": {id_mapping_example if k == "createNewRecordsIdMapping" else "False"}'
            for k in _SF_IMPORT_SYNC_KEYS if k in missing or k in wrong_keys
        )
        raise ValueError(
            f"{where}: import {import_id!r} importMetadata {' and '.join(problems)}; refusing to guess "
            f"(the mapping PATCH replaces them, so a default could switch a sync on or off). Read the import "
            f"with list_audience_imports() to see what Clay put there, then pass the setting(s) explicitly, "
            f"e.g. sync_flags={{{example}}}, using the value the UI's Salesforce sync toggle shows. "
            f"(isTaskSyncEnabled was absent from a live CONTACT import's importMetadata on 2026-09-29 while "
            f"the ACCOUNT import had all five; the UI's replay sent False.)"
        )
    # "create new records on, no id mapping" passes only when the import itself already holds exactly
    # that — isCreateNewRecordsEnabled True and a createNewRecordsIdMapping KEY holding null. An absent
    # key is not a null: sync_flags={"createNewRecordsIdMapping": None} for a missing key is refused
    # like any other override that lands on this combination
    import_already_has_it = (
        meta.get("isCreateNewRecordsEnabled") is True
        and "createNewRecordsIdMapping" in meta
        and meta["createNewRecordsIdMapping"] is None
    )
    if flags["isCreateNewRecordsEnabled"] is True and flags["createNewRecordsIdMapping"] is None and not import_already_has_it:
        raise ValueError(
            f"{where}: sync_flags would set isCreateNewRecordsEnabled=True with createNewRecordsIdMapping=None "
            f"on import {import_id!r} (create-new-records enabled without an id mapping); pass both settings, "
            f"leave the import's own values in place, or send the PATCH yourself with "
            f"update_salesforce_import_field_mapping()"
        )
    return flags


def _validate_salesforce_field_mapping(field_mapping: list[dict], *, where: str) -> None:
    """Every mapping entry must be a dict with a non-empty audienceFieldId and salesforceFieldId.
    Shared by update_salesforce_import_field_mapping (its own input, where=
    "update_salesforce_import_field_mapping: field_mapping") and add_salesforce_import_fields
    (the import's EXISTING entries, checked before anything is written — the PATCH replaces the
    whole list, so an incomplete pair fails the call rather than being dropped)."""
    if not isinstance(field_mapping, list):
        raise ValueError(f"{where} must be a list of mapping entries, got {type(field_mapping).__name__}")
    for i, m in enumerate(field_mapping):
        if not isinstance(m, dict):
            raise ValueError(f"{where}[{i}] is not a mapping entry: {m!r}")
        for key in ("audienceFieldId", "salesforceFieldId"):
            if not m.get(key):
                raise ValueError(f"{where}[{i}] missing {key}: {m!r}")


def _salesforce_import_current_mapping(imp: dict, *, where: str) -> list[dict]:
    """An import's existing pairs: `fieldMapping.fieldMappings` (the shape the GET returns) or a
    bare list (the shape the PATCH is sent); None -> []. Any other shape is a ValueError, raised
    before anything is written."""
    fm = imp.get("fieldMapping")
    if fm is None:
        return []
    if isinstance(fm, list):
        return list(fm)
    if isinstance(fm, dict):
        entries = fm.get("fieldMappings")
        if entries is None:
            return []
        if isinstance(entries, list):
            return list(entries)
        raise ValueError(
            f"{where}: import {imp.get('id')!r} fieldMapping.fieldMappings is not a list "
            f"({type(entries).__name__})"
        )
    raise ValueError(
        f"{where}: import {imp.get('id')!r} fieldMapping has an unexpected shape ({type(fm).__name__}); "
        f"expected {{'fieldMappings': [...]}} or a list"
    )


def _salesforce_mapping_signature(entries: list[dict]) -> list[str]:
    """Order-independent fingerprint of a mapping list, for the drift check between the import
    read at the start of add_salesforce_import_fields() and the re-read before its PATCH."""
    return sorted(json.dumps(m, sort_keys=True, default=str) for m in entries)


def _dedupe_new_salesforce_fields(new_fields: list[dict]) -> list[dict]:
    """One spec per salesforceFieldId, first-seen order kept — the endpoints do not dedupe (a
    repeated id would create a second field and map it twice). Repeats merge: a displayName /
    dataType that one entry gives and another leaves out (None or "") is not a conflict — the
    to_create builder treats "" as absent too; two explicit values that disagree are ambiguous
    and raise. Every entry must be a dict with a non-empty salesforceFieldId; an empty list
    raises. Network-free, so it runs before any read."""
    if not isinstance(new_fields, (list, tuple)):
        raise ValueError(
            f"add_salesforce_import_fields: new_fields must be a list of {{'salesforceFieldId': ...}} dicts, "
            f"got {type(new_fields).__name__}"
        )
    if not new_fields:
        raise ValueError("add_salesforce_import_fields: new_fields is empty")
    merged: dict[str, dict] = {}
    for i, nf in enumerate(new_fields):
        if not isinstance(nf, dict) or not nf.get("salesforceFieldId"):
            raise ValueError(f"add_salesforce_import_fields: new_fields[{i}] missing salesforceFieldId: {nf!r}")
        sf_id = nf["salesforceFieldId"]
        spec = merged.setdefault(sf_id, {"salesforceFieldId": sf_id})
        for key in ("displayName", "dataType"):
            val = nf.get(key)
            if val is None or val == "":
                continue
            if key in spec and spec[key] != val:
                raise ValueError(
                    f"add_salesforce_import_fields: new_fields has conflicting entries for "
                    f"salesforceFieldId {sf_id!r}: {key} {spec[key]!r} vs {val!r}; give one entry per "
                    f"Salesforce field"
                )
            spec[key] = val
    return list(merged.values())


def _audience_fields_delete_text(entity_type: str, ws_id: int | str | None) -> str:
    """The only path for removing orphaned Audiences fields, spelled out for error messages:
    claycast wraps no delete (endpoint never captured), the official CLI does a soft delete."""
    cli_entity = "companies" if entity_type == "ACCOUNT" else "people"
    return (
        f"after confirming 'clay whoami' reports workspace {ws_id}; claycast has no delete wrapper for "
        f"Audiences fields (endpoint never captured): official CLI clay audiences fields delete <audf_id> "
        f"--entity-type {cli_entity} (soft, idempotent; run 'clay audiences fields segments <id>' first) "
        f"or the UI."
    )


class ClayClient:
    def __init__(self, workspace_id: int = None, clay_session: str | None = None):
        """
        clay_session: optional constructor-level override. Takes precedence over
            process env CLAY_SESSION and any `.env` file. Useful when callers
            want to wire a session from elsewhere (a vault, another service, a
            test fixture). Per-call overrides are NOT supported — set the
            override at construction time and reuse the client.
        """
        import requests as requests_module

        self._session_override = clay_session
        self.session = requests_module.Session()
        self.session.headers.update(HEADERS)
        self._load_cookies()
        me = self.me()
        self.user_id = me["id"]
        self.workspace_id = workspace_id or self._default_workspace()
        print(f"[clay] logged in as {me.get('email')} | workspace {self.workspace_id}")

    def _load_cookies(self):
        cookie = self._session_override or _load_claysession()
        self.session.cookies.set("claysession", cookie, domain=".clay.com")

    def _default_workspace(self) -> int:
        res = self.get("/my-workspaces")
        ws = res.get("results", res) if isinstance(res, dict) else res
        return ws[0]["id"]

    def _url(self, path: str) -> str:
        return f"{BASE}{path}"

    def get(self, path: str, **kwargs) -> Any:
        r = self.session.get(self._url(path), **kwargs)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, body: dict = None, **kwargs) -> Any:
        r = self.session.post(self._url(path), json=body, **kwargs)
        r.raise_for_status()
        return r.json()

    def patch(self, path: str, body: dict = None, **kwargs) -> Any:
        r = self.session.patch(self._url(path), json=body, **kwargs)
        r.raise_for_status()
        return r.json()

    def delete(self, path: str, **kwargs) -> Any:
        r = self.session.delete(self._url(path), **kwargs)
        r.raise_for_status()
        return r.json()

    # ── Auth ──────────────────────────────────────────────────────────────────

    def me(self) -> dict:
        return self.get("/me")

    # ── Workspaces / Tables ───────────────────────────────────────────────────

    def _resolve_workspace_id(self, workspace_id: int | str | None = None) -> int | str:
        return workspace_id or self.workspace_id

    def _resource_type_from_id(self, resource_id: str, *, resource_types: dict[str, str] | None = None) -> str:
        if resource_types and resource_id in resource_types:
            return resource_types[resource_id]
        if str(resource_id).isdigit():
            return "workspace"
        if resource_id.startswith("wb_"):
            return "workbook"
        if resource_id.startswith("t_"):
            return "table"
        if resource_id.startswith("gv_"):
            return "view"
        if resource_id.startswith("r_"):
            return "record"
        if resource_id.startswith("f_"):
            raise ValueError(
                f"Resource id {resource_id!r} is ambiguous (`f_` can be folder or field). "
                "Pass resource_types={id: 'folder'} explicitly."
            )
        raise ValueError(f"Could not infer resource type for {resource_id!r}")

    def _resolve_field_id(
        self,
        table_id: str,
        *,
        field_id: str | None = None,
        name: str | None = None,
        view_id: str | None = None,
    ) -> str:
        fields = self.list_fields(table_id, view_id=view_id)
        by_id = {f["field_id"]: f for f in fields}
        by_name = {f["name"]: f for f in fields}
        if field_id:
            if field_id not in by_id:
                raise ValueError(f"Field id {field_id!r} not found on table {table_id}")
            return field_id
        if name:
            if name not in by_name:
                raise ValueError(f"Field name {name!r} not found on table {table_id}")
            return by_name[name]["field_id"]
        raise ValueError("Either field_id or name is required")

    def _find_special_view(self, table_id: str, preconfigured_type: str) -> str | None:
        raw = self.get_table(table_id, include_extra_data=True)
        table = raw.get("table", raw)
        for view in table.get("views", []) or []:
            ts = view.get("typeSettings") or {}
            if ts.get("preconfiguredType") == preconfigured_type:
                return view.get("id")
        return None

    def _resolve_view_id(
        self,
        table_id: str,
        *,
        view_id: str | None = None,
        view_name: str | None = None,
    ) -> str:
        """Resolve a Clay view id from an explicit id, display name, or table default."""
        raw = self.get_table(table_id, include_extra_data=True)
        table = raw.get("table", raw)
        views = table.get("views", []) or table.get("gridViews", []) or []
        by_id = {view.get("id"): view for view in views if view.get("id")}
        by_name = {str(view.get("name", "")).lower(): view for view in views if view.get("name")}

        if view_id:
            if view_id not in by_id:
                raise ValueError(f"View id {view_id!r} not found on table {table_id}")
            return view_id

        if view_name:
            if view_name.startswith("gv_"):
                if view_name not in by_id:
                    raise ValueError(f"View id {view_name!r} not found on table {table_id}")
                return view_name
            resolved = by_name.get(view_name.lower())
            if not resolved:
                raise ValueError(f"View name {view_name!r} not found on table {table_id}")
            return resolved["id"]

        default_view = table.get("defaultViewId") or table.get("firstViewId")
        if default_view:
            return default_view
        if views:
            return views[0]["id"]
        raise ValueError(f"No views found for table {table_id}")

    def _collect_rows_by_name(
        self,
        table_id: str,
        *,
        view_id: str,
        limit: int | None = None,
    ) -> list[dict]:
        """
        Collect rows through the 2-step ids → bulk-fetch path and return
        `{field_name: value}` dicts with `_record_id` included.

        This intentionally avoids `list_records()` because that helper's generic
        read semantics include a `limit<=1000` branch that is wrong for export
        flows that need larger limits.
        """
        if limit is not None:
            limit = int(limit)
            if limit < 1:
                raise ValueError("limit must be >= 1")

        fmap = self.get_field_map(table_id)
        id_to_name = fmap["id_to_name"]
        record_ids = self.get_record_ids(table_id, view_id)
        if limit is not None:
            record_ids = record_ids[:limit]
        if not record_ids:
            return []

        raw_by_id: dict[str, dict] = {}
        for batch in _chunk_list(record_ids, 500):
            for record in self.get_records(table_id, batch):
                if record.get("id"):
                    raw_by_id[record["id"]] = record

        rows = []
        for record_id in record_ids:
            raw = raw_by_id.get(record_id)
            if not raw:
                continue
            row = {"_record_id": record_id}
            for fid, cell in (raw.get("cells") or {}).items():
                row[id_to_name.get(fid, fid)] = extract_cell_value(cell)
            rows.append(row)
        return rows

    def _write_artifact(
        self,
        content,
        *,
        output_dir: str | None = None,
        filename: str | None = None,
        default_stem: str,
        suffix: str,
        serializer=None,
    ) -> str:
        """Write an append-only local artifact and return its absolute path."""
        if output_dir:
            artifact_dir = Path(output_dir).expanduser().resolve()
        else:
            project_root = _find_project_root()
            if project_root is None:
                raise RuntimeError(
                    "Could not determine project root for default artifact placement. "
                    "Pass output_dir explicitly or run from within a git-backed project."
                )
            artifact_dir = project_root / "tmp" / "clay-artifacts"

        artifact_dir.mkdir(parents=True, exist_ok=True)
        try:
            artifact_dir.chmod(0o700)
        except PermissionError:
            pass

        ext = suffix if suffix.startswith(".") else f".{suffix}"
        if filename:
            target_name = Path(filename).name
            if ext and not Path(target_name).suffix:
                target_name += ext
        else:
            timestamp = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace(":", "-")
            target_name = f"{default_stem}-{timestamp}{ext}"

        target_path = artifact_dir / target_name
        if target_path.exists():
            stem = target_path.stem
            suffix_part = target_path.suffix or ext
            counter = 2
            while True:
                candidate = artifact_dir / f"{stem}-{counter}{suffix_part}"
                if not candidate.exists():
                    target_path = candidate
                    break
                counter += 1

        if serializer:
            rendered = serializer(content)
        elif isinstance(content, str):
            rendered = content
        else:
            rendered = json.dumps(content, indent=2, ensure_ascii=False)

        target_path.write_text(rendered, encoding="utf-8")
        return str(target_path.resolve())

    def _read_export_source(self, source: str, *, timeout_seconds: int = 30) -> str:
        """Read a JSON export artifact from an absolute path, file:// URL, or HTTP(S) URL."""
        timeout = int(timeout_seconds)
        if timeout < 1:
            raise ValueError("timeout_seconds must be >= 1")

        if source.startswith("http://") or source.startswith("https://"):
            resp = self.session.get(source, timeout=timeout)
            resp.raise_for_status()
            return resp.text

        if source.startswith("file://"):
            path = Path(source[len("file://"):]).expanduser()
        else:
            path = Path(source).expanduser()
        return path.resolve().read_text(encoding="utf-8")

    def _resolve_runnable_field_ids(
        self,
        table_id: str,
        *,
        field_ids: list[str] | None = None,
        field_names: list[str] | None = None,
    ) -> list[str]:
        if field_ids or field_names:
            resolved = list(field_ids or [])
            if field_names:
                fmap = self.get_field_map(table_id)["name_to_id"]
                for name in field_names:
                    fid = fmap.get(name)
                    if not fid:
                        raise ValueError(f"Field name {name!r} not found on table {table_id}")
                    resolved.append(fid)
            return list(dict.fromkeys(resolved))

        raw = self.get_table(table_id, include_extra_data=True)
        table = raw.get("table", raw)
        return [
            field["id"]
            for field in table.get("fields", []) or []
            if field.get("type") in _RUNNABLE_FIELD_TYPES
        ]

    def list_workspaces(self) -> list[dict]:
        """List accessible workspaces."""
        res = self.get("/my-workspaces")
        if isinstance(res, list):
            return res
        return res.get("results", res.get("workspaces", res.get("data", [])))

    def list_tables(
        self,
        folder_id: str = None,
        *,
        include_functions: bool = True,
    ) -> list[dict]:
        """List the workspace's tables — regular data tables AND function tables.

        Clay splits these across two endpoints and `GET /workspaces/{ws}/tables`
        returns ONLY the regular ones. Function tables (Clay "functions" /
        subroutines) live behind `GET /workspaces/{ws}/subroutines` and are
        invisible here unless you go get them — which is why this method fetches
        both. Verified live 2026-08-18 against workspace 12345: `/tables`
        returned 212 tables, NONE carrying `tableSettings.BLOCK_TYPE ==
        "SUBROUTINE"`, while `/subroutines` returned 31 more that all did, with
        zero id overlap between the two sets.

        The two kinds are not interchangeable and must not be read as one
        undifferentiated list of "tables":

        * `_kind == "TABLE"` — an ordinary data table you browse rows in.
        * `_kind == "FUNCTION_TABLE"` — a callable Clay function. It is a
          `spreadsheet` table underneath, but you INVOKE it (from an
          `execute-subroutine` column, or a registered routine) rather than
          reading it, and each call can cost credits.

        Every item is the raw Clay table object plus synthetic `_`-prefixed keys,
        so the distinction survives being merged into one list:

            {..., "_kind": "TABLE"}

            {..., "_kind": "FUNCTION_TABLE",
                  "_note": "Function table (subroutine): CALLED, not browsed...",
                  "_function": {"source_id": ..., "inputs": [...], "cost": ...,
                                "action_execution_cost": ...,
                                "contains_variable_pricing": ...,
                                "reference_count": ..., "is_clay_managed": ...}}

        Regular tables come first and function tables last, so reading only the
        head of the list never silently mixes the two.

        Args:
            folder_id: sent as `parentFolderId` to `/tables`. WARNING: Clay
                IGNORES it — verified live 2026-08-18, a real folder id and the
                string `"garbage_xyz"` both returned the identical full
                212-table set (and 211 of those 212 carry
                `parentFolderId: null`, because tables belong to workbooks, not
                folders). Kept for signature compatibility; do not rely on it to
                scope results. It is deliberately NOT forwarded to
                `/subroutines`, which DOES honor the param — forwarding it would
                drop every function table whenever a folder was named, since all
                function tables sit at the workspace root.
            include_functions: pass False to skip the `/subroutines` call and get
                regular tables only (one request instead of two). Items are still
                `_kind`-tagged.
        """
        params = {}
        if folder_id:
            params["parentFolderId"] = folder_id
        res = self.get(f"/workspaces/{self.workspace_id}/tables", params=params)
        raw = res.get("results", res) if isinstance(res, dict) else res
        tables = [{**t, "_kind": "TABLE"} for t in raw or []]
        if not include_functions:
            return tables
        return tables + self.list_function_tables()

    def list_function_tables(self) -> list[dict]:
        """List ONLY the function tables (Clay "functions" / subroutines),
        normalized to look like `list_tables()` entries.

        These never appear in `GET /workspaces/{ws}/tables` (see `list_tables`),
        so this is the only way to enumerate them. Source:
        `GET /workspaces/{ws}/subroutines` -> `{"subroutines": [...]}`, where
        each item wraps the function's underlying table plus its pricing/usage
        metadata (verified live 2026-08-18 against workspace 12345, 31 items):

            {"sourceId": "s_...",       # the subroutine input source
             "table": {...},           # full table obj, BLOCK_TYPE=SUBROUTINE
             "cost": 1.1,              # credits per call
             "actionExecutionCost": ..., "containsVariablePricing": bool,
             "referenceCount": 19,     # how many columns currently call it
             "managedSubroutineSubscription": {...} | None}

        Returned items spread `table` flat, tag it `_kind == "FUNCTION_TABLE"`,
        and fold the wrapper metadata into `_function` — so they concatenate with
        regular tables without losing the distinction.
        `_function["is_clay_managed"]` separates Clay's built-in catalog
        functions from ones this workspace authored (19 vs 12 in the smoke).

        Declared inputs are surfaced as `_function["inputs"]` (names only); the
        full specs stay where Clay puts them, at
        `tableSettings.SUBROUTINE_INPUTS`.

        `_function["routine_id"]` is the `function:{table_id}` handle you CALL
        the function by (`POST /public/v0/routines/{routine_id}/run`) — it only
        resolves once the function is registered in the workspace tools
        registry, and UI-built functions do NOT auto-register (see
        clay-api-reference.md § "Creating a custom function (subroutine table)
        via API").
        """
        res = self.get(f"/workspaces/{self.workspace_id}/subroutines")
        subs = res.get("subroutines", res) if isinstance(res, dict) else res
        out: list[dict] = []
        for sub in subs or []:
            table = sub.get("table") or {}
            settings = table.get("tableSettings") or {}
            block_settings = settings.get("BLOCK_SETTINGS") or {}
            out.append({
                **table,
                "_kind": "FUNCTION_TABLE",
                "_note": (
                    "Function table (subroutine): CALLED from an "
                    "execute-subroutine column or a registered routine, not "
                    "browsed like a data table. Calls can cost credits."
                ),
                "_function": {
                    "source_id": sub.get("sourceId"),
                    "routine_id": f"function:{table.get('id')}" if table.get("id") else None,
                    "inputs": [
                        i.get("inputName")
                        for i in settings.get("SUBROUTINE_INPUTS") or []
                    ],
                    "cost": sub.get("cost"),
                    "action_execution_cost": sub.get("actionExecutionCost"),
                    "contains_variable_pricing": sub.get("containsVariablePricing"),
                    "reference_count": sub.get("referenceCount"),
                    "is_clay_managed": bool(sub.get("managedSubroutineSubscription"))
                    or block_settings.get("isClayManaged") is True,
                },
            })
        return out

    def list_folders(self) -> list[dict]:
        """List top-level folders and resources."""
        body = {"parentResource": None, "filters": {}, "isGlobalSearch": False}
        res = self.post(f"/workspaces/{self.workspace_id}/resources_v2/", body)
        return res.get("resources", [])

    def get_workspace_permissions(self, workspace_id: int | str | None = None) -> dict:
        """Fetch workspace permission data."""
        ws_id = self._resolve_workspace_id(workspace_id)
        return self.get(f"/workspaces/{ws_id}/permissions")

    def get_workbook(self, workbook_id: str, *, workspace_id: int | str | None = None) -> dict:
        """
        Fetch a workbook via Clay's direct endpoint:
        `GET /v3/{workspace_id}/workbooks/{workbook_id}`.
        """
        ws_id = self._resolve_workspace_id(workspace_id)
        return self.get(f"/{ws_id}/workbooks/{workbook_id}")

    def list_workbook_tables(self, workbook_id: str) -> list[dict]:
        """List tables in a workbook."""
        res = self.get(f"/workbooks/{workbook_id}/tables")
        if isinstance(res, list):
            return res
        return res.get("tables", res.get("results", []))

    def list_workspace_contents(
        self,
        workspace_id: int | str | None = None,
        *,
        include_tables: bool = True,
        include_permissions: bool = False,
    ) -> dict:
        """
        List folders, workbooks, and optionally tables for a workspace.
        """
        ws_id = self._resolve_workspace_id(workspace_id)
        res = self.post(f"/workspaces/{ws_id}/resources_v2/", {
            "parentResource": None,
            "filters": {},
            "isGlobalSearch": True,
        })
        resources = res.get("resources", []) if isinstance(res, dict) else []

        folders: list[dict] = []
        workbooks: list[dict] = []
        tables: list[dict] = []
        folder_names: dict[str, str] = {}

        for resource in resources:
            rtype = resource.get("resourceType")
            if rtype == "FOLDER":
                folder = {
                    "folder_id": resource.get("id"),
                    "name": resource.get("name"),
                    "parent_folder_id": resource.get("parentFolderId"),
                }
                folders.append(folder)
                if folder["folder_id"]:
                    folder_names[folder["folder_id"]] = folder["name"]
            elif rtype == "WORKBOOK":
                workbooks.append({
                    "workbook_id": resource.get("id"),
                    "name": resource.get("name"),
                    "folder_id": resource.get("parentFolderId"),
                    "folder_name": None,
                    "url": f"https://app.clay.com/workspaces/{ws_id}/workbooks/{resource.get('id')}",
                })

        for workbook in workbooks:
            workbook["folder_name"] = folder_names.get(workbook.get("folder_id"))

        if include_tables:
            for workbook in workbooks:
                workbook_tables = self.list_workbook_tables(workbook["workbook_id"])
                workbook["table_count"] = len(workbook_tables)
                for table in workbook_tables:
                    view_id = table.get("defaultViewId") or table.get("firstViewId")
                    tables.append({
                        "table_id": table.get("id"),
                        "name": table.get("name"),
                        "workbook_id": workbook["workbook_id"],
                        "workbook_name": workbook["name"],
                        "folder_id": workbook.get("folder_id"),
                        "view_id": view_id,
                        "row_count": table.get("rowCount", 0),
                        "field_count": len(table.get("fields", []) or []),
                        "url": (
                            f"https://app.clay.com/workspaces/{ws_id}/workbooks/{workbook['workbook_id']}"
                            f"/tables/{table.get('id')}"
                            + (f"/views/{view_id}" if view_id else "")
                        ),
                    })
        else:
            for workbook in workbooks:
                workbook["table_count"] = 0

        out = {
            "workspace_id": ws_id,
            "folders": folders,
            "workbooks": workbooks,
            "tables": tables,
            "summary": {
                "folder_count": len(folders),
                "workbook_count": len(workbooks),
                "table_count": len(tables),
            },
        }
        if include_permissions:
            out["permissions"] = self.get_workspace_permissions(ws_id)
        return out

    def find_tables(self, search: str, *, workspace_id: int | str | None = None) -> list[dict]:
        """Case-insensitive partial-name table search within a workspace."""
        if not search:
            raise ValueError("search is required")
        contents = self.list_workspace_contents(workspace_id, include_tables=True)
        needle = search.lower()
        matches = []
        for table in contents["tables"]:
            if needle in (table.get("name") or "").lower():
                matches.append({**table, "match_type": "table_name"})
        return matches

    def get_workspace_hierarchy(
        self,
        workspace_id: int | str | None = None,
        *,
        include_tables: bool = True,
    ) -> dict:
        """Build a nested folder → workbook → table hierarchy."""
        contents = self.list_workspace_contents(workspace_id, include_tables=include_tables)
        folder_nodes: dict[str, dict] = {}
        for folder in contents["folders"]:
            folder_nodes[folder["folder_id"]] = {
                "folder_id": folder["folder_id"],
                "name": folder["name"],
                "parent_folder_id": folder.get("parent_folder_id"),
                "subfolders": [],
                "workbooks": [],
            }

        tables_by_workbook: dict[str, list[dict]] = {}
        for table in contents["tables"]:
            tables_by_workbook.setdefault(table["workbook_id"], []).append(table)

        root = {"workbooks": [], "folders": []}
        for folder in folder_nodes.values():
            parent = folder.get("parent_folder_id")
            if parent and parent in folder_nodes:
                folder_nodes[parent]["subfolders"].append(folder)
            else:
                root["folders"].append(folder)

        for workbook in contents["workbooks"]:
            workbook_node = {
                **workbook,
                "tables": tables_by_workbook.get(workbook["workbook_id"], []),
            }
            folder_id = workbook.get("folder_id")
            if folder_id and folder_id in folder_nodes:
                folder_nodes[folder_id]["workbooks"].append(workbook_node)
            else:
                root["workbooks"].append(workbook_node)

        return {"workspace_id": contents["workspace_id"], "_root": root}

    def get_resource_urls(
        self,
        resource_ids: list[str],
        *,
        workspace_id: int | str | None = None,
        resource_types: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Generate Clay app URLs for workspace, folder, workbook, and table ids."""
        ws_id = self._resolve_workspace_id(workspace_id)
        contents = self.list_workspace_contents(ws_id, include_tables=True)
        folder_ids = {f["folder_id"] for f in contents["folders"]}
        workbook_map = {wb["workbook_id"]: wb for wb in contents["workbooks"]}
        table_map = {t["table_id"]: t for t in contents["tables"]}

        urls: dict[str, str] = {}
        for resource_id in resource_ids:
            rtype = resource_types.get(resource_id) if resource_types else None
            if not rtype:
                if resource_id in folder_ids:
                    rtype = "folder"
                elif resource_id in workbook_map:
                    rtype = "workbook"
                elif resource_id in table_map:
                    rtype = "table"
                else:
                    rtype = self._resource_type_from_id(resource_id, resource_types=resource_types)

            if rtype == "workspace":
                urls[resource_id] = f"https://app.clay.com/workspaces/{ws_id}"
            elif rtype == "folder":
                urls[resource_id] = f"https://app.clay.com/workspaces/{ws_id}?folder={resource_id}"
            elif rtype == "workbook":
                urls[resource_id] = f"https://app.clay.com/workspaces/{ws_id}/workbooks/{resource_id}"
            elif rtype == "table":
                table = table_map.get(resource_id, {})
                workbook_id = table.get("workbook_id")
                view_id = table.get("view_id")
                url = (
                    f"https://app.clay.com/workspaces/{ws_id}/workbooks/{workbook_id}/tables/{resource_id}"
                    if workbook_id else
                    f"https://app.clay.com/workspaces/{ws_id}/tables/{resource_id}"
                )
                if view_id:
                    url += f"/views/{view_id}"
                urls[resource_id] = url
            else:
                raise ValueError(f"Unsupported resource type {rtype!r} for {resource_id!r}")
        return urls

    def export_workspace(
        self,
        workspace_id: int | str | None = None,
        *,
        include_rows: bool = False,
        output_dir: str | None = None,
        filename: str | None = None,
        continue_on_error: bool = False,
    ) -> dict:
        """
        Export a workspace hierarchy with table metadata and optional row data to
        a local JSON artifact. Returns the content, manifest, and written path.
        """
        ws_id = self._resolve_workspace_id(workspace_id)
        contents = self.list_workspace_contents(ws_id, include_tables=True)

        folders = {folder["folder_id"]: folder for folder in contents["folders"] if folder.get("folder_id")}
        workbooks_by_folder: dict[str, list[dict]] = {}
        for workbook in contents["workbooks"]:
            parent = workbook.get("folder_id") or "_root"
            workbooks_by_folder.setdefault(parent, []).append(workbook)

        tables_by_workbook: dict[str, list[dict]] = {}
        for table in contents["tables"]:
            tables_by_workbook.setdefault(table["workbook_id"], []).append(table)

        timestamp = datetime.datetime.utcnow().isoformat() + "Z"
        export_data = {
            "workspace_id": str(ws_id),
            "export_timestamp": timestamp,
            "include_rows": include_rows,
            "stats": {
                "folders": len(contents["folders"]),
                "workbooks": len(contents["workbooks"]),
                "tables": len(contents["tables"]),
                "tables_exported": 0,
                "tables_failed": 0,
                "total_rows": 0,
            },
            "hierarchy": {},
            "errors": [],
        }

        def export_table(table_info: dict) -> dict:
            table_id = table_info.get("table_id") or table_info.get("id")
            result = {
                "id": table_id,
                "name": table_info.get("name"),
                "status": "success",
                "row_count": 0,
                "metadata": None,
                "rows": {},
            }
            try:
                raw = self.get_table(table_id, include_extra_data=True)
                table = raw.get("table", raw)
                fields = table.get("fields", []) or []
                views = table.get("views", []) or []
                result["metadata"] = {
                    "table_id": table_id,
                    "table_name": table.get("name"),
                    "row_count": table.get("rowCount", 0),
                    "fields": fields,
                    "views": views,
                }
                if include_rows:
                    resolved_view_id = self._resolve_view_id(table_id)
                    rows = self._collect_rows_by_name(table_id, view_id=resolved_view_id, limit=None)
                    result["rows"] = {
                        row["_record_id"]: {k: v for k, v in row.items() if k != "_record_id"}
                        for row in rows
                    }
                    result["row_count"] = len(result["rows"])
                    export_data["stats"]["total_rows"] += len(result["rows"])
                export_data["stats"]["tables_exported"] += 1
                return result
            except Exception as exc:
                result["status"] = "failed"
                result["error"] = str(exc)
                export_data["errors"].append({"table_id": table_id, "error": str(exc)})
                export_data["stats"]["tables_failed"] += 1
                if not continue_on_error:
                    raise
                return result

        for folder_id in list(folders.keys()) + ["_root"]:
            workbooks = workbooks_by_folder.get(folder_id, [])
            if not workbooks:
                continue
            folder_entry = {
                "id": folder_id,
                "name": "_root" if folder_id == "_root" else folders[folder_id].get("name", folder_id),
                "workbooks": {},
            }
            for workbook in workbooks:
                workbook_id = workbook["workbook_id"]
                workbook_entry = {
                    "id": workbook_id,
                    "name": workbook.get("name"),
                    "tables": {},
                }
                for table in tables_by_workbook.get(workbook_id, []):
                    exported = export_table(table)
                    workbook_entry["tables"][exported["id"]] = exported
                folder_entry["workbooks"][workbook_id] = workbook_entry
            export_data["hierarchy"][folder_id] = folder_entry

        manifest = {
            "workspace_id": str(ws_id),
            "export_timestamp": timestamp,
            "stats": export_data["stats"],
            "errors": export_data["errors"],
        }
        path = self._write_artifact(
            export_data,
            output_dir=output_dir,
            filename=filename,
            default_stem=f"workspace-{ws_id}",
            suffix=".json",
        )
        return {"content": export_data, "path": path, "manifest": manifest}

    def get_table(
        self,
        table_id: str,
        *,
        include_extra_data: bool = False,
        workspace_id: int | str | None = None,
        extra_data_view_id: str | None = None,
    ) -> dict:
        """
        Fetch a table dict.

        include_extra_data=True returns the richer shape used by the writer:
        top-level keys become `['table', 'extraData']`; `table.fields[]` is
        populated with the full column list; `table.firstViewId` is usable as
        the default view (Clay's `defaultViewId` is typically null — the
        `firstViewId` field is what the UI uses).

        Envelope note: the raw response may be {"table": ..., "extraData": ...}
        OR a bare table dict — envelopes are polymorphic. Consumers should
        unwrap with `raw.get("table", raw)` (the SDK's own callers already do;
        see clay-api-reference "Response envelopes are polymorphic").
        """
        params = {}
        if include_extra_data:
            params["includeExtraData"] = "true"
        if workspace_id is not None:
            params["workspaceId"] = workspace_id
        if extra_data_view_id is not None:
            params["extraDataViewId"] = extra_data_view_id
        return self.get(f"/tables/{table_id}", params=params or None)

    def count_records(self, table_id: str) -> int:
        """
        Return the total record count for a table.
        Endpoint: `GET /tables/{t}/count` → `{"tableTotalRecordsCount": N}`.
        Raises HTTPError on API failure (not 0-on-error like the writer).
        """
        res = self.get(f"/tables/{table_id}/count")
        return int(res.get("tableTotalRecordsCount", 0))

    def get_field_map(self, table_id: str) -> dict:
        """
        Return field-name ↔ field-id mappings and the default view id.

        Returns:
            {
                "name_to_id": {field_name: field_id, ...},
                "id_to_name": {field_id: field_name, ...},
                "default_view_id": <str | None>,  # firstViewId fallback
            }
        """
        raw = self.get_table(table_id, include_extra_data=True)
        tbl = raw.get("table", raw) if isinstance(raw, dict) else {}
        fields = tbl.get("fields") or []
        name_to_id = {f.get("name"): f.get("id") for f in fields if f.get("name") and f.get("id")}
        id_to_name = {f.get("id"): f.get("name") for f in fields if f.get("name") and f.get("id")}
        default_view = tbl.get("defaultViewId") or tbl.get("firstViewId")
        if not default_view:
            views = tbl.get("views") or []
            if views:
                default_view = views[0].get("id")
        return {
            "name_to_id": name_to_id,
            "id_to_name": id_to_name,
            "default_view_id": default_view,
        }

    def delete_table(self, table_id: str) -> dict:
        """Delete a table. Returns the deleted table dict."""
        return self.delete(f"/tables/{table_id}")

    def set_table_description(self, table_id: str, description: str) -> dict:
        """
        Set a table's description. Returns the updated table dict.

        Endpoint: `PATCH /v3/tables/{table_id}`. The empty `tableSettings`/
        `fieldGroupMap`/`sourceSettings` keys mirror exactly what the Clay UI
        sends; Clay treats an empty `{}` as no-change (merge semantics — the
        live AUTO_RUN/dedupe settings survive a capture-verified empty `{}`),
        so this only rewrites the description.
        """
        body = {
            "description": description,
            "tableSettings": {},
            "fieldGroupMap": {},
            "sourceSettings": {},
        }
        return self.patch(f"/tables/{table_id}", body)

    def generate_table_description(
        self,
        table_id: str,
        *,
        save: bool = True,
        workspace_id: int | str | None = None,
    ) -> dict:
        """
        Generate a table description with Clay's built-in AI tool — the
        "Generate" button next to a table's Description field.

        Two-step flow, mirrored from the Clay UI:
          1. `POST /v3/ai-generation/table-description`
             body `{"workspaceId": <int>, "tableId": "t_..."}`
             → `{"description": "<AI summary>"}`. Read-only: the AI reads the
             table's columns/sources and writes a prose summary; nothing saved.
          2. If `save` (default — matches the UI), persist it via
             `set_table_description` (`PATCH /v3/tables/{table_id}`),
             overwriting any existing description.

        Pass `save=False` to preview the generated text without writing it.

        Returns:
            {"description": <str>, "saved": <bool>, "table": <dict | None>}
            where `table` is the updated table dict when saved, else None.
        """
        ws_id = self._resolve_workspace_id(workspace_id)
        gen = self.post(
            "/ai-generation/table-description",
            {
                "workspaceId": int(ws_id) if str(ws_id).isdigit() else ws_id,
                "tableId": table_id,
            },
        )
        description = gen.get("description", "") if isinstance(gen, dict) else ""
        table = None
        if save and description:
            table = self.set_table_description(table_id, description)
        return {
            "description": description,
            "saved": bool(save and description),
            "table": table,
        }

    def create_table(
        self,
        name: str,
        workbook_id: str = None,
        *,
        workspace_id: int | str | None = None,
        table_type: str = "spreadsheet",
        fields: list[dict] | None = None,
        seed_data: list[dict] | None = None,
        source_table_id: str | None = None,
        clone_mode: str = "shallow",
    ) -> dict:
        """
        Create a table, optionally create fields, seed rows, or shallow-clone
        a source table's name/type schema.
        """
        ws_id = self._resolve_workspace_id(workspace_id)
        if table_type not in {"spreadsheet", "company", "people", "jobs"}:
            raise ValueError(f"Unsupported table_type {table_type!r}")
        if source_table_id and fields:
            raise ValueError("Pass either fields or source_table_id, not both")
        if source_table_id and clone_mode != "shallow":
            raise ValueError("Only clone_mode='shallow' is supported")

        if not workbook_id:
            wb = self.post("/workbooks", {
                "name": name,
                "workspaceId": ws_id,
                # NOTE (verified 2026-07-23): Clay does NOT persist this create-body
                # settings value — the response echoes `settings: {}`. AUTO_RUN is
                # effectively per-table: PATCH /v3/tables/{t}
                # {"tableSettings": {"AUTO_RUN_ON": ...}}.
                "settings": {"isAutoRun": True},
            })
            workbook_id = wb["id"]

        body = {
            "workspaceId": int(ws_id) if str(ws_id).isdigit() else ws_id,
            "workbookId": workbook_id,
            "name": name,
            "type": table_type,
            "callerName": "clay-client",
        }
        if table_type == "spreadsheet":
            body["template"] = "basic"
            body["sourceSettings"] = {}

        res = self.post("/tables", body)
        table = res.get("table", res)
        table["workbookId"] = workbook_id
        view_id = table.get("firstViewId") or ((table.get("views") or [{}])[0].get("id"))
        existing_names_on_new_table = {
            field.get("name")
            for field in (self.get_table(table["id"], include_extra_data=True).get("table", {}).get("fields", []) or [])
            if field.get("name")
        }

        if source_table_id:
            source_raw = self.get_table(source_table_id, include_extra_data=True)
            source_table = source_raw.get("table", source_raw)
            fields = [
                {"name": field.get("name"), "type": field.get("type", "text")}
                for field in source_table.get("fields", []) or []
                if field.get("id") not in _SYSTEM_FIELD_IDS and field.get("name") not in existing_names_on_new_table
            ]

        created_fields = []
        field_map = {}
        for field in fields or []:
            field_name = field.get("name")
            field_type = "boolean" if field.get("type") == "checkbox" else field.get("type", "text")
            if not field_name:
                raise ValueError("Each field definition requires a name")
            if field_type not in _VALID_FIELD_TYPES:
                raise ValueError(f"Unsupported field type {field_type!r} for {field_name!r}")

            column_def = {"name": field_name, "type": field_type}
            incoming_ts = field.get("typeSettings") or field.get("type_settings")
            if incoming_ts or field_type in {"text", "number", "boolean", "url", "date", "email", "image", "select", "multiselect", "phone", "currency", "percent"}:
                column_def["typeSettings"] = _default_type_settings(field_type, incoming_ts)

            created = self.create_column(table["id"], column_def, view_id=view_id)
            created_fields.append({
                "field_id": created.get("id"),
                "name": created.get("name", field_name),
                "type": field_type,
            })
            if created.get("id"):
                field_map[created.get("name", field_name)] = created["id"]

        created_records = []
        if seed_data:
            created_records = self.create_records(table["id"], seed_data, field_names=True)

        table["tableUrl"] = f"https://app.clay.com/workspaces/{ws_id}/workbooks/{workbook_id}/tables/{table['id']}"
        if view_id:
            table["tableUrl"] += f"/views/{view_id}"
        table["fieldsCreated"] = created_fields
        table["fieldMap"] = field_map
        table["recordsCreated"] = len(created_records)
        return table

    def _resolve_import_mapping(
        self,
        table_id: str,
        csv_columns: list[str],
        *,
        column_mapping: dict[str, str] | None = None,
        create_missing_fields: bool = False,
        skip_unmapped_columns: bool = True,
        workspace_id: int | str | None = None,
    ) -> dict:
        raw = self.get_table(table_id, include_extra_data=True, workspace_id=workspace_id)
        table = raw.get("table", raw)
        ws_id = workspace_id or table.get("workspaceId") or (table.get("workbook") or {}).get("workspaceId")
        view_id = table.get("firstViewId") or ((table.get("views") or [{}])[0].get("id"))

        table_fields = {}
        field_id_to_name = {}
        for field in table.get("fields", []) or []:
            fid = field.get("id")
            fname = field.get("name")
            if fid and fname:
                table_fields[fname.lower()] = {"id": fid, "name": fname}
                field_id_to_name[fid] = fname

        mapped: dict[str, str] = {}
        unmapped: list[str] = []
        auto_matched: list[str] = []
        created: list[str] = []
        skipped: list[str] = []

        if column_mapping:
            for csv_col, clay_name in column_mapping.items():
                key = str(clay_name).lower()
                if key in table_fields:
                    mapped[csv_col] = table_fields[key]["id"]
                elif clay_name in field_id_to_name:
                    mapped[csv_col] = clay_name
                else:
                    unmapped.append(csv_col)
        else:
            for csv_col in csv_columns:
                key = csv_col.strip().lower()
                if key in table_fields:
                    mapped[csv_col] = table_fields[key]["id"]
                    auto_matched.append(csv_col)
                else:
                    unmapped.append(csv_col)

        if create_missing_fields and unmapped:
            for csv_col in list(unmapped):
                created_field = self.create_column(
                    table_id,
                    {
                        "name": csv_col,
                        "type": "text",
                        "typeSettings": _default_type_settings("text"),
                    },
                    view_id=view_id,
                )
                fid = created_field.get("id")
                if not fid:
                    raise RuntimeError(f"Clay did not return an id for newly created field {csv_col!r}")
                mapped[csv_col] = fid
                created.append(csv_col)
                unmapped.remove(csv_col)

        if skip_unmapped_columns:
            skipped = list(unmapped)
        elif unmapped:
            raise ValueError(
                "Unmapped CSV columns remain: "
                + ", ".join(unmapped)
                + ". Pass skip_unmapped_columns=True or create_missing_fields=True."
            )

        if not mapped:
            raise ValueError("No CSV columns could be mapped to Clay fields")

        return {
            "workspace_id": str(ws_id) if ws_id is not None else None,
            "table": table,
            "mapping_used": mapped,
            "mapping_details": {
                "mapped": len(mapped),
                "unmapped": len(unmapped),
                "created": len(created),
                "skipped_columns": skipped,
                "auto_matched": auto_matched,
                "fields_created": created,
            },
        }

    def preview_csv_input(
        self,
        *,
        csv_url: str | None = None,
        csv_data: str | None = None,
        preview_rows: int = 10,
    ) -> dict:
        """
        Parse inline or remote CSV without touching Clay.
        Exactly one of `csv_url` or `csv_data` is required.
        """
        if bool(csv_url) == bool(csv_data):
            raise ValueError("Pass exactly one of csv_url or csv_data")

        raw_csv = csv_data
        if csv_url:
            resp = self.session.get(csv_url, allow_redirects=True, timeout=30)
            resp.raise_for_status()
            raw_csv = resp.text

        reader = csv.DictReader(io.StringIO(raw_csv or ""))
        columns = reader.fieldnames or []
        rows = list(reader)
        if not columns:
            raise ValueError("CSV has no columns")
        if not rows:
            raise ValueError("CSV has no data rows")

        return {
            "csv_info": {
                "total_rows": len(rows),
                "total_columns": len(columns),
                "columns": columns,
            },
            "preview_data": rows[:preview_rows],
            "_rows": rows,
            "_raw_csv": raw_csv,
        }

    def _get_signed_import_upload(self, filename: str) -> dict:
        upload = self.post("/imports/signed-s3-post-url", {
            "filename": filename,
            "uploadMode": "import",
        })
        if not upload.get("url") or not isinstance(upload.get("fields"), dict) or not upload["fields"].get("key"):
            raise RuntimeError(f"Unexpected signed upload payload: {upload}")
        return upload

    def _upload_import_blob(self, upload: dict, content: bytes, filename: str) -> None:
        import requests as requests_module

        resp = requests_module.post(
            upload["url"],
            data=upload.get("fields", {}),
            files={"file": (filename, content, "text/csv")},
            timeout=60,
        )
        if resp.status_code not in {200, 201, 204}:
            raise RuntimeError(f"S3 upload failed ({resp.status_code}): {resp.text[:300]}")

    def get_import_job(self, job_id: str) -> dict:
        """Fetch one Clay import job."""
        return self.get(f"/imports/{job_id}")

    def wait_for_import_job(
        self,
        job_id: str,
        *,
        timeout_seconds: int = 300,
        poll_interval_seconds: int = 3,
    ) -> dict:
        """
        Poll an import job until FINISHED or a terminal failure.

        Defaults: 300s total wait (writer schema's documented default), polling
        every 3s with backoff up to 10s. ClayCast does NOT silently clamp the caller's
        `timeout_seconds` — pass 600, get 600. (The upstream Datagen writer
        silently clamped to 120s because of its sandbox; claycast has no such limit.)
        For very large imports where you want to return immediately and poll
        externally, use `import_csv_to_table(..., wait_for_completion=False)`
        and call `get_import_job(job_id)` yourself.
        """
        timeout = max(1, int(timeout_seconds))
        start = time.time()
        poll = max(1, poll_interval_seconds)
        while time.time() - start < timeout:
            job = self.get_import_job(job_id)
            state = job.get("state", {}) if isinstance(job, dict) else {}
            status = state.get("status", job.get("status"))
            if status == "FINISHED":
                return job
            if status in {"FAILED", "ERROR", "STOPPED"}:
                raise RuntimeError(f"Import job {job_id} failed with status {status}")
            time.sleep(poll)
            poll = min(poll + 1, 10)
        raise TimeoutError(f"Import job {job_id} did not finish within {timeout}s")

    def import_csv_to_table(
        self,
        table_id: str,
        *,
        csv_url: str | None = None,
        csv_data: str | None = None,
        workspace_id: int | str | None = None,
        mode: str = "auto_import",
        column_mapping: dict[str, str] | None = None,
        create_missing_fields: bool = False,
        skip_unmapped_columns: bool = True,
        wait_for_completion: bool = True,
        timeout_seconds: int = 300,
        preview_rows: int = 10,
        run_enrichments: bool = True,
    ) -> dict:
        """
        Import CSV data into an EXISTING table through Clay's signed-upload +
        import-job flow.
        """
        if mode == "preview":
            return self.preview_csv_input(csv_url=csv_url, csv_data=csv_data, preview_rows=preview_rows)
        if mode not in {"import", "auto_import"}:
            raise ValueError("mode must be 'import', 'auto_import', or 'preview'")
        if mode == "import" and not column_mapping:
            raise ValueError("mode='import' requires column_mapping")

        preview = self.preview_csv_input(csv_url=csv_url, csv_data=csv_data, preview_rows=preview_rows)
        rows = preview["_rows"]
        columns = preview["csv_info"]["columns"]
        started_at = time.time()

        mapping = self._resolve_import_mapping(
            table_id,
            columns,
            column_mapping=column_mapping if mode == "import" else None,
            create_missing_fields=create_missing_fields,
            skip_unmapped_columns=skip_unmapped_columns,
            workspace_id=workspace_id,
        )
        ws_id = mapping["workspace_id"]

        filename = "import_data.csv"
        if csv_url:
            filename = csv_url.split("/")[-1].split("?")[0] or filename

        upload = self._get_signed_import_upload(filename)
        self._upload_import_blob(upload, preview["_raw_csv"].encode("utf-8"), filename)

        header_row = {column: column for column in columns}
        preview_records = [{column: row.get(column, "") for column in columns} for row in rows[:5]]
        template_map = {
            field_id: "{{" + csv_col + "}}"
            for csv_col, field_id in mapping["mapping_used"].items()
        }

        payload = {
            "config": {
                "source": {
                    "type": "S3_CSV",
                    "filename": filename,
                    "key": upload["fields"]["key"],
                    "recordKeys": list(columns),
                    "records": [header_row] + preview_records,
                    "hasHeader": True,
                    "fieldDelimiter": ",",
                    "uploadMode": "import",
                },
                "destination": {"type": "TABLE", "tableId": table_id},
                "map": template_map,
                "isImportWithoutRun": not run_enrichments,
            },
            "workspaceId": str(ws_id),
        }

        job = self.post("/imports", payload)
        job_id = job.get("id") or job.get("jobId")
        if not job_id:
            raise RuntimeError(f"Clay did not return an import job id: {job}")

        out = {
            "success": True,
            "mode": mode,
            "status": "importing",
            "import_job_id": job_id,
            "csv_info": preview["csv_info"],
            "preview_data": preview["preview_data"],
            "mapping_used": mapping["mapping_used"],
            "mapping_details": mapping["mapping_details"],
            "import_results": {},
            "job": job,
        }

        if wait_for_completion:
            final_job = self.wait_for_import_job(
                job_id,
                timeout_seconds=timeout_seconds,
                poll_interval_seconds=3,
            )
            state = final_job.get("state", {})
            out["success"] = True
            out["status"] = "completed"
            out["import_results"] = {
                "rows_imported": state.get("numRowsSoFar", final_job.get("rowsImported", final_job.get("totalRows", 0))),
                "rows_failed": 0,
                "elapsed_seconds": round(time.time() - started_at, 1),
                "progress_percent": 100,
            }
            out["job"] = final_job
        return out

    # ── Fields (Columns) ──────────────────────────────────────────────────────

    def get_schema(self, table_id: str, view_id: str) -> dict:
        """Get table schema including all field definitions."""
        return self.get(f"/tables/{table_id}/views/{view_id}/table-schema-v2")

    def inspect_table(
        self,
        table_id: str,
        *,
        view_id: str | None = None,
        include_samples: bool = True,
        include_lineage: bool = True,
    ) -> dict:
        """
        Lightweight structured table inspection from `table-schema-v2`.
        Returns field/type/semantic-type metadata plus optional example records.
        """
        resolved_view_id = self._resolve_view_id(table_id, view_id=view_id)
        data = self.get_schema(table_id, resolved_view_id)
        schema = data.get("tableSchema", {})
        example_records = data.get("exampleRecords", [])

        fields = []
        source_fields = []
        semantic_types: set[str] = set()
        fid_to_name = {}
        for fid, info in schema.items():
            field_type = info.get("type", "unknown")
            field_name = info.get("name", fid)
            data_type = info.get("dataType", "")
            semantic_type = info.get("semanticType", "")
            extracted_from = info.get("extractedFrom", "")
            fid_to_name[fid] = field_name
            if semantic_type:
                semantic_types.add(semantic_type)

            entry = {
                "field_id": fid,
                "name": field_name,
                "type": field_type,
                "data_type": data_type,
                "semantic_type": semantic_type,
            }
            if include_lineage and extracted_from:
                entry["extracted_from"] = extracted_from
            fields.append(entry)

            if field_type == "source":
                children = info.get("children", {}) or {}
                child_list = []
                for child_name, child_info in children.items():
                    child_semantic = child_info.get("semanticType", "")
                    if child_semantic:
                        semantic_types.add(child_semantic)
                    child_list.append({
                        "name": child_name,
                        "data_type": child_info.get("dataType", ""),
                        "semantic_type": child_semantic,
                    })
                source_fields.append({
                    "field_id": fid,
                    "name": field_name,
                    "children": child_list,
                })

        samples = []
        if include_samples:
            for record in example_records or []:
                samples.append({fid_to_name.get(fid, fid): value for fid, value in record.items()})

        return {
            "table_id": table_id,
            "view_id": resolved_view_id,
            "field_count": len(fields),
            "sample_count": len(samples),
            "fields": fields,
            "samples": samples,
            "source_fields": source_fields,
            "semantic_types": sorted(semantic_types),
        }

    def list_fields(self, table_id: str, *, view_id: str | None = None) -> list[dict]:
        """
        Return a normalized field list with optional view-aware ordering and
        visibility metadata.
        """
        raw = self.get_table(
            table_id,
            include_extra_data=True,
            extra_data_view_id=view_id,
        )
        table = raw.get("table", raw)
        views = table.get("views", []) or table.get("gridViews", []) or []
        chosen_view = None
        if view_id:
            chosen_view = next((view for view in views if view.get("id") == view_id), None)
        elif views:
            chosen_view = views[0]

        view_fields = (chosen_view or {}).get("fields") or {}
        out = []
        for idx, field in enumerate(table.get("fields", []) or []):
            meta = view_fields.get(field.get("id"), {}) if isinstance(view_fields, dict) else {}
            out.append({
                "field_id": field.get("id"),
                "name": field.get("name"),
                "type": field.get("type", "text"),
                "position": meta.get("order", idx + 1),
                "visible": meta.get("isVisible", True),
                "type_settings": field.get("typeSettings") or {},
            })

        def sort_key(item):
            return str(item.get("position", ""))

        ordered = sorted(out, key=sort_key)
        for idx, field in enumerate(ordered, start=1):
            field["position"] = idx
        return ordered

    def create_column(self, table_id: str, column_def: dict, view_id: str = None) -> dict:
        """
        Add a column to a table.
        column_def matches the Clay JSON schema format from clay-api-reference.md.

        Envelope note: the raw endpoint returns either {"field": {...}} or a
        bare field dict — response envelopes are polymorphic across endpoints
        and over time. This method unwraps both (`res.get("field", res)`); new
        consumers of raw endpoints must copy that idiom (see clay-api-reference
        "Response envelopes are polymorphic").

        Examples:
            # Text column
            {"type": "text", "name": "Company Name"}

            # Formula column
            {"type": "formula", "name": "Email",
             "typeSettings": {"formulaText": "{{f_abc123}}?.email", "dataTypeSettings": {"type": "email"}}}

            # Action column (enrichment)
            {"type": "action", "name": "Find Work Email",
             "typeSettings": {
               "actionKey": "leadmagic-find-work-email",
               "actionPackageId": "edb58209-a62d-42be-992a-e41b87eeacc2",
               "inputsBinding": [...],
               "authAccountId": "aa_..."
             }}
        """
        body = dict(column_def)
        field_type = body.get("type")
        if field_type == "checkbox":
            body["type"] = "boolean"
            field_type = "boolean"
        if field_type in {"text", "number", "boolean", "url", "date", "email", "image", "select", "multiselect", "phone", "currency", "percent"} and "typeSettings" not in body:
            body["typeSettings"] = _default_type_settings(field_type)
        if view_id:
            body["activeViewId"] = view_id
        res = self.post(f"/tables/{table_id}/fields", body)
        return res.get("field", res)

    def update_column(self, table_id: str, field_id: str, updates: dict) -> dict:
        """Rename or update a column's settings."""
        return self.patch(f"/tables/{table_id}/fields/{field_id}", updates)

    def apply_field_operations(
        self,
        table_id: str,
        operations: list[dict],
        *,
        view_id: str | None = None,
    ) -> list[dict]:
        """
        Apply mixed field operations in order. Supported actions:
        `add`, `rename`, `retype`.

        WARNING (verified 2026-08-06): a `retype` op on a FORMULA column that
        retypes it to `text` is DESTRUCTIVE — the single PATCH clears
        `formulaText` AND wipes the previously computed values from ALL rows.
        No undo; export first if the computed values matter.
        """
        if not isinstance(operations, list):
            raise ValueError("operations must be a list")

        results = []
        current_fields = self.list_fields(table_id, view_id=view_id)
        by_id = {field["field_id"]: field for field in current_fields}
        by_name = {field["name"]: field for field in current_fields}

        def refresh_indexes():
            nonlocal current_fields, by_id, by_name
            current_fields = self.list_fields(table_id, view_id=view_id)
            by_id = {field["field_id"]: field for field in current_fields}
            by_name = {field["name"]: field for field in current_fields}

        for op in operations:
            action = str(op.get("action", "")).lower()
            if action == "add":
                name = op.get("name")
                field_type = "boolean" if op.get("type") == "checkbox" else op.get("type", "text")
                if not name:
                    raise ValueError("add operation requires 'name'")
                if field_type not in _VALID_FIELD_TYPES:
                    raise ValueError(f"Unsupported field type {field_type!r} for add {name!r}")
                type_settings = op.get("type_settings") or op.get("typeSettings")
                created = self.create_column(
                    table_id,
                    {
                        "name": name,
                        "type": field_type,
                        "typeSettings": _default_type_settings(field_type, type_settings),
                    },
                    view_id=view_id,
                )
                results.append({
                    "action": "add",
                    "success": True,
                    "field_id": created.get("id"),
                    "field_name": created.get("name", name),
                    "old_value": None,
                    "new_value": field_type,
                })
                refresh_indexes()
            elif action == "rename":
                target_id = op.get("field_id")
                target_name = op.get("name")
                new_name = op.get("new_name")
                if not new_name:
                    raise ValueError("rename operation requires 'new_name'")
                resolved_id = target_id or (by_name.get(target_name) or {}).get("field_id")
                if not resolved_id or resolved_id not in by_id:
                    raise ValueError(f"Field not found for rename: {target_id or target_name!r}")
                if resolved_id in _SYSTEM_FIELD_IDS:
                    raise ValueError(f"Cannot rename system field {resolved_id}")
                old_name = by_id[resolved_id]["name"]
                updated = self.update_column(table_id, resolved_id, {"name": new_name})
                results.append({
                    "action": "rename",
                    "success": True,
                    "field_id": resolved_id,
                    "field_name": updated.get("name", new_name),
                    "old_value": old_name,
                    "new_value": updated.get("name", new_name),
                })
                refresh_indexes()
            elif action == "retype":
                target_id = op.get("field_id")
                target_name = op.get("name")
                new_type = "boolean" if (op.get("new_type") or op.get("type")) == "checkbox" else (op.get("new_type") or op.get("type"))
                if not new_type:
                    raise ValueError("retype operation requires 'new_type'")
                if new_type not in _VALID_FIELD_TYPES:
                    raise ValueError(f"Unsupported retype target {new_type!r}")
                resolved_id = target_id or (by_name.get(target_name) or {}).get("field_id")
                if not resolved_id or resolved_id not in by_id:
                    raise ValueError(f"Field not found for retype: {target_id or target_name!r}")
                if resolved_id in _SYSTEM_FIELD_IDS:
                    raise ValueError(f"Cannot retype system field {resolved_id}")
                old_type = by_id[resolved_id]["type"]
                type_settings = op.get("type_settings") or op.get("typeSettings")
                self.update_column(
                    table_id,
                    resolved_id,
                    {
                        "type": new_type,
                        "typeSettings": _default_type_settings(new_type, type_settings),
                    },
                )
                results.append({
                    "action": "retype",
                    "success": True,
                    "field_id": resolved_id,
                    "field_name": by_id[resolved_id]["name"],
                    "old_value": old_type,
                    "new_value": new_type,
                })
                refresh_indexes()
            else:
                raise ValueError(f"Unsupported field operation {action!r}")

        return results

    def generate_formula(self, table_id: str, prompt: str, column_name_map: dict = None) -> dict:
        """Ask Clay's AI to generate a formula from a natural language prompt."""
        body = {
            "id": self.user_id,
            "workspaceId": str(self.workspace_id),
            "userPromptInput": prompt,
            "userProvidedCorrectedExamples": [],
            "columnNamesToIds": column_name_map or {},
            "mode": "basic",
            "rawExampleTableData": [],
            "formattedExampleTableData": [],
        }
        return self.post("/ai-generation/formula", body)

    # ── Export ───────────────────────────────────────────────────────────────

    def export_csv(self, table_id: str, view_id: str = None,
                   poll_interval: float = 2.0, timeout: int = 300) -> str:
        """
        Export a table (or view) as CSV and return the S3 download URL.

        Native CSV export: action ("Response") columns export as the literal
        string "Response" — NOT the full enrichment JSON. To get full data,
        either add formula columns (JSON.stringify({{field_id}})) or use
        fetch_all_records_full() instead.

        If view_id is given, only view-filtered rows are exported.
        If view_id is omitted, ALL table rows are exported (ignores filters).

        Returns the signed S3 download URL (valid 24h).
        """
        url = f"/tables/{table_id}/views/{view_id}/export" if view_id else f"/tables/{table_id}/export"
        r = self.session.post(f"https://api.clay.com/v3{url}")
        r.raise_for_status()
        job = r.json()
        job_id = job["id"]
        total = job.get("totalRecordsInViewCount", "?")
        print(f"[clay] export job {job_id} | {total} records")

        start = time.time()
        while time.time() - start < timeout:
            time.sleep(poll_interval)
            sr = self.session.get(f"https://api.clay.com/v3/exports/{job_id}")
            sr.raise_for_status()
            data = sr.json()
            status = data.get("status")
            exported = data.get("recordsExportedCount", 0)
            if status == "FINISHED":
                print(f"[clay] export done — {exported}/{total} rows")
                return data["downloadUrl"]
            elif status == "FAILED":
                raise RuntimeError(f"Export job failed: {data}")

        raise TimeoutError(f"Export job {job_id} did not finish within {timeout}s")

    def export_rows(
        self,
        table_id: str,
        *,
        view_id: str | None = None,
        view_name: str | None = None,
        limit: int = 50000,
        format: str = "csv",
        output_dir: str | None = None,
        filename: str | None = None,
    ) -> dict:
        """
        Export rows from a Clay table to a local CSV or JSON artifact.

        Unlike `export_csv()`, this is a ClayCast-side export that returns both the
        in-memory payload and the absolute local artifact path.
        """
        fmt = str(format).lower()
        if fmt not in {"csv", "json"}:
            raise ValueError("format must be 'csv' or 'json'")
        if limit is not None:
            limit = int(limit)
            if limit < 1:
                raise ValueError("limit must be >= 1")

        raw = self.get_table(table_id, include_extra_data=True)
        table = raw.get("table", raw)
        resolved_view_id = self._resolve_view_id(table_id, view_id=view_id, view_name=view_name)
        rows = self._collect_rows_by_name(table_id, view_id=resolved_view_id, limit=limit)

        ordered_columns = []
        for field in self.list_fields(table_id, view_id=resolved_view_id):
            name = field.get("name")
            if name and name not in ordered_columns:
                ordered_columns.append(name)
        for row in rows:
            for key in row:
                if key != "_record_id" and key not in ordered_columns:
                    ordered_columns.append(key)

        payload_rows = [{k: v for k, v in row.items() if k != "_record_id"} for row in rows]
        field_name_to_id = {
            field.get("name"): field.get("id")
            for field in table.get("fields", []) or []
            if field.get("name") and field.get("id")
        }

        if fmt == "csv":
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=ordered_columns)
            writer.writeheader()
            writer.writerows(payload_rows)
            content = output.getvalue()
            path = self._write_artifact(
                content,
                output_dir=output_dir,
                filename=filename,
                default_stem=f"rows-{table_id}",
                suffix=".csv",
                serializer=lambda value: value,
            )
        else:
            content = {
                "table_name": table.get("name"),
                "table_id": table_id,
                "row_count": len(payload_rows),
                "columns": field_name_to_id,
                "rows": {
                    row["_record_id"]: {k: v for k, v in row.items() if k != "_record_id"}
                    for row in rows
                },
            }
            path = self._write_artifact(
                content,
                output_dir=output_dir,
                filename=filename,
                default_stem=f"rows-{table_id}",
                suffix=".json",
            )

        return {
            "table_id": table_id,
            "table_name": table.get("name"),
            "view_id": resolved_view_id,
            "row_count": len(payload_rows),
            "col_count": len(ordered_columns),
            "column_names": ordered_columns,
            "format": fmt,
            "content": content,
            "path": path,
        }

    # ── Audience export ──────────────────────────────────────────────────────
    # `list_audience_segments`, `count_audience_segment`, and
    # `export_audience_segment` verified live 2026-04-30 against workspace 12345.
    # The Tier A smoke used CONTACT segment `audseg_xxx`; the
    # count/export path returned 0 rows cleanly and wrote a local JSON artifact.

    def export_audience_segment(
        self,
        segment_id: str,
        *,
        entity_type: str | None = None,
        format: str = "csv",
        limit: int | None = None,
        page_size: int = 300,
        include_signals: bool = False,
        include_activities: bool = False,
        include_custom_objects: bool = False,
        output_dir: str | None = None,
        filename: str | None = None,
        workspace_id: int | str | None = None,
    ) -> dict:
        """Export an audience segment to a local CSV or JSON artifact.

        Uses Clay's audience pagination surface directly, which is the only
        path to export segments larger than the UI's 50K table-export cap.

        Which endpoint is paged (/audiences/accounts or /audiences/contacts) comes from the
        fetched segment's own `entityType` (one GET first, no credits). `entity_type` is
        optional; an explicit value that disagrees with the segment raises ValueError after that
        GET, before any row is fetched or a file written. Before this fix (the PR head) it defaulted
        to CONTACT, so an ACCOUNT segment exported without it silently paged the contacts endpoint.
        """
        if entity_type is not None:
            entity_type = str(entity_type).upper()
            if entity_type not in {"CONTACT", "ACCOUNT"}:
                raise ValueError(f"entity_type must be CONTACT or ACCOUNT, got {entity_type!r}")
        fmt = str(format).lower()
        if fmt not in {"csv", "json"}:
            raise ValueError(f"format must be csv or json, got {format!r}")
        if limit is not None:
            limit = int(limit)
            if limit < 1:
                raise ValueError("limit must be >= 1 when provided")
        page_size = int(page_size)
        if page_size < 1 or page_size > 300:
            raise ValueError("page_size must be between 1 and 300 inclusive")

        ws_id = self._resolve_workspace_id(workspace_id)
        segment = self.get_audience_segment(segment_id, workspace_id=ws_id)
        entity_type = _audience_segment_entity("export_audience_segment", "exported", segment_id, segment, entity_type)
        if include_custom_objects and entity_type != "CONTACT":
            raise ValueError("include_custom_objects is CONTACT-only")
        endpoint_seg = "contacts" if entity_type == "CONTACT" else "accounts"
        all_rows: list[dict] = []
        offset = 0

        while True:
            body = {
                "limit": page_size,
                "offset": offset,
                "segmentId": segment_id,
                "includeDeleted": False,
                "isArchived": False,
                "shouldInjectDraftFilter": True,
                "segmentType": None,
            }
            if entity_type == "CONTACT":
                body["includeData"] = {"accountIds": True}
            res = self.post(f"/workspaces/{ws_id}/audiences/{endpoint_seg}", body)
            rows = res.get(endpoint_seg, []) if isinstance(res, dict) else []
            all_rows.extend(rows)
            if limit is not None and len(all_rows) >= limit:
                all_rows = all_rows[:limit]
                break
            if not (res.get("pagination") or {}).get("hasMore"):
                break
            offset += page_size

        if include_signals or include_activities or include_custom_objects:
            for row in all_rows:
                entity_id = (row.get("entity") or {}).get("id")
                if not entity_id:
                    continue
                if include_signals:
                    row["_signals"] = self.get(
                        f"/workspaces/{ws_id}/audiences/entities/{entity_id}/signal-events"
                    )
                if include_activities:
                    row["_activities"] = self.get(
                        f"/workspaces/{ws_id}/audiences/entities/{entity_id}/activities"
                    )
                if include_custom_objects and entity_type == "CONTACT":
                    row["_custom_objects"] = self.get(
                        f"/workspaces/{ws_id}/audiences/contacts/{entity_id}/custom-objects"
                    )

        if fmt == "csv":
            columns, flat_rows = _pivot_audience_rows_to_csv(all_rows)
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=columns)
            writer.writeheader()
            writer.writerows(flat_rows)
            content: str | dict = output.getvalue()
            path = self._write_artifact(
                content,
                output_dir=output_dir,
                filename=filename,
                default_stem=f"audience-{segment_id}",
                suffix=".csv",
                serializer=lambda value: value,
            )
            col_count: int | None = len(columns)
        else:
            content = {
                "segment_id": segment_id,
                "entity_type": entity_type,
                "row_count": len(all_rows),
                "rows": all_rows,
            }
            path = self._write_artifact(
                content,
                output_dir=output_dir,
                filename=filename,
                default_stem=f"audience-{segment_id}",
                suffix=".json",
            )
            col_count = None

        return {
            "segment_id": segment_id,
            "entity_type": entity_type,
            "row_count": len(all_rows),
            "col_count": col_count,
            "format": fmt,
            "content": content,
            "path": path,
        }

    def count_audience_segment(
        self,
        segment_id: str,
        *,
        entity_type: str | None = None,
        workspace_id: int | str | None = None,
    ) -> int:
        """Count rows in an audience segment without fetching them.

        Fixed 2026-09-30: `segmentId` alone does not apply the segment's filter — Clay's count
        endpoint expects the filter in `filters` (the UI keeps it client-side), so the old body
        returned the size of the whole entity. Now delegates to count_audience_records(), which
        fetches the saved `filterAst` and sends both. `entity_type` is optional and defaults to
        the segment's own entityType (an explicit value that disagrees raises ValueError); before
        this fix (the PR head) it defaulted to CONTACT, so an ACCOUNT segment counted without it
        was sent as CONTACT.
        """
        return self.count_audience_records(entity_type, segment_id=segment_id, workspace_id=workspace_id)

    def list_audience_segments(
        self,
        *,
        entity_type: str = "CONTACT",
        workspace_id: int | str | None = None,
    ) -> list[dict]:
        """List audience segments for a workspace."""
        entity_type = str(entity_type).upper()
        if entity_type not in {"CONTACT", "ACCOUNT"}:
            raise ValueError(f"entity_type must be CONTACT or ACCOUNT, got {entity_type!r}")
        ws_id = self._resolve_workspace_id(workspace_id)
        res = self.get(
            f"/workspaces/{ws_id}/audiences/segments",
            params={"entityType": entity_type},
        )
        return res.get("segments", res) if isinstance(res, dict) else res

    # ── Audiences: Salesforce import field mapping ───────────────────────────
    # Captured live with clay_browser.py on 2026-09-29 while adding one field in the
    # Audiences > Salesforce sync settings UI. The UI does two calls: POST /audiences/field
    # (bulk-creates the Audiences field definitions) and PATCH /audiences/salesforce-imports
    # (REPLACES the import's whole field mapping). Neither spends credits; both are
    # workspace-config writes. There is no official CLI/API surface for this mapping.

    # Salesforce field `type` (from /imports/salesforce-fields/{object}) -> Audiences dataType.
    _SF_TO_AUDIENCE_DATA_TYPE = {
        "boolean": "boolean",
        "date": "date",
        "datetime": "date",
        "double": "number",
        "int": "number",
        "currency": "number",
        "percent": "number",
        "email": "email",
        "url": "url",
    }

    def list_audience_imports(
        self,
        *,
        entity_type: str | None = None,
        workspace_id: int | str | None = None,
    ) -> list[dict]:
        """List the Audiences imports (Salesforce object syncs, CPJ imports, …).

        GET /workspaces/{ws}/audiences/imports[?entityType=ACCOUNT|CONTACT]. Each item
        carries `id` (audimp_…), `entityType`, `displayName`, `importSourceType`
        (e.g. SALESFORCE), `importSourceSubtype` (the Salesforce object, e.g. `account`),
        `status`, `importedCount`, `fieldMapping.fieldMappings` (the current mapping)
        and `importMetadata` (appAccountId `aa_…`, sync flags). Verified live
        2026-09-29 against workspace 12345: 3 imports, 2 of them Salesforce.
        """
        ws_id = self._resolve_workspace_id(workspace_id)
        params = {}
        if entity_type:
            entity_type = str(entity_type).upper()
            if entity_type not in {"CONTACT", "ACCOUNT"}:
                raise ValueError(f"entity_type must be CONTACT or ACCOUNT, got {entity_type!r}")
            params["entityType"] = entity_type
        res = self.get(f"/workspaces/{ws_id}/audiences/imports", params=params or None)
        if isinstance(res, dict):
            return res.get("audienceImports") or res.get("imports") or []
        return res

    def get_audience_import_sync_status(
        self,
        import_id: str,
        *,
        source_type: str = "SALESFORCE",
        workspace_id: int | str | None = None,
    ) -> dict:
        """Live sync state of one external-source import (the LAST run, not a log).

        GET /workspaces/{ws}/audiences/imports/external-source-sync-status/{SOURCE}/{import_id}
        -> {importSyncStatus: "success" | "in_progress" | ..., importSyncType:
        "sync_incremental" | "sync_full", lastSyncedTime, numImportRecordsSynced,
        numImportRecordsTotal, exportSyncStatus, lastExportedTime, hasExportInProgress,
        numExportRecordsSynced, numExportRecordsTotal}. The UI polls this every ~5 s after a
        mapping change; Clay's server occasionally answers 500 mid-run — retry, do not treat it
        as a failed sync.

        How to read it (measured 2026-09-30 on a large Salesforce import):
        - `sync_incremental` runs on the workspace's plan cadence (per Clay's docs: every 15 min
          on Enterprise, daily on other paid plans) and only touches records changed in
          Salesforce since the last run. It does NOT fill a newly mapped field on records that
          did not change.
        - `sync_full` is the weekly full re-import, or the backfill Clay starts when a mapped
          field is added — but that backfill is not guaranteed: a field added while a previous
          backfill was still running never got one, and one backfill stopped at ~60 % of the
          records. Measure the fill (count non-null values in the new column) before you build
          on the field; see the reference ("Import sync state, cadence and the backfill caveat").
        - A run has finished when `importSyncStatus == "success"` and `lastSyncedTime` is later
          than your save. `numImportRecordsSynced` / `numImportRecordsTotal` are the import's
          cumulative record totals, not the progress of a run: they were equal while a run was
          still `in_progress` (verified live 2026-10-06). They show neither run progress nor how
          far a newly mapped field is filled.
        - The import's own `status` (from list_audience_imports) flips to PENDING after any
          mapping PATCH and stays there; it does not mean a sync is queued.
        - Nothing triggers a `sync_full` on demand — not this API, not re-saving the mapping,
          not removing and re-adding a field, and the UI has no "sync now" (checked 2026-09-30).
        Verified live 2026-09-29 and 2026-09-30 (the record totals and `exportSyncStatus`:
        2026-10-06).
        """
        ws_id = self._resolve_workspace_id(workspace_id)
        return self.get(
            f"/workspaces/{ws_id}/audiences/imports/external-source-sync-status/"
            f"{source_type.upper()}/{import_id}"
        )

    def get_audience_import_history(
        self,
        entity_type: str,
        *,
        workspace_id: int | str | None = None,
    ) -> list[dict]:
        """Current sync state per external-source import feeding one entity (rows can be missing).

        GET /workspaces/{ws}/audiences/imports/external-source-import-history/{ACCOUNT|CONTACT}
        -> bare list, up to one row per external-source (e.g. Salesforce) import that feeds the
        entity — the object import plus any activity import (`audactimp_…`, which also carries
        `activityTypeId` and is listed under both ACCOUNT and CONTACT):
        [{importId, importSourceType, importSyncStatus, importSyncType, lastSyncedTime,
          numImportRecordsSynced, numImportRecordsTotal, activityTypeId?}, ...].
        Each row reads like get_audience_import_sync_status() for that import, with the same
        caveats. Imports that are not external-source (a Find People / CPJ import) are not
        listed. Despite the name it is the CURRENT state per import, not a run log: on
        2026-09-30 a workspace with weeks of syncs returned two rows (the Salesforce object
        import and the activity import), both `sync_incremental`. Rows can also be MISSING: the
        same ACCOUNT call returned two rows at 06:00Z and an empty list 80 minutes later — after
        two mapping PATCHes on that import — while get_audience_import_sync_status() kept
        answering for it. Treat `[]` as "nothing to show right now", not "no imports";
        list_audience_imports() is the inventory. Use this for an at-a-glance view across
        imports and get_audience_import_sync_status() for one import's full status (it adds the
        export-side fields). The UI calls it from Settings > Audiences > Sources.

        A JSON null body returns []; a dict whose only key holds a list of objects is unwrapped
        (never seen live; an error body of exactly that shape would be unwrapped too); any other
        shape raises ValueError naming it instead of being passed off as rows or as "nothing to
        show". Verified live 2026-09-30 (`lastSyncedTime` on every row, the activity import under both entities and
        the external-source-only listing: 2026-10-06).
        """
        entity_type = str(entity_type).upper()
        if entity_type not in {"CONTACT", "ACCOUNT"}:
            raise ValueError(f"entity_type must be CONTACT or ACCOUNT, got {entity_type!r}")
        ws_id = self._resolve_workspace_id(workspace_id)
        res = self.get(f"/workspaces/{ws_id}/audiences/imports/external-source-import-history/{entity_type}")
        if res is None:
            return []
        if isinstance(res, list):
            return res
        if isinstance(res, dict) and len(res) == 1:
            (rows,) = res.values()
            if isinstance(rows, list) and all(isinstance(r, dict) for r in rows):
                return rows
        shape = f"dict with keys {sorted(res)}" if isinstance(res, dict) else type(res).__name__
        raise ValueError(
            f"get_audience_import_history: unexpected response shape ({shape}); expected a bare "
            f"list of import rows, or a dict whose only key holds that list"
        )

    def list_salesforce_import_fields(
        self,
        object_name: str,
        *,
        auth_account_id: str,
        workspace_id: int | str | None = None,
    ) -> list[dict]:
        """The Salesforce fields Clay can map for one object on one connection.

        GET /workspaces/{ws}/audiences/imports/salesforce-fields/{object}?authAccountId=aa_…
        -> {"fields": [{value (API name), label, type, isUpdateable, externalId,
        isAssociatedObjectField}, …]}. `object_name` is the import's
        `importSourceSubtype` (`account`, `contact`); `auth_account_id` is
        `importMetadata.appAccountId`. Verified live 2026-09-29 (842 mappable fields on a
        large Account object).
        """
        ws_id = self._resolve_workspace_id(workspace_id)
        res = self.get(
            f"/workspaces/{ws_id}/audiences/imports/salesforce-fields/{object_name}",
            params={"authAccountId": auth_account_id},
        )
        return res.get("fields", res) if isinstance(res, dict) else res

    def create_audience_fields(
        self,
        entity_type: str,
        fields: list[dict],
        *,
        workspace_id: int | str | None = None,
    ) -> list[dict]:
        """Bulk-create Audiences field definitions (the "columns" of People / Companies).

        POST /workspaces/{ws}/audiences/field with
        {"entityType": "ACCOUNT"|"CONTACT", "audienceFields": [{"displayName", "fieldType":
        "SCALAR", "dataType": "text"|"number"|"boolean"|"date"|"email"|"url"}, …]}
        -> list of the created fields, each with its `id` (audf_…). Returns an array even
        for one field. Verified live 2026-09-29. No credits.
        """
        entity_type = str(entity_type).upper()
        if entity_type not in {"CONTACT", "ACCOUNT"}:
            raise ValueError(f"entity_type must be CONTACT or ACCOUNT, got {entity_type!r}")
        if not fields:
            return []
        payload = [
            {
                "displayName": f["displayName"],
                "fieldType": f.get("fieldType", "SCALAR"),
                "dataType": f.get("dataType", "text"),
            }
            for f in fields
        ]
        ws_id = self._resolve_workspace_id(workspace_id)
        return self.post(
            f"/workspaces/{ws_id}/audiences/field",
            {"entityType": entity_type, "audienceFields": payload},
        )

    def update_salesforce_import_field_mapping(
        self,
        import_id: str,
        field_mapping: list[dict],
        *,
        entity_type: str,
        is_import_sync_enabled: bool = _REQUIRED_SYNC_FLAG,
        is_export_sync_enabled: bool = _REQUIRED_SYNC_FLAG,
        is_create_new_records_enabled: bool = _REQUIRED_SYNC_FLAG,
        create_new_records_id_mapping: dict | None = _REQUIRED_SYNC_FLAG,
        is_task_sync_enabled: bool = _REQUIRED_SYNC_FLAG,
        sync_flags: dict | None = None,
        reconcile_opportunity_import_dependencies: bool = True,
        workspace_id: int | str | None = None,
    ) -> dict:
        """REPLACE a Salesforce import's field mapping (what the UI's Save button sends).

        PATCH /workspaces/{ws}/audiences/salesforce-imports. `field_mapping` is the COMPLETE
        list — anything you leave out stops syncing — of
        {"type": "SALESFORCE", "audienceFieldId": "audf_…"|built-in id such as "org_name",
        "salesforceFieldId": "<API name>", "mappingRule": "NEVER_WRITE"}; every entry is
        validated (a dict, both ids non-empty) before anything is sent.

        The five sync settings are REQUIRED and have no defaults, because the body REPLACES
        them on the import: pass `is_import_sync_enabled`, `is_export_sync_enabled`,
        `is_create_new_records_enabled`, `create_new_records_id_mapping` (a dict or None —
        None is a real value and must still be passed) and `is_task_sync_enabled` from a fresh
        list_audience_imports() read of `importMetadata`, or pass them together as
        `sync_flags={...}` keyed like importMetadata (the shape AudienceFieldsOrphanedError
        hands back). Omitting them is a TypeError naming the missing kwargs; a boolean flag
        that is None or a string is a ValueError; giving both forms is a ValueError.
        `reconcile_opportunity_import_dependencies` is a request option (captured as true),
        not import state, and keeps its default.

        Shape trap: you send `fieldMapping: [...]`, the response nests it as
        `fieldMapping.fieldMappings`. The response `status` flips to PENDING. Clay usually
        starts a backfill of newly mapped fields (it has taken from minutes to many hours), but
        it is NOT guaranteed: watch the run with get_audience_import_sync_status() and count the
        new field's non-null values before relying on it — see the reference section "Import
        sync state, cadence and the backfill caveat". Prefer add_salesforce_import_fields()
        unless you need to remove mappings. Verified live 2026-09-29. No credits.
        """
        where = "update_salesforce_import_field_mapping"
        given = {
            "is_import_sync_enabled": is_import_sync_enabled,
            "is_export_sync_enabled": is_export_sync_enabled,
            "is_create_new_records_enabled": is_create_new_records_enabled,
            "create_new_records_id_mapping": create_new_records_id_mapping,
            "is_task_sync_enabled": is_task_sync_enabled,
        }
        omitted = [name for name, val in given.items() if val is _REQUIRED_SYNC_FLAG]
        if sync_flags is not None:
            if len(omitted) < len(given):
                raise ValueError(
                    f"{where}: pass either sync_flags= or the five sync-flag kwargs ({', '.join(given)}), not both"
                )
            flags = _check_salesforce_sync_overrides(sync_flags, where=where)
            missing = [k for k in _SF_IMPORT_SYNC_KEYS if k not in flags]
            if missing:
                raise ValueError(
                    f"{where}: sync_flags is missing {', '.join(missing)}; it must carry all five settings "
                    f"({', '.join(_SF_IMPORT_SYNC_KEYS)}) — copy them from the import's importMetadata"
                )
            given = {_SF_IMPORT_SYNC_KWARGS[k]: flags[k] for k in _SF_IMPORT_SYNC_KEYS}
        elif omitted:
            # Same wording as Python's own error for required keyword-only arguments: the five
            # have no usable default (a guess would switch a sync on or off) and sync_flags= is
            # the only alternative to passing them all.
            if len(omitted) == 1:
                listing = repr(omitted[0])
            elif len(omitted) == 2:
                listing = f"{omitted[0]!r} and {omitted[1]!r}"
            else:
                listing = ", ".join(repr(n) for n in omitted[:-1]) + f", and {omitted[-1]!r}"
            raise TypeError(
                f"{where}() missing {len(omitted)} required keyword-only argument{'s' if len(omitted) > 1 else ''}: "
                f"{listing} (pass all five, or sync_flags={{...}} keyed like importMetadata)"
            )
        entity_type = str(entity_type).upper()
        if entity_type not in {"CONTACT", "ACCOUNT"}:
            raise ValueError(f"entity_type must be CONTACT or ACCOUNT, got {entity_type!r}")
        for name in (
            "is_import_sync_enabled",
            "is_export_sync_enabled",
            "is_create_new_records_enabled",
            "is_task_sync_enabled",
        ):
            if not isinstance(given[name], bool):
                raise ValueError(f"{where}: {name} must be True or False, got {given[name]!r}")
        id_mapping = given["create_new_records_id_mapping"]
        if id_mapping is not None and not isinstance(id_mapping, dict):
            raise ValueError(f"{where}: create_new_records_id_mapping must be a dict or None, got {id_mapping!r}")
        _validate_salesforce_field_mapping(field_mapping, where=f"{where}: field_mapping")
        ws_id = self._resolve_workspace_id(workspace_id)
        body = {
            "audienceImports": [
                {
                    "audienceImportId": import_id,
                    "fieldMapping": [
                        {
                            "type": m.get("type", "SALESFORCE"),
                            "audienceFieldId": m["audienceFieldId"],
                            "salesforceFieldId": m["salesforceFieldId"],
                            "mappingRule": m.get("mappingRule", "NEVER_WRITE"),
                        }
                        for m in field_mapping
                    ],
                    "isImportSyncEnabled": given["is_import_sync_enabled"],
                    "isExportSyncEnabled": given["is_export_sync_enabled"],
                    "isCreateNewRecordsEnabled": given["is_create_new_records_enabled"],
                    "createNewRecordsIdMapping": id_mapping,
                    "entityType": entity_type,
                    "isTaskSyncEnabled": given["is_task_sync_enabled"],
                }
            ],
            "reconcileOpportunityImportDependencies": reconcile_opportunity_import_dependencies,
        }
        return self.patch(f"/workspaces/{ws_id}/audiences/salesforce-imports", body)

    def _read_salesforce_import_mapping(
        self, import_id: str, *, workspace_id: int | str | None = None
    ) -> list[dict] | None:
        """One GET of the imports list -> the import's current `fieldMapping.fieldMappings`, or
        None when the import is not there or its mapping is malformed. Network errors propagate.
        Used by add_salesforce_import_fields() before its PATCH (drift check) and after a failed
        one (did the REPLACE land?)."""
        imports = self.list_audience_imports(workspace_id=workspace_id)
        imp = next((i for i in imports if isinstance(i, dict) and i.get("id") == import_id), None)
        if imp is None:
            return None
        try:
            entries = _salesforce_import_current_mapping(imp, where="add_salesforce_import_fields")
            _validate_salesforce_field_mapping(
                entries, where="add_salesforce_import_fields: re-read mapping entry fieldMappings"
            )
        except ValueError:
            return None
        return entries

    def add_salesforce_import_fields(
        self,
        import_id: str,
        new_fields: list[dict],
        *,
        sync_flags: dict | None = None,
        dry_run: bool = False,
        workspace_id: int | str | None = None,
    ) -> dict:
        """Map more Salesforce fields into an existing Audiences import — the safe way.

        `new_fields` = [{"salesforceFieldId": "Account_Owner_Manager__c",
        "displayName": "Account Owner - Manager", "dataType": "text"?}, …]. Returns
        {"import": <import>, "created_fields": [...], "skipped": [salesforceFieldId, …],
        "to_create": [{salesforceFieldId, displayName, dataType}, …], "dry_run": bool}.

        Order of operations — everything is validated before the first write, like
        upsert_records() / bulk_update_records():

        1. Network-free: `new_fields` must be a non-empty list of dicts with a
           salesforceFieldId; repeats collapse to one field (first-seen order; repeats that
           disagree on displayName or dataType raise ValueError); `sync_flags`, if given, must
           be a dict keyed like importMetadata with known keys and well-typed values.
        2. GET imports: the import must exist, be a Salesforce import, have entityType
           ACCOUNT/CONTACT, an importSourceSubtype (the Salesforce object) and
           importMetadata.appAccountId (the connection). Its five sync settings are read from
           importMetadata and never defaulted: a missing or null one raises ValueError naming
           it and the exact `sync_flags={...}` remedy, because the mapping PATCH replaces them
           and a guess could switch a sync on or off. (isTaskSyncEnabled was absent from a live
           CONTACT import's importMetadata on 2026-09-29 while the ACCOUNT import had all
           five; the UI's replay sent False.) Every EXISTING mapping entry is validated too — an
           incomplete pair fails the call instead of being dropped, since the PATCH replaces the
           whole list.
        3. GET the field catalog: every new API name must be mappable; an API name already
           mapped is skipped and reported once in `skipped`; `dataType` is inferred from the
           Salesforce type when not given (boolean/date/number/email/url, else text) and
           `displayName` defaults to the Salesforce label. The new fields' displayNames must be
           distinct — the create response is paired to the request by position and checked by
           displayName. Nothing to create, or `dry_run=True`: return the plan (`to_create`)
           with no writes.
        4. WRITE 1 — create_audience_fields(). The response must be a list of one dict per
           requested field, in order, each with an id and (when echoed) the requested
           displayName; otherwise AudienceFieldsOrphanedError with `pairing_verified=False` and
           `mapping=None`: the fields exist and must not be mapped by position. (A server-side
           rename of a displayName would trip this check too; the fields still exist.)
        5. Re-read the import and compare its mapping with the one read in step 2 — one
           zero-credit GET that closes the GET→PATCH replace race. If it changed underneath,
           AudienceFieldsOrphanedError with `pairing_verified=True` and `mapping` = the fresh
           pairs + the new ones; the PATCH is not sent.
        6. WRITE 2 — the mapping PATCH via update_salesforce_import_field_mapping() with the
           five settings from step 2. If it raises, AudienceFieldsOrphanedError carries the
           exact `mapping` to re-send (idempotent: the PATCH is a full REPLACE) plus
           `mapped_after_failure` from a best-effort re-read. A requests.HTTPError means Clay
           rejected the request; anything else (connection reset, non-JSON 2xx) means it may
           have been applied. Either way the message states what the re-read found — mapped
           (nothing to re-send, do not delete), not mapped, or unknown when the re-read failed
           — rather than inferring the state from the exception type.

        The two writes are not atomic and claycast has no delete for Audiences fields (the
        endpoint was never captured): orphans go through the official CLI
        `clay audiences fields delete <audf_id> --entity-type people|companies` (soft,
        idempotent) or the UI. Verified live 2026-09-29: two fields added to a large Account
        import; that time the backfill started immediately. A backfill is not guaranteed,
        though: check with get_audience_import_sync_status() and count the new fields'
        non-null values before relying on them (reference: "Import sync state, cadence and the
        backfill caveat"). No credits.
        """
        where = "add_salesforce_import_fields"
        # 1. network-free
        to_check = _dedupe_new_salesforce_fields(new_fields)
        overrides = _check_salesforce_sync_overrides(sync_flags, where=where)
        ws_id = self._resolve_workspace_id(workspace_id)

        # 2. the import
        imports = self.list_audience_imports(workspace_id=ws_id)
        imp = next((i for i in imports if isinstance(i, dict) and i.get("id") == import_id), None)
        if imp is None:
            raise ValueError(f"{where}: import {import_id!r} not found in workspace {ws_id}")
        meta = imp.get("importMetadata") or {}
        if not isinstance(meta, dict):
            raise ValueError(f"{where}: import {import_id!r} importMetadata is not an object ({type(meta).__name__})")
        source_type = imp.get("importSourceType") or meta.get("type")
        if source_type != "SALESFORCE":
            raise ValueError(
                f"{where}: import {import_id!r} is not a Salesforce import (importSourceType={source_type!r})"
            )
        entity_type = str(imp.get("entityType") or "").upper()
        if entity_type not in {"CONTACT", "ACCOUNT"}:
            raise ValueError(
                f"{where}: import {import_id!r} has entityType {imp.get('entityType')!r}; expected ACCOUNT or CONTACT"
            )
        object_name = imp.get("importSourceSubtype")
        if not object_name:
            raise ValueError(
                f"{where}: import {import_id!r} has no importSourceSubtype (the Salesforce object name); "
                f"cannot load its field catalog"
            )
        auth_account_id = meta.get("appAccountId")
        if not auth_account_id:
            raise ValueError(
                f"{where}: import {import_id!r} importMetadata has no appAccountId (the Salesforce connection "
                f"aa_...); cannot load its field catalog"
            )
        flags = _salesforce_import_sync_flags(import_id, meta, overrides, where=where)
        current = _salesforce_import_current_mapping(imp, where=where)
        _validate_salesforce_field_mapping(current, where=f"{where}: existing mapping entry fieldMappings")
        already = {m["salesforceFieldId"] for m in current}

        # 3. the catalog and the plan
        catalog = {
            f["value"]: f
            for f in self.list_salesforce_import_fields(object_name, auth_account_id=auth_account_id, workspace_id=ws_id)
        }
        to_create, skipped = [], []
        for nf in to_check:
            sf_id = nf["salesforceFieldId"]
            if sf_id in already:
                skipped.append(sf_id)
                continue
            if sf_id not in catalog:
                raise ValueError(
                    f"{where}: {sf_id!r} is not a mappable field on this Salesforce connection "
                    f"(object {object_name!r}, connection {auth_account_id!r})"
                )
            sf_type = catalog[sf_id].get("type", "")
            to_create.append(
                {
                    "salesforceFieldId": sf_id,
                    "displayName": nf.get("displayName") or catalog[sf_id].get("label") or sf_id,
                    "dataType": nf.get("dataType") or self._SF_TO_AUDIENCE_DATA_TYPE.get(sf_type, "text"),
                }
            )
        names = [c["displayName"] for c in to_create]
        shared = sorted({n for n in names if names.count(n) > 1})
        if shared:
            owners = ", ".join(c["salesforceFieldId"] for c in to_create if c["displayName"] == shared[0])
            raise ValueError(
                f"{where}: two new fields would share displayName {shared[0]!r} ({owners}); give distinct "
                f"displayName values so the created ids can be paired with the request"
            )
        if not to_create or dry_run:
            return {"import": imp, "created_fields": [], "skipped": skipped, "to_create": to_create, "dry_run": dry_run}

        # 4. first write — create the fields, then prove the response pairs with the request
        created = self.create_audience_fields(
            entity_type,
            [{"displayName": c["displayName"], "dataType": c["dataType"]} for c in to_create],
            workspace_id=ws_id,
        )
        delete_text = _audience_fields_delete_text(entity_type, ws_id)
        unpaired: dict[str, Any] = dict(
            import_id=import_id, entity_type=entity_type, workspace_id=ws_id, mapping=None,
            pairing_verified=False, sync_flags=flags,
        )
        unpaired_text = (
            "The created fields exist unmapped and cannot be paired with the request, so err.mapping is None: "
            "map them by hand after checking each field's displayName in Audiences > Settings, or delete them "
            f"{delete_text}"
        )
        if not isinstance(created, list) or not all(isinstance(f, dict) for f in created):
            raise AudienceFieldsOrphanedError(
                f"{where}: create_audience_fields returned an unexpected shape ({repr(created)[:200]}) on import "
                f"{import_id!r}; the mapping PATCH was not sent. {unpaired_text}",
                created_fields=[],
                **unpaired,
            )
        ids = ", ".join(str(f.get("id")) for f in created) or "(no ids returned)"
        if len(created) != len(to_create):
            raise AudienceFieldsOrphanedError(
                f"{where}: expected {len(to_create)} created fields, got {len(created)} ({ids}) on import "
                f"{import_id!r}; the mapping PATCH was not sent. {unpaired_text}",
                created_fields=created,
                **unpaired,
            )
        for spec, field in zip(to_create, created):
            got = field.get("displayName")
            if not field.get("id"):
                bad = f"a created field has no id: {field!r}"
            elif got is None and len(created) > 1:
                bad = f"the response does not echo displayName, so {len(created)} created ids cannot be paired"
            elif got is not None and got != spec["displayName"]:
                bad = f"expected displayName {spec['displayName']!r}, got {got!r} with id {field.get('id')!r}"
            else:
                continue
            raise AudienceFieldsOrphanedError(
                f"{where}: created fields ({ids}) do not pair with the request ({bad}) on import {import_id!r}; "
                f"the mapping PATCH was not sent. {unpaired_text}",
                created_fields=created,
                **unpaired,
            )
        new_pairs = [
            {
                "type": "SALESFORCE",
                "audienceFieldId": field["id"],
                "salesforceFieldId": spec["salesforceFieldId"],
                "mappingRule": "NEVER_WRITE",
            }
            for spec, field in zip(to_create, created)
        ]
        verified: dict[str, Any] = dict(
            import_id=import_id, entity_type=entity_type, workspace_id=ws_id, created_fields=created,
            pairing_verified=True, sync_flags=flags,
        )
        resend = (
            "Re-send the mapping (idempotent — the PATCH is a full REPLACE): "
            "clay.update_salesforce_import_field_mapping(err.import_id, err.mapping, entity_type=err.entity_type, "
            "**err.sync_flag_kwargs())."
        )

        # 5. re-read: the PATCH replaces the whole list, so a pair added meanwhile would be dropped
        try:
            fresh = self._read_salesforce_import_mapping(import_id, workspace_id=ws_id)
        except Exception as exc:
            raise AudienceFieldsOrphanedError(
                f"{where}: created {len(created)} Audiences field(s) {ids} on import {import_id!r}, but re-reading the "
                f"import before the mapping PATCH failed ({type(exc).__name__}: {exc}); the PATCH was not sent. "
                f"err.mapping is the mapping read at the start plus the new pairs: confirm with list_audience_imports() "
                f"that the import's fieldMappings still match it, then re-send it. {resend} Delete the fields instead "
                f"only {delete_text}",
                mapping=current + new_pairs,
                **verified,
            ) from exc
        if fresh is None:
            raise AudienceFieldsOrphanedError(
                f"{where}: created {len(created)} Audiences field(s) {ids}, but import {import_id!r} was not found (or "
                f"its mapping is malformed) when re-read before the mapping PATCH; the PATCH was not sent and err.mapping "
                f"is None. Check the import with list_audience_imports() before mapping or deleting anything; delete "
                f"only {delete_text}",
                mapping=None,
                **verified,
            )
        if _salesforce_mapping_signature(fresh) != _salesforce_mapping_signature(current):
            raise AudienceFieldsOrphanedError(
                f"{where}: created {len(created)} Audiences field(s) {ids}, but import {import_id!r}'s mapping changed "
                f"between the first read and the PATCH ({len(current)} -> {len(fresh)} pair(s)); the PATCH was not sent, "
                f"so the concurrent change is not overwritten. err.mapping is the fresh mapping plus the new pairs. "
                f"{resend} Delete the fields instead only {delete_text}",
                mapping=fresh + new_pairs,
                **verified,
            )
        mapping = current + new_pairs

        # 6. second write — the PATCH
        try:
            res = self.update_salesforce_import_field_mapping(
                import_id, mapping, entity_type=entity_type, sync_flags=flags, workspace_id=ws_id
            )
        except Exception as exc:
            import requests as requests_module

            rejected = isinstance(exc, requests_module.HTTPError)
            try:
                after = self._read_salesforce_import_mapping(import_id, workspace_id=ws_id)
            except Exception:
                after = None
            mapped_after = None if after is None else all(
                any(isinstance(m, dict) and m.get("audienceFieldId") == p["audienceFieldId"] for m in after)
                for p in new_pairs
            )
            head = (
                f"{where}: created {len(created)} Audiences field(s) {ids} on import {import_id!r} but the mapping "
                f"PATCH failed ({type(exc).__name__}: {exc})"
            )
            # The state clause follows the post-failure re-read, not the exception type: a rejected
            # PATCH whose mapping is nevertheless present (the server applied it, or someone else
            # mapped the fields meanwhile) must not be described as "NOT mapped"
            if mapped_after is True:
                state = (
                    (": the request was rejected but" if rejected else " after it was sent, but")
                    + " the mapping is present (the server applied it or someone else mapped the fields) — "
                    "nothing to re-send, do NOT delete"
                )
            elif mapped_after is False:
                state = (
                    ": Clay rejected the request, so the fields exist and are NOT mapped" if rejected
                    else " after it was sent, and the fields exist and are NOT mapped"
                )
            elif rejected:
                state = ": Clay rejected the request, but the outcome is unknown"
            else:
                state = " after it was sent, so its outcome is UNKNOWN — the server may have applied the REPLACE"
            reread = {
                True: " (a re-read of the import shows the created fields ARE mapped)",
                False: " (a re-read of the import shows them NOT mapped)",
                None: " (the import could not be re-read to confirm)",
            }[mapped_after]
            if mapped_after is True:
                advice = " Verify the mapping in Audiences > Settings."
            elif rejected and mapped_after is False:
                advice = f" {resend} If you would rather not map them, delete the orphans {delete_text}"
            else:
                advice = (
                    f" {resend} Do not delete the fields unless list_audience_imports() confirms they are unmapped "
                    f"(soft-deleting fields the import is mapped to is the worst outcome); if it does and you drop "
                    f"them, do so only {delete_text}"
                )
            raise AudienceFieldsOrphanedError(
                head + state + reread + "." + advice,
                mapping=mapping,
                mapped_after_failure=mapped_after,
                **verified,
            ) from exc
        updated = (res.get("audienceImports") or [res])[0] if isinstance(res, dict) else res
        return {"import": updated, "created_fields": created, "skipped": skipped, "to_create": to_create, "dry_run": False}

    # ── Audiences: segments (saved filters) and ad-hoc counts ────────────────
    # Captured live with clay_browser.py 2026-09-30 while creating a segment in the UI and
    # replayed via the SDK (workspace 12345). No credits; workspace-config writes. Build the
    # filter with the af_* helpers above.

    def get_audience_segment(self, segment_id: str, *, workspace_id: int | str | None = None) -> dict:
        """GET /workspaces/{ws}/audiences/segments/{id} -> {id, name, description, filterAst,
        entityType, estimatedSize, ownerId, order, createdAt, updatedAt, deletedAt, ...}.
        `estimatedSize` is Clay's cached member count. Verified live 2026-09-30."""
        ws_id = self._resolve_workspace_id(workspace_id)
        return self.get(f"/workspaces/{ws_id}/audiences/segments/{segment_id}")

    def create_audience_segment(
        self,
        entity_type: str,
        name: str,
        filter_ast: dict | None = None,
        *,
        workspace_id: int | str | None = None,
    ) -> dict:
        """Create a saved segment. POST /workspaces/{ws}/audiences/segments with
        {"name", "filterAst": <root GroupOp>, "entityType": "ACCOUNT"|"CONTACT"} -> the segment
        object (see get_audience_segment). An empty filter (the default) matches every record —
        that is exactly what the UI's "Create segment" button sends. Set a description with
        update_audience_segment(). Verified live 2026-09-30. No credits.
        """
        et = _af_entity(entity_type)
        ws_id = self._resolve_workspace_id(workspace_id)
        body = {"name": name, "filterAst": af_root(filter_ast if filter_ast is not None else af_and()), "entityType": et}
        return self.post(f"/workspaces/{ws_id}/audiences/segments", body)

    def update_audience_segment(
        self,
        segment_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        filter_ast: dict | None = None,
        workspace_id: int | str | None = None,
    ) -> dict:
        """Rename, describe or re-filter a saved segment. PUT /workspaces/{ws}/audiences/segments/{id}
        with a PARTIAL body — only the keys you pass change (the UI's Rename sends just {"name"}).
        Returns the full segment object. PATCH and DELETE on this URL are 404 (NoMatchingURL).
        Verified live 2026-09-30 (UI capture for name; SDK for description and filterAst). No credits.
        """
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description
        if filter_ast is not None:
            body["filterAst"] = af_root(filter_ast)
        if not body:
            raise ValueError("pass at least one of name, description, filter_ast")
        ws_id = self._resolve_workspace_id(workspace_id)
        r = self.session.put(self._url(f"/workspaces/{ws_id}/audiences/segments/{segment_id}"), json=body)
        r.raise_for_status()
        return r.json()

    def delete_audience_segment(self, segment_id: str, *, workspace_id: int | str | None = None) -> dict:
        """Delete a saved segment (the UI's "Delete segment"). POST
        /workspaces/{ws}/audiences/segments/{id}/delete -> {"success": true, "segmentId": ...}.
        Verified live 2026-09-30 (UI capture and SDK). HARD delete: afterwards GET returns 404
        "Segment not found" and the segment is gone from the list — there is no undo, so confirm
        before calling it on anything you did not create. (Whether the official CLI's `archive`
        maps to this or to a soft archive was not verified.) No credits.
        """
        ws_id = self._resolve_workspace_id(workspace_id)
        return self.post(f"/workspaces/{ws_id}/audiences/segments/{segment_id}/delete", {})

    def count_audience_records(
        self,
        entity_type: str | None = None,
        *,
        filter_ast: dict | None = None,
        segment_id: str | None = None,
        archived: bool = False,
        workspace_id: int | str | None = None,
    ) -> int:
        """Server-side count of people/companies matching an ad-hoc filter AST, a saved segment,
        or (neither) the whole entity. POST /workspaces/{ws}/audiences/count with
        {"entityType", "isArchived", "shouldInjectDraftFilter": true, "segmentType": null} plus
        "filters": <AST> or "segmentId" + "filters". This is what the segment editor calls while
        you edit, so an ad-hoc count equals the segment the same AST would save. Verified live
        2026-09-30.

        With `segment_id`, `entity_type` is optional: it is read from the fetched segment's own
        `entityType`, and an explicit value that disagrees raises ValueError after that GET
        (before the count). A whole-entity or ad-hoc (`filter_ast`) count still requires
        `entity_type`. Only ACCOUNT and CONTACT segments can be counted here.
        """
        if filter_ast is not None and segment_id is not None:
            raise ValueError("count_audience_records: pass filter_ast or segment_id, not both")
        if entity_type is None and segment_id is None:
            raise ValueError("count_audience_records: entity_type is required unless segment_id is given")
        et = _af_entity(entity_type) if entity_type is not None else None
        ws_id = self._resolve_workspace_id(workspace_id)
        if segment_id is not None:
            # `segmentId` alone does NOT apply the segment's filter — the UI keeps the filter
            # client-side and sends it as `filters` (verified 2026-09-30: segmentId-only returned the
            # whole entity). Fetch the saved AST and send both; the same object says which entity
            # the segment belongs to, so an ACCOUNT segment is no longer counted as CONTACT.
            segment = self.get_audience_segment(segment_id, workspace_id=ws_id)
            et = _audience_segment_entity("count_audience_records", "counted", segment_id, segment, et)
            filter_ast = segment.get("filterAst") or af_and()
        body = {"entityType": et, "isArchived": archived, "shouldInjectDraftFilter": True, "segmentType": None}
        if segment_id is not None:
            body["segmentId"] = segment_id
        if filter_ast is not None:
            body["filters"] = af_root(filter_ast)
        res = self.post(f"/workspaces/{ws_id}/audiences/count", body)
        return int(res.get("count", 0))

    def count_audience_filter_stages(
        self,
        entity_type: str,
        stages: list[dict],
        *,
        workspace_id: int | str | None = None,
    ) -> list[dict]:
        """Count a filter one rule at a time: stage k counts And(stages[0..k]). Returns
        [{"stage": k, "count": n}, ...] — the funnel that shows what each rule removes. Use it
        before saving a segment so a rule that removes nothing (a value that does not exist, a
        dead predicate) is caught. Each stage is one count call."""
        out = []
        for k in range(len(stages)):
            out.append({"stage": k + 1, "count": self.count_audience_records(entity_type, filter_ast=af_and(*stages[: k + 1]), workspace_id=workspace_id)})
        return out

    def verify_audience_filter_complement(
        self,
        entity_type: str,
        exclusion_ast: dict,
        inclusion_ast: dict,
        *,
        workspace_id: int | str | None = None,
    ) -> dict:
        """Prove two filters partition the entity: total == excluded + included and nothing is in
        both. Returns {total, excluded, included, both, neither, exact}. `neither` > 0 with
        `both` == 0 usually means a field is being back-filled between counts (re-run) or that
        records lack the related object the rules test (people with no linked company)."""
        total = self.count_audience_records(entity_type, workspace_id=workspace_id)
        excluded = self.count_audience_records(entity_type, filter_ast=exclusion_ast, workspace_id=workspace_id)
        included = self.count_audience_records(entity_type, filter_ast=inclusion_ast, workspace_id=workspace_id)
        both = self.count_audience_records(entity_type, filter_ast=af_and(exclusion_ast, inclusion_ast), workspace_id=workspace_id)
        neither = total - excluded - included + both
        return {"total": total, "excluded": excluded, "included": included, "both": both, "neither": neither, "exact": both == 0 and neither == 0}

    def search_export_artifacts(
        self,
        sources: list[str],
        *,
        row_search: str | None = None,
        header_name_search: str | None = None,
        header_based_column_search: list[str] | None = None,
        max_row_results: int | None = None,
        case_sensitive: bool = False,
        fuzzy_header_name_match: bool = False,
        fuzzy_header_name_threshold: float = 0.65,
        fetch_timeout_seconds: int = 30,
        max_workers: int = 8,
    ) -> dict:
        """Search previously exported JSON artifacts from local paths or explicit URLs."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        if not isinstance(sources, list) or not sources:
            raise ValueError("sources must be a non-empty list")
        if fetch_timeout_seconds < 1:
            raise ValueError("fetch_timeout_seconds must be >= 1")
        if max_workers < 1:
            raise ValueError("max_workers must be >= 1")

        def similarity_ratio(a, b):
            if not a or not b:
                return 0.0
            a, b = str(a), str(b)
            if a == b:
                return 1.0
            shorter = a if len(a) < len(b) else b
            longer = b if len(a) < len(b) else a
            if shorter in longer:
                return len(shorter) / len(longer)
            min_len = min(len(a), len(b))
            for i in range(min_len, 2, -1):
                if a[:i] == b[:i] or a[-i:] == b[-i:]:
                    return i / max(len(a), len(b))
            return 0.0

        def check_match(search_term, value, *, fuzzy=False, threshold=0.0):
            if value is None:
                return False, 0.0
            search_str = str(search_term)
            value_str = str(value)
            if not case_sensitive:
                search_str = search_str.lower()
                value_str = value_str.lower()
            if search_str in value_str:
                return True, 1.0
            if fuzzy:
                score = similarity_ratio(search_str, value_str)
                if score >= threshold:
                    return True, score
            return False, 0.0

        def fetch_source(source: str, idx: int) -> dict:
            result = {"fetch_ok": False, "fetch_url": source, "url_index": idx}
            lowered = source.lower()
            if lowered.endswith(".csv"):
                result["fetch_error"] = "CSV not supported. Use JSON export."
                return result
            try:
                parsed = json.loads(self._read_export_source(source, timeout_seconds=fetch_timeout_seconds))
            except Exception as exc:
                result["fetch_error"] = str(exc)
                return result

            def infer_columns(rows):
                ordered = []
                for row in rows:
                    if isinstance(row, dict) and row:
                        for key in row.keys():
                            if key not in ordered:
                                ordered.append(key)
                return ordered

            rows_list = []
            cols_list = []
            rec_ids = {}
            table_name = None
            table_id = None
            if isinstance(parsed, dict) and "rows" in parsed:
                table_name = parsed.get("table_name")
                table_id = parsed.get("table_id")
                explicit_columns = parsed.get("columns")
                if isinstance(explicit_columns, dict):
                    cols_list = list(explicit_columns.keys())
                elif isinstance(explicit_columns, list):
                    cols_list = [str(column) for column in explicit_columns]
                rows_obj = parsed.get("rows", {})
                if isinstance(rows_obj, dict):
                    for offset, (record_id, row) in enumerate(rows_obj.items()):
                        rows_list.append(row)
                        rec_ids[offset] = record_id
                    if not cols_list:
                        cols_list = infer_columns(rows_list)
                elif isinstance(rows_obj, list):
                    rows_list = rows_obj
                    if not cols_list:
                        cols_list = infer_columns(rows_list)
            elif isinstance(parsed, list):
                rows_list = parsed
                cols_list = infer_columns(rows_list)
            elif isinstance(parsed, dict):
                rows_list = [parsed]
                cols_list = list(parsed.keys())
            else:
                result["fetch_error"] = f"Unsupported JSON payload type: {type(parsed).__name__}"
                return result

            result.update({
                "fetch_ok": True,
                "tbl_name": table_name,
                "tbl_id": table_id,
                "rows_list": rows_list,
                "cols_list": cols_list,
                "rec_ids": rec_ids,
            })
            return result

        warnings_list = []
        unique_sources = list(dict.fromkeys(sources))
        if len(unique_sources) < len(sources):
            warnings_list.append(f"Removed {len(sources) - len(unique_sources)} duplicate URL(s)")
        if fuzzy_header_name_threshold != 0.65 and not fuzzy_header_name_match:
            warnings_list.append("fuzzy_header_name_threshold ignored when fuzzy_header_name_match is false")
        if header_based_column_search:
            if header_name_search:
                warnings_list.append("header_name_search ignored when header_based_column_search is used")
            if fuzzy_header_name_match:
                warnings_list.append("fuzzy_header_name_match ignored when header_based_column_search is used")
        if max_row_results and header_name_search and not row_search and not header_based_column_search:
            warnings_list.append("max_row_results has no effect in header_search mode")

        use_parallel = len(unique_sources) >= 3
        worker_count = 1 if not use_parallel else min(len(unique_sources), max_workers, 8)
        fetched_list = [None] * len(unique_sources)
        if use_parallel:
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                future_map = {executor.submit(fetch_source, source, idx): idx for idx, source in enumerate(unique_sources)}
                for future in as_completed(future_map):
                    idx = future_map[future]
                    fetched_list[idx] = future.result()
        else:
            for idx, source in enumerate(unique_sources):
                fetched_list[idx] = fetch_source(source, idx)

        sources_list = []
        all_not_found_cols = []
        requested_columns = header_based_column_search or []
        for fetched in fetched_list:
            src = {"url": fetched["fetch_url"], "url_index": fetched["url_index"]}
            if fetched["fetch_ok"]:
                src.update({
                    "status": "ok",
                    "error": None,
                    "content_type": "json",
                    "table_name": fetched.get("tbl_name"),
                    "table_id": fetched.get("tbl_id"),
                    "total_rows": len(fetched.get("rows_list", [])),
                    "total_columns": len(fetched.get("cols_list", [])),
                    "columns": fetched.get("cols_list", []),
                })
                if requested_columns:
                    cols_set = {
                        column if case_sensitive else str(column).lower()
                        for column in fetched.get("cols_list", [])
                    }
                    found = []
                    not_found = []
                    for requested in requested_columns:
                        key = requested if case_sensitive else requested.lower()
                        if key in cols_set:
                            found.append(requested)
                        else:
                            not_found.append(requested)
                    src["columns_requested"] = requested_columns
                    src["columns_found"] = found
                    src["columns_not_found"] = not_found
                    all_not_found_cols.extend(not_found)
                else:
                    src["columns_requested"] = None
                    src["columns_found"] = None
                    src["columns_not_found"] = None
            else:
                src.update({
                    "status": "error",
                    "error": fetched.get("fetch_error"),
                    "content_type": None,
                    "table_name": None,
                    "table_id": None,
                    "total_rows": 0,
                    "total_columns": 0,
                    "columns": [],
                    "columns_requested": None,
                    "columns_found": None,
                    "columns_not_found": None,
                })
            sources_list.append(src)

        if all_not_found_cols:
            unique_not_found = list(dict.fromkeys(all_not_found_cols))
            warnings_list.append(f"{len(unique_not_found)} column(s) not found: {unique_not_found}")

        if requested_columns and row_search:
            mode = "row_column_extract"
        elif requested_columns:
            mode = "column_extract"
        elif row_search and header_name_search:
            mode = "combined"
        elif row_search:
            mode = "row_search"
        elif header_name_search:
            mode = "header_search"
        else:
            mode = "preview"

        rows_out = []
        header_matches = []
        total_scanned = 0
        for fetched in fetched_list:
            if not fetched["fetch_ok"]:
                continue
            rows_data = fetched.get("rows_list", [])
            rec_ids = fetched.get("rec_ids", {})
            cols = fetched.get("cols_list", [])
            source_url = fetched["fetch_url"]
            source_index = fetched["url_index"]
            total_scanned += len(rows_data)

            if mode in {"row_search", "combined", "row_column_extract"}:
                found_count = 0
                for row_index, row in enumerate(rows_data):
                    if max_row_results and found_count >= max_row_results:
                        break
                    for field_name, field_value in (row or {}).items():
                        matched, score = check_match(row_search, field_value)
                        if matched:
                            data = row
                            if mode == "row_column_extract":
                                column_map = {}
                                for requested in requested_columns:
                                    for actual in cols:
                                        same = requested == actual if case_sensitive else requested.lower() == str(actual).lower()
                                        if same:
                                            column_map[requested] = actual
                                            break
                                data = {requested: row.get(column_map.get(requested)) for requested in requested_columns}
                            rows_out.append({
                                "source_index": source_index,
                                "source_url": source_url,
                                "record_id": rec_ids.get(row_index),
                                "row_index": row_index,
                                "match_info": {
                                    "matched_field": field_name,
                                    "matched_value": str(field_value)[:500],
                                    "match_type": "exact",
                                    "similarity": score,
                                },
                                "data": data,
                            })
                            found_count += 1
                            break

            if mode in {"header_search", "combined"}:
                for column_name in cols:
                    matched, score = check_match(
                        header_name_search,
                        column_name,
                        fuzzy=fuzzy_header_name_match,
                        threshold=fuzzy_header_name_threshold,
                    )
                    if matched:
                        header_matches.append({
                            "source_index": source_index,
                            "source_url": source_url,
                            "column_name": column_name,
                            "match_type": "exact" if score >= 1.0 else "fuzzy",
                            "similarity": None if score >= 1.0 else round(score, 3),
                        })

            if mode == "column_extract":
                column_map = {}
                for requested in requested_columns:
                    for actual in cols:
                        same = requested == actual if case_sensitive else requested.lower() == str(actual).lower()
                        if same:
                            column_map[requested] = actual
                            break
                limit = min(max_row_results, len(rows_data)) if max_row_results else len(rows_data)
                for row_index in range(limit):
                    row = rows_data[row_index]
                    rows_out.append({
                        "source_index": source_index,
                        "source_url": source_url,
                        "record_id": rec_ids.get(row_index),
                        "row_index": row_index,
                        "match_info": None,
                        "data": {requested: row.get(column_map.get(requested)) for requested in requested_columns},
                    })

            if mode == "preview":
                limit = min(max_row_results, len(rows_data)) if max_row_results else len(rows_data)
                for row_index in range(limit):
                    rows_out.append({
                        "source_index": source_index,
                        "source_url": source_url,
                        "record_id": rec_ids.get(row_index),
                        "row_index": row_index,
                        "match_info": None,
                        "data": rows_data[row_index],
                    })

        ok_count = sum(1 for source in sources_list if source["status"] == "ok")
        return {
            "success": ok_count > 0 or len(unique_sources) == 0,
            "mode": mode,
            "warnings": warnings_list,
            "sources": sources_list,
            "rows": rows_out,
            "header_matches": header_matches,
            "stats": {
                "urls_submitted": len(sources),
                "urls_after_dedupe": len(unique_sources),
                "urls_processed": ok_count,
                "urls_failed": len(sources_list) - ok_count,
                "total_rows_scanned": total_scanned,
                "rows_returned": len(rows_out),
                "header_matches_found": len(header_matches),
                "parallelized": use_parallel,
            },
        }

    def fetch_all_records_full(self, table_id: str, view_id: str,
                               field_id: str, workers: int = 20) -> list[dict]:
        """
        Fetch the full externalContent.fullValue for an action column across
        ALL records in a view — in parallel.

        Use this when you need the raw enrichment JSON that native CSV export
        omits (action columns export as "Response" only).

        Returns list of {record_id, value} dicts. ~27ms/record with 20 workers.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        record_ids = self.get_record_ids(table_id, view_id)
        print(f"[clay] fetching full values for {len(record_ids)} records ({workers} workers)...")

        def fetch_one(rec_id):
            r = self.session.get(f"https://api.clay.com/v3/tables/{table_id}/records/{rec_id}")
            r.raise_for_status()
            cell = r.json().get("cells", {}).get(field_id, {})
            return {
                "record_id": rec_id,
                "value": cell.get("externalContent", {}).get("fullValue"),
                "status": cell.get("metadata", {}).get("status"),
            }

        results = []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(fetch_one, rid): rid for rid in record_ids}
            for f in as_completed(futures):
                results.append(f.result())

        print(f"[clay] done — {len(results)} records fetched")
        return results

    # ── Records ───────────────────────────────────────────────────────────────

    def get_record_ids(self, table_id: str, view_id: str) -> list[str]:
        """
        List ALL record IDs in a table via its view.
        This is the correct endpoint — NOT /views/{id}/records (which 404s).
        Filters out the "search" placeholder entry.
        """
        res = self.get(f"/tables/{table_id}/views/{view_id}/records/ids")
        ids = res.get("results", [])
        return [rid for rid in ids if rid != "search"]

    def list_records(
        self,
        table_id: str,
        view_id: str,
        field_ids: list[str] = None,
        *,
        limit: int | None = None,
        strategy: str = "auto",
    ) -> list[dict]:
        """
        List records in a view with their cell data.

        Strategy:
          - `strategy="auto"` (default): uses the direct endpoint
            `GET /tables/{t}/views/{v}/records?limit=N` ONLY when `limit` is
            set AND `field_ids` is omitted. Otherwise falls back to the 2-step
            `get_record_ids() → get_records()` path.
          - `strategy="direct"`: forces the direct endpoint; `limit` is sent
            as a query param, `field_ids` is not honored (server ignores).
          - `strategy="two_step"`: forces the 2-step flow; `limit` slices the
            id list before the bulk fetch.

        Returns a list of raw record dicts (field-id-keyed cells). Use
        `list_records_by_name()` for a name-keyed, extract_cell_value-applied
        convenience result.

        `limit` bounds: if set, capped at 1000 (writer parity). If omitted,
        claycast returns all records in the view via the 2-step flow (this differs
        from the writer, which defaults to `limit=100`). Pass an explicit
        `limit` if you want bounded reads.
        """
        if limit is not None and int(limit) > 1000:
            limit = 1000
        direct_ok = limit is not None and not field_ids
        use_direct = (strategy == "direct") or (strategy == "auto" and direct_ok)
        if strategy == "direct" and field_ids:
            raise ValueError("strategy='direct' does not support field_ids filtering")

        if use_direct:
            res = self.get(f"/tables/{table_id}/views/{view_id}/records?limit={int(limit)}")
            return res.get("results", [])

        record_ids = self.get_record_ids(table_id, view_id)
        if limit is not None:
            record_ids = record_ids[: int(limit)]
        if not record_ids:
            return []
        body = {"recordIds": record_ids}
        if field_ids:
            body["fieldIds"] = field_ids
        res = self.post(f"/tables/{table_id}/bulk-fetch-records", body)
        return res.get("results", [])

    def list_records_by_name(
        self,
        table_id: str,
        view_id: str | None = None,
        *,
        limit: int | None = None,
    ) -> list[dict]:
        """
        Convenience wrapper: list records with field NAMES as keys (not ids),
        `extract_cell_value()` applied, and `_record_id` included per row.
        If `view_id` is None, resolves via `get_field_map()`.
        """
        fmap = self.get_field_map(table_id)
        vid = view_id or fmap["default_view_id"]
        if not vid:
            raise ValueError(f"No view_id provided and no default view found for table {table_id}")
        raw = self.list_records(table_id, vid, limit=limit)
        out = []
        id_to_name = fmap["id_to_name"]
        for r in raw:
            rec = {"_record_id": r.get("id")}
            for fid, cell in (r.get("cells") or {}).items():
                fname = id_to_name.get(fid, fid)
                rec[fname] = extract_cell_value(cell)
            out.append(rec)
        return out

    def get_records(self, table_id: str, record_ids: list[str], field_ids: list[str] = None) -> list[dict]:
        """Fetch specific records by ID. Optionally filter to specific fields."""
        body = {"recordIds": record_ids}
        if field_ids:
            body["fieldIds"] = field_ids
        res = self.post(f"/tables/{table_id}/bulk-fetch-records", body)
        return res.get("results", [])

    def get_record(self, table_id: str, record_id: str) -> dict:
        """
        Fetch a single record, raw shape.

        Endpoint: `GET /tables/{t}/records/{r}`. Returns the full record dict
        including `cells[fid].externalContent.fullValue` for action columns —
        which `bulk-fetch-records` and the `?limit=N` direct endpoint do not
        always populate the same way. Raises HTTPError (404) if the record
        doesn't exist.
        """
        return self.get(f"/tables/{table_id}/records/{record_id}")

    def get_record_by_name(self, table_id: str, record_id: str) -> dict:
        """
        Single-record fetch with field NAMES as keys and `extract_cell_value()`
        applied. Thin wrapper over `get_record()` + `get_field_map()`.
        """
        fmap = self.get_field_map(table_id)
        raw = self.get_record(table_id, record_id)
        rec = {"_record_id": raw.get("id")}
        id_to_name = fmap["id_to_name"]
        for fid, cell in (raw.get("cells") or {}).items():
            fname = id_to_name.get(fid, fid)
            rec[fname] = extract_cell_value(cell)
        return rec

    def create_records(
        self,
        table_id: str,
        cells_list: list[dict],
        *,
        record_ids: list[str] | None = None,
        field_names: bool = False,
        batch_size: int = 100,
    ) -> list[dict]:
        """
        Create records in a table. Returns list of created record dicts fetched
        back from Clay after the values have landed.

        Modes:
          - `field_names=False` (default): `cells_list` is a list of
            `{field_id: value}` dicts. `get_field_map()` is NOT called.
          - `field_names=True`: `cells_list` is a list of `{field_name: value}`
            dicts. `get_field_map()` is called once up front. If any name does
            NOT resolve to a field id, raises `ValueError` before any network
            request is sent.

        Optional `record_ids`: pre-generated Clay record ids (one per entry
        in `cells_list`). If omitted, claycast pre-generates Clay-style ids via
        `_gen_record_id()`.

        Clay's UI writes NEW rows in two steps:
          1. `POST /tables/{t}/records` with blank `{id, cells:{}}` rows
          2. `PATCH /tables/{t}/records` with the real cell values

        ClayCast mirrors that pattern because a single `POST /tables/{t}/records`
        with populated cells returns 200 but silently drops user-cell values.

        WARNING — fresh tables (verified 2026-07-23): on a freshly created
        table, the name-keyed path (`field_names=True`) can silently drop —
        the name->field-id mapped PATCH returned 200 but the values NEVER
        landed (rows stayed blank; this method raised its 5s "values did not
        persist" RuntimeError). The IDENTICAL PATCH keyed by raw field ids
        committed fine (`update_record` / `bulk_update_records` with
        `{fid: value}`). `preflight(table_id=...)` showed `write: True` — NOT
        the write-restricted-cookie mode. Workaround: create blank rows, then
        fid-keyed `bulk_update_records`, then re-fetch to verify.

        Batching: bounded at `batch_size` (max 500 per Clay's internal cap).
        """
        if batch_size > 500:
            batch_size = 500
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        if record_ids is not None and len(record_ids) != len(cells_list):
            raise ValueError(
                f"record_ids length ({len(record_ids)}) must match cells_list length ({len(cells_list)})"
            )

        if field_names:
            fmap = self.get_field_map(table_id)
            name_to_id = fmap["name_to_id"]
            resolved_cells = []
            for i, cells in enumerate(cells_list):
                row = {}
                for k, v in cells.items():
                    fid = name_to_id.get(k)
                    if not fid:
                        raise ValueError(
                            f"create_records: unknown field name {k!r} in row {i}; "
                            f"known names: {sorted(name_to_id)[:20]}"
                        )
                    row[fid] = v
                resolved_cells.append(row)
            cells_list = resolved_cells

        if not cells_list:
            return []

        target_ids = list(record_ids) if record_ids is not None else [
            _gen_record_id() for _ in range(len(cells_list))
        ]

        def records_match_expected(fetched_records: list[dict], expected_batch: list[dict]) -> bool:
            by_id = {r.get("id"): r for r in fetched_records}
            for item in expected_batch:
                rec = by_id.get(item["id"])
                if not rec:
                    return False
                cells = rec.get("cells", {})
                for fid, expected_val in item["cells"].items():
                    if extract_cell_value(cells.get(fid)) != expected_val:
                        return False
            return True

        out: list[dict] = []
        for i, batch in enumerate(_chunk_list(cells_list, batch_size)):
            base_idx = i * batch_size
            batch_ids = target_ids[base_idx: base_idx + len(batch)]

            blank_records = [{"id": rid, "cells": {}} for rid in batch_ids]
            self.post(f"/tables/{table_id}/records", {"records": blank_records})

            patch_records = [
                {"id": rid, "cells": cells}
                for rid, cells in zip(batch_ids, batch)
            ]
            self.patch(f"/tables/{table_id}/records", {"records": patch_records})

            deadline = time.time() + 5.0
            fetched: list[dict] = []
            while True:
                fetched = self.get_records(table_id, batch_ids)
                if records_match_expected(fetched, patch_records):
                    break
                if time.time() >= deadline:
                    raise RuntimeError(
                        "create_records: values did not persist after POST blank rows + PATCH values "
                        f"for record ids {batch_ids}"
                    )
                time.sleep(0.5)

            by_id = {r.get("id"): r for r in fetched}
            out.extend(by_id[rid] for rid in batch_ids if rid in by_id)
        return out

    def update_record(self, table_id: str, record_id: str, field_values: dict) -> dict:
        """Set cell values on ONE record via the proven bulk PATCH endpoint.
        field_values = {field_id: value}. For multiple records, use
        `bulk_update_records()`."""
        return self.patch(
            f"/tables/{table_id}/records",
            {"records": [{"id": record_id, "cells": field_values}]},
        )

    def bulk_update_records(
        self,
        table_id: str,
        records: list[dict],
        *,
        field_names: bool = False,
        batch_size: int = 100,
    ) -> list[dict]:
        """
        Update many records in one request (or chunked requests at `batch_size`).

        Endpoint: `PATCH /tables/{t}/records` with body
        `{"records": [{"id": <rid>, "cells": {<fid>: value}}, ...]}`. The
        response is an ASYNC enqueue ack: `{"records": [], "extraData":
        {"message": "Record updates enqueued"}}`. Callers that need to verify
        the updates landed should re-fetch with `get_records()`.

        Modes:
          - `field_names=False` (default): each record is
            `{"record_id": str, "cells": {<field_id>: value, ...}}`.
            `get_field_map()` is NOT called.
          - `field_names=True`: each record is
            `{"_record_id": str, <field_name>: value, ...}` — flattened per
            the writer's convention. `get_field_map()` is called once up
            front. If any record is missing `_record_id` or any field name
            does NOT resolve, raises `ValueError` BEFORE any network request.

        Batching: bounded at `batch_size` (max 500 per Clay's cap).
        Returns the accumulated `records` fields from each batch's response
        (typically empty — Clay enqueues async).
        """
        if batch_size > 500:
            batch_size = 500

        name_to_id: dict = {}
        if field_names:
            name_to_id = self.get_field_map(table_id)["name_to_id"]

        payload: list[dict] = []
        for i, rec in enumerate(records):
            if field_names:
                rid = rec.get("_record_id")
                if not rid:
                    raise ValueError(f"bulk_update_records: record {i} missing '_record_id'")
                cells = {}
                for k, v in rec.items():
                    if k == "_record_id":
                        continue
                    fid = name_to_id.get(k)
                    if not fid:
                        raise ValueError(
                            f"bulk_update_records: unknown field name {k!r} on record {i} "
                            f"(id={rid!r})"
                        )
                    cells[fid] = v
                payload.append({"id": rid, "cells": cells})
            else:
                rid = rec.get("record_id") or rec.get("id")
                if not rid:
                    raise ValueError(f"bulk_update_records: record {i} missing 'record_id'")
                cells = rec.get("cells")
                if not isinstance(cells, dict):
                    raise ValueError(f"bulk_update_records: record {i} missing 'cells' dict")
                payload.append({"id": rid, "cells": cells})

        results: list[dict] = []
        for batch in _chunk_list(payload, batch_size):
            res = self.patch(f"/tables/{table_id}/records", {"records": batch})
            results.extend(res.get("records", []))
        return results

    def delete_records(
        self,
        table_id: str,
        record_ids: list[str],
        *,
        batch_size: int = 100,
    ) -> list[dict]:
        """
        Delete specific records by ID, chunked at `batch_size` (max 500).
        Endpoint: `DELETE /tables/{t}/records` with body `{"recordIds": [...]}`.
        Returns the accumulated per-batch response dicts.
        """
        if batch_size > 500:
            batch_size = 500
        results: list[dict] = []
        for batch in _chunk_list(record_ids, batch_size):
            r = self.session.delete(self._url(f"/tables/{table_id}/records"), json={"recordIds": batch})
            r.raise_for_status()
            results.append(r.json() if r.text else {})
        return results

    def upsert_records(
        self,
        table_id: str,
        records_by_name: list[dict],
        match_field_name: str,
        *,
        view_id: str | None = None,
        batch_size: int = 100,
        max_scan_rows: int | None = None,
        confirm_large_scan: bool = False,
    ) -> dict:
        """
        Upsert rows against an existing view: match on `match_field_name`,
        update existing rows, create new ones. Pre-generates record ids for
        the create branch so the caller can reference both in the returned
        summary.

        `records_by_name` is a list of `{field_name: value, ...}` dicts. Each
        MUST contain `match_field_name` (or a ValueError is raised before any
        network call). Additional unknown field names also raise.

        Duplicate-match behavior (documented from 2026-04-23 probes):
          - Existing rows with duplicate match values: "last-seen wins" in the
            match index. If you care, deduplicate the table first.
          - Incoming duplicates in `records_by_name`: ClayCast DEDUPES them
            deterministically by taking the LAST occurrence of each match
            value. (Writer's behavior at writer:281-317 is order-sensitive
            and routes later duplicates against synthetic ids before they
            are created; ClayCast corrects that.)

        Large-table guardrail: `upsert_records` fetches ALL records in the
        chosen view before deciding update-vs-create. For a table with N
        rows, that's O(N) API calls. If `max_scan_rows` is set and the live
        `count_records(table_id)` exceeds it, ClayCast raises `ValueError` unless
        `confirm_large_scan=True`.

        Returns:
            {
                "created": int,
                "updated": int,
                "skipped": int,              # incoming-duplicate collapses
                "record_ids": {
                    "created": [<rid>, ...],
                    "updated": [<rid>, ...],
                },
                "scanned_existing": int,     # rows fetched from the view
            }
        """
        if not match_field_name:
            raise ValueError("upsert_records: match_field_name is required")
        if batch_size > 500:
            batch_size = 500

        fmap = self.get_field_map(table_id)
        name_to_id = fmap["name_to_id"]
        match_fid = name_to_id.get(match_field_name)
        if not match_fid:
            raise ValueError(
                f"upsert_records: match_field_name {match_field_name!r} not found on table"
            )
        vid = view_id or fmap["default_view_id"]
        if not vid:
            raise ValueError("upsert_records: no view_id provided and no default view found")

        # Preflight row-count guardrail
        if max_scan_rows is not None:
            live_count = self.count_records(table_id)
            if live_count > max_scan_rows and not confirm_large_scan:
                raise ValueError(
                    f"upsert_records: table has {live_count} rows which exceeds "
                    f"max_scan_rows={max_scan_rows}. Pass a smaller view, raise the "
                    "cap, or set confirm_large_scan=True to bypass."
                )

        # Validate incoming payload BEFORE any mutation
        for i, rec in enumerate(records_by_name):
            if match_field_name not in rec or rec[match_field_name] in (None, ""):
                raise ValueError(
                    f"upsert_records: record {i} is missing required match field "
                    f"{match_field_name!r}"
                )
            for k in rec:
                if k not in name_to_id:
                    raise ValueError(
                        f"upsert_records: unknown field name {k!r} on record {i}"
                    )

        # Dedupe incoming payload — last-occurrence wins, deterministically.
        seen = {}
        for rec in records_by_name:
            seen[rec[match_field_name]] = rec
        deduped = list(seen.values())
        skipped = len(records_by_name) - len(deduped)

        # Fetch existing rows and build match index
        existing_ids = self.get_record_ids(table_id, vid)
        existing = self.get_records(table_id, existing_ids) if existing_ids else []
        scanned_existing = len(existing)
        idx = _build_match_index(existing, match_fid)

        # Partition into updates vs creates (with pre-generated ids)
        updates: list[dict] = []   # shape: {"record_id": ..., "cells": {fid: v}}
        creates_cells: list[dict] = []
        creates_ids: list[str] = []
        created_ids_out: list[str] = []
        updated_ids_out: list[str] = []

        for rec in deduped:
            mv = rec[match_field_name]
            cells_by_id = {name_to_id[k]: v for k, v in rec.items()}
            existing_rid = idx.get(mv)
            if existing_rid:
                updates.append({"record_id": existing_rid, "cells": cells_by_id})
                updated_ids_out.append(existing_rid)
            else:
                new_id = _gen_record_id()
                creates_cells.append(cells_by_id)
                creates_ids.append(new_id)
                created_ids_out.append(new_id)

        # Dispatch
        if updates:
            self.bulk_update_records(
                table_id, updates, field_names=False, batch_size=batch_size
            )
        if creates_cells:
            self.create_records(
                table_id,
                creates_cells,
                record_ids=creates_ids,
                field_names=False,
                batch_size=batch_size,
            )

        return {
            "created": len(created_ids_out),
            "updated": len(updated_ids_out),
            "skipped": skipped,
            "record_ids": {
                "created": created_ids_out,
                "updated": updated_ids_out,
            },
            "scanned_existing": scanned_existing,
        }

    # ── Fields (delete) ────────────────────────────────────────────────────────

    # ── Dependency graph (authoritative — what Clay's delete-warning uses) ────

    def get_table_graph(self, table_id: str) -> dict:
        """Fetch Clay's column-dependency graph: `GET /tables/{t}/graph`.

        Returns `{"nodes": [...], "edges": [...]}`. Each node has
        nodeId/name/type/field/extractedFieldIds. Each edge is
        `{"sourceNodeId", "targetNodeId", "type"}` where type is `"Input"` or
        `"ConditionalRun"`; an edge source->target means **target DEPENDS ON
        source**. A synthetic `"root"` node is the table's source rows.

        This is the graph Clay's UI reads for its delete-warning dialog — good
        for downstream *structure* (incl. `formulaMap` inputs, `ConditionalRun`,
        and transitive reach). BUT it COLLAPSES an action's extracted formula
        columns into the action node (`node.extractedFieldIds`), so the edges
        alone do NOT list those extractors. For the literal "what references
        this field's id / what would break" answer — needed to remap or to fully
        verify delete-safety — use `get_field_references` (a full `typeSettings`
        scan). Neither view alone is complete: graph = structure, scan = literal
        references. (Verified 2026-06-04.)
        """
        return self.get(f"/tables/{table_id}/graph")

    @staticmethod
    def _references_in(fields: list[dict], field_id: str) -> list[dict]:
        """Scan field dicts for every literal reference to `field_id` across the
        FULL typeSettings: formulaText, conditionalRunFormulaText, each
        inputsBinding.formulaText, and formulaMap values (dict-style sub-inputs
        used by execute-subroutine etc.). Returns `[{field_id, name, where}]`."""
        out = []
        for f in fields:
            if f.get("id") == field_id:
                continue
            ts = f.get("typeSettings") or {}
            where = []
            if field_id in (ts.get("formulaText") or ""):
                where.append("formula")
            if field_id in (ts.get("conditionalRunFormulaText") or ""):
                where.append("runCondition")
            for b in (ts.get("inputsBinding") or []):
                if field_id in (b.get("formulaText") or ""):
                    where.append(f"input:{b.get('name')}")
                fm = b.get("formulaMap")
                if isinstance(fm, dict) and any(field_id in str(v) for v in fm.values()):
                    where.append(f"formulaMap:{b.get('name')}")
            if where:
                out.append({"field_id": f["id"], "name": f.get("name"), "where": where})
        return out

    def get_field_references(self, table_id: str, field_id: str) -> list[dict]:
        """Every column whose CONFIG literally references `field_id` — the
        complete "what would break if I delete this / what to repoint on a
        remap" answer. Scans every field's full `typeSettings` (formulaText,
        conditionalRunFormulaText, inputsBinding formulaText, AND `formulaMap`).
        Returns `[{field_id, name, where}]` (`where` = `formula` / `runCondition`
        / `input:<name>` / `formulaMap:<name>`).

        Use THIS (not `get_field_dependents`) for literal id references — a
        remap, or to fully verify delete-safety. Clay's graph collapses an
        action's extracted formula columns into its node, so
        `get_field_dependents` can miss them; this never does. The
        `delete_fields` guard uses this scan."""
        fields = self.get_table(table_id, include_extra_data=True)["table"].get("fields", [])
        return self._references_in(fields, field_id)

    def get_field_dependents(self, table_id: str, field_id: str, *,
                             transitive: bool = False,
                             graph: dict | None = None) -> list[dict]:
        """Columns that DEPEND ON `field_id`, per Clay's graph, **folding in the
        node's `extractedFieldIds`** so an action's own extractor formulas are
        not silently missed. `transitive=False` -> direct; `True` -> full
        downstream closure. Returns `[{field_id, name, type}]` (`type` is the
        edge type, or `"Extracted"` for collapsed extractor columns). Pass
        `graph=` to reuse a fetched graph.

        This is the structural/graph view. For the authoritative literal "what
        references this id" (remap targets, delete-safety) use
        `get_field_references` — graph = structure, scan = literal references."""
        g = graph or self.get_table_graph(table_id)
        nodes = g.get("nodes", [])
        nid2name = {n.get("nodeId"): n.get("name") for n in nodes}
        extracted = {n.get("nodeId"): list(n.get("extractedFieldIds") or [])
                     for n in nodes}
        out_edges: dict[str, list] = {}
        for e in g.get("edges", []):
            out_edges.setdefault(e.get("sourceNodeId"), []).append(
                (e.get("targetNodeId"), e.get("type")))
        _names: dict = {}
        def name_of(fid):
            if fid in nid2name:
                return nid2name[fid]
            if not _names:
                _names.update({f["id"]: f.get("name") for f in self.get_table(
                    table_id, include_extra_data=True)["table"].get("fields", [])})
            return _names.get(fid, fid)
        def direct(nid):
            seen_l = {}
            for t, ty in out_edges.get(nid, []):
                seen_l.setdefault(t, {"field_id": t, "name": name_of(t), "type": ty})
            for ext in extracted.get(nid, []):
                seen_l.setdefault(ext, {"field_id": ext, "name": name_of(ext),
                                        "type": "Extracted"})
            return list(seen_l.values())
        if not transitive:
            return direct(field_id)
        seen: set = set(); stack = [field_id]; res = []
        while stack:
            for d in direct(stack.pop()):
                if d["field_id"] not in seen:
                    seen.add(d["field_id"]); res.append(d); stack.append(d["field_id"])
        return res

    def get_field_dependencies(self, table_id: str, field_id: str, *,
                               transitive: bool = False,
                               graph: dict | None = None) -> list[dict]:
        """What `field_id` itself consumes (its `Input` / `ConditionalRun`
        sources), per Clay's graph — the mirror of `get_field_dependents`. Same
        return shape and `transitive` / `graph` kwargs."""
        g = graph or self.get_table_graph(table_id)
        nid2name = {n.get("nodeId"): n.get("name") for n in g.get("nodes", [])}
        in_edges: dict[str, list] = {}
        for e in g.get("edges", []):
            in_edges.setdefault(e.get("targetNodeId"), []).append(
                (e.get("sourceNodeId"), e.get("type")))
        if not transitive:
            return [{"field_id": s, "name": nid2name.get(s, s), "type": ty}
                    for s, ty in in_edges.get(field_id, [])]
        seen: set = set(); stack = [field_id]; res = []
        while stack:
            for s, ty in in_edges.get(stack.pop(), []):
                if s not in seen:
                    seen.add(s)
                    res.append({"field_id": s, "name": nid2name.get(s, s), "type": ty})
                    stack.append(s)
        return res

    # ── Delete (table-scoped; guarded by a full reference scan) ───────────────

    def delete_column(self, table_id: str, field_id: str, *,
                      force: bool = False) -> dict:
        """Delete a single column. Delegates to `delete_fields` so the same
        reference guard applies (raises if other columns still reference it
        unless `force=True`)."""
        return self.delete_fields(table_id, [field_id], force=force)

    def delete_fields(self, table_id: str, field_ids: list[str], *,
                      force: bool = False) -> dict:
        """
        Delete one or more fields (columns). Table-scoped — affects all views.

        GUARDED: before deleting, this scans every other column's full
        `typeSettings` (`get_field_references`) and RAISES `RuntimeError` if any
        field being deleted is still referenced — by a formula, run condition,
        input, or `formulaMap` — from a column NOT in the delete set (those would
        break). This literal scan is used instead of the graph alone, because
        Clay's graph collapses an action's extractor formulas into its node and
        can under-report. Repoint references first (`get_field_references`), or
        pass `force=True`.

        Ordering (2026-08-13): when removing several columns that reference
        EACH OTHER one call at a time, delete referencers before referencees —
        deleting the referencee first trips this guard. Or pass them all in a
        single call: references from columns inside the delete set are ignored.

        Endpoint: `DELETE /tables/{t}/fields` body `{"fieldIds": [...]}` — the
        bulk primitive Clay's UI uses for single and multi-column deletes.
        """
        if not field_ids:
            raise ValueError("delete_fields: field_ids must be a non-empty list")
        if not force:
            fields = self.get_table(
                table_id, include_extra_data=True)["table"].get("fields", [])
            id2name = {f["id"]: f.get("name") for f in fields}
            deleting = set(field_ids)
            blocking = {}
            for fid in field_ids:
                refs = [r for r in self._references_in(fields, fid)
                        if r["field_id"] not in deleting]
                if refs:
                    blocking[fid] = refs
            if blocking:
                lines = []
                for fid, refs in blocking.items():
                    names = ", ".join(r["name"] for r in refs)
                    lines.append(f"  {id2name.get(fid, fid)} ({fid}): "
                                 f"{len(refs)} reference(s) -> {names}")
                raise RuntimeError(
                    "delete_fields: refusing to delete — these columns are still "
                    "referenced by other columns (formula / run-condition / input / "
                    "formulaMap) and would break. Repoint them first "
                    "(get_field_references), or pass force=True:\n" + "\n".join(lines))
        r = self.session.delete(
            self._url(f"/tables/{table_id}/fields"),
            json={"fieldIds": list(field_ids)},
        )
        r.raise_for_status()
        return r.json() if r.text else {}

    # ── Fields (view-scoped: move, hide/show) ─────────────────────────────────

    def move_field(
        self,
        table_id: str,
        view_id: str,
        field_id: str,
        *,
        after_field_id: str | None = None,
        before_field_id: str | None = None,
    ) -> dict:
        """
        Move a single field to a new position within a view. Exactly one of
        `after_field_id` or `before_field_id` is required.

        Endpoint: `PATCH /tables/{t}/views/{v}/fields/{fid}` body:
        `{"afterFieldId": ...}` OR `{"beforeFieldId": ...}`.
        View-scoped — does not affect other views' column orders.

        For re-ordering 2+ fields at once, use `reorder_fields()` — it's a
        single call with one anchor instead of N sequential moves.
        """
        if (after_field_id is None) == (before_field_id is None):
            raise ValueError(
                "move_field: pass exactly one of after_field_id / before_field_id"
            )
        body = {"afterFieldId": after_field_id} if after_field_id else {"beforeFieldId": before_field_id}
        return self.patch(f"/tables/{table_id}/views/{view_id}/fields/{field_id}", body)

    def reorder_fields(
        self,
        table_id: str,
        view_id: str,
        field_ids: list[str],
        *,
        after_field_id: str | None = None,
        before_field_id: str | None = None,
    ) -> dict:
        """
        Move a group of fields as an ordered block to a new position within a
        view. The group lands adjacent to the anchor, preserving the order
        given in `field_ids`. Exactly one of `after_field_id` or
        `before_field_id` is required.

        Endpoint: `PATCH /tables/{t}/views/{v}/reorder-fields` body:
        `{"fieldIds": [...], "afterFieldId"|"beforeFieldId": "..."}`.
        View-scoped. Same destination semantics as `move_field`.

        Clay's bulk reorder endpoint requires the target fields to currently
        form a contiguous block in the view's field list. Passing non-adjacent
        field ids raises HTTP 400 `"Fields are not adjacent in the view."`
        If you need to reorder non-adjacent fields, call `move_field()` per
        field instead.

        Convenience: to set a whole view's field order to `[A, B, C, D, E]`,
        call once with `field_ids=[B, C, D, E]`, `after_field_id=A`.
        """
        if not field_ids:
            raise ValueError("reorder_fields: field_ids must be a non-empty list")
        if (after_field_id is None) == (before_field_id is None):
            raise ValueError(
                "reorder_fields: pass exactly one of after_field_id / before_field_id"
            )
        body: dict = {"fieldIds": list(field_ids)}
        if after_field_id:
            body["afterFieldId"] = after_field_id
        else:
            body["beforeFieldId"] = before_field_id
        return self.patch(f"/tables/{table_id}/views/{view_id}/reorder-fields", body)

    def set_field_visibility(
        self,
        table_id: str,
        view_id: str,
        field_id: str,
        visible: bool,
    ) -> dict:
        """
        Hide or show a single field in a view.

        Endpoint: `PATCH /tables/{t}/views/{v}/fields/{fid}` body:
        `{"isVisible": bool}`. View-scoped — hides the field only in this
        view; other views are unaffected.
        """
        return self.patch(
            f"/tables/{table_id}/views/{view_id}/fields/{field_id}",
            {"isVisible": bool(visible)},
        )

    def set_fields_visibility(
        self,
        table_id: str,
        view_id: str,
        visibility: dict[str, bool],
    ) -> dict:
        """
        Bulk hide/show across any number of fields in one call. `visibility`
        maps field_id → bool; true shows, false hides. Mixed hide+show in the
        same call is legal.

        Endpoint: `PATCH /tables/{t}/views/{v}/fields` body:
        `{"<fid_1>": {"isVisible": bool}, "<fid_2>": {"isVisible": bool}, ...}`.
        (Note: body is a dict keyed by field id, NOT a list — same shape the
        Clay UI sends.)

        Clay's UI exposes bulk HIDE but not bulk SHOW; this method supports
        both directions since the server accepts it.
        """
        if not visibility:
            raise ValueError("set_fields_visibility: visibility dict must be non-empty")
        body = {fid: {"isVisible": bool(v)} for fid, v in visibility.items()}
        return self.patch(f"/tables/{table_id}/views/{view_id}/fields", body)

    # ── Session preflight ─────────────────────────────────────────────────────

    def preflight(self, table_id: str | None = None, view_id: str | None = None) -> dict:
        """Session health check — run this BEFORE long-running mutation work.

        The `claysession` cookie has a known failure mode where it silently
        becomes "write-restricted": reads keep working, but writes (and action
        runs) enqueue and never commit, with healthy-looking 200 responses.
        Catch that up front, not 50 calls into a build.

        - Auth/read check (always): `me()` + `/my-workspaces` must succeed.
        - Write check (only when `table_id` is given — use a scratch or dark
          table, e.g. a replica): creates one blank record, confirms it lands
          via the view-independent per-record endpoint, then deletes it.
          (`view_id` is accepted for compat but unused.) Raises RuntimeError
          with cookie-refresh guidance if the write never commits (~12s poll).

        Returns {"auth": True, "write": True|None} (write None = not checked).
        """
        import time as _time
        self.me()
        self.get("/my-workspaces")
        result = {"auth": True, "write": None}
        if not table_id:
            return result
        rid = _gen_record_id()
        self.post(f"/tables/{table_id}/records", {"records": [{"id": rid, "cells": {}}]})
        deadline = _time.time() + 12
        landed = False
        while _time.time() < deadline:
            # per-record endpoint: view-independent (a filtered view would hide a
            # blank record and false-negative this check)
            r = self.session.get(self._url(f"/tables/{table_id}/records/{rid}"))
            if r.status_code == 200:
                landed = True
                break
            _time.sleep(1.5)
        if landed:
            self.delete_records(table_id, [rid])
            result["write"] = True
            return result
        raise RuntimeError(
            "preflight: write did not commit within 12s — the claysession cookie is "
            "likely write-restricted. Refresh it from DevTools (references/cookie-setup.md), "
            "replace CLAY_SESSION in .env, and retry. Do NOT start bulk work on this session."
        )

    # ── Views CRUD ────────────────────────────────────────────────────────────
    # Endpoint behavior verified live 2026-07-21: PATCH/POST on a view return 200
    # but SILENTLY DROP `filter` and `sort` from the body — those two must go
    # through their sub-endpoints. Column order cannot be written via order
    # strings (server owns fractional indexing) and `reorder-fields` rejects
    # full-view blocks (400/500), so whole-view ordering is a per-field
    # `move_field` walk. See clay-api-reference.md → "View filter/sort write
    # path + replication side-effects".

    def list_views(self, table_id: str) -> list[dict]:
        """List a table's views (id, name, filter, sort, fields config, ...).

        Reads from `get_table(...)`; there is no dedicated list-views endpoint.
        """
        raw = self.get_table(table_id, include_extra_data=True)
        table = raw.get("table", raw)
        return table.get("views") or table.get("gridViews") or []

    def create_view(
        self,
        table_id: str,
        name: str,
        *,
        filter: dict | None = None,
        sort: dict | None = None,
        description: str | None = None,
    ) -> dict:
        """Create a view, then apply filter/sort via their sub-endpoints.

        `POST /tables/{t}/views` accepts `name` but silently drops `filter` and
        `sort` (verified live) — so this method creates first, then calls
        `set_view_filter` / `set_view_sort` for any provided config, and returns
        the view dict with those fields re-fetched.

        `filter` shape: `{"items": [{"type": "NOT_EMPTY", "fieldId": "f_..."},
        ...], "combinationMode": "AND"}`. `sort` shape: `{"items": [{"fieldId":
        "f_...", "direction": "ASC"|"DESC"}]}`.
        """
        res = self.post(f"/tables/{table_id}/views", {"name": name})
        view = res.get("view", res)
        view_id = view["id"]
        if description:
            self.patch(f"/tables/{table_id}/views/{view_id}", {"description": description})
        if filter is not None:
            self.set_view_filter(table_id, view_id, filter)
        if sort is not None:
            self.set_view_sort(table_id, view_id, sort)
        if filter is not None or sort is not None or description:
            view = next((v for v in self.list_views(table_id) if v["id"] == view_id), view)
        return view

    def update_view(
        self,
        table_id: str,
        view_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> dict:
        """Rename a view / set its description via `PATCH /tables/{t}/views/{v}`.

        Do NOT pass filter/sort here — the endpoint returns 200 and silently
        drops them. Use `set_view_filter` / `set_view_sort`.
        """
        body = {}
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description
        if not body:
            raise ValueError("update_view: pass name and/or description")
        res = self.patch(f"/tables/{table_id}/views/{view_id}", body)
        return res.get("view", res)

    def delete_view(self, table_id: str, view_id: str) -> dict:
        """Delete a view via `DELETE /tables/{t}/views/{v}` (verified live)."""
        return self.delete(f"/tables/{table_id}/views/{view_id}")

    def set_view_filter(self, table_id: str, view_id: str, filter: dict) -> dict:
        """Set a view's filter via `PATCH /tables/{t}/views/{v}/filter`.

        This sub-endpoint is the ONLY working write path for view filters —
        including `filter` in a view PATCH/POST body is silently ignored.
        Body = the filter object itself:
        `{"items": [...], "combinationMode": "AND"}`.
        """
        return self.patch(f"/tables/{table_id}/views/{view_id}/filter", filter)

    def set_view_sort(self, table_id: str, view_id: str, sort: dict) -> dict:
        """Set a view's sort via `PATCH /tables/{t}/views/{v}/sort` (the only
        working write path — sort in a view PATCH body is silently ignored).
        Body = `{"items": [{"fieldId": ..., "direction": "ASC"|"DESC"}, ...]}`.
        """
        return self.patch(f"/tables/{table_id}/views/{view_id}/sort", sort)

    def set_view_fields(self, table_id: str, view_id: str, fields: dict) -> dict:
        """Bulk per-field view config via `PATCH /tables/{t}/views/{v}/fields`.

        `fields` maps field_id → config dict; accepted keys verified live:
        `isVisible` (bool), `width` (int), and `isPinned` (bool — clears/sets the UI
        column pin; pinned columns are forced to the front of the view, which blocks
        `set_view_field_order` from repositioning them — unpin first, verified 2026-08-06). Mixed hide/show/width in one call
        is fine. (Order strings are NOT settable this way — use
        `set_view_field_order`.)
        """
        if not fields:
            raise ValueError("set_view_fields: fields dict must be non-empty")
        return self.patch(f"/tables/{table_id}/views/{view_id}/fields", fields)

    def autofit_view_fields(self, table_id: str, view_id: str, *,
                            min_width: int = 100, max_width: int = 480,
                            char_px: float = 8.0, header_pad: int = 70,
                            include_hidden: bool = False) -> dict:
        """Auto-fit every column's width in a view so its HEADER text is fully
        visible in the UI. Header-only BY DESIGN (user directive 2026-08-06):
        cell content is deliberately ignored — no row reads, no sampling.

        width = clamp(min_width, max_width, header_pad + char_px * len(name)),
        where header_pad covers the type icon / menu chrome. Only visible
        fields are touched unless include_hidden=True. Applies in ONE
        `set_view_fields` PATCH (per-field `width` verified live 2026-07-21).
        Returns {field_id: width}. Idempotent — re-running converges.
        """
        view = next((v for v in self.list_views(table_id) if v["id"] == view_id), None)
        if view is None:
            raise ValueError(f"autofit_view_fields: view {view_id} not found on {table_id}")
        fcfg = view.get("fields") or {}
        raw = self.get_table(table_id)
        table = raw.get("table", raw)
        names = {f["id"]: f.get("name", "") for f in table.get("fields", [])}
        widths: dict = {}
        for fid, cfg in fcfg.items():
            if not include_hidden and (cfg or {}).get("isVisible") is False:
                continue
            if fid not in names:
                continue
            header_px = header_pad + char_px * len(names[fid])
            widths[fid] = int(max(min_width, min(max_width, header_px)))
        if widths:
            self.set_view_fields(table_id, view_id,
                                 {fid: {"width": w} for fid, w in widths.items()})
        return widths

    def set_view_field_order(
        self,
        table_id: str,
        view_id: str,
        ordered_field_ids: list[str],
    ) -> dict:
        """Impose a complete column order on a view via a per-field
        `move_field` walk.

        Why not `reorder_fields`: full-view blocks are rejected live (HTTP 400
        on spreadsheet tables, 500 on people tables). This walk simulates the
        order locally between calls and only moves fields that are out of
        place (0 failures across 334 moves in the live validation run).

        Fields present in the view but absent from `ordered_field_ids` keep
        their relative order after the ordered block. Returns
        `{"moves": N, "order_exact": bool}`.
        """
        if len(ordered_field_ids) < 2:
            return {"moves": 0, "order_exact": True}
        view = next((v for v in self.list_views(table_id) if v["id"] == view_id), None)
        if view is None:
            raise ValueError(f"set_view_field_order: view {view_id} not found on {table_id}")
        fcfg = view.get("fields") or {}
        cur = [fid for fid, cfg in sorted(fcfg.items(), key=lambda kv: str((kv[1] or {}).get("order", "")))]
        want = [f for f in ordered_field_ids if f in set(cur)]
        moves = 0
        for i in range(1, len(want)):
            a, b = want[i - 1], want[i]
            if cur.index(b) == cur.index(a) + 1:
                continue
            self.move_field(table_id, view_id, b, after_field_id=a)
            cur.remove(b)
            cur.insert(cur.index(a) + 1, b)
            moves += 1
        view2 = next((v for v in self.list_views(table_id) if v["id"] == view_id), {})
        got = [fid for fid, cfg in sorted((view2.get("fields") or {}).items(),
                                          key=lambda kv: str((kv[1] or {}).get("order", "")))]
        got = [g for g in got if g in set(want)]
        return {"moves": moves, "order_exact": got == want}

    # ── Field groups ──────────────────────────────────────────────────────────

    def create_field_group(self, table_id: str, field_ids: list[str]) -> dict:
        """
        Create a field group (a collapsible cluster of columns) from existing
        fields. Returns `{"groupId": "gr_..."}` — the key is `groupId`, NOT
        `id` (verified live 2026-07-21). Clay defaults the LAST member as the
        output field; set `isOutputField` flags via `update_field_group`.

        Endpoint: `POST /tables/{t}/fields/group` body: `{"fieldIds": [...]}`.
        Table-scoped. The returned group can then be reordered per-view via
        `move_field_group()`.
        """
        if not field_ids:
            raise ValueError("create_field_group: field_ids must be a non-empty list")
        return self.post(
            f"/tables/{table_id}/fields/group",
            {"fieldIds": list(field_ids)},
        )

    def update_field_group(
        self,
        table_id: str,
        group_id: str,
        *,
        name: str | None = None,
        fields: list[dict] | None = None,
    ) -> dict:
        """
        Update a field group — rename, reorder members, or change which member
        is the output field. At least one of `name` or `fields` must be set.

        Endpoint: `POST /tables/{t}/fields/group/{gr_id}` body:
        `{"name"?: str, "fields": [{"id": fid, "isOutputField"?: bool}, ...]}`.

        Clay requires `fields` on EVERY update. If you pass `name=` alone,
        claycast fetches the current group state from `get_table(..., include_extra_data=True)`
        and preserves the current members automatically. If you pass `fields=`,
        that list is sent as-is.

        WARNING: `fields=` is an ATOMIC REPLACEMENT of the group's membership.
        Omitting a currently-present field id from the list REMOVES it from
        the group. To safely tweak one member without dropping others, fetch
        the current members first (e.g. via `get_table(..., include_extra_data=True)`
        and read `fieldGroupMap`) and send back the full updated list.
        """
        if name is None and fields is None:
            raise ValueError("update_field_group: pass at least one of name= or fields=")
        body: dict = {}
        if name is not None:
            body["name"] = name
        if fields is not None:
            body["fields"] = fields
        else:
            table_extra = self.get_table(table_id, include_extra_data=True)
            table = table_extra.get("table", table_extra)
            group = (table.get("fieldGroupMap") or {}).get(group_id)
            if not group:
                raise ValueError(f"update_field_group: group {group_id} not in table {table_id}")
            current = (
                (group.get("groupDetails") or {}).get("fields")
                or group.get("fields")
                or []
            )
            body["fields"] = [
                {k: entry[k] for k in ("id", "isOutputField") if k in entry}
                for entry in current
                if isinstance(entry, dict) and entry.get("id")
            ]
        return self.post(
            f"/tables/{table_id}/fields/group/{group_id}",
            body,
        )

    def move_field_group(
        self,
        table_id: str,
        view_id: str,
        group_id: str,
        *,
        after_field_id: str | None = None,
        before_field_id: str | None = None,
    ) -> dict:
        """
        Move a field group to a new position within a view. Exactly one of
        `after_field_id` or `before_field_id` is required.

        Endpoint: `PATCH /tables/{t}/views/{v}/group/{gr_id}` body:
        `{"groupId": "<same as url>", "afterFieldId"|"beforeFieldId": "..."}`.
        View-scoped — same destination semantics as `move_field`.
        """
        if (after_field_id is None) == (before_field_id is None):
            raise ValueError(
                "move_field_group: pass exactly one of after_field_id / before_field_id"
            )
        body: dict = {"groupId": group_id}
        if after_field_id:
            body["afterFieldId"] = after_field_id
        else:
            body["beforeFieldId"] = before_field_id
        return self.patch(
            f"/tables/{table_id}/views/{view_id}/group/{group_id}",
            body,
        )

    def ungroup(self, table_id: str, group_id: str) -> dict:
        """
        Dissolve a field group while KEEPING its member fields intact as
        loose columns in the table.

        Endpoint: `DELETE /tables/{t}/fields/group/{gr_id}` body:
        `{"deleteFields": false}`. This method and `delete_field_group()`
        call the same endpoint — they are kept as separate verbs because
        picking the wrong kwarg value means total loss of the group's
        member fields. Separate methods = no wrong-kwarg footgun.
        """
        r = self.session.delete(
            self._url(f"/tables/{table_id}/fields/group/{group_id}"),
            json={"deleteFields": False},
        )
        r.raise_for_status()
        return r.json() if r.text else {}

    def delete_field_group(self, table_id: str, group_id: str) -> dict:
        """
        Delete a field group AND all its member fields in a single call.

        Endpoint: `DELETE /tables/{t}/fields/group/{gr_id}` body:
        `{"deleteFields": true}`. Irreversible. For non-destructive group
        removal that preserves the columns, use `ungroup()` instead.
        """
        r = self.session.delete(
            self._url(f"/tables/{table_id}/fields/group/{group_id}"),
            json={"deleteFields": True},
        )
        r.raise_for_status()
        return r.json() if r.text else {}

    # ── Running ───────────────────────────────────────────────────────────────

    def run_column(
        self,
        table_id: str,
        field_ids: list[str] | None = None,
        *,
        field_names: list[str] | None = None,
        record_ids: list[str] | None = None,
        view_id: str | None = None,
        top_n: int | None = None,
        force_run: bool = False,
        caller_name: str = "clay-client",
    ) -> dict:
        """
        Trigger enrichment/action/source fields.

        - If `field_ids` / `field_names` are omitted, ClayCast resolves all runnable
          fields (`action`, `enrichment`, `source`, `waterfall`, `claygent`).
        - `record_ids` targets explicit rows.
        - `view_id` scopes the run to a view.
        - `top_n` requires `view_id` and sends Clay's `viewIdTopRecords`.

        The ACK ({"runMode": "INDIVIDUAL"}) does NOT guarantee execution
        (verified 2026-07-30):
        - a row whose `conditionalRunFormulaText` doesn't pass is handled per
          action: `http-api-v2` skipped it SILENTLY (blank cell, no status) and
          `force_run=True` bypassed the gate; SOQL lookups record
          ERROR_RUN_CONDITION_NOT_MET (2026-09-30); `execute-subroutine` honors
          the gate even with `force_run=True` (2026-08-06). See
          clay-api-reference.md "Conditional Execution".
        - `use-ai` columns never executed via this API in testing (0 credits,
          blank cell even with force_run) while lookup columns ran fine — run
          AI columns from the Clay UI. See clay-api-reference.md "AI Columns".
        """
        resolved_field_ids = self._resolve_runnable_field_ids(
            table_id,
            field_ids=field_ids,
            field_names=field_names,
        )
        body = {"callerName": caller_name}
        if resolved_field_ids:
            body["fieldIds"] = resolved_field_ids
        if force_run:
            body["forceRun"] = True
        if record_ids:
            body["runRecords"] = {"recordIds": record_ids}
        elif top_n is not None:
            if not view_id:
                raise ValueError("view_id is required when top_n is set")
            body["runRecords"] = {"viewIdTopRecords": {"viewId": view_id, "numRecords": int(top_n)}}
        elif view_id:
            body["runRecords"] = {"viewId": view_id}
        return self.patch(f"/tables/{table_id}/run", body)

    def get_run_status(self, table_id: str, *, workspace_id: int | str | None = None) -> dict:
        """
        Normalize Clay's `fieldrun` / workspace `runstatus` endpoints.
        Returns `{"source", "raw", "fields"}`.
        """
        total_records = None
        try:
            total_records = self.count_records(table_id)
        except Exception:
            pass

        fieldrun_error = None
        try:
            raw = self.get(f"/tables/{table_id}/fieldrun")
            normalized = _normalize_run_status(raw, total_records=total_records)
            if normalized["fields"]:
                return {"source": "fieldrun", "raw": raw, **normalized}
        except Exception as exc:
            fieldrun_error = str(exc)

        ws_id = self._resolve_workspace_id(workspace_id)
        raw = self.get(f"/workspaces/{ws_id}/tables/{table_id}/fields/runstatus")
        normalized = _normalize_run_status(raw, total_records=total_records)
        out = {"source": "runstatus", "raw": raw, **normalized}
        if fieldrun_error:
            out["fieldrun_error"] = fieldrun_error
        return out

    def wait_for_runs(
        self,
        table_id: str,
        *,
        workspace_id: int | str | None = None,
        field_ids: list[str] | None = None,
        field_names: list[str] | None = None,
        timeout_seconds: int = 60,
        poll_interval_seconds: int = 10,
        stall_threshold: int | None = None,
        include_failed_ids: bool = False,
    ) -> dict:
        """
        Poll Clay run status until completion, timeout, or stall.
        """
        if timeout_seconds < 1:
            raise ValueError("timeout_seconds must be >= 1")
        if poll_interval_seconds < 1:
            raise ValueError("poll_interval_seconds must be >= 1")

        ws_id = self._resolve_workspace_id(workspace_id)
        target_ids = self._resolve_runnable_field_ids(
            table_id,
            field_ids=field_ids,
            field_names=field_names,
        ) if (field_ids or field_names) else []
        fmap = self.get_field_map(table_id)
        id_to_name = fmap["id_to_name"]
        total_records = self.count_records(table_id)

        start = time.time()
        polls_completed = 0
        last_progress_total = 0
        polls_without_progress = 0
        last_progress_at = 0.0
        threshold = max(2, int(stall_threshold)) if stall_threshold is not None else None

        while True:
            elapsed = time.time() - start
            if elapsed >= timeout_seconds:
                status = "timeout"
                success = False
                break

            status_data = self.get_run_status(table_id, workspace_id=ws_id)
            polls_completed += 1
            fields = status_data.get("fields", [])

            if target_ids:
                fields = [field for field in fields if field["field_id"] in target_ids]
                if not fields:
                    fields = [{
                        "field_id": fid,
                        "status": "queued",
                        "progress_percent": 0,
                        "records_processed": 0,
                        "success": 0,
                        "failed": 0,
                        "running": 0,
                        "pending": total_records,
                        "error": None,
                    } for fid in target_ids]
            elif not fields:
                target_ids = self._resolve_runnable_field_ids(table_id)

            fields_summary = {}
            overall = {"success": 0, "failed": 0, "running": 0, "pending": 0, "percent_complete": 0.0}
            for field in fields:
                fid = field["field_id"]
                entry = {
                    "name": id_to_name.get(fid, fid),
                    "status": field.get("status", "unknown"),
                    "success": int(field.get("success", 0) or 0),
                    "failed": int(field.get("failed", 0) or 0),
                    "running": int(field.get("running", 0) or 0),
                    "pending": int(field.get("pending", 0) or 0),
                    "percent_complete": float(field.get("progress_percent", 0) or 0),
                }
                fields_summary[fid] = entry
                overall["success"] += entry["success"]
                overall["failed"] += entry["failed"]
                overall["running"] += entry["running"]
                overall["pending"] += entry["pending"]
            if fields_summary:
                overall["percent_complete"] = round(
                    sum(item["percent_complete"] for item in fields_summary.values()) / len(fields_summary),
                    1,
                )

            current_progress = overall["success"] + overall["failed"]
            if current_progress > last_progress_total:
                last_progress_total = current_progress
                polls_without_progress = 0
                last_progress_at = elapsed
            else:
                polls_without_progress += 1

            if not fields_summary and not target_ids:
                status = "completed"
                success = True
                break

            if fields_summary:
                all_done = all(
                    entry["running"] == 0 and entry["pending"] == 0 and entry["percent_complete"] >= 100
                    or entry["status"] in {"completed", "partial", "failed"}
                    for entry in fields_summary.values()
                )
            else:
                all_done = False

            if threshold is not None and polls_without_progress >= threshold and (overall["running"] + overall["pending"]) > 0:
                status = "stalled"
                success = False
                break

            if all_done:
                status = "partial" if overall["failed"] else "completed"
                success = True
                break

            time.sleep(poll_interval_seconds)

        failed_record_ids: list[str] = []
        if include_failed_ids and (status == "partial" or (status == "completed" and overall.get("failed"))):
            errored_view_id = self._find_special_view(table_id, "errored-rows")
            if errored_view_id:
                failed_record_ids = self.get_record_ids(table_id, errored_view_id)

        return {
            "success": success,
            "status": status,
            "total_records": total_records,
            "elapsed_seconds": round(time.time() - start, 2),
            "polls_completed": polls_completed,
            "fields_summary": fields_summary,
            "overall_progress": overall,
            "failed_record_ids": failed_record_ids,
            "stall_info": {
                "polls_without_progress": polls_without_progress,
                "last_progress_at": round(last_progress_at, 2),
            },
        }

    def rerun_errored_cells(
        self,
        table_id: str,
        *,
        field_ids: list[str] | None = None,
        field_names: list[str] | None = None,
        workspace_id: int | str | None = None,
        wait_for_completion: bool = False,
        timeout_seconds: int = 300,
        poll_interval_seconds: int = 10,
        caller_name: str = "clay-client",
    ) -> dict:
        """
        Find errored rows, inspect which cells failed, and re-run only those
        field+record pairs.
        """
        ws_id = self._resolve_workspace_id(workspace_id)
        errored_view_id = self._find_special_view(table_id, "errored-rows")
        if not errored_view_id:
            raise RuntimeError(f"No 'Errored Rows' view found on table {table_id}")

        target_field_ids = self._resolve_runnable_field_ids(
            table_id,
            field_ids=field_ids,
            field_names=field_names,
        )
        errored_record_ids = self.get_record_ids(table_id, errored_view_id)
        fmap = self.get_field_map(table_id)
        id_to_name = fmap["id_to_name"]

        if not errored_record_ids:
            return {
                "success": True,
                "status": "no_errors",
                "errored_record_ids": [],
                "errored_cells": {},
                "total_errored_cells": 0,
                "runs_triggered": 0,
                "records_targeted": 0,
                "warnings": [],
            }

        fetched_records = []
        for batch in _chunk_list(errored_record_ids, 500):
            fetched_records.extend(self.get_records(table_id, batch))

        errors_by_field: dict[str, list[str]] = {}
        for record in fetched_records:
            rid = record.get("id")
            cells = record.get("cells") or record.get("fields") or {}
            for fid in target_field_ids:
                if _cell_has_error(cells.get(fid)):
                    errors_by_field.setdefault(fid, []).append(rid)

        warnings = []
        if not errors_by_field:
            warnings.append(
                "No cell-level error markers found in bulk-fetch payload; falling back to row-level targeting."
            )
            errors_by_field = {
                fid: list(errored_record_ids)
                for fid in target_field_ids
            }

        runs_triggered = 0
        for fid, record_ids_for_field in errors_by_field.items():
            self.run_column(
                table_id,
                [fid],
                record_ids=record_ids_for_field,
                force_run=False,
                caller_name=caller_name,
            )
            runs_triggered += 1

        errored_cells_named = {
            id_to_name.get(fid, fid): record_ids_for_field
            for fid, record_ids_for_field in errors_by_field.items()
        }
        out = {
            "success": True,
            "status": "triggered",
            "errored_record_ids": errored_record_ids,
            "errored_cells": errored_cells_named,
            "total_errored_cells": sum(len(v) for v in errors_by_field.values()),
            "runs_triggered": runs_triggered,
            "records_targeted": sum(len(v) for v in errors_by_field.values()),
            "warnings": warnings,
        }
        if wait_for_completion and errors_by_field:
            wait_result = self.wait_for_runs(
                table_id,
                workspace_id=ws_id,
                field_ids=list(errors_by_field.keys()),
                timeout_seconds=timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
                include_failed_ids=True,
            )
            out["wait_result"] = wait_result
            out["status"] = wait_result["status"]
            out["success"] = wait_result["success"]
        return out

    def run_and_wait(self, table_id: str, field_ids: list[str],
                     record_ids: list[str], timeout: int = 120, poll: int = 5) -> list[dict]:
        """Run columns on records and poll until done or timeout."""
        self.run_column(table_id, field_ids, record_ids=record_ids)
        start = time.time()
        while time.time() - start < timeout:
            time.sleep(poll)
            records = self.get_records(table_id, record_ids)
            all_done = True
            for rec in records:
                for fid in field_ids:
                    cell = rec.get("cells", {}).get(fid, {})
                    status = cell.get("metadata", {}).get("status", "")
                    if status in ("QUEUED", "RUNNING", "PENDING", ""):
                        if not cell.get("value") and status != "ERROR":
                            all_done = False
                            break
                if not all_done:
                    break
            if all_done:
                return records
        return self.get_records(table_id, record_ids)

    # ── Column helpers ────────────────────────────────────────────────────────

    # Substrings in an action_key whose cells hold structured records
    # (objects/arrays) rather than a scalar. These REQUIRE
    # dataTypeSettings.type="json"; passing "text" is rejected at create time
    # with the opaque 400 "value does not match any of the allowed types".
    _JSON_RESULT_ACTION_HINTS = ("salesforce-", "lookup", "soql", "enrich")

    def create_action_column(self, table_id: str, name: str,
                             action_key: str, package_id: str,
                             inputs: dict[str, Any],
                             view_id: str = None,
                             auth_account_id: str = None,
                             condition: str = None,
                             data_type: str = None) -> dict:
        """
        Create an action column (enrichment, AI, HTTP API).

        inputs: `{input_name: value}` where each value is either:
          - a formula string, sent as `formulaText`
          - a dict, sent as `formulaMap` (used by inputs like `answerSchemaType`)
          - `None`, sent as a bare `{"name": ...}` entry (present but unset)
          PASS EVERY PARAMETER THE ACTION DECLARES, using None for the ones you
          don't set. Clay's UI builds an action column's input form from the
          stored `inputsBinding` array, NOT from the action schema, so a column
          bound with only the params you use runs fine but opens in the UI with
          NO inputs visible. Get the authoritative names from
          `clay workflows actions schema <packageId> <actionKey>`
          (`inputParameters`), including pipe-nested children such as
          `retryOptions|maxRetries`. Verified 2026-07-24 (http-api-v2 = 15 params).
          NOTE on native-query inputs (e.g. `soql_query`): the formulaText must
          be a valid formula EXPRESSION that evaluates to the query string — a
          JS string literal like `'"SELECT Id FROM User"'`, or a concatenation
          `'"SELECT ... \\'" + {{f_email}} + "\\'"'`. Passing raw query text
          (`"SELECT ..."`) is not a valid expression and fails validation.
        condition: optional "Only run if" formula (conditionalRunFormulaText)
        data_type: dataTypeSettings.type. If left None (recommended), it is
            auto-selected: "json" for record-returning actions (Salesforce
            lookup/SOQL, enrichments — see `_JSON_RESULT_ACTION_HINTS`), else
            "text". This matters: a record-returning action created with "text"
            is rejected with 400 "value does not match any of the allowed
            types", while a formula/text column created with "json" breaks the
            Clay UI. Pass explicitly only to override the heuristic.
            ⚠ write-to-cell (function send-back) columns match none of the
            hints, so a manual build here defaults to "text" and the send-back
            silently misdelivers — pass data_type="json" explicitly and keep
            actionVersion 1 (both required; verified 2026-07-31, see
            clay-api-reference "Creating a custom function" step 4).
        auth_account_id: resolve this FRESH via
            `list_auth_accounts_by_type('<type>')`. Do NOT copy it out of an
            existing column's typeSettings — stale ids cause a 404
            "App Account not found" at create time.

        Common action_key / package_id combos:
          enrich-person-with-mixrank-v2     / e251a70e-46d7-4f3a-b3ef-a211ad3d8bd2
          enrich-company-with-mixrank-v2    / e251a70e-46d7-4f3a-b3ef-a211ad3d8bd2
          use-ai                            / 67ba01e9-1898-4e7d-afe7-7ebe24819a57
          http-api-v2                       / 4299091f-3cd3-4d68-b198-0143575f471d
          salesforce-lookup-via-soql-v2     / d0c0a70d-7c1e-40de-b214-9d8d82672770
          salesforce-lookup-record-v2       / d0c0a70d-7c1e-40de-b214-9d8d82672770
        """
        if action_key == "ai":
            raise ValueError(
                "actionKey must be 'use-ai', not 'ai'. Clay's API accepts "
                "'ai' but silently discards inputsBinding on the server, "
                "producing a broken column. Pass action_key='use-ai'."
            )
        if data_type is None:
            akey = action_key.lower()
            data_type = ("json"
                         if any(h in akey for h in self._JSON_RESULT_ACTION_HINTS)
                         else "text")
        if action_key == "http-api-v2":
            _body = inputs.get("body")
            _hit = _find_body_expression(_body) if isinstance(_body, str) else None
            if _hit:
                raise ValueError(
                    _body_expression_error("create_action_column()", _body,
                                           _hit[0], _hit[1])
                )
        inputs_binding = []
        for k, v in inputs.items():
            if isinstance(v, dict):
                inputs_binding.append({"name": k, "formulaMap": v})
            elif v:
                inputs_binding.append({"name": k, "formulaText": str(v)})
            else:
                inputs_binding.append({"name": k})
        ts = {
            "dataTypeSettings": {"type": data_type},
            "actionKey": action_key,
            "actionVersion": 1,
            "actionPackageId": package_id,
            "inputsBinding": inputs_binding,
        }
        if auth_account_id:
            ts["authAccountId"] = auth_account_id
        if condition:
            ts["conditionalRunFormulaText"] = condition

        try:
            return self.create_column(table_id, {
                "type": "action", "name": name, "typeSettings": ts
            }, view_id=view_id)
        except Exception as e:
            resp = getattr(e, "response", None)
            body = ""
            if resp is not None:
                try:
                    body = resp.text or ""
                except Exception:
                    body = ""
            if resp is not None and resp.status_code == 400 and \
                    "does not match any of the allowed types" in body and \
                    data_type != "json":
                raise RuntimeError(
                    f"Clay rejected this action column with 400 'value does not "
                    f"match any of the allowed types'. Record-returning actions "
                    f"(here: {action_key!r}) need data_type='json', not "
                    f"{data_type!r}. Retry create_action_column(..., "
                    f"data_type='json')."
                ) from e
            if resp is not None and resp.status_code == 404 and \
                    "App Account not found" in body:
                raise RuntimeError(
                    f"Clay returned 404 'App Account not found' for "
                    f"authAccountId={auth_account_id!r} — it is stale/invalid. "
                    f"Resolve a live account via "
                    f"list_auth_accounts_by_type('<type>'); do NOT reuse an "
                    f"authAccountId copied from an existing column."
                ) from e
            raise

    # ── Saved Claygents as table columns ────────────────────────────────────
    # A saved Claygent (c_...) is a workspace resource with a prompt, an output
    # format and model settings. A table column that uses it is a `use-ai`
    # column with useCase "claygent" plus claygentId + claygentFieldMapping, AND
    # its own copies of the output schema (answerSchemaType) and the model.
    # Without the schema copy the Clay UI shows "Unable to parse the output
    # schema for the column" and the column will not run. The schema and model
    # copies do NOT follow later edits to the Claygent: re-sync after any change.
    #
    # The PROMPT is different: Clay rebuilds it server-side on every column
    # create/update, from the bound Claygent's current prompt with each
    # {{variable}} replaced by its claygentFieldMapping expression. A prompt
    # sent in the write is discarded (a bogus literal was replaced; repointing
    # claygentId alone swapped the whole prompt). We still send the rendered
    # prompt so the payload matches what the UI sends. Verified live 2026-09-30.
    #
    # Only JSON Schema and Fields output formats are copied, in the encodings
    # UI-made columns carry (read live 2026-10-06). Any other format (text, null,
    # unknown) is unmeasured: the builders raise and verify reports it.

    USE_AI_PACKAGE_ID = "67ba01e9-1898-4e7d-afe7-7ebe24819a57"
    # Clay's own keys, merged into a Claygent cell's object next to the answer
    # (clay-api-reference "Saved Claygents"). unwrap_claygent_output strips them.
    CLAYGENT_META_KEYS = ("reasoning", "confidence", "stepsTaken", "totalInputTokens",
                          "totalOutputTokens", "timeTakenInSeconds", "totalCostToAIProvider",
                          "forcedToFinishEarlyBecauseOfCost")
    # The inputs claygent_column_inputs derives from the Claygent. sync_claygent_column
    # rewrites exactly these and keeps every other entry (temperature, _metadata, ...).
    _CLAYGENT_OWNED_INPUTS = ("useCase", "claygentId", "model", "claygentFieldMapping",
                              "prompt", "answerSchemaType")

    def get_claygent(self, claygent_id: str) -> dict:
        """`GET /workspaces/{ws}/claygents/{id}` -> the Claygent, incl.
        `currentVersion` (userPrompt, outputFormat, modelSettings, variables)."""
        res = self.get(f"/workspaces/{self.workspace_id}/claygents/{claygent_id}")
        return res.get("claygent", res)

    def _use_ai_param_names(self) -> list[str]:
        """Every input the `use-ai` action declares (a column must bind them all,
        unset ones bare, or the UI shows no inputs)."""
        cached = getattr(self, "_use_ai_params_cache", None)
        if cached:
            return cached
        act = next((a for a in self.list_actions() if a.get("key") == "use-ai"), None)
        if not act:
            raise RuntimeError("use-ai action not found in list_actions()")
        names = [p["name"] for p in act.get("inputParameterSchema") or []]
        self._use_ai_params_cache = names
        return names

    @staticmethod
    def _claygent_split(user_prompt: str, names) -> list[str]:
        """A Claygent prompt split on the {{name}} placeholders of `names`, kept as
        separators: literal text at even indices, placeholders at odd ones. Only
        the given names count, so a name may hold spaces, dots or colons, and any
        other {{...}} text stays literal."""
        if not names:
            return [user_prompt]
        alts = sorted({"{{" + n + "}}" for n in names}, key=lambda t: (-len(t), t))
        return re.split("(" + "|".join(map(re.escape, alts)) + ")", user_prompt)

    @staticmethod
    def _claygent_prompt_formula(user_prompt: str, var_map: dict[str, str]) -> str:
        """The Claygent's prompt as a column formula: literal pieces as JS string
        literals, each {{variable}} named in var_map as
        Clay.formatForAIPrompt(<its expression>)."""
        pieces = []
        for i, part in enumerate(ClayClient._claygent_split(user_prompt, var_map)):
            if i % 2:
                pieces.append("Clay.formatForAIPrompt(" + var_map[part[2:-2]] + ")")
            elif part:
                # ensure_ascii=False: \\uXXXX escapes in formula string literals are rejected by Clay.
                pieces.append(json.dumps(part, ensure_ascii=False))
        return " + ".join(pieces)

    @staticmethod
    def _claygent_schema_copy(fmt: Any) -> dict:
        """The column's `answerSchemaType` formulaMap for a Claygent `outputFormat`,
        encoded the way UI-made columns store it (read live 2026-10-06):
          JSON Schema: {"type": '"json"', "jsonType": '"JSONSchema"',
                        "jsonSchema": <the Claygent's schema string, as a string literal>}
          Fields:      {"type": '"json"', "fields": <the fields as a compact object
                        literal>, "jsonType": '"Fields"'}
        Raises ValueError for any other format (text, null, unknown: unmeasured)."""
        f = fmt if isinstance(fmt, dict) else {}
        if f.get("type") == "json" and f.get("jsonType") == "JSONSchema" and isinstance(f.get("jsonSchema"), str):
            return {"type": '"json"', "jsonType": '"JSONSchema"',
                    "jsonSchema": json.dumps(f["jsonSchema"], ensure_ascii=False)}
        if f.get("type") == "json" and f.get("jsonType") == "Fields" and isinstance(f.get("fields"), dict):
            return {"type": '"json"',
                    "fields": json.dumps(f["fields"], ensure_ascii=False, separators=(",", ":")),
                    "jsonType": '"Fields"'}
        if isinstance(fmt, dict):
            what = f"type={fmt.get('type')!r}, jsonType={fmt.get('jsonType')!r}"
        else:
            what = "null" if fmt is None else repr(fmt)[:60]
        raise ValueError(f"unsupported output format ({what})")

    @staticmethod
    def _decoded_schema_copy(fm: dict) -> dict | None:
        """A schema copy with its formula literals decoded (and the JSON Schema
        decoded once more), for comparing copies; None when it does not decode."""
        try:
            out = {k: json.loads(v) for k, v in fm.items()}
            if "jsonSchema" in out:
                out["jsonSchema"] = json.loads(out["jsonSchema"])
            return out
        except (TypeError, ValueError):  # not a string, not JSON, or a single-encoded schema
            return None

    @staticmethod
    def _binding_literal(entry: dict | None) -> Any:
        """The JSON literal an inputsBinding entry's formulaText holds; None when the
        entry is missing, bare, or not a JSON literal."""
        try:
            return json.loads((entry or {}).get("formulaText") or "null")
        except (TypeError, ValueError):
            return None

    def claygent_column_inputs(self, claygent_id: str, var_map: dict[str, str],
                               *, byo_key: bool = True) -> dict[str, Any]:
        """The full `use-ai` inputs for a column bound to a saved Claygent.

        var_map: {claygent variable: formula expression}, e.g.
            {"user_message": "{{f_abc}}"}. Keys are the Claygent's variable names,
            exactly as declared (they may contain spaces, dots or colons). Any
            expression works, not only a column reference: an expression is
            evaluated at run time as the action's input and is NOT stored in a
            cell, so it is the way to feed an input larger than a text or
            formula cell's 8,192-character cap.
        byo_key: True binds `_metadata` = {"modelSource": '"user"'}, the marker of
            a column on your own provider key (pass that connection as
            `auth_account_id` when creating the column). False leaves `_metadata`
            unset (bare when the action declares it, else absent), as on use-ai
            columns that run on the workspace key. UI-made Claygent columns were
            also seen with modelSource `generated`; this method never writes
            that value.

        The output format is copied for JSON Schema and Fields Claygents only.
        Any other format (text, null, unknown) raises ValueError, because its
        column copy has not been measured.
        """
        cg = self.get_claygent(claygent_id)
        v = cg["currentVersion"]
        names = [x["name"] for x in v.get("variables") or []]
        if sorted(names) != sorted(var_map):
            raise ValueError(f"claygent_column_inputs: Claygent {claygent_id} variables {sorted(names)} "
                             f"!= var_map keys {sorted(var_map)}")
        try:
            schema_copy = self._claygent_schema_copy(v.get("outputFormat"))
        except ValueError as e:
            raise ValueError(f"claygent_column_inputs: Claygent {claygent_id} has an {e}; only JSON Schema "
                             f"and Fields output can be copied onto a column") from None
        inputs: dict[str, Any] = {n: None for n in self._use_ai_param_names()}
        inputs.update({
            "useCase": '"claygent"',
            "claygentId": json.dumps(claygent_id),
            "model": json.dumps(v["modelSettings"]["model"]),
            "claygentFieldMapping": {"{{" + k + "}}": "Clay.formatForAIPrompt(" + e + ")" for k, e in var_map.items()},
            "prompt": self._claygent_prompt_formula(v["userPrompt"], var_map),
            "answerSchemaType": schema_copy,
        })
        if byo_key:
            inputs["_metadata"] = {"modelSource": '"user"'}
        return inputs

    def create_claygent_column(self, table_id: str, name: str, claygent_id: str,
                               var_map: dict[str, str], *, view_id: str = None,
                               auth_account_id: str = None, condition: str = None,
                               byo_key: bool = True) -> dict:
        """Create a table column that runs a saved Claygent, with the schema and
        model copies Clay requires, then verify it (`verify_claygent_column`).

        Runs only from the Clay UI's Run button: `run_column` ACKs a
        useCase-"claygent" column but never executes it. `condition` is the
        "Only run if" formula; a non-force UI Run honors it, "Force run" does not.
        byo_key: see `claygent_column_inputs`. True (default) binds modelSource
        "user" (your own provider key: pass `auth_account_id`); False leaves
        `_metadata` unset. Raises ValueError before writing on a var_map mismatch
        or an output format other than JSON Schema / Fields, and RuntimeError if
        the read-back does not match the Claygent (the column is left in place).
        """
        inputs = self.claygent_column_inputs(claygent_id, var_map, byo_key=byo_key)
        col = self.create_action_column(table_id, name, "use-ai", self.USE_AI_PACKAGE_ID, inputs,
                                        view_id=view_id, auth_account_id=auth_account_id,
                                        condition=condition, data_type="json")
        field_id = (col.get("field") or col).get("id")
        check = self.verify_claygent_column(table_id, field_id)
        if not check["ok"]:
            raise RuntimeError(f"Claygent column {field_id} did not match its Claygent: {check['problems']}")
        return col

    def _claygent_column_settings(self, table_id: str, field_id: str, method: str) -> tuple[dict, dict]:
        f = next((x for x in self.list_fields(table_id) if x["field_id"] == field_id), None)
        if f is None:
            raise ValueError(f"{method}: field {field_id} not found on {table_id}")
        ts = f.get("type_settings") or {}
        return ts, {b["name"]: b for b in ts.get("inputsBinding") or []}

    def sync_claygent_column(self, table_id: str, field_id: str,
                             var_map: dict[str, str] | None = None, *,
                             condition: str | None = None) -> dict:
        """Re-copy the Claygent's CURRENT schema and model onto an existing column
        (do this after any Claygent edit: the column keeps the old copies
        otherwise; the write also makes Clay re-render the prompt from the
        Claygent). var_map defaults to the column's existing mapping; condition
        defaults to the existing "Only run if". Verifies by read-back.

        Rewrites the inputs claygent_column_inputs derives from the Claygent
        (useCase, claygentId, model, claygentFieldMapping, prompt,
        answerSchemaType) and adds any the column lacks, e.g. a missing schema
        copy ("Unable to parse the output schema"). Every other entry is kept as
        it is, `_metadata` included, so a sync never changes which key the column
        bills. Inputs the action declares but the column lacks are added bare
        (never `_metadata`); entries follow create's order. Raises ValueError
        before writing when the column is not bound to a saved Claygent, its
        mapping cannot be read back (pass var_map), or the Claygent's output is
        not JSON Schema / Fields.

        Changing the model this way did NOT mark finished cells out of date
        (2026-09-30). Changing an input the condition reads DOES (see
        clay-api-reference.md "Table run traps").
        """
        ts, b = self._claygent_column_settings(table_id, field_id, "sync_claygent_column")
        cid = self._binding_literal(b.get("claygentId"))
        if not cid or not isinstance(cid, str):
            raise ValueError(f"sync_claygent_column: field {field_id} is not bound to a saved Claygent")
        if var_map is None:
            fm = (b.get("claygentFieldMapping") or {}).get("formulaMap") or {}
            var_map = {}
            for k, v in fm.items():
                m = re.fullmatch(r"\{\{(.+?)\}\}", k)
                inner = re.fullmatch(r"Clay\.formatForAIPrompt\((.*)\)", v, re.S)
                if not m or not inner:
                    raise ValueError(f"sync_claygent_column: cannot read the existing mapping for {k!r}; pass var_map")
                var_map[m.group(1)] = inner.group(1)
        # byo_key=False: _metadata is never rewritten here (it is kept as found below).
        inputs = self.claygent_column_inputs(cid, var_map, byo_key=False)
        binding = []
        for name in list(inputs) + [n for n in b if n not in inputs]:
            if name in self._CLAYGENT_OWNED_INPUTS:
                val = inputs[name]
                if isinstance(val, dict):
                    binding.append({"name": name, "formulaMap": val})
                elif val:
                    binding.append({"name": name, "formulaText": val})
                else:
                    binding.append({"name": name})
            elif name in b:
                binding.append(b[name])
            elif name != "_metadata":
                binding.append({"name": name})
        new_ts = dict(ts, inputsBinding=binding)
        if condition is not None:
            new_ts["conditionalRunFormulaText"] = condition
        self.update_column(table_id, field_id, {"typeSettings": new_ts})
        check = self.verify_claygent_column(table_id, field_id, var_map=var_map)
        if not check["ok"]:
            raise RuntimeError(f"sync did not stick on {field_id}: {check['problems']}")
        return check

    @staticmethod
    def _close_paren(s: str, i: int) -> int:
        """Index of the ')' that closes the '(' at s[i], skipping string literals;
        -1 when there is none."""
        depth = 0
        while i < len(s):
            ch = s[i]
            if ch in "\"'`":
                j = i + 1
                while j < len(s) and s[j] != ch:
                    j += 2 if s[j] == "\\" else 1
                if j >= len(s):
                    return -1
                i = j
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return i
            i += 1
        return -1

    @staticmethod
    def _prompt_formula_segments(formula: str, slots=()) -> list[str] | None:
        """A stored prompt formula as the literal text around its
        Clay.formatForAIPrompt(...) slots: n slots give n + 1 segments (whitespace
        around `+` is ignored). None when the formula is anything other than
        `+`-joined JSON string literals and such slots.

        slots: the column's own mapping expressions ('Clay.formatForAIPrompt(<expr>)',
        what Clay renders into the prompt). A slot is matched against them first,
        longest first; only a slot that is none of them is read up to its closing
        ')' by bracket matching, which skips string literals but not regex literals."""
        slot = "Clay.formatForAIPrompt("
        known = sorted({s for s in slots if isinstance(s, str) and s.startswith(slot) and s.endswith(")")},
                       key=len, reverse=True)
        dec = json.JSONDecoder(strict=False)
        segs, i, n, operand_next, started = [""], 0, len(formula), True, False
        while True:
            while i < n and formula[i].isspace():
                i += 1
            if i == n:
                return None if (operand_next and started) else segs  # a dangling `+` is malformed
            if not operand_next:
                if formula[i] != "+":
                    return None
                i, operand_next = i + 1, True
            elif formula[i] == '"':
                try:
                    text, i = dec.raw_decode(formula, i)
                except ValueError:
                    return None
                segs[-1] += text
                operand_next, started = False, True
            elif formula.startswith(slot, i):
                hit = next((s for s in known if formula.startswith(s, i)), None)
                if hit:
                    i += len(hit)
                else:
                    i = ClayClient._close_paren(formula, i + len(slot) - 1)
                    if i < 0:
                        return None
                    i += 1
                segs.append("")
                operand_next, started = False, True
            else:
                return None

    @staticmethod
    def _norm_prompt_segments(segs: list[str]) -> list[str]:
        """Prompt text with the whitespace a UI save re-flows taken out: trailing
        spaces at line ends, runs of blank lines, and whitespace at either end of
        the whole prompt."""
        out = []
        for s in segs:
            s = re.sub(r"[ \t]+\n", "\n", s.replace("\r\n", "\n"))
            out.append(re.sub(r"\n{2,}", "\n", s))
        out[0] = out[0].lstrip()
        out[-1] = out[-1].rstrip()
        return out

    @staticmethod
    def _claygent_prompt_problems(version: dict, stored_prompt: str, slots=()) -> list[str]:
        """verify_claygent_column's prompt check: the stored prompt must hold one
        slot per variable placeholder in the Claygent's prompt and the same text
        between them, whitespace re-flow aside. slots: see _prompt_formula_segments."""
        names = [x["name"] for x in version.get("variables") or []]
        raw = ClayClient._prompt_formula_segments(stored_prompt, slots)
        if raw is None:
            return ["stored prompt is not a +-joined list of string literals and Clay.formatForAIPrompt(...) slots"]
        want = ClayClient._norm_prompt_segments(ClayClient._claygent_split(version.get("userPrompt") or "", names)[::2])
        have = ClayClient._norm_prompt_segments(raw)
        problems = []
        if len(have) == len(want):
            pairs = list(zip(want, have))
        else:  # the slot counts differ: compare the text alone, wherever the slots sit
            w_text, h_text = [t for t in want if t], [t for t in have if t]
            pad = max(len(w_text), len(h_text))
            pairs = [] if "".join(w_text) == "".join(h_text) else list(
                zip(w_text + [""] * (pad - len(w_text)), h_text + [""] * (pad - len(h_text))))
        diff = next(((w, h) for w, h in pairs if w != h), None)
        if diff:
            problems.append(f"stored prompt text differs from the Claygent prompt near: {(diff[0] or diff[1])[:60]!r}")
        if len(have) != len(want):
            problems.append(f"stored prompt has {len(have) - 1} variable slot(s), the Claygent prompt has {len(want) - 1}")
        return problems

    def verify_claygent_column(self, table_id: str, field_id: str,
                               var_map: dict[str, str] | None = None) -> dict:
        """READ-ONLY: does the column still say what its Claygent says? Checks
        - the Claygent binding;
        - the model;
        - the schema copy: it must decode to what claygent_column_inputs writes for
          the Claygent's CURRENT output format (JSON Schema: same schema; Fields:
          same fields; same type/jsonType markers). Any other format is reported
          as "schema copy not checked", never passed;
        - the prompt: read as text pieces and Clay.formatForAIPrompt(...) slots, it
          must hold exactly one slot per variable placeholder in the Claygent's
          prompt and the same text between them. Whitespace a UI save re-flows is
          ignored (spaces around `+`, trailing spaces at line ends, runs of blank
          lines, either end of the prompt). A mismatch means the stored prompt
          differs from the Claygent's current prompt, e.g. the Claygent was edited
          after the column was last written. Slots are matched against the
          column's own mapping expressions first, so any var_map expression reads
          back; a slot that is none of them is read by bracket matching, which
          does not understand regex literals;
        - the mapping covers exactly the Claygent's variables; with var_map, also
          each mapping expression.
        Returns {"ok", "problems", "claygent_id"}. Use after anyone opened the
        column's panel in the UI: opening it can arm "Save", and saving re-writes
        Clay's re-parsed copy.
        """
        ts, b = self._claygent_column_settings(table_id, field_id, "verify_claygent_column")
        problems = []
        cid = self._binding_literal(b.get("claygentId"))
        if not cid or not isinstance(cid, str):
            return {"ok": False, "problems": ["column is not bound to a saved Claygent"], "claygent_id": None}
        v = self.get_claygent(cid)["currentVersion"]
        if self._binding_literal(b.get("model")) != v["modelSettings"].get("model"):
            problems.append("model differs from the Claygent's")
        try:
            want = self._claygent_schema_copy(v.get("outputFormat"))
        except ValueError as e:
            problems.append(f"schema copy not checked: {e}")
        else:
            have = self._decoded_schema_copy((b.get("answerSchemaType") or {}).get("formulaMap") or {})
            if have is None or have != self._decoded_schema_copy(want):
                problems.append("output schema copy differs from the Claygent's (UI: 'Unable to parse the output schema')")
        fm = (b.get("claygentFieldMapping") or {}).get("formulaMap") or {}
        problems += self._claygent_prompt_problems(v, (b.get("prompt") or {}).get("formulaText") or "", fm.values())
        wanted = {"{{" + x["name"] + "}}" for x in v.get("variables") or []}
        if set(fm) != wanted:
            problems.append(f"variable mapping {sorted(fm)} != Claygent variables {sorted(wanted)}")
        for k, e in (var_map or {}).items():
            if fm.get("{{" + k + "}}") != "Clay.formatForAIPrompt(" + e + ")":
                problems.append(f"variable {k!r} is mapped to {fm.get('{{' + k + '}}')!r}, expected {e!r}")
        return {"ok": not problems, "problems": problems, "claygent_id": cid}

    @staticmethod
    def unwrap_claygent_output(value: Any, keys: list[str]) -> dict:
        """A Claygent answer as a new dict holding `keys`. Clay does NOT enforce a
        JSON Schema's shape: answers sometimes arrive one level down, e.g.
        {"body": {...}} or {"parameters": {...}}, despite additionalProperties:
        false (2026-09-30), and Clay merges its own keys (CLAYGENT_META_KEYS:
        reasoning, confidence, stepsTaken, ...) into the cell's object.

        Decides on the requested keys that are not Clay metadata names (on all
        of them when every one is), and picks the object holding all of those:
        the top level first, then each object one level down, in order. Failing
        that, the object holding the most of them (the top level wins a tie);
        else {}. Clay's metadata keys are removed from the result unless you
        asked for them in `keys`. A partial result is returned as it is: check
        that every required key is present (a missing required key is the
        truncation signal, not totalOutputTokens == 4096; see clay-api-reference
        "Saved Claygents"). Ask for totalOutputTokens in `keys` to keep it.
        Also accepts a JSON string.
        """
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except Exception:
                return {}
        if not isinstance(value, dict):
            return {}
        meta = ClayClient.CLAYGENT_META_KEYS
        deciding = [k for k in keys if k not in meta] or list(keys)
        candidates = [value] + [v for v in value.values() if isinstance(v, dict)]
        best = next((c for c in candidates if all(k in c for k in deciding)), None)
        if best is None:
            best = max(candidates, key=lambda c: sum(k in c for k in deciding))
            if not any(k in best for k in deciding):
                return {}
        return {k: v for k, v in best.items() if k not in meta or k in keys}

    def create_formula_column(self, table_id: str, name: str,
                              formula_text: str, view_id: str = None,
                              data_type: str = "text",
                              mapped_result_path: list[str] = None) -> dict:
        """
        Create a formula column using the create-as-text-then-PATCH pattern.

        mapped_result_path: required for nested enrichment data.
          e.g. ["experience", "0", "url"] to extract company LinkedIn URL
          from Enrich Person > experience > 0 > url.
          Without this, nested paths return empty even with the correct formula.
        """
        # Step 1: create as text
        field = self.create_column(table_id, {
            "type": "text",
            "name": name,
            "typeSettings": {"dataTypeSettings": {"type": data_type}},
        }, view_id=view_id)
        field_id = (field.get("field") or field).get("id") or field.get("id")

        # Step 2: PATCH with formula
        patch_ts = {
            "formulaText": formula_text,
            "formulaType": "text",
            "dataTypeSettings": {"type": data_type},
        }
        if mapped_result_path:
            patch_ts["mappedResultPath"] = mapped_result_path
            patch_ts["formula"] = formula_text

        self.update_column(table_id, field_id, {"typeSettings": patch_ts})
        return {"id": field_id, "name": name}

    def set_condition(self, table_id: str, field_id: str, condition: str) -> dict:
        """
        Set "Only run if" condition on an action column.
        condition: formula expression, e.g. 'Number({{f_employees}}) > 5'
        """
        raw = self.get_table(table_id)
        table = raw.get("table", raw)
        for f in table.get("fields", []):
            if f["id"] == field_id:
                ts = f.get("typeSettings", {})
                ts["conditionalRunFormulaText"] = condition
                return self.update_column(table_id, field_id, {"typeSettings": ts})
        raise ValueError(f"Field {field_id} not found in table {table_id}")

    # ── Sources ────────────────────────────────────────────────────────────────

    def create_webhook_source(self, table_id: str, name: str = "Webhook") -> dict:
        """Create a webhook source on a table. Returns source dict with webhook URL.

        NOT ready to ingest by itself (verified 2026-07-24): the new source has
        `sourceSubscriptions: []` — POSTs to its webhook URL return OK and
        increment `state.numSourceRecords`, but NO table rows appear (records
        buffer server-side). You must also create a source FIELD to register
        the subscription, which retroactively materializes the buffered
        records:

            clay.create_column(table_id, {
                "type": "source",
                "name": "Webhook",
                "typeSettings": {"sourceIds": [source["id"]], "canCreateRecords": True},
            })
            # get_source(source_id) then shows sourceSubscriptions [{tableId, fieldId}]

        Also: the webhook endpoint does NOT fan out JSON arrays — an array of
        N objects counts as ONE source record and does not create N rows
        (verified 2026-07-24). Send one object per POST."""
        res = self.post("/sources", {
            "workspaceId": int(self.workspace_id),
            "tableId": table_id,
            "name": name,
            "type": "webhook",
            "typeSettings": {},
        })
        source = res.get("source", res)
        return source

    # Known-good SUBROUTINE input semantic types, observed across Clay-managed
    # functions (verified 2026-08-05). ⚠ UNKNOWN values don't just fail — they 500
    # the ENTIRE workspace tools registry (GET /workspaces/{ws}/tools) until the
    # offending SUBROUTINE_INPUTS entry is reverted. Never guess new values.
    SUBROUTINE_SEMANTIC_TYPES = {
        "company-domain", "company-linkedin-url", "company-name",
        "date", "unknown", "person-linkedin-url", "email",
    }

    # write-to-cell = the send-back action every Clay function uses to return its
    # result to the calling cell (verified 2026-08-06). Origin coords arrive on each
    # intake row under the function's OWN source field — bind {{<your_src_fid>}}
    # ?.origin?.<key>, NEVER the literal `f_subroutine_source` (that's the id of
    # Clay-managed functions' source field; cloned verbatim it shows "deleted column"
    # errors and the column silently never runs).
    WRITE_TO_CELL_PACKAGE = "b1ab3d5d-b0db-4b30-9251-3f32d8b103c1"
    FUNCTION_ORIGIN_KEYS = ("recordId", "tableId", "fieldId", "asyncCallbackId",
                            "workflowRunId", "stepId", "searchId", "entityId",
                            "enrichmentId", "toolId", "isTestRun")

    def register_tool(self, tool_id: str, *, tool_type: str, name: str,
                      entity_type: str | None = None, description: str = "",
                      input_schema: dict | None = None,
                      integrations: tuple = ("api",)) -> dict:
        """Register a workspace TOOL so the public Routines API can run it
        (verified 2026-07-31 for workflows, 2026-08-05 for functions).

        tool_id: "workflow:wf_..." or "function:t_...". tool_type: "workflow"|"function".
        entity_type: REQUIRED for functions ("contact"|"company" — the API names the
        field in its 400 if omitted); ignored for workflows.

        Registry facts (all live-verified):
        - UI/Sculptor-built workflows auto-register; CLI/MCP/claycast-built resources
          do NOT — call this yourself.
        - Records are effectively immutable: no PATCH route (404), POST on an existing
          id → 409. To change one, DELETE (if supported) and re-POST.
        - For WORKFLOW tools the registry input_schema IS the runtime item validator
          (empty-string values are stripped pre-spread; nulls are type-rejected;
          whitespace survives). For FUNCTION tools, runtime validation comes from the
          table's SUBROUTINE_INPUTS instead — the registry schema is documentation.
        - The empty-string strip also applies on the in-table execute-subroutine
          caller path (verified 2026-08-06) — a stripped "" lets function-side
          defaults apply (preserving downstream values instead of blanking them);
          "" also fails required-input resolution MID-workflow, not just at the
          items boundary. Sentinel convention (" "/"NONE") applies end-to-end.
        After registering: POST /public/v0/routines/{tool_id}/run (clay-api-key) with
        {"items": [{"id": ..., "inputs": {...}}]} → 202 {routine_run_id}; results at
        GET /public/v0/routines/run/{routine_run_id}/results.
        """
        if tool_type not in ("workflow", "function"):
            raise ValueError(f"register_tool: tool_type must be workflow|function, got {tool_type!r}")
        if tool_type == "function" and entity_type not in ("contact", "company"):
            raise ValueError("register_tool: functions require entity_type 'contact' or 'company'")
        body = {
            "id": tool_id, "type": tool_type, "name": name, "description": description,
            "access": {"integrations": list(integrations)},
            "inputSchema": input_schema or {"type": "object", "properties": {}, "required": []},
        }
        if tool_type == "function":
            body["entityType"] = entity_type
        return self.post(f"/workspaces/{self.workspace_id}/tools", body)

    def create_function(self, name: str, inputs: list[dict], *,
                        entity_type: str = "contact", description: str = "",
                        extractors: dict | None = None,
                        success_field: str | None = None,
                        send_back: dict | None = None,
                        register: bool = True) -> dict:
        """Create a Clay FUNCTION — a UI-style subroutine table (NOT a workflow) —
        entirely via the internal API (recipe verified end-to-end 2026-07-31).

        inputs: [{"name": str, "optional": bool = True, "semantic_type": str|None}].
        semantic_type is validated against SUBROUTINE_SEMANTIC_TYPES (unknown values
        500 the workspace tools registry — see class attr). Inputs are SCALAR-ONLY:
        there is no json/object type; objects are rejected at run validation.
        JSON-string inputs land and ARE parseable in formulas via an expression IIFE
        (((s) => s ? JSON.parse(s) : ({}))({{col}})) — direct ?.key access on the
        string returns nothing (corrected 2026-08-06).
        Callers may send undeclared extra inputs (tolerated) — declare only what the
        function consumes.

        extractors: {column_name: input_name} → creates formula columns
        `{{source_field}}?.input_name` (these compute on row arrival even with
        AUTO_RUN off). success_field: extractor column name to mark as the
        pass-through output (IS_PASS_THROUGH_TABLE). KNOWN GAP (2026-07-31):
        pass-through completion does not fire for API-created functions — routine
        runs deliver their row but linger "in_progress" for pollers; wire the
        managed-style `write-to-cell` send-back (package
        b1ab3d5d-b0db-4b30-9251-3f32d8b103c1, `{{f_subroutine_source}}?.origin?.*`
        bindings) if callers must await results. The send-back column MUST carry
        actionVersion 1 + dataTypeSettings {"type": "json"} (verified 2026-07-31)
        — this method sets both; a MANUAL build via create_action_column defaults
        to text (write-to-cell matches no _JSON_RESULT_ACTION_HINTS) and the
        send-back silently misdelivers.

        Returns {"table_id", "routine_id", "source_id", "source_field_id",
        "extractor_ids", "registered"}. The function is created dark (AUTO_RUN off).
        """
        subs = []
        for i in inputs:
            st = i.get("semantic_type")
            if st is not None and st not in self.SUBROUTINE_SEMANTIC_TYPES:
                raise ValueError(
                    f"create_function: unknown semantic_type {st!r} for input "
                    f"{i.get('name')!r} — unknown values 500 the workspace tools "
                    f"registry. Known-good: {sorted(self.SUBROUTINE_SEMANTIC_TYPES)}")
            entry = {"inputName": i["name"], "optional": bool(i.get("optional", True))}
            if st:
                entry["semanticTypeEnum"] = st
            subs.append(entry)

        table = self.create_table(name).get("table") or {}
        tid = table.get("id") or self.create_table(name)["id"]
        cur = self.get_table(tid).get("table", {})
        ts = cur.get("tableSettings") or {}
        ts.update({"BLOCK_TYPE": "SUBROUTINE",
                   "BLOCK_SETTINGS": {"blockType": "SUBROUTINE"},
                   "SUBROUTINE_INPUTS": subs, "AUTO_RUN_ON": False})
        self.patch(f"/tables/{tid}", {"tableSettings": ts})

        src = self.post("/sources", {"workspaceId": int(self.workspace_id),
                                     "name": "Function inputs", "type": "manual",
                                     "typeSettings": {"type": "subroutine"}})
        sid = (src.get("source") or src).get("id")
        fld = self.post(f"/tables/{tid}/fields", {
            "name": "Function inputs", "type": "source",
            "typeSettings": {"sourceIds": [sid], "canCreateRecords": True}})
        src_fid = (fld.get("field") or fld).get("id")

        ex_ids = {}
        for col, inp in (extractors or {}).items():
            r = self.create_formula_column(tid, col, "{{%s}}?.%s" % (src_fid, inp),
                                           data_type="text")
            ex_ids[col] = (r.get("field", r)).get("id")
        if success_field:
            if success_field not in ex_ids:
                raise ValueError(f"create_function: success_field {success_field!r} "
                                 "must name one of the extractors")
            cur2 = self.get_table(tid).get("table", {})
            ts2 = cur2.get("tableSettings") or {}
            ts2["IS_PASS_THROUGH_TABLE"] = True
            ts2["PASS_THROUGH_TABLE_SUCCESS_FIELD_IDS"] = [ex_ids[success_field]]
            self.patch(f"/tables/{tid}", {"tableSettings": ts2})

        sendback_fid = None
        if send_back:
            bad = [v for v in send_back.values() if v not in ex_ids]
            if bad:
                raise ValueError(f"create_function: send_back values must name extractor "
                                 f"columns; unknown: {bad}")
            binding = [{"name": k, "formulaText": "{{%s}}?.origin?.%s" % (src_fid, k)}
                       for k in self.FUNCTION_ORIGIN_KEYS]
            binding.append({"name": "data", "formulaMap": {
                out: "{{%s}}" % ex_ids[col] for out, col in send_back.items()}})
            r = self.post(f"/tables/{tid}/fields", {
                "name": "Send data back", "type": "action",
                "typeSettings": {"actionKey": "write-to-cell",
                                 "actionPackageId": self.WRITE_TO_CELL_PACKAGE,
                                 "actionVersion": 1,
                                 "dataTypeSettings": {"type": "json"},
                                 "inputsBinding": binding}})
            sendback_fid = (r.get("field") or r).get("id")
            # completion requires the pipeline to run per intake row
            cur3 = self.get_table(tid).get("table", {})
            ts3 = cur3.get("tableSettings") or {}
            ts3["AUTO_RUN_ON"] = True
            self.patch(f"/tables/{tid}", {"tableSettings": ts3})

        routine_id = f"function:{tid}"
        registered = False
        if register:
            props = {i["name"]: {"type": "string"} for i in inputs}
            req = [i["name"] for i in inputs if not i.get("optional", True)]
            self.register_tool(routine_id, tool_type="function", name=name,
                               entity_type=entity_type, description=description,
                               input_schema={"type": "object", "properties": props,
                                             "required": req})
            registered = True
        return {"table_id": tid, "routine_id": routine_id, "source_id": sid,
                "source_field_id": src_fid, "extractor_ids": ex_ids,
                "send_back_field_id": sendback_fid, "registered": registered}

    def create_function_sandbox(self, fn_table_id: str, view_id: str | None = None) -> dict:
        """Open (or reuse) the edit sandbox for a LIVE function table.

        Once a function gains a caller (execute-subroutine subscription), the parent
        table locks: abilities.canUpdate=false, direct field/settings/run writes 403
        ("You do not have the proper access for this table") — but
        canUpdateFromSandbox=true. Edit path (verified 2026-08-06): create sandbox →
        mutate the SANDBOX table id with normal field/settings calls (fids match the
        parent) → publish. POSTing again returns the existing open sandbox."""
        if view_id is None:
            view_id = self.get_table(fn_table_id).get("table", {}).get("firstViewId")
        r = self.post(f"/workspaces/{self.workspace_id}/subroutines/{fn_table_id}/sandbox",
                      {"viewId": view_id})
        return r.get("sandboxTable") or r

    def publish_function_sandbox(self, fn_table_id: str, sandbox_table_id: str,
                                 run_changes: bool = False) -> dict:
        """Publish sandbox edits onto the live function. Body {"runChanges": bool} is
        REQUIRED — publishing without it 400s. Returns {"fieldIds": [changed]}.
        Discard instead with DELETE /workspaces/{ws}/tables/{fn}/sandbox/{sb}."""
        return self.post(
            f"/workspaces/{self.workspace_id}/tables/{fn_table_id}/sandbox/"
            f"{sandbox_table_id}/publish", {"runChanges": run_changes})

    def list_sources(self, table_id: str) -> list[dict]:
        """
        Return Clay's raw `GET /sources?tableId={table_id}` response.

        Real Clay behavior is subscription-scoped: sources with
        `sourceSubscriptions: []` do NOT appear here, even if the source
        exists and `get_source(source_id)` can fetch it directly.
        """
        return self.get("/sources", params={"tableId": table_id})

    def get_source(self, source_id: str, *, include_subscriptions: bool = True) -> dict:
        """Fetch one source by id."""
        source = self.get(f"/sources/{source_id}")
        if not include_subscriptions and isinstance(source, dict):
            source = dict(source)
            source.pop("sourceSubscriptions", None)
        return source

    # ── Sourced tables (Find People / Find Companies) ───────────────────────
    # `preview_sourced_table` and `create_sourced_table` verified live
    # 2026-05-01 against workspace 12345 for both `cpj_type="people"` and
    # `cpj_type="companies"`. This includes Companies preview hard-cap
    # enforcement at 50 rows, `conversation_id` creation, and
    # `destination_table_id` append-mode acceptance. Unsupported `cpj_type`
    # raises ValueError.

    def create_sourced_table(
        self,
        workbook_name: str,
        *,
        inputs: dict,
        cpj_type: str = "people",
        basic_fields_override: list[dict] | None = None,
        workbook_id: str | None = None,
        destination_table_id: str | None = None,
        conversation_id: str | None = None,
        preview_action_task_id: str | None = None,
        workspace_id: int | str | None = None,
    ) -> dict:
        """Create a Find People / Find Companies sourced table.

        `cpj_type` accepts any captured CPJ source type currently in
        `_CPJ_TYPE_SETTINGS` (`"people"` and `"companies"` as of 2026-04-30).

        `basic_fields_override` is an escape hatch for advanced callers who
        want to replace Clay's default starter columns. When omitted, ClayCast uses
        stable built-in defaults. For Companies specifically, ClayCast defaults the
        `Size` field to plain text instead of shipping frontend-captured select
        option UUIDs. If you explicitly want the legacy select-based Size
        column, import the module-level helper and pass
        `basic_fields_override=companies_basic_fields_with_select_size()`.

        `destination_table_id` appends into an existing table instead of
        creating a new one. Clay accepted this live for Companies on
        2026-05-01, but overlapping searches may still dedupe to zero net new
        rows.
        """
        if not workbook_name or not str(workbook_name).strip():
            raise ValueError("create_sourced_table: workbook_name is required")
        if not isinstance(inputs, dict):
            raise ValueError("create_sourced_table: inputs must be a dict")
        if basic_fields_override is not None and not isinstance(basic_fields_override, list):
            raise ValueError("create_sourced_table: basic_fields_override must be a list of field dicts")
        if workbook_id and destination_table_id:
            raise ValueError(
                "create_sourced_table: workbook_id and destination_table_id are mutually exclusive"
            )
        cpj_type = str(cpj_type).lower()
        if cpj_type not in _CPJ_TYPE_SETTINGS:
            raise ValueError(
                f"create_sourced_table: cpj_type must be one of {sorted(_CPJ_TYPE_SETTINGS)}, got {cpj_type!r}"
            )

        inputs_with_limit = copy.deepcopy(inputs)
        if "limit" in inputs_with_limit:
            limit = int(inputs_with_limit["limit"])
            if limit < 1 or limit > 50000:
                raise ValueError("create_sourced_table: inputs['limit'] must be between 1 and 50000")
            inputs_with_limit["limit"] = limit
        else:
            inputs_with_limit["limit"] = 50000
        for field_name in ("company_identifier", "exclude_entities_configuration"):
            if field_name in inputs_with_limit:
                if inputs_with_limit[field_name] is None:
                    inputs_with_limit[field_name] = []
                elif not isinstance(inputs_with_limit[field_name], list):
                    raise ValueError(f"create_sourced_table: inputs['{field_name}'] must be a list")
        if "search_raw_location" in inputs_with_limit:
            if inputs_with_limit["search_raw_location"] is None:
                inputs_with_limit["search_raw_location"] = False
            elif not isinstance(inputs_with_limit["search_raw_location"], bool):
                raise ValueError("create_sourced_table: inputs['search_raw_location'] must be a bool")

        ws_id = self._resolve_workspace_id(workspace_id)
        type_settings = _CPJ_TYPE_SETTINGS[cpj_type]
        if basic_fields_override is not None:
            basic_fields = copy.deepcopy(basic_fields_override)
        else:
            basic_fields = copy.deepcopy(_CPJ_BASIC_FIELDS[cpj_type])
        cpj_config = {
            "type": cpj_type,
            "typeSettings": {**type_settings, "inputs": inputs_with_limit},
            "clientSettings": {"tableType": _CPJ_CLIENT_TABLE_TYPE[cpj_type]},
            "basicFields": basic_fields,
        }
        if destination_table_id:
            cpj_config["destinationTableId"] = destination_table_id
        if preview_action_task_id:
            cpj_config["previewActionTaskId"] = preview_action_task_id

        body = {
            "workspaceId": str(ws_id),
            "workbookName": workbook_name,
            "workbookId": workbook_id,
            "assignedFieldId": _CPJ_ASSIGNED_FIELD_ID[cpj_type],
            "cpjConfig": cpj_config,
        }
        if conversation_id is not None:
            body["conversationId"] = conversation_id
        return self.post("/sources/create-cpj-table", body)

    def preview_sourced_table(
        self,
        inputs: dict,
        *,
        cpj_type: str = "people",
        workspace_id: int | str | None = None,
    ) -> dict:
        """DEAD as of 2026-07-23 — this call now returns HTTP 400.

        Clay removed the `*-preview` enrichmentTypes from the
        POST /v3/actions/run-enrichment server allowlist (now only
        find-and-enrich-personal-linkedin, enrich-personal-linkedin-url,
        enrich-company, claygent, find-employee-headcount,
        search-companies-from-table). Free-preview workaround: the OFFICIAL
        `clay` CLI search (`clay search filters-mode create/run`) or the
        public API /public/v0/search/filters-mode — both free.

        Historical behavior (verified 2026-05-01): ran the zero-credit preview
        for a Find People / Find Companies search without creating a table;
        Clay hard-capped the preview at exactly 50 rows."""
        if not isinstance(inputs, dict):
            raise ValueError("preview_sourced_table: inputs must be a dict")
        cpj_type = str(cpj_type).lower()
        if cpj_type not in _CPJ_TYPE_SETTINGS:
            raise ValueError(
                f"preview_sourced_table: cpj_type must be one of {sorted(_CPJ_TYPE_SETTINGS)}, got {cpj_type!r}"
            )

        preview_inputs = copy.deepcopy(inputs)
        if "limit" in preview_inputs and int(preview_inputs["limit"]) != 50:
            raise ValueError(
                "preview_sourced_table: Clay's preview endpoint is hard-capped at exactly 50 rows; "
                "omit inputs['limit'] or set it to 50"
            )
        preview_inputs["limit"] = 50

        ws_id = self._resolve_workspace_id(workspace_id)
        body = {
            "workspaceId": str(ws_id),
            "enrichmentType": _CPJ_TYPE_SETTINGS[cpj_type]["previewActionKey"],
            "options": {"sync": True, "returnTaskId": True, "returnActionMetadata": True},
            "inputs": preview_inputs,
        }
        return self.post("/actions/run-enrichment", body)

    # ── Enrichments / Actions ─────────────────────────────────────────────────

    def search_enrichments(self, query: str) -> list[dict]:
        """Search available enrichment actions by keyword."""
        body = {
            "userQuery": query,
            "types": ["action", "waterfall", "template", "source_action"],
        }
        res = self.post(f"/enrichment-search/{self.workspace_id}/query", body)
        return res.get("results", [])

    def list_actions(self) -> list[dict]:
        """List all action definitions available in the workspace.

        The underlying `/actions` endpoint requires a workspaceId query param —
        without it, Clay's API returns HTTP 400 with
        `"workspaceId" is required`.
        """
        res = self.get("/actions", params={"workspaceId": self.workspace_id})
        return res.get("actions", res)

    def list_auth_accounts(self) -> list[dict]:
        """List all connected integration accounts (OpenAI, LeadMagic, etc.)"""
        res = self.get(f"/workspaces/{self.workspace_id}/app-accounts")
        return res if isinstance(res, list) else res.get("accounts", [])

    def list_subroutines(self) -> list[dict]:
        """Raw `GET /workspaces/{ws}/subroutines` — the workspace's function
        tables exactly as Clay returns them (each item wraps `table` plus
        pricing/usage metadata).

        Prefer `list_function_tables()`, which flattens the same payload into
        `list_tables()`-shaped, `_kind`-tagged entries.
        """
        res = self.get(f"/workspaces/{self.workspace_id}/subroutines")
        return res.get("subroutines", res)

    # ── Auth account inspection ──────────────────────────────────────────────
    # All methods in this section verified live 2026-04-30 against workspace 12345
    # (Tier A+B+C smoke; paths, unwrap shapes, query-param plumbing all confirmed).

    def get_auth_account(
        self,
        auth_account_id: str,
        *,
        resource_type: str | None = None,
        resource_id: dict | None = None,
        workspace_id: int | str | None = None,
    ) -> dict:
        """Get one connected auth account by id. Use this to look up a specific
        `auth_account_id` you'll pass to `apply_preset(auth_account_id=...)` or
        `create_action_column(auth_account_id=...)`.

        Endpoint: `GET /workspaces/{ws}/app-accounts/accounts/{aa_id}`.

        Returns the account dict including `id`, `name`, `appAccountTypeId`,
        `enabledScopes`, `abilities`, etc. Obfuscated credentials are not
        returned (`obfuscatedCredentials` is null in the API response).

        Optional context filters (mirror what the Clay UI sends when looking
        up an account from inside an action-column config):
        - `resource_type="action-field"` and
        - `resource_id={"tableId": "t_…", "fieldId": "f_…"}`
        scope the result to "is this account usable for this column?" — the
        response shape is the same, but `abilities` may differ.
        """
        ws_id = self._resolve_workspace_id(workspace_id)
        params = {}
        if resource_type:
            params["resourceType"] = resource_type
        if resource_id is not None:
            import json as _json
            params["resourceId"] = _json.dumps(resource_id, separators=(",", ":"))
        return self.get(
            f"/workspaces/{ws_id}/app-accounts/accounts/{auth_account_id}",
            params=params or None,
        )

    def list_auth_accounts_by_type(
        self,
        account_type: str,
        *,
        resource_type: str | None = None,
        resource_id: dict | None = None,
        workspace_id: int | str | None = None,
    ) -> list[dict]:
        """List connected auth accounts of one integration type
        (e.g. `"salesforce"`, `"hubspot"`, `"clay-sequencer-smartlead"`).

        Endpoint: `GET /workspaces/{ws}/app-accounts/accounts/type/{type}`.

        Returns a list of account dicts. Use this to find the right
        `auth_account_id` when an integration has multiple connected accounts.

        **By default this is the broad, integration-wide list** — every
        connected account of the given type, regardless of context. To get
        the UI's context-aware account picker behavior (only accounts
        usable for a specific table+field), pass
        `resource_type="action-field"` and
        `resource_id={"tableId": "t_…", "fieldId": "f_…"}`.
        """
        ws_id = self._resolve_workspace_id(workspace_id)
        params = {}
        if resource_type:
            params["resourceType"] = resource_type
        if resource_id is not None:
            import json as _json
            params["resourceId"] = _json.dumps(resource_id, separators=(",", ":"))
        res = self.get(
            f"/workspaces/{ws_id}/app-accounts/accounts/type/{account_type}",
            params=params or None,
        )
        return res if isinstance(res, list) else res.get("accounts", [])

    def list_auth_account_types(self) -> list[dict]:
        """List every integration type Clay supports (not filtered by what's
        connected). Useful for catalog browsing — what CAN we connect?

        Endpoint: `GET /app-accounts/types` (no workspace required).

        Returns a list of type dicts with `id`, `authenticationType` (e.g.
        `"oauth"`, `"api_key"`), and `displayMetadata`.
        """
        res = self.get("/app-accounts/types")
        return res if isinstance(res, list) else res.get("types", [])

    def get_auth_account_type(
        self,
        account_type: str,
        *,
        workspace_id: int | str | None = None,
    ) -> dict:
        """Get full metadata for one integration type, including auth methods
        (e.g. Salesforce supports both `salesforce-user-sign-in` and
        `salesforce-client-credentials`).

        Endpoint: `GET /app-accounts/type/{type}` (with optional
        `?workspaceId=` for workspace-aware fields).

        Returns a dict with `authMethods`, `displayMetadata`,
        `validateAuthActionInfo`, etc.
        """
        params = {}
        if workspace_id is not None or self.workspace_id is not None:
            params["workspaceId"] = self._resolve_workspace_id(workspace_id)
        return self.get(f"/app-accounts/type/{account_type}", params=params)

    def validate_auth_credentials(
        self,
        auth_account_id: str,
        *,
        action_context: dict | None = None,
        workspace_id: int | str | None = None,
    ) -> dict:
        """Validate a connected auth account by invoking Clay's
        `<type>-validate-auth` action. The Clay UI runs this when a user
        clicks "Test connection" on an existing account.

        Endpoint: `POST /app-accounts/{type}/validate-auth`.

        Body the UI actually sends:
            {
              "appAccountType": <full type metadata>,
              "workspaceId": <ws_id>,
              "authMethod": {"type": "app-account-id", "appAccountId": <aa_id>},
              "inputs": {"actionKey": ..., "actionPackageId": ...}
            }

        ClayCast builds this for you:
        - `auth_account_id` (required) selects which connected account to test
        - the account's `appAccountTypeId` is fetched and used to find the
          matching type metadata + the type's validate-auth action info
        - `inputs` defaults to `typeSpecific.validateAuthActionInfo` (the
          validate-auth action itself); pass `action_context={actionKey,
          actionPackageId}` to validate inside a specific action's context

        Returns `{"status": "ok"|"error", "message": str, "actionMetadata": {...}}`.

        **Credit note:** invokes a Clay action. Cost is typically 0 (validate-auth
        actions are free), but inspect `actionMetadata.upfrontCreditUsage` after
        the call to confirm — Clay may bill differently per integration.
        """
        ws_id = self._resolve_workspace_id(workspace_id)
        acct = self.get_auth_account(auth_account_id, workspace_id=ws_id)
        account_type = acct.get("appAccountTypeId")
        if not account_type:
            raise ValueError(
                f"validate_auth_credentials: account {auth_account_id} has no appAccountTypeId"
            )
        type_meta = self.get_auth_account_type(account_type, workspace_id=ws_id)

        if action_context is None:
            vai = (type_meta.get("typeSpecific") or {}).get("validateAuthActionInfo") or {}
            action_context = {
                "actionKey": vai.get("actionKey"),
                "actionPackageId": vai.get("actionPackageId"),
            }

        body = {
            "appAccountType": type_meta,
            "workspaceId": ws_id,
            "authMethod": {"type": "app-account-id", "appAccountId": auth_account_id},
            "inputs": action_context,
        }
        return self.post(f"/app-accounts/{account_type}/validate-auth", body)

    # ── Sources / runs ───────────────────────────────────────────────────────
    # `list_source_runs` verified live 2026-04-30 against workspace 12345;
    # `limit` was discovered to be a Clay-required param during that test.

    def list_source_runs(
        self,
        source_id: str,
        *,
        limit: int = 50,
    ) -> list[dict]:
        """List recent runs of a webhook / data source.

        Endpoint: `GET /sources/{source_id}/runs?limit=N`.

        `limit` is **required** by Clay's API — calls without it return HTTP 400
        `"Invalid request parameter(s): Field 'limit' - Expected number,
        received nan"`. Defaults to 50; tune as needed.

        Returns a list of run dicts (empty list when no runs yet). Useful for
        debugging webhook source-table issues — confirms whether incoming data
        actually triggered runs.
        """
        if not isinstance(limit, int) or limit < 1:
            raise ValueError(
                f"list_source_runs: limit must be a positive int, got {limit!r}"
            )
        res = self.get(f"/sources/{source_id}/runs", params={"limit": limit})
        return res.get("runs", res) if isinstance(res, dict) else res

    # ── Credit usage / spend reporting ──────────────────────────────────────
    # `get_credit_usage`, `get_table_credit_usage`, and
    # `get_default_workbook_credit_limit` verified live 2026-04-30 against
    # workspace 12345. `get_table_credit_usage(..., aggregation="run")`
    # returned a raw list of run dicts on a populated table, not `{entities: ...}`.

    def get_credit_usage(
        self,
        *,
        report_type: str = "workspace",
        start_time,
        end_time,
        owner_ids: list[int] | None = None,
        integration_ids: list[str] | None = None,
        is_recurring_only: bool = False,
        has_credit_limit: bool = False,
        workspace_id: int | str | None = None,
    ) -> dict:
        """Get Clay's Settings → Usage data for one of the 6 report tabs."""
        valid_types = {"workspace", "integration", "signal", "triggerDefinition", "mcp", "api"}
        if report_type not in valid_types:
            raise ValueError(f"report_type must be one of {sorted(valid_types)}, got {report_type!r}")
        ws_id = self._resolve_workspace_id(workspace_id)
        params = [
            ("timeRange[startTime]", _to_iso(start_time)),
            ("timeRange[endTime]", _to_iso(end_time)),
        ]
        if is_recurring_only:
            params.append(("isRecurringOnly", "true"))
        if has_credit_limit:
            params.append(("hasCreditLimit", "true"))
        params.extend(_encode_array_filter("ownerIds", owner_ids))
        params.extend(_encode_array_filter("integrations", integration_ids))
        return self.get(f"/credit-reporting/{ws_id}/creditReportType/{report_type}", params=params)

    def get_table_credit_usage(
        self,
        table_id: str,
        *,
        aggregation: str = "run",
        start_time,
        end_time,
        time_aggregation_unit: str = "day",
        include_action_breakdown: bool = False,
        workspace_id: int | str | None = None,
    ) -> dict | list[dict]:
        """Get per-table credit-usage drill-down for one aggregation.

        Clay returns different raw shapes by aggregation. Verified live:
        `aggregation="run"` returns a `list[dict]` of run entries
        (`runId`, `timestamp`, `creditsSpent`, `columns`, ...). Time/column
        shapes should be treated as endpoint-native payloads rather than
        forced into one wrapper type.
        """
        valid_agg = {"time", "column", "run"}
        if aggregation not in valid_agg:
            raise ValueError(f"aggregation must be one of {sorted(valid_agg)}, got {aggregation!r}")
        ws_id = self._resolve_workspace_id(workspace_id)
        params = [
            ("timeRange[startTime]", _to_iso(start_time)),
            ("timeRange[endTime]", _to_iso(end_time)),
        ]
        if aggregation == "time":
            if time_aggregation_unit != "day":
                raise ValueError(
                    "time_aggregation_unit='day' is the only live-verified value; "
                    f"got {time_aggregation_unit!r}"
                )
            params.append(("timeAggregationUnit", time_aggregation_unit))
            params.append(("includeActionBreakdown", "true" if include_action_breakdown else "false"))
        return self.get(
            f"/realtime-credit-usage/{ws_id}/table/{table_id}/{aggregation}",
            params=params,
        )

    def get_default_workbook_credit_limit(
        self,
        *,
        workspace_id: int | str | None = None,
    ) -> dict:
        """Get the workspace default credit limit applied to new workbooks.

        Verified live response shape included `{"creditLimit": <number>}`.
        """
        ws_id = self._resolve_workspace_id(workspace_id)
        return self.get(
            f"/workspaces/{ws_id}/default-credit-limits",
            params={"appliesTo": "workbook"},
        )

    # ── Workspace metadata ───────────────────────────────────────────────────
    # All methods in this section verified live 2026-04-30 against workspace 12345
    # (Tier A smoke; paths + unwrap shapes confirmed). `get_workbook_overview`
    # confirmed to return both `nodes` and `edges`.

    def list_workspace_users(
        self,
        workspace_id: int | str | None = None,
    ) -> list[dict]:
        """List members of a workspace with their roles.

        Endpoint: `GET /workspaces/{ws}/users`.

        Returns a list of user dicts with `id`, `username`, `email`, `name`,
        `fullName`, `profilePicture`, and `role` (`{id, role}`).
        """
        ws_id = self._resolve_workspace_id(workspace_id)
        res = self.get(f"/workspaces/{ws_id}/users")
        return res.get("users", res) if isinstance(res, dict) else res

    def get_workbook_overview(
        self,
        workbook_id: str,
        *,
        workspace_id: int | str | None = None,
    ) -> dict:
        """Get a richer workbook view than `get_workbook()`: every node
        (table) in the workbook with its full table details, field counts,
        send-data fields, and credit estimates — PLUS the edge graph showing
        how tables feed into each other.

        Endpoint: `GET /{ws_id}/workbooks/{wb_id}/overview` (note: this path
        starts with `/{ws_id}/`, not `/workspaces/{ws_id}/`).

        Returns `{"nodes": [...], "edges": [...]}`:
        - each `node` = `{nodeId, name, description, creditEstimate,
          totalFieldCount, type, tableDetails, sendDataFields}`
        - `edges` describes the workbook DAG (which tables send data to which)

        The edges are most of what makes this richer than `get_workbook()` —
        they expose the workbook's data-flow topology.
        """
        ws_id = self._resolve_workspace_id(workspace_id)
        return self.get(f"/{ws_id}/workbooks/{workbook_id}/overview")

    def list_trigger_definitions(
        self,
        workspace_id: int | str | None = None,
    ) -> list[dict]:
        """List workflow trigger definitions with their schedules. These are
        Clay's "scheduled trigger" entities that run signals/workflows on a
        recurring basis.

        Endpoint: `GET /workspaces/{ws}/trigger-definitions-with-schedule`.

        Returns a list of dicts each containing `id`, `name`, `signalId`,
        `signal` (full signal definition), and `schedule` (`{id, periodAmount,
        periodUnit, lastRunAt, createdAt}`).

        This covers the read side of `feature-gaps.md` #3 (Run scheduling).
        Create / pause / delete schedules are not yet implemented.
        """
        ws_id = self._resolve_workspace_id(workspace_id)
        res = self.get(
            f"/workspaces/{ws_id}/trigger-definitions-with-schedule"
        )
        return res.get("triggerDefinitions", res) if isinstance(res, dict) else res

    def list_agent_configs(
        self,
        workspace_id: int | str | None = None,
    ) -> list[dict]:
        """List Claygent / agent configuration entries for the workspace.

        Endpoint: `GET /{ws_id}/agent-configs` (note: path starts with
        `/{ws_id}/`, not `/workspaces/{ws_id}/`).

        Returns a list of agent config dicts. Useful for inspecting which
        Claygents are set up in the workspace and their configuration.
        """
        ws_id = self._resolve_workspace_id(workspace_id)
        res = self.get(f"/{ws_id}/agent-configs")
        if isinstance(res, list):
            return res
        return res.get("agentConfigs", res.get("configs", []))

    # ── Dynamic action fields ────────────────────────────────────────────────
    # `get_dynamic_action_fields` verified live 2026-04-30 against workspace 12345
    # (Tier C smoke). Underlying action invocation cost confirmed at 0 credits;
    # `errors` array correctly populated when the upstream integration errors
    # (e.g. expired Salesforce session).

    def get_dynamic_action_fields(
        self,
        dynamic_requests: list[dict],
        *,
        workspace_id: int | str | None = None,
    ) -> list[dict]:
        """Resolve dynamic input fields for one or more action invocations.

        This powers the Clay UI's behavior where, after picking an action and
        an auth account, the UI shows action-specific dropdowns (e.g.
        "Object Type" on a Salesforce lookup). The dropdown choices are
        computed at request time by calling the action with the current input
        state and a `parameterPath` indicating which field to resolve.

        Endpoint: `POST /workspaces/{ws}/actions/dynamicFields` with body
        `{"dynamicRequests": [<request>, ...]}`.

        Each request dict should contain:

        - `actionPackageId` (str)
        - `actionKey` (str)
        - `authAccountId` (str) — required for auth-bound actions
        - `parameterPath` (str) — which field to resolve (e.g. `"object_type"`)
        - `type` (str) — `"select"` for dropdown options, `"input"` for text
        - `inputs` (dict) — current values of all input fields, used to
          dependent-resolve (e.g. selecting `Account` for `object_type`
          changes which `dynamicFields|object_fields` choices are valid)
        - `tableId` (str, optional) — context table id

        Returns a list of result dicts each with `parameterPath`, `dynamicData`
        (the resolved options), `errors`, and `logUrl`. When the underlying
        action errors (e.g. expired auth), `dynamicData` is `[]` and `errors`
        is non-empty with `errorMessage` + `errorDetails`.

        **Use case:** before calling `apply_preset(...)` or
        `create_action_column(...)` on an action with dynamic dependencies,
        call this to learn what valid values exist for each dynamic input.
        """
        ws_id = self._resolve_workspace_id(workspace_id)
        body = {"dynamicRequests": dynamic_requests}
        res = self.post(f"/workspaces/{ws_id}/actions/dynamicFields", body)
        return res if isinstance(res, list) else res.get("results", res)

    # ── Preset catalog ───────────────────────────────────────────────────────

    def list_preset_categories(self, workspace_id: int | str | None = None) -> list[str]:
        """List preset catalog category names for a workspace."""
        ws_id = self._resolve_workspace_id(workspace_id)
        res = self.get(f"/presets/workspace/{ws_id}/categories")
        return res if isinstance(res, list) else res.get("categories", res)

    def list_presets_filtered(
        self,
        workspace_id: int | str | None = None,
        *,
        types: list[str] | None = None,
        categories: list[str] | None = None,
    ) -> list[dict]:
        """
        List presets from Clay's filtered catalog endpoint.

        At least one of `types` or `categories` is required.
        """
        if not types and not categories:
            raise ValueError("list_presets_filtered: pass at least one of types= or categories=")
        ws_id = self._resolve_workspace_id(workspace_id)
        pairs = []
        for preset_type in types or []:
            pairs.append(("types[]", preset_type))
        for category in categories or []:
            pairs.append(("categories[]", category))
        query = urlencode(pairs)
        res = self.get(f"/presets/workspace/{ws_id}/filtered?{query}")
        return res if isinstance(res, list) else res.get("presets", res)

    def list_presets_by_category(
        self,
        category: str,
        *,
        workspace_id: int | str | None = None,
    ) -> list[dict]:
        """List presets from the richer per-category preset endpoint."""
        if not category:
            raise ValueError("list_presets_by_category: category is required")
        ws_id = self._resolve_workspace_id(workspace_id)
        query = urlencode({"category": category})
        res = self.get(f"/presets/workspace/{ws_id}?{query}")
        return res if isinstance(res, list) else res.get("presets", res)

    def apply_preset(
        self,
        preset: dict,
        table_id: str,
        *,
        column_mapping: dict,
        name: str | None = None,
        auth_account_id: str | None = None,
    ) -> dict:
        """
        Apply a preset to a table as an action column.

        `preset` must include `actionKey`, `actionPackageId`, and
        `preset.inputsBinding`. Use `list_presets_by_category(...)` to get the
        richer preset shape that includes those keys; lighter shapes from
        `list_presets_filtered(...)` may fail here.
        """
        if not isinstance(preset, dict):
            raise ValueError("apply_preset: preset must be a dict")

        def required(key: str, *, from_preset: dict = preset, hint: str | None = None):
            if key not in from_preset or from_preset.get(key) is None:
                message = f"apply_preset: preset missing '{key}'"
                if hint:
                    message += f" — {hint}"
                raise ValueError(message)
            return from_preset[key]

        action_key = required(
            "actionKey",
            hint="use list_presets_by_category() which populates it",
        )
        package_id = required(
            "actionPackageId",
            hint="use list_presets_by_category() which populates it",
        )
        preset_body = required(
            "preset",
            hint="use list_presets_by_category() which returns the full preset payload",
        )
        if not isinstance(preset_body, dict):
            raise ValueError("apply_preset: preset['preset'] must be a dict")
        inputs_binding = required(
            "inputsBinding",
            from_preset=preset_body,
            hint="use list_presets_by_category() which returns preset.inputsBinding",
        )
        if not isinstance(inputs_binding, dict):
            raise ValueError("apply_preset: preset['preset']['inputsBinding'] must be a dict")

        rewritten_inputs = rewrite_preset_placeholders(inputs_binding, column_mapping)
        column_name = name or preset.get("name")
        if not column_name:
            raise ValueError("apply_preset: preset missing 'name' — pass name= explicitly")

        return self.create_action_column(
            table_id,
            name=column_name,
            action_key=action_key,
            package_id=package_id,
            inputs=rewritten_inputs,
            auth_account_id=auth_account_id,
        )

    def list_disabled_actions(self, workspace_id: int | str | None = None) -> list[str]:
        """List workspace-disabled action ids."""
        ws_id = self._resolve_workspace_id(workspace_id)
        res = self.get(f"/workspaces/{ws_id}/all-disabled-actions")
        return res.get("disabledActionIds", res)

    def list_starred_resources(self, *, resource_type: str = "ACTION") -> list[dict]:
        """List this user's starred resources for the given resource type."""
        if not resource_type:
            raise ValueError("list_starred_resources: resource_type is required")
        res = self.get("/resources/starred", params={"resourceType": resource_type})
        return res.get("starredResources", res)

    def get_resource_star(
        self,
        package_id: str,
        action_key: str,
        *,
        resource_type: str = "ACTION",
    ) -> bool:
        """
        Return whether this user has starred a specific resource.

        Endpoint: `GET /resources/{package_id}%2F{action_key}/star?resourceType=<type>`.
        Returns the unwrapped bool from the response body `{"isStarred": bool}`.
        """
        if not package_id or not action_key:
            raise ValueError("get_resource_star: package_id and action_key are required")
        if not resource_type:
            raise ValueError("get_resource_star: resource_type is required")
        composite_id = quote(f"{package_id}/{action_key}", safe="")
        res = self.get(f"/resources/{composite_id}/star", params={"resourceType": resource_type})
        if not isinstance(res, dict) or "isStarred" not in res:
            raise ValueError(f"get_resource_star: unexpected response shape: {res!r}")
        return bool(res["isStarred"])

    def document_table(
        self,
        table_id: str,
        *,
        view_id: str | None = None,
        show_field_id: bool = False,
        show_position: bool = True,
        show_only_visible: bool = True,
        show_visibility_status: bool = False,
        output_dir: str | None = None,
        filename: str | None = None,
    ) -> dict:
        """
        Generate a markdown report for a Clay table and persist it to the local
        filesystem.

        Default output path:
          <project_root>/tmp/clay-artifacts/document-<table_id>-<UTC-ISO8601>.md
        """
        raw = self.get_table(
            table_id,
            include_extra_data=True,
            extra_data_view_id=view_id,
        )
        table = raw.get("table", raw)
        views = table.get("views", []) or table.get("gridViews", []) or []
        selected_view = None
        if view_id:
            selected_view = next((view for view in views if view.get("id") == view_id), None)
        elif views:
            selected_view = views[0]

        fields = table.get("fields", []) or []
        id_to_name = {field.get("id"): field.get("name") for field in fields if field.get("id") and field.get("name")}
        id_to_field = {field.get("id"): field for field in fields if field.get("id")}
        view_fields = (selected_view or {}).get("fields") or {}

        def refs_in(value) -> set[str]:
            blob = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
            return {id_to_name.get(fid, fid) for fid in _FIELD_RE.findall(blob)}

        def humanize(value):
            return _refs_to_names(copy.deepcopy(value), id_to_name)

        def render_code_block(value) -> str:
            if isinstance(value, (dict, list)):
                rendered = json.dumps(humanize(value), indent=2, ensure_ascii=False)
            else:
                rendered = str(humanize(value))
            return f"```json\n{rendered}\n```" if rendered.startswith("{") or rendered.startswith("[") else f"```\n{rendered}\n```"

        ordered_ids = []
        if isinstance(view_fields, dict) and view_fields:
            def order_key(fid):
                order = (view_fields.get(fid) or {}).get("order")
                if isinstance(order, (int, float)):
                    return (0, order)
                if isinstance(order, str) and order.isdigit():
                    return (0, int(order))
                return (1, fid)
            ordered_ids.extend(sorted(view_fields.keys(), key=order_key))
        for field in fields:
            if field.get("id") not in ordered_ids:
                ordered_ids.append(field.get("id"))

        workspace_id = table.get("workspaceId", self.workspace_id)
        workbook_id = table.get("workbookId")
        default_view_id = selected_view.get("id") if selected_view else table.get("firstViewId")
        table_url = (
            f"https://app.clay.com/workspaces/{workspace_id}/workbooks/{workbook_id}/tables/{table_id}"
            if workbook_id else
            f"https://app.clay.com/workspaces/{workspace_id}/tables/{table_id}"
        )
        if default_view_id:
            table_url += f"/views/{default_view_id}"

        lines = [
            f"# {table.get('name', table_id)}",
            "",
            f"- Table ID: `{table_id}`",
            f"- Table URL: {table_url}",
            f"- View ID: `{default_view_id}`" if default_view_id else "- View ID: `N/A`",
            f"- Description: {table.get('description') or 'N/A'}",
            "",
        ]

        dependency_rows: list[tuple[str, list[str]]] = []
        count = 0
        for abs_index, fid in enumerate(ordered_ids, start=1):
            field = id_to_field.get(fid)
            if not field or fid in _SYSTEM_FIELD_IDS:
                continue
            meta = view_fields.get(fid, {}) if isinstance(view_fields, dict) else {}
            visible = meta.get("isVisible", True)
            if show_only_visible and not visible:
                continue

            count += 1
            ts = copy.deepcopy(field.get("typeSettings") or {})
            inputs: set[str] = set()
            action_params: list[tuple[str, object]] = []
            ai_params: list[tuple[str, object]] = []
            logic_lines: list[tuple[str, str]] = []

            if ts.get("formulaText"):
                formula = humanize(ts["formulaText"])
                logic_lines.append(("Formula", f"`{formula}`"))
                inputs.update(refs_in(ts["formulaText"]))
            if ts.get("conditionalRunFormulaText"):
                cond = humanize(ts["conditionalRunFormulaText"])
                logic_lines.append(("Run Condition", f"`{cond}`"))
                inputs.update(refs_in(ts["conditionalRunFormulaText"]))
            if ts.get("formulaWaterfall"):
                waterfall_steps = []
                for idx, step in enumerate(ts["formulaWaterfall"], start=1):
                    prompt = step.get("prompt") or step.get("formula")
                    if prompt:
                        waterfall_steps.append(f"Step {idx}: `{humanize(prompt)}`")
                        inputs.update(refs_in(prompt))
                if waterfall_steps:
                    logic_lines.append(("Waterfall Logic", "\n".join(waterfall_steps)))

            for binding in ts.get("inputsBinding", []) or []:
                key = binding.get("name")
                value = binding.get("formulaText")
                if value is None and "formulaMap" in binding:
                    value = binding["formulaMap"]
                if value is None:
                    continue
                inputs.update(refs_in(value))
                if key in {"prompt", "answerSchemaType", "answerSchema", "model"}:
                    ai_params.append((key, value))
                else:
                    action_params.append((key, value))

            dependency_rows.append((field.get("name"), sorted(inputs)))

            lines.append(f"## {count}. {field.get('name')}")
            lines.append("")
            lines.append("### Basic Information")
            lines.append(f"- Type: `{field.get('type', 'text')}`")
            if show_field_id:
                lines.append(f"- Field ID: `{fid}`")
            if show_position:
                lines.append(f"- Position: `{abs_index}`")
            if show_visibility_status:
                lines.append(f"- Visible: `{visible}`")
            if ts.get("actionKey"):
                lines.append(f"- Action Key: `{ts.get('actionKey')}`")
            if ts.get("actionPackageId"):
                lines.append(f"- Action Package ID: `{ts.get('actionPackageId')}`")
            if (ts.get("dataTypeSettings") or {}).get("type"):
                lines.append(f"- Data Type: `{ts['dataTypeSettings']['type']}`")
            lines.append("")

            if logic_lines:
                lines.append("### Logic")
                for key, value in logic_lines:
                    lines.append(f"- {key}: {value}")
                lines.append("")

            if inputs:
                lines.append("### Dependency")
                lines.append(f"- Referenced Field(s): {', '.join(sorted(inputs))}")
                lines.append("")

            if action_params:
                lines.append("### Action Input Parameters")
                for key, value in action_params:
                    lines.append(f"- {key}:")
                    lines.append(render_code_block(value))
                lines.append("")

            if ai_params:
                lines.append("### AI Agent Parameters")
                for key, value in ai_params:
                    lines.append(f"- {key}:")
                    lines.append(render_code_block(value))
                lines.append("")

            lines.append("---")
            lines.append("")

        if dependency_rows:
            lines.append("## Dependency Graph")
            lines.append("")
            for field_name, inputs in dependency_rows:
                deps = ", ".join(inputs) if inputs else "none"
                lines.append(f"- `{field_name}` <- {deps}")
            lines.append("")

        markdown = "\n".join(lines).strip() + "\n"

        path = self._write_artifact(
            markdown,
            output_dir=output_dir,
            filename=filename,
            default_stem=f"document-{table_id}",
            suffix=".md",
            serializer=lambda value: value,
        )
        return {"markdown": markdown, "path": path}

    # ── Portable Schema (ClayPrint format) ─────────────────────────────────────

    def export_schema(self, table_id: str, column_names: list[str] = None) -> dict:
        """
        Export a table as a portable ClayPrint-compatible schema.
        Field IDs are converted to {{@Column Name}} references.
        Optionally filter to specific column_names.
        """
        raw = self.get_table(table_id)
        table = raw.get("table", raw)
        fields = table.get("fields", [])

        # Determine view-based field order
        grid_views = table.get("gridViews", [])
        view = grid_views[0] if grid_views else None
        view_order = view.get("fieldOrder", []) if view else []
        ordered_ids = view_order if view_order else [f["id"] for f in fields]

        # Build ordered field list (skip system fields)
        skip = {"f_created_at", "f_updated_at"}
        ordered_fields = []
        for fid in ordered_ids:
            if fid in skip:
                continue
            fd = next((f for f in fields if f["id"] == fid), None)
            if fd:
                ordered_fields.append(fd)

        # Filter to selected columns if specified
        if column_names:
            names_set = set(column_names)
            # Include dependencies too
            id_to_name = {f["id"]: f["name"] for f in ordered_fields}
            selected = [f for f in ordered_fields if f["name"] in names_set]
            # Also include any column referenced by selected columns
            for f in selected:
                ts_str = json.dumps(f.get("typeSettings", {}))
                for ref_id in re.findall(r"\{\{(f_[a-zA-Z0-9_]+)\}\}", ts_str):
                    dep_name = id_to_name.get(ref_id)
                    if dep_name:
                        names_set.add(dep_name)
            ordered_fields = [f for f in ordered_fields if f["name"] in names_set]

        # Build ID → name maps
        id_to_name = {f["id"]: f["name"] for f in ordered_fields}
        field_order = [f["id"] for f in ordered_fields]

        # Fetch source details for source columns
        for field in ordered_fields:
            if field.get("type") == "source":
                source_ids = (field.get("typeSettings") or {}).get("sourceIds", [])
                if source_ids:
                    try:
                        field["_sourceDetails"] = [
                            self.get(f"/sources/{sid}") for sid in source_ids
                        ]
                    except Exception:
                        pass

        # Build source data ref map
        source_data_ref_to_name = {}
        for f in ordered_fields:
            for sd in f.get("_sourceDetails", []):
                if sd.get("dataFieldId"):
                    source_data_ref_to_name[sd["dataFieldId"]] = f["name"]

        # Convert to portable format
        columns = []
        for idx, field in enumerate(ordered_fields):
            col = {
                "index": idx,
                "name": field["name"],
                "type": field["type"],
            }
            if field.get("typeSettings"):
                col["typeSettings"] = _refs_to_names(
                    copy.deepcopy(field["typeSettings"]),
                    id_to_name, source_data_ref_to_name,
                )
            if field.get("_sourceDetails"):
                col["sourceDetails"] = [
                    {
                        "name": sd["name"],
                        "type": sd.get("type", "webhook"),
                        "dataFieldId": sd.get("dataFieldId"),
                        "typeSettings": _refs_to_names(
                            copy.deepcopy(sd.get("typeSettings", {})),
                            id_to_name, source_data_ref_to_name,
                        ),
                    }
                    for sd in field["_sourceDetails"]
                ]
            columns.append(col)

        schema = {
            "version": "1.0",
            "exportedAt": __import__("datetime").datetime.utcnow().isoformat() + "Z",
            "tableId": table_id,
            "columnCount": len(columns),
            "columns": columns,
        }
        print(f"[clay] exported {len(columns)} columns from {table_id}")
        return schema

    def import_schema(self, table_id: str, schema: dict, dry_run: bool = False) -> list[dict]:
        """
        Import a portable ClayPrint schema into a table.
        Resolves {{@Column Name}} refs to real field IDs.
        Creates columns in dependency order. Returns list of results.
        """
        raw = self.get_table(table_id)
        table = raw.get("table", raw)
        existing = table.get("fields", [])
        views = table.get("gridViews", table.get("views", []))
        view_id = views[0]["id"] if views else table.get("firstViewId")

        # Name → ID map (existing columns)
        name_to_id = {f["name"]: f["id"] for f in existing}
        source_name_to_data_ref = {}

        columns = schema.get("columns", [])
        sorted_cols = _sort_by_deps(columns)

        if dry_run:
            print(f"[clay] DRY RUN — would create {len(sorted_cols)} columns:")
            for c in sorted_cols:
                deps = _extract_deps(c.get("typeSettings"))
                dep_str = f" (depends on: {', '.join(deps)})" if deps else ""
                print(f"  {c['type']:8s} | {c['name']}{dep_str}")
            return []

        results = []
        print(f"[clay] importing {len(sorted_cols)} columns into {table_id}...")

        for col in sorted_cols:
            col_type = col.get("type", "text")
            try:
                if col_type == "source" and col.get("sourceDetails"):
                    created_source_ids = []
                    for sd in col["sourceDetails"]:
                        ts = _names_to_refs(
                            copy.deepcopy(sd.get("typeSettings", {})),
                            name_to_id, source_name_to_data_ref,
                        )
                        src = self.post("/sources", {
                            "workspaceId": int(self.workspace_id),
                            "tableId": table_id,
                            "name": sd["name"],
                            "type": sd.get("type", "v3-action"),
                            "typeSettings": ts,
                        })
                        sid = src.get("id") or (src.get("source") or {}).get("id")
                        if sid:
                            created_source_ids.append(sid)
                            dfid = src.get("dataFieldId") or (src.get("source") or {}).get("dataFieldId")
                            if not dfid:
                                try:
                                    dfid = self.get(f"/sources/{sid}").get("dataFieldId")
                                except Exception:
                                    pass
                            if dfid:
                                source_name_to_data_ref[col["name"]] = dfid
                        time.sleep(0.15)

                    if not created_source_ids:
                        raise Exception("No sources created")

                    result = self._create_field(table_id, view_id, {
                        "name": col["name"],
                        "type": "source",
                        "typeSettings": {
                            "sourceIds": created_source_ids,
                            "canCreateRecords": (col.get("typeSettings") or {}).get("canCreateRecords", True),
                        },
                    })
                else:
                    ts = None
                    if col.get("typeSettings"):
                        ts = _names_to_refs(
                            copy.deepcopy(col["typeSettings"]),
                            name_to_id, source_name_to_data_ref,
                        )

                    field_def = {"name": col["name"], "type": col_type}
                    if ts:
                        field_def["typeSettings"] = ts

                    # Text columns need dataTypeSettings
                    if col_type == "text":
                        field_def.setdefault("typeSettings", {})
                        field_def["typeSettings"].setdefault("dataTypeSettings", {"type": "text"})

                    # Formula columns: create as text, then PATCH formulaText
                    if col_type == "formula" and ts and ts.get("formulaText"):
                        formula_ts = ts
                        field_def["type"] = "text"
                        field_def["typeSettings"] = {"dataTypeSettings": ts.get("dataTypeSettings", {"type": "text"})}
                        result = self._create_field(table_id, view_id, field_def)
                        fid = (result.get("field") or result).get("id")
                        if fid:
                            self.patch(f"/tables/{table_id}/fields/{fid}", {"typeSettings": formula_ts})
                    elif col_type == "action" and ts:
                        ts.setdefault("dataTypeSettings", {"type": "text"})
                        field_def["typeSettings"] = ts
                        result = self._create_field(table_id, view_id, field_def)
                    else:
                        result = self._create_field(table_id, view_id, field_def)

                fid = (result.get("field") or result).get("id")
                if fid:
                    name_to_id[col["name"]] = fid
                results.append({"success": True, "name": col["name"]})
                print(f"  [ok] {col['name']}")

            except Exception as e:
                results.append({"success": False, "name": col["name"], "error": str(e)})
                print(f"  [FAIL] {col['name']}: {e}")

            time.sleep(0.15)

        ok = sum(1 for r in results if r["success"])
        fail = sum(1 for r in results if not r["success"])
        print(f"[clay] done: {ok} created, {fail} failed")
        return results

    def _create_field(self, table_id: str, view_id: str, field_def: dict) -> dict:
        """Internal: create field with view context."""
        body = {**field_def, "activeViewId": view_id}
        return self.post(f"/tables/{table_id}/fields", body)


# ── Portable schema helpers ──────────────────────────────────────────────────

_FIELD_RE = re.compile(r"\{\{(f_[a-zA-Z0-9_]+)\}\}")
_NAME_RE = re.compile(r"\{\{@([^}]+)\}\}")
_SOURCE_RE = re.compile(r"\{\{@source:([^}]+)\}\}")


def _refs_to_names(obj, id_to_name: dict, source_ref_to_name: dict = None):
    """Convert {{f_xxx}} → {{@Column Name}} in any nested structure."""
    if source_ref_to_name is None:
        source_ref_to_name = {}
    if isinstance(obj, str):
        def replace(m):
            fid = m.group(1)
            name = id_to_name.get(fid)
            if name:
                return "{{@" + name + "}}"
            sname = source_ref_to_name.get(fid)
            if sname:
                return "{{@source:" + sname + "}}"
            return m.group(0)
        return _FIELD_RE.sub(replace, obj)
    if isinstance(obj, list):
        return [_refs_to_names(item, id_to_name, source_ref_to_name) for item in obj]
    if isinstance(obj, dict):
        return {k: _refs_to_names(v, id_to_name, source_ref_to_name) for k, v in obj.items()}
    return obj


def _names_to_refs(obj, name_to_id: dict, source_name_to_ref: dict = None):
    """Convert {{@Column Name}} → {{f_xxx}} in any nested structure."""
    if source_name_to_ref is None:
        source_name_to_ref = {}
    if isinstance(obj, str):
        result = _SOURCE_RE.sub(
            lambda m: "{{" + source_name_to_ref.get(m.group(1), m.group(0)) + "}}"
            if m.group(1) in source_name_to_ref else m.group(0),
            obj,
        )
        result = _NAME_RE.sub(
            lambda m: "{{" + name_to_id[m.group(1)] + "}}"
            if m.group(1) in name_to_id else m.group(0),
            result,
        )
        return result
    if isinstance(obj, list):
        return [_names_to_refs(item, name_to_id, source_name_to_ref) for item in obj]
    if isinstance(obj, dict):
        return {k: _names_to_refs(v, name_to_id, source_name_to_ref) for k, v in obj.items()}
    return obj


def _extract_deps(type_settings) -> list[str]:
    """Extract column names referenced via {{@Name}} in typeSettings."""
    if not type_settings:
        return []
    deps = set()
    s = json.dumps(type_settings)
    for m in _SOURCE_RE.finditer(s):
        deps.add(m.group(1))
    for m in _NAME_RE.finditer(s):
        if not m.group(1).startswith("source:"):
            deps.add(m.group(1))
    return list(deps)


def _sort_by_deps(columns: list[dict]) -> list[dict]:
    """Topological sort: sources first, then by dependency order."""
    by_name = {c["name"]: c for c in columns}
    dep_map = {c["name"]: _extract_deps(c.get("typeSettings")) for c in columns}

    result = []
    visited = set()
    visiting = set()

    def visit(name):
        if name in visited or name not in by_name:
            return
        if name in visiting:
            return  # cycle — skip
        visiting.add(name)
        for dep in dep_map.get(name, []):
            visit(dep)
        visiting.discard(name)
        visited.add(name)
        result.append(by_name[name])

    # Sources first
    for c in columns:
        if c.get("type") == "source":
            visit(c["name"])
    for c in columns:
        visit(c["name"])

    return result


# ── CLI quick test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    clay = ClayClient()

    print("\n--- Tables ---")
    tables = clay.list_tables()
    regular = [t for t in tables if t.get("_kind") == "TABLE"]
    functions = [t for t in tables if t.get("_kind") == "FUNCTION_TABLE"]
    print(f"  {len(regular)} data tables + {len(functions)} function tables")
    for t in regular[:10]:
        print(f"  [TABLE]    {t.get('name', '?')} ({t.get('id', '')})")
    for t in functions[:10]:
        fn = t.get("_function", {})
        managed = "clay-managed" if fn.get("is_clay_managed") else "workspace"
        print(f"  [FUNCTION] {t.get('name', '?')} ({t.get('id', '')}) "
              f"{managed}, cost={fn.get('cost')}, inputs={fn.get('inputs')}")

    print("\n--- Auth Accounts ---")
    accounts = clay.list_auth_accounts()
    if isinstance(accounts, list):
        for a in accounts[:10]:
            print(f"  {a.get('name', a.get('displayName', '?'))} ({a.get('id')})")

    print("\n--- Formula generation test ---")
    # Use a real table id from the list above to test
    tables_only = regular
    if tables_only:
        tid = tables_only[0]["id"]
        result = clay.generate_formula(tid, "If company name is empty, output 'Unknown'")
        print(f"  Prompt: 'If company name is empty, output Unknown'")
        print(f"  Formula: {result.get('formula')}")
