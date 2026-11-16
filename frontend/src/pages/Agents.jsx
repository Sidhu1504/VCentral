import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import {
  Alert, App, Button, Card, Collapse, Form, Input, InputNumber, Modal, Popconfirm, Radio, Segmented, Select, Space, Table, Tabs,
  Tag, Tooltip, Typography,
} from 'antd'
import { DownloadOutlined, LinkOutlined, PlusOutlined } from '@ant-design/icons'
import { api } from '../api'
import { useAuth } from '../auth'
import InstallCommands, { buildUpgradeCommands } from '../components/InstallCommands'
import { AGENT_STATE, fmtAgo, fmtDate } from '../format'
import { OsIcon } from './VMs'

export function AgentStateTag({ state }) {
  const s = AGENT_STATE[state] || { label: state, color: 'default' }
  return <Tag color={s.color}>{s.label}</Tag>
}

/** Pick a VM for an agent, or go back to automatic matching. */
export function LinkVmModal({ agent, onClose, onSaved }) {
  const { message } = App.useApp()
  const [options, setOptions] = useState([])
  const [vmId, setVmId] = useState(agent.vm_id)

  const search = useCallback((text) => {
    api.get('/api/vms', { search: text, page_size: 20 })
      .then((d) => setOptions(d.items.map((v) => ({ value: v.id, label: `${v.name}${v.primary_ip ? ` (${v.primary_ip})` : ''}` }))))
      .catch(() => {})
  }, [])
  useEffect(() => search(agent.hostname || ''), [agent.hostname, search])

  const save = async (auto) => {
    try {
      const res = auto
        ? await api.post(`/api/agents/${agent.id}/link/auto`)
        : await api.post(`/api/agents/${agent.id}/link`, { vm_id: vmId ?? null })
      message.success(res.vm_name ? `Linked to ${res.vm_name}` : 'Not linked to any VM')
      onSaved(res)
      onClose()
    } catch (e) {
      message.error(e.message)
    }
  }

  return (
    <Modal title={`Link ${agent.hostname} to a VM`} open onCancel={onClose} onOk={() => save(false)} okText="Save link"
      footer={(_, { OkBtn, CancelBtn }) => (<><Button onClick={() => save(true)}>Use automatic matching</Button><CancelBtn /><OkBtn /></>)}>
      <Typography.Paragraph type="secondary">
        Agents are matched to VMs automatically by BIOS UUID or MAC address. Set a link by hand only when that fails.
        A manual link is never changed automatically.
      </Typography.Paragraph>
      <Select showSearch allowClear filterOption={false} onSearch={search} options={options} value={vmId}
        onChange={setVmId} placeholder="Search VM by name or IP (empty = not linked)" style={{ width: '100%' }} />
    </Modal>
  )
}

