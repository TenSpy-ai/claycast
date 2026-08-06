---
name: claycast
description: UNOFFICIAL Clay toolkit that drives Clay's internal REST API with the user's browser session cookie (`CLAY_SESSION`). Use ONLY when you need schema-level work the official Clay MCP connector cannot do — creating/modifying tables, columns, or action columns by code; running/waiting on enrichments; exporting/importing schemas; or discovering undocumented action-input shapes via request-capture against the live app. Trigger on "ClayCast", "clay_client", "clay_browser", "CLAY_SESSION", "Clay internal API", "session cookie", "request capture/discovery", or any "create/modify Clay table/column/action programmatically" ask. DO NOT trigger if the user just wants to run an existing enrichment subroutine — use the Clay MCP (`mcp__claude_ai_Clay__*`) for that instead.
---

# ClayCast — Clay Internal API Toolkit

Direct access to Clay's internal REST API via the user's browser session cookie. The official Clay MCP only exposes enrichment subroutines; ClayCast handles schema-level work (new tables/columns/actions) and action-input discovery.

---

## Authentication — `CLAY_SESSION` from the project's `.env`

The loader starts from `os.getcwd()`, resolves symlinks to the real path, and walks upward looking for `.env` files. The `.env` path is NOT hard-coded to the skill install directory — the cookie lives with whatever project you're working in, so the skill can be moved between profiles or installed globally without breaking auth.

**Resolution order** (in `scripts/clay_client.py`, shared by `scripts/clay_browser.py`):
1. Constructor kwarg `ClayClient(clay_session=...)` — optional, highest precedence. Useful for wiring a session from a vault, another service, or a test fixture. No per-call override is supported; set at construction time.
2. Process env var `CLAY_SESSION`.
3. `.env` walk-up from the current working directory — checks `./.env`, then each parent directory for `CLAY_SESSION=` until one is found, and stops at the first ancestor containing a `.git` directory, at `$HOME`, or at the filesystem root. If a candidate `.env` exists but cannot be read, the loader raises a `RuntimeError` that includes the offending path instead of skipping it silently.

If none is set, scripts raise `RuntimeError` pointing at `references/cookie-setup.md`.

**Setup:**
1. Follow `references/cookie-setup.md` Steps 1–3 to copy the `claysession` cookie from Chrome DevTools.
2. `cp "$SKILL_ROOT/references/.env.example" <project-root>/.env` (or add `CLAY_SESSION=...` to an existing `.env`).
3. Paste the value: `CLAY_SESSION=s%3A...` (no surrounding quotes needed; the loader strips them if present).
4. Verify from project root: `python -c "import sys, os; sys.path.insert(0, os.path.expanduser(os.environ['SKILL_ROOT']) + '/scripts'); from clay_client import ClayClient; ClayClient()"` — prints `[clay] logged in as <email> | workspace <id>`.

Cookie expires every few weeks (signaled by 401s) — refresh by re-running DevTools steps and replacing the value in `.env`. Never commit `.env`; the cookie grants full access to the Clay workspace.

---

## Directory layout

```
$SKILL_ROOT/
├── SKILL.md
├── references/
│   ├── .env.example               ← template; copy to <project-root>/.env
│   ├── clay-api-reference.md      ← full API reference, load on-demand
│   ├── action-registry.md         ← catalog of known action keys + inputs
│   ├── cookie-setup.md            ← DevTools walkthrough
│   └── requirements.txt           ← requests, playwright, playwright-stealth
└── scripts/
    ├── clay_client.py             ← ClayClient SDK
    └── clay_browser.py            ← Playwright daemon for API discovery
```

---

## Install

Set `SKILL_ROOT` to the skill's install path (e.g. `SKILL_ROOT=~/.claude/skills/claycast`), then:

```bash
pip install -r "$SKILL_ROOT/references/requirements.txt"
python -m playwright install chromium   # only needed for clay_browser.py
```

---

## Quickstart

```python
import os, sys
sys.path.insert(0, os.path.expanduser(os.environ["SKILL_ROOT"]) + "/scripts")
from clay_client import ClayClient

clay = ClayClient()
# → [clay] logged in as user@company.com | workspace 12345

# Create a table (returns a dict, not a tuple).
table = clay.create_table("My New Table")
table_id = table["id"]
view_id = table["firstViewId"]

# List records needs both table_id AND view_id (internally: 2-step ids → bulk-fetch).
records = clay.list_records(table_id, view_id)
tables = clay.list_tables()
```

---

## Capability map — `clay_client.py`

