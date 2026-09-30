"""Filter-AST builders (af_*): node shapes, id stamping, the rule grammar, and the exclusion-pair
partition claim.

The partition tests use a local evaluator run under two models of what a text negative
operator does on a blank cell, and each test states which it relies on:
  SQL   - NotEqual/NotContain are false on a blank; only Empty/NotEmpty look at blanks
  LOOSE - NotEqual/NotContain are true on a blank (the live behaviour: verified 2026-09-29,
          one production workspace, read-only counts)
Under BOTH models `False` is true on a blank boolean: an unset checkbox is stored blank and Clay's
False matches it (True + False = total, same probe), so the builders emit boolean rules unpinned
and the evaluator must not model the reading the probe ruled out.
"""
import copy
import itertools

import pytest

from conftest import all_nodes, cc, is_uuid

A, C = "ACCOUNT", "CONTACT"
MODELS = ["SQL", "LOOSE"]


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


@pytest.mark.parametrize("op", sorted(cc.AUDIENCE_TIME_OPERATORS))
def test_field_time_operator_without_unit_raises(op):
    with pytest.raises(ValueError, match="needs time_unit"):
        cc.af_field(A, "created_at", op, 30)


@pytest.mark.parametrize("unit", ["year", "days", "", 5])
def test_field_bad_time_unit_raises(unit):
    with pytest.raises(ValueError, match="time_unit must be one of day/week/month"):
        cc.af_field(A, "created_at", "WithinLast", 1, time_unit=unit)


def test_field_time_unit_on_non_time_operator_raises():
    with pytest.raises(ValueError, match="time_unit only applies to"):
        cc.af_field(A, "org_name", "Equal", "Acme", time_unit="day")


def test_activity_and_activity_timestamp_enforce_time_unit():
    ok = cc.af_activity("acttyp_1", "created_at", "WithinLast", 30, time_unit="day")
    assert ok["timeUnit"] == "day" and ok["value"] == 30
    ts = cc.af_activity_timestamp(C, "NotWithinNext", 2, time_unit="week")
    assert ts["timeUnit"] == "week"
    with pytest.raises(ValueError, match="af_activity: WithinLast needs time_unit"):
        cc.af_activity("acttyp_1", "created_at", "WithinLast", 30)
    with pytest.raises(ValueError, match="af_activity_timestamp: WithinLast needs time_unit"):
        cc.af_activity_timestamp(C, "WithinLast", 30)
    with pytest.raises(ValueError, match="time_unit must be one of"):
        cc.af_activity("acttyp_1", "created_at", "WithinLast", 30, time_unit="year")
    with pytest.raises(ValueError, match="time_unit only applies to"):
        cc.af_activity_timestamp(C, "Empty", time_unit="day")


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


# ── rule grammar (af_rule) ───────────────────────────────────────────────────

def test_rule_accepted_forms_normalise_identically():
    ref = ("WithinLast", 30, "day")
    assert cc.af_rule(("WithinLast", 30, "day")) == ref
    assert cc.af_rule(["WithinLast", 30, "day"]) == ref
    assert cc.af_rule({"operator": "WithinLast", "value": 30, "time_unit": "day"}) == ref
    assert cc.af_rule({"op": "WithinLast", "value": 30, "timeUnit": "day"}) == ref
    assert cc.af_rule(("Empty",)) == cc.af_rule(("Empty", None)) == cc.af_rule({"operator": "Empty"}) == ("Empty", None, None)
    assert cc.af_rule(("Equal", "x")) == cc.af_rule({"op": "Equal", "value": "x"}) == ("Equal", "x", None)
    assert cc.af_rule(("WithinNext", 1.5, "month")) == ("WithinNext", 1.5, "month")
    assert cc.af_rule(("WithinLast", 0, "day"))[1] == 0
    for v in (0, False):  # falsy but real values are kept
        assert cc.af_rule(("Equal", v))[1] == v
    # not in the negation table, but a rule af_any_of passes through (parity with af_owner_in)
    assert cc.af_rule(("ContainAny", ["a", "b"])) == ("ContainAny", ["a", "b"], None)


