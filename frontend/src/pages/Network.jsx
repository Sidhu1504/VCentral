import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Alert, Card, Col, Empty, Row, Skeleton, Space, Statistic, Table, Tag, Tooltip, Typography } from 'antd'
import { CloseCircleFilled, ClockCircleFilled, CheckCircleFilled } from '@ant-design/icons'
import { api } from '../api'
import { useViz } from '../components/Chart'
import { STATUS } from '../format'

const STATE = {
  used: { label: 'In use', icon: <CheckCircleFilled /> },
  conflict: { label: 'Conflict', icon: <CloseCircleFilled />, color: STATUS.critical },
  stale: { label: 'Last IP of a powered-off VM', icon: <ClockCircleFilled />, color: STATUS.warning },
}

function ipToInt(ip) {
  return ip.split('.').reduce((acc, o) => acc * 256 + Number(o), 0)
}
function intToIp(n) {
  return [24, 16, 8, 0].map((s) => Math.floor(n / 2 ** s) % 256).join('.')
}

function SubnetGrid({ subnet }) {
  const viz = useViz()
  const navigate = useNavigate()
  const size = 2 ** (32 - subnet.prefixlen)
  if (subnet.version !== 4 || size > 1024) return null
  const start = ipToInt(subnet.first)
  const byIp = Object.fromEntries(subnet.ips.map((r) => [r.ip, r]))
  const free = { background: 'transparent', border: `1px solid ${viz.grid}` }
  const cols = size >= 256 ? 32 : 16

  return (
    <div role="grid" aria-label={`Address map for ${subnet.cidr}`}
      style={{ display: 'grid', gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`, gap: 2, margin: '12px 0' }}>
      {Array.from({ length: size }, (_, i) => {
        const ip = intToIp(start + i)
        const row = byIp[ip]
        const reserved = subnet.prefixlen < 31 && (i === 0 || i === size - 1)
        let style = free
        let tip = `${ip} — free`
        if (reserved) {
          style = { background: viz.axis }
          tip = `${ip} — ${i === 0 ? 'network' : 'broadcast'} address`
        } else if (row) {
          const color = row.state === 'used' ? viz.series[0] : STATE[row.state].color
          style = { background: color }
          const names = [...row.vms, ...row.stale_vms].map((v) => v.name).join(', ')
          tip = `${ip} — ${STATE[row.state].label}: ${names}`
        }
        const target = row && (row.vms[0] || row.stale_vms[0])
        return (
          <Tooltip key={ip} title={tip} mouseEnterDelay={0.05}>
            <div
              onClick={() => target && navigate(`/vms/${target.id}`)}
              style={{ aspectRatio: '1', borderRadius: 3, cursor: target ? 'pointer' : 'default', ...style }}
            />
          </Tooltip>
        )
      })}
    </div>
  )
}

function Legend() {
  const viz = useViz()
  const item = (bg, label, icon, border) => (
    <Space size={6}>
      <span style={{ width: 12, height: 12, borderRadius: 3, display: 'inline-block', background: bg, border }} />
      {icon}
      <Typography.Text type="secondary">{label}</Typography.Text>
    </Space>
  )
  return (
    <Space wrap size={16}>
      {item(viz.series[0], 'In use')}
      {item(STATUS.critical, 'Conflict (2+ VMs)', <CloseCircleFilled style={{ color: STATUS.critical }} />)}
      {item(STATUS.warning, 'Last IP of a powered-off VM', <ClockCircleFilled style={{ color: STATUS.warning }} />)}
      {item('transparent', 'Free', null, `1px solid ${viz.grid}`)}
    </Space>
  )
}

export default function Network() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  useEffect(() => {
    api.get('/api/network/subnets').then(setData, (e) => setError(e.message))
  }, [])

  if (error) return <Alert type="error" message={error} showIcon />
  if (!data) return <Skeleton active />

  const vmLinks = (list) => list.map((v, i) => <span key={v.id}>{i > 0 && ', '}<Link to={`/vms/${v.id}`}>{v.name}</Link></span>)

  return (
    <>
      <Typography.Title level={3} style={{ marginTop: 0 }}>IP & subnets</Typography.Title>
      <Typography.Paragraph type="secondary">
        Built from the IPs VMware Tools reports for each VM. VMs without Tools running are not visible here.
      </Typography.Paragraph>
      {data.subnets.length === 0 && (
        <Card><Empty description="No subnets configured. Set VCENTRAL_SUBNETS (for example 10.10.20.0/24) and restart vCentral." /></Card>
      )}
      {data.subnets.map((s) => (
        <Card key={s.cidr} title={s.cidr} size="small" style={{ marginBottom: 16 }} extra={<Legend />}>
          <Row gutter={16}>
            <Col xs={12} sm={6} lg={4}><Statistic title="Usable" value={s.usable} /></Col>
            <Col xs={12} sm={6} lg={4}><Statistic title="In use" value={s.used} suffix={<Typography.Text type="secondary" style={{ fontSize: 14 }}>({s.used_pct}%)</Typography.Text>} /></Col>
            <Col xs={12} sm={6} lg={4}><Statistic title="Free" value={s.free} /></Col>
            <Col xs={12} sm={6} lg={4}><Statistic title="Held by powered-off VMs" value={s.stale} /></Col>
            <Col xs={12} sm={6} lg={4}><Statistic title="Conflicts" value={s.conflicts} prefix={s.conflicts ? <CloseCircleFilled style={{ color: STATUS.critical }} /> : null} /></Col>
          </Row>
          <SubnetGrid subnet={s} />
          <Table
            size="small" rowKey="ip" dataSource={s.ips} pagination={{ pageSize: 20, hideOnSinglePage: true }}
            columns={[
              { title: 'IP', dataIndex: 'ip', width: 140 },
              {
                title: 'State', dataIndex: 'state', width: 220,
                filters: Object.entries(STATE).map(([value, v]) => ({ text: v.label, value })),
                onFilter: (v, r) => r.state === v,
                render: (v) => <Tag color={v === 'conflict' ? 'error' : v === 'stale' ? 'warning' : 'processing'} icon={STATE[v].icon}>{STATE[v].label}</Tag>,
              },
              { title: 'Used by (powered on)', dataIndex: 'vms', render: vmLinks },
              { title: 'Last held by (powered off)', dataIndex: 'stale_vms', render: vmLinks },
            ]}
          />
        </Card>
      ))}
      {data.outside.length > 0 && (
        <Card title="VMs with IPs outside the configured subnets" size="small">
          <Table size="small" rowKey={(r) => r.ip + r.vm.id} dataSource={data.outside} pagination={{ pageSize: 20, hideOnSinglePage: true }}
            columns={[{ title: 'IP', dataIndex: 'ip' }, { title: 'VM', dataIndex: 'vm', render: (v) => <Link to={`/vms/${v.id}`}>{v.name}</Link> }]} />
        </Card>
      )}
    </>
  )
}
