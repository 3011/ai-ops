# AIOps Console 架构与历史交接手册

> **状态说明（2026-07-28）**：本文档保留完整架构、数据库、API 和历史阶段背景。其“当前 live 状态”、Commit、计数和部署操作可能已过期。下一位 Agent 必须先阅读 [`CURRENT_HANDOFF.md`](CURRENT_HANDOFF.md) 和 [`ENGINEERING_RULES.md`](ENGINEERING_RULES.md)，实时状态以集群核验与 [`AGENT_HANDOFF_STATE.json`](AGENT_HANDOFF_STATE.json) 为准。

> 历史快照时间：2026-07-16 05:36:08 +02:00（Europe/Amsterdam）
> 当时发布版本：`0.9.0`
> 当时发布分支：`release/0.9.0`
> 当时部署与 `v0.9.0` Tag Commit：`260c6da374a0c5676dcd558af506d25f38d62115`

本文档是详细背景参考，不是接管入口。开始任何修改前，先阅读 `CURRENT_HANDOFF.md`、`ENGINEERING_RULES.md`、`README.md` 和 `CHANGELOG.md`，再检查实时集群状态。不要仅依赖聊天历史。

---

## 1. 五分钟接管清单

```bash
cd /root/ai-ops
git branch --show-current
git status --short
git rev-parse HEAD
git ls-remote origin refs/heads/release/0.9.0

ssh k8s 'kubectl get pods -n aiops-dev -o wide'
ssh k8s 'kubectl get configmap aiops-config -n aiops-dev \
  -o jsonpath="{.data.APP_VERSION}{\" \"}{.data.GIT_COMMIT}{\"\n\"}"'

curl -fsS http://172.30.10.11:30801/healthz
curl -fsS http://172.30.10.11:30801/readyz
curl -fsS -o /dev/null -w '%{http_code}\n' http://172.30.10.11:30300
```

预期基线：

```text
本地分支：release/0.9.0
部署应用 Commit / v0.9.0：260c6da374a0c5676dcd558af506d25f38d62115
分支 HEAD：可能仅比应用 Commit 多一笔 docs-only 最终交接提交
API 版本：0.9.0
INVESTIGATION_MODE：shadow
API、Worker、Web、PostgreSQL：1/1 Running
healthz：ok
readyz：ready
Web：HTTP 200
```

发生不一致时，先查 Git、ConfigMap、数据库版本说明和 Pod 环境变量，不要立即重复部署或删除 Pod。

---

## 2. 仓库、主机和运行环境

### 2.1 GitHub

```text
Repository：https://github.com/3011/ai-ops
稳定分支：main
开发分支：release/0.9.0
```

分支状态：

```text
main / origin/main：3f5e96c（0.8.1 稳定基线）
release/0.9.0 部署应用与 v0.9.0：260c6da（0.9.0）
release/0.9.0 分支 HEAD：可能仅比应用基线多一笔 docs-only 最终交接提交
```

`release/0.9.0` 已完成并部署；是否合并到 `main` 由项目负责人按发布流程决定，不要在未核对 live 状态和回归结果时直接改写稳定分支。

### 2.2 开发与部署拓扑

```text
Exec_MCP 当前环境
└── /root/ai-ops                 # GitHub 主工作副本，有 git 和 GitHub SSH 权限
    └── ssh k8s
        └── k8s-cp01:/root/aiops-console  # K8s hostPath 运行源码
```

重要：用户说的“`ssh k8s` 的上一层环境”就是 `/root/ai-ops` 所在环境。日常开发、提交和 push 在这里完成。

### 2.3 Kubernetes

```text
SSH alias：k8s
控制节点：k8s-cp01
Namespace：aiops-dev
Web：http://172.30.10.11:30300
API：http://172.30.10.11:30801
API Docs：http://172.30.10.11:30801/docs
```

组件：

```text
aiops-api       FastAPI，hostPath 挂载 backend
aiops-worker    Outbox Worker，hostPath 挂载 backend
aiops-web       React/Vite，hostPath 挂载 frontend
postgresql-0    PostgreSQL，PVC 5Gi
```

API 和 Worker Deployment 使用 `Recreate`。命名空间 ResourceQuota 较紧，同时替换多个高 limit Pod 可能被拒绝。部署后建议按 API → Worker → Web 顺序重启并等待 Ready。

### 2.4 当前 live 状态

最后核验：2026-07-16 05:36 +02:00（Europe/Amsterdam）。

```text
aiops-api       1/1 Running
aiops-worker    1/1 Running
aiops-web       1/1 Running
postgresql-0    1/1 Running

APP_VERSION：0.9.0
GIT_COMMIT：260c6da374a0c5676dcd558af506d25f38d62115
INVESTIGATION_MODE：shadow
模型：enabled=true，openai-compatible / deepseek-v4-flash
Trace：enabled=false，未配置真实 Tempo/Jaeger
Pending / retry / processing jobs：0
Dead jobs：0
临时测试 Pod/资源：0
```

数据库当前概要：

