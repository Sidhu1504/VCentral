import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Alert, App, Button, Card, Progress, Space, Table, Tag, Tooltip, Typography } from 'antd'
import { PlayCircleOutlined } from '@ant-design/icons'
import { api } from '../api'
import { useAuth } from '../auth'
import NewTaskModal from '../components/NewTaskModal'
import { describeTargets } from '../components/TargetPicker'
import { fmtAgo, fmtDate, STATUS, TASK_STATE } from '../format'

export function TaskStateTag({ state }) {
  const s = TASK_STATE[state] || { label: state, color: 'default' }
  return <Tag color={s.color}>{s.label}</Tag>
}

export function RunBar({ counts, total }) {
  const ok = counts.succeeded || 0
  const bad = (counts.failed || 0) + (counts.timeout || 0) + (counts.lost || 0) + (counts.expired || 0)
  const done = ok + bad + (counts.cancelled || 0) + (counts.skipped || 0)
  return (
    <Tooltip title={`${ok} succeeded, ${bad} failed, ${total - done} still running or waiting, ${counts.skipped || 0} skipped`}>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, whiteSpace: 'nowrap' }}>
        <Progress size={[120, 8]} showInfo={false} percent={total ? (done / total) * 100 : 0}
          success={{ percent: total ? (ok / total) * 100 : 0, strokeColor: STATUS.good }}
          strokeColor={bad ? STATUS.critical : '#a8a8a8'} style={{ margin: 0 }} />
        <span style={{ fontVariantNumeric: 'tabular-nums' }}>
          {ok} / {total - (counts.skipped || 0)}{counts.skipped ? ` (${counts.skipped} skipped)` : ''}
        </span>
      </span>
    </Tooltip>
  )
}

export default function Tasks() {
  const { can } = useAuth()
  const { message } = App.useApp()
  const [data, setData] = useState({ items: [], total: 0 })
  const [status, setStatus] = useState(null)
  const [page, setPage] = useState(1)
  const [open, setOpen] = useState(false)
  const load = useCallback(() => api.get('/api/tasks', { page, page_size: 25 }).then(setData, (e) => message.error(e.message)), [page, message])
  useEffect(() => {
    load()
    api.get('/api/tasks/status').then(setStatus).catch(() => {})
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [load])

  return (
    <>
      <Space style={{ width: '100%', justifyContent: 'space-between' }} wrap>
        <Typography.Title level={3} style={{ marginTop: 0 }}>Tasks</Typography.Title>
        {can('admin', 'operator') && (
          <Button type="primary" icon={<PlayCircleOutlined />} disabled={status && !status.enabled} onClick={() => setOpen(true)}>Run a task</Button>
        )}
      </Space>
      <Typography.Paragraph type="secondary">
        Scripts run by the vCentral agent on your VMs (bash on Linux as root, PowerShell on Windows as SYSTEM). Agents
        pick a task up with their next report and run it only if it is signed by this vCentral.
      </Typography.Paragraph>
      {status && !status.enabled && <Alert type="warning" showIcon style={{ marginBottom: 16 }} message="Tasks are disabled" description={status.reason} />}
      <Card size="small">
        <Table rowKey="id" size="small" dataSource={data.items} scroll={{ x: 900 }}
          pagination={{ current: page, pageSize: 25, total: data.total, showSizeChanger: false, onChange: setPage, hideOnSinglePage: true }}
          locale={{ emptyText: 'No tasks yet' }}
          columns={[
            { title: 'Task', dataIndex: 'name', ellipsis: true, render: (v, t) => <Link to={`/tasks/${t.id}`}>{v}</Link> },
            { title: 'State', dataIndex: 'state', width: 130, render: (s) => <TaskStateTag state={s} /> },
            { title: 'Progress', key: 'p', width: 190, render: (_, t) => <RunBar counts={t.counts} total={t.total} /> },
            { title: 'Targets', dataIndex: 'targets', width: 220, ellipsis: true, render: describeTargets },
            { title: 'By', dataIndex: 'created_by', width: 160, ellipsis: true },
            { title: 'When', dataIndex: 'created_at', width: 140, render: (v) => <Tooltip title={fmtDate(v)}>{fmtAgo(v)}</Tooltip> },
          ]} />
      </Card>
      {open && <NewTaskModal open onClose={() => { setOpen(false); load() }} />}
    </>
  )
}
