# Changelog

## 0.9.0 - 可信 Agent Shadow 调查与离线重放

- 新增框架无关 `InvestigationAgent`、`AgentToolRuntime`、输入/输出和 Hypothesis 契约；
- 新增受控结构化模型循环、Schema 修复、预算上限和 Prompt Injection 隔离；
- 新增 `investigation_model_invocations`，模型请求/响应脱敏后保存 Artifact URI、Hash、Token、耗时和错误；
- 新增独立 `agent_shadow` 与 `agent_offline` 子 Run，使用 `parent_run_id` 关联确定性父 Run；
- 默认 `INVESTIGATION_MODE=shadow`，Agent 或模型失败不会改变确定性 Finding、Diagnosis 和完成状态；
- Agent 只能调用九个注册只读工具，不接受自由 PromQL、LogQL、query 或 expression；
- 新增 Offline Snapshot Tool Runtime，缺失调用返回 `SNAPSHOT_TOOL_NOT_AVAILABLE`，禁止实时数据源访问；
- Replay Snapshot/Validator 升级至 `1.2.0`，校验父级 Finding、模型 Artifact、Agent Validation 和离线复用关系；
- Agent 输出禁止确认性措辞和概率百分比；虚构 Finding、日志单独 high support、未注册工具均判为 `INVALID`；
- `INVALID` Agent 输出只保留模型审计、校验报告和评估，不进入正式 `DiagnosisResult`；
- 新增 OOM/CPU 安全与效果评估及聚合发布门槛；
- 事件详情新增确定性结果、实时 Shadow、离线 Replay、假设支持/反证、模型 Artifact 和评估对比；
- 新增手动 Agent Shadow、Offline Replay、Comparison、Evaluation 和聚合 Evaluation API；
- 手动 Shadow API 通过 Outbox 交给 Worker 执行，保持只读 Kubernetes RBAC 边界；
- 离线缺失参数调用仍绑定原 Target/Scope，Replay Snapshot 可通过 1.2.0 Validator；
- 工具预算停止后增加一次强制 final diagnosis 轮次，避免仅因预算耗尽丢失结构化结论；
- 后端完整单元与隔离 PostgreSQL 回归通过，前端生产构建通过；
- 场景回归工具改为内部签名会话、配额安全的顺序执行，并在清理时取消测试 follow-up；
- Agent Evaluation Suite 定版为 `0.9.0`，模型不可用但未产生反向声明时不再误判为硬事实不一致；
- Prompt/Runtime 审计升级为 `agent-investigation-v2` / `1.1.0`，重要受支持假设新增结构化 `counterevidence_check`；
- 完整 `NOT_FOUND` 与负向观测计入有效工具调用，反证率只衡量 highly/partially supported 的重要假设；
- 重构登录页为响应式双栏安全入口，增加能力概览、卡片内错误反馈、密码自动清空聚焦和大写锁定提示；
- 错误密码登录统一返回不可缓存 `401`，主动清除旧会话 Cookie，并对未知账号执行同等密码校验路径；
- 登录页、侧栏和环境标识统一读取 `VITE_APP_VERSION` / `VITE_ENVIRONMENT`，修复前端 Deployment 重复 `env` 字段；
- 登录页进一步精简为浅色单卡片布局，固定高对比文字、输入框和移动端样式；
- Agent 失败原因区分模型不可用与输出契约失败，历史 Run 可根据审计错误兼容识别，避免误报“模型不可用”。
- 清理 0.7 遗留登录网格规则，改为全屏 Flex 精确居中；使用自定义 AIOps 节点网络 Logo，并通过 Chromium 几何渲染校验。

## 0.9.0-dev.4 - 冻结调查输入与历史 Replay 适配

- 在 `InvestigationAnalysisRun` 创建时写入不可变 `run_input_json`、Schema、来源 Hash 和来源模式。
- Snapshot 不再读取当前 Incident 状态，后续告警、标签和 resolved 变化不会改变旧 Run 输入。
- 新增 PostgreSQL 不可变触发器，冻结输入首次写入后禁止修改。
- 新增 `native_frozen`、`historical_reconstructed`、`legacy_incomplete` 三种来源模式。
- 新增 0.8.x 与 0.9.0-dev.1 历史输入适配器，并按 Run 开始时间截断历史告警集合。
- 真实旧版 ToolResult/Finding 契约不一致继续保持 `INVALID`，并标记 `legacy_contract_violation=true`。