```text
用户：2
角色：4
权限：11
事件：36，全部 resolved（其中包含准确性闭环测试事件）
变更事件：14
Outbox：succeeded 79，skipped 17；pending/retry/processing/dead 均为 0

可信调查 Run：
  deterministic：COMPLETED_PARTIAL 34
  agent_offline：COMPLETED 2
  agent_shadow：COMPLETED_PARTIAL 13，FAILED 8

ModelInvocation：SUCCEEDED 169
Snapshot 1.1.0：VALID 12，VALID_WITH_WARNINGS 2，INVALID 1
Snapshot 1.2.0：VALID 31，VALID_WITH_WARNINGS 10，INVALID 1
Agent Evaluation 0.9.0：PASS 10，EFFECTIVENESS_WARNING 13，FAIL 0；聚合状态 PASS
```

不要删除 `ljx` 或自定义角色 `sss`，它们不是临时测试对象。

---

## 3. 项目当前定位

平台同时存在两条分析链路，必须严格区分。

### 3.1 旧分析链路

```text
Alertmanager
→ Webhook Delivery / AlertInstance / Incident
→ Outbox
→ Kubernetes / Prometheus / Loki / Change / Trace 证据
→ DeepSeek 动态查询规划
→ 结构化 AI 根因假设
```

特点：

- 仍在生产页面保留；
- DeepSeek 可关闭；
- 动态规划最多 4 条 PromQL、2 条 LogQL；
- 查询有作用域与复杂度限制；
- 输出是“假设”，不是确定性硬事实。

### 3.2 可信确定性调查链路

```text
Incident
→ frozen Run input
→ TargetContext Resolver
→ ToolRegistry / ToolRuntime
→ ToolExecution
→ DeterministicFinding
→ Deterministic DiagnosisResult
→ Snapshot Replay / Result Validator
→ 独立 Agent Shadow 或 Offline 子 Run
→ Agent Validator / Evaluation / Comparison
```

特点：

- 不依赖模型确认硬事实；
- 目标固定到 Pod UID、Container、Workload UID 和时间窗口；
- 所有事实引用真实 ToolExecution；
- Agent 已默认以 `shadow` 模式运行，使用独立子 Run、预算、Diagnosis 和校验；
- Agent 失败或模型不可用不会改变确定性父 Run；
- Offline Replay 只消费已保存 Snapshot，禁止访问实时数据源。

Agent Runtime 已建立在第二条链路上，仍禁止直接复用旧 DeepSeek Prompt、自由查询或写操作。

---

## 4. 功能范围

### 4.1 告警与事件

数据层次：

```text
webhook_deliveries   Alertmanager HTTP 投递审计
alert_instances      fingerprint + starts_at 生命周期
incidents            按 cluster/namespace/service/environment 聚合
incident_alerts      Incident 与 AlertInstance 关系
outbox_jobs          异步分析任务
```

已处理的关键边缘：同 fingerprint 的 resolved 可能携带不同 startsAt，系统会关闭该 fingerprint 下仍 firing 的旧实例，避免事件永久 open。

### 4.2 证据采集

旧链路支持：

- Kubernetes Pod/Workload/Node 状态；
- Kubernetes Events；
- current / previous Pod logs；
- generatorURL 原始 PromQL；
- 通用 CPU、内存、重启、Ready、OOM、网络、throttling、Node、up；
- Loki；
- Deployment revision、ReplicaSet、镜像；
- ConfigMap 引用、resourceVersion、键名和 managedFields 时间；
- CI/CD ChangeEvent；
- 可选 Tempo/Jaeger。

安全边界：不读取 Secret；ConfigMap 不读取值，只读取引用、元数据和键名。

### 4.3 用户、权限与审计

认证：HttpOnly 签名 Cookie，会话默认 8 小时。密码使用 PBKDF2-HMAC-SHA256 加盐哈希。首次管理员凭据只存在 Kubernetes Secret。错误密码统一返回不可缓存的 401，不暴露账号是否存在，主动清除浏览器旧会话 Cookie，并写入审计；前端会显示卡片内错误、清空并聚焦密码框。

默认角色：

```text
admin     全部权限
operator  查看和分析事件、查看变更和配置
viewer    只读
```

权限：

```text
audit.view
changes.view
dashboard.view
incidents.analyze
incidents.view
settings.manage
settings.view
users.manage
users.view
versions.manage
versions.view
```

后端授权是安全边界，前端菜单隐藏不是安全边界。

### 4.4 页面

```text
/dashboard
/incidents
/incidents/:id
/alerts
/changes
/webhooks
/jobs
/settings/model
/settings/integrations
/users
/releases
```

事件详情包含：诊断概览、可信调查、变更时间线、全部证据、关联与历史。

---

## 5. 可信 Tool Runtime

### 5.1 核心文件

```text
backend/app/investigation/contracts.py
backend/app/investigation/enums.py
backend/app/investigation/registry.py
backend/app/investigation/catalog.py
backend/app/investigation/tool_runtime.py
backend/app/investigation/budget.py
backend/app/investigation/artifacts.py
backend/app/investigation/resolver.py
backend/app/investigation/service.py
```

