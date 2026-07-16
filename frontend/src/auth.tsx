import {
  ApiOutlined,
  CheckCircleFilled,
  CloudServerOutlined,
  LockOutlined,
  RadarChartOutlined,
  SafetyCertificateOutlined,
  UserOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Card, Form, Input, Result, Spin, Typography, message } from 'antd'
import type { InputRef } from 'antd'
import axios from 'axios'
import { createContext, useContext, useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { api } from './client'
import { APP_ENVIRONMENT, APP_VERSION } from './version'

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
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) return detail.map((item) => item?.msg).filter(Boolean).join('；') || error.message
    return error.message
  }
  return String(error)
}

function loginErrorMessage(error: unknown) {
  if (!axios.isAxiosError(error)) return '登录失败，请稍后重试。'
  if (!error.response) return '无法连接到平台服务，请检查网络后重试。'
  if (error.response.status === 401) return '用户名或密码错误，请重新输入。'
  if (error.response.status === 422) return '请输入有效的用户名和密码。'
  if (error.response.status === 429) return '登录尝试过于频繁，请稍后再试。'
  return errorMessage(error) || '登录失败，请稍后重试。'
}

function AuthShell({ children }: { children: ReactNode }) {
  return (
    <div className="auth-shell">
      <section className="auth-brand-panel" aria-label="平台能力介绍">
        <div className="auth-brand-top">
          <div className="auth-logo"><SafetyCertificateOutlined /></div>
          <div>
            <span className="auth-brand-kicker">WORK&apos;S K8S</span>
            <strong>AIOps Console</strong>
          </div>
        </div>

        <div className="auth-brand-copy">
          <Typography.Title level={1}>把告警、变更与运行证据汇成一条可信调查链。</Typography.Title>
          <Typography.Paragraph>
            面向 Kubernetes 的事件关联、证据采集与 AI 辅助诊断工作台，帮助运维人员更快定位问题并保留完整审计轨迹。
          </Typography.Paragraph>
        </div>

        <div className="auth-capability-grid">
          <div className="auth-capability-card">
            <CloudServerOutlined />
            <strong>统一证据</strong>
            <span>Kubernetes、Prometheus、Loki 与 Trace</span>
          </div>
          <div className="auth-capability-card">
            <RadarChartOutlined />
            <strong>可信调查</strong>
            <span>确定性基线、Agent Shadow 与离线重放</span>
          </div>
          <div className="auth-capability-card">
            <ApiOutlined />
            <strong>治理闭环</strong>
            <span>角色权限、操作审计与版本追踪</span>
          </div>
        </div>

        <div className="auth-trust-strip">
          <span className="auth-live-dot" />
          <span>Agent Shadow 默认启用</span>
          <span className="auth-trust-divider" />
          <span>安全门槛持续校验</span>
        </div>
      </section>
      <main className="auth-form-panel">{children}</main>
    </div>
  )
}

