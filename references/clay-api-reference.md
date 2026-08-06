# Clay Internal API Reference

> Reverse-engineered internal API for programmatically building and updating Clay tables.
> Validated in production March-April 2026. See `clay_client.py` for the Python client.

---

## Setup

```python
from clay_client import ClayClient

clay = ClayClient()  # reads CLAY_SESSION from process env or project .env, prints logged-in email + workspace
```

**Auth:** Session cookie from Chrome DevTools → `app.clay.com` → Application → Cookies → `claysession`.
Set it as `CLAY_SESSION` using either the current process environment or a `.env` file discovered by walking up from the current working directory:
```dotenv
CLAY_SESSION=s%3A...
```
This patched skill does **not** read `clay-session.json`. Cookie expires every few weeks — refresh manually. No Keychain or browser automation needed.

**Workspace ID:** `YOUR_WORKSPACE_ID` (auto-discovered via API if not provided)
**Base URL:** `https://api.clay.com/v3/`

---

## Verified ClayCast SDK Surfaces (2026-04-24)

These methods are live-verified in the current ClayCast SDK and are the preferred surfaces over raw HTTP when they cover your use case.

### Workspace navigation

- `clay.list_workspaces()` → wraps `GET /my-workspaces`
- `clay.get_workbook(workbook_id, workspace_id=...)` → wraps the direct endpoint `GET /{workspace_id}/workbooks/{workbook_id}`
- `clay.list_workspace_contents(workspace_id, include_tables=True)` → uses `POST /workspaces/{ws}/resources_v2/` with `{"parentResource": null, "filters": {}, "isGlobalSearch": true}`; the `isGlobalSearch=true` bit matters because Clay's default/naive request body misses many workbooks in larger workspaces
- `clay.find_tables(...)`, `clay.get_workspace_hierarchy(...)`, and `clay.get_resource_urls(...)` are layered on top of that verified workspace/workbook data

### Table / field management

- `clay.create_table(...)` now supports:
  - `workspace_id=`
  - `table_type=` (`spreadsheet`, `company`, `people`, `jobs`) — all four returned 200 in live probes
  - `fields=` for create-with-schema
  - `seed_data=` for create-with-data
  - `source_table_id=` + `clone_mode="shallow"` for name/type-only structure clone
