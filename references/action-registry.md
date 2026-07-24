# Clay Action Registry

Every known Clay action with exact keys, input names, output keys, gotchas, and code examples.
For actions not listed here, use `clay.search_enrichments("keyword")` to discover them (Section 8).

Auth account IDs are workspace-specific — look them up with `clay.list_auth_accounts()`.

```python
accounts = clay.list_auth_accounts()
for account in accounts:
    print(account.get("name", account.get("displayName", "?")), account["id"])
```

---

## 1. AI & Content

### Create Content (no web access)

Generates text/JSON from data already in the table. Cheap, fast, deterministic.

- **key:** `use-ai`
- **package:** `67ba01e9-1898-4e7d-afe7-7ebe24819a57`
- **inputs:**
  - `useCase`: `'"use-ai"'` (NOT "claygent")
  - `model`: `'"gemini-2.5-flash"'` or `'"grok-4-1-fast-reasoning"'` (for JSON)
  - `prompt`: formula string with column refs
  - `systemPrompt` (optional): `'"You are a..."'` — keep under 1000 chars, no markdown
- **output:** text or JSON (use `?.key` to extract fields)
- **auth:** Gemini or Grok auth account ID from `clay.list_auth_accounts()`
- **gotchas:**
  - actionKey is `"use-ai"`, NOT `"ai"` — `"ai"` silently drops all inputs
  - For JSON output: use Grok + `answerSchemaType`. Gemini/GPT wrap JSON in code fences.
  - `answerSchemaType` needs `formulaMap` (not `formulaText`) + `_metadata` input with `"modelSource": "user"`
  - JSON schemas must be double-encoded: `json.dumps(json.dumps(schema))`

```python
clay.create_action_column(t_id, "Qualify Lead",
    action_key="use-ai",
    package_id="67ba01e9-1898-4e7d-afe7-7ebe24819a57",
    inputs={
        "useCase": '"use-ai"',
        "model": '"gemini-2.5-flash"',
        "prompt": '"Qualify this company:\\n" + {{@Enrich Company}}',
    },
    auth_account_id="<gemini-or-grok-auth-account-id>",
    view_id=v_id)
```

### Claygent (web research)

AI agent that browses the internet to find answers. Burns more credits but can look up live data.

- **key:** `use-ai` (same as Create Content)
- **package:** `67ba01e9-1898-4e7d-afe7-7ebe24819a57`
- **inputs:**
  - `useCase`: `'"claygent"'` (this differentiates it from Create Content)
  - `model`: `'"gpt-5-nano"'` (cheapest) or `'"gpt-4.1"'` (complex research)
  - `prompt`: research instruction with column refs
- **output:** text research results
- **auth:** OpenAI auth account ID from `clay.list_auth_accounts()`
- **gotchas:**
  - Same actionKey `"use-ai"` as Create Content — `useCase` differentiates
  - More expensive (agent loop + web search), use only when table data isn't enough
  - Best with OpenAI models (GPT), not Gemini

```python
clay.create_action_column(t_id, "Research Company",
    action_key="use-ai",
    package_id="67ba01e9-1898-4e7d-afe7-7ebe24819a57",
    inputs={
        "useCase": '"claygent"',
        "model": '"gpt-5-nano"',
        "prompt": '"Find the exact employee count for " + {{@Company LI URL}}',
    },
    auth_account_id="<openai-auth-account-id>",
    view_id=v_id)
```

---

## 2. Enrichment

### Enrich Company (MixRank)

Firmographics, funding, tech stack, employee count from LinkedIn company URL.

- **key:** `enrich-company-with-mixrank-v2`
- **package:** `e251a70e-46d7-4f3a-b3ef-a211ad3d8bd2`
- **inputs:**
  - `company_identifier`: LinkedIn company URL (NOT company name, NOT domain)
