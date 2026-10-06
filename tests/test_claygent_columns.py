"""ClayClient saved-Claygent column methods, driven through a recording FakeSession.

Covers get_claygent, _use_ai_param_names, _claygent_prompt_formula, claygent_column_inputs,
create_claygent_column, _claygent_column_settings, sync_claygent_column,
verify_claygent_column, unwrap_claygent_output and their private helpers.

A small in-memory "Clay" (FakeClay) answers the routes and models the one server behaviour
the docs measured live (clay-api-reference "Claygent columns from code"): on every column
create/update Clay discards the prompt in the request and re-renders it from the bound
Claygent's CURRENT prompt, each {{variable}} replaced by its claygentFieldMapping expression,
without spaces around `+`. Whether Clay really does that is a live claim and cannot be
re-checked offline; these tests check that the code does what its docstrings say given it.

Shapes labelled "UI-made" (the Fields schema copy, modelSource `generated`, a UI-saved prompt
with re-flowed whitespace, an answer wrapped in `parameters`) mirror columns and cells read
from a live workspace on 2026-10-06, with every id, name and text replaced.
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
SCHEMA_PROBLEM = "output schema copy differs from the Claygent's (UI: 'Unable to parse the output schema')"

# Every input the fake `use-ai` action declares, in its declared order.
USE_AI_PARAMS = ["useCase", "prompt", "model", "claygentId", "claygentFieldMapping",
                 "answerSchemaType", "temperature", "_metadata"]

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["score", "reason"],
          "properties": {"score": {"type": "number"}, "reason": {"type": "string", "description": "é"}}}
PROMPT = 'Score {{company}} for fit.\nNotes: {{notes}} — say "why". é'
VAR_MAP = {"company": "{{f_co}}", "notes": "{{f_notes}}"}

# A Fields-mode output format, shaped like the UI's (each field carries id/type/options/description).
FIELDS_SPEC = {
    "score": {"id": "00000000-0000-4000-8000-000000000001", "type": "number", "options": "", "description": ""},
    "summary": {"id": "00000000-0000-4000-8000-000000000002", "type": "string", "options": "",
                "description": "é — one line"},
}
FIELDS_FMT = {"type": "json", "fields": FIELDS_SPEC, "jsonType": "Fields"}
# The copy a UI-made Fields column carries: keys in this order, fields as a compact object literal.
FIELDS_COPY = {
    "type": '"json"',
    "fields": '{"score":{"id":"00000000-0000-4000-8000-000000000001","type":"number","options":"",'
              '"description":""},"summary":{"id":"00000000-0000-4000-8000-000000000002","type":"string",'
              '"options":"","description":"é — one line"}}',
    "jsonType": '"Fields"',
}


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
    each mapping key ({{var}}) -> its mapping expression; literals as JSON strings; no spaces
    around +."""
    keys = sorted(fmap, key=len, reverse=True)
    parts = re.split("(" + "|".join(map(re.escape, keys)) + ")", user_prompt) if keys else [user_prompt]
    pieces = []
    for i, part in enumerate(parts):
        if i % 2:
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

    def writes(self):
        return [c for c in self.calls if c["method"] != "GET"]

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