## 0.9.0-dev.3 - Snapshot Replay 与结果校验

- 新增不可变的模型可见 Replay Snapshot，不保存 Kubernetes、Prometheus 或 Loki 原始响应正文；
- 新增 Result Validator，校验 Target UID、namespace 作用域、工具目录和工具版本；
- 校验 ToolExecution、Finding、Diagnosis fact_refs 的双向引用一致性；
- 为每个可信工具定义允许生成的 Finding 类型白名单；
- 强制日志 Finding 保留 `untrusted_input=true`，检测 raw response 泄漏；
- 新 Investigation Run 完成时自动生成 Snapshot，历史 Run 支持离线回填；
- 事件详情新增 Snapshot Hash、Source Hash、校验报告和显式 replay 操作；
- 新增 UID 篡改、未知 Finding、悬空引用、日志信任标记和快照幂等 PostgreSQL 测试。

## 0.9.0-dev.2 - 应用与关联可信工具

- 新增 `get_container_restart_history`。
- 新增 `get_recent_rollouts`，只表达时间相关性，不输出发布导致事故。
- 新增 `search_container_logs`，类别枚举、脱敏、去重和 Prompt Injection 隔离。
- 新增 `compare_cpu_across_replicas`，按 Workload UID 固定副本集合。
- 新增 `get_application_red_metrics`，仅支持内置 HTTP 指标 Profile。
- OOM 固定计划扩展为 5 个工具，CPU 固定计划扩展为 7 个工具。
- 新增三副本 CPU/RED/日志真实回归场景。

## 0.9.0-dev.1 - 可信 Prometheus 工具与 CPU 确定性闭环

- 建立受控 `PrometheusReadClient`，统一范围、步长、序列数、数据点和错误语义；
- 新增 `get_memory_usage_vs_limit`、`get_cpu_usage_vs_request_limit`、`get_cpu_throttling`；
- 对 cAdvisor 序列执行 Pod UID 二次校验，防止同名重建对象混淆；
- 新增内存接近 limit、CPU Spike、request 饱和、接近 limit 和 throttling Findings；
- OOM 可信调查补充退出前内存证据；
- 新增无 Agent 的 CPU Spike 确定性调查链路；
- 新增真实 CPU 基线→忙循环→throttling 场景及 Prometheus/数据库测试。

## 0.8.1 - 可信 Tool Runtime 加固

- 新增 `TrustedTool`、`ToolRegistry` 和通用 `ToolRuntime.execute()`；
- 新增 `InvestigationBudget` 与 `BudgetLedger`，在数据源访问前执行步骤、调用、成本、同工具、无进展和截止时间限制；
- 实现同一 AnalysisRun 内基于工具版本、规范化参数和 TargetContext 身份的缓存复用；
- 缓存命中不重复访问数据源、不重复创建 Finding，记录 `reused_execution_id`，成本为 0；
- Kubernetes 404、403、429、超时、5xx 和响应格式错误使用不同 ToolStatus、error_code 与 retryable 语义；
- Target Resolver 改为 Pod 名直接 GET、UID 分页查找和受控 labelSelector，支持 500 条以上 Pod；
- ownerReferences 优先选择 `controller=true`，Workload 元数据失败不破坏 Pod UID 的 high 定位质量；
- Finding Parser 纳入 Runtime，Finding ID 自动写回 ToolResult 和模型可见摘要；
- 新增 ArtifactStorage 接口和 PostgreSQL 压缩实现，支持敏感字段脱敏、大小限制、裁剪和完整对象 Hash；
- 事件详情增加预算账本、缓存命中、Artifact、截断和权限拒绝展示；
- 新增预算、缓存隔离、错误审计、分页、Artifact 和 Finding 契约集成测试。

## 0.8.0 - OOMKilled 可信调查闭环

- 新增框架无关的 TargetContext、ToolResult、DeterministicFinding 契约和枚举；
- 新增四张可信调查表，保留旧分析表与链路；
- Target Resolver 优先使用告警 Pod UID，并沿 ownerReferences 定位 ReplicaSet/Deployment；
- 新增只读工具 `get_container_termination_status` 和 ToolExecution 审计；
- 新增 `container_oom_killed_v1` 代码确认规则，不依赖模型；
- 模型关闭时仍可生成 OOMKilled 确定性事实和 `COMPLETED_PARTIAL` DiagnosisResult；
- 事件详情新增可信调查、目标 UID、定位路径、工具状态和降级展示；
- 新增 UID 防混淆、歧义定位、数据源失败、Finding 规则和 PostgreSQL 持久化测试。

