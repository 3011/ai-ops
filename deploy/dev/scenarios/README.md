# AIOps 场景测试

该目录只用于 `aiops-dev`。测试告警均携带 `aiops_test=true`，默认不会进入生产视图统计。

覆盖场景：

- CrashLoopBackOff：真实 Deployment + PrometheusRule + Alertmanager；
- OOMKilled：真实内存受限容器 + PrometheusRule + Alertmanager；
- Node-only：仅提供 Node 标签，验证自动节点发现；
- Sparse labels：缺少 namespace/service，验证降级与缺口说明；
- HTTP 5xx/延迟：验证 DeepSeek 受限动态 PromQL/LogQL 规划；
- Fingerprint 生命周期：resolved 使用不同 startsAt，验证旧 firing 不会卡住事件；
- firing/resolved、Kubernetes Events、previous logs、原始 PromQL、延迟补充分析、死信检查和测试数据隔离。

```bash
bash deploy/dev/scenarios/run.sh
# 等待 Prometheus/Alertmanager 和 AI 分析完成
python3 deploy/dev/scenarios/validate.py
bash deploy/dev/scenarios/cleanup.sh
```
