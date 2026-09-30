"""ClayClient Audiences methods, driven through a recording FakeSession.

Asserts the exact HTTP method / path / params / body each method sends and how it parses
responses — i.e. that the code does what its docstrings say. Whether Clay's server accepts
these shapes is a live claim and cannot be re-checked offline.
"""
import pytest

from conftest import all_nodes, cc, is_uuid

WS = 12345
IMPORTS = f"/workspaces/{WS}/audiences/imports"


def _import(**over):
    imp = {
        "id": "audimp_1", "entityType": "ACCOUNT", "importSourceType": "SALESFORCE",
        "importSourceSubtype": "account",
        "fieldMapping": {"fieldMappings": [
            {"type": "SALESFORCE", "audienceFieldId": "org_name", "salesforceFieldId": "Name", "mappingRule": "NEVER_WRITE"},
            {"type": "SALESFORCE", "audienceFieldId": "audf_old", "salesforceFieldId": "Industry", "mappingRule": "ALWAYS_WRITE"},
        ]},
        "importMetadata": {"type": "SALESFORCE", "appAccountId": "aa_1", "isImportSyncEnabled": True,
                           "isExportSyncEnabled": True, "isCreateNewRecordsEnabled": False,
                           "createNewRecordsIdMapping": None, "isTaskSyncEnabled": True},
    }
    imp.update(over)
    return imp


CATALOG = {"fields": [
    {"value": "Name", "label": "Account Name", "type": "string"},
    {"value": "Industry", "label": "Industry", "type": "picklist"},
    {"value": "AnnualRevenue", "label": "Annual Revenue", "type": "currency"},
    {"value": "Is_Partner__c", "label": "Is Partner", "type": "boolean"},
    {"value": "Website", "label": "Website", "type": "url"},
    {"value": "Last_Touch__c", "label": "Last Touch", "type": "datetime"},
    {"value": "Notes__c", "label": "Notes", "type": "textarea"},
]}


def _created(call):
    return [{"id": f"audf_new{i}", **f} for i, f in enumerate(call["json"]["audienceFields"])]


def _sf_routes(imp=None, created=_created):
    return [
        ("GET", IMPORTS, {"audienceImports": [imp or _import()]}),
        ("GET", IMPORTS + r"/salesforce-fields/account", CATALOG),
        ("POST", f"/workspaces/{WS}/audiences/field", created),
        ("PATCH", f"/workspaces/{WS}/audiences/salesforce-imports",
         lambda call: {"audienceImports": [{"id": "audimp_1", "status": "PENDING", "echo": call["json"]}]}),
    ]


# ── imports / field catalog / field create ───────────────────────────────────

def test_list_audience_imports_paths_and_parsing(client):
    c = client([("GET", IMPORTS, {"audienceImports": [{"id": "a"}]})])
    assert c.list_audience_imports() == [{"id": "a"}]
    assert c.session.calls[-1]["params"] is None
    c.list_audience_imports(entity_type="account")
    assert c.session.calls[-1]["params"] == {"entityType": "ACCOUNT"}
    with pytest.raises(ValueError):
        c.list_audience_imports(entity_type="deal")


def test_list_audience_imports_legacy_key_and_empty(client):
    assert client([("GET", IMPORTS, {"imports": [1]})]).list_audience_imports() == [1]
    assert client([("GET", IMPORTS, {})]).list_audience_imports() == []


def test_sync_status_path(client):
    c = client([("GET", IMPORTS + "/external-source-sync-status/SALESFORCE/audimp_1", {"importSyncStatus": "DONE"})])
    assert c.get_audience_import_sync_status("audimp_1", source_type="salesforce") == {"importSyncStatus": "DONE"}


def test_salesforce_fields_path_and_params(client):
    c = client([("GET", IMPORTS + "/salesforce-fields/contact", {"fields": [{"value": "Email"}]})])
    assert c.list_salesforce_import_fields("contact", auth_account_id="aa_9") == [{"value": "Email"}]
    assert c.session.calls[-1]["params"] == {"authAccountId": "aa_9"}


def test_create_audience_fields_body_and_defaults(client):
    c = client([("POST", f"/workspaces/{WS}/audiences/field", _created)])
    out = c.create_audience_fields("contact", [{"displayName": "A"}, {"displayName": "B", "dataType": "number"}])
    assert c.session.calls[-1]["json"] == {"entityType": "CONTACT", "audienceFields": [
        {"displayName": "A", "fieldType": "SCALAR", "dataType": "text"},
        {"displayName": "B", "fieldType": "SCALAR", "dataType": "number"}]}
    assert [f["id"] for f in out] == ["audf_new0", "audf_new1"]


