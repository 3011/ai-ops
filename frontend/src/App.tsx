import {
  AlertOutlined,
  DeploymentUnitOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Layout,
  List,
  Menu,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import axios from 'axios'
import dayjs from 'dayjs'
import { Link, Navigate, Route, Routes, useNavigate, useParams } from 'react-router-dom'
import './styles.css'

const api = axios.create({ baseURL: '/api/v1', timeout: 10000 })

const colors: Record<string, string> = {
  critical: 'red',
  warning: 'orange',
  info: 'blue',
  open: 'red',
  resolved: 'green',
  firing: 'red',
  evidence_ready: 'blue',
  skipped: 'default',
  succeeded: 'green',
}

function formatTime(value?: string | null) {
  return value ? dayjs(value).format('YYYY-MM-DD HH:mm:ss') : '-'
}

function Incidents() {
  const navigate = useNavigate()
  const query = useQuery({
    queryKey: ['incidents'],
    queryFn: async () => (await api.get('/incidents')).data,
    refetchInterval: 10000,
  })
  const items = query.data?.items || []

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Space align="center" className="page-title-row">
        <Typography.Title level={3}>告警事件</Typography.Title>
        <Button icon={<ReloadOutlined />} onClick={() => query.refetch()}>
          刷新
        </Button>
      </Space>
      {query.error && (
        <Alert
          type="error"
          showIcon
          message="加载失败"
          description={String(query.error)}
        />
      )}
      <Card>
        <Table
          rowKey="id"
          loading={query.isLoading}
          dataSource={items}
          locale={{ emptyText: <Empty description="尚无事件" /> }}
          pagination={{ pageSize: 20 }}
          onRow={(row: any) => ({
            onClick: () => navigate(`/incidents/${row.id}`),
            style: { cursor: 'pointer' },
          })}
          columns={[
            { title: 'ID', dataIndex: 'id', width: 70 },
            { title: '事件', dataIndex: 'title' },
            {
              title: '服务',
              render: (_: unknown, row: any) => String(row.labels?.service || '-'),
            },
            {
              title: '级别',
              dataIndex: 'severity',
              render: (value: string) => <Tag color={colors[value]}>{value}</Tag>,
            },
            {
              title: '状态',
              dataIndex: 'status',
              render: (value: string) => <Tag color={colors[value]}>{value}</Tag>,
            },
            { title: '告警数', dataIndex: 'alert_count', width: 90 },
            {
              title: '最后发生',
              dataIndex: 'last_seen_at',
              render: formatTime,
              width: 180,
            },
          ]}
        />
      </Card>
    </Space>
  )
}

function AnalysisCard({ analysis }: { analysis: any }) {
  if (!analysis) {
    return <Empty description="尚未生成分析记录" />
  }
  const result = analysis.result || {}
  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Alert
        type={analysis.error ? 'error' : 'info'}
        showIcon
        message={result.summary || '分析任务已完成'}
        description={analysis.error || undefined}
      />
      <Descriptions bordered size="small" column={2}>
        <Descriptions.Item label="分析状态">
          <Tag color={colors[analysis.status]}>{analysis.status}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="模型">{analysis.model || '未启用 LLM'}</Descriptions.Item>
        <Descriptions.Item label="开始时间">{formatTime(analysis.created_at)}</Descriptions.Item>
        <Descriptions.Item label="完成时间">{formatTime(analysis.finished_at)}</Descriptions.Item>
      </Descriptions>
      <div>
        <Typography.Title level={5}>建议检查项</Typography.Title>
        <List
          size="small"
          bordered
          dataSource={result.recommended_checks || []}
          locale={{ emptyText: '暂无建议' }}
          renderItem={(item: string, index) => (
            <List.Item>
              {index + 1}. {item}
            </List.Item>
          )}
        />
      </div>
      {(result.missing_evidence || []).length > 0 && (
        <Alert
          type="warning"
          showIcon
          message="缺失或异常证据"
          description={(result.missing_evidence || []).join('\n')}
        />
      )}
      {(result.risk_notes || []).length > 0 && (
        <Typography.Text type="secondary">
          {(result.risk_notes || []).join('；')}
        </Typography.Text>
      )}
    </Space>
  )
}

function EvidenceCard({ evidence }: { evidence: any }) {
  return (
    <Card
      size="small"
      title={
        <Space>
          <Tag color={evidence.source_type === 'prometheus' ? 'blue' : 'purple'}>
            {evidence.source_type}
          </Tag>
          <Typography.Text>{evidence.summary?.query_name || '日志证据'}</Typography.Text>
        </Space>
      }
      extra={`${evidence.duration_ms ?? '-'} ms`}
    >
      <Descriptions size="small" column={1}>
        <Descriptions.Item label="时间窗口">
          {formatTime(evidence.query_start)} ～ {formatTime(evidence.query_end)}
        </Descriptions.Item>
        <Descriptions.Item label="查询语句">
          <Typography.Text code copyable={{ text: evidence.query_text }}>
            {evidence.query_text}
          </Typography.Text>
        </Descriptions.Item>
      </Descriptions>
      {evidence.error ? (
        <Alert type="warning" showIcon message="数据源查询失败" description={evidence.error} />
      ) : (
        <pre>{JSON.stringify(evidence.summary, null, 2)}</pre>
      )}
    </Card>
  )
}

