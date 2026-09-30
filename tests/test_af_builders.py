"""Filter-AST builders (af_*): node shapes, id stamping, and the exclusion-pair partition claim.

The partition tests use a local evaluator. Clay's real blank-value semantics are NOT known
offline, so the evaluator is run under two models and each test states which it relies on:
  SQL   - any comparison against a blank is false; only Empty/NotEmpty look at blanks
  LOOSE - negative operators (NotEqual/NotContain) are true on a blank
"""
import copy
import itertools

import pytest

from conftest import all_nodes, cc, is_uuid

A, C = "ACCOUNT", "CONTACT"


# ── shapes ───────────────────────────────────────────────────────────────────

def test_field_shape_account():
    n = cc.af_field(A, "org_name", "Equal", "Acme")
    assert n == {"type": "BinOp", "key": "org_name",
                 "dataPath": ["account_entity_field_values", "field", "org_name"],
                 "operator": "Equal", "entityType": "ACCOUNT", "value": "Acme"}


def test_field_shape_contact_lowercase_entity_and_no_value():
    n = cc.af_field("contact", "email", "Empty")
    assert n["entityType"] == "CONTACT"
    assert n["dataPath"][0] == "contact_entity_field_values"
    assert "value" not in n and "timeUnit" not in n


def test_field_time_unit():
    n = cc.af_field(A, "created_at", "WithinLast", 30, time_unit="day")
    assert n["value"] == 30 and n["timeUnit"] == "day"


def test_field_falsy_values_are_kept():
    # value=0 / "" / False must still be sent (only None means "no value")
    assert cc.af_field(A, "x", "Equal", 0)["value"] == 0
    assert cc.af_field(A, "x", "Equal", "")["value"] == ""
    assert cc.af_field(A, "x", "Equal", False)["value"] is False


def test_bad_entity_rejected():
    with pytest.raises(ValueError):
        cc.af_field("DEAL", "x", "Equal", 1)


def test_activity_shape():
    n = cc.af_activity("acttyp_1", "title", "Contain", "demo")
    assert n == {"type": "BinOp", "key": "title::acttyp_1",
                 "dataPath": ["activities", "fields", "title", "acttyp_1"],
                 "operator": "Contain", "value": "demo"}


def test_activity_same_event_shape_and_entity_injection():
    cond1 = cc.af_activity("acttyp_1", "title", "Contain", "demo")
    cond2 = cc.af_activity_timestamp(C, "WithinLast", 30, time_unit="day")
    n = cc.af_activity_same_event("acttyp_1", A, cond1, cond2)
    assert n["type"] == "ColOp" and n["operator"] == "AnyItems"
    assert n["dataPath"] == ["activities", "acttyp_1"] and n["entityType"] == "ACCOUNT"
    assert n["condition"]["type"] == "GroupOp" and n["condition"]["combinationMode"] == "And"
    assert all(i["entityType"] == "ACCOUNT" for i in n["condition"]["items"])
    assert "entityType" not in cond1, "input condition must not be mutated"


def test_activity_same_event_rejects_allitems():
    with pytest.raises(ValueError):
        cc.af_activity_same_event("acttyp_1", A, operator="AllItems")


def test_owner_in():
    ids = [f"005{i:015d}" for i in range(125)]
    n = cc.af_owner_in(ids)
    assert n["operator"] == "ContainAny" and n["key"] == "sfdc_owner_id" and n["value"] == ids
    assert n["value"] is not ids, "list is copied"


def test_none_of_rejects_operator_without_negation():
    with pytest.raises(ValueError):
        cc.af_none_of(A, "x", [("ContainAny", ["a"])])


def test_negation_table_is_an_involution():
    neg = cc.AUDIENCE_NEGATED_OPERATOR
    assert all(neg[neg[op]] == op for op in neg)


# ── ids / root ───────────────────────────────────────────────────────────────

def _sample_ast():
    return cc.af_or(
        cc.af_field(A, "org_name", "Equal", "Acme"),
        cc.af_and(cc.af_field(A, "domain", "NotEmpty"),
                  cc.af_activity_same_event("acttyp_1", A, cc.af_activity("acttyp_1", "title", "Equal", "x"))),
    )


def test_with_ids_stamps_every_node_including_colop_condition():
    ast = cc.af_with_ids(_sample_ast())
    nodes = list(all_nodes(ast))
    assert len(nodes) == 7  # or, bin, and, bin, colop, cond-group, bin
    assert all(is_uuid(n.get("id")) for n in nodes)
    assert len({n["id"] for n in nodes}) == len(nodes), "ids unique"


def test_with_ids_does_not_mutate_input_and_keeps_existing_ids():
    src = _sample_ast()
    before = copy.deepcopy(src)
    once = cc.af_with_ids(src)
    assert src == before, "input AST mutated"
    twice = cc.af_with_ids(once)
    assert [n["id"] for n in all_nodes(once)] == [n["id"] for n in all_nodes(twice)]


