"""Quota-aware HTTP client for OpenRouter chat completions.

Tries each configured model in order until one returns a usable response.
Mutates the supplied QuotaState in place so callers can persist usage after
a wake cycle. Raises QuotaExhausted when no calls remain locally or when
every model returns HTTP 429.
"""

from __future__ import annotations

import logging

import httpx

from src.memory import QuotaState


logger = logging.getLogger(__name__)


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_TIMEOUT_SECONDS = 30.0


class QuotaExhausted(Exception):
    """Raised when the local quota counter is spent or the server returns 429."""


class OpenRouterClient:
    """Wraps OpenRouter chat completions with a local quota counter and model fallback."""

    def __init__(
        self,
        api_key: str,
        models: list[str],
        quota_state: QuotaState,
    ) -> None:
        self.api_key = api_key
        self.models = list(models)
        self.quota_state = quota_state

    def complete(self, prompt: str, max_tokens: int = 1000) -> str:
        """Run a chat completion against the configured models in order.

        Tries every configured model in turn, moving to the next on ANY
        failure: a transport error (timeout, connection drop), HTTP 429, any
        other non-200 status, or a 200 with an empty/unparseable body. The
        first model that returns usable text wins. Only when every model has
        failed does this raise QuotaExhausted, with a message summarising the
        real per-model failures so the private log shows true diagnostics.

        Decrements quota_state.calls_made before each attempt. Raises
        QuotaExhausted immediately if the local counter is already spent.
        """
        if not self.models:
            raise QuotaExhausted("no models configured")

        # The daily budget counts one unit per logical call (one wake's
        # thinking), NOT per model attempt. Trying several fallback models
        # within a single call must not multiply the count, otherwise one
        # failing wake that walks the whole fallback list would exhaust the
        # daily budget by itself.
        if self.quota_state.calls_made >= self.quota_state.calls_limit:
            raise QuotaExhausted(
                "local quota counter exhausted (daily call limit reached)"
            )
        self.quota_state.calls_made += 1

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # One human-readable diagnostic per attempted model, e.g.
        # "meta-llama/...:free -> HTTP 429". Surfaced on total failure.
        failures: list[str] = []

        for model in self.models:
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
            }

            try:
                response = httpx.post(
                    OPENROUTER_URL,
                    headers=headers,
                    json=payload,
                    timeout=DEFAULT_TIMEOUT_SECONDS,
                )
            except httpx.HTTPError as exc:
                reason = f"transport error: {type(exc).__name__}: {exc}"
                failures.append(f"{model} -> {reason}")
                logger.warning("openrouter model %s failed: %s", model, reason)
                continue

            if response.status_code != 200:
                reason = f"HTTP {response.status_code}"
                body = _error_snippet(response)
                if body:
                    reason = f"{reason}: {body}"
                failures.append(f"{model} -> {reason}")
                logger.warning("openrouter model %s failed: %s", model, reason)
                continue

            text = _extract_text(response)
            if text:
                if failures:
                    logger.info(
                        "openrouter model %s succeeded after %d failure(s)",
                        model,
                        len(failures),
                    )
                return text

            reason = "empty or unparseable response body"
            failures.append(f"{model} -> {reason}")
            logger.warning("openrouter model %s failed: %s", model, reason)

        summary = "; ".join(failures) if failures else "no models attempted"
        raise QuotaExhausted(f"all models failed: {summary}")


def _error_snippet(response: httpx.Response, max_len: int = 200) -> str:
    """Best-effort short description of a non-200 body for diagnostics.

    Prefers OpenRouter's JSON {"error": {"message": ...}} shape, falls back
    to raw text. Always returns a trimmed, length-capped string and never
    raises, so it is safe to call on any failed response.
    """
    try:
        data = response.json()
    except ValueError:
        snippet = (response.text or "").strip()
        return snippet[:max_len]

    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            message = err.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()[:max_len]
        if isinstance(err, str) and err.strip():
            return err.strip()[:max_len]
    return str(data)[:max_len]


def _extract_text(response: httpx.Response) -> str:
    """Pull the assistant message text out of an OpenRouter response body."""
    try:
        data = response.json()
    except ValueError:
        return ""

    choices = data.get("choices") or []
    if not choices:
        return ""

    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    return ""
