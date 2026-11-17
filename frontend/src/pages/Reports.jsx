import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Alert, App, Button, Card, Col, Empty, Input, Row, Segmented, Skeleton, Space, Statistic, Table, Tag, Tooltip, Typography } from 'antd'
import {
  AlertOutlined, ApartmentOutlined, ArrowLeftOutlined, BarChartOutlined, CloudServerOutlined, DatabaseOutlined,
  DownloadOutlined, IdcardOutlined, PlayCircleOutlined, PoweroffOutlined, PrinterOutlined, SwapOutlined, ToolOutlined,
} from '@ant-design/icons'
import { api } from '../api'
import { fmtDate } from '../format'
import './reports.css'

const ICONS = {
  inventory: <DatabaseOutlined />, owners: <IdcardOutlined />, problems: <AlertOutlined />, agents: <CloudServerOutlined />,
  changes: <SwapOutlined />, usage: <BarChartOutlined />, off: <PoweroffOutlined />, ip: <ApartmentOutlined />, tasks: <PlayCircleOutlined />, tools: <ToolOutlined />,
}
const FLAG_COLOR = { idle: 'blue', 'CPU busy': 'red', 'memory high': 'orange', 'disk nearly full': 'volcano' }
const PROBLEM_COLOR = { 'not installed': 'red', 'installed, not running': 'orange', outdated: 'gold' }

function cell(col, value, row) {
  if (value === null || value === undefined || value === '') return <Typography.Text type="secondary">—</Typography.Text>
  switch (col.type) {
    case 'vm': return row.vm_id ? <Link to={`/vms/${row.vm_id}`}>{value}</Link> : value
    case 'pct': return `${Math.round(value * 10) / 10}%`
    case 'date': return <Tooltip title={fmtDate(value)}>{fmtDate(value)}</Tooltip>
    case 'tags': return value.length ? value.slice(0, 8).map((t) => <Tag key={t} color={FLAG_COLOR[t]} style={{ marginBottom: 2 }}>{t}</Tag>).concat(value.length > 8 ? [<Tag key="more">+{value.length - 8} more</Tag>] : []) : <Typography.Text type="secondary">—</Typography.Text>
    default: return PROBLEM_COLOR[value] ? <Tag color={PROBLEM_COLOR[value]}>{value}</Tag> : String(value)
  }
}

function sorter(col) {
  if (col.type === 'tags') return (a, b) => (a[col.key]?.length || 0) - (b[col.key]?.length || 0)
  return (a, b) => {
    const x = a[col.key], y = b[col.key]
    if (x === y) return 0
    if (x === null || x === undefined || x === '') return 1
    if (y === null || y === undefined || y === '') return -1
    return typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y), undefined, { numeric: true })
  }
}

