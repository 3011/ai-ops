# AIOps Console 当前交接

> 权威快照：2026-07-28 09:28 +02:00（Europe/Amsterdam）  
> 项目：Work's K8s / AIOps Console  
> 当前版本：`0.9.0`  
> 开发分支：`release/0.9.0`  
> 当前运行应用 Commit：`6877ec653e164baa1ff1626140773fe05edefd3f`

本文档只描述**当前可操作状态**。长期工程约束见 [`ENGINEERING_RULES.md`](ENGINEERING_RULES.md)，完整架构与历史背景见 [`AGENT_HANDOFF.md`](AGENT_HANDOFF.md)，机器可读快照见 [`AGENT_HANDOFF_STATE.json`](AGENT_HANDOFF_STATE.json)。

## 1. 接管顺序

下一位 Agent 开始修改前，按顺序执行：

```bash
cd /root/ai-ops
git checkout release/0.9.0
git pull --ff-only origin release/0.9.0
git status --short --branch
git log -5 --oneline --decorate

ssh k8s 'kubectl get deploy,pod,svc -n aiops-dev -o wide'
ssh k8s 'kubectl get configmap aiops-config -n aiops-dev \
  -o jsonpath="APP_VERSION={.data.APP_VERSION}{\"\\n\"}GIT_COMMIT={.data.GIT_COMMIT}{\"\\n\"}INVESTIGATION_MODE={.data.INVESTIGATION_MODE}{\"\\n\"}"'

curl -fsS http://172.30.10.11:30801/healthz
curl -fsS http://172.30.10.11:30801/readyz
curl -fsS -o /dev/null -w '%{http_code}\n' http://172.30.10.11:30300/
```

预期：

```text
branch                   release/0.9.0
APP_VERSION              0.9.0
runtime GIT_COMMIT       6877ec653e164baa1ff1626140773fe05edefd3f
INVESTIGATION_MODE       shadow
aiops-api                1/1 Ready
aiops-worker             1/1 Ready
aiops-web                1/1 Ready
postgresql-0             1/1 Ready
healthz                  200 {"status":"ok"}
readyz                   200 {"status":"ready"}
web                      HTTP 200
```

Git 分支 HEAD 可能因文档提交晚于运行应用 Commit。判断代码是否漂移时，必须同时比较：

1. GitHub `release/0.9.0`；
2. `/root/aiops-console` 受控源码；
3. ConfigMap `GIT_COMMIT`；
4. API/Worker Pod 环境变量 `GIT_COMMIT`；
5. `release_notes.commit_sha`。

不要只根据 Tag、聊天记录或文件修改时间判断当前版本。

## 2. 环境与入口

```text
开发工作副本       Exec_MCP:/root/ai-ops
运行源码           k8s-cp01:/root/aiops-console
SSH alias          k8s
Kubernetes         aiops-dev
Web                http://172.30.10.11:30300
API                http://172.30.10.11:30801
API Docs           http://172.30.10.11:30801/docs
```

运行方式是开发环境模式：

- API、Worker 使用 `hostPath` 挂载 `backend`；
- Web 使用 `hostPath` 挂载 `frontend`，运行 Vite dev server；
- API 使用 `uvicorn --reload`，Worker不会热重载；
- Python 与 Node 依赖在 Pod InitContainer 中安装到 `emptyDir`；
- PostgreSQL 使用 5 Gi PVC。

这不是不可变镜像发布形态。任何涉及准确性、Replay 或版本审计的判断，都要先确认源码与 Commit 一致。

## 3. 当前运行状态

最后核验：2026-07-28 09:28 +02:00。

```text
组件                      状态
aiops-api                 1/1 Running
aiops-worker              1/1 Running
aiops-web                 1/1 Running
postgresql-0              1/1 Running

APP_VERSION               0.9.0
GIT_COMMIT                6877ec653e164baa1ff1626140773fe05edefd3f
INVESTIGATION_MODE        shadow
LLM_MODEL                 deepseek-v4-flash
可信工具数                9
测试资源                  0
最近 30 分钟后端错误      0
```

数据库概要：

