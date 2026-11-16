import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Alert, Card, Col, Empty, List, Progress, Row, Skeleton, Space, Statistic, Table, Tag, Tooltip, Typography } from 'antd'
import { CheckCircleFilled, CloseCircleFilled, ExclamationCircleFilled } from '@ant-design/icons'
import { api } from '../api'
import Chart, { axisStyle, baseOption, useViz } from '../components/Chart'
import { CriticalStar, HealthTag } from '../components/Health'
import UsageBar from '../components/UsageBar'
import { EVENT_KINDS, fmtAgo, fmtDate, fmtGB, OS_LABEL, STATUS, usageColor } from '../format'

function Tile({ title, value, to, hint }) {
  const body = (
    <Card size="small" hoverable={!!to} style={{ height: '100%' }}>
      <Statistic title={title} value={value} />
      {hint && <Typography.Text type="secondary" style={{ fontSize: 12 }}>{hint}</Typography.Text>}
    </Card>
  )
  return to ? <Link to={to}>{body}</Link> : body
}

function TopList({ items, empty }) {
  if (!items.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={empty} />
  return (
    <List
      size="small"
      dataSource={items}
      renderItem={(i) => (
        <List.Item extra={<UsageBar value={i.value} width={80} />}>
          <Link to={`/vms/${i.id}`} style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{i.name}</Link>
        </List.Item>
      )}
    />
  )
}

export default function Dashboard() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const viz = useViz()

  useEffect(() => {
    const load = () => api.get('/api/dashboard').then(setData, (e) => setError(e.message))
    load()
    const t = setInterval(load, 15000)
    return () => clearInterval(t)
  }, [])

  const osOption = useMemo(() => {
    if (!data) return null
    const rows = Object.entries(data.os_family).sort((a, b) => a[1] - b[1])
    return {
      ...baseOption(viz),
      grid: { left: 8, right: 40, top: 8, bottom: 8, containLabel: true },
      tooltip: { ...baseOption(viz).tooltip, trigger: 'item', formatter: (p) => `${p.name}: ${p.value} VMs` },
      xAxis: { type: 'value', ...axisStyle(viz), axisLabel: { show: false }, splitLine: { show: false } },
      yAxis: { type: 'category', data: rows.map(([k]) => OS_LABEL[k] || k), ...axisStyle(viz), splitLine: { show: false } },
      series: [{
        type: 'bar', data: rows.map(([, v]) => v), barMaxWidth: 22,
        itemStyle: { color: viz.series[0], borderRadius: [0, 4, 4, 0] },
        label: { show: true, position: 'right', color: viz.textSecondary },
      }],
    }
  }, [data, viz])

  const lifecycleOption = useMemo(() => {
    if (!data) return null
    const days = data.lifecycle_30d
    return {
      ...baseOption(viz),
      tooltip: { ...baseOption(viz).tooltip, trigger: 'axis', axisPointer: { type: 'shadow' } },
      xAxis: { type: 'category', data: days.map((d) => d.date.slice(5)), ...axisStyle(viz), splitLine: { show: false } },
      yAxis: { type: 'value', minInterval: 1, ...axisStyle(viz) },
      series: [
        { name: 'Created', type: 'bar', data: days.map((d) => d.created), itemStyle: { color: viz.series[0], borderRadius: [4, 4, 0, 0] }, barGap: '10%', barMaxWidth: 10 },
        { name: 'Deleted', type: 'bar', data: days.map((d) => d.deleted), itemStyle: { color: viz.series[1], borderRadius: [4, 4, 0, 0] }, barMaxWidth: 10 },
      ],
    }
  }, [data, viz])

  if (error) return <Alert type="error" message={error} showIcon />
  if (!data) return <Skeleton active />

  const t = data.totals
  const a = data.allocated
  return (
    <>
      <Typography.Title level={3} style={{ marginTop: 0 }}>Dashboard</Typography.Title>
      {!data.sync?.status && (
        <Alert style={{ marginBottom: 16 }} type="info" showIcon
          message="Waiting for the first vCenter sync. Make sure the worker is running and vCenter settings are correct." />
      )}
      <Row gutter={[16, 16]}>
        <Col xs={12} md={6} xl={3}>
          <Tile title={<Space size={6}><CloseCircleFilled style={{ color: STATUS.critical }} />Critical problems</Space>}
            value={t.health_critical} to="/vms?health=critical&status=all" hint={`${t.critical_vms} VMs marked critical`} />
        </Col>
        <Col xs={12} md={6} xl={3}>
          <Tile title={<Space size={6}><ExclamationCircleFilled style={{ color: STATUS.warning }} />Warnings</Space>}
            value={t.health_warning} to="/vms?health=warning" />
        </Col>
        <Col xs={12} md={6} xl={3}><Tile title="No IP address" value={t.no_ip} to="/vms?health=problem" hint="Powered-on VMs" /></Col>
        <Col xs={12} md={6} xl={3}><Tile title="Total VMs" value={t.total} to="/vms" hint={`${t.powered_on} on · ${t.powered_off} off`} /></Col>
        <Col xs={12} md={6} xl={3}>
          <Tile title="Agent installed" value={t.managed} to="/vms?agent=online"
            hint={`${t.agents_online} online · ${t.agents_offline} offline${t.agents_unlinked ? ` · ${t.agents_unlinked} not linked` : ''}`} />
        </Col>
        <Col xs={12} md={6} xl={3}><Tile title="Owner unknown" value={t.owner_unknown} to="/owners" hint="See suggestions" /></Col>
        <Col xs={12} md={6} xl={3}>
          <Tile title="Created / deleted (7 days)" value={`${t.created_7d} / ${t.deleted_7d}`} to="/activity" />
        </Col>
        <Col xs={12} md={6} xl={3}><Tile title={`Disks ≥ ${data.disk_warnings.threshold}%`} value={data.disk_warnings.count} hint="From VMware Tools" /></Col>
      </Row>

      <Card size="small" style={{ marginTop: 16 }}
        title={<Space>Needs attention {data.attention.length > 0 && <Tag>{data.attention.length}</Tag>}</Space>}
        extra={<Link to="/vms?health=problem&status=all">All problems</Link>}>
        {data.attention.length === 0 ? (
          <Space><CheckCircleFilled style={{ color: STATUS.good }} /><Typography.Text>No problems. All critical VMs are up and every VM has an IP address.</Typography.Text></Space>
        ) : (
          <Table size="small" rowKey="id" dataSource={data.attention} pagination={{ pageSize: 8, hideOnSinglePage: true }}
            columns={[
              { title: 'Health', dataIndex: 'health', width: 120, render: (v, r) => <HealthTag health={v} problems={r.problems} /> },
              { title: 'VM', dataIndex: 'name', width: 220, ellipsis: true, render: (v, r) => <Space size={6}><CriticalStar critical={r.critical} /><Link to={`/vms/${r.id}`}>{v}</Link></Space> },
              { title: 'Problem', key: 'p', render: (_, r) => r.problems.map((p) => p.message).join(' · ') },
              { title: 'Since', key: 's', width: 140, render: (_, r) => fmtAgo(r.problems.map((p) => p.since).sort()[0]) },
            ]} />
        )}
      </Card>

      <Card size="small" style={{ marginTop: 16 }} title={<Space>New and deleted VMs (last 7 days) <Tag>{data.vm_changes_7d.length}</Tag></Space>}
        extra={<Link to="/activity?kind=created">All activity</Link>}>
        {data.vm_changes_7d.length === 0 ? (
          <Typography.Text type="secondary">No VMs were created or deleted in the last 7 days.</Typography.Text>
        ) : (
          <Table size="small" rowKey={(r) => `${r.vm_id}-${r.ts}-${r.kind}`} dataSource={data.vm_changes_7d}
            pagination={{ pageSize: 8, hideOnSinglePage: true }} scroll={{ x: 700 }}
            columns={[
              { title: 'When', dataIndex: 'ts', width: 150, render: (v) => <Tooltip title={fmtDate(v)}>{fmtAgo(v)}</Tooltip> },
              { title: 'Change', dataIndex: 'kind', width: 120, render: (k) => <Tag color={EVENT_KINDS[k]?.color}>{EVENT_KINDS[k]?.label || k}</Tag> },
              {
                title: 'VM', dataIndex: 'vm_name', ellipsis: true,
                render: (v, r) => <Space size={6}><Link to={`/vms/${r.vm_id}`}>{v}</Link>{r.deleted && r.kind !== 'deleted' && <Tag>since deleted</Tag>}</Space>,
              },
              { title: 'OS', dataIndex: 'guest_os', width: 220, ellipsis: true, render: (v) => v || '—' },
              {
                title: 'Created by / owner', dataIndex: 'owner', width: 220, ellipsis: true,
                render: (v) => v || <Tooltip title="vCenter recorded no user for this VM yet; see Owners"><Tag color="warning">Unknown</Tag></Tooltip>,
              },
              { title: 'Agent', dataIndex: 'has_agent', width: 90, render: (a, r) => (r.kind === 'deleted' ? '—' : a ? <Tag color="success">Yes</Tag> : <Tag>No</Tag>) },
            ]} />
        )}
      </Card>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} lg={16}>
          <Card title="VMs created and deleted (30 days)" size="small">
            <Chart option={lifecycleOption} height={240} />
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card title="Operating systems" size="small" style={{ height: '100%' }}>
            <Chart option={osOption} height={120} />
            <List
              size="small"
              header={<Typography.Text type="secondary">Most common guest OS</Typography.Text>}
              dataSource={data.guest_os_top.slice(0, 4)}
              renderItem={([name, n]) => (
                <List.Item extra={<Typography.Text strong>{n}</Typography.Text>}>
                  <Typography.Text ellipsis style={{ maxWidth: 220 }}>{name}</Typography.Text>
                </List.Item>
              )}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} md={12} xl={8}>
          <Card title="Top CPU usage" size="small" extra={<Link to="/vms?sort=cpu_pct&order=desc">All</Link>}>
            <TopList items={data.top_cpu} empty="No powered-on VMs" />
          </Card>
        </Col>
        <Col xs={24} md={12} xl={8}>
          <Card title="Top active memory" size="small" extra={<Link to="/vms?sort=mem_pct&order=desc">All</Link>}>
            <TopList items={data.top_mem} empty="No powered-on VMs" />
          </Card>
        </Col>
        <Col xs={24} xl={8}>
          <Card title="Fullest guest disks" size="small" extra={<Link to="/vms?sort=disk_used_pct_max&order=desc">All</Link>}>
            <TopList items={data.disk_warnings.items} empty={`No disk above ${data.disk_warnings.threshold}%`} />
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} lg={8}>
          <Card title="IP address usage" size="small" extra={<Link to="/network">Details</Link>} style={{ height: '100%' }}>
            {data.subnets.length === 0 && <Typography.Text type="secondary">No subnets configured (VCENTRAL_SUBNETS).</Typography.Text>}
            {data.subnets.map((s) => (
              <div key={s.cidr} style={{ marginBottom: 12 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <Typography.Text strong>{s.cidr}</Typography.Text>
                  <Typography.Text type="secondary">{s.used} used · {s.free} free</Typography.Text>
                </div>
                <Progress percent={s.used_pct} strokeColor={usageColor(s.used_pct)} format={(p) => `${Math.round(p)}%`} />
                {s.conflicts > 0 && <Tag color="error">{s.conflicts} IP conflict(s)</Tag>}
              </div>
            ))}
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              Allocated to powered-on VMs: {a.vcpu_on} vCPU · {fmtGB(a.memory_gb_on)} RAM · {fmtGB(a.storage_gb)} storage (all VMs)
            </Typography.Text>
          </Card>
        </Col>
        <Col xs={24} lg={16}>
          <Card title="Recent activity" size="small" extra={<Link to="/activity">All activity</Link>}>
            <List
              size="small"
              dataSource={data.recent_activity}
              locale={{ emptyText: 'No activity yet' }}
              renderItem={(h) => (
                <List.Item>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', minWidth: 0 }}>
                    <Tag color={EVENT_KINDS[h.kind]?.color} style={{ flex: 'none' }}>{EVENT_KINDS[h.kind]?.label || h.kind}</Tag>
                    <Link to={`/vms/${h.vm_id}`} style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{h.message}</Link>
                    <Typography.Text type="secondary" style={{ flex: 'none' }}>{fmtAgo(h.ts)}</Typography.Text>
                  </div>
                </List.Item>
              )}
            />
          </Card>
        </Col>
      </Row>
    </>
  )
}
