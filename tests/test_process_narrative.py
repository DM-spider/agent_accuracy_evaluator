import json

from evaluator.process_narrative import (
    build_messages,
    extract_probes,
    narrate,
    needs_narrative,
    validate_narrative,
)


class FakeLlm:
    configured = True

    def __init__(self, data=None, error=None):
        self.data = data
        self.error = error
        self.calls = 0

    def complete_json(self, messages, max_tokens=None):
        self.calls += 1
        if self.error:
            raise self.error
        return self.data


def _process():
    return {
        "summary": {"counts": {"tool_calls": 2}},
        "timeline": [
            {"kind": "thought", "t_ms": 1000, "text": "先试一下接口"},
            {
                "kind": "step",
                "t_ms": 14300,
                "step": {
                    "status": "empty",
                    "rows": 0,
                    "excerpt": 'retry basic -> { "respCode": "1002" } bz_bm futian -> totalRows: 4634',
                },
            },
        ],
    }


def test_extract_probes_keeps_every_probe():
    probes = extract_probes('retry basic -> { "respCode": "1002" } bz_bm futian -> totalRows: 4634')
    assert probes == [
        {"label": "retry basic", "value": "1002", "kind": "code"},
        {"label": "bz_bm futian", "value": "4634", "kind": "rows"},
    ]


def test_validate_builds_line_with_program_results():
    items = [
        {"kind": "调用", "t": 14.3, "status": "empty", "rows": 0,
         "probes": [{"label": "basic", "value": "1002", "kind": "code"},
                    {"label": "futian", "value": "4634", "kind": "rows"}]}
    ]
    result = validate_narrative({"walk": [{"t": 14.3, "action": "试了两种参数"}]}, items)
    assert result["walk"] == ["14.3 秒：试了两种参数 → basic 错误码 1002、futian 4634 行"]


def test_unmatched_time_is_dropped():
    items = [{"kind": "调用", "t": 14.3, "status": "ok", "rows": 3, "probes": []}]
    assert validate_narrative({"walk": [{"t": 99.0, "action": "无关动作"}]}, items) is None


def test_narrate_uses_model_actions_and_local_results():
    llm = FakeLlm({"walk": [{"t": 14.3, "action": "先试接口"}]})
    result = narrate(llm, _process(), "问题")
    assert result["walk"] == [
        "14.3 秒：先试接口 → retry basic 错误码 1002、bz_bm futian 4634 行"
    ]
    assert llm.calls == 1


def test_narrate_returns_none_on_model_failure():
    llm = FakeLlm(error=RuntimeError("boom"))
    assert narrate(llm, _process()) is None


def test_narrate_skips_when_not_configured():
    class Disabled:
        configured = False

    assert narrate(Disabled(), _process()) is None


def test_needs_narrative_for_multi_probe_calls():
    process = {
        "review": {"findings": [], "observations": []},
        "steps": [
            {"excerpt": "a -> totalRows: 0 | b -> totalRows: 4634"},
            {"excerpt": "c -> totalRows: 5"},
        ],
    }
    assert needs_narrative(process) is True
    simple = {"review": {}, "steps": [{"excerpt": "a -> totalRows: 5"}, {"excerpt": "b -> totalRows: 6"}]}
    assert needs_narrative(simple) is False


def test_build_messages_carries_probes_not_raw_numbers():
    messages = build_messages(_process(), "今年以来工单数")
    payload = json.loads(messages[1]["content"])
    assert payload["question"] == "今年以来工单数"
    step = [item for item in payload["timeline"] if item.get("kind") == "调用"][0]
    assert step["probes"][1] == {"label": "bz_bm futian", "value": "4634", "kind": "rows"}
