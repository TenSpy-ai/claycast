"""ClayClient Audiences methods, driven through a recording FakeSession.

Asserts the exact HTTP method / path / params / body each method sends and how it parses
responses — i.e. that the code does what its docstrings say. Whether Clay's server accepts
these shapes is a live claim and cannot be re-checked offline.
"""
import copy
import json
import pickle
import re
import sys
from pathlib import Path

import pytest
import requests

from conftest import FakeResponse, all_nodes, cc, is_uuid

WS = 12345
IMPORTS = f"/workspaces/{WS}/audiences/imports"
FIELD = f"/workspaces/{WS}/audiences/field"
SFI = f"/workspaces/{WS}/audiences/salesforce-imports"

# The five sync settings the mapping PATCH replaces, as importMetadata carries them, and the
# same five as update_salesforce_import_field_mapping() keyword arguments.
FLAGS = {"isImportSyncEnabled": True, "isExportSyncEnabled": True, "isCreateNewRecordsEnabled": False,
         "createNewRecordsIdMapping": None, "isTaskSyncEnabled": True}
FLAG_KW = dict(is_import_sync_enabled=True, is_export_sync_enabled=True, is_create_new_records_enabled=False,
               create_new_records_id_mapping=None, is_task_sync_enabled=True)


def _import(**over):
    imp = {
        "id": "audimp_1", "entityType": "ACCOUNT", "importSourceType": "SALESFORCE",
        "importSourceSubtype": "account",
        "fieldMapping": {"fieldMappings": [
            {"type": "SALESFORCE", "audienceFieldId": "org_name", "salesforceFieldId": "Name", "mappingRule": "NEVER_WRITE"},
            {"type": "SALESFORCE", "audienceFieldId": "audf_old", "salesforceFieldId": "Industry", "mappingRule": "ALWAYS_WRITE"},
        ]},
        "importMetadata": {"type": "SALESFORCE", "appAccountId": "aa_1", **FLAGS},
    }
    imp.update(over)
    return imp


def _meta(**over):
    """importMetadata with all five sync settings, minus/plus what the test wants."""
    meta = {"type": "SALESFORCE", "appAccountId": "aa_1", **FLAGS}
    for k, v in over.items():
        if v is ...:
            meta.pop(k, None)
        else:
            meta[k] = v
    return meta


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


def _echo_patch(call):
    return {"audienceImports": [{"id": "audimp_1", "status": "PENDING", "echo": call["json"]}]}


def _sf_routes(imp=None, created=_created, patch=_echo_patch, imports=None):
    """imports: payload/callable for GET imports (answers the first read, the pre-PATCH re-read
    and the post-failure re-read alike); patch: payload/callable — a callable that raises
    simulates a failed PATCH (FakeSession cannot set a status code)."""
    return [
        ("GET", IMPORTS, imports if imports is not None else {"audienceImports": [imp or _import()]}),
        ("GET", IMPORTS + r"/salesforce-fields/account", CATALOG),
        ("POST", FIELD, created),
        ("PATCH", SFI, patch),
    ]


def _methods(c):
    return [x["method"] for x in c.session.calls]


def _post_fields(c):
    return [x for x in c.session.calls if x["method"] == "POST"][0]["json"]["audienceFields"]


def _patch_import(c):
    return [x for x in c.session.calls if x["method"] == "PATCH"][0]["json"]["audienceImports"][0]


def _boom_http(call):
    raise requests.HTTPError("500 Server Error")


def _boom_json(call):
    raise json.JSONDecodeError("Expecting value", "", 0)


def _boom_conn(call):
    raise requests.ConnectionError("Connection reset by peer")


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


def test_import_history_path_entity_and_shape(client):
    rows = [{"importId": "audimp_1", "importSyncType": "sync_incremental"},
            {"importId": "audactimp_1", "activityTypeId": "acttyp_1", "importSyncType": "sync_incremental"}]
    c = client([("GET", IMPORTS + "/external-source-import-history/ACCOUNT", rows)])
    assert c.get_audience_import_history("account") == rows          # entity upper-cased into the path
    assert c.session.calls[-1]["params"] is None                     # path segment, not a query param
    c = client([("GET", IMPORTS + "/external-source-import-history/CONTACT", rows[:1])])
    assert c.get_audience_import_history("CONTACT") == rows[:1]
    with pytest.raises(ValueError):
        c.get_audience_import_history("deal")


def test_import_history_tolerates_wrapped_or_null(client):
    # Live shape is a bare list; one key wrapping the rows is unwrapped and a JSON null body
    # yields []. (A truly empty body is not JSON at all: ClayClient.get raises JSONDecodeError.)
    assert client([("GET", IMPORTS + "/external-source-import-history/ACCOUNT", {"history": [{"importId": "x"}]})]).get_audience_import_history("ACCOUNT") == [{"importId": "x"}]
    assert client([("GET", IMPORTS + "/external-source-import-history/ACCOUNT", {"history": []})]).get_audience_import_history("ACCOUNT") == []
    assert client([("GET", IMPORTS + "/external-source-import-history/ACCOUNT", None)]).get_audience_import_history("ACCOUNT") == []


@pytest.mark.parametrize("body,shape", [
    ({}, "dict with keys []"),
    ({"message": "forbidden", "errors": [{"code": "E1"}]}, "dict with keys ['errors', 'message']"),  # never rows
    ({"warnings": [], "history": [{"importId": "a"}]}, "dict with keys ['history', 'warnings']"),
    ({"activityImports": [{"importId": "a"}], "objectImports": [{"importId": "o"}]}, "dict with keys ['activityImports', 'objectImports']"),
    ({"importId": "audimp_1", "importSyncType": "sync_full"}, "dict with keys ['importId', 'importSyncType']"),  # a bare row
    ({"history": ["audimp_1"]}, "dict with keys ['history']"),  # one key, but not a list of rows
    ({"history": {"importId": "x"}}, "dict with keys ['history']"),
    ("OK", "str"),
    (5, "int"),
    (True, "bool"),
])
def test_import_history_rejects_any_other_shape(client, body, shape):
    c = client([("GET", IMPORTS + "/external-source-import-history/ACCOUNT", body)])
    with pytest.raises(ValueError, match=r"get_audience_import_history: unexpected response shape \(" + re.escape(shape) + r"\)"):
        c.get_audience_import_history("ACCOUNT")


def test_import_history_workspace_override_reaches_the_path(client):
    c = client([("GET", "/workspaces/999/audiences/imports/external-source-import-history/CONTACT", [{"importId": "w"}])])
    assert c.get_audience_import_history("contact", workspace_id=999) == [{"importId": "w"}]
    assert c.session.calls[-1]["path"] == "/workspaces/999/audiences/imports/external-source-import-history/CONTACT"


@pytest.mark.parametrize("call", [lambda c: c.get_audience_import_history("ACCOUNT"),
                                  lambda c: c.get_audience_import_sync_status("audimp_1")])
