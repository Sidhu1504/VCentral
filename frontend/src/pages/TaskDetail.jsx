import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Alert, App, Breadcrumb, Button, Card, Collapse, Descriptions, Drawer, Popconfirm, Segmented, Skeleton, Space, Table, Tag, Tooltip, Typography } from 'antd'
import { DownloadOutlined } from '@ant-design/icons'
import { api } from '../api'
import { useAuth } from '../auth'
import { codeStyle } from '../components/NewTaskModal'
import { HelpLabel } from '../components/PageHeader'
import { describeTargets } from '../components/TargetPicker'
import { fmtDate, fmtDuration, TASK_STATUS } from '../format'
import { OsIcon } from './VMs'
import { RunBar, TaskStateTag } from './Tasks'

const RUN_STATUS_TIP = {
  pending: 'The agent picks the task up with its next report.',
  sent: 'Handed to the agent; it has not started yet.',
  running: 'The script is running on the VM.',
  succeeded: 'Finished with exit code 0.',
  failed: 'Finished with a non-zero exit code, or could not start. See the output.',
  timeout: 'Stopped because it ran longer than the task timeout.',
  cancelled: 'Cancelled by a user.',
  skipped: 'Not run on this VM: no agent, or no script for its OS. See the note.',
  expired: 'The agent did not pick the task up in time, probably because it was offline.',
  lost: 'No result came back: the agent may have restarted or lost its connection.',
}

export function RunStatusTag({ status }) {
  const s = TASK_STATUS[status] || { label: status, color: 'default' }
  return <Tooltip title={RUN_STATUS_TIP[status]}><Tag color={s.color}>{s.label}</Tag></Tooltip>
}

const ACTIVE = ['pending', 'sent', 'running']

