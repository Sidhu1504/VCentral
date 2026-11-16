import { useState } from 'react'
import { App, Input, Modal, Typography } from 'antd'
import { api } from '../api'

/** Set one owner for many VMs (also used from the VM list). */
export default function SetOwnerModal({ vmIds, onClose, onDone }) {
  const { message } = App.useApp()
  const [owner, setOwner] = useState('')
  const [busy, setBusy] = useState(false)
  const save = async () => {
    setBusy(true)
    try {
      const r = await api.post('/api/vms/bulk-owner', { vm_ids: vmIds, owner })
      message.success(`Owner set on ${r.updated} VM${r.updated === 1 ? '' : 's'}`)
      onDone()
      onClose()
    } catch (e) {
      message.error(e.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal open title={`Set the owner of ${vmIds.length} VM${vmIds.length === 1 ? '' : 's'}`} onCancel={onClose} onOk={save}
      okText="Set owner" confirmLoading={busy} okButtonProps={{ disabled: !owner.trim() }}>
      <Typography.Paragraph type="secondary">A person or a team, e.g. <code>VSPHERE.LOCAL\nachiket</code>, <code>nachiket@company.com</code> or <code>QA team</code>. Owners set here are never changed automatically.</Typography.Paragraph>
      <Input autoFocus value={owner} onChange={(e) => setOwner(e.target.value)} maxLength={255} onPressEnter={save} placeholder="Owner" />
    </Modal>
  )
}