边界：

```text
TrustedTool        只访问数据源并返回 ToolObservation
ToolRuntime        参数校验、作用域、预算、缓存、审计、Artifact、Parser、Finding
Service            固定确定性编排和 DiagnosisResult
Finding Parser     只能从 ToolResult 生成白名单 Finding
```

新增工具时不得自行写 `investigation_tool_executions`、`investigation_findings` 或 Artifact 表。

### 5.2 当前九个工具

```text
get_container_termination_status@1.0.0   cost 1
get_memory_usage_vs_limit@1.0.0          cost 3
get_cpu_usage_vs_request_limit@1.0.0     cost 4
get_cpu_throttling@1.0.0                 cost 3
get_container_restart_history@1.0.0      cost 2
get_recent_rollouts@1.0.0                cost 2
search_container_logs@1.0.0              cost 3
compare_cpu_across_replicas@1.0.0        cost 5
get_application_red_metrics@1.0.0        cost 4
```

工具注册唯一入口：`backend/app/investigation/catalog.py::build_default_registry()`。

### 5.3 固定确定性计划

OOM：

```text
get_container_termination_status
get_memory_usage_vs_limit
get_container_restart_history
get_recent_rollouts
search_container_logs
```

CPU：

```text
get_cpu_usage_vs_request_limit
get_cpu_throttling
get_container_restart_history
get_recent_rollouts
search_container_logs
compare_cpu_across_replicas
get_application_red_metrics
```

当前固定计划预算：

```text
max_steps=10
max_tool_calls=10
max_total_cost_units=30
max_same_tool_calls=3
max_no_progress_rounds=10
deadline=3 分钟
```

`max_no_progress_rounds=10` 是因为固定计划必须执行完整清单；未来 Agent Runtime 应使用更严格的 no-progress 停止规则，并使用独立预算账本。

### 5.4 错误语义

```text
404                 NOT_FOUND，retryable=false
401/403             DENIED，retryable=false
429                 UNAVAILABLE / RATE_LIMITED，retryable=true
timeout / 5xx       UNAVAILABLE，retryable=true
参数错误            INVALID_REQUEST
UID 不一致          TARGET_UNCERTAIN
响应不完整          PARTIAL
预算拒绝            BUDGET_EXCEEDED，数据源不应被访问
```

禁止推导：

```text
NOT_FOUND / UNAVAILABLE ≠ 未发生异常
PARTIAL ≠ 数据完整
没有 Prometheus 峰值 ≠ OOMKilled 没发生
没有近期 rollout ≠ 排除所有发布问题
```

### 5.5 缓存

缓存限定在同一 AnalysisRun。键包括：

```text
analysis_run_id
工具名和版本
规范化参数
cluster / namespace
Pod name / UID / Container
Workload UID
调查窗口
定位方法与定位质量
允许 namespace
```

命中缓存时：

- 不访问外部数据源；
- 新增 ToolExecution 审计；
- `reused_execution_id` 指向原执行；
- cost 为 0；
- 不重复创建 Finding；
- 返回原 Finding IDs。

### 5.6 Artifact

默认策略：

```text
Inline JSON 最大 65536 bytes
集合最大 200 项
单字符串最大 4000 字符
最大深度 10
模型可见摘要最大 2000 字符
```

超限：裁剪 JSONB，`is_truncated=true`，完整脱敏对象 zlib 压缩存入 `investigation_artifacts`，URI 为 `db://investigation_artifacts/<id>`。

先脱敏，再计算完整对象 SHA-256。不要把日志、Token、Cookie、Authorization、密码或连接串直接写入快照或模型输入。

---

## 6. TargetContext 与确定性规则

### 6.1 Resolver 顺序

```text
有 Pod name
→ 直接 GET Pod
→ 校验告警 Pod UID

只有 Pod UID
→ 使用 continue token 分页扫描

只有 service/app
→ 受控 labelSelector
→ 必要时分页扫描
```

ownerReferences 必须优先 `controller=true`。Workload 查询失败不应降低已经 high 的 Pod UID 身份质量，但要记录 Workload 元数据不完整。

### 6.2 OOM 硬事实

`container_oom_killed_v1` 必须同时满足：

```text
ToolStatus=FOUND
resolution_quality=high
termination.reason=OOMKilled
finishedAt 存在且落在调查窗口
Pod UID 和 Container 与 TargetContext 完全一致
```

日志出现 OOM 字符串不能生成 OOMKilled confirmed Finding。

### 6.3 CPU Spike

同时满足：

```text
相对历史基线至少 2 倍
绝对增量至少 0.10 Core
至少两个持续升高样本
达到最小持续时间
```

新 Pod 缺少固定历史窗口时，只能对同一 Pod UID 的观测序列做前后分段。

### 6.4 Rollout、日志、RED