- **output keys:** `?.name`, `?.url` (LI URL), `?.website`, `?.description`, `?.employee_count`, `?.industry`, `?.country`, `?.founded`, `?.org_id`
- **does NOT return:** `domain`, `city`, `funding_stage`, `short_description`
- **auth:** none needed (Clay-managed enrichment)
- **gotchas:**
  - Input name is `company_identifier`, NOT `url` or `domain`
  - Company names like "Stealth" or "Cuez" fail with `ERROR_INVALID_INPUT`
  - Best practice: extract LI company URL from Enrich Person → `experience[0].url` using `mappedResultPath`

```python
clay.create_action_column(t_id, "Enrich Company",
    action_key="enrich-company-with-mixrank-v2",
    package_id="e251a70e-46d7-4f3a-b3ef-a211ad3d8bd2",
    inputs={"company_identifier": '{{@Company LI URL}}'},
    view_id=v_id)
```

### Enrich Person (MixRank)

Profile data, job title, experience history from LinkedIn profile URL.

- **key:** `enrich-person-with-mixrank-v2`
- **package:** `e251a70e-46d7-4f3a-b3ef-a211ad3d8bd2`
- **inputs:**
  - `person_identifier`: LinkedIn profile URL (NOT `linkedin_url`, NOT `url`)
  - `email`: include as empty binding (required even if blank)
- **output keys (top-level):** `?.title`, `?.org`, `?.location_name`, `?.headline`, `?.url`, `?.num_followers`, `?.connections`
- **nested data (use `mappedResultPath`):**
  - `["experience", "0", "url"]` — current company LI URL
  - `["experience", "0", "org"]` — current company name
- **auth:** none needed (Clay-managed enrichment)
- **gotchas:**
  - Input name is `person_identifier`, NOT `linkedin_url`
  - MUST include empty `email` input (silently required)
  - Nested paths need `mappedResultPath` array or formula returns empty

```python
col = clay.create_action_column(t_id, "Enrich Person",
    action_key="enrich-person-with-mixrank-v2",
    package_id="e251a70e-46d7-4f3a-b3ef-a211ad3d8bd2",
    inputs={"person_identifier": '{{@Person LI URL}}', "email": ""},
    view_id=v_id)

# Extract nested company URL:
clay.create_formula_column(t_id, "Current Company URL",
    formula_text=f'{{{{{col["id"]}}}}}',
    mapped_result_path=["experience", "0", "url"], view_id=v_id)
```

---

## 3. Sources (Table Creation)

### Find People (MixRank Source)

Creates a new table populated with people matching filters (job title, geography, company).

- **key:** `find-lists-of-people-with-mixrank-source`
- **endpoint:** `POST /sources/create-cpj-table` (NOT regular `POST /sources`)
- **config:**
  - `cpjConfig.type`: `"people"`
  - `cpjConfig.typeSettings.inputs`: filters (job title, geography, company list)
  - `cpjConfig.basicFields`: auto-created formula columns
  - `tableName`: display name
  - `workbookId` (optional): existing workbook, or omit to create new
- **returns:** `{"tableId", "viewId", "workbookId", "sourceId", "isNewTable"}`
- **gotchas:**
  - Regular `POST /sources` returns 404 "Invalid subscriptions"
  - Requires Clay plan with source subscriptions (not all workspaces have this)
  - `company_table_id` references must point to a table with actual data (empty table = 400)

```python
r = clay.session.post("https://api.clay.com/v3/sources/create-cpj-table", json={
    "cpjConfig": {
        "type": "people",
        "typeSettings": {"inputs": {
            # filters — discover exact input names via clay_browser.py or HAR
        }},
        "basicFields": []
    },
    "tableName": "Find People - Sales Leaders DE",
})
```

### Find Companies (MixRank Source)

Same pattern as Find People but for companies.

- **key:** `find-lists-of-companies-with-mixrank-source`
- **endpoint:** `POST /sources/create-cpj-table`
- **config:** Same as Find People with `cpjConfig.type: "companies"`
- **gotchas:** Same as Find People — requires plan subscription

### Webhook Source

Receives JSON payloads via HTTP POST. Creates a source column with full payload.