- Fresh tables still come with Clay's auto-blank row. Count assertions must account for that.
- `clay.list_fields(...)` returns normalized field metadata with view-aware ordering / visibility.
- `clay.apply_field_operations(...)` ports Datagen bulk add / rename / retype without the Datagen `errors[]` envelope.
- `clay.generate_table_description(table_id, save=True)` mirrors Clay's built-in **AI "Generate"** button next to a table's Description. Two calls (discovered via clay-spy, verified 2026-06-12): (1) `POST /ai-generation/table-description` body `{"workspaceId": <int>, "tableId": "t_..."}` → `{"description": "<AI summary>"}` (read-only — AI reads the table's columns/sources); (2) if `save` (default), persists via `set_table_description`. `save=False` previews without writing. Returns `{description, saved, table}`.
- `clay.set_table_description(table_id, description)` → `PATCH /tables/{table_id}` with `{"description", "tableSettings": {}, "fieldGroupMap": {}, "sourceSettings": {}}`. This is the general **top-level table PATCH** endpoint; the empty setting dicts are no-op merges (live-verified that AUTO_RUN/dedupe settings survive an empty `{}`), so only the description is rewritten.

### CSV imports

- `clay.preview_csv_input(...)` parses inline or remote CSV without touching Clay.
- `clay.import_csv_to_table(...)` uses the real Clay import flow:
  1. `POST /imports/signed-s3-post-url`
  2. multipart upload to the returned S3 URL
  3. `POST /imports`
  4. optional poll via `GET /imports/{job_id}`
- The S3 upload must be a clean multipart request. Reusing a Clay JSON session header (`Content-Type: application/json`) breaks the signed upload.
- `clay.get_import_job(job_id)` and `clay.wait_for_import_job(job_id)` are the low-level job surfaces.
- `clay.wait_for_import_job(..., timeout_seconds=300)` and `clay.import_csv_to_table(..., timeout_seconds=300)` honor the caller's timeout exactly. ClayCast does **not** silently clamp `600` down to `120`.

### Source inspection

- `clay.list_sources(table_id)` mirrors `GET /sources?tableId={t}` exactly.
- Real Clay behavior is **subscription-scoped**: a source with `sourceSubscriptions: []` does not appear in `list_sources(table_id)` even though `clay.get_source(source_id)` returns it normally. Verified 2026-04-24 with a fresh webhook source.
- Use `clay.get_source(source_id)` when you already know the source id and need the superset view, including `sourceSubscriptions`.

### Runs / monitoring

- `clay.run_column(...)` now supports:
  - `field_names=`
  - `top_n=` + `view_id=` (`viewIdTopRecords`)
  - `force_run=`
  - omitted field list = resolve all runnable fields (`action`, `enrichment`, `source`, `waterfall`, `claygent`)
  - **Silent-skip gotchas (verified 2026-07-30):** the ACK (`{"runMode": "INDIVIDUAL"}`) does NOT mean the run will execute. (a) Columns with `conditionalRunFormulaText` whose condition doesn't pass are skipped with a completely blank cell — no status, no error; `force_run=True` bypasses. (b) `use-ai` columns never executed via the API at all in testing — see "AI Columns" checklist below. `lookup-row-in-other-table` columns ran fine through the same call in the same session.
- `clay.get_run_status(table_id)` normalizes both `GET /tables/{t}/fieldrun` and `GET /workspaces/{ws}/tables/{t}/fields/runstatus`.
- `clay.wait_for_runs(...)` is the shared polling / stall-detection surface used to cover the Datagen job-monitor behavior.
- `clay.rerun_errored_cells(...)` is the SDK recipe for Datagen `rerun_errors`: find the Errored Rows view, inspect which specific cells failed, then re-run only those field+record combinations.

### Structured inspection / local artifacts

- `clay.inspect_table(table_id, ...)` wraps `GET /tables/{t}/views/{v}/table-schema-v2` via `get_schema(...)` and returns structured field/type/semantic-type metadata plus optional sample rows.
- `clay.document_table(table_id, ...)` writes markdown locally and returns:

```python
{"markdown": "...", "path": "/abs/path/to/document-<table_id>-<timestamp>.md"}
```

- `clay.export_rows(table_id, format="csv"|"json", ...)` writes a local artifact and returns both the in-memory payload and absolute artifact path.
- `clay.export_workspace(workspace_id=..., include_rows=False, ...)` writes a local JSON workspace export and returns `{"content": ..., "path": ..., "manifest": ...}`.
- `clay.search_export_artifacts([...])` searches previously written **JSON** export artifacts from local paths or explicit URLs. CSV search is not implemented in the current SDK.
- Default output directory: `<project_root>/tmp/clay-artifacts/`
- `project_root` uses the same upward-walk boundaries as the auth loader (`.git`, `$HOME`, filesystem root)
- The artifact directory is append-only by default: ClayCast never deletes prior files automatically, and if `filename=` already exists it suffixes instead of overwriting
- For the higher-level routing and current capability map, see `../SKILL.md`; this reference stays focused on endpoint and payload behavior.

---

## Known Action Package IDs

| Action | `actionKey` | `actionPackageId` |
|--------|-------------|-------------------|
| Use AI (Claude/Gemini/GPT) | `use-ai` | `67ba01e9-1898-4e7d-afe7-7ebe24819a57` |
| Enrich Company (Mixrank) | `enrich-company-with-mixrank-v2` | `e251a70e-46d7-4f3a-b3ef-a211ad3d8bd2` |
| HTTP API v2 | `http-api-v2` | `4299091f-3cd3-4d68-b198-0143575f471d` |
| Lookup Multiple Rows | `lookup-multiple-rows` | `4299091f-3cd3-4d68-b198-0143575f471d` |
| LinkedIn Posts | `social-posts-get-post-activity-posts-and-shares` | `b210a16b-cdaf-4cbd-ad9b-42d762cd165f` |
| Instantly: Add Lead to Campaign | `instantly-v2-add-lead-to-campaign` | `70cda03a-a576-4a6c-b3b3-55e241f828b5` |
| Instantly: Find Leads | `instantly-v2-find-leads` | `70cda03a-a576-4a6c-b3b3-55e241f828b5` |
| Instantly: Update Lead | `instantly-v2-update-lead` | `70cda03a-a576-4a6c-b3b3-55e241f828b5` |
| Lookup Single Row | `lookup-row-in-other-table` | `4299091f-3cd3-4d68-b198-0143575f471d` |
| Lookup Multiple Rows | `lookup-multiple-rows-in-other-table` | `4299091f-3cd3-4d68-b198-0143575f471d` |
| LeadMagic: Find Work Email | `leadmagic-find-work-email` | `edb58209-a62d-42be-992a-e41b87eeacc2` |
| Prospeo: Find Work Email | `prospeo-find-work-email-v2` | `48a31bbb-63e6-4461-8a62-d88bb2cd6b0f` |
| FindyMail: Find Work Email | `findymail-find-work-email` | `9515bb04-4267-4074-94eb-653545c3c38f` |

To find other action package IDs:
```python
results = clay.search_enrichments("leadmagic email")
# → [{"entity_id": "pkg_id/action_key", "name": "...", ...}]
# entity_id format: "{actionPackageId}/{actionKey}"
```

---

## AI Model Names (valid as of March 2026)

| Model | formulaText value | Auth Account | Notes |
|-------|-------------------|--------------|-------|
| Grok 4.1 Fast Reasoning ✅ | `"grok-4-1-fast-reasoning"` | `YOUR_XAI_AUTH_ACCOUNT_ID` | Best for structured JSON via `answerSchemaType` |
| Gemini 2.5 Flash ✅ | `"gemini-2.5-flash"` | `YOUR_CLAY_GEMINI_AUTH_ACCOUNT_ID` | Fast, Clay credits. Wraps JSON in code fences. |
| Gemini 2.5 Pro | `"gemini-2.5-pro"` | `YOUR_CLAY_GEMINI_AUTH_ACCOUNT_ID` | Wraps JSON in code fences. |
| GPT-4o-mini | `"gpt-4o-mini"` | `YOUR_CLAY_OPENAI_AUTH_ACCOUNT_ID` | Also wraps JSON in code fences. |
| GPT 5 Nano | `"gpt-5-nano"` | `YOUR_CLAY_OPENAI_AUTH_ACCOUNT_ID` or custom | Cheapest (0.5 credits). Successor to GPT 4.1 Nano. |
| GPT 4.1 Nano | `"gpt-4.1-nano"` | `YOUR_CLAY_OPENAI_AUTH_ACCOUNT_ID` or custom | 0.5 credits. Use for lightweight Claygent tasks. |
| GPT 4.1 Mini | `"gpt-4.1-mini"` | `YOUR_CLAY_OPENAI_AUTH_ACCOUNT_ID` or custom | 1 credit. |
| GPT 4.1 | `"gpt-4.1"` | `YOUR_CLAY_OPENAI_AUTH_ACCOUNT_ID` or custom | 9.7 credits. Smartest non-reasoning OpenAI model. |
| o4 Mini | `"o4-mini"` | `YOUR_CLAY_OPENAI_AUTH_ACCOUNT_ID` or custom | ~2.9 credits. Reasoning model. |
| ~~Gemini 2.0 Flash~~ ❌ | deprecated | — | throws "No model found" error |

**For structured JSON output:** Use Grok 4.1 + `answerSchemaType` (see below). Both Gemini and GPT wrap output in markdown code fences which breaks `?.key` formula extractors.

### AI Column Use Cases: Claygent vs Create Content

Two distinct `useCase` values control AI column behavior:

| useCase input | Clay UI Name | What it does |
|---------|-------------|-------------|
| `"claygent"` | Web Research (Claygent) | AI agent with web search — browses the internet to find answers. Use for data enrichment tasks like "Get employee count from {{@Company LI URL}}". |
| `"use-ai"` | Create Content | Simple text/JSON generation from provided inputs — no web access. Use for qualification, copy generation, data transformation. |

**CRITICAL: Both use `actionKey: "use-ai"`** (not `"ai"`). The `useCase` input differentiates them.
Using `actionKey: "ai"` causes all `inputsBinding` to be silently dropped.

**Key differences:**
- **Claygent** burns more credits (agent loop + web search) but can look up live data
- **Create Content** is cheaper, faster, deterministic — works only with data already in the table
- Both support `answerSchemaType` for structured output and `conditionalRunFormulaText` for conditional execution
- Both use `actionKey: "use-ai"` and `actionPackageId: "67ba01e9-1898-4e7d-afe7-7ebe24819a57"`
- Both require an `authAccountId` — Clay-managed (`YOUR_CLAY_GEMINI_AUTH_ACCOUNT_ID` for Gemini, `YOUR_CLAY_OPENAI_AUTH_ACCOUNT_ID` for OpenAI) or custom (`YOUR_AUTH_ACCOUNT_ID` for your-custom-key)

**Output formats:**
- **Fields** — typed output fields (Number, Text, etc). Claygent default for single-value lookups.
- **JSON Schema** — structured JSON via `answerSchemaType` + `formulaMap`. Better for multi-field outputs.

### Creating a Use AI Column (Step-by-Step)

**1. Create the column** — single POST with all config:

```python
import json

body = {
    "type": "action",
    "name": "My AI Column",
    "viewId": VIEW_ID,
    "typeSettings": {
        "actionKey": "use-ai",                                          # ALWAYS "use-ai", never "ai"
        "actionPackageId": "67ba01e9-1898-4e7d-afe7-7ebe24819a57",     # same for all AI columns
        "actionVersion": 1,
        "authAccountId": "YOUR_AUTH_ACCOUNT_ID",                     # required — pick from Known Auth Accounts table
        "dataTypeSettings": {"type": "text"},                            # ⚠ MUST be "text", NOT "json" — "json" works via API but breaks Clay UI ("Could not find properties for data type json")
        "inputsBinding": [
            {"name": "useCase",      "formulaText": '"claygent"'},      # or "use-ai" for Create Content
            {"name": "model",        "formulaText": '"gpt-4.1-nano"'},  # see model table above
            {"name": "prompt",       "formulaText": '"Do X from " + {{f_input_field}}'},
            # For structured output — add these two (REQUIRED for ?.key extractors to work):
            {"name": "answerSchemaType", "formulaMap": {
                "type": '"json"',
                "jsonType": '"JSONSchema"',
                "jsonSchema": json.dumps(json.dumps({                   # double-encoded!
                    "type": "object",
                    "properties": {
                        "field_1": {"type": "string"},
                        "field_2": {"type": "number"},
                    },
                    "required": ["field_1", "field_2"]
                }))
            }},
            {"name": "_metadata", "formulaMap": {"modelSource": '"user"'}},
        ]
    }
}
r = clay.session.post(f"{BASE}/tables/{TABLE_ID}/fields", json=body)
ai_field_id = r.json()["field"]["id"]
```

**2. Add extractor columns** — one formula per output field:

```python
clay.create_formula_column(TABLE_ID, "Field 1", f"{{{{{ai_field_id}}}}}?.field_1", view_id=VIEW_ID, data_type="text")
clay.create_formula_column(TABLE_ID, "Field 2", f"{{{{{ai_field_id}}}}}?.field_2", view_id=VIEW_ID, data_type="number")
```

**3. Run:**

```python
clay.run_column(TABLE_ID, [ai_field_id], record_ids=RECORD_IDS)
```

**Checklist (common mistakes):**
- `actionKey` must be `"use-ai"` — `"ai"` silently drops all inputs
- `authAccountId` is required — without it the column never runs
- `answerSchemaType` uses `formulaMap` not `formulaText` — `formulaText` silently fails
- `jsonSchema` value is **double JSON-encoded**: `json.dumps(json.dumps(schema))` — single encoding produces a dict where Clay expects a string; column creates OK but never runs
- `_metadata` with `modelSource: '"user"'` (inner quotes!) is required when using `answerSchemaType`
- `dataTypeSettings` must be `{"type": "text"}` — `{"type": "json"}` breaks Clay UI rendering

**API-triggered `use-ai` runs never executed (verified failing 2026-07-30; mechanism
hypothesis unconfirmed).** A `use-ai` column (useCase `"claygent"`, `gpt-5-nano`,
`dataTypeSettings: json`, **no `authAccountId`** — cloned from a working UI-built column
that also shows `authAccountId: null`) ACK'd every `run_column` call
(`{"runMode": "INDIVIDUAL"}`, incl. `force_run=True`) but **never executed**: 0 credits
moved, cell stayed blank; with caller_name variants (`table-page`/`table`/`clay-web`) the
cell recorded `{"metadata": {"trigger": "FORCE-RUN"}}` and sat queued 4+ minutes without
running. A `lookup-row-in-other-table` column ran fine via the identical call in the same
minutes (so NOT the global write-restricted-cookie mode above). Candidate explanations:
(a) AI-action execution is gated to UI-originated runs (same family as the 2026-07-23
run-enrichment allowlist shrink); (b) the missing/`null` `authAccountId` blocks API runs
while UI runs resolve a default account. Until resolved: **run AI columns from the Clay
UI Run button**, and treat a blank-cell-after-ACK on an AI column as expected, not a bug
in your code.
- `answerSchemaType` + `_metadata` are REQUIRED for `?.key` extractors to work — without them, Clay shows "Unable to parse output schema" even if the column was created successfully
- `systemPrompt` must be < ~1,000 chars — put long instructions in `prompt` instead
- For Claygent: expect 1-2 min per record (web research). For Create Content: seconds.

---

## CRITICAL: Formula Reference Rules

**Always use field IDs, never column names.**

```python
# ✅ CORRECT — field ID reference
"{{f_xxx}}"

# ❌ WRONG — Clay formula parser ignores name references
"{{Company URL}}"
```

Get field IDs from the table:
```python
raw = clay.get_table(table_id)
fields = raw["table"].get("fields", [])
field_map = {f["name"]: f["id"] for f in fields}

# Build ref helper
def ref(name): return "{{" + field_map[name] + "}}"
```

---

## CRITICAL: `inputsBinding` Rules

### Action columns need the action's FULL parameter list, or the UI shows NO inputs

**Verified 2026-07-24.** Clay's UI renders an action column's input rows from the
`inputsBinding` array itself — not from the action's schema. A column created with only the
parameters you actually use is *functionally correct* (it runs, it returns data) but opens in
the UI with **no inputs visible at all**, so nobody can see or edit its configuration.

A UI-created `http-api-v2` column carries **all 15 entries**, unset ones present but empty:

```json
{"inputsBinding": [
  {"name": "method"},                                  // set:   {"name":"method","formulaText":"\"POST\""}
  {"name": "url", "formulaText": "\"https://...\""},
  {"name": "queryString"}, {"name": "body"}, {"name": "headers"}, {"name": "fieldPaths"},
  {"name": "removeNull", "formulaText": "true"},
  {"name": "returnResponseMetadata"},
  {"name": "followRedirects", "formulaText": "true"},
  {"name": "followRedirectsOptions|maxRedirects"},      // pipe-nested children are listed too
  {"name": "responseTimeout"},
  {"name": "shouldRetry", "formulaText": "true"},
  {"name": "retryOptions|maxRetries"},
  {"name": "retryOptions|statusCodesToRetry"},
  {"name": "retryOptions|errorCodesToRetry"}
]}
```

Get the authoritative list per action from `clay workflows actions schema <packageId> <actionKey>`
(returns `inputParameters`), then pass **every** name to `create_action_column` — unset ones as
`None`, which the wrapper emits as bare `{"name": …}` entries:

```python
clay.create_action_column(t_id, "My HTTP Call",
    action_key="http-api-v2", package_id="4299091f-3cd3-4d68-b198-0143575f471d",
    inputs={"method": '"POST"', "url": '"https://example.com/hook"',
            "headers": {"Content-Type": '"application/json"'}, "body": {"k": "{{f_xxx}}"},
            # everything else present-but-unset so the UI renders the full form:
            "queryString": None, "fieldPaths": None, "removeNull": "true",
            "returnResponseMetadata": None, "followRedirects": "true",
            "followRedirectsOptions|maxRedirects": None, "responseTimeout": None,
            "shouldRetry": "true", "retryOptions|maxRetries": None,
            "retryOptions|statusCodesToRetry": None, "retryOptions|errorCodesToRetry": None},
    data_type="json", view_id=v_id)
```

Symptom to recognize: the column works when run programmatically, but a user opening it in Clay
reports "this column has no inputs". Applies to any action column, not just `http-api-v2`.


### 1. `authAccountId` goes top-level, NOT in `inputsBinding`

```python
# ✅ CORRECT
typeSettings = {
    "actionKey": "use-ai",
    "actionPackageId": "67ba01e9-1898-4e7d-afe7-7ebe24819a57",
    "authAccountId": "YOUR_GEMINI_AUTH_ACCOUNT_ID",   # ← top level
    "inputsBinding": [
        {"name": "useCase", "formulaText": '"use-ai"'},
        ...
    ]
}

# ❌ WRONG — authAccountId in inputsBinding silently fails, Gemini won't connect
"inputsBinding": [
    {"name": "authAccountId", "value": "aa_..."},  # ← wrong place
    ...
]
```

### 2. ALL inputs MUST use `"formulaText"` — `"value"` is silently dropped

```python
# ✅ CORRECT — all inputs use formulaText
{"name": "systemPrompt", "formulaText": '"You are a qualification specialist..."'}  # static string in quotes
{"name": "model",        "formulaText": '"gemini-2.5-flash"'}
{"name": "useCase",      "formulaText": '"use-ai"'}
{"name": "prompt",       "formulaText": '"Company: " + ' + ref("Name") + ' + "\\n"'}

# ❌ WRONG — "value" key is SILENTLY DROPPED, field reads back as empty
{"name": "systemPrompt", "value": "You are a qualification specialist..."}
```

**Rule:** Always use `"formulaText"`. Static strings must be wrapped in `"outer quotes"` so Clay treats them as string literals.

### 3. `systemPrompt` must be SHORT (< ~1,000 chars)

Long text in `formulaText` breaks Clay's formula parser:
- Markdown characters (`**`, `#`, backticks) cause "Invalid formula"
- Strings > ~2,000 chars fail at parse time

```python
# ❌ Too long — causes "Invalid formula" in Clay UI
{"name": "systemPrompt", "formulaText": json.dumps(long_760_line_prompt)}

# ✅ Works — short, clean string literal (no markdown, under ~1,000 chars)
{"name": "systemPrompt", "formulaText": (
    '"You qualify companies for YourClient. Return ONLY valid JSON.\\n\\n'
    'T1: NYC startup 50-500 employees, Series A+\\n'
    'T2: US-based, similar profile\\n'
    'DISQUALIFY: non-US, <20 or >2000 employees\\n\\n'
    'Return ONLY valid JSON."'
)}
```

Keep `systemPrompt` to ~500-1,000 chars max. Put the full instructions in `prompt` if needed.

---

## Column Definitions by Type

### Source column (Webhook)
```python
{
    "type": "source",
    "name": "Webhook",
    "typeSettings": {"sourceType": "webhook", "sourceIds": []}
}
```

Creating this source FIELD is what binds a webhook source to a table: with `typeSettings: {"sourceIds": [s_id], "canCreateRecords": true}` it registers the `sourceSubscriptions` entry. A webhook source WITHOUT one buffers POSTs (`state.numSourceRecords` increments) but never creates rows; adding the field retroactively materializes the buffered records. Verified 2026-07-24 — see "Webhook Source Tables" below.

### Text / Number / Basic column
```python
{"type": "text", "name": "Company Name"}
{"type": "number", "name": "Employee Count"}
```

### Formula column (extracts from another column)
```python
# Extract a JSON field from an action column result
{
    "type": "formula",
    "name": "Tier",
    "typeSettings": {
        "formulaText": "{{f_qualification_col_id}}?.tier",
        "dataTypeSettings": {"type": "text"}
    }
}

# The ?.key pattern safely accesses object properties (returns null if missing)
# Works for: strings, numbers, nested objects, arrays
```

### Enrich Company (Mixrank v2)
```python
{
    "type": "action",
    "name": "Enrich Company",
    "typeSettings": {
        "actionKey": "enrich-company-with-mixrank-v2",
        "actionPackageId": "e251a70e-46d7-4f3a-b3ef-a211ad3d8bd2",
        "dataTypeSettings": {"type": "text"},
        "inputsBinding": [
            # ⚠️ Input name is "company_identifier", NOT "url"
            {"name": "company_identifier", "formulaText": ref("Company URL")}
        ]
    }
}
```

**Mixrank v2 confirmed output keys** (validated live March 2026, Spacelift test):

| Formula | Value returned | Notes |
|---------|---------------|-------|
| `?.name` | `"Spacelift"` | Company name |
| `?.url` | `"https://www.linkedin.com/company/spacelift-io"` | ⚠️ LinkedIn URL, NOT website |
| `?.website` | `"https://spacelift.io"` | Actual website URL |
| `?.description` | `"Spacelift is an infrastructure..."` | Full company description |
| `?.employee_count` | `141` | Headcount (number) |
| `?.industry` | `"Software Development"` | Industry string |
| `?.country` | `"US"` | Country code |
| `?.founded` | `"2020"` | Founded year |
| `?.org_id` | _(string)_ | Internal Mixrank org ID |

**NOT available from Mixrank v2:** `domain`, `city`, `funding_stage`, `linkedin_url`, `short_description`

```python
# Correct extractor formulas:
"{{f_enrich_col_id}}?.name"           # company name
"{{f_enrich_col_id}}?.website"        # ✅ website URL (NOT ?.url)
"{{f_enrich_col_id}}?.url"            # ✅ LinkedIn company URL (NOT website)
"{{f_enrich_col_id}}?.description"    # full description
"{{f_enrich_col_id}}?.employee_count" # headcount (number)
"{{f_enrich_col_id}}?.industry"       # industry string
"{{f_enrich_col_id}}?.country"        # country code
```

### Enrich Person
```python
{
    "type": "action",
    "name": "Enrich Person",
    "typeSettings": {
        "actionKey": "enrich-person",   # find via search_enrichments("enrich person")
        "actionPackageId": "<pkg_id>",  # from search_enrichments result
        "dataTypeSettings": {"type": "text"},
        "inputsBinding": [
            # ⚠️ Input name is "person_identifier" — NOT linkedin_url, url, profile_url
            {"name": "person_identifier", "formulaText": ref(f_linkedin_url)},
            {"name": "email"}   # include empty email binding
        ]
    }
}
```

**Person enrichment output keys** (top-level, accessible via `?.key`):
| Formula | Returns |
|---------|---------|
| `?.title` | Job title |
| `?.org` | Current company name |
| `?.location_name` | Location string |
| `?.headline` | LinkedIn headline |
| `?.url` | LinkedIn profile URL |
| `?.num_followers` | Follower count |
| `?.connections` | Connection count |

**Nested data (e.g. experience) requires `mappedResultPath`** — see below.

### Enrich Company — Input Gotcha

The Enrich Company (Mixrank) input `company_identifier` works best with a **LinkedIn company URL** (e.g., `https://www.linkedin.com/company/spacelift-io`). Company names like "Stealth", "Cuez" fail with `ERROR_INVALID_INPUT`. Extract the company LinkedIn URL from person enrichment's `experience[0].url` using `mappedResultPath`.

### mappedResultPath — Extracting Nested Enrichment Data

Formula columns using `?.key` can only access **top-level** enrichment keys. For nested paths (e.g., `experience > 0 > url`), you MUST use `mappedResultPath`:

```python
# Extract company LinkedIn URL from person enrichment (nested: experience[0].url)
f_co_url = clay.create_column(table_id, {"type": "text", "name": "Company LI URL"})["id"]
clay.session.patch(
    f"https://api.clay.com/v3/tables/{table_id}/fields/{f_co_url}",
    json={"typeSettings": {
        "dataTypeSettings": {"type": "url"},
        "formulaType": "text",
        "formulaText": ref(f_enrich_person) + "?.experience?.[0]?.url",
        "mappedResultPath": ["experience", "0", "url"],   # ← REQUIRED for nested paths
    },
    "attributionData": {"created_from": "object_mapper"}}
)
# Without mappedResultPath, the same formula returns empty
```

Use `mappedResultPath` columns as inputs to downstream action columns (e.g., Enrich Company).

### Use AI column
```python
{
    "type": "action",
    "name": "Qualification",
    "typeSettings": {
        "actionKey": "use-ai",
        "actionPackageId": "67ba01e9-1898-4e7d-afe7-7ebe24819a57",
        "dataTypeSettings": {"type": "text"},
        "authAccountId": "YOUR_GEMINI_AUTH_ACCOUNT_ID",   # ← top level, not in inputsBinding
        "inputsBinding": [
            {"name": "useCase",      "formulaText": '"use-ai"'},
            {"name": "model",        "formulaText": '"gemini-2.5-flash"'},
            # ⚠️ "value" key is SILENTLY DROPPED — always use "formulaText"
            # Keep systemPrompt short (< ~1,000 chars), no markdown
            {"name": "systemPrompt", "formulaText": '"You qualify companies for YourClient. Return ONLY valid JSON."'},
            {"name": "prompt",       "formulaText": (
                '"Company: " + ' + ref("Name") + ' + "\\n" + '
                '"Domain: " + ' + ref("Domain") + ' + "\\n" + '
                '"Return JSON with keys: tier, score, status"'
            )},
        ]
    }
}
```

The `prompt` formulaText is a Clay formula expression (JS-like):
- String literals: `"text"` (double-quoted)
- Concatenation: `"text" + {{f_id}} + "more text"`
- Newlines in strings: `"line1\\nline2"` (double-escaped in Python → `\n` in formula)
- Null-safe access: `{{f_id}}?.property`
- Type conversion: `String({{f_number_id}})`

### Use AI with structured JSON output (answerSchemaType)

**When you need formula extractors (`?.key`) to work, use `answerSchemaType` with Grok.**
Without it, both Gemini and GPT wrap JSON in code fences which breaks `?.key` accessors.

```python
import json

# Define your output schema
schema = json.dumps({
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["QUALIFY", "DISQUALIFY"]},
        "reason": {"type": "string"},
        "signals": {"type": "string"}
    },
    "required": ["decision", "reason", "signals"],
    "additionalProperties": False
})

{
    "type": "action",
    "name": "AI Qualification",
    "typeSettings": {
        "actionKey": "use-ai",
        "actionPackageId": "67ba01e9-1898-4e7d-afe7-7ebe24819a57",
        "dataTypeSettings": {"type": "text"},
        "authAccountId": "YOUR_XAI_AUTH_ACCOUNT_ID",  # Grok xAI
        "inputsBinding": [
            {"name": "useCase",      "formulaText": '"use-ai"'},
            {"name": "model",        "formulaText": '"grok-4-1-fast-reasoning"'},
            {"name": "systemPrompt", "formulaText": '"You are a qualification specialist. Return JSON only."'},
            {"name": "prompt",       "formulaText": '"Company: " + {{f_name}} + "\\nReturn JSON"'},
            # ✅ CRITICAL: answerSchemaType uses formulaMap, NOT formulaText
            {"name": "answerSchemaType", "formulaMap": {
                "type": '"json"',
                "jsonType": '"JSONSchema"',
                "jsonSchema": json.dumps(schema)  # double-encoded string
            }},
            # ✅ Required metadata for user-provided model
            {"name": "_metadata", "formulaMap": {"modelSource": '"user"'}},
        ]
    }
}
```

The `answerSchemaType` input enforces structured JSON output — the AI returns a parsed JSON object, not a string. Formula extractors (`?.decision`, `?.reason`) work correctly.

Ask for JSON output by including it in the prompt text:
```
"Return valid JSON with keys: tier (string), score (number 0-100)"
```

Extract results with formula columns:
```python
"{{f_ai_col_id}}?.tier"    # string field
"{{f_ai_col_id}}?.score"   # number field
```

### HTTP API v2 column (e.g. RapidAPI GET)

**`body` is `longtext`, not an object — the UI binds it as `formulaText` + `Clay.formatForJSON()`.**
Verified 2026-07-24 by inspecting a UI-created column. `queryString` and `headers` are object-typed
(hence the `formulaMap` rule below), but `body` per `clay workflows actions schema` is **longtext**, and
Clay's own UI writes it as a concatenated JSON *string* with each interpolated value wrapped in the
`Clay.formatForJSON()` formula helper (escapes quotes/newlines/control chars):

```javascript
"{\n  \"company\": \"" + Clay.formatForJSON({{f_domain}}) + "\",\n  \"record_id\": \""
  + Clay.formatForJSON({{f_name}}) + "\"\n}"
```

claycast ships **`format_json_body({...})`** (module-level in `clay_client.py`) which emits exactly this form —
field refs get `Clay.formatForJSON()`, bools/numbers stay bare, everything else becomes a JSON string literal.
Verified byte-identical to a UI-built column.

A `formulaMap` body is *accepted* by the API and does send valid JSON (used in production here), but it is
NOT the UI's canonical form — prefer `formulaText` + `Clay.formatForJSON()` so a column round-trips
identically whether a human or claycast built it, and so values containing quotes/newlines can't break the
payload.

**CRITICAL: bind the action's FULL parameter list — see "Action columns need the action's FULL parameter list" below; a partial binding renders NO inputs in the Clay UI.**

**CRITICAL: `queryString` and `headers` use `formulaMap`, NOT `formulaText`.**
Using `formulaText` with a JSON object `{"key": val}` causes Clay to split the string character-by-character into numbered rows — completely broken. Do not check the cell preview (`"Status Code: 200"`) to verify; that can lie when the target server returns 200 regardless. Inspect `externalContent.fullValue` via `GET /tables/{t}/records/{r}` to see the actual URL/payload Clay sent.

Verified 2026-04-23: `formulaText` with `'{"q": hello}'` produced URL `https://httpbin.org/get?0={&1="&2=q&3="&4=%3A&5= &6=h&7=e&8=l&9=l&10=o&11=}` — one query param per input char. `formulaMap` with `{"q": "hello"}` produced the correct `?q=hello`.

```python
# ✅ BEST — use auth account (YOUR_RAPIDAPI_AUTH_ACCOUNT_ID) — no hardcoded keys
{
    "type": "action",
    "name": "Step 4a | Company Profile | RapidAPI",
    "typeSettings": {
        "actionKey": "http-api-v2",
        "actionPackageId": "4299091f-3cd3-4d68-b198-0143575f471d",
        "authAccountId": "YOUR_RAPIDAPI_AUTH_ACCOUNT_ID",   # ← injects X-RapidAPI-Key + Host automatically
        "dataTypeSettings": {"type": "text"},
        "inputsBinding": [
            {"name": "method", "formulaText": '"GET"'},
            {"name": "url",    "formulaText": '"https://fresh-linkedin-scraper-api.p.rapidapi.com/api/v1/company/profile"'},
            # ✅ queryString as formulaMap — each key maps to a formula
            {"name": "queryString", "formulaMap": {
                "company": "{{f_name_field_id}}"
            }},
            # No headers needed — auth account injects them
            {"name": "removeNull",      "formulaText": "true"},
            {"name": "followRedirects", "formulaText": "true"},
            {"name": "shouldRetry",     "formulaText": "true"},
        ]
    }
}

# Step 4b — chain: extract id from Step 4a response
{"name": "queryString", "formulaMap": {
    "company_id": "String({{f_step4a_id}}?.data?.id)"
}}

# ❌ WRONG — formulaText with JSON object: Clay splits chars into numbered rows
# (verified 2026-04-23: `{"q": hello}` became ?0={&1="&2=q&3="&4=:&5= &6=h...).
# The cell PREVIEW will still say "Status Code: 200" if the target accepts any GET;
# check externalContent.fullValue to see the actual URL sent.
{"name": "queryString", "formulaText": '{"company": {{f_name}}}'}
{"name": "headers",     "formulaText": '{"X-RapidAPI-Key": "..."}'}

# ✅ CORRECT — formulaMap for key-value inputs (if not using auth account)
{"name": "queryString", "formulaMap": {"company": "{{f_name}}"}}
{"name": "headers",     "formulaMap": {"X-RapidAPI-Key": '"your-key"'}}
```

> **Note on bulk-fetch-records API:** For `http-api-v2` action columns, `clay.get_records()` returns `value: "Status Code: 200"` (a display summary). The full JSON response body is stored internally and IS accessible to Clay's formula engine — downstream columns referencing `{{f_http_col}}?.data?.id` work correctly despite the API showing only the status string.

### Lookup Multiple Rows in Other Table

**CRITICAL: Input names use `fields|` prefix for filter parameters.**

```python
{
    "type": "action",
    "name": "People at Company",
    "typeSettings": {
        "actionKey": "lookup-multiple-rows-in-other-table",
        "actionPackageId": "4299091f-3cd3-4d68-b198-0143575f471d",
        "actionVersion": 1,
        "dataTypeSettings": {"type": "text"},
        "inputsBinding": [
            {"name": "tableId",               "formulaText": '"t_target_table_id"'},
            {"name": "fields|targetColumn",    "formulaText": '"f_field_in_target_table"'},
            {"name": "fields|filterOperator",  "formulaText": '"EQUAL"'},
            {"name": "fields|rowValue",        "formulaText": "{{f_field_in_current_table}}"},
            # Optional:
            # {"name": "fields|limit",         "formulaText": "20"},
        ]
    }
}
```

**Input names mapping:**

| UI Label | Input Name | Value |
|----------|-----------|-------|
| Table to Search | `tableId` | `"t_xxx"` (string literal) |
| Target Column | `fields\|targetColumn` | `"f_xxx"` (field ID in target table, string literal) |
| Filter Operator | `fields\|filterOperator` | `"EQUAL"`, `"CONTAINS"`, etc. |
| Row Value | `fields\|rowValue` | `{{f_xxx}}` (formula ref from current table) |
| Limit | `fields\|limit` | number |

**Lookup Single Row** (`lookup-row-in-other-table`) uses the same pattern — same package ID, same `fields|` prefix inputs.

**Response:** `value` is a display string like `"✅ 3 Records Found"` — for single-row lookup just `"✅ Record Found"` (`metadata.isPreview`); the real payload is formula-visible only, shaped `{"record": {"<Column Name>": value, ...}}` keyed by COLUMN NAMES. Bracket-key access works (`{{f_lookup}}?.record?.["Target Column Name"]`); `mappedResultPath` on a formula PATCH does NOT take effect — extract in `formulaText` itself. 0 credits per lookup execution. Verified 2026-07-24.

### Instantly: Add Lead to Campaign

```python
{
    "type": "action",
    "name": "Add to Instantly",
    "typeSettings": {
        "actionKey": "instantly-v2-add-lead-to-campaign",
        "actionPackageId": "70cda03a-a576-4a6c-b3b3-55e241f828b5",
        "authAccountId": "YOUR_INSTANTLY_AUTH_ACCOUNT_ID",  # your-instantly
        "dataTypeSettings": {"type": "text"},
        "inputsBinding": [
            {"name": "email",        "formulaText": "{{f_email_field}}"},
            {"name": "first_name",   "formulaText": "{{f_first_name_field}}"},
            {"name": "last_name",    "formulaText": "{{f_last_name_field}}"},
            {"name": "company_name", "formulaText": "{{f_company_field}}"},
            {"name": "campaign",     "formulaText": '"campaign-uuid-here"'},
        ]
    }
}
```

**Campaign IDs** are fetched dynamically via `POST /actions/dynamicFields` with `parameterPath: "campaign"`.

### HTTP API v2 column (POST with JSON body, e.g. HubSpot)
```python
{
    "type": "action",
    "name": "Check Company in HS",
    "typeSettings": {
        "actionKey": "http-api-v2",
        "actionPackageId": "4299091f-3cd3-4d68-b198-0143575f471d",
        "dataTypeSettings": {"type": "text"},
        "authAccountId": "YOUR_HUBSPOT_AUTH_ACCOUNT_ID",
        "inputsBinding": [
            {"name": "method", "formulaText": '"POST"'},
            {"name": "url",    "formulaText": '"https://api.hubapi.com/crm/v3/objects/companies/search"'},
            {"name": "body",   "formulaText": (
                '\'{"filterGroups":[{"filters":[{"propertyName":"domain","operator":"EQ",'
                '"value":"\' + ' + ref("Domain") + ' + \'"}]}]}\''
            )},
            {"name": "headers", "formulaMap": {
                "Authorization":  '"Bearer " + Clay.secret("hubspot_token")',
                "Content-Type":   '"application/json"',
            }},
        ],
    }
}
```

---

## Export

### Decision rule — which method to call

claycast has three export methods that hit different paths and produce different output. Pick based on what you actually need.

| Need | Use | Why |
|---|---|---|
| CSV that matches what Clay's UI / email export gives | `export_csv(table_id, view_id=)` | Wraps Clay's native server-side export job. Returns an S3 URL valid 24h. **Action ("Response") columns will export as the literal string `"Response"`** — no enrichment JSON. Use when you don't have action columns OR you don't need their data. |
| CSV (or JSON) with **full enrichment data** including action-column responses | **`export_rows(table_id, view_id=, format="csv"|"json")`** | claycast-side export. Fetches via `bulk-fetch-records` then writes locally to `<project_root>/tmp/clay-artifacts/`. Returns `{payload, artifact_path}`. Strictly more complete than `export_csv` for tables with enrichments. |
| All rows of a huge table efficiently, with full per-record data | `fetch_all_records_full(table_id, view_id, field_id, workers=20)` | Parallel fetch via the per-record endpoint `/tables/{t}/records/{r}` (which `bulk-fetch-records` skips). Highest fidelity — includes `externalContent.fullValue` from action columns. ~27ms/record with 20 workers; 556 rows in ~15s, 10k rows in ~4.5 min. |
| Markdown documentation of a table's structure | `document_table(table_id, ...)` | Schema doc, not row data. Different output category. |

**Quickest mental model:**
- `export_csv` = Clay's UI export (incomplete for action columns)
- `export_rows` = local CSV/JSON with everything `bulk-fetch-records` returns
- `fetch_all_records_full` = local data with everything `/records/{r}` returns (most complete; only path that surfaces `externalContent.fullValue`)

If you care about action columns at all, do NOT use `export_csv`.

### How CSV export works

Export is **fully server-side** — no scrolling, no pagination. Two variants:

```python
# With view (respects filters)
POST /v3/tables/{TABLE_ID}/views/{VIEW_ID}/export

# All rows (ignores all filters)
POST /v3/tables/{TABLE_ID}/export
```

Poll until done, then download from a signed S3 URL (valid 24h):

```python
GET /v3/exports/{job_id}
# → {"status": "FINISHED", "downloadUrl": "https://s3.amazonaws.com/...", "recordsExportedCount": 556}
```

Client method: `clay.export_csv(table_id, view_id=None)` — returns the download URL. Completes in ~1 second for 556 rows.

### Action column values in CSV export

**Problem:** Action ("Response") columns always export as the literal string `"Response"` in the native CSV. The full enrichment JSON is intentionally omitted.

**Two solutions:**

**Option A — Formula columns (no code, recommended):**
Add a formula column in Clay UI with `JSON.stringify({{f_action_field_id}})`. This gets included in the native CSV export with the full JSON. Best when you control the table.

**Option B — Parallel API fetch:**
```python
# GET /v3/tables/{TABLE_ID}/records/{record_id}
# → cells[field_id].externalContent.fullValue = full JSON

results = clay.fetch_all_records_full(table_id, view_id, field_id, workers=20)
# [{record_id, value, status}, ...]
# ~27ms/record with 20 workers → 556 rows in ~15s, 10k rows in ~4.5 min
```

Note: `bulk-fetch-records` does NOT return `externalContent` — you must hit the single-record endpoint `/tables/{TABLE_ID}/records/{record_id}` individually.

### Key export endpoints

```
# Start export
POST https://api.clay.com/v3/tables/{TABLE_ID}/views/{VIEW_ID}/export  # filtered view
POST https://api.clay.com/v3/tables/{TABLE_ID}/export                  # all rows

# Poll job
GET  https://api.clay.com/v3/exports/{job_id}

# Single record with full action data
GET  https://api.clay.com/v3/tables/{TABLE_ID}/records/{record_id}
     → cells[field_id].externalContent.fullValue

# All record IDs (no pagination)
GET  https://api.clay.com/v3/tables/{TABLE_ID}/views/{VIEW_ID}/records/ids
```

---

## Records

All record writes go through **two endpoints** that behave asynchronously — Clay returns `200 OK` with `{"records": [], "extraData": {"message": "Record updates enqueued"}}` and the values land shortly after. The SDK (`clay_client.ClayClient`) handles the async + verification automatically; code that hits raw HTTP must follow the patterns below.

### Create records — use the SDK helper

```python
# SDK (handles blank-POST + PATCH + post-PATCH verification poll):
recs = clay.create_records(t_id, [
    {"Name": "Alice", "Domain": "a.com"},
    {"Name": "Bob",   "Domain": "b.com"},
], field_names=True)
# OR field-id keyed (default):
recs = clay.create_records(t_id, [{name_col: "Alice", dom_col: "a.com"}])
# Returns: list[dict] of created records with cells populated (re-fetched after PATCH).

# With pre-generated ids (for upsert patterns):
pregen = [_gen_record_id(), _gen_record_id()]
recs = clay.create_records(t_id, [...], record_ids=pregen)
```

**FRESH-TABLE silent drop (verified 2026-07-23):** on a freshly created table, the name-keyed path (`field_names=True`) can silently fail — the blank-POST + name→field-id mapped PATCH returns 200 but the values NEVER land (rows stay blank; `create_records` raises its 5s `"values did not persist"` RuntimeError). The IDENTICAL PATCH keyed by raw field ids commits fine (`update_record` / `bulk_update_records` with `{fid: value}`). `preflight()` reports `write: True` — this is NOT the write-restricted-cookie mode. Workaround: create blank rows, then fid-keyed `bulk_update_records`, then re-fetch to verify.

### Create records — raw HTTP (for debugging only)

**CRITICAL: `POST /tables/{t}/records` with populated cells silently drops the values.** The endpoint returns 200 and echoes record IDs back with only `f_created_at` / `f_updated_at` system cells — your user-cell values disappear. Clay's own UI uses a two-step pattern:

```python
# Step 1: POST blank rows with pre-generated IDs
clay.session.post(
    f"https://api.clay.com/v3/tables/{table_id}/records",
    json={"records": [{"id": rid, "cells": {}} for rid in pregen_ids]},
)
# → {"records": [{"id": ..., "cells": {f_created_at: ..., f_updated_at: ...}}, ...]}

# Step 2: PATCH with values (async enqueue)
clay.session.patch(
    f"https://api.clay.com/v3/tables/{table_id}/records",
    json={"records": [{"id": rid, "cells": {fid: value, ...}} for rid in pregen_ids]},
)
# → {"records": [], "extraData": {"message": "Record updates enqueued"}}

# Step 3: poll get_records until values are visible
for _ in range(10):
    time.sleep(0.5)
    recs = clay.get_records(table_id, pregen_ids)
    if all(r.get("cells", {}).get(fid, {}).get("value") is not None for r in recs):
        break
```

Clay **auto-adds one blank row** when a table is created. Count endpoints include it; your `N` inserts → `N+1` rows in `count_records`.

### Read records — four endpoints

| Endpoint | Method | Returns | When to use |
|---|---|---|---|
| `GET /tables/{t}/records/{r}` | single | full record dict, incl. `externalContent.fullValue` for action cells | single-record fetch; best for cell-shape reconciliation (`get_record`) |
| `POST /tables/{t}/bulk-fetch-records` | bulk | `{results: [...]}` | N specific IDs at once (`get_records`) |
| `GET /tables/{t}/views/{v}/records/ids` | list-ids | `{results: [<id>, ...]}` | first half of the 2-step flow (`get_record_ids`) |
| `GET /tables/{t}/views/{v}/records?limit=N` | direct-list | `{results: [{id, cells, ...}]}` | single-call limited read; cells in same shape as bulk-fetch (`list_records(limit=N, strategy="direct")`) |

```python
# Raw single-record (includes externalContent.fullValue for action cells):
rec = clay.get(f"/tables/{table_id}/records/{record_id}")

# Bulk fetch:
recs = clay.get_records(table_id, [r1, r2, r3])

# 2-step list:
ids = clay.get_record_ids(table_id, view_id)
recs = clay.get_records(table_id, ids)

# Direct single-call with limit (strategy="auto" uses this when limit is set
# AND field_ids is omitted):
recs = clay.list_records(table_id, view_id, limit=25)
```

### Update records — bulk PATCH only

The **single-record** `PATCH /tables/{t}/records/{r}` endpoint exists but behaves the same as the bulk endpoint (enqueue-async) and is less consistent. ClayCast's `update_record` wraps a single record into the **bulk** call:

```python
# SDK:
clay.update_record(table_id, record_id, {field_id: new_value})        # single-row convenience
clay.bulk_update_records(table_id, [                                   # many rows
    {"record_id": rid, "cells": {fid: value}},
    ...
])
clay.bulk_update_records(table_id, [                                   # name-keyed mode
    {"_record_id": rid, "Name": "Alice UPDATED"},
    ...
], field_names=True)

# Raw HTTP:
clay.session.patch(
    f"https://api.clay.com/v3/tables/{table_id}/records",
    json={"records": [{"id": rid, "cells": {fid: value}}, ...]},
)
# → {"records": [], "extraData": {"message": "Record updates enqueued"}}
```

Body key is **`"id"`**, not `"recordId"`. The writer-docs convention of `recordId` is wrong for this endpoint.

### Delete records

```python
clay.delete_records(table_id, [r1, r2, r3])          # chunked at batch_size=100, max 500
# Raw: DELETE /tables/{t}/records  body: {"recordIds": [...]}
```

### Count records

```python
clay.count_records(table_id)   # int; includes the auto-blank row on new tables
# Raw: GET /tables/{t}/count  → {"tableTotalRecordsCount": N}
```

### Upsert (SDK only — recipe, not a single Clay endpoint)

`clay.upsert_records(table_id, records_by_name, match_field_name, ...)` fetches all rows in the view, builds a `{match_value → record_id}` index, partitions incoming rows into update vs create, and dispatches via `bulk_update_records` + `create_records` (pre-generated IDs). O(N) scan over the view — pass `max_scan_rows=<N>` / `confirm_large_scan=True` for guardrails. Existing-row duplicates: last-seen-wins. Incoming-payload duplicates: claycast dedupes (last-occurrence wins), deterministically.

### Cell payload shapes

Different endpoints populate cells differently. `extract_cell_value(cell)` (module-level helper) handles all observed shapes:

| Column type | Shape | Best read via |
|---|---|---|
| Plain text | `{"value": <scalar>}` | any endpoint |
| Formula | `{"value": <scalar>, "metadata": {"status": "SUCCESS"}}` | any endpoint |
| Action / enrichment | `{"value": "<preview>", "metadata": {...}, "externalContent": {"fullValue": <json|dict>, ...}}` | `get_record` (single) reliably populates `externalContent`; bulk-fetch may show only the preview. |
| Auto-timestamps | `{"value": "2026-04-24T...", "metadata": {"isCoerced": true}}` | any |

The action-column gotcha: `cell["value"]` can say `"Status Code: 200"` while the actual HTTP response body is in `cell["externalContent"]["fullValue"]`. Always use `extract_cell_value()` or explicitly reach into `externalContent.fullValue` for action cells — don't trust the preview.

---

## Running Columns

```python
# ⚠️ runRecords is REQUIRED — omitting causes 400 error
r = clay.session.patch(
    f"https://api.clay.com/v3/tables/{table_id}/run",
    json={
        "fieldIds": [field_id_1, field_id_2],
        "callerName": "clay-client",
        "runRecords": {"recordIds": [record_id]}   # specific records
        # OR: "runRecords": {"viewId": view_id}    # all records in view
    }
)
# Returns: {"recordCount": 1, "runMode": "INDIVIDUAL"}
```

See "Running Columns — Rate Limits" section below for `ERROR_TOO_MANY_RUNS` and `isPreview` behavior.

---

## Column Operations (verified 2026-04-24 via UI HAR capture)

All endpoints below were captured from Clay's own UI driving the table view. Column order + visibility are stored lexicographically per view; Clay computes a new position string between the anchor and its neighbor on each move.

### Move / reorder (view-scoped)

| Operation | Endpoint | Body | ClayCast method |
|---|---|---|---|
| Single field move | `PATCH /v3/tables/{t}/views/{v}/fields/{fid}` | `{"afterFieldId": <fid>}` OR `{"beforeFieldId": <fid>}` | `clay.move_field(...)` |
| Bulk (group) reorder | `PATCH /v3/tables/{t}/views/{v}/reorder-fields` | `{"fieldIds": [<ordered>], "afterFieldId": <fid>}` or `{..., "beforeFieldId": <fid>}`. **Precondition:** `fieldIds` must already be adjacent in the view; otherwise Clay returns HTTP 400 `"Fields are not adjacent in the view."` | `clay.reorder_fields(...)` |

`fieldIds` order is preserved when the group lands. To set a whole view's column order to `[A, B, C, D, E]`, one call: `reorder_fields(field_ids=[B, C, D, E], after_field_id=A)`.
- The endpoint is a block-move, not an arbitrary bulk reorder. For non-contiguous sets, use per-field `move_field` calls.
- **Whole-view reorder failed live (2026-07-21):** despite the one-call trick above, passing a full view's field list to `reorder-fields` returned 400 on spreadsheet tables and 500 on people tables. Reliable fallback for imposing an entire view's order: per-field `move_field` walk left→right, skipping already-placed fields (simulate the order locally between calls — 334 moves across 8 views ran with 0 failures).

### Hide / show (view-scoped)

| Operation | Endpoint | Body | ClayCast method |
|---|---|---|---|
| Single field visibility | `PATCH /v3/tables/{t}/views/{v}/fields/{fid}` | `{"isVisible": true\|false}` | `clay.set_field_visibility(...)` |
| Bulk visibility (mixed hide+show OK) | `PATCH /v3/tables/{t}/views/{v}/fields` | `{"<fid_1>": {"isVisible": bool}, "<fid_2>": {"isVisible": bool}, ...}` (dict keyed by field id, NOT list) | `clay.set_fields_visibility(...)` |

Clay's UI exposes bulk HIDE but not bulk SHOW; the server accepts both — claycast method is bidirectional.

### Delete (table-scoped)

| Operation | Endpoint | Body | ClayCast method |
|---|---|---|---|
| Single field delete (legacy) | `DELETE /v3/tables/{t}/fields/{fid}` | — | `clay.delete_column(...)` (legacy single-id path) |
| Bulk field delete | `DELETE /v3/tables/{t}/fields` | `{"fieldIds": [<fid>, ...]}` | `clay.delete_fields(...)` — the bulk endpoint Clay's UI uses for both single and multi-delete |

Delete is table-scoped — removes the column from ALL views.

### Field groups (mixed scope)

| Operation | Endpoint | Body | ClayCast method | Scope |
|---|---|---|---|---|
| Create group | `POST /v3/tables/{t}/fields/group` | `{"fieldIds": [<fid>, ...]}` | `clay.create_field_group(...)` | Table |
| Rename / reorder / toggle output | `POST /v3/tables/{t}/fields/group/{gr_id}` | `{"name"?: str, "fields": [{"id": fid, "isOutputField"?: bool}, ...]}`. **`fields` is required on every update**; rename-only `{name}` returns HTTP 400 `Invalid request parameter(s): Field "fields" - Required`. UI patterns observed: rename sends `{name, fields}`, reorder/output-flag updates send `{fields}`. | `clay.update_field_group(...)` | Table |
| Move group | `PATCH /v3/tables/{t}/views/{v}/group/{gr_id}` | `{"groupId": <same as URL>, "afterFieldId"\|"beforeFieldId": <fid>}` | `clay.move_field_group(...)` | View |
| Ungroup (KEEP member fields) | `DELETE /v3/tables/{t}/fields/group/{gr_id}` | `{"deleteFields": false}` | `clay.ungroup(...)` | Table |
| Delete group + members | `DELETE /v3/tables/{t}/fields/group/{gr_id}` | `{"deleteFields": true}` | `clay.delete_field_group(...)` | Table |

**Group gotchas:**
- `create_field_group`'s response is `{"groupId": "gr_..."}` — NOT `id`. Clay defaults the LAST member as the output field; set `isOutputField` flags via the update call. Verified 2026-07-21.
- `update_field_group(fields=[...])` is an ATOMIC REPLACEMENT of the group's member list. Omitting a current field id removes it from the group. To safely tweak one member, fetch current membership (via `get_table(..., include_extra_data=True)` → `fieldGroupMap`) and send back the full list.
- ClayCast preserves a rename-only ergonomic: if you call `update_field_group(name="...")` with no `fields=`, it pre-fetches the current `fieldGroupMap[gr_id].groupDetails.fields` array and re-sends it so Clay's `fields` requirement is still satisfied.
- Closing the group-settings UI panel fires ZERO API calls — pure client-side state.
- `ungroup` and `delete_field_group` hit the same endpoint with different bodies; claycast keeps them as separate methods because the wrong-kwarg cost is total field loss.

### View id discovery

```python
# Get the view id for a named view on a table:
r = clay.session.get(f"https://api.clay.com/v3/tables/{table_id}")
views = r.json()["table"]["views"]
view_id = next(v["id"] for v in views if v["name"] == "Default view")
# For fresh tables, the returned `table["firstViewId"]` is the default view id.
```

---

## Table Schema Access

```python
raw = clay.get_table(table_id)
# Response structure: {"table": {...}, "extraData": {...}}
table = raw["table"]

# Key fields on table object:
view_id   = table["firstViewId"]           # needed for get_schema()
fields    = table["fields"]                # list of all column definitions
wb_id     = table["workbookId"]
settings  = table["tableSettings"]        # usually {}

# Build field map
field_map = {f["name"]: f["id"] for f in fields}

# Get full schema (includes typeSettings for all columns)
schema = clay.get_schema(table_id, view_id)
fields_dict = schema["tableSchema"]       # dict keyed by field ID
# Note: view schema omits some typeSettings detail — use table["fields"] for full data
```

---

## Creating a Table

```python
# 1. Create workbook + table (new workbook)
table = clay.create_table("My Table Name")
table_id  = table["id"]
wb_id     = table["workbookId"]

# 2. Add columns
field = clay.create_column(table_id, {
    "type": "text",
    "name": "Company URL"
})
field_id = field["id"]   # e.g. "f_xxx"

# 3. Create in existing workbook
table = clay.create_table("My Table", workbook_id="wb_xxx")
```

---

## Updating Columns

```python
import copy

# Read current state first
raw = clay.get_table(table_id)
fields = raw["table"]["fields"]
target = next(f for f in fields if f["name"] == "Qualification")
fid = target["id"]

# Make a deep copy, modify, patch
ts = copy.deepcopy(target["typeSettings"])
ts["authAccountId"] = "aa_new_account_id"
ts["inputsBinding"][0]["formulaText"] = '"new-model"'

result = clay.update_column(table_id, fid, {"typeSettings": ts})
```

Always deep-copy typeSettings before modifying. Patching replaces the full typeSettings object.

---

## Workflow: Build a Table from Scratch

```python
from clay_client import ClayClient
import copy

clay = ClayClient()

# 1. Create table
table = clay.create_table("Company Qualification")
table_id = table["id"]

# 2. Add input columns
f_url  = clay.create_column(table_id, {"type": "text", "name": "Company URL"})["id"]
f_name = clay.create_column(table_id, {"type": "text", "name": "Company Name"})["id"]

# 3. Add enrichment column
def ref(fid): return "{{" + fid + "}}"

f_enrich = clay.create_column(table_id, {
    "type": "action", "name": "Enrich Company",
    "typeSettings": {
        "actionKey": "enrich-company-with-mixrank-v2",
        "actionPackageId": "e251a70e-46d7-4f3a-b3ef-a211ad3d8bd2",
        "dataTypeSettings": {"type": "text"},
        "inputsBinding": [{"name": "company_identifier", "formulaText": ref(f_url)}]
    }
})["id"]

# 4. Add formula extractors
# ⚠️ Clay API ignores "type": "formula" on create — columns come back as "text" type.
# To set a formula, PATCH the typeSettings with formulaType + formulaText AFTER creation.
f_website = clay.create_column(table_id, {"type": "text", "name": "Website"})["id"]
clay.session.patch(
    f"https://api.clay.com/v3/tables/{table_id}/fields/{f_website}",
    json={"typeSettings": {
        "dataTypeSettings": {"type": "text"},
        "formulaType": "text",
        "formulaText": ref(f_enrich) + "?.website",  # ✅ ?.website (not ?.url which = LinkedIn URL)
        "mappedResultPath": ["website"]
    }}
)

# 5. Add AI column
# ⚠️ systemPrompt "value" is silently dropped — use "formulaText" with quoted string
f_qual = clay.create_column(table_id, {
    "type": "action", "name": "Qualification",
    "typeSettings": {
        "actionKey": "use-ai",
        "actionPackageId": "67ba01e9-1898-4e7d-afe7-7ebe24819a57",
        "dataTypeSettings": {"type": "text"},
        "authAccountId": "YOUR_GEMINI_AUTH_ACCOUNT_ID",
        "inputsBinding": [
            {"name": "useCase",      "formulaText": '"use-ai"'},
            {"name": "model",        "formulaText": '"gemini-2.5-flash"'},
            {"name": "systemPrompt", "formulaText": '"You are a B2B qualification specialist. Return JSON only."'},
            {"name": "prompt",       "formulaText": (
                '"Company: " + ' + ref(f_name) + ' + "\\n" + '
                '"Website: " + ' + ref(f_website) + ' + "\\n" + '
                '"Return JSON: {tier, score}"'
            )},
        ]
    }
})["id"]

# 6. Extract AI results — same PATCH pattern as formula extractors
f_tier = clay.create_column(table_id, {"type": "text", "name": "Tier"})["id"]
clay.session.patch(
    f"https://api.clay.com/v3/tables/{table_id}/fields/{f_tier}",
    json={"typeSettings": {
        "dataTypeSettings": {"type": "text"},
        "formulaType": "text",
        "formulaText": ref(f_qual) + "?.tier",
        "mappedResultPath": ["tier"]
    }}
)

# 7. Inject a test record
r = clay.session.post(
    f"https://api.clay.com/v3/tables/{table_id}/records",
    json={"records": [{"cells": {f_url: "https://spacelift.io", f_name: "Spacelift"}}]}
)
record_id = r.json()["records"][0]["id"]
print(f"Record created: {record_id}")
print(f"Table: https://app.clay.com/workspaces/YOUR_WORKSPACE_ID/workbooks/{table['workbookId']}/tables/{table_id}")
```

---

## Formula Columns — How They Actually Work

**Key insight:** In Clay's API, "formula column" = any column with `typeSettings.formulaText` set. The `type` field is the DATA type (`text`, `number`, `url`), not whether it's computed.

```python
# ❌ WRONG — "type": "formula" is silently ignored by the API
clay.create_column(table_id, {
    "type": "formula", "name": "Tier",
    "typeSettings": {"formulaText": "...", "dataTypeSettings": {"type": "text"}}
})
# Returns a plain text column with no formulaText

# ✅ CORRECT — create column first, then PATCH formulaText
fid = clay.create_column(table_id, {"type": "text", "name": "Tier"})["id"]
clay.session.patch(
    f"https://api.clay.com/v3/tables/{table_id}/fields/{fid}",
    json={"typeSettings": {
        "dataTypeSettings": {"type": "text"},
        "formulaType": "text",
        "formulaText": "{{f_qual_id}}?.tier",
        "mappedResultPath": ["tier"]   # optional but recommended
    }}
)
```

**`url` and `number` columns CAN accept formulaText** — `dataTypeSettings.type` can stay as `url`/`number`:
```python
# url/number typed columns accept formulaText as long as formulaType is included
clay.session.patch(
    f"https://api.clay.com/v3/tables/{table_id}/fields/{fid}",
    json={"typeSettings": {
        "dataTypeSettings": {"type": "url"},  # can stay as url/number
        "formulaType": "text",                # ← REQUIRED (the real gatekeeper)
        "formulaText": "{{f_enrich_id}}?.url",
    }}
)
# Column type auto-promotes from "text" to "formula" after successful PATCH
```

### Formula Syntax — What Clay Actually Supports

Clay formulas use a **limited expression evaluator**, NOT full JavaScript. Key rules:

**Works:**
- Ternary expressions: `condition ? "yes" : "no"`
- Nested ternaries: `a ? "x" : b ? "y" : "z"`
- String methods: `.toLowerCase()`, `.split()`, `.join()`, `.slice()`, `parseInt()`, `String()`
- Regex `.test()`: `/pattern/i.test(String({{f_id}}) || "")` — **preferred for matching**
- Simple regex in `.match()`: `hcRaw.match(/[0-9]+/)`
- `let` declarations (but only the LAST expression returns — variables from earlier `let` lines are NOT accessible)
- Null coalescing: `({{f_id}} || "")`
- Optional chaining: `{{f_id}}?.key`

**Does NOT work:**
- IIFE: `(function() { ... })()` — parses but doesn't execute correctly
- Arrow functions with block bodies: `(x => { return x; })(val)` — block body ignored
- `.includes()`, `.indexOf()` — may cause "Error evaluating formula" on some Clay versions
- `.some()`, `.filter()`, `.map()`, `.find()` — parse error
- `REGEXMATCH()`, `REGEXEXTRACT()`, `LOWER()` — these are spreadsheet functions, NOT available in Clay
- Regex word boundaries `\b` — causes parse error
- Multi-statement `let` with semicolons — only last expression returns, earlier variables lost

**Pattern for complex formulas:** Use pure nested ternaries with inline expressions. Repeat the field reference rather than trying to store in a variable:

```python
# ✅ CORRECT — pure nested ternary, inline everything
formula = (
    '!(loc_check_expression)'
    ' ? "No - Location"'
    ' : hc_check === "" ? "No - HC"'
    ' : parseInt(hc_expr, 10) < 11 ? "Too small"'
    ' : parseInt(hc_expr, 10) > 200 ? "Too large"'
    ' : "Yes"'
)
# Where loc_check_expression and hc_expr are inlined field references,
# NOT variables. The field ref repeats each time it's used.

# Example: location check with 30 countries — use .test() with regex
countries = "united states|usa|canada|united kingdom|uk|germany|france|netherlands|poland|spain|italy"
formula = f'/{countries}/i.test(String({{{{f_location_id}}}}) || "") ? "QUALIFIED" : "SKIP"'
# Much cleaner than repeating .indexOf() 30 times
```

**PATCH formula requires `formulaType`:** Without it, the formulaText is silently dropped:
```python
# ❌ WRONG — formulaText silently dropped
clay.patch(f"/tables/{tid}/fields/{fid}", {
    "typeSettings": {"formulaText": "...", "dataTypeSettings": {"type": "text"}}
})

# ✅ CORRECT — include formulaType
clay.patch(f"/tables/{tid}/fields/{fid}", {
    "typeSettings": {
        "formulaText": "...",
        "formulaType": "text",        # ← REQUIRED
        "dataTypeSettings": {"type": "text"}
    }
})
```

---

## Running Columns — Rate Limits

**`runRecords` is required** — omitting it returns a 400 error:
```python
# ❌ 400 Bad Request: "Field runRecords - Required"
{"fieldIds": [...], "callerName": "clay-client"}

# ✅ Correct
{"fieldIds": [...], "callerName": "clay-client", "runRecords": {"recordIds": [record_id]}}
# OR for all records in a view:
{"fieldIds": [...], "callerName": "clay-client", "runRecords": {"viewId": view_id}}
```

**`runRecords: {"viewId": ...}` uses the UI row limit:** If the Clay UI view is set to show only 10 rows, the run endpoint will only trigger 10 records. Set the view to show all rows before running via API.

**`ERROR_TOO_MANY_RUNS`** — Clay rate-limits columns triggered too frequently:
- Status in cell: `{"metadata": {"status": "ERROR_TOO_MANY_RUNS"}}`
- The run API still returns 200 but execution is rejected
- Fix: wait ~3 minutes before re-running the column
- Affects testing heavily — don't trigger the same column more than 3-4 times in quick succession

**`isPreview: true`** — normal for API-triggered runs:
- `{"metadata": {"status": "SUCCESS", "isPreview": true}}`
- This is NOT an error — downstream formula columns CAN access preview data
- Enrichment data is usable even with `isPreview: true`

---

## Common Errors and Fixes

| Error | Cause | Fix |
|-------|-------|-----|
| "No model found with name 'gemini-2.0-flash'" | Deprecated model | Use `"gemini-2.5-flash"` |
| systemPrompt shows empty in Clay UI | `"value"` key silently dropped | Use `"formulaText"` with `'"quoted string"'` |
| systemPrompt causes "Invalid formula" | String too long or contains markdown | Keep under ~1,000 chars, no `**`, `#`, backticks |
| Column mapping empty in UI | Wrong input name (e.g. `url` instead of `company_identifier`) | Check exact input names by inspecting a working column via `clay.get_records()` or the HAR approach |
| Column mapping empty in UI | Using `{{Column Name}}` reference | Use `{{field_id}}` (field IDs only) |
| Gemini account not connecting | `authAccountId` in `inputsBinding` | Move `authAccountId` to top-level `typeSettings` |
| Formula column has no formula after create | Used `"type": "formula"` on create | Create as `text` type, then PATCH `typeSettings.formulaText` |
| formulaText not saving on any column | Missing `formulaType: "text"` in PATCH | Always include `formulaType: "text"` — it's the gatekeeper (url/number dataTypes work fine) |
| `get_schema()` returns empty fields | Wrong viewId or schema has no typeSettings | Use `table["fields"]` from `get_table()` for full column data |
| Record creation returns 200 but values vanish | Used populated `POST /tables/{t}/records` | Use `clay.create_records()` or Clay's 2-step UI flow: blank `POST /records` with pre-generated ids, then bulk `PATCH /records` with values |
| Fresh table: name-keyed `create_records` raises "values did not persist" (rows stay blank) | Name→field-id mapped PATCH 200s but never commits on freshly created tables (verified 2026-07-23; NOT the write-restricted-cookie mode — `preflight()` shows `write: True`) | Blank rows + fid-keyed `bulk_update_records({fid: value})` + re-fetch to verify |
| Column creation 400: "Missing data type settings" | Text column missing typeSettings | Always include `"typeSettings": {"dataTypeSettings": {"type": "text"}}` for text columns |
| Run rejected: "Field runRecords - Required" | Missing runRecords | Always include `"runRecords": {"recordIds": [...]}` or `{"viewId": ...}` |
| `ERROR_TOO_MANY_RUNS` | Column triggered too many times in short window | Wait ~3 minutes, then retry |
| http-api-v2 queryString/headers broken (chars 0,1,2,3...) — verified 2026-04-23 | Used `formulaText` with JSON object | Use `formulaMap` with per-key formulas. The cell preview `"Status Code: 200"` can hide this if the target server accepts any GET — verify via `externalContent.fullValue` on the full record endpoint. |
| Claygent "Unable to parse output schema" | `answerSchemaType` + `_metadata` missing, or `jsonSchema` single-encoded | Add both inputs with `formulaMap`. `jsonSchema` must be double-encoded: `json.dumps(json.dumps(schema))`. `_metadata` must have `modelSource: '"user"'` (inner quotes). |
| Formula "Error evaluating formula" | Used `.indexOf()`, `.includes()`, `REGEXMATCH()`, or `LOWER()` | Use `/pattern/i.test(String({{f_id}}) \|\| "")` for matching. See Formula Syntax section. |
| Webhook columns blank despite data in source | Extraction columns are plain text, not formulas | PATCH with `formulaText: "{{source_field}}?.key"`, `formulaType: "text"`, `dataTypeSettings: {"type": "text"}` |
| 404 "NoMatchingURL" on `/views/{view_id}/records` | Endpoint does not exist | Use 2-step: `/views/{view_id}/records/ids` then `bulk-fetch-records` |
| `bulk-fetch-records` 400 error | Empty or missing `recordIds` | Always pass a non-empty `recordIds` array |
| Enrich Company `ERROR_INVALID_INPUT` | Company name used instead of LinkedIn URL | Use LinkedIn company URL as `company_identifier` input |
| `mappedResultPath` formula returns empty | Missing `mappedResultPath` array for nested data | Add `"mappedResultPath": ["experience", "0", "url"]` to typeSettings. **Caveat (2026-07-24): API create/PATCH silently STRIP `mappedResultPath`** — only the UI extract flow persists it. Functionally harmless when `formulaText` does the extraction itself (`?.a?.[0]?.b`); treat it as UI metadata you cannot set via API. |
| `POST /sources` returns "Invalid subscriptions" | Wrong endpoint for people/company SEARCH sources | Use `POST /sources/create-cpj-table` instead. `POST /sources` works fine for `type: "manual"` routing sources (verified 2026-07-21). |

---

## LinkedIn Posts (social-posts action)

```python
{
    "type": "action",
    "name": "LinkedIn Posts",
    "typeSettings": {
        "actionKey": "social-posts-get-post-activity-posts-and-shares",
        "actionPackageId": "b210a16b-cdaf-4cbd-ad9b-42d762cd165f",
        "dataTypeSettings": {"type": "text"},
        "inputsBinding": [
            # ⚠️ Input name is "socialUrl" — NOT linkedin_url
            {"name": "socialUrl", "formulaText": ref(f_linkedin_url)},
            {"name": "num_posts", "formulaText": '"10"'}   # string, not number
        ]
    }
}
```

---

## Conditional Execution ("Only run if")

Add `conditionalRunFormulaText` to `typeSettings` to gate column execution:

```python
{
    "type": "action",
    "name": "Enrich Company",
    "typeSettings": {
        "actionKey": "enrich-company-with-mixrank-v2",
        # ... other settings ...
        "conditionalRunFormulaText": "Number({{f_employees_id}}) > 5"
    }
}
# When condition not met, cell status is ERROR_RUN_CONDITION_NOT_MET
```

**API-run skip is SILENT (verified 2026-07-30):** triggering a gated column via
`run_column` when the condition doesn't pass leaves the cell **completely blank** (`{}` —
no value, no metadata status; the `ERROR_RUN_CONDITION_NOT_MET` status above was observed
in other contexts, not on API-triggered runs). Worse, a `!!{{boolean_field}}`-style gate
was observed skipping even when the referenced gate cell read `true` — evaluation
semantics are unclear (possibly stale-dependency related). `force_run: true` bypasses the
conditional and is the reliable manual-activation path; treat `conditionalRunFormulaText`
as a spend-guard for auto-run mode, not as logic you can depend on during API-driven runs.

