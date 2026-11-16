import { useEffect, useMemo, useState } from 'react'
import { App, Button, Card, Descriptions, Empty, Input, Popconfirm, Select, Space, Table, Tabs, Tag, Tooltip, Typography } from 'antd'
import { CloudServerOutlined, ReloadOutlined } from '@ant-design/icons'
import { api } from '../api'
import { useAuth } from '../auth'
import { fmtAgo, fmtDate, fmtGB, fmtMem, fmtUptime } from '../format'
import { AgentStateTag, LinkVmModal } from '../pages/Agents'
import { Link } from 'react-router-dom'
import { OutputDrawer, RunStatusTag } from '../pages/TaskDetail'
import InstallAgentModal from './InstallAgentModal'
import NewTaskModal from './NewTaskModal'
import UsageBar from './UsageBar'

export function NoAgentCard({ vm, onChanged }) {
  const { can } = useAuth()
  const [installing, setInstalling] = useState(false)
  const canInstall = can('admin', 'operator') && vm && !vm.is_deleted && vm.power_state === 'poweredOn'
  return (
    <Card size="small" title="Agent">
      <Empty
        image={<CloudServerOutlined style={{ fontSize: 40, opacity: 0.4 }} />}
        imageStyle={{ height: 44 }}
        description={
          <Typography.Text type="secondary">
            No agent on this VM. Without it you only see what vCenter knows: no in-guest memory, processes, software or services.
          </Typography.Text>
        }
      >
        {canInstall && <Button type="primary" onClick={() => setInstalling(true)}>Install the agent</Button>}
        {can('admin', 'operator') && vm?.power_state !== 'poweredOn' && <Typography.Text type="secondary">Power the VM on to install the agent.</Typography.Text>}
      </Empty>
      {installing && <InstallAgentModal vm={vm} onClose={() => setInstalling(false)} onDone={onChanged} />}
    </Card>
  )
}

export function AgentCard({ agent, onChanged }) {
  const { can } = useAuth()
  const { message } = App.useApp()
  const [linking, setLinking] = useState(false)
  const [running, setRunning] = useState(false)
  const m = agent.metrics || {}
  const sys = agent.system || {}

  const act = async (path, ok) => {
    try {
      await api.post(path)
      message.success(ok)
      onChanged()
    } catch (e) {
      message.error(e.message)
    }
  }

  return (
    <Card
      size="small"
      title={<Space>Agent <AgentStateTag state={agent.state} /></Space>}
      extra={can('admin', 'operator') && agent.state !== 'revoked' && (
        <Space size={4}>
          {agent.vm_id && <Button size="small" type="primary" onClick={() => setRunning(true)}>Run</Button>}
          <Tooltip title="Ask the agent for a fresh inventory with its next report">
            <Button size="small" icon={<ReloadOutlined />} onClick={() => act(`/api/agents/${agent.id}/refresh-inventory`, 'Inventory requested')} />
          </Tooltip>
          <Button size="small" onClick={() => setLinking(true)}>Link</Button>
          <Popconfirm title="Revoke this agent?" description="vCentral stops accepting its reports. Uninstall it on the VM too."
            okText="Revoke" okButtonProps={{ danger: true }} onConfirm={() => act(`/api/agents/${agent.id}/revoke`, 'Agent revoked')}>
            <Button size="small" danger>Revoke</Button>
          </Popconfirm>
        </Space>
      )}
    >
      <Descriptions column={1} size="small">
        <Descriptions.Item label="Last report">{agent.last_seen_at ? `${fmtAgo(agent.last_seen_at)} from ${agent.last_ip}` : 'Never'}</Descriptions.Item>
        <Descriptions.Item label="Version">
          {agent.agent_version}{agent.outdated && <Tag color="gold" style={{ marginLeft: 8 }}>update available</Tag>}
        </Descriptions.Item>
        <Descriptions.Item label="OS">{agent.os_name}</Descriptions.Item>
        {agent.kernel_version && <Descriptions.Item label="Kernel">{agent.kernel_version}</Descriptions.Item>}
        <Descriptions.Item label="CPU">{sys.cpu_model ? `${sys.cpu_model} · ${sys.cpu_cores} threads` : '—'}</Descriptions.Item>
        <Descriptions.Item label="Memory">
          {m.mem_total_mb ? `${fmtMem(m.mem_used_mb)} used of ${fmtMem(m.mem_total_mb)}` : '—'}
          {m.swap_pct != null && <Typography.Text type="secondary"> · swap {Math.round(m.swap_pct)}%</Typography.Text>}
        </Descriptions.Item>
        {m.load1 != null && <Descriptions.Item label="Load">{m.load1} / {m.load5} / {m.load15}</Descriptions.Item>}
        <Descriptions.Item label="Uptime">{fmtUptime(m.uptime_seconds)}{m.process_count ? ` · ${m.process_count} processes` : ''}</Descriptions.Item>
        <Descriptions.Item label="Linked by">
          {{ bios_uuid: 'BIOS UUID', mac: 'MAC address', manual: 'Set by hand' }[agent.link_source] || '—'}
        </Descriptions.Item>
        <Descriptions.Item label="Enrolled">{fmtDate(agent.enrolled_at)} · token “{agent.enrolled_with}”</Descriptions.Item>
        {agent.revoked_reason && <Descriptions.Item label="Revoked">{agent.revoked_reason}</Descriptions.Item>}
      </Descriptions>
      {linking && <LinkVmModal agent={agent} onClose={() => setLinking(false)} onSaved={onChanged} />}
      {running && <NewTaskModal open fixedTargets={{ vm_ids: [agent.vm_id] }} title={`Run a task on ${agent.vm_name || agent.hostname}`} onClose={() => setRunning(false)} />}
    </Card>
  )
}

