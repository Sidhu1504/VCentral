import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  Alert, App, Breadcrumb, Button, Card, Col, Descriptions, Empty, Form, Input, Row, Segmented, Skeleton, Space,
  Switch, Table, Tag, Timeline, Tooltip, Typography,
} from 'antd'
import { DesktopOutlined, EditOutlined } from '@ant-design/icons'
import ConnectModal from '../components/ConnectModal'
import { ToolsCell } from '../components/Tools'
import { api } from '../api'
import { useAuth } from '../auth'
import Chart, { axisStyle, baseOption, useViz } from '../components/Chart'
import UsageBar from '../components/UsageBar'
import { AgentCard, AgentDisks, InventoryCard, NoAgentCard, RecentTasks, TopProcesses } from '../components/AgentPanels'
import { HealthTag, ProblemAlerts } from '../components/Health'
import { EVENT_KINDS, fmtAgo, fmtDate, fmtGB, fmtMem, fmtRate, fmtUptime } from '../format'
import { AgentTag, OsIcon, OwnerCell, PowerTag } from './VMs'

const pct = (v) => (v === null || v === undefined ? '—' : `${Math.round(v)}%`)
const rate = (v) => (v === null || v === undefined ? '—' : fmtRate(v))

/** Insert empty points where samples are missing (VM off, agent down) so lines break instead of bridging the gap. */
function withGaps(points) {
  if (points.length < 3) return points
  const times = points.map((p) => Date.parse(p.ts))
  const steps = times.slice(1).map((t, i) => t - times[i]).sort((a, b) => a - b)
  const limit = steps[Math.floor(steps.length / 2)] * 3
  const out = [points[0]]
  for (let i = 1; i < points.length; i++) {
    if (times[i] - times[i - 1] > limit) out.push({ ts: new Date(times[i - 1] + 1).toISOString(), gap: true })
    out.push(points[i])
  }
  return out
}

/** One time-series line chart; all series share one unit (percent or bytes/s). */
function lineOption(viz, rawPoints, defs, unit) {
  if (!rawPoints?.length) return null
  const points = withGaps(rawPoints)
  return {
    ...baseOption(viz),
    tooltip: { ...baseOption(viz).tooltip, trigger: 'axis', axisPointer: { type: 'line' }, valueFormatter: unit === 'pct' ? pct : rate },
    xAxis: {
      type: 'time', ...axisStyle(viz), splitLine: { show: false },
      axisLabel: { color: viz.muted, hideOverlap: true, formatter: { day: '{MMM} {d}', hour: '{HH}:{mm}', minute: '{HH}:{mm}' } },
    },
    yAxis: unit === 'pct'
      ? { type: 'value', min: 0, max: 100, ...axisStyle(viz), axisLabel: { color: viz.muted, formatter: '{value}%' } }
      : { type: 'value', min: 0, ...axisStyle(viz), axisLabel: { color: viz.muted, formatter: (v) => fmtRate(v) } },
    series: defs.map(([name, key], i) => ({
      name, type: 'line', showSymbol: false, symbolSize: 8, lineStyle: { width: 2 }, connectNulls: false,
      itemStyle: { color: viz.series[i] }, data: points.map((p) => [p.ts, p.gap ? null : p[key]]),
    })),
  }
}