function LoginPage({ onSuccess }: { onSuccess: () => Promise<unknown> }) {
  const [form] = Form.useForm<{ username: string; password: string }>()
  const passwordRef = useRef<InputRef>(null)
  const [loginError, setLoginError] = useState('')
  const [capsLock, setCapsLock] = useState(false)

  const mutation = useMutation({
    mutationFn: async (values: { username: string; password: string }) => (
      await api.post('/auth/login', { ...values, username: values.username.trim() })
    ).data,
    onMutate: () => setLoginError(''),
    onSuccess: async () => {
      form.setFieldValue('password', '')
      message.success('登录成功')
      await onSuccess()
    },
    onError: (error) => {
      setLoginError(loginErrorMessage(error))
      form.setFieldValue('password', '')
      requestAnimationFrame(() => passwordRef.current?.focus())
    },
  })

  const updateCapsLock = (event: KeyboardEvent<HTMLInputElement>) => {
    setCapsLock(event.getModifierState('CapsLock'))
  }

  return (
    <AuthShell>
      <Card className="auth-card" bordered={false}>
        <div className="auth-card-header">
          <div>
            <Typography.Text className="auth-eyebrow">SECURE OPERATIONS ACCESS</Typography.Text>
            <Typography.Title level={2}>欢迎回来</Typography.Title>
            <Typography.Paragraph type="secondary">使用平台管理员分配的账号登录运维工作台。</Typography.Paragraph>
          </div>
          <div className="auth-service-status" role="status">
            <CheckCircleFilled />
            <span>安全会话</span>
          </div>
        </div>

        {loginError && (
          <Alert
            className="auth-login-alert"
            type="error"
            showIcon
            message="登录未成功"
            description={loginError}
            closable
            onClose={() => setLoginError('')}
          />
        )}

        <Form
          className="auth-login-form"
          form={form}
          layout="vertical"
          size="large"
          requiredMark={false}
          onValuesChange={() => loginError && setLoginError('')}
          onFinish={(values) => !mutation.isPending && mutation.mutate(values)}
        >
          <Form.Item
            name="username"
            label="用户名"
            rules={[{ required: true, whitespace: true, message: '请输入用户名' }]}
          >
            <Input
              prefix={<UserOutlined />}
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              autoFocus
              placeholder="请输入用户名"
            />
          </Form.Item>
          <Form.Item
            name="password"
            label="密码"
            rules={[{ required: true, message: '请输入密码' }]}
            extra={capsLock ? <span className="auth-caps-warning">大写锁定已开启，请确认密码大小写。</span> : null}
          >
            <Input.Password
              ref={passwordRef}
              prefix={<LockOutlined />}
              autoComplete="current-password"
              placeholder="请输入密码"
              status={loginError ? 'error' : undefined}
              onKeyDown={updateCapsLock}
              onKeyUp={updateCapsLock}
              onBlur={() => setCapsLock(false)}
            />
          </Form.Item>
          <Button
            className="auth-submit"
            block
            type="primary"
            htmlType="submit"
            loading={mutation.isPending}
          >
            {mutation.isPending ? '正在验证…' : '安全登录'}
          </Button>
        </Form>

        <div className="auth-security-note">
          <SafetyCertificateOutlined />
          <span>认证失败不会暴露账号状态，错误尝试会写入安全审计。</span>
        </div>
        <div className="auth-version">
          <span>AIOps Console v{APP_VERSION}</span>
          <span>{APP_ENVIRONMENT}</span>
        </div>
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
        <Typography.Text className="auth-eyebrow">ACCOUNT SECURITY</Typography.Text>
        <Typography.Title level={2}>首次登录修改密码</Typography.Title>
        <Typography.Paragraph type="secondary">完成密码更新后即可进入运维工作台。</Typography.Paragraph>
        <Alert className="auth-login-alert" type="warning" showIcon message="初始密码仅用于首次登录" description="新密码至少 10 个字符，修改成功后当前会话会自动更新。" />
        <Form className="password-form" layout="vertical" size="large" requiredMark={false} onFinish={(values) => mutation.mutate(values)}>
          <Form.Item name="current_password" label="当前密码" rules={[{ required: true, message: '请输入当前密码' }]}><Input.Password autoComplete="current-password" /></Form.Item>
          <Form.Item name="new_password" label="新密码" rules={[{ required: true, message: '请输入新密码' }, { min: 10, message: '至少 10 个字符' }]}><Input.Password autoComplete="new-password" /></Form.Item>
          <Form.Item name="confirm" label="确认新密码" dependencies={['new_password']} rules={[{ required: true, message: '请再次输入新密码' }, ({ getFieldValue }) => ({ validator(_, value) { return !value || getFieldValue('new_password') === value ? Promise.resolve() : Promise.reject(new Error('两次密码不一致')) } })]}><Input.Password autoComplete="new-password" /></Form.Item>
          <Button className="auth-submit" block type="primary" htmlType="submit" loading={mutation.isPending}>保存新密码</Button>
        </Form>
        <div className="auth-version"><span>AIOps Console v{APP_VERSION}</span><span>{APP_ENVIRONMENT}</span></div>
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
