"""HTTP providers: Anthropic (default), OpenAI, Gemini and OpenAI-compatible servers (Ollama).

Every provider has the same tiny interface: complete(system, prompt, model) -> text.
Network problems that are worth retrying raise RetryableError; everything else raises LLMError.
API keys are sent in headers, never put in URLs, and never logged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from ipa.config import Settings


class LLMError(Exception):
    """Base class for LLM problems."""

    attempts: int = 0  # provider calls made before giving up


class LLMConfigError(LLMError):
    """Missing key or unknown provider."""


class RetryableError(LLMError):
    """Timeouts, connection errors, HTTP 429 and 5xx."""


class LLMTransportError(LLMError):
    """The provider could not be reached after all retries."""


class LLMValidationError(LLMError):
    """The model kept returning JSON that did not match the schema."""


class Provider(Protocol):
    name: str
    default_model: str
    default_fallback_model: str

    def complete(self, system: str, prompt: str, model: str) -> str: ...


def _post(
    http: httpx.Client, provider: str, url: str, headers: dict[str, str], body: dict[str, Any]
) -> dict[str, Any]:
    try:
        resp = http.post(url, headers=headers, json=body)
    except httpx.TimeoutException as exc:
        raise RetryableError(f"{provider}: request timed out") from exc
    except httpx.TransportError as exc:
        raise RetryableError(f"{provider}: connection problem ({type(exc).__name__})") from exc
    if resp.status_code == 429 or resp.status_code >= 500:
        raise RetryableError(f"{provider}: HTTP {resp.status_code}")
    if resp.status_code >= 400:
        raise LLMError(f"{provider}: HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise LLMError(f"{provider}: response was not JSON") from exc
    if not isinstance(data, dict):
        raise LLMError(f"{provider}: unexpected response shape")
    return data


@dataclass
class AnthropicProvider:
    api_key: str
    http: httpx.Client
    base_url: str = "https://api.anthropic.com"
    name: str = "anthropic"
    default_model: str = "claude-sonnet-5-5"
    default_fallback_model: str = "claude-haiku-5-5"

    def complete(self, system: str, prompt: str, model: str) -> str:
        data = _post(
            self.http,
            self.name,
            f"{self.base_url}/v1/messages",
            {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            {
                "model": model,
                "max_tokens": 2000,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
        if not parts:
            raise LLMError("anthropic: no text in response")
        return "".join(parts)


@dataclass
class OpenAIProvider:
    http: httpx.Client
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    name: str = "openai"
    default_model: str = "gpt-4o-mini"
    default_fallback_model: str = "gpt-4o"

    def complete(self, system: str, prompt: str, model: str) -> str:
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        data = _post(
            self.http,
            self.name,
            f"{self.base_url.rstrip('/')}/chat/completions",
            headers,
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            },
        )
        try:
            return str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"{self.name}: no message in response") from exc


@dataclass
class OllamaProvider(OpenAIProvider):
    """Any OpenAI-compatible local server (Ollama, LM Studio, vLLM). No API key needed."""

    name: str = "ollama"
    default_model: str = "llama3.1"
    default_fallback_model: str = ""


@dataclass
class GeminiProvider:
    api_key: str
    http: httpx.Client
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    name: str = "gemini"
    default_model: str = "gemini-2.5-flash"
    default_fallback_model: str = "gemini-2.5-flash-lite"

    def complete(self, system: str, prompt: str, model: str) -> str:
        data = _post(
            self.http,
            self.name,
            f"{self.base_url}/models/{model}:generateContent",
            {"x-goog-api-key": self.api_key, "content-type": "application/json"},
            {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            },
        )
        try:
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("gemini: no text in response") from exc


def build_provider(name: str, settings: Settings, http: httpx.Client | None = None) -> Provider:
    """Create a provider by name. `fake` gives the deterministic offline LLM."""
    name = name.lower()
    if name == "fake":
        from ipa.llm.fake import FakeProvider

        return FakeProvider()
    client = http or httpx.Client(timeout=settings.llm_timeout_seconds)
    keys = {
        "anthropic": ("ANTHROPIC_API_KEY", settings.anthropic_api_key),
        "openai": ("OPENAI_API_KEY", settings.openai_api_key),
        "gemini": ("GEMINI_API_KEY", settings.gemini_api_key),
    }
    if name in keys:
        env_name, key = keys[name]
        if not key:
            raise LLMConfigError(
                f"{env_name} is not set. Add it to your .env file (see .env.example), "
                "or run with --provider fake for an offline demo."
            )
        if name == "anthropic":
            return AnthropicProvider(api_key=key, http=client)
        if name == "openai":
            return OpenAIProvider(api_key=key, http=client)
        return GeminiProvider(api_key=key, http=client)
    if name == "ollama":
        return OllamaProvider(http=client, base_url=settings.ollama_base_url)
    raise LLMConfigError(
        f"Unknown provider '{name}'. Use anthropic, openai, gemini, ollama or fake."
    )
