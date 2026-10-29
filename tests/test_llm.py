from __future__ import annotations

import json

import httpx
import pytest
from pydantic import BaseModel, Field

from ipa.config import Settings
from ipa.llm.client import DbResponseCache, LLMClient
from ipa.llm.fake import FakeProvider, score_answer_text
from ipa.llm.prompts import (
    REPAIR_MARKER,
    parse_json_loose,
    questions_prompt,
    repair_prompt,
    score_prompt,
)
from ipa.llm.providers import (
    AnthropicProvider,
    GeminiProvider,
    LLMConfigError,
    LLMError,
    LLMTransportError,
    LLMValidationError,
    OllamaProvider,
    OpenAIProvider,
    RetryableError,
    build_provider,
)
from ipa.schemas import QuestionList, ScoreResult
from ipa.speech import SpeechMetrics

GOOD = {
    "score": 7,
    "strengths": ["clear"],
    "improvements": ["more numbers"],
    "improved_answer": "x",
}


class Tiny(BaseModel):
    score: int = Field(ge=1, le=10)


class ScriptedProvider:
    """Returns scripted replies (strings) or raises scripted exceptions, in order."""

    name = "scripted"
    default_model = "m1"
    default_fallback_model = "m2"

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system, prompt, model):
        self.calls.append((model, prompt))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def client_for(provider, **kw) -> tuple[LLMClient, list[float]]:
    sleeps: list[float] = []
    kw.setdefault("max_retries", 2)
    return LLMClient(provider, sleep=sleeps.append, **kw), sleeps


# ---------- JSON repair ----------


def test_parse_plain_fenced_and_prose_wrapped_json():
    assert parse_json_loose('{"a": 1}') == {"a": 1}
    assert parse_json_loose('Here you go:\n```json\n{"a": 2}\n```\nBye') == {"a": 2}
    assert parse_json_loose('Sure! {"a": 3} hope that helps') == {"a": 3}


def test_parse_repairs_trailing_commas():
    assert parse_json_loose('{"a": [1, 2,], "b": 1,}') == {"a": [1, 2], "b": 1}


@pytest.mark.parametrize("text", ["no json here", "{unclosed", '{"a": }', ""])
def test_parse_rejects_garbage(text):
    with pytest.raises(ValueError):
        parse_json_loose(text)


# ---------- retry / validation logic ----------


def test_valid_reply_needs_one_attempt():
    client, _ = client_for(ScriptedProvider([json.dumps(GOOD)]))
    res = client.complete_json("p", ScoreResult)
    assert (res.value.score, res.attempts, res.cached, res.model) == (7, 1, False, "m1")


def test_invalid_json_is_retried_with_a_repair_hint():
    prov = ScriptedProvider(["not json", '{"score": 99}', json.dumps({"score": 4})])
    client, _ = client_for(prov)
    res = client.complete_json("question?", Tiny)
    assert res.value.score == 4 and res.attempts == 3
    assert REPAIR_MARKER not in prov.calls[0][1]
    assert REPAIR_MARKER in prov.calls[1][1] and "question?" in prov.calls[1][1]


def test_clear_error_when_json_stays_invalid():
    prov = ScriptedProvider(["nope"] * 20)
    client, _ = client_for(prov, max_retries=1)
    with pytest.raises(LLMValidationError) as err:
        client.complete_json("p", Tiny)
    assert "did not return valid JSON after 4 attempts" in str(err.value)
    assert err.value.attempts == 4
    assert [m for m, _ in prov.calls] == ["m1", "m1", "m2", "m2"]  # fallback model was tried


def test_fallback_model_succeeds_when_primary_returns_garbage():
    prov = ScriptedProvider(["bad", "bad", json.dumps(GOOD)])
    client, _ = client_for(prov, max_retries=1)
    res = client.complete_json("p", ScoreResult)
    assert res.model == "m2" and res.attempts == 3


def test_exponential_backoff_between_transport_retries():
    prov = ScriptedProvider(
        [RetryableError("timeout"), RetryableError("429"), RetryableError("500"), '{"score": 3}']
    )
    client, sleeps = client_for(prov, max_retries=3, backoff_seconds=0.5)
    assert client.complete_json("p", Tiny).value.score == 3
    assert sleeps == [0.5, 1.0, 2.0]


