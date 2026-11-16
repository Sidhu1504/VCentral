import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Alert, App, Button, Card, Col, Empty, Form, Input, Modal, Popconfirm, Row, Skeleton, Space, Statistic, Table, Tag, Tooltip, Typography } from 'antd'
import { CopyOutlined, PlusOutlined, WarningFilled } from '@ant-design/icons'
import { api } from '../api'
import { useAuth } from '../auth'
import { useViz } from '../components/Chart'
import { fmtAgo, STATUS } from '../format'

// Colours: VM use is the main series colour; everything else that blocks an address has its own.
const STATE = {
  conflict: { label: 'Conflict (2+ VMs)', color: STATUS.critical, tag: 'error' },
  used: { label: 'VM', tag: 'processing' },
  infra: { label: 'Infrastructure', color: '#8b5cf6', tag: 'purple' },
  template: { label: 'Template', color: '#06b6d4', tag: 'cyan' },
  reserved: { label: 'Reserved', color: '#64748b', tag: 'default' },
  stale: { label: 'Powered-off VM', color: STATUS.warning, tag: 'warning' },
  device: { label: 'Other device', color: '#ec835a', tag: 'orange' },
}

function ipToInt(ip) {
  return ip.split('.').reduce((acc, o) => acc * 256 + Number(o), 0)
}
function intToIp(n) {
  return [24, 16, 8, 0].map((s) => Math.floor(n / 2 ** s) % 256).join('.')
}

/** What holds an address, as text. */
function holder(r) {
  if (r.vms.length) return r.vms.map((v) => v.name + (v.by_mac ? ' (found by MAC)' : '')).join(', ')
  if (r.infra.length) return r.infra.join('; ')
  if (r.templates.length) return r.templates.map((t) => `template ${t.name}`).join(', ')
  if (r.reservation) return `Reserved: ${r.reservation.label}`
  if (r.stale_vms.length) return `${r.stale_vms.map((v) => v.name).join(', ')} (powered off)`
  if (r.device) return `Unknown device${r.device.mac ? ` ${r.device.mac}` : ''}`
  return ''
}