export function OutputDrawer({ runId, onClose }) {
  const [run, setRun] = useState(null)
  const pre = useRef(null)
  useEffect(() => {
    let timer
    const load = () => api.get(`/api/task-runs/${runId}`).then((r) => {
      setRun(r)
      if (ACTIVE.includes(r.status)) timer = setTimeout(load, 2000)
    }).catch(() => {})
    load()
    return () => clearTimeout(timer)
  }, [runId])
  useEffect(() => { if (pre.current) pre.current.scrollTop = pre.current.scrollHeight }, [run?.output])
  return (
    <Drawer open width={Math.min(900, window.innerWidth)} onClose={onClose}
      title={run ? <Space>{run.vm_name}<RunStatusTag status={run.status} />{run.exit_code !== null && run.exit_code !== undefined && <Tag>exit {run.exit_code}</Tag>}</Space> : 'Output'}>
      {!run ? <Skeleton active /> : (
        <>
          {run.error && <Alert type={run.status === 'skipped' ? 'info' : 'error'} showIcon message={run.error} style={{ marginBottom: 12 }} />}
          <Typography.Paragraph type="secondary">
            Started {fmtDate(run.started_at)} · ran {fmtDuration(run.started_at, run.finished_at)}{run.output_truncated && ' · output was cut at 1 MB'}
          </Typography.Paragraph>
          <pre ref={pre} style={{ ...codeStyle, background: 'rgba(127,127,127,0.12)', padding: 12, borderRadius: 6, maxHeight: '70vh', overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
            {run.output || (ACTIVE.includes(run.status) ? 'Waiting for output…' : 'No output.')}
          </pre>
        </>
      )}
    </Drawer>
  )
}

export default function TaskDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { can } = useAuth()
  const { message } = App.useApp()
  const [task, setTask] = useState(null)
  const [filter, setFilter] = useState('all')
  const [runId, setRunId] = useState(null)
  const load = useCallback(() => api.get(`/api/tasks/${id}`).then(setTask, (e) => message.error(e.message)), [id, message])
  useEffect(() => {
    load()
    const t = setInterval(() => load(), 3000)
    return () => clearInterval(t)
  }, [load])

  if (!task) return <Skeleton active />
  const act = async (fn, ok) => {
    try {
      const r = await fn()
      message.success(ok)
      if (r?.id) navigate(`/tasks/${r.id}`)
      else load()
    } catch (e) {
      message.error(e.message)
    }
  }
  const failed = task.counts.failed + task.counts.timeout + task.counts.lost + task.counts.expired + task.counts.cancelled
  const canRerun = can('admin') || (can('operator') && task.script_id)
  const runs = task.runs.filter((r) => filter === 'all' || (filter === 'problems' ? ['failed', 'timeout', 'lost', 'expired'].includes(r.status) : ACTIVE.includes(r.status)))
  return (
    <>
      <Breadcrumb items={[{ title: <Link to="/tasks">Tasks</Link> }, { title: task.name }]} style={{ marginBottom: 8 }} />
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 16 }} wrap>
        <Space wrap>
          <Typography.Title level={3} style={{ margin: 0 }}>{task.name}</Typography.Title>
          <TaskStateTag state={task.state} />
        </Space>
        {can('admin', 'operator') && (
          <Space wrap>
            {task.state === 'running' && (
              <Popconfirm title="Cancel this task?" description="Waiting runs are dropped; running scripts are stopped." okText="Cancel task" okButtonProps={{ danger: true }}
                onConfirm={() => act(() => api.post(`/api/tasks/${task.id}/cancel`), 'Cancelling')}>
                <Tooltip title="Stops this task: VMs still waiting will not run it, and scripts already running are stopped by their agent at its next report.">
                  <Button danger>Cancel</Button>
                </Tooltip>
              </Popconfirm>
            )}
            {task.kind === 'script' && canRerun && task.state !== 'running' && (
              <>
                {failed > 0 && (
                  <Tooltip title="Starts a new task with the same script and values, only on the VMs that failed, timed out, expired, lost contact or were cancelled.">
                    <Button onClick={() => act(() => api.post(`/api/tasks/${task.id}/rerun`, { only_failed: true }), 'Re-running on failed VMs')}>Re-run failed ({failed})</Button>
                  </Tooltip>
                )}
                <Tooltip title="Starts a new task with the same script and values on every VM of this task.">
                  <Button onClick={() => act(() => api.post(`/api/tasks/${task.id}/rerun`, { only_failed: false }), 'Re-running')}>Re-run all</Button>
                </Tooltip>
              </>
            )}
            {task.kind === 'script' && (
              <Tooltip title="Downloads one row per VM (status, exit code, times, note and full output) as a CSV file for Excel.">
                <Button icon={<DownloadOutlined />} href={api.url(`/api/tasks/${task.id}/results.csv`)}>Results (CSV)</Button>
              </Tooltip>
            )}
          </Space>
        )}
      </Space>
      <Card size="small" style={{ marginBottom: 16 }}>
        <Descriptions size="small" column={{ xs: 1, md: 3 }}>
          <Descriptions.Item label="Progress"><RunBar counts={task.counts} total={task.total} /></Descriptions.Item>
          <Descriptions.Item label="Targets">{describeTargets(task.targets)}</Descriptions.Item>
          <Descriptions.Item label="Started by">{task.created_by}, {fmtDate(task.created_at)}</Descriptions.Item>
          <Descriptions.Item label={<HelpLabel tip="A script still running after this long is stopped and marked Timed out.">Timeout</HelpLabel>}>{task.timeout_seconds}s</Descriptions.Item>
          <Descriptions.Item label={<HelpLabel tip="VMs whose agent has not picked the task up by then are marked Expired and do not run it.">Waiting runs expire</HelpLabel>}>{fmtDate(task.expires_at)}</Descriptions.Item>
          <Descriptions.Item label="Kind">{task.kind === 'update' ? 'Agent update' : task.script_id ? 'Library script' : 'Ad-hoc script'}</Descriptions.Item>
        </Descriptions>
        {(task.linux_script || task.windows_script) && (
          <Collapse ghost size="small" items={[{
            key: 's', label: 'Show script',
            children: (
              <>
                {task.linux_script && <><Typography.Text strong>Linux (bash)</Typography.Text><pre style={{ ...codeStyle, whiteSpace: 'pre-wrap' }}>{task.linux_script}</pre></>}
                {task.windows_script && <><Typography.Text strong>Windows (PowerShell)</Typography.Text><pre style={{ ...codeStyle, whiteSpace: 'pre-wrap' }}>{task.windows_script}</pre></>}
              </>
            ),
          }]} />
        )}
      </Card>
      <Card size="small" title="VMs" extra={<Segmented size="small" value={filter} onChange={setFilter}
        options={[{ value: 'all', label: `All (${task.total})` }, { value: 'problems', label: 'Problems' }, { value: 'active', label: 'Not finished' }]} />}>
        <Table rowKey="id" size="small" dataSource={runs} pagination={{ pageSize: 50, hideOnSinglePage: true }} scroll={{ x: 800 }}
          onRow={(r) => (can('admin', 'operator') && !['pending', 'skipped'].includes(r.status) ? { onClick: () => setRunId(r.id), style: { cursor: 'pointer' } } : {})}
          columns={[
            { title: 'VM', dataIndex: 'vm_name', ellipsis: true, render: (v, r) => <Space size={6}><OsIcon family={r.os_family} />{r.vm_id ? <Link to={`/vms/${r.vm_id}`} onClick={(e) => e.stopPropagation()}>{v}</Link> : v}</Space> },
            { title: 'Status', dataIndex: 'status', width: 150, render: (s, r) => <Space size={4}><RunStatusTag status={s} />{r.cancel_requested && ACTIVE.includes(s) && <Tag>cancelling</Tag>}</Space> },
            { title: 'Exit code', dataIndex: 'exit_code', width: 100, render: (v) => (v === null || v === undefined ? '—' : v) },
            { title: 'Duration', key: 'd', width: 110, render: (_, r) => (r.started_at ? fmtDuration(r.started_at, r.finished_at) : '—') },
            { title: 'Output', dataIndex: 'output_size', width: 110, render: (n) => (n ? `${n < 1024 ? n + ' B' : Math.round(n / 1024) + ' KB'}` : '—') },
            { title: 'Note', dataIndex: 'error', ellipsis: true },
          ]} />
        {can('admin', 'operator') && <Typography.Text type="secondary" style={{ fontSize: 12 }}>Click a VM to see its output.</Typography.Text>}
      </Card>
      {runId && <OutputDrawer runId={runId} onClose={() => setRunId(null)} />}
    </>
  )
}