function UpdateAgentModal({ agent, info, onClose }) {
  const { message } = App.useApp()
  const navigate = useNavigate()
  const { can } = useAuth()
  const [busy, setBusy] = useState(false)
  const cmd = buildUpgradeCommands(info?.fingerprint)
  const windows = agent.os_family === 'windows'
  const arch = { x86_64: 'amd64', amd64: 'amd64', aarch64: 'arm64', arm64: 'arm64', i386: '386', i686: '386', '386': '386' }[(agent.arch || '').toLowerCase()] || 'amd64'
  const platform = agent.platform || `${windows ? 'windows' : 'linux'}-${arch}`
  const fileName = info?.files?.[platform]?.file || `vcentral-agent-${platform}${windows ? '.exe' : ''}`
  const auto = async () => {
    setBusy(true)
    try {
      const t = await api.post('/api/agents/update', { agent_ids: [agent.id] })
      onClose()
      navigate(`/tasks/${t.id}`)
    } catch (e) {
      message.error(e.message)
    } finally {
      setBusy(false)
    }
  }
  const block = (text) => (
    <Typography.Paragraph copyable={{ text }} style={{ marginBottom: 8 }}>
      <code style={{ display: 'block', whiteSpace: 'pre-wrap', wordBreak: 'break-all', padding: 12, fontSize: 12 }}>{text}</code>
    </Typography.Paragraph>
  )
  return (
    <Modal open width={760} title={`Update the agent on ${agent.hostname}`} onCancel={onClose} footer={null}>
      <Typography.Paragraph>
        From <b>{agent.agent_version || 'unknown'}</b> to <b>{info?.version || 'the latest version'}</b>. The agent keeps its
        identity, settings and history; nothing needs to be re-enrolled.
      </Typography.Paragraph>
      {agent.can_self_update ? (
        <Card size="small" style={{ marginBottom: 16 }}>
          <Space style={{ width: '100%', justifyContent: 'space-between' }} wrap>
            <div>
              <Typography.Text strong>1. Automatically (recommended)</Typography.Text>
              <Typography.Paragraph type="secondary" style={{ margin: 0 }}>The agent downloads the new version from vCentral with its next report, checks it and restarts.</Typography.Paragraph>
            </div>
            {can('admin') && <Button type="primary" loading={busy} onClick={auto}>Update now</Button>}
          </Space>
        </Card>
      ) : (
        <Alert type="info" showIcon style={{ marginBottom: 16 }}
          message={`Version ${agent.agent_version || 'unknown'} cannot update itself`}
          description="Run the command below on the VM once. After that, updates are automatic (Update outdated agents)." />
      )}
      <Typography.Text strong>{agent.can_self_update ? '2. By command, on the VM' : 'Run on the VM'}</Typography.Text>
      <Typography.Paragraph type="secondary">
        {windows ? 'Paste into PowerShell opened with Run as administrator.' : 'Run as a user with sudo (needs curl and openssl).'}{' '}
        No token or password for vCentral is needed. If you can't log in to the VM, use <i>Install agent</i> on its VM page (VMware Tools) instead.
      </Typography.Paragraph>
      {block(windows ? cmd.windows : cmd.linux)}
      <Collapse ghost size="small" items={[{
        key: 'offline', label: 'No network access from the VM to vCentral? Update offline',
        children: (
          <>
            <Typography.Paragraph type="secondary">
              Download <b>{fileName}</b> from the <b>Downloads</b> tab{windows && !agent.platform ? ' (Windows Server 2012 R2 and older: the windows-legacy file)' : ''},
              copy it to the VM, then run it with <code>upgrade</code> as {windows ? 'Administrator' : 'root'}:
            </Typography.Paragraph>
            {block(windows ? `.\\${fileName} upgrade` : `sudo sh -c "chmod +x ./${fileName} && ./${fileName} upgrade"`)}
          </>
        ),
      }]} />
    </Modal>
  )
}

