"""ClayClient saved-Claygent column methods, driven through a recording FakeSession.

Covers get_claygent, _use_ai_param_names, _claygent_prompt_formula, claygent_column_inputs,
create_claygent_column, _claygent_column_settings, sync_claygent_column,
verify_claygent_column and unwrap_claygent_output.

A small in-memory "Clay" (FakeClay) answers the routes and models the one server behaviour
the docs measured live (clay-api-reference "Claygent columns from code"): on every column
create/update Clay discards the prompt in the request and re-renders it from the bound
Claygent's CURRENT prompt, each {{variable}} replaced by its claygentFieldMapping expression,
without spaces around `+`. Whether Clay really does that is a live claim and cannot be
re-checked offline; these tests check that the code does what its docstrings say given it.

Tests marked xfail(strict=True) assert the CORRECT behaviour where the shipped code is wrong;
each reason names the defect.
"""
import copy
import json
import re

import pytest

from conftest import cc, make_client

WS = 12345
T = "t_1"
CG_PATH = f"/workspaces/{WS}/claygents/c_1"
FIELDS = f"/tables/{T}/fields"
USE_AI_PKG = "67ba01e9-1898-4e7d-afe7-7ebe24819a57"

# Every input the fake `use-ai` action declares, in its declared order.
USE_AI_PARAMS = ["useCase", "prompt", "model", "claygentId", "claygentFieldMapping",
                 "answerSchemaType", "temperature", "_metadata"]

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["score", "reason"],
          "properties": {"score": {"type": "number"}, "reason": {"type": "string", "description": "é"}}}
PROMPT = 'Score {{company}} for fit.\nNotes: {{notes}} — say "why". é'
VAR_MAP = {"company": "{{f_co}}", "notes": "{{f_notes}}"}


def _claygent(prompt=PROMPT, model="claude-sonnet-x", fmt=None, variables=("company", "notes"), cid="c_1"):
    return {"id": cid, "name": "Fit scorer", "currentVersion": {
        "userPrompt": prompt,
        "outputFormat": fmt if fmt is not None else
        {"type": "json", "jsonType": "JSONSchema", "jsonSchema": json.dumps(SCHEMA)},
        "modelSettings": {"model": model},
        "variables": None if variables is None else [{"name": n, "type": "text"} for n in variables],
    }}


def _render(user_prompt, fmap, sep="+"):
    """What Clay stores as `prompt` after a write (per the docs): the Claygent's prompt with
    each {{var}} -> its mapping expression; literals as JSON strings; no spaces around +."""
    pieces = []
    for part in re.split(r"(\{\{[A-Za-z0-9_]+\}\})", user_prompt):
        if re.fullmatch(r"\{\{[A-Za-z0-9_]+\}\}", part):
            pieces.append(fmap[part])
        elif part:
            pieces.append(json.dumps(part, ensure_ascii=False))
    return sep.join(pieces)


def _has_u_escape(formula):
    """True when a formula contains a \\uXXXX escape (Clay rejects those in string literals).
    An escaped backslash followed by 'u' (\\\\u...) is not one."""
    return re.search(r"(?<!\\)(?:\\\\)*\\u[0-9a-fA-F]{4}", formula) is not None


class FakeClay:
    """In-memory workspace: one table, its fields, Claygents and the action catalog."""

    def __init__(self, claygents=None, params=USE_AI_PARAMS, envelope=True, rerender=True,
                 ignore_patch=False, mangle=None, actions=None):
        self.claygents = claygents if claygents is not None else {"c_1": _claygent()}
        self.fields = []
        self.params = params
        self.envelope, self.rerender, self.ignore_patch, self.mangle = envelope, rerender, ignore_patch, mangle
        self.actions = actions
        self.client = make_client(cc, [
            ("GET", r"/workspaces/12345/claygents/([^/]+)", self._get_claygent),
            ("GET", r"/actions", self._actions),
            ("GET", rf"/tables/{T}", self._get_table),
            ("POST", rf"/tables/{T}/fields", self._post_field),
            ("PATCH", rf"/tables/{T}/fields/([^/]+)", self._patch_field),
        ])

    @property
    def calls(self):
        return self.client.session.calls

    def _get_claygent(self, call):
        cg = self.claygents[call["path"].rsplit("/", 1)[1]]
        return {"claygent": cg} if self.envelope else cg

    def _actions(self, call):
        if self.actions is not None:
            return {"actions": self.actions}
        return {"actions": [
            {"key": "use-ai-legacy", "inputParameterSchema": [{"name": "nope"}]},
            {"key": "use-ai", "inputParameterSchema": [{"name": n, "type": "text"} for n in self.params]},
        ]}

    def _get_table(self, call):
        return {"table": {"id": T, "fields": copy.deepcopy(self.fields), "views": []}}

    def _server_side(self, ts):
        b = {x["name"]: x for x in ts.get("inputsBinding") or []}
        if self.rerender and "claygentId" in b and "formulaText" in b["claygentId"]:
            cg = self.claygents[json.loads(b["claygentId"]["formulaText"])]
            fmap = (b.get("claygentFieldMapping") or {}).get("formulaMap") or {}
            b["prompt"]["formulaText"] = _render(cg["currentVersion"]["userPrompt"], fmap)
        if self.mangle:
            self.mangle(b)

    def _post_field(self, call):
        body = copy.deepcopy(call["json"])
        f = {"id": "f_new", "name": body["name"], "type": body["type"], "typeSettings": body["typeSettings"]}
        self._server_side(f["typeSettings"])
        self.fields.append(f)
        return {"field": f}

    def _patch_field(self, call):
        fid = call["path"].rsplit("/", 1)[1]
        f = next(x for x in self.fields if x["id"] == fid)
        if not self.ignore_patch:
            f["typeSettings"] = copy.deepcopy(call["json"]["typeSettings"])
            self._server_side(f["typeSettings"])
        return {"field": f}

    def add_column(self, inputs, fid="f_1", extra_ts=None, rerender=True):
        """Seed a column as create_action_column would bind `inputs`, then let the server
        re-render it (unless rerender=False)."""
        binding = []
        for k, v in inputs.items():
            if isinstance(v, dict):
                binding.append({"name": k, "formulaMap": v})
            elif v:
                binding.append({"name": k, "formulaText": v})
            else:
                binding.append({"name": k})
        ts = {"dataTypeSettings": {"type": "json"}, "actionKey": "use-ai", "actionVersion": 1,
              "actionPackageId": USE_AI_PKG, "inputsBinding": binding, **(extra_ts or {})}
        if rerender:
            self._server_side(ts)
        self.fields.append({"id": fid, "name": "Research", "type": "action", "typeSettings": ts})
        return fid

    def binding(self, fid="f_1"):
        f = next(x for x in self.fields if x["id"] == fid)
        return {b["name"]: b for b in f["typeSettings"]["inputsBinding"]}

    def ts(self, fid="f_1"):
        return next(x for x in self.fields if x["id"] == fid)["typeSettings"]