**Gate references are dependency-DAG edges (verified 2026-08-06):** PATCHing an action
column whose `conditionalRunFormulaText` references a formula column that — transitively,
via another column's GATE — depends back on the gated column returns
`400 {"type": "BadRequest", "message": "Dag is cyclical",
"details": {"inputFieldIdsWithErrors": [...]}}`. Cycles through GATES are rejected the
same as cycles through formula values. Practical rule: a spend-gate must only reference
columns strictly upstream of the gated column; when a helper formula aggregates from
multiple lookups, gate on the specific upstream lookup's raw path instead of the
aggregate.

---

## AI Columns — API Read Behavior

When reading AI column values via `bulk-fetch-records`, the API returns:
```json
{"value": "Response", "metadata": {"isPreview": true, "status": "SUCCESS"}}
```
The actual parsed JSON is stored internally. Formula extractors (`?.key`) CAN access the parsed JSON from AI columns even though the API shows just `"Response"`.

---

## runRecords: recordIds vs viewId

Always prefer `recordIds` when you have them:
```python
# ✅ Reliable — explicit record IDs
{"runRecords": {"recordIds": ["r_xxx", "r_yyy"]}}

# ⚠️ Less reliable — depends on view's UI settings (row limit, filters)
{"runRecords": {"viewId": "v_xxx"}}
```

