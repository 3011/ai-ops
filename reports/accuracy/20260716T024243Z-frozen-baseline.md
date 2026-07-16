# Accuracy Loop 20260716T024243Z-frozen-baseline

- Suite: `core-accuracy-v1`
- Started: `2026-07-16T02:42:48+00:00`
- Evaluated: `2026-07-16T03:02:44+00:00`
- Result: **FAIL**

## Aggregate

| Metric | Value |
|---|---:|
| Finding Precision | 100.00% |
| Finding Recall | 100.00% |
| Target Accuracy | 100.00% |
| Replay Integrity | 100.00% |
| Agent Safe Validation | 100.00% |
| Agent Model Output Acceptance | 40.00% |
| Agent Useful Result Rate | 40.00% |
| Agent Parent Fact Overlap | 100.00% |
| Model Availability | 100.00% |
| Scenario Requirement Rate | 100.00% |
| TP / FP / FN / TN | 4 / 0 / 0 / 4 |

## Scenarios

| Scenario | Status | Incident | Findings | Target | Replay | Agent |
|---|---|---:|---|---|---|---|
| oom-killed | EVALUATED | 33 | container_oom_killed, container_restart_increased, memory_usage_increased, rollout_preceded_incident | True | VALID | FAILED/VALID_WITH_WARNINGS |
| oom-sampled | EVALUATED | 35 | container_oom_killed, container_restart_increased, memory_usage_increased, rollout_preceded_incident | True | VALID | FAILED/VALID_WITH_WARNINGS |
| cpu-spike | EVALUATED | 36 | container_cpu_spike, container_restart_stable, cpu_hot_loop_hint_log_observed, cpu_near_limit, cpu_request_saturated, cpu_throttling_sustained, rollout_preceded_incident | True | VALID | COMPLETED/VALID |
| oom-negative-control | EVALUATED | 31 | rollout_preceded_incident | True | VALID | COMPLETED/VALID |
| cpu-negative-control | EVALUATED | 32 | rollout_preceded_incident | True | VALID | COMPLETED_PARTIAL/VALID_WITH_WARNINGS |
| crashloop-coverage | COVERED_LEGACY | 34 | - | - | - | - |

## Feedback

- **P1 · oom-killed** — 提高 Agent 模型输出接受率：输出契约失败。
- **P1 · oom-sampled** — 提高 Agent 模型输出接受率：输出契约失败。
- **P2 · oom-negative-control** — 负对照在创建后立即告警，混入了真实 rollout 关联；后续使用预热基线资源以获得更纯净的负样本。
- **P1 · cpu-negative-control** — 提高 Agent 模型输出接受率：未在受控步骤内形成最终输出。
- **P2 · cpu-negative-control** — 负对照在创建后立即告警，混入了真实 rollout 关联；后续使用预热基线资源以获得更纯净的负样本。
- **P1 · crashloop-coverage** — CrashLoop 当前只有通用证据分析，没有确定性 Finding 引擎。 下一步应增加受控工具、Parser、Finding 和正负样本。