def _expected_inputs(byo=True, prompt=PROMPT, var_map=VAR_MAP, model="claude-sonnet-x", schema=SCHEMA):
    exp = {n: None for n in USE_AI_PARAMS}
    exp.update({
        "useCase": '"claygent"',
        "claygentId": '"c_1"',
        "model": json.dumps(model),
        "claygentFieldMapping": {"{{" + k + "}}": f"Clay.formatForAIPrompt({e})" for k, e in var_map.items()},
        "prompt": cc.ClayClient._claygent_prompt_formula(prompt, var_map),
        "answerSchemaType": {"type": '"json"', "jsonType": '"JSONSchema"',
                             "jsonSchema": json.dumps(json.dumps(schema), ensure_ascii=False)},
    })
    if byo:
        exp["_metadata"] = {"modelSource": '"user"'}
    return exp


# ── get_claygent ──────────────────────────────────────────────────────────────

def test_get_claygent_path_and_envelope():
    fc = FakeClay()
    assert fc.client.get_claygent("c_1") == _claygent()
    assert [(c["method"], c["path"]) for c in fc.calls] == [("GET", CG_PATH)]


def test_get_claygent_bare_response():
    fc = FakeClay(envelope=False)
    assert fc.client.get_claygent("c_1")["currentVersion"]["userPrompt"] == PROMPT


# ── _use_ai_param_names ───────────────────────────────────────────────────────

def test_use_ai_param_names_reads_use_ai_action_and_caches():
    fc = FakeClay()
    assert fc.client._use_ai_param_names() == USE_AI_PARAMS
    assert fc.client._use_ai_param_names() == USE_AI_PARAMS
    gets = [c for c in fc.calls if c["path"] == "/actions"]
    assert len(gets) == 1 and gets[0]["params"] == {"workspaceId": WS}


def test_use_ai_param_names_missing_action_raises():
    fc = FakeClay(actions=[{"key": "use-ai-legacy", "inputParameterSchema": [{"name": "x"}]}])
    with pytest.raises(RuntimeError, match="use-ai action not found"):
        fc.client._use_ai_param_names()


def test_use_ai_param_names_without_schema_is_empty():
    fc = FakeClay(actions=[{"key": "use-ai", "inputParameterSchema": None}])
    assert fc.client._use_ai_param_names() == []


# ── _claygent_prompt_formula ──────────────────────────────────────────────────

def test_prompt_formula_exact():
    f = cc.ClayClient._claygent_prompt_formula("Hi {{name}}, from {{co}}!", {"name": "{{f_a}}", "co": "{{f_b}}?.x"})
    assert f == '"Hi " + Clay.formatForAIPrompt({{f_a}}) + ", from " + Clay.formatForAIPrompt({{f_b}}?.x) + "!"'


def test_prompt_formula_escapes_quotes_and_newlines_but_not_unicode():
    f = cc.ClayClient._claygent_prompt_formula('a "b"\nc — é {{v}}', {"v": "{{f}}"})
    assert f == '"a \\"b\\"\\nc — é " + Clay.formatForAIPrompt({{f}})'
    assert not _has_u_escape(f)


def test_prompt_formula_only_and_adjacent_variables():
    assert cc.ClayClient._claygent_prompt_formula("{{a}}{{b}}", {"a": "1", "b": "2"}) == \
        "Clay.formatForAIPrompt(1) + Clay.formatForAIPrompt(2)"


def test_prompt_formula_empty_and_literal_only():
    assert cc.ClayClient._claygent_prompt_formula("", {}) == ""
    assert cc.ClayClient._claygent_prompt_formula("plain", {}) == '"plain"'