| Area | Methods |
|---|---|
| Workspace | `list_workspaces`, `get_workspace_permissions`, `list_workspace_contents`, `get_workbook`, `list_workbook_tables`, `find_tables`, `get_workspace_hierarchy`, `get_resource_urls` |
| Tables | `create_table`, `list_tables`, `list_folders`, `get_table`, `get_schema`, `get_field_map`, `count_records`, `inspect_table`, `delete_table`, `set_table_description`, `generate_table_description` (built-in AI) |
| Fields / Columns | `list_fields`, `apply_field_operations`, `create_column`, `create_action_column`, `create_formula_column`, `update_column`, `delete_column`, `delete_fields`, `move_field`, `reorder_fields`, `set_field_visibility`, `set_fields_visibility`, `set_condition` |
| Dependency graph & references | `get_table_graph`, `get_field_dependents`, `get_field_dependencies` (structural — Clay's own graph), and `get_field_references` (literal `typeSettings` scan). Use the **graph** for downstream structure; use **`get_field_references`** to find the literal id references to remap or to verify delete-safety (the graph collapses an action's extractor columns into its node). `delete_column`/`delete_fields` are guarded by `get_field_references`. |
| Views | `list_views`, `create_view`, `update_view`, `delete_view`, `set_view_filter`, `set_view_sort`, `set_view_fields`, `set_view_field_order` (filter/sort go through their sub-endpoints; whole-view order is a `move_field` walk — see api-reference "View filter/sort write path") |
| Field groups | `create_field_group`, `update_field_group`, `move_field_group`, `ungroup`, `delete_field_group` |
| Imports | `preview_csv_input`, `import_csv_to_table`, `get_import_job`, `wait_for_import_job` |
| Sources | `list_sources`, `get_source`, `create_webhook_source`, `list_source_runs` |
| Sourced tables (Find People) | `create_sourced_table` (`preview_sourced_table` is DEAD as of 2026-07-23 — allowlist regression; use the official `clay search` for free previews) |
| Records (raw, field-id keyed) | `create_records`, `get_record_ids`, `list_records`, `get_records`, `get_record`, `update_record`, `bulk_update_records`, `delete_records`, `upsert_records` |
| Records (name-keyed, value-extracted) | `list_records_by_name`, `get_record_by_name` |
| Runs / Jobs | `run_column`, `get_run_status`, `wait_for_runs`, `rerun_errored_cells`, `run_and_wait` |
| Credit usage / spend reporting | `get_credit_usage`, `get_table_credit_usage`, `get_default_workbook_credit_limit` |
| Export / Documentation | `export_csv`, `fetch_all_records_full`, `export_rows`, `export_workspace`, `document_table`, `search_export_artifacts` |
| Audience export (>50K rows) | `list_audience_segments`, `count_audience_segment`, `export_audience_segment` |
| Portable schema | `export_schema`, `import_schema` |
| AI helpers | `generate_formula`, `search_enrichments` |
| Registry | `list_actions`, `list_subroutines`, `get_dynamic_action_fields` |
| Functions & tools registry | `create_function(name, inputs, entity_type, extractors, success_field, register, send_back)` — UI-style subroutine function table end-to-end (registered, publicly runnable; `send_back={output: extractor_col}` wires the write-to-cell return path + AUTO_RUN so caller cells resolve "✅ Success"); `register_tool(tool_id, tool_type, name, entity_type, ...)` — register workflows/functions in the workspace tools registry for public Routines execution; `create_function_sandbox` / `publish_function_sandbox` — edit a LIVE (caller-locked) function via Clay's sandbox flow |
| Presets / catalog | `list_preset_categories`, `list_presets_filtered`, `list_presets_by_category`, `list_disabled_actions`, `list_starred_resources`, `get_resource_star`, `apply_preset` |
| Auth / Account | `me`, `list_auth_accounts`, `get_auth_account`, `list_auth_accounts_by_type`, `list_auth_account_types`, `get_auth_account_type`, `validate_auth_credentials` |
| Workspace metadata | `list_workspace_users`, `get_workbook_overview`, `list_trigger_definitions`, `list_agent_configs` |
| Module-level helpers | `extract_cell_value(cell)`, `rewrite_preset_placeholders(preset_inputs_binding, mapping)`, `build_salesforce_user_soql(emails, names, fields, sandbox)` |
| Raw HTTP escape hatch | `clay.get / post / patch / delete(path, **kwargs)` — authenticated session, use when claycast hasn't wrapped an endpoint yet. Prefer the high-level methods for anything they cover. |

Exact signatures + field-binding details: `references/clay-api-reference.md`.
Known action keys + input field names (use-ai, HTTP API, Find People, Instantly, HeyReach, etc.): `references/action-registry.md`.
Capabilities claycast does NOT have yet (before saying "no, use the Clay UI"): `references/feature-gaps.md` — tiered list of missing features + operational gaps for headless automation.

### Preset catalog

`list_actions()` is the raw action schema layer; the preset-catalog methods expose the curated "+ Add enrichment" layer Clay shows in the UI: categories, filtered preset lists, per-category bundles, disabled actions, and star state. The high-level path is:

```python
preset = clay.list_presets_by_category("AI")[0]
col = clay.apply_preset(preset, table_id, column_mapping={"Input_1": "{{@Domain}}"})
```

Use `list_presets_by_category` (not `list_presets_filtered`) — only the former populates `actionKey` / `actionPackageId`, which `apply_preset` requires. Swapping in `list_presets_filtered` raises `ValueError` from the required-key guard.

For integration actions (HubSpot, Apollo, Instantly, etc.), pass `auth_account_id=` when calling `apply_preset`. The column is created either way, but run attempts 401 without it. AI / pure-compute actions (`use-ai`, formula-style) don't need auth. Resolve account ids via `list_auth_accounts_by_type("salesforce")` (preferred — filters to one integration) or `list_auth_accounts()` (returns all).

Under the hood, preset payloads still use `{{Input_N}}` placeholders rather than real field refs, so `rewrite_preset_placeholders(...)` and `create_action_column(...)` remain the lower-level building blocks when you need more control.

**Untested surfaces (as of 2026-04-24):** integration presets requiring `auth_account_id` weren't live-tested — only no-auth `use-ai` was verified end-to-end. Waterfall presets (`type="waterfall"` / `"parent_waterfall"`) weren't routed through `apply_preset`; their `inputsBinding` shape may require a different wrapper. Running the resulting action columns wasn't exercised (creation only, 0 credits); first real run may surface runtime-only validation we haven't caught.

### Dynamic action fields — runtime-resolved dropdowns

Many integration actions have dropdowns whose options depend on the connected account: e.g. Salesforce `object_type` lists the user's Salesforce objects, then `object_fields` lists fields on the chosen object. The Clay UI resolves these dropdowns by calling the action with current input state and a `parameterPath` to ask "what valid values exist for THIS input right now?" — `get_dynamic_action_fields()` exposes that same mechanism.

