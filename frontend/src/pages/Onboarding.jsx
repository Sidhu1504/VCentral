import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Alert, App, Button, Card, Col, Progress, Row, Space, Statistic, Table, Tabs, Tag, Tooltip, Typography } from 'antd'
import { SendOutlined } from '@ant-design/icons'
import { ToolsCell } from '../components/Tools'
import { api } from '../api'
import { useAuth } from '../auth'
import InstallAgentModal from '../components/InstallAgentModal'
import { CriticalStar } from '../components/Health'
import { fmtAgo, fmtDate, STATUS } from '../format'
import { OsIcon } from './VMs'

const JOB = { succeeded: ['success', 'Installed'], failed: ['error', 'Install failed'], running: ['processing', 'Installing'], queued: ['processing', 'Queued'] }

function Queue({ data, reload }) {
  const { can } = useAuth()
  const [installing, setInstalling] = useState(null)
  return (
    <>
      <Table rowKey="id" size="small" dataSource={data.items} scroll={{ x: 1000 }} pagination={{ pageSize: 50, hideOnSinglePage: true }}
        locale={{ emptyText: 'Every powered-on VM has an agent.' }}
        columns={[
          { title: 'VM', dataIndex: 'name', width: 220, ellipsis: true, render: (v, r) => <Space size={6}><CriticalStar critical={r.critical} /><OsIcon family={r.os_family} /><Link to={`/vms/${r.id}`}>{v}</Link></Space> },
          { title: 'Owner', dataIndex: 'owner', width: 220, ellipsis: true, render: (v) => v || <Tag color="warning">Unknown</Tag> },
          { title: 'IP address', dataIndex: 'primary_ip', width: 140, render: (v) => v || '—' },
          {
            title: 'Without agent', dataIndex: 'unmanaged_since', width: 150,
            render: (v, r) => <Tooltip title={fmtDate(v)}><span style={r.overdue ? { color: STATUS.critical, fontWeight: 600 } : null}>{v ? fmtAgo(v).replace(' ago', '') : '—'}{r.overdue && ' (overdue)'}</span></Tooltip>,
          },
          {
            title: 'VMware Tools', dataIndex: 'tools', width: 240,
            render: (_, r) => <ToolsCell vm={r} onWoken={reload} />,
          },
          {
            title: 'Last install', dataIndex: 'last_push', width: 150,
            render: (j) => j && <Tooltip title={j.error || `${fmtDate(j.created_at)} by ${j.requested_by}`}><Tag color={JOB[j.status][0]}>{JOB[j.status][1]}</Tag></Tooltip>,
          },
          ...(can('admin', 'operator') ? [{
            title: '', key: 'a', width: 130, fixed: 'right',
            render: (_, r) => <Button size="small" type="primary" onClick={() => setInstalling(r)}>Install agent</Button>,
          }] : []),
        ]} />
      {installing && <InstallAgentModal vm={installing} onClose={() => { setInstalling(null); reload() }} onDone={reload} />}
    </>
  )
}

function Messages() {
  const [rows, setRows] = useState([])
  useEffect(() => { api.get('/api/notifications').then(setRows).catch(() => {}) }, [])
  return (
    <Table rowKey="id" size="small" dataSource={rows} pagination={{ pageSize: 25, hideOnSinglePage: true }} scroll={{ x: 900 }}
      locale={{ emptyText: 'No messages sent yet' }}
      columns={[
        { title: 'When', dataIndex: 'ts', width: 160, render: fmtDate },
        { title: 'Type', dataIndex: 'kind', width: 90 },
        { title: 'Channel', dataIndex: 'channel', width: 110, render: (c) => (c === 'chat' ? 'Google Chat' : 'Email') },
        { title: 'To', dataIndex: 'recipient', width: 220, ellipsis: true },
        { title: 'Subject', dataIndex: 'subject', ellipsis: true },
        { title: 'Result', dataIndex: 'success', width: 100, render: (ok, r) => (ok ? <Tag color="success">Sent</Tag> : <Tooltip title={r.error}><Tag color="error">Failed</Tag></Tooltip>) },
      ]} />
  )
}

export default function Onboarding() {
  const { can } = useAuth()
  const { message } = App.useApp()
  const [data, setData] = useState(null)
  const load = useCallback(() => api.get('/api/onboarding').then(setData, (e) => message.error(e.message)), [message])
  useEffect(() => {
    load()
    const t = setInterval(load, 30000)
    return () => clearInterval(t)
  }, [load])

  if (!data) return null
  const s = data.stats
  const pct = s.powered_on ? Math.round((s.managed_on / s.powered_on) * 100) : 0
  const sendDigest = async () => {
    try {
      const r = await api.post('/api/onboarding/digest')
      if (r.sent) message.success(`Digest sent to the admin team (${r.vm_count} VMs)`)
      else if (!r.vm_count) message.info('Nothing to send: every powered-on VM has the agent')
      else message.error('Not sent: configure admin emails and/or Google Chat (see Settings)')
      load()
    } catch (e) {
      message.error(e.message)
    }
  }
  return (
    <>
      <Space style={{ width: '100%', justifyContent: 'space-between' }} wrap>
        <Typography.Title level={3} style={{ marginTop: 0 }}>Onboarding</Typography.Title>
        {can('admin', 'operator') && <Button icon={<SendOutlined />} onClick={sendDigest}>Send digest now</Button>}
      </Space>
      <Typography.Paragraph type="secondary">
        Powered-on VMs without the agent. Install it through VMware Tools with the VM's root, sudo-capable user or Administrator login (used
        once, never saved), or with a one-time command.
      </Typography.Paragraph>
      {(!data.policy.enabled || (!data.channels.email && !data.channels.chat)) && (
        <Alert type="info" showIcon style={{ marginBottom: 12 }}
          message={!data.policy.enabled ? 'The scheduled digest to the admin team is off.' : 'No admin emails or Google Chat configured, so the digest cannot be sent.'}
          description={can('admin') ? <>Set it up in <Link to="/settings">Settings</Link>.</> : 'An admin can set it up in Settings.'} />
      )}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} md={8}>
          <Card size="small"><Typography.Text type="secondary">Powered-on VMs with an agent</Typography.Text>
            <Progress percent={pct} format={() => `${s.managed_on} / ${s.powered_on}`} strokeColor={STATUS.good} />
          </Card>
        </Col>
        <Col xs={12} md={4}><Card size="small"><Statistic title="Without agent" value={s.unmanaged} /></Card></Col>
        <Col xs={12} md={4}><Card size="small"><Statistic title={`Overdue (> ${data.policy.overdue_after_days} d)`} value={s.overdue} valueStyle={s.overdue ? { color: STATUS.critical } : null} /></Card></Col>
        <Col xs={12} md={4}><Card size="small"><Statistic title="Installable via Tools" value={s.tools_running} /></Card></Col>
        <Col xs={12} md={4}><Card size="small"><Statistic title="Unknown owner" value={s.no_owner} /></Card></Col>
      </Row>
      <Card size="small" extra={data.last_digest && <Typography.Text type="secondary">Last digest {fmtAgo(data.last_digest)}</Typography.Text>}>
        <Tabs items={[
          { key: 'queue', label: `Without agent (${s.unmanaged})`, children: <Queue data={data} reload={load} /> },
          ...(can('admin', 'operator') ? [{ key: 'messages', label: 'Messages sent', children: <Messages /> }] : []),
        ]} />
      </Card>
    </>
  )
}
