import {
  AlertOutlined,
  ApiOutlined,
  BarChartOutlined,
  BellOutlined,
  CloudServerOutlined,
  DashboardOutlined,
  DeploymentUnitOutlined,
  HistoryOutlined,
  ReloadOutlined,
  SearchOutlined,
  SettingOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Form,
  Input,
  Layout,
  List,
  Menu,
  Progress,
  Row,
  Select,
  Space,
  Statistic,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import axios from 'axios'
import dayjs from 'dayjs'
import { useEffect, useMemo, useState } from 'react'
import { Link, Navigate, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom'
import './styles.css'

const api = axios.create({ baseURL: '/api/v1', timeout: 15000 })

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
  const summary = useQuery({
    queryKey: ['dashboard-summary'],
    queryFn: async () => (await api.get('/dashboard/summary')).data,
    refetchInterval: 15000,
  })
  const trend = useQuery({
    queryKey: ['dashboard-trend'],
    queryFn: async () => (await api.get('/dashboard/trend', { params: { hours: 24 } })).data,
    refetchInterval: 60000,
  })
  const data = summary.data || {}
  return (
    <Space direction="vertical" size={18} style={{ width: '100%' }}>
      <PageHeader
        title="运维总览"
        subtitle="集中查看事件压力、AI 分析效果、任务积压和关键数据源状态。"
        actions={<Button icon={<ReloadOutlined />} onClick={() => { summary.refetch(); trend.refetch() }}>刷新</Button>}
      />
      {summary.error && <Alert type="error" showIcon message="总览加载失败" description={apiErrorMessage(summary.error)} />}
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}><Card className="stat-card stat-red"><Statistic title="未恢复事件" value={data.open_incidents || 0} prefix={<AlertOutlined />} /></Card></Col>
        <Col xs={24} sm={12} lg={6}><Card className="stat-card stat-orange"><Statistic title="Critical / Warning" value={`${data.critical_open || 0} / ${data.warning_open || 0}`} /></Card></Col>
        <Col xs={24} sm={12} lg={6}><Card className="stat-card stat-blue"><Statistic title="近 24 小时事件" value={data.incidents_24h || 0} prefix={<BarChartOutlined />} /></Card></Col>
        <Col xs={24} sm={12} lg={6}><Card className="stat-card stat-green"><Statistic title="AI 分析成功率" value={data.analysis_success_rate || 0} suffix="%" prefix={<ThunderboltOutlined />} /></Card></Col>
      </Row>
      <Row gutter={[16, 16]}>
        <Col xs={24} xl={16}>
          <Card title="24 小时事件趋势" extra={<Space><Tag color="red">Critical</Tag><Tag color="orange">Warning</Tag><Tag color="blue">Info</Tag></Space>} loading={trend.isLoading}>
            <TrendBars buckets={trend.data?.buckets || []} />
          </Card>
        </Col>
        <Col xs={24} xl={8}>
          <Card title="运行状态">
            <Row gutter={[12, 12]}>
              <Col span={12}><Statistic title="待处理任务" value={data.pending_jobs || 0} /></Col>
              <Col span={12}><Statistic title="死信任务" value={data.failed_jobs || 0} valueStyle={{ color: data.failed_jobs ? '#cf1322' : undefined }} /></Col>
              <Col span={12}><Statistic title="分析总数" value={data.analysis_total || 0} /></Col>
              <Col span={12}><Statistic title="当前模型" value={data.model?.model || '-'} valueStyle={{ fontSize: 15 }} /></Col>
            </Row>
          </Card>
          <Card title="高频服务" className="spaced-card">
            <List
              size="small"
              dataSource={trend.data?.top_services || []}
              locale={{ emptyText: '暂无事件数据' }}
              renderItem={(item: any, index) => <List.Item><Space><Tag>{index + 1}</Tag><Typography.Text>{item.service}</Typography.Text></Space><strong>{item.count}</strong></List.Item>}
            />
          </Card>
        </Col>
      </Row>
      <Row gutter={[16, 16]}>
        <Col xs={24} xl={10}>
          <Card title="数据源健康">
            <List
              dataSource={data.data_sources || []}
              loading={summary.isLoading}
              renderItem={(item: any) => (
                <List.Item>
                  <List.Item.Meta title={<Space><StatusTag value={item.status} /><strong>{item.name}</strong></Space>} description={item.message} />
                  <Typography.Text type="secondary">{item.latency_ms == null ? '-' : `${item.latency_ms} ms`}</Typography.Text>
                </List.Item>
              )}
            />
          </Card>
        </Col>
        <Col xs={24} xl={14}>
          <Card title="最近事件" extra={<Link to="/incidents">查看全部</Link>}>
            <Table
              rowKey="id"
              size="small"
              pagination={false}
              dataSource={data.recent_incidents || []}
              onRow={(row: any) => ({ onClick: () => navigate(`/incidents/${row.id}`), style: { cursor: 'pointer' } })}
              columns={[
                { title: '事件', dataIndex: 'title', ellipsis: true },
                { title: '级别', dataIndex: 'severity', width: 90, render: (value) => <StatusTag value={value} /> },
                { title: '状态', dataIndex: 'status', width: 90, render: (value) => <StatusTag value={value} /> },
                { title: '最后发生', dataIndex: 'last_seen_at', width: 160, render: formatTime },
              ]}
            />
          </Card>
        </Col>
      </Row>
    </Space>
  )
}

