import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Alert, App, Button, Card, Progress, Table, Tag, Tooltip } from 'antd'
import { PlayCircleOutlined } from '@ant-design/icons'
import { api } from '../api'
import { useAuth } from '../auth'
import NewTaskModal from '../components/NewTaskModal'
import PageHeader, { HelpLabel } from '../components/PageHeader'
import { describeTargets } from '../components/TargetPicker'
import { fmtAgo, fmtDate, STATUS, TASK_STATE } from '../format'

const TASK_STATE_TIP = {
  running: 'Some VMs are still waiting for, or running, the script.',
  succeeded: 'Every VM that ran the script finished with exit code 0.',
  partial: 'Some VMs succeeded and some failed, timed out, or never ran it. Use Re-run failed.',
  failed: 'No VM succeeded.',
  cancelled: 'Cancelled before it finished.',
  skipped: 'No VM ran it: no agent on the chosen VMs, or no script for their OS.',
}

export function TaskStateTag({ state }) {
  const s = TASK_STATE[state] || { label: state, color: 'default' }
  return <Tooltip title={TASK_STATE_TIP[state]}><Tag color={s.color}>{s.label}</Tag></Tooltip>
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
      <PageHeader title="Tasks"
        description="Scripts run by the vCentral agent on your VMs (bash on Linux as root, PowerShell on Windows as SYSTEM). Agents pick a task up with their next report and run it only if it is signed by this vCentral."
        extra={can('admin', 'operator') && (
          <Tooltip title={status && !status.enabled ? status.reason : 'Run a library script (or, for admins, a script you write now) on one or more VMs with an agent.'}>
            <Button type="primary" icon={<PlayCircleOutlined />} disabled={status && !status.enabled} onClick={() => setOpen(true)}>Run a task</Button>
          </Tooltip>
        )} />
      {status && !status.enabled && <Alert type="warning" showIcon style={{ marginBottom: 16 }} message="Tasks are disabled" description={status.reason} />}
      <Card size="small">
        <Table rowKey="id" size="small" dataSource={data.items} scroll={{ x: 900 }}
          pagination={{ current: page, pageSize: 25, total: data.total, showSizeChanger: false, onChange: setPage, hideOnSinglePage: true }}
          locale={{ emptyText: can('admin', 'operator') ? 'No tasks yet. Use “Run a task”, or Run on a script in the library.' : 'No tasks yet.' }}
          columns={[
            { title: 'Task', dataIndex: 'name', ellipsis: true, render: (v, t) => <Link to={`/tasks/${t.id}`}>{v}</Link> },
            { title: 'State', dataIndex: 'state', width: 130, render: (s) => <TaskStateTag state={s} /> },
            { title: <HelpLabel tip="Succeeded VMs of those the task runs on (skipped VMs not counted). Hover a bar for the breakdown.">Progress</HelpLabel>, key: 'p', width: 190, render: (_, t) => <RunBar counts={t.counts} total={t.total} /> },
            { title: 'Targets', dataIndex: 'targets', width: 220, ellipsis: true, render: describeTargets },
            { title: 'By', dataIndex: 'created_by', width: 160, ellipsis: true },
            { title: 'When', dataIndex: 'created_at', width: 140, render: (v) => <Tooltip title={fmtDate(v)}>{fmtAgo(v)}</Tooltip> },
          ]} />
      </Card>
      {open && <NewTaskModal open onClose={() => { setOpen(false); load() }} />}
    </>
  )
}