function MetricsCard({ vmId, agent }) {
  const [source, setSource] = useState(agent ? 'agent' : 'vcenter')
  const [range, setRange] = useState('1h')
  const [data, setData] = useState(null)
  const viz = useViz()
  const agentId = agent?.id
  const fromAgent = source === 'agent' && !!agentId

  useEffect(() => {
    setData(null)
    const url = fromAgent ? `/api/agents/${agentId}/metrics` : `/api/vms/${vmId}/metrics`
    const load = () => api.get(url, { range }).then(setData, () => setData((d) => d || { points: [] }))
    load()
    // Live graphs for short ranges; longer ranges change slowly.
    const every = { '1h': 15000, '6h': 15000, '24h': 60000 }[range]
    const t = every ? setInterval(load, every) : null
    return () => t && clearInterval(t)
  }, [vmId, agentId, fromAgent, range])

  const usage = useMemo(() => lineOption(viz, data?.points, [
    ['CPU', 'cpu_pct'], [fromAgent ? 'Memory used' : 'Active memory', 'mem_pct'], ['Fullest disk', 'disk_used_pct_max'],
  ], 'pct'), [data, viz, fromAgent])
  const network = useMemo(() => fromAgent && lineOption(viz, data?.points, [['Received', 'net_rx_bps'], ['Sent', 'net_tx_bps']], 'rate'), [data, viz, fromAgent])
  const diskIO = useMemo(() => fromAgent && lineOption(viz, data?.points, [['Read', 'disk_read_bps'], ['Write', 'disk_write_bps']], 'rate'), [data, viz, fromAgent])

  return (
    <Card
      size="small"
      title="Resource usage"
      extra={
        <Space wrap size={8}>
          {agent && <Segmented size="small" value={source} onChange={setSource} options={[{ value: 'agent', label: 'Agent' }, { value: 'vcenter', label: 'vCenter' }]} />}
          <Segmented size="small" value={range} onChange={setRange} options={['1h', '6h', '24h', '7d', '30d', '90d']} />
        </Space>
      }
    >
      {!data ? <Skeleton active /> : usage ? (
        <>
          <Chart option={usage} height={240} />
          {network && (
            <Row gutter={16}>
              <Col xs={24} lg={12}><Typography.Text strong>Network</Typography.Text><Chart option={network} height={200} /></Col>
              <Col xs={24} lg={12}><Typography.Text strong>Disk I/O</Typography.Text><Chart option={diskIO} height={200} /></Col>
            </Row>
          )}
        </>
      ) : <Empty description="No samples yet for this range" />}
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {fromAgent
          ? 'Source: vCentral agent inside the VM. Memory used excludes cache that the OS can reclaim.'
          : 'Source: vCenter quick stats. Active memory is what the guest touched recently, so it is usually lower than in-guest "used" memory.'}
        {data?.resolution === 'hourly' && ' Hourly averages.'}
      </Typography.Text>
    </Card>
  )
}

function OwnerNotesCard({ vm, onSaved }) {
  const { can } = useAuth()
  const { message } = App.useApp()
  const [editing, setEditing] = useState(false)
  const [form] = Form.useForm()

  const save = async (values) => {
    try {
      onSaved(await api.patch(`/api/vms/${vm.id}`, { owner: values.owner ?? '', notes: values.notes ?? '' }))
      message.success('Saved')
      setEditing(false)
    } catch (e) {
      message.error(e.message)
    }
  }

  return (
    <Card
      size="small" title="Owner & notes"
      extra={can('admin', 'operator') && !editing && <Button size="small" icon={<EditOutlined />} onClick={() => setEditing(true)}>Edit</Button>}
    >
      {editing ? (
        <Form form={form} layout="vertical" initialValues={{ owner: vm.owner_source === 'manual' ? vm.owner : '', notes: vm.notes }} onFinish={save}>
          <Form.Item name="owner" label="Owner" extra="Leave empty to use automatic detection (vCenter attribute or creation event).">
            <Input placeholder={vm.owner_source !== 'manual' && vm.owner ? `Detected: ${vm.owner}` : 'e.g. john@company.com'} maxLength={255} />
          </Form.Item>
          <Form.Item name="notes" label="Notes">
            <Input.TextArea rows={3} maxLength={5000} placeholder="Purpose, team, ticket number…" />
          </Form.Item>
          <Space>
            <Button type="primary" htmlType="submit">Save</Button>
            <Button onClick={() => setEditing(false)}>Cancel</Button>
          </Space>
        </Form>
      ) : (
        <Descriptions column={1} size="small">
          <Descriptions.Item label="Owner"><OwnerCell owner={vm.owner} source={vm.owner_source} /></Descriptions.Item>
          <Descriptions.Item label="Source">{{ event: 'vCenter creation event', attribute: 'vCenter attribute', manual: 'Set in vCentral', suggested: 'Accepted suggestion' }[vm.owner_source] || '—'}</Descriptions.Item>
          <Descriptions.Item label="Notes"><span style={{ whiteSpace: 'pre-wrap' }}>{vm.notes || '—'}</span></Descriptions.Item>
          {vm.annotation && <Descriptions.Item label="vCenter notes"><span style={{ whiteSpace: 'pre-wrap' }}>{vm.annotation}</span></Descriptions.Item>}
        </Descriptions>
      )}
    </Card>
  )
}