def test_import_reads_propagate_http_errors(client, call):
    # ClayClient.get raises on a 5xx: neither read swallows it (the caller decides on a retry).
    c = client()
    c.session.get = lambda url, **kw: FakeResponse({"message": "Internal Server Error"}, status=500)
    with pytest.raises(requests.HTTPError, match="500"):
        call(c)


def test_salesforce_fields_path_and_params(client):
    c = client([("GET", IMPORTS + "/salesforce-fields/contact", {"fields": [{"value": "Email"}]})])
    assert c.list_salesforce_import_fields("contact", auth_account_id="aa_9") == [{"value": "Email"}]
    assert c.session.calls[-1]["params"] == {"authAccountId": "aa_9"}


def test_create_audience_fields_body_and_defaults(client):
    c = client([("POST", FIELD, _created)])
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
    c = client([("PATCH", SFI, {"ok": 1})])
    c.update_salesforce_import_field_mapping(
        "audimp_1",
        [{"audienceFieldId": "org_name", "salesforceFieldId": "Name", "extra": "dropped"},
         {"audienceFieldId": "audf_1", "salesforceFieldId": "X__c", "mappingRule": "ALWAYS_WRITE"}],
        entity_type="account", is_import_sync_enabled=True, is_export_sync_enabled=True,
        is_create_new_records_enabled=False, create_new_records_id_mapping=None, is_task_sync_enabled=False)
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
    with pytest.raises(ValueError, match=r"update_salesforce_import_field_mapping: field_mapping\[0\] missing"):
        c.update_salesforce_import_field_mapping("audimp_1", [bad], entity_type="ACCOUNT", **FLAG_KW)
    assert c.session.calls == []


def test_update_mapping_rejects_non_dict_entries_and_non_list_mapping(client):
    c = client()
    with pytest.raises(ValueError, match=r"field_mapping\[1\] is not a mapping entry"):
        c.update_salesforce_import_field_mapping(
            "audimp_1", [{"audienceFieldId": "a", "salesforceFieldId": "X"}, "junk"], entity_type="ACCOUNT", **FLAG_KW)
    with pytest.raises(ValueError, match="field_mapping must be a list"):
        c.update_salesforce_import_field_mapping("audimp_1", {"audienceFieldId": "a"}, entity_type="ACCOUNT", **FLAG_KW)
    assert c.session.calls == []


def test_update_mapping_requires_every_sync_flag(client):
    """The five sync settings have no defaults (the PATCH replaces them): omitting them is a
    TypeError worded like Python's own, naming exactly the missing kwargs."""
    c = client()
    entry = [{"audienceFieldId": "a", "salesforceFieldId": "X"}]
    with pytest.raises(TypeError, match="missing 5 required keyword-only arguments: 'is_import_sync_enabled', "
                                        "'is_export_sync_enabled', 'is_create_new_records_enabled', "
                                        "'create_new_records_id_mapping', and 'is_task_sync_enabled'"):
        c.update_salesforce_import_field_mapping("audimp_1", entry, entity_type="ACCOUNT")
    with pytest.raises(TypeError, match="missing 4 required keyword-only arguments"):
        c.update_salesforce_import_field_mapping("audimp_1", entry, entity_type="ACCOUNT", is_export_sync_enabled=True)
    with pytest.raises(TypeError, match="missing 1 required keyword-only argument: 'is_task_sync_enabled'"):
        c.update_salesforce_import_field_mapping("audimp_1", entry, entity_type="ACCOUNT",
                                                 **{k: v for k, v in FLAG_KW.items() if k != "is_task_sync_enabled"})
    assert c.session.calls == []


@pytest.mark.parametrize("name,val", [("is_task_sync_enabled", None), ("is_export_sync_enabled", "true"),
                                      ("is_import_sync_enabled", 1)])
def test_update_mapping_rejects_non_bool_flag(client, name, val):
    c = client()
    with pytest.raises(ValueError, match=f"{name} must be True or False, got {val!r}"):
        c.update_salesforce_import_field_mapping("audimp_1", [{"audienceFieldId": "a", "salesforceFieldId": "X"}],
                                                 entity_type="ACCOUNT", **{**FLAG_KW, name: val})
    assert c.session.calls == []


def test_update_mapping_rejects_non_dict_id_mapping(client):
    c = client()
    with pytest.raises(ValueError, match="create_new_records_id_mapping must be a dict or None, got 'garbage'"):
        c.update_salesforce_import_field_mapping("audimp_1", [{"audienceFieldId": "a", "salesforceFieldId": "X"}],
                                                 entity_type="ACCOUNT", **{**FLAG_KW, "create_new_records_id_mapping": "garbage"})
    assert c.session.calls == []


def test_update_mapping_accepts_sync_flags_instead_of_the_five_kwargs(client):
    """`sync_flags` (camelCase, keyed like importMetadata) is the alternative to the five
    kwargs — mutually exclusive, complete, and type-checked by the same helper."""
    c = client([("PATCH", SFI, {"ok": 1})])
    entry = [{"audienceFieldId": "a", "salesforceFieldId": "X"}]
    c.update_salesforce_import_field_mapping("audimp_1", entry, entity_type="ACCOUNT", sync_flags=FLAGS)
    body = c.session.calls[-1]["json"]["audienceImports"][0]
    assert (body["isImportSyncEnabled"], body["isExportSyncEnabled"], body["isCreateNewRecordsEnabled"],
            body["createNewRecordsIdMapping"], body["isTaskSyncEnabled"]) == (True, True, False, None, True)
    with pytest.raises(ValueError, match="pass either sync_flags= or the five sync-flag kwargs .*, not both"):
        c.update_salesforce_import_field_mapping("audimp_1", entry, entity_type="ACCOUNT", sync_flags=FLAGS, is_task_sync_enabled=True)
    with pytest.raises(ValueError, match="sync_flags is missing isTaskSyncEnabled; it must carry all five"):
        c.update_salesforce_import_field_mapping("audimp_1", entry, entity_type="ACCOUNT",
                                                 sync_flags={k: v for k, v in FLAGS.items() if k != "isTaskSyncEnabled"})
    with pytest.raises(ValueError, match=r"unknown sync_flags key\(s\) is_task_sync_enabled"):
        c.update_salesforce_import_field_mapping("audimp_1", entry, entity_type="ACCOUNT", sync_flags={**FLAGS, "is_task_sync_enabled": True})
    with pytest.raises(ValueError, match=r"sync_flags\['isExportSyncEnabled'\] must be True or False, got None"):
        c.update_salesforce_import_field_mapping("audimp_1", entry, entity_type="ACCOUNT", sync_flags={**FLAGS, "isExportSyncEnabled": None})
    assert len(c.session.calls) == 1


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
    assert out["skipped"] == ["Industry"] and out["dry_run"] is False
    post = [x for x in c.session.calls if x["method"] == "POST"][0]["json"]
    assert post["entityType"] == "ACCOUNT"
    assert [(f["displayName"], f["dataType"]) for f in post["audienceFields"]] == [
        ("Annual Revenue", "number"), ("Partner?", "boolean"), ("Website", "url"),
        ("Last Touch", "date"), ("Notes", "text")]
    assert [(t["salesforceFieldId"], t["displayName"], t["dataType"]) for t in out["to_create"]] == [
        ("AnnualRevenue", "Annual Revenue", "number"), ("Is_Partner__c", "Partner?", "boolean"),
        ("Website", "Website", "url"), ("Last_Touch__c", "Last Touch", "date"), ("Notes__c", "Notes", "text")]
    patch = _patch_import(c)
    mapping = patch["fieldMapping"]
    assert mapping[:2] == [  # existing entries preserved verbatim, incl. a non-default rule
        {"type": "SALESFORCE", "audienceFieldId": "org_name", "salesforceFieldId": "Name", "mappingRule": "NEVER_WRITE"},
        {"type": "SALESFORCE", "audienceFieldId": "audf_old", "salesforceFieldId": "Industry", "mappingRule": "ALWAYS_WRITE"}]
    assert [(m["audienceFieldId"], m["salesforceFieldId"]) for m in mapping[2:]] == [
        ("audf_new0", "AnnualRevenue"), ("audf_new1", "Is_Partner__c"), ("audf_new2", "Website"),
        ("audf_new3", "Last_Touch__c"), ("audf_new4", "Notes__c")]
    # all five sync settings carried over from importMetadata, none defaulted
    assert (patch["isImportSyncEnabled"], patch["isExportSyncEnabled"], patch["isCreateNewRecordsEnabled"],
            patch["createNewRecordsIdMapping"], patch["isTaskSyncEnabled"]) == (True, True, False, None, True)
    assert out["import"]["status"] == "PENDING" and len(out["created_fields"]) == 5
    # import read, catalog read, create, re-read before the PATCH (drift check), PATCH
    assert _methods(c) == ["GET", "GET", "POST", "GET", "PATCH"]