def _expected_inputs(byo=True, prompt=PROMPT, var_map=VAR_MAP, model="claude-sonnet-x", schema=SCHEMA,
                     schema_copy=None):
    exp = {n: None for n in USE_AI_PARAMS}
    exp.update({
        "useCase": '"claygent"',
        "claygentId": '"c_1"',
        "model": json.dumps(model),
        "claygentFieldMapping": {"{{" + k + "}}": f"Clay.formatForAIPrompt({e})" for k, e in var_map.items()},
        "prompt": cc.ClayClient._claygent_prompt_formula(prompt, var_map),
        "answerSchemaType": schema_copy or {"type": '"json"', "jsonType": '"JSONSchema"',
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
    # {{not a var}} is not in var_map and {single} is not a placeholder at all.
    assert cc.ClayClient._claygent_prompt_formula("{{not a var}} {x}", {}) == '"{{not a var}} {x}"'


def test_prompt_formula_only_var_map_names_become_slots():
    # A {{token}} the Claygent does not declare stays literal text; nothing raises.
    assert cc.ClayClient._claygent_prompt_formula("{{a}} {{b}}", {"a": "1"}) == \
        'Clay.formatForAIPrompt(1) + " {{b}}"'


@pytest.mark.parametrize("name", ["Company Name", "Account.Domain", "Parent: Website", "a-b"])
def test_prompt_formula_variable_names_with_spaces_dots_colons(name):
    f = cc.ClayClient._claygent_prompt_formula("Visit {{" + name + "}} now, {{" + name + "}}.", {name: "{{f_w}}"})
    assert f == '"Visit " + Clay.formatForAIPrompt({{f_w}}) + " now, " + Clay.formatForAIPrompt({{f_w}}) + "."'


def test_prompt_formula_longest_name_wins():
    # "{{a b}}" must not be read as a shorter name that happens to be a prefix.
    f = cc.ClayClient._claygent_prompt_formula("{{a}}|{{a b}}", {"a": "1", "a b": "2"})
    assert f == 'Clay.formatForAIPrompt(1) + "|" + Clay.formatForAIPrompt(2)'


# ── _claygent_schema_copy / output formats ────────────────────────────────────

def test_schema_copy_json_schema_is_a_string_literal_of_the_claygent_string():
    copy_ = cc.ClayClient._claygent_schema_copy({"type": "json", "jsonType": "JSONSchema",
                                                 "jsonSchema": json.dumps(SCHEMA, ensure_ascii=False)})
    assert list(copy_) == ["type", "jsonType", "jsonSchema"]
    assert json.loads(json.loads(copy_["jsonSchema"])) == SCHEMA
    assert not _has_u_escape(copy_["jsonSchema"])


def test_schema_copy_fields_matches_the_ui_encoding_exactly():
    assert cc.ClayClient._claygent_schema_copy(FIELDS_FMT) == FIELDS_COPY
    assert list(cc.ClayClient._claygent_schema_copy(FIELDS_FMT)) == ["type", "fields", "jsonType"]


def test_schema_copy_fields_empty():
    assert cc.ClayClient._claygent_schema_copy({"type": "json", "fields": {}, "jsonType": "Fields"})["fields"] == "{}"


@pytest.mark.parametrize("fmt,desc", [
    (None, "null"),
    ({"type": "text"}, "type='text', jsonType=None"),
    ({"type": "json"}, "type='json', jsonType=None"),                                   # no mode marker
    ({"type": "json", "fields": {"a": {}}}, "type='json', jsonType=None"),
    ({"type": "json", "jsonType": "JSONSchema", "jsonSchema": SCHEMA}, "type='json', jsonType='JSONSchema'"),
    ({"type": "json", "jsonType": "Fields"}, "type='json', jsonType='Fields'"),          # no fields
    ({"type": "json", "jsonType": "Table"}, "type='json', jsonType='Table'"),
    ({}, "type=None, jsonType=None"),
    ("text", "'text'"),
])
def test_schema_copy_unsupported_formats_raise(fmt, desc):
    with pytest.raises(ValueError, match=re.escape(f"unsupported output format ({desc})")):
        cc.ClayClient._claygent_schema_copy(fmt)


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
    fc = FakeClay(claygents={"c_1": _claygent(fmt=FIELDS_FMT)})
    inputs = fc.client.claygent_column_inputs("c_1", VAR_MAP)
    assert inputs["answerSchemaType"] == FIELDS_COPY
    assert not _has_u_escape(inputs["answerSchemaType"]["fields"])


@pytest.mark.parametrize("fmt,desc", [
    (None, "null"),
    ({"type": "text"}, "type='text', jsonType=None"),
    ({"type": "json"}, "type='json', jsonType=None"),
])
def test_inputs_unsupported_output_format_fails_closed(fmt, desc):
    cg = _claygent()
    cg["currentVersion"]["outputFormat"] = fmt
    fc = FakeClay(claygents={"c_1": cg})
    with pytest.raises(ValueError, match=re.escape(
            f"claygent_column_inputs: Claygent c_1 has an unsupported output format ({desc}); "
            "only JSON Schema and Fields output can be copied onto a column")):
        fc.client.claygent_column_inputs("c_1", VAR_MAP)
    assert [c["path"] for c in fc.calls] == [CG_PATH]  # refused before the action catalog is read


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
    with pytest.raises(ValueError, match=r"^claygent_column_inputs: Claygent c_1 variables "
                                         r"\['company', 'notes'\] != var_map keys"):
        fc.client.claygent_column_inputs("c_1", var_map)
    # validated before the action catalog is read
    assert [c["path"] for c in fc.calls] == [CG_PATH]


def test_inputs_any_expression_in_var_map():
    fc = FakeClay()
    vm = {"company": '{{f_co}} + " (" + {{f_dom}} + ")"', "notes": "JSON.stringify({{f_n}})"}
    inputs = fc.client.claygent_column_inputs("c_1", vm)
    assert inputs["claygentFieldMapping"]["{{company}}"] == 'Clay.formatForAIPrompt({{f_co}} + " (" + {{f_dom}} + ")")'
    assert "Clay.formatForAIPrompt(JSON.stringify({{f_n}}))" in inputs["prompt"]


def test_inputs_spaced_and_dotted_variable_names():
    prompt = "Visit {{Account.Domain}} for {{Company Name}} ({{Parent: Site}}), then {{Account.Domain}} again."
    names = ("Account.Domain", "Company Name", "Parent: Site")
    fc = FakeClay(claygents={"c_1": _claygent(prompt=prompt, variables=names)})
    vm = {"Account.Domain": "{{f_w}}", "Company Name": "{{f_n}}", "Parent: Site": "{{f_p}}"}
    inputs = fc.client.claygent_column_inputs("c_1", vm)
    assert inputs["claygentFieldMapping"] == {"{{Account.Domain}}": "Clay.formatForAIPrompt({{f_w}})",
                                              "{{Company Name}}": "Clay.formatForAIPrompt({{f_n}})",
                                              "{{Parent: Site}}": "Clay.formatForAIPrompt({{f_p}})"}
    assert inputs["prompt"].count("Clay.formatForAIPrompt(") == 4
    assert "{{Account" not in inputs["prompt"] and "{{Parent" not in inputs["prompt"]


# ── create_claygent_column ────────────────────────────────────────────────────

def _binding_list(inputs):
    out = []
    for k, v in inputs.items():
        if isinstance(v, dict):
            out.append({"name": k, "formulaMap": v})
        elif v:
            out.append({"name": k, "formulaText": v})
        else:
            out.append({"name": k})
    return out


def test_create_exact_request_then_verifies():
    fc = FakeClay()
    col = fc.client.create_claygent_column(T, "Research", "c_1", VAR_MAP, view_id="gv_1",
                                          auth_account_id="aa_9", condition='{{f_ready}} == "yes"')
    assert col["id"] == "f_new"
    post = [c for c in fc.calls if c["method"] == "POST"]
    assert len(post) == 1 and post[0]["path"] == FIELDS
    binding = _binding_list(_expected_inputs())
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


def test_create_fields_claygent_sends_the_ui_fields_copy_and_verifies():
    fc = FakeClay(claygents={"c_1": _claygent(fmt=FIELDS_FMT)})
    fc.client.create_claygent_column(T, "Research", "c_1", VAR_MAP)
    body = next(c for c in fc.calls if c["method"] == "POST")["json"]
    sent = {x["name"]: x for x in body["typeSettings"]["inputsBinding"]}
    assert sent["answerSchemaType"] == {"name": "answerSchemaType", "formulaMap": FIELDS_COPY}


def test_create_spaced_variable_names_round_trip():
    prompt = "Visit {{Account.Domain}} as {{Company Name}}."
    fc = FakeClay(claygents={"c_1": _claygent(prompt=prompt, variables=("Account.Domain", "Company Name"))})
    fc.client.create_claygent_column(T, "Research", "c_1", {"Account.Domain": "{{f_w}}", "Company Name": "{{f_n}}"})
    assert fc.binding("f_new")["prompt"]["formulaText"] == \
        '"Visit "+Clay.formatForAIPrompt({{f_w}})+" as "+Clay.formatForAIPrompt({{f_n}})+"."'


@pytest.mark.parametrize("fmt", [None, {"type": "text"}])
def test_create_unsupported_output_format_writes_nothing(fmt):
    cg = _claygent()
    cg["currentVersion"]["outputFormat"] = fmt
    fc = FakeClay(claygents={"c_1": cg})
    with pytest.raises(ValueError, match="unsupported output format"):
        fc.client.create_claygent_column(T, "Research", "c_1", VAR_MAP)
    assert fc.writes() == [] and fc.fields == []


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
    assert not fc.writes()


# ── _claygent_column_settings ─────────────────────────────────────────────────

def test_column_settings_returns_typesettings_and_binding_index():
    fc = FakeClay()
    fc.add_column(_expected_inputs())
    ts, b = fc.client._claygent_column_settings(T, "f_1", "verify_claygent_column")
    assert ts["actionKey"] == "use-ai"
    assert set(b) == set(USE_AI_PARAMS) and b["claygentId"] == {"name": "claygentId", "formulaText": '"c_1"'}


def test_column_settings_unknown_field_names_the_calling_method():
    fc = FakeClay()
    with pytest.raises(ValueError, match="^sync_claygent_column: field f_zz not found on t_1$"):
        fc.client._claygent_column_settings(T, "f_zz", "sync_claygent_column")


def test_column_settings_field_without_type_settings():
    fc = FakeClay()
    fc.fields.append({"id": "f_txt", "name": "Plain", "type": "text"})
    assert fc.client._claygent_column_settings(T, "f_txt", "verify_claygent_column") == ({}, {})


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
                                         {"name": "claygentId", "formulaText": '""'},
                                         {"name": "claygentId", "formulaText": "'c_1'"},   # not a JSON literal
                                         {"name": "claygentId", "formulaText": "42"}])
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
    with pytest.raises(ValueError, match="^verify_claygent_column: field f_1 not found on t_1$"):
        _verify(FakeClay())


def test_verify_model_differs_after_claygent_edit():
    fc = FakeClay()
    fc.add_column(_expected_inputs())
    fc.claygents["c_1"]["currentVersion"]["modelSettings"]["model"] = "gpt-x"
    r = _verify(fc)
    assert r["ok"] is False and r["problems"] == ["model differs from the Claygent's"]


@pytest.mark.parametrize("model", [None, "'claude-sonnet-x'"])  # unset, or not a JSON literal
def test_verify_model_binding_unset_or_unreadable(model):
    fc = FakeClay()
    fc.add_column({**_expected_inputs(), "model": model})
    assert _verify(fc)["problems"] == ["model differs from the Claygent's"]


@pytest.mark.parametrize("stored", [
    None,                                                                       # bare entry
    {"type": '"json"', "jsonType": '"JSONSchema"'},                             # no jsonSchema key
    {"type": '"json"', "jsonType": '"JSONSchema"', "jsonSchema": "{not json"},  # unparsable
    {"type": '"json"', "jsonType": '"JSONSchema"', "jsonSchema": json.dumps(SCHEMA)},  # single-encoded
    {"type": '"json"', "jsonType": '"JSONSchema"',
     "jsonSchema": json.dumps(json.dumps({**SCHEMA, "required": ["score"]}))},  # different schema
    {"type": '"json"', "jsonSchema": json.dumps(json.dumps(SCHEMA))},           # no mode marker
    {"type": '"json"', "jsonType": '"Fields"', "jsonSchema": json.dumps(json.dumps(SCHEMA))},  # wrong marker
    FIELDS_COPY,                                                                # the other mode's copy
])
def test_verify_schema_copy_problems(stored):
    fc = FakeClay()
    fc.add_column({**_expected_inputs(), "answerSchemaType": stored})
    assert _verify(fc)["problems"] == [SCHEMA_PROBLEM]


def test_verify_schema_compare_ignores_key_order_and_whitespace():
    fc = FakeClay()
    reordered = json.dumps(dict(reversed(list(SCHEMA.items()))), indent=2)
    fc.add_column({**_expected_inputs(), "answerSchemaType": {"jsonSchema": json.dumps(reordered),
                                                              "jsonType": ' "JSONSchema" ', "type": '"json"'}})
    assert _verify(fc)["ok"] is True


def test_verify_schema_stale_after_claygent_schema_edit():
    fc = FakeClay()
    fc.add_column(_expected_inputs())
    new = {**SCHEMA, "required": ["score"]}
    fc.claygents["c_1"]["currentVersion"]["outputFormat"]["jsonSchema"] = json.dumps(new)
    assert _verify(fc)["problems"] == [SCHEMA_PROBLEM]


def test_verify_claygent_schema_that_is_not_json_is_a_problem():
    fc = FakeClay()
    fc.add_column(_expected_inputs())
    fc.claygents["c_1"]["currentVersion"]["outputFormat"]["jsonSchema"] = "{oops"
    assert _verify(fc)["problems"] == [SCHEMA_PROBLEM]


def test_verify_fields_copy_from_a_ui_made_column_is_ok():
    fc = FakeClay(claygents={"c_1": _claygent(fmt=FIELDS_FMT)})
    fc.add_column(_expected_inputs(schema_copy=FIELDS_COPY))
    assert _verify(fc, var_map=VAR_MAP) == {"ok": True, "problems": [], "claygent_id": "c_1"}


@pytest.mark.parametrize("stored", [
    None,                                                                           # copy missing
    {"type": '"json"', "fields": FIELDS_COPY["fields"]},                            # no jsonType marker
    {**FIELDS_COPY, "fields": '{"totally_wrong":{"type":"string"}}'},              # stale / wrong fields
    {**FIELDS_COPY, "fields": json.dumps(json.dumps(FIELDS_SPEC))},                # double-encoded
    {**FIELDS_COPY, "fields": "{broken"},
])
def test_verify_checks_the_fields_copy(stored):
    fc = FakeClay(claygents={"c_1": _claygent(fmt=FIELDS_FMT)})
    fc.add_column({**_expected_inputs(), "answerSchemaType": stored})
    assert _verify(fc)["problems"] == [SCHEMA_PROBLEM]


def test_verify_fields_copy_stale_after_claygent_fields_edit():
    fc = FakeClay(claygents={"c_1": _claygent(fmt=copy.deepcopy(FIELDS_FMT))})
    fc.add_column(_expected_inputs(schema_copy=FIELDS_COPY))
    fc.claygents["c_1"]["currentVersion"]["outputFormat"]["fields"]["summary"]["type"] = "boolean"
    assert _verify(fc)["problems"] == [SCHEMA_PROBLEM]


@pytest.mark.parametrize("fmt,desc", [
    (None, "null"),
    ({"type": "text"}, "type='text', jsonType=None"),
    ({"type": "json", "jsonType": "Table"}, "type='json', jsonType='Table'"),
])
def test_verify_unsupported_output_format_is_reported_not_passed(fmt, desc):
    fc = FakeClay()
    fc.add_column(_expected_inputs())
    fc.claygents["c_1"]["currentVersion"]["outputFormat"] = fmt
    r = _verify(fc)
    assert r["ok"] is False
    assert r["problems"] == [f"schema copy not checked: unsupported output format ({desc})"]


def test_verify_prompt_differs_after_claygent_prompt_edit():
    fc = FakeClay()
    fc.add_column(_expected_inputs())
    fc.claygents["c_1"]["currentVersion"]["userPrompt"] = PROMPT.replace("for fit.", "for ICP fit.")
    r = _verify(fc)
    assert r["ok"] is False
    assert r["problems"] == ["stored prompt text differs from the Claygent prompt near: ' for ICP fit.\\nNotes: '"]


def test_verify_prompt_pieces_must_be_in_order():
    fc = FakeClay(claygents={"c_1": _claygent(prompt="AAA {{company}} BBB {{notes}}")})
    fc.add_column(_expected_inputs(prompt="AAA {{company}} BBB {{notes}}"))
    fc.claygents["c_1"]["currentVersion"]["userPrompt"] = "BBB {{company}} AAA {{notes}}"
    assert _verify(fc)["problems"] == ["stored prompt text differs from the Claygent prompt near: 'BBB '"]


def test_verify_prompt_reports_only_first_differing_piece():
    fc = FakeClay()
    fc.add_column(_expected_inputs())
    fc.claygents["c_1"]["currentVersion"]["userPrompt"] = "X {{company}} Y {{notes}} Z"
    probs = [p for p in _verify(fc)["problems"] if "differs" in p]
    assert probs == ["stored prompt text differs from the Claygent prompt near: 'X '"]


def test_verify_prompt_long_piece_truncated_in_message():
    long = "L" * 100
    fc = FakeClay(claygents={"c_1": _claygent(prompt="short {{company}}{{notes}}")})
    fc.add_column(_expected_inputs(prompt="short {{company}}{{notes}}"))
    fc.claygents["c_1"]["currentVersion"]["userPrompt"] = long + "{{company}}{{notes}}"
    assert f"near: {'L' * 60!r}" in _verify(fc)["problems"][0]


def test_verify_flags_text_removed_from_claygent_prompt():
    fc = FakeClay()
    fc.add_column(_expected_inputs())
    # Claygent edited after the column's last write: the trailing instruction is deleted.
    fc.claygents["c_1"]["currentVersion"]["userPrompt"] = "Score {{company}} for fit.\nNotes: {{notes}}"
    assert _verify(fc)["problems"] == [
        "stored prompt text differs from the Claygent prompt near: ' — say \"why\". é'"]


def test_verify_flags_text_removed_before_the_first_variable():
    fc = FakeClay()
    fc.add_column(_expected_inputs())
    fc.claygents["c_1"]["currentVersion"]["userPrompt"] = PROMPT.replace("Score ", "")
    assert _verify(fc)["problems"] == ["stored prompt text differs from the Claygent prompt near: 'Score '"]


def test_verify_flags_text_added_to_claygent_prompt():
    fc = FakeClay()
    fc.add_column(_expected_inputs())
    fc.claygents["c_1"]["currentVersion"]["userPrompt"] = PROMPT + " Be brief."
    assert _verify(fc)["ok"] is False


def test_verify_flags_a_moved_variable():
    fc = FakeClay(claygents={"c_1": _claygent(prompt="A {{company}}{{notes}} B")})
    fc.add_column(_expected_inputs(prompt="A {{company}}{{notes}} B"))
    fc.claygents["c_1"]["currentVersion"]["userPrompt"] = "A {{company}} B{{notes}}"
    assert _verify(fc)["problems"] == ["stored prompt text differs from the Claygent prompt near: ' B'"]


def test_verify_prompt_fewer_variable_slots():
    fc = FakeClay()
    fc.add_column(_expected_inputs())
    b = fc.binding()
    # keep every literal piece but drop the second variable's slot
    b["prompt"]["formulaText"] = b["prompt"]["formulaText"].replace("Clay.formatForAIPrompt({{f_notes}})", '""')
    assert _verify(fc)["problems"] == ["stored prompt has 1 variable slot(s), the Claygent prompt has 2"]


def test_verify_prompt_extra_variable_slot():
    fc = FakeClay()
    fc.add_column(_expected_inputs())
    b = fc.binding()
    b["prompt"]["formulaText"] += "+Clay.formatForAIPrompt({{f_x}})"
    assert _verify(fc)["problems"] == ["stored prompt has 3 variable slot(s), the Claygent prompt has 2"]


def test_verify_prompt_slot_count_and_text_both_reported():
    fc = FakeClay()
    fc.add_column(_expected_inputs())
    fc.claygents["c_1"]["currentVersion"]["userPrompt"] = "Only {{company}} here."
    fc.claygents["c_1"]["currentVersion"]["variables"].pop()
    probs = _verify(fc)["problems"]
    assert probs[:2] == ["stored prompt text differs from the Claygent prompt near: 'Only '",
                         "stored prompt has 2 variable slot(s), the Claygent prompt has 1"]


def test_verify_prompt_binding_unset():
    fc = FakeClay()
    fc.add_column({**_expected_inputs(), "prompt": None}, rerender=False)
    assert _verify(fc)["problems"] == [
        "stored prompt text differs from the Claygent prompt near: 'Score '",
        "stored prompt has 0 variable slot(s), the Claygent prompt has 2"]


@pytest.mark.parametrize("stored", [
    '"a" +',                                   # dangling +
    '+ "a"',                                   # leading +
    '"a" "b"',                                 # no operator
    '"unterminated',
    'Clay.formatForAIPrompt({{f_co}}',          # unbalanced
    'Clay.formatForAIPrompt({{f_co}} + ")"',    # unbalanced after a string holding ')'
    'Clay.formatForAIPrompt({{f_co}} + ")',     # unterminated string inside a slot
    '"a" + {{f_co}}',                          # a bare reference, not a slot
    "'single'",
])
def test_verify_unreadable_prompt_formula_is_a_problem(stored):
    fc = FakeClay()
    fc.add_column(_expected_inputs())
    fc.binding()["prompt"]["formulaText"] = stored
    assert _verify(fc)["problems"] == [
        "stored prompt is not a +-joined list of string literals and Clay.formatForAIPrompt(...) slots"]


def test_verify_whitespace_reflowed_by_a_ui_save_is_ok():
    # UI-made: a column saved from the UI stored the Claygent's prompt with the blank line after a
    # heading dropped, trailing spaces gone, the trailing blank lines dropped, and " + " separators.
    prompt = "### Goal\n\nScore {{company}}.  \n\n\n### Notes\n\n{{notes}}\n\n"
    fc = FakeClay(claygents={"c_1": _claygent(prompt=prompt)}, rerender=False)
    fc.add_column(_expected_inputs(prompt=prompt), rerender=False)
    fc.binding()["prompt"]["formulaText"] = (
        '"### Goal\\nScore " + Clay.formatForAIPrompt({{f_co}}) + ".\\n### Notes\\n" + '
        'Clay.formatForAIPrompt({{f_notes}})')
    assert _verify(fc, var_map=VAR_MAP) == {"ok": True, "problems": [], "claygent_id": "c_1"}


def test_verify_whitespace_inside_a_line_still_counts():
    fc = FakeClay()
    fc.add_column(_expected_inputs())
    fc.claygents["c_1"]["currentVersion"]["userPrompt"] = PROMPT.replace("for fit.", "for  fit.")
    assert _verify(fc)["ok"] is False


def test_verify_slot_expressions_with_parens_quotes_and_escapes():
    vm = {"company": '{{f_co}} + " (" + ({{f_dom}} || \'?\') + ")"', "notes": 'String({{f_n}}).replace(/x/g, "\\")")'}
    fc = FakeClay()
    fc.add_column(_expected_inputs(var_map=vm))
    assert _verify(fc, var_map=vm)["ok"] is True


# var_map expressions holding a regex literal with a quote or a paren in it: bracket matching alone
# cannot find where such a slot ends.
REGEX_EXPRS = ['{{f_x}}.replace(/"/g, "\'")', "{{f_x}}.replace(/'/g, \"\")", '{{f_x}}.replace(/\\(/g, "")']


@pytest.mark.parametrize("expr", REGEX_EXPRS)
def test_regex_literal_var_map_creates_verifies_and_syncs(expr):
    # The slot is matched against the column's own mapping expressions first, so create and sync
    # read their write back as ok instead of raising after it.
    vm = {"company": expr, "notes": "{{f_notes}}"}
    fc = FakeClay()
    fc.client.create_claygent_column(T, "Research", "c_1", vm)
    stored = fc.binding("f_new")["prompt"]["formulaText"]
    assert f"Clay.formatForAIPrompt({expr})" in stored
    assert cc.ClayClient._prompt_formula_segments(stored) is None  # what the bracket scan alone made of it
    assert fc.client.verify_claygent_column(T, "f_new", var_map=vm) == {"ok": True, "problems": [], "claygent_id": "c_1"}
    _edit_claygent(fc)
    assert fc.client.sync_claygent_column(T, "f_new") == {"ok": True, "problems": [], "claygent_id": "c_1"}
    assert fc.binding("f_new")["claygentFieldMapping"]["formulaMap"]["{{company}}"] == f"Clay.formatForAIPrompt({expr})"


def test_regex_literal_slot_still_catches_prompt_drift():
    fc = FakeClay()
    fc.add_column(_expected_inputs(var_map={"company": REGEX_EXPRS[0], "notes": "{{f_notes}}"}))
    fc.claygents["c_1"]["currentVersion"]["userPrompt"] = PROMPT.replace(" for fit.", "")  # text removed
    assert _verify(fc)["problems"] == ["stored prompt text differs from the Claygent prompt near: '\\nNotes: '"]


def test_prompt_segments_match_the_columns_own_slots_first():
    seg = cc.ClayClient._prompt_formula_segments
    slot = f"Clay.formatForAIPrompt({REGEX_EXPRS[0]})"
    assert seg('"a"+' + slot + '+"b"') is None
    assert seg('"a"+' + slot + '+"b"', [slot]) == ["a", "b"]
    # mapping values that are not slots are ignored; a slot that is none of them falls back to
    # bracket matching
    assert seg('"a" + Clay.formatForAIPrompt({{f_y}})', [None, 42, "{{f_y}}", slot]) == ["a", ""]
    # the longest known slot wins when one is a prefix of another
    short = 'Clay.formatForAIPrompt(/"/)'
    longer = short + '.concat(/"/)'
    assert seg(longer + '+"z"', [short, longer]) == ["", "z"]


def test_verify_spaced_and_dotted_variable_names():
    # UI-made: Claygent variables such as "Account.Domain" or "Parent: Website".
    prompt = "Visit {{Account.Domain}} ({{Parent: Website}}). Use only {{Account.Domain}}."
    names = ("Account.Domain", "Parent: Website")
    vm = {"Account.Domain": "{{f_w}}", "Parent: Website": "{{f_p}}"}
    fc = FakeClay(claygents={"c_1": _claygent(prompt=prompt, variables=names)})
    fc.add_column(_expected_inputs(prompt=prompt, var_map=vm))
    assert _verify(fc, var_map=vm) == {"ok": True, "problems": [], "claygent_id": "c_1"}
    assert fc.binding()["prompt"]["formulaText"].count("Clay.formatForAIPrompt(") == 3


def test_verify_undeclared_placeholder_is_literal_text():
    # {{other}} is not a declared variable: it is plain text on both sides.
    prompt = "Score {{company}} and {{notes}} with {{other}} as text."
    fc = FakeClay(claygents={"c_1": _claygent(prompt=prompt)})
    fc.add_column(_expected_inputs(prompt=prompt))
    assert _verify(fc)["ok"] is True


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
    assert probs[2].startswith("stored prompt text differs")
    assert probs[3] == "stored prompt has 2 variable slot(s), the Claygent prompt has 3"
    assert probs[4].startswith("variable mapping")


# ── sync_claygent_column ──────────────────────────────────────────────────────

def _edit_claygent(fc, model="claude-opus-y", schema=None, prompt=None):
    v = fc.claygents["c_1"]["currentVersion"]
    v["modelSettings"]["model"] = model
    v["outputFormat"]["jsonSchema"] = json.dumps(schema or {**SCHEMA, "required": ["score"]})
    if prompt:
        v["userPrompt"] = prompt


def _patch_bindings(fc, n_before=0):
    patches = [c for c in fc.calls[n_before:] if c["method"] == "PATCH"]
    assert len(patches) == 1
    return patches[0]["json"]["typeSettings"]["inputsBinding"]


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


def test_sync_reads_spaced_and_dotted_names_back():
    prompt = "Visit {{Account.Domain}} for {{Company Name}} ({{Parent: Website}})."
    names = ("Account.Domain", "Company Name", "Parent: Website")
    vm = {"Account.Domain": "{{f_w}}", "Company Name": "{{f_n}}", "Parent: Website": "{{f_p}}"}
    fc = FakeClay(claygents={"c_1": _claygent(prompt=prompt, variables=names)})
    fc.add_column(_expected_inputs(prompt=prompt, var_map=vm))
    _edit_claygent(fc)
    assert fc.client.sync_claygent_column(T, "f_1") == {"ok": True, "problems": [], "claygent_id": "c_1"}
    assert fc.binding()["claygentFieldMapping"]["formulaMap"] == _expected_inputs(var_map=vm)["claygentFieldMapping"]


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


@pytest.mark.parametrize("meta", [
    {"modelSource": "generated"},      # UI-made: Clay's own value, written unquoted
    {"modelSource": '"generated"'},
    {"modelSource": '"clay"'},
    {"modelSource": '"user"', "other": '"x"'},
])
def test_sync_never_changes_the_columns_metadata(meta):
    # _metadata decides which key a column bills; sync must send it back exactly as found.
    fc = FakeClay()
    fc.add_column({**_expected_inputs(), "_metadata": meta})
    _edit_claygent(fc)
    n_before = len(fc.calls)
    fc.client.sync_claygent_column(T, "f_1")
    sent = {x["name"]: x for x in _patch_bindings(fc, n_before)}
    assert sent["_metadata"] == {"name": "_metadata", "formulaMap": meta}
    assert fc.binding()["_metadata"]["formulaMap"] == meta


def test_sync_never_adds_metadata_the_column_lacks():
    fc = FakeClay()  # the fake action declares _metadata
    inputs = _expected_inputs()
    del inputs["_metadata"]
    fc.add_column(inputs)
    fc.client.sync_claygent_column(T, "f_1")
    assert "_metadata" not in [x["name"] for x in _patch_bindings(fc)]


def test_sync_ui_made_column_keeps_its_billing_and_extra_inputs():
    # UI-made: modelSource `generated`, an input the action no longer declares, a Fields copy.
    fc = FakeClay(claygents={"c_1": _claygent(fmt=FIELDS_FMT)},
                  params=[p for p in USE_AI_PARAMS if p != "_metadata"] + ["maxTokens"])
    inputs = _expected_inputs(schema_copy=FIELDS_COPY)
    del inputs["temperature"], inputs["_metadata"]
    inputs.update({"browserbaseContextId": None, "_metadata": {"modelSource": "generated"}})
    fc.add_column(inputs, extra_ts={"authAccountId": "aa_9"})
    fc.client.sync_claygent_column(T, "f_1")
    names = [x["name"] for x in _patch_bindings(fc)]
    # create's order (declared params), then the column's own extras in their order
    assert names == ["useCase", "prompt", "model", "claygentId", "claygentFieldMapping", "answerSchemaType",
                     "temperature", "maxTokens", "browserbaseContextId", "_metadata"]
    b = fc.binding()
    assert b["_metadata"] == {"name": "_metadata", "formulaMap": {"modelSource": "generated"}}
    assert b["temperature"] == {"name": "temperature"} and b["maxTokens"] == {"name": "maxTokens"}
    assert b["answerSchemaType"]["formulaMap"] == FIELDS_COPY
    assert fc.ts()["authAccountId"] == "aa_9"


@pytest.mark.parametrize("bad", [
    {"{{company}}": "{{f_co}}", "{{notes}}": "Clay.formatForAIPrompt({{f_notes}})"},   # not wrapped
    {"company": "Clay.formatForAIPrompt({{f_co}})", "{{notes}}": "Clay.formatForAIPrompt({{f_notes}})"},  # bad key
])
def test_sync_unreadable_mapping_raises_before_writing(bad):
    fc = FakeClay()
    fc.add_column({**_expected_inputs(), "claygentFieldMapping": bad}, rerender=False)
    with pytest.raises(ValueError, match="^sync_claygent_column: cannot read the existing mapping for .*; pass var_map"):
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


@pytest.mark.parametrize("cid_binding", [None, {"name": "claygentId"}, {"name": "claygentId", "formulaText": "'c_1'"}])
def test_sync_unbound_column_raises_clear_error(cid_binding):
    fc = FakeClay()
    inputs = _expected_inputs()
    del inputs["claygentId"]
    fid = fc.add_column(inputs, rerender=False)
    if cid_binding is not None:
        fc.ts(fid)["inputsBinding"].append(cid_binding)
    with pytest.raises(ValueError, match="^sync_claygent_column: field f_1 is not bound to a saved Claygent$"):
        fc.client.sync_claygent_column(T, "f_1")
    assert not fc.writes()


def test_sync_unknown_field_raises_before_writing():
    fc = FakeClay()
    with pytest.raises(ValueError, match="^sync_claygent_column: field f_1 not found on t_1$"):
        fc.client.sync_claygent_column(T, "f_1")
    assert not fc.writes()


def test_sync_adds_missing_schema_binding():
    # The "Unable to parse the output schema" state: the column has no answerSchemaType entry.
    fc = FakeClay()
    inputs = _expected_inputs()
    del inputs["answerSchemaType"]
    fc.add_column(inputs)
    assert fc.client.verify_claygent_column(T, "f_1")["problems"] == [SCHEMA_PROBLEM]
    assert fc.client.sync_claygent_column(T, "f_1")["ok"] is True
    assert fc.binding()["answerSchemaType"] == {"name": "answerSchemaType",
                                                "formulaMap": _expected_inputs()["answerSchemaType"]}
    assert [x["name"] for x in fc.ts()["inputsBinding"]] == USE_AI_PARAMS  # in create's position


def test_sync_adds_owned_inputs_the_column_lacks():
    fc = FakeClay()
    fc.add_column({"claygentId": '"c_1"', "claygentFieldMapping": _expected_inputs()["claygentFieldMapping"]},
                  rerender=False)
    assert fc.client.sync_claygent_column(T, "f_1")["ok"] is True
    assert [x["name"] for x in fc.ts()["inputsBinding"]] == [p for p in USE_AI_PARAMS if p != "_metadata"]


def test_sync_binds_an_empty_owned_value_bare():
    # A Claygent with an empty prompt renders an empty prompt formula: bound bare, as create does.
    fc = FakeClay(claygents={"c_1": _claygent(prompt="", variables=None)})
    fc.add_column(_expected_inputs(prompt="", var_map={}))
    fc.client.sync_claygent_column(T, "f_1")
    assert {"name": "prompt"} in _patch_bindings(fc)


def test_sync_replaces_a_stale_copy_when_the_output_mode_changes():
    fc = FakeClay()
    fc.add_column(_expected_inputs())
    fc.claygents["c_1"]["currentVersion"]["outputFormat"] = copy.deepcopy(FIELDS_FMT)
    assert fc.client.verify_claygent_column(T, "f_1")["problems"] == [SCHEMA_PROBLEM]
    assert fc.client.sync_claygent_column(T, "f_1")["ok"] is True
    assert fc.binding()["answerSchemaType"]["formulaMap"] == FIELDS_COPY


@pytest.mark.parametrize("fmt", [{"type": "text"}, None])
def test_sync_refuses_an_unsupported_output_format_before_writing(fmt):
    # A switch to text (or a null format) is unmeasured: sync must neither keep the stale copy and
    # report ok, nor guess a new one.
    fc = FakeClay()
    fc.add_column(_expected_inputs())
    before = copy.deepcopy(fc.ts())
    fc.claygents["c_1"]["currentVersion"]["outputFormat"] = fmt
    with pytest.raises(ValueError, match="unsupported output format"):
        fc.client.sync_claygent_column(T, "f_1")
    assert not fc.writes() and fc.ts() == before


# ── unwrap_claygent_output ────────────────────────────────────────────────────

U = cc.ClayClient.unwrap_claygent_output
KEYS = ["score", "reason"]
META = {"stepsTaken": [], "totalInputTokens": 9, "totalOutputTokens": 50, "timeTakenInSeconds": "4.2",
        "totalCostToAIProvider": "$0.01", "forcedToFinishEarlyBecauseOfCost": False}


@pytest.mark.parametrize("value,expected", [
    ({"score": 7, "reason": "fit"}, {"score": 7, "reason": "fit"}),                      # top level
    ({"score": 7, "other": 1}, {"score": 7, "other": 1}),                                # partial top level
    ({"body": {"score": 7, "reason": "fit"}}, {"score": 7, "reason": "fit"}),            # one level down
    ({"parameters": {"score": 1, "reason": "x", "extra": 2}}, {"score": 1, "reason": "x", "extra": 2}),
    ({"body": {"score": 7}}, {"score": 7}),                                               # nested partial
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


def test_unwrap_prefers_the_object_holding_all_keys():
    # The top level holds one key, the wrapped answer both: the wrapped answer wins.
    assert U({"score": 1, "body": {"score": 2, "reason": "r"}}, KEYS) == {"score": 2, "reason": "r"}
    # Neither holds all: the one holding the most wins; a tie goes to the top level.
    v = {"score": 1, "body": {"score": 2, "reason": "r", "x": 0}}
    assert U(v, ["score", "reason", "summary"]) == {"score": 2, "reason": "r", "x": 0}
    assert U({"score": 1, "body": {"reason": "r"}}, ["score", "reason", "summary"]) == \
        {"score": 1, "body": {"reason": "r"}}


def test_unwrap_strips_clay_metadata_unless_requested():
    cell = {"score": 1, "reason": "r", "reasoning": "clay", "confidence": "high", **META}
    assert U(cell, KEYS) == {"score": 1, "reason": "r"}
    assert U(cell, ["score", "reason", "totalOutputTokens", "confidence"]) == \
        {"score": 1, "reason": "r", "confidence": "high", "totalOutputTokens": 50}
    assert set(cc.ClayClient.CLAYGENT_META_KEYS) == set(META) | {"reasoning", "confidence"}


def test_unwrap_metadata_named_schema_key_with_a_wrapped_answer():
    # A schema key named like Clay's metadata ("reasoning") must not make the top level look
    # like the answer when the real answer is wrapped.
    cell = {"body": {"score": 1, "reasoning": "mine"}, "reasoning": "clay-meta", **META}
    assert U(cell, ["score", "reasoning"]) == {"score": 1, "reasoning": "mine"}
    assert U(cell, ["score"]) == {"score": 1}


def test_unwrap_real_wrapped_shape():
    # UI-made: the answer arrived under "parameters"; Clay's keys sit at the top level, and the
    # wrapper also carries reasoning/confidence.
    cell = {"confidence": "high", "parameters": {"reasoning": "why", "confidence": "medium", "pitch": "p",
                                                 "angle": {"name": "a", "reasoning": "nested"}},
            **META}
    assert U(cell, ["angle", "pitch"]) == {"pitch": "p", "angle": {"name": "a", "reasoning": "nested"}}
    assert U(cell, ["angle", "confidence"]) == {"confidence": "medium", "pitch": "p",
                                                "angle": {"name": "a", "reasoning": "nested"}}


def test_unwrap_truncated_answer_is_returned_partial():
    # A cut-short answer comes back as it is: the missing required keys are what tells the caller
    # it was truncated. Clay's token count is kept only when it is requested.
    cell = {"score": 1, "reasoning": "clay", **META, "totalOutputTokens": 4096}
    out = U(cell, ["score", "reason", "summary"])
    assert out == {"score": 1} and [k for k in ("score", "reason", "summary") if k not in out] == ["reason", "summary"]
    assert U(cell, ["score", "reason", "totalOutputTokens"]) == {"score": 1, "totalOutputTokens": 4096}


def test_unwrap_only_metadata_names_requested():
    cell = {"reasoning": "clay", "score": 1, **META}
    assert U(cell, ["reasoning"]) == {"reasoning": "clay", "score": 1}
    assert U({"parameters": {"confidence": "low"}, "stepsTaken": []}, ["confidence"]) == {"confidence": "low"}


def test_unwrap_empty_keys_returns_the_top_level_without_metadata():
    assert U({"score": 1, **META}, []) == {"score": 1}


def test_unwrap_returns_a_new_dict_and_is_static():
    v = {"score": 1, "reason": "r"}
    assert U(v, KEYS) == v and U(v, KEYS) is not v
    nested = {"body": {"score": 1, "reason": "r"}}
    assert U(nested, KEYS) == nested["body"] and U(nested, KEYS) is not nested["body"]
    assert make_client(cc).unwrap_claygent_output(v, KEYS) == v


def test_use_ai_package_id_matches_create_action_column_docs():
    assert cc.ClayClient.USE_AI_PACKAGE_ID == USE_AI_PKG
    assert f"use-ai                            / {USE_AI_PKG}" in cc.ClayClient.create_action_column.__doc__


def test_u_escape_helper_itself():
    assert _has_u_escape(json.dumps("é")) and not _has_u_escape(json.dumps("é", ensure_ascii=False))
    assert not _has_u_escape(json.dumps(json.dumps("é")))  # escaped backslash + 'u' is not an escape
