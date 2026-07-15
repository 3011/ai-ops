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
- React + TypeScript + Ant Design 事件列表、详情、分析和证据展示
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

默认 `LLM_API_KEY` 为空，Worker 只生成证据报告，不会伪造 AI 根因。

设置兼容 OpenAI Chat Completions 的 API Key：

```bash
kubectl patch secret aiops-secrets -n aiops-dev --type merge \
  -p '{"stringData":{"LLM_API_KEY":"替换为实际Key"}}'

kubectl rollout restart deployment/aiops-worker -n aiops-dev
```

模型地址和模型名位于 `deploy/dev/00-base.yaml`：

```text
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
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
