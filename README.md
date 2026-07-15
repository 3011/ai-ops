# AIOps Console

源码目录：`k8s-cp01:/root/aiops-console`。开发服务运行在 Kubernetes `aiops-dev` 命名空间；API、Worker 和 React 通过 `hostPath` 挂载源码，PostgreSQL 使用 PVC。

- Web：`http://172.30.10.11:30300`
- API 文档：`http://172.30.10.11:30801/docs`
- 当前 API：`0.9.0-dev.4`

## 核心链路

```text
Prometheus → Alertmanager → FastAPI webhook
                          → PostgreSQL webhook/alert/incident/outbox
                          → Worker 自动发现 Kubernetes 目标
                          → Kubernetes API / Events / current+previous logs
                          → 原始告警 PromQL + 通用 Prometheus/Loki 证据
                          → Kubernetes rollout / 镜像 / ConfigMap 元数据
                          → CI/CD 发布事件 / 可选 Tempo 或 Jaeger Trace
                          → DeepSeek 受限动态 PromQL/LogQL 规划
                          → 安全校验与只读执行
                          → 结构化根因假设、覆盖度和缺口说明
```

## 当前能力

- `webhook_deliveries → alert_instances → incidents` 三层模型；
- firing/resolved 幂等和同 fingerprint 跨 startsAt 生命周期收敛；
- PostgreSQL Outbox、`FOR UPDATE SKIP LOCKED`、重试、死信和锁回收；
- 自动发现 Pod、Deployment、ReplicaSet、StatefulSet、DaemonSet、Service 和 Node；
- 自动采集容器状态、Ready、重启、退出码、OOMKilled、镜像和 Kubernetes Events；
- 自动采集 Deployment revision、ReplicaSet rollout 历史、镜像变化和 ConfigMap 引用/元数据变更；
- 通用 CI/CD 发布事件 webhook，支持 token 校验、幂等和开放事件自动重分析；
- 可配置 Tempo/Jaeger Trace 数据源，按 service 和事件时间窗查询链路；
- 自动读取当前与 previous 容器日志，Loki 延迟或短生命周期容器也能取证；
- 从 Alertmanager `generatorURL` 解析原始 PromQL；
- 自动查询 CPU、内存、重启、Ready、OOM、网络、CPU throttling、Node 和 target up；
- DeepSeek 动态规划最多 4 条 PromQL 和 2 条 LogQL；
- 动态查询必须命中事件作用域，LogQL 仅允许 namespace 内简单行过滤；
- 初次告警后自动安排完整 30 分钟观察窗口的补充分析；
- 证据、查询、响应摘要、耗时和错误全部持久化；
- React 工作台：总览、事件、告警、变更记录、Webhook、任务、模型与集成设置；
- 事件详情按诊断概览、变更时间线、全部证据、关联与历史四个标签页组织；
- 前端 vendor chunk 拆分，React、Ant Design 和数据层独立缓存；
- 生产视图默认隐藏 `aiops_test=true` 测试数据，可通过页面开关查看。


## OOMKilled 可信调查（0.8.0）

第一阶段新增独立于模型的可信调查链路：

```text
Incident → TargetContext Resolver → get_container_termination_status
→ ToolExecution → DeterministicFinding → container_oom_killed_v1
→ DiagnosisResult
```

关键约束：

- TargetContext 固定 namespace、Pod name、Pod UID 和 Container；
- Kubernetes 查询返回的 UID 必须再次匹配 TargetContext；
- 只有 high 定位质量、OOMKilled termination reason 且终止时间在调查窗口内，才生成 `container_oom_killed` Finding；
- `UNAVAILABLE`、`TARGET_UNCERTAIN` 和 `NOT_FOUND` 不会生成否定或确认 Finding；
- Finding 必须关联 ToolExecution，工具保存结构化结果、模型可见摘要、原始结果 Hash、状态和错误语义；
- Agent 尚未启用，成功确认后状态仍为 `COMPLETED_PARTIAL`，降级原因是 `AGENT_NOT_ENABLED`；
- 事件详情新增“可信调查”页，显示目标 UID、定位路径、确定性事实、工具审计和降级状态。

现有 DeepSeek 分析仍并行保留，但不参与上述 OOMKilled 硬事实确认。

## 可信 Tool Runtime（0.8.1）

0.8.1 不增加第二个诊断工具，而是把 0.8.0 的单工具硬编码改造成通用可信执行管线：

```text
ToolRegistry
→ Tool Schema Validation
→ TargetContext Binding
→ Budget Reservation
→ Same-Run Cache Lookup
→ TrustedTool.execute
→ Artifact Redaction / Hash / Truncation
→ ToolExecution Persistence
→ Finding Parser
→ DeterministicFinding Persistence
→ ToolResult finding_ids 回填
```

