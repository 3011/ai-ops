from __future__ import annotations

import re
from typing import Iterable

from app.investigation.agents.contracts import (
    AgentDiagnosisOutput,
    AgentValidationIssue,
    AgentValidationReport,
    AgentToolObservation,
)

AGENT_VALIDATOR_VERSION = "1.0.0"
_COUNTEREVIDENCE_TOKENS = (
    "counterevidence", "contradiction", "contradict", "反证", "矛盾",
    "但", "然而", "不过", "无法", "不足", "未发现", "未观察到",
    "无证据", "没有证据", "不能排除", "不支持", "not observed",
    "no evidence", "insufficient", "unable to", "cannot",
)


def _has_counterevidence_check(hypothesis) -> bool:
    if hypothesis.contradicting_fact_refs or hypothesis.support_level == "contradicted":
        return True
    if hypothesis.counterevidence_check.strip():
        return True
    rationale = hypothesis.rationale.casefold()
    return any(token.casefold() in rationale for token in _COUNTEREVIDENCE_TOKENS)


_FORBIDDEN_CERTAINTY = re.compile(
    r"root[_ ]cause[_ ]confirmed|confirmed root cause|根因已确认|确认根因|(?:probability|概率)\s*(?:is|为)?\s*[:：]?\s*\d|\d+(?:\.\d+)?%",
    re.IGNORECASE,
)


class AgentResultValidator:
    def validate(
        self,
        diagnosis: AgentDiagnosisOutput,
        *,
        allowed_finding_ids: Iterable[str],
        observations: list[AgentToolObservation],
        allowed_tool_names: Iterable[str],
        target_resolved: bool,
    ) -> AgentValidationReport:
        allowed_ids = set(allowed_finding_ids)
        for observation in observations:
            allowed_ids.update(observation.finding_ids)
        allowed_tools = set(allowed_tool_names)
        errors: list[AgentValidationIssue] = []
        warnings: list[AgentValidationIssue] = []
        checks: dict[str, bool] = {}

        def issue(code: str, path: str, message: str, severity: str = "error") -> None:
            item = AgentValidationIssue(code=code, severity=severity, path=path, message=message)
            (errors if severity == "error" else warnings).append(item)

        rendered = diagnosis.model_dump_json()
        checks["no_forbidden_certainty"] = _FORBIDDEN_CERTAINTY.search(rendered) is None
        if not checks["no_forbidden_certainty"]:
            issue("FORBIDDEN_CERTAINTY", "$", "Agent 输出包含禁止的确认性或概率表达。")

        checks["fact_refs_exist"] = set(diagnosis.fact_refs).issubset(allowed_ids)
        for ref in sorted(set(diagnosis.fact_refs) - allowed_ids):
            issue("UNKNOWN_FACT_REF", "$.fact_refs", f"Agent 引用了不存在的 Finding：{ref}")

        hypothesis_ids = [item.id for item in diagnosis.hypotheses]
        checks["unique_hypothesis_ids"] = len(hypothesis_ids) == len(set(hypothesis_ids))
        if not checks["unique_hypothesis_ids"]:
            issue("DUPLICATE_HYPOTHESIS_ID", "$.hypotheses", "Hypothesis ID 重复。")

        for index, hypothesis in enumerate(diagnosis.hypotheses):
            refs = set(hypothesis.fact_refs) | set(hypothesis.contradicting_fact_refs)
            unknown = refs - allowed_ids
            if unknown:
                issue("HYPOTHESIS_UNKNOWN_FACT_REF", f"$.hypotheses[{index}]", f"假设引用未知 Finding：{sorted(unknown)}")
            if hypothesis.support_level == "highly_supported" and not hypothesis.fact_refs:
                issue("HIGH_SUPPORT_WITHOUT_FACT", f"$.hypotheses[{index}]", "highly_supported 必须引用事实。")

        checks["tool_calls_registered"] = all(item.tool_name in allowed_tools for item in observations)
        for index, observation in enumerate(observations):
            if observation.tool_name not in allowed_tools:
                issue("UNREGISTERED_TOOL_EXECUTION", f"$.observations[{index}].tool_name", "执行了未注册工具。")

        if not target_resolved and diagnosis.fact_refs:
            issue("FACTS_WITH_UNRESOLVED_TARGET", "$.fact_refs", "目标未可靠解析时 Agent 不得输出对象级事实引用。")

        log_refs = {
            ref
            for observation in observations
            if observation.tool_name == "search_container_logs"
            for ref in observation.finding_ids
        }
        for index, hypothesis in enumerate(diagnosis.hypotheses):
            if hypothesis.support_level == "highly_supported" and set(hypothesis.fact_refs) and set(hypothesis.fact_refs).issubset(log_refs):
                issue(
                    "LOG_ONLY_HIGH_SUPPORT",
                    f"$.hypotheses[{index}]",
                    "仅由日志文本支持的假设不能标记为 highly_supported。",
                )

        if not diagnosis.hypotheses:
            issue("NO_HYPOTHESES", "$.hypotheses", "Agent 未形成假设；结果仍可审计但信息有限。", "warning")
        important = [
            item for item in diagnosis.hypotheses
            if item.support_level in {"highly_supported", "partially_supported"}
        ]
        unchecked = [item.id for item in important if not _has_counterevidence_check(item)]
        checks["important_hypotheses_counterevidence_checked"] = not unchecked
        if unchecked:
            issue(
                "NO_CONTRADICTION_CHECK",
                "$.hypotheses",
                f"重要受支持假设缺少反证检查：{unchecked}",
                "warning",
            )

        status = "INVALID" if errors else "VALID_WITH_WARNINGS" if warnings else "VALID"
        return AgentValidationReport(
            status=status,
            validator_version=AGENT_VALIDATOR_VERSION,
            checks=checks,
            errors=errors,
            warnings=warnings,
        )