@pytest.mark.parametrize("rule,match", [
    (("WithinLast", 30), "needs time_unit"),                      # F6: 2-tuple time rule
    (("WithinLast", 30, "year"), "time_unit must be one of day/week/month"),
    (("Equal", "x", "day"), "time_unit only applies to"),        # unit on a non-time operator
    (("Empty", None, "day"), "time_unit only applies to"),
    (("Equal", ""), "empty-string value is ambiguous"),          # Equal "" matches blank cells live
    (("NotContain", ""), "empty-string value is ambiguous"),
    (("Equal",), "Equal needs a value"),
    (("Equal", None), "Equal needs a value"),
    (("Contain", None), "Contain needs a value"),
    (("Equal", ["a", "b"]), "takes one scalar value"),           # one rule per value
    (("NotEqual", ("a", "b")), "takes one scalar value"),
    (("Contain", {"a": 1}), "takes one scalar value"),
    (("Empty", "x"), "Empty takes no value"),
    (("True", True), "True takes no value"),
    (("NotEmpty", 0), "NotEmpty takes no value"),
    (("WithinLast", "30", "day"), "needs a finite number"),
    (("WithinLast", True, "day"), "needs a finite number"),
    (("WithinLast", -1, "day"), "needs a finite number"),
    (("WithinLast", float("inf"), "day"), "needs a finite number"),
    (("WithinLast", None, "day"), "needs a finite number"),
    (("Equal", "x", "day", "z"), r"a rule is \(op,\)"),          # 4-tuple
    ((), r"a rule is \(op,\)"),
    ("Equal", r"a rule is \(op,\)"),                              # bare string
    (42, r"a rule is \(op,\)"),
    (("", "x"), "operator must be a non-empty string"),
    ((None,), "operator must be a non-empty string"),
    ({"value": 1}, "needs an 'operator' key"),
    ({"operator": "Equal", "op": "Equal", "value": 1}, "not both"),
    ({"operator": "WithinLast", "value": 1, "time_unit": "day", "timeUnit": "day"}, "not both"),
    ({"operator": "Equal", "value": 1, "vlaue": 2}, "unknown keys"),
])
def test_rule_rejections(rule, match):
    with pytest.raises(ValueError, match=match):
        cc.af_rule(rule)


# ── exclusion pair: validation, shapes ───────────────────────────────────────

@pytest.mark.parametrize("call,match", [
    (lambda: cc.af_any_of(A, "t1", []), "field 't1' has no rules"),
    (lambda: cc.af_none_of(A, "t1", []), "field 't1' has no rules"),
    (lambda: cc.af_exclusion_pair(A, [("t1", [])]), "field 't1' has no rules"),
    (lambda: cc.af_exclusion_pair(A, []), "rule_table is empty"),
    (lambda: cc.af_exclusion_pair(A, {"t1": [("Equal", "x")]}), "rule_table must be a list"),
    (lambda: cc.af_exclusion_pair(A, [("t1", [("Equal", "x")], "extra")]), r"expected \(field_id, rules\)"),
    (lambda: cc.af_exclusion_pair(A, ["t1"]), r"expected \(field_id, rules\)"),
    (lambda: cc.af_exclusion_pair(A, [("", [("Equal", "x")])]), "field id must be a non-empty string"),
    (lambda: cc.af_exclusion_pair(A, [(None, [("Equal", "x")])]), "field id must be a non-empty string"),
    (lambda: cc.af_any_of(A, "t1", ("Equal", "x")), r"field 't1': a rule is \(op,\)"),   # a rule, not a list of rules
    (lambda: cc.af_any_of(A, "t1", "Equal"), "rules must be a list of rules"),
    (lambda: cc.af_exclusion_pair("DEAL", [("t1", [("Equal", "x")])]), "entity_type must be ACCOUNT or CONTACT"),
    (lambda: cc.af_none_of(A, "t1", [("Frobnicate", "x")]),
     "field 't1': operator 'Frobnicate' is unknown or has no exact negation; exclusion tables accept only "
     "Contain, Empty, Equal, False, NotContain, NotEmpty, NotEqual, NotWithinLast, NotWithinNext, True, WithinLast, WithinNext"),
    (lambda: cc.af_exclusion_pair(A, [("t1", [("ContainAny", ["a"])])]), "field 't1': operator 'ContainAny' is unknown or has no exact negation"),
    (lambda: cc.af_none_of(A, "t1", [("ContainAny", ["a"])]), "has no exact negation"),
    (lambda: cc.af_none_of(A, "t1", [("GreaterThan", 5)]), "has no exact negation"),
    (lambda: cc.af_exclusion_pair(A, [("t1", [("Equal", "")])]), "field 't1': rule"),
    (lambda: cc.af_exclusion_pair(A, [("t1", [("Equal", None)])]), "field 't1': rule"),
    (lambda: cc.af_exclusion_pair(A, [("t1", [("Equal", ["a"])])]), "field 't1': rule"),
    (lambda: cc.af_exclusion_pair(A, [("t1", [("Empty", "x")])]), "field 't1': rule"),
    (lambda: cc.af_exclusion_pair(A, [("t1", [("Equal", "x", "day")])]), "field 't1': rule"),
    (lambda: cc.af_exclusion_pair(A, [("d1", [("WithinLast", 30)])]), "field 'd1': rule"),
    (lambda: cc.af_exclusion_pair(A, [("d1", [("WithinLast", 30, "year")])]), "field 'd1': rule"),
    (lambda: cc.af_exclusion_pair(A, [("t1", [("Equal", "x", "day", "z")])]), "field 't1': a rule is"),
])
def test_pair_builders_reject(call, match):
    with pytest.raises(ValueError, match=match):
        call()


