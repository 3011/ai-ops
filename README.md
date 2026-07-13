# AIOps Console MVP

开发源码目标目录：`k8s-cp01:/root/aiops-console`。开发服务运行在 Kubernetes `aiops-dev` 命名空间，API、Worker、React 开发容器通过 hostPath 挂载源码并固定到 `k8s-cp01`；PostgreSQL 使用 PVC。

## 当前实现

- FastAPI Alertmanager webhook
- `webhook_deliveries → alert_instances → incidents` 三层模型
- firing/resolved 生命周期与重复投递幂等
- PostgreSQL Outbox、`FOR UPDATE SKIP LOCKED`、重试和死信
- React + TypeScript + Ant Design 事件列表和详情
- `/healthz`、`/readyz`、`/metrics`
- Prometheus/Loki 连接配置已预置；证据采集和 LLM 分析在下一迭代启用

## 部署

```bash
cd /root/aiops-console
bash deploy/dev/deploy.sh
```

访问：

- Web: `http://172.30.10.11:30300`
- API docs: `http://172.30.10.11:30800/docs`

测试：

```bash
curl -sS -X POST -H 'Content-Type: application/json' \
  --data-binary @deploy/dev/test-firing.json \
  http://172.30.10.11:30800/api/v1/webhooks/alertmanager

curl -sS http://172.30.10.11:30800/api/v1/incidents

curl -sS -X POST -H 'Content-Type: application/json' \
  --data-binary @deploy/dev/test-resolved.json \
  http://172.30.10.11:30800/api/v1/webhooks/alertmanager
```

查看日志：

```bash
kubectl logs -n aiops-dev deploy/aiops-api -f
kubectl logs -n aiops-dev deploy/aiops-worker -f
kubectl logs -n aiops-dev deploy/aiops-web -f
```