def test_add_fields_nothing_new_makes_no_writes(client):
    c = client(_sf_routes())
    out = c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Name"}])
    assert out["created_fields"] == [] and out["skipped"] == ["Name"] and out["to_create"] == []
    assert _methods(c) == ["GET", "GET"]


def test_add_fields_dry_run_returns_the_plan_and_writes_nothing(client):
    c = client(_sf_routes())
    out = c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}, {"salesforceFieldId": "Industry"}], dry_run=True)
    assert out["dry_run"] is True and out["created_fields"] == [] and out["skipped"] == ["Industry"]
    assert out["to_create"] == [{"salesforceFieldId": "Website", "displayName": "Website", "dataType": "url"}]
    assert out["import"]["id"] == "audimp_1"
    assert _methods(c) == ["GET", "GET"]


def test_add_fields_unknown_sf_field_fails_before_any_write(client):
    c = client(_sf_routes())
    with pytest.raises(ValueError, match="not a mappable field"):
        c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}, {"salesforceFieldId": "Nope__c"}])
    assert _methods(c) == ["GET", "GET"]


def test_add_fields_import_not_found_or_not_salesforce(client):
    with pytest.raises(ValueError, match="not found"):
        client(_sf_routes()).add_salesforce_import_fields("audimp_x", [{"salesforceFieldId": "Website"}])
    cpj = _import(importSourceType="CPJ", importMetadata={"type": "CPJ"})
    with pytest.raises(ValueError, match="not a Salesforce import"):
        client(_sf_routes(cpj)).add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}])


@pytest.mark.parametrize("over,match", [
    ({"entityType": None}, "has entityType None; expected ACCOUNT or CONTACT"),
    ({"entityType": "CUSTOM"}, "has entityType 'CUSTOM'; expected ACCOUNT or CONTACT"),
    ({"importSourceSubtype": None}, "has no importSourceSubtype"),
    ({"importMetadata": {"type": "SALESFORCE", **FLAGS}}, "importMetadata has no appAccountId"),
    ({"importMetadata": ["not", "an", "object"]}, "importMetadata is not an object"),
    ({"fieldMapping": "junk"}, "fieldMapping has an unexpected shape"),
    ({"fieldMapping": {"fieldMappings": "junk"}}, "fieldMapping.fieldMappings is not a list"),
])
def test_incomplete_import_object_fails_before_any_write(client, over, match):
    c = client(_sf_routes(_import(**over)))
    with pytest.raises(ValueError, match=match):
        c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}])
    assert _methods(c) == ["GET"]


def test_flat_list_field_mapping_is_accepted(client):
    imp = _import()
    imp["fieldMapping"] = imp["fieldMapping"]["fieldMappings"]  # the shape the PATCH is sent
    c = client(_sf_routes(imp))
    c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}])
    assert [m["salesforceFieldId"] for m in _patch_import(c)["fieldMapping"]] == ["Name", "Industry", "Website"]


# ── F5: one field per Salesforce API name ────────────────────────────────────

def test_duplicate_new_field_is_created_and_mapped_once(client):
    c = client(_sf_routes())
    out = c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}, {"salesforceFieldId": "Website"}])
    assert len(_post_fields(c)) == 1
    assert [m["salesforceFieldId"] for m in _patch_import(c)["fieldMapping"]].count("Website") == 1
    assert len(out["created_fields"]) == 1


def test_duplicate_entries_merge_when_they_agree(client):
    c = client(_sf_routes())
    c.add_salesforce_import_fields("audimp_1", [
        {"salesforceFieldId": "Website"},
        {"salesforceFieldId": "Website", "displayName": "Site"},
        {"salesforceFieldId": "Website", "dataType": "text", "displayName": "Site"},
        {"salesforceFieldId": "Notes__c"},
    ])
    assert _post_fields(c) == [{"displayName": "Site", "fieldType": "SCALAR", "dataType": "text"},
                               {"displayName": "Notes", "fieldType": "SCALAR", "dataType": "text"}]  # first-seen order kept


@pytest.mark.parametrize("a,b,key", [
    ({"displayName": "Site"}, {"displayName": "Web"}, "displayName"),
    ({"dataType": "text"}, {"dataType": "url"}, "dataType"),
])
def test_duplicate_entries_that_disagree_raise_before_any_call(client, a, b, key):
    c = client(_sf_routes())
    with pytest.raises(ValueError, match=f"conflicting entries for salesforceFieldId 'Website': {key}"):
        c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website", **a}, {"salesforceFieldId": "Website", **b}])
    assert c.session.calls == []


def test_empty_string_display_name_or_data_type_is_not_a_conflict(client):
    c = client(_sf_routes())
    c.add_salesforce_import_fields("audimp_1", [
        {"salesforceFieldId": "Website", "displayName": "", "dataType": ""},
        {"salesforceFieldId": "Website", "displayName": "Site"},
    ])
    assert _post_fields(c) == [{"displayName": "Site", "fieldType": "SCALAR", "dataType": "url"}]  # "" -> catalog default


def test_duplicate_of_already_mapped_field_is_skipped_once(client):
    c = client(_sf_routes())
    out = c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Industry"}, {"salesforceFieldId": "Industry"}])
    assert out["skipped"] == ["Industry"] and out["created_fields"] == []
    assert _methods(c) == ["GET", "GET"]