def test_transport_failure_switches_to_fallback_then_raises():
    prov = ScriptedProvider([RetryableError("down")] * 2 + ['{"score": 5}'])
    client, _ = client_for(prov, max_retries=1)
    res = client.complete_json("p", Tiny)
    assert res.model == "m2"

    prov = ScriptedProvider([RetryableError("down")] * 4)
    client, _ = client_for(prov, max_retries=1)
    with pytest.raises(LLMTransportError, match="request failed"):
        client.complete_json("p", Tiny)


def test_non_retryable_error_goes_straight_to_fallback():
    prov = ScriptedProvider([LLMError("HTTP 404 model not found"), '{"score": 2}'])
    client, sleeps = client_for(prov)
    assert client.complete_json("p", Tiny).model == "m2"
    assert sleeps == []


def test_no_fallback_when_same_as_primary():
    client, _ = client_for(ScriptedProvider([]), model="m2")
    assert client.fallback_model == ""


# ---------- response cache ----------


def test_cache_hit_skips_the_provider(engine):
    prov = ScriptedProvider([json.dumps(GOOD)])
    client, _ = client_for(prov, cache=DbResponseCache(engine))
    first = client.complete_json("same prompt", ScoreResult)
    second = client.complete_json("same prompt", ScoreResult)
    assert (first.cached, second.cached) == (False, True)
    assert second.attempts == 0 and len(prov.calls) == 1
    assert second.value == first.value


def test_invalid_answers_are_not_cached_and_bad_entries_are_ignored(engine):
    cache = DbResponseCache(engine)
    prov = ScriptedProvider(["bad", json.dumps(GOOD)])
    client, _ = client_for(prov, max_retries=0, cache=cache, fallback_model="")
    client.fallback_model = ""
    with pytest.raises(LLMValidationError):
        client.complete_json("p", ScoreResult)
    assert cache.get(client.cache_key(client_system(), "p")) is None
    # a corrupt cache entry is ignored and the provider is asked again
    cache.set(client.cache_key(client_system(), "p"), "scripted", "m1", "corrupt")
    assert client.complete_json("p", ScoreResult).cached is False


def client_system() -> str:
    from ipa.llm.prompts import SYSTEM_PROMPT

    return SYSTEM_PROMPT


def test_cache_key_depends_on_provider_model_and_prompt():
    a, _ = client_for(ScriptedProvider([]), model="m1")
    b, _ = client_for(ScriptedProvider([]), model="other")
    assert a.cache_key("s", "p") != b.cache_key("s", "p")
    assert a.cache_key("s", "p") != a.cache_key("s", "q")


# ---------- prompts ----------


def test_prompts_contain_task_and_answer_markers():
    m = SpeechMetrics(words=10, duration_sec=5, words_per_minute=120, filler_count=0)
    p = score_prompt("Why?", "SQL", "Because indexes.", m)
    assert "TASK: score_answer" in p and "Because indexes." in p and "120 words per minute" in p
    q = questions_prompt("x" * 9000, 4)
    assert "COUNT: 4" in q and "[...truncated]" in q
    assert REPAIR_MARKER in repair_prompt("orig", "bad json")


# ---------- fake LLM ----------


def test_fake_is_deterministic_and_never_calls_network():
    fake = FakeProvider()
    m = SpeechMetrics(words=10, duration_sec=5, words_per_minute=120, filler_count=0)
    prompt = score_prompt("Why?", "SQL", "Because indexes help, for example 10x.", m)
    assert fake.complete("", prompt, "x") == fake.complete("", prompt, "x")
    assert fake.complete("", "unknown task", "x") == '{"error": "unknown task"}'


def test_fake_scores_follow_answer_quality():
    weak = score_answer_text("Um, so basically I would, like, just use it, uh, you know.")
    strong = score_answer_text(
        "I would read the plan first, because that shows the slow step. For example, an index "
        "cut a 14 minute report to 40 seconds, and I checked statistics and locking as well. "
        "The trade-off is slower inserts, so I measure before adding more indexes."
    )
    assert weak["score"] < strong["score"]
    assert ScoreResult.model_validate(weak) and ScoreResult.model_validate(strong)