def test_create_audience_fields_empty_makes_no_call(client):
    c = client()
    assert c.create_audience_fields("ACCOUNT", []) == [] and c.session.calls == []
    with pytest.raises(ValueError):
        c.create_audience_fields("people", [{"displayName": "x"}])


# ── mapping replace ──────────────────────────────────────────────────────────

def test_update_mapping_body_exact(client):
    c = client([("PATCH", f"/workspaces/{WS}/audiences/salesforce-imports", {"ok": 1})])
    c.update_salesforce_import_field_mapping(
        "audimp_1",
        [{"audienceFieldId": "org_name", "salesforceFieldId": "Name", "extra": "dropped"},
         {"audienceFieldId": "audf_1", "salesforceFieldId": "X__c", "mappingRule": "ALWAYS_WRITE"}],
        entity_type="account", is_export_sync_enabled=True)
    body = c.session.calls[-1]["json"]
    assert body == {"audienceImports": [{
        "audienceImportId": "audimp_1",
        "fieldMapping": [
            {"type": "SALESFORCE", "audienceFieldId": "org_name", "salesforceFieldId": "Name", "mappingRule": "NEVER_WRITE"},
            {"type": "SALESFORCE", "audienceFieldId": "audf_1", "salesforceFieldId": "X__c", "mappingRule": "ALWAYS_WRITE"}],
        "isImportSyncEnabled": True, "isExportSyncEnabled": True, "isCreateNewRecordsEnabled": False,
        "createNewRecordsIdMapping": None, "entityType": "ACCOUNT", "isTaskSyncEnabled": False}],
        "reconcileOpportunityImportDependencies": True}


@pytest.mark.parametrize("bad", [{"salesforceFieldId": "X"}, {"audienceFieldId": "a"}, {"audienceFieldId": "", "salesforceFieldId": "X"}])
def test_update_mapping_rejects_incomplete_entries_before_any_call(client, bad):
    c = client()
    with pytest.raises(ValueError):
        c.update_salesforce_import_field_mapping("audimp_1", [bad], entity_type="ACCOUNT")
    assert c.session.calls == []


# ── add_salesforce_import_fields (the convenience method) ────────────────────

def test_add_fields_happy_path(client):
    c = client(_sf_routes())
    out = c.add_salesforce_import_fields("audimp_1", [
        {"salesforceFieldId": "Industry"},                       # already mapped -> skipped
        {"salesforceFieldId": "AnnualRevenue"},                  # currency -> number, label as name
        {"salesforceFieldId": "Is_Partner__c", "displayName": "Partner?"},
        {"salesforceFieldId": "Website"},
        {"salesforceFieldId": "Last_Touch__c"},                  # datetime -> date
        {"salesforceFieldId": "Notes__c", "dataType": "text"},   # explicit wins
    ])
    assert out["skipped"] == ["Industry"]
    post = [x for x in c.session.calls if x["method"] == "POST"][0]["json"]
    assert post["entityType"] == "ACCOUNT"
    assert [(f["displayName"], f["dataType"]) for f in post["audienceFields"]] == [
        ("Annual Revenue", "number"), ("Partner?", "boolean"), ("Website", "url"),
        ("Last Touch", "date"), ("Notes", "text")]
    patch = [x for x in c.session.calls if x["method"] == "PATCH"][0]["json"]["audienceImports"][0]
    mapping = patch["fieldMapping"]
    assert mapping[:2] == [  # existing entries preserved verbatim, incl. a non-default rule
        {"type": "SALESFORCE", "audienceFieldId": "org_name", "salesforceFieldId": "Name", "mappingRule": "NEVER_WRITE"},
        {"type": "SALESFORCE", "audienceFieldId": "audf_old", "salesforceFieldId": "Industry", "mappingRule": "ALWAYS_WRITE"}]
    assert [(m["audienceFieldId"], m["salesforceFieldId"]) for m in mapping[2:]] == [
        ("audf_new0", "AnnualRevenue"), ("audf_new1", "Is_Partner__c"), ("audf_new2", "Website"),
        ("audf_new3", "Last_Touch__c"), ("audf_new4", "Notes__c")]
    # sync flags carried over from importMetadata
    assert (patch["isExportSyncEnabled"], patch["isTaskSyncEnabled"]) == (True, True)
    assert out["import"]["status"] == "PENDING" and len(out["created_fields"]) == 5


def test_add_fields_nothing_new_makes_no_writes(client):
    c = client(_sf_routes())
    out = c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Name"}])
    assert out["created_fields"] == [] and out["skipped"] == ["Name"]
    assert {x["method"] for x in c.session.calls} == {"GET"}


def test_add_fields_unknown_sf_field_fails_before_any_write(client):
    c = client(_sf_routes())
    with pytest.raises(ValueError, match="not a mappable field"):
        c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}, {"salesforceFieldId": "Nope__c"}])
    assert {x["method"] for x in c.session.calls} == {"GET"}


