# AIOps 文档索引

| 文档 | 用途 | 是否实时权威 |
|---|---|---|
| [`CURRENT_HANDOFF.md`](CURRENT_HANDOFF.md) | 下一位 Agent 的接管入口、当前运行状态和近期风险 | 是 |
| [`AGENT_HANDOFF_STATE.json`](AGENT_HANDOFF_STATE.json) | 机器可读交接快照 | 是 |
| [`ENGINEERING_RULES.md`](ENGINEERING_RULES.md) | 开发、测试、部署、安全和完成定义 | 长期规范 |
| [`AGENT_HANDOFF.md`](AGENT_HANDOFF.md) | 完整架构、数据库、API、历史里程碑和背景 | 参考；实时段落可能过期 |
| [`ACCURACY_LOOP.md`](ACCURACY_LOOP.md) | Ground Truth 场景、Precision/Recall 和 Agent 效果闭环 | 方法权威 |
| [`../CHANGELOG.md`](../CHANGELOG.md) | 发布能力与维护记录 | 发布记录 |

接管阅读顺序：

```text
CURRENT_HANDOFF.md
→ ENGINEERING_RULES.md
→ AGENT_HANDOFF_STATE.json
→ 按任务查 AGENT_HANDOFF.md / ACCURACY_LOOP.md
```