---

## create-cpj-table

This endpoint is fully documented at `## Find People / Find Companies sourced-table creation (documented, NOT yet wrapped in claycast)` later in this file (captured live 2026-04-30). **Note:** regular `POST /sources` returns 404 "Invalid subscriptions" for these source types — `create-cpj-table` is the only valid path.

Additional live-verified behavior (2026-07-21): the endpoint VALIDATES the company scope at create — the company table must contain at least one row whose bound column resolves to a real Clay company, else `400 "None of the company table rows resolved"`. Every attempt, **including failed ones**, side-creates a companion `Update People Search (...)` trigger column on the company table (clean up orphans after failures). Passing `disableTriggerOnCreate: true` + `disableTriggerOnUpdate: true` inside `cpjConfig.typeSettings` suppresses the initial search run. Full notes: `## View filter/sort write path + replication side-effects` at the end of this file.

---

## Webhook Source Tables — Extraction Columns

### A webhook SOURCE alone does not ingest — you need a source FIELD (verified 2026-07-24)

`create_webhook_source` (`POST /v3/sources` with `type: "webhook"`) creates a source with `sourceSubscriptions: []`. POSTs to its webhook URL return OK and increment `state.numSourceRecords`, but **NO table rows appear** — the records buffer server-side. Creating a source FIELD is what registers the subscription:

