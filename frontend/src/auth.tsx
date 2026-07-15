import { LockOutlined, SafetyCertificateOutlined, UserOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Card, Form, Input, Result, Space, Spin, Typography, message } from 'antd'
import axios from 'axios'
import { createContext, useContext, useEffect, type ReactNode } from 'react'
import { api } from './client'

export type AuthUser = {
  id: number
  username: string
  display_name: string
  email?: string | null
  must_change_password: boolean
  roles: Array<{ id: number; name: string; display_name: string }>
  permissions: string[]
}

type AuthContextValue = {
  user: AuthUser
  has: (permission: string) => boolean
  refresh: () => Promise<unknown>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

function errorMessage(error: unknown) {
  if (axios.isAxiosError(error)) return error.response?.data?.detail || error.message
  return String(error)
}

function AuthShell({ children }: { children: ReactNode }) {
  return (
    <div className="auth-shell">
      <div className="auth-brand-panel">
        <div className="auth-logo"><SafetyCertificateOutlined /></div>
        <Typography.Title level={1}>AIOps Console</Typography.Title>
        <Typography.Paragraph>面向 Kubernetes 的事件关联、证据采集与 AI 辅助诊断工作台。</Typography.Paragraph>
        <div className="auth-feature-list">
          <span>告警与变更自动关联</span>
          <span>Prometheus、Loki、Kubernetes 与 Trace 证据</span>
          <span>用户、角色、权限和审计闭环</span>
        </div>
      </div>
      <div className="auth-form-panel">{children}</div>
    </div>
  )
}

function LoginPage({ onSuccess }: { onSuccess: () => Promise<unknown> }) {
  const mutation = useMutation({
    mutationFn: async (values: { username: string; password: string }) => (await api.post('/auth/login', values)).data,
    onSuccess: async () => { message.success('登录成功'); await onSuccess() },
    onError: (error) => message.error(errorMessage(error)),
  })
  return (
    <AuthShell>
      <Card className="auth-card" bordered={false}>
        <Typography.Text className="auth-eyebrow">WORK&apos;S K8S</Typography.Text>
        <Typography.Title level={2}>登录运维工作台</Typography.Title>
        <Typography.Paragraph type="secondary">使用平台管理员分配的账号登录。</Typography.Paragraph>
        <Form layout="vertical" size="large" onFinish={(values) => mutation.mutate(values)}>
          <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input prefix={<UserOutlined />} autoComplete="username" autoFocus />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password prefix={<LockOutlined />} autoComplete="current-password" />
          </Form.Item>
          <Button block type="primary" htmlType="submit" loading={mutation.isPending}>登录</Button>
        </Form>
        <div className="auth-version">AIOps Console 0.7.0</div>
      </Card>
    </AuthShell>
  )
}

function ChangePasswordGate({ onSuccess }: { onSuccess: () => Promise<unknown> }) {
  const mutation = useMutation({
    mutationFn: async (values: any) => (await api.post('/auth/change-password', values)).data,
    onSuccess: async () => { message.success('密码修改成功'); await onSuccess() },
    onError: (error) => message.error(errorMessage(error)),
  })
  return (
    <AuthShell>
      <Card className="auth-card" bordered={false}>
        <Typography.Title level={2}>首次登录修改密码</Typography.Title>
        <Alert type="warning" showIcon message="初始密码仅用于首次登录" description="修改成功后，当前会话会自动更新。新密码至少 10 个字符。" />
        <Form className="password-form" layout="vertical" size="large" onFinish={(values) => mutation.mutate(values)}>
          <Form.Item name="current_password" label="当前密码" rules={[{ required: true }]}><Input.Password autoComplete="current-password" /></Form.Item>
          <Form.Item name="new_password" label="新密码" rules={[{ required: true }, { min: 10, message: '至少 10 个字符' }]}><Input.Password autoComplete="new-password" /></Form.Item>
          <Form.Item name="confirm" label="确认新密码" dependencies={['new_password']} rules={[{ required: true }, ({ getFieldValue }) => ({ validator(_, value) { return !value || getFieldValue('new_password') === value ? Promise.resolve() : Promise.reject(new Error('两次密码不一致')) } })]}><Input.Password autoComplete="new-password" /></Form.Item>
          <Button block type="primary" htmlType="submit" loading={mutation.isPending}>保存新密码</Button>
        </Form>
      </Card>
    </AuthShell>
  )
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient()
  const me = useQuery({
    queryKey: ['auth-me'],
    queryFn: async () => (await api.get('/auth/me')).data.user as AuthUser,
    retry: false,
    staleTime: 60_000,
  })

  useEffect(() => {
    const handler = () => client.invalidateQueries({ queryKey: ['auth-me'] })
    window.addEventListener('aiops-auth-expired', handler)
    return () => window.removeEventListener('aiops-auth-expired', handler)
  }, [client])

  useEffect(() => {
    const interceptor = api.interceptors.response.use(
      (response) => response,
      (error) => {
        const url = String(error.config?.url || '')
        if (error.response?.status === 401 && !url.includes('/auth/login')) {
          window.dispatchEvent(new Event('aiops-auth-expired'))
        }
        return Promise.reject(error)
      },
    )
    return () => api.interceptors.response.eject(interceptor)
  }, [])

  if (me.isLoading) return <div className="app-loading"><Spin size="large" /><Typography.Text type="secondary">正在验证会话…</Typography.Text></div>
  if (!me.data) return <LoginPage onSuccess={() => me.refetch()} />
  if (me.data.must_change_password) return <ChangePasswordGate onSuccess={() => me.refetch()} />

  const value: AuthContextValue = {
    user: me.data,
    has: (permission) => me.data.permissions.includes(permission),
    refresh: () => me.refetch(),
    logout: async () => {
      await api.post('/auth/logout')
      client.clear()
      await me.refetch()
    },
  }
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used within AuthProvider')
  return value
}

export function PermissionRoute({ permission, children }: { permission: string; children: ReactNode }) {
  const { has } = useAuth()
  if (!has(permission)) return <Result status="403" title="403" subTitle={`当前账号缺少权限：${permission}`} />
  return <>{children}</>
}