- Rollout Finding 只表达时间相关性，不表达因果；
- 日志是 `untrusted_input=true`，只生成 observation Finding；
- Prompt Injection 文本必须隔离；
- RED 只允许内置 Profile，不允许自由指标名或 PromQL；
- 副本比较必须限定同一 Workload UID。

---

## 7. Snapshot Replay 与 Result Validator

### 7.1 关键文件

```text
backend/app/investigation/replay.py
backend/app/investigation/run_input.py
backend/migrations/0006_snapshot_replay.sql
backend/migrations/0007_freeze_investigation_run_input.sql
```

### 7.2 Run 输入冻结

新 Run 创建时立即写入：

```text
run_input_json
run_input_schema_version
run_input_source_hash
run_input_source_mode=native_frozen
input_snapshot_hash=run_input_source_hash
```

PostgreSQL Trigger `trg_investigation_run_input_immutable` 禁止冻结后修改。

来源模式：

```text
native_frozen              新 Run 原生冻结
historical_reconstructed   旧 Run 按 run.started_at 和 engine_version 重建
legacy_incomplete          历史字段不足，不能精确恢复
```

旧 Run 不回写冻结字段。历史适配器只在 Replay 构建期间临时工作。

### 7.3 Snapshot 内容

包含：

- frozen / reconstructed Run input；
- 冻结的 Incident 摘要；
- TargetContext；
- 工具版本和模型可见输入；
- 模型可见 ToolResult；
- Finding；
- Diagnosis；
- Artifact URI 和 Hash。

不包含：

```text
raw_output_json
Prometheus/Loki 原始正文
Artifact 正文
Secret
```

重放不访问 Kubernetes、Prometheus、Loki、Alertmanager 或 DeepSeek。

### 7.4 Validator

当前 Schema / Validator：`1.1.0`。

校验：

- Run input 正文 Hash；
- source mode 和 frozen metadata；
- Pod UID / Container / Workload UID；
- namespace 作用域；
- 工具注册与版本漂移；
- 工具允许生成的 Finding 白名单；
- ToolExecution/Finding/Diagnosis 引用；
- 日志 untrusted 标记；
- OOM 规则版本；
- raw payload 泄漏；
- Snapshot 大小；
- 确定性模式不得有模型 Hypothesis。

状态：`VALID`、`VALID_WITH_WARNINGS`、`INVALID`。

历史基线：

```text
Run 1   INVALID
        TOOL_FINDING_REF_MISMATCH
        legacy_contract_violation=true
        这是旧版本真实契约违规，必须永久保留

Run 2   VALID
        historical_reconstructed，0.8.x adapter

Run 4   VALID
        historical_reconstructed，0.9.x adapter

Run 13  VALID_WITH_WARNINGS
        TARGET_UNRESOLVED，合理历史警告

Run 16  native_frozen
        VALID_WITH_WARNINGS，因为测试 Pod 已删除
```

不要为了“全绿”修改 Validator 让 Run 1 变成 VALID。

### 7.5 API

```text
GET  /api/v1/investigations/{analysis_run_id}/replay
POST /api/v1/investigations/{analysis_run_id}/replay
POST /api/v1/investigations/replay-backfill
```

POST 需要 `incidents.analyze`，并写审计日志。

---

## 8. 数据库与迁移

迁移顺序：

```text
0002_change_trace.sql
0003_auth_versions.sql
0004_trusted_oom_investigation.sql
0005_trusted_tool_runtime.sql
0006_snapshot_replay.sql
0007_freeze_investigation_run_input.sql
```

主要表：

```text
webhook_deliveries
alert_instances
incidents
incident_alerts
outbox_jobs
evidence_snapshots
analysis_runs                     # 旧模型分析
model_settings
change_events
trace_settings
app_users / roles / permissions
audit_logs
release_notes

investigation_analysis_runs
investigation_tool_executions
investigation_findings
investigation_diagnosis_results
investigation_artifacts
investigation_replay_snapshots
```

`deploy/dev/deploy.sh` 会按文件名顺序执行全部 migration，SQL 必须幂等。

注意：API lifespan 仍会执行 SQLAlchemy `create_all`，但修改已有表必须使用 migration。

---

## 9. API 入口

公开健康检查：

```text
GET /healthz
GET /readyz
```

机器 Webhook：

```text
POST /api/v1/webhooks/alertmanager
POST /api/v1/webhooks/deployment-events
```

deployment-events 使用 `X-AIOps-Token`。Token 只在 Kubernetes Secret 中，禁止输出或提交。

用户 API：

```text
/api/v1/auth/*
/api/v1/dashboard/*
/api/v1/incidents/*
/api/v1/alerts
/api/v1/webhook-deliveries
/api/v1/analysis-jobs
/api/v1/change-events
/api/v1/settings/model
/api/v1/settings/traces
/api/v1/investigations/*/replay
/api/v1/users
/api/v1/roles
/api/v1/permissions
/api/v1/audit-logs
/api/v1/releases
```

匿名业务 API 应返回 401。权限不足返回 403。

---