def test_new_field_without_sf_id_raises_before_any_call(client):
    c = client(_sf_routes())
    with pytest.raises(ValueError, match=r"new_fields\[1\] missing salesforceFieldId"):
        c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}, {"displayName": "x"}])
    assert c.session.calls == []


def test_empty_new_fields_raises_before_any_call(client):
    c = client(_sf_routes())
    with pytest.raises(ValueError, match="new_fields is empty"):
        c.add_salesforce_import_fields("audimp_1", [])
    with pytest.raises(ValueError, match="new_fields must be a list"):
        c.add_salesforce_import_fields("audimp_1", {"salesforceFieldId": "Website"})
    assert c.session.calls == []


# ── F3: sync settings fail closed ────────────────────────────────────────────

def test_missing_sync_flags_refuse_to_guess(client):
    """importMetadata without the five sync settings: the PATCH would replace them, so the
    method refuses instead of defaulting — after the import read only (no catalog read, no
    POST, no PATCH)."""
    c = client(_sf_routes(_import(importMetadata={"type": "SALESFORCE", "appAccountId": "aa_1"})))  # flags absent
    with pytest.raises(ValueError, match=r"importMetadata is missing sync setting\(s\) isImportSyncEnabled, "
                                         r"isExportSyncEnabled, isCreateNewRecordsEnabled, isTaskSyncEnabled, "
                                         r"createNewRecordsIdMapping; refusing to guess"):
        c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}])
    assert _methods(c) == ["GET"]


def test_missing_task_sync_flag_names_the_exact_remedy(client):
    """The live case: a CONTACT import's importMetadata without isTaskSyncEnabled (observed
    2026-09-29). The message names the key, the sync_flags= remedy and the live observation."""
    imp = _import(entityType="CONTACT", importSourceSubtype="contact", importMetadata=_meta(isTaskSyncEnabled=...))
    c = client([("GET", IMPORTS, {"audienceImports": [imp]})])
    with pytest.raises(ValueError) as ei:
        c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}])
    msg = str(ei.value)
    assert "importMetadata is missing sync setting(s) isTaskSyncEnabled;" in msg
    assert 'sync_flags={"isTaskSyncEnabled": False}' in msg
    assert "isTaskSyncEnabled was absent from a live CONTACT import's importMetadata on 2026-09-29" in msg
    assert _methods(c) == ["GET"]


def test_partially_missing_sync_flags_name_only_the_missing_ones(client):
    meta = _meta(isExportSyncEnabled=..., isTaskSyncEnabled=None)  # one absent, one null
    c = client(_sf_routes(_import(importMetadata=meta)))
    with pytest.raises(ValueError, match=r"missing sync setting\(s\) isExportSyncEnabled, isTaskSyncEnabled;") as ei:
        c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}])
    assert 'sync_flags={"isExportSyncEnabled": False, "isTaskSyncEnabled": False}' in str(ei.value)
    assert _methods(c) == ["GET"]


def test_wrongly_typed_meta_flag_is_reported_as_such_not_as_missing(client):
    c = client(_sf_routes(_import(importMetadata=_meta(isTaskSyncEnabled="true"))))
    with pytest.raises(ValueError, match=r"has sync setting\(s\) of an unexpected type: isTaskSyncEnabled='true' "
                                         r"\(expected True or False\)") as ei:
        c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}])
    assert "is missing" not in str(ei.value)
    assert _methods(c) == ["GET"]


def test_sync_flags_override_fills_missing_and_wins_over_meta(client):
    meta = _meta(isExportSyncEnabled=False, isTaskSyncEnabled=...)  # isTaskSyncEnabled absent
    c = client(_sf_routes(_import(importMetadata=meta)))
    out = c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}],
                                         sync_flags={"isTaskSyncEnabled": True, "isExportSyncEnabled": True})
    patch = _patch_import(c)
    assert (patch["isImportSyncEnabled"], patch["isExportSyncEnabled"], patch["isTaskSyncEnabled"]) == (True, True, True)
    assert len(out["created_fields"]) == 1


def test_sync_flags_override_replaces_a_wrongly_typed_meta_value(client):
    c = client(_sf_routes(_import(importMetadata=_meta(isTaskSyncEnabled="true"))))
    c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}], sync_flags={"isTaskSyncEnabled": False})
    assert _patch_import(c)["isTaskSyncEnabled"] is False


def test_sync_flags_unknown_key_raises_before_any_call(client):
    c = client(_sf_routes())
    with pytest.raises(ValueError, match=r"unknown sync_flags key\(s\) is_export_sync_enabled; expected any of isImportSyncEnabled"):
        c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}], sync_flags={"is_export_sync_enabled": True})
    assert c.session.calls == []


@pytest.mark.parametrize("bad", [["isTaskSyncEnabled"], "isTaskSyncEnabled", 7])
def test_non_dict_sync_flags_raises_before_any_call(client, bad):
    c = client(_sf_routes())
    with pytest.raises(ValueError, match="sync_flags must be a dict keyed like importMetadata"):
        c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}], sync_flags=bad)
    assert c.session.calls == []


@pytest.mark.parametrize("val", [None, "true", 1])
def test_bad_override_value_is_blamed_on_sync_flags_not_import_metadata(client, val):
    c = client(_sf_routes())
    with pytest.raises(ValueError, match=rf"sync_flags\['isTaskSyncEnabled'\] must be True or False, got {val!r}") as ei:
        c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}], sync_flags={"isTaskSyncEnabled": val})
    assert "importMetadata" not in str(ei.value).split("keyed like")[0]
    assert c.session.calls == []  # type-checked before the import read


def test_non_dict_id_mapping_override_raises_before_any_call(client):
    c = client(_sf_routes())
    with pytest.raises(ValueError, match=r"sync_flags\['createNewRecordsIdMapping'\] must be a dict or None, got 'oops'"):
        c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}], sync_flags={"createNewRecordsIdMapping": "oops"})
    assert c.session.calls == []


def test_create_new_records_without_id_mapping_is_refused_only_when_an_override_introduces_it(client):
    # the import's own state (True, None) passes through unchanged …
    c = client(_sf_routes(_import(importMetadata=_meta(isCreateNewRecordsEnabled=True))))
    c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}])
    patch = _patch_import(c)
    assert (patch["isCreateNewRecordsEnabled"], patch["createNewRecordsIdMapping"]) == (True, None)
    # … but an override that creates the combination is refused after the import read
    c = client(_sf_routes())
    with pytest.raises(ValueError, match="isCreateNewRecordsEnabled=True with createNewRecordsIdMapping=None"):
        c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}], sync_flags={"isCreateNewRecordsEnabled": True})
    assert _methods(c) == ["GET"]