def test_pair_validates_the_whole_table_before_building():
    """A bad entry anywhere fails the call — nothing half-built is returned."""
    with pytest.raises(ValueError, match="field 't3'"):
        cc.af_exclusion_pair(A, [("t1", [("Equal", "a")]), ("t2", [("Empty",)]), ("t3", [("StartsWith", "x")])])


def test_any_of_stays_permissive_for_containany():
    n = cc.af_any_of(A, "sfdc_owner_id", [("ContainAny", ["005a", "005b"])])
    assert n == cc.af_or(cc.af_field(A, "sfdc_owner_id", "ContainAny", ["005a", "005b"]))


def test_identical_rules_collapse_first_occurrence_kept():
    rules = [("Equal", "a"), ["Equal", "a"], {"operator": "Equal", "value": "a"}, ("Contain", "b"), ("Equal", "a")]
    ex = cc.af_any_of(A, "t1", rules)
    assert [(r["operator"], r["value"]) for r in ex["items"]] == [("Equal", "a"), ("Contain", "b")]
    inc = cc.af_none_of(A, "t1", rules)
    assert [(r["operator"], r["value"]) for r in inc["items"][1]["items"]] == [("NotEqual", "a"), ("NotContain", "b")]
    assert cc.af_any_of(A, "t1", [("Empty",), ("Empty",)]) == cc.af_or(cc.af_field(A, "t1", "Empty"))


def test_positive_only_tables_match_the_pr_shapes_exactly():
    """Positive text/date rules build the shapes the PR shipped: Or(rules) / Or(Empty, And(negated))."""
    def pr_any_of(field, rules):
        return cc.af_or(*[cc.af_field(A, field, r[0], r[1] if len(r) > 1 else None, time_unit=r[2] if len(r) > 2 else None) for r in rules])

    def pr_none_of(field, rules):
        neg = cc.AUDIENCE_NEGATED_OPERATOR
        return cc.af_or(cc.af_field(A, field, "Empty"),
                        cc.af_and(*[cc.af_field(A, field, neg[r[0]], r[1] if len(r) > 1 else None, time_unit=r[2] if len(r) > 2 else None) for r in rules]))

    tables = [
        [("t1", [("Equal", "alpha")])],
        [("t1", [("Equal", "alpha"), ("Contain", "bet")])],
        [("t1", [("Equal", "a"), ("Equal", "b"), ("Contain", "c")]), ("t2", [("Contain", "x")])],
        [("t1", [("Contain", "x"), ("Equal", 0)])],
        [("d1", [("WithinLast", 30, "day"), ("WithinNext", 1, "month")])],
    ]
    for table in tables:
        ex, inc = cc.af_exclusion_pair(A, table)
        assert ex == [pr_any_of(f, r) for f, r in table], table
        assert inc == [pr_none_of(f, r) for f, r in table], table
        assert cc.af_any_of(A, *table[0]) == pr_any_of(*table[0])
        assert cc.af_none_of(A, *table[0]) == pr_none_of(*table[0])