## 10. 模型和 Trace

当前模型配置：

```text
provider=openai-compatible
base_url=https://api.deepseek.com
model=deepseek-v4-flash
enabled=true
last_test_status=success
```

API Key 使用 Fernet 加密存入 PostgreSQL，不得回显、打印或提交。不要从 Secret 或数据库中读取明文到对话。

Trace：

```text
provider=tempo
enabled=false
base_url 未配置
```

历史 `last_test_status=success` 来自临时 Tempo-compatible mock，不代表真实 Trace 后端正在运行。

---

## 11. Kubernetes RBAC 与安全边界

Worker 当前：

```text
get pods：yes
get pods/log：yes
get configmaps：yes
get secrets：no
delete pods：no
patch deployments：no
```

系统不自动修复，不删除 Pod，不扩缩容，不修改 Deployment，不读取 Secret。

未来 Agent 也只能调用注册的只读工具，不得新增写工具或自由 Shell/Kubernetes API。

---

## 12. 开发、测试、部署流程

### 12.1 开发

```bash
cd /root/ai-ops
git checkout release/0.9.0
git pull --ff-only origin release/0.9.0
```

先在 `/root/ai-ops` 修改和测试，不要直接在 `k8s-cp01:/root/aiops-console` 开发。

### 12.2 后端单元测试

本地通常缺 SQLAlchemy 依赖。已有一次性依赖目录可能存在 `/tmp/aiops-test-deps`，但不能假设永久存在。

集群测试：

```bash
ssh k8s 'kubectl exec -n aiops-dev deploy/aiops-worker -- \
  sh -ec "PYTHONPATH=/deps:/workspace python -m unittest discover -s /workspace/tests -v"'
```

当前本地完整回归：

```text
102 tests
77 PASS
25 个隔离 PostgreSQL 用例按设计 skipped
```

命令：

```bash
PYTHONPATH=backend .venv/bin/python -m unittest discover -s backend/tests -p 'test_*.py'
```

### 12.3 隔离 PostgreSQL 测试

数据库用例需要 `AIOPS_TEST_DATABASE_URL`，必须使用隔离 PostgreSQL，不能在开发数据库运行 drop_all/create_all。

当前预期：13 passed。

测试文件：

```text
backend/tests/investigation/test_persistence.py
backend/tests/investigation/test_resource_persistence.py
backend/tests/investigation/test_release_persistence.py
backend/tests/investigation/test_replay_persistence.py
```

### 12.4 前端

```bash
cd /root/ai-ops/frontend
npm ci
npm run build
```

当前构建通过。Ant Design chunk >500k 是警告，不是失败。

### 12.5 场景回归

```bash
ssh k8s 'cd /root/aiops-console && bash deploy/dev/scenarios/run.sh'
# 等待 Prometheus、Alertmanager 和 Worker
ssh k8s 'cd /root/aiops-console && python3 deploy/dev/scenarios/validate.py'
ssh k8s 'cd /root/aiops-console && bash deploy/dev/scenarios/cleanup.sh'
```

测试资源全部应带 `aiops_test=true`，默认生产页面隐藏测试数据。

覆盖：CrashLoop、快速/采样 OOM、CPU Spike、throttling、缺失标签、Node、HTTP planner、fingerprint 生命周期、rollout、ConfigMap、CI/CD、Trace mock、Gate 3 日志/RED/副本比较、Replay。

Ground Truth 准确性闭环：

```bash
ssh k8s 'cd /root/aiops-console && bash deploy/dev/scenarios/run_accuracy_loop.sh'
```

闭环使用固定 Ground Truth、真实故障、负对照、冻结 Incident/父 Run、Precision/Recall、Agent 效果和自动反馈。规范见 `docs/ACCURACY_LOOP.md`；权威修复前后报告见：

```text
reports/accuracy/20260716T024243Z-frozen-baseline.md
reports/accuracy/20260716T024243Z-iteration3.md
```

### 12.6 同步源码

目标机可能没有 `rsync`，也没有可靠的 native git。推荐 tar 流：

```bash
cd /root/ai-ops
tar --exclude='./frontend/node_modules' \
    --exclude='./frontend/dist' \
    --exclude='./deploy/backups' \
    -cf - . | ssh k8s 'tar -xf - -C /root/aiops-console'
```

同步前先备份到源码目录之外，避免递归复制：

```bash
ssh k8s 'mkdir -p /root/aiops-console-backups && \
  cp -a /root/aiops-console /root/aiops-console-backups/<name>-<timestamp>'
```

不要备份到 `/root/aiops-console/deploy/backups/` 后再复制整个 deploy 目录，可能形成自包含递归。

### 12.7 部署

```bash
ssh k8s 'cd /root/aiops-console && bash deploy/dev/deploy.sh'
```

`deploy.sh` 会：

1. 应用 Namespace、Quota、ConfigMap；
2. 确保 Secret；
3. 启动 PostgreSQL；
4. 顺序执行 migrations；
5. 应用 API、Worker、Web、AlertmanagerConfig；
6. 等待 rollout。

