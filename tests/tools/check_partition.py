#!/usr/bin/env python3
"""Brute-force partition checker for claycast `af_exclusion_pair()` candidates.

A candidate module must define, with clay_client's signatures:
    af_any_of(entity_type, field_id, rules) -> dict
    af_none_of(entity_type, field_id, rules) -> dict
    af_exclusion_pair(entity_type, rule_table) -> (excluded_items, included_items)
Rules are tuples: (op,), (op, value) or (op, value, time_unit). The module may import
af_field / af_and / af_or / af_root from clay_client (the repo's scripts/ dir is on sys.path
when the candidate is loaded) - do not copy them. The candidate may be scripts/clay_client.py
itself, which checks the shipped implementation.

Method: both sides of every rule table in BATTERY are evaluated over every record of a
small domain (blank = None or "") under EVERY combination of blank semantics for the
operators whose behaviour on a blank cell is unknown offline
    NotEqual, NotContain, False, NotWithinLast, NotWithinNext  -> each False or True on blank
= 32 models. Positive operators (Equal, Contain, True, WithinLast, WithinNext, ContainAny)
are False on a blank; Empty is True, NotEmpty False. A table PASSES when exactly one side
is true for every record under every model. Time operators must carry `timeUnit`.

Usage:  python3 tests/tools/check_partition.py <candidate.py> [--json]
Import: from check_partition import check_candidate
        check_candidate(path, extra_battery=[(name, [(field, kind, rules), ...]), ...])
"""
from __future__ import annotations

import importlib.util
import itertools
import json
import os
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"

UNKNOWN_ON_BLANK = ["NotEqual", "NotContain", "False", "NotWithinLast", "NotWithinNext"]
TIME_OPS = {"WithinLast", "NotWithinLast", "WithinNext", "NotWithinNext"}
DOMAINS = {
    "text": [None, "", "alpha", "beta", "alphabet"],
    "bool": [None, True, False],
    "date": [None, "recent", "old", "soon"],
}

# (name, [(field_id, kind, rules), ...])
BATTERY = [
    ("single_equal",            [("t1", "text", [("Equal", "alpha")])]),
    ("two_positive_same_field", [("t1", "text", [("Equal", "alpha"), ("Contain", "bet")])]),
    ("multi_field_positive",    [("t1", "text", [("Equal", "alpha")]),
                                 ("t2", "text", [("Contain", "alp"), ("Equal", "beta")])]),
    ("empty_alone",             [("t1", "text", [("Empty",)])]),
    ("empty_plus_equal",        [("t1", "text", [("Empty",), ("Equal", "alpha")])]),
    ("empty_and_notempty",      [("t1", "text", [("Empty",), ("NotEmpty",)])]),
    ("notempty_alone",          [("t1", "text", [("NotEmpty",)])]),
    ("notequal_alone",          [("t1", "text", [("NotEqual", "alpha")])]),
    ("notcontain_plus_equal",   [("t1", "text", [("NotContain", "alp"), ("Equal", "beta")])]),
    ("bool_false",              [("b1", "bool", [("False",)])]),
    ("bool_true",               [("b1", "bool", [("True",)])]),
    ("within_last",             [("d1", "date", [("WithinLast", 30, "day")])]),
    ("not_within_last",         [("d1", "date", [("NotWithinLast", 7, "week")])]),
    ("mixed_three_fields",      [("t1", "text", [("Equal", "alpha")]),
                                 ("t2", "text", [("NotEqual", "beta")]),
                                 ("t3", "text", [("Empty",)])]),
    ("bool_false_plus_text",    [("b1", "bool", [("False",)]),
                                 ("t1", "text", [("NotContain", "alp")])]),
]


class CheckError(Exception):
    pass


