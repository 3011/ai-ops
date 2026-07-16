from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.request import HTTPCookieProcessor, Request, build_opener

API_DEFAULT = "http://127.0.0.1:30801/api/v1"
TERMINAL = {"COMPLETED", "COMPLETED_PARTIAL", "FAILED"}


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def ratio(numerator: int, denominator: int, empty: float = 1.0) -> float:
    return numerator / denominator if denominator else empty


def score_truth(truth: dict[str, bool], actual: set[str]) -> dict[str, Any]:
    tp = fp = fn = tn = 0
    rows = []
    for finding_type, expected in truth.items():
        observed = finding_type in actual
        if expected and observed:
            outcome, tp = "TP", tp + 1
        elif expected and not observed:
            outcome, fn = "FN", fn + 1
        elif not expected and observed:
            outcome, fp = "FP", fp + 1
        else:
            outcome, tn = "TN", tn + 1
        rows.append({"finding_type": finding_type, "expected": expected, "observed": observed, "outcome": outcome})
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "rows": rows}


class ApiClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = os.getenv("AIOPS_SESSION_TOKEN", "").strip()
        self.opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        if not self.token:
            auth_script = Path(__file__).with_name("scenario_auth.sh")
            command = f"source {shlex.quote(str(auth_script))}; ensure_scenario_session; printf %s \"$AIOPS_SESSION_TOKEN\""
            self.token = subprocess.check_output(["bash", "-lc", command], text=True).strip()

    def get(self, path: str) -> dict[str, Any]:
        request = Request(self.base_url + path, headers={"Cookie": f"aiops_session={self.token}"})
        with self.opener.open(request, timeout=30) as response:
            return json.load(response)


@dataclass
class ScenarioResult:
    payload: dict[str, Any]
    ready: bool