def test_id_mapping_remedy_for_an_enabled_import_is_not_none_and_none_does_not_bypass_the_refusal(client):
    """isCreateNewRecordsEnabled=True with NO createNewRecordsIdMapping key: the remedy used to
    suggest sync_flags={"createNewRecordsIdMapping": None}, and passing exactly that slipped past
    the enabled-without-mapping refusal (an absent key read as the import's own null)."""
    meta = _meta(isCreateNewRecordsEnabled=True, createNewRecordsIdMapping=...)
    c = client(_sf_routes(_import(importMetadata=meta)))
    with pytest.raises(ValueError, match=r"is missing sync setting\(s\) createNewRecordsIdMapping;") as ei:
        c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}])
    msg = str(ei.value)
    assert "sync_flags={\"createNewRecordsIdMapping\": <the import's id-mapping dict>}" in msg
    assert '"createNewRecordsIdMapping": None' not in msg
    assert _methods(c) == ["GET"]
    # following the old remedy is refused after the import read: no catalog read, no writes
    c = client(_sf_routes(_import(importMetadata=meta)))
    with pytest.raises(ValueError, match="isCreateNewRecordsEnabled=True with createNewRecordsIdMapping=None"):
        c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}],
                                       sync_flags={"createNewRecordsIdMapping": None})
    assert _methods(c) == ["GET"]
    # the real remedy goes through and the PATCH carries it
    c = client(_sf_routes(_import(importMetadata=meta)))
    c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}],
                                   sync_flags={"createNewRecordsIdMapping": {"Id": "audf_old"}})
    patch = _patch_import(c)
    assert (patch["isCreateNewRecordsEnabled"], patch["createNewRecordsIdMapping"]) == (True, {"Id": "audf_old"})
    # with create-new-records off, None stays the example — and is accepted
    c = client(_sf_routes(_import(importMetadata=_meta(createNewRecordsIdMapping=...))))
    with pytest.raises(ValueError) as ei:
        c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}])
    assert 'sync_flags={"createNewRecordsIdMapping": None}' in str(ei.value)
    c = client(_sf_routes(_import(importMetadata=_meta(createNewRecordsIdMapping=...))))
    c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}], sync_flags={"createNewRecordsIdMapping": None})
    assert (_patch_import(c)["isCreateNewRecordsEnabled"], _patch_import(c)["createNewRecordsIdMapping"]) == (False, None)


# ── F4: validate everything before the first write; orphan story after it ────

def test_bad_existing_mapping_fails_before_any_write(client):
    """An existing mapping entry without audienceFieldId fails the call after the import read:
    no catalog read, no create — the PATCH replaces the whole list, so the pair is never
    dropped either (was ['GET', 'GET', 'POST'] before the fix)."""
    imp = _import()
    imp["fieldMapping"]["fieldMappings"].append({"type": "SALESFORCE", "salesforceFieldId": "Owner"})
    c = client(_sf_routes(imp))
    with pytest.raises(ValueError, match=r"add_salesforce_import_fields: existing mapping entry fieldMappings\[2\] missing audienceFieldId"):
        c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}])
    assert _methods(c) == ["GET"]


def test_shared_display_name_among_new_fields_raises_before_any_write(client):
    c = client(_sf_routes())
    with pytest.raises(ValueError, match=r"two new fields would share displayName 'Same' \(Website, Notes__c\)"):
        c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website", "displayName": "Same"},
                                                    {"salesforceFieldId": "Notes__c", "displayName": "Same"}])
    assert _methods(c) == ["GET", "GET"]


def _assert_unpaired(err, ids):
    assert isinstance(err, cc.AudienceFieldsOrphanedError) and isinstance(err, RuntimeError)
    assert err.created_field_ids == ids and err.mapping is None and err.pairing_verified is False
    assert err.import_id == "audimp_1" and err.entity_type == "ACCOUNT" and err.workspace_id == WS
    assert err.sync_flags == FLAGS and err.mapped_after_failure is None
    msg = str(err)
    assert "the mapping PATCH was not sent" in msg and "err.mapping is None" in msg
    assert "finish the mapping" not in msg and "Re-send" not in msg and "err.sync_flag_kwargs" not in msg
    assert "after checking each field's displayName" in msg
    assert "clay audiences fields delete <audf_id> --entity-type companies" in msg
    assert f"after confirming 'clay whoami' reports workspace {WS}" in msg


def test_add_fields_created_count_mismatch_raises_before_patch(client):
    c = client(_sf_routes(created=lambda call: _created(call)[:1]))
    with pytest.raises(RuntimeError) as ei:  # the orphan error subclasses RuntimeError
        c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}, {"salesforceFieldId": "Notes__c"}])
    _assert_unpaired(ei.value, ["audf_new0"])
    assert "expected 2 created fields, got 1 (audf_new0)" in str(ei.value)
    assert "PATCH" not in _methods(c)


def test_created_fields_that_do_not_pair_with_the_request_raise_before_patch(client):
    def swapped(call):
        out = _created(call)
        out[0]["displayName"], out[1]["displayName"] = out[1]["displayName"], out[0]["displayName"]
        return out
    c = client(_sf_routes(created=swapped))
    with pytest.raises(cc.AudienceFieldsOrphanedError, match="do not pair with the request .*expected displayName 'Website', got 'Notes' with id 'audf_new0'") as ei:
        c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}, {"salesforceFieldId": "Notes__c"}])
    _assert_unpaired(ei.value, ["audf_new0", "audf_new1"])
    assert "PATCH" not in _methods(c)


def test_create_response_without_display_names_is_unpaired_for_two_fields_but_fine_for_one(client):
    bare = lambda call: [{"id": f"audf_new{i}"} for i, _ in enumerate(call["json"]["audienceFields"])]  # noqa: E731
    c = client(_sf_routes(created=bare))
    with pytest.raises(cc.AudienceFieldsOrphanedError, match="does not echo displayName, so 2 created ids cannot be paired") as ei:
        c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}, {"salesforceFieldId": "Notes__c"}])
    _assert_unpaired(ei.value, ["audf_new0", "audf_new1"])
    assert "PATCH" not in _methods(c)
    c = client(_sf_routes(created=bare))  # a single field cannot be misordered
    out = c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}])
    assert out["created_fields"] == [{"id": "audf_new0"}] and _methods(c)[-1] == "PATCH"


def test_created_field_without_id_is_unpaired(client):
    c = client(_sf_routes(created=lambda call: [{"displayName": "Website"}]))
    with pytest.raises(cc.AudienceFieldsOrphanedError, match="a created field has no id") as ei:
        c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}])
    assert ei.value.created_field_ids == [None] and ei.value.mapping is None and "PATCH" not in _methods(c)


@pytest.mark.parametrize("shape", [lambda call: {"audienceFields": _created(call)}, lambda call: "created", lambda call: [["audf_new0"]]])
def test_non_list_create_response_raises_orphan_error(client, shape):
    c = client(_sf_routes(created=shape))
    with pytest.raises(cc.AudienceFieldsOrphanedError, match="create_audience_fields returned an unexpected shape") as ei:
        c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}])
    _assert_unpaired(ei.value, [])
    assert ei.value.created_fields == [] and "PATCH" not in _methods(c)