def test_prompt_formula_non_variable_braces_stay_literal():
    # {{not a var}} (spaces) and {single} do not match the variable pattern.
    assert cc.ClayClient._claygent_prompt_formula("{{not a var}} {x}", {}) == '"{{not a var}} {x}"'


def test_prompt_formula_undeclared_variable_raises():
    # A {{token}} in the prompt with no mapping cannot be rendered; it surfaces as KeyError.
    with pytest.raises(KeyError):
        cc.ClayClient._claygent_prompt_formula("{{a}} {{b}}", {"a": "1"})


# ── claygent_column_inputs ────────────────────────────────────────────────────

def test_inputs_full_shape_json_schema_byo():
    fc = FakeClay()
    inputs = fc.client.claygent_column_inputs("c_1", VAR_MAP)
    assert inputs == _expected_inputs()
    assert list(inputs) == USE_AI_PARAMS  # every declared param, in declared order
    assert [(c["method"], c["path"]) for c in fc.calls] == [("GET", CG_PATH), ("GET", "/actions")]
    # the schema copy is double-encoded and round-trips exactly
    assert json.loads(json.loads(inputs["answerSchemaType"]["jsonSchema"])) == SCHEMA
    assert not _has_u_escape(inputs["answerSchemaType"]["jsonSchema"])
    assert not _has_u_escape(inputs["prompt"])


def test_inputs_not_byo_leaves_metadata_unset():
    fc = FakeClay()
    inputs = fc.client.claygent_column_inputs("c_1", VAR_MAP, byo_key=False)
    assert inputs == _expected_inputs(byo=False)
    assert inputs["_metadata"] is None


def test_inputs_byo_adds_metadata_even_if_action_does_not_declare_it():
    fc = FakeClay(params=[p for p in USE_AI_PARAMS if p != "_metadata"])
    inputs = fc.client.claygent_column_inputs("c_1", VAR_MAP)
    assert inputs["_metadata"] == {"modelSource": '"user"'}
    assert list(inputs)[-1] == "_metadata"
    assert "_metadata" not in fc.client.claygent_column_inputs("c_1", VAR_MAP, byo_key=False)


def test_inputs_fields_output_format():
    fields = {"score": {"type": "number"}, "reason": {"type": "string", "description": "é"}}
    fc = FakeClay(claygents={"c_1": _claygent(fmt={"type": "json", "fields": fields})})
    inputs = fc.client.claygent_column_inputs("c_1", VAR_MAP)
    assert inputs["answerSchemaType"] == {"type": '"json"', "fields": json.dumps(fields, ensure_ascii=False)}


def test_inputs_fields_output_format_without_fields():
    fc = FakeClay(claygents={"c_1": _claygent(fmt={"type": "json"})})
    assert fc.client.claygent_column_inputs("c_1", VAR_MAP)["answerSchemaType"] == {"type": '"json"', "fields": "{}"}


def test_inputs_text_output_has_no_schema():
    fc = FakeClay(claygents={"c_1": _claygent(fmt={"type": "text"})})
    inputs = fc.client.claygent_column_inputs("c_1", VAR_MAP)
    assert inputs["answerSchemaType"] is None


def test_inputs_claygent_without_variables():
    fc = FakeClay(claygents={"c_1": _claygent(prompt="Just think.", variables=None)})
    inputs = fc.client.claygent_column_inputs("c_1", {})
    assert inputs["claygentFieldMapping"] == {}
    assert inputs["prompt"] == '"Just think."'


@pytest.mark.parametrize("var_map", [
    {"company": "{{f_co}}"},                                         # missing a variable
    {"company": "{{f_co}}", "notes": "{{f_n}}", "extra": "{{f_x}}"},  # extra variable
    {"Company": "{{f_co}}", "notes": "{{f_n}}"},                      # wrong name (case)
    {},
])
def test_inputs_var_map_must_match_claygent_variables(var_map):
    fc = FakeClay()
    with pytest.raises(ValueError, match=r"Claygent c_1 variables \['company', 'notes'\] != var_map keys"):
        fc.client.claygent_column_inputs("c_1", var_map)
    # validated before the action catalog is read
    assert [c["path"] for c in fc.calls] == [CG_PATH]


def test_inputs_any_expression_in_var_map():
    fc = FakeClay()
    vm = {"company": '{{f_co}} + " (" + {{f_dom}} + ")"', "notes": "JSON.stringify({{f_n}})"}
    inputs = fc.client.claygent_column_inputs("c_1", vm)
    assert inputs["claygentFieldMapping"]["{{company}}"] == 'Clay.formatForAIPrompt({{f_co}} + " (" + {{f_dom}} + ")")'
    assert "Clay.formatForAIPrompt(JSON.stringify({{f_n}}))" in inputs["prompt"]


# ── create_claygent_column ────────────────────────────────────────────────────