def test_root_wraps_bare_nodes_but_not_groups():
    b = cc.af_root(cc.af_field(A, "x", "Equal", 1))
    assert b["type"] == "GroupOp" and b["combinationMode"] == "And" and len(b["items"]) == 1
    g = cc.af_root(cc.af_or(cc.af_field(A, "x", "Equal", 1)))
    assert g["combinationMode"] == "Or"
    empty = cc.af_root(cc.af_and())
    assert empty["items"] == [] and is_uuid(empty["id"])


# ── exclusion pair: partition ────────────────────────────────────────────────

def _match(op, cell, value, model):
    blank = cell is None or cell == ""
    if op == "Empty":
        return blank
    if op == "NotEmpty":
        return not blank
    if blank:
        return model == "LOOSE" and op in ("NotEqual", "NotContain")
    return {
        "Equal": lambda: cell == value,
        "NotEqual": lambda: cell != value,
        "Contain": lambda: value in cell,
        "NotContain": lambda: value not in cell,
        "True": lambda: cell is True,
        "False": lambda: cell is False,
    }[op]()


def evaluate(node, record, model):
    t = node["type"]
    if t == "GroupOp":
        vals = [evaluate(i, record, model) for i in node["items"]]
        if not vals:
            return True
        return all(vals) if node["combinationMode"] == "And" else any(vals)
    if t == "BinOp":
        return _match(node["operator"], record.get(node["key"]), node.get("value"), model)
    raise NotImplementedError(t)


STRING_DOMAIN = [None, "alpha", "beta", "alphabet"]


def _partition(rule_table, domain, model):
    excl, incl = cc.af_exclusion_pair(A, rule_table)
    ex_ast, in_ast = cc.af_or(*excl), cc.af_and(*incl)
    fields = [f for f, _ in rule_table]
    bad = []
    for values in itertools.product(domain, repeat=len(fields)):
        rec = dict(zip(fields, values))
        e, i = evaluate(ex_ast, rec, model), evaluate(in_ast, rec, model)
        if e == i:
            bad.append((rec, "both" if e else "neither"))
    return bad


POSITIVE_TABLES = [
    [("f1", [("Equal", "alpha")])],
    [("f1", [("Equal", "alpha"), ("Contain", "bet")])],
    [("f1", [("Equal", "alpha")]), ("f2", [("Contain", "alp"), ("Equal", "beta")])],
    [("f1", [("Contain", "alpha")]), ("f2", [("NotEmpty",)]), ("f3", [("Equal", "beta")])],
]


@pytest.mark.parametrize("table", POSITIVE_TABLES)
@pytest.mark.parametrize("model", ["SQL", "LOOSE"])
def test_exclusion_pair_partitions_with_positive_rules(table, model):
    assert _partition(table, STRING_DOMAIN, model) == []


def test_exclusion_pair_partitions_booleans_sql():
    table = [("flag", [("True",)])]
    assert _partition(table, [None, True, False], "SQL") == []


@pytest.mark.parametrize("model", ["SQL", "LOOSE"])
def test_FINDING_empty_rule_breaks_partition(model):
    """A rule table containing ("Empty",) puts blank records on BOTH sides: the excluded side
    matches them via the rule, the included side via its built-in "blank => included" branch.
    Holds under any blank semantics."""
    bad = _partition([("f1", [("Empty",)])], STRING_DOMAIN, model)
    assert bad == [({"f1": None}, "both")]


def test_FINDING_negative_rule_depends_on_blank_semantics():
    """NotEqual/NotContain rules partition cleanly only if Clay treats a blank as NOT matching
    them (SQL model). If Clay's NotEqual matches blanks, blanks land on both sides."""
    table = [("f1", [("NotEqual", "alpha")])]
    assert _partition(table, STRING_DOMAIN, "SQL") == []
    assert _partition(table, STRING_DOMAIN, "LOOSE") == [({"f1": None}, "both")]


def test_FINDING_rule_tuples_cannot_carry_time_unit():
    """WithinLast/WithinNext are in the negation table, but a rule tuple is (operator, value) and
    af_any_of/af_none_of never pass time_unit — so the node is built without timeUnit, which
    af_field's own docstring says these operators need."""
    excl, incl = cc.af_exclusion_pair(A, [("created_at", [("WithinLast", 30)])])
    node = excl[0]["items"][0]
    assert node["operator"] == "WithinLast" and "timeUnit" not in node
    # a 3-tuple is not a way to add it either: the third element is silently ignored
    three = cc.af_any_of(A, "created_at", [("WithinLast", 30, "day")])["items"][0]
    assert "timeUnit" not in three