function AgentsTab({ info }) {
  const { can } = useAuth()
  const { message } = App.useApp()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const [agents, setAgents] = useState([])
  const [loading, setLoading] = useState(false)
  const [linking, setLinking] = useState(null)
  const [updating, setUpdating] = useState(null)
  const state = params.get('state') || 'all'
  const linked = params.get('linked') || undefined
  const search = params.get('search') || undefined

  const load = useCallback(() => {
    setLoading(true)
    api.get('/api/agents', { state: state === 'all' ? undefined : state, linked, search })
      .then(setAgents, (e) => message.error(e.message))
      .finally(() => setLoading(false))
  }, [state, linked, search, message])
  useEffect(load, [load])

  const set = (k, v) => {
    const next = new URLSearchParams(params)
    v ? next.set(k, v) : next.delete(k)
    setParams(next, { replace: true })
  }
  const act = async (fn, ok) => {
    try {
      await fn()
      message.success(ok)
      load()
    } catch (e) {
      message.error(e.message)
    }
  }

  return (
    <>
      <Space wrap style={{ marginBottom: 16 }}>
        <Segmented value={state} onChange={(v) => set('state', v === 'all' ? undefined : v)}
          options={[{ value: 'all', label: 'All' }, { value: 'online', label: 'Online' }, { value: 'offline', label: 'Offline' }, { value: 'revoked', label: 'Revoked' }]} />
        <Select allowClear placeholder="Linked to a VM?" style={{ width: 170 }} value={linked} onChange={(v) => set('linked', v)}
          options={[{ value: 'true', label: 'Linked' }, { value: 'false', label: 'Not linked' }]} />
        <Input.Search allowClear placeholder="Hostname, OS, IP" style={{ width: 240 }} defaultValue={search} onSearch={(v) => set('search', v)} />
        {can('admin') && agents.some((a) => a.outdated && a.state !== 'revoked') && (
          <Popconfirm title="Update all outdated agents?" okText="Update"
            description="Each agent downloads the new version from vCentral, checks it and restarts. Agents older than 0.4.0 cannot do this: use the Update button on their row for a command to run once on the VM."
            onConfirm={async () => {
              try {
                const t = await api.post('/api/agents/update', { agent_ids: null })
                navigate(`/tasks/${t.id}`)
              } catch (e) {
                message.error(e.message)
              }
            }}>
            <Button>Update outdated agents</Button>
          </Popconfirm>
        )}
      </Space>
      <Table
        rowKey="id" size="small" loading={loading} dataSource={agents} scroll={{ x: 1000 }}
        pagination={{ pageSize: 50, hideOnSinglePage: true }}
        locale={{ emptyText: 'No agents yet. Create an enrollment token and run the install command on a VM.' }}
        columns={[
          { title: 'State', dataIndex: 'state', width: 100, render: (s) => <AgentStateTag state={s} /> },
          { title: 'Hostname', dataIndex: 'hostname', width: 150, ellipsis: true },
          {
            title: 'VM', dataIndex: 'vm_name', width: 170, ellipsis: true,
            render: (v, a) => (v
              ? <Space size={4}><Link to={`/vms/${a.vm_id}`}>{v}</Link>{a.link_source === 'manual' && <Tooltip title="Linked by hand"><LinkOutlined /></Tooltip>}</Space>
              : <Tag color="warning">Not linked</Tag>),
          },
          { title: 'OS', dataIndex: 'os_name', ellipsis: true, render: (v, a) => <Space size={6}><OsIcon family={a.os_family} />{v}</Space> },
          {
            title: 'Version', dataIndex: 'agent_version', width: 100,
            render: (v, a) => <Space size={4}>{v}{a.outdated && <Tooltip title={a.can_self_update ? 'A newer agent is available' : 'A newer agent is available. This version cannot update itself: use Update to get the command'}><Tag color="gold">old</Tag></Tooltip>}</Space>,
          },
          {
            title: 'Last report', dataIndex: 'last_seen_at', width: 120,
            render: (v, a) => <Tooltip title={v ? `${fmtDate(v)} from ${a.last_ip}` : null}>{v ? fmtAgo(v) : 'Never'}</Tooltip>,
          },
          { title: 'Enrolled', dataIndex: 'enrolled_at', width: 110, render: (v, a) => <Tooltip title={`Token: ${a.enrolled_with || '—'}`}>{fmtAgo(v)}</Tooltip> },
          ...(can('admin', 'operator') ? [{
            title: '', key: 'actions', width: 270, fixed: 'right',
            render: (_, a) => (
              <Space>
                {a.outdated && a.state !== 'revoked' && <Button size="small" type="primary" onClick={() => setUpdating(a)}>Update</Button>}
                {a.state !== 'revoked' && <Button size="small" onClick={() => setLinking(a)}>Link VM</Button>}
                {a.state !== 'revoked' && (
                  <Popconfirm title={`Revoke ${a.hostname}?`} description="It will stop being accepted. Uninstall it on the VM too." okText="Revoke" okButtonProps={{ danger: true }}
                    onConfirm={() => act(() => api.post(`/api/agents/${a.id}/revoke`), 'Agent revoked')}>
                    <Button size="small" danger>Revoke</Button>
                  </Popconfirm>
                )}
                {a.state === 'revoked' && can('admin') && (
                  <Popconfirm title="Delete this agent and its history?" okText="Delete" okButtonProps={{ danger: true }}
                    onConfirm={() => act(() => api.del(`/api/agents/${a.id}`), 'Agent deleted')}>
                    <Button size="small" danger>Delete</Button>
                  </Popconfirm>
                )}
              </Space>
            ),
          }] : []),
        ]}
      />
      {linking && <LinkVmModal agent={linking} onClose={() => setLinking(null)} onSaved={load} />}
      {updating && <UpdateAgentModal agent={updating} info={info} onClose={() => { setUpdating(null); load() }} />}
    </>
  )
}

const EXPIRY = [
  { value: 24, label: '1 day' }, { value: 24 * 7, label: '7 days' }, { value: 24 * 30, label: '30 days' }, { value: 0, label: 'Never' },
]