```text
用户 / 角色 / 权限        2 / 4 / 11
Incident                  37，全部 resolved
ChangeEvent               14
Outbox                    succeeded 93，skipped 23
Outbox pending/retry/dead 0
ModelInvocation           SUCCEEDED 244
```

历史数据包含故意保留的失败 Run、旧 Snapshot 和准确性迭代，不能通过删除历史数据“修绿”。当前发布门槛应看冻结报告：

```text
reports/accuracy/20260716T024243Z-iteration3.md
```

## 4. 当前系统定位

平台包含两条并行链路。

### 4.1 兼容分析链路

```text
Alertmanager → Incident / Outbox → Kubernetes / Prometheus / Loki / Change / Trace
→ 受限动态查询 Planner → LLM 结构化假设
```

它用于通用证据收集和假设生成，不是硬事实权威。

### 4.2 可信调查链路

```text
Incident → frozen Run input → TargetContext Resolver
→ ToolRegistry / ToolRuntime → ToolExecution
→ DeterministicFinding → deterministic Diagnosis
→ Agent Shadow / Offline child Run → Validator / Evaluation
```

权威边界：

- OOMKilled、CPU Spike 等硬事实只来自确定性 Finding；
- Agent 只能引用父级事实，不得改写父 Run 或 Finding；
- Agent 失败不影响确定性调查完成状态；
- Offline Replay 只读 Snapshot，不访问实时数据源；
- 日志和告警文本始终是不可信输入；
- 系统不自动修复、重启、删除、扩缩容或修改业务资源。

## 5. 可信工具目录

当前 `TOOL_CATALOG_VERSION=0.9.0`，共 9 个只读工具：

```text
get_container_termination_status
get_memory_usage_vs_limit
get_cpu_usage_vs_request_limit
get_cpu_throttling
get_container_restart_history
get_recent_rollouts
search_container_logs
compare_cpu_across_replicas
get_application_red_metrics
```

当前没有 `get_external_db_metrics`，也没有独立 `deployment_cpu` 引擎。2026-07-28 曾发现这两项未提交实验改动，因缺少可靠依赖映射、测试失败且与现有 CPU 调查重复，已从运行环境移除。

新增可信工具前必须同时具备：

1. 明确且可验证的目标绑定；
2. 有界只读数据访问；
3. ToolResult 与错误语义；
4. Finding Parser 与确认规则；
5. Replay 白名单；
6. 正负 Ground Truth 场景；
7. 单元、持久化和场景回归。

缺任何一项，不得注册为可信工具。

## 6. 关键版本契约

```text
Tool catalog             0.9.0
Replay Snapshot schema   1.2.0
Replay Validator         1.2.0
Agent Runtime            1.1.0
Agent Model Runtime      1.1.0
Agent Validator          1.2.0
Agent Prompt             agent-investigation-v5
```

关键文件：

```text
backend/app/investigation/contracts.py
backend/app/investigation/catalog.py
backend/app/investigation/service.py
backend/app/investigation/tool_runtime.py
backend/app/investigation/replay.py
backend/app/investigation/agents/
backend/app/worker.py
backend/app/models.py
```

## 7. 仓库结构

```text
backend/app/                 FastAPI、Worker、认证、可信调查
backend/migrations/          顺序 SQL 迁移
backend/tests/               单元与隔离 PostgreSQL 测试
frontend/src/                React 19 + Ant Design 5
frontend/package.json        前端依赖和构建命令
deploy/dev/                  开发集群清单与部署脚本
deploy/dev/scenarios/        场景回归和准确性闭环
docs/                        当前交接、规则、架构和准确性说明
reports/accuracy/            冻结准确性报告
```

## 8. 修改流程

1. 只在 `/root/ai-ops` 开发；不要直接编辑 `/root/aiops-console`。
2. 修改前确认工作区干净，阅读相关契约和测试。
3. 优先修复已有抽象；不要为单一现象新增平行引擎。
4. 运行最小专项测试，再运行完整后端回归和前端构建。
5. 提交并 push 到 `release/0.9.0`。
6. 备份运行目录到 `/root/aiops-backups`。
7. 仅同步已提交的受控文件，不同步脏工作区、`node_modules`、`dist` 或临时报告。
8. 校验本地与运行目录的受控文件 Manifest。
9. 使用明确 Commit 部署：

