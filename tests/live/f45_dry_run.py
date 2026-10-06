#!/usr/bin/env python3
"""Gate D — add_salesforce_import_fields() validation paths, with dry_run=True (read-only).

Exercises the fail-closed and dedupe behaviour against a real Salesforce import WITHOUT
writing: an unknown API name is rejected, an already-mapped name is skipped once, a repeated
name collapses to one planned field, a typo in sync_flags fails before any request, and an
import whose importMetadata lacks a sync key refuses until the key is supplied.

    python tests/live/f45_dry_run.py --workspace <id> [--import-id audimp_...] [--new-field <unmapped Salesforce API name>]

--new-field is optional: give a VALID, currently unmapped API name (from
list_salesforce_import_fields) to exercise the duplicate-collapse plan; the script never
creates it (dry_run=True).
"""
import argparse

from _common import client, verdict

ap = argparse.ArgumentParser()
ap.add_argument("--workspace", required=True)
ap.add_argument("--import-id")
ap.add_argument("--new-field")
args = ap.parse_args()
ws = int(args.workspace)
c = client(ws)

imports = [i for i in c.list_audience_imports(workspace_id=ws)
           if (i.get("importSourceType") or (i.get("importMetadata") or {}).get("type")) == "SALESFORCE"]
if args.import_id:
    imports = [i for i in imports if i["id"] == args.import_id]
if not imports:
    verdict("a Salesforce import to test against", False)
    raise SystemExit(1)

# a typo in sync_flags must fail before ANY request — even with a bogus import id
try:
    c.add_salesforce_import_fields("audimp_does_not_exist", [{"salesforceFieldId": "Name"}],
                                   sync_flags={"is_export_sync_enabled": True}, dry_run=True, workspace_id=ws)
    verdict("unknown sync_flags key fails before any request", False, "no error")
except ValueError as e:
    verdict("unknown sync_flags key fails before any request", "unknown sync_flags key" in str(e), str(e)[:80])

# conflicting duplicates must fail before ANY request
try:
    c.add_salesforce_import_fields("audimp_does_not_exist",
                                   [{"salesforceFieldId": "Website", "displayName": "A"},
                                    {"salesforceFieldId": "Website", "displayName": "B"}], dry_run=True, workspace_id=ws)
    verdict("conflicting duplicate entries fail before any request", False, "no error")
except ValueError as e:
    verdict("conflicting duplicate entries fail before any request", "conflicting entries" in str(e), str(e)[:80])

for imp in imports:
    iid, et = imp["id"], imp.get("entityType")
    mapped = [m.get("salesforceFieldId") for m in (imp.get("fieldMapping") or {}).get("fieldMappings") or []]
    print(f"\n{et} import: {len(mapped)} mapped field(s)")
    flags = {}

    def run(fields, label):
        try:
            out = c.add_salesforce_import_fields(iid, fields, dry_run=True, sync_flags=flags or None, workspace_id=ws)
            return out, None
        except ValueError as e:
            return None, e

    probe = [{"salesforceFieldId": mapped[0]}] if mapped else [{"salesforceFieldId": "Name"}]
    out, err = run(probe, "already-mapped")
    if err and "missing sync setting" in str(err):
        verdict("fail-closed when importMetadata lacks a sync key", True, str(err)[:110])
        # the message names the key; supply the value the UI/PR replay used and retry
        missing = [k for k in ("isImportSyncEnabled", "isExportSyncEnabled", "isCreateNewRecordsEnabled",
                               "createNewRecordsIdMapping", "isTaskSyncEnabled") if k in str(err)]
        flags = {k: (None if k == "createNewRecordsIdMapping" else False) for k in missing}
        print(f"       retrying with sync_flags={flags} (read-only)")
        out, err = run(probe, "already-mapped (with override)")
    if err:
        verdict("dry run on an already-mapped field", False, f"{type(err).__name__}: {str(err)[:100]}")
        continue
    verdict("dry run on an already-mapped field returns skipped once and writes nothing",
            out.get("dry_run") is True and out.get("created_fields") == [] and out.get("skipped") == [probe[0]["salesforceFieldId"]] and out.get("to_create") == [],
            f"skipped={out.get('skipped')} to_create={len(out.get('to_create') or [])}")

    out, err = run([{"salesforceFieldId": "This_Field_Does_Not_Exist__c"}], "unknown API name")
    verdict("unknown API name is rejected (no field created)", err is not None and "not a mappable field" in str(err),
            str(err)[:80] if err else "no error")

    if args.new_field:
        out, err = run([{"salesforceFieldId": args.new_field}, {"salesforceFieldId": args.new_field}], "duplicates")
        if err:
            verdict("duplicate new field collapses to one planned field", False, str(err)[:100])
        else:
            verdict("duplicate new field collapses to one planned field",
                    len(out.get("to_create") or []) == 1 and out.get("created_fields") == [],
                    f"plan={out.get('to_create')}")
    else:
        verdict("duplicate-collapse plan", None, "skipped — pass --new-field <valid unmapped API name> to exercise it")
