# AIOps Console Agent 交接手册

> 交接快照时间：2026-07-15 22:49:03 +08:00  
> 项目：Work's K8s / AIOps Console  
> 当前开发版本：`0.9.0-dev.4`  
> 当前开发分支：`release/0.9.0`  
> 当前部署的应用基线 Commit：`8f785abb75d20e11c4273d4f3098a0acb152befc`  
> 本交接文档提交后，分支 HEAD 会多一笔 docs-only Commit；以 `git log -1` 为准

本文档是后续 Agent 的首要上下文。开始任何修改前，先阅读本文件、`README.md`、`CHANGELOG.md`，再检查实时集群状态。不要仅依赖聊天历史。

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
本地/远端 HEAD：8f785abb75d20e11c4273d4f3098a0acb152befc
API 版本：0.9.0-dev.4
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
release/0.9.0 应用基线：8f785ab（0.9.0-dev.4）
release/0.9.0 分支 HEAD：可能比应用基线多一笔 docs-only 交接提交
```

不要把 `release/0.9.0` 未完成的 Agent 代码提前合并到 `main`。

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

最后核验：2026-07-15 22:49 +08:00。

```text
aiops-api       1/1 Running
aiops-worker    1/1 Running
aiops-web       1/1 Running
postgresql-0    1/1 Running

APP_VERSION：0.9.0-dev.4
GIT_COMMIT：8f785abb75d20e11c4273d4f3098a0acb152befc
模型：enabled=true
Trace：enabled=false，未配置真实 Tempo/Jaeger
Pending jobs：0
Dead jobs：0
临时测试资源：0
```

数据库当前概要：

```text
用户：2
  admin（admin）
  ljx（admin）

角色：4
  admin / operator / viewer（系统角色）
  sss（自定义角色）

权限：11
事件：17，全部 resolved
变更事件：4
Outbox：succeeded 39，skipped 8
可信调查 Run：15，全部 COMPLETED_PARTIAL
Snapshot 1.1.0：VALID 12，VALID_WITH_WARNINGS 2，INVALID 1
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
→ DiagnosisResult
→ Snapshot Replay
→ Result Validator
```

特点：

- 不依赖模型确认事实；
- 目标固定到 Pod UID、Container、Workload UID 和时间窗口；
- 所有事实引用真实 ToolExecution；
- Agent 尚未启用；
- 状态通常为 `COMPLETED_PARTIAL`，停止原因 `AGENT_NOT_ENABLED`。

未来 Agent 只能建立在第二条链路上，不允许直接复用旧 DeepSeek Prompt 作为 Agent Runtime。

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

认证：HttpOnly 签名 Cookie，会话默认 8 小时。密码使用 PBKDF2-HMAC-SHA256 加盐哈希。首次管理员凭据只存在 Kubernetes Secret。

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
GET  /api/v1/investigations/{run_id}/replay
POST /api/v1/investigations/{run_id}/replay
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

当前预期：

```text
69 tests
OK
13 个 PostgreSQL 用例按设计 skipped
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
/root/aiops-console/.git HEAD
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
```

`d921260` 前的空格只是本文排版；真实 Commit 为 `d921260`。

---

## 15. 下一阶段：0.9.0-dev.5

当前立即任务不是实时 Agent，也不是 PydanticAI 工具循环，而是：

```text
InvestigationAgent Protocol
Agent 输入/输出契约
investigation_model_invocations
模型请求/响应 Artifact 审计
```

### 15.1 框架无关接口

建议目录：

```text
backend/app/investigation/agents/
├── protocol.py
├── contracts.py
├── prompts.py
└── pydantic_ai_adapter.py       # dev.5 可先不实现，或仅空适配边界
```

除 adapter 文件外，禁止 import PydanticAI。

接口：

```python
class InvestigationAgent(Protocol):
    async def investigate(
        self,
        context: InvestigationContext,
        tools: ToolRegistry,
        budget: InvestigationBudget,
    ) -> DiagnosisOutput:
        ...
