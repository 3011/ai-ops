# Changelog

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