```python
clay.create_column(t, {
    "type": "source",
    "name": "Webhook",
    "typeSettings": {"sourceIds": [s_id], "canCreateRecords": True},
})
# get_source(s_id) then shows sourceSubscriptions: [{tableId, fieldId}]
```

After the field exists, the previously buffered records **retroactively materialize** as rows.

### Arrays do NOT fan out (verified 2026-07-24)

POSTing a JSON array of N objects to the webhook URL counts as ONE source record and does NOT create N rows (verified twice — before and after the subscription existed; the array-row materialized with no matching fields). Send **one object per POST**.

### Dedupe semantics — SKIP-mode, URL-shaped keys only (verified 2026-07-24/30)

Webhook source tables dedupe on the payload's `url` key (stored as `dedupeValue` on the
record), with three sharp edges:

- **`dedupeValue` registers ONLY for URL-shaped values.** A synthetic key like
  `sf-contact:003...` stores `dedupeValue: null` = NO dedupe protection (duplicates land as
  separate rows). A URL-shaped sentinel (`https://anything.invalid/{id}`) registers fine —
  use that form when contacts lack a real URL.
- **Dedupe is SKIP-mode, not update-mode.** Re-posting a payload whose key already exists
  neither creates a row nor updates the existing one — and **new payload keys are never
  merged retroactively** into existing rows. Adding a field to your POST body only reaches
  rows created after the change; backfill needs delete + re-post or a table-side enrichment.
- **Received ≠ written.** The source's `state.numSourceRecords` still increments on every
  accepted POST, including dedupe-skipped ones — don't use it as a row count.

Related: the records API (`list_records` / bulk-fetch) shows the webhook source field's cell
as a **preview string** (e.g. `"Received July 24th, 2026"`) — the raw payload object is NOT
readable through the records API. Formula columns referencing the source field DO see the
full object: `{{source_field}}?.any_key` works for any payload key (no source-side mapping
needed) and computes on row arrival even with table `AUTO_RUN_ON: false`. Verified 2026-07-30.

### Extraction columns

When you create a table with a webhook source, Clay creates a source column that stores the full JSON payload. The individual data columns (name, headline, etc.) are **NOT automatically populated** — you must create formula extractors.

**Common mistake:** Creating columns like "name", "headline" as plain text. They show up in the UI with the right names but contain NO data. All downstream columns see blanks.

```python
# After creating a webhook source table, PATCH each data column to extract from source:
raw = clay.get_table(TABLE_ID)
source_field_id = None
for f in raw['table']['fields']:
    if f['type'] == 'source':
        source_field_id = f['id']
        break

# For each extraction column:
for field_id, json_key in extraction_columns.items():
    clay.session.patch(f"{BASE}/tables/{TABLE_ID}/fields/{field_id}", json={
        "typeSettings": {
            "formulaText": f'{{{{{source_field_id}}}}}?.{json_key}',
            "formulaType": "text",
            "dataTypeSettings": {"type": "text"}    # REQUIRED for PATCH
        }
    })
    time.sleep(0.15)
```

---

## Verification (MANDATORY after every create/patch)

**Never trust a 200 status.** Clay accepts broken configs silently. After creating or patching any column:

```python
# 1. GET the column back and verify config
raw = clay.get_table(TABLE_ID)
for f in raw['table']['fields']:
    if f['id'] == field_id:
        ts = f.get('typeSettings', {})
        inputs = ts.get('inputsBinding', [])
        input_names = [i.get('name') for i in inputs]

        # For AI columns: verify answerSchemaType exists and is double-encoded
        for inp in inputs:
            if inp.get('name') == 'answerSchemaType' and 'formulaMap' in inp:
                js = inp['formulaMap'].get('jsonSchema', '')
                parsed = json.loads(js)
                assert isinstance(parsed, str), f"jsonSchema is {type(parsed)} — needs double encoding"
```

---

## Enrichments Panel / Preset Catalog Endpoints (verified 2026-04-24 via UI HAR capture)

These endpoints power Clay's "+ Add enrichment" panel — the catalog browser that lists actions and waterfalls grouped by category. ClayCast now exposes them via `clay.list_preset_categories(...)`, `clay.list_presets_filtered(...)`, `clay.list_presets_by_category(...)`, `clay.list_disabled_actions(...)`, `clay.list_starred_resources(...)`, and `clay.get_resource_star(...)`. Use `rewrite_preset_placeholders(...)` before feeding preset `inputsBinding` payloads into `create_action_column(...)`.

### Catalog browsing

| Endpoint | Purpose | Query params | Response shape |
|---|---|---|---|
| `GET /v3/presets/workspace/{ws}/categories` | All category names | none | `list[str]` — e.g. `["AI", "Company Data", "6 Sense", ...]` (59 observed on one workspace) |
| `GET /v3/presets/workspace/{ws}/filtered` | Primary filter/search | `types[]`, `categories[]` (repeatable) | `list[dict]` of presets — each has `id` (`pre__...`), `name`, `type`, `description`, `preset`, `category`, `isPublic`, `createdAt`, `updatedAt` |
| `GET /v3/presets/workspace/{ws}` | Richer per-category preset list | `category` (single) | `list[dict]` of presets with extra fields: `actionKey`, `actionPackageId`, `createdByUserId`, `deletedAt` |
| `GET /v3/actions?workspaceId={ws}` | Raw action catalog | `workspaceId` (required) | `{"actions": [...]}` — already wrapped by `clay.list_actions()` |
| `GET /v3/workspaces/{ws}/all-disabled-actions` | Admin-disabled actions for this workspace | none | `{"disabledActionIds": [<ids>, ...]}` |
| `GET /v3/resources/starred` | User's starred resources | `resourceType` (e.g. `TABLE`, `ACTION`) | `{"starredResources": [...]}` |
| `GET /v3/resources/{package_id}%2F{action_key}/star` | Star state for ONE action | `resourceType` required (`ACTION` observed) | `{"isStarred": bool}` |

### Preset `type` values observed

- `action` — a standard single-action preset
- `waterfall` — a waterfall (Source A → fallback B → …) preset
- `parent_waterfall` — appears to be a category-level waterfall grouping

### Config-panel side effect

When the user hovers or opens a preset's config, Clay fires `GET /v3/tables/{t}/views/{v}/table-schema-v2` — the view-schema-with-sample-records call — so the config UI can populate its column-picker dropdowns. Already documented under Table Schema Access above.

### Notes for claycast implementers

- The primary browse endpoint is `/presets/.../filtered` — it accepts repeated `types[]` and `categories[]` params. `urllib.parse.urlencode([('types[]', 'waterfall'), ('types[]', 'action'), ('categories[]', 'AI')])` produces the right shape.
- The per-action star-state endpoint is hit ~60× per panel open (once per visible action). If exposing via claycast, batch this client-side or document the N+1 cost.
- `isPublic: true` distinguishes Clay-shipped presets from workspace-authored ones; a claycast filter helper could expose `include_private=False` to filter down to Clay-official.

---

## Audience endpoints (documented, NOT yet wrapped in claycast)

Captured during clay-spy smoke tests on 2026-04-30. These power Clay's "Find People" / "Find Companies" / audience-search product surface. **Not yet exposed via claycast SDK methods** — until they're implemented, call them via the raw HTTP escape hatch (`clay.get(path, params=...)` for GETs, `clay.post(path, body)` for POSTs). Closing the SDK gap here would partially close `feature-gaps.md` #2 ("Find People sourced-table creation").

Required query params and body shapes are listed verbatim from the captured calls — Clay's server validates these, so they are NOT optional unless noted.

### POSTs

| Endpoint | Body shape | Response shape |
|---|---|---|
| `POST /v3/workspaces/{ws}/audiences/accounts` | `{limit, offset, includeDeleted, isArchived, shouldInjectDraftFilter, segmentType}` | `{accounts: [...], pagination: {limit, offset, total, hasMore}}` |
| `POST /v3/workspaces/{ws}/audiences/contacts` | `{limit, offset, includeDeleted, isArchived, shouldInjectDraftFilter, segmentType, includeData: {accountIds: bool}}` — note the extra `includeData` key vs `/accounts` | `{contacts: [...], pagination: {...}}` |
| `POST /v3/workspaces/{ws}/audiences/count` | `{entityType: "ACCOUNT"\|"CONTACT", isArchived, shouldInjectDraftFilter, segmentType}` | `{count: N}` |

### GETs (with required query params)

| Endpoint | Required params | Response shape |
|---|---|---|
| `GET /v3/workspaces/{ws}/audiences/segments` | `entityType=ACCOUNT\|CONTACT` | `{segments: [...], total: N}` |
| `GET /v3/workspaces/{ws}/audiences/scheduled-searches` | none | `{scheduledSearches: [...], total: N}` |
| `GET /v3/workspaces/{ws}/audiences/imports` | `entityType=ACCOUNT\|CONTACT` | `{imports: [...]}` |
| `GET /v3/workspaces/{ws}/audiences/imports/external-source-import-history/{TYPE}` | `{TYPE}` is `ACCOUNT` or `CONTACT` (path segment, not query) | bare `list[dict]` |
| `GET /v3/workspaces/{ws}/audiences/accounts/columns` | `includeSystemFields=true\|false` | bare `list[dict]` |
| `GET /v3/workspaces/{ws}/audiences/contacts/columns` | `includeSystemFields=true\|false` | bare `list[dict]` |
| `GET /v3/workspaces/{ws}/audiences/custom-objects/columns` | `includeSystemFields=true\|false` AND `objectType=<NAME>` (e.g. `OPPORTUNITY`) | bare `list[dict]` |
| `GET /v3/workspaces/{ws}/audiences/referencing-people-segments` | none | `{referencingSegmentsMap: {...}}` |
| `GET /v3/workspaces/{ws}/audiences/fields/distinct-origin-source-ids` | `entityType=ACCOUNT\|CONTACT` | `{originSources: [...]}` |
| `GET /v3/workspaces/{ws}/tables/{t}/views/{v}/ad-audiences` | none | observed `null` in capture (may need a different state to populate) |
| `GET /v3/workspaces/{ws}/ad-audiences/sync-limit-status` | none | `{limit, used, remaining, canStartNewSync, isEnabled}` |

Concrete request/response payloads for any of these are in the local clay-spy capture archives (look for the corresponding `kind: "http"` lines).

---