function CreateTokenModal({ fingerprint, onClose, onCreated }) {
  const [form] = Form.useForm()
  const { message } = App.useApp()
  const [created, setCreated] = useState(null)
  const uses = Form.useWatch('uses', form)
  const purpose = Form.useWatch('purpose', form) || 'vms'
  const template = purpose === 'template'

  const submit = async () => {
    const v = await form.validateFields()
    try {
      const t = await api.post('/api/agent-tokens', template
        // Template tokens must keep working for every VM deployed later: unlimited uses, no expiry.
        ? { name: v.name, expires_in_hours: null, max_uses: null }
        : { name: v.name, expires_in_hours: v.expiry || null, max_uses: v.uses === 'one' ? 1 : v.uses === 'many' ? v.max_uses : null })
      setCreated({ ...t, template })
      onCreated()
    } catch (e) {
      message.error(e.message)
    }
  }

  if (created) {
    return (
      <Modal title={`Token "${created.name}" created`} open width={820} onCancel={onClose} footer={<Button type="primary" onClick={onClose}>Done</Button>}>
        <Alert type="warning" showIcon style={{ marginBottom: 16 }}
          message="Copy the command now. The token is shown only once; vCentral stores only a hash of it." />
        {created.template && (
          <Alert type="info" showIcon style={{ marginBottom: 16 }} message="Run this inside the VM you turn into a template"
            description="The agent installs but stays idle on that machine. Then shut it down and convert it to a template (on Windows, sysprep as usual). Every VM deployed from the template enrolls itself on first boot and appears on the Agents page; no password or action needed. Revoke this token to stop new VMs from enrolling." />
        )}
        <InstallCommands token={created.token} fingerprint={fingerprint} template={created.template} />
      </Modal>
    )
  }
  return (
    <Modal title="New enrollment token" open onCancel={onClose} onOk={submit} okText="Create token">
      <Typography.Paragraph type="secondary">
        A token lets machines register as agents. Give one to a VM owner, or use one token for a batch of VMs.
      </Typography.Paragraph>
      <Form form={form} layout="vertical" initialValues={{ purpose: 'vms', expiry: 24 * 7, uses: 'one', max_uses: 10 }}>
        <Form.Item name="purpose" label="For">
          <Radio.Group optionType="button" options={[{ value: 'vms', label: 'Existing VMs' }, { value: 'template', label: 'A VM template (self-enrolling VMs)' }]} />
        </Form.Item>
        <Form.Item name="name" label="Name" rules={[{ required: true, max: 128 }]}
          extra={template ? 'For example the template name: “Ubuntu 22.04 template”' : 'For example the owner or team: “QA team web servers”'}>
          <Input />
        </Form.Item>
        {template ? (
          <Typography.Paragraph type="secondary">
            Template tokens never expire and can enroll any number of machines, because every VM deployed from the
            template uses it on its first boot.
          </Typography.Paragraph>
        ) : (
          <>
            <Form.Item name="expiry" label="Valid for"><Radio.Group options={EXPIRY} optionType="button" /></Form.Item>
            <Form.Item name="uses" label="Can enroll"
              extra="Reusable tokens are also kept by the agent so a cloned VM can register itself as a new agent.">
              <Radio.Group options={[{ value: 'one', label: 'One machine' }, { value: 'many', label: 'Up to…' }, { value: 'unlimited', label: 'Unlimited' }]} optionType="button" />
            </Form.Item>
          </>
        )}
        {!template && uses === 'many' && (
          <Form.Item name="max_uses" label="Maximum machines" rules={[{ required: true }]}><InputNumber min={2} max={100000} /></Form.Item>
        )}
      </Form>
    </Modal>
  )
}

const TOKEN_STATE = { active: 'success', expired: 'default', 'used up': 'default', revoked: 'error' }