/** The last tasks that ran on this VM. */
export function RecentTasks({ vmId }) {
  const { can } = useAuth()
  const [rows, setRows] = useState(null)
  const [runId, setRunId] = useState(null)
  useEffect(() => { api.get(`/api/vms/${vmId}/task-runs`).then(setRows).catch(() => setRows([])) }, [vmId])
  if (!rows || rows.length === 0) return null
  return (
    <Card size="small" title="Recent tasks">
      <Table size="small" rowKey="id" dataSource={rows} pagination={false}
        onRow={(r) => (can('admin', 'operator') && !['pending', 'skipped'].includes(r.status) ? { onClick: () => setRunId(r.id), style: { cursor: 'pointer' } } : {})}
        columns={[
          { title: 'Task', dataIndex: 'task_name', ellipsis: true, render: (v, r) => <Link to={`/tasks/${r.task_id}`} onClick={(e) => e.stopPropagation()}>{v}</Link> },
          { title: 'Status', dataIndex: 'status', width: 150, render: (s) => <RunStatusTag status={s} /> },
          { title: 'Exit', dataIndex: 'exit_code', width: 60, render: (v) => (v ?? '—') },
          { title: 'When', dataIndex: 'created_at', width: 120, render: fmtAgo },
        ]} />
      {runId && <OutputDrawer runId={runId} onClose={() => setRunId(null)} />}
    </Card>
  )
}

export function AgentDisks({ agent }) {
  const disks = agent.metrics?.disks || []
  return (
    <Card size="small" title="Disks (from agent)">
      <Table size="small" rowKey="mount" pagination={false} dataSource={disks} locale={{ emptyText: 'No disks reported' }}
        columns={[
          { title: 'Mount / drive', dataIndex: 'mount' },
          { title: 'Type', dataIndex: 'fstype', responsive: ['md'] },
          { title: 'Size', dataIndex: 'total_gb', render: fmtGB },
          { title: 'Free', key: 'free', render: (_, d) => fmtGB(d.total_gb - d.used_gb) },
          { title: 'Used', dataIndex: 'used_pct', render: (v) => <UsageBar value={v} /> },
        ]}
      />
    </Card>
  )
}

export function TopProcesses({ agent }) {
  const top = agent.top_processes || {}
  const table = (rows, empty) => (
    <Table size="small" rowKey="pid" pagination={false} dataSource={rows || []} locale={{ emptyText: empty }}
      columns={[
        { title: 'Process', dataIndex: 'name', ellipsis: true },
        { title: 'PID', dataIndex: 'pid', width: 80 },
        { title: 'User', dataIndex: 'user', ellipsis: true, responsive: ['md'] },
        { title: 'CPU', dataIndex: 'cpu_pct', width: 80, render: (v) => `${v}%` },
        { title: 'Memory', dataIndex: 'mem_mb', width: 100, render: (v) => fmtMem(Math.round(v)) },
      ]} />
  )
  return (
    <Card size="small" title="Top processes" extra={<Typography.Text type="secondary">{fmtAgo(agent.metrics?.ts)}</Typography.Text>}>
      <Tabs size="small" items={[
        { key: 'cpu', label: 'By CPU', children: table(top.by_cpu, 'Available from the second report') },
        { key: 'mem', label: 'By memory', children: table(top.by_mem, 'None reported') },
      ]} />
    </Card>
  )
}

function useSection(agentId, section, active) {
  const [rows, setRows] = useState(null)
  useEffect(() => {
    if (active && rows === null) api.get(`/api/agents/${agentId}/inventory/${section}`).then(setRows, () => setRows([]))
  }, [agentId, section, active, rows])
  return rows
}

