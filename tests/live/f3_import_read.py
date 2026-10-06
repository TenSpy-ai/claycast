#!/usr/bin/env python3
"""Gate C — where the Salesforce sync settings live (read-only).

Lists the workspace's Audiences imports and, for each Salesforce import, reports which of the
five sync settings the mapping PATCH replaces are present under importMetadata, and whether
any appear at the import's top level. A key that is absent is exactly the case
add_salesforce_import_fields() refuses to guess (pass sync_flags={...} for it).

    python tests/live/f3_import_read.py --workspace <id>
"""
import argparse

from _common import client, verdict

KEYS = ["isImportSyncEnabled", "isExportSyncEnabled", "isCreateNewRecordsEnabled",
        "createNewRecordsIdMapping", "isTaskSyncEnabled"]

ap = argparse.ArgumentParser()
ap.add_argument("--workspace", required=True)
args = ap.parse_args()

c = client(args.workspace)
imports = c.list_audience_imports(workspace_id=int(args.workspace))
print(f"{len(imports)} import(s) in workspace {args.workspace}")
sf = [i for i in imports if (i.get("importSourceType") or (i.get("importMetadata") or {}).get("type")) == "SALESFORCE"]
verdict("at least one Salesforce import", bool(sf), f"{len(sf)} found")
for imp in sf:
    meta = imp.get("importMetadata") or {}
    present = [k for k in KEYS if k in meta]
    missing = [k for k in KEYS if k not in meta]
    top = [k for k in imp if k in KEYS]
    print(f"\n  {imp.get('entityType')} import ({imp.get('importSourceSubtype')}), status={imp.get('status')}, mappings={len((imp.get('fieldMapping') or {}).get('fieldMappings') or [])}")
    # a missing key is not a defect — it is the case the SDK refuses to guess (see the remedy below)
    verdict("five sync keys under importMetadata", True if not missing else None,
            f"missing={missing}" if missing else "all present")
    verdict("no sync keys at the import's top level", not top, f"top-level={top}" if top else "")
    bad = [k for k in KEYS if k != "createNewRecordsIdMapping" and k in meta and not isinstance(meta[k], bool)]
    verdict("boolean flags are true/false (not null/strings)", not bad, f"non-boolean={bad}" if bad else "")
    idmap = meta.get("createNewRecordsIdMapping")
    verdict("createNewRecordsIdMapping is a mapping or null", idmap is None or isinstance(idmap, dict), repr(idmap)[:60])
    if missing:
        print(f"       -> add_salesforce_import_fields() will refuse on this import until you pass "
              f"sync_flags={{{', '.join(repr(k) + ': <True|False>' for k in missing)}}} after checking the UI toggles")