def test_create_exact_request_then_verifies():
    fc = FakeClay()
    col = fc.client.create_claygent_column(T, "Research", "c_1", VAR_MAP, view_id="gv_1",
                                          auth_account_id="aa_9", condition='{{f_ready}} == "yes"')
    assert col["id"] == "f_new"
    post = [c for c in fc.calls if c["method"] == "POST"]
    assert len(post) == 1 and post[0]["path"] == FIELDS
    exp = _expected_inputs()
    binding = []
    for k, v in exp.items():
        if isinstance(v, dict):
            binding.append({"name": k, "formulaMap": v})
        elif v:
            binding.append({"name": k, "formulaText": v})
        else:
            binding.append({"name": k})
    assert post[0]["json"] == {
        "type": "action", "name": "Research", "activeViewId": "gv_1",
        "typeSettings": {
            "dataTypeSettings": {"type": "json"},
            "actionKey": "use-ai", "actionVersion": 1, "actionPackageId": USE_AI_PKG,
            "inputsBinding": binding,
            "authAccountId": "aa_9",
            "conditionalRunFormulaText": '{{f_ready}} == "yes"',
        },
    }
    # unset params are bound bare (the UI needs every declared input)
    assert {"name": "temperature"} in binding
    # read-back: GET table (list_fields) then the Claygent again
    seq = [(c["method"], c["path"]) for c in fc.calls]
    assert seq == [("GET", CG_PATH), ("GET", "/actions"), ("POST", FIELDS), ("GET", f"/tables/{T}"), ("GET", CG_PATH)]
    assert fc.calls[3]["params"] == {"includeExtraData": "true"}
    assert all(c["method"] == "GET" for c in fc.calls[3:])


def test_create_minimal_has_no_optional_keys_and_uses_json_data_type():
    fc = FakeClay()
    fc.client.create_claygent_column(T, "Research", "c_1", VAR_MAP, byo_key=False)
    body = next(c for c in fc.calls if c["method"] == "POST")["json"]
    assert "activeViewId" not in body
    ts = body["typeSettings"]
    assert ts["dataTypeSettings"] == {"type": "json"}  # forced: use-ai matches no json hint
    assert "authAccountId" not in ts and "conditionalRunFormulaText" not in ts
    assert {"name": "_metadata"} in ts["inputsBinding"]


def test_create_passes_with_clay_rerendered_prompt():
    # Clay re-renders the prompt without spaces around `+`; verify must still accept it.
    fc = FakeClay()
    fc.client.create_claygent_column(T, "Research", "c_1", VAR_MAP)
    stored = fc.binding("f_new")["prompt"]["formulaText"]
    assert " + " not in stored and stored == _render(PROMPT, _expected_inputs()["claygentFieldMapping"])


def test_create_raises_when_read_back_differs_but_column_exists():
    def drop_schema(b):
        b["answerSchemaType"].pop("formulaMap")  # stored bare: the schema copy did not stick
    fc = FakeClay(mangle=drop_schema)
    with pytest.raises(RuntimeError, match=r"Claygent column f_new did not match its Claygent: .*output schema"):
        fc.client.create_claygent_column(T, "Research", "c_1", VAR_MAP)
    assert [f["id"] for f in fc.fields] == ["f_new"]  # created, not rolled back


def test_create_bad_var_map_sends_nothing():
    fc = FakeClay()
    with pytest.raises(ValueError):
        fc.client.create_claygent_column(T, "Research", "c_1", {"company": "{{f_co}}"})
    assert not [c for c in fc.calls if c["method"] != "GET"]


# ── _claygent_column_settings ─────────────────────────────────────────────────

def test_column_settings_returns_typesettings_and_binding_index():
    fc = FakeClay()
    fc.add_column(_expected_inputs())
    ts, b = fc.client._claygent_column_settings(T, "f_1")
    assert ts["actionKey"] == "use-ai"
    assert set(b) == set(USE_AI_PARAMS) and b["claygentId"] == {"name": "claygentId", "formulaText": '"c_1"'}


def test_column_settings_unknown_field():
    fc = FakeClay()
    with pytest.raises(ValueError, match="field f_zz not found on t_1"):
        fc.client._claygent_column_settings(T, "f_zz")


def test_column_settings_field_without_type_settings():
    fc = FakeClay()
    fc.fields.append({"id": "f_txt", "name": "Plain", "type": "text"})
    assert fc.client._claygent_column_settings(T, "f_txt") == ({}, {})


# ── verify_claygent_column ────────────────────────────────────────────────────

def _verify(fc, **kw):
    return fc.client.verify_claygent_column(T, "f_1", **kw)


def test_verify_match_is_read_only():
    fc = FakeClay()
    fc.add_column(_expected_inputs())
    assert _verify(fc) == {"ok": True, "problems": [], "claygent_id": "c_1"}
    assert _verify(fc, var_map=VAR_MAP) == {"ok": True, "problems": [], "claygent_id": "c_1"}
    assert {c["method"] for c in fc.calls} == {"GET"}


def test_verify_accepts_our_own_spaced_prompt_too():
    fc = FakeClay()
    fc.add_column(_expected_inputs(), rerender=False)  # prompt as we sent it, " + " separators
    assert _verify(fc, var_map=VAR_MAP)["ok"] is True


@pytest.mark.parametrize("cid_binding", [None, {"name": "claygentId"}, {"name": "claygentId", "formulaText": "null"},
                                         {"name": "claygentId", "formulaText": '""'}])