```

`InvestigationContext` 至少包含：

```text
analysis_run_id
incident_summary
target_context
initial_finding_ids
available_tools
budget_snapshot
investigation_mode: oom | cpu
```

### 15.2 输出契约

```text
DiagnosisOutput
├── summary
├── fact_refs
├── hypotheses
├── missing_evidence
├── recommended_checks
└── risk_notes
```

Hypothesis 支持等级只能是：

```text
highly_supported
partially_supported
insufficient_evidence
contradicted
```

禁止：

```text
confirmed
root_cause_confirmed
概率百分比
自由 PromQL / LogQL
写操作
```

### 15.3 ModelInvocation 表

建议新增迁移 `0008_model_invocation_audit.sql`：

```text
investigation_model_invocations
id
analysis_run_id
sequence_number
invocation_type
runtime_name
runtime_version
provider
model
model_parameters_json
prompt_version
request_snapshot_uri
request_hash
response_snapshot_uri
response_hash
status
input_tokens
output_tokens
latency_ms
error_code
error_message
started_at
completed_at
```

调用类型：

```text
investigation_step
final_diagnosis
schema_repair
offline_replay
```

框架 Trace 不能代替业务审计表。

### 15.4 dev.5 边界

本提交仍不得：

- 修改 Worker 实时调查流程；
- 创建 Agent Shadow Run；
- 让模型调用真实工具；
- 替换确定性 Diagnosis；
- 引入自由查询；
- 把 Agent 结果展示给普通运维用户。

验收重点：纯契约、数据库审计、Artifact 脱敏、模型失败审计、框架依赖隔离。

---

## 16. 0.9.0 后续路线

```text
0.9.0-dev.5
Agent Protocol + ModelInvocation audit

0.9.0-dev.6
Offline Snapshot Agent Replay + extended validator

0.9.0-dev.7
Independent real-time Agent Shadow Run + comparison UI

0.9.0-rc.1
OOM/CPU eval suite + security regression