def test_add_fields_import_not_found_or_not_salesforce(client):
    with pytest.raises(ValueError, match="not found"):
        client(_sf_routes()).add_salesforce_import_fields("audimp_x", [{"salesforceFieldId": "Website"}])
    cpj = _import(importSourceType="CPJ", importMetadata={"type": "CPJ"})
    with pytest.raises(ValueError, match="not a Salesforce import"):
        client(_sf_routes(cpj)).add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}])


def test_add_fields_created_count_mismatch_raises_before_patch(client):
    c = client(_sf_routes(created=lambda call: _created(call)[:1]))
    with pytest.raises(RuntimeError):
        c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}, {"salesforceFieldId": "Notes__c"}])
    assert "PATCH" not in {x["method"] for x in c.session.calls}


def test_FINDING_duplicate_new_field_is_created_and_mapped_twice(client):
    c = client(_sf_routes())
    c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}, {"salesforceFieldId": "Website"}])
    post = [x for x in c.session.calls if x["method"] == "POST"][0]["json"]["audienceFields"]
    patch = [x for x in c.session.calls if x["method"] == "PATCH"][0]["json"]["audienceImports"][0]["fieldMapping"]
    assert len(post) == 2
    assert [m["salesforceFieldId"] for m in patch].count("Website") == 2


def test_FINDING_missing_sync_flags_fall_back_to_defaults(client):
    """If importMetadata comes back without a flag, the PATCH sends the default instead of the
    import's real setting (export False, task False). Shown here; whether Clay ever omits these
    keys is unknown offline."""
    meta = {"type": "SALESFORCE", "appAccountId": "aa_1"}  # flags absent
    c = client(_sf_routes(_import(importMetadata=meta)))
    c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}])
    patch = [x for x in c.session.calls if x["method"] == "PATCH"][0]["json"]["audienceImports"][0]
    assert (patch["isImportSyncEnabled"], patch["isExportSyncEnabled"], patch["isTaskSyncEnabled"]) == (True, False, False)


def test_FINDING_bad_existing_mapping_leaves_orphan_fields(client):
    """An existing mapping entry without audienceFieldId passes the pre-write checks, the new
    fields get created, and only then does the PATCH validation raise — fields exist, unmapped."""
    imp = _import()
    imp["fieldMapping"]["fieldMappings"].append({"type": "SALESFORCE", "salesforceFieldId": "Owner"})
    c = client(_sf_routes(imp))
    with pytest.raises(ValueError, match="missing audienceFieldId"):
        c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}])
    assert [x["method"] for x in c.session.calls] == ["GET", "GET", "POST"]


# ── segments ─────────────────────────────────────────────────────────────────

SEG = f"/workspaces/{WS}/audiences/segments"


def test_create_segment_default_filter_matches_all(client):
    c = client([("POST", SEG, {"id": "audseg_1"})])
    assert c.create_audience_segment("account", "All") == {"id": "audseg_1"}
    body = c.session.calls[-1]["json"]
    assert body["name"] == "All" and body["entityType"] == "ACCOUNT"
    assert body["filterAst"]["type"] == "GroupOp" and body["filterAst"]["items"] == []
    assert is_uuid(body["filterAst"]["id"])


def test_create_segment_wraps_bare_node_and_ids_everything(client):
    c = client([("POST", SEG, {})])
    c.create_audience_segment("CONTACT", "x", cc.af_field("CONTACT", "email", "NotEmpty"))
    ast = c.session.calls[-1]["json"]["filterAst"]
    assert ast["type"] == "GroupOp" and ast["items"][0]["type"] == "BinOp"
    assert all(is_uuid(n["id"]) for n in all_nodes(ast))


def test_update_segment_partial_put(client):
    c = client([("PUT", SEG + "/audseg_1", {"id": "audseg_1"})])
    c.update_audience_segment("audseg_1", name="New")
    assert c.session.calls[-1]["json"] == {"name": "New"}
    c.update_audience_segment("audseg_1", description="d", filter_ast=cc.af_field("ACCOUNT", "x", "Empty"))
    body = c.session.calls[-1]["json"]
    assert set(body) == {"description", "filterAst"} and body["filterAst"]["type"] == "GroupOp"
    with pytest.raises(ValueError):
        c.update_audience_segment("audseg_1")


def test_update_segment_description_can_be_cleared(client):
    c = client([("PUT", SEG + "/audseg_1", {})])
    c.update_audience_segment("audseg_1", description="")
    assert c.session.calls[-1]["json"] == {"description": ""}