## Find People / Find Companies sourced-table creation (documented, NOT yet wrapped in claycast)

Captured during the Find leads UI walkthrough on 2026-04-30. **This is the endpoint that closes `feature-gaps.md` #2** ("Find People / Find Companies sourced-table creation"). One atomic call creates workbook + table + view + source.

### `POST /v3/sources/create-cpj-table` — atomic Find People sourced-table creation

**Note:** "CPJ" appears to be Clay's internal code for these source-driven tables (the UI component is named `SculptorCPJFilters`). The Find leads filter-builder UI is internally called "Sculptor".

#### Request body

```json
{
  "workspaceId": 12345,
  "workbookName": "People Search",
  "workbookId": null,                          // null = create new workbook; or existing wb_<id>
  "conversationId": "cc_<id>",                 // chat-conversation that built filters; can be null for direct creation
  "assignedFieldId": "f_people_search",        // synthetic field id (constant for People search)
  "cpjConfig": {
    "type": "people",                          // or "companies"
    "typeSettings": {
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
      "dedupeOnUniqueIds": true,
      "hasEvaluatedInputs": false,
      "inputs": { /* full ~50-key filter dict — see below */ }
    },
    "clientSettings": {"tableType": "people"},
    "basicFields": [ /* 7 default columns — see below */ ],
    "previewActionTaskId": "at_<id>"          // optional; task id from preview run
  }
}
```

#### Default `basicFields` for People

```json
[
  {"name": "First Name", "dataType": "text", "formulaText": "{{source}}.first_name"},
  {"name": "Last Name", "dataType": "text", "formulaText": "{{source}}.last_name"},
  {"name": "Full Name", "dataType": "text", "formulaText": "{{source}}.name"},
  {"name": "Job Title", "dataType": "text",
    "formulaText": "{{source}}.matched_experience.job_title || {{source}}.latest_experience_title"},
  {"name": "Location", "dataType": "text", "formulaText": "{{source}}.location_name"},
  {"name": "Company Domain", "dataType": "url", "formulaText": "{{source}}.domain"},
  {"name": "LinkedIn Profile", "dataType": "url", "formulaText": "{{source}}.url",
   "isDedupeField": true}
]
```

`{{source}}` references the current row's data from the action's response. `isDedupeField: true` marks the field claycast will dedupe on.

#### Full `inputs` schema (~50 keys, mostly empty when unset)

Same shape as the filter UI exposes. Key fields:

| Filter category | Input keys |
|---|---|
| Job title | `job_title_keywords`, `job_title_mode` (`"smart"`/?), `job_title_seniority_levels`, `job_title_seniority_levels_v2`, `job_title_seniority_match_mode` (`"exact"`), `job_title_seniority_floor_level`, `job_title_exact_match`, `job_title_exact_keyword_match`, `job_title_exclude_keywords`, `job_functions` |
| Location | `location_countries_include` / `_exclude`, `location_regions_include` / `_exclude`, `location_cities_include` / `_exclude`, `location_states_include` / `_exclude`, `locations`, `locations_exclude`, `search_raw_location` |
| Company attrs | `company_industries_include` / `_exclude`, `company_sizes` (e.g. `"201-500"`), `company_annual_revenues`, `company_description_keywords` / `_exclude`, `company_identifier`, `company_record_id`, `company_table_id`, `company_audience_segment_id`, `include_company_filter_bitmap`, `include_company_filter_identifier_count` |
| Experience | `experience_count`, `max_experience_count`, `current_role_min_months_since_start_date`, `current_role_max_months_since_start_date`, `role_range_start_month`, `role_range_end_month`, `include_past_experiences`, `previous_entities_bitmap` |
| Profile | `headline_keywords`, `profile_keywords`, `about_keywords`, `certification_keywords`, `job_description_keywords`, `school_names`, `languages` |
| Network | `connection_count`, `max_connection_count`, `follower_count`, `max_follower_count` |
| Identity | `name`, `names`, `exclude_people_identifiers_mixed`, `exclude_entities_configuration`, `exclude_entities_bitmap`, `exclude_entity_bitmap` |
| Output / clustering | `limit` (50 in preview, 50000 when creating tables), `result_count` (`true`), `cluster_count`, `clustering_method` (`"hdbscan"`), `start_from_method` (`"CsvOfCompanies"`) |

Empty/null values are kept (not omitted) — the UI sends the full schema every time.

#### Response

```json
{
  "tableId": "t_...",
  "viewId": "gv_...",
  "workbookId": "wb_...",
  "sourceId": "s_...",
  "isNewTable": true
}
```

### `POST /v3/actions/run-enrichment` — the search-execution endpoint

The Find leads UI fires this on every filter change. Body shape:

```json
{
  "workspaceId": "12345",                  // STRING (numeric value rejected with `"workspaceId" must be a string`)
  "enrichmentType": "<one of allowed list>",
  "options": {"sync": true, "returnTaskId": true, "returnActionMetadata": true},
  "inputs": {"limit": 50, ...filter dict...}
}
```

**Allowed `enrichmentType` values (6 total, server-allowlisted — SHRANK 2026-07-23; was 10 as of 2026-04-30):**

| `enrichmentType` | What it is |
|---|---|
| `find-and-enrich-personal-linkedin` | Find-and-enrich for personal LinkedIn |
| `enrich-personal-linkedin-url` | Enrich a single LinkedIn URL |
| `enrich-company` | Enrich a company |
| `claygent` | Run a Claygent |
| `find-employee-headcount` | Get employee headcount for a company |
| `search-companies-from-table` | Search companies seeded from a table |

**ALLOWLIST REGRESSION (verified 2026-07-23):** the four `*-preview` search types in the 2026-04-30 allowlist — `find-lists-of-people-with-mixrank-source-preview`, `find-lists-of-companies-with-mixrank-source-preview`, `find-lists-of-jobs-with-mixrank-source-preview`, `find-company-lookalikes-clustered-preview` — were REMOVED server-side. Sending any of them now returns HTTP 400: `{"error":"BadRequest","message":"\"enrichmentType\" must be one of [find-and-enrich-personal-linkedin, enrich-personal-linkedin-url, enrich-company, claygent, find-employee-headcount, search-companies-from-table]"}`. Consequence: `clay_client.preview_sourced_table()` is DEAD (400s). Free-preview workaround: the OFFICIAL `clay` CLI search (`clay search filters-mode create/run`) or the public API `/public/v0/search/filters-mode` — both free.

**Critical: non-preview Mixrank actions are NOT directly callable via this endpoint.** Sending `enrichmentType: "find-lists-of-people-with-mixrank-source"` (no `-preview` suffix) returns HTTP 400 with the allowlist above. The full action is only invokable via `POST /v3/sources/create-cpj-table` (which forces the save-to-table flow with `limit: 50000`).

### Preview behavior — what's free vs gated

**REGRESSED 2026-07-23:** everything in this subsection is HISTORICAL — the `*-preview` enrichmentTypes were removed from the run-enrichment allowlist (see above) and now 400. Kept as a record of how the preview layer behaved while it existed; the free-preview replacement is the official `clay` CLI search / public API filters-mode.

For the `*-preview` variants:

| Property | Value (verified live 2026-04-30) |
|---|---|
| Total count exposed in `result.peopleCount` (or analogous) | ✓ Yes |
| Inline rows in `result.people` (or analogous) | ✓ Yes — but **hard-capped at exactly 50** |
| `additionalCreditCost` | 0 |
| `limit > 50` | Returns `{result: {people: [], peopleCount: null}, metadata: {status: "ERROR_INVALID_INPUT"}}` |
| `limit = 51` | Same `ERROR_INVALID_INPUT` — boundary is exactly at 50 |

**Practical implication:** Clay deliberately gates Mixrank's full search results behind table creation. You can iterate filter combinations forever at zero cost to learn the count + sample 50 rows, but rows 51-N require committing to a `create-cpj-table` import.

### Companion endpoints in the Find People flow

| Endpoint | Purpose |
|---|---|
| `POST /v3/actions/run-enrichment` | Runs the search action. Body: `{workspaceId, enrichmentType, options: {sync, returnTaskId, returnActionMetadata}, inputs: <full filter dict>}`. **Preview mode** (`enrichmentType: "<actionKey>-preview"`) returns `{result: {people: [...], peopleCount: N}, metadata: {...}, taskId}` with full inline rows. **Preview is 0 credits** but capped at 50 rows by UI. **(Preview mode REMOVED from the allowlist 2026-07-23 — now 400s; see the allowlist section above.)** The non-preview variant (drop the `-preview` suffix) is used inside `create-cpj-table` with `limit: 50000`. |
| `POST /v3/presets` | Save a search as a Preset. Body wraps the filter inputs + name + description. Returns the new preset (id format `pre__<id>` — note double underscore). |
| `GET /v3/workspaces/{ws}/presets/{pre__id}` | Fetch one preset by id. (Different surface from the `/v3/presets/workspace/{ws}/...` listing endpoints.) |
| `GET /v3/presets/workspace/{ws}/recent-searches?actionPackageId=<pkg>&actionKey=<key>` | Recent searches scoped to one action |
| `GET /v3/workspaces/{ws}/peopleSearchLimit` | Per-workspace people search quota |
| `GET /v3/ai-quickstart/{ws}/sculptor-suggestions` | AI-suggested filter values based on workspace context |
| `GET /v3/workspaces/{ws}/rollover-multiplier?billingSchedule=monthly\|annually` | Rollover credit rate per billing period |
| `GET /v3/credit-accrual?workspaceId={ws}&rewardsOnly=true` | Credit accrual filtered to rewards-only |
| `POST /v3/12345/ai-generation/chat-conversation` | Create new ai-chat-conversation (Find leads filter UI lives in one of these) |
| `GET /v3/{ws}/ai-generation/chat-conversation/{cc_id}/source-state` | Read filter state for a conversation |
| `PATCH /v3/{ws}/ai-generation/chat-conversation/{cc_id}/source-state` | Update filter state (fires on every UI filter change) |
| `GET /v3/{ws}/ai-generation/chat-conversation/{cc_id}/messages` | Read messages in conversation |
| `GET /v3/{ws}/ai-generation/chat-conversation/{cc_id}/stream` | SSE stream for live updates (HTTP 204 is normal) |
| `POST /v3/{ws}/ai-generation/chat-conversation/{cc_id}/confirm-ai-onboarding-output` | Confirm chat output for materialization (fires before `create-cpj-table`) |

### Proposed claycast surface

```python
def create_sourced_table(
    self, workbook_name: str, *,
    inputs: dict,                          # the ~50-key filter dict
    cpj_type: str = "people",              # "people" | "companies"
    workbook_id: str | None = None,        # None = create new workbook
    conversation_id: str | None = None,    # None = standalone (no chat-built state)
    workspace_id: int | str | None = None,
) -> dict:                                  # {tableId, viewId, workbookId, sourceId, isNewTable}
    ...
```

The `typeSettings` (actionKey, basicFields, recordsPath, idPath, etc.) can be hardcoded per `cpj_type`:
- `"people"` → action `find-lists-of-people-with-mixrank-source` + the 7 standard person fields
- `"companies"` → equivalent companies action + standard company fields (need to capture this variant separately)

**Companies variant NOT YET captured.** The user only walked through the People flow. A future capture session driving the Companies tab would surface the analogous `actionKey`, `basicFields`, etc. for `"type": "companies"`.

---

## End-to-end flows (Find People / Find Companies / saved searches)

Centralized orchestration reference for the multi-endpoint flows captured during the 2026-04-30 walkthrough. Each flow lists the endpoint sequence, the IDs that pass between steps, and decision points.

### Conceptual model — IDs and what they represent

| ID | Created by | Purpose / lifecycle |
|---|---|---|
| `cc_<id>` (chat-conversation) | `POST /v3/{ws}/ai-generation/chat-conversation` | Server-side state holder for an in-progress filter session. Each filter change PATCHes its `source-state`. Survives across page reloads. The Find leads UI lives inside one of these. |
| `pre__<id>` (preset, double-underscore) | `POST /v3/presets` | A saved Find People/Companies search. `type: "evaluated_source"` distinguishes these from action-presets (`type: "action"`) and waterfall-presets (`type: "waterfall"`). Stores the full filter `inputsBinding` + `aiSummary`. |
| `at_<id>` (action-task) | Returned in `taskId` of any `POST /actions/run-enrichment` response | Identifies one execution of a search action. Optionally referenced as `previewActionTaskId` when materializing a table. |
| `t_<id>` / `gv_<id>` / `wb_<id>` / `s_<id>` | Returned by `POST /v3/sources/create-cpj-table` | The materialized table, view, workbook, and source. Standard claycast resources from this point on. |
| `audimp_<id>` (audience-import) | `POST /v3/workspaces/{ws}/audiences/import-cpj-source` | One queued import job from a CPJ source into the People/Companies audience. Has a `jobId` for status polling. |
| `audseg_<id>` (audience-segment) | Same — returned alongside `audimp_<id>` | A named segment in the People/Companies audience. Auto-named by Clay (e.g. "Retail, $25M-$75M Revenue Apr 30 2026"). Becomes the `segmentId` filter for subsequent audience queries. |

### Flow A — Build search from scratch → create table (UI-fidelity path)

Mirrors what the Find leads UI does. Each filter change is server-persisted via the chat-conversation, and the table is created from that conversation's accumulated state.

```
1. POST /v3/{ws}/ai-generation/chat-conversation
   ← {} or seed body
   → {id: "cc_<id>", ...}                       // creates conversation

2. (per filter change in UI)
   PATCH /v3/{ws}/ai-generation/chat-conversation/cc_<id>/source-state
   ← {<partial filter delta>}
   → 200                                         // persists state server-side

3. (per filter change, fired alongside step 2) — ⚠ DEAD 2026-07-23: the *-preview
   enrichmentTypes were removed from the run-enrichment allowlist (400 BadRequest);
   shape kept for history. Free preview today = official `clay search filters-mode`
   or public API /public/v0/search/filters-mode.
   POST /v3/actions/run-enrichment
   ← {workspaceId: "<ws>" (string!), enrichmentType: "find-lists-of-people-with-mixrank-source-preview",
       options: {sync: true, returnTaskId: true, returnActionMetadata: true},
       inputs: <full ~50-key filter dict, limit ≤ 50>}
   → (historical) {result: {people: [...50 max...], peopleCount: N},
       metadata: {status, additionalCreditCost: 0, ...},
       taskId: "at_<id>"}                        // was: 0 credits; capped at 50

4. (optional — save the in-progress search as a preset BEFORE materializing)
   POST /v3/presets
   ← {name, description, preset: {type: "evaluated_source", inputsBinding: <filters>, aiSummary},
       actionKey: "find-lists-of-people-with-mixrank-source",
       actionPackageId: "e251a70e-46d7-4f3a-b3ef-a211ad3d8bd2"}
   → {id: "pre__<id>", ...}

5. (right before table creation)
   POST /v3/{ws}/ai-generation/chat-conversation/cc_<id>/confirm-ai-onboarding-output
   ← (no body / minimal)
   → 200                                         // commits the conversation's output for materialization

6. POST /v3/sources/create-cpj-table
   ← {workspaceId, workbookName, workbookId: null|wb_<id>, conversationId: "cc_<id>",
       assignedFieldId: "f_people_search", cpjConfig: {<full config — see Find People section>}}
   → {tableId: "t_<id>", viewId: "gv_<id>", workbookId: "wb_<id>", sourceId: "s_<id>", isNewTable: true}
```