function IncidentsPage() {
  const navigate = useNavigate()
  const [draft, setDraft] = useState<any>({})
  const [filters, setFilters] = useState<any>({})
  const query = useQuery({
    queryKey: ['incidents', filters],
    queryFn: async () => (await api.get('/incidents', { params: filters })).data,
    refetchInterval: 10000,
  })
  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <PageHeader title="事件中心" subtitle="查看聚合后的故障事件，并按状态、级别、集群和服务快速定位。" actions={<Button icon={<ReloadOutlined />} onClick={() => query.refetch()}>刷新</Button>} />
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
  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Alert type={analysis.error ? 'error' : 'info'} showIcon message={result.summary || '分析任务已完成'} description={analysis.error || undefined} />
      <Descriptions bordered size="small" column={{ xs: 1, md: 2 }}>
        <Descriptions.Item label="分析状态"><StatusTag value={analysis.status} /></Descriptions.Item>
        <Descriptions.Item label="模型">{analysis.model || '未启用 LLM'}</Descriptions.Item>
        <Descriptions.Item label="严重性判断">{result.severity_assessment || '-'}</Descriptions.Item>
        <Descriptions.Item label="完成时间">{formatTime(analysis.finished_at)}</Descriptions.Item>
      </Descriptions>
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
  const queryName = evidence.summary?.query_name || '日志证据'
  return (
    <Card size="small" title={<Space><Tag color={evidence.source_type === 'prometheus' ? 'blue' : 'purple'}>{evidence.source_type}</Tag><Typography.Text>{queryName}</Typography.Text></Space>} extra={`${evidence.duration_ms ?? '-'} ms`}>
      <Descriptions size="small" column={1}>
        <Descriptions.Item label="时间窗口">{formatTime(evidence.query_start)} ～ {formatTime(evidence.query_end)}</Descriptions.Item>
        <Descriptions.Item label="查询语句"><Typography.Text code copyable={{ text: evidence.query_text }}>{evidence.query_text}</Typography.Text></Descriptions.Item>
      </Descriptions>
      {evidence.error ? <Alert type="warning" showIcon message="数据源查询失败" description={evidence.error} /> : evidence.source_type === 'prometheus' ? <MetricChart evidence={evidence} /> : (
        <div>
          <Descriptions size="small" column={3}>
            <Descriptions.Item label="日志行数">{evidence.summary?.line_count || 0}</Descriptions.Item>
            <Descriptions.Item label="日志流数">{evidence.summary?.stream_count || 0}</Descriptions.Item>
          </Descriptions>
          {(evidence.summary?.sample_lines || []).length ? <pre>{(evidence.summary.sample_lines || []).join('\n')}</pre> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有匹配到错误日志" />}
        </div>
      )}
    </Card>
  )
}

function IncidentDetailPage() {
  const { id } = useParams()
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
  const latestEvidenceIds = new Set(latestAnalysis?.result?.evidence_refs || [])
  const visibleEvidence = latestEvidenceIds.size ? (data.evidence || []).filter((item: any) => latestEvidenceIds.has(item.id)) : (data.evidence || []).slice(0, 5)
  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Link to="/incidents">← 返回事件中心</Link>
      <PageHeader title={data.title} subtitle={`事件 #${data.id} · ${data.labels?.cluster || '-'} / ${data.labels?.namespace || '-'} / ${data.labels?.service || '-'}`} actions={<><StatusTag value={data.severity} /><StatusTag value={data.status} /><Button type="primary" icon={<ReloadOutlined />} loading={reanalyze.isPending} onClick={() => reanalyze.mutate()}>重新分析</Button></>} />
      <Card title="事件概况">
        <Descriptions bordered column={{ xs: 1, md: 2, xl: 4 }} size="small">
          <Descriptions.Item label="关联告警数">{data.alert_count}</Descriptions.Item>
          <Descriptions.Item label="环境">{data.labels?.environment || '-'}</Descriptions.Item>
          <Descriptions.Item label="首次发生">{formatTime(data.first_seen_at)}</Descriptions.Item>
          <Descriptions.Item label="恢复时间">{formatTime(data.resolved_at)}</Descriptions.Item>
        </Descriptions>
      </Card>
      <Card title="AI 诊断"><AnalysisCard analysis={latestAnalysis} /></Card>
      <Card title={`关键证据（${visibleEvidence.length}）`}>
        {visibleEvidence.length ? <Space direction="vertical" size={12} style={{ width: '100%' }}>{visibleEvidence.map((item: any) => <EvidenceCard key={item.id} evidence={item} />)}</Space> : <Empty description="尚无证据快照，可点击重新分析" />}
      </Card>
      <Card title="关联告警">
        <Table rowKey="id" size="small" pagination={false} dataSource={data.alerts || []} columns={[
          { title: '告警名', dataIndex: 'alertname' },
          { title: '级别', dataIndex: 'severity', width: 95, render: (value) => <StatusTag value={value} /> },
          { title: '状态', dataIndex: 'status', width: 95, render: (value) => <StatusTag value={value} /> },
          { title: '摘要', render: (_: unknown, row: any) => row.annotations?.summary || '-' },
          { title: '开始', dataIndex: 'starts_at', width: 170, render: formatTime },
          { title: '结束', dataIndex: 'ends_at', width: 170, render: formatTime },
        ]} />
      </Card>
      <Card title="分析历史">
        <Table rowKey="id" size="small" pagination={false} dataSource={data.analyses || []} columns={[
          { title: 'ID', dataIndex: 'id', width: 70 },
          { title: '状态', dataIndex: 'status', render: (value) => <StatusTag value={value} /> },
          { title: '模型', dataIndex: 'model' },
          { title: '开始', dataIndex: 'created_at', render: formatTime },
          { title: '完成', dataIndex: 'finished_at', render: formatTime },
        ]} />
      </Card>
    </Space>
  )
}

