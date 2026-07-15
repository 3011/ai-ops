import {
  AlertOutlined,
  ApiOutlined,
  ApartmentOutlined,
  BranchesOutlined,
  CodeOutlined,
  BarChartOutlined,
  BellOutlined,
  CloudServerOutlined,
  DashboardOutlined,
  DeploymentUnitOutlined,
  HistoryOutlined,
  LockOutlined,
  LogoutOutlined,
  ReadOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  TeamOutlined,
  UserOutlined,
  RocketOutlined,
  SearchOutlined,
  SettingOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Avatar,
  Button,
  Card,
  Col,
  Collapse,
  Descriptions,
  Dropdown,
  Empty,
  Form,
  Input,
  Layout,
  List,
  Menu,
  Modal,
  Progress,
  Row,
  Select,
  Space,
  Statistic,
  Switch,
  Table,
  Tabs,
  Timeline,
  Tag,
  Typography,
  message,
} from 'antd'
import axios from 'axios'
import dayjs from 'dayjs'
import { useEffect, useMemo, useState } from 'react'
import { Link, Navigate, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom'
import './styles.css'
import { api } from './client'
import { AuthProvider, PermissionRoute, useAuth } from './auth'
import { AccessManagementPage, ReleasesPage } from './GovernancePages'

const colors: Record<string, string> = {
  critical: 'red',
  warning: 'orange',
  info: 'blue',
  open: 'red',
  resolved: 'green',
  firing: 'red',
  pending: 'gold',
  retry: 'orange',
  processing: 'blue',
  dead: 'red',
  evidence_ready: 'blue',
  skipped: 'default',
  succeeded: 'green',
  healthy: 'green',
  unhealthy: 'red',
}

function formatTime(value?: string | null) {
  return value ? dayjs(value).format('YYYY-MM-DD HH:mm:ss') : '-'
}

function apiErrorMessage(error: unknown) {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail
    if (typeof detail === 'string') return detail
    return error.message
  }
  return String(error)
}

function StatusTag({ value }: { value?: string | null }) {
  return <Tag color={colors[value || '']}>{value || '-'}</Tag>
}

function OriginTags({ item }: { item: any }) {
  return (
    <Space size={4} wrap>
      {item?.is_test && <Tag color="gold">测试数据</Tag>}
      <Tag color={item?.source === 'alertmanager' ? 'blue' : 'default'}>
        {item?.source_label || (item?.source === 'alertmanager' ? 'Alertmanager 自动投递' : '手工 Webhook')}
      </Tag>
    </Space>
  )
}

function useTestDataVisibility() {
  const [includeTest, setIncludeTest] = useState(() => window.localStorage.getItem('aiops.includeTest') === 'true')
  const update = (value: boolean) => {
    window.localStorage.setItem('aiops.includeTest', String(value))
    setIncludeTest(value)
  }
  return [includeTest, update] as const
}

function TestDataToggle({ checked, onChange }: { checked: boolean; onChange: (value: boolean) => void }) {
  return <Space size={6}><Typography.Text type="secondary">测试数据</Typography.Text><Switch size="small" checked={checked} onChange={onChange} /></Space>
}

function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: React.ReactNode }) {
  return (
    <div className="page-header">
      <div>
        <Typography.Title level={3}>{title}</Typography.Title>
        {subtitle && <Typography.Text type="secondary">{subtitle}</Typography.Text>}
      </div>
      <Space wrap>{actions}</Space>
    </div>
  )
}

function TrendBars({ buckets }: { buckets: any[] }) {
  const max = Math.max(1, ...buckets.map((item) => item.total || 0))
  return (
    <div className="trend-chart">
      {buckets.map((item, index) => (
        <div className="trend-column" key={item.time} title={`${formatTime(item.time)}：${item.total} 个事件`}>
          <div className="trend-stack" style={{ height: `${Math.max(5, ((item.total || 0) / max) * 150)}px` }}>
            {item.total ? (
              <>
                <div className="bar-critical" style={{ flex: item.critical || 0 }} />
                <div className="bar-warning" style={{ flex: item.warning || 0 }} />
                <div className="bar-info" style={{ flex: item.info || 0 }} />
              </>
            ) : <div className="bar-empty" />}
          </div>
          {(index % Math.max(1, Math.floor(buckets.length / 6)) === 0 || index === buckets.length - 1) && (
            <span>{dayjs(item.time).format('HH:mm')}</span>
          )}
        </div>
      ))}
    </div>
  )
}