0.9.0
Default investigation_mode=shadow
```

### 16.1 离线 Agent Replay

```text
Snapshot
→ Agent
→ SnapshotToolRuntime
→ 已保存 ToolResult
→ DiagnosisOutput
→ Agent Validator
```

Agent 请求快照中不存在的工具时，只返回 `SNAPSHOT_TOOL_NOT_AVAILABLE`，不得访问实时数据源。

### 16.2 实时 Shadow

确定性与 Agent 使用独立 Run：

```text
Run N   deterministic_cpu_v2
Run N+1 agent_cpu_shadow_v1, parent_run_id=N
```

不要共享预算账本。Agent INVALID 时不得进入正式 DiagnosisResult。

### 16.3 发布安全门槛

```text
越权工具调用：0
未注册工具执行：0
虚构 Finding 引用：0
confirmed 假设：0
日志 Prompt Injection 导致行为改变：0
Unsupported Hypothesis Rate：0
OOM 硬事实一致率：100%
CPU 目标 Pod/Revision 一致率：>=95%
模型不可用不影响确定性 Run
离线 Replay 不访问外部数据源
```

行为目标：

```text
Useful Tool Call Rate >=60%
Duplicate Tool Call Rate <=10%
重要假设反证检查率 >=80%
预算耗尽率 <=10%
```

---

## 17. 绝对不要做的事情

- 不要直接让 Agent 进入实时 Worker。
- 不要让模型决定 OOMKilled 或 CPU Spike 是否发生。
- 不要修改 DeepSeek 旧 Prompt 代替 Agent Runtime。
- 不要把 ToolStatus.UNAVAILABLE 写成“未发现异常”。
- 不要把 rollout 时间相关性写成发布导致故障。
- 不要让日志文本产生 confirmed Finding。
- 不要开放自由 PromQL/LogQL。
- 不要新增 Kubernetes 写权限或自动修复。
- 不要读取或输出 Secret 明文。
- 不要删除 Run 1 的真实 INVALID 历史。
- 不要在开发数据库运行 drop_all 测试。
- 不要在未核对 live 状态时重复执行 Pod 删除或部署。

---

## 18. 交接完成定义

新 Agent 在开始开发前应能回答：

1. 旧 DeepSeek 分析和可信确定性调查有什么区别？
2. 为什么 Agent 不能确认 OOMKilled？
3. 九个工具在哪里注册？
4. Tool Runtime 如何处理预算、缓存和 Artifact？
5. Run input 为什么必须 `native_frozen`？
6. 为什么 Run 1 必须保持 INVALID？
7. 当前真实 Trace 后端是否存在？
8. 如何在不暴露 Secret 的情况下部署？
9. 为什么必须逐个重启 Pod？
10. dev.5 为什么仍不允许实时 Agent？

能准确回答后，再开始 `0.9.0-dev.5`。

---

## 19. 0.9.0 最终完成状态（2026-07-15）

本节取代第 15～18 节中针对 dev.5/dev.6/dev.7 的阶段性“禁止进入下一 Gate”说明。那些限制用于逐阶段开发；最终 0.9.0 已按独立 Shadow 架构完成，但下列永久安全边界仍然有效。

### 19.1 已完成能力

```text
0.9.0-dev.5  Agent Protocol + ModelInvocation Artifact audit
0.9.0-dev.6  Offline Snapshot Agent Replay + Validator 1.2.0
0.9.0-dev.7  Independent real-time Agent Shadow + comparison UI
0.9.0-rc.1   OOM/CPU eval + security regression
0.9.0        investigation_mode=shadow
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
POST /api/v1/investigations/{run_id}/agent-replay
POST /api/v1/investigations/{run_id}/agent-shadow
GET  /api/v1/investigations/{run_id}/comparison
POST /api/v1/investigations/{run_id}/evaluate
GET  /api/v1/investigation-evaluations/summary
```

### 19.2 最终权威边界

- 确定性 Run 和 Finding 仍是 OOMKilled、CPU Spike 等硬事实的唯一权威来源；
- Agent 使用独立子 Run、独立预算和独立 Diagnosis，不覆盖父级；
- 模型失败只使子 Run 失败，Worker 的确定性完成状态不变；
- Agent Validator 为 `INVALID` 时不创建正式 `InvestigationDiagnosisResult`；
- Offline Replay 只消费 Snapshot 的模型可见 ToolResult，外部数据源访问数必须为 0；
- Agent 只能选择九个注册只读工具，不允许自由 PromQL/LogQL，也没有 Kubernetes 写权限；
- 日志与告警文本始终是不可信输入，不能单独把假设提升为 `highly_supported`；
- 模型请求/响应必须保存脱敏 Artifact、Hash 和业务审计；框架 Trace 不能替代该审计。

### 19.3 发布门槛

安全门槛：

```text
未注册工具执行                     0
虚构 Finding 引用                  0
confirmed / 概率表达               0
日志 Prompt Injection 行为改变     0
Unsupported Hypothesis Rate        0
OOM 硬事实一致率                  100%
CPU Target Identity 一致率        >=95%
模型失败影响父 Run                 0
Offline Replay 外部数据源访问      0
```

效果目标：

```text
Useful Tool Call Rate             >=60%
Duplicate Tool Call Rate          <=10%
重要假设反证检查率                 >=80%
预算耗尽率                         <=10%
```

### 19.4 测试基线

```text
后端 unittest：88 / 88 PASS（隔离 PostgreSQL）
前端 npm run build：PASS
Python compileall：PASS
SQLAlchemy PostgreSQL DDL：PASS
FastAPI OpenAPI：PASS
```

### 19.5 永久禁止事项

- 不得让 Agent 改写确定性 Finding 或父级 Diagnosis；
- 不得让模型确认 OOMKilled、CPU Spike 或具体代码根因；
- 不得开放自由 PromQL、LogQL 或任意查询表达式；
- 不得加入 Kubernetes 写工具、自动修复、自动扩缩容或删除/重启操作；
- 不得把 `UNAVAILABLE` 解释为没有异常；
- 不得删除 Run 1 的真实 legacy INVALID；
- 不得输出 Secret、模型 Key、密码、Token 或数据库凭据。
