import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  Alert, App, Badge, Button, Card, Col, Descriptions, Drawer, Empty, Input, Popconfirm, Row, Segmented, Skeleton, Space,
  Statistic, Table, Tabs, Tag, Tooltip, Typography,
} from 'antd'
import {
  CloudServerOutlined, DatabaseOutlined, PlayCircleOutlined, PoweroffOutlined, QuestionCircleOutlined, ReloadOutlined,
  SyncOutlined,
} from '@ant-design/icons'
import { api } from '../api'
import { useAuth } from '../auth'
import { fmtAgo, fmtDate, fmtGB, fmtMem, fmtUptime } from '../format'
import UsageBar from '../components/UsageBar'

const STATUS_COLOR = { green: 'success', yellow: 'warning', orange: 'warning', red: 'error', gray: 'default', unknown: 'default' }
const HEALTH_HELP = {
  system: 'Overall appliance health: the worst of the parts below.',
  load: 'CPU load of the vCenter appliance.',
  mem: 'Memory use of the vCenter appliance.',
  storage: 'Disk space on the appliance partitions (logs, database, core dumps).',
  swap: 'Swap use; high swap means the appliance is short of memory.',
  database: 'vCenter\'s PostgreSQL database (space and health).',
  applmgmt: 'The appliance management service (the VAMI on port 5480).',
  'software-packages': 'Whether appliance updates are pending.',
}
const SERVICE_STATE = { started: 'success', stopped: 'default', starting: 'processing', stopping: 'processing' }

function Help({ text }) {
  return <Tooltip title={text}><QuestionCircleOutlined style={{ marginInlineStart: 6, opacity: 0.55 }} /></Tooltip>
}

function StatusTag({ value, children }) {
  return <Tag color={STATUS_COLOR[value] || 'default'} style={{ marginInlineEnd: 0 }}>{children ?? value}</Tag>
}

// -- vCenter -----------------------------------------------------------------------------------------

function ServiceActions({ svc, onDone }) {
  const { message, modal } = App.useApp()
  const [busy, setBusy] = useState(null)
  const run = async (action) => {
    setBusy(action)
    const hide = message.loading(`${action[0].toUpperCase() + action.slice(1)}ing ${svc.name}… this can take a few minutes`, 0)
    try {
      const r = await api.post(`/api/infra/vcenter/services/${encodeURIComponent(svc.id)}/${action}`)
      message.success(r.message, 6)
      onDone()
    } catch (e) {
      message.error(e.message, 8)
    } finally {
      hide()
      setBusy(null)
    }
  }
  // Core services take vCenter down while they restart: ask the admin to type the service ID.
  const confirmCore = (action) => {
    let typed = ''
    modal.confirm({
      title: `${action === 'stop' ? 'Stop' : 'Restart'} ${svc.name}?`,
      width: 520,
      content: (
        <Space direction="vertical" style={{ width: '100%' }}>
          <Alert type="warning" showIcon message={action === 'stop'
            ? 'This is a core vCenter service. Stopping it makes vCenter (and vCentral\'s VM data) unavailable until it is started again, possibly only from the appliance shell.'
            : 'This is a core vCenter service. vCenter and the vSphere Client are unavailable while it restarts (usually a few minutes).'} />
          <Typography.Text>Type <Typography.Text code>{svc.id}</Typography.Text> to confirm:</Typography.Text>
          <Input onChange={(e) => { typed = e.target.value }} autoFocus />
        </Space>
      ),
      okText: action === 'stop' ? 'Stop service' : 'Restart service', okButtonProps: { danger: true },
      onOk: () => {
        if (typed.trim() !== svc.id) {
          message.error('The service ID does not match')
          return Promise.reject()
        }
        return run(action)
      },
    })
  }
  const started = svc.state === 'started'
  // Core services: typed confirmation. Others: a simple popconfirm.
  const button = (action, label, tip, props = {}) => {
    const b = <Button size="small" loading={busy === action} disabled={!!busy && busy !== action} {...props}
      onClick={svc.core && action !== 'start' ? () => confirmCore(action) : undefined}>{label}</Button>
    if (svc.core && action !== 'start') return <Tooltip title={tip}>{b}</Tooltip>
    return (
      <Popconfirm title={`${label} ${svc.name}?`} description={<div style={{ maxWidth: 280 }}>{tip}</div>} okText={label}
        okButtonProps={{ danger: !!props.danger }} onConfirm={() => run(action)}>
        <Tooltip title={tip}>{b}</Tooltip>
      </Popconfirm>
    )
  }
  return (
    <Space size={4}>
      {!started && button('start', 'Start', 'Start this service now. It does not change whether it starts automatically at boot.', { type: 'primary', icon: <PlayCircleOutlined /> })}
      {started && button('restart', 'Restart', 'Stop and start the service again; the usual fix when a vCenter feature hangs or misbehaves.', { icon: <SyncOutlined /> })}
      {started && button('stop', 'Stop', 'Stop the service until someone starts it again (or the appliance reboots, if it starts automatically).', { danger: true, icon: <PoweroffOutlined /> })}
    </Space>
  )
}