```python
results = clay.get_dynamic_action_fields([{
    "actionPackageId": preset["actionPackageId"],
    "actionKey": preset["actionKey"],
    "authAccountId": "aa_0t...",
    "parameterPath": "object_type",       # which dropdown to resolve
    "type": "select",
    "inputs": {},                         # current values; influences dependent dropdowns
    "tableId": "t_0t...",
}])
# results[0]["dynamicData"] is the list of valid options
# results[0]["errors"] is non-empty when the underlying integration errored (e.g. expired auth)
```

This closes the AI-driven preset-application loop: `list_presets_by_category` → `get_dynamic_action_fields` (learn valid values) → `apply_preset` with real values, instead of guessing. Without this, programmatic action configuration relied on hardcoded `action-registry.md` knowledge that drifted over time.

### Auth account resolution

When `apply_preset` or `create_action_column` needs an `auth_account_id`, resolve it by integration type rather than hardcoding:

```python
hubspot_accounts = clay.list_auth_accounts_by_type("hubspot")
if not hubspot_accounts:
    raise RuntimeError("No HubSpot account connected to this workspace")
clay.apply_preset(preset, table_id, column_mapping={"Input_1": "{{@Email}}"},
                  auth_account_id=hubspot_accounts[0]["id"])
```

(If multiple accounts of the same type are connected, pick the right one explicitly — by `name` or `id`, or by passing `resource_type` / `resource_id` for context filtering.)

Other helpers in this cluster:
- `list_auth_account_types()` — every integration Clay supports (whether or not connected)
- `get_auth_account_type(type)` — full type metadata + auth methods (e.g. Salesforce supports both OAuth and JWT)
- `get_auth_account(aa_id)` — fetch one account's details. Pass `resource_type="action-field"` + `resource_id={"tableId":..., "fieldId":...}` to mirror the UI's context-aware account picker (returns the same shape with abilities scoped to that table+field)
- `list_auth_accounts_by_type(type)` — broad integration-wide list by default; same `resource_type` / `resource_id` knobs if you want context filtering
- `validate_auth_credentials(auth_account_id)` — run Clay's `<type>-validate-auth` action against a specific connected account. Returns `{status, message, actionMetadata}`. Typically 0 credits but verify via `actionMetadata.upfrontCreditUsage`.

### Sourced-table creation (Find People / Find Companies)

**`preview_sourced_table` is DEAD as of 2026-07-23** — Clay removed the `*-preview` enrichmentTypes from the `run-enrichment` server allowlist, so the call now 400s. Free-preview replacement: the official `clay` CLI search (`clay search filters-mode create/run`) or the public API `/public/v0/search/filters-mode`. Historical behavior (through 2026-05-01): ran Clay's zero-credit preview and returned `{result, metadata, taskId}` without creating anything, hard-capped at exactly 50 rows, with `result.people` / `result.companies` keyed by entity type.

`create_sourced_table(workbook_name, inputs=..., cpj_type="people"|"companies")` is the materialization step: one call to `POST /sources/create-cpj-table` that returns `{tableId, viewId, workbookId, sourceId, isNewTable}`. This is the ClayCast path for headless Find People / Find Companies imports.

Notes:
- both `cpj_type="people"` and `cpj_type="companies"` are live-verified against workspace 12345 (latest pass: 2026-05-01)
- Companies preview follows the same hard 50-row cap as People; `limit=51` raises before any network call
- the body contract is asymmetric — `cpjConfig.type` is `"companies"` (plural) but `clientSettings.tableType` is `"company"` (singular). ClayCast maps this internally via `_CPJ_CLIENT_TABLE_TYPE`.
- the Companies `Size` starter column now defaults to plain `text`, not a hardcoded `select`, so ClayCast no longer depends on frontend-captured option UUIDs in the normal path. If you explicitly want the old chip-style Size column, `from clay_client import companies_basic_fields_with_select_size` and pass `basic_fields_override=companies_basic_fields_with_select_size()`.
- `destination_table_id=` is accepted live for Companies append-mode, but Clay may dedupe overlapping searches to zero net new rows
- for result sets over 50K rows, don't chain table exports; use `export_audience_segment(...)` against the saved audience layer instead

### Credit-usage reporting

`get_credit_usage(...)` wraps the 6 Settings → Usage report tabs: `workspace`, `integration`, `signal`, `triggerDefinition`, `mcp`, `api`. Multi-select filters use Clay's indexed bracket-array syntax under the hood (`ownerIds[0]=...`, `integrations[0]=...`), so callers just pass `owner_ids=[...]` / `integration_ids=[...]`.

`get_table_credit_usage(table_id, aggregation=...)` is the drill-down view for one table. Important shape note: the endpoint is not normalized across aggregations. Verified live, `aggregation="run"` returns a raw `list[dict]` of runs; treat the response as Clay-native payload, not a forced wrapper.

`get_default_workbook_credit_limit()` reads the workspace default workbook spend limit (`{"creditLimit": ...}` in the live smoke). ClayCast does **not** currently implement the UI's client-side Download CSV recreation for per-table credit usage; that remains intentionally deferred until UI parity is verified.

### Audience-segment export (>50K rows)

The audience layer is the only ClayCast export path that scales beyond Clay's 50K table-export cap. Use:

```python
segments = clay.list_audience_segments(entity_type="CONTACT")
segment_id = segments[0]["id"]
artifact = clay.export_audience_segment(segment_id, entity_type="CONTACT", format="csv")
```

Helpers:
- `list_audience_segments(entity_type="CONTACT"|"ACCOUNT")` — lists available segments
- `count_audience_segment(segment_id, ...)` — cheap count before export
- `export_audience_segment(...)` — writes local CSV/JSON under `<project_root>/tmp/clay-artifacts/`

