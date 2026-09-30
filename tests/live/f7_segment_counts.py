#!/usr/bin/env python3
"""Gate B — segment counts take the entity type from the segment (read-only).

For one ACCOUNT and one CONTACT segment: count_audience_segment(id) must equal
count_audience_segment(id, entity_type=<the segment's type>) and be close to the segment's
cached estimatedSize; passing the WRONG entity_type must raise ValueError after only the GET.

    python tests/live/f7_segment_counts.py --workspace <id> [--account-segment audseg_...] [--contact-segment audseg_...]
"""
import argparse

from _common import client, verdict

ap = argparse.ArgumentParser()
ap.add_argument("--workspace", required=True)
ap.add_argument("--account-segment")
ap.add_argument("--contact-segment")
args = ap.parse_args()
ws = int(args.workspace)
c = client(ws)


def pick(entity_type, wanted):
    segs = c.list_audience_segments(entity_type=entity_type, workspace_id=ws)
    if wanted:
        return next((s for s in segs if s["id"] == wanted), None)
    return next((s for s in segs if (s.get("filterAst") or {}).get("items")), segs[0] if segs else None)


for et, wanted in (("ACCOUNT", args.account_segment), ("CONTACT", args.contact_segment)):
    seg = pick(et, wanted)
    print(f"\n{et} segment {'(none found)' if not seg else ''}")
    if not seg:
        verdict(f"a {et} segment exists", False)
        continue
    sid = seg["id"]
    derived = c.count_audience_segment(sid, workspace_id=ws)
    explicit = c.count_audience_segment(sid, entity_type=et, workspace_id=ws)
    records = c.count_audience_records(segment_id=sid, workspace_id=ws)
    est = c.get_audience_segment(sid, workspace_id=ws).get("estimatedSize")
    verdict("count(id) == count(id, entity_type=<segment type>)", derived == explicit)
    verdict("count_audience_records(segment_id=id) agrees", records == derived)
    verdict("close to the cached estimatedSize", est is None or abs(int(est) - derived) <= max(5, derived // 100),
            "estimatedSize is Clay's cached number; a small drift is normal" if est is not None else "no estimatedSize on the segment")
    wrong = "CONTACT" if et == "ACCOUNT" else "ACCOUNT"
    try:
        c.count_audience_segment(sid, entity_type=wrong, workspace_id=ws)
        verdict(f"entity_type={wrong!r} on a {et} segment raises", False, "no error was raised")
    except ValueError as e:
        verdict(f"entity_type={wrong!r} on a {et} segment raises", True, f"ValueError: {str(e)[:90]}")
