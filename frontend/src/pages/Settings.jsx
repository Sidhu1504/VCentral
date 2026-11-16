import { useEffect, useState } from 'react'
import { Alert, App, Button, Card, Descriptions, Form, Input, InputNumber, Radio, Skeleton, Space, Switch, Tag, Typography } from 'antd'
import { api } from '../api'
import { useAuth } from '../auth'
import { INTERVAL_LABEL } from '../format'

const FIELDS = [
  {
    key: 'agent_report_interval_seconds',
    label: 'Agent metrics interval',
    help: 'How often each agent sends CPU, memory, disk, network and top processes. Shorter means fresher graphs and faster “agent offline” detection, and more data stored.',
  },
  {
    key: 'agent_inventory_interval_seconds',
    label: 'Agent inventory interval',
    help: 'How often agents send installed software, services and open ports. You can also refresh one VM at any time from its page.',
  },
  {
    key: 'sync_interval_seconds',
    label: 'vCenter sync interval',
    help: 'How often vCentral reads all VMs from vCenter: power state, IP addresses (also for VMs without an agent), creates and deletes. This is what detects a VM that lost its IP, because VMware Tools still reports when the network is down.',
  },
]

export default function Settings() {
  const { can } = useAuth()
  const { message } = App.useApp()
  const [form] = Form.useForm()
  const [data, setData] = useState(null)
  const [saving, setSaving] = useState(false)
  const editable = can('admin')

  useEffect(() => {
    api.get('/api/system/settings').then((d) => { setData(d); form.setFieldsValue(d.values) }, (e) => message.error(e.message))
  }, [form, message])

  const save = async (values) => {
    setSaving(true)
    try {
      const d = await api.put('/api/system/settings', values)
      setData(d)
      message.success('Saved. Agents pick up new intervals with their next report.')
    } catch (e) {
      message.error(e.message)
    } finally {
      setSaving(false)
    }
  }

  if (!data) return <Skeleton active />
  const samples = (86400 / data.values.agent_report_interval_seconds) * 300
  const perDay = samples >= 1e6 ? `${(samples / 1e6).toFixed(1)} million` : `${Math.round(samples / 1000)} thousand`
  return (
    <>
      <Typography.Title level={3} style={{ marginTop: 0 }}>Settings</Typography.Title>
      {!editable && <Alert type="info" showIcon style={{ marginBottom: 16 }} message="Only admins can change these settings." />}
      <Card size="small" style={{ maxWidth: 820 }}>
        <Form form={form} layout="vertical" onFinish={save} disabled={!editable}>
          {FIELDS.map((f) => (
            <Form.Item key={f.key} name={f.key} label={<b>{f.label}</b>} extra={f.help}>
              <Radio.Group optionType="button" buttonStyle="solid"
                options={data.choices[f.key].map((v) => ({ value: v, label: INTERVAL_LABEL(v) + (v === data.defaults[f.key] ? ' (default)' : '') }))} />
            </Form.Item>
          ))}
          <Typography.Paragraph type="secondary">
            An agent is shown as offline after {data.agent_offline_after_seconds} seconds without a report (three missed reports).
            At the current interval, 300 agents store about {perDay} samples a day; raw samples are kept for 7 days, then hourly averages.
          </Typography.Paragraph>
          {editable && <Button type="primary" htmlType="submit" loading={saving}>Save</Button>}
        </Form>
      </Card>
      <OnboardingSettings data={data} setData={setData} editable={editable} />
    </>
  )
}

function OnboardingSettings({ data, setData, editable }) {
  const { message } = App.useApp()
  const [form] = Form.useForm()
  const [saving, setSaving] = useState(false)
  const [testTo, setTestTo] = useState('')
  const n = data.notifications

  useEffect(() => { form.setFieldsValue(data.onboarding) }, [data.onboarding, form])

  const save = async (values) => {
    setSaving(true)
    try {
      setData(await api.put('/api/system/settings', { onboarding: values }))
      message.success('Reminder policy saved')
    } catch (e) {
      message.error(e.message)
    } finally {
      setSaving(false)
    }
  }
  const test = async (channel) => {
    try {
      const r = await api.post('/api/system/notifications/test', { channel, to: channel === 'email' ? testTo || null : null })
      r.ok ? message.success('Test message sent') : message.error(`Not sent: ${r.error}`)
    } catch (e) {
      message.error(e.message)
    }
  }
  const status = (ok, text) => (ok ? <Tag color="success">{text}</Tag> : <Tag>not configured</Tag>)

  return (
    <Card size="small" title="Onboarding digest and notifications" style={{ maxWidth: 820, marginTop: 16 }}>
      <Typography.Paragraph type="secondary">
        A summary of powered-on VMs without the agent, grouped by owner with overdue VMs first, sent to the admin team
        (admin emails and the Google Chat space). Nothing is ever sent to VM owners or other end users.
      </Typography.Paragraph>
      <Form form={form} layout="inline" onFinish={save} disabled={!editable} style={{ rowGap: 12 }}>
        <Form.Item name="enabled" label="Send digest" valuePropName="checked"><Switch /></Form.Item>
        <Form.Item name="every_days" label="Every" rules={[{ required: true }]}>
          <InputNumber min={1} max={30} addonAfter="days" style={{ width: 130 }} />
        </Form.Item>
        <Form.Item name="include_after_days" label="Include VMs without agent for" rules={[{ required: true }]}
          tooltip="Gives the team time to install the agent on brand-new VMs before they appear in the digest">
          <InputNumber min={0} max={30} addonAfter="days" style={{ width: 130 }} />
        </Form.Item>
        <Form.Item name="overdue_after_days" label="Overdue after" rules={[{ required: true }]}>
          <InputNumber min={1} max={180} addonAfter="days" style={{ width: 140 }} />
        </Form.Item>
        {editable && <Form.Item><Button type="primary" htmlType="submit" loading={saving}>Save</Button></Form.Item>}
      </Form>
      <Descriptions size="small" column={1} style={{ marginTop: 20 }} title="Delivery (set in .env)">
        <Descriptions.Item label="Email">{status(n.email, `${n.smtp_from} via ${n.smtp_host}`)}</Descriptions.Item>
        <Descriptions.Item label="Google Chat">{status(n.chat, 'webhook set')}</Descriptions.Item>
        <Descriptions.Item label="Admin team emails">{n.admin_emails.length ? n.admin_emails.join(', ') : <Tag>none</Tag>}</Descriptions.Item>
        <Descriptions.Item label="Link in messages">{n.public_url || <Tag>none (set VCENTRAL_PUBLIC_URL)</Tag>}</Descriptions.Item>
      </Descriptions>
      {editable && (
        <Space wrap style={{ marginTop: 8 }}>
          <Input placeholder="Send test email to (default: you / admins)" style={{ width: 300 }} value={testTo} onChange={(e) => setTestTo(e.target.value)} />
          <Button onClick={() => test('email')}>Send test email</Button>
          <Button onClick={() => test('chat')}>Send test to Google Chat</Button>
        </Space>
      )}
    </Card>
  )
}