function SoftwareTab({ agentId }) {
  const rows = useSection(agentId, 'software', true)
  const [q, setQ] = useState('')
  const filtered = useMemo(() => {
    const t = q.trim().toLowerCase()
    return (rows || []).filter((r) => !t || `${r.name} ${r.version} ${r.publisher || ''}`.toLowerCase().includes(t))
  }, [rows, q])
  return (
    <>
      <Input.Search allowClear placeholder="Search name, version, publisher" style={{ maxWidth: 320, marginBottom: 8 }} onChange={(e) => setQ(e.target.value)} />
      <Table size="small" rowKey={(r) => `${r.name}|${r.version}`} loading={rows === null} dataSource={filtered}
        pagination={{ pageSize: 25, showSizeChanger: false, showTotal: (t) => `${t} packages` }}
        columns={[
          { title: 'Name', dataIndex: 'name', ellipsis: true },
          { title: 'Version', dataIndex: 'version', ellipsis: true, width: 220 },
          { title: 'Publisher', dataIndex: 'publisher', ellipsis: true, responsive: ['lg'] },
        ]} />
    </>
  )
}

const SERVICE_COLORS = { running: 'success', stopped: 'default', failed: 'error' }

function ServicesTab({ agentId, active }) {
  const rows = useSection(agentId, 'services', active)
  const [q, setQ] = useState('')
  const [state, setState] = useState()
  const filtered = useMemo(() => {
    const t = q.trim().toLowerCase()
    return (rows || []).filter((r) => (!state || r.state === state) && (!t || `${r.name} ${r.display_name || ''}`.toLowerCase().includes(t)))
  }, [rows, q, state])
  return (
    <>
      <Space wrap style={{ marginBottom: 8 }}>
        <Input.Search allowClear placeholder="Search services" style={{ width: 260 }} onChange={(e) => setQ(e.target.value)} />
        <Select allowClear placeholder="State" style={{ width: 140 }} value={state} onChange={setState}
          options={['running', 'stopped', 'failed'].map((v) => ({ value: v, label: v }))} />
      </Space>
      <Table size="small" rowKey="name" loading={rows === null} dataSource={filtered}
        pagination={{ pageSize: 25, showSizeChanger: false, showTotal: (t) => `${t} services` }}
        columns={[
          { title: 'Service', dataIndex: 'name', ellipsis: true },
          { title: 'Description', dataIndex: 'display_name', ellipsis: true, responsive: ['md'] },
          { title: 'State', dataIndex: 'state', width: 130, render: (s) => <Tag color={SERVICE_COLORS[s]}>{s}</Tag> },
          { title: 'Start', dataIndex: 'start_mode', width: 130 },
        ]} />
    </>
  )
}

function PortsTab({ agentId, active }) {
  const rows = useSection(agentId, 'listening_ports', active)
  return (
    <Table size="small" rowKey={(r) => `${r.address}:${r.port}`} loading={rows === null} dataSource={rows || []}
      pagination={{ pageSize: 25, hideOnSinglePage: true }}
      columns={[
        { title: 'Port', dataIndex: 'port', width: 90 },
        { title: 'Address', dataIndex: 'address', render: (a) => (a === '0.0.0.0' || a === '::' ? `${a} (all interfaces)` : a) },
        { title: 'Process', dataIndex: 'process', render: (p, r) => (p ? `${p} (${r.pid})` : '—') },
      ]} />
  )
}

function InterfacesTab({ agentId, active }) {
  const rows = useSection(agentId, 'interfaces', active)
  return (
    <Table size="small" rowKey="name" loading={rows === null} dataSource={rows || []} pagination={false}
      columns={[
        { title: 'Interface', dataIndex: 'name' },
        { title: 'MAC', dataIndex: 'mac' },
        { title: 'IP addresses', dataIndex: 'ips', render: (ips) => (ips || []).join(', ') || '—' },
      ]} />
  )
}

export function InventoryCard({ agent }) {
  const [tab, setTab] = useState('software')
  const c = agent.counts || {}
  if (!agent.inventory_at) {
    return <Card size="small" title="Inventory"><Empty description="Waiting for the agent's first inventory" /></Card>
  }
  return (
    <Card size="small" title="Inventory"
      extra={<Typography.Text type="secondary">Collected {fmtAgo(agent.inventory_at)}{agent.inventory_requested && ' · refresh requested'}</Typography.Text>}>
      {agent.inventory_errors?.length > 0 && (
        <Typography.Paragraph type="warning">Partly collected: {agent.inventory_errors.join('; ')}</Typography.Paragraph>
      )}
      <Tabs activeKey={tab} onChange={setTab} items={[
        { key: 'software', label: `Software (${c.software ?? 0})`, children: <SoftwareTab agentId={agent.id} /> },
        { key: 'services', label: `Services (${c.services ?? 0})`, children: <ServicesTab agentId={agent.id} active={tab === 'services'} /> },
        { key: 'ports', label: `Listening ports (${c.listening_ports ?? 0})`, children: <PortsTab agentId={agent.id} active={tab === 'ports'} /> },
        { key: 'nics', label: 'Network interfaces', children: <InterfacesTab agentId={agent.id} active={tab === 'nics'} /> },
      ]} />
    </Card>
  )
}