重要：ConfigMap 或 hostPath 源码变化不会必然修改 Deployment PodTemplate。部署脚本结束后通常仍需显式重启 Pod。

建议逐个执行：

```bash
ssh k8s 'kubectl delete pod -n aiops-dev -l app=aiops-api && \
  kubectl wait --for=condition=Ready pod -n aiops-dev -l app=aiops-api --timeout=300s'

ssh k8s 'kubectl delete pod -n aiops-dev -l app=aiops-worker && \
  kubectl wait --for=condition=Ready pod -n aiops-dev -l app=aiops-worker --timeout=300s'

ssh k8s 'kubectl delete pod -n aiops-dev -l app=aiops-web && \
  kubectl wait --for=condition=Ready pod -n aiops-dev -l app=aiops-web --timeout=300s'
```

API/Worker Pod 有依赖安装 InitContainer，启动可能较慢。长 SSH 超时不等于 Kubernetes 失败。先用短命令检查 Pod，再决定是否重试。不要并发重复删除。

### 12.8 提交和版本一致性

```bash
git add ...
git commit -m '...'
git push origin release/0.9.0
```

正式部署后必须核对四处：

```text
GitHub release/0.9.0 HEAD
v0.9.0 Tag
/root/aiops-console 当前同步源码
aiops-config GIT_COMMIT
release_notes 当前版本 commit_sha
```

同版本再次部署时，启动种子会更新版本说明的 Commit，不会重复创建版本记录。

---

## 13. 当前已知运维陷阱

1. **ResourceQuota**：同时替换 API/Worker/Web 可能因为临时并行 Pod 超 quota。逐个重启。
2. **SSH 长连接**：Exec_MCP 长命令偶尔卡在传输层，`remote_pid=null`。不要假设远端执行；先查看 active exec 和短命令现状。
3. **hostPath 热加载**：API 使用 uvicorn reload，Worker 不会可靠热加载；Worker 代码变更必须重启。
4. **ConfigMap 不触发 rollout**：部署后检查 Pod age 和环境变量。
5. **测试数据库**：隔离测试会 drop_all，绝不能指向开发 PostgreSQL。
6. **历史 Snapshot**：Validator 升级会创建新 Snapshot 记录，旧记录保留。查询时按 `created_at DESC` 取最新。
7. **历史 Run 输入**：旧 Run 的 `run_input_*` 保持 NULL，Replay 使用 adapter；不要回填覆盖历史原始记录。
8. **Run 1**：真实 INVALID，禁止“修绿”。
9. **Trace**：未部署真实后端，不要声称 Trace 可用。
10. **ConfigMap**：只读取键名和元数据，不读取值。
11. **Secrets**：禁止输出 API Key、管理员密码、release webhook token、session secret、encryption key。
12. **测试数据**：历史测试事件保留用于审计，默认 UI 隐藏，不要为追求数据库干净而删除。

---

## 14. Git 里程碑

```text
adf534c  Initialize AIOps Console MVP
17ecfd2  Prometheus/Loki evidence and React detail
268fb3f  Optional structured LLM analysis
4633044  Alertmanager wiring
27e568d  Opt-in routing docs
dd26ca1  Operations dashboard/workflows
b8aae80  Header and test provenance
4077767  Fixed navigation
d151ba3  Template-free dynamic evidence
bd2b049  Rollout/change/trace evidence and UX

d19287e  0.7.0 governance, users, roles, release notes
b39331c  0.8.0 deterministic OOMKilled trusted slice
3f5e96c  0.8.1 Tool Runtime budgets/cache/artifacts

3ca4e32  0.9 dev.1 Prometheus resource tools and CPU slice
39b4915  Late Pod CPU baseline
d921260  Sampled OOM scenario
41bf0ba  0.9 dev.2 application/correlation tools
0d38740  Release note commit synchronization
b4e9d1a  0.9 dev.3 Snapshot Replay and Validator
8f785ab  0.9 dev.4 frozen Run input and legacy adapters
204c7bc  0.9 Agent runtime, shadow, offline replay and evaluation baseline
7067753  Model-unavailable evaluation semantics and release suite finalization
6af06c2  Effective tool-call and counterevidence evaluation semantics
8450382  Login UX redesign and failed-authentication session hardening
```

`d921260` 前的空格只是本文排版；真实 Commit 为 `d921260`。

---

## 15. 0.9.0 最终完成状态

本节是发布后的权威状态。旧 dev.5/dev.6/dev.7 阶段计划已经完成，不再是“下一步任务”。下一版本号与范围尚未决定，后续工作必须先由项目负责人定义。

### 15.1 已完成能力

```text
Agent Protocol + ModelInvocation Artifact audit
Offline Snapshot Agent Replay + Snapshot Validator 1.2.0
Agent Output Validator 1.2.0 + Prompt agent-investigation-v5
Ground Truth Accuracy Loop + frozen scenario/run bindings
Independent real-time Agent Shadow + comparison UI
OOM/CPU evaluation + security regression
Default investigation_mode=shadow
High-contrast single-card login UI + failed-authentication session hardening
```

