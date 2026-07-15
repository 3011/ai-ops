from __future__ import annotations

import json
from typing import Any, Protocol

import httpx
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.investigation.agents.audit import ModelInvocationAudit
from app.investigation.agents.contracts import AgentTurn
from app.investigation.agents.prompts import PROMPT_VERSION
from app.model_config import RuntimeModelConfig

RUNTIME_NAME = "openai_compatible_json"
RUNTIME_VERSION = "1.1.0"


class StructuredModel(Protocol):
    async def invoke(
        self,
        *,
        invocation_type: str,
        messages: list[dict[str, str]],
    ) -> dict[str, Any]: ...


def extract_json_object(content: Any) -> dict[str, Any]:
    if isinstance(content, dict):
        return content
    text = str(content or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("model response is not a JSON object")
    return value


class OpenAICompatibleStructuredModel:
    def __init__(
        self,
        session: AsyncSession,
        analysis_run_id: int,
        runtime: RuntimeModelConfig,
        *,
        timeout_seconds: float = 45.0,
    ) -> None:
        self.runtime = runtime
        self.timeout_seconds = timeout_seconds
        self.audit = ModelInvocationAudit(session, analysis_run_id)

    async def invoke(self, *, invocation_type: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        if not self.runtime.enabled or not self.runtime.api_key:
            raise RuntimeError("MODEL_NOT_CONFIGURED")
        parameters = {
            "temperature": 0,
            "max_tokens": 2600,
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
        }
        body = {
            "model": self.runtime.model,
            **parameters,
            "messages": messages,
        }
        row, started = await self.audit.start(
            invocation_type=invocation_type,
            runtime_name=RUNTIME_NAME,
            runtime_version=RUNTIME_VERSION,
            provider="openai-compatible",
            model=self.runtime.model,
            model_parameters=parameters,
            prompt_version=PROMPT_VERSION,
            request_payload=body,
        )
        response_payload: dict[str, Any] | None = None
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds)) as client:
                response = await client.post(
                    f"{self.runtime.base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {self.runtime.api_key}", "Content-Type": "application/json"},
                    json=body,
                )
                response.raise_for_status()
                response_payload = response.json()
            message = response_payload["choices"][0]["message"]
            parsed = extract_json_object(message.get("content"))
            usage = response_payload.get("usage") or {}
            await self.audit.complete(
                row,
                started,
                response_payload={"parsed": parsed, "provider_response": response_payload},
                input_tokens=usage.get("prompt_tokens"),
                output_tokens=usage.get("completion_tokens"),
            )
            return parsed
        except Exception as exc:
            code = "MODEL_SCHEMA_ERROR" if isinstance(exc, (ValueError, json.JSONDecodeError, ValidationError)) else "MODEL_INVOCATION_FAILED"
            await self.audit.fail(
                row,
                started,
                error_code=code,
                error_message=f"{type(exc).__name__}: {exc}",
                response_payload=response_payload,
            )
            raise


class ScriptedStructuredModel:
    """Deterministic model used by unit/evaluation tests."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def invoke(self, *, invocation_type: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        self.calls.append({"invocation_type": invocation_type, "messages": messages})
        if not self.responses:
            raise RuntimeError("SCRIPTED_MODEL_EXHAUSTED")
        return self.responses.pop(0)