CSV ordering is deterministic: `name`, `first_name`, `last_name`, `title` first when present, then alphabetical by `field_id`. Optional `include_signals`, `include_activities`, and `include_custom_objects` add per-row N+1 fetches; leave them off unless you really need the extra data.

### Record writes: critical gotcha

Writes against Clay's internal API follow a **two-step pattern**, not a one-shot `POST` with cells populated. `POST /tables/{t}/records` with cell values returns 200 and echoes back IDs — but silently drops the cells. The actual mechanism is:

1. `POST /tables/{t}/records` with `{"records": [{"id": <pregen>, "cells": {}}]}` — creates blank rows.
2. `PATCH /tables/{t}/records` with `{"records": [{"id": <pregen>, "cells": {...}}]}` — fills values.

`create_records` hides this by pre-generating IDs, doing both calls, and polling `get_records` until the values land (5-second deadline, raises `RuntimeError` on timeout). `update_record` and `bulk_update_records` use the `PATCH /records` bulk endpoint directly. Callers do not need to know about the two-step flow — the SDK handles it — but anyone reaching past the SDK to the raw HTTP layer must replicate this pattern.

**Fresh-table caveat (verified 2026-07-23):** on a freshly created table the name-keyed path (`create_records(..., field_names=True)`) can silently drop — the name→field-id mapped PATCH 200s but values never land, and the method raises its 5s "values did not persist" `RuntimeError`. The IDENTICAL PATCH keyed by raw field ids commits fine. `preflight()` shows `write: True`, so this is NOT the write-restricted-cookie mode. Workaround: create blank rows, then fid-keyed `bulk_update_records`, then re-fetch to verify.

### `list_records` strategy rule

`list_records(..., strategy="auto")` (default) uses the direct endpoint `GET /tables/{t}/views/{v}/records?limit=N` **only when** `limit` is set AND `field_ids` is omitted. Otherwise it uses the 2-step `get_record_ids() → get_records()` path. Pass `strategy="direct"` or `strategy="two_step"` to override.

### `list_sources` is subscription-scoped

`list_sources(table_id)` mirrors Clay's real `GET /sources?tableId={t}` behavior exactly: it returns only sources with active `sourceSubscriptions` bound into the target table. A source can exist and be fetchable via `get_source(source_id)` but still be absent from `list_sources()` if it has `sourceSubscriptions: []` (verified 2026-04-24 with a fresh webhook source).

### Choosing an export method (`export_csv` vs `export_rows` vs `fetch_all_records_full`)

Three methods, three fidelity levels. Decision rule:

| Need | Use |
|---|---|
| Same CSV the Clay UI / email gives | `export_csv()` — wraps Clay's native server-side export. **Action ("Response") columns export as the literal string `"Response"`** — no enrichment data. |
| CSV/JSON with full enrichment payloads, written locally | **`export_rows()`** — fetches via `bulk-fetch-records`, writes to `<project_root>/tmp/clay-artifacts/`. Strictly richer than `export_csv` for any table with action columns. |
| Highest-fidelity per-record data (including `externalContent.fullValue` from action columns) | `fetch_all_records_full()` — parallel fetch via the single-record endpoint that `bulk-fetch-records` doesn't expose. Slower (~27ms/record × workers) but completest. |

**If you care about action-column data at all, do NOT use `export_csv()`.** Full discussion at `references/clay-api-reference.md` → `## Export → Decision rule`.

### Local artifact output (document_table / export_rows / export_workspace)

`document_table(table_id, ...)` (markdown), `export_rows(table_id, ..., format="csv"|"json")`, and `export_workspace(workspace_id, ..., include_rows=)` all write under `<project_root>/tmp/clay-artifacts/` by default. `project_root` is discovered with the same upward-walk boundaries as the auth loader. Every method returns both the in-memory payload (markdown string / CSV string / workspace JSON dict) AND the absolute `path` of the written file. Append-only: if `filename=` targets an existing path, claycast suffixes rather than overwriting, so prior artifacts are never silently deleted. Pass `output_dir=` to target a different directory. R2 upload / public URL generation is explicitly NOT part of claycast — callers decide whether/how to publish.

`search_export_artifacts(sources, ...)` is the consumer side: takes absolute paths, `file://` URLs, or `http(s)://` URLs (including local HTTP servers) and runs row/header searches over Clay-export **JSON** artifacts. CSV search is not implemented in the current SDK. No implicit directory crawling — source lists are explicit.

### `inspect_table` vs `document_table`

`inspect_table(table_id, ..., include_samples=True, include_lineage=True)` returns **structured JSON** with field types, semantic types, lineage, and sample records — designed for programmatic analysis. `document_table(...)` returns **markdown** designed for humans. They are intentionally non-overlapping; use whichever format fits the downstream consumer.

### `upsert_records` cost + duplicate + pre-generated-ID semantics

- **O(N) scan** over the chosen view before any upsert work. For large tables, pass `max_scan_rows=<N>` to refuse if the view exceeds the cap; pass `confirm_large_scan=True` to override.
- **Existing-row duplicates** (same match-field value on multiple rows): last-seen-wins in the match index.
- **Incoming-payload duplicates**: claycast dedupes deterministically, keeping the LAST occurrence of each match value. The writer's original behavior was order-sensitive and non-deterministic; claycast corrects it.
- **Pre-generated record IDs for the create branch.** When claycast decides a row needs to be CREATED (match value not in the existing index), it generates a Clay-style id via `_gen_record_id()` (= `"r_" + 12 random alphanumeric chars`, 62^12 ≈ 3.2e21 namespace) and passes that id explicitly in the POST body. This lets the return value expose the new ids immediately (`result["record_ids"]["created"]`) so callers can reference them without a follow-up fetch. Clay accepts caller-generated ids on `POST /tables/{t}/records` — no conflict with Clay's own id scheme. The same helper is used by `create_records(record_ids=[...])` when callers want pre-generated ids for any reason.
- **Return shape.** `{"created": int, "updated": int, "skipped": int, "record_ids": {"created": [...], "updated": [...]}, "scanned_existing": int}`. `skipped` counts incoming-payload dedupes; `scanned_existing` is the count of rows fetched from the view to build the match index (useful for guardrail tuning).

