# ClayCast Feature Gaps — What's Missing for Headless Clay Productivity

Snapshot of capabilities users commonly need for **mostly-headless Clay workflows** (lead sourcing, enrichment pipelines, scheduled refreshes, CRM export) that claycast does NOT currently implement. This is a gap list, not a roadmap commitment — anything here is an opportunity, not a bug.

**When to consult this doc:** if a user asks "can claycast do X?" and X isn't in the capability map in SKILL.md, check here before saying "no, use the Clay UI." Some gaps have achievable workarounds; others genuinely block automation.

**How this doc was generated:** analyzed Clay's primary use cases against the claycast SDK surface after the clay_record_writer_v2 port, the WIP-new 8-tool port, and the WIP-merge export/inspection port. Reviewed 2026-04.

## Recently closed

- `CLOSED 2026-04-24:` lightweight structured schema + sample inspection is now available via `clay.inspect_table(...)`.
- `CLOSED 2026-04-24:` local row export is now available via `clay.export_rows(...)`.
- `CLOSED 2026-04-24:` local workspace export is now available via `clay.export_workspace(...)`.
- `CLOSED 2026-04-24:` local JSON export-artifact search is now available via `clay.search_export_artifacts(...)`.
- `CLOSED 2026-04-24:` preset / enrichment-catalog browsing is now available via `clay.list_preset_categories(...)`, `clay.list_presets_filtered(...)`, `clay.list_presets_by_category(...)`, `clay.list_disabled_actions(...)`, `clay.list_starred_resources(...)`, `clay.get_resource_star(...)`, plus `rewrite_preset_placeholders(...)`.
- `PARTIAL 2026-04-30:` Run scheduling read-side now available via `clay.list_trigger_definitions(...)` (gap #3). Create / pause / resume / delete schedules still missing.
- `CLOSED 2026-04-30:` auth-account inspection (resolves `auth_account_id` for `apply_preset` and `create_action_column`) is now available via `clay.get_auth_account(...)`, `clay.list_auth_accounts_by_type(...)`, `clay.list_auth_account_types(...)`, `clay.get_auth_account_type(...)`, `clay.validate_auth_credentials(...)`.
- `CLOSED 2026-04-30:` workspace-metadata helpers added — `clay.list_workspace_users(...)`, `clay.get_workbook_overview(...)`, `clay.list_agent_configs(...)`.
- `CLOSED 2026-04-30:` source run history added — `clay.list_source_runs(source_id, limit=...)`.
- `CLOSED 2026-04-30:` dynamic action-field resolution added — `clay.get_dynamic_action_fields([...])`. Lets callers learn valid options for action dropdowns (e.g. Salesforce `object_type`) before invoking `apply_preset` / `create_action_column`.
- `CLOSED 2026-04-30:` Find People AND Find Companies sourced-table creation is now available via `clay.preview_sourced_table(...)` + `clay.create_sourced_table(...)`. Both `cpj_type="people"` and `cpj_type="companies"` are live-verified (workspace 12345).
- `CLOSED 2026-04-30:` credit-usage / spend reporting is now available via `clay.get_credit_usage(...)`, `clay.get_table_credit_usage(...)`, and `clay.get_default_workbook_credit_limit(...)`. The UI's client-side credit-usage CSV recreation remains intentionally deferred.
- `CLOSED 2026-04-30:` audience-segment export is now available via `clay.list_audience_segments(...)`, `clay.count_audience_segment(...)`, and `clay.export_audience_segment(...)`.

---

## Tier 1 — Actually blocks common headless workflows

### 1. Views CRUD

ClayCast references `view_id` everywhere but has no `create_view` / `update_view` / `delete_view`. Clay users build views constantly — "errored rows," "qualified leads," "missing email," "ICP only" — and those views are how downstream operations get scoped (rerun_errors, export, filtered runs). Without a views API, users either use the default view (too broad) or open the Clay UI to create filters manually.

**Impact:** this is the biggest hole for automation. Most real pipelines want "enrich only rows matching X" or "export only rows where Y" — both require views.

**Proposed surface:**
- `create_view(table_id, name, filters=..., sort=..., visible_fields=...) → view dict`
- `update_view(view_id, ...)`
- `delete_view(view_id)`
- `list_views(table_id)`

`CLOSED 2026-07-21:` wrapped as `clay.list_views` / `create_view` / `update_view` / `delete_view` / `set_view_filter` / `set_view_sort` / `set_view_fields` / `set_view_field_order`, live-smoke-tested end-to-end. `create_view` routes filter/sort through their sub-endpoints automatically.

**Endpoint discovery notes (2026-07-21):** `POST /tables/{t}/views` creates, `DELETE /tables/{t}/views/{v}` deletes, view PATCH renames; `filter`/`sort` are ONLY settable via `PATCH /tables/{t}/views/{v}/filter` and `.../sort` (view PATCH/POST silently drop them); visibility+width via bulk `PATCH .../views/{v}/fields`; whole-view column order via per-field `move_field` walk (`reorder-fields` rejects full-view blocks). Details: clay-api-reference.md → "View filter/sort write path + replication side-effects". The gap is now "wrap it", no longer "discover it".

### 2. "Find People" / "Find Companies" sourced-table creation

THE primary lead-sourcing flow in Clay. The user supplies filters (industry, headcount, geography, tech stack); Clay populates rows from a source-query action. ClayCast's `create_action_column` adds actions to EXISTING tables — creating a new table whose rows come from a source query is a different endpoint (`POST /v3/sources/create-cpj-table`) and different semantic.

**Status as of 2026-05-01:** `CLOSED` for both People AND Companies variants. ClayCast wraps the flow via `clay.preview_sourced_table(...)` and `clay.create_sourced_table(...)` for either entity type. The full request/response contract — `cpjConfig`, `basicFields`, the captured inputs schema for both entity types — is documented in `clay-api-reference.md` → "Find People / Find Companies sourced-table creation".

**Impact:** headless Find People AND Find Companies sourcing is now available directly in ClayCast. This section remains as contract/reference history because the captured body shape is non-trivial.

**Proposed surface (now implemented):**
- `create_sourced_table(name, *, inputs, cpj_type="people"|"companies", basic_fields_override=None, workbook_id=None, destination_table_id=None, conversation_id=None, preview_action_task_id=None, workspace_id=None) → dict` returning `{tableId, viewId, workbookId, sourceId, isNewTable}`
- Wraps `POST /v3/sources/create-cpj-table` directly (no need to walk the chat-conversation UI flow — `conversation_id=None` works for direct creation).
- The `typeSettings` (actionKey, basicFields, recordsPath, idPath) is hardcoded per `cpj_type` so callers only pass filter inputs.

**Preview-without-saving:** `POST /v3/actions/run-enrichment` with `enrichmentType: "<actionKey>-preview"` returns `{result: {people|companies: [...rows...], peopleCount|companiesCount: N}, metadata: {additionalCreditCost: 0}, ...}` for ANY filter combination at 0 credits. Verified live 2026-05-01, Clay hard-caps preview at exactly `50`; ClayCast raises before network on `limit > 50`. Useful for "validate filters cheaply before committing to a 50K-row table."

**Subtle asymmetry:** the body uses `cpjConfig.type = "companies"` (plural) but `clientSettings.tableType = "company"` (singular). ClayCast maps this internally via `_CPJ_CLIENT_TABLE_TYPE`. Companies now default the `Size` starter column to plain text so ClayCast does not depend on frontend-captured select-option UUIDs in the normal path; callers who explicitly want the legacy chip-style Size column can opt in via `from clay_client import companies_basic_fields_with_select_size` plus `basic_fields_override=companies_basic_fields_with_select_size()`.

### 3. Run scheduling / recurring triggers

ClayCast has `run_column` for on-demand runs. Clay has **scheduled triggers** ("refresh this enrichment weekly," "run at 9am daily") that live inside workflow definitions. ClayCast has no method to create or manage them. For genuine unattended automation, users need external cron + SSH + `run_column` calls — which defeats "headless in-tool."

**Impact:** the difference between a manual tool and a real automation platform.

**Proposed surface (requires Clay endpoint reverse-engineering):**
- `create_schedule(table_id, field_ids, cron_expression, *, active=True)`
- `list_schedules(table_id)`
- `pause_schedule(schedule_id)` / `resume_schedule(schedule_id)` / `delete_schedule(schedule_id)`

---

## Tier 2 — High value, workaround-able with existing primitives

### 4. Waterfall column helper

Clay's "waterfall" pattern (try Source A → fallback B → fallback C, stop on first hit) is heavily used for enrichment cost control. ClayCast can build it via `create_action_column` manually, but there's no `create_waterfall_column(sources=[a, b, c], stop_on_first_hit=True)` convenience method. Users must know the waterfall-binding payload shape.

### 5. Integration "finisher" methods

ClayCast can wire Instantly / Apollo / HubSpot / HeyReach action columns via `create_action_column`. But there's no high-level convenience:

- `push_to_instantly(table_id, campaign_id, *, ...)` 
- `sync_to_hubspot(table_id, list_id, *, ...)`
- `export_to_apollo(table_id, *, ...)`

The "enrich then export to CRM" flow is where most real pipelines end. Users have to hand-assemble bindings + know the action's `inputsBinding` schema.

### 6. Subroutine execution

ClayCast has `list_subroutines` but no `run_subroutine(subroutine_id, inputs=...)`. Subroutines are Clay's pre-built multi-step flows — big productivity unlock when someone's shared a good one.

### 7. Incremental / delta sync

"Rows updated since timestamp X" or "new rows added since last sync." For external CRM syncs you need this or you push everything every time. Currently possible via `list_records` + client-side timestamp filtering, but painful for large tables.

**Proposed surface:**
- `list_records_since(table_id, view_id, since_timestamp, *, limit=...)` — backed by `updatedAt >= since_timestamp` filter or Clay's diff API if one exists.

### 8. Credit-usage / spend reporting + workbook credit limits

**Status as of 2026-04-30:** `CLOSED` for the read-side. ClayCast now wraps the top-level usage reports, per-table drill-down, and default workbook credit-limit read via `clay.get_credit_usage(...)`, `clay.get_table_credit_usage(...)`, and `clay.get_default_workbook_credit_limit(...)`.

**Why it matters:** Clay's UI exposes rich credit-spend analytics (per-workbook, per-integration, per-signal, per-trigger, per-MCP-server, per-API-key) plus per-table drill-down (time-series, by-column, by-run breakdowns). For ops dashboards, cost-attribution to teams or campaigns, or anomaly detection, you'd want to query this data programmatically — and currently you'd have to. Plus the default-credit-limit endpoint is the only safe path for governance ("set default of X credits for any new workbook so a runaway pipeline can't burn the workspace").

**Endpoints captured live 2026-04-30 in workspace 12345:**

#### A. Top-level credit reporting (the 6 tab views)

```
GET /v3/credit-reporting/{ws}/creditReportType/{type}
    ?timeRange[startTime]=<ISO>
    &timeRange[endTime]=<ISO>
    &<optional filter params — see below>
```

**`{type}` values (6 variants, one per Usage subtab):**

| Type | UI tab | Returns |
|---|---|---|
| `workspace` | Workbooks | Folder/workbook tree with `subentities` (hierarchical) |
| `integration` | Integrations | Per-integration credit/action breakdown |
| `signal` | Signals (one of two requests fired) | Per-signal breakdown |
| `triggerDefinition` | Signals (other request fired alongside) | Per-trigger-definition breakdown |
| `mcp` | MCP | Per-MCP-server breakdown |
| `api` | API | Per-API-key breakdown |

**Total tab is special:** parallel-fires all 6 of the above and aggregates client-side.

**Response shape (verified for `workspace` type; others likely analogous):**

```json
{
  "entities": [
    {
      "id": "f_<id>",                                     // folder id (or wb_<id> at next level)
      "entity": {"name": "TRA", "isDeleted": false, "__kind": "folder"},
      "credits": 15109.5,
      "actionExecutions": 12266,
      "subentities": [
        {"id": "wb_<id>", "entity": {"name": "JR: Scoring WIP", "__kind": "workbook"}, "credits": ..., "actionExecutions": ..., "subentities": [...]}
      ]
    }
  ],
  "unattributedCredits": 0,
  "unattributedActionExecutions": 0
}
```

Hierarchical: `entities` are top-level (folders), each has `subentities` (workbooks), which can have further sub-entities (tables/sources). `unattributed*` is the workspace-level overflow not attributable to any folder.

**Optional filter query params (all verified live on `creditReportType/workspace`; same params likely valid for the other 5 types):**

| Filter | Param syntax | Notes |
|---|---|---|
| Recurring only | `isRecurringOnly=true` | Boolean toggle |
| Has credit limit | `hasCreditLimit=true` | Boolean toggle |
| Owner multi-select | `ownerIds[0]=<userId>`, `ownerIds[1]=...` | Numeric workspace-user IDs (from `list_workspace_users()`); bracket-array syntax |
| Integration multi-select | `integrations[0]=<actionPackageId-UUID>`, `integrations[1]=...` | Action-package UUIDs from `list_actions()` / `list_auth_account_types()`; bracket-array syntax. NOT integration display names. |

Bracket-array syntax (`key[0]=v1&key[1]=v2`) needs explicit encoding — `requests` library's `params=` dict will URL-encode `[` and `]` but won't generate the array indexes; callers either pre-encode or use a list-of-tuples.

#### B. Per-table drill-down (drilldown view when you click a workbook → table)

Three parallel-fired endpoints, one per radio aggregation (Time / Column / Run):

```
GET /v3/realtime-credit-usage/{ws}/table/{tableId}/time
    ?timeRange[startTime]=<ISO>
    &timeRange[endTime]=<ISO>
    &timeAggregationUnit=day
    &includeActionBreakdown=false

GET /v3/realtime-credit-usage/{ws}/table/{tableId}/column
    ?timeRange[startTime]=<ISO>
    &timeRange[endTime]=<ISO>

GET /v3/realtime-credit-usage/{ws}/table/{tableId}/run
    ?timeRange[startTime]=<ISO>
    &timeRange[endTime]=<ISO>
```

The `time` variant additionally accepts `timeAggregationUnit` (`day` observed; `hour`, `week`, `month` likely also valid but unverified) and `includeActionBreakdown` (`false` observed; `true` likely toggles per-action detail).

**Important client-side behaviors verified live:**

- The Time / Column / Run radio is **purely client-side**. All three endpoints fire on initial page load; toggling the radio just re-renders cached data. Only date-range changes trigger refetches (and only of the active radio's endpoint).
- The "Download CSV" button is **purely client-side**. No CSV-export API endpoint exists. The frontend (re)fetches the three `realtime-credit-usage/...` calls + a fields-metadata fetch (`GET /workspaces/{ws}/tables/{t}/fields?fieldIds=<csv>&includeDeleted=true`) and assembles the CSV in JavaScript via blob URL.

#### C. Default workbook credit-spend limit (configuration, not usage)

```
GET /v3/workspaces/{ws}/default-credit-limits?appliesTo=workbook
→ {"defaultLimit": 15000, ...}
```

`appliesTo=workbook` is the only value observed; whether other scopes exist (e.g., `appliesTo=table`) is uncaptured.

The "Manage Default Limit" button in the Workbook limits tab presumably writes to a corresponding PUT/PATCH on this endpoint — not yet captured.

#### D. URL state

Every Usage subtab updates the URL with `?usageTab=<value>` for deep-linking. Observed value: `usageTab=workspace` for the Workbooks tab. Likely other values map to the corresponding `creditReportType` strings.

**Proposed claycast surface (3 methods):**

```python
def get_credit_usage(
    self,
    *,
    report_type: str = "workspace",
    # workspace | integration | signal | triggerDefinition | mcp | api
    start_time: datetime | str,
    end_time: datetime | str,
    owner_ids: list[int] | None = None,
    integration_ids: list[str] | None = None,        # actionPackageId UUIDs
    is_recurring_only: bool = False,
    has_credit_limit: bool = False,
    workspace_id: int | str | None = None,
) -> dict:
    """
    Get Clay's Settings → Usage data for any of the 6 report types.

    Returns:
        {
          "entities": [
            {"id": <id>, "entity": {"name", "__kind", ...},
             "credits": <float>, "actionExecutions": <int>,
             "subentities": [...]},
            ...
          ],
          "unattributedCredits": <float>,
          "unattributedActionExecutions": <int>,
        }

    The 6 report_type values map to the 6 Usage subtabs in the Clay UI:
        workspace          → Workbooks tab (folder/workbook tree)
        integration        → Integrations tab
        signal             → Signals tab (signals)
        triggerDefinition  → Signals tab (triggers; UI fires both signal+triggerDefinition)
        mcp                → MCP tab
        api                → API tab
    """

def get_table_credit_usage(
    self,
    table_id: str,
    *,
    aggregation: str = "run",                       # "time" | "column" | "run"
    start_time: datetime | str,
    end_time: datetime | str,
    time_aggregation_unit: str = "day",             # only used when aggregation="time"
    include_action_breakdown: bool = False,         # only used when aggregation="time"
    workspace_id: int | str | None = None,
) -> dict:
    """Per-table credit-usage drill-down. Three aggregations available."""

def get_default_workbook_credit_limit(
    self, *, workspace_id: int | str | None = None
) -> dict:
    """Get the workspace-level default credit-spend limit applied to new workbooks.

    Endpoint: GET /v3/workspaces/{ws}/default-credit-limits?appliesTo=workbook
    Returns the configured default-limit value. Workspaces with no default
    explicitly set may return null/0/missing — verify response shape per workspace.
    """
```

**Bonus convenience method (composes the per-table drill-down + builds CSV like the UI):**

```python
def export_table_credit_usage_csv(
    self, table_id: str, *,
    start_time, end_time,
    output_dir: str | None = None,
    filename: str | None = None,
    workspace_id: int | str | None = None,
) -> dict:
    """
    Replicate the Clay UI's Download CSV button for per-table credit usage.

    Calls all three /realtime-credit-usage/.../{time, column, run} endpoints
    and the field-metadata endpoint, assembles a CSV locally with the same
    layout the UI produces, writes to <project_root>/tmp/clay-artifacts/.
    Returns {payload, artifact_path}.
    """
```

**Implementation notes for the bracket-array filter encoding:**

```python
def _encode_filters(*, owner_ids, integration_ids, is_recurring_only, has_credit_limit, start_time, end_time):
    params = [
        ("timeRange[startTime]", _to_iso(start_time)),
        ("timeRange[endTime]", _to_iso(end_time)),
    ]
    if is_recurring_only:
        params.append(("isRecurringOnly", "true"))
    if has_credit_limit:
        params.append(("hasCreditLimit", "true"))
    for i, oid in enumerate(owner_ids or []):
        params.append((f"ownerIds[{i}]", str(oid)))
    for i, iid in enumerate(integration_ids or []):
        params.append((f"integrations[{i}]", str(iid)))
    return params  # pass to requests as params=[...]
```

`requests` will URL-encode the `[`/`]` characters but generate the right index sequence when given a list-of-tuples.

**What's NOT yet captured (would need a follow-up walkthrough):**

- The Manage Default Limit write endpoint (PUT/PATCH on `default-credit-limits`?)
- The Export button on the Workbooks tab (currently disabled in our captures)
- Filter param shapes specifically on the `integration`/`signal`/`triggerDefinition`/`mcp`/`api` types (only `workspace` was verified; same params likely apply but not proven)
- `timeAggregationUnit` valid values beyond `day` (`hour` / `week` / `month` are conjectures)
- `includeActionBreakdown=true` response shape
- `appliesTo` valid values beyond `workbook` for `default-credit-limits`
- Custom-range date picker mechanics (programmatic equivalent is just passing `start_time` / `end_time`, but the UI's calendar widget wasn't exercised)
- Other Settings sidebar sections (Workspace, AI context, Team, Connections, Web intent, Plan & billing, Referrals, Enrichments, Ads — 9 unexplored)

**Tier:** 2 (high value, workaround-able). Workaround is hand-rolling the URL with bracket-array params, which is doable but undiscoverable from claycast's capability map and easy to get wrong (the workspaceId-must-be-string gotcha from `validate_auth_credentials` doesn't apply here, but bracket-array encoding is its own footgun).

**Walkthrough notes:** detailed UI walkthrough at `/tmp/credit-usage-walkthrough-notes.md` (workspace 12345, captured 2026-04-30) — has the verified filter-param schema, the radio-toggle / Download-CSV client-side findings, and the URL-state observations.

### 9. Audience-segment export (the only path to >50K rows)

**Status as of 2026-04-30:** `CLOSED`. ClayCast now exposes `clay.list_audience_segments(...)`, `clay.count_audience_segment(...)`, and `clay.export_audience_segment(...)` for direct audience-layer export without routing through a 50K-capped table materialization step.

**Why it matters:** The audience layer is the **only path that scales beyond 50K rows** (Clay-confirmed unlimited capacity 2026-04-30). To get audience data into local CSV/JSON form, today's only options are:
1. Run Flow F (`audiences/create-source`) to materialize a 50K-capped table from the segment, then export THAT table — bottlenecked at 50K per export
2. Hand-roll a pagination loop using `POST /workspaces/{ws}/audiences/contacts` (or `/accounts`) with `segmentId` filter — works for unlimited segments but is tedious and undocumented

For workflows where the audience already holds 100K+ leads (Find People → Save to People is the natural source of these), neither option is convenient.

**Native Clay UI doesn't help.** The `GET /v3/workspaces/{ws}/audiences/{TYPE}/exports?segmentId=<aud>` endpoint surfaces only two export types — `TABLE` (= Flow F, 50K-capped) and `EMAIL_CAMPAIGN`. There is **no native CSV export** for audience segments.

**Proposed claycast surface:**

```python
def export_audience_segment(
    self,
    segment_id: str,
    *,
    entity_type: str = "CONTACT",       # "CONTACT" | "ACCOUNT"
    format: str = "csv",                # "csv" | "json"
    limit: int | None = None,           # None = entire segment, no cap
    output_dir: str | None = None,
    filename: str | None = None,
    page_size: int = 300,               # rows per /audiences/{type}s call
    workspace_id: int | str | None = None,
) -> dict:
    """
    Export an entire audience segment to a local CSV or JSON artifact.

    Mirrors the existing `export_rows()` API surface (returns
    `{payload, artifact_path}`, writes append-only to
    <project_root>/tmp/clay-artifacts/), but pulls rows via
    POST /v3/workspaces/{ws}/audiences/contacts (or /accounts) with
    segmentId filter, paginating until pagination.hasMore is false.

    Strictly more capable than routing through Flow F + export_csv:
    no 50K row cap, no separate workbook+table created, single artifact.
    """
```

**Implementation sketch (no SDK gaps to overcome):**

```python
endpoint_seg = "contacts" if entity_type == "CONTACT" else "accounts"
ws_id = self._resolve_workspace_id(workspace_id)
all_rows = []
offset = 0
while True:
    body = {
        "limit": page_size, "offset": offset,
        "segmentId": segment_id,
        "includeDeleted": False, "isArchived": False,
        "shouldInjectDraftFilter": True, "segmentType": None,
    }
    if entity_type == "CONTACT":
        body["includeData"] = {"accountIds": True}
    res = self.post(f"/workspaces/{ws_id}/audiences/{endpoint_seg}", body)
    rows = res.get(endpoint_seg, [])
    all_rows.extend(rows)
    if not res.get("pagination", {}).get("hasMore"):
        break
    if limit and len(all_rows) >= limit:
        all_rows = all_rows[:limit]
        break
    offset += page_size
# Then write CSV/JSON locally via the existing export_rows artifact-writer helpers
```

**Notable design points:**

- The 300-row default `page_size` matches what the Clay UI uses (verified in HAR captures). Bumpable but unverified-above-300.
- `audiences/contacts` returns full per-row data inline via `contact.entity.fields[]` — same field set the per-contact detail endpoint (`GET /audiences/contacts/{id}`) returns. **No N+1 detail fetches needed for the standard fields.** Verified 2026-04-30 by diffing both responses against the same record (id 464501906): same 17 `field_id` entries, just wrapped differently.
- The CSV writer should pivot `entity.fields[]` from flat key/value/type rows into columns — each unique `field_id` becomes a column header. Example fields seen: `name`, `first_name`, `last_name`, `title`, `linkedin_url`, `location`, `country_iso`, `created_at`, `updated_at`, `origin_source_id`, `origin_source_type`, `is_draft`, `sources`, `enhanced_match_status`, `external_source_sync_status_v[123]`. Other fields visible in the UI's "All information" panel (Email, Phone, Job title, Seniority, Department, Hashed email 1-3) are NOT returned by the list endpoint when null — they're UI-side placeholders that render as `—`. So the CSV will only have columns for fields that have at least one populated row; null-only fields are omitted.
- For Companies (ACCOUNT), drop `includeData.accountIds` — that's a contacts-specific field.
- **Optional richer exports** for callers who want more than the default `entity.fields`: per-record signals, activities, and custom-objects each live in a separate endpoint (`GET /audiences/entities/{id}/signal-events`, `/audiences/entities/{id}/activities`, `/audiences/contacts/{id}/custom-objects`). These would be N+1 (one call per record), so expose via opt-in flags like `include_signals=False`, `include_activities=False`, `include_custom_objects=False`.
- Consider exposing companion methods `count_audience_segment(segment_id)` (already feasible via `POST /audiences/count`) and `list_audience_segments(entity_type)` (`GET /audiences/segments?entityType=...`) so callers can introspect before exporting.

**Decision rule for callers (worth surfacing in SKILL.md alongside the existing export-method comparison):**

| Source of data | Recommended export |
|---|---|
| Table | `export_csv` (UI parity) / `export_rows` (full enrichment) / `fetch_all_records_full` (most complete) |
| Audience segment ≤ 50K | EITHER Flow F → table → existing export methods, OR `export_audience_segment()` directly |
| Audience segment > 50K | **`export_audience_segment()` is the only path** (audience → table is capped at 50K) |

**Tier:** 2 (high value, workaround-able with existing primitives). Workaround is hand-rolling the pagination loop, which is doable but un-Pythonic and not discoverable from the capability map.

---

## Tier 3 — Nice-to-have

- **Snapshot / point-in-time clone WITH DATA.** ClayCast's `create_table(source_table_id=)` is schema-only. A data-preserving clone would support A/B testing different enrichment strategies or "freeze this list before we mutate it."
- **Archive / unarchive** for tables and workbooks (not just `delete_table`).
- **Collaborator / workspace-permission management** via the API.
- **Comment / annotation APIs** on records.
- **Column-failure alerting / webhook hooks** (but really: that belongs to external monitoring infra, not claycast).
- **Data validation on action-column inputs.** Clay silently accepts unknown `inputsBinding` names; a claycast-side schema validator that cross-references `action-registry.md` and raises would prevent silent credit burns.

---

## Operational gaps (not features, but real blockers for unattended pipelines)

### Session cookie expiry

`CLAY_SESSION` rotates every few weeks. A long-running unattended script will silently start getting 401s. ClayCast has no refresh mechanism — browser-session auth can't self-renew. Headless deployments need one of:
- Periodic manual re-auth + .env swap (current reality)
- Migration to a proper API token if Clay ever exposes one
- Cookie auto-harvest from a persistent Chrome profile (possible via `clay_browser.py` but not implemented as a refresh loop)

### Rate limiting / 429 retries

ClayCast uses raw `requests.Session()` with no backoff. Production pipelines that hammer enrichments will hit 429 and either crash or burn retries unproductively. No `RetryPolicy(max=3, backoff=exponential)` wired in.

### Parallel-run safety

`upsert_records` does an O(N) scan serially. A heavy pipeline re-running upserts every few minutes against a large table is hot CPU + lots of API calls. `fetch_all_records_full` parallelizes with a thread pool — that pattern could extend to other bulk ops.

### No durable job state

Long-running jobs (CSV import, bulk enrichment) survive in Clay's backend but claycast has no persistence layer for the user's local job-tracking. If the user's Python process dies mid-wait, they lose their place. Adding a minimal `~/.claycast/state.json` with job-id + status + last-polled timestamp would let users resume.

---

## Where to start if you want to close the biggest gap with minimum work

**Views CRUD + "Find People" sourced-table creation.**

Together they unlock the end-to-end flow:

```
source a list of companies → filter via a view → enrich on the filtered subset
  → re-view the enriched results → export
```

Every step of that is currently in claycast except step 1 (sourcing) and step 2 (view creation). With those two, claycast goes from "Clay inspector + editor" to "Clay automation platform."

Scheduling (#3) is the next biggest unlock but is really two problems: a claycast method to register a schedule + the Clay-side workflow engine that executes it. Scope that separately.

---

## How to add a new gap to this file

When you discover a missing capability that blocks a real workflow, add it here with:
1. What it is + what Clay UI equivalent looks like
2. Why it blocks automation (impact)
3. Proposed claycast surface (method name + signature sketch)
4. Tier it honestly (1: blocks workflows, 2: high value + workaround-able, 3: nice-to-have)

Keep the tone factual. This is a gap list, not a wishlist.
