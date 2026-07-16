# AIOps Ground Truth 与准确性闭环

该闭环只在 `aiops-dev` 中运行，所有场景资源和告警必须携带 `aiops_test=true`。生产视图默认隐藏测试数据。

## 流程

```text
Ground Truth
→ 创建隔离故障和负对照
→ Alertmanager / Incident / Worker
→ 确定性 Tool / Finding / Replay
→ Agent Shadow / Evaluation
→ Precision、Recall、Target Accuracy
→ 自动反馈建议
→ 清理测试资源
```

## 第一版覆盖

- 真实 OOMKilled；
- 带 Prometheus 峰值采样的 OOMKilled；
- 真实 CPU Spike；
- 声称 OOMKilled、实际稳定的负对照；
- 声称 High CPU、实际空闲的负对照；
- CrashLoop 通用证据覆盖，并明确记录“尚无确定性 Finding 引擎”的能力缺口。

## 指标定义

- Finding Precision：被评估的确定性 Finding 中，真实为正的比例；
- Finding Recall：Ground Truth 正例中，被平台发现的比例；
- Target Accuracy：namespace、service、container、Pod UID 和高质量定位全部正确的场景比例；
- Replay Integrity：Snapshot Validator 为 `VALID` 或允许的历史警告；
- Agent Effect：模型可用性、输出契约、父事实保留、无依据假设和反证检查。

第一版只对明确可控的核心 Finding 计分。额外 Finding 不会自动视为误报；要加入 Precision 统计，必须先在 Ground Truth 中明确标注真假。

## 执行

```bash
bash /root/aiops-console/deploy/dev/scenarios/run_accuracy_loop.sh
```

报告写入：

```text
/root/aiops-console/reports/accuracy/<RUN_ID>.json
/root/aiops-console/reports/accuracy/<RUN_ID>.md
```

## 安全约束

- 只操作 `aiops-dev` 中名称为 `aiops-scenario-*` 的资源；
- 不修改真实业务 Deployment；
- 不执行自动修复；
- 负对照告警使用唯一 fingerprint；
- 清理只取消测试 Incident 的未来 follow-up Job；
- 历史测试 Incident、Finding 和 Artifact 保留用于回归趋势。