function VCenterTab() {
  const { can } = useAuth()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)
  const [filter, setFilter] = useState('all')
  const [search, setSearch] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    api.get('/api/infra/vcenter').then((d) => { setData(d); setError(null) }, (e) => setError(e.message)).finally(() => setLoading(false))
  }, [])
  useEffect(load, [load])

  const services = useMemo(() => {
    const q = search.trim().toLowerCase()
    return (data?.services || []).filter((s) => {
      if (filter === 'stopped' && s.state !== 'stopped') return false
      if (filter === 'problems' && !(s.health && s.health !== 'healthy') && !(s.state === 'stopped' && s.startup === 'automatic')) return false
      return !q || s.name.toLowerCase().includes(q) || s.id.toLowerCase().includes(q)
    })
  }, [data, filter, search])

  if (error) return <Alert type="error" showIcon message="Could not read vCenter" description={error} action={<Button onClick={load}>Retry</Button>} />
  if (!data) return <Skeleton active />
  const { about, appliance, health, errors, vm } = data
  const svcList = data.services || []
  const stoppedAuto = svcList.filter((s) => s.state === 'stopped' && s.startup === 'automatic').length
  const unhealthy = svcList.filter((s) => s.health && s.health !== 'healthy').length

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={14}>
          <Card title={<Space><CloudServerOutlined />vCenter Server</Space>} size="small" style={{ height: '100%' }}
            extra={<Tooltip title="Read everything again from vCenter"><Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={load}>Refresh</Button></Tooltip>}>
            {errors?.about && <Alert type="warning" showIcon message={errors.about} style={{ marginBottom: 12 }} />}
            <Descriptions size="small" column={{ xs: 1, sm: 2 }} colon={false}>
              <Descriptions.Item label="Address">{data.host}</Descriptions.Item>
              <Descriptions.Item label="IP">{data.ips?.join(', ') || '—'}</Descriptions.Item>
              <Descriptions.Item label="Product" span={2}>{about?.name || '—'}</Descriptions.Item>
              <Descriptions.Item label="Version">{appliance?.version || about?.version || '—'}</Descriptions.Item>
              <Descriptions.Item label="Build">{appliance?.build || about?.build || '—'}</Descriptions.Item>
              <Descriptions.Item label={<>Uptime<Help text="Time since the vCenter appliance last booted." /></>}>{fmtUptime(appliance?.uptime_seconds)}</Descriptions.Item>
              <Descriptions.Item label="Installed">{appliance?.install_time ? fmtDate(appliance.install_time) : '—'}</Descriptions.Item>
              <Descriptions.Item label={<>Clock<Help text="vCenter's own time. A big difference from real time breaks logins and certificates." /></>}>{about?.server_time ? fmtDate(about.server_time) : '—'}</Descriptions.Item>
              <Descriptions.Item label={<>Runs in VM<Help text="The VM in this vCenter whose IP is the vCenter address." /></>}>
                {vm ? <Link to={`/vms/${vm.id}`}>{vm.name}</Link> : '—'}
              </Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
        <Col xs={24} lg={10}>
          <Card title={<>Appliance health<Help text="vCenter's own health checks (the same as the appliance management page on port 5480). Green is fine; yellow/orange need a look; red needs action." /></>}
            size="small" style={{ height: '100%' }}>
            {errors?.appliance && <Alert type="warning" showIcon message="Appliance details unavailable" description={errors.appliance} style={{ marginBottom: 12 }} />}
            {Object.keys(health || {}).length ? (
              <Row gutter={[8, 8]}>
                {Object.entries(health).map(([k, v]) => (
                  <Col span={12} key={k}>
                    <Tooltip title={HEALTH_HELP[k]}>
                      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                        <Typography.Text style={{ textTransform: 'capitalize' }}>{k.replace('-', ' ').replace('mem', 'memory').replace('applmgmt', 'appliance mgmt')}</Typography.Text>
                        <StatusTag value={v} />
                      </Space>
                    </Tooltip>
                  </Col>
                ))}
              </Row>
            ) : !errors?.appliance && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No health data" />}
          </Card>
        </Col>
      </Row>

      <Card size="small" title={<>Services<Help text="vCenter's internal services (the same list as the vSphere Client's Administration > Deployment > System Configuration). Core services are marked; restarting them interrupts vCenter itself." /></>}
        extra={(
          <Space wrap>
            <Segmented size="small" value={filter} onChange={setFilter} options={[
              { value: 'all', label: `All ${svcList.length}` },
              { value: 'stopped', label: `Stopped ${svcList.filter((s) => s.state === 'stopped').length}` },
              { value: 'problems', label: <Tooltip title="Unhealthy, or set to start automatically but stopped">Problems {stoppedAuto + unhealthy}</Tooltip> },
            ]} />
            <Input.Search size="small" allowClear placeholder="Find a service" style={{ width: 200 }} value={search} onChange={(e) => setSearch(e.target.value)} />
          </Space>
        )}>
        {errors?.services && (
          <Alert type="warning" showIcon style={{ marginBottom: 12 }} message="Could not list vCenter services" description={errors.services} />
        )}
        {!can('admin') && svcList.length > 0 && (
          <Alert type="info" showIcon style={{ marginBottom: 12 }} message="Only admins can start, stop or restart services." />
        )}
        <Table size="small" rowKey="id" dataSource={services} pagination={false} scroll={{ x: 'max-content' }}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={errors?.services ? 'Not available' : 'No services match'} /> }}
          columns={[
            {
              title: 'Service', key: 'name', sorter: (a, b) => a.name.localeCompare(b.name),
              render: (_, s) => (
                <Space direction="vertical" size={0}>
                  <Space size={6}>
                    <Typography.Text strong>{s.name}</Typography.Text>
                    {s.core && <Tooltip title="Core service: vCenter does not work without it; restarting it interrupts vCenter for a few minutes."><Tag color="purple">core</Tag></Tooltip>}
                  </Space>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>{s.id}</Typography.Text>
                </Space>
              ),
            },
            {
              title: 'State', dataIndex: 'state', width: 120, sorter: (a, b) => a.state.localeCompare(b.state),
              render: (v, s) => (
                <Space size={4}>
                  <Badge status={SERVICE_STATE[v] || 'default'} text={v || '—'} />
                  {v === 'stopped' && s.startup === 'automatic' && <Tooltip title="Set to start automatically but not running: usually a problem."><Tag color="warning">should run</Tag></Tooltip>}
                </Space>
              ),
            },
            {
              title: <>Health<Help text="Health reported by the service itself; empty when it is stopped or does not report health." /></>,
              dataIndex: 'health', width: 110,
              render: (v) => (v ? <Tag color={v === 'healthy' ? 'success' : v === 'degraded' ? 'warning' : 'error'}>{v.replace('_', ' ')}</Tag> : '—'),
            },
            {
              title: <>Startup<Help text="Automatic: starts when the appliance boots. Manual: only when someone starts it. Disabled: cannot be started." /></>,
              dataIndex: 'startup', width: 110, render: (v) => v || '—',
            },
            ...(can('admin') ? [{ title: 'Actions', key: 'a', width: 230, render: (_, s) => s.startup !== 'disabled' && <ServiceActions svc={s} onDone={load} /> }] : []),
          ]} />
      </Card>
    </Space>
  )
}