def test_fake_generates_valid_questions_for_every_category():
    out = FakeProvider().complete("", questions_prompt("We use Spark and SQL.", 12), "x")
    parsed = QuestionList.model_validate(json.loads(out))
    assert len(parsed.questions) == 12
    assert parsed.questions[0].category in {"Spark", "SQL"}  # job-description categories first


# ---------- HTTP providers (mock transport, no network) ----------


def http_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_anthropic_request_and_response_shape():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["headers"] = str(request.url), request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "hello"}]})

    p = AnthropicProvider(api_key="sk-test", http=http_client(handler))
    assert p.complete("sys", "hi", "claude-x") == "hello"
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == "sk-test"
    assert seen["headers"]["anthropic-version"] == "2023-06-01"
    assert seen["body"]["system"] == "sys" and seen["body"]["messages"][0]["content"] == "hi"


def test_openai_and_ollama_use_chat_completions():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), request.headers.get("authorization")))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    assert OpenAIProvider(api_key="k", http=http_client(handler)).complete("s", "p", "m") == "ok"
    local = OllamaProvider(http=http_client(handler), base_url="http://localhost:11434/v1")
    assert local.complete("s", "p", "llama") == "ok"
    assert seen[0] == ("https://api.openai.com/v1/chat/completions", "Bearer k")
    assert seen[1] == ("http://localhost:11434/v1/chat/completions", None)


def test_gemini_key_goes_in_a_header_not_the_url():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["key"] = str(request.url), request.headers.get("x-goog-api-key")
        return httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "a"}, {"text": "b"}]}}]}
        )

    assert (
        GeminiProvider(api_key="g-key", http=http_client(handler)).complete("s", "p", "gm") == "ab"
    )
    assert "g-key" not in seen["url"] and seen["key"] == "g-key"
    assert seen["url"].endswith("/models/gm:generateContent")


@pytest.mark.parametrize("status", [429, 500, 503])
def test_retryable_http_statuses(status):
    p = OpenAIProvider(api_key="k", http=http_client(lambda r: httpx.Response(status)))
    with pytest.raises(RetryableError):
        p.complete("s", "p", "m")


def test_client_errors_are_not_retryable_and_hide_nothing_secret():
    p = OpenAIProvider(api_key="k", http=http_client(lambda r: httpx.Response(401, text="bad key")))
    with pytest.raises(LLMError) as err:
        p.complete("s", "p", "m")
    assert not isinstance(err.value, RetryableError) and "401" in str(err.value)


def test_timeouts_and_connection_errors_are_retryable():
    def boom(request):
        raise httpx.ConnectTimeout("slow")

    def refused(request):
        raise httpx.ConnectError("refused")

    for handler in (boom, refused):
        with pytest.raises(RetryableError):
            OpenAIProvider(api_key="k", http=http_client(handler)).complete("s", "p", "m")


@pytest.mark.parametrize(
    "provider_cls,payload",
    [
        (AnthropicProvider, {"content": []}),
        (OpenAIProvider, {"choices": []}),
        (GeminiProvider, {"candidates": []}),
    ],
)
def test_unexpected_response_shapes(provider_cls, payload):
    p = provider_cls(api_key="k", http=http_client(lambda r: httpx.Response(200, json=payload)))
    with pytest.raises(LLMError):
        p.complete("s", "p", "m")


def test_non_json_response_body():
    p = OpenAIProvider(api_key="k", http=http_client(lambda r: httpx.Response(200, text="<html>")))
    with pytest.raises(LLMError, match="not JSON"):
        p.complete("s", "p", "m")
    p = OpenAIProvider(api_key="k", http=http_client(lambda r: httpx.Response(200, json=[1])))
    with pytest.raises(LLMError, match="unexpected"):
        p.complete("s", "p", "m")


# ---------- provider factory ----------


def test_build_provider_names_and_errors():
    s = Settings(anthropic_api_key="a", openai_api_key="o", gemini_api_key="g")
    assert build_provider("anthropic", s).name == "anthropic"
    assert build_provider("openai", s).name == "openai"
    assert build_provider("gemini", s).name == "gemini"
    assert build_provider("ollama", s).name == "ollama"
    assert build_provider("fake", s).name == "fake"
    with pytest.raises(LLMConfigError, match="ANTHROPIC_API_KEY"):
        build_provider("anthropic", Settings())
    with pytest.raises(LLMConfigError, match="Unknown provider"):
        build_provider("skynet", s)