function ReportView({ meta, periods, days, setDays, onBack }) {
  const { message } = App.useApp()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [search, setSearch] = useState('')
  const [printing, setPrinting] = useState(false)

  useEffect(() => {
    setData(null)
    setError(null)
    api.get(`/api/reports/${meta.key}`, { days }).then(setData, (e) => setError(e.message))
  }, [meta.key, days])

  useEffect(() => {
    if (!printing) return
    const done = () => setPrinting(false)
    window.addEventListener('afterprint', done)
    const t = setTimeout(() => window.print(), 300)
    return () => { clearTimeout(t); window.removeEventListener('afterprint', done) }
  }, [printing])

  const rows = useMemo(() => {
    if (!data) return []
    const q = search.trim().toLowerCase()
    if (!q) return data.rows
    return data.rows.filter((r) => data.columns.some((c) => String(Array.isArray(r[c.key]) ? r[c.key].join(' ') : r[c.key] ?? '').toLowerCase().includes(q)))
  }, [data, search])

  const download = () => {
    window.location.href = api.url(`/api/reports/${meta.key}/export.csv`, { days })
    message.success('Downloading CSV')
  }

  return (
    <div className="report-view">
      <Space className="no-print" style={{ marginBottom: 12 }}>
        <Button type="link" icon={<ArrowLeftOutlined />} onClick={onBack} style={{ paddingInline: 0 }}>All reports</Button>
      </Space>
      <Space style={{ width: '100%', justifyContent: 'space-between', alignItems: 'flex-start' }} wrap>
        <div>
          <Typography.Title level={3} style={{ margin: 0 }}>{meta.title}</Typography.Title>
          <Typography.Text type="secondary">
            {meta.description}{data && <> Generated {fmtDate(data.generated_at)}{meta.periodic ? `, last ${days} days` : ''}.</>}
          </Typography.Text>
        </div>
        <Space wrap className="no-print">
          {meta.periodic && <Segmented value={days} onChange={setDays} options={periods.map((p) => ({ value: p, label: `${p} days` }))} />}
          <Button icon={<PrinterOutlined />} disabled={!data} onClick={() => setPrinting(true)}>Print / PDF</Button>
          <Button type="primary" icon={<DownloadOutlined />} disabled={!data} onClick={download}>Download CSV</Button>
        </Space>
      </Space>
      {error && <Alert type="error" message={error} showIcon style={{ marginTop: 16 }} />}
      {!data && !error && <Skeleton active style={{ marginTop: 16 }} />}
      {data && (
        <>
          <Row gutter={[12, 12]} style={{ margin: '16px 0' }}>
            {data.summary.map((s) => (
              <Col key={s.label} xs={12} sm={8} lg={Math.max(4, Math.floor(24 / Math.min(data.summary.length, 6)))}>
                <Card size="small" className="report-stat"><Statistic title={s.label} value={s.value} /></Card>
              </Col>
            ))}
          </Row>
          {data.note && <Alert type="info" showIcon message={data.note} style={{ marginBottom: 12 }} />}
          <Card size="small"
            title={<Typography.Text type="secondary">{rows.length === data.rows.length ? `${data.rows.length} rows` : `${rows.length} of ${data.rows.length} rows`}</Typography.Text>}
            extra={<Input.Search className="no-print" allowClear placeholder="Filter rows" style={{ width: 240 }} value={search} onChange={(e) => setSearch(e.target.value)} />}>
            <Table size="small" rowKey={(r, i) => `${r.vm_id ?? r.ip ?? r.task_id ?? r.owner ?? ''}-${i}`} dataSource={rows} scroll={{ x: 'max-content' }}
              pagination={printing ? false : { pageSize: 50, hideOnSinglePage: true, showSizeChanger: true, pageSizeOptions: [50, 100, 500] }}
              locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Nothing to report" /> }}
              columns={data.columns.map((c) => ({
                title: c.title, dataIndex: c.key, key: c.key, sorter: sorter(c),
                align: ['number', 'pct'].includes(c.type) ? 'right' : undefined,
                render: (v, r) => cell(c, v, r),
              }))} />
          </Card>
        </>
      )}
    </div>
  )
}

export default function Reports() {
  const [params, setParams] = useSearchParams()
  const [list, setList] = useState(null)
  const [error, setError] = useState(null)
  useEffect(() => { api.get('/api/reports').then(setList, (e) => setError(e.message)) }, [])

  if (error) return <Alert type="error" message={error} showIcon />
  if (!list) return <Skeleton active />
  const key = params.get('r')
  const days = Number(params.get('days')) || 30
  const meta = list.reports.find((r) => r.key === key)
  const set = (changes) => {
    const next = new URLSearchParams(params)
    Object.entries(changes).forEach(([k, v]) => (v ? next.set(k, v) : next.delete(k)))
    setParams(next)
  }

  if (meta) return <ReportView meta={meta} periods={list.periods} days={days} setDays={(d) => set({ days: d })} onBack={() => set({ r: null, days: null })} />

  return (
    <>
      <Typography.Title level={3} style={{ marginTop: 0 }}>Reports</Typography.Title>
      <Typography.Paragraph type="secondary">
        Open a report to see it here, filter and sort it, then download it as CSV (Excel) or print it / save it as PDF.
        Reports are built from live data each time you open them.
      </Typography.Paragraph>
      <Row gutter={[16, 16]}>
        {list.reports.map((r) => (
          <Col key={r.key} xs={24} sm={12} xl={8}>
            <Card hoverable className="report-card" onClick={() => set({ r: r.key })}>
              <Space align="start" size={14}>
                <span className="report-icon">{ICONS[r.icon]}</span>
                <div>
                  <Typography.Text strong style={{ fontSize: 15 }}>{r.title}</Typography.Text>
                  {r.periodic && <Tag style={{ marginLeft: 8 }}>7 / 30 / 90 days</Tag>}
                  <Typography.Paragraph type="secondary" style={{ margin: '4px 0 0' }}>{r.description}</Typography.Paragraph>
                </div>
              </Space>
            </Card>
          </Col>
        ))}
      </Row>
    </>
  )
}