- **type:** `"source"` column (not action column)
- **config:** `typeSettings: {"sourceType": "webhook", "sourceIds": []}`
- **gotchas:**
  - Individual fields are NOT auto-populated — must create formula extractors
  - Extractors: `formulaText: "{{source_field_id}}?.json_key"` with `formulaType: "text"`
  - Common mistake: plain text columns show in UI but are empty (they're not formulas)

---

## 4. Lookups (Cross-Table)

### Lookup Row in Other Table

Find a single matching row in another Clay table.

- **key:** `lookup-row-in-other-table`
- **package:** `4299091f-3cd3-4d68-b198-0143575f471d`
- **inputs (ALL use `fields|` prefix):**
  - `tableId`: target table ID (string literal `'"t_xxx"'`)
  - `fields|targetColumn`: field ID in target table (`'"f_xxx"'`)
  - `fields|filterOperator`: `'"EQUAL"'`, `'"CONTAINS"'`, etc.
  - `fields|rowValue`: formula ref from current table (`'{{@Column Name}}'`)
  - `fields|limit` (optional): max results
- **output:** display string like "1 Record Found"
- **gotchas:**
  - Input names use `fields|` prefix — NOT just `targetColumn`
  - Without `fields|` prefix, inputs are silently dropped
  - The old "`?.key` extractor" guidance is **not reliable enough to treat as a contract**. Live ClayCast testing found the lookup display cell populates, but downstream extractor formulas did not resolve consistently. Treat extraction as "validate on a real table first," not "guaranteed pattern."

```python
clay.create_action_column(t_id, "Find in CRM",
    action_key="lookup-row-in-other-table",
    package_id="4299091f-3cd3-4d68-b198-0143575f471d",
    inputs={
        "tableId": '"t_target_table_id"',
        "fields|targetColumn": '"f_email_field"',
        "fields|filterOperator": '"EQUAL"',
        "fields|rowValue": '{{@Email}}',
    },
    view_id=v_id)
```

### Lookup Multiple Rows in Other Table

Same as Lookup Row but returns multiple matches.

- **key:** `lookup-multiple-rows-in-other-table`
- **package:** `4299091f-3cd3-4d68-b198-0143575f471d`
- **inputs:** Same as Lookup Row (all use `fields|` prefix)
- **output:** display string like "3 Records Found"
- **gotcha:** same extractor warning applies here — do not assume a stable `?.key` pattern without live validation.

---

## 5. HTTP API

### HTTP API v2 (Generic)

Make any HTTP request to external APIs. Used for RapidAPI, HubSpot, Tavily, custom endpoints.

- **key:** `http-api-v2`
- **package:** `4299091f-3cd3-4d68-b198-0143575f471d`
- **inputs:**
  - `method`: `'"GET"'`, `'"POST"'`, `'"PATCH"'`
  - `url`: API endpoint (string literal or formula)
  - `queryString`: use `formulaMap` for key-value pairs (NOT `formulaText`)
  - `body`: schema type is **longtext**. UI-canonical form = `formulaText` building a JSON string with
    `Clay.formatForJSON({{f_id}})` around each value (escapes quotes/newlines) — use the helper
    **`format_json_body({...})`** from `clay_client.py`. A `formulaMap` body also works but is not what the
    UI writes. (verified 2026-07-24)
  - `headers`: use `formulaMap` for each header (NOT `formulaText`)
  - `removeNull`, `followRedirects`, `shouldRetry` (optional booleans)
  - **Bind ALL 15 params** (unset as `None`) or the Clay UI shows no inputs for the column — full list:
    `method, url, queryString, body, headers, fieldPaths, removeNull, returnResponseMetadata, followRedirects,`
    `followRedirectsOptions|maxRedirects, responseTimeout, shouldRetry, retryOptions|maxRetries,`
    `retryOptions|statusCodesToRetry, retryOptions|errorCodesToRetry` (verified 2026-07-24)
- **output:** display string "Status Code: 200" — use `?.key` extractors for response body
- **auth:** RapidAPI auth account ID from `clay.list_auth_accounts()` (auto-injects API key headers)
- **gotchas:**
  - `queryString`, `headers`, `body` MUST use `formulaMap` — `formulaText` with JSON splits chars into numbered rows. Verified 2026-04-23: formulaText `'{"q": hello}'` produced `?0={&1="&2=q...` per char. Don't trust the cell's `"Status Code: 200"` preview — inspect `externalContent.fullValue` to see what Clay actually sent.
  - Auth account (RapidAPI) auto-injects `X-RapidAPI-Key` and `Host` headers
  - Use `Clay.secret("token_name")` in formulas for stored secrets

**Common HTTP patterns:**

RapidAPI (LinkedIn data):
```python
clay.create_action_column(t_id, "LinkedIn Profile",
    action_key="http-api-v2",
    package_id="4299091f-3cd3-4d68-b198-0143575f471d",
    inputs={
        "method": '"GET"',
        "url": '"https://fresh-linkedin-scraper-api.p.rapidapi.com/api/v1/profile"',
        # queryString needs formulaMap pattern — see clay-api-reference.md for details
    },
    auth_account_id="<rapidapi-auth-account-id>",
    view_id=v_id)
```

HubSpot CRM (search/create/update):
```python
# Uses Clay.secret() for HubSpot token or hubspot auth account
clay.create_action_column(t_id, "HubSpot Search",
    action_key="http-api-v2",
    package_id="4299091f-3cd3-4d68-b198-0143575f471d",
    inputs={
        "method": '"POST"',
        "url": '"https://api.hubapi.com/crm/v3/objects/contacts/search"',
        # body + headers via formulaMap
    },
    auth_account_id="<hubspot-auth-account-id>",
    view_id=v_id)
```

---

## 6. Integrations

### Instantly: Add Lead to Campaign

Enroll a prospect into an Instantly email campaign.

- **key:** `instantly-v2-add-lead-to-campaign`
- **package:** `70cda03a-a576-4a6c-b3b3-55e241f828b5`
- **inputs:**
  - `email`: prospect email
  - `first_name`: first name
  - `last_name`: last name
  - `company_name`: company name
  - `campaign`: campaign UUID (get via `POST /actions/dynamicFields` with `parameterPath: "campaign"`)
- **auth:** Instantly auth account ID from `clay.list_auth_accounts()`
- **gotchas:**
  - Campaign UUIDs are dynamic — retrieve via API or save in config
  - Requires email (fails without it)

```python
clay.create_action_column(t_id, "Add to Instantly",
    action_key="instantly-v2-add-lead-to-campaign",
    package_id="70cda03a-a576-4a6c-b3b3-55e241f828b5",
    inputs={
        "email": '{{@Email}}',
        "first_name": '{{@First Name}}',
        "last_name": '{{@Last Name}}',
        "company_name": '{{@Company}}',
        "campaign": '"campaign-uuid-here"',
    },
    auth_account_id="<instantly-auth-account-id>",
    view_id=v_id)
```

### Instantly: Find Leads

- **key:** `instantly-v2-find-leads`
- **package:** `70cda03a-a576-4a6c-b3b3-55e241f828b5`
- **status:** Available but input details not fully documented. Use `clay_browser.py` or HAR to discover inputs.

### Instantly: Update Lead

- **key:** `instantly-v2-update-lead`
- **package:** `70cda03a-a576-4a6c-b3b3-55e241f828b5`
- **status:** Available but input details not fully documented. Use `clay_browser.py` or HAR to discover inputs.

### HeyReach: Add Lead to Campaign

Enroll a prospect into a HeyReach LinkedIn outreach campaign.

- **key:** `heyreach-add-lead-to-campaign`
- **package:** (workspace-specific — find via `search_enrichments("heyreach")`)
- **inputs:**
  - `firstName`, `lastName`: prospect names
  - `profileUrl`: LinkedIn profile URL
  - `companyName`: company name
  - `position`: job title
  - Custom fields: `vmid`, `Campaign`, `HubSpot Company ID`, `HubSpot Contact ID`
- **auth:** HeyReach auth account ID from `clay.list_auth_accounts()`
- **gotchas:**
  - Campaign ID is hardcoded per workflow (not dynamic like Instantly)
  - Custom fields map to HeyReach campaign field definitions

### Google Sheets: Add Row

Append a row to a Google Sheet for reporting/tracking.

- **key:** `google-sheets-add-row-v2`
- **package:** (workspace-specific — find via `search_enrichments("google sheets")`)
- **inputs:** spreadsheet ID, sheet name, column values (discover exact names via HAR)
- **status:** Used in production (influencer monitoring) but input details not fully documented in registry.

### Salesforce: Lookup Records via SOQL

Run an arbitrary SOQL query against the connected Salesforce org. The cell holds the returned record(s) as JSON.

- **key:** `salesforce-lookup-via-soql-v2` (also `salesforce-lookup-via-soql`)
- **package:** `d0c0a70d-7c1e-40de-b214-9d8d82672770` (shared with `salesforce-lookup-record-v2` / `salesforce-lookup-by-id`)
- **inputs:**
  - `soql_query`: **native-query** input. The formulaText must be a valid formula EXPRESSION that evaluates to the query string — i.e. a JS string literal or concatenation, NOT raw SOQL. Static: `'"SELECT Id, Name FROM User LIMIT 1"'`. Per-row: `'"SELECT Id, Name, Title FROM User WHERE Email = \'" + {{f_email}} + "\'"'`.
- **auth:** resolve via `list_auth_accounts_by_type('salesforce')` — do NOT copy the `authAccountId` from an existing Salesforce column (it may be stale → 404 "App Account not found").
- **data_type:** MUST be `json`. `create_action_column` auto-selects this for `salesforce-*` keys; if you build the body by hand, `text` → 400 "value does not match any of the allowed types".

```python
acct = clay.list_auth_accounts_by_type("salesforce")[0]["id"]
# Per-row query keyed on an Email field (f_email). Note the LIKE + Name fallback
# for sandbox orgs (see gotcha #2). The formulaText is a JS string expression.
q = ('"SELECT Id, Name, Email, Title, Department, Division, IsActive FROM User '
     "WHERE Email LIKE '\" + {{f_email}} + \"%'\"")
clay.create_action_column(rep_table, "SFDC Role (SOQL)",
    action_key="salesforce-lookup-via-soql-v2",
    package_id="d0c0a70d-7c1e-40de-b214-9d8d82672770",
    inputs={"soql_query": q},
    auth_account_id=acct)        # data_type defaults to "json" automatically
```

**SOQL query gotchas (verified 2026-05-28, learned the hard way):**

1. **Restricted/read-only connections silently block fields via FLS — surfaced as parse errors, not permission errors.** A read-only API user (e.g. an `API_Read_Only_*` account) frequently can't see role/profile data. `UserRole.Name` → `INVALID_FIELD "Didn't understand relationship 'UserRole'"`; `UserRoleId` → `INVALID_FIELD "No such column 'UserRoleId' on entity 'User'"` — even though both are standard. Strategy: **start with direct descriptive fields** (`Title`, `Department`, `Division`, `IsActive`), **avoid relationship traversal** (`Foo.Name`), and resolve an id→name only via a SEPARATE object query (`SELECT Id, Name FROM UserRole WHERE Id IN (...)`) — which itself needs the id to be FLS-readable. SF reports only the FIRST invalid field (read the `Column:N` caret position to see which field parsed and which broke), so peel fields off one at a time.

2. **Sandbox orgs munge emails → exact match returns zero rows.** If the auth-account name contains `test` / `sandbox` / `--` (e.g. `API_Read_Only_spstest`), it's a sandbox, and Salesforce appends a suffix like `.invalid` or `.<sandboxname>` to every User's `Email`. So `WHERE Email = 'x@co.com'` silently returns "no records found" (a clean run, not an error). Use `WHERE Email LIKE 'x@co.com%'` plus a `Name IN (...)` fallback instead of equality.

---

## 7. Social

### LinkedIn Posts

Fetch recent posts and shares from a LinkedIn profile.

- **key:** `social-posts-get-post-activity-posts-and-shares`
- **package:** `b210a16b-cdaf-4cbd-ad9b-42d762cd165f`
- **inputs:**
  - `socialUrl`: LinkedIn profile URL (NOT `linkedin_url`)
  - `num_posts`: number of posts as STRING (e.g. `'"10"'`, not `10`)
- **output:** JSON with post activity, engagement, shares
- **auth:** none needed (Clay-managed)
- **gotchas:**
  - Input name is `socialUrl`, NOT `linkedin_url` or `url`
  - `num_posts` is a STRING, not a number

```python
clay.create_action_column(t_id, "LinkedIn Posts",
    action_key="social-posts-get-post-activity-posts-and-shares",
    package_id="b210a16b-cdaf-4cbd-ad9b-42d762cd165f",
    inputs={
        "socialUrl": '{{@Person LI URL}}',
        "num_posts": '"10"',
    },
    view_id=v_id)
```

**Alternative: RapidAPI LinkedIn Posts (via HTTP API)**

Use `http-api-v2` with Fresh LinkedIn Scraper for LinkedIn posts.
- **endpoint:** `GET /api/v1/user/posts`
- **param:** `username` (NOT `url`) — extract slug from LinkedIn URL
- **gotcha:** `/user/posts` does NOT accept `url` param — only `urn` or `username`. Error: "Either urn or username must be provided"

```python
# Extract username from LinkedIn URL: "https://linkedin.com/in/johndoe/" → "johndoe"
username_formula = f'{li_ref}.split("/in/")[1]?.split("/")[0]'

clay.create_column(t_id, {
    "type": "action", "name": "LinkedIn Posts",
    "typeSettings": {
        "dataTypeSettings": {"type": "text"},  # keep action columns UI-safe
        "actionKey": "http-api-v2",
        "actionVersion": 1,
        "actionPackageId": "4299091f-3cd3-4d68-b198-0143575f471d",
        "authAccountId": "<rapidapi-auth-account-id>",
        "conditionalRunFormulaText": f"!!{li_ref}",
        "inputsBinding": [
            {"name": "method", "formulaText": '"GET"'},
            {"name": "url", "formulaText": '"https://fresh-linkedin-scraper-api.p.rapidapi.com/api/v1/user/posts"'},
            {"name": "queryString", "formulaMap": {"username": username_formula}},
        ],
    }
}, view_id=v_id)
```

---

## 8. Discovery (Finding New Actions)

Not all actions are in this registry. Clay has hundreds of enrichment providers.

```python
# Search by keyword
results = clay.search_enrichments("find email")
for r in results:
    print(r["entity_id"])  # format: "{packageId}/{actionKey}"
    print(r["name"], r.get("description", ""))

# Documented actions not in sections above:

# LeadMagic Find Work Email (discovered 2026-03-30; verified 2026-04-23)
# - key: leadmagic-find-work-email
# - package: edb58209-a62d-42be-992a-e41b87eeacc2
# - inputs: name (full name string, required), domain (company domain, required).
#   Optional UI-only switch: "Include catch-all email results" — not mapped to
#   an inputsBinding name in captured waterfall presets.
# - auth: <your-auth-account-id> (look up via clay.list_auth_accounts())
# - output: ?.email (work email address)
# - attributePath: "email" (direct, not nested)
# - gotcha: name AND domain required. Clay's API SILENTLY stores unknown
#   binding names (e.g. the phantom "linkedin_url" a prior version of this
#   registry listed), so a wrong input name won't error at create time — it'll
#   just be ignored at run time and your credits will still burn.

# - Many more available via search
```

When using a newly discovered action:
1. Get `action_key` and `package_id` from `entity_id` (split on `/`)
2. Input names are UNKNOWN — use `clay_browser.py` or ask user for HAR
3. Create a test column, inspect in Clay UI, iterate
4. Once working, add to this registry for future use

---

## Common Patterns

### Formula Extractors (access action results)
```python
# Top-level key
clay.create_formula_column(t_id, "Company Name",
    formula_text=f'{{{{{enrich_col_id}}}}}?.name', view_id=v_id)

# Nested key (requires mappedResultPath)
clay.create_formula_column(t_id, "Company LI URL",
    formula_text=f'{{{{{enrich_col_id}}}}}',
    mapped_result_path=["experience", "0", "url"], view_id=v_id)
```

### Conditional Execution (save credits)
```python
# Only run expensive AI if gate check passes
clay.create_action_column(t_id, "Deep Qualify",
    action_key="use-ai",
    package_id="67ba01e9-1898-4e7d-afe7-7ebe24819a57",
    inputs={...},
    condition='{{@Gate Check}} == "PASS"',
    auth_account_id="<gemini-auth-account-id>",
    view_id=v_id)
# Status when skipped: ERROR_RUN_CONDITION_NOT_MET (normal, 0 credits spent)
```

### Records (CRUD)
```python
# Create
records = clay.create_records(t_id, [{f_url: "https://linkedin.com/in/someone"}])
record_ids = [r["id"] for r in records]

# Read
records = clay.list_records(t_id, v_id)           # raw Clay record shape
rows = clay.list_records_by_name(t_id, v_id)      # name-keyed + extract_cell_value()

# Run columns on records
clay.run_column(t_id, [col_id], record_ids=record_ids)
clay.wait_for_runs(t_id, timeout_seconds=300)
```


---

### Route Row (send table data to another table)

Push the current row's data into another Clay table (the "Send table data" column).

- **key:** `route-row`
- **package:** `b1ab3d5d-b0db-4b30-9251-3f32d8b103c1`
- **inputs** (captured from production columns 2026-07-21; created live by POSTing the full typeSettings):
  - `tableId`: target table id as a string-literal formula (`'"t_xxx"'`)
  - `rowData`: **`formulaMap`** keyed by TARGET column NAMES → source-table formula refs (`{"Company Domain": "{{@Domain}}"}`)
  - `nestedData`: **`formulaMap`** keyed by the receiving source's display name → typically `{{f_people_search}}` (the whole source payload)
  - plus top-level typeSettings key `referencedTableId: "t_xxx"` (same target id, unquoted)
- **output:** `{"numberOfRowsSent": 1}` (downstream booleans read `?.numberOfRowsSent == 1`)
- **gotchas:**
  - Creating the column **auto-creates the receiving pipeline on the target table**: a `Rows from: <sender table name>` manual routing source, a source column, and extractor formula columns for every `rowData` key. Keep those (they hold the sender binding); don't also create them yourself.
  - `rowData` keys match target columns by NAME — a stale key silently creates a NEW column on the target at wiring time.

### Trigger Find-People Source ("Update People Search" button)

Per-row trigger of a Find People search source, living on the COMPANY table.

- **key:** `trigger-find-people-source`
- **package:** `4299091f-3cd3-4d68-b198-0143575f471d`
- **inputs:**
  - `sourceId`: string-literal source id (`'"s_xxx"'`)
  - `companyIdentifier`: formula ref to the company's domain column (`'{{@Domain}}'`)
  - `enableAutoUpdate`: `'true'`
- **gotchas:** auto-created by `create-cpj-table` (named `Update People Search (<search name>) - <ISO timestamp>`) — including on FAILED create attempts, which leave orphan columns bound to nothing.

### Lookup Company in Other Table

Standard companion column on cpj people tables ("Company Table Data") — resolves the source row's company back to its record in the company table.

- **key:** `lookup-company-in-other-table`
- **package:** `4299091f-3cd3-4d68-b198-0143575f471d`
- **inputs:**
  - `companyTableId`: `'{{f_people_search}}.company_table_id'`
  - `companyRecordId`: `'{{f_people_search}}.company_record_id'`
- **notes:** `dataTypeSettings` json (record-returning); downstream formulas extract with `?.["Column Name"]`.