### Portable schema (ClayPrint format)

`export_schema` / `import_schema` serialize a table's column structure (types, formulas, action configs, dependencies) in a portable format where `{{f_<id>}}` field references are rewritten as `{{@Column Name}}`. This is claycast's own format (not an official Clay term) and is the idiomatic way to copy or clone a table's structure across tables where field IDs differ:

```python
schema = clay.export_schema(src_table_id)                # optional: column_names=[...]
clay.import_schema(dst_table_id, schema)                 # creates columns in dependency order
clay.import_schema(dst_table_id, schema, dry_run=True)   # validate without creating
```

Import automatically topologically sorts columns so formulas land AFTER the columns they reference. Source / `v3-action` columns round-trip via separate name↔id maps inside the helpers; callers don't need to resolve those manually. There is no single `clone_table` method today — compose the two calls above.

### Column operations — view-scoped vs table-scoped

Two axes matter when mutating columns:

- **Move + visibility + group-move are VIEW-SCOPED.** Each view gets its own column order and hide/show state. A field hidden in one view still shows in another. Methods: `move_field`, `reorder_fields`, `set_field_visibility`, `set_fields_visibility`, `move_field_group`.
- **Delete + group-create/update/destroy are TABLE-SCOPED.** They mutate the underlying table schema, not a per-view projection. Deleting a column removes it everywhere. Methods: `delete_column`, `delete_fields`, `create_field_group`, `update_field_group`, `ungroup`, `delete_field_group`.

For bulk reordering within one view, `reorder_fields(field_ids=[B,C,D,E], after_field_id=A)` moves the whole block in ONE request — preferred over N sequential `move_field` calls.
`reorder_fields` only works on a currently contiguous block; Clay returns HTTP 400 `"Fields are not adjacent in the view"` for non-adjacent sets.
Live 2026-07-21: even the full-view block failed (`reorder-fields` 400 on spreadsheet tables, 500 on people tables) — to impose a whole view's order, walk per-field `move_field` calls, skipping already-placed fields.

### `ungroup` vs `delete_field_group` — keep them separate

Both hit `DELETE /tables/{t}/fields/group/{gr_id}` but with different bodies:
- `ungroup(table_id, group_id)` → `{"deleteFields": false}` — dissolves the group, keeps members as loose columns. **Safe.**
- `delete_field_group(table_id, group_id)` → `{"deleteFields": true}` — destroys the group AND all member fields. **Irreversible.**

ClayCast keeps these as separate verbs deliberately. If they shared one method with a kwarg, picking the wrong value means total loss of the group's columns. Separate names = no wrong-kwarg footgun.

### `update_field_group(fields=)` is atomic replacement

`update_field_group(fields=[...])` REPLACES the group's entire member list. A field id you omit from the list is REMOVED from the group. To safely tweak one member's `isOutputField` flag without disturbing others, fetch current membership first (via `get_table(..., include_extra_data=True)` and read `fieldGroupMap`) then send back the full updated array.
If you only want to rename the group, claycast auto-fetches the current members and preserves them for you — Clay itself still requires `fields` on every update.

### ClayCast design principles (applies to any new method added)

Cross-cutting conventions enforced across the SDK — follow these when adding or modifying methods:

- **No silent timeout clamps.** Timeout-style kwargs should honor the caller's value exactly; claycast no longer silently clamps importer waits to 120s. Some older helpers still apply documented compatibility bounds today (`list_records(limit)` caps at 1000; record-write helpers cap `batch_size` at 500). Treat the method docstring as the source of truth for those cases rather than assuming unlimited input.
- **Raise, don't accumulate.** ClayCast surfaces server and validation errors as exceptions. No `errors[]` accumulator bag, no `success: False` with a string message — that's Datagen-flow style, not SDK style.
- **Pre-fetch for validation.** Name-based methods (`field_names=True`, `match_field_name`, etc.) fetch the field map once up front and raise `ValueError` on unknown keys BEFORE any network mutation.
- **Constructor-only auth overrides.** `ClayClient(clay_session=...)` takes the override at construction; per-call `claysession=` kwargs are not supported.
- **Append-only local artifacts.** Artifact producers (`document_table`, `export_rows`, `export_workspace`) never overwrite existing files. Suffix-on-collision, 0o700 perms, user-owned cleanup.
- **Bounded walk-ups.** `_find_env_with_session` and `_find_project_root` both refuse `$HOME` and `/` as terminal dirs and stop at `.git` sentinels. Any new walk-up logic should reuse those helpers.

### Where to look for method-level detail

The capability map above is an index, not a reference. For exact kwargs, return shapes, edge cases, and examples:

- **Docstrings** in `scripts/clay_client.py` are the source of truth per method. Use `help(clay.inspect_table)` in a REPL, or `python -c "from clay_client import ClayClient; help(ClayClient.export_rows)"`. Most methods document their failure modes, kwarg defaults, and return-dict shape inline.
- **`references/clay-api-reference.md`** documents the underlying Clay endpoints (URLs, payloads, response shapes) that claycast wraps.
- **`references/clay-api-reference.md` → "End-to-end flows"** sequences multi-endpoint Clay workflows (Find People search → save → table; loading a saved search; Continue dropdown variants). Read this when implementing a flow that crosses 3+ endpoints — it shows the order, the IDs that pass between them, and the SDK-direct shortcut for each path.
- **`references/action-registry.md`** documents known action keys + their `inputsBinding` schemas.
- **`references/feature-gaps.md`** documents things claycast doesn't do yet, before you conclude "claycast can't do X."