新增持久化：

```text
investigation_analysis_runs.parent_run_id
investigation_analysis_runs.run_kind
investigation_analysis_runs.source_snapshot_id
investigation_analysis_runs.agent_validation_status
investigation_analysis_runs.agent_validation_report_json
investigation_model_invocations
investigation_agent_evaluations
```

新增 API：

```text
POST /api/v1/investigations/{analysis_run_id}/agent-replay
POST /api/v1/investigations/{analysis_run_id}/agent-shadow
GET  /api/v1/investigations/{analysis_run_id}/comparison
POST /api/v1/investigations/{analysis_run_id}/evaluate
GET  /api/v1/investigation-evaluations/summary
```

### 15.2 最终权威边界

- 确定性 Run 和 Finding 仍是 OOMKilled、CPU Spike 等硬事实的唯一权威来源；
- Agent 使用独立子 Run、独立预算和独立 Diagnosis，不覆盖父级；
- 模型失败只使子 Run失败，Worker 的确定性完成状态不变；
- 新 Run 明确区分 `MODEL_UNAVAILABLE` 与 `OUTPUT_CONTRACT_FAILED`；模型已响应但 Schema/安全措辞不合规时不得再标记为模型不可用；
- Agent Validator 为 `INVALID` 时不创建正式 `InvestigationDiagnosisResult`；
- Offline Replay 只消费 Snapshot 的模型可见 ToolResult，外部数据源访问数必须为 0；
- Agent 只能选择九个注册只读工具，不允许自由 PromQL/LogQL，也没有 Kubernetes 写权限；
- 日志与告警文本始终是不可信输入，不能单独把假设提升为 `highly_supported`；
- 模型请求/响应必须保存脱敏 Artifact、Hash 和业务审计；框架 Trace 不能替代该审计；
- 登录失败不区分用户不存在、停用或密码错误，必须清除旧会话并记录审计。

### 15.3 发布评估结果

Evaluation Suite：`0.9.0`，重算样本 23 个 Agent 子 Run，聚合状态 `PASS`。

```text
Safety Pass Rate                    100.00%
OOM Hard Fact Consistency           100.00%
CPU Target Identity Match           100.00%
Unsupported Hypothesis Rate           0.00%
Useful Tool Call Rate                89.58%  （门槛 >=60%）
Duplicate Tool Call Rate              1.81%  （门槛 <=10%）
重要假设反证检查率                    90.58%  （门槛 >=80%）
预算耗尽率                             0.00%  （门槛 <=10%）
Model Availability Rate              65.22%  （观测指标，不是安全失败门槛）
```

所有发布 Gate 均为 true：越权/未注册工具、虚构 Finding、confirmed 表达、Prompt Injection 行为改变、父 Run 被模型失败影响、Offline 外部访问均为 0。

`EFFECTIVENESS_WARNING` 13 条主要来自历史模型不可用或旧输出质量，不是安全失败；该段是闭环运行前的 23 个子 Run 发布基线。

准确性闭环随后故意产生并永久保留了契约失败、Validator 失败和修复后 Replay，因此当前全库 Evaluation 状态包含 `PASS 24 / EFFECTIVENESS_WARNING 27 / FAIL 9`。这些全库历史计数不能替代冻结场景门槛，也不得通过删除失败 Run“修绿”。

### 15.4 Ground Truth 准确性闭环

第一轮真实执行创建并清理以下隔离场景：

```text
真实 OOMKilled
带内存采样的 OOMKilled
真实 CPU Spike / hot loop
稳定内存 OOM 负对照
空闲 CPU 负对照
CrashLoop BackOff 覆盖场景
```

所有资源和告警携带 `aiops_test=true`；场景结束后临时资源为 0，未来测试任务为 0。每次复评冻结 Incident ID 和确定性父 Run ID，只更新 Agent 子 Run，确保修复前后使用同一 Snapshot。

修复前冻结基线：

```text
Finding Precision                 100%
Finding Recall                    100%
Target Accuracy                   100%
Replay Integrity                  100%
Agent Safe Validation             100%
Agent Model Output Acceptance      40%
Agent Useful Result Rate           40%
Agent Parent Fact Overlap         100%
```

闭环发现并修复：

- 安全回退被错误计为 Agent 契约成功；
- 所有百分号都被误判为根因概率，导致 OOM 指标百分比输出失败；
- Pydantic 契约与独立 Validator 百分比语义不一致；
- “证据不足以确认根因”被误判为确认性表达；
- “根因为/根因是”可绕过原确认性规则；
- 复评选择最新 follow-up Run，导致 Ground Truth 父 Run 漂移；
- 仅检查“是否存在强假设”不能衡量 Agent 是否命中真实故障机制。

最终 iteration3：