def load_candidate(path: str):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("af_candidate_" + str(abs(hash(path))), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_pr():
    """The repo's clay_client: supplies af_or / af_and / af_root to assemble both sides."""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    import clay_client  # noqa: WPS433
    return clay_client


def eval_bin(node, record, model):
    op = node["operator"]
    if op in TIME_OPS and "timeUnit" not in node:
        raise CheckError(f"{op} node lacks timeUnit: {node}")
    cell = record.get(node.get("key"))
    blank = cell is None or cell == ""
    if op == "Empty":
        return blank
    if op == "NotEmpty":
        return not blank
    if blank:
        return model.get(op, False)
    v = node.get("value")
    table = {
        "Equal": lambda: cell == v,
        "NotEqual": lambda: cell != v,
        "Contain": lambda: str(v) in str(cell),
        "NotContain": lambda: str(v) not in str(cell),
        "True": lambda: cell is True,
        "False": lambda: cell is False,
        "WithinLast": lambda: cell == "recent",
        "NotWithinLast": lambda: cell != "recent",
        "WithinNext": lambda: cell == "soon",
        "NotWithinNext": lambda: cell != "soon",
        "ContainAny": lambda: cell in (v or []),
        "NotContainAny": lambda: cell not in (v or []),
    }
    if op not in table:
        raise CheckError(f"unknown operator {op!r}")
    return table[op]()


def eval_node(node, record, model):
    t = node.get("type")
    if t == "GroupOp":
        items = node.get("items") or []
        if not items:
            return node.get("combinationMode") == "And"
        vals = [eval_node(i, record, model) for i in items]
        return all(vals) if node.get("combinationMode") == "And" else any(vals)
    if t == "BinOp":
        return eval_bin(node, record, model)
    raise CheckError(f"unsupported node type {t!r} (ColOp is not modelled)")


def count_binops(node):
    if node.get("type") == "BinOp":
        return 1
    return sum(count_binops(i) for i in node.get("items") or []) + (
        count_binops(node["condition"]) if isinstance(node.get("condition"), dict) else 0)


def all_have_ids(node):
    if "id" not in node:
        return False
    return all(all_have_ids(i) for i in node.get("items") or []) and (
        all_have_ids(node["condition"]) if isinstance(node.get("condition"), dict) else True)


def check_table(cand, pr, name, table):
    out = {"name": name, "passed": False, "error": None, "failing_models": 0, "examples": [],
           "rows_excluded": None, "rows_included": None, "id_stamp_ok": None}
    try:
        excl, incl = cand.af_exclusion_pair("ACCOUNT", [(f, rules) for f, _, rules in table])
        ex_ast, in_ast = pr.af_or(*excl), pr.af_and(*incl)
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    out["rows_excluded"], out["rows_included"] = count_binops(ex_ast), count_binops(in_ast)
    try:
        out["id_stamp_ok"] = all_have_ids(pr.af_root(ex_ast)) and all_have_ids(pr.af_root(in_ast))
    except Exception as e:  # noqa: BLE001
        out["id_stamp_ok"] = f"error: {e}"
    fields = [(f, kind) for f, kind, _ in table]
    failing = 0
    for bits in itertools.product([False, True], repeat=len(UNKNOWN_ON_BLANK)):
        model = dict(zip(UNKNOWN_ON_BLANK, bits))
        bad = None
        for values in itertools.product(*[DOMAINS[k] for _, k in fields]):
            rec = {f: v for (f, _), v in zip(fields, values)}
            try:
                e, i = eval_node(ex_ast, rec, model), eval_node(in_ast, rec, model)
            except CheckError as err:
                out["error"] = str(err)
                return out
            if e == i:
                bad = {"model": {k: v for k, v in model.items() if v}, "record": rec,
                       "outcome": "both" if e else "neither"}
                break
        if bad:
            failing += 1
            if len(out["examples"]) < 3:
                out["examples"].append(bad)
    out["failing_models"] = failing
    out["passed"] = failing == 0
    return out


def check_contracts(cand):
    """Behavioural contracts that are not partition questions."""
    res = {}
    try:
        cand.af_exclusion_pair("ACCOUNT", [("t1", [("ContainAny", ["a"])])])
        res["op_without_negation"] = "built silently (no ValueError)"
    except ValueError as e:
        res["op_without_negation"] = f"ValueError: {e}"
    except Exception as e:  # noqa: BLE001
        res["op_without_negation"] = f"{type(e).__name__}: {e}"
    try:
        ex, _ = cand.af_exclusion_pair("ACCOUNT", [("d1", [("WithinLast", 30)])])
        node = ex[0]
        while node.get("type") == "GroupOp":
            node = node["items"][0]
        res["time_op_without_unit"] = ("built WITHOUT timeUnit (bad)" if "timeUnit" not in node
                                       else f"built with timeUnit={node['timeUnit']!r}")
    except ValueError as e:
        res["time_op_without_unit"] = f"ValueError: {e}"
    except Exception as e:  # noqa: BLE001
        res["time_op_without_unit"] = f"{type(e).__name__}: {e}"
    try:
        ex, inc = cand.af_exclusion_pair("ACCOUNT", [("t1", [("Equal", "alpha")])])
        res["legacy_tuple_forms_accepted"] = bool(ex) and bool(inc)
    except Exception as e:  # noqa: BLE001
        res["legacy_tuple_forms_accepted"] = f"{type(e).__name__}: {e}"
    return res


def check_candidate(path, extra_battery=None):
    cand, pr = load_candidate(path), load_pr()
    tables = [check_table(cand, pr, n, t) for n, t in BATTERY + list(extra_battery or [])]
    return {"candidate": os.path.abspath(path), "tables": tables, "contracts": check_contracts(cand),
            "all_passed": all(t["passed"] for t in tables),
            "total_rows": sum((t["rows_excluded"] or 0) + (t["rows_included"] or 0) for t in tables)}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(2)
    result = check_candidate(args[0])
    if "--json" in sys.argv:
        print(json.dumps(result, indent=2, default=str))
    else:
        for t in result["tables"]:
            flag = "PASS" if t["passed"] else "FAIL"
            extra = f" error={t['error']}" if t["error"] else (
                f" failing_models={t['failing_models']}/32 e.g. {t['examples'][0]}" if t["examples"] else "")
            print(f"{flag:4} {t['name']:26} rows ex/in={t['rows_excluded']}/{t['rows_included']} ids={t['id_stamp_ok']}{extra}")
        print("contracts:", json.dumps(result["contracts"]))
        print(f"ALL_PASSED={result['all_passed']} total_rows={result['total_rows']}")
    sys.exit(0 if result["all_passed"] else 1)


if __name__ == "__main__":
    main()
