import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Alert, App, Form, Input, InputNumber, Modal, Radio, Select, Typography } from 'antd'
import { api } from '../api'
import { useAuth } from '../auth'
import { TargetPicker, TargetPreview } from './TargetPicker'

export const codeStyle = { fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace', fontSize: 13 }

/**
 * Start a task. Admins may write a script; operators pick one from the library.
 * `fixedTargets` (e.g. {vm_ids:[5]}) skips the target picker, `title` names it.
 */
export default function NewTaskModal({ open, onClose, fixedTargets, title, scriptId: initialScript }) {
  const { can } = useAuth()
  const { message, modal } = App.useApp()
  const navigate = useNavigate()
  const admin = can('admin')
  const [form] = Form.useForm()
  const [scripts, setScripts] = useState([])
  const [source, setSource] = useState(initialScript || !admin ? 'library' : 'adhoc')
  const [targets, setTargets] = useState(fixedTargets || { vm_ids: [] })
  const [count, setCount] = useState(0)
  const [busy, setBusy] = useState(false)
  const scriptId = Form.useWatch('script_id', form)
  const linux = Form.useWatch('linux_script', form)
  const windows = Form.useWatch('windows_script', form)
  const chosen = scripts.find((s) => s.id === scriptId)

  useEffect(() => { if (open) api.get('/api/scripts').then(setScripts).catch(() => {}) }, [open])

  const start = async () => {
    const v = await form.validateFields()
    const body = {
      name: v.name || '', targets, timeout_seconds: v.timeout_seconds || 300,
      ...(source === 'library' ? { script_id: v.script_id } : { linux_script: v.linux_script || null, windows_script: v.windows_script || null }),
    }
    const go = async () => {
      setBusy(true)
      try {
        const t = await api.post('/api/tasks', body)
        message.success('Task started')
        onClose()
        navigate(`/tasks/${t.id}`)
      } catch (e) {
        message.error(e.message)
      } finally {
        setBusy(false)
      }
    }
    if (count > 5) {
      modal.confirm({ title: `Run this on ${count} VMs?`, content: 'The script runs as root / SYSTEM on every one of them.', okText: `Run on ${count} VMs`, okButtonProps: { danger: true }, onOk: go })
    } else go()
  }

  return (
    <Modal open={open} title={title || 'Run a task'} width={860} onCancel={onClose} onOk={start} okText="Run" confirmLoading={busy}
      okButtonProps={{ disabled: !count }} destroyOnClose>
      <Form form={form} layout="vertical" initialValues={{ timeout_seconds: 300, script_id: initialScript }}>
        {admin && (
          <Form.Item label="Script">
            <Radio.Group value={source} onChange={(e) => setSource(e.target.value)} optionType="button"
              options={[{ value: 'library', label: 'From the library' }, { value: 'adhoc', label: 'Write one now' }]} />
          </Form.Item>
        )}
        {source === 'library' ? (
          <>
            <Form.Item name="script_id" label={admin ? null : 'Script from the library'} rules={[{ required: true, message: 'Choose a script' }]}>
              <Select showSearch optionFilterProp="label" placeholder="Choose a script"
                options={scripts.map((s) => ({ value: s.id, label: s.name }))} />
            </Form.Item>
            {chosen && (
              <Typography.Paragraph type="secondary">
                {chosen.description || 'No description.'} {chosen.linux_script ? 'Linux ✓' : 'No Linux version'} · {chosen.windows_script ? 'Windows ✓' : 'No Windows version'} · timeout {chosen.timeout_seconds}s
              </Typography.Paragraph>
            )}
            {!admin && scripts.length === 0 && <Alert type="info" showIcon message="The library is empty. An admin can add scripts under Tasks → Scripts." />}
          </>
        ) : (
          <>
            <Form.Item name="name" label="Name (for the history)"><Input placeholder="e.g. Check disk space" maxLength={255} /></Form.Item>
            <Form.Item name="linux_script" label="Linux (bash, runs as root)">
              <Input.TextArea rows={6} style={codeStyle} placeholder={'df -h\nsystemctl is-active nginx'} spellCheck={false} />
            </Form.Item>
            <Form.Item name="windows_script" label="Windows (PowerShell, runs as SYSTEM)">
              <Input.TextArea rows={6} style={codeStyle} placeholder={'Get-PSDrive -PSProvider FileSystem\nGet-Service W32Time'} spellCheck={false} />
            </Form.Item>
            <Form.Item name="timeout_seconds" label="Stop after"><InputNumber min={10} max={21600} addonAfter="seconds" /></Form.Item>
          </>
        )}
        <Form.Item label="Run on">
          {fixedTargets ? <Typography.Text>{title?.replace(/^Run a task on /, '') || 'The selected VM'}</Typography.Text>
            : <TargetPicker value={targets} onChange={setTargets} />}
        </Form.Item>
        <TargetPreview targets={targets} scriptId={source === 'library' ? scriptId : null}
          linux={source === 'adhoc' ? linux : null} windows={source === 'adhoc' ? windows : null} onCount={setCount} />
      </Form>
    </Modal>
  )
}
