import { AuditOutlined, EditOutlined, PlusOutlined, SafetyCertificateOutlined, TeamOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Card, Checkbox, Col, Descriptions, Form, Input, List, Modal, Row, Select, Space, Table, Tabs, Tag, Timeline, Typography, message } from 'antd'
import axios from 'axios'
import dayjs from 'dayjs'
import { useMemo, useState } from 'react'
import { api } from './client'
import { useAuth } from './auth'

function formatTime(value?: string | null) { return value ? dayjs(value).format('YYYY-MM-DD HH:mm:ss') : '-' }
function errorMessage(error: unknown) { return axios.isAxiosError(error) ? error.response?.data?.detail || error.message : String(error) }

function PageTitle({ title, subtitle, actions }: { title: string; subtitle: string; actions?: React.ReactNode }) {
  return <div className="page-header"><div><Typography.Title level={3}>{title}</Typography.Title><Typography.Text type="secondary">{subtitle}</Typography.Text></div><Space>{actions}</Space></div>
}

export function AccessManagementPage() {
  const { has } = useAuth()
  const client = useQueryClient()
  const users = useQuery({ queryKey: ['users'], queryFn: async () => (await api.get('/users')).data })
  const roles = useQuery({ queryKey: ['roles'], queryFn: async () => (await api.get('/roles')).data })
  const permissions = useQuery({ queryKey: ['permissions'], queryFn: async () => (await api.get('/permissions')).data })
  const audit = useQuery({ queryKey: ['audit'], queryFn: async () => (await api.get('/audit-logs')).data, enabled: has('audit.view') })
  const [userModal, setUserModal] = useState<{ open: boolean; editing?: any }>({ open: false })
  const [roleModal, setRoleModal] = useState<{ open: boolean; editing?: any }>({ open: false })
  const [userForm] = Form.useForm()
  const [roleForm] = Form.useForm()

  const saveUser = useMutation({
    mutationFn: async (values: any) => {
      const payload: any = { ...values, email: values.email || null, role_ids: values.role_ids || [] }
      if (!payload.new_password?.trim()) delete payload.new_password
      if (userModal.editing) return (await api.put(`/users/${userModal.editing.id}`, payload)).data
      return (await api.post('/users', payload)).data
    },
    onSuccess: async () => { message.success('用户已保存'); setUserModal({ open: false }); userForm.resetFields(); await client.invalidateQueries({ queryKey: ['users'] }); await client.invalidateQueries({ queryKey: ['audit'] }) },
    onError: (error) => message.error(errorMessage(error)),
  })

  const saveRole = useMutation({
    mutationFn: async (values: any) => {
      const payload = { ...values, permission_ids: values.permission_ids || [] }
      if (roleModal.editing) return (await api.put(`/roles/${roleModal.editing.id}`, payload)).data
      return (await api.post('/roles', payload)).data
    },
    onSuccess: async () => { message.success('角色已保存'); setRoleModal({ open: false }); roleForm.resetFields(); await client.invalidateQueries({ queryKey: ['roles'] }); await client.invalidateQueries({ queryKey: ['audit'] }) },
    onError: (error) => message.error(errorMessage(error)),
  })

  const permissionGroups = useMemo(() => {
    const groups: Record<string, any[]> = {}
    for (const item of permissions.data?.items || []) (groups[item.category] ||= []).push(item)
    return groups
  }, [permissions.data])

  const openUser = (editing?: any) => {
    setUserModal({ open: true, editing })
    setTimeout(() => userForm.setFieldsValue(editing ? {
      display_name: editing.display_name, email: editing.email, role_ids: editing.roles.map((item: any) => item.id),
      is_active: editing.is_active, must_change_password: editing.must_change_password, new_password: '',
    } : { is_active: true, must_change_password: true, role_ids: [] }), 0)
  }
  const openRole = (editing?: any) => {
    setRoleModal({ open: true, editing })
    setTimeout(() => roleForm.setFieldsValue(editing ? {
      display_name: editing.display_name, description: editing.description,
      permission_ids: editing.permissions.map((item: any) => item.id),
    } : { permission_ids: [] }), 0)
  }

  const userTab = <Card bordered={false} className="governance-card">
    <div className="section-toolbar"><div><Typography.Title level={5}>平台用户</Typography.Title><Typography.Text type="secondary">账号状态、角色和最近登录记录。</Typography.Text></div>{has('users.manage') && <Button type="primary" icon={<PlusOutlined />} onClick={() => openUser()}>新建用户</Button>}</div>
    <Table rowKey="id" loading={users.isLoading} dataSource={users.data?.items || []} pagination={false} columns={[
      { title: '用户', render: (_: unknown, row: any) => <Space direction="vertical" size={0}><Typography.Text strong>{row.display_name}</Typography.Text><Typography.Text type="secondary">@{row.username}</Typography.Text></Space> },
      { title: '邮箱', dataIndex: 'email', render: (value) => value || '-' },
      { title: '角色', render: (_: unknown, row: any) => <Space wrap>{row.roles.map((role: any) => <Tag color="blue" key={role.id}>{role.display_name}</Tag>)}</Space> },
      { title: '状态', dataIndex: 'is_active', width: 100, render: (value) => <Tag color={value ? 'green' : 'default'}>{value ? '启用' : '停用'}</Tag> },
      { title: '首次改密', dataIndex: 'must_change_password', width: 100, render: (value) => <Tag color={value ? 'orange' : 'green'}>{value ? '待完成' : '已完成'}</Tag> },
      { title: '最后登录', dataIndex: 'last_login_at', width: 170, render: formatTime },
      ...(has('users.manage') ? [{ title: '操作', width: 90, render: (_: unknown, row: any) => <Button type="link" icon={<EditOutlined />} onClick={() => openUser(row)}>编辑</Button> }] : []),
    ]} />
  </Card>

  const roleTab = <Card bordered={false} className="governance-card">
    <div className="section-toolbar"><div><Typography.Title level={5}>角色与权限</Typography.Title><Typography.Text type="secondary">权限由后端强制校验，前端菜单仅用于改善体验。</Typography.Text></div>{has('users.manage') && <Button type="primary" icon={<PlusOutlined />} onClick={() => openRole()}>新建角色</Button>}</div>
    <Row gutter={[16, 16]}>{(roles.data?.items || []).map((role: any) => <Col xs={24} xl={8} key={role.id}><Card className="role-card" title={<Space><SafetyCertificateOutlined /><span>{role.display_name}</span>{role.is_system && <Tag>系统</Tag>}</Space>} extra={has('users.manage') ? <Button type="text" icon={<EditOutlined />} onClick={() => openRole(role)} /> : null}>
      <Typography.Paragraph type="secondary">{role.description || '暂无说明'}</Typography.Paragraph>
      <Descriptions size="small" column={1}><Descriptions.Item label="角色标识">{role.name}</Descriptions.Item><Descriptions.Item label="用户数">{role.user_count}</Descriptions.Item><Descriptions.Item label="权限数">{role.permissions.length}</Descriptions.Item></Descriptions>
      <Space wrap>{role.permissions.slice(0, 8).map((item: any) => <Tag key={item.id}>{item.name}</Tag>)}{role.permissions.length > 8 && <Tag>+{role.permissions.length - 8}</Tag>}</Space>
    </Card></Col>)}</Row>
  </Card>

  const auditTab = <Card bordered={false} className="governance-card"><Table rowKey="id" loading={audit.isLoading} dataSource={audit.data?.items || []} pagination={{ pageSize: 20 }} columns={[
    { title: '时间', dataIndex: 'created_at', width: 170, render: formatTime },
    { title: '用户', dataIndex: 'username', width: 130 },
    { title: '动作', dataIndex: 'action', width: 190, render: (value) => <Tag color="geekblue">{value}</Tag> },
    { title: '资源', render: (_: unknown, row: any) => `${row.resource_type}${row.resource_id ? ` #${row.resource_id}` : ''}`, width: 180 },
    { title: '来源 IP', dataIndex: 'ip_address', width: 140, render: (value) => value || '-' },
    { title: '详情', dataIndex: 'details', render: (value) => <Typography.Text code>{JSON.stringify(value)}</Typography.Text> },
  ]} /></Card>

  return <Space direction="vertical" size={18} style={{ width: '100%' }}>
    <PageTitle title="用户与权限" subtitle="管理平台用户、角色授权和关键操作审计。" />
    <Row gutter={[16, 16]}>
      <Col xs={24} md={8}><Card className="mini-stat"><Typography.Text type="secondary">用户总数</Typography.Text><Typography.Title level={2}>{users.data?.total || 0}</Typography.Title></Card></Col>
      <Col xs={24} md={8}><Card className="mini-stat"><Typography.Text type="secondary">角色数量</Typography.Text><Typography.Title level={2}>{roles.data?.total || 0}</Typography.Title></Card></Col>
      <Col xs={24} md={8}><Card className="mini-stat"><Typography.Text type="secondary">权限项</Typography.Text><Typography.Title level={2}>{permissions.data?.items?.length || 0}</Typography.Title></Card></Col>
    </Row>
    <Tabs items={[
      { key: 'users', label: <Space><TeamOutlined />用户</Space>, children: userTab },
      { key: 'roles', label: <Space><SafetyCertificateOutlined />角色与权限</Space>, children: roleTab },
      ...(has('audit.view') ? [{ key: 'audit', label: <Space><AuditOutlined />审计日志</Space>, children: auditTab }] : []),
    ]} />

    <Modal title={userModal.editing ? `编辑用户 @${userModal.editing.username}` : '新建用户'} open={userModal.open} onCancel={() => setUserModal({ open: false })} onOk={() => userForm.submit()} confirmLoading={saveUser.isPending} destroyOnHidden>
      <Form form={userForm} layout="vertical" onFinish={(values) => saveUser.mutate(values)}>
        {!userModal.editing && <Form.Item name="username" label="用户名" rules={[{ required: true }, { pattern: /^[a-z0-9][a-z0-9._-]{2,63}$/, message: '使用小写字母、数字、点、下划线或短横线' }]}><Input /></Form.Item>}
        <Form.Item name="display_name" label="显示名称" rules={[{ required: true }]}><Input /></Form.Item>
        <Form.Item name="email" label="邮箱"><Input /></Form.Item>
        <Form.Item name={userModal.editing ? 'new_password' : 'password'} label={userModal.editing ? '重置密码（留空不修改）' : '初始密码'} rules={userModal.editing ? [] : [{ required: true }, { min: 10 }]}><Input.Password /></Form.Item>
        <Form.Item name="role_ids" label="角色" rules={[{ required: true }]}><Select mode="multiple" options={(roles.data?.items || []).map((role: any) => ({ value: role.id, label: role.display_name }))} /></Form.Item>
        <Form.Item name="is_active" label="账号状态"><Select options={[{ value: true, label: '启用' }, { value: false, label: '停用' }]} /></Form.Item>
        <Form.Item name="must_change_password" label="下次登录强制改密"><Select options={[{ value: true, label: '是' }, { value: false, label: '否' }]} /></Form.Item>
      </Form>
    </Modal>

    <Modal title={roleModal.editing ? `编辑角色 ${roleModal.editing.display_name}` : '新建角色'} width={760} open={roleModal.open} onCancel={() => setRoleModal({ open: false })} onOk={() => roleForm.submit()} confirmLoading={saveRole.isPending} destroyOnHidden>
      <Form form={roleForm} layout="vertical" onFinish={(values) => saveRole.mutate(values)}>
        {!roleModal.editing && <Form.Item name="name" label="角色标识" rules={[{ required: true }, { pattern: /^[a-z0-9][a-z0-9._-]{2,63}$/ }]}><Input /></Form.Item>}
        <Form.Item name="display_name" label="显示名称" rules={[{ required: true }]}><Input /></Form.Item>
        <Form.Item name="description" label="说明"><Input.TextArea rows={2} /></Form.Item>
        <Form.Item name="permission_ids" label="权限范围">
          <Checkbox.Group style={{ width: '100%' }}>
            <Space direction="vertical" size={14} style={{ width: '100%' }}>{Object.entries(permissionGroups).map(([category, items]) => <Card size="small" title={category} key={category}><Row gutter={[12, 10]}>{items.map((item: any) => <Col xs={24} md={12} key={item.id}><Checkbox value={item.id}><strong>{item.name}</strong><div className="permission-code">{item.code}</div></Checkbox></Col>)}</Row></Card>)}</Space>
          </Checkbox.Group>
        </Form.Item>
      </Form>
    </Modal>
  </Space>
}

export function ReleasesPage() {
  const { has } = useAuth()
  const client = useQueryClient()
  const query = useQuery({ queryKey: ['releases'], queryFn: async () => (await api.get('/releases')).data })
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm()
  const save = useMutation({
    mutationFn: async (values: any) => (await api.post('/releases', { ...values, changes: String(values.changes || '').split('\n').map((item) => item.trim()).filter(Boolean), is_current: true })).data,
    onSuccess: async () => { message.success('版本说明已记录'); setOpen(false); form.resetFields(); await client.invalidateQueries({ queryKey: ['releases'] }) },
    onError: (error) => message.error(errorMessage(error)),
  })
  const current = query.data?.current
  return <Space direction="vertical" size={18} style={{ width: '100%' }}>
    <PageTitle title="版本说明" subtitle="记录每次平台发布的功能、修复、Git 提交和发布时间。" actions={has('versions.manage') ? <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>记录新版本</Button> : undefined} />
    {current && <Card className="release-hero" bordered={false}><Space direction="vertical" size={10}><Space wrap><Tag color="blue">CURRENT</Tag><Typography.Title level={2} style={{ margin: 0 }}>v{current.version}</Typography.Title></Space><Typography.Title level={4} style={{ margin: 0 }}>{current.title}</Typography.Title><Typography.Paragraph>{current.summary}</Typography.Paragraph><Space wrap><Typography.Text type="secondary">发布于 {formatTime(current.released_at)}</Typography.Text>{current.commit_sha && <Typography.Text code>{current.commit_sha}</Typography.Text>}</Space></Space></Card>}
    <Card title="版本时间线" bordered={false}>
      <Timeline items={(query.data?.items || []).map((release: any) => ({ color: release.is_current ? 'blue' : 'gray', children: <div className="release-entry"><Space wrap><Typography.Title level={4}>v{release.version}</Typography.Title>{release.is_current && <Tag color="blue">当前版本</Tag>}<Typography.Text type="secondary">{formatTime(release.released_at)}</Typography.Text></Space><Typography.Text strong>{release.title}</Typography.Text><Typography.Paragraph type="secondary">{release.summary}</Typography.Paragraph><List size="small" dataSource={release.changes || []} renderItem={(item: string) => <List.Item>• {item}</List.Item>} />{release.commit_sha && <Typography.Text code>{release.commit_sha}</Typography.Text>}</div> }))} />
    </Card>
    <Modal title="记录新版本" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} confirmLoading={save.isPending} destroyOnHidden>
      <Form form={form} layout="vertical" onFinish={(values) => save.mutate(values)}>
        <Form.Item name="version" label="版本号" rules={[{ required: true }]}><Input placeholder="0.8.0" /></Form.Item>
        <Form.Item name="title" label="版本标题" rules={[{ required: true }]}><Input /></Form.Item>
        <Form.Item name="summary" label="版本摘要"><Input.TextArea rows={2} /></Form.Item>
        <Form.Item name="changes" label="变更项（每行一条）" rules={[{ required: true }]}><Input.TextArea rows={7} /></Form.Item>
        <Form.Item name="commit_sha" label="Git Commit"><Input /></Form.Item>
      </Form>
    </Modal>
  </Space>
}
