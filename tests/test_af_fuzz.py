"""Fuzz the shipped af_* exclusion-pair builders through tests/tools/check_partition.py: every
single-field rule table of 1-3 rules over the text / bool / date operator sets (exhaustive), plus
random 2-3-field tables, under the checker's default model set (False fixed to "matches a
blank", measured live 2026-09-29; the four other negative operators enumerated both ways). Also
the structural promises the checker does not make: rule tables are never mutated, every BinOp
row is a distinct dict, af_root stamps every node on both sides, and time rows carry timeUnit.
Bounded to a few seconds."""
import itertools
import json
import random

import pytest

from conftest import SCRIPTS, all_nodes, cc, is_uuid
from tools.check_partition import TIME_OPS, check_candidate

A = "ACCOUNT"
CANDIDATE = str(SCRIPTS / "clay_client.py")

TEXT = [("Equal", "alpha"), ("Equal", "beta"), ("NotEqual", "alpha"), ("Contain", "alp"),
        ("NotContain", "bet"), ("Empty",), ("NotEmpty",)]
BOOL = [("True",), ("False",), ("Empty",), ("NotEmpty",)]
DATE = [("WithinLast", 30, "day"), ("NotWithinLast", 7, "week"), ("WithinNext", 2, "month"),
        ("NotWithinNext", 1, "day"), ("Empty",), ("NotEmpty",)]
KINDS = {"text": TEXT, "bool": BOOL, "date": DATE}
RANDOM_TABLES = 300


def _expect(table):
    """A field with a True/False rule must come out unpinned (one row per rule, no guard)."""
    bool_rules = any(op in ("True", "False") for _, _, rules in table for op, *_ in rules)
    return {"unpinned": True} if bool_rules and len(table) == 1 else None


def single_field_tables():
    for kind, ops in KINDS.items():
        for n in (1, 2, 3):
            for combo in itertools.combinations(ops, n):
                table = [("f1", kind, list(combo))]
                yield (f"1f_{kind}_{'+'.join(r[0] for r in combo)}", table, _expect(table))


def random_tables(seed, count):
    rng = random.Random(seed)
    for k in range(count):
        table = []
        for i in range(rng.choice([2, 3])):
            kind = rng.choice(list(KINDS))
            ops = KINDS[kind]
            table.append((f"f{i + 1}", kind, rng.sample(ops, rng.randint(1, min(3, len(ops))))))
        yield (f"rnd{k}", table)


FUZZ = list(single_field_tables()) + list(random_tables(20260929, RANDOM_TABLES))


def test_fuzz_partition_under_measured_models():
    res = check_candidate(CANDIDATE, extra_battery=FUZZ)
    failed = [(t["name"], t["error"] or t["examples"][:1] or "pinned") for t in res["tables"] if not t["passed"]]
    assert not failed, failed
    assert len(res["tables"]) > len(FUZZ)
    assert all(t["id_stamp_ok"] is True for t in res["tables"])


def _binops(node):
    return [n for n in all_nodes(node) if n.get("type") == "BinOp"]


@pytest.mark.parametrize("name,table", [(e[0], e[1]) for e in FUZZ[::7]])
def test_builders_do_not_mutate_and_emit_distinct_id_stamped_rows(name, table):
    rule_table = [(f, list(rules)) for f, _, rules in table]
    snapshot = json.dumps(rule_table)
    ex, inc = cc.af_exclusion_pair(A, rule_table)
    assert json.dumps(rule_table) == snapshot, "rule table mutated"
    rows = [b for group in ex + inc for b in _binops(group)]
    assert len({id(b) for b in rows}) == len(rows), "a BinOp dict is shared between rows"
    for b in rows:
        assert "id" not in b
        if b["operator"] in TIME_OPS:
            assert b["timeUnit"] in cc.AUDIENCE_TIME_UNITS
    for side in (cc.af_root(cc.af_or(*ex)), cc.af_root(cc.af_and(*inc))):
        stamped = list(all_nodes(side))
        assert all(is_uuid(n.get("id")) for n in stamped)
        assert len({n["id"] for n in stamped}) == len(stamped)
    assert all("id" not in n for group in ex + inc for n in all_nodes(group)), "af_root mutated its input"