// -- ESXi hosts --------------------------------------------------------------------------------------

function HostDetail({ h }) {
  return (
    <Row gutter={[16, 16]} style={{ padding: '4px 0' }}>
      <Col xs={24} lg={10}>
        <Card size="small" title="Hardware">
          <Descriptions size="small" column={1} colon={false}>
            <Descriptions.Item label="Server">{[h.vendor, h.model].filter(Boolean).join(' ') || '—'}</Descriptions.Item>
            <Descriptions.Item label="CPU">{h.cpu_model || '—'}</Descriptions.Item>
            <Descriptions.Item label="Sockets / cores / threads">{[h.cpu_sockets, h.cpu_cores, h.cpu_threads].map((x) => x ?? '—').join(' / ')}</Descriptions.Item>
            <Descriptions.Item label="Memory">{fmtMem(h.mem_total_mb)}</Descriptions.Item>
            <Descriptions.Item label="Network cards">{h.nics ?? '—'}</Descriptions.Item>
            <Descriptions.Item label="Management IP">{h.management_ips.join(', ') || '—'}</Descriptions.Item>
            <Descriptions.Item label="ESXi">{h.product || '—'}</Descriptions.Item>
            <Descriptions.Item label="Booted">{h.boot_time ? `${fmtDate(h.boot_time)} (${fmtAgo(h.boot_time)})` : '—'}</Descriptions.Item>
          </Descriptions>
        </Card>
      </Col>
      <Col xs={24} lg={14}>
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Card size="small" title={<Space><DatabaseOutlined />Datastores</Space>}>
            <Table size="small" rowKey="name" pagination={false} dataSource={h.datastores}
              columns={[
                { title: 'Name', dataIndex: 'name', render: (v, d) => <Space>{v}{!d.accessible && <Tag color="error">inaccessible</Tag>}</Space> },
                { title: 'Type', dataIndex: 'type', width: 80 },
                { title: 'Used', dataIndex: 'used_pct', width: 170, render: (v) => <UsageBar value={v} /> },
                { title: 'Free', dataIndex: 'free_gb', width: 90, align: 'right', render: fmtGB },
                { title: 'Size', dataIndex: 'capacity_gb', width: 90, align: 'right', render: fmtGB },
              ]} />
          </Card>
          {h.alarms.length > 0 && (
            <Card size="small" title="Triggered alarms">
              {h.alarms.map((a, i) => (
                <div key={i} style={{ marginBottom: 6 }}>
                  <StatusTag value={a.status} /> <Typography.Text>{a.name}</Typography.Text>{' '}
                  <Typography.Text type="secondary">{a.time ? fmtAgo(a.time) : ''}{a.acknowledged ? ' · acknowledged' : ''}</Typography.Text>
                </div>
              ))}
            </Card>
          )}
        </Space>
      </Col>
      <Col xs={24}>
        <Card size="small" title={<>Hardware sensors<Help text="Temperatures, fans, power supplies and voltages reported by the server's management controller, plus any sensor that is not healthy." /></>}>
          {h.sensors.length ? (
            <Table size="small" rowKey="name" pagination={h.sensors.length > 12 ? { pageSize: 12, size: 'small' } : false} dataSource={h.sensors}
              columns={[
                { title: 'Sensor', dataIndex: 'name' },
                { title: 'Type', dataIndex: 'type', width: 110 },
                { title: 'Reading', key: 'r', width: 100, align: 'right', render: (_, s) => (s.reading === null ? '—' : `${s.reading} ${s.unit}`) },
                { title: 'State', dataIndex: 'state', width: 90, render: (v) => <StatusTag value={v} /> },
              ]} />
          ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="This host reports no hardware sensors (common on nested or some white-box servers)" />}
        </Card>
      </Col>
      <Col xs={24}>
        <Card size="small" title={<>ESXi services<Help text="Services on the host itself (SSH, NTP, ...). Shown for monitoring only; manage them in the vSphere Client." /></>}>
          <Space size={[6, 6]} wrap>
            {h.services.map((s) => (
              <Tooltip key={s.id} title={`${s.id} · startup policy: ${s.policy}`}>
                <Tag color={s.running ? 'success' : 'default'}>{s.name}{s.running ? '' : ' (stopped)'}</Tag>
              </Tooltip>
            ))}
          </Space>
        </Card>
      </Col>
    </Row>
  )
}

function HostsTab() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)
  const [open, setOpen] = useState(null)
  const load = useCallback(() => {
    setLoading(true)
    api.get('/api/infra/hosts').then((d) => { setData(d.items); setError(null) }, (e) => setError(e.message)).finally(() => setLoading(false))
  }, [])
  useEffect(() => {
    load()
    const t = setInterval(load, 60000)
    return () => clearInterval(t)
  }, [load])

  if (error && !data) return <Alert type="error" showIcon message="Could not read ESXi hosts" description={error} action={<Button onClick={load}>Retry</Button>} />
  if (!data) return <Skeleton active />
  const current = data.find((h) => h.moid === open)
  const alarms = data.reduce((n, h) => n + h.alarms.length, 0)
  const sensors = data.reduce((n, h) => n + h.sensor_problems, 0)
  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Row gutter={[12, 12]}>
        <Col xs={12} md={6}><Card size="small"><Statistic title="Hosts" value={data.length} /></Card></Col>
        <Col xs={12} md={6}><Card size="small"><Statistic title={<>Not connected<Help text="Hosts vCenter cannot talk to (disconnected or not responding)." /></>} value={data.filter((h) => h.connection !== 'connected').length} /></Card></Col>
        <Col xs={12} md={6}><Card size="small"><Statistic title={<>Triggered alarms<Help text="vCenter alarms currently active on the hosts." /></>} value={alarms} valueStyle={alarms ? { color: '#d4380d' } : undefined} /></Card></Col>
        <Col xs={12} md={6}><Card size="small"><Statistic title={<>Sensor warnings<Help text="Hardware sensors in a warning or critical state (temperature, fans, power...)." /></>} value={sensors} valueStyle={sensors ? { color: '#d4380d' } : undefined} /></Card></Col>
      </Row>
      <Card size="small" title={<Typography.Text type="secondary">Live from vCenter, refreshed every minute. Click a host for hardware, datastores, sensors and services.</Typography.Text>}
        extra={<Tooltip title="Read the hosts again from vCenter now"><Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={load}>Refresh</Button></Tooltip>}>
        <Table size="small" rowKey="moid" dataSource={data} pagination={false} scroll={{ x: 'max-content' }}
          onRow={(h) => ({ onClick: () => setOpen(h.moid), style: { cursor: 'pointer' } })}
          columns={[
            {
              title: 'Host', dataIndex: 'name', render: (v, h) => (
                <Space direction="vertical" size={0}>
                  <Space size={6}>
                    <Tooltip title={`vCenter overall status: ${h.status}`}><Badge status={STATUS_COLOR[h.status] || 'default'} /></Tooltip>
                    <Typography.Text strong>{v}</Typography.Text>
                  </Space>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>{h.cluster ? `Cluster ${h.cluster}` : 'Standalone host'}</Typography.Text>
                </Space>
              ),
            },
            {
              title: 'State', key: 'state', render: (_, h) => (
                <Space size={4} wrap>
                  <Tag color={h.connection === 'connected' ? 'success' : 'error'}>{h.connection}</Tag>
                  {h.maintenance && <Tooltip title="In maintenance mode: no VMs run here until it exits."><Tag color="warning">maintenance</Tag></Tooltip>}
                </Space>
              ),
            },
            { title: 'ESXi', key: 'v', render: (_, h) => (h.version ? <Tooltip title={h.product}>{`${h.version} (${h.build})`}</Tooltip> : '—') },
            {
              title: <>CPU<Help text="CPU in use across all cores, as vCenter reports it." /></>, dataIndex: 'cpu_pct', width: 170,
              render: (v, h) => <Tooltip title={`${h.cpu_used_mhz ?? '—'} of ${h.cpu_total_mhz ?? '—'} MHz`}><span><UsageBar value={v} /></span></Tooltip>,
            },
            {
              title: <>Memory<Help text="Host memory in use (VMs plus ESXi itself)." /></>, dataIndex: 'mem_pct', width: 170,
              render: (v, h) => <Tooltip title={`${fmtMem(h.mem_used_mb)} of ${fmtMem(h.mem_total_mb)}`}><span><UsageBar value={v} /></span></Tooltip>,
            },
            {
              title: <>Fullest datastore<Help text="The most used datastore on this host; open the host for all of them." /></>, key: 'ds', width: 190,
              render: (_, h) => (h.datastores[0] ? <Tooltip title={h.datastores[0].name}><span><UsageBar value={h.datastores[0].used_pct} /></span></Tooltip> : '—'),
            },
            { title: 'VMs on', key: 'vms', align: 'right', render: (_, h) => <Tooltip title={`${h.vms_on} powered on of ${h.vms_total} registered`}>{h.vms_on} / {h.vms_total}</Tooltip> },
            { title: 'Uptime', dataIndex: 'uptime_seconds', render: fmtUptime },
            {
              title: 'Alerts', key: 'al', render: (_, h) => (
                <Space size={4}>
                  {h.alarms.length > 0 && <Tag color="error">{h.alarms.length} alarm{h.alarms.length > 1 ? 's' : ''}</Tag>}
                  {h.sensor_problems > 0 && <Tag color="warning">{h.sensor_problems} sensor{h.sensor_problems > 1 ? 's' : ''}</Tag>}
                  {!h.alarms.length && !h.sensor_problems && <Typography.Text type="secondary">none</Typography.Text>}
                </Space>
              ),
            },
          ]} />
      </Card>
      <Drawer open={!!current} onClose={() => setOpen(null)} width={Math.min(980, window.innerWidth)} destroyOnClose
        title={current && (
          <Space>
            <Badge status={STATUS_COLOR[current.status] || 'default'} />{current.name}
            <Typography.Text type="secondary" style={{ fontWeight: 'normal' }}>{current.cluster ? `Cluster ${current.cluster}` : 'Standalone host'}</Typography.Text>
          </Space>
        )}>
        {current && <HostDetail h={current} />}
      </Drawer>
    </Space>
  )
}

export default function Infrastructure() {
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') === 'hosts' ? 'hosts' : 'vcenter'
  return (
    <div>
      <Typography.Title level={3} style={{ marginTop: 0, marginBottom: 4 }}>Infrastructure</Typography.Title>
      <Typography.Paragraph type="secondary">vCenter Server and the ESXi hosts the VMs run on, kept apart from the VM list.</Typography.Paragraph>
      <Tabs activeKey={tab} onChange={(k) => setParams({ tab: k })} destroyInactiveTabPane items={[
        { key: 'vcenter', label: 'vCenter Server', children: <VCenterTab /> },
        { key: 'hosts', label: 'ESXi hosts', children: <HostsTab /> },
      ]} />
    </div>
  )
}