function RawAlertsPage() {
  const [status, setStatus] = useState<string | undefined>()
  const [severity, setSeverity] = useState<string | undefined>()
  const query = useQuery({ queryKey: ['alerts', status, severity], queryFn: async () => (await api.get('/alerts', { params: { status, severity } })).data, refetchInterval: 10000 })
  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <PageHeader title="原始告警" subtitle="Alertmanager 告警实例的完整生命周期，便于核对聚合前的原始信号。" actions={<Button icon={<ReloadOutlined />} onClick={() => query.refetch()}>刷新</Button>} />
      <Card className="filter-card"><Space wrap><Select allowClear placeholder="状态" value={status} onChange={setStatus} options={['firing', 'resolved'].map((value) => ({ value, label: value }))} /><Select allowClear placeholder="级别" value={severity} onChange={setSeverity} options={['critical', 'warning', 'info'].map((value) => ({ value, label: value }))} /><Typography.Text type="secondary">共 {query.data?.total || 0} 条</Typography.Text></Space></Card>
      <Card><Table rowKey="id" loading={query.isLoading} dataSource={query.data?.items || []} pagination={{ pageSize: 20 }} columns={[
        { title: '告警名', dataIndex: 'alertname' },
        { title: '服务', render: (_: unknown, row: any) => row.labels?.service || row.labels?.job || '-' },
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
  const query = useQuery({ queryKey: ['deliveries'], queryFn: async () => (await api.get('/webhook-deliveries')).data, refetchInterval: 10000 })
  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <PageHeader title="Webhook 投递" subtitle="审计 Alertmanager 每一次 HTTP 投递、关联事件和处理结果。" actions={<Button icon={<ReloadOutlined />} onClick={() => query.refetch()}>刷新</Button>} />
      <Card><Table rowKey="id" loading={query.isLoading} dataSource={query.data?.items || []} pagination={{ pageSize: 20 }} columns={[
        { title: 'ID', dataIndex: 'id', width: 70 },
        { title: '状态', dataIndex: 'status', width: 95, render: (value) => <StatusTag value={value} /> },
        { title: 'Receiver', dataIndex: 'receiver', ellipsis: true },
        { title: '告警数', dataIndex: 'alert_count', width: 85 },
        { title: '关联事件', dataIndex: 'incidents', render: (values: number[]) => <Space wrap>{(values || []).map((id) => <Link key={id} to={`/incidents/${id}`}>#{id}</Link>)}</Space> },
        { title: '接收时间', dataIndex: 'received_at', width: 170, render: formatTime },
      ]} /></Card>
    </Space>
  )
}

function JobsPage() {
  const query = useQuery({ queryKey: ['analysis-jobs'], queryFn: async () => (await api.get('/analysis-jobs')).data, refetchInterval: 5000 })
  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <PageHeader title="分析任务" subtitle="查看 Outbox Worker 的领取、重试、完成和死信状态。" actions={<Button icon={<ReloadOutlined />} onClick={() => query.refetch()}>刷新</Button>} />
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
      <Alert type="warning" showIcon message="安全提示" description="设置接口仍仅适用于内网开发环境。正式开放前需要增加登录、RBAC 和审计日志。" />
    </Space>
  )
}

function AppLayout() {
  const location = useLocation()
  const selectedKey = useMemo(() => {
    if (location.pathname.startsWith('/settings')) return 'settings'
    if (location.pathname.startsWith('/deliveries')) return 'deliveries'
    if (location.pathname.startsWith('/jobs')) return 'jobs'
    if (location.pathname.startsWith('/alerts')) return 'alerts'
    if (location.pathname.startsWith('/incidents')) return 'incidents'
    return 'dashboard'
  }, [location.pathname])
  return (
    <Layout className="shell">
      <Layout.Sider width={238} breakpoint="lg" collapsedWidth={0}>
        <div className="brand"><DeploymentUnitOutlined /><span>AIOps Console</span></div>
        <Menu theme="dark" mode="inline" selectedKeys={[selectedKey]} items={[
          { key: 'dashboard', icon: <DashboardOutlined />, label: <Link to="/dashboard">运维总览</Link> },
          { key: 'incidents', icon: <AlertOutlined />, label: <Link to="/incidents">事件中心</Link> },
          { key: 'alerts', icon: <BellOutlined />, label: <Link to="/alerts">原始告警</Link> },
          { key: 'deliveries', icon: <CloudServerOutlined />, label: <Link to="/deliveries">Webhook 投递</Link> },
          { key: 'jobs', icon: <HistoryOutlined />, label: <Link to="/jobs">分析任务</Link> },
          { type: 'divider' },
          { key: 'settings', icon: <SettingOutlined />, label: <Link to="/settings/model">设置</Link> },
        ]} />
      </Layout.Sider>
      <Layout>
        <Layout.Header className="header"><div><Typography.Text type="secondary">WORK'S K8S</Typography.Text><Typography.Title level={4}>AI 运维事件中心</Typography.Title></div><Space><Tag color="green">DEV</Tag><Typography.Text type="secondary">k8s-cp01</Typography.Text></Space></Layout.Header>
        <Layout.Content className="content">
          <Routes>
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/incidents" element={<IncidentsPage />} />
            <Route path="/incidents/:id" element={<IncidentDetailPage />} />
            <Route path="/alerts" element={<RawAlertsPage />} />
            <Route path="/deliveries" element={<DeliveriesPage />} />
            <Route path="/jobs" element={<JobsPage />} />
            <Route path="/settings/model" element={<ModelSettingsPage />} />
            <Route path="*" element={<Navigate to="/dashboard" replace />} />
          </Routes>
        </Layout.Content>
      </Layout>
    </Layout>
  )
}

export default AppLayout
