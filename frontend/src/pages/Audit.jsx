import { useEffect, useState } from 'react'
import { Alert, Card, Input, Space, Table, Tag } from 'antd'
import { api } from '../api'
import { fmtDate } from '../format'
import PageHeader from '../components/PageHeader'

export default function Audit() {
  const [data, setData] = useState({ items: [], total: 0 })
  const [filters, setFilters] = useState({ page: 1 })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    setLoading(true)
    api.get('/api/audit', { ...filters, page_size: 50 })
      .then(setData, (e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [filters])

  return (
    <>
      <PageHeader title="Audit log" description="Every sign-in and every change made through vCentral: who did what, when, and from which address." />
      <Card size="small" style={{ marginBottom: 16 }}>
        <Space wrap>
          <Input.Search allowClear placeholder="Username (exact)" style={{ width: 200 }} onSearch={(v) => setFilters((f) => ({ ...f, username: v || undefined, page: 1 }))} />
          <Input.Search allowClear placeholder="Action, e.g. login" style={{ width: 200 }} onSearch={(v) => setFilters((f) => ({ ...f, action: v || undefined, page: 1 }))} />
        </Space>
      </Card>
      {error && <Alert type="error" message={error} showIcon style={{ marginBottom: 16 }} />}
      <Table
        rowKey="id" size="small" loading={loading} dataSource={data.items} scroll={{ x: 900 }}
        locale={{ emptyText: filters.username || filters.action ? 'No entries match. The username filter needs the exact username.' : 'No entries yet.' }}
        pagination={{ current: filters.page, pageSize: 50, total: data.total, showSizeChanger: false, onChange: (p) => setFilters((f) => ({ ...f, page: p })) }}
        columns={[
          { title: 'When', dataIndex: 'ts', width: 170, render: fmtDate },
          { title: 'User', dataIndex: 'username', width: 140 },
          { title: 'Action', dataIndex: 'action', width: 150 },
          { title: 'Target', dataIndex: 'target', width: 200, ellipsis: true },
          { title: 'Result', dataIndex: 'success', width: 90, render: (v) => (v ? <Tag color="success">OK</Tag> : <Tag color="error">Failed</Tag>) },
          { title: 'Source IP', dataIndex: 'source_ip', width: 130 },
          { title: 'Details', dataIndex: 'detail', render: (d) => (d && Object.keys(d).length ? <code style={{ fontSize: 12 }}>{JSON.stringify(d)}</code> : '') },
        ]}
      />
    </>
  )
}
