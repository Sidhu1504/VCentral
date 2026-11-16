import { useCallback, useEffect, useRef, useState } from 'react'
import { App, Button, Card, Dropdown, Form, Input, InputNumber, List, Modal, Popconfirm, Radio, Space, Table, Tag, Typography } from 'antd'
import { AppstoreAddOutlined, DownloadOutlined, DownOutlined, ImportOutlined, PlusOutlined, UploadOutlined } from '@ant-design/icons'
import { api } from '../api'
import { useAuth } from '../auth'
import NewTaskModal, { codeStyle } from '../components/NewTaskModal'
import { downloadText, findVariables, osForFile, readScriptFile, safeFileName, ScriptBox } from '../components/scriptFiles'
import { fmtAgo } from '../format'

const baseName = (file) => file.replace(/\.[^.]+$/, '').replace(/[-_]+/g, ' ').trim()

function ScriptEditor({ script, onClose, onSaved }) {
  const [form] = Form.useForm()
  const { message } = App.useApp()
  const linux = Form.useWatch('linux_script', form)
  const windows = Form.useWatch('windows_script', form)
  const vars = findVariables(linux, windows)
  const nameFromFile = (file) => { if (!form.getFieldValue('name')) form.setFieldValue('name', baseName(file)) }
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
        scripts but cannot change them. Write <code>{'{{NAME}}'}</code> (no quotes around it) where a value should be
        asked for at run time, e.g. <code>{'systemctl restart {{SERVICE}}'}</code>.
      </Typography.Paragraph>
      <Form form={form} layout="vertical" preserve={false}
        initialValues={script || { timeout_seconds: 300 }}>
        <Form.Item name="name" label="Name" rules={[{ required: true, max: 128 }]}><Input placeholder="e.g. Clean temp files" /></Form.Item>
        <Form.Item name="description" label="Description"><Input.TextArea rows={2} maxLength={2000} placeholder="What it does, when to use it" /></Form.Item>
        <Form.Item name="linux_script" label="Linux (bash, runs as root)">
          <ScriptBox os="linux" style={codeStyle} onFileName={nameFromFile} />
        </Form.Item>
        <Form.Item name="windows_script" label="Windows (PowerShell, runs as SYSTEM)">
          <ScriptBox os="windows" style={codeStyle} onFileName={nameFromFile} />
        </Form.Item>
        {vars.length > 0 && (
          <Typography.Paragraph type="secondary">
            Asks for when run: {vars.map((v) => <Tag key={v} color="blue">{v}</Tag>)}
          </Typography.Paragraph>
        )}
        <Form.Item name="timeout_seconds" label="Stop after" rules={[{ required: true }]}><InputNumber min={10} max={21600} addonAfter="seconds" /></Form.Item>
      </Form>
    </Modal>
  )
}

