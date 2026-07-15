# AIOps Console MVP

开发源码目录：`k8s-cp01:/root/aiops-console`。

开发服务运行在 Kubernetes `aiops-dev` 命名空间。API、Worker、React 开发容器通过 `hostPath` 挂载源码并固定到 `k8s-cp01`；PostgreSQL 使用独立 PVC，不把数据库数据写入 `/root`。

## 当前实现

- FastAPI Alertmanager webhook
- `webhook_deliveries → alert_instances → incidents` 三层模型
- firing/resolved 生命周期和重复投递幂等
- PostgreSQL Outbox、`FOR UPDATE SKIP LOCKED`、超时锁回收、重试和死信
- 围绕告警 `startsAt` 查询 Prometheus 和 Loki
- 保存 PromQL、LogQL、查询时间窗、摘要、耗时和错误
- OpenAI-compatible 结构化 LLM 分析；无 Key 时自动降级为确定性证据报告
- 日志样本脱敏、Prompt Injection 隔离和高风险操作过滤
- React + TypeScript + Ant Design 运维工作台
- 运维总览、事件中心、原始告警、Webhook 投递、分析任务和模型设置
- 事件状态/级别/命名空间/服务筛选
- 数据源健康检查、24 小时事件趋势和高频服务统计
- Prometheus 指标曲线、Loki 日志摘要、AI 根因假设和分析历史
- 手工“重新分析”功能
- `/healthz`、`/readyz`、`/metrics`

## 部署

```bash
cd /root/aiops-console
bash deploy/dev/deploy.sh
```

访问：

- Web: `http://172.30.10.11:30300`
- API docs: `http://172.30.10.11:30801/docs`


## 控制台页面

- `/dashboard`：运维总览、事件趋势、数据源健康和最近事件
- `/incidents`：聚合事件中心和多条件筛选
- `/alerts`：原始告警实例生命周期
- `/deliveries`：Alertmanager Webhook 投递审计
- `/jobs`：Outbox 分析任务和重试状态
- `/settings/model`：模型 API 设置

## 测试告警生命周期

```bash
curl -sS -X POST -H 'Content-Type: application/json' \
  --data-binary @deploy/dev/test-firing.json \
  http://172.30.10.11:30801/api/v1/webhooks/alertmanager

curl -sS http://172.30.10.11:30801/api/v1/incidents

curl -sS -X POST -H 'Content-Type: application/json' \
  --data-binary @deploy/dev/test-resolved.json \
  http://172.30.10.11:30801/api/v1/webhooks/alertmanager
```

重新采集证据并分析：

```bash
curl -sS -X POST \
  http://172.30.10.11:30801/api/v1/incidents/1/reanalyze
```

## 配置 LLM

打开前端菜单 `设置 → 模型设置`，可以维护：

- OpenAI-compatible API Base URL
- 模型名称
- API Key
- AI 分析启用状态
- API 连通性测试

API Key 使用 Fernet 加密后保存到 PostgreSQL，页面不会回显明文。Worker 每次分析任务都会读取最新配置，无需修改 YAML 或重启。

当前推荐配置：

```text
Base URL: https://api.deepseek.com
Model: deepseek-v4-flash
```

## 查看日志

```bash
kubectl logs -n aiops-dev deployment/aiops-api -f
kubectl logs -n aiops-dev deployment/aiops-worker -f
kubectl logs -n aiops-dev deployment/aiops-web -f
```

## Alertmanager 实际接入

`deploy/dev/50-alertmanager-config.yaml` 已将 Alertmanager 接到集群内地址：

```text
http://aiops-api.aiops-dev.svc:8000/api/v1/webhooks/alertmanager
```

开发阶段使用显式准入机制。告警必须同时满足：

```yaml
labels:
  namespace: aiops-dev
  aiops_enabled: "true"
```

才会被 `AlertmanagerConfig` 路由到 AIOps 控制台。其他命名空间和未标记告警不受影响。

真实链路测试：

```bash
kubectl apply -f deploy/dev/test-alert-rule.yaml

# 查看事件进入控制台后删除测试规则
kubectl delete -f deploy/dev/test-alert-rule.yaml
```

测试链路为：

```text
PrometheusRule → Prometheus → Alertmanager → AIOps API → PostgreSQL Outbox → Worker
```


## 模型设置

前端菜单进入 `设置 → 模型设置`，可维护：

- OpenAI-compatible API Base URL
- 模型名称
- API Key
- AI 分析启用状态
- API 连通性测试

API Key 使用 `SETTINGS_ENCRYPTION_KEY` 通过 Fernet 加密后存入 PostgreSQL，前端只显示是否已配置，不回显明文。Worker 每次分析时读取最新数据库配置，无需重启。
