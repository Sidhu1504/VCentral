import { useEffect, useState } from 'react'
import { Alert, App, Button, Collapse, Form, Input, InputNumber, Modal, Segmented, Select, Typography } from 'antd'
import { api } from '../api'
import RemoteConsole, { consoleSize } from './RemoteConsole'

/** "Connect" on the VM page: choose SSH or RDP, type the VM login once, open the session in the browser. */
export default function ConnectModal({ vm, onClose }) {
  const { message } = App.useApp()
  const [form] = Form.useForm()
  const [info, setInfo] = useState(null)
  const [status, setStatus] = useState(null)
  const [busy, setBusy] = useState(false)
  const [session, setSession] = useState(null)
  const protocol = Form.useWatch('protocol', form)

  useEffect(() => {
    api.get(`/api/vms/${vm.id}/remote`).then((d) => {
      setInfo(d)
      form.setFieldsValue({ protocol: d.suggested, host: d.addresses[0], port: d.suggested === 'rdp' ? 3389 : 22,
        username: d.suggested === 'rdp' ? 'Administrator' : 'root', security: 'any' })
    }, (e) => message.error(e.message))
    api.get('/api/remote/status').then(setStatus, () => {})
  }, [vm.id, form, message])

  const connect = async (v) => {
    setBusy(true)
    try {
      const { ticket } = await api.post(`/api/vms/${vm.id}/remote`, {
        ...v, ...consoleSize(), dpi: 96, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      })
      form.setFieldValue('password', '')  // don't keep it in the page
      setSession({ ticket, protocol: v.protocol, title: `${vm.name} · ${v.username}@${v.host}` })
    } catch (e) {
      message.error(e.message)
    } finally {
      setBusy(false)
    }
  }

  if (session) return <RemoteConsole {...session} onClose={onClose} />

  const vmrc = status?.vcenter && info?.moid ? `vmrc://${status.vcenter}/?moid=${info.moid}` : null
  const noAddress = info && !info.addresses.length
  return (
    <Modal open title={`Connect to ${vm.name}`} onCancel={onClose} footer={null} width={560} destroyOnClose>
      {status && !status.available && (
        <Alert type="error" showIcon style={{ marginBottom: 12 }} message="Browser SSH/RDP is not available"
          description={status.enabled ? 'The remote access service (guacd) is not running on the vCentral server. Re-run deploy/native/install.sh, or run: sudo systemctl start guacd' : 'Turned off on this server (VCENTRAL_REMOTE_ACCESS_ENABLED=false).'} />
      )}
      {noAddress && (
        <Alert type="warning" showIcon style={{ marginBottom: 12 }} message="vCentral knows no IP address for this VM"
          description="SSH and RDP need the VM's IP. Use the VMware console below: it works without network access to the VM." />
      )}
      <Form form={form} layout="vertical" onFinish={connect} requiredMark={false} disabled={noAddress || (status && !status.available)}>
        <Form.Item name="protocol">
          <Segmented block options={[{ value: 'ssh', label: 'SSH (Linux)' }, { value: 'rdp', label: 'Remote Desktop (Windows)' }]}
            onChange={(p) => form.setFieldsValue({ port: p === 'rdp' ? 3389 : 22, username: p === 'rdp' ? 'Administrator' : 'root' })} />
        </Form.Item>
        <div style={{ display: 'flex', gap: 12 }}>
          <Form.Item name="host" label="Address" style={{ flex: 1 }} rules={[{ required: true }]}>
            <Select options={(info?.addresses || []).map((a) => ({ value: a, label: a }))} />
          </Form.Item>
          <Form.Item name="port" label="Port" style={{ width: 110 }} rules={[{ required: true }]}>
            <InputNumber min={1} max={65535} style={{ width: '100%' }} />
          </Form.Item>
        </div>
        <Form.Item name="username" label="Username on the VM" rules={[{ required: true }]}>
          <Input autoComplete="off" />
        </Form.Item>
        <Form.Item name="password" label="Password" extra={protocol === 'ssh' ? 'Optional for SSH: leave it empty to type it at the VM\'s login prompt instead.' : null}>
          <Input.Password autoComplete="new-password" />
        </Form.Item>
        {protocol === 'rdp' && (
          <Collapse ghost size="small" style={{ marginBottom: 12 }} items={[{ key: 'adv', label: 'Domain and security', children: (
            <>
              <Form.Item name="domain" label="Domain (if the account is a domain account)"><Input /></Form.Item>
              <Form.Item name="security" label="Security mode" extra="Leave on Automatic unless the connection fails; older Windows may need RDP or TLS.">
                <Select options={[{ value: 'any', label: 'Automatic' }, { value: 'nla', label: 'NLA' }, { value: 'tls', label: 'TLS' }, { value: 'rdp', label: 'RDP (old Windows)' }]} />
              </Form.Item>
            </>
          ) }]} />
        )}
        <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
          The login goes to the VM for this session only. vCentral does not save it; the audit log records who connected, to which VM, as which user, and for how long.
        </Typography.Paragraph>
        <Button type="primary" htmlType="submit" loading={busy} block>Connect</Button>
      </Form>
      {vmrc && (
        <Typography.Paragraph type="secondary" style={{ marginTop: 16, marginBottom: 0, fontSize: 12 }}>
          No IP, or SSH/RDP turned off on the VM? <a href={vmrc}>Open the VMware console</a> (needs VMware Remote Console on your PC; you sign in with your own vCenter account). It shows the VM's screen even when its network is down.
        </Typography.Paragraph>
      )}
    </Modal>
  )
}