function StarterModal({ onClose, onDone }) {
  const { message } = App.useApp()
  const [items, setItems] = useState(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { api.get('/api/scripts/starter').then(setItems, (e) => message.error(e.message)) }, [message])
  const add = async () => {
    setBusy(true)
    try {
      const r = await api.post('/api/scripts/starter')
      message.success(r.added.length ? `Added ${r.added.length} script${r.added.length === 1 ? '' : 's'}` : 'All starter scripts are already in the library')
      onDone()
      onClose()
    } catch (e) {
      message.error(e.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal open width={720} title="Starter scripts" onCancel={onClose} onOk={add} okText="Add to the library" confirmLoading={busy}>
      <Typography.Paragraph type="secondary">
        Ready-made scripts, each with a Linux and a Windows version. All only read information except
        <b> Restart a service</b>. Scripts already in your library (same name) are left as they are. You can edit any of
        them afterwards.
      </Typography.Paragraph>
      <List size="small" loading={!items} dataSource={items || []} renderItem={(s) => (
        <List.Item>
          <List.Item.Meta title={<Space size={6}>{s.name}{s.variables.map((v) => <Tag key={v} color="blue">{v}</Tag>)}{/CHANGES THE VM/.test(s.description) && <Tag color="orange">changes the VM</Tag>}</Space>}
            description={s.description.replace(/^Starter script\. /, '')} />
        </List.Item>
      )} />
    </Modal>
  )
}

function ImportModal({ data, onClose, onDone }) {
  const { message } = App.useApp()
  const [replace, setReplace] = useState(false)
  const [busy, setBusy] = useState(false)
  const run = async () => {
    setBusy(true)
    try {
      const r = await api.post('/api/scripts/import', { scripts: data.scripts, replace })
      message.success(`Added ${r.added.length}, updated ${r.updated.length}, skipped ${r.skipped.length}`)
      onDone()
      onClose()
    } catch (e) {
      message.error(e.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal open title={`Import ${data.scripts.length} script${data.scripts.length === 1 ? '' : 's'}`} onCancel={onClose} onOk={run} okText="Import" confirmLoading={busy}>
      <Typography.Paragraph type="secondary">From {data.file}. Scripts whose name is already in the library:</Typography.Paragraph>
      <Radio.Group value={replace} onChange={(e) => setReplace(e.target.value)} style={{ display: 'grid', gap: 8 }}
        options={[{ value: false, label: 'Keep mine (skip them)' }, { value: true, label: 'Replace mine with the imported version' }]} />
    </Modal>
  )
}

export default function Scripts() {
  const { can } = useAuth()
  const { message } = App.useApp()
  const [scripts, setScripts] = useState([])
  const [editing, setEditing] = useState(null)
  const [running, setRunning] = useState(null)
  const [starter, setStarter] = useState(false)
  const [importing, setImporting] = useState(null)
  const uploadInput = useRef(null)
  const importInput = useRef(null)
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
  const uploadNew = async (file) => {
    if (!file) return
    const os = osForFile(file.name)
    if (!os) return message.error('Choose a .sh, .bash or .py file (Linux) or a .ps1, .bat or .cmd file (Windows)')
    try {
      const text = await readScriptFile(file, os)
      setEditing({ name: baseName(file.name), timeout_seconds: 300, [`${os}_script`]: text, fromFile: true })
    } catch (e) {
      message.error(e.message)
    }
  }
  const exportAll = () => {
    const out = scripts.map(({ name, description, linux_script, windows_script, timeout_seconds }) => ({ name, description, linux_script, windows_script, timeout_seconds }))
    downloadText(`vcentral-scripts-${new Date().toISOString().slice(0, 10)}.json`,
      JSON.stringify({ vcentral_scripts: 1, exported_at: new Date().toISOString(), scripts: out }, null, 2), 'application/json')
  }
  const importFile = async (file) => {
    if (!file) return
    try {
      const parsed = JSON.parse(await file.text())
      const list = Array.isArray(parsed) ? parsed : parsed.scripts
      if (!Array.isArray(list) || !list.length) throw new Error('No scripts found in this file')
      setImporting({ file: file.name, scripts: list })
    } catch (e) {
      message.error(e instanceof SyntaxError ? `${file.name} is not a vCentral script export (JSON)` : e.message)
    }
  }
  const downloadItems = (s) => [
    s.linux_script && { key: 'l', label: 'Linux version (.sh)', onClick: () => downloadText(`${safeFileName(s.name)}.sh`, s.linux_script) },
    s.windows_script && { key: 'w', label: 'Windows version (.ps1)', onClick: () => downloadText(`${safeFileName(s.name)}.ps1`, s.windows_script.replace(/\r?\n/g, '\r\n')) },
  ].filter(Boolean)

  return (
    <>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 12 }} wrap>
        <Typography.Title level={3} style={{ margin: 0 }}>Script library</Typography.Title>
        <Space wrap>
          {can('admin') && <Button icon={<AppstoreAddOutlined />} onClick={() => setStarter(true)}>Starter scripts</Button>}
          {can('admin') && <Button icon={<ImportOutlined />} onClick={() => importInput.current?.click()}>Import</Button>}
          <Button icon={<DownloadOutlined />} disabled={!scripts.length} onClick={exportAll}>Export</Button>
          {can('admin') && <Button icon={<UploadOutlined />} onClick={() => uploadInput.current?.click()}>Upload script</Button>}
          {can('admin') && <Button type="primary" icon={<PlusOutlined />} onClick={() => setEditing({})}>New script</Button>}
        </Space>
      </Space>
      <input ref={uploadInput} type="file" style={{ display: 'none' }} accept=".sh,.bash,.py,.ps1,.bat,.cmd" onChange={(e) => { uploadNew(e.target.files?.[0]); e.target.value = '' }} />
      <input ref={importInput} type="file" style={{ display: 'none' }} accept=".json,application/json" onChange={(e) => { importFile(e.target.files?.[0]); e.target.value = '' }} />
      <Card size="small">
        <Table rowKey="id" size="small" dataSource={scripts} pagination={{ pageSize: 50, hideOnSinglePage: true }} scroll={{ x: 900 }}
          locale={{ emptyText: can('admin') ? 'No scripts yet. Add the starter scripts, upload a file, or write one.' : 'No scripts yet. An admin can add some.' }}
          expandable={{ expandedRowRender: (s) => (
            <Space direction="vertical" style={{ width: '100%' }}>
              {s.linux_script && <><Typography.Text strong>Linux</Typography.Text><pre style={{ ...codeStyle, whiteSpace: 'pre-wrap', margin: 0 }}>{s.linux_script}</pre></>}
              {s.windows_script && <><Typography.Text strong>Windows</Typography.Text><pre style={{ ...codeStyle, whiteSpace: 'pre-wrap', margin: 0 }}>{s.windows_script}</pre></>}
            </Space>
          ) }}
          columns={[
            { title: 'Name', dataIndex: 'name', width: 220 },
            { title: 'Description', dataIndex: 'description', ellipsis: true, render: (d) => d?.replace(/^Starter script\. /, '') },
            { title: 'For', key: 'os', width: 140, render: (_, s) => <Space size={4}>{s.linux_script && <Tag>Linux</Tag>}{s.windows_script && <Tag>Windows</Tag>}</Space> },
            { title: 'Asks for', dataIndex: 'variables', width: 130, render: (v) => (v?.length ? v.map((n) => <Tag key={n} color="blue">{n}</Tag>) : '—') },
            { title: 'Timeout', dataIndex: 'timeout_seconds', width: 80, render: (v) => `${v}s` },
            { title: 'Updated', dataIndex: 'updated_at', width: 150, render: (v, s) => `${fmtAgo(v)} by ${s.updated_by || '—'}` },
            {
              title: '', key: 'a', width: 300,
              render: (_, s) => (
                <Space>
                  <Button size="small" type="primary" onClick={() => setRunning(s)}>Run</Button>
                  <Dropdown menu={{ items: downloadItems(s) }} trigger={['click']}>
                    <Button size="small" icon={<DownloadOutlined />}>Download <DownOutlined /></Button>
                  </Dropdown>
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
      {editing && <ScriptEditor script={editing.id || editing.fromFile ? editing : null} onClose={() => setEditing(null)} onSaved={load} />}
      {running && <NewTaskModal open scriptId={running.id} title={`Run ${running.name}`} onClose={() => setRunning(null)} />}
      {starter && <StarterModal onClose={() => setStarter(false)} onDone={load} />}
      {importing && <ImportModal data={importing} onClose={() => setImporting(null)} onDone={load} />}
    </>
  )
}
