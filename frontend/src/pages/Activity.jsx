import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Alert, Card, Input, Select, Space, Table, Tag, Typography } from 'antd'
import { api } from '../api'
import { EVENT_KINDS, fmtDate } from '../format'

export default function Activity() {
  const [params, setParams] = useSearchParams()
  const [data, setData] = useState({ items: [], total: 0 })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const kind = params.get('kind') || undefined
  const search = params.get('search') || undefined
  const page = Number(params.get('page') || 1)

  useEffect(() => {
    setLoading(true)
    api.get('/api/activity', { kind, search, page, page_size: 50 })
      .then(setData, (e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [kind, search, page])

  const set = (k, v) => {
    const next = new URLSearchParams(params)
    v ? next.set(k, v) : next.delete(k)
    if (k !== 'page') next.delete('page')
    setParams(next, { replace: true })
  }

  return (
    <>
      <Typography.Title level={3} style={{ marginTop: 0 }}>Activity</Typography.Title>
      <Typography.Paragraph type="secondary">Changes vCentral detected in vCenter: VMs created, deleted, powered on/off, renamed, resized, or changing IP.</Typography.Paragraph>
      <Card size="small" style={{ marginBottom: 16 }}>
        <Space wrap>
          <Select allowClear placeholder="Event type" style={{ width: 180 }} value={kind} onChange={(v) => set('kind', v)}
            options={Object.entries(EVENT_KINDS).map(([value, v]) => ({ value, label: v.label }))} />
          <Input.Search allowClear placeholder="VM name" style={{ width: 240 }} defaultValue={search} onSearch={(v) => set('search', v)} />
        </Space>
      </Card>
      {error && <Alert type="error" message={error} showIcon style={{ marginBottom: 16 }} />}
      <Table
        rowKey="id" size="small" loading={loading} dataSource={data.items}
        pagination={{ current: page, pageSize: 50, total: data.total, showSizeChanger: false, onChange: (p) => set('page', p > 1 ? p : undefined) }}
        columns={[
          { title: 'When', dataIndex: 'ts', width: 170, render: fmtDate },
          { title: 'Event', dataIndex: 'kind', width: 140, render: (k) => <Tag color={EVENT_KINDS[k]?.color}>{EVENT_KINDS[k]?.label || k}</Tag> },
          { title: 'VM', dataIndex: 'vm_name', width: 220, render: (v, r) => <Link to={`/vms/${r.vm_id}`}>{v}</Link> },
          { title: 'Details', dataIndex: 'message' },
        ]}
      />
    </>
  )
}
