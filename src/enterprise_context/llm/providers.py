"""LLM provider abstraction.

The LLM is used for flexible language tasks only (intent fallback, NL-to-SPARQL
generation, phrasing). It is never an authority for access, policy, entity merges
or transaction validity, and every output is validated before use.

Providers:
- ``mock``: deterministic, offline; the default and the only provider used in tests.
- ``openai_compatible``: any ``/chat/completions`` server, including open-source
  Ollama, vLLM and llama.cpp servers.
- ``bedrock``: optional AWS adapter (requires ``boto3``); not exercised by tests.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, ValidationError

from enterprise_context.config import Settings
from enterprise_context.observability.metrics import LLM_TOKENS
from enterprise_context.observability.tracing import observed_store_call


class LLMError(RuntimeError):
    """Raised when a provider call fails or returns unusable output."""


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class LLMResponse(BaseModel):
    text: str
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMProvider(Protocol):
    name: str
    model: str

    def complete(
        self, messages: Sequence[ChatMessage], *, max_tokens: int = 512, json_mode: bool = False
    ) -> LLMResponse: ...


def _record_usage(response: LLMResponse) -> LLMResponse:
    LLM_TOKENS.labels(provider=response.provider, kind="prompt").inc(response.prompt_tokens)
    LLM_TOKENS.labels(provider=response.provider, kind="completion").inc(
        response.completion_tokens
    )
    return response


class MockLLM:
    """Deterministic provider: the first rule whose pattern matches the last user
    message wins; otherwise the default response is returned."""

    name = "mock"

    def __init__(
        self,
        rules: Sequence[tuple[str, str]] = (),
        *,
        default: str = "{}",
        model: str = "mock-deterministic-v1",
    ) -> None:
        self._rules = [(re.compile(pattern, re.I | re.S), output) for pattern, output in rules]
        self._default = default
        self.model = model

    def complete(
        self, messages: Sequence[ChatMessage], *, max_tokens: int = 512, json_mode: bool = False
    ) -> LLMResponse:
        del max_tokens, json_mode
        prompt = next((m.content for m in reversed(messages) if m.role == "user"), "")
        text = next((out for pattern, out in self._rules if pattern.search(prompt)), self._default)
        with observed_store_call("llm", "complete", **{"ecg.provider": self.name}):
            return _record_usage(
                LLMResponse(
                    text=text,
                    provider=self.name,
                    model=self.model,
                    prompt_tokens=sum(len(m.content.split()) for m in messages),
                    completion_tokens=len(text.split()),
                )
            )


class OpenAICompatibleLLM:
    name = "openai_compatible"

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str | None = None,
        timeout_seconds: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._client = client

    def complete(
        self, messages: Sequence[ChatMessage], *, max_tokens: int = 512, json_mode: bool = False
    ) -> LLMResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [message.model_dump() for message in messages],
            "max_tokens": max_tokens,
            "temperature": 0,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        owns_client = self._client is None
        client = self._client or httpx.Client(timeout=self._timeout_seconds)
        try:
            with observed_store_call("llm", "complete", **{"ecg.provider": self.name}):
                response = client.post(
                    f"{self._base_url}/chat/completions", json=body, headers=headers
                )
                response.raise_for_status()
                payload = response.json()
                usage = payload.get("usage") or {}
                return _record_usage(
                    LLMResponse(
                        text=str(payload["choices"][0]["message"]["content"] or ""),
                        provider=self.name,
                        model=str(payload.get("model") or self.model),
                        prompt_tokens=int(usage.get("prompt_tokens") or 0),
                        completion_tokens=int(usage.get("completion_tokens") or 0),
                    )
                )
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as error:
            raise LLMError("LLM completion failed") from error
        finally:
            if owns_client:
                client.close()


class BedrockLLM:
    """Optional AWS Bedrock Converse adapter. Untested in CI; requires boto3."""

    name = "bedrock"

    def __init__(self, model_id: str, *, region: str | None = None) -> None:
        try:
            import boto3  # type: ignore[import-not-found]
        except ImportError as error:  # pragma: no cover - optional dependency
            raise LLMError("Install boto3 to use the Bedrock provider") from error
        self.model = model_id
        self._client = boto3.client("bedrock-runtime", region_name=region)

    def complete(  # pragma: no cover - requires AWS credentials
        self, messages: Sequence[ChatMessage], *, max_tokens: int = 512, json_mode: bool = False
    ) -> LLMResponse:
        del json_mode
        system = [{"text": m.content} for m in messages if m.role == "system"]
        conversation = [
            {"role": m.role, "content": [{"text": m.content}]}
            for m in messages
            if m.role != "system"
        ]
        try:
            with observed_store_call("llm", "complete", **{"ecg.provider": self.name}):
                payload = self._client.converse(
                    modelId=self.model,
                    system=system,
                    messages=conversation,
                    inferenceConfig={"maxTokens": max_tokens, "temperature": 0},
                )
        except Exception as error:
            raise LLMError("Bedrock completion failed") from error
        usage = payload.get("usage", {})
        return _record_usage(
            LLMResponse(
                text="".join(
                    part.get("text", "") for part in payload["output"]["message"]["content"]
                ),
                provider=self.name,
                model=self.model,
                prompt_tokens=int(usage.get("inputTokens", 0)),
                completion_tokens=int(usage.get("outputTokens", 0)),
            )
        )


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)


def complete_structured[T: BaseModel](
    provider: LLMProvider,
    messages: Sequence[ChatMessage],
    output_model: type[T],
    *,
    max_tokens: int = 512,
) -> T:
    """Request JSON output and validate it against a Pydantic model."""
    response = provider.complete(messages, max_tokens=max_tokens, json_mode=True)
    text = _FENCE.sub("", response.text.strip())
    try:
        return output_model.model_validate(json.loads(text))
    except (json.JSONDecodeError, ValidationError) as error:
        raise LLMError(f"LLM output did not match {output_model.__name__}") from error


def build_llm_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "mock":
        from enterprise_context.llm.mock_rules import DEFAULT_MOCK_RULES

        return MockLLM(DEFAULT_MOCK_RULES)
    if settings.llm_provider == "openai_compatible":
        if not settings.llm_base_url or not settings.llm_model:
            raise LLMError("LLM_BASE_URL and LLM_MODEL are required for openai_compatible")
        return OpenAICompatibleLLM(
            settings.llm_base_url,
            settings.llm_model,
            api_key=settings.llm_api_key,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    if settings.llm_provider == "bedrock":
        if not settings.llm_model:
            raise LLMError("LLM_MODEL (Bedrock model ID) is required for bedrock")
        return BedrockLLM(settings.llm_model, region=settings.aws_region)
    raise LLMError(f"Unsupported LLM provider: {settings.llm_provider}")