def test_verify_unbound_column(cid_binding):
    fc = FakeClay()
    inputs = _expected_inputs()
    del inputs["claygentId"]
    fid = fc.add_column(inputs, rerender=False)
    if cid_binding is not None:
        fc.ts(fid)["inputsBinding"].append(cid_binding)
    assert _verify(fc) == {"ok": False, "problems": ["column is not bound to a saved Claygent"], "claygent_id": None}
    assert CG_PATH not in [c["path"] for c in fc.calls]


def test_verify_unknown_field():
    with pytest.raises(ValueError, match="not found"):
        _verify(FakeClay())


def test_verify_model_differs_after_claygent_edit():
    fc = FakeClay()
    fc.add_column(_expected_inputs())
    fc.claygents["c_1"]["currentVersion"]["modelSettings"]["model"] = "gpt-x"
    r = _verify(fc)
    assert r["ok"] is False and r["problems"] == ["model differs from the Claygent's"]


def test_verify_model_binding_unset():
    fc = FakeClay()
    fc.add_column({**_expected_inputs(), "model": None})
    assert _verify(fc)["problems"] == ["model differs from the Claygent's"]


SCHEMA_PROBLEM = "output schema copy differs from the Claygent's (UI: 'Unable to parse the output schema')"


@pytest.mark.parametrize("stored", [
    None,                                                                       # bare entry
    {"type": '"json"', "jsonType": '"JSONSchema"'},                             # no jsonSchema key
    {"type": '"json"', "jsonType": '"JSONSchema"', "jsonSchema": "{not json"},  # unparsable
    {"type": '"json"', "jsonType": '"JSONSchema"', "jsonSchema": json.dumps(SCHEMA)},  # single-encoded
    {"type": '"json"', "jsonType": '"JSONSchema"',
     "jsonSchema": json.dumps(json.dumps({**SCHEMA, "required": ["score"]}))},  # different schema
])
def test_verify_schema_copy_problems(stored):
    fc = FakeClay()
    fc.add_column({**_expected_inputs(), "answerSchemaType": stored})
    assert _verify(fc)["problems"] == [SCHEMA_PROBLEM]


def test_verify_schema_compare_ignores_key_order_and_whitespace():
    fc = FakeClay()
    reordered = json.dumps(dict(reversed(list(SCHEMA.items()))), indent=2)
    fc.add_column({**_expected_inputs(), "answerSchemaType": {"type": '"json"', "jsonType": '"JSONSchema"',
                                                              "jsonSchema": json.dumps(reordered)}})
    assert _verify(fc)["ok"] is True


def test_verify_schema_stale_after_claygent_schema_edit():
    fc = FakeClay()
    fc.add_column(_expected_inputs())
    new = {**SCHEMA, "required": ["score"]}
    fc.claygents["c_1"]["currentVersion"]["outputFormat"]["jsonSchema"] = json.dumps(new)
    assert _verify(fc)["problems"] == [SCHEMA_PROBLEM]


def test_verify_text_output_claygent_skips_schema_check():
    fc = FakeClay(claygents={"c_1": _claygent(fmt={"type": "text"})})
    fc.add_column({**_expected_inputs(), "answerSchemaType": None})
    assert _verify(fc)["ok"] is True


def test_verify_prompt_missing_piece_after_claygent_prompt_edit():
    fc = FakeClay()
    fc.add_column(_expected_inputs())
    fc.claygents["c_1"]["currentVersion"]["userPrompt"] = PROMPT.replace("for fit.", "for ICP fit.")
    r = _verify(fc)
    assert r["ok"] is False
    assert r["problems"] == ["stored prompt is missing a piece of the Claygent prompt near: ' for ICP fit.\\nNotes: '"]


def test_verify_prompt_pieces_must_be_in_order():
    fc = FakeClay(claygents={"c_1": _claygent(prompt="AAA {{company}} BBB {{notes}}")})
    fc.add_column(_expected_inputs(prompt="AAA {{company}} BBB {{notes}}"))
    fc.claygents["c_1"]["currentVersion"]["userPrompt"] = "BBB {{company}} AAA {{notes}}"
    assert any("missing a piece" in p for p in _verify(fc)["problems"])


def test_verify_prompt_reports_only_first_missing_piece():
    fc = FakeClay()
    fc.add_column(_expected_inputs())
    fc.claygents["c_1"]["currentVersion"]["userPrompt"] = "X {{company}} Y {{notes}} Z"
    probs = [p for p in _verify(fc)["problems"] if "missing a piece" in p]
    assert probs == ["stored prompt is missing a piece of the Claygent prompt near: 'X '"]


def test_verify_prompt_long_piece_truncated_in_message():
    long = "L" * 100
    fc = FakeClay(claygents={"c_1": _claygent(prompt="short {{company}}{{notes}}")})
    fc.add_column(_expected_inputs(prompt="short {{company}}{{notes}}"))
    fc.claygents["c_1"]["currentVersion"]["userPrompt"] = long + "{{company}}{{notes}}"
    assert f"near: {'L' * 60!r}" in _verify(fc)["problems"][0]


def test_verify_prompt_fewer_variable_slots():
    fc = FakeClay()
    fc.add_column(_expected_inputs())
    b = fc.binding()
    # keep every literal piece but drop the second variable's slot
    b["prompt"]["formulaText"] = b["prompt"]["formulaText"].replace("Clay.formatForAIPrompt({{f_notes}})", '""')
    assert _verify(fc)["problems"] == ["stored prompt has fewer variable slots than the Claygent prompt"]


