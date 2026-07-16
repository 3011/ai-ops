# AIOps 场景测试

该目录只用于 `aiops-dev`。测试告警均携带 `aiops_test=true`，默认不会进入生产视图统计。

覆盖场景：

- CrashLoopBackOff：真实 Deployment + PrometheusRule + Alertmanager；
- OOMKilled：真实内存受限容器 + PrometheusRule + Alertmanager；
- Deployment rollout：revision 1→2、ReplicaSet 与镜像差异；
- ConfigMap：引用关系、resourceVersion、managedFields 时间；
- CI/CD：GitLab 风格发布事件、token 校验、幂等和开放事件自动重分析；
- Trace：Tempo 兼容 mock、连接测试、服务作用域查询和证据引用；
- Node-only：仅提供 Node 标签，验证自动节点发现；
- Sparse labels：缺少 namespace/service，验证降级与缺口说明；
- HTTP 5xx/延迟：验证 DeepSeek 受限动态 PromQL/LogQL 规划；
- Fingerprint 生命周期：resolved 使用不同 startsAt，验证旧 firing 不会卡住事件。

```bash
bash deploy/dev/scenarios/run.sh
# 等待 Prometheus/Alertmanager 和 AI 分析完成
python3 deploy/dev/scenarios/validate.py
bash deploy/dev/scenarios/cleanup.sh
```

`cleanup.sh` 会删除所有测试工作负载、停用 Trace mock，但保留数据库中的测试事件与证据，便于回归审计。

## Ground Truth 准确性闭环

核心准确性回归使用真实 OOM、采样 OOM、CPU Spike、CrashLoop，以及 OOM/CPU 负对照，输出 Finding Precision/Recall、目标定位、Replay 和 Agent 效果报告：

```bash
bash /root/aiops-console/deploy/dev/scenarios/run_accuracy_loop.sh
```

规范和指标定义见 `docs/ACCURACY_LOOP.md`。
