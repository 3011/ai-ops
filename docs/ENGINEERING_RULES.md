# AIOps Console 工程规范

本文档定义长期工程规则。当前运行事实以 [`CURRENT_HANDOFF.md`](CURRENT_HANDOFF.md) 为准。

## 1. 事实来源优先级

发生冲突时，按以下顺序判断：

1. 运行集群实时状态和数据库只读查询；
2. Git 当前分支受控代码与测试；
3. `CURRENT_HANDOFF.md` 和 `AGENT_HANDOFF_STATE.json`；
4. `AGENT_HANDOFF.md` 的架构与历史说明；
5. 聊天记录、旧报告和文件时间戳。

任何部署结论都必须同时记录完整 Git SHA，不使用“最新代码”“应该已经部署”等模糊描述。

## 2. 开发原则

- **最小充分实现**：先修现有抽象和契约，不创建重复路线。
- **确定性优先**：硬事实由代码规则与持久化 Finding 产生，模型只生成假设。
- **显式边界**：Target、时间窗、namespace、预算、错误语义和数据来源必须明确。
- **失败可审计**：模型失败、数据源失败、契约失败和目标不确定必须分别记录。
- **历史不可篡改**：旧 Run、Snapshot、Finding、Artifact 和 Evaluation 是审计证据。
- **无隐式升级**：相关性不能升级为因果，无样本不能升级为正常，日志不能升级为硬事实。

## 3. 可信工具准入

新增或修改 `TrustedTool` 时，必须审查：

```text
目标绑定 → 权限与作用域 → 有界查询 → 状态/错误契约
→ Artifact 脱敏 → Finding Parser → Replay 白名单
→ 正例/负例 Ground Truth → 单元/持久化/场景测试
```

禁止：

- 根据名称相似或标签猜测外部依赖；
- 接受模型生成的自由 PromQL、LogQL、Shell 或 Kubernetes 写请求；
- 使用固定绝对阈值声称跨容量实例的根因；
- 在缺少 baseline/incident 对比时使用全窗口最大值声称“突增”；
- 注册没有负对照的确定性 Finding。

## 4. Agent 规则

- Agent Run 必须是确定性父 Run 的独立子 Run；
- 不得修改父 Run、Finding 或 Diagnosis；
- Tool 调用只能来自 Registry；
- Offline Replay 不得访问实时数据源；
- `INVALID` 输出只保留审计，不创建正式 Diagnosis；
- 所有受支持假设必须包含反证检查；
- 自然语言使用简体中文，工具名、指标名、Finding 类型和代码标识符可保留原文；
- 禁止确认性根因、概率百分比和虚构 Finding。

## 5. 数据与安全

- Kubernetes RBAC 保持只读；
- 不读取 Secret 正文；
- 日志、annotation 和外部响应按不可信输入处理；
- Artifact 必须脱敏、限长、Hash 并记录来源；
- 不输出 API Key、密码、Token、Cookie、数据库凭据或加密密钥；
- 不自动执行删除、重启、扩缩容、rollout、配置修改或数据库写操作；
- 破坏性数据库测试只允许使用一次性隔离数据库。

## 6. 分支与提交

当前开发分支为 `release/0.9.0`。修改流程：

```bash
git checkout release/0.9.0
git pull --ff-only origin release/0.9.0
git status --short --branch
# 修改、测试
git add <reviewed-files>
git commit -m '<imperative summary>'
git push origin release/0.9.0
```

规则：

- 不在脏工作区部署；
- 不直接修改运行目录后再反向补 Git；
- 一个提交只表达一个可审查目的；
- 文档必须记录改变的边界、验证结果和剩余风险；
- 不强推共享分支，不改写发布历史。

## 7. 测试层级

每次改动至少完成与影响范围匹配的测试：

1. 语法/类型或专项单元测试；
2. 后端完整 `unittest discover`；
3. 前端 `npm run build`；
4. 涉及持久化时运行隔离 PostgreSQL 测试；
5. 涉及工具、Finding、Replay 或 Agent 时运行对应场景/准确性闭环；
6. 部署后运行 HTTP、Pod、队列和日志 smoke check。

不得把 skipped 当作 passed。报告必须分别写明 passed、skipped 和未执行项。

## 8. 部署规范

- 运行源码位于 `k8s-cp01:/root/aiops-console`；
- 同步前在 `/root/aiops-backups` 创建备份；
- 只同步 Git 已提交文件；
- 不同步 `node_modules`、`dist`、`.env`、缓存或临时测试输出；
- 部署时显式传入完整 `GIT_COMMIT`；
- `deploy.sh` 应保持幂等迁移、清单应用和 API/Worker restart；
- 长 SSH 超时后先检查实际状态，不得并发重复部署；
- `kubectl apply` 未必会删除其他管理者拥有的容器列表项，发现 PodTemplate 漂移时先查 managed fields、last-applied 和 Admission Webhook。

部署完成定义：

```text
Git commit 已 push
运行源码 Manifest 与该 commit 一致
ConfigMap / Pod env / release_notes 使用同一 commit
API、Worker、Web、PostgreSQL Ready
healthz / readyz / Web 返回 200
无 pending/retry/dead Outbox
最近日志无新增持续错误
```

## 9. 文档规范

- `CURRENT_HANDOFF.md`：只记录当前事实、接管命令和近期风险；
- `AGENT_HANDOFF_STATE.json`：与 CURRENT_HANDOFF 同步的机器可读快照；
- `ENGINEERING_RULES.md`：长期不变的工程和安全规则；
- `AGENT_HANDOFF.md`：详细架构与历史背景，不作为实时状态来源；
- `ACCURACY_LOOP.md`：Ground Truth、指标和运行方法；
- `CHANGELOG.md`：用户可感知能力和发布维护记录。

修改运行代码、部署方式、工具目录、版本契约或安全边界时，必须同步更新相关文档。

## 10. Definition of Done

一个任务只有同时满足以下条件才算完成：

- 需求和非目标明确；
- 方案没有重复现有能力或扩大权限；
- 代码、迁移、清单和文档一致；
- 测试结果可复现；
- 部署版本可追溯；
- 回滚路径存在；
- 运行状态已核验；
- 已知限制被明确记录，而不是用乐观描述掩盖。