function TokensTab({ fingerprint }) {
  const { message } = App.useApp()
  const [tokens, setTokens] = useState([])
  const [creating, setCreating] = useState(false)
  const load = useCallback(() => api.get('/api/agent-tokens').then(setTokens, (e) => message.error(e.message)), [message])
  useEffect(() => { load() }, [load])

  const revoke = async (t) => {
    try {
      await api.post(`/api/agent-tokens/${t.id}/revoke`)
      message.success('Token revoked')
      load()
    } catch (e) {
      message.error(e.message)
    }
  }

  return (
    <>
      <Button type="primary" icon={<PlusOutlined />} style={{ marginBottom: 16 }} onClick={() => setCreating(true)}>New token</Button>
      <Table rowKey="id" size="small" dataSource={tokens} pagination={{ pageSize: 50, hideOnSinglePage: true }} scroll={{ x: 900 }}
        locale={{ emptyText: 'No tokens yet' }}
        columns={[
          { title: 'Name', dataIndex: 'name' },
          { title: 'Token', dataIndex: 'prefix', render: (p) => <code>{p}…</code> },
          { title: 'State', dataIndex: 'state', render: (s) => <Tag color={TOKEN_STATE[s]}>{s}</Tag> },
          { title: 'Used', key: 'uses', render: (_, t) => `${t.uses} / ${t.max_uses ?? '∞'}` },
          { title: 'Expires', dataIndex: 'expires_at', render: (v) => (v ? fmtDate(v) : 'Never') },
          { title: 'Created by', dataIndex: 'created_by' },
          { title: 'Last used', dataIndex: 'last_used_at', render: (v) => (v ? fmtAgo(v) : '—') },
          {
            title: '', key: 'a', width: 90,
            render: (_, t) => t.state === 'active' && (
              <Popconfirm title="Revoke this token?" description="Agents already enrolled keep working." onConfirm={() => revoke(t)} okText="Revoke" okButtonProps={{ danger: true }}>
                <Button size="small" danger>Revoke</Button>
              </Popconfirm>
            ),
          },
        ]}
      />
      {creating && <CreateTokenModal fingerprint={fingerprint} onClose={() => setCreating(false)} onCreated={load} />}
    </>
  )
}

const PLATFORM_LABEL = {
  'linux-amd64': 'Linux 64-bit (x86_64)', 'linux-386': 'Linux 32-bit (x86)', 'linux-arm64': 'Linux ARM64',
  'windows-amd64': 'Windows 64-bit — Server 2016+, Windows 10+', 'windows-386': 'Windows 32-bit — Windows 10+',
  'windows-legacy-amd64': 'Windows 64-bit — Server 2008 R2 / 2012 / 2012 R2, Windows 7 / 8.1',
  'windows-legacy-386': 'Windows 32-bit — Windows 7 / 8.1',
}

function DownloadsTab({ info }) {
  return (
    <>
      <Typography.Paragraph type="secondary">
        Agent version {info.version || 'unknown'}. Use these for VMs that cannot download from vCentral directly, then run the
        manual install command shown when you create a token.
        {info.fingerprint && <> Certificate fingerprint (SHA-256): <Typography.Text code copyable>{info.fingerprint}</Typography.Text></>}
      </Typography.Paragraph>
      <Table rowKey="platform" size="small" pagination={false}
        dataSource={info.platforms.map((p) => ({ platform: p, ...info.files[p] }))}
        locale={{ emptyText: 'No agent binaries in this server image.' }}
        columns={[
          { title: 'Platform', dataIndex: 'platform', render: (p) => PLATFORM_LABEL[p] || p },
          { title: 'Size', dataIndex: 'size', width: 100, render: (s) => (s ? `${(s / 1048576).toFixed(1)} MB` : '—') },
          { title: 'SHA-256', dataIndex: 'sha256', ellipsis: true, render: (v) => <Typography.Text code copyable={{ text: v }} style={{ fontSize: 12 }}>{v?.slice(0, 16)}…</Typography.Text> },
          { title: '', key: 'd', width: 120, render: (_, r) => <Button size="small" icon={<DownloadOutlined />} href={`/api/agent/download/${r.platform}`}>Download</Button> },
        ]}
      />
    </>
  )
}

export default function Agents() {
  const { can } = useAuth()
  const [info, setInfo] = useState(null)
  useEffect(() => {
    if (can('admin', 'operator')) api.get('/api/agents/install-info').then(setInfo).catch(() => {})
  }, [can])

  return (
    <>
      <Typography.Title level={3} style={{ marginTop: 0 }}>Agents</Typography.Title>
      <Typography.Paragraph type="secondary">
        The vCentral agent runs on each VM and reports CPU, memory, disks, network, top processes, installed software,
        services and open ports. It only connects out to vCentral, and runs tasks only when they are signed by this vCentral.
      </Typography.Paragraph>
      <Card size="small">
        <Tabs
          items={[
            { key: 'agents', label: 'Agents', children: <AgentsTab info={info} /> },
            ...(can('admin', 'operator') ? [
              { key: 'tokens', label: 'Enrollment tokens', children: <TokensTab fingerprint={info?.fingerprint} /> },
              { key: 'downloads', label: 'Downloads', children: info ? <DownloadsTab info={info} /> : null },
            ] : []),
          ]}
        />
      </Card>
    </>
  )
}