function Detail() {
  const { id } = useParams()
  const client = useQueryClient()
  const query = useQuery({
    queryKey: ['incident', id],
    queryFn: async () => (await api.get(`/incidents/${id}`)).data,
    enabled: Boolean(id),
    refetchInterval: 5000,
  })
  const reanalyze = useMutation({
    mutationFn: async () => (await api.post(`/incidents/${id}/reanalyze`)).data,
    onSuccess: async () => {
      message.success('重新分析任务已进入队列')
      await client.invalidateQueries({ queryKey: ['incident', id] })
    },
    onError: (error) => message.error(`提交失败：${String(error)}`),
  })

  if (query.isLoading) return <Card>加载中...</Card>
  if (query.error) return <Alert type="error" message="详情加载失败" description={String(query.error)} />

  const data = query.data
  const latestAnalysis = data.analyses?.[0]
  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Link to="/incidents">← 返回事件列表</Link>
      <Space align="center" className="page-title-row">
        <Typography.Title level={3}>{data.title}</Typography.Title>
        <Tag color={colors[data.severity]}>{data.severity}</Tag>
        <Tag color={colors[data.status]}>{data.status}</Tag>
        <Button
          type="primary"
          icon={<ReloadOutlined />}
          loading={reanalyze.isPending}
          onClick={() => reanalyze.mutate()}
        >
          重新分析
        </Button>
      </Space>

      <Card title="事件信息">
        <Descriptions bordered column={2} size="small">
          <Descriptions.Item label="事件 ID">{data.id}</Descriptions.Item>
          <Descriptions.Item label="关联告警数">{data.alert_count}</Descriptions.Item>
          <Descriptions.Item label="集群">{data.labels?.cluster || '-'}</Descriptions.Item>
          <Descriptions.Item label="命名空间">{data.labels?.namespace || '-'}</Descriptions.Item>
          <Descriptions.Item label="服务">{data.labels?.service || '-'}</Descriptions.Item>
          <Descriptions.Item label="环境">{data.labels?.environment || '-'}</Descriptions.Item>
          <Descriptions.Item label="首次发生">{formatTime(data.first_seen_at)}</Descriptions.Item>
          <Descriptions.Item label="最后发生">{formatTime(data.last_seen_at)}</Descriptions.Item>
        </Descriptions>
      </Card>

      <Card title="关联告警">
        <Table
          rowKey="id"
          size="small"
          pagination={false}
          dataSource={data.alerts || []}
          columns={[
            { title: '告警名', dataIndex: 'alertname' },
            {
              title: '级别',
              dataIndex: 'severity',
              render: (value: string) => <Tag color={colors[value]}>{value}</Tag>,
            },
            {
              title: '状态',
              dataIndex: 'status',
              render: (value: string) => <Tag color={colors[value]}>{value}</Tag>,
            },
            {
              title: '摘要',
              render: (_: unknown, row: any) => row.annotations?.summary || '-',
            },
            { title: '开始', dataIndex: 'starts_at', render: formatTime },
            { title: '结束', dataIndex: 'ends_at', render: formatTime },
          ]}
        />
      </Card>

      <Card title="诊断分析">
        <AnalysisCard analysis={latestAnalysis} />
      </Card>

      <Card title={`证据快照（${data.evidence?.length || 0}）`}>
        {data.evidence?.length ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            {data.evidence.map((item: any) => (
              <EvidenceCard key={item.id} evidence={item} />
            ))}
          </Space>
        ) : (
          <Empty description="尚无证据快照，可点击重新分析" />
        )}
      </Card>
    </Space>
  )
}

export default function App() {
  return (
    <Layout className="shell">
      <Layout.Sider width={220} breakpoint="lg" collapsedWidth={0}>
        <div className="brand">
          <DeploymentUnitOutlined /> AIOps Console
        </div>
        <Menu
          theme="dark"
          items={[
            {
              key: 'incidents',
              icon: <AlertOutlined />,
              label: <Link to="/incidents">告警事件</Link>,
            },
          ]}
        />
      </Layout.Sider>
      <Layout>
        <Layout.Header className="header">
          <Typography.Title level={4}>AI 运维事件中心</Typography.Title>
        </Layout.Header>
        <Layout.Content className="content">
          <Routes>
            <Route path="/incidents" element={<Incidents />} />
            <Route path="/incidents/:id" element={<Detail />} />
            <Route path="*" element={<Navigate to="/incidents" replace />} />
          </Routes>
        </Layout.Content>
      </Layout>
    </Layout>
  )
}