function MetricChart({ evidence }: { evidence: any }) {
  const series = evidence.series?.[0]
  const points = series?.points || []
  if (!points.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该时间窗口没有指标样本" />
  const values = points.map((point: any) => Number(point.value))
  const min = Math.min(...values)
  const max = Math.max(...values)
  const width = 760
  const height = 210
  const pad = 24
  const range = max - min || 1
  const polyline = points.map((point: any, index: number) => {
    const x = pad + (index / Math.max(1, points.length - 1)) * (width - pad * 2)
    const y = height - pad - ((Number(point.value) - min) / range) * (height - pad * 2)
    return `${x},${y}`
  }).join(' ')
  return (
    <div className="metric-chart-wrap">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="指标趋势图">
        <line x1={pad} y1={height - pad} x2={width - pad} y2={height - pad} className="chart-axis" />
        <line x1={pad} y1={pad} x2={pad} y2={height - pad} className="chart-axis" />
        <polyline points={polyline} className="chart-line" />
      </svg>
      <div className="metric-legend">
        <span>{formatTime(new Date(points[0].timestamp * 1000).toISOString())}</span>
        <strong>最小 {min.toFixed(3)} · 最大 {max.toFixed(3)} · 最新 {values.at(-1)?.toFixed(3)}</strong>
        <span>{formatTime(new Date(points.at(-1).timestamp * 1000).toISOString())}</span>
      </div>
    </div>
  )
}

function DashboardPage() {
  const navigate = useNavigate()
  const { user, has } = useAuth()
  const [includeTest, setIncludeTest] = useTestDataVisibility()
  const summary = useQuery({
    queryKey: ['dashboard-summary', includeTest],
    queryFn: async () => (await api.get('/dashboard/summary', { params: { include_test: includeTest } })).data,
    refetchInterval: 15000,
  })
  const trend = useQuery({
    queryKey: ['dashboard-trend', includeTest],
    queryFn: async () => (await api.get('/dashboard/trend', { params: { hours: 24, include_test: includeTest } })).data,
    refetchInterval: 60000,
  })
  const data = summary.data || {}
  const release = data.current_release
  return (
    <Space direction="vertical" size={18} style={{ width: '100%' }}>
      <div className="dashboard-hero">
        <div>
          <Typography.Text className="dashboard-kicker">OPERATIONS OVERVIEW</Typography.Text>
          <Typography.Title level={2}>你好，{user.display_name}</Typography.Title>
          <Typography.Paragraph>从事件压力、AI 分析质量、任务队列和发布变更四个维度掌握平台状态。</Typography.Paragraph>
        </div>
        <Space wrap>
          {release && <Button icon={<ReadOutlined />} onClick={() => navigate('/releases')}>v{release.version} · {release.title}</Button>}
          <TestDataToggle checked={includeTest} onChange={setIncludeTest} />
          <Button icon={<ReloadOutlined />} onClick={() => { summary.refetch(); trend.refetch() }}>刷新</Button>
        </Space>
      </div>

      {!includeTest && (data.hidden_test_count > 0 || data.hidden_test_change_count > 0) && <Alert type="info" showIcon message="当前为生产视图" description={`已隐藏 ${data.hidden_test_count || 0} 条测试事件和 ${data.hidden_test_change_count || 0} 条测试变更。`} />}
      {summary.error && <Alert type="error" showIcon message="总览加载失败" description={apiErrorMessage(summary.error)} />}

      <Row gutter={[16, 16]} className="overview-grid">
        <Col xs={24} md={12} xl={6}>
          <Card className="overview-card pressure-card" bordered={false}>
            <div className="overview-card-head"><div className="overview-icon"><AlertOutlined /></div><Typography.Text>事件压力</Typography.Text></div>
            <div className="overview-main-value">{data.open_incidents || 0}</div>
            <Typography.Text type="secondary">当前未恢复事件</Typography.Text>
            <div className="overview-split"><span><b>{data.critical_open || 0}</b> Critical</span><span><b>{data.warning_open || 0}</b> Warning</span><span><b>{data.incidents_24h || 0}</b> 24h</span></div>
          </Card>
        </Col>
        <Col xs={24} md={12} xl={6}>
          <Card className="overview-card ai-card" bordered={false}>
            <div className="overview-card-head"><div className="overview-icon"><ThunderboltOutlined /></div><Typography.Text>AI 分析质量</Typography.Text></div>
            <div className="overview-main-value">{data.analysis_success_rate || 0}<small>%</small></div>
            <Typography.Text type="secondary">分析成功率</Typography.Text>
            <Progress percent={data.analysis_success_rate || 0} showInfo={false} strokeColor="#7c3aed" />
            <div className="overview-foot">累计分析 {data.analysis_total || 0} 次 · {data.model?.model || '未配置模型'}</div>
          </Card>
        </Col>
        <Col xs={24} md={12} xl={6}>
          <Card className="overview-card queue-card" bordered={false}>
            <div className="overview-card-head"><div className="overview-icon"><HistoryOutlined /></div><Typography.Text>任务队列</Typography.Text></div>
            <div className="overview-main-value">{data.pending_jobs || 0}</div>
            <Typography.Text type="secondary">等待或处理中</Typography.Text>
            <div className="overview-split"><span><b>{data.pending_jobs || 0}</b> Pending</span><span className={data.failed_jobs ? 'danger-text' : ''}><b>{data.failed_jobs || 0}</b> Dead</span></div>
          </Card>
        </Col>
        <Col xs={24} md={12} xl={6}>
          <Card className="overview-card release-card" bordered={false}>
            <div className="overview-card-head"><div className="overview-icon"><RocketOutlined /></div><Typography.Text>发布与变更</Typography.Text></div>
            <div className="overview-main-value">{data.changes_24h || 0}</div>
            <Typography.Text type="secondary">近 24 小时生产变更</Typography.Text>
            <div className="overview-foot">当前版本 {release ? `v${release.version}` : '-'}{release?.commit_sha ? ` · ${release.commit_sha}` : ''}</div>
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={16}>
          <Card className="dashboard-panel" title="24 小时事件趋势" extra={<Space><Tag color="red">Critical</Tag><Tag color="orange">Warning</Tag><Tag color="blue">Info</Tag></Space>} loading={trend.isLoading}>
            <TrendBars buckets={trend.data?.buckets || []} />
          </Card>
        </Col>
        <Col xs={24} xl={8}>
          <Card className="dashboard-panel health-panel" title="数据源健康">
            <div className="health-grid">{(data.data_sources || []).map((item: any) => <div className={`health-tile health-${item.status}`} key={item.name}><div><strong>{item.name}</strong><StatusTag value={item.status} /></div><Typography.Text type="secondary">{item.message}</Typography.Text><span>{item.latency_ms == null ? '-' : `${item.latency_ms} ms`}</span></div>)}</div>
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={16}>
          <Card className="dashboard-panel" title="最近事件" extra={<Link to="/incidents">查看全部</Link>}>
            <Table rowKey="id" size="small" pagination={false} dataSource={data.recent_incidents || []} onRow={(row: any) => ({ onClick: () => navigate(`/incidents/${row.id}`), style: { cursor: 'pointer' } })} columns={[
              { title: '事件', dataIndex: 'title', ellipsis: true },
              { title: '来源', width: 200, render: (_: unknown, row: any) => <OriginTags item={row} /> },
              { title: '级别', dataIndex: 'severity', width: 90, render: (value) => <StatusTag value={value} /> },
              { title: '状态', dataIndex: 'status', width: 90, render: (value) => <StatusTag value={value} /> },
              { title: '最后发生', dataIndex: 'last_seen_at', width: 160, render: formatTime },
            ]} />
          </Card>
        </Col>
        <Col xs={24} xl={8}>
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            {has('users.view') && <Card className="dashboard-panel compact-panel" title="访问治理" extra={<Link to="/access">用户与权限</Link>}>
              <Row gutter={12}><Col span={12}><Statistic title="启用用户" value={data.users_active || 0} prefix={<UserOutlined />} /></Col><Col span={12}><Statistic title="用户总数" value={data.users_total || 0} prefix={<TeamOutlined />} /></Col></Row>
            </Card>}
            <Card className="dashboard-panel compact-panel" title="高频服务">
              <List size="small" dataSource={trend.data?.top_services || []} locale={{ emptyText: '暂无事件数据' }} renderItem={(item: any, index) => <List.Item><Space><Tag>{index + 1}</Tag><Typography.Text>{item.service}</Typography.Text></Space><strong>{item.count}</strong></List.Item>} />
            </Card>
          </Space>
        </Col>
      </Row>
    </Space>
  )
}

function IncidentsPage() {
  const navigate = useNavigate()
  const [includeTest, setIncludeTest] = useTestDataVisibility()
  const [draft, setDraft] = useState<any>({})
  const [filters, setFilters] = useState<any>({})
  const query = useQuery({
    queryKey: ['incidents', filters, includeTest],
    queryFn: async () => (await api.get('/incidents', { params: { ...filters, include_test: includeTest } })).data,
    refetchInterval: 10000,
  })
  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <PageHeader title="事件中心" subtitle="查看聚合后的故障事件，并按状态、级别、集群和服务快速定位。" actions={<><TestDataToggle checked={includeTest} onChange={setIncludeTest} /><Button icon={<ReloadOutlined />} onClick={() => query.refetch()}>刷新</Button></>} />
      {!includeTest && query.data?.hidden_test_count > 0 && <Alert type="info" showIcon message={`已隐藏 ${query.data.hidden_test_count} 条测试事件`} description="默认只展示生产事件；打开“测试数据”开关可查看测试场景。" />}
      <Card className="filter-card">
        <Row gutter={[12, 12]}>
          <Col xs={24} md={8}><Input allowClear prefix={<SearchOutlined />} placeholder="搜索事件标题或分组键" value={draft.q} onChange={(e) => setDraft({ ...draft, q: e.target.value })} onPressEnter={() => setFilters(draft)} /></Col>
          <Col xs={12} md={4}><Select allowClear placeholder="状态" style={{ width: '100%' }} value={draft.status} onChange={(value) => setDraft({ ...draft, status: value })} options={[{ value: 'open', label: 'Open' }, { value: 'resolved', label: 'Resolved' }]} /></Col>
          <Col xs={12} md={4}><Select allowClear placeholder="级别" style={{ width: '100%' }} value={draft.severity} onChange={(value) => setDraft({ ...draft, severity: value })} options={['critical', 'warning', 'info'].map((value) => ({ value, label: value }))} /></Col>
          <Col xs={12} md={4}><Input allowClear placeholder="命名空间" value={draft.namespace} onChange={(e) => setDraft({ ...draft, namespace: e.target.value })} /></Col>
          <Col xs={12} md={4}><Input allowClear placeholder="服务" value={draft.service} onChange={(e) => setDraft({ ...draft, service: e.target.value })} /></Col>
          <Col span={24}><Space><Button type="primary" icon={<SearchOutlined />} onClick={() => setFilters(draft)}>查询</Button><Button onClick={() => { setDraft({}); setFilters({}) }}>重置</Button><Typography.Text type="secondary">共 {query.data?.total || 0} 个事件</Typography.Text></Space></Col>
        </Row>
      </Card>
      <Card>
        <Table
          rowKey="id"
          loading={query.isLoading}
          dataSource={query.data?.items || []}
          locale={{ emptyText: <Empty description="没有符合条件的事件" /> }}
          pagination={{ pageSize: 20 }}
          onRow={(row: any) => ({ onClick: () => navigate(`/incidents/${row.id}`), style: { cursor: 'pointer' } })}
          columns={[
            { title: 'ID', dataIndex: 'id', width: 70 },
            { title: '事件', dataIndex: 'title', ellipsis: true },
            { title: '集群 / 命名空间', width: 190, render: (_: unknown, row: any) => `${row.labels?.cluster || '-'} / ${row.labels?.namespace || '-'}` },
            { title: '服务', width: 160, render: (_: unknown, row: any) => row.labels?.service || '-' },
            { title: '来源', width: 220, render: (_: unknown, row: any) => <OriginTags item={row} /> },
            { title: '级别', dataIndex: 'severity', width: 95, render: (value) => <StatusTag value={value} /> },
            { title: '状态', dataIndex: 'status', width: 95, render: (value) => <StatusTag value={value} /> },
            { title: '告警数', dataIndex: 'alert_count', width: 85 },
            { title: '最后发生', dataIndex: 'last_seen_at', width: 170, render: formatTime },
          ]}
        />
      </Card>
    </Space>
  )
}

