import { useEffect, useRef, useState } from 'react'
import { Alert, App, Button, Form, Input, Modal, Steps, Tabs, Typography } from 'antd'
import { CheckCircleFilled, CloseCircleFilled, LoadingOutlined } from '@ant-design/icons'
import { api } from '../api'
import { buildCommands } from './InstallCommands'

/**
 * Install the agent on one VM: push it through VMware Tools with a guest login (used once, never stored),
 * or get a one-time command to run on the VM.
 */
export default function InstallAgentModal({ vm, onClose, onDone }) {
  const { message } = App.useApp()
  const [form] = Form.useForm()
  const [job, setJob] = useState(null)
  const [cmd, setCmd] = useState(null)
  const [busy, setBusy] = useState(false)
  const timer = useRef(null)
  const windows = vm.os_family === 'windows'

  useEffect(() => () => clearInterval(timer.current), [])

  const poll = (id) => {
    timer.current = setInterval(async () => {
      try {
        const j = await api.get(`/api/push-jobs/${id}`)
        setJob(j)
        if (j.status === 'succeeded' || j.status === 'failed') {
          clearInterval(timer.current)
          if (j.status === 'succeeded') onDone?.()
        }
      } catch {
        clearInterval(timer.current)
      }
    }, 1500)
  }

  const push = async () => {
    const values = await form.validateFields()
    setBusy(true)
    try {
      const j = await api.post(`/api/vms/${vm.id}/push`, values)
      form.resetFields() // drop the password from the page as soon as it has been sent
      setJob(j)
      poll(j.id)
    } catch (e) {
      message.error(e.message)
    } finally {
      setBusy(false)
    }
  }

  const getCommand = async () => {
    setBusy(true)
    try {
      setCmd(await api.post(`/api/vms/${vm.id}/install-command`))
    } catch (e) {
      message.error(e.message)
    } finally {
      setBusy(false)
    }
  }

  const running = job && (job.status === 'queued' || job.status === 'running')
  const pushTab = job ? (
    <>
      <Steps direction="vertical" size="small" current={job.steps.length}
        items={[
          ...job.steps.map((s) => ({ title: s.message, status: s.ok ? 'finish' : 'error', icon: s.ok ? <CheckCircleFilled /> : <CloseCircleFilled /> })),
          ...(running ? [{ title: 'Working…', status: 'process', icon: <LoadingOutlined /> }] : []),
        ]} />
      {job.status === 'succeeded' && <Alert type="success" showIcon message="The agent is installed. Data appears on this VM's page within a minute." />}
      {job.status === 'failed' && (
        <>
          <Alert type="error" showIcon message={job.error} style={{ marginBottom: 8 }} />
          {job.output && <Typography.Paragraph><pre style={{ maxHeight: 160, overflow: 'auto', fontSize: 12 }}>{job.output}</pre></Typography.Paragraph>}
          <Button onClick={() => setJob(null)}>Try again</Button>
        </>
      )}
    </>
  ) : (
    <>
      <Typography.Paragraph type="secondary">
        vCentral copies the agent into the VM through VMware Tools and installs it. This works even if the VM's
        network is blocked. The login is used for this one installation only and is never saved.
      </Typography.Paragraph>
      {vm.tools_running === false || vm.tools_status === 'guestToolsNotRunning' ? (
        <Alert type="warning" showIcon message="VMware Tools is not running in this VM, so this method is not available. Use the command instead." />
      ) : (
        <Form form={form} layout="vertical" onFinish={push} autoComplete="off">
          <Form.Item name="username" label={windows ? 'Windows administrator account' : 'Linux account'}
            initialValue={windows ? 'Administrator' : 'root'}
            extra={windows ? 'A local or domain account in the Administrators group (e.g. Administrator or .\\admin).' : 'Use root: the agent installs to /usr/local/bin and runs as a system service.'}
            rules={[{ required: true }]}>
            <Input autoComplete="off" />
          </Form.Item>
          <Form.Item name="password" label="Password" rules={[{ required: true }]}>
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={busy}>Install agent</Button>
        </Form>
      )}
    </>
  )

  const commands = cmd && buildCommands(cmd.token, cmd.fingerprint, cmd.server)
  const commandTab = cmd ? (
    <>
      <Typography.Paragraph type="secondary">
        Run this on <b>{vm.name}</b> {windows ? 'in PowerShell opened with “Run as administrator”' : 'as a user with sudo'}.
        It works once, within {cmd.expires_in_hours} hours.
      </Typography.Paragraph>
      <Typography.Paragraph copyable={{ text: windows ? commands.windows : commands.linux }}>
        <code style={{ display: 'block', whiteSpace: 'pre-wrap', wordBreak: 'break-all', padding: 12, fontSize: 12 }}>
          {windows ? commands.windows : commands.linux}
        </code>
      </Typography.Paragraph>
    </>
  ) : (
    <>
      <Typography.Paragraph type="secondary">
        Get a one-time command to run on the VM yourself, for example if VMware Tools is not running or you prefer not
        to type a password here.
      </Typography.Paragraph>
      <Button onClick={getCommand} loading={busy}>Create install command</Button>
    </>
  )

  return (
    <Modal title={`Install the agent on ${vm.name}`} open width={720} onCancel={onClose} maskClosable={!running}
      footer={<Button onClick={onClose}>{job?.status === 'succeeded' ? 'Done' : 'Close'}</Button>}>
      <Tabs items={[
        { key: 'push', label: 'Install through VMware Tools', children: pushTab },
        { key: 'command', label: 'Run a command', children: commandTab },
      ]} />
    </Modal>
  )
}
