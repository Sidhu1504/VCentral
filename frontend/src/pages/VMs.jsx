import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Alert, Button, Card, Checkbox, Input, Select, Space, Table, Tag, Tooltip, Typography } from 'antd'
import { DownloadOutlined, WindowsOutlined, QuestionOutlined } from '@ant-design/icons'
import { api } from '../api'
import { useAuth } from '../auth'
import SetOwnerModal from '../components/SetOwnerModal'
import UsageBar from '../components/UsageBar'
import { CriticalStar, HealthTag } from '../components/Health'
import { AGENT_STATE, fmtMem, OS_LABEL, POWER } from '../format'

const LinuxIcon = () => <span aria-hidden style={{ fontWeight: 700, fontSize: 11 }}>🐧</span>

export function OsIcon({ family }) {
  if (family === 'windows') return <WindowsOutlined />
  if (family === 'linux') return <LinuxIcon />
  return <QuestionOutlined />
}

export function PowerTag({ state }) {
  const p = POWER[state] || { label: state, color: 'default' }
  return <Tag color={p.color}>{p.label}</Tag>
}

export function OwnerCell({ owner, source }) {
  if (!owner) return <Tag color="warning">Unknown</Tag>
  const how = { event: 'From vCenter creation event', attribute: 'From vCenter "Owner" attribute', manual: 'Set manually in vCentral', suggested: 'Accepted suggestion (Owners page)' }
  return <Tooltip title={how[source]}><span>{owner}</span></Tooltip>
}

const FILTER_KEYS = ['search', 'power', 'os_family', 'owner_unknown', 'agent', 'health', 'critical', 'status', 'sort', 'order', 'page', 'page_size']

export function AgentTag({ state }) {
  const s = AGENT_STATE[state] || AGENT_STATE.none
  return state === 'none' || !state ? <Typography.Text type="secondary">None</Typography.Text> : <Tag color={s.color}>{s.label}</Tag>
}