def test_verify_prompt_binding_unset():
    fc = FakeClay()
    fc.add_column({**_expected_inputs(), "prompt": None}, rerender=False)
    probs = _verify(fc)["problems"]
    assert probs[0].startswith("stored prompt is missing a piece")
    assert probs[1] == "stored prompt has fewer variable slots than the Claygent prompt"


def test_verify_mapping_keys_differ():
    fc = FakeClay()
    fc.add_column(_expected_inputs())
    fc.claygents["c_1"]["currentVersion"]["variables"].append({"name": "region"})
    assert _verify(fc)["problems"] == [
        "variable mapping ['{{company}}', '{{notes}}'] != Claygent variables ['{{company}}', '{{notes}}', '{{region}}']"]


def test_verify_mapping_unset_binding():
    fc = FakeClay()
    fc.add_column({**_expected_inputs(), "claygentFieldMapping": None}, rerender=False)
    assert "variable mapping [] != Claygent variables ['{{company}}', '{{notes}}']" in _verify(fc)["problems"]


def test_verify_var_map_expression_mismatch():
    fc = FakeClay()
    fc.add_column(_expected_inputs())
    r = _verify(fc, var_map={"company": "{{f_other}}", "notes": "{{f_notes}}", "ghost": "{{f_g}}"})
    assert r["problems"] == [
        "variable 'company' is mapped to 'Clay.formatForAIPrompt({{f_co}})', expected '{{f_other}}'",
        "variable 'ghost' is mapped to None, expected '{{f_g}}'",
    ]


def test_verify_collects_every_problem():
    fc = FakeClay()
    fc.add_column(_expected_inputs())
    v = fc.claygents["c_1"]["currentVersion"]
    v["modelSettings"]["model"] = "gpt-x"
    v["outputFormat"]["jsonSchema"] = json.dumps({"type": "object"})
    v["userPrompt"] = "New {{company}} {{notes}} {{region}}"
    v["variables"].append({"name": "region"})
    probs = _verify(fc)["problems"]
    assert len(probs) == 5
    assert probs[0] == "model differs from the Claygent's" and probs[1] == SCHEMA_PROBLEM
    assert probs[2].startswith("stored prompt is missing a piece")
    assert probs[3] == "stored prompt has fewer variable slots than the Claygent prompt"
    assert probs[4].startswith("variable mapping")


@pytest.mark.xfail(strict=True, reason="verify only checks that the Claygent's literal pieces appear in the stored "
                   "prompt in order; text REMOVED from the Claygent (a whole literal piece next to a variable) leaves "
                   "the stale column reporting ok=True")
def test_verify_flags_text_removed_from_claygent_prompt():
    fc = FakeClay()
    fc.add_column(_expected_inputs())
    # Claygent edited after the column's last write: the trailing instruction is deleted.
    fc.claygents["c_1"]["currentVersion"]["userPrompt"] = "Score {{company}} for fit.\nNotes: {{notes}}"
    assert _verify(fc)["ok"] is False


@pytest.mark.xfail(strict=True, reason="verify skips the schema copy entirely for a 'fields'-type json Claygent, so a "
                   "stale or missing answerSchemaType is reported ok=True despite the docstring's 'schema copy (exact)'")
def test_verify_checks_fields_type_schema_copy():
    fields = {"score": {"type": "number"}}
    fc = FakeClay(claygents={"c_1": _claygent(fmt={"type": "json", "fields": fields})})
    fc.add_column({**_expected_inputs(), "answerSchemaType": None})  # schema copy missing
    assert _verify(fc)["ok"] is False


# ── sync_claygent_column ──────────────────────────────────────────────────────

def _edit_claygent(fc, model="claude-opus-y", schema=None, prompt=None):
    v = fc.claygents["c_1"]["currentVersion"]
    v["modelSettings"]["model"] = model
    v["outputFormat"]["jsonSchema"] = json.dumps(schema or {**SCHEMA, "required": ["score"]})
    if prompt:
        v["userPrompt"] = prompt


def test_sync_recopies_schema_and_model_keeps_everything_else():
    fc = FakeClay()
    fc.add_column({**_expected_inputs(), "temperature": "0.2"},
                  extra_ts={"authAccountId": "aa_9", "conditionalRunFormulaText": "!!{{f_ready}}"})
    _edit_claygent(fc, prompt="Rate {{company}} given {{notes}}.")
    assert fc.client.verify_claygent_column(T, "f_1")["ok"] is False  # drifted
    n_before = len(fc.calls)

    r = fc.client.sync_claygent_column(T, "f_1")
    assert r == {"ok": True, "problems": [], "claygent_id": "c_1"}

    patch = [c for c in fc.calls[n_before:] if c["method"] == "PATCH"]
    assert len(patch) == 1 and patch[0]["path"] == f"{FIELDS}/f_1"
    ts = patch[0]["json"]["typeSettings"]
    assert set(patch[0]["json"]) == {"typeSettings"}
    assert ts["authAccountId"] == "aa_9" and ts["conditionalRunFormulaText"] == "!!{{f_ready}}"
    assert ts["actionKey"] == "use-ai" and ts["dataTypeSettings"] == {"type": "json"}
    b = {x["name"]: x for x in ts["inputsBinding"]}
    assert [x["name"] for x in ts["inputsBinding"]] == USE_AI_PARAMS  # same entries, same order
    assert b["model"] == {"name": "model", "formulaText": '"claude-opus-y"'}
    assert json.loads(json.loads(b["answerSchemaType"]["formulaMap"]["jsonSchema"]))["required"] == ["score"]
    assert b["temperature"] == {"name": "temperature", "formulaText": "0.2"}  # user value kept, not blanked
    assert b["_metadata"] == {"name": "_metadata", "formulaMap": {"modelSource": '"user"'}}
    assert b["prompt"]["formulaText"] == cc.ClayClient._claygent_prompt_formula(
        "Rate {{company}} given {{notes}}.", VAR_MAP)
    # mapping re-read from the column, unchanged
    assert b["claygentFieldMapping"]["formulaMap"] == _expected_inputs()["claygentFieldMapping"]


