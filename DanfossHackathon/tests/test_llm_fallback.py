"""A 400 from the API is explained and, where it is about the answer format or effort, retried without it."""
import types

import anthropic
import httpx
import pytest

from comparison_engine import llm


def _bad(msg):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    body = {"type": "error", "error": {"type": "invalid_request_error", "message": msg}}
    return anthropic.BadRequestError(msg, response=httpx.Response(400, request=req, json=body), body=body)


class FakeClient:
    def __init__(self, reject):
        self.calls = []
        self.reject = reject
        self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(create=self._beta))
        self.messages = types.SimpleNamespace(create=self._create)

    def _beta(self, **kw):
        raise _bad("fallbacks: unknown parameter")

    def _create(self, **req):
        self.calls.append(req)
        msg = self.reject(req)
        if msg:
            raise _bad(msg)
        return types.SimpleNamespace(usage=None, stop_reason="end_turn", model=req["model"],
                                     content=[types.SimpleNamespace(type="text", text='{"ok": true}')])


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    holder = {}
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: holder["c"])
    return holder


def test_schema_too_complex_is_retried_with_the_schema_in_the_prompt(client):
    client["c"] = FakeClient(lambda req: "The compiled grammar is too large"
                             if "format" in req.get("output_config", {}) else None)
    assert llm.ask_claude("sys", "question", {"type": "object"}) == '{"ok": true}'
    last = client["c"].calls[-1]
    assert "format" not in last.get("output_config", {}) and "JSON Schema" in last["messages"][0]["content"]


def test_other_400_shows_the_api_reason(client):
    client["c"] = FakeClient(lambda req: "max_tokens: too large for this model")
    with pytest.raises(llm.LLMUnavailable, match="API error 400: max_tokens: too large"):
        llm.ask_claude("sys", "question")