export default function VMs() {
  const [params, setParams] = useSearchParams()
  const [data, setData] = useState({ items: [], total: 0 })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const { can } = useAuth()
  const [selected, setSelected] = useState([])
  const [settingOwner, setSettingOwner] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)

  const q = Object.fromEntries(FILTER_KEYS.map((k) => [k, params.get(k) || undefined]))
  q.page = Number(q.page || 1)
  q.page_size = Number(q.page_size || 50)
  q.status = q.status || 'active'
  const key = params.toString()

  useEffect(() => {
    const load = (quiet) => {
      if (!quiet) setLoading(true)
      return api
        .get('/api/vms', { ...q, owner_unknown: q.owner_unknown ? 'true' : undefined, critical: q.critical ? 'true' : undefined })
        .then((d) => { setData(d); setError(null) }, (e) => setError(e.message))
        .finally(() => setLoading(false))
    }
    load(false)
    const t = setInterval(() => load(true), 30000) // keep health and usage current
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, reloadKey])

  const update = (changes) => {
    const next = new URLSearchParams(params)
    Object.entries(changes).forEach(([k, v]) => (v === undefined || v === null || v === '' || v === false ? next.delete(k) : next.set(k, v)))
    if (!('page' in changes)) next.delete('page')
    setParams(next, { replace: true })
  }

  const sortOrder = (field) => (q.sort === field ? (q.order === 'desc' ? 'descend' : 'ascend') : null)
  const columns = [
    {
      title: 'Name', dataIndex: 'name', sorter: true, sortOrder: sortOrder('name') ?? (q.sort ? null : 'ascend'), fixed: 'left', width: 220,
      render: (v, r) => <Space size={6}><CriticalStar critical={r.critical} /><Link to={`/vms/${r.id}`}>{v}</Link></Space>,
    },
    { title: 'Health', dataIndex: 'health', width: 120, render: (v, r) => <HealthTag health={v} problems={r.problems} /> },
    { title: 'Power', dataIndex: 'power_state', width: 90, sorter: true, sortOrder: sortOrder('power_state'), render: (v) => <PowerTag state={v} /> },
    { title: 'Agent', dataIndex: 'agent', width: 90, render: (v) => <AgentTag state={v} /> },
    {
      title: 'OS', dataIndex: 'guest_os', sorter: true, sortOrder: sortOrder('guest_os'), width: 230, ellipsis: true,
      render: (v, r) => <Space size={6}><OsIcon family={r.os_family} /><span>{v || OS_LABEL[r.os_family]}</span></Space>,
    },
    {
      title: 'IP address', dataIndex: 'primary_ip', sorter: true, sortOrder: sortOrder('primary_ip'), width: 140,
      render: (v, r) => {
        if (v) return v
        if (r.problems?.some((p) => p.code === 'no_ip')) {
          return <Tooltip title={r.last_known_ip ? `Last IP was ${r.last_known_ip}` : null}><Typography.Text type="danger" strong>No IP</Typography.Text></Tooltip>
        }
        return r.last_known_ip ? <Tooltip title="Last known IP (VM is not reporting one now)"><Typography.Text type="secondary">{r.last_known_ip}</Typography.Text></Tooltip> : '—'
      },
    },
    { title: 'Owner', dataIndex: 'owner', sorter: true, sortOrder: sortOrder('owner'), width: 190, ellipsis: true, render: (v, r) => <OwnerCell owner={v} source={r.owner_source} /> },
    { title: <Tooltip title="vCenter's measurement, the same source for every VM">CPU</Tooltip>, dataIndex: 'cpu_pct', sorter: true, sortOrder: sortOrder('cpu_pct'), width: 150, render: (v) => <UsageBar value={v} width={70} /> },
    { title: <Tooltip title="vCenter active memory: memory the guest touched recently. In-guest used memory is on the VM page (agent).">Active memory</Tooltip>, dataIndex: 'mem_pct', sorter: true, sortOrder: sortOrder('mem_pct'), width: 150, render: (v) => <UsageBar value={v} width={70} /> },
    { title: <Tooltip title="Fullest drive or mount point, as reported by VMware Tools">Fullest disk</Tooltip>, dataIndex: 'disk_used_pct_max', sorter: true, sortOrder: sortOrder('disk_used_pct_max'), width: 150, render: (v) => <UsageBar value={v} width={70} /> },
    { title: 'Size', dataIndex: 'num_cpu', sorter: true, sortOrder: sortOrder('num_cpu'), width: 130, render: (v, r) => `${v ?? '—'} vCPU · ${fmtMem(r.memory_mb)}` },
    { title: 'Host', dataIndex: 'esxi_host', sorter: true, sortOrder: sortOrder('esxi_host'), width: 160, ellipsis: true },
    { title: 'Folder', dataIndex: 'folder', width: 200, ellipsis: true },
  ]

  return (
    <>
      <Typography.Title level={3} style={{ marginTop: 0 }}>Virtual machines</Typography.Title>
      <Card size="small" style={{ marginBottom: 16 }}>
        <Space wrap>
          <Input.Search
            allowClear placeholder="Name, IP, hostname, OS, owner, folder" style={{ width: 300 }}
            defaultValue={q.search} onSearch={(v) => update({ search: v })}
          />
          <Select
            allowClear placeholder="Health" style={{ width: 150 }} value={q.health}
            onChange={(v) => update({ health: v })}
            options={[{ value: 'problem', label: 'Any problem' }, { value: 'critical', label: 'Critical' }, { value: 'warning', label: 'Warning' }]}
          />
          <Checkbox checked={!!q.critical} onChange={(e) => update({ critical: e.target.checked ? '1' : undefined })}>
            Critical VMs only
          </Checkbox>
          <Select
            allowClear placeholder="Power state" style={{ width: 140 }} value={q.power}
            onChange={(v) => update({ power: v })}
            options={[{ value: 'poweredOn', label: 'On' }, { value: 'poweredOff', label: 'Off' }, { value: 'suspended', label: 'Suspended' }]}
          />
          <Select
            allowClear placeholder="OS family" style={{ width: 130 }} value={q.os_family}
            onChange={(v) => update({ os_family: v })}
            options={Object.entries(OS_LABEL).map(([value, label]) => ({ value, label }))}
          />
          <Select
            allowClear placeholder="Agent" style={{ width: 130 }} value={q.agent}
            onChange={(v) => update({ agent: v })}
            options={[{ value: 'online', label: 'Online' }, { value: 'offline', label: 'Offline' }, { value: 'none', label: 'No agent' }]}
          />
          <Select
            style={{ width: 150 }} value={q.status} onChange={(v) => update({ status: v === 'active' ? undefined : v })}
            options={[{ value: 'active', label: 'Existing VMs' }, { value: 'deleted', label: 'Deleted VMs' }, { value: 'all', label: 'All' }]}
          />
          <Checkbox checked={!!q.owner_unknown} onChange={(e) => update({ owner_unknown: e.target.checked ? '1' : undefined })}>
            Owner unknown
          </Checkbox>
          <Button icon={<DownloadOutlined />} href={api.url('/api/vms/export.csv', { ...q, page: undefined, page_size: undefined, sort: undefined, order: undefined, owner_unknown: q.owner_unknown ? 'true' : undefined, critical: q.critical ? 'true' : undefined })}>
            Export CSV
          </Button>
          {can('admin', 'operator') && selected.length > 0 && (
            <Button type="primary" onClick={() => setSettingOwner(true)}>Set owner for {selected.length} selected</Button>
          )}
        </Space>
      </Card>
      {error && <Alert type="error" message={error} showIcon style={{ marginBottom: 16 }} />}
      {settingOwner && (
        <SetOwnerModal vmIds={selected} onClose={() => setSettingOwner(false)}
          onDone={() => { setSelected([]); setReloadKey((k) => k + 1) }} />
      )}
      <Table
        rowKey="id" size="small" columns={columns} dataSource={data.items} loading={loading} scroll={{ x: 1600 }}
        rowSelection={can('admin', 'operator') ? { selectedRowKeys: selected, onChange: setSelected, preserveSelectedRowKeys: true } : undefined}
        pagination={{ current: q.page, pageSize: q.page_size, total: data.total, showSizeChanger: true, pageSizeOptions: [25, 50, 100, 200], showTotal: (t) => `${t} VMs` }}
        onChange={(pg, _f, sorter) => {
          update({
            page: pg.current > 1 ? pg.current : undefined,
            page_size: pg.pageSize !== 50 ? pg.pageSize : undefined,
            sort: sorter.order ? sorter.field : undefined,
            order: sorter.order === 'descend' ? 'desc' : undefined,
          })
        }}
      />
    </>
  )
}