function CriticalSwitch({ vm, onSaved }) {
  const { can } = useAuth()
  const { message } = App.useApp()
  const [busy, setBusy] = useState(false)
  if (!can('admin', 'operator')) return vm.critical ? <Tag color="volcano">Critical VM</Tag> : null
  const toggle = async (critical) => {
    setBusy(true)
    try {
      await api.patch(`/api/vms/${vm.id}`, { critical })
      message.success(critical ? 'Marked as critical: any problem is now raised as critical' : 'No longer critical')
      onSaved()
    } catch (e) {
      message.error(e.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Tooltip title="Critical VMs must always be up: powered off, deleted, no IP or agent offline is raised as Critical">
      <Space size={6}>
        <Switch size="small" checked={vm.critical} loading={busy} onChange={toggle} aria-label="Critical VM" />
        <Typography.Text>Critical VM</Typography.Text>
      </Space>
    </Tooltip>
  )
}

function SourceLabel({ text, tip }) {
  return <Tooltip title={tip}><span style={{ borderBottom: '1px dotted currentColor', cursor: 'help' }}>{text}</span></Tooltip>
}

export default function VMDetail() {
  const { id } = useParams()
  const [vm, setVm] = useState(null)
  const [history, setHistory] = useState([])
  const [error, setError] = useState(null)
  const [connecting, setConnecting] = useState(false)
  const { can } = useAuth()

  const load = useCallback(() => {
    api.get(`/api/vms/${id}`).then(setVm, (e) => setError(e.message))
    api.get(`/api/vms/${id}/history`).then(setHistory, () => {})
  }, [id])
  useEffect(() => {
    load()
    const t = setInterval(load, 15000)
    return () => clearInterval(t)
  }, [load])

  if (error) return <Alert type="error" message={error} showIcon />
  if (!vm) return <Skeleton active />

  const attrs = Object.entries(vm.custom_attributes || {})
  const agent = vm.agent_detail
  return (
    <>
      <Breadcrumb items={[{ title: <Link to="/vms">Virtual machines</Link> }, { title: vm.name }]} style={{ marginBottom: 8 }} />
      <Space align="center" wrap style={{ marginBottom: 16 }}>
        <Typography.Title level={3} style={{ margin: 0 }}>{vm.name}</Typography.Title>
        <HealthTag health={vm.health} problems={vm.problems} showOk={vm.power_state === 'poweredOn'} />
        <PowerTag state={vm.power_state} />
        <Tooltip title="vCentral agent"><span><AgentTag state={vm.agent} /></span></Tooltip>
        <CriticalSwitch vm={vm} onSaved={load} />
        {can('admin', 'operator') && !vm.is_deleted && (
          <Button type="primary" icon={<DesktopOutlined />} onClick={() => setConnecting(true)}>Connect (SSH / RDP)</Button>
        )}
      </Space>
      {connecting && <ConnectModal vm={vm} onClose={() => setConnecting(false)} />}
      {vm.is_deleted && <Alert type="warning" showIcon style={{ marginBottom: 16 }} message={`This VM was deleted from vCenter ${fmtAgo(vm.deleted_at)}. Its history is kept for reference.`} />}
      <ProblemAlerts problems={vm.problems} />

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={16}>
          <Card size="small" title="Overview">
            <Descriptions size="small" column={{ xs: 1, sm: 2, lg: 3 }}>
              <Descriptions.Item label="Guest OS"><Space size={6}><OsIcon family={vm.os_family} />{vm.guest_os || '—'}</Space></Descriptions.Item>
              <Descriptions.Item label="Hostname">{vm.hostname || '—'}</Descriptions.Item>
              <Descriptions.Item label="IP address">
                {vm.primary_ip || (vm.problems?.some((p) => p.code === 'no_ip')
                  ? <Typography.Text type="danger" strong>No IP{vm.last_known_ip ? ` (was ${vm.last_known_ip})` : ''}</Typography.Text>
                  : vm.last_known_ip ? `${vm.last_known_ip} (last known)` : '—')}
              </Descriptions.Item>
              <Descriptions.Item label="vCPU / RAM">{vm.num_cpu ?? '—'} vCPU · {fmtMem(vm.memory_mb)}</Descriptions.Item>
              <Descriptions.Item label={<SourceLabel text="Datastore space" tip="Space the VM's disk files take on the datastore, of the size allocated to them. Thin disks grow as data is written and do not shrink when files are deleted inside the VM, so this is not how full the VM's drives are: see the disk usage per drive below." />}>
                {fmtGB(vm.storage_committed_gb)} of {fmtGB(vm.storage_provisioned_gb)}
              </Descriptions.Item>
              <Descriptions.Item label="Uptime">{fmtUptime(vm.uptime_seconds)}</Descriptions.Item>
              <Descriptions.Item label={<SourceLabel text="CPU (vCenter)" tip="vCenter's measurement from the hypervisor, refreshed every sync. The agent card shows the guest OS's own figure; the two are close but not identical." />}>
                <UsageBar value={vm.cpu_pct} width={70} />
              </Descriptions.Item>
              <Descriptions.Item label={<SourceLabel text="Active memory (vCenter)" tip={'Memory the guest touched recently, estimated by the hypervisor. It is not the same as "used" memory inside the OS (shown on the agent card, same as `free` / Task Manager) and can be higher or lower.'} />}>
                <UsageBar value={vm.mem_pct} width={70} />
              </Descriptions.Item>
              <Descriptions.Item label="VMware Tools"><ToolsCell vm={vm} onWoken={load} /></Descriptions.Item>
              <Descriptions.Item label="ESXi host">{vm.esxi_host || '—'}</Descriptions.Item>
              <Descriptions.Item label="Cluster">{vm.cluster || '—'}</Descriptions.Item>
              <Descriptions.Item label="Folder">{vm.folder || '—'}</Descriptions.Item>
              <Descriptions.Item label="Datastores">{vm.datastores?.join(', ') || '—'}</Descriptions.Item>
              <Descriptions.Item label="Created in vCenter">{fmtDate(vm.vcenter_created_at)}</Descriptions.Item>
              <Descriptions.Item label="First seen">{fmtDate(vm.first_seen_at)}</Descriptions.Item>
            </Descriptions>
          </Card>
          <div style={{ height: 16 }} />
          <MetricsCard key={agent?.id ?? 'none'} vmId={vm.id} agent={agent} />
          <div style={{ height: 16 }} />
          {agent?.metrics?.disks ? <AgentDisks agent={agent} /> : <Card size="small" title="Guest disks (from VMware Tools)">
            <Table
              size="small" rowKey="path" pagination={false} dataSource={vm.guest_disks}
              locale={{ emptyText: 'Not reported. VMware Tools must be running in the VM.' }}
              columns={[
                { title: 'Mount / drive', dataIndex: 'path' },
                { title: 'Capacity', dataIndex: 'capacity_gb', render: fmtGB },
                { title: 'Free', dataIndex: 'free_gb', render: fmtGB },
                { title: 'Used', dataIndex: 'used_pct', render: (v) => <UsageBar value={v} /> },
              ]}
            />
          </Card>}
          {agent && (
            <>
              <div style={{ height: 16 }} />
              <TopProcesses agent={agent} />
              <div style={{ height: 16 }} />
              <InventoryCard key={agent.id} agent={agent} />
              <div style={{ height: 16 }} />
              <RecentTasks vmId={vm.id} />
            </>
          )}
        </Col>
        <Col xs={24} xl={8}>
          {agent ? <AgentCard agent={agent} onChanged={load} /> : <NoAgentCard vm={vm} onChanged={load} />}
          <div style={{ height: 16 }} />
          <OwnerNotesCard vm={vm} onSaved={(v) => { setVm(v); load() }} />
          <div style={{ height: 16 }} />
          <Card size="small" title="Network">
            <Descriptions column={1} size="small">
              <Descriptions.Item label="Current IPs">{vm.ip_addresses?.join(', ') || '—'}</Descriptions.Item>
              <Descriptions.Item label="Last known IPs">{vm.last_known_ips?.join(', ') || '—'}</Descriptions.Item>
              <Descriptions.Item label="MAC addresses">{vm.mac_addresses?.join(', ') || '—'}</Descriptions.Item>
            </Descriptions>
          </Card>
          {attrs.length > 0 && (
            <>
              <div style={{ height: 16 }} />
              <Card size="small" title="vCenter custom attributes">
                <Descriptions column={1} size="small">
                  {attrs.map(([k, v]) => <Descriptions.Item key={k} label={k}>{v || '—'}</Descriptions.Item>)}
                </Descriptions>
              </Card>
            </>
          )}
          <div style={{ height: 16 }} />
          <Card size="small" title="History">
            {history.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} /> : (
              <Timeline
                items={history.slice(0, 50).map((h) => ({
                  color: EVENT_KINDS[h.kind]?.color === 'red' ? 'red' : 'gray',
                  children: (
                    <>
                      <Typography.Text>{h.message}</Typography.Text>
                      <br />
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>{fmtDate(h.ts)}</Typography.Text>
                    </>
                  ),
                }))}
              />
            )}
          </Card>
          <div style={{ height: 16 }} />
          <Card size="small" title="Identifiers">
            <Descriptions column={1} size="small">
              <Descriptions.Item label="Instance UUID"><Typography.Text copyable style={{ fontSize: 12 }}>{vm.uuid}</Typography.Text></Descriptions.Item>
              <Descriptions.Item label="vCenter ID">{vm.moid}</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
      </Row>
    </>
  )
}
