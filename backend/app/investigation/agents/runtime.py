from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.investigation.agents.contracts import (
    AgentDiagnosisOutput,
    AgentToolObservation,
    AgentTurn,
    InvestigationContext,
)
from app.investigation.agents.model_runtime import StructuredModel
from app.investigation.agents.prompts import build_turn_messages, sanitize_untrusted_value
from app.investigation.agents.protocol import AgentToolRuntime
from app.investigation.contracts import InvestigationBudget

AGENT_RUNTIME_VERSION = "1.0.0"
def model_safe_value(value: Any, *, key: str = "", depth: int = 0) -> Any:
    return sanitize_untrusted_value(value, key=key, depth=depth)



def observation_payload(item: AgentToolObservation) -> dict[str, Any]:
    payload = item.model_dump(mode="json")
    payload["data"] = model_safe_value(payload.get("data") or {})
    return payload


class StructuredInvestigationAgent:
    def __init__(self, model: StructuredModel) -> None:
        self.model = model

    async def investigate(
        self,
        context: InvestigationContext,
        tools: AgentToolRuntime,
        budget: InvestigationBudget,
    ) -> AgentDiagnosisOutput:
        observations: list[AgentToolObservation] = []
        validation_errors: list[str] = []
        max_turns = max(2, min(budget.max_steps + 2, 24))
        for turn_number in range(max_turns):
            invocation_type = "schema_repair" if validation_errors else "investigation_step"
            raw = await self.model.invoke(
                invocation_type=invocation_type,
                messages=build_turn_messages(
                    context,
                    [observation_payload(item) for item in observations],
                    validation_errors=validation_errors,
                ),
            )
            try:
                turn = AgentTurn.model_validate(raw)
            except ValidationError as exc:
                validation_errors = [error["msg"] for error in exc.errors()[:12]]
                if turn_number + 1 >= max_turns:
                    raise
                continue
            validation_errors = []
            if turn.action == "final":
                return turn.diagnosis  # type: ignore[return-value]
            call = turn.tool_call
            if call is None:
                raise RuntimeError("AGENT_TOOL_CALL_MISSING")
            observation = await tools.execute(call.tool_name, call.arguments)
            observations.append(observation)
            if observation.error_code in {
                "DEADLINE_EXCEEDED", "MAX_STEPS_REACHED", "MAX_TOOL_CALLS_REACHED",
                "MAX_SAME_TOOL_CALLS_REACHED", "COST_BUDGET_EXHAUSTED", "NO_PROGRESS",
            }:
                final = await self._request_final(context, observations, "工具预算已停止；必须输出 action=final，不得再请求工具。")
                if final is not None:
                    return final
                break
        final = await self._request_final(context, observations, "调查轮次已结束；必须输出 action=final，不得再请求工具。")
        if final is not None:
            return final
        return self._fallback(context, observations)

    async def _request_final(
        self,
        context: InvestigationContext,
        observations: list[AgentToolObservation],
        reason: str,
    ) -> AgentDiagnosisOutput | None:
        try:
            raw = await self.model.invoke(
                invocation_type="final_diagnosis",
                messages=build_turn_messages(
                    context,
                    [observation_payload(item) for item in observations],
                    validation_errors=[reason],
                ),
            )
            turn = AgentTurn.model_validate(raw)
        except (ValidationError, RuntimeError, ValueError):
            return None
        if turn.action != "final" or turn.diagnosis is None:
            return None
        return turn.diagnosis

    @staticmethod
    def _fallback(
        context: InvestigationContext,
        observations: list[AgentToolObservation],
    ) -> AgentDiagnosisOutput:
        available = list(dict.fromkeys([
            *context.initial_finding_ids,
            *(finding for item in observations for finding in item.finding_ids),
        ]))
        return AgentDiagnosisOutput(
            summary="Agent 调查在预算或步骤上限内结束，未获得可通过契约校验的最终输出。",
            fact_refs=available,
            hypotheses=[],
            missing_evidence=["模型未在受控步骤内返回最终 Diagnosis。"],
            recommended_checks=["检查模型可用性、工具预算和 Agent invocation 审计。"],
            risk_notes=["该结果不得替换确定性 Diagnosis。"],
        )