当前正式注册工具仍只有：

```text
get_container_termination_status@1.0.0
```

运行时保证：

- 工具实现不操作 `investigation_tool_executions`、Finding 或 Artifact 数据库表；
- 未注册工具和参数错误返回 `INVALID_REQUEST`，并产生审计记录；
- namespace 越权返回 `DENIED`；
- 预算在访问 Kubernetes 之前预留，缓存命中成本为 0；
- 缓存键包含 Run、工具版本、规范化参数、Pod UID、Container、时间窗口、定位质量和允许作用域；
- 同一调用命中缓存时不访问数据源、不重复创建 Finding，并记录 `reused_execution_id`；
- 404 为 `NOT_FOUND`，401/403 为 `DENIED`，429/超时/5xx 为可重试 `UNAVAILABLE`；
- Resolver 有 Pod 名时直接 GET，仅 UID 时分页扫描，service/app 时优先受控 labelSelector；
- `ownerReferences` 优先使用 `controller=true`，Workload 元数据失败不会降低已经确认的 Pod UID 质量；
- 原始与结构化结果先脱敏，超过阈值后裁剪 JSONB，并将完整内容压缩保存到 `investigation_artifacts`；
- `model_visible_output_json.finding_ids` 与数据库 Finding 保持一致。

默认 Artifact 策略：

```text
Inline JSON 上限：65536 bytes
集合元素上限：200
单字符串上限：4000 characters
完整 Artifact：PostgreSQL BYTEA + zlib
Artifact URI：db://investigation_artifacts/<id>
Hash：完整脱敏对象的 SHA-256
```

预算默认值：

```text
max_steps=8
max_tool_calls=12
max_total_cost_units=20
max_same_tool_calls=3
max_no_progress_rounds=2
deadline=调查开始后 2 分钟
```

Agent、第二个诊断工具、Snapshot Replay 和 Result Validator 仍未启用。

## 可信 Prometheus 工具与 CPU 确定性调查（0.9.0-dev.1）

当前内部 Gate 1/2 新增三个注册工具：

```text
get_memory_usage_vs_limit
get_cpu_usage_vs_request_limit
get_cpu_throttling
```

约束：

- 工具参数不接受自由 PromQL；查询由代码生成并固定 namespace、Pod、Container；
- cAdvisor 序列必须通过 Pod UID 二次校验；
- Prometheus 403、429、5xx、超时、无数据和部分响应使用不同失败语义；
- 查询范围、步长、最大序列数和最大数据点由客户端强制限制；
- 内存未观察到达到 limit 不能反证 OOMKilled；
- CPU Spike 必须同时满足历史基线倍数、绝对增量和持续样本条件；
- throttling 可以确认发生过限流，但不能单独确认 CPU limit 是事故根因；
- 无 Profile 数据时不能声明具体函数、线程或代码路径导致 CPU 升高。

无 Agent 链路：

```text
OOM：ContainerStatus → Memory usage vs limit → Deterministic Findings
CPU：CPU usage vs request/limit → CPU throttling → Deterministic Findings
```

Agent、RED 指标、日志工具、发布工具、Replay 和 Validator 尚未进入本开发闸门。

## “无需告警模板”的边界

平台不要求为每个告警名称手工维护 PromQL/LogQL 模板。它采用：

1. 确定性目标发现；
2. 通用只读证据采集；
3. 原始告警表达式复用；
4. LLM 受限动态查询规划；
5. 安全校验和预算控制；
6. LLM 最终假设与人工确认。

这能覆盖大量 Kubernetes、Node、容器和具备指标/日志的应用故障，但不能保证任何故障都得到唯一根因。缺少业务指标、外部托管服务数据、变更记录、Trace 或领域语义时，平台会降低覆盖度并明确列出缺口，而不是虚构结论。


## 登录与权限

首次部署会创建 `admin` 管理员，随机初始密码只保存在 Kubernetes Secret：

```bash
ssh k8s 'kubectl get secret aiops-secrets -n aiops-dev -o jsonpath="{.data.BOOTSTRAP_ADMIN_PASSWORD}" | base64 -d; echo'
```

首次登录必须修改密码。默认角色：

- `admin`：全部权限；
- `operator`：查看与分析事件、查看变更、配置只读和版本查看；
- `viewer`：只读查看总览、事件、变更和版本。

用户、角色、权限和审计位于前端 `平台治理 → 用户与权限`。后端对每个 API 权限做强制校验，前端菜单隐藏不是安全边界。

## 版本记录

版本说明位于 `平台治理 → 版本说明`。系统启动时会自动登记当前版本，并补录 0.1～0.6 的历史里程碑。管理员也可以在页面新增版本记录。