def test_sync_reads_complex_existing_mapping_back():
    vm = {"company": '{{f_co}} + " (" + ({{f_dom}} || "?") + ")"', "notes": "{{f_n}}"}
    fc = FakeClay()
    fc.add_column(_expected_inputs(var_map=vm))
    _edit_claygent(fc)
    r = fc.client.sync_claygent_column(T, "f_1")
    assert r["ok"] is True
    assert fc.binding()["claygentFieldMapping"]["formulaMap"]["{{company}}"] == f"Clay.formatForAIPrompt({vm['company']})"


def test_sync_with_explicit_var_map_remaps():
    fc = FakeClay()
    fc.add_column(_expected_inputs())
    new_vm = {"company": "{{f_co2}}", "notes": "{{f_notes}}"}
    r = fc.client.sync_claygent_column(T, "f_1", new_vm)
    assert r["ok"] is True
    assert fc.binding()["claygentFieldMapping"]["formulaMap"]["{{company}}"] == "Clay.formatForAIPrompt({{f_co2}})"
    assert "Clay.formatForAIPrompt({{f_co2}})" in fc.binding()["prompt"]["formulaText"]


def test_sync_condition_override_and_keep():
    fc = FakeClay()
    fc.add_column(_expected_inputs(), extra_ts={"conditionalRunFormulaText": "!!{{f_a}}"})
    fc.client.sync_claygent_column(T, "f_1")
    assert fc.ts()["conditionalRunFormulaText"] == "!!{{f_a}}"
    fc.client.sync_claygent_column(T, "f_1", condition="!!{{f_b}}")
    assert fc.ts()["conditionalRunFormulaText"] == "!!{{f_b}}"
    fc.client.sync_claygent_column(T, "f_1", condition="")
    assert fc.ts()["conditionalRunFormulaText"] == ""


def test_sync_without_byo_keeps_metadata_bare():
    fc = FakeClay()
    fc.add_column(_expected_inputs(byo=False))
    fc.client.sync_claygent_column(T, "f_1")
    assert fc.binding()["_metadata"] == {"name": "_metadata"}


@pytest.mark.parametrize("bad", [
    {"{{company}}": "{{f_co}}", "{{notes}}": "Clay.formatForAIPrompt({{f_notes}})"},   # not wrapped
    {"company": "Clay.formatForAIPrompt({{f_co}})", "{{notes}}": "Clay.formatForAIPrompt({{f_notes}})"},  # bad key
])
def test_sync_unreadable_mapping_raises_before_writing(bad):
    fc = FakeClay()
    fc.add_column({**_expected_inputs(), "claygentFieldMapping": bad}, rerender=False)
    with pytest.raises(ValueError, match="cannot read the existing mapping for .*; pass var_map"):
        fc.client.sync_claygent_column(T, "f_1")
    assert not [c for c in fc.calls if c["method"] == "PATCH"]


def test_sync_unreadable_mapping_ok_with_explicit_var_map():
    fc = FakeClay()
    fc.add_column({**_expected_inputs(), "claygentFieldMapping": {"{{company}}": "{{f_co}}"}}, rerender=False)
    assert fc.client.sync_claygent_column(T, "f_1", VAR_MAP)["ok"] is True


def test_sync_var_map_mismatch_raises_before_writing():
    fc = FakeClay()
    fc.add_column(_expected_inputs())
    fc.claygents["c_1"]["currentVersion"]["variables"].append({"name": "region"})
    with pytest.raises(ValueError, match="!= var_map keys"):
        fc.client.sync_claygent_column(T, "f_1")
    assert not [c for c in fc.calls if c["method"] == "PATCH"]


def test_sync_that_does_not_stick_raises():
    fc = FakeClay(ignore_patch=True)
    fc.add_column(_expected_inputs())
    _edit_claygent(fc)
    with pytest.raises(RuntimeError, match=r"sync did not stick on f_1: .*model differs"):
        fc.client.sync_claygent_column(T, "f_1")


def test_sync_verifies_with_the_var_map_it_wrote():
    # Server keeps the old mapping although the PATCH asked for a new one -> must raise.
    def keep_old_mapping(b):
        b["claygentFieldMapping"]["formulaMap"] = _expected_inputs()["claygentFieldMapping"]
    fc = FakeClay(mangle=keep_old_mapping)
    fc.add_column(_expected_inputs())
    with pytest.raises(RuntimeError, match=r"variable 'company' is mapped to"):
        fc.client.sync_claygent_column(T, "f_1", {"company": "{{f_new}}", "notes": "{{f_notes}}"})