---

## Migrating from the `clay_record_writer_v2` Datagen flow

ClayCast ports every mode of the writer deployed at Datagen UUID `71197300-6fdb-4ed6-bd50-aad91eff49ef`. If you've been calling that flow, here's the equivalence:

| Writer mode | Writer input shape | ClayCast equivalent |
|---|---|---|
| `create`   | `records=[{field_name: value}]`             | `clay.create_records(t, records, field_names=True)` |
| `update`   | `records=[{_record_id, field_name: value}]` | `clay.bulk_update_records(t, records, field_names=True)` |
| `upsert`   | `records=[...], match_field`                | `clay.upsert_records(t, records, match_field_name=...)` |
| `delete`   | `record_ids=[...]`                          | `clay.delete_records(t, record_ids)` |
| `read`     | `view_id?, limit=100`                       | `clay.list_records_by_name(t, view_id, limit=100)` |
| `read_one` | `record_id`                                 | `clay.get_record_by_name(t, record_id)` |
| `count`    | —                                           | `clay.count_records(t)` |

**When to keep using the Datagen flow instead of claycast:** the writer is deployed as an HTTP endpoint (sync `POST https://api.datagen.dev/apps/71197300-6fdb-4ed6-bd50-aad91eff49ef`, async variant with `/async`). If you need CRUD callable from *inside* Clay — e.g., an `http-api-v2` action column that writes to another Clay table — keep using the Datagen endpoint, because claycast is a Python SDK and can't run inside Clay's action runtime. ClayCast is for running CRUD from your own Python; the Datagen flow is for calling CRUD from inside Clay itself.

---

## Browser daemon — `clay_browser.py`

Playwright-based daemon that runs a visible or headless Chromium with your Clay session cookie injected, and auto-captures every `api.clay.com` request + response to `/tmp/clay-browser/requests.jsonl`. Use it to (a) discover real API shapes when claycast doesn't wrap an endpoint yet, (b) watch live Clay UI behavior against a live workspace, or (c) drive the UI programmatically.

### Commands

| Command | Purpose |
|---|---|
| `launch [--headless]` | Start the daemon (forks in the background), inject session cookie, begin capture |
| `close` | Graceful shutdown; synchronously unlinks all capture files (`requests.jsonl`, `daemon.log`, `server.{sock,pid}`) before returning |
| `goto <url>` | Navigate the page |
| `snapshot` | Aria-tree snapshot of the current page |
| `screenshot [path]` | PNG (default `/tmp/clay-browser/shot.png`) |
| `click <text> [--role <aria-role>] [--nth N]` | Click by visible text (optionally scoped to role or nth match) |
| `click_selector <css>` | Click by CSS selector |
| `fill <text> [--placeholder <str>]` | Type into a text input (targets `[placeholder=]` when provided) |
| `eval <js>` | Run JS in page context — wrap multi-statement logic in an IIFE `(() => { …; return X; })()` since top-level `return` is a SyntaxError |
| `requests [--filter <substr>] [--last N]` | Dump captured `api.clay.com` traffic (filter by URL substring, tail last N entries) |

### Action-discovery recipe

```bash
cd "$SKILL_ROOT/scripts"
python clay_browser.py launch --headless
python clay_browser.py goto "https://app.clay.com/workbooks/<id>"
python clay_browser.py click "Add enrichment" --role button
python clay_browser.py fill "<action name>" --placeholder "Search"
python clay_browser.py click "<exact action title>"
python clay_browser.py requests --filter fields --last 5
python clay_browser.py close
```

Pull the `inputsBinding` array from the real POST and mirror it in `create_action_column`. If a claycast call starts returning 400/422/silently-wrong data, re-probe the payload shape the same way.

### Caveats