def test_empty_rule_drops_the_included_guard():
    ex, inc = cc.af_exclusion_pair(A, [("t1", [("Empty",), ("Equal", "a")])])
    assert ex == [cc.af_or(cc.af_field(A, "t1", "Empty"), cc.af_field(A, "t1", "Equal", "a"))]   # no pin
    assert inc == [cc.af_and(cc.af_field(A, "t1", "NotEmpty"), cc.af_field(A, "t1", "NotEqual", "a"))]


def test_notempty_rule_gives_included_and_empty():
    ex, inc = cc.af_exclusion_pair(A, [("t1", [("NotEmpty",)])])
    assert ex == [cc.af_or(cc.af_field(A, "t1", "NotEmpty"))]
    assert inc == [cc.af_and(cc.af_field(A, "t1", "Empty"))]


def test_negative_text_rule_is_pinned_under_notempty():
    ex, inc = cc.af_exclusion_pair(A, [("t1", [("NotContain", "x"), ("Equal", "y")])])
    assert ex == [cc.af_and(cc.af_field(A, "t1", "NotEmpty"),
                            cc.af_or(cc.af_field(A, "t1", "NotContain", "x"), cc.af_field(A, "t1", "Equal", "y")))]
    assert inc == [cc.af_or(cc.af_field(A, "t1", "Empty"),
                            cc.af_and(cc.af_field(A, "t1", "Contain", "x"), cc.af_field(A, "t1", "NotEqual", "y")))]
    # the pin is per field, and suppressed when an Empty rule already decides blanks
    ex2 = cc.af_any_of(A, "t1", [("NotEqual", "a"), ("NotContain", "b")])
    assert ex2["combinationMode"] == "And" and ex2["items"][0]["operator"] == "NotEmpty" and len(ex2["items"][1]["items"]) == 2
    ex3 = cc.af_any_of(A, "t1", [("NotEqual", "a"), ("Empty",)])
    assert ex3 == cc.af_or(cc.af_field(A, "t1", "NotEqual", "a"), cc.af_field(A, "t1", "Empty"))


def test_boolean_rules_are_exact_without_pin_or_guard():
    """A blank boolean IS False live (AUDIENCE_BOOLEAN_BLANK_IS_FALSE), so ("False",) excludes
    unset checkboxes and its complement is just True — no NotEmpty pin, no Empty guard."""
    assert cc.AUDIENCE_BOOLEAN_BLANK_IS_FALSE is True
    ex, inc = cc.af_exclusion_pair(A, [("b1", [("False",)])])
    assert ex == [cc.af_or(cc.af_field(A, "b1", "False"))]
    assert inc == [cc.af_and(cc.af_field(A, "b1", "True"))]
    ex, inc = cc.af_exclusion_pair(A, [("b1", [("True",)])])
    assert ex == [cc.af_or(cc.af_field(A, "b1", "True"))]
    assert inc == [cc.af_and(cc.af_field(A, "b1", "False"))]
    # Empty/NotEmpty may be mixed in; the pair stays bare
    ex, inc = cc.af_exclusion_pair(A, [("b1", [("False",), ("Empty",)])])
    assert ex == [cc.af_or(cc.af_field(A, "b1", "False"), cc.af_field(A, "b1", "Empty"))]
    assert inc == [cc.af_and(cc.af_field(A, "b1", "True"), cc.af_field(A, "b1", "NotEmpty"))]
    ex, inc = cc.af_exclusion_pair(A, [("b1", [("True",), ("NotEmpty",)])])
    assert inc == [cc.af_and(cc.af_field(A, "b1", "False"), cc.af_field(A, "b1", "Empty"))]