@pytest.mark.xfail(strict=True, raises=KeyError, reason="sync on a column with no claygentId binding raises a bare "
                   "KeyError('claygentId') instead of a clear error (verify handles the same case gracefully)")
def test_sync_unbound_column_raises_clear_error():
    fc = FakeClay()
    inputs = _expected_inputs()
    del inputs["claygentId"]
    fc.add_column(inputs, rerender=False)
    with pytest.raises(ValueError, match="not bound"):
        fc.client.sync_claygent_column(T, "f_1")


@pytest.mark.xfail(strict=True, reason="sync only rewrites inputsBinding entries that already exist; a column with no "
                   "answerSchemaType entry (the 'Unable to parse the output schema' case sync is meant to repair) never "
                   "gets one, so sync PATCHes and then raises 'sync did not stick'")
def test_sync_adds_missing_schema_binding():
    fc = FakeClay()
    inputs = _expected_inputs()
    del inputs["answerSchemaType"]
    fc.add_column(inputs)
    assert fc.client.sync_claygent_column(T, "f_1")["ok"] is True
    assert "answerSchemaType" in fc.binding()


@pytest.mark.xfail(strict=True, reason="when the Claygent's output changes from a JSON schema to text, "
                   "claygent_column_inputs yields answerSchemaType=None and sync treats None as 'keep existing', so the "
                   "column keeps the OLD schema copy; verify skips the schema check for text output and reports ok")
def test_sync_clears_schema_when_claygent_switches_to_text():
    fc = FakeClay()
    fc.add_column(_expected_inputs())
    fc.claygents["c_1"]["currentVersion"]["outputFormat"] = {"type": "text"}
    assert fc.client.sync_claygent_column(T, "f_1")["ok"] is True
    assert fc.binding()["answerSchemaType"] == {"name": "answerSchemaType"}


@pytest.mark.xfail(strict=True, reason="sync infers byo_key from ANY non-empty _metadata formulaMap and then writes "
                   "modelSource '\"user\"', overwriting whatever modelSource the column had")
def test_sync_preserves_existing_model_source():
    fc = FakeClay()
    fc.add_column({**_expected_inputs(), "_metadata": {"modelSource": '"clay"'}})
    fc.client.sync_claygent_column(T, "f_1")
    assert fc.binding()["_metadata"]["formulaMap"] == {"modelSource": '"clay"'}


# ── unwrap_claygent_output ────────────────────────────────────────────────────

U = cc.ClayClient.unwrap_claygent_output
KEYS = ["score", "reason"]


@pytest.mark.parametrize("value,expected", [
    ({"score": 7, "reason": "fit"}, {"score": 7, "reason": "fit"}),                      # top level
    ({"score": 7, "other": 1}, {"score": 7, "other": 1}),                                # any key -> top level
    ({"body": {"score": 7, "reason": "fit"}}, {"score": 7, "reason": "fit"}),            # one level down
    ({"parameters": {"score": 1, "reason": "x", "extra": 2}}, {"score": 1, "reason": "x", "extra": 2}),
    ({"body": {"score": 7}}, {}),                                                         # nested, partial -> {}
    ({"a": "s", "b": [1], "c": {"score": 1, "reason": "r"}}, {"score": 1, "reason": "r"}),  # skips non-dicts
    ({"a": {"score": 1, "reason": "first"}, "b": {"score": 2, "reason": "second"}}, {"score": 1, "reason": "first"}),
    ({"body": {"inner": {"score": 1, "reason": "r"}}}, {}),                              # only one level searched
    ({}, {}),
    ({"x": 1}, {}),
    ('{"score": 3, "reason": "j"}', {"score": 3, "reason": "j"}),                       # JSON string
    ('{"body": {"score": 3, "reason": "j"}}', {"score": 3, "reason": "j"}),             # JSON string, wrapped
    ("not json", {}),
    ("", {}),
    ('[{"score": 1, "reason": "r"}]', {}),                                               # JSON list
    ('"just a string"', {}),
    (None, {}),
    ([{"score": 1, "reason": "r"}], {}),
    (42, {}),
])
def test_unwrap(value, expected):
    assert U(value, KEYS) == expected


def test_unwrap_returns_same_object_and_is_static():
    v = {"score": 1, "reason": "r"}
    assert U(v, KEYS) is v
    nested = {"body": {"score": 1, "reason": "r"}}
    assert U(nested, KEYS) is nested["body"]
    assert make_client(cc).unwrap_claygent_output(v, KEYS) is v


def test_use_ai_package_id_matches_create_action_column_docs():
    assert cc.ClayClient.USE_AI_PACKAGE_ID == USE_AI_PKG
    assert f"use-ai                            / {USE_AI_PKG}" in cc.ClayClient.create_action_column.__doc__


def test_u_escape_helper_itself():
    assert _has_u_escape(json.dumps("é")) and not _has_u_escape(json.dumps("é", ensure_ascii=False))
    assert not _has_u_escape(json.dumps(json.dumps("é")))  # escaped backslash + 'u' is not an escape