代码仓库同时维护 `CHANGELOG.md`。每次发布至少记录：版本号、标题、Git Commit、发布时间和变更项。

## 模型设置

进入前端 `设置 → 模型设置`，可维护 OpenAI-compatible Base URL、模型、API Key 和启用状态。API Key 使用 Fernet 加密存入 PostgreSQL，前端不回显明文；Worker 每次任务读取最新配置，无需重启。

当前验证模型：`deepseek-v4-flash`。

## 变更与 Trace 集成

CI/CD 发布后调用：

```text
POST /api/v1/webhooks/deployment-events
X-AIOps-Token: <aiops-secrets/RELEASE_WEBHOOK_TOKEN>
```

请求包含 `namespace`、`service`、版本、commit、镜像、执行人和发生时间。平台会保存变更，并对同作用域的开放事件自动重新分析。

Trace 在 `设置 → 集成设置` 中配置。当前支持 Tempo 和 Jaeger；未配置时页面明确显示未接入，不影响其他证据采集。

## 部署

```bash
cd /root/aiops-console
bash deploy/dev/deploy.sh
```

## 场景回归

测试资源只创建在 `aiops-dev`，全部携带 `aiops_test=true`：

```bash
bash deploy/dev/scenarios/run.sh
# 等待 Prometheus、Alertmanager 和 Worker 完成
python3 deploy/dev/scenarios/validate.py
bash deploy/dev/scenarios/cleanup.sh
```

覆盖 CrashLoopBackOff、OOMKilled、Node-only、缺失标签降级、HTTP 动态查询规划、rollout/镜像/ConfigMap、CI/CD 事件、Tempo Trace、不同 startsAt 的 fingerprint 生命周期、测试数据隔离和无死信任务。

后端单元测试：

```bash
kubectl exec -n aiops-dev deploy/aiops-worker -- \
  sh -ec 'PYTHONPATH=/deps:/workspace python -m unittest discover -s /workspace/tests -v'
```

## 查看日志

```bash
kubectl logs -n aiops-dev deployment/aiops-api -f
kubectl logs -n aiops-dev deployment/aiops-worker -f
kubectl logs -n aiops-dev deployment/aiops-web -f
```

## 安全边界

- Worker Kubernetes RBAC 仅 `get/list` 和 `pods/log get`；
- Prometheus、Loki、Alertmanager 只读；
- 动态查询有数量、长度、复杂度和作用域限制；
- 日志脱敏，告警和日志被视为不可信输入；
- 不执行自动修复、删除、重启、扩缩容或配置变更；
- 登录、RBAC 和审计已启用；正式公网开放前仍需补充 HTTPS、CSRF Token、SSO/OIDC 和更严格的会话策略。

## 0.9.0-dev.2 Gate 3

当前 `release/0.9.0` 已注册九个只读可信工具。新增的重启、rollout、日志、副本 CPU 和应用 RED 工具全部通过 Tool Runtime 执行，不接受自由 PromQL/LogQL，不执行写操作。日志内容按不可信输入处理，发布 Finding 只表示时间相关性。Agent Runtime 尚未启用。

## 0.9.0-dev.3 Gate 4

Gate 4 新增 Snapshot Replay 与 Result Validator。每个新可信调查在 Diagnosis 保存后生成一份不可变的模型可见快照，包含告警上下文、TargetContext、受控工具输入、裁剪后的结构化结果、Finding 和 Diagnosis；不包含 `raw_output_json` 或 Artifact 正文，也不会在重放时访问 Kubernetes、Prometheus 或 Loki。

Validator 检查：

- Run、ToolExecution、Finding 和 Diagnosis 的引用完整性；
- Pod UID、Container、Workload UID 和 namespace 作用域一致性；
- 工具名称、版本和 Finding 类型白名单；
- 日志 Finding 的 `untrusted_input` 标记；
- 输入 Snapshot Hash、重放正文大小和 raw response 泄漏；
- 确定性模式不得写入模型假设。

结果状态为 `VALID`、`VALID_WITH_WARNINGS` 或 `INVALID`。校验失败不会改写原始 Finding，也不会被解释为“没有异常”。Agent Runtime 仍未启用。

## 0.9.0-dev.4 Gate 4.1

每个新可信调查在创建 `AnalysisRun` 时立即冻结模型可见输入，写入 `run_input_json`、Schema Version、来源 Hash 和 `native_frozen` 标识。Snapshot Replay 只读取冻结输入，不会因 Incident 后续新增告警、标签变化或 resolved 状态变化而漂移。旧 Run 通过版本化 adapter 和 Run 开始时间截止恢复为 `historical_reconstructed`；无法精确恢复时标记 `legacy_incomplete`。数据库触发器禁止已冻结输入被更新。
