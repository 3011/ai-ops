# Changelog

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
