import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { App, Button, Card, Form, Input, Modal, Popconfirm, Select, Space, Switch, Table, Tooltip, Typography } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { api } from '../api'
import { useAuth } from '../auth'
import { describeTargets, TargetPicker } from '../components/TargetPicker'
import { fmtAgo, fmtDate } from '../format'

const PRESETS = [
  { value: '0 * * * *', label: 'Every hour' },
  { value: '30 20 * * *', label: 'Every day at 02:00 IST (20:30 UTC)' },
  { value: '30 21 * * 6', label: 'Every Sunday at 03:00 IST (Sat 21:30 UTC)' },
  { value: '*/15 * * * *', label: 'Every 15 minutes' },
]

function toTargetsState(t) {
  if (!t) return { os_family: null, critical: false, search: '', only_online: false }
  return t.vm_ids ? { vm_ids: t.vm_ids } : { ...t.filter }
}

function ScheduleEditor({ schedule, scripts, onClose, onSaved }) {
  const [form] = Form.useForm()
  const { message } = App.useApp()
  const [targets, setTargets] = useState(toTargetsState(schedule?.targets))
  const save = async () => {
    const v = await form.validateFields()
    try {
      const body = { ...v, targets, enabled: schedule?.enabled ?? true }
      if (schedule?.id) await api.put(`/api/schedules/${schedule.id}`, body)
      else await api.post('/api/schedules', body)
      message.success('Schedule saved')
      onSaved()
      onClose()
    } catch (e) {
      message.error(e.message)
    }
  }
  return (
    <Modal open width={760} title={schedule?.id ? `Edit ${schedule.name}` : 'New schedule'} onCancel={onClose} onOk={save} okText="Save" destroyOnClose>
      <Form form={form} layout="vertical" preserve={false} initialValues={schedule || { cron: '30 20 * * *' }}>
        <Form.Item name="name" label="Name" rules={[{ required: true, max: 128 }]}><Input placeholder="e.g. Nightly temp cleanup" /></Form.Item>
        <Form.Item name="script_id" label="Script" rules={[{ required: true }]}>
          <Select showSearch optionFilterProp="label" options={scripts.map((s) => ({ value: s.id, label: s.name }))} placeholder="Choose a library script" />
        </Form.Item>
        <Form.Item label="When" required>
          <Space.Compact style={{ width: '100%' }}>
            <Select style={{ width: '55%' }} placeholder="Presets" options={PRESETS} onChange={(v) => form.setFieldsValue({ cron: v })} />
            <Form.Item name="cron" noStyle rules={[{ required: true }]}><Input style={{ width: '45%' }} placeholder="cron: min hour day month weekday" /></Form.Item>
          </Space.Compact>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>Cron in UTC (India is UTC+5:30). The next run is shown in your local time after saving.</Typography.Text>
        </Form.Item>
        <Form.Item label="Run on" extra="Filters are applied each time the schedule runs, so new matching VMs are included automatically.">
          <TargetPicker value={targets} onChange={setTargets} />
        </Form.Item>
      </Form>
    </Modal>
  )
}

export default function Schedules() {
  const { can } = useAuth()
  const { message } = App.useApp()
  const [rows, setRows] = useState([])
  const [scripts, setScripts] = useState([])
  const [editing, setEditing] = useState(null)
  const load = useCallback(() => api.get('/api/schedules').then(setRows, (e) => message.error(e.message)), [message])
  useEffect(() => { load(); api.get('/api/scripts').then(setScripts).catch(() => {}) }, [load])

  const act = async (fn, ok) => {
    try {
      await fn()
      message.success(ok)
      load()
    } catch (e) {
      message.error(e.message)
    }
  }
  const toggle = (s, enabled) => act(() => api.put(`/api/schedules/${s.id}`, { name: s.name, cron: s.cron, script_id: s.script_id,
    targets: toTargetsState(s.targets), enabled }), enabled ? 'Schedule enabled' : 'Schedule paused')

  return (
    <>
      <Space style={{ width: '100%', justifyContent: 'space-between' }} wrap>
        <Typography.Title level={3} style={{ marginTop: 0 }}>Schedules</Typography.Title>
        {can('admin') && <Button type="primary" icon={<PlusOutlined />} disabled={!scripts.length} onClick={() => setEditing({})}>New schedule</Button>}
      </Space>
      <Typography.Paragraph type="secondary">Run library scripts automatically. Each run appears under Tasks.</Typography.Paragraph>
      <Card size="small">
        <Table rowKey="id" size="small" dataSource={rows} pagination={false} scroll={{ x: 900 }}
          locale={{ emptyText: scripts.length ? 'No schedules yet' : 'Add a script to the library first' }}
          columns={[
            { title: 'On', dataIndex: 'enabled', width: 70, render: (v, s) => <Switch size="small" checked={v} disabled={!can('admin')} onChange={(c) => toggle(s, c)} /> },
            { title: 'Name', dataIndex: 'name', width: 200 },
            { title: 'Script', dataIndex: 'script_name', width: 180 },
            { title: 'When (UTC)', dataIndex: 'cron', width: 130, render: (c) => <code>{c}</code> },
            { title: 'Run on', dataIndex: 'targets', ellipsis: true, render: describeTargets },
            { title: 'Next run', dataIndex: 'next_run_at', width: 160, render: (v, s) => (s.enabled ? fmtDate(v) : 'Paused') },
            { title: 'Last run', dataIndex: 'last_run_at', width: 130, render: (v, s) => (v ? <Tooltip title={fmtDate(v)}>{s.last_task_id ? <Link to={`/tasks/${s.last_task_id}`}>{fmtAgo(v)}</Link> : fmtAgo(v)}</Tooltip> : '—') },
            {
              title: '', key: 'a', width: 230,
              render: (_, s) => (
                <Space>
                  {can('admin', 'operator') && <Button size="small" onClick={() => act(() => api.post(`/api/schedules/${s.id}/run`), 'Started')}>Run now</Button>}
                  {can('admin') && <Button size="small" onClick={() => setEditing(s)}>Edit</Button>}
                  {can('admin') && (
                    <Popconfirm title={`Delete ${s.name}?`} okText="Delete" okButtonProps={{ danger: true }} onConfirm={() => act(() => api.del(`/api/schedules/${s.id}`), 'Deleted')}>
                      <Button size="small" danger>Delete</Button>
                    </Popconfirm>
                  )}
                </Space>
              ),
            },
          ]} />
      </Card>
      {editing && <ScheduleEditor schedule={editing.id ? editing : null} scripts={scripts} onClose={() => setEditing(null)} onSaved={load} />}
    </>
  )
}