- `fill --placeholder` cannot reliably drive React-controlled token-picker components (e.g. Clay's column picker). For those, inspect concurrent waterfall-preset responses instead.
- `--headless` means no visible window; the daemon still captures traffic. For manual driving, launch without `--headless`.
- Capture files have `0600` perms, but still contain your session cookie and scraped PII until `close` removes them. Don't `kill -9` the daemon mid-capture without manually deleting `/tmp/clay-browser/`.

---

## Gotchas

Highest-risk items only. Full list in `references/clay-api-reference.md` (see its Gotchas section).

- **View `filter`/`sort` can only be written via sub-endpoints.** `PATCH`/`POST` on a view return 200 but SILENTLY DROP `filter` and `sort` from the body — use `PATCH /tables/{t}/views/{v}/filter` and `.../sort` instead. Bulk visibility+width go through `PATCH .../views/{v}/fields` (`{fid: {isVisible, width}}`); whole-view column order needs a per-field `move_field` walk (`reorder-fields` rejects full-view blocks). Verified 2026-07-21 — see the reference's "View filter/sort write path + replication side-effects" section.
- **Listing records has two working endpoints.** `GET /tables/{t}/views/{v}/records?limit=N` returns `{results: [...]}` directly and honors `limit` (verified 2026-04-23). The old view-only `GET /views/{id}/records` (no tables prefix) 404s and is unsupported. `list_records(..., strategy="auto")` uses the direct endpoint only when `limit` is set AND `field_ids` is omitted; otherwise it uses the 2-step `get_record_ids()` → `get_records()` path against `POST /tables/{t}/bulk-fetch-records`. Do not reinvent offset/cursor paging.
- **`dataTypeSettings.type` is `text` for formula/display columns but `json` for record-returning action columns.** For a **formula or plain text** column, `{"type": "json"}` is accepted by the API but breaks the Clay UI with "Could not find properties for data type json" — use `text`. For an **action column that returns structured records** (Salesforce lookup / SOQL, enrichments, anything whose cell is an object or array), the opposite holds: `text` is *rejected at create time* with the opaque `400 BadRequest "value does not match any of the allowed types"`, and you must pass `{"type": "json"}`. The json column renders fine in the UI for these actions (verified: a `salesforce-lookup-record-v2` column has used json in production since 2026-04). `create_action_column` defaults `data_type="json"` for known record-returning action keys (see its docstring); pass `data_type` explicitly to override. Verified 2026-05-28.
- **`actionKey` must be `"use-ai"`**, not `"ai"` — the wrong key silently drops all `inputsBinding`. `create_action_column` raises `ValueError` if you pass the wrong one.
- **`http-api-v2` `body` is `longtext`, not an object** — build it with **`format_json_body({...})`** (emits `formulaText` + `Clay.formatForJSON()` exactly as the UI does). The `formulaMap` rule applies to the object-typed `queryString`/`headers` only. Verified 2026-07-24.
- **Bind an action's FULL parameter list or the column shows NO inputs in the UI.** Clay renders the input form from `inputsBinding` itself, so a column bound with only the params you use runs correctly but opens with an empty config panel (users report "this column has no inputs"). Get the list from `clay workflows actions schema <pkg> <actionKey>` and pass every name to `create_action_column`, unset ones as `None` (emitted as bare `{"name": …}`). Include pipe-nested children (`retryOptions|maxRetries`). `http-api-v2` = 15 params. Verified 2026-07-24.
- **Dependency checks need BOTH the graph AND a full reference scan — graph = structure, scan = literal references.** Two independent failure modes, neither view alone is complete:
  - A naive `{{f_id}}` scan of `formulaText` alone misses **`formulaMap`** inputs (`execute-subroutine` and others bind dict-style sub-inputs), `ConditionalRun` edges, and **transitive** downstream. (Verified 2026-06-04: such a scan called a column orphaned while the graph showed an `execute-subroutine` taking it as a `formulaMap` input, with 28 transitive downstream columns.)
  - Clay's **graph collapses an action's extracted formula columns into the action node** (`node.extractedFieldIds`), so `get_field_dependents`/the edges do NOT list those extractors. (Verified 2026-06-04: `get_field_dependents` on a Salesforce lookup returned its downstream consumers but not the two extractor formulas holding the literal id — those are what a remap must repoint.)
  - So: use `get_table_graph()` / `get_field_dependents(transitive=…)` for downstream **structure**; use **`get_field_references(table_id, field_id)`** (a full `typeSettings` scan covering `formulaText`, `conditionalRunFormulaText`, every `inputsBinding.formulaText`, AND `formulaMap` values) to find the **literal references** you must edit for a remap or check for delete-safety. `get_field_dependents` now folds in `extractedFieldIds`, and `delete_column`/`delete_fields` are guarded by `get_field_references` (raise listing references unless `force=True`).
- **`404 "App Account not found"` on column create = stale `authAccountId`.** Once the opaque 400 above is cleared (by using json), the next failure is usually a 404 because the `authAccountId` you passed no longer exists. The `authAccountId` baked into an *existing* column's `typeSettings` can be stale — we hit a workspace where existing Salesforce columns referenced an `aa_…` id that `list_auth_accounts()` no longer returned. **Always resolve auth fresh via `list_auth_accounts_by_type('<type>')`; never copy `authAccountId` out of an existing column's typeSettings.** Verified 2026-05-28.

---

## Known operational risks

Things that will silently bite you if you don't know they exist. Ranked by how often they cost real debugging time.

- **Creating a `route-row` action column auto-creates the whole receiving pipeline on the TARGET table** — a `Rows from: <sender>` routing source, a source column, and extractor formula columns for every `rowData` key. If you script those receiving columns yourself you'll hit duplicate-name 400s; keep the auto-created structures (they hold the sender binding) and repoint/delete your duplicates. Likewise every `create-cpj-table` attempt (even a failed one) drops a companion `Update People Search` trigger column on the company table — and (verified 2026-08-06) every FAILED attempt also leaves an invisible empty `Find people Table (N)` shell with no presentation-map position; after any create-cpj work, assert the exact workbook table set via a full workbook-tables listing (per-table sweeps can't see the shells). Verified 2026-07-21.
- Workflow webhook URLs (`…/streams/wfrs_*/webhook`) rotate on ANY trigger edit — status flips AND inputSchema changes. Re-read `webhookUrl` after every edit and update every hardcoded reference (verified 2026-08-06).
- **Writes are async-enqueue.** Every PATCH returns `{"records": [], "extraData": {"message": "Record updates enqueued"}}` within milliseconds; the cells actually land seconds later. `create_records` polls internally (5-second deadline) and raises `RuntimeError` on timeout — if you see this on a slow workspace, bump the deadline in code rather than assuming the write failed. `bulk_update_records` and `update_record` don't auto-verify; callers who need confirmation must re-fetch and assert.

- **A webhook source without a source FIELD swallows every POST.** `create_webhook_source` returns a working URL — POSTs come back OK and `state.numSourceRecords` climbs — but NO rows appear until a source FIELD (`{"type": "source", "typeSettings": {"sourceIds": [s_id], "canCreateRecords": true}}`) registers the subscription, which then retroactively materializes the buffered records. Also: a JSON array of N objects counts as ONE source record — no fan-out; send one object per POST. Verified 2026-07-24.

- **Name-keyed `create_records` silently drops on FRESH tables.** The name→field-id mapped PATCH 200s but values never land (the method raises its 5s "values did not persist" RuntimeError); the identical fid-keyed PATCH commits fine, and `preflight()` shows `write: True` (not the cookie mode). On fresh tables seed via blank rows + fid-keyed `bulk_update_records` + re-fetch. Verified 2026-07-23.