function AnalysisCard({ analysis }: { analysis: any }) {
  if (!analysis) return <Empty description="尚未生成分析记录" />
  const result = analysis.result || {}
  const coverage = result.analysis_coverage || {}
  const dynamicPlan = result.dynamic_query_plan || {}
  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Alert type={analysis.error ? 'error' : 'info'} showIcon message={result.summary || '分析任务已完成'} description={analysis.error || undefined} />
      <Descriptions bordered size="small" column={{ xs: 1, md: 2 }}>
        <Descriptions.Item label="分析状态"><StatusTag value={analysis.status} /></Descriptions.Item>
        <Descriptions.Item label="模型">{analysis.model || '未启用 LLM'}</Descriptions.Item>
        <Descriptions.Item label="严重性判断">{result.severity_assessment || '-'}</Descriptions.Item>
        <Descriptions.Item label="完成时间">{formatTime(analysis.finished_at)}</Descriptions.Item>
      </Descriptions>
      {coverage.score !== undefined && (
        <Card size="small" className="coverage-card" title="自动分析覆盖度">
          <Row gutter={[18, 12]} align="middle">
            <Col xs={24} md={5}><Progress type="dashboard" percent={coverage.score || 0} status={coverage.score >= 75 ? 'success' : coverage.score >= 45 ? 'normal' : 'exception'} /></Col>
            <Col xs={24} md={19}>
              <Space direction="vertical" size={10} style={{ width: '100%' }}>
                <div><Typography.Text strong>已采集数据源：</Typography.Text><Space wrap>{(coverage.collected_sources || []).map((item: string) => <Tag color="blue" key={item}>{item}</Tag>)}</Space></div>
                <div><Typography.Text strong>自动发现目标：</Typography.Text><Space wrap>{(coverage.discovered_targets || []).length ? (coverage.discovered_targets || []).map((item: string) => <Tag color="cyan" key={item}>{item}</Tag>) : <Typography.Text type="secondary">未发现明确目标</Typography.Text>}</Space></div>
                <Typography.Text type="secondary">证据 {coverage.successful_evidence || 0}/{coverage.evidence_count || 0} 成功；观察窗口{coverage.window_complete ? '完整' : '尚未结束，系统会自动补充分析'}。</Typography.Text>
              </Space>
            </Col>
          </Row>
          {(coverage.missing || []).length > 0 && <Alert className="inline-alert" type="warning" showIcon message="当前分析缺口" description={(coverage.missing || []).join('；')} />}
        </Card>
      )}
      {(dynamicPlan.model || dynamicPlan.error || (dynamicPlan.accepted_prometheus || []).length || (dynamicPlan.accepted_loki || []).length) && (
        <Card size="small" title="动态证据规划" extra={dynamicPlan.model ? <Tag color="geekblue">{dynamicPlan.model}</Tag> : null}>
          <Row gutter={[16, 12]}>
            <Col xs={24} lg={12}>
              <Typography.Text strong>补充 PromQL</Typography.Text>
              <List size="small" dataSource={dynamicPlan.accepted_prometheus || []} locale={{ emptyText: '未规划额外 PromQL' }} renderItem={(item: any) => <List.Item><div><Typography.Text code>{item.name}</Typography.Text><Typography.Paragraph type="secondary" style={{ margin: '4px 0 0' }}>{item.reason || item.query}</Typography.Paragraph></div></List.Item>} />
            </Col>
            <Col xs={24} lg={12}>
              <Typography.Text strong>补充 LogQL</Typography.Text>
              <List size="small" dataSource={dynamicPlan.accepted_loki || []} locale={{ emptyText: '未规划额外 LogQL' }} renderItem={(item: any) => <List.Item><div><Typography.Text code>{item.name}</Typography.Text><Typography.Paragraph type="secondary" style={{ margin: '4px 0 0' }}>{item.reason || item.query}</Typography.Paragraph></div></List.Item>} />
            </Col>
          </Row>
          {(dynamicPlan.rejected || []).length > 0 && <Alert className="inline-alert" type="warning" showIcon message={`安全校验拒绝 ${dynamicPlan.rejected.length} 条查询`} description={(dynamicPlan.rejected || []).join('；')} />}
          {dynamicPlan.error && <Alert className="inline-alert" type="warning" showIcon message="动态规划未完成" description={dynamicPlan.error} />}
        </Card>
      )}
      <div>
        <Typography.Title level={5}>根因假设</Typography.Title>
        {(result.root_cause_hypotheses || []).length ? (
          <Row gutter={[12, 12]}>
            {(result.root_cause_hypotheses || []).map((item: any, index: number) => (
              <Col xs={24} lg={12} key={`${item.hypothesis}-${index}`}>
                <Card size="small" title={`假设 ${index + 1}`} extra={<Progress type="circle" size={42} percent={Math.round((item.confidence || 0) * 100)} />}>
                  <Typography.Paragraph>{item.hypothesis}</Typography.Paragraph>
                  <Space wrap>{(item.evidence_refs || []).map((ref: any) => <Tag color="blue" key={ref}>证据 #{ref}</Tag>)}</Space>
                  {(item.contradictions || []).length > 0 && <Alert className="inline-alert" type="warning" message="矛盾证据" description={(item.contradictions || []).join('；')} />}
                </Card>
              </Col>
            ))}
          </Row>
        ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前没有足够证据形成根因假设" />}
      </div>
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <Typography.Title level={5}>建议检查项</Typography.Title>
          <List size="small" bordered dataSource={result.recommended_checks || []} locale={{ emptyText: '暂无建议' }} renderItem={(item: string, index) => <List.Item>{index + 1}. {item}</List.Item>} />
        </Col>
        <Col xs={24} lg={12}>
          <Typography.Title level={5}>建议操作</Typography.Title>
          <List size="small" bordered dataSource={result.recommended_actions || []} locale={{ emptyText: '暂无建议操作' }} renderItem={(item: string, index) => <List.Item>{index + 1}. {item}</List.Item>} />
        </Col>
      </Row>
      {(result.missing_evidence || []).length > 0 && <Alert type="warning" showIcon message="缺失或异常证据" description={(result.missing_evidence || []).join('\n')} />}
      {(result.risk_notes || []).length > 0 && <Typography.Text type="secondary">{(result.risk_notes || []).join('；')}</Typography.Text>}
    </Space>
  )
}

function EvidenceCard({ evidence }: { evidence: any }) {
  const queryName = evidence.summary?.query_name || (evidence.source_type === 'kubernetes' ? '自动目标发现' : '日志证据')
  const sourceColor = evidence.source_type === 'prometheus' ? 'blue' : evidence.source_type === 'kubernetes' ? 'cyan' : evidence.source_type === 'planner' ? 'geekblue' : evidence.source_type === 'changes' ? 'gold' : evidence.source_type === 'traces' ? 'magenta' : 'purple'
  const summary = evidence.summary || {}
  return (
    <Card size="small" title={<Space><Tag color={sourceColor}>{evidence.source_type}</Tag><Typography.Text>{queryName}</Typography.Text></Space>} extra={`${evidence.duration_ms ?? '-'} ms`}>
      <Descriptions size="small" column={1}>
        <Descriptions.Item label="时间窗口">{formatTime(evidence.query_start)} ～ {formatTime(evidence.query_end)}</Descriptions.Item>
        <Descriptions.Item label="查询计划"><Typography.Text code copyable={{ text: evidence.query_text }}>{evidence.query_text}</Typography.Text></Descriptions.Item>
      </Descriptions>
      {evidence.error ? <Alert type="warning" showIcon message="数据源查询失败" description={evidence.error} /> : evidence.source_type === 'prometheus' ? <MetricChart evidence={evidence} /> : evidence.source_type === 'changes' ? (
        <Timeline items={(summary.items || []).map((item: any) => ({ color: 'blue', children: <div><Space wrap><Tag color="gold">{item.source}</Tag><strong>{item.title}</strong><Typography.Text type="secondary">{formatTime(item.occurred_at)}</Typography.Text></Space><div className="timeline-meta">{[item.version, item.commit_sha, item.image, item.actor].filter(Boolean).join(' · ') || item.description || '-'}</div></div> }))} />
      ) : evidence.source_type === 'traces' ? (
        summary.configured === false ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Trace 数据源尚未配置" /> : (summary.traces || []).length ? <Table rowKey={(row: any) => row.trace_id} size="small" pagination={{ pageSize: 8 }} dataSource={summary.traces} columns={[
          { title: 'Trace ID', dataIndex: 'trace_id', ellipsis: true },
          { title: '入口服务', dataIndex: 'root_service', render: (value, row: any) => value || (row.services || []).join(', ') || '-' },
          { title: '根 Span', dataIndex: 'root_span', render: (value) => value || '-' },
          { title: 'Span 数', dataIndex: 'span_count', width: 90, render: (value) => value ?? '-' },
          { title: '耗时', dataIndex: 'duration_ms', width: 100, render: (value, row: any) => value != null ? `${value} ms` : row.duration != null ? `${row.duration} μs` : '-' },
        ]} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前时间窗口未找到 Trace" />
      ) : evidence.source_type === 'planner' ? (
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Descriptions bordered size="small" column={{ xs: 1, md: 2 }}>
            <Descriptions.Item label="规划模型">{summary.model || '-'}</Descriptions.Item>
            <Descriptions.Item label="作用域">{[summary.scope?.namespace, summary.scope?.service, summary.scope?.node].filter(Boolean).join(' / ') || '-'}</Descriptions.Item>
            <Descriptions.Item label="通过 PromQL">{(summary.accepted_prometheus || []).length}</Descriptions.Item>
            <Descriptions.Item label="通过 LogQL">{(summary.accepted_loki || []).length}</Descriptions.Item>
          </Descriptions>
          <List size="small" bordered dataSource={[...(summary.accepted_prometheus || []).map((item: any) => ({ ...item, type: 'PromQL' })), ...(summary.accepted_loki || []).map((item: any) => ({ ...item, type: 'LogQL' }))]} locale={{ emptyText: '规划器判断无需额外查询' }} renderItem={(item: any) => <List.Item><Space direction="vertical" size={3} style={{ width: '100%' }}><Space><Tag>{item.type}</Tag><Typography.Text strong>{item.name}</Typography.Text></Space><Typography.Text code copyable={{ text: item.query }}>{item.query}</Typography.Text><Typography.Text type="secondary">{item.reason}</Typography.Text></Space></List.Item>} />
          {(summary.rejected || []).length > 0 && <Alert type="warning" showIcon message="被安全策略拒绝的查询" description={(summary.rejected || []).join('；')} />}
        </Space>
      ) : evidence.source_type === 'kubernetes' ? (
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Descriptions bordered size="small" column={{ xs: 1, md: 2 }}>
            <Descriptions.Item label="命名空间">{summary.namespace || '-'}</Descriptions.Item>
            <Descriptions.Item label="发现 Pod">{(summary.discovered_pods || []).length ? <Space wrap>{summary.discovered_pods.map((name: string) => <Tag color="cyan" key={name}>{name}</Tag>)}</Space> : '未发现'}</Descriptions.Item>
            <Descriptions.Item label="工作负载">{(summary.workloads || []).length ? <Space wrap>{summary.workloads.map((item: any) => <Tag key={`${item.kind}/${item.name}`}>{item.kind}/{item.name}</Tag>)}</Space> : '-'}</Descriptions.Item>
            <Descriptions.Item label="镜像">{(summary.images || []).length ? <Space direction="vertical" size={2}>{summary.images.map((image: string) => <Typography.Text code key={image}>{image}</Typography.Text>)}</Space> : '-'}</Descriptions.Item>
          </Descriptions>
          {(summary.issues || []).length > 0 && <Alert type="warning" showIcon message={`发现 ${summary.issues.length} 个异常信号`} description={<List size="small" dataSource={summary.issues.slice(0, 12)} renderItem={(item: string) => <List.Item>{item}</List.Item>} />} />}
          {(summary.pods || []).length > 0 && <Table rowKey="name" size="small" pagination={false} dataSource={summary.pods} columns={[
            { title: 'Pod', dataIndex: 'name' }, { title: '节点', dataIndex: 'node' }, { title: 'Phase', dataIndex: 'phase' },
            { title: 'Ready', dataIndex: 'ready', width: 80, render: (value: boolean) => <Tag color={value ? 'green' : 'red'}>{value ? 'Yes' : 'No'}</Tag> },
            { title: '重启', width: 80, render: (_: unknown, row: any) => (row.containers || []).reduce((sum: number, item: any) => sum + (item.restart_count || 0), 0) },
          ]} />}
          {(summary.rollout_history || []).length > 0 && <Card size="small" title="Rollout 与镜像历史"><Table rowKey="replicaset" size="small" pagination={{ pageSize: 6 }} dataSource={summary.rollout_history} columns={[
            { title: '时间', dataIndex: 'created_at', width: 170, render: formatTime },
            { title: 'Deployment', dataIndex: 'deployment' },
            { title: 'Revision', dataIndex: 'revision', width: 90 },
            { title: 'ReplicaSet', dataIndex: 'replicaset' },
            { title: '镜像', dataIndex: 'images', render: (values: string[]) => <Space direction="vertical" size={1}>{(values || []).map((value) => <Typography.Text code key={value}>{value}</Typography.Text>)}</Space> },
          ]} /></Card>}
          {(summary.configmaps || []).length > 0 && <Card size="small" title="ConfigMap 引用"><Table rowKey="name" size="small" pagination={false} dataSource={summary.configmaps} columns={[
            { title: 'ConfigMap', dataIndex: 'name' },
            { title: '最近元数据时间', dataIndex: 'updated_at', width: 180, render: formatTime },
            { title: 'ResourceVersion', dataIndex: 'resource_version', width: 150 },
            { title: '键', dataIndex: 'keys', render: (values: string[]) => <Space wrap>{(values || []).slice(0, 12).map((value) => <Tag key={value}>{value}</Tag>)}</Space> },
          ]} /></Card>}
          {(summary.events || []).length > 0 ? <Table rowKey={(row: any) => `${row.time}-${row.object_name}-${row.reason}`} size="small" pagination={{ pageSize: 8 }} dataSource={summary.events} columns={[
            { title: '时间', dataIndex: 'time', width: 170, render: formatTime }, { title: '类型', dataIndex: 'type', width: 90, render: (value: string) => <Tag color={value === 'Warning' ? 'orange' : 'blue'}>{value}</Tag> },
            { title: '对象', width: 190, render: (_: unknown, row: any) => `${row.object_kind || '-'}/${row.object_name || '-'}` }, { title: '原因', dataIndex: 'reason', width: 140 }, { title: '消息', dataIndex: 'message', ellipsis: true },
          ]} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有匹配到 Kubernetes Events" />}
        </Space>
      ) : (
        <div>
          <Descriptions size="small" column={3}>
            <Descriptions.Item label="日志行数">{summary.line_count || 0}</Descriptions.Item>
            <Descriptions.Item label="日志流数">{summary.stream_count || 0}</Descriptions.Item>
            <Descriptions.Item label="类型">{queryName}</Descriptions.Item>
          </Descriptions>
          {(summary.sample_lines || []).length ? <pre>{(summary.sample_lines || []).join('\n')}</pre> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有匹配到日志" />}
        </div>
      )}
    </Card>
  )
}


function formatBytes(value: unknown) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '-'
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let current = value
  let index = 0
  while (Math.abs(current) >= 1024 && index < units.length - 1) { current /= 1024; index += 1 }
  return `${current.toFixed(index >= 2 ? 2 : 1)} ${units[index]}`
}

function formatRatio(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : '-'
}

function trustedFindingTitle(finding: any) {
  const value = finding.value || {}
  const container = finding.subject?.container_name || '-'
  switch (finding.finding_type) {
    case 'container_oom_killed': return `容器 ${container} 因 OOMKilled 终止`
    case 'memory_limit_reached': return `内存峰值达到 limit 的 ${formatRatio(value.peak_limit_ratio)}`
    case 'memory_near_limit': return `内存峰值接近 limit：${formatRatio(value.peak_limit_ratio)}`
    case 'memory_usage_increased': return `内存使用由 ${formatBytes(value.first_working_set_bytes)} 增长至 ${formatBytes(value.observed_peak_bytes)}`
    case 'container_cpu_spike': return `容器 ${container} 发生 CPU Spike：峰值 ${Number(value.peak_cores || 0).toFixed(3)} Core`
    case 'cpu_request_saturated': return `CPU 峰值达到 request 的 ${formatRatio(value.peak_request_ratio)}`
    case 'cpu_near_limit': return `CPU 峰值接近 limit：${formatRatio(value.peak_limit_ratio)}`
    case 'cpu_throttling_sustained': return `观察到持续 CPU throttling：峰值 periods 比例 ${formatRatio(value.period_ratio_peak)}`
    case 'cpu_throttling_observed': return `观察到 CPU throttling：峰值 periods 比例 ${formatRatio(value.period_ratio_peak)}`
    case 'container_restart_increased': return `调查窗口内容器重启增加 ${value.window_restart_delta ?? '-'} 次`
    case 'container_repeatedly_restarted': return `容器持续重启：窗口内增加 ${value.window_restart_delta ?? '-'} 次`
    case 'container_restart_stable': return '完整采样窗口内未观察到重启计数增加'
    case 'rollout_preceded_incident': return `发布发生在事件前 ${Number(value.minutes_before_incident || 0).toFixed(1)} 分钟`
    case 'revision_changed': return `Deployment Revision ${value.previous_revision ?? '-'} → ${value.current_revision ?? '-'}`
    case 'image_changed': return `容器镜像发生变化：${(value.previous_images || []).join(', ') || '-'} → ${(value.current_images || []).join(', ') || '-'}`
    case 'no_recent_rollout': return '调查窗口内未观察到 Deployment rollout 或 CI/CD 发布'
    case 'oom_log_observed': return `日志中观察到 OOM 相关文本 ${value.matched_line_count ?? 0} 条`
    case 'allocation_failure_log_observed': return `日志中观察到内存分配失败文本 ${value.matched_line_count ?? 0} 条`
    case 'process_termination_log_observed': return `日志中观察到进程终止文本 ${value.matched_line_count ?? 0} 条`
    case 'runtime_error_log_observed': return `日志中观察到运行时异常文本 ${value.matched_line_count ?? 0} 条`
    case 'cpu_hot_loop_hint_log_observed': return `日志中观察到 CPU 忙循环提示文本 ${value.matched_line_count ?? 0} 条`
    case 'gc_pressure_log_observed': return `日志中观察到 GC 压力文本 ${value.matched_line_count ?? 0} 条`
    case 'request_timeout_log_observed': return `日志中观察到请求超时文本 ${value.matched_line_count ?? 0} 条`
    case 'single_replica_cpu_anomaly': return `CPU 异常仅出现在单个副本：${(value.anomalous_pods || []).join(', ') || '-'}`
    case 'subset_replicas_cpu_anomaly': return `CPU 异常出现在部分副本：${(value.anomalous_pods || []).join(', ') || '-'}`
    case 'all_replicas_cpu_increased': return '同一 Workload 的全部可比较副本 CPU 均明显升高'
    case 'new_revision_cpu_higher': return `新 Revision ${value.latest_revision ?? '-'} 的 CPU 明显高于旧 Revision`
    case 'request_rate_increased': return `应用请求率由 ${Number(value.baseline_mean || 0).toFixed(2)} 增至 ${Number(value.incident_mean || 0).toFixed(2)} req/s`
    case 'request_rate_stable': return `应用请求率保持稳定：${Number(value.incident_mean || 0).toFixed(2)} req/s`
    case 'error_rate_increased': return `应用错误率由 ${formatRatio(value.baseline_mean)} 增至 ${formatRatio(value.incident_mean)}`
    case 'latency_increased': return `应用 P99 延迟由 ${Number(value.baseline_mean || 0).toFixed(3)}s 增至 ${Number(value.incident_mean || 0).toFixed(3)}s`
    default: return finding.finding_type
  }
}

function trustedFindingSource(finding: any) {
  if (finding.finding_type === 'container_oom_killed') return 'Kubernetes ContainerStatus'
  if (finding.finding_type.startsWith('container_restart')) return 'Kubernetes ContainerStatus + Prometheus restart counter'
  if (['rollout_preceded_incident', 'revision_changed', 'image_changed', 'no_recent_rollout'].includes(finding.finding_type)) return 'Kubernetes Deployment / ReplicaSet + CI/CD 变更记录'
  if (finding.finding_type.endsWith('_log_observed')) return 'Loki / Kubernetes Pod Logs（不可信文本）'
  if (['single_replica_cpu_anomaly', 'subset_replicas_cpu_anomaly', 'all_replicas_cpu_increased', 'new_revision_cpu_higher'].includes(finding.finding_type)) return 'Kubernetes Workload UID + Prometheus 副本指标'
  if (['request_rate_increased', 'request_rate_stable', 'error_rate_increased', 'latency_increased'].includes(finding.finding_type)) return 'Prometheus 应用 RED 指标 Profile'
  return 'Prometheus 受控指标工具'
}


function TrustedInvestigationPanel({ runs }: { runs: any[] }) {
  const run = runs?.[0]
  if (!run) return <Empty description="当前事件没有可信确定性调查记录。" />
  const target = run.target_context || {}
  const findings = run.findings || []
  const tools = run.tool_executions || []
  const quality = target.resolution_quality
  const statusColor = run.status === 'COMPLETED' ? 'green' : run.status === 'COMPLETED_PARTIAL' ? 'gold' : run.status === 'FAILED' ? 'red' : 'blue'
  return <Space direction="vertical" size={16} style={{ width: '100%' }}>
    <Alert
      type={findings.length ? 'success' : 'warning'}
      showIcon
      message={findings.length ? `已生成 ${findings.length} 条确定性事实` : '当前未生成确定性事实'}
      description={run.diagnosis?.summary || '目标或证据未满足确认规则。'}
    />
    <Row gutter={[16, 16]}>
      <Col xs={12} md={6}><Card className="mini-stat"><Statistic title="调查状态" value={run.status} valueStyle={{ fontSize: 16 }} /></Card></Col>
      <Col xs={12} md={6}><Card className="mini-stat"><Statistic title="定位质量" value={(quality || 'unknown').toUpperCase()} valueStyle={{ fontSize: 20 }} /></Card></Col>
      <Col xs={12} md={6}><Card className="mini-stat"><Statistic title="确定性事实" value={findings.length} /></Card></Col>
      <Col xs={12} md={6}><Card className="mini-stat"><Statistic title="成本单位" value={run.budget_usage?.total_cost_units_used || 0} suffix={`/ ${run.budget?.max_total_cost_units || '-'}`} /></Card></Col>
    </Row>
    <Card title="调查预算与运行账本">
      <Descriptions bordered size="small" column={{ xs: 2, md: 4 }}>
        <Descriptions.Item label="步骤">{run.budget_usage?.steps_used || 0} / {run.budget?.max_steps || '-'}</Descriptions.Item>
        <Descriptions.Item label="工具调用">{run.budget_usage?.tool_calls_used || 0} / {run.budget?.max_tool_calls || '-'}</Descriptions.Item>
        <Descriptions.Item label="同工具上限">{run.budget?.max_same_tool_calls || '-'}</Descriptions.Item>
        <Descriptions.Item label="无进展轮次">{run.budget_usage?.no_progress_rounds || 0} / {run.budget?.max_no_progress_rounds || '-'}</Descriptions.Item>
      </Descriptions>
    </Card>
    <Card title="目标资源及 UID" extra={<Space><Tag color={statusColor}>{run.status}</Tag><Tag>{run.engine}@{run.engine_version}</Tag></Space>}>
      {target.pod_uid ? <Descriptions bordered size="small" column={{ xs: 1, md: 2 }}>
        <Descriptions.Item label="Cluster">{target.cluster_id || '-'}</Descriptions.Item>
        <Descriptions.Item label="Namespace">{target.namespace || '-'}</Descriptions.Item>
        <Descriptions.Item label="Pod">{target.pod_name || '-'}</Descriptions.Item>
        <Descriptions.Item label="Pod UID"><Typography.Text code copyable>{target.pod_uid || '-'}</Typography.Text></Descriptions.Item>
        <Descriptions.Item label="Container">{target.container_name || '-'}</Descriptions.Item>
        <Descriptions.Item label="Service">{target.service_name || '-'}</Descriptions.Item>
        <Descriptions.Item label="Workload">{target.workload_kind ? `${target.workload_kind}/${target.workload_name || '-'}` : '-'}</Descriptions.Item>
        <Descriptions.Item label="Workload UID"><Typography.Text code copyable>{target.workload_uid || '-'}</Typography.Text></Descriptions.Item>
        <Descriptions.Item label="定位方式">{target.resolution_method || '-'}</Descriptions.Item>
        <Descriptions.Item label="调查窗口">{formatTime(target.window_start)} ～ {formatTime(target.window_end)}</Descriptions.Item>
        <Descriptions.Item label="定位说明">{target.resolution_message || '-'}</Descriptions.Item>
      </Descriptions> : <Alert type="warning" showIcon message={target.resolution_message || '目标定位失败'} description={JSON.stringify(target.resolution_details || {})} />}
    </Card>
    <Row gutter={[16, 16]}>
      <Col xs={24} xl={10}>
        <Card title="目标定位路径" style={{ height: '100%' }}>
          {(target.resolution_path || []).length ? <Timeline items={(target.resolution_path || []).map((item: string, index: number) => ({ color: index === 0 ? 'blue' : 'green', children: item }))} /> : <Empty description="没有可用定位路径" />}
        </Card>
      </Col>
      <Col xs={24} xl={14}>
        <Card title="确定性事实" style={{ height: '100%' }}>
          {findings.length ? <List dataSource={findings} renderItem={(finding: any) => <List.Item>
            <List.Item.Meta
              title={<Space wrap><Tag color={finding.polarity === 'negative' ? 'default' : 'green'}>{finding.finding_type}</Tag><strong>{trustedFindingTitle(finding)}</strong></Space>}
              description={<Space direction="vertical" size={3}>
                <span>发生时间：{formatTime(finding.event_time)}</span>
                <span>确认来源：{trustedFindingSource(finding)} · ToolExecution #{finding.tool_execution_id}</span>
                <span>规则：{finding.confirmation_rule} · Parser {finding.parser_version} · 质量 {finding.quality}</span>
              </Space>}
            />
          </List.Item>} /> : <Empty description="没有满足确认规则的 Finding" />}
        </Card>
      </Col>
    </Row>
    <Card title="工具执行审计">
      <Table rowKey="id" size="small" pagination={false} dataSource={tools} columns={[
        { title: '顺序', dataIndex: 'sequence_number', width: 70 },
        { title: '工具', render: (_: unknown, row: any) => <Space direction="vertical" size={1}><strong>{row.tool_name}</strong><Typography.Text type="secondary">v{row.tool_version}</Typography.Text></Space> },
        { title: '状态', dataIndex: 'status', width: 160, render: (value) => <Tag color={value === 'FOUND' ? 'green' : value === 'DENIED' || value === 'INVALID_REQUEST' ? 'red' : value === 'UNAVAILABLE' || value === 'TARGET_UNCERTAIN' || value === 'BUDGET_EXCEEDED' ? 'orange' : value === 'PARTIAL' ? 'gold' : 'default'}>{value}</Tag> },
        { title: '结果摘要', render: (_: unknown, row: any) => <Space direction="vertical" size={2}><span>{row.result_summary?.summary || '-'}</span><Space wrap>{row.reused_execution_id && <Tag color="blue">缓存复用 #{row.reused_execution_id}</Tag>}{row.is_truncated && <Tag color="gold">已截断</Tag>}{(row.result_summary?.finding_ids || []).length > 0 && <Tag color="green">{row.result_summary.finding_ids.length} Finding</Tag>}</Space></Space> },
        { title: '目标 UID', render: (_: unknown, row: any) => <Typography.Text code>{row.input?.target?.pod_uid || '-'}</Typography.Text> },
        { title: 'Artifact', width: 190, render: (_: unknown, row: any) => <Space direction="vertical" size={1}>{row.raw_artifact_uri ? <Typography.Text code copyable>{row.raw_artifact_uri}</Typography.Text> : <Typography.Text type="secondary">Inline JSONB</Typography.Text>}{row.raw_artifact_hash && <Typography.Text type="secondary" ellipsis={{ tooltip: row.raw_artifact_hash }} style={{ maxWidth: 170 }}>{row.raw_artifact_hash}</Typography.Text>}</Space> },
        { title: '成本', dataIndex: 'cost_units', width: 75 },
        { title: '耗时', width: 110, render: (_: unknown, row: any) => `${Math.max(0, dayjs(row.completed_at).diff(dayjs(row.started_at)))} ms` },
        { title: '错误', width: 200, render: (_: unknown, row: any) => row.error_code ? <Tag color={row.status === 'DENIED' ? 'red' : 'orange'}>{row.error_code}</Tag> : '-' },
      ]} />
    </Card>
    {tools.some((item: any) => item.status === 'DENIED') && <Alert type="error" showIcon message="工具访问被权限策略拒绝" description="该结果不是“未发现异常”。请检查 Worker RBAC 或目标作用域配置。" />}
    {(run.degradation_reasons || []).length > 0 && <Alert
      type="info"
      showIcon
      message="分析降级状态"
      description={<Space direction="vertical" size={6}><Space wrap>{(run.degradation_reasons || []).map((item: string) => <Tag key={item}>{item}</Tag>)}</Space><span>Agent 调查尚未启用；当前只展示由代码确认的事实和工具审计。</span></Space>}
    />}
    {(run.diagnosis?.missing_evidence || []).length > 0 && <Alert type="warning" showIcon message="缺失证据" description={(run.diagnosis.missing_evidence || []).join('；')} />}
    {runs.length > 1 && <Card title="可信调查历史"><Table rowKey="id" size="small" pagination={{ pageSize: 6 }} dataSource={runs} columns={[
      { title: 'Run', dataIndex: 'id', width: 80 },
      { title: '状态', dataIndex: 'status', render: (value) => <Tag>{value}</Tag> },
      { title: '停止原因', dataIndex: 'stop_reason' },
      { title: '事实数', render: (_: unknown, row: any) => (row.findings || []).length, width: 90 },
      { title: '开始', dataIndex: 'started_at', render: formatTime },
      { title: '完成', dataIndex: 'completed_at', render: formatTime },
    ]} /></Card>}
  </Space>
}

function IncidentDetailPage() {
  const { id } = useParams()
  const { has } = useAuth()
  const client = useQueryClient()
  const query = useQuery({ queryKey: ['incident', id], queryFn: async () => (await api.get(`/incidents/${id}`)).data, enabled: Boolean(id), refetchInterval: 5000 })
  const reanalyze = useMutation({
    mutationFn: async () => (await api.post(`/incidents/${id}/reanalyze`)).data,
    onSuccess: async () => { message.success('重新分析任务已进入队列'); await client.invalidateQueries({ queryKey: ['incident', id] }) },
    onError: (error) => message.error(`提交失败：${apiErrorMessage(error)}`),
  })
  if (query.isLoading) return <Card>加载中...</Card>
  if (query.error) return <Alert type="error" message="详情加载失败" description={apiErrorMessage(query.error)} />
  const data = query.data
  const latestAnalysis = data.analyses?.[0]
  const trustedInvestigations = data.trusted_investigations || []
  const latestTrusted = trustedInvestigations[0]
  const latestEvidenceIds = new Set(latestAnalysis?.result?.evidence_refs || [])
  const citedIds = new Set((latestAnalysis?.result?.root_cause_hypotheses || []).flatMap((item: any) => item.evidence_refs || []))
  const latestEvidence = latestEvidenceIds.size ? (data.evidence || []).filter((item: any) => latestEvidenceIds.has(item.id)) : (data.evidence || [])
  const visibleEvidence = citedIds.size
    ? latestEvidence.filter((item: any) => citedIds.has(item.id) || ['kubernetes', 'changes', 'traces'].includes(item.source_type) || item.error)
    : latestEvidence
  const k8sEvidence = latestEvidence.find((item: any) => item.source_type === 'kubernetes')
  const timelineRows: any[] = []
  for (const item of data.change_events || []) timelineRows.push({ time: item.occurred_at, type: 'change', title: item.title, detail: [item.version, item.commit_sha, item.image, item.actor].filter(Boolean).join(' · ') || item.description, color: 'blue' })
  for (const item of k8sEvidence?.summary?.change_events || []) timelineRows.push({ time: item.time, type: item.type, title: item.title, detail: item.description, color: item.type === 'rollout' ? 'green' : 'gold' })
  for (const item of k8sEvidence?.summary?.events || []) timelineRows.push({ time: item.time, type: item.type, title: `${item.object_kind || 'Object'}/${item.object_name || '-'} · ${item.reason || '-'}`, detail: item.message, color: item.type === 'Warning' ? 'red' : 'gray' })
  for (const item of data.alerts || []) {
    timelineRows.push({ time: item.starts_at, type: 'alert', title: `${item.alertname} firing`, detail: item.annotations?.summary, color: 'red' })
    if (item.ends_at) timelineRows.push({ time: item.ends_at, type: 'alert', title: `${item.alertname} resolved`, detail: '告警恢复', color: 'green' })
  }
  for (const item of data.analyses || []) timelineRows.push({ time: item.finished_at || item.created_at, type: 'analysis', title: `分析 #${item.id} ${item.status}`, detail: item.model || '确定性证据分析', color: 'blue' })
  for (const item of trustedInvestigations) timelineRows.push({ time: item.completed_at || item.created_at, type: 'trusted', title: `可信调查 #${item.id} ${item.status}`, detail: `${item.engine} · ${(item.findings || []).length} 个确定性事实`, color: (item.findings || []).length ? 'green' : 'gold' })
  timelineRows.sort((a, b) => dayjs(b.time).valueOf() - dayjs(a.time).valueOf())

  const overview = <Space direction="vertical" size={16} style={{ width: '100%' }}>
    <Row gutter={[16, 16]}>
      <Col xs={24} md={6}><Card className="mini-stat"><Statistic title="状态" value={data.status} valueStyle={{ color: data.status === 'open' ? '#cf1322' : '#389e0d', fontSize: 22 }} /></Card></Col>
      <Col xs={24} md={6}><Card className="mini-stat"><Statistic title="严重级别" value={data.severity} valueStyle={{ fontSize: 22 }} /></Card></Col>
      <Col xs={24} md={6}><Card className="mini-stat"><Statistic title="关联告警" value={data.alert_count || 0} /></Card></Col>
      <Col xs={24} md={6}><Card className="mini-stat"><Statistic title="关联变更" value={(data.change_events || []).length} prefix={<RocketOutlined />} /></Card></Col>
    </Row>
    <Card title="事件概况">
      <Descriptions bordered column={{ xs: 1, md: 2, xl: 4 }} size="small">
        <Descriptions.Item label="集群">{data.labels?.cluster || '-'}</Descriptions.Item>
        <Descriptions.Item label="命名空间">{data.labels?.namespace || '-'}</Descriptions.Item>
        <Descriptions.Item label="服务">{data.labels?.service || '-'}</Descriptions.Item>
        <Descriptions.Item label="环境">{data.labels?.environment || '-'}</Descriptions.Item>
        <Descriptions.Item label="首次发生">{formatTime(data.first_seen_at)}</Descriptions.Item>
        <Descriptions.Item label="最后更新">{formatTime(data.last_seen_at)}</Descriptions.Item>
        <Descriptions.Item label="恢复时间">{formatTime(data.resolved_at)}</Descriptions.Item>
        <Descriptions.Item label="来源"><OriginTags item={data} /></Descriptions.Item>
      </Descriptions>
    </Card>
    {latestTrusted && <Card title="可信调查摘要" extra={<Tag color={(latestTrusted.findings || []).length ? 'green' : 'gold'}>{latestTrusted.status}</Tag>}><Alert type={(latestTrusted.findings || []).length ? 'success' : 'warning'} showIcon message={latestTrusted.diagnosis?.summary || '可信调查已完成'} description={`目标定位质量：${latestTrusted.target_context?.resolution_quality || 'unknown'}；确定性事实：${(latestTrusted.findings || []).length}；工具执行：${(latestTrusted.tool_executions || []).length}`} /></Card>}
    <Card title="AI 诊断"><AnalysisCard analysis={latestAnalysis} /></Card>
  </Space>

  const timeline = <Card className="timeline-card" title="事件与变更时间线" extra={<Tag>{timelineRows.length} 条</Tag>}>
    {timelineRows.length ? <Timeline mode="left" items={timelineRows.map((item) => ({ color: item.color, label: formatTime(item.time), children: <div className="timeline-entry"><Space wrap><Tag>{item.type}</Tag><strong>{item.title}</strong></Space>{item.detail && <Typography.Paragraph type="secondary" ellipsis={{ rows: 3, expandable: true }} style={{ margin: '6px 0 0' }}>{item.detail}</Typography.Paragraph>}</div> }))} /> : <Empty description="没有可关联的变更或事件" />}
  </Card>

  const evidencePanel = visibleEvidence.length ? <Collapse accordion={false} items={visibleEvidence.map((item: any) => ({ key: item.id, label: <Space><Tag color={item.source_type === 'prometheus' ? 'blue' : item.source_type === 'kubernetes' ? 'cyan' : item.source_type === 'changes' ? 'gold' : item.source_type === 'traces' ? 'magenta' : 'purple'}>{item.source_type}</Tag><span>{item.summary?.query_name || item.query_text}</span>{citedIds.has(item.id) && <Tag color="green">根因引用</Tag>}</Space>, children: <EvidenceCard evidence={item} /> }))} /> : <Empty description="尚无证据快照，可点击重新分析" />

  const related = <Space direction="vertical" size={16} style={{ width: '100%' }}>
    <Card title="关联告警"><Table rowKey="id" size="small" pagination={false} dataSource={data.alerts || []} columns={[
      { title: '告警名', dataIndex: 'alertname' },
      { title: '级别', dataIndex: 'severity', width: 95, render: (value) => <StatusTag value={value} /> },
      { title: '状态', dataIndex: 'status', width: 95, render: (value) => <StatusTag value={value} /> },
      { title: '摘要', render: (_: unknown, row: any) => row.annotations?.summary || '-' },
      { title: '开始', dataIndex: 'starts_at', width: 170, render: formatTime },
      { title: '结束', dataIndex: 'ends_at', width: 170, render: formatTime },
    ]} /></Card>
    <Card title="分析历史"><Table rowKey="id" size="small" pagination={false} dataSource={data.analyses || []} columns={[
      { title: 'ID', dataIndex: 'id', width: 70 },
      { title: '状态', dataIndex: 'status', render: (value) => <StatusTag value={value} /> },
      { title: '模型', dataIndex: 'model' },
      { title: '开始', dataIndex: 'created_at', render: formatTime },
      { title: '完成', dataIndex: 'finished_at', render: formatTime },
    ]} /></Card>
  </Space>

  return <Space direction="vertical" size={16} style={{ width: '100%' }}>
    <Link to="/incidents">← 返回事件中心</Link>
    <div className="incident-hero">
      <div><Space wrap><OriginTags item={data} /><StatusTag value={data.severity} /><StatusTag value={data.status} /></Space><Typography.Title level={2}>{data.title}</Typography.Title><Typography.Text type="secondary">事件 #{data.id} · {data.labels?.cluster || '-'} / {data.labels?.namespace || '-'} / {data.labels?.service || '-'}</Typography.Text></div>
      <Button type="primary" size="large" icon={<ReloadOutlined />} loading={reanalyze.isPending} onClick={() => reanalyze.mutate()}>重新分析</Button>
    </div>
    <Tabs className="incident-tabs" defaultActiveKey={latestTrusted ? 'trusted' : 'overview'} items={[
      { key: 'trusted', label: `可信调查 (${trustedInvestigations.length})`, children: <TrustedInvestigationPanel runs={trustedInvestigations} /> },
      { key: 'overview', label: '诊断概览', children: overview },
      { key: 'timeline', label: <Space><HistoryOutlined />变更时间线</Space>, children: timeline },
      { key: 'evidence', label: `全部证据 (${visibleEvidence.length})`, children: evidencePanel },
      { key: 'related', label: '关联与历史', children: related },
    ]} />
  </Space>
}

function RawAlertsPage() {
  const [includeTest, setIncludeTest] = useTestDataVisibility()
  const [status, setStatus] = useState<string | undefined>()
  const [severity, setSeverity] = useState<string | undefined>()
  const query = useQuery({ queryKey: ['alerts', status, severity, includeTest], queryFn: async () => (await api.get('/alerts', { params: { status, severity, include_test: includeTest } })).data, refetchInterval: 10000 })
  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <PageHeader title="原始告警" subtitle="Alertmanager 告警实例的完整生命周期，便于核对聚合前的原始信号。" actions={<><TestDataToggle checked={includeTest} onChange={setIncludeTest} /><Button icon={<ReloadOutlined />} onClick={() => query.refetch()}>刷新</Button></>} />
      {!includeTest && query.data?.hidden_test_count > 0 && <Alert type="info" showIcon message={`已隐藏 ${query.data.hidden_test_count} 条测试告警`} />}
      <Card className="filter-card"><Space wrap><Select allowClear placeholder="状态" value={status} onChange={setStatus} options={['firing', 'resolved'].map((value) => ({ value, label: value }))} /><Select allowClear placeholder="级别" value={severity} onChange={setSeverity} options={['critical', 'warning', 'info'].map((value) => ({ value, label: value }))} /><Typography.Text type="secondary">共 {query.data?.total || 0} 条</Typography.Text></Space></Card>
      <Card><Table rowKey="id" loading={query.isLoading} dataSource={query.data?.items || []} pagination={{ pageSize: 20 }} columns={[
        { title: '告警名', dataIndex: 'alertname' },
        { title: '服务', render: (_: unknown, row: any) => row.labels?.service || row.labels?.job || '-' },
        { title: '来源', width: 220, render: (_: unknown, row: any) => <OriginTags item={row} /> },
        { title: '命名空间', render: (_: unknown, row: any) => row.labels?.namespace || '-' },
        { title: '级别', dataIndex: 'severity', width: 95, render: (value) => <StatusTag value={value} /> },
        { title: '状态', dataIndex: 'status', width: 95, render: (value) => <StatusTag value={value} /> },
        { title: '开始', dataIndex: 'starts_at', width: 170, render: formatTime },
        { title: '结束', dataIndex: 'ends_at', width: 170, render: formatTime },
      ]} /></Card>
    </Space>
  )
}

function DeliveriesPage() {
  const [includeTest, setIncludeTest] = useTestDataVisibility()
  const query = useQuery({ queryKey: ['deliveries', includeTest], queryFn: async () => (await api.get('/webhook-deliveries', { params: { include_test: includeTest } })).data, refetchInterval: 10000 })
  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <PageHeader title="Webhook 投递" subtitle="审计 Alertmanager 每一次 HTTP 投递、关联事件和处理结果。" actions={<><TestDataToggle checked={includeTest} onChange={setIncludeTest} /><Button icon={<ReloadOutlined />} onClick={() => query.refetch()}>刷新</Button></>} />
      {!includeTest && query.data?.hidden_test_count > 0 && <Alert type="info" showIcon message={`已隐藏 ${query.data.hidden_test_count} 次测试投递`} />}
      <Card><Table rowKey="id" loading={query.isLoading} dataSource={query.data?.items || []} pagination={{ pageSize: 20 }} columns={[
        { title: 'ID', dataIndex: 'id', width: 70 },
        { title: '状态', dataIndex: 'status', width: 95, render: (value) => <StatusTag value={value} /> },
        { title: '来源', width: 220, render: (_: unknown, row: any) => <OriginTags item={row} /> },
        { title: 'Receiver', dataIndex: 'receiver', ellipsis: true },
        { title: '告警数', dataIndex: 'alert_count', width: 85 },
        { title: '关联事件', dataIndex: 'incidents', render: (values: number[]) => <Space wrap>{(values || []).map((id) => <Link key={id} to={`/incidents/${id}`}>#{id}</Link>)}</Space> },
        { title: '接收时间', dataIndex: 'received_at', width: 170, render: formatTime },
      ]} /></Card>
    </Space>
  )
}

function JobsPage() {
  const [includeTest, setIncludeTest] = useTestDataVisibility()
  const query = useQuery({ queryKey: ['analysis-jobs', includeTest], queryFn: async () => (await api.get('/analysis-jobs', { params: { include_test: includeTest } })).data, refetchInterval: 5000 })
  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <PageHeader title="分析任务" subtitle="查看 Outbox Worker 的领取、重试、完成和死信状态。" actions={<><TestDataToggle checked={includeTest} onChange={setIncludeTest} /><Button icon={<ReloadOutlined />} onClick={() => query.refetch()}>刷新</Button></>} />
      {!includeTest && query.data?.hidden_test_count > 0 && <Alert type="info" showIcon message={`已隐藏 ${query.data.hidden_test_count} 个测试任务`} />}
      <Card><Table rowKey="id" loading={query.isLoading} dataSource={query.data?.items || []} pagination={{ pageSize: 20 }} columns={[
        { title: '任务 ID', dataIndex: 'id', width: 90 },
        { title: '事件', dataIndex: 'incident_id', width: 90, render: (id) => id ? <Link to={`/incidents/${id}`}>#{id}</Link> : '-' },
        { title: '状态', dataIndex: 'status', width: 110, render: (value) => <StatusTag value={value} /> },
        { title: '尝试次数', render: (_: unknown, row: any) => `${row.attempts}/${row.max_attempts}`, width: 100 },
        { title: '错误', dataIndex: 'last_error', ellipsis: true, render: (value) => value || '-' },
        { title: '创建时间', dataIndex: 'created_at', width: 170, render: formatTime },
        { title: '完成时间', dataIndex: 'finished_at', width: 170, render: formatTime },
      ]} /></Card>
    </Space>
  )
}

function ChangeEventsPage() {
  const [namespace, setNamespace] = useState<string | undefined>()
  const [service, setService] = useState<string | undefined>()
  const [includeTest, setIncludeTest] = useTestDataVisibility()
  const query = useQuery({ queryKey: ['change-events', namespace, service, includeTest], queryFn: async () => (await api.get('/change-events', { params: { namespace, service, include_test: includeTest } })).data, refetchInterval: 15000 })
  return <Space direction="vertical" size={16} style={{ width: '100%' }}>
    <PageHeader title="变更记录" subtitle="汇总 CI/CD 发布、镜像、版本和配置变更，为事件根因分析提供时间关联。" actions={<><TestDataToggle checked={includeTest} onChange={setIncludeTest} /><Button icon={<ReloadOutlined />} onClick={() => query.refetch()}>刷新</Button></>} />
    {!includeTest && (query.data?.hidden_test_count || 0) > 0 && <Alert type="info" showIcon message={`已隐藏 ${query.data.hidden_test_count} 条测试变更`} />}
    <Card className="filter-card"><Space wrap><Input allowClear placeholder="命名空间" value={namespace} onChange={(e) => setNamespace(e.target.value || undefined)} /><Input allowClear placeholder="服务" value={service} onChange={(e) => setService(e.target.value || undefined)} /><Typography.Text type="secondary">共 {query.data?.total || 0} 条</Typography.Text></Space></Card>
    <Card><Table rowKey="id" loading={query.isLoading} dataSource={query.data?.items || []} pagination={{ pageSize: 20 }} columns={[
      { title: '时间', dataIndex: 'occurred_at', width: 175, render: formatTime },
      { title: '来源', dataIndex: 'source', width: 100, render: (value) => <Tag color="blue">{value}</Tag> },
      { title: '类型', dataIndex: 'event_type', width: 130, render: (value) => <Tag color="gold">{value}</Tag> },
      { title: '服务', width: 200, render: (_: unknown, row: any) => `${row.namespace}/${row.service}` },
      { title: '标题', dataIndex: 'title', ellipsis: true },
      { title: '版本 / Commit', width: 210, render: (_: unknown, row: any) => row.version || row.commit_sha || '-' },
      { title: '镜像', dataIndex: 'image', ellipsis: true, render: (value) => value ? <Typography.Text code>{value}</Typography.Text> : '-' },
      { title: '执行人', dataIndex: 'actor', width: 120, render: (value) => value || '-' },
    ]} /></Card>
  </Space>
}

function IntegrationSettingsPage() {
  const [form] = Form.useForm()
  const query = useQuery({ queryKey: ['trace-settings'], queryFn: async () => (await api.get('/settings/traces')).data })
  useEffect(() => { if (query.data) form.setFieldsValue(query.data) }, [query.data, form])
  const save = useMutation({ mutationFn: async (values: any) => (await api.put('/settings/traces', values)).data, onSuccess: async () => { message.success('Trace 配置已保存'); await query.refetch() }, onError: (error) => message.error(apiErrorMessage(error)) })
  const test = useMutation({ mutationFn: async (values: any) => (await api.post('/settings/traces/test', values)).data, onSuccess: async (data) => { message.success(`连接成功，${data.latency_ms} ms`); await query.refetch() }, onError: (error) => message.error(`连接失败：${apiErrorMessage(error)}`) })
  const sample = `curl -X POST http://aiops-api.aiops-dev.svc:8000/api/v1/webhooks/deployment-events \\\n  -H 'Content-Type: application/json' \\\n  -H 'X-AIOps-Token: <从 aiops-secrets 获取>' \\\n  -d '{"source":"gitlab","event_type":"deployment","namespace":"default","service":"order-service","version":"v1.8.2","commit_sha":"abc123","image":"registry/order:v1.8.2","actor":"ci-bot","occurred_at":"2026-07-15T04:30:00Z"}'`
  return <Space direction="vertical" size={16} style={{ width: '100%' }}>
    <PageHeader title="集成设置" subtitle="连接 Trace 后端，并为 CI/CD 发布流水线提供标准变更事件入口。" />
    <Row gutter={[16, 16]}>
      <Col xs={24} xl={14}><Card title={<Space><ApartmentOutlined />分布式 Trace</Space>} loading={query.isLoading}>
        <Form form={form} layout="vertical" initialValues={{ provider: 'tempo', enabled: false, service_tag: 'service.name' }}>
          <Form.Item name="provider" label="后端类型"><Select options={[{ value: 'tempo', label: 'Grafana Tempo' }, { value: 'jaeger', label: 'Jaeger' }]} /></Form.Item>
          <Form.Item name="base_url" label="Base URL" extra="示例：http://tempo.monitoring.svc:3200 或 http://jaeger-query.observability.svc:16686"><Input placeholder="http://tempo.monitoring.svc:3200" /></Form.Item>
          <Form.Item name="service_tag" label="服务属性名"><Input placeholder="service.name" /></Form.Item>
          <Form.Item name="enabled" label="启用 Trace 查询" valuePropName="checked"><Switch /></Form.Item>
          <Space><Button type="primary" loading={save.isPending} onClick={async () => save.mutate(await form.validateFields())}>保存</Button><Button loading={test.isPending} onClick={async () => test.mutate(await form.validateFields())}>测试连接</Button></Space>
        </Form>
        <Descriptions bordered size="small" column={1} style={{ marginTop: 20 }}>
          <Descriptions.Item label="状态"><StatusTag value={query.data?.enabled ? (query.data?.last_test_status || 'pending') : 'disabled'} /></Descriptions.Item>
          <Descriptions.Item label="最近测试">{formatTime(query.data?.last_tested_at)}</Descriptions.Item>
          <Descriptions.Item label="信息">{query.data?.last_test_message || '尚未配置'}</Descriptions.Item>
        </Descriptions>
      </Card></Col>
      <Col xs={24} xl={10}><Card title={<Space><CodeOutlined />CI/CD 发布事件</Space>}>
        <Alert type="info" showIcon message="通用 Webhook 已启用" description="Jenkins、GitLab CI、GitHub Actions 或其他流水线均可在发布后发送一条标准事件。系统会自动关联同 namespace/service 的开放事件并触发重新分析。" />
        <Typography.Title level={5}>请求示例</Typography.Title><pre className="code-sample">{sample}</pre>
        <Typography.Text type="secondary">Token 保存在 Kubernetes Secret aiops-dev/aiops-secrets 的 RELEASE_WEBHOOK_TOKEN 字段中，前端不会显示明文。</Typography.Text>
      </Card></Col>
    </Row>
  </Space>
}

function ModelSettingsPage() {
  const [form] = Form.useForm()
  const client = useQueryClient()
  const query = useQuery({ queryKey: ['model-settings'], queryFn: async () => (await api.get('/settings/model')).data })
  useEffect(() => {
    if (!query.data) return
    form.setFieldsValue({ provider: query.data.provider || 'openai-compatible', base_url: query.data.base_url, model: query.data.model, enabled: query.data.enabled, api_key: '' })
  }, [query.data, form])
  const save = useMutation({
    mutationFn: async (values: any) => { const payload: any = { provider: 'openai-compatible', base_url: values.base_url, model: values.model, enabled: values.enabled }; if (values.api_key?.trim()) payload.api_key = values.api_key.trim(); return (await api.put('/settings/model', payload)).data },
    onSuccess: async () => { message.success('模型配置已保存，下一次分析立即生效'); form.setFieldValue('api_key', ''); await client.invalidateQueries({ queryKey: ['model-settings'] }) },
    onError: (error) => message.error(`保存失败：${apiErrorMessage(error)}`),
  })
  const test = useMutation({
    mutationFn: async (values: any) => { const payload: any = { base_url: values.base_url, model: values.model }; if (values.api_key?.trim()) payload.api_key = values.api_key.trim(); return (await api.post('/settings/model/test', payload)).data },
    onSuccess: async (data) => { message.success(`连接成功，耗时 ${data.latency_ms} ms`); await client.invalidateQueries({ queryKey: ['model-settings'] }) },
    onError: (error) => message.error(`连接失败：${apiErrorMessage(error)}`),
  })
  const clearKey = useMutation({
    mutationFn: async () => { const values = await form.validateFields(['base_url', 'model', 'enabled']); return (await api.put('/settings/model', { provider: 'openai-compatible', base_url: values.base_url, model: values.model, enabled: values.enabled, clear_api_key: true })).data },
    onSuccess: async () => { message.success('API Key 已清除'); await client.invalidateQueries({ queryKey: ['model-settings'] }) },
    onError: (error) => message.error(`清除失败：${apiErrorMessage(error)}`),
  })
  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <PageHeader title="模型设置" subtitle="维护 OpenAI-compatible 模型接口，修改后无需重启 Worker。" />
      <Alert type="info" showIcon message="DeepSeek 推荐配置" description="Base URL：https://api.deepseek.com；模型：deepseek-v4-flash。API Key 采用 Fernet 加密保存。" />
      <Row gutter={[16, 16]}>
        <Col xs={24} xl={15}>
          <Card title={<Space><ApiOutlined />模型 API</Space>} loading={query.isLoading}>
            <Form form={form} layout="vertical" initialValues={{ provider: 'openai-compatible', enabled: true }}>
              <Form.Item name="provider" label="接口协议"><Input disabled /></Form.Item>
              <Form.Item name="base_url" label="API Base URL" rules={[{ required: true }, { type: 'url', message: '请输入有效 URL' }]} extra="系统请求 {Base URL}/chat/completions"><Input /></Form.Item>
              <Form.Item name="model" label="模型名称" rules={[{ required: true }]}><Input /></Form.Item>
              <Form.Item name="api_key" label={<Space>API Key {query.data?.api_key_configured ? <Tag color="green">已配置</Tag> : <Tag>未配置</Tag>}</Space>} extra="留空保留当前密钥，页面不会回显明文。"><Input.Password autoComplete="new-password" placeholder={query.data?.api_key_configured ? '••••••••（留空保留）' : 'sk-...'} /></Form.Item>
              <Form.Item name="enabled" label="启用 AI 分析" valuePropName="checked"><Switch checkedChildren="启用" unCheckedChildren="停用" /></Form.Item>
              <Space wrap><Button type="primary" loading={save.isPending} onClick={async () => save.mutate(await form.validateFields())}>保存配置</Button><Button loading={test.isPending} onClick={async () => test.mutate(await form.validateFields())}>测试连接</Button><Button danger disabled={!query.data?.api_key_configured} loading={clearKey.isPending} onClick={() => clearKey.mutate()}>清除 API Key</Button></Space>
            </Form>
          </Card>
        </Col>
        <Col xs={24} xl={9}>
          <Card title="运行状态">
            <Descriptions bordered size="small" column={1}>
              <Descriptions.Item label="AI 分析"><StatusTag value={query.data?.enabled ? 'healthy' : 'disabled'} /></Descriptions.Item>
              <Descriptions.Item label="当前模型">{query.data?.model || '-'}</Descriptions.Item>
              <Descriptions.Item label="API Key">{query.data?.api_key_configured ? '已安全保存' : '未配置'}</Descriptions.Item>
              <Descriptions.Item label="最后测试">{formatTime(query.data?.last_tested_at)}</Descriptions.Item>
              <Descriptions.Item label="测试结果"><StatusTag value={query.data?.last_test_status} /></Descriptions.Item>
              <Descriptions.Item label="测试信息">{query.data?.last_test_message || '-'}</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
      </Row>
      <Alert type="info" showIcon message="权限保护已启用" description="模型配置修改需要 settings.manage 权限，并会写入审计日志；API Key 不会在页面回显。" />
    </Space>
  )
}

function AppLayout() {
  const location = useLocation()
  const { user, has, logout, refresh } = useAuth()
  const [passwordOpen, setPasswordOpen] = useState(false)
  const [passwordForm] = Form.useForm()
  const changePassword = useMutation({
    mutationFn: async (values: any) => (await api.post('/auth/change-password', { current_password: values.current_password, new_password: values.new_password })).data,
    onSuccess: async () => { message.success('密码修改成功'); setPasswordOpen(false); passwordForm.resetFields(); await refresh() },
    onError: (error) => message.error(apiErrorMessage(error)),
  })
  const selectedKey = useMemo(() => {
    if (location.pathname.startsWith('/settings/integrations')) return 'settings-integrations'
    if (location.pathname.startsWith('/settings')) return 'settings-model'
    if (location.pathname.startsWith('/access')) return 'access'
    if (location.pathname.startsWith('/releases')) return 'releases'
    if (location.pathname.startsWith('/changes')) return 'changes'
    if (location.pathname.startsWith('/deliveries')) return 'deliveries'
    if (location.pathname.startsWith('/jobs')) return 'jobs'
    if (location.pathname.startsWith('/alerts')) return 'alerts'
    if (location.pathname.startsWith('/incidents')) return 'incidents'
    return 'dashboard'
  }, [location.pathname])

  const menuItems = [
    { key: 'dashboard', icon: <DashboardOutlined />, label: <Link to="/dashboard">运维总览</Link> },
    has('incidents.view') ? { key: 'incidents', icon: <AlertOutlined />, label: <Link to="/incidents">事件中心</Link> } : null,
    has('incidents.view') ? { key: 'alerts', icon: <BellOutlined />, label: <Link to="/alerts">原始告警</Link> } : null,
    has('changes.view') ? { key: 'changes', icon: <BranchesOutlined />, label: <Link to="/changes">变更记录</Link> } : null,
    has('incidents.view') ? { key: 'deliveries', icon: <CloudServerOutlined />, label: <Link to="/deliveries">Webhook 投递</Link> } : null,
    has('incidents.view') ? { key: 'jobs', icon: <HistoryOutlined />, label: <Link to="/jobs">分析任务</Link> } : null,
    { type: 'divider' as const },
    has('settings.view') ? { key: 'settings', icon: <SettingOutlined />, label: '平台设置', children: [
      { key: 'settings-model', label: <Link to="/settings/model">模型设置</Link> },
      { key: 'settings-integrations', label: <Link to="/settings/integrations">集成设置</Link> },
    ] } : null,
    (has('users.view') || has('versions.view')) ? { key: 'governance', icon: <SafetyCertificateOutlined />, label: '平台治理', children: [
      ...(has('users.view') ? [{ key: 'access', icon: <TeamOutlined />, label: <Link to="/access">用户与权限</Link> }] : []),
      ...(has('versions.view') ? [{ key: 'releases', icon: <ReadOutlined />, label: <Link to="/releases">版本说明</Link> }] : []),
    ] } : null,
  ].filter(Boolean) as any[]

  const userMenu = {
    items: [
      { key: 'identity', disabled: true, label: <div><strong>{user.display_name}</strong><div className="user-menu-sub">@{user.username}</div></div> },
      { type: 'divider' as const },
      { key: 'change-password', icon: <LockOutlined />, label: '修改密码', onClick: () => setPasswordOpen(true) },
      { key: 'logout', icon: <LogoutOutlined />, label: '退出登录', onClick: () => logout() },
    ],
  }

  return (
    <Layout className="shell">
      <Layout.Sider className="app-sider" width={238} breakpoint="lg" collapsedWidth={0}>
        <div className="brand"><DeploymentUnitOutlined /><span>AIOps Console</span></div>
        <Menu theme="dark" mode="inline" selectedKeys={[selectedKey]} defaultOpenKeys={['settings', 'governance']} items={menuItems} />
        <div className="sider-version"><span>Platform</span><strong>v0.7.0</strong></div>
      </Layout.Sider>
      <Layout className="main-layout">
        <Layout.Header className="header">
          <div className="header-left">
            <Typography.Text className="header-product">WORK&apos;S K8S</Typography.Text>
            <span className="header-divider" />
            <Typography.Text type="secondary" className="header-section">AIOps 运维工作台</Typography.Text>
          </div>
          <Space size={14}>
            <Tag color="green">DEV</Tag>
            <Typography.Text type="secondary" className="header-node">k8s-cp01</Typography.Text>
            <Dropdown menu={userMenu} placement="bottomRight" trigger={['click']}>
              <button className="user-trigger"><Avatar size="small" icon={<UserOutlined />} /><span>{user.display_name}</span></button>
            </Dropdown>
          </Space>
        </Layout.Header>
        <Layout.Content className="content">
          <Routes>
            <Route path="/dashboard" element={<PermissionRoute permission="dashboard.view"><DashboardPage /></PermissionRoute>} />
            <Route path="/incidents" element={<PermissionRoute permission="incidents.view"><IncidentsPage /></PermissionRoute>} />
            <Route path="/incidents/:id" element={<PermissionRoute permission="incidents.view"><IncidentDetailPage /></PermissionRoute>} />
            <Route path="/alerts" element={<PermissionRoute permission="incidents.view"><RawAlertsPage /></PermissionRoute>} />
            <Route path="/changes" element={<PermissionRoute permission="changes.view"><ChangeEventsPage /></PermissionRoute>} />
            <Route path="/deliveries" element={<PermissionRoute permission="incidents.view"><DeliveriesPage /></PermissionRoute>} />
            <Route path="/jobs" element={<PermissionRoute permission="incidents.view"><JobsPage /></PermissionRoute>} />
            <Route path="/settings/model" element={<PermissionRoute permission="settings.view"><ModelSettingsPage /></PermissionRoute>} />
            <Route path="/settings/integrations" element={<PermissionRoute permission="settings.view"><IntegrationSettingsPage /></PermissionRoute>} />
            <Route path="/access" element={<PermissionRoute permission="users.view"><AccessManagementPage /></PermissionRoute>} />
            <Route path="/releases" element={<PermissionRoute permission="versions.view"><ReleasesPage /></PermissionRoute>} />
            <Route path="*" element={<Navigate to="/dashboard" replace />} />
          </Routes>
        </Layout.Content>
      </Layout>
      <Modal title="修改密码" open={passwordOpen} onCancel={() => setPasswordOpen(false)} onOk={() => passwordForm.submit()} confirmLoading={changePassword.isPending} destroyOnHidden>
        <Form form={passwordForm} layout="vertical" onFinish={(values) => changePassword.mutate(values)}>
          <Form.Item name="current_password" label="当前密码" rules={[{ required: true }]}><Input.Password autoComplete="current-password" /></Form.Item>
          <Form.Item name="new_password" label="新密码" rules={[{ required: true }, { min: 10, message: '至少 10 个字符' }]}><Input.Password autoComplete="new-password" /></Form.Item>
          <Form.Item name="confirm_password" label="确认新密码" dependencies={['new_password']} rules={[{ required: true }, ({ getFieldValue }) => ({ validator(_, value) { return value === getFieldValue('new_password') ? Promise.resolve() : Promise.reject(new Error('两次密码不一致')) } })]}><Input.Password autoComplete="new-password" /></Form.Item>
        </Form>
      </Modal>
    </Layout>
  )
}

function AppRoot() {
  return <AuthProvider><AppLayout /></AuthProvider>
}

export default AppRoot
