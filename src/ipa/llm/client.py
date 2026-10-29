"""Provider-agnostic LLM client with timeouts, retries, a fallback model and a response cache."""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Generic, Protocol, TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy import insert, select
from sqlalchemy.engine import Engine

from ipa.db import llm_cache
from ipa.llm.prompts import SYSTEM_PROMPT, parse_json_loose, repair_prompt
from ipa.llm.providers import (
    LLMError,
    LLMTransportError,
    LLMValidationError,
    Provider,
    RetryableError,
)

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class ResponseCache(Protocol):
    def get(self, key: str) -> str | None: ...

    def set(self, key: str, provider: str, model: str, text: str) -> None: ...


class DbResponseCache:
    """Stores raw, already-validated responses in the llm_cache table (hash of prompt -> text)."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get(self, key: str) -> str | None:
        with self.engine.connect() as conn:
            return conn.execute(
                select(llm_cache.c.response_text).where(llm_cache.c.prompt_hash == key)
            ).scalar()

    def set(self, key: str, provider: str, model: str, text: str) -> None:
        with self.engine.begin() as conn:
            exists = conn.execute(
                select(llm_cache.c.prompt_hash).where(llm_cache.c.prompt_hash == key)
            ).first()
            if not exists:
                conn.execute(
                    insert(llm_cache).values(
                        prompt_hash=key,
                        provider=provider,
                        model=model,
                        response_text=text,
                        created_at=datetime.now(UTC).replace(tzinfo=None),
                    )
                )


@dataclass
class JsonResult(Generic[T]):
    value: T
    model: str
    attempts: int  # provider calls made (0 when served from the cache)
    cached: bool


class LLMClient:
    def __init__(
        self,
        provider: Provider,
        *,
        model: str = "",
        fallback_model: str = "",
        max_retries: int = 3,
        backoff_seconds: float = 1.0,
        cache: ResponseCache | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.provider = provider
        self.model = model or provider.default_model
        fb = fallback_model or provider.default_fallback_model
        self.fallback_model = fb if fb and fb != self.model else ""
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.cache = cache
        self._sleep = sleep

    def cache_key(self, system: str, prompt: str) -> str:
        raw = "\x1f".join([self.provider.name, self.model, system, prompt])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _call_with_backoff(self, model: str, system: str, prompt: str) -> tuple[str, int]:
        """One logical call: retries timeouts/429/5xx with exponential backoff."""
        for retry in range(self.max_retries + 1):
            try:
                return self.provider.complete(system, prompt, model), retry + 1
            except RetryableError as exc:
                if retry == self.max_retries:
                    raise LLMTransportError(
                        f"{self.provider.name}/{model} failed after {retry + 1} tries: {exc}"
                    ) from exc
                delay = self.backoff_seconds * (2**retry)
                log.warning(
                    "%s; retrying in %.1fs (%d/%d)", exc, delay, retry + 1, self.max_retries
                )
                self._sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    def complete_json(
        self, prompt: str, schema: type[T], system: str = SYSTEM_PROMPT
    ) -> JsonResult[T]:
        key = self.cache_key(system, prompt)
        if self.cache:
            hit = self.cache.get(key)
            if hit is not None:
                try:
                    return JsonResult(
                        schema.model_validate(parse_json_loose(hit)), self.model, 0, True
                    )
                except (ValueError, ValidationError):
                    log.warning("Ignoring unusable cache entry %s", key[:8])

        models = [self.model] + ([self.fallback_model] if self.fallback_model else [])
        attempts = 0
        last_problem = ""
        saw_bad_json = False
        for model in models:
            current = prompt
            for json_try in range(self.max_retries + 1):
                try:
                    text, calls = self._call_with_backoff(model, system, current)
                except LLMTransportError as exc:
                    attempts += self.max_retries + 1
                    last_problem = str(exc)
                    log.error("%s", exc)
                    break  # try the fallback model
                except LLMError as exc:
                    attempts += 1
                    last_problem = str(exc)
                    log.error("%s", exc)
                    break
                attempts += calls
                try:
                    value = schema.model_validate(parse_json_loose(text))
                except (ValueError, ValidationError) as exc:
                    saw_bad_json = True
                    last_problem = _short(exc)
                    log.warning(
                        "Invalid JSON from %s/%s (try %d): %s",
                        self.provider.name, model, json_try + 1, last_problem,
                    )  # fmt: skip
                    current = repair_prompt(prompt, last_problem)
                    continue
                if self.cache:
                    self.cache.set(key, self.provider.name, model, text)
                return JsonResult(value, model, attempts, False)
            if models.index(model) + 1 < len(models):
                log.warning("Switching to fallback model %s", self.fallback_model)

        error: LLMError
        if saw_bad_json:
            error = LLMValidationError(
                f"{self.provider.name} did not return valid JSON after {attempts} attempts "
                f"(last problem: {last_problem})"
            )
        else:
            error = LLMTransportError(f"{self.provider.name} request failed: {last_problem}")
        error.attempts = attempts
        raise error


def _short(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first["loc"])
        return f"{loc}: {first['msg']}"
    return str(exc)