function SubnetGrid({ subnet }) {
  const viz = useViz()
  const navigate = useNavigate()
  const size = 2 ** (32 - subnet.prefixlen)
  if (subnet.version !== 4 || size > 1024) return null
  const start = ipToInt(subnet.first)
  const byIp = Object.fromEntries(subnet.ips.map((r) => [r.ip, r]))
  const cols = size >= 256 ? 32 : 16
  return (
    <div role="grid" aria-label={`Address map for ${subnet.cidr}`}
      style={{ display: 'grid', gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`, gap: 2, margin: '12px 0' }}>
      {Array.from({ length: size }, (_, i) => {
        const ip = intToIp(start + i)
        const row = byIp[ip]
        const edge = subnet.prefixlen < 31 && (i === 0 || i === size - 1)
        let style = { background: 'transparent', border: `1px solid ${viz.grid}` }
        let tip = `${ip}: free`
        if (edge) {
          style = { background: viz.axis }
          tip = `${ip}: ${i === 0 ? 'network' : 'broadcast'} address`
        } else if (row) {
          style = { background: row.state === 'used' ? viz.series[0] : STATE[row.state].color }
          tip = `${ip}: ${STATE[row.state].label}: ${holder(row)}${row.warnings.length ? ` ⚠ ${row.warnings[0]}` : ''}`
        }
        const target = row && (row.vms[0] || row.stale_vms[0])
        return (
          <Tooltip key={ip} title={tip} mouseEnterDelay={0.05}>
            <div onClick={() => target && navigate(`/vms/${target.id}`)}
              style={{ aspectRatio: '1', borderRadius: 3, cursor: target ? 'pointer' : 'default', position: 'relative', ...style,
                boxShadow: row?.warnings.length ? `inset 0 0 0 2px ${STATUS.critical}` : undefined }} />
          </Tooltip>
        )
      })}
    </div>
  )
}

function Legend() {
  const viz = useViz()
  const item = (bg, label, border) => (
    <Space size={6} key={label}>
      <span style={{ width: 12, height: 12, borderRadius: 3, display: 'inline-block', background: bg, border }} />
      <Typography.Text type="secondary">{label}</Typography.Text>
    </Space>
  )
  return (
    <Space wrap size={14}>
      {item(viz.series[0], 'VM')}
      {Object.entries(STATE).filter(([k]) => k !== 'used').map(([, s]) => item(s.color, s.label))}
      {item('transparent', 'Free', `1px solid ${viz.grid}`)}
      {item('transparent', 'Has a warning', `2px solid ${STATUS.critical}`)}
    </Space>
  )
}

function ReserveModal({ onClose, onSaved, initial }) {
  const [form] = Form.useForm()
  const { message } = App.useApp()
  const save = async () => {
    const v = await form.validateFields()
    try {
      await api.post('/api/network/reservations', { ...v, end_ip: v.end_ip || null })
      message.success('Reserved')
      onSaved()
      onClose()
    } catch (e) {
      message.error(e.message)
    }
  }
  return (
    <Modal open title="Reserve addresses" onCancel={onClose} onOk={save} okText="Reserve" destroyOnClose>
      <Typography.Paragraph type="secondary">
        For addresses used by things vCentral cannot see, or kept free on purpose: a printer, firewall, DHCP pool,
        or an address promised to a new VM. Reserved addresses are never shown as free.
      </Typography.Paragraph>
      <Form form={form} layout="vertical" initialValues={initial} preserve={false}>
        <div style={{ display: 'flex', gap: 12 }}>
          <Form.Item name="start_ip" label="Address (or first of a range)" style={{ flex: 1 }} rules={[{ required: true }]}><Input placeholder="192.168.14.100" /></Form.Item>
          <Form.Item name="end_ip" label="Last of the range (optional)" style={{ flex: 1 }}><Input placeholder="192.168.14.150" /></Form.Item>
        </div>
        <Form.Item name="label" label="What it is for" rules={[{ required: true, max: 128 }]}><Input placeholder="e.g. DHCP pool, Office printer, Firewall" /></Form.Item>
        <Form.Item name="note" label="Note"><Input.TextArea rows={2} maxLength={1000} /></Form.Item>
      </Form>
    </Modal>
  )
}

export default function Network() {
  const { can } = useAuth()
  const { message } = App.useApp()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [reserving, setReserving] = useState(null)
  const load = useCallback(() => api.get('/api/network/subnets').then(setData, (e) => setError(e.message)), [])
  useEffect(() => { load() }, [load])

  if (error) return <Alert type="error" message={error} showIcon />
  if (!data) return <Skeleton active />
  const canEdit = can('admin', 'operator')
  const unreserve = async (r) => {
    try {
      await api.del(`/api/network/reservations/${r.id}`)
      message.success('Reservation removed')
      load()
    } catch (e) {
      message.error(e.message)
    }
  }
  const copy = (text) => navigator.clipboard?.writeText(text).then(() => message.success(`Copied ${text}`), () => message.info(text))
  const vmLinks = (list) => list.map((v, i) => <span key={v.id}>{i > 0 && ', '}<Link to={`/vms/${v.id}`}>{v.name}</Link>{v.by_mac && <Tooltip title="VMware Tools did not report this IP; the network scan found the VM's MAC address on it"><Tag style={{ marginLeft: 4 }}>by MAC</Tag></Tooltip>}</span>)
  const scan = data.scan

  return (
    <>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 8 }} wrap>
        <Typography.Title level={3} style={{ margin: 0 }}>IP & subnets</Typography.Title>
        {canEdit && <Button icon={<PlusOutlined />} onClick={() => setReserving({})}>Reserve addresses</Button>}
      </Space>
      <Typography.Paragraph type="secondary">
        An address is shown as free only when nothing claims it: no VM (running or powered off), no template, no
        gateway, DNS server, ESXi host or vCenter, no reservation, and nothing answered the network scan.{' '}
        {scan.interval_minutes
          ? (scan.finished_at
            ? <>Last network scan {fmtAgo(scan.finished_at)}: {scan.answered} of {scan.scanned} addresses answered ({scan.method === 'ping' ? 'ping' : 'TCP probes'} and ARP), every {scan.interval_minutes} min.</>
            : <>The first network scan runs within a few minutes.</>)
          : <Typography.Text type="warning">The network scan is off (Settings), so devices that are not VMs are only known if you reserve them.</Typography.Text>}
      </Typography.Paragraph>
      {data.subnets.length === 0 && (
        <Card><Empty description="No subnets configured. Set VCENTRAL_SUBNETS (for example 10.10.20.0/24) and restart vCentral." /></Card>
      )}
      {data.subnets.map((s) => (
        <Card key={s.cidr} title={s.cidr} size="small" style={{ marginBottom: 16 }} extra={<Legend />}>
          <Row gutter={[16, 8]}>
            <Col xs={12} sm={8} lg={3}><Statistic title="Usable" value={s.usable} /></Col>
            <Col xs={12} sm={8} lg={3}><Statistic title="Free" value={s.free} valueStyle={{ color: STATUS.good }} /></Col>
            <Col xs={12} sm={8} lg={3}><Statistic title="VMs" value={s.used} /></Col>
            <Col xs={12} sm={8} lg={3}><Statistic title="Infrastructure" value={s.counts.infra} /></Col>
            <Col xs={12} sm={8} lg={3}><Statistic title="Templates" value={s.counts.template} /></Col>
            <Col xs={12} sm={8} lg={3}><Statistic title="Reserved" value={s.counts.reserved} /></Col>
            <Col xs={12} sm={8} lg={3}><Statistic title="Powered-off VMs" value={s.counts.stale} /></Col>
            <Col xs={12} sm={8} lg={3}><Statistic title="Other devices" value={s.counts.device} /></Col>
          </Row>
          {(s.conflicts > 0 || s.warnings > 0) && (
            <Alert type="error" showIcon style={{ marginTop: 12 }}
              message={`${s.conflicts} conflict${s.conflicts === 1 ? '' : 's'}, ${s.warnings} address${s.warnings === 1 ? '' : 'es'} with a warning`}
              description="See the rows marked with a warning below (also outlined in red in the map)." />
          )}
          {s.next_free.length > 0 && (
            <div style={{ marginTop: 12 }}>
              <Typography.Text type="secondary">Next free addresses (click to copy): </Typography.Text>
              <Space size={4} wrap>
                {s.next_free.map((ip) => <Tag key={ip} color="success" style={{ cursor: 'pointer' }} icon={<CopyOutlined />} onClick={() => copy(ip)}>{ip}</Tag>)}
              </Space>
            </div>
          )}
          <SubnetGrid subnet={s} />
          <Table
            size="small" rowKey="ip" dataSource={s.ips} pagination={{ pageSize: 25, hideOnSinglePage: true }} scroll={{ x: 900 }}
            columns={[
              { title: 'IP', dataIndex: 'ip', width: 130, sorter: (a, b) => ipToInt(a.ip) - ipToInt(b.ip) },
              {
                title: 'Taken by', dataIndex: 'state', width: 160,
                filters: Object.entries(STATE).map(([value, v]) => ({ text: v.label, value })),
                onFilter: (v, r) => r.state === v,
                render: (v) => <Tag color={STATE[v].tag}>{STATE[v].label}</Tag>,
              },
              {
                title: 'Who / what', key: 'who',
                render: (_, r) => (r.vms.length ? vmLinks(r.vms) : r.stale_vms.length && !r.infra.length && !r.templates.length && !r.reservation
                  ? <>{vmLinks(r.stale_vms)} <Typography.Text type="secondary">(powered off)</Typography.Text></> : holder(r)),
              },
              {
                title: 'Answers on network', dataIndex: 'device', width: 170,
                render: (d) => (d ? <Tooltip title={d.mac ? `MAC ${d.mac}` : null}>{d.last_seen ? `yes, ${fmtAgo(d.last_seen)}` : 'yes'}</Tooltip> : <Typography.Text type="secondary">—</Typography.Text>),
              },
              {
                title: 'Warnings', dataIndex: 'warnings', width: 300,
                filters: [{ text: 'Has a warning', value: 1 }], onFilter: (_, r) => r.warnings.length > 0,
                render: (w) => w.map((x) => <div key={x}><WarningFilled style={{ color: STATUS.critical, marginRight: 6 }} />{x}</div>),
              },
              ...(canEdit ? [{
                title: '', key: 'a', width: 90,
                render: (_, r) => (r.reservation
                  ? <Popconfirm title={`Remove reservation "${r.reservation.label}"?`} onConfirm={() => unreserve(r.reservation)}><Button size="small">Unreserve</Button></Popconfirm>
                  : !r.vms.length && <Button size="small" onClick={() => setReserving({ start_ip: r.ip, label: r.device ? 'Device' : '' })}>Reserve</Button>),
              }] : []),
            ]}
          />
        </Card>
      ))}
      {data.reservations.length > 0 && (
        <Card title="Reservations" size="small" style={{ marginBottom: 16 }}>
          <Table size="small" rowKey="id" dataSource={data.reservations} pagination={{ pageSize: 20, hideOnSinglePage: true }}
            columns={[
              { title: 'Addresses', key: 'r', render: (_, r) => r.end_ip ? `${r.start_ip} – ${r.end_ip}` : r.start_ip },
              { title: 'For', dataIndex: 'label' },
              { title: 'Note', dataIndex: 'note', ellipsis: true },
              { title: 'By', dataIndex: 'created_by', width: 140, render: (v, r) => `${v || '—'}, ${fmtAgo(r.created_at)}` },
              ...(canEdit ? [{ title: '', key: 'a', width: 100, render: (_, r) => <Popconfirm title={`Remove "${r.label}"?`} onConfirm={() => unreserve(r)}><Button size="small" danger>Remove</Button></Popconfirm> }] : []),
            ]} />
        </Card>
      )}
      {data.outside.length > 0 && (
        <Card title="VMs with IPs outside the configured subnets" size="small">
          <Table size="small" rowKey={(r) => r.ip + r.vm.id} dataSource={data.outside} pagination={{ pageSize: 20, hideOnSinglePage: true }}
            columns={[{ title: 'IP', dataIndex: 'ip' }, { title: 'VM', dataIndex: 'vm', render: (v) => <Link to={`/vms/${v.id}`}>{v.name}</Link> }]} />
        </Card>
      )}
      {reserving && <ReserveModal initial={reserving} onClose={() => setReserving(null)} onSaved={load} />}
    </>
  )
}