```text
Finding Precision                 100%
Finding Recall                    100%
Target Accuracy                   100%
Replay Integrity                  100%
Agent Safe Validation             100%
Agent Model Output Acceptance     100%
Agent Useful Result Rate          100%
Agent Ground Truth Match          100%
Agent Parent Fact Overlap         100%
Unsupported Hypothesis Rate         0%
TP / FP / FN / TN                4 / 0 / 0 / 4
```

最终报告：

```text
reports/accuracy/20260716T024243Z-frozen-baseline.{json,md}
reports/accuracy/20260716T024243Z-iteration3.{json,md}
```

已知剩余覆盖缺口：CrashLoop 当前能够在通用证据链识别 `BackOff`，但还没有独立的受控确定性 Finding 引擎。负对照因测试 Deployment 与告警时间接近，会产生真实 `rollout_preceded_incident`；它没有造成 OOM/CPU 误报，但后续应改为预热的长驻基线 Fixture，使负样本更纯净。

### 15.5 登录修复验收

```text
有效签名会话调用 /auth/me：200
携带有效旧会话提交错误密码：401
错误响应 Cache-Control：no-store
错误响应清除 aiops_session：是（Max-Age=0、HttpOnly）
清除后再次调用 /auth/me：401
未知用户名执行同等密码哈希校验路径：是
错误登录安全审计：已记录
```

前端登录页已精简为浅色单卡片布局，文字与输入框使用固定高对比色；已删除 0.7 遗留双栏网格规则，使用全屏 Flex 将品牌区、卡片和页脚作为一个整体精确居中。通用安全证书图标已替换为自定义 AIOps 节点网络 Logo；继续保留卡片内错误提示、错误后密码清空与自动聚焦、大写锁定提示、提交中防重复操作，以及动态版本/环境标识。

### 15.6 测试基线

```text
后端本地 discover：共 102 项，77 PASS，25 个隔离 PostgreSQL 用例按环境变量跳过
独立 Docker Network + PostgreSQL 17：65 PASS，0 skip
认证路由专项：4 PASS
前端 npm run build：PASS
Chromium 1440×900 几何渲染：卡片/品牌/登录组水平偏差 0px，登录组垂直偏差 0px，Logo 48×48，PASS
Python compileall：PASS
Kubernetes YAML client dry-run：PASS
真实 K8s 登录会话回归：PASS
历史全场景回归：PASS
```

隔离 PostgreSQL 测试必须使用独立数据库；测试会执行 `drop_all/create_all`，严禁指向开发数据库。

### 15.7 历史异常与保留证据

- Run 1 的旧 ToolResult/Finding 契约不一致是真实历史 `INVALID`，必须保留；
- Snapshot 1.2.0 的一个 `INVALID` 是修复前的历史回归证据；修复后的正式 Offline/Shadow Run 已为 `VALID`；
- 历史 Shadow Run #54 的 10 次模型调用均为 `SUCCEEDED`，最终失败来自当时输出契约禁止的概率百分比；旧记录保留原始降级码，前端根据审计错误兼容显示为“输出契约失败”；
- Accuracy Loop 原始 OOM Shadow #66/#68 因旧规则把指标百分比误判为概率而失败；修复后同 Snapshot 的 Offline #74/#75 与语义收紧后的 #78/#79 均为 `COMPLETED/VALID`；
- Offline #73 记录 Pydantic 与 Validator 百分比规则不一致，#76 记录否定语境“证据不足以确认根因”的误判；修复后的 CPU 负对照 #77 为 `COMPLETED/VALID`。这些失败证据必须保留；
- `VALID_WITH_WARNINGS` 主要来自目标未解析或历史降级，不得通过篡改历史数据“修绿”；
- Trace 当前关闭且未配置真实 Tempo/Jaeger，不能声称 Trace 已可用。

### 15.8 永久禁止事项

- 不得让 Agent 改写确定性 Finding 或父级 Diagnosis；
- 不得让模型确认 OOMKilled、CPU Spike 或具体代码根因；
- 不得开放自由 PromQL、LogQL 或任意查询表达式；
- 不得加入 Kubernetes 写工具、自动修复、自动扩缩容或删除/重启操作；
- 不得把 `UNAVAILABLE` 解释为没有异常；
- 不得删除 Run 1 或其他真实历史 INVALID；
- 不得输出 Secret、模型 Key、密码、Token 或数据库凭据；
- 不得在开发数据库运行破坏性测试；
- 不得把静态 UI 文案当成后端健康探针，健康状态以 `/healthz`、`/readyz` 和 Pod Ready 为准。

### 15.9 下一位 Agent 接管检查

接管时依次确认：Git 分支/Tag、ConfigMap Commit、Pod Ready、API health/ready、Web 200、队列无 pending/dead，以及 `reports/accuracy/20260716T024243Z-iteration3.md` 的冻结准确性 Gate 为 PASS。全库 Evaluation 含故意保留的失败实验，不能要求全库计数全绿。发现差异时先调查，不要重复部署或删除历史数据。

下一版本：`TBD`。在没有明确需求前，不要自行扩展 Agent 权限、工具范围或自动化修复能力。