def test_time_rules_carry_time_unit():
    """WithinLast-family rules need a unit: a 2-tuple raises (the PR silently built a node
    without timeUnit — a Clay server error), the 3-tuple and dict forms carry it on both sides."""
    with pytest.raises(ValueError, match="WithinLast needs time_unit"):
        cc.af_exclusion_pair(A, [("created_at", [("WithinLast", 30)])])
    with pytest.raises(ValueError, match="time_unit must be one of day/week/month"):
        cc.af_exclusion_pair(A, [("created_at", [("WithinLast", 1, "year")])])
    for rules in ([("NotWithinLast", 7, "week")], [{"operator": "NotWithinLast", "value": 7, "timeUnit": "week"}]):
        ex, inc = cc.af_exclusion_pair(A, [("created_at", rules)])
        assert ex == [cc.af_and(cc.af_field(A, "created_at", "NotEmpty"),
                                cc.af_or(cc.af_field(A, "created_at", "NotWithinLast", 7, time_unit="week")))]
        assert inc == [cc.af_or(cc.af_field(A, "created_at", "Empty"),
                                cc.af_and(cc.af_field(A, "created_at", "WithinLast", 7, time_unit="week")))]
    three = cc.af_any_of(A, "created_at", [("WithinLast", 30, "day")])["items"][0]
    assert three["timeUnit"] == "day" and three["value"] == 30


# ── exclusion pair: partition ────────────────────────────────────────────────

def _match(op, cell, value, model):
    blank = cell is None or cell == ""
    if op == "Empty":
        return blank
    if op == "NotEmpty":
        return not blank
    if blank:
        # An unset boolean is stored blank and Clay's False matches it (verified live 2026-09-29),
        # under both models; only the text negatives differ between SQL and LOOSE.
        if op == "False":
            return True
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


STRING_DOMAIN = [None, "", "alpha", "beta", "alphabet"]
BOOL_DOMAIN = [None, True, False]


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
@pytest.mark.parametrize("model", MODELS)
def test_exclusion_pair_partitions_with_positive_rules(table, model):
    assert _partition(table, STRING_DOMAIN, model) == []


BOOLEAN_TABLES = [
    [("flag", [("True",)])],
    [("flag", [("False",)])],
    [("flag", [("True",), ("False",)])],
    [("flag", [("False",), ("Empty",)])],
    [("flag", [("True",), ("NotEmpty",)])],
    [("flag", [("False",)]), ("other", [("True",)])],
]


@pytest.mark.parametrize("table", BOOLEAN_TABLES)
@pytest.mark.parametrize("model", MODELS)
def test_exclusion_pair_partitions_booleans(table, model):
    assert _partition(table, BOOL_DOMAIN, model) == []


@pytest.mark.parametrize("model", MODELS)
def test_empty_rule_partitions(model):
    """An ("Empty",) rule excludes blank records; the included side drops its blank branch, so
    blanks land on exactly one side under any blank semantics (was FINDING F1: both sides)."""
    assert _partition([("f1", [("Empty",)])], STRING_DOMAIN, model) == []
    assert _partition([("f1", [("Empty",), ("Equal", "alpha")])], STRING_DOMAIN, model) == []
    assert _partition([("f1", [("Empty",), ("NotEmpty",)])], STRING_DOMAIN, model) == []
    assert _partition([("f1", [("Empty",), ("NotEqual", "alpha")])], STRING_DOMAIN, model) == []


@pytest.mark.parametrize("model", MODELS)
def test_negative_rule_is_pinned(model):
    """NotEqual/NotContain rules partition under BOTH blank models because the excluded group is
    pinned: And(NotEmpty, Or(rules)) — a blank can never be excluded by a negative operator
    (was FINDING F2: under LOOSE semantics, the live ones, blanks landed on both sides)."""
    for table in ([("f1", [("NotEqual", "alpha")])],
                  [("f1", [("NotContain", "alp"), ("Equal", "beta")])],
                  [("f1", [("NotEqual", "alpha")]), ("f2", [("NotContain", "bet")]), ("f3", [("Empty",)])]):
        assert _partition(table, STRING_DOMAIN, model) == []
    excl, _ = cc.af_exclusion_pair(A, [("f1", [("NotEqual", "alpha")])])
    assert excl[0]["combinationMode"] == "And"
    assert excl[0]["items"][0] == cc.af_field(A, "f1", "NotEmpty")
    assert excl[0]["items"][1] == cc.af_or(cc.af_field(A, "f1", "NotEqual", "alpha"))
