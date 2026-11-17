import { useState } from 'react'
import { App, Button, Space, Tag, Tooltip, Typography } from 'antd'
import { ThunderboltOutlined } from '@ant-design/icons'
import { api } from '../api'
import { useAuth } from '../auth'

const STATES = {
  running: { tag: 'success', label: 'Running' },
  not_installed: { tag: 'error', label: 'Not installed', tip: 'VMware Tools is not installed in this VM. Without it vCenter cannot see its IP or disks.' },
  asleep: {
    tag: 'warning', label: 'Not running: asleep?',
    tip: 'VMware Tools is installed but stopped, on a Windows VM that had an IP before. That is almost always Windows idle sleep: '
      + 'Tools and the agent stop until someone wakes the VM. Use Wake up. The vCentral agent (0.6.0+) keeps Windows from idle sleep.',
  },
  not_running: { tag: 'warning', label: 'Not running', tip: 'VMware Tools is installed but its service is stopped, or the VM is still starting.' },
  off: { tag: 'default', label: 'VM off' },
  unknown: { tag: 'default', label: 'Unknown' },
}

export function WakeButton({ vmId, size = 'small', onDone }) {
  const { message } = App.useApp()
  const [busy, setBusy] = useState(false)
  const wake = async () => {
    setBusy(true)
    try {
      const r = await api.post(`/api/vms/${vmId}/wake`)
      message.success(r.message)
      onDone?.()
    } catch (e) {
      message.error(e.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Tooltip title="Presses F15 on the VM's keyboard through vCenter (no network or login needed). F15 does nothing in programs but wakes a sleeping VM.">
      <Button size={size} icon={<ThunderboltOutlined />} loading={busy} onClick={wake}>Wake up</Button>
    </Tooltip>
  )
}

/** VMware Tools state with an explanation, and Wake up when the VM looks asleep. */
export function ToolsCell({ vm, onWoken }) {
  const { can } = useAuth()
  const s = STATES[vm.tools] || (vm.tools_status === 'guestToolsRunning' ? STATES.running : STATES.not_running)
  const tag = <Tag color={s.tag} style={{ marginInlineEnd: 0 }}>{s.label}</Tag>
  const canWake = can('admin', 'operator') && (vm.tools === 'asleep' || (vm.tools === 'not_running' && vm.os_family === 'windows'))
  return (
    <Space size={6} wrap>
      {s.tip ? <Tooltip title={s.tip}>{tag}</Tooltip> : tag}
      {canWake && <WakeButton vmId={vm.id} onDone={onWoken} />}
      {vm.tools === 'not_installed' && <Typography.Text type="secondary" style={{ fontSize: 12 }}>use the install command</Typography.Text>}
    </Space>
  )
}