def pick_incident(items: list[dict[str, Any]], scenario: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
    binding = (state.get("scenario_bindings") or {}).get(scenario["id"]) or {}
    if binding.get("incident_id") is not None:
        return next((item for item in items if int(item.get("id") or 0) == int(binding["incident_id"])), None)
    started = parse_time(state.get("started_at"))
    candidates = []
    for item in items:
        if scenario["title_contains"] not in str(item.get("title") or ""):
            continue
        if started:
            first_seen = parse_time(item.get("first_seen_at"))
            if first_seen and first_seen < started - timedelta(minutes=2):
                continue
        candidates.append(item)
    return max(candidates, key=lambda row: int(row.get("id") or 0), default=None)


def evaluate_scenario(client: ApiClient, scenario: dict[str, Any], incidents: list[dict[str, Any]], state: dict[str, Any]) -> ScenarioResult:
    row = pick_incident(incidents, scenario, state)
    base: dict[str, Any] = {"id": scenario["id"], "kind": scenario["kind"], "found": bool(row)}
    if not row:
        base.update({"ready": False, "status": "MISSING", "feedback": "未找到本轮场景 Incident。"})
        return ScenarioResult(base, False)
    detail = client.get(f"/incidents/{row['id']}")
    base.update({"incident_id": row["id"], "title": row["title"]})
    if scenario["kind"] == "coverage":
        serialized = json.dumps(detail, ensure_ascii=False)
        required = scenario.get("legacy_required_strings") or []
        matched = [value for value in required if value in serialized]
        trusted = [run for run in detail.get("trusted_investigations") or [] if run.get("run_kind") == "deterministic"]
        passed = len(matched) == len(required)
        base.update({
            "ready": True,
            "status": "COVERED_LEGACY" if passed else "COVERAGE_FAILED",
            "legacy_signals_expected": required,
            "legacy_signals_matched": matched,
            "trusted_run_count": len(trusted),
            "known_gap": scenario.get("known_gap"),
        })
        return ScenarioResult(base, True)

    runs = detail.get("trusted_investigations") or []
    deterministic = [run for run in runs if run.get("run_kind") == "deterministic" and run.get("engine") == scenario["engine"]]
    binding = (state.get("scenario_bindings") or {}).get(scenario["id"]) or {}
    pinned_run_id = binding.get("deterministic_run_id")
    run = (
        next((item for item in deterministic if int(item.get("id") or 0) == int(pinned_run_id)), None)
        if pinned_run_id is not None
        else max(deterministic, key=lambda value: int(value.get("id") or 0), default=None)
    )
    if not run or run.get("status") not in TERMINAL:
        base.update({"ready": False, "status": "RUN_PENDING", "run": run})
        return ScenarioResult(base, False)

    findings = {item.get("finding_type") for item in run.get("findings") or [] if item.get("finding_type")}
    scored = score_truth(scenario.get("finding_truth") or {}, findings)
    target = run.get("target_context") or {}
    expected_target = scenario.get("target") or {}
    target_checks = {
        "namespace": target.get("namespace") == expected_target.get("namespace"),
        "service": target.get("service_name") == expected_target.get("service"),
        "container": target.get("container_name") == expected_target.get("container"),
        "resolution_quality": str(target.get("resolution_quality") or "").lower() == "high",
        "pod_uid_present": bool(target.get("pod_uid")),
    }
    alert_uids = {
        str((alert.get("labels") or {}).get("pod_uid") or (alert.get("labels") or {}).get("uid") or "")
        for alert in detail.get("alerts") or []
    } - {""}
    if alert_uids:
        target_checks["alert_uid_match"] = target.get("pod_uid") in alert_uids

    required_any = scenario.get("required_any_findings") or []
    any_check = not required_any or bool(findings.intersection(required_any))
    replay = run.get("replay_snapshot") or {}
    replay_ok = replay.get("validation_status") in (scenario.get("required_replay_status") or [])

    children = [
        child for child in runs
        if child.get("parent_run_id") == run.get("id")
        and child.get("run_kind") in {"agent_shadow", "agent_offline"}
    ]
    agent = max(children, key=lambda value: int(value.get("id") or 0), default=None)
    agent_ready = not scenario.get("agent_expected") or bool(agent and agent.get("status") in TERMINAL)
    agent_summary: dict[str, Any] = {"expected": bool(scenario.get("agent_expected")), "present": bool(agent)}
    if agent:
        evaluation = agent.get("evaluation") or {}
        metrics = evaluation.get("metrics") or {}
        diagnosis = agent.get("diagnosis") or {}
        validated = diagnosis.get("validated_output") or {}
        hypotheses = diagnosis.get("hypotheses") or []
        degradation = set(agent.get("degradation_reasons") or [])
        summary_text = str(diagnosis.get("summary") or "")
        contract_failure = (
            agent.get("status") == "FAILED"
            or "AGENT_OUTPUT_CONTRACT_FAILED" in degradation
            or validated.get("agent_error_kind") == "output_contract"
        )
        fallback_output = (
            "未获得可通过契约校验的最终输出" in summary_text
            or "模型未在受控步骤内返回最终 Diagnosis" in " ".join(diagnosis.get("missing_evidence") or [])
        )
        model_output_accepted = (
            agent.get("status") in {"COMPLETED", "COMPLETED_PARTIAL"}
            and not contract_failure
            and not fallback_output
        )
        strong = [item for item in hypotheses if item.get("support_level") in {"highly_supported", "partially_supported"}]
        terms = [str(term).casefold() for term in scenario.get("negative_signal_terms") or []]
        false_signal_strong = [
            item for item in strong
            if any(term in str(item.get("statement") or "").casefold() for term in terms)
        ]
        positive_scenario = any(bool(value) for value in (scenario.get("finding_truth") or {}).values())
        useful_output = (
            model_output_accepted
            and ((positive_scenario and bool(strong)) or (not positive_scenario and not false_signal_strong))
        )
        agent_summary.update({
            "run_id": agent.get("id"),
            "run_kind": agent.get("run_kind"),
            "status": agent.get("status"),
            "validation_status": agent.get("agent_validation_status"),
            "evaluation_status": evaluation.get("status"),
            "model_available": metrics.get("model_available"),
            "parent_fact_overlap": metrics.get("parent_fact_overlap"),
            "unsupported_hypothesis_rate": metrics.get("unsupported_hypothesis_rate"),
            "contradiction_check_rate": metrics.get("contradiction_check_rate"),
            "hypotheses": len(hypotheses),
            "strong_hypotheses": len(strong),
            "false_signal_strong_hypotheses": [item.get("statement") for item in false_signal_strong],
            "safe_validation": agent.get("agent_validation_status") in {"VALID", "VALID_WITH_WARNINGS"},
            "model_output_accepted": model_output_accepted,
            "useful_output": useful_output,
            "fallback_output": fallback_output,
            "contract_failure": contract_failure,
            "degradation_reasons": sorted(degradation),
            "stop_reason": agent.get("stop_reason"),
        })

    ready = agent_ready
    base.update({
        "ready": ready,
        "status": "EVALUATED" if ready else "AGENT_PENDING",
        "deterministic_run_id": run.get("id"),
        "deterministic_status": run.get("status"),
        "actual_findings": sorted(findings),
        "finding_score": scored,
        "required_any_findings": required_any,
        "required_any_pass": any_check,
        "target_checks": target_checks,
        "target_pass": all(target_checks.values()),
        "replay_status": replay.get("validation_status"),
        "replay_pass": replay_ok,
        "agent": agent_summary,
    })
    return ScenarioResult(base, ready)


def aggregate(results: list[dict[str, Any]], suite: dict[str, Any]) -> dict[str, Any]:
    trusted = [item for item in results if item.get("kind") == "trusted" and item.get("status") == "EVALUATED"]
    score = {key: sum(int((item.get("finding_score") or {}).get(key) or 0) for item in trusted) for key in ("tp", "fp", "fn", "tn")}
    precision = ratio(score["tp"], score["tp"] + score["fp"])
    recall = ratio(score["tp"], score["tp"] + score["fn"])
    target_accuracy = ratio(sum(bool(item.get("target_pass")) for item in trusted), len(trusted), empty=0.0)
    replay_rate = ratio(sum(bool(item.get("replay_pass")) for item in trusted), len(trusted), empty=0.0)
    agents = [item.get("agent") or {} for item in trusted if (item.get("agent") or {}).get("expected")]
    agent_safe_validation_rate = ratio(sum(agent.get("safe_validation") is True for agent in agents), len(agents), empty=0.0)
    agent_output_acceptance_rate = ratio(sum(agent.get("model_output_accepted") is True for agent in agents), len(agents), empty=0.0)
    agent_useful_result_rate = ratio(sum(agent.get("useful_output") is True for agent in agents), len(agents), empty=0.0)
    model_availability_rate = ratio(sum(agent.get("model_available") is True for agent in agents), len(agents), empty=0.0)
    parent_overlap_values = [agent.get("parent_fact_overlap") for agent in agents if isinstance(agent.get("parent_fact_overlap"), (int, float))]
    parent_overlap_rate = ratio(sum(value == 1.0 for value in parent_overlap_values), len(parent_overlap_values), empty=0.0)
    unsupported_values = [agent.get("unsupported_hypothesis_rate") for agent in agents if isinstance(agent.get("unsupported_hypothesis_rate"), (int, float))]
    scenario_requirement_rate = ratio(sum(bool(item.get("required_any_pass", True)) for item in trusted), len(trusted), empty=0.0)
    unsupported_rate = max(unsupported_values, default=0.0)
    gates = suite.get("gates") or {}
    gate_results = {
        "finding_precision": precision >= gates.get("finding_precision_min", 0.99),
        "finding_recall": recall >= gates.get("finding_recall_min", 0.90),
        "target_accuracy": target_accuracy >= gates.get("target_accuracy_min", 0.99),
        "replay_integrity": replay_rate == 1.0,
        "scenario_requirements": scenario_requirement_rate == 1.0,
        "agent_safe_validation": agent_safe_validation_rate >= gates.get("agent_safe_validation_min", 1.0),
        "agent_parent_fact_overlap": parent_overlap_rate >= gates.get("parent_fact_overlap_min", 1.0),
        "agent_model_output_acceptance": agent_output_acceptance_rate >= gates.get("agent_model_output_acceptance_min", 0.80),
        "agent_useful_results": agent_useful_result_rate >= gates.get("agent_useful_result_min", 0.80),
        "unsupported_hypotheses": unsupported_rate <= gates.get("unsupported_hypothesis_rate_max", 0.0),
    }
    return {
        **score,
        "finding_precision": precision,
        "finding_recall": recall,
        "target_accuracy": target_accuracy,
        "replay_integrity_rate": replay_rate,
        "agent_safe_validation_rate": agent_safe_validation_rate,
        "agent_contract_rate": agent_output_acceptance_rate,
        "agent_model_output_acceptance_rate": agent_output_acceptance_rate,
        "agent_useful_result_rate": agent_useful_result_rate,
        "model_availability_rate": model_availability_rate,
        "agent_parent_fact_overlap_rate": parent_overlap_rate,
        "max_unsupported_hypothesis_rate": unsupported_rate,
        "scenario_requirement_rate": scenario_requirement_rate,
        "trusted_scenarios_evaluated": len(trusted),
        "coverage_scenarios": len([item for item in results if item.get("kind") == "coverage"]),
        "gates": gate_results,
        "pass": all(gate_results.values()),
    }


def feedback(results: list[dict[str, Any]], summary: dict[str, Any]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for result in results:
        for row in (result.get("finding_score") or {}).get("rows") or []:
            if row["outcome"] == "FN":
                items.append({"priority": "P0", "scenario": result["id"], "action": f"提高 {row['finding_type']} 召回率：检查采样窗口、工具输出和 Parser 规则。"})
            elif row["outcome"] == "FP":
                items.append({"priority": "P0", "scenario": result["id"], "action": f"阻断 {row['finding_type']} 误报：收紧确认规则并增加负样本。"})
        if result.get("kind") == "trusted" and not result.get("target_pass", True):
            failed = [key for key, value in (result.get("target_checks") or {}).items() if not value]
            items.append({"priority": "P0", "scenario": result["id"], "action": f"修复目标定位：失败检查项 {', '.join(failed)}。"})
        if result.get("kind") == "trusted" and not result.get("replay_pass", True):
            items.append({"priority": "P0", "scenario": result["id"], "action": "修复 Replay/Validator 完整性，避免不可复现结果。"})
        if result.get("kind") == "trusted" and not result.get("required_any_pass", True):
            items.append({"priority": "P0", "scenario": result["id"], "action": f"场景缺少要求的任一 Finding：{', '.join(result.get('required_any_findings') or [])}。"})
        agent = result.get("agent") or {}
        if agent.get("expected") and agent.get("present"):
            if agent.get("model_available") is False:
                items.append({"priority": "P1", "scenario": result["id"], "action": "提高模型可用性：重试、退避、超时分层或供应商回退。"})
            if not agent.get("safe_validation"):
                items.append({"priority": "P0", "scenario": result["id"], "action": f"修复 Agent 安全校验；当前 {agent.get('validation_status') or agent.get('stop_reason')}。"})
            if not agent.get("model_output_accepted"):
                reason = "输出契约失败" if agent.get("contract_failure") else "未在受控步骤内形成最终输出"
                items.append({"priority": "P1", "scenario": result["id"], "action": f"提高 Agent 模型输出接受率：{reason}。"})
            elif not agent.get("useful_output"):
                items.append({"priority": "P1", "scenario": result["id"], "action": "Agent 输出安全但未形成符合场景期望的有效假设或安全弃权。"})
            if isinstance(agent.get("unsupported_hypothesis_rate"), (int, float)) and agent["unsupported_hypothesis_rate"] > 0:
                items.append({"priority": "P0", "scenario": result["id"], "action": "消除无 Finding 支持的 Agent 假设。"})
        if result.get("kind") == "trusted" and result.get("actual_findings") and result.get("id", "").endswith("negative-control") and "rollout_preceded_incident" in result.get("actual_findings", []):
            items.append({"priority": "P2", "scenario": result["id"], "action": "负对照在创建后立即告警，混入了真实 rollout 关联；后续使用预热基线资源以获得更纯净的负样本。"})
        if result.get("kind") == "coverage" and result.get("known_gap"):
            priority = "P1" if result.get("status") == "COVERED_LEGACY" else "P0"
            items.append({"priority": priority, "scenario": result["id"], "action": result["known_gap"] + " 下一步应增加受控工具、Parser、Finding 和正负样本。"})
    if summary.get("pass") and not items:
        items.append({"priority": "P2", "scenario": "suite", "action": "核心场景通过；继续增加探针失败、Pending、NodePressure、磁盘和网络负样本，扩大覆盖而不是放宽规则。"})
    return items


def write_report(report: dict[str, Any], directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    run_id = report["run_id"]
    json_path = directory / f"{run_id}.json"
    md_path = directory / f"{run_id}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    s = report["summary"]
    lines = [
        f"# Accuracy Loop {run_id}", "",
        f"- Suite: `{report['suite']}`",
        f"- Started: `{report.get('started_at')}`",
        f"- Evaluated: `{report['evaluated_at']}`",
        f"- Result: **{'PASS' if s['pass'] else 'FAIL'}**", "",
        "## Aggregate", "",
        "| Metric | Value |", "|---|---:|",
        f"| Finding Precision | {s['finding_precision']:.2%} |",
        f"| Finding Recall | {s['finding_recall']:.2%} |",
        f"| Target Accuracy | {s['target_accuracy']:.2%} |",
        f"| Replay Integrity | {s['replay_integrity_rate']:.2%} |",
        f"| Agent Safe Validation | {s['agent_safe_validation_rate']:.2%} |",
        f"| Agent Model Output Acceptance | {s['agent_model_output_acceptance_rate']:.2%} |",
        f"| Agent Useful Result Rate | {s['agent_useful_result_rate']:.2%} |",
        f"| Agent Parent Fact Overlap | {s['agent_parent_fact_overlap_rate']:.2%} |",
        f"| Model Availability | {s['model_availability_rate']:.2%} |",
        f"| Scenario Requirement Rate | {s['scenario_requirement_rate']:.2%} |",
        f"| TP / FP / FN / TN | {s['tp']} / {s['fp']} / {s['fn']} / {s['tn']} |", "",
        "## Scenarios", "",
        "| Scenario | Status | Incident | Findings | Target | Replay | Agent |", "|---|---|---:|---|---|---|---|",
    ]
    for item in report["scenarios"]:
        findings = ", ".join(item.get("actual_findings") or []) or "-"
        agent = item.get("agent") or {}
        agent_text = f"{agent.get('status', '-')}/{agent.get('validation_status', '-')}" if agent else "-"
        lines.append(f"| {item['id']} | {item.get('status')} | {item.get('incident_id', '-')} | {findings} | {item.get('target_pass', '-')} | {item.get('replay_status', '-')} | {agent_text} |")
    lines.extend(["", "## Feedback", ""])
    for item in report["feedback"]:
        lines.append(f"- **{item['priority']} · {item['scenario']}** — {item['action']}")
    md_path.write_text("\n".join(lines) + "\n")
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default=API_DEFAULT)
    parser.add_argument("--ground-truth", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--wait-seconds", type=int, default=0)
    parser.add_argument("--poll-seconds", type=int, default=15)
    args = parser.parse_args()
    suite = json.loads(args.ground_truth.read_text())
    state = json.loads(args.state.read_text())
    client = ApiClient(args.api)
    deadline = time.monotonic() + args.wait_seconds
    results: list[ScenarioResult] = []
    while True:
        incidents = client.get("/incidents?include_test=true&limit=500").get("items") or []
        results = [evaluate_scenario(client, scenario, incidents, state) for scenario in suite["scenarios"]]
        if all(item.ready for item in results) or time.monotonic() >= deadline:
            break
        pending = [item.payload["id"] for item in results if not item.ready]
        print(f"waiting for scenarios: {', '.join(pending)}", flush=True)
        time.sleep(args.poll_seconds)
    payloads = [item.payload for item in results]
    bindings = dict(state.get("scenario_bindings") or {})
    for item in payloads:
        if item.get("incident_id") is None:
            continue
        binding = {"incident_id": item["incident_id"]}
        if item.get("deterministic_run_id") is not None:
            binding["deterministic_run_id"] = item["deterministic_run_id"]
        bindings[item["id"]] = binding
    state["scenario_bindings"] = bindings
    args.state.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str))
    summary = aggregate(payloads, suite)
    report = {
        "schema_version": "1.0.0",
        "suite": suite["suite"],
        "run_id": state["run_id"],
        "started_at": state.get("started_at"),
        "evaluated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "summary": summary,
        "scenarios": payloads,
    }
    report["feedback"] = feedback(payloads, summary)
    json_path, md_path = write_report(report, args.report_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"JSON_REPORT={json_path}")
    print(f"MARKDOWN_REPORT={md_path}")
    incomplete = [item.payload["id"] for item in results if not item.ready]
    if incomplete:
        print(f"INCOMPLETE={','.join(incomplete)}")
        return 2
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