def test_patch_rejected_after_create_raises_orphan_error_with_ids_and_exact_mapping(client):
    c = client(_sf_routes(patch=_boom_http))
    with pytest.raises(cc.AudienceFieldsOrphanedError, match=r"created 2 Audiences field\(s\) audf_new0, audf_new1 .* mapping PATCH failed \(HTTPError: 500 Server Error\)") as ei:
        c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}, {"salesforceFieldId": "Notes__c"}])
    err = ei.value
    assert isinstance(err, RuntimeError) and isinstance(err.__cause__, requests.HTTPError)
    assert err.import_id == "audimp_1" and err.entity_type == "ACCOUNT" and err.workspace_id == WS
    assert err.created_field_ids == ["audf_new0", "audf_new1"] and err.pairing_verified is True
    assert [(m["audienceFieldId"], m["salesforceFieldId"]) for m in err.mapping] == [
        ("org_name", "Name"), ("audf_old", "Industry"), ("audf_new0", "Website"), ("audf_new1", "Notes__c")]
    assert err.sync_flags == FLAGS and err.sync_flag_kwargs() == FLAG_KW
    assert err.mapped_after_failure is False  # the re-read (original import) shows them unmapped
    msg = str(err)
    assert "Clay rejected the request, so the fields exist and are NOT mapped (a re-read of the import shows them NOT mapped)" in msg
    assert "Re-send the mapping (idempotent — the PATCH is a full REPLACE): clay.update_salesforce_import_field_mapping(err.import_id, err.mapping, entity_type=err.entity_type, **err.sync_flag_kwargs())" in msg
    assert "If you would rather not map them, delete the orphans after confirming 'clay whoami' reports workspace 12345" in msg
    assert "clay audiences fields delete <audf_id> --entity-type companies" in msg
    assert _methods(c) == ["GET", "GET", "POST", "GET", "PATCH", "GET"]  # …, failed PATCH, post-failure re-read


@pytest.mark.parametrize("boom,exc_type", [(_boom_json, json.JSONDecodeError), (_boom_conn, requests.ConnectionError)])
def test_patch_failure_of_unknown_outcome_recommends_resend_not_delete(client, boom, exc_type):
    """A connection reset or a non-JSON 2xx after the PATCH was sent: the server may have
    applied the REPLACE. Here the post-failure re-read shows the fields unmapped, so the message
    says so (not "Clay rejected"), recommends the idempotent re-send and still refuses to
    recommend deletion."""
    c = client(_sf_routes(patch=boom))
    with pytest.raises(cc.AudienceFieldsOrphanedError) as ei:
        c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}])
    err = ei.value
    assert isinstance(err.__cause__, exc_type) and err.pairing_verified is True
    assert err.mapped_after_failure is False and err.created_field_ids == ["audf_new0"]
    msg = str(err)
    assert "after it was sent, and the fields exist and are NOT mapped (a re-read of the import shows them NOT mapped)" in msg
    assert "outcome is UNKNOWN" not in msg and "Clay rejected" not in msg
    assert "Re-send the mapping (idempotent" in msg
    assert "Do not delete the fields unless list_audience_imports() confirms they are unmapped" in msg
    assert "If you would rather not map them" not in msg
    assert "clay audiences fields delete <audf_id> --entity-type companies" in msg  # the only delete path, still named


def test_patch_failure_when_the_reread_shows_the_mapping_landed(client):
    """The PATCH raised after the server applied it: the post-failure re-read finds every
    created id mapped, so the message says there is nothing to re-send and not to delete."""
    reads = []

    def imports(call):
        reads.append(1)
        imp = _import()
        if len(reads) > 2:  # third read = after the failed PATCH: the REPLACE did land
            imp["fieldMapping"]["fieldMappings"].append(
                {"type": "SALESFORCE", "audienceFieldId": "audf_new0", "salesforceFieldId": "Website", "mappingRule": "NEVER_WRITE"})
        return {"audienceImports": [imp]}
    c = client(_sf_routes(imports=imports, patch=_boom_conn))
    with pytest.raises(cc.AudienceFieldsOrphanedError) as ei:
        c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}])
    err = ei.value
    assert err.mapped_after_failure is True
    msg = str(err)
    assert ("after it was sent, but the mapping is present (the server applied it or someone else mapped the fields) "
            "— nothing to re-send, do NOT delete (a re-read of the import shows the created fields ARE mapped)") in msg
    assert "Verify the mapping in Audiences > Settings" in msg
    assert "NOT mapped" not in msg and "outcome is UNKNOWN" not in msg   # the state follows the re-read, not the exception type
    assert "clay audiences fields delete" not in msg and "Re-send" not in msg


def test_rejected_patch_whose_mapping_is_nevertheless_present_is_not_called_unmapped(client):
    """An HTTPError, but the re-read shows every created id mapped (the server applied the REPLACE
    before answering with an error, or someone else mapped the fields meanwhile): the message used
    to assert "NOT mapped" from the exception type and contradict itself in the same sentence."""
    reads = []

    def imports(call):
        reads.append(1)
        imp = _import()
        if len(reads) > 2:  # third read = after the failed PATCH
            imp["fieldMapping"]["fieldMappings"].append(
                {"type": "SALESFORCE", "audienceFieldId": "audf_new0", "salesforceFieldId": "Website", "mappingRule": "NEVER_WRITE"})
        return {"audienceImports": [imp]}
    c = client(_sf_routes(imports=imports, patch=_boom_http))
    with pytest.raises(cc.AudienceFieldsOrphanedError) as ei:
        c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}])
    err = ei.value
    assert err.mapped_after_failure is True and isinstance(err.__cause__, requests.HTTPError)
    msg = str(err)
    assert (": the request was rejected but the mapping is present (the server applied it or someone else mapped the fields) "
            "— nothing to re-send, do NOT delete (a re-read of the import shows the created fields ARE mapped).") in msg
    assert "NOT mapped" not in msg and "Clay rejected the request, so" not in msg
    assert "Verify the mapping in Audiences > Settings" in msg
    assert "clay audiences fields delete" not in msg and "Re-send" not in msg


def test_patch_failure_when_the_reread_itself_fails(client):
    reads = []

    def imports(call):
        reads.append(1)
        if len(reads) > 2:
            raise requests.ConnectionError("down")
        return {"audienceImports": [_import()]}
    c = client(_sf_routes(imports=imports, patch=_boom_http))
    with pytest.raises(cc.AudienceFieldsOrphanedError) as ei:
        c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}])
    err = ei.value
    assert err.mapped_after_failure is None and isinstance(err.__cause__, requests.HTTPError)
    msg = str(err)
    assert "Clay rejected the request, but the outcome is unknown (the import could not be re-read to confirm)" in msg
    assert "NOT mapped" not in msg   # nothing was confirmed either way
    assert "Re-send the mapping (idempotent" in msg
    assert "Do not delete the fields unless list_audience_imports() confirms they are unmapped" in msg
    assert "If you would rather not map them" not in msg  # deletion is recommended only when confirmed unmapped