```bash
ssh k8s 'cd /root/aiops-console && \
  GIT_COMMIT=<FULL_COMMIT_SHA> bash deploy/dev/deploy.sh'
```

10. 部署后核对 Pod Ready、HTTP、Pod 环境变量、工具目录、队列和日志。

`deploy.sh` 已显式重启 API 与 Worker，避免 hostPath 更新后 Worker 仍运行旧代码。不要在 SSH 超时后并发重复执行部署；先查看实际 Pod 和 Deployment 状态。

## 9. 测试基线

2026-07-28 重新验证：

```text
后端 unittest discover       102 项；77 PASS；25 isolated PostgreSQL skipped
前端 npm run build           PASS
Ant Design chunk             988.88 kB，Vite warning，非失败
```

后端常规回归：

```bash
ssh k8s 'kubectl exec -n aiops-dev deploy/aiops-worker -- \
  sh -ec "PYTHONPATH=/deps:/workspace python -m unittest discover -s /workspace/tests -v"'
```

前端：

```bash
cd /root/ai-ops/frontend
npm ci
npm run build
```

隔离 PostgreSQL 测试会执行建表/删表，必须设置独立 `AIOPS_TEST_DATABASE_URL`，严禁指向 `aiops-dev` 开发数据库。

完整场景：

```bash
ssh k8s 'cd /root/aiops-console && bash deploy/dev/scenarios/run.sh'
ssh k8s 'cd /root/aiops-console && python3 deploy/dev/scenarios/validate.py'
ssh k8s 'cd /root/aiops-console && bash deploy/dev/scenarios/cleanup.sh'
```

准确性闭环：

```bash
ssh k8s 'cd /root/aiops-console && bash deploy/dev/scenarios/run_accuracy_loop.sh'
```

## 10. 已知边界与下一优先级

当前明确缺口：

1. CrashLoop 有通用 BackOff 证据，但没有独立确定性 Finding 引擎；
2. 准确性负对照应改为预热长驻 Fixture，减少真实 rollout 时间相关性；
3. 前端 Ant Design chunk 较大，可在有真实性能需求时做路由懒加载；
4. 开发部署仍是 hostPath + dev server，不具备不可变制品可重复性；
5. Trace 代码支持 Tempo/Jaeger，但当前没有启用真实 Trace 后端；
6. 公网生产化仍缺 HTTPS、SSO/OIDC、CSRF Token 和更严格会话策略。

下一版本仍为 `TBD`。没有明确需求时，不要自行扩大 Agent 权限、工具范围或自动修复能力。

## 11. 永久保留与禁止事项

- 保留 Run 1 及其他真实历史 `INVALID`；
- 保留失败的准确性迭代和模型审计；
- 不回填或改写历史 frozen Run input；
- 不把 `UNAVAILABLE` 或无样本解释为“正常”；
- 不读取或输出 Secret 值；
- 不在开发数据库运行破坏性测试；
- 不加入 Kubernetes 写工具；
- 不允许模型输出确认性根因或改写确定性事实；
- 健康状态以 Pod Ready、`/healthz` 和 `/readyz` 为准，不以静态 UI 文案为准。

## 12. 最近一次收敛记录

2026-07-28 完成：

- 删除未提交的 External DB 与重复 Deployment CPU 实验路线；
- 恢复 9 工具可信目录；
- 收紧中文自然语言输出要求，同时允许技术标识符保留原文；
- 修复部署 Commit 注入；
- 修复 hostPath 部署后 API/Worker 未显式重启；
- 清除 API Deployment 中无资源配额的残留 sidecar；
- 恢复 Git、运行源码、Pod 环境和数据库版本记录一致。

对应应用提交：

```text
7be1a6d  Constrain Chinese diagnostic output and deployment revision
6877ec6  Restart backend workloads after hostPath deployment
```

部署前实验源码备份保存在 K8s 节点：

```text
/root/aiops-backups/aiops-console-pre-7be1a6d-20260728T050648Z.tar.gz
```
