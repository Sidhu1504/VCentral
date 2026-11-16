import { useState } from 'react'
import { Alert, Button, Form, Typography } from 'antd'
import { useAuth } from '../auth'
import { PasswordFields, submitPasswordChange } from '../components/ChangePasswordModal'
import { CenteredCard } from './Login'

export default function ForcePasswordChange() {
  const { setUser, logout, user } = useAuth()
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  const onFinish = async (values) => {
    setLoading(true)
    setError(null)
    try {
      await submitPasswordChange(values, setUser)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <CenteredCard width={420}>
      <Typography.Title level={4}>Set a new password</Typography.Title>
      <Typography.Paragraph type="secondary">
        Hi {user.full_name || user.username}, you need to choose your own password before continuing.
      </Typography.Paragraph>
      {error && <Alert type="error" message={error} showIcon style={{ marginBottom: 16 }} />}
      <Form layout="vertical" onFinish={onFinish} requiredMark={false}>
        <PasswordFields />
        <Button type="primary" htmlType="submit" block loading={loading}>Save password</Button>
        <Button type="link" block onClick={logout} style={{ marginTop: 8 }}>Sign out</Button>
      </Form>
    </CenteredCard>
  )
}