def test_orphan_error_carries_what_the_retry_needs(client):
    """The remediation in the message is real: replaying err.mapping with err.sync_flag_kwargs()
    through update_salesforce_import_field_mapping sends exactly the PATCH body that failed."""
    c = client(_sf_routes(patch=_boom_http))
    with pytest.raises(cc.AudienceFieldsOrphanedError) as ei:
        c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}])
    err = ei.value
    failed_body = [x for x in c.session.calls if x["method"] == "PATCH"][0]["json"]
    c2 = client([("PATCH", SFI, {"audienceImports": [{"id": "audimp_1"}]})])
    c2.update_salesforce_import_field_mapping(err.import_id, err.mapping, entity_type=err.entity_type, **err.sync_flag_kwargs())
    assert c2.session.calls[-1]["json"] == failed_body
    c3 = client([("PATCH", SFI, {"audienceImports": [{"id": "audimp_1"}]})])  # the sync_flags= spelling is equivalent
    c3.update_salesforce_import_field_mapping(err.import_id, err.mapping, entity_type=err.entity_type, sync_flags=err.sync_flags)
    assert c3.session.calls[-1]["json"] == failed_body


def test_orphan_error_pickles_and_copies(client, monkeypatch):
    c = client(_sf_routes(patch=_boom_http))
    with pytest.raises(cc.AudienceFieldsOrphanedError) as ei:
        c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}])
    err = ei.value
    monkeypatch.setitem(sys.modules, cc.__name__, cc)  # the suite loads the module under a test name
    clone = pickle.loads(pickle.dumps(err))
    assert type(clone) is type(err) and str(clone) == str(err)
    assert clone.created_field_ids == ["audf_new0"] and clone.mapping == err.mapping and clone.sync_flags == FLAGS
    assert clone.pairing_verified is True and clone.mapped_after_failure is False and clone.workspace_id == WS
    assert copy.copy(err).sync_flag_kwargs() == FLAG_KW
    bare = cc.AudienceFieldsOrphanedError("just a message")  # constructible from the message alone
    assert bare.created_field_ids == [] and bare.mapping is None and bare.sync_flag_kwargs() == {}


def test_people_import_remediation_names_people(client):
    imp = _import(entityType="CONTACT", importSourceSubtype="contact")
    routes = [("GET", IMPORTS, {"audienceImports": [imp]}), ("GET", IMPORTS + r"/salesforce-fields/contact", CATALOG),
              ("POST", FIELD, _created), ("PATCH", SFI, _boom_http)]
    with pytest.raises(cc.AudienceFieldsOrphanedError, match="--entity-type people") as ei:
        client(routes).add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}])
    assert ei.value.entity_type == "CONTACT"


def test_mapping_drift_between_the_two_import_reads_raises_before_patch(client):
    """A pair added in the UI between the first read and the PATCH would be dropped by the
    REPLACE: the re-read before the PATCH catches it, the PATCH is not sent, and err.mapping is
    the fresh mapping plus the new pairs (pairing verified), so the re-send appends to the
    new state."""
    reads = []

    def imports(call):
        reads.append(1)
        imp = _import()
        if len(reads) > 1:
            imp["fieldMapping"]["fieldMappings"].append(
                {"type": "SALESFORCE", "audienceFieldId": "audf_ui", "salesforceFieldId": "Phone", "mappingRule": "NEVER_WRITE"})
        return {"audienceImports": [imp]}
    c = client(_sf_routes(imports=imports))
    with pytest.raises(cc.AudienceFieldsOrphanedError, match=r"mapping changed between the first read and the PATCH \(2 -> 3 pair\(s\)\)") as ei:
        c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}])
    err = ei.value
    assert err.pairing_verified is True and err.created_field_ids == ["audf_new0"] and err.mapped_after_failure is None
    assert [(m["audienceFieldId"], m["salesforceFieldId"]) for m in err.mapping] == [
        ("org_name", "Name"), ("audf_old", "Industry"), ("audf_ui", "Phone"), ("audf_new0", "Website")]
    assert "Re-send the mapping (idempotent" in str(err) and "the PATCH was not sent" in str(err)
    assert _methods(c) == ["GET", "GET", "POST", "GET"]


def test_reordered_but_identical_mapping_is_not_drift(client):
    reads = []

    def imports(call):
        reads.append(1)
        imp = _import()
        if len(reads) > 1:
            imp["fieldMapping"]["fieldMappings"].reverse()
        return {"audienceImports": [imp]}
    c = client(_sf_routes(imports=imports))
    c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}])
    assert _methods(c) == ["GET", "GET", "POST", "GET", "PATCH"]


def test_import_vanishing_before_the_patch_raises_orphan_error_without_a_mapping(client):
    reads = []

    def imports(call):
        reads.append(1)
        return {"audienceImports": [_import()] if len(reads) == 1 else []}
    c = client(_sf_routes(imports=imports))
    with pytest.raises(cc.AudienceFieldsOrphanedError, match="was not found .* when re-read before the mapping PATCH") as ei:
        c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}])
    assert ei.value.mapping is None and ei.value.created_field_ids == ["audf_new0"]
    assert "PATCH" not in _methods(c)


def test_reread_failure_before_the_patch_raises_orphan_error_with_the_initial_mapping(client):
    reads = []

    def imports(call):
        reads.append(1)
        if len(reads) > 1:
            raise requests.ConnectionError("down")
        return {"audienceImports": [_import()]}
    c = client(_sf_routes(imports=imports))
    with pytest.raises(cc.AudienceFieldsOrphanedError, match="re-reading the import before the mapping PATCH failed") as ei:
        c.add_salesforce_import_fields("audimp_1", [{"salesforceFieldId": "Website"}])
    err = ei.value
    assert isinstance(err.__cause__, requests.ConnectionError) and err.pairing_verified is True
    assert [(m["audienceFieldId"], m["salesforceFieldId"]) for m in err.mapping] == [
        ("org_name", "Name"), ("audf_old", "Industry"), ("audf_new0", "Website")]
    assert "confirm with list_audience_imports() that the import's fieldMappings still match it" in str(err)
    assert "PATCH" not in _methods(c)


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


def _segment(entity_type="ACCOUNT", **over):
    seg = {"id": "audseg_1", "entityType": entity_type, "filterAst": SAVED_AST}
    seg.update(over)
    return seg


def test_count_segment_uses_the_segments_own_entity_type(client):
    """count_audience_segment reads entityType from the fetched segment: an ACCOUNT segment
    counted without entity_type is sent as ACCOUNT with its own filter. (Before 2026-09-29 the
    default was CONTACT — a default that predates the PR — so the count was of the wrong entity.)"""
    c = client([("GET", SEG + "/audseg_1", _segment("ACCOUNT")), ("POST", COUNT, {"count": 214})])
    assert c.count_audience_segment("audseg_1") == 214
    body = c.session.calls[-1]["json"]
    assert body["entityType"] == "ACCOUNT" and body["segmentId"] == "audseg_1" and body["filters"] == SAVED_AST


def test_count_records_segment_without_entity_type(client):
    c = client([("GET", SEG + "/audseg_1", _segment("CONTACT")), ("POST", COUNT, {"count": 3})])
    assert c.count_audience_records(segment_id="audseg_1") == 3
    assert c.session.calls[-1]["json"]["entityType"] == "CONTACT"
    assert _methods(c) == ["GET", "POST"]