所有重要平台变更都记录在此文件，并同步到控制台“版本说明”。

## 0.7.0 - 2026-07-15

### 平台治理与总览体验升级

- 重新设计运维总览，将事件压力、AI 分析质量、任务队列和发布变更分组展示；
- 新增同源 HttpOnly 会话登录，初始管理员首次登录强制修改密码；
- 新增用户、角色、细粒度权限和后端 RBAC 强制校验；
- 新增关键管理操作审计日志；
- 新增版本说明页面和版本记录 API；
- 补录 0.1.0～0.6.0 历史里程碑；
- 新增登录页、用户菜单和自助修改密码；
- 新增数据库迁移 `0003_auth_versions.sql`。

## 0.6.0 - 2026-07-15

- Kubernetes rollout、ReplicaSet、镜像和 ConfigMap 变更证据；
- CI/CD 发布事件 webhook；
- Tempo/Jaeger Trace 兼容查询；
- 事件详情改为诊断、时间线、证据、历史四段式结构。

## 0.5.1 - 2026-07-15

- Kubernetes 目标自动发现；
- CrashLoop、OOMKilled、Node 和缺失标签场景覆盖；
- DeepSeek 受限动态 PromQL/LogQL 规划；
- fingerprint 跨 startsAt 生命周期修复。

## 0.4.0 - 2026-07-14

- 运维总览、事件中心、原始告警、Webhook 投递和分析任务页面；
- 事件详情指标趋势与证据展示；
- 测试数据来源标识和生产视图隔离。

## 0.3.0 - 2026-07-14

- 前台模型配置中心；
- API Key Fernet 加密保存；
- 模型连通性测试和动态生效。

## 0.2.0 - 2026-07-14

- Prometheus、Loki 证据快照；
- DeepSeek 结构化分析；
- 证据不足时安全降级。

## 0.1.0 - 2026-07-14

- Alertmanager webhook 可靠接入；
- 告警实例、事件和 Outbox Worker；
- firing/resolved 生命周期与幂等处理。

### 0.9.0 accuracy-loop tooling

- 新增隔离 Ground Truth 场景闭环，覆盖 OOM、采样 OOM、CPU Spike、CrashLoop 与 OOM/CPU 负对照；
- 新增 Finding Precision/Recall、目标定位准确率、Replay 完整性和 Agent Shadow 效果评估；
- 新增自动 JSON/Markdown 报告和基于误报、漏报、定位、Replay、模型及契约结果的反馈建议。

- 准确性闭环修正 Agent 指标口径：安全回退不再计为模型输出契约成功，并新增有效结果率；
- Agent 契约允许引用真实 CPU/内存等指标百分比，仅禁止根因概率和置信度百分比，避免正确 OOM 结论被过度校验拒绝；
- Schema repair 错误反馈增加字段路径，便于模型精确修复失败字段。

- 准确性复评冻结每个场景的 Incident ID 与确定性父 Run ID，防止 resolved/follow-up Run 漂移污染 Precision/Recall；后续只更新 Agent 子 Run，以相同 Snapshot 对比修复效果。

- Agent 独立 Validator 升级到 1.1.0，与 Pydantic 输出契约统一概率百分比语义，允许工具观测指标百分比，继续禁止确认性和概率/置信度百分比。

- Agent 契约与 Validator 允许“无法确认根因”“证据不足以确认根因”等否定语境的安全弃权，继续拒绝正向确认性结论；Validator 升级到 1.2.0，Prompt 升级到 v4。
- 准确性发布门槛新增 Agent 安全校验率 100%，任何 `INVALID` 子 Run 都不会被有效结果率掩盖。

- Agent 确认性语言进一步覆盖“根因为/根因是/root cause is”，防止模型绕过“根因已确认”规则；Prompt 升级到 v5。
- Accuracy Loop 新增 Agent Ground Truth 语义命中率：OOM 强假设需命中内存异常增长/分配语义，CPU Spike 需命中 hot loop，不再以“有强假设”代替根因效果评估。
