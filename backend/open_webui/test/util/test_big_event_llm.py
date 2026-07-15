import json

import pytest

from open_webui.utils.big_event_llm import (
    DEFAULT_BIG_EVENTS_LLM_MODEL,
    CLASSIFIER_TOOL_NAME,
    classify_events,
    classifier_payload,
    parse_classifier_response,
)


def test_default_classifier_model_is_gpt_oss_120b():
    assert DEFAULT_BIG_EVENTS_LLM_MODEL == "openai/gpt-oss-120b"


def _response(suitable):
    return {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": CLASSIFIER_TOOL_NAME,
                                "arguments": json.dumps({"suitable": suitable}),
                            },
                        }
                    ]
                }
            }
        ]
    }


def test_classifier_payload_forces_one_strict_boolean_function_call():
    payload = classifier_payload(
        "vllm-model", {"title": "Sydney CEO Forum", "description": "For CEOs"}
    )

    function = payload["tools"][0]["function"]
    assert payload["model"] == "vllm-model"
    assert payload["tool_choice"]["function"]["name"] == CLASSIFIER_TOOL_NAME
    assert payload["parallel_tool_calls"] is False
    assert function["strict"] is True
    assert function["parameters"]["properties"] == {
        "suitable": {
            "type": "boolean",
            "description": "True only when the primary audience includes senior decision-makers.",
        }
    }
    assert function["parameters"]["additionalProperties"] is False


@pytest.mark.parametrize("value", [True, False])
def test_classifier_response_accepts_only_boolean_tool_argument(value):
    assert parse_classifier_response(_response(value)) is value


@pytest.mark.parametrize("value", ["true", 1, None])
def test_classifier_response_rejects_non_boolean_tool_argument(value):
    with pytest.raises(ValueError, match="not boolean"):
        parse_classifier_response(_response(value))


def test_classifier_response_rejects_plain_text():
    with pytest.raises(ValueError, match="did not return any tool calls"):
        parse_classifier_response(
            {"choices": [{"message": {"content": "true", "tool_calls": []}}]}
        )


def test_classifier_response_accepts_identical_duplicate_tool_calls():
    response = _response(True)
    response["choices"][0]["message"]["tool_calls"] *= 2

    assert parse_classifier_response(response) is True


def test_classifier_response_rejects_conflicting_duplicate_tool_calls():
    response = _response(True)
    response["choices"][0]["message"]["tool_calls"].extend(
        _response(False)["choices"][0]["message"]["tool_calls"]
    )

    with pytest.raises(ValueError, match="conflicting duplicate decisions"):
        parse_classifier_response(response)


@pytest.mark.asyncio
async def test_every_candidate_is_sent_to_llm_without_keyword_filtering():
    events = [
        {"id": "one", "title": "CEO Forum"},
        {"id": "two", "title": "Ambiguous Business Gathering"},
        {"id": "three", "title": "Community Picnic"},
    ]
    decisions = iter((True, True, False))
    calls = []

    async def completion_fn(_request, form_data, user):
        calls.append(form_data)
        return _response(next(decisions))

    selected = await classify_events(
        request=None,
        user=object(),
        events=events,
        completion_fn=completion_fn,
        model_id="vllm-model",
    )

    assert len(calls) == len(events)
    assert [event["id"] for event in selected] == ["one", "two"]


@pytest.mark.asyncio
async def test_classifier_retries_a_missing_tool_call():
    event = {"id": "one", "title": "CEO Forum"}
    responses = iter(
        (
            {"choices": [{"message": {"content": "true", "tool_calls": []}}]},
            _response(True),
        )
    )
    calls = 0

    async def completion_fn(_request, form_data, user):
        nonlocal calls
        calls += 1
        return next(responses)

    selected = await classify_events(
        request=None,
        user=None,
        events=[event],
        completion_fn=completion_fn,
        model_id="vllm-model",
    )

    assert calls == 2
    assert selected == [event]