def test_delete_and_get_segment(client):
    c = client([("POST", SEG + "/audseg_1/delete", {"success": True, "segmentId": "audseg_1"}),
                ("GET", SEG + "/audseg_1", {"id": "audseg_1"})])
    assert c.delete_audience_segment("audseg_1")["success"] is True
    assert c.session.calls[-1]["json"] == {}
    assert c.get_audience_segment("audseg_1") == {"id": "audseg_1"}


# ── counts ───────────────────────────────────────────────────────────────────

COUNT = f"/workspaces/{WS}/audiences/count"
SAVED_AST = {"type": "GroupOp", "combinationMode": "And", "id": "11111111-1111-1111-1111-111111111111",
             "items": [{"type": "BinOp", "key": "org_name", "operator": "Equal", "value": "Acme", "id": "22222222-2222-2222-2222-222222222222",
                        "dataPath": ["account_entity_field_values", "field", "org_name"], "entityType": "ACCOUNT"}]}


def test_count_whole_entity(client):
    c = client([("POST", COUNT, {"count": "7"})])
    assert c.count_audience_records("account") == 7
    assert c.session.calls[-1]["json"] == {"entityType": "ACCOUNT", "isArchived": False,
                                           "shouldInjectDraftFilter": True, "segmentType": None}


def test_count_adhoc_filter(client):
    c = client([("POST", COUNT, {"count": 3})])
    c.count_audience_records("ACCOUNT", filter_ast=cc.af_field("ACCOUNT", "x", "Empty"), archived=True)
    body = c.session.calls[-1]["json"]
    assert body["isArchived"] is True and "segmentId" not in body and body["filters"]["type"] == "GroupOp"


def test_count_segment_sends_saved_filter_and_keeps_its_ids(client):
    c = client([("GET", SEG + "/audseg_1", {"id": "audseg_1", "entityType": "ACCOUNT", "filterAst": SAVED_AST}),
                ("POST", COUNT, {"count": 214})])
    assert c.count_audience_records("ACCOUNT", segment_id="audseg_1") == 214
    body = c.session.calls[-1]["json"]
    assert body["segmentId"] == "audseg_1" and body["filters"] == SAVED_AST


def test_count_rejects_filter_and_segment_together(client):
    with pytest.raises(ValueError):
        client().count_audience_records("ACCOUNT", filter_ast=cc.af_and(), segment_id="s")


def test_FINDING_count_segment_ignores_the_segments_own_entity_type(client):
    """count_audience_segment defaults entity_type to CONTACT and never checks the fetched
    segment's entityType, so an ACCOUNT segment counted without entity_type is sent as CONTACT
    with an account-field filter. (Same default existed before the PR.)"""
    c = client([("GET", SEG + "/audseg_1", {"id": "audseg_1", "entityType": "ACCOUNT", "filterAst": SAVED_AST}),
                ("POST", COUNT, {"count": 0})])
    c.count_audience_segment("audseg_1")
    assert c.session.calls[-1]["json"]["entityType"] == "CONTACT"


def test_filter_stages_are_cumulative(client):
    c = client([("POST", COUNT, lambda call: {"count": 100 - 10 * len(call["json"]["filters"]["items"])})])
    s1, s2, s3 = (cc.af_field("ACCOUNT", f, "NotEmpty") for f in ("a", "b", "c"))
    assert c.count_audience_filter_stages("ACCOUNT", [s1, s2, s3]) == [
        {"stage": 1, "count": 90}, {"stage": 2, "count": 80}, {"stage": 3, "count": 70}]
    assert [len(x["json"]["filters"]["items"]) for x in c.session.calls] == [1, 2, 3]


@pytest.mark.parametrize("counts,expected", [
    ((100, 30, 70, 0), {"both": 0, "neither": 0, "exact": True}),
    ((100, 30, 75, 5), {"both": 5, "neither": 0, "exact": False}),   # overlap
    ((100, 30, 60, 0), {"both": 0, "neither": 10, "exact": False}),  # gap
])
def test_verify_complement_math(client, counts, expected):
    seq = iter(counts)
    c = client([("POST", COUNT, lambda call: {"count": next(seq)})])
    ex, inc = cc.af_field("ACCOUNT", "x", "Equal", 1), cc.af_field("ACCOUNT", "x", "NotEqual", 1)
    out = c.verify_audience_filter_complement("ACCOUNT", ex, inc)
    assert {k: out[k] for k in expected} == expected
    assert out["total"] == counts[0]
    bodies = [x["json"] for x in c.session.calls]
    assert "filters" not in bodies[0]                      # 1st call = whole entity
    inter = bodies[3]["filters"]                            # 4th = intersection: And(ex, inc) as root
    assert inter["combinationMode"] == "And" and [i["operator"] for i in inter["items"]] == ["Equal", "NotEqual"]
