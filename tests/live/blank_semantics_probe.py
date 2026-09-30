#!/usr/bin/env python3
"""Gate A — Clay's blank semantics for Audiences filter operators (read-only counts).

Only ClayClient.count_audience_records(entity, filter_ast=...) is used (POST /audiences/count,
what the segment editor calls while you type; no credits, no writes). DRY RUN by default: prints
every AST it would send. --live sends them (needs CLAY_SESSION resolvable: run from the claycast
repo root, or pass clay_session=...).

  python3 live_probe.py --entity ACCOUNT --text-field audf_xxx --bool-field audf_yyy \
      [--date-field audf_zzz] [--value "Retail"] --workspace <id> [--live]

Read-only: only POST /audiences/count is called. --workspace is the id to probe (the
cookie's first workspace is the default and is usually not the one you mean).

Pick a TEXT field that actually has blanks (official CLI: `clay audiences fields list-values <id>`;
sum of value counts < total) and a BOOLEAN field with unset values (Salesforce checkboxes are
never null, so prefer a Clay-native / HubSpot / partially back-filled boolean).
"""
import argparse, importlib.util, json, os, sys, uuid

ap = argparse.ArgumentParser()
ap.add_argument("--entity", default="ACCOUNT")
ap.add_argument("--text-field", required=True)
ap.add_argument("--bool-field", required=True)
ap.add_argument("--date-field")
ap.add_argument("--value", help="a REAL value of --text-field (from fields list-values) for the A2/A3 checks")
ap.add_argument("--scripts-dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
ap.add_argument("--live", action="store_true")
ap.add_argument("--workspace", help="workspace id to probe (default: the cookie's first workspace — usually NOT what you want)")
args = ap.parse_args()

sys.path.insert(0, args.scripts_dir)
spec = importlib.util.spec_from_file_location("clay_client", os.path.join(args.scripts_dir, "clay_client.py"))
cc = importlib.util.module_from_spec(spec); spec.loader.exec_module(cc)
af_field, af_and, af_or = cc.af_field, cc.af_and, cc.af_or
ET, f, b, d, V = args.entity, args.text_field, args.bool_field, args.date_field, args.value
X = "__claycast_probe_" + uuid.uuid4().hex[:12]          # a value that exists nowhere
F = lambda op, v=None, **kw: af_field(ET, f, op, v, **kw)
B = lambda op, v=None, **kw: af_field(ET, b, op, v, **kw)
D = lambda op, v=None, **kw: af_field(ET, d, op, v, **kw)

probes = {
    # --- A1: Empty/NotEmpty are complements on the text field ---
    "T":            None,
    "E":            F("Empty"),
    "NE":           F("NotEmpty"),
    # --- sanity: X is truly absent ---
    "EQX":          F("Equal", X),                          # expect 0
    # --- F2: what do the negative operators do on a blank? ---
    "NEQX":         F("NotEqual", X),                       # == NE -> sql ; == T -> loose ; between -> mixed
    "NCX":          F("NotContain", X),                     # same trichotomy for NotContain
    "PIN_NEQ":      af_and(F("NotEmpty"), F("NotEqual", X)),      # minimal/c-f pin: expect == NE always
    "COMP_NEQ":     af_or(F("NotEqual", X), F("Empty")),          # ui-first companion: expect == T always
    "PR_BOTH_NEQ":  af_and(F("NotEqual", X), af_or(F("Empty"), af_and(F("Equal", X)))),  # PR pair overlap: >0 => F2 live
    # --- the '' hole: does Clay store "" and does Equal "" match it? ---
    "EQE":          F("Equal", ""),                         # >0 => '' cells exist AND Equal '' matches -> reject '' values
    "NEQE":         F("NotEqual", ""),
    # --- boolean field ---
    "TB":           B("True"),
    "FB":           B("False"),
    "EB":           B("Empty"),                             # HTTPError => Empty invalid on booleans (all designs' False pin breaks)
    "NEB":          B("NotEmpty"),
    "PIN_F":        af_and(B("NotEmpty"), B("False")),      # expect == T - TB - EB
    "COMP_F":       af_or(B("False"), B("Empty")),          # expect == T - TB
}
if V is not None:
    probes.update({
        "A3_EQV_ON_BLANK": af_and(F("Empty"), F("Equal", V)),        # expect 0 (positive op never matches a blank)
        "A3_CV_ON_BLANK":  af_and(F("Empty"), F("Contain", V)),      # expect 0
        "A2_EQV":          af_and(F("NotEmpty"), F("Equal", V)),
        "A2_NEQV":         af_and(F("NotEmpty"), F("NotEqual", V)),  # A2_EQV + A2_NEQV == NE
        "CASE_EQ_UPPER":   F("Equal", V.upper()),                    # == CASE_EQ_LOWER if Equal is case-insensitive
        "CASE_EQ_LOWER":   F("Equal", V.lower()),
        "CASE_NEQ_UPPER":  F("NotEqual", V.upper()),                 # T-shape check: NotEqual folds the same way as Equal
    })
if d:
    probes.update({
        "ED":           D("Empty"),
        "NED":          D("NotEmpty"),
        "NWL":          D("NotWithinLast", 30, time_unit="day"),   # == NED sql / == T loose
        "PIN_NWL":      af_and(D("NotEmpty"), D("NotWithinLast", 30, time_unit="day")),
        "WL_DAY":       D("WithinLast", 30, time_unit="day"),
        "WL_WEEK":      D("WithinLast", 4, time_unit="week"),
        "WL_MONTH":     D("WithinLast", 1, time_unit="month"),
        "WL_YEAR":      D("WithinLast", 1, time_unit="year"),      # expect HTTPError (enum has no year)
        "WL_NOUNIT":    {k: v for k, v in D("WithinLast", 30).items()},  # no timeUnit: 400? or silent default? (F6)
    })

def classify(neg, populated, total):
    if neg == populated: return "sql (blank never matches)"
    if neg == total: return "loose (blank matches)"
    if populated < neg < total: return "mixed ('' vs null treated differently)"
    return "anomaly (A1 broken or data moved between counts)"

if not args.live:
    print(json.dumps({"dry_run": True, "impossible_value": X, "probes": probes}, indent=1))
    sys.exit(0)

import requests, io, contextlib
with contextlib.redirect_stdout(io.StringIO()):   # the constructor prints the login email
    clay = cc.ClayClient(workspace_id=int(args.workspace) if args.workspace else None)
print(json.dumps({"probing_workspace": clay.workspace_id, "entity": ET, "text_field": f, "bool_field": b, "date_field": d}))
res, err = {}, {}
for name, ast in probes.items():
    try:
        res[name] = clay.count_audience_records(ET, filter_ast=ast)
    except requests.HTTPError as e:
        err[name] = f"{e.response.status_code}: {e.response.text[:200]}"
res["T_again"] = clay.count_audience_records(ET)
T = res["T"]
verdict = {
    "A1_text_Empty_plus_NotEmpty_equals_total": res["E"] + res["NE"] == T,
    "X_absent": res["EQX"] == 0,
    "NotEqual_on_blank": classify(res["NEQX"], res["NE"], T),
    "NotContain_on_blank": classify(res["NCX"], res["NE"], T),
    "pin_masks_blanks": res["PIN_NEQ"] == res["NE"],
    "companion_covers_blanks": res["COMP_NEQ"] == T,
    "F2_live_in_PR_pair_overlap": res["PR_BOTH_NEQ"],
    "empty_string_cells_matched_by_Equal": res["EQE"],
    "boolean_Empty_accepted": "EB" not in err,
    "counts_drifted_during_run": res["T_again"] != T,
}
if "EB" not in err:
    verdict["A1_bool"] = res["EB"] + res["NEB"] == T
    verdict["bool_has_blanks"] = res["EB"] > 0
    verdict["False_on_blank"] = (
        "sql (blank never matches)" if res["FB"] == T - res["TB"] - res["EB"] else
        "loose (blank matches)" if res["FB"] == T - res["TB"] else "mixed/anomaly")
if V is not None:
    verdict["A3_positive_never_matches_blank"] = res.get("A3_EQV_ON_BLANK") == 0 and res.get("A3_CV_ON_BLANK") == 0
    verdict["A2_Equal_NotEqual_complement_on_populated"] = res.get("A2_EQV", 0) + res.get("A2_NEQV", 0) == res["NE"]
    verdict["Equal_case_insensitive"] = res.get("CASE_EQ_UPPER") == res.get("CASE_EQ_LOWER")
if d:
    verdict["NotWithinLast_on_blank"] = classify(res.get("NWL", -1), res.get("NED", -2), T)
    verdict["timeUnit_year_rejected"] = "WL_YEAR" in err
    verdict["missing_timeUnit_rejected"] = "WL_NOUNIT" in err
print(json.dumps({"impossible_value": X, "counts": res, "errors": err, "verdict": verdict}, indent=1))
