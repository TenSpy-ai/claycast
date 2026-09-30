"""The exclusion-pair partition property, as a permanent regression test: the shipped
scripts/clay_client.py must pass tests/tools/check_partition.py — every BATTERY rule table lands
each record on exactly one side under every blank-semantics model the checker enumerates (False
fixed to "matches a blank", as measured live 2026-09-29), boolean tables stay unpinned, every
node is id-stamped by af_root, and the grammar contracts hold."""
from conftest import SCRIPTS
from tools.check_partition import BATTERY, check_candidate

CANDIDATE = str(SCRIPTS / "clay_client.py")


def test_shipped_builders_partition_under_measured_models():
    res = check_candidate(CANDIDATE)
    failed = [(t["name"], t["error"] or t["examples"][:1] or "pinned") for t in res["tables"] if not t["passed"]]
    assert res["all_passed"], failed
    assert res["models"] == 16
    assert len(res["tables"]) == len(BATTERY)
    assert all(t["id_stamp_ok"] is True for t in res["tables"])


def test_boolean_tables_are_unpinned():
    by_name = {t["name"]: t for t in check_candidate(CANDIDATE)["tables"]}
    for name in ("bool_false", "bool_true", "bool_true_false", "bool_false_plus_empty"):
        assert by_name[name]["unpinned_ok"] is True, name


def test_checker_contracts():
    contracts = check_candidate(CANDIDATE)["contracts"]
    assert contracts["legacy_tuple_forms_accepted"] is True
    for key in ("op_without_negation", "time_op_without_unit", "empty_rules_list", "empty_string_value"):
        assert contracts[key].startswith("ValueError:"), (key, contracts[key])


def _false_sensitive(table):
    """A field with a True/False rule and no ("Empty",) rule lets False decide its blank cells."""
    return any({op for op, *_ in rules} & {"True", "False"} and "Empty" not in {op for op, *_ in rules}
               for _, _, rules in table)


def test_all_models_only_fails_booleans_under_the_unmeasured_false_semantics():
    """For reference: with False enumerated both ways (32 models) exactly the tables where False
    decides a blank cell fail, and only under the 16 models where False never matches a blank —
    the reading the live probe ruled out. A boolean field with an ("Empty",) rule is decided by
    that rule and stays model-independent."""
    res = check_candidate(CANDIDATE, all_models=True)
    assert res["models"] == 32
    sensitive = {name for name, table, *_ in BATTERY if _false_sensitive(table)}
    assert {"bool_false", "bool_true", "bool_true_false", "bool_false_plus_text"} == sensitive
    for t in res["tables"]:
        if t["name"] in sensitive:
            assert t["failing_models"] == 16, t["name"]
            assert all(not ex["model"].get("False") for ex in t["examples"]), t["examples"]
        else:
            assert t["passed"], (t["name"], t["examples"][:1])
