import { useCallback, useEffect, useState } from 'react'
import { App, Button, Card, Form, Input, InputNumber, Modal, Popconfirm, Space, Table, Tag, Typography } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { api } from '../api'
import { useAuth } from '../auth'
import NewTaskModal, { codeStyle } from '../components/NewTaskModal'
import { fmtAgo } from '../format'

function ScriptEditor({ script, onClose, onSaved }) {
  const [form] = Form.useForm()
  const { message } = App.useApp()
  const save = async () => {
    const v = await form.validateFields()
    try {
      if (script?.id) await api.put(`/api/scripts/${script.id}`, v)
      else await api.post('/api/scripts', v)
      message.success('Script saved')
      onSaved()
      onClose()
    } catch (e) {
      message.error(e.message)
    }
  }
  return (
    <Modal open width={860} title={script?.id ? `Edit ${script.name}` : 'New script'} onCancel={onClose} onOk={save} okText="Save" destroyOnClose>
      <Typography.Paragraph type="secondary">
        A script can have a Linux and a Windows version; each VM gets the one for its OS. Operators can run library
        scripts but cannot change them.
      </Typography.Paragraph>
      <Form form={form} layout="vertical" preserve={false}
        initialValues={script || { timeout_seconds: 300 }}>
        <Form.Item name="name" label="Name" rules={[{ required: true, max: 128 }]}><Input placeholder="e.g. Clean temp files" /></Form.Item>
        <Form.Item name="description" label="Description"><Input.TextArea rows={2} maxLength={2000} placeholder="What it does, when to use it" /></Form.Item>
        <Form.Item name="linux_script" label="Linux (bash, runs as root)">
          <Input.TextArea rows={8} style={codeStyle} spellCheck={false} />
        </Form.Item>
        <Form.Item name="windows_script" label="Windows (PowerShell, runs as SYSTEM)">
          <Input.TextArea rows={8} style={codeStyle} spellCheck={false} />
        </Form.Item>
        <Form.Item name="timeout_seconds" label="Stop after" rules={[{ required: true }]}><InputNumber min={10} max={21600} addonAfter="seconds" /></Form.Item>
      </Form>
    </Modal>
  )
}

export default function Scripts() {
  const { can } = useAuth()
  const { message } = App.useApp()
  const [scripts, setScripts] = useState([])
  const [editing, setEditing] = useState(null)
  const [running, setRunning] = useState(null)
  const load = useCallback(() => api.get('/api/scripts').then(setScripts, (e) => message.error(e.message)), [message])
  useEffect(() => { load() }, [load])

  const remove = async (s) => {
    try {
      await api.del(`/api/scripts/${s.id}`)
      message.success('Script deleted')
      load()
    } catch (e) {
      message.error(e.message)
    }
  }
  return (
    <>
      <Space style={{ width: '100%', justifyContent: 'space-between' }} wrap>
        <Typography.Title level={3} style={{ marginTop: 0 }}>Script library</Typography.Title>
        {can('admin') && <Button type="primary" icon={<PlusOutlined />} onClick={() => setEditing({})}>New script</Button>}
      </Space>
      <Card size="small">
        <Table rowKey="id" size="small" dataSource={scripts} pagination={{ pageSize: 50, hideOnSinglePage: true }} scroll={{ x: 800 }}
          locale={{ emptyText: can('admin') ? 'No scripts yet: add the commands your team runs often.' : 'No scripts yet. An admin can add some.' }}
          expandable={{ expandedRowRender: (s) => (
            <Space direction="vertical" style={{ width: '100%' }}>
              {s.linux_script && <><Typography.Text strong>Linux</Typography.Text><pre style={{ ...codeStyle, whiteSpace: 'pre-wrap', margin: 0 }}>{s.linux_script}</pre></>}
              {s.windows_script && <><Typography.Text strong>Windows</Typography.Text><pre style={{ ...codeStyle, whiteSpace: 'pre-wrap', margin: 0 }}>{s.windows_script}</pre></>}
            </Space>
          ) }}
          columns={[
            { title: 'Name', dataIndex: 'name', width: 220 },
            { title: 'Description', dataIndex: 'description', ellipsis: true },
            { title: 'For', key: 'os', width: 150, render: (_, s) => <Space size={4}>{s.linux_script && <Tag>Linux</Tag>}{s.windows_script && <Tag>Windows</Tag>}</Space> },
            { title: 'Timeout', dataIndex: 'timeout_seconds', width: 90, render: (v) => `${v}s` },
            { title: 'Updated', dataIndex: 'updated_at', width: 150, render: (v, s) => `${fmtAgo(v)} by ${s.updated_by || '—'}` },
            {
              title: '', key: 'a', width: 210,
              render: (_, s) => (
                <Space>
                  <Button size="small" type="primary" onClick={() => setRunning(s)}>Run</Button>
                  {can('admin') && <Button size="small" onClick={() => setEditing(s)}>Edit</Button>}
                  {can('admin') && (
                    <Popconfirm title={`Delete ${s.name}?`} description="Its schedules are deleted too." okText="Delete" okButtonProps={{ danger: true }} onConfirm={() => remove(s)}>
                      <Button size="small" danger>Delete</Button>
                    </Popconfirm>
                  )}
                </Space>
              ),
            },
          ]} />
      </Card>
      {editing && <ScriptEditor script={editing.id ? editing : null} onClose={() => setEditing(null)} onSaved={load} />}
      {running && <NewTaskModal open scriptId={running.id} title={`Run ${running.name}`} onClose={() => setRunning(null)} />}
    </>
  )
}
