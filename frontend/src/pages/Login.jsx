import { useState } from 'react'
import { Alert, Button, Card, Form, Input, Typography } from 'antd'
import { LockOutlined, UserOutlined } from '@ant-design/icons'
import { useAuth } from '../auth'

export function CenteredCard({ children, width = 380 }) {
  return (
    <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', padding: 16 }}>
      <Card style={{ width: '100%', maxWidth: width }}>{children}</Card>
    </div>
  )
}

export default function Login() {
  const { login } = useAuth()
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  const onFinish = async ({ username, password }) => {
    setLoading(true)
    setError(null)
    try {
      await login(username, password)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <CenteredCard>
      <div style={{ textAlign: 'center', marginBottom: 24 }}>
        <img src="/favicon.svg" alt="" width={40} />
        <Typography.Title level={3} style={{ margin: '8px 0 0' }}>vCentral</Typography.Title>
        <Typography.Text type="secondary">Central management for your vSphere VMs</Typography.Text>
      </div>
      {error && <Alert type="error" message={error} showIcon style={{ marginBottom: 16 }} />}
      <Form layout="vertical" onFinish={onFinish} requiredMark={false}>
        <Form.Item name="username" rules={[{ required: true, message: 'Enter your username' }]}>
          <Input prefix={<UserOutlined />} placeholder="Username" autoComplete="username" autoFocus size="large" />
        </Form.Item>
        <Form.Item name="password" rules={[{ required: true, message: 'Enter your password' }]}>
          <Input.Password prefix={<LockOutlined />} placeholder="Password" autoComplete="current-password" size="large" />
        </Form.Item>
        <Button type="primary" htmlType="submit" block size="large" loading={loading}>
          Sign in
        </Button>
      </Form>
    </CenteredCard>
  )
}