def test_count_segment_explicit_matching_entity_type_still_works(client):
    c = client([("GET", SEG + "/audseg_1", _segment("ACCOUNT")), ("POST", COUNT, {"count": 214})])
    assert c.count_audience_records("account", segment_id="audseg_1") == 214
    assert c.count_audience_segment("audseg_1", entity_type="ACCOUNT") == 214
    assert [x["json"]["entityType"] for x in c.session.calls if x["method"] == "POST"] == ["ACCOUNT", "ACCOUNT"]


def test_count_segment_explicit_mismatch_raises_without_counting(client):
    c = client([("GET", SEG + "/audseg_1", _segment("ACCOUNT")), ("POST", COUNT, {"count": 214})])
    with pytest.raises(ValueError, match="count_audience_records: segment 'audseg_1' is ACCOUNT, but entity_type='CONTACT' was passed"):
        c.count_audience_segment("audseg_1", entity_type="CONTACT")
    assert _methods(c) == ["GET"]


def test_count_segment_unsupported_entity_type_raises(client):
    c = client([("GET", SEG + "/audseg_1", _segment("CUSTOM")), ("POST", COUNT, {"count": 1})])
    with pytest.raises(ValueError, match="segment 'audseg_1' has entityType 'CUSTOM'; only ACCOUNT and CONTACT segments can be counted"):
        c.count_audience_segment("audseg_1")
    assert _methods(c) == ["GET"]


def test_count_segment_lacking_entity_type_needs_an_explicit_one(client):
    seg = {"id": "audseg_1", "filterAst": SAVED_AST}                      # no entityType key at all
    c = client([("GET", SEG + "/audseg_1", seg), ("POST", COUNT, {"count": 214})])
    with pytest.raises(ValueError, match="segment 'audseg_1' has no entityType; pass entity_type="):
        c.count_audience_segment("audseg_1")
    assert _methods(c) == ["GET"]
    assert c.count_audience_segment("audseg_1", entity_type="ACCOUNT") == 214
    assert c.session.calls[-1]["json"]["entityType"] == "ACCOUNT"


def test_count_segment_invalid_explicit_entity_type_is_rejected_before_the_get(client):
    c = client([("GET", SEG + "/audseg_1", _segment("ACCOUNT")), ("POST", COUNT, {"count": 214})])
    with pytest.raises(ValueError, match="entity_type must be ACCOUNT or CONTACT"):
        c.count_audience_records("deal", segment_id="audseg_1")
    assert c.session.calls == []


def test_whole_entity_and_adhoc_counts_still_require_entity_type(client):
    c = client()
    with pytest.raises(ValueError, match="entity_type is required unless segment_id is given"):
        c.count_audience_records()
    with pytest.raises(ValueError, match="entity_type is required unless segment_id is given"):
        c.count_audience_records(filter_ast=cc.af_and())
    with pytest.raises(ValueError, match="not both"):
        c.count_audience_records("ACCOUNT", filter_ast=cc.af_and(), segment_id="s")
    with pytest.raises(ValueError, match="not both"):
        c.count_audience_records(filter_ast=cc.af_and(), segment_id="s")
    assert c.session.calls == []


# ── export (entity type from the segment, like count) ────────────────────────

ACCOUNTS = f"/workspaces/{WS}/audiences/accounts"
CONTACTS = f"/workspaces/{WS}/audiences/contacts"


def _page(key, rows):
    return {key: rows, "pagination": {"hasMore": False}}


def test_export_segment_derives_the_entity_from_the_segment(client, tmp_path):
    row = {"entity": {"id": "aa_1", "fields": [{"field_id": "org_name", "value": "Acme"}]}}
    c = client([("GET", SEG + "/audseg_1", _segment("ACCOUNT")), ("POST", ACCOUNTS, _page("accounts", [row]))])
    out = c.export_audience_segment("audseg_1", format="json", output_dir=str(tmp_path))
    assert [(x["method"], x["path"]) for x in c.session.calls] == [("GET", SEG + "/audseg_1"), ("POST", ACCOUNTS)]
    body = c.session.calls[-1]["json"]
    assert body["segmentId"] == "audseg_1" and "includeData" not in body
    assert out["entity_type"] == "ACCOUNT" and out["row_count"] == 1 and out["content"]["entity_type"] == "ACCOUNT"
    assert Path(out["path"]).parent == tmp_path.resolve()


def test_export_segment_explicit_matching_entity_type_pages_contacts(client, tmp_path):
    row = {"entity": {"id": "aa_1", "fields": [{"field_id": "name", "value": "Test Person"}]}}
    c = client([("GET", SEG + "/audseg_1", _segment("CONTACT")), ("POST", CONTACTS, _page("contacts", [row]))])
    out = c.export_audience_segment("audseg_1", entity_type="contact", output_dir=str(tmp_path))
    assert c.session.calls[-1]["path"] == CONTACTS and c.session.calls[-1]["json"]["includeData"] == {"accountIds": True}
    assert out["entity_type"] == "CONTACT" and out["format"] == "csv"
    assert Path(out["path"]).read_text(encoding="utf-8").splitlines()[0] == "name"


def test_export_segment_explicit_mismatch_raises_before_the_post(client, tmp_path):
    c = client([("GET", SEG + "/audseg_1", _segment("ACCOUNT")), ("POST", ACCOUNTS, _page("accounts", []))])
    with pytest.raises(ValueError, match="export_audience_segment: segment 'audseg_1' is ACCOUNT, but entity_type='CONTACT' was passed"):
        c.export_audience_segment("audseg_1", entity_type="CONTACT", output_dir=str(tmp_path))
    assert _methods(c) == ["GET"]
    assert list(tmp_path.iterdir()) == []


def test_export_segment_unsupported_entity_type_raises_before_the_post(client, tmp_path):
    c = client([("GET", SEG + "/audseg_1", _segment("CUSTOM")), ("POST", ACCOUNTS, _page("accounts", []))])
    with pytest.raises(ValueError, match="only ACCOUNT and CONTACT segments can be exported"):
        c.export_audience_segment("audseg_1", output_dir=str(tmp_path))
    assert _methods(c) == ["GET"]


def test_export_segment_invalid_explicit_entity_type_is_rejected_before_the_get(client, tmp_path):
    c = client([("GET", SEG + "/audseg_1", _segment("ACCOUNT"))])
    with pytest.raises(ValueError, match="entity_type must be CONTACT or ACCOUNT"):
        c.export_audience_segment("audseg_1", entity_type="deal", output_dir=str(tmp_path))
    assert c.session.calls == []


def test_export_segment_custom_objects_are_refused_for_an_account_segment_after_the_get(client, tmp_path):
    c = client([("GET", SEG + "/audseg_1", _segment("ACCOUNT")), ("POST", ACCOUNTS, _page("accounts", []))])
    with pytest.raises(ValueError, match="include_custom_objects is CONTACT-only"):
        c.export_audience_segment("audseg_1", include_custom_objects=True, output_dir=str(tmp_path))
    assert _methods(c) == ["GET"]


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