- **Cell `value` is a preview, not the truth, for action cells.** An HTTP-API column cell may show `cell["value"] == "Status Code: 200"` while the real response body (and any silent data corruption from a mangled request) lives in `cell["externalContent"]["fullValue"]`. Always use `extract_cell_value()` or read `externalContent.fullValue` directly. **G3 real-world example: a `formulaText` holding the string `'{"q": hello}'` for an `http-api-v2` `queryString` input gets character-split server-side — Clay sends `?0={&1="&2=q...` instead of `?q=hello`, httpbin returns 200, preview says "Status Code: 200", and the attack only shows up in `externalContent.fullValue`.** Verify 2026-04-23.

- **Bulk writes silently accept fake IDs.** A `bulk_update_records` batch with one valid `record_id` and one fake `record_id` succeeds with 200, updates the valid row, drops the fake without comment. "Migrate these ids" workflows can lose writes undetected. For strict guarantees, re-fetch each id post-write and assert presence + value.

- **Clay silently stores unknown action-column input names.** `create_action_column` with a phantom `linkedin_url` binding (or any other typo/wrong name): the API accepts the shape verbatim, the action ignores the unknown key at runtime, credits burn on rows that produce nothing. `action-registry.md` accuracy is the only safety rail and has known drift — when an enrichment produces blank results, first suspect a stale registry entry and re-discover with `clay_browser.py`.

- **Cookie can become "write-restricted" for no clear reason — this includes action RUNS, not just record writes.** We hit a state where the same cookie that worked in a browser window wrote successfully via the UI but every direct PATCH from Python enqueued and never committed. The same applies to `run_column` / `run_and_wait`: the call returns a healthy-looking `{"recordCount": N, "runMode": "INDIVIDUAL"}` but the action never executes — cells stay `null` with **no value and no metadata status** no matter how long you poll. Don't mistake this for a slow run or a bad query (if the query were bad you'd get an error status on the cell). `clay.preflight(table_id=...)` packages the up-front check (auth + one committed-and-deleted blank record via the view-independent per-record endpoint) — run it before any bulk work. If reads keep working but a freshly-triggered run produces null cells with no status after ~30–45s, stop polling: either **run the column from the Clay UI** (its runtime executes where the API trigger stalls) or **pull a fresh `claysession` cookie from DevTools and replace `.env`**. Verified 2026-05-28.

- **`run_column` ACKs are not execution — two more silent-skip modes beyond the cookie one above (verified 2026-07-30).** (a) A column whose `conditionalRunFormulaText` doesn't pass is skipped silently — blank cell, no status (`!!{{gate}}` gates skipped even when the gate cell read true); `force_run=True` bypasses. (b) AI **and provider** action columns never executed via the API at all — `use-ai` (claygent-useCase; plain `"use-ai"` useCase DOES auto-run on arriving rows — see api-reference "AI Columns" corrected entry), and (verified 2026-08-06) provider enrichment actions like `leadmagic-enrich-company` on dark tables even with `preflight()` passing — 0 credits moved, blank cell, even force-run just parks `{"metadata": {"trigger": "FORCE-RUN"}}` — while a lookup column ran fine through the identical call (so NOT the write-restricted-cookie mode). The identical inputs succeed via the plugin MCP's `execute_clay_action`. Run AI/provider columns from the Clay UI or verify via `execute_clay_action`; only free lookups/formulas run reliably through the in-table API path. Details: clay-api-reference.md "AI Columns" + "Conditional Execution".

- **⚠ Invalid `semanticTypeEnum` in `SUBROUTINE_INPUTS` bricks the workspace tools registry** (verified 2026-07-31). Guessing an enum value (e.g. `"json"`) when configuring a function table's inputs makes `GET /workspaces/{ws}/tools` 500 workspace-wide — degrading routines infrastructure — until reverted. Known-good vocabulary: `company-domain, company-linkedin-url, company-name, date, unknown, person-linkedin-url, email`; inputs are scalar-only (no json/object type). Full function-creation recipe: clay-api-reference.md § "Creating a custom function (subroutine table) via API".

- **Auto-blank row on `create_table`.** Every freshly-created table contains one empty row before you insert anything. `count_records` returns `N+1` for `N` inserts. Assertions and idempotency checks must account for it.

- **`list_records` strategy branch is conditional.** `strategy="auto"` uses the fast direct endpoint only when `limit` is set AND `field_ids` is omitted. Pass `field_ids` with a small `limit` and claycast falls back to the 2-step flow (fetch all ids, then bulk-fetch the subset you want) — slow for large tables. Force with `strategy="direct"` if you don't need field filtering.

- **`upsert_records` is O(N) over the view.** It fetches every row in the selected view before deciding update-vs-create. 10k rows ≈ 20 `bulk-fetch-records` calls before any mutation work starts. Use `max_scan_rows=<N>` to refuse if the view is too big; pass `confirm_large_scan=True` to override.

- **Ambient undocumented-API risk.** The whole skill rides Clay's internal REST API — no stability guarantees. Any endpoint, body shape, or required header can change without notice. If a call that used to work starts returning 400/404/silently-wrong data, re-probe with `clay_browser.py` against the live UI before assuming your code is wrong. Keep `references/clay-api-reference.md` and `references/action-registry.md` updated when you discover drift.

---

## Credit safety

ClayCast hits the real Clay API — action runs (`run_column`, `run_and_wait`, and any `create_action_column` that auto-executes on existing rows) burn credits just like MCP calls. Before a bulk run, estimate credits (rows × per-row cost) and confirm with the user unless they've said "go wild".

- Re-enabling AUTO_RUN_ON backfills every stale/empty auto-run cell immediately (full-table credit spend). Flip only after data load + sample QA, with conditional-run gates re-armed (verified 2026-08-06).
