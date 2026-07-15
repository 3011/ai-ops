# AIOps Console

源码目录：`k8s-cp01:/root/aiops-console`。开发服务运行在 Kubernetes `aiops-dev` 命名空间；API、Worker 和 React 通过 `hostPath` 挂载源码，PostgreSQL 使用 PVC。

- Web：`http://172.30.10.11:30300`
- API 文档：`http://172.30.10.11:30801/docs`
- 当前 API：`0.5.1`

## 核心链路

```text
Prometheus → Alertmanager → FastAPI webhook
                          → PostgreSQL webhook/alert/incident/outbox
                          → Worker 自动发现 Kubernetes 目标
                          → Kubernetes API / Events / current+previous logs
                          → 原始告警 PromQL + 通用 Prometheus/Loki 证据
                          → DeepSeek 受限动态 PromQL/LogQL 规划
                          → 安全校验与只读执行
                          → 结构化根因假设、覆盖度和缺口说明
```

## 当前能力

- `webhook_deliveries → alert_instances → incidents` 三层模型；
- firing/resolved 幂等和同 fingerprint 跨 startsAt 生命周期收敛；
- PostgreSQL Outbox、`FOR UPDATE SKIP LOCKED`、重试、死信和锁回收；
- 自动发现 Pod、Deployment、StatefulSet、DaemonSet、Service 和 Node；
- 自动采集容器状态、Ready、重启、退出码、OOMKilled、镜像和 Kubernetes Events；
- 自动读取当前与 previous 容器日志，Loki 延迟或短生命周期容器也能取证；
- 从 Alertmanager `generatorURL` 解析原始 PromQL；
- 自动查询 CPU、内存、重启、Ready、OOM、网络、CPU throttling、Node 和 target up；
- DeepSeek 动态规划最多 4 条 PromQL 和 2 条 LogQL；
- 动态查询必须命中事件作用域，LogQL 仅允许 namespace 内简单行过滤；
- 初次告警后自动安排完整 30 分钟观察窗口的补充分析；
- 证据、查询、响应摘要、耗时和错误全部持久化；
- React 工作台：总览、事件、告警、Webhook、任务、模型设置、证据覆盖度和动态计划；
- 生产视图默认隐藏 `aiops_test=true` 测试数据，可通过页面开关查看。

## “无需告警模板”的边界

平台不要求为每个告警名称手工维护 PromQL/LogQL 模板。它采用：

1. 确定性目标发现；
2. 通用只读证据采集；
3. 原始告警表达式复用；
4. LLM 受限动态查询规划；
5. 安全校验和预算控制；
6. LLM 最终假设与人工确认。

这能覆盖大量 Kubernetes、Node、容器和具备指标/日志的应用故障，但不能保证任何故障都得到唯一根因。缺少业务指标、外部托管服务数据、变更记录、Trace 或领域语义时，平台会降低覆盖度并明确列出缺口，而不是虚构结论。

## 模型设置

进入前端 `设置`，可维护 OpenAI-compatible Base URL、模型、API Key 和启用状态。API Key 使用 Fernet 加密存入 PostgreSQL，前端不回显明文；Worker 每次任务读取最新配置，无需重启。

当前验证模型：`deepseek-v4-flash`。

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

覆盖 CrashLoopBackOff、OOMKilled、Node-only、缺失标签降级、HTTP 动态查询规划、不同 startsAt 的 fingerprint 生命周期、测试数据隔离和无死信任务。

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
- 正式开放前仍需补充登录、RBAC、审计和管理员权限控制。