**State passing:** `cc_<id>` flows from step 1 through 6. `pre__<id>` is optional. `at_<id>` (from step 3's response) is referenced in step 6's `cpjConfig.typeSettings.previewActionTaskId` (optional).

### Flow B — Direct create (SDK-friendly shortcut, skip the chat-conversation)

The chat-conversation only exists to mirror the UI's incremental-filter UX. The `create-cpj-table` endpoint accepts the full `cpjConfig` directly — no chat-conversation needed.

```
1. (optionally) POST /v3/actions/run-enrichment with preview enrichmentType to validate filters + see count
   → {result: {peopleCount: N}, ...}             // free, capped at 50, optional
   // DEAD 2026-07-23: preview enrichmentTypes removed from the allowlist (400). Skip straight to
   // step 2, or validate filters for free via the official `clay` CLI search / public API filters-mode.

2. POST /v3/sources/create-cpj-table
   ← {workspaceId, workbookName, workbookId: null, conversationId: null,
       assignedFieldId: "f_people_search",
       cpjConfig: {type: "people", typeSettings: {<inputs + actionKey + basicFields>}, ...}}
   → {tableId, viewId, workbookId, sourceId, isNewTable: true}
```

**One round trip if you skip the optional preview.** The `inputs` you'd otherwise PATCH onto a chat-conversation are passed inline in `cpjConfig.typeSettings.inputs`. Verified 2026-04-30 by inspecting the actual request body Clay sends — it contains the full filter dict, so the conversation is just a UX scaffold, not a server-side requirement for materialization.

### Flow C — Open a saved search (preset → fresh preview)

Loading a saved search creates a NEW chat-conversation seeded from the preset's `inputsBinding`.

```
1. URL navigation: /workspaces/{ws}/chats/cc_<new>?loadedPresetId=pre__<id>
   (the cc_<new> is created server-side as part of step 2)

2. POST /v3/{ws}/ai-generation/chat-conversation
   → {id: "cc_<new>", ...}                       // fresh conversation each open

3. GET /v3/workspaces/{ws}/presets/pre__<id>
   → {id, name, type: "evaluated_source", actionKey, actionPackageId,
       preset: {type, aiSummary, inputsBinding: <full filter dict>}, ...}

4. GET /v3/{ws}/ai-generation/chat-conversation/cc_<new>/source-state
   → {<filter state seeded from preset.inputsBinding>}

5. POST /v3/actions/run-enrichment (preview, with preset's filters)
   → {result: {people: [...], peopleCount: N}, ...}
   // DEAD 2026-07-23: preview enrichmentTypes now 400 (allowlist regression — see run-enrichment section)
```

Each open creates a NEW `cc_<id>` — saved searches are stateless from the chat-conversation perspective. The preset is the durable record.

### Flow D — Continue dropdown variants

After running a preview, the "Continue" button has 3 menu items. **Options 1 and 2 hit the same endpoint** (`POST /v3/sources/create-cpj-table`) and only differ in body fields. Option 3 has not been captured.

| Option | Endpoint | Distinguishing body fields | Response `isNewTable` |
|---|---|---|---|
| **Save to new workbook and table** | `POST /v3/sources/create-cpj-table` | `workbookId: null`, no `cpjConfig.destinationTableId` | `true` |
| **Save to existing table** | `POST /v3/sources/create-cpj-table` | `workbookId: null`, **`cpjConfig.destinationTableId: "t_<existing>"`** | `false` |
| **Save to People** (Beta) | One endpoint (`POST /audiences/import-cpj-source`) — see Flow E. **Does NOT auto-create a table** — the user has to separately click an audience-export button (Flow F) if they also want a table. | n/a (different ID semantics) | n/a |

**Unified contract for options 1 + 2:** the same endpoint handles both new-table and existing-table flows. The presence of `cpjConfig.destinationTableId` is the discriminator. Response shape is identical except for `isNewTable`.

Verified live 2026-04-30 by pushing the "test" preset into existing table `t_xxx`:

```json
// Body (Save-to-existing variant)
{
  "workspaceId": "12345",
  "workbookName": "People Search",          // still sent even though new wb isn't created
  "workbookId": null,                        // null even though target table has a known workbook
  "conversationId": "cc_<id>",
  "assignedFieldId": "f_people_search",
  "cpjConfig": {
    "type": "people",
    "destinationTableId": "t_xxx",   // <-- the discriminator
    "typeSettings": {<same as new-table flow>},
    "clientSettings": {"tableType": "people"},
    "basicFields": [<same 7 default fields>],
    "previewActionTaskId": "at_<id>"
  }
}

// Response
{
  "tableId": "t_xxx",
  "viewId": "gv_xxx",         // existing view of the existing table
  "workbookId": "wb_xxx",     // resolved server-side from destinationTableId
  "sourceId": "s_<NEW source id>",            // a NEW source is created even on append
  "isNewTable": false
}
```

**Important:** even when targeting an existing table, a NEW `sourceId` is created. So an existing table can have multiple sources from multiple Find People runs accumulated against it. Each push adds rows from a fresh source-run.

**Companion endpoints fired during the existing-table flow** (in addition to the standard create-cpj-table sequence):

- `POST /v3/workspaces/{ws}/resources_v2/` (3 times during the table-picker dialog) — picks the destination resource
- `GET /v3/workspaces/{ws}/resources/{wb_<id>}` — fetches destination workbook metadata
- `GET /v3/workspaces/{ws}/credit-limits/workbook/{wb}/balance` — credit-balance check on destination workbook before commit

### Flow E — Save to People (Beta) — audience-only import

Captured live 2026-04-30. Despite the option's name, **this is a one-shot audience import**, NOT a table creation. The single click of "Save to People" fires only one endpoint and creates only an audience segment. To later turn that segment into a table, the user has to **separately** click an audience-export button — see Flow F below.

```
POST /v3/workspaces/{ws}/audiences/import-cpj-source
  ← {cpjSourceType: "people" | "companies",
      searchInputs: <full ~50-key filter dict from cpjConfig.typeSettings.inputs>}
  → {success: true,
      jobId: "<numeric>",                     // for async status polling
      importId: "audimp_<id>",
      segmentId: "audseg_<id>",
      segmentName: "<Clay auto-generated, e.g. 'Retail, $25M-$75M Revenue Apr 30 2026'>",
      message: "CPJ source import job successfully queued"}
```

**Capacity is unlimited.** Flows A/B (`create-cpj-table`) are capped at 50K rows per import. **Flow E has NO row cap** — confirmed by Clay 2026-04-30. Verified via a ~250K push the same day. **For any lead-sourcing workflow with more than 50K rows (or that you expect to grow past 50K), Flow E is the only viable path**; Flow A/B are for small, visibility-into-a-table workflows.

**Why "Excludes results already in People":** because the import goes through the audience-import path, Clay dedupes against existing people in the audience before importing. Flows A/B don't have this benefit — direct table creation can re-import the same person across multiple tables.

**Auto-generated segment name** is descriptive — Clay-side names the segment based on the filter values + date (e.g. `"Retail, $25M-$75M Revenue Apr 30 2026"`). Useful for human-readable audit trails of where data came from.

**Async note:** the import runs in the background. The response returns immediately with `jobId` (numeric) and the import is queued. Status-polling endpoint not yet captured.

### Flow F — Audience export → new table (separate user action)

Captured live 2026-04-30 right after Flow E completed. **This is a SEPARATE, USER-INITIATED step** — the user has to physically click an audience-export button and name the new workbook + table. It's not chained automatically from Flow E.

```
POST /v3/workspaces/{ws}/audiences/create-source
  ← {entityType: "CONTACT" | "ACCOUNT",
      segmentId: "audseg_<id>",          // any audience segment, not necessarily fresh from Flow E
      tableName: "<user-typed-name>"}
  → {success: true,
      sourceId: "s_<id>",
      tableId: "t_<id>",
      tableName: "<echoed>",
      message: "Created audience source and table. Data import has been queued."}
```

The destination workbook is also auto-created (the user chose its name in the UI; presumably there's a `workbookName` field too — the captured body shows only the three fields above, suggesting the workbook may default to a Clay-side scheme based on the segment name). Verify via a dedicated capture if a future caller needs to control workbook naming.

**Implication:** the audience layer can be the durable record, with multiple table exports created from the same segment over time (each `create-source` call yields a new `s_<id>` and `t_<id>` from the same `audseg_<id>`). Different tables can pull different subsets / shapes from the same underlying segment.

**Row cap: 50K per export.** Confirmed by the Clay UI's own messaging on the audience-export panel ("50K export limit for audience lists"). The `audiences/create-source` request body has no `limit` field, so the cap is **server-side and uniform** — callers cannot raise it via the body. To get more than 50K rows out of a segment into table form, run the export multiple times (each producing a different table) or paginate via `POST /audiences/contacts` directly against the segment.

**Comparison table — three paths from search to table:**

| Aspect | Flows A/B (`create-cpj-table`) | Flow E only (audience import) | Flow E + Flow F (audience → table) |
|---|---|---|---|
| Final state | Table | Audience segment | Audience segment + table |
| Row capacity | 50K per import (explicit `limit: 50000` in body) | **Unlimited** (confirmed by Clay 2026-04-30; ~250K push verified same day) | Segment holds unlimited rows; **table-export capped at 50K per call** (server-side; UI-confirmed) |
| Dedup scope | Per-table | **Cross-audience** | Cross-audience (table inherits) |
| Number of API calls | 1 | 1 | 1 + 1 = 2 (separate user actions) |
| Where rows are queryable | Table-records endpoints | `POST /audiences/contacts` | Both — table-records endpoints AND audience endpoints |
| Best for | Quick table-style review of <50K leads | Volume bulk-add to People for cross-table dedup | Volume + table-style review on a sub-segment |

### Cross-flow state-passing summary

```
   chat-conversation                        preset
   (cc_<id>)                                (pre__<id>)
        │                                      ▲
        │ ▲                                    │
        │ │ PATCH source-state per filter      │
        │ │                                    │
        ▼ │                                    │
        run-enrichment ─→ taskId at_<id> ──┐   │
        (preview — DEAD 2026-07-23)        │   │
        │                                  │   │
        │                                  ▼   │
        │                     POST /presets ───┘   ◄── ?loadedPresetId=pre__<id>
        │                                          (URL re-opens preset in new cc_<id>)
        │
        │ confirm-ai-onboarding-output
        │
        ▼
   create-cpj-table ─→ {tableId, viewId, workbookId, sourceId}
```

### What's NOT yet captured

- Companies-side equivalents (the user only walked the People flow on 2026-04-30; the actionKey + basicFields for `cpjConfig.type: "companies"` need a separate capture session driving the Companies tab)
- Preset UPDATE flow (saving over an existing search)
- Preset DELETE flow (removing a saved search)
- The 3 `POST /v3/workspaces/{ws}/resources_v2/` calls during the table-picker dialog have NOT had their body shapes individually documented — they appear to be the resource-picker's lookup/search calls but the exact contract is unverified.
- The audience-import job-status polling endpoint (referenced via `jobId: "<numeric>"` in Flow E step 1's response) — Clay's UI presumably has a status-poll loop, but we didn't capture it during the smoke. Likely something like `GET /workspaces/{ws}/audiences/imports/{jobId}` or a generic job-status endpoint.

---

## Workspace / auth-account endpoints (newly added 2026-04-30)

Wrapped in claycast by `get_auth_account`, `list_auth_accounts_by_type`, `list_auth_account_types`, `get_auth_account_type`, `validate_auth_credentials`, `list_workspace_users`, `get_workbook_overview`, `list_trigger_definitions`, `list_agent_configs`, `list_source_runs`, `get_dynamic_action_fields`. See `clay_client.py` for full docstrings.

| Endpoint | ClayCast method | Notes |
|---|---|---|
| `GET /workspaces/{ws}/app-accounts/accounts/{aa_id}` | `get_auth_account` | Get one connected auth account by id |
| `GET /workspaces/{ws}/app-accounts/accounts/type/{type}` | `list_auth_accounts_by_type` | List accounts of a single integration type |
| `GET /app-accounts/types` | `list_auth_account_types` | All available integration types (no workspace) |
| `GET /app-accounts/type/{type}` | `get_auth_account_type` | Type metadata + auth methods |
| `POST /app-accounts/{type}/validate-auth` | `validate_auth_credentials` | Calls `<type>-validate-auth` action; cost typically 0 — verify via `actionMetadata.upfrontCreditUsage` |
| `GET /workspaces/{ws}/users` | `list_workspace_users` | Workspace members + roles |
| `GET /{ws}/workbooks/{wb}/overview` | `get_workbook_overview` | Richer than `get_workbook` — returns `{nodes, edges}` where each node is a table (with field counts, send-data fields) and `edges` describes the workbook DAG (which tables send data to which). Note path uses `/{ws}/`, not `/workspaces/{ws}/`. |
| `GET /workspaces/{ws}/trigger-definitions-with-schedule` | `list_trigger_definitions` | Read side of `feature-gaps.md` #3 (Run scheduling). Create / pause / delete still missing. |
| `GET /{ws}/agent-configs` | `list_agent_configs` | Note path uses `/{ws}/`, not `/workspaces/{ws}/`. |
| `GET /sources/{source_id}/runs` | `list_source_runs` | Webhook source run history. Clay requires `limit` as a query param (calls without it return HTTP 400 `"Field 'limit' - Expected number, received nan"`); claycast defaults to `limit=50`. |
| `POST /workspaces/{ws}/actions/dynamicFields` | `get_dynamic_action_fields` | Resolves dynamic dropdowns for actions. Body: `{dynamicRequests: [{actionPackageId, actionKey, authAccountId, parameterPath, type, inputs, tableId}, ...]}`. Useful before `apply_preset` to learn valid values for dependent inputs. |

---

## Reference Files

> This repo contains the API reference and Python client only. Column definition JSON examples can be found in `clay-api-reference.md` inline. Build your own table schemas by adapting the patterns documented in the sections above.

---

## Gotchas

Lower-risk footguns trimmed out of `SKILL.md` (the top-3 highest-risk ones remain there: the 2-step records flow, `dataTypeSettings: {"type": "text"}`, and `actionKey: "use-ai"`). All of the items below are things that silently misbehave against Clay's internal API.

- **HTTP API `queryString` and `headers`** must use `formulaMap`, not `formulaText` — `formulaText` splits the JSON character-by-character. Verified 2026-04-23: a `formulaText` value of `'{"q": hello}'` produced `?0={&1="&2=q&3="&4=:&5= &6=h...` when sent. Cell previews (`"Status Code: 200"`) can mask the bug if the target accepts any GET; inspect `externalContent.fullValue` via `GET /tables/{t}/records/{r}` to verify what Clay actually sent.
- **Formula columns:** create as `text` first, then PATCH with `formulaText` + `formulaType: "text"`. Creating with the formula in one shot drops it.
- **`answerSchemaType`** requires `formulaMap`; `jsonSchema` must be double-JSON-encoded; `_metadata.modelSource` needs inner quotes: `'"user"'`.
- **Formula string ops:** `.indexOf()` and `.includes()` are unreliable — use `/pattern/i.test(String({{f_id}}) || "")`.
- **Lookup columns** use a `fields|` prefix on filter inputs: `fields|targetColumn`, `fields|filterOperator`, `fields|rowValue`. Extractor mechanics (verified 2026-07-24): the CELL value is only the preview string `"✅ Record Found"` (`metadata.isPreview`); the real payload is formula-visible only, shaped `{"record": {"<Column Name>": value, ...}}` keyed by COLUMN NAMES. Bracket-key access works in the formula engine (`{{f_lookup}}?.record?.["Target Column Name"]`); `mappedResultPath` on a formula PATCH does NOT take effect. Lookup execution is 0 credits. Details: action-registry.md → lookup-row-in-other-table.
- **Webhook source tables** need formula extractors — incoming columns are not auto-populated; PATCH each downstream column with `formulaText` + `formulaType`.

---

---

## View filter/sort write path + replication side-effects (discovered 2026-07-21, workbook-replica build)

- **`PATCH /v3/tables/{t}/views/{v}` and `POST /v3/tables/{t}/views` both return 200 but SILENTLY DROP `filter` and `sort`** in the body. The working write paths are the sub-endpoints: `PATCH /v3/tables/{t}/views/{v}/filter` with the filter object (`{"items": [...], "combinationMode": "AND"}`) and `PATCH /v3/tables/{t}/views/{v}/sort` with `{"items": [{"fieldId", "direction"}]}`. Both verified live.
- The view PATCH **does** accept `name` and a `fields` dict, but only `isVisible` from per-field configs applies; `order` strings are ignored (server owns fractional ordering). Bulk `PATCH /v3/tables/{t}/views/{v}/fields` accepts `{fid: {"isVisible": bool, "width": int}}` — width verified persisting.
- `reorder-fields` rejected full-view blocks in practice (400 on spreadsheet tables, 500 on people tables). Reliable fallback: per-field `move_field` walk with an in-memory simulation of the current order (334 moves across 8 views, 0 failures).
- **Field-group create returns `{"groupId": "gr_..."}`** — not `id`. Clay defaults the LAST member as output field; fix flags via the group update call.
- **`POST /v3/sources/create-cpj-table` side effects & validation:** (a) validates the company scope at create — the company table must contain ≥1 row whose bound column resolves to a real Clay company, else `400 "None of the company table rows resolved"`; a failed attempt STILL creates an orphan `Update People Search (...)` trigger column on the company table. (b) On success it auto-creates that companion trigger column too. (c) Passing `disableTriggerOnCreate: true` + `disableTriggerOnUpdate: true` inside `cpjConfig.typeSettings` prevented the initial search run on all 5 real creations (source `state` stayed `{}`), and subsequent `PATCH /v3/sources/{id}` (accepts `{"name"}` and `{"typeSettings"}`) did not trigger runs either — used to restore `inputs.limit` after a neutered create.
- **Creating a `route-row` action column auto-creates the full receiving pipeline on the target table**: a `manual`/routing source named `Rows from: <sender table name>`, a source column, and extractor formula columns for every `rowData` key. When replicating a workbook, KEEP these auto structures (they hold the sender binding) and delete/repoint any manually created duplicates.
- `POST /v3/sources` works fine for `type: "manual"` routing sources (`{"workspaceId", "tableId", "name", "type", "typeSettings": {"type": "routing"}}`); the "Invalid subscriptions" 404 applies to people/company search sources only.
- `POST /v3/workbooks` accepts `parentFolderId` at create (honored), but `settings: {"isAutoRun": false}` in the create body is NOT persisted — the response echoes `settings: {}` (verified 2026-07-23). The effective dark control is per-table: `PATCH /v3/tables/{t}` with `{"tableSettings": {"AUTO_RUN_ON": false}}`. Workbook settings PATCH path is `PATCH /v3/{ws}/workbooks/{wb}` (NOT `/v3/workbooks/{wb}`, which 404s) — used for `settings.tablePresentationSettings` (table order in the sidebar, `{table_id: index}`). The same workspace-scoped path pattern applies to Terracotta workflows — see "Terracotta (tc-workflows) metadata endpoints" below.
- **Wrapped in claycast (2026-07-21):** the whole view surface above is now SDK methods — `list_views`, `create_view` (applies filter/sort via the sub-endpoints automatically), `update_view`, `delete_view`, `set_view_filter`, `set_view_sort`, `set_view_fields` (visibility+width), `set_view_field_order` (`move_field` walk) — plus `preflight(table_id=)` for the write-restricted-cookie check. All live-smoke-tested (create→configure→reorder→rename→delete).
- (`formulaType` requirement on formula PATCH was already documented above — see "PATCH formula requires `formulaType`".) New nuance: editing a formula via PATCH does NOT recompute existing rows; only new rows or input-cell writes trigger evaluation.

---

## Terracotta (tc-workflows) metadata endpoints (discovered 2026-07-24, extended 2026-07-30)

Terracotta workflows follow the same workspace-scoped path pattern as the workbook-settings PATCH above — the unscoped variants (`/v3/tc-workflows/{id}`, `/v3/tc-workflows/runs/{id}`, `/v3/terracotta/workflows/{id}`, `/v3/workflows/{id}`) all 404 (`NoMatchingURL`).

- `PATCH /v3/workspaces/{ws}/tc-workflows/{wf_id}` with `{"name": "..."}` → `200 {"workflow": {...}}` — renames a workflow. Verified live 2026-07-24.
- `GET /v3/workspaces/{ws}/tc-workflows/{wf_id}` → thin metadata object only (`id`, `name`, `workspaceId`, `creatorUserId`, `lastRunAt`, `liveWorkflowSnapshotId`, `createdAt`/`updatedAt` — no node graph). Verified 2026-07-30.
- `GET /v3/workspaces/{ws}/tc-workflows/{wf_id}/runs/{run_id}` → full run object: `runState`, `runStatus`, `trigger`, `dataCreditsUsed`/`actionCreditsUsed`, and per-node outputs (a leaf node's JSON verdict is findable inside it). Verified 2026-07-30.

**Auth verdict (verified 2026-07-30) — these routes are session-cookie/OAuth ONLY.** A Clay
public API key gets `403 Forbidden ("Insufficient permissions")` — keys are unscoped
(`clay api-keys create` takes only `--name`) and only valid for `public/v0`, and the public
API has **zero direct workflow endpoints** (its surface is me/routines/search/tables only).
Consequence: an `http-api-v2` **table column can never read tc-workflow RUN objects**
(cookie-only) — the tempting write-back pattern "capture `workflowRunId` from the webhook
ACK, then GET the run from a column" is impossible, not merely miswired. Never embed the
`CLAY_SESSION` cookie in a column to force it. **BUT a REGISTERED workflow tool can be
run + polled via the public Routines API with an api key** — that's the supported,
column-friendly path (registration recipe below).

No SDK wrapper yet — raw recipe via the escape hatch:

```python
clay.patch(f"/workspaces/{clay.workspace_id}/tc-workflows/{wf_id}", {"name": "New name"})
clay.get(f"/workspaces/{clay.workspace_id}/tc-workflows/{wf_id}/runs/{run_id}")
```

### Node updates — `PATCH .../tc-workflows/{wf}/nodes/{node}` is FULL REPLACEMENT (verified 2026-08-05)

The workflow-node write gap is closed: clay-spy captured the UI's own node-update route
(Sentry headers in the capture confirm it's the app's route; the non-workspace-scoped
guess `/tc-workflows/{wf}/nodes/{id}` 404s):

`PATCH /v3/workspaces/{ws}/tc-workflows/{wf}/nodes/{node}` — body (200 on capture):

```json
{"name": "...", "description": null, "nodeType": "code",
 "scriptVersionId": "...", "inlineScript": {"code": "..."},
 "nodeConfig": {"nodeType": "code", "inputSchema": {...}, "automap_inputs": true,
                "listMode": null, "listEntriesRef": null, "inputRefs": [...],
                "inputMappingConfig": {...}},
 "retryConfig": null, "subroutineIds": [], "toolIds": []}
```

- **`nodeConfig.inputSchema` + `inputRefs` are REPLACED wholesale.** This is the escape
  hatch for the plugin MCP's `edit_node` on CODE nodes, whose inputSchema edits merge
  per-property and can NEVER remove pins or required flags (an explicit
  `{properties: {}, required: []}` returns "noop"). PATCH here with the pin-free schema
  to actually remove wiring.
- **`edit_node` schema semantics differ by node type (refined 2026-08-05):** side by side —
  **code nodes** = merge-and-sticky (per-property merge; pins unremovable, as above).
  **Tool nodes** = replace-on-update for EXISTING inputs: redefining a declared input
  (e.g. repointing a pin's `sourceNodeId`/`sourcePath`) succeeds AND drops the other
  previously-declared schema properties, while the tool's `inputMappingConfig` (the
  actual param bindings) survives untouched. The 2026-07 "Invalid node config" failure
  applies specifically to ADDING new input variables to a tool node. So: pin repoints on
  tool nodes ARE possible via MCP; re-declare the full schema you want in one update.
- Workflow tool-RESULT delivery is string-only for `extract-field-from-object`-style
  extractors — numeric extractions crash the run; see action-registry.md § "extract-json /
  extract-field-from-object extractor tools" (verified 2026-08-05).
- **Caution: full replacement cuts both ways** — send the COMPLETE node (fetch first,
  mutate, PATCH); omitting sections likely clears them. Response echoes the full node
  with a new `scriptVersionId`.

**Multi-trigger workflows: REQUIRED pins to a trigger that didn't fire are FATAL**
(verified 2026-08-05; refined 2026-08-08): `"Input ref resolution failed: no completed
step found for source node"`. A node whose REQUIRED inputs pin to BOTH triggers of a
dual-trigger workflow can never run from either door. Trigger-adjacent nodes must read
trigger inputs by NAME from the spread (no pins) — never pin to trigger nodes.

**Refinement — fatality is scoped to the `required` array (verified 2026-08-05/08):**
- **required + pinned** = hard failure when the pin can't resolve — both flavors:
  `"no completed step found for source node"` (source never executed) and
  `"resolved to undefined"` (source completed but the path is empty).
- **optional + pinned** (input not listed in `required`, or no required list) = the pin
  resolves to missing and the node RUNS. Live proof: a conditional's optional pin to a
  lookup's `$.result.data[0].id` on a lookup-miss run (completed source, undefined path)
  still completed and routed correctly, while REQUIRED pins hard-failed with the errors
  above in the same workflow.
So optional pins are safe for "may be absent" wiring (not-found branches); reserve
`required` for inputs whose absence should abort the run.

### Workspace tools registry — registering a workflow as a public routine (verified 2026-07-30)

The "why do only some workflows appear in `clay routines list`" mystery is solved: routine
visibility = having a record in the **workspace tools registry**. UI/Sculptor-built
workflows get one automatically; CLI/MCP-created workflows do NOT.

- `GET /v3/workspaces/{ws}/tools` → list of tool records:
  `{id, type, name, description, access: {integrations: [...]}, inputSchema}`. Workflow
  tools have `id: "workflow:wf_..."`, `type: "workflow"`; `access.integrations: ["api"]`
  = publicly executable via the Routines API. (Observed: 7 of 11 workspace workflows had
  entries — exactly the UI-built ones.)
- The same registry also holds FUNCTION tools (`id: "function:t_x"`, `type: "function"`)
  — see "Creating a custom function (subroutine table) via API" below for the
  function-side registration nuances (`entityType` required). (verified 2026-07-31)
- `POST /v3/workspaces/{ws}/tools` with a full record body **registers** a workflow:

```python
clay.post(f"/workspaces/{clay.workspace_id}/tools", {
    "id": "workflow:wf_xxx",
    "type": "workflow",
    "name": "My Workflow",
    "description": "What it does",
    "access": {"integrations": ["api"]},
    "inputSchema": {"type": "object",
                    "properties": {"title": {"type": "string"}},
                    "required": ["title"]},
})
```

Verified end-to-end: a CLI-built workflow registered this way became executable through the
public Routines API (api-key auth — the credential a table HTTP column CAN carry):

- `POST https://api.clay.com/public/v0/routines/workflow:wf_xxx/run`
  (header `clay-api-key`), body
  `{"items": [{"id": "<caller-chosen id>", "inputs": {<trigger input fields>}}]}`
  (1–100 items) → `202 {"routine_run_id": "run_...", "status": "in_progress"}`.
- `GET https://api.clay.com/public/v0/routines/run/{routine_run_id}/results`
  (header `clay-api-key`) →
  `{routine_run_id, status: in_progress|complete, total, finished, data: [{id, status,
  result: {...ALL leaf-node outputs incl. structuredOutputs...}}]}`.
  Full leaf output returned; the whole probe cost 0.2 credits.
  **Results-shape nuance (verified 2026-07-30 on both leaf types of one workflow):** the
  per-item `result`'s TOP-LEVEL fields are only reliably populated for **code-node**
  leaves; with an **agent-node** leaf they can come back empty (`reasoning: ""`) while the
  real verdict lives ONLY under `result.structuredOutputs.*` (agent leaves also add a
  `stepsTaken` array). `structuredOutputs` is present on BOTH leaf types, so consumers —
  e.g. table formula extractors — should always read
  `?.data?.[0]?.result?.structuredOutputs?.<field>`, never top-level `result.<field>`.

**Gotcha:** request-body validation runs BEFORE the tool-existence check — an unregistered
tool id returns 400 schema errors when the body is invalid, and only 404
`"Tool workflow:wf_xxx not found"` once the body is valid. Don't read a 400 as "the tool
exists".

**Registry records are effectively immutable (verified 2026-08-05; refined 2026-08-06):**
there is no update route (`PATCH /workspaces/{ws}/tools/{id}` 404s; `POST` on an existing
id 409s) and **no DELETE route either** (`DELETE /workspaces/{ws}/tools/{id}` → 404).
Removal paths: FUNCTION tool records are **cascade-removed when their table is deleted**
(verified 2026-08-06) — for workflow tool records no removal path is known short of
deleting the workflow itself (untested). Consequence: changing a WORKFLOW tool's schema
has no clean route — and it matters, because for WORKFLOW tools the registry
`inputSchema` is the RUNTIME item validator (see the items-layer note in the pattern
below); for FUNCTION tools it's documentation-only (their validator is the table's
`SUBROUTINE_INPUTS`), and a function's record can be recycled via delete-table +
`create_function(register=True)`.

### Pattern: calling a workflow-as-routine from a table (POST run → poll results)

Live-verified 2026-07-30. The full table-side setup for getting a registered workflow's
verdict back into row cells — the supported replacement for the impossible
"GET the tc-workflow run from a column" pattern above. Prereq: workflow registered as a
workspace tool with `access.integrations: ["api"]` (previous section).

**Column 1 — `Call <routine>`** (`http-api-v2` action column, `dataTypeSettings: json`,
and the action's FULL 15-param `inputsBinding` — see "Action columns need the action's
FULL parameter list" above; a partial binding renders NO inputs in the UI):

- `method` `"POST"`, `url` `"https://api.clay.com/public/v0/routines/workflow:wf_xxx/run"`
- `headers` formulaMap:
  `{"Content-Type": '"application/json"', "clay-api-key": '"<clay-public-api-key>"'}` —
  ⚠ this **embeds the public key in column config**, same exposure class as a key pasted
  into workflow node code; acceptable for workspace-internal keys, but know it's there.
- `body` via string-concat + `Clay.formatForJSON` (or `format_json_body({...})`), items
  shape: `{"items": [{"id": <stable per-row id, e.g. the LinkedIn URL>,
  "inputs": {<the workflow's trigger input fields>}}]}`
- optional `conditionalRunFormulaText` gate — with the caveat documented under
  "Conditional Execution": scripted runs skip silently; `force_run` bypasses.
- Response lands as json `{routine_run_id, status: "in_progress"}` (cell preview shows
  `"Status Code: 202"` — read the json via `?.` extractors, not the preview).

**Column 2 — `Run Id`** (formula, text): `{{call_col}}?.routine_run_id`

**Column 3 — `Results`** (`http-api-v2`, `dataTypeSettings: json`): `method` `"GET"`,
`url` `"https://api.clay.com/public/v0/routines/run/" + {{run_id_col}} + "/results"`,
`headers` `{"clay-api-key": ...}` (same full-binding rule).

**Columns 4+ — verdict extractors** (formula):
`{{results_col}}?.data?.[0]?.result?.structuredOutputs?.<field>` — never top-level
`result.<field>` (agent-leaf nuance above: top-level fields can be empty while the
verdict lives only under `structuredOutputs`).

Operational notes:

1. **RACE:** the results GET can catch `status: "in_progress"` with empty `data` — re-run
   the Results column after the routine completes. On dark tables sequence manually (run
   Call → wait → run Results); with AUTO_RUN the dependency cascade fires automatically
   but the race remains.
2. **Cost:** one run of a two-node router (table lookup + nano-LLM fallback) + the two
   `http-api-v2` executions ≈ **2 credits/row** — cost scales with the workflow being
   called; estimate before bulk runs.
3. `items` supports 1–100 per POST; the per-row column pattern sends 1.
4. **Items-layer input semantics (verified 2026-08-05):** empty-string input values are
   STRIPPED before the workflow spread — they vanish from the trigger payload entirely, so
   pins / `$.key` refs resolve undefined. JSON `null`s are REJECTED by the registered tool
   schema's types (`"phone: must be string"`). Whitespace-only strings (`" "`) survive
   both filters — use them (or a sentinel like `"NONE"`) when a field must arrive.

### Creating a custom function (subroutine table) via API (verified 2026-07-31)

Custom Clay FUNCTIONS (the UI's "function" tables; routine id `function:t_x`) can be
created entirely via the internal API — closing most of feature-gaps.md § 6. A function
is not a special object; it's a **plain `spreadsheet` table with subroutine
tableSettings** plus a subroutine input source and a tools-registry entry.

**Wrapped (2026-08-06):** `clay.create_function(...)` performs the whole recipe below
end-to-end and `clay.register_tool(...)` handles step 3 for both workflows and functions.
Smoke-tested live: created → registered → public routine run 202 → row landed →
extractor computed → cleanup. Two facts from that cleanup:

- There is **NO registry DELETE route** — `DELETE /workspaces/{ws}/tools/{id}` → 404.
- **Deleting a function's table cascade-removes its tools-registry entry** — function
  tools are cleanly removable via table deletion. (Whether workflow deletion cascades
  the same way is untested.)

**1. The table** — create normally (`POST /tables` / claycast `create_table`), then PATCH
`/tables/{id}` with:

```python
{"tableSettings": {
    "BLOCK_TYPE": "SUBROUTINE",
    "BLOCK_SETTINGS": {"blockType": "SUBROUTINE"},   # managed ones add isClayManaged: true
    "SUBROUTINE_INPUTS": [
        {"inputName": "domain", "optional": False, "semanticTypeEnum": "company-domain"},
        {"inputName": "note",   "optional": True},
    ],
    # output mapping (which field(s) the function "returns"):
    "IS_PASS_THROUGH_TABLE": True,
    "PASS_THROUGH_TABLE_SUCCESS_FIELD_IDS": ["f_result_col"],
}}
```

**2. Input delivery** — a subroutine source + source field:
`POST /sources` with `{type: "manual", typeSettings: {type: "subroutine"}}`, then a
`source`-type field on the table with `sourceIds: [s_id]`. Run inputs land in that field
as an object; formula extractors (`{{src_field}}?.input_name`) compute on arrival.
Managed functions reference it via a reserved `{{f_subroutine_source}}` token in bindings.

**3. Registration is NOT automatic** — same tools registry as workflows (section above):
`POST /v3/workspaces/{ws}/tools` with
`{id: "function:t_x", type: "function", entityType: "contact"|"company"` (**REQUIRED** —
validation names it)`, name, access: {integrations: ["api"]}, inputSchema}`. After that,
`POST /public/v0/routines/function:t_x/run` returns 202 and the item lands as a row.

**4. Return path (managed pattern)** — a `write-to-cell` action column (package
`b1ab3d5d-b0db-4b30-9251-3f32d8b103c1`) bound to
`{{f_subroutine_source}}?.origin?.recordId/tableId/fieldId/asyncCallbackId` writes results
back to the ORIGIN cell for table-column invocations; the pass-through success fields
drive API results.

**Gotchas (the valuable part):**

- **Input types are SCALAR-ONLY** (verified 2026-07-31). Runtime validation derives from
  `SUBROUTINE_INPUTS`, defaulting to string; sending an object → `400 "Expected type
  string"`. Observed `semanticTypeEnum` vocabulary from managed functions:
  `company-domain, company-linkedin-url, company-name, date, unknown,
  person-linkedin-url, email`. There is NO json/object type.
- **⚠ Invalid `semanticTypeEnum` values (e.g. `"json"`, `"object"`) don't just fail —
  they 500 the ENTIRE workspace tools registry endpoint** (`GET /workspaces/{ws}/tools`)
  until reverted, degrading routines infrastructure workspace-wide. Never guess enum
  values on a live workspace. (verified 2026-07-31)
- Stringified-JSON inputs are accepted and land, but Clay formulas can NOT parse JSON
  strings (`?.` access returns nothing) — a JSON-string input is archival only.
- **Extra undeclared inputs are tolerated** (202) — callers can send a rich flat input
  set while each function declares only the subset it consumes; contracts can grow
  without breaking existing functions.
- **Pass-through completion unresolved (needs verification):** on a minimal probe
  (formula-only success field, dark table) the routine run stayed `in_progress`
  indefinitely even though the row landed and extractors computed. Completion plumbing
  likely requires the managed-style send-back/async-callback wiring or a non-dark run
  path.
- The registry `inputSchema` is documentation-only **for FUNCTION tools** — their runtime
  validation comes from the table's `SUBROUTINE_INPUTS`. (WORKFLOW tools are the
  opposite: the registry record IS the runtime item validator — refined 2026-08-05, see
  the tools-registry section above.)

**Aside — official CLI structured query (needs verification, observed 2026-07-30):**
`clay tables query` requires `tables: [{"id": ...}]` (not `tableId` — validation error names
`query.tables.0.id`). One session saw filters `{fieldName, operator: "CONTAIN", value}`
return apparently-unfiltered records once, then 0 on retry — the filter syntax is unverified;
for ad-hoc searches over big tables, paging `list_records` and filtering client-side was the
reliable path.
