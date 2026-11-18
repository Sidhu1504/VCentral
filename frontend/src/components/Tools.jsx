import { useState } from 'react'
import { Link } from 'react-router-dom'
import { App, Button, List, Modal, Space, Tag, Tooltip, Typography } from 'antd'
import { ThunderboltOutlined, ToolOutlined } from '@ant-design/icons'
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

const OUTDATED = ['guestToolsNeedUpgrade', 'guestToolsSupportedOld', 'guestToolsTooOld', 'guestToolsBlacklisted']
const OUTCOME = {
  started: { color: 'processing', label: 'Started' }, ok: { color: 'success', label: 'Nothing to do' },
  manual: { color: 'warning', label: 'Needs you' }, failed: { color: 'error', label: 'Failed' }, skipped: { color: 'default', label: 'Skipped' },
}

/** Install / start / upgrade VMware Tools on one or more VMs, then show what happened per VM. */
export function InstallToolsButton({ vmIds, label = 'Install Tools', size = 'small', type, onDone }) {
  const { message, modal } = App.useApp()
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)
  const run = async () => {
    setBusy(true)
    try {
      setResult(await api.post('/api/vms/tools/install', { vm_ids: vmIds }))
      onDone?.()
    } catch (e) {
      message.error(e.message)
    } finally {
      setBusy(false)
    }
  }
  const confirm = () => modal.confirm({
    title: vmIds.length === 1 ? 'Install or start VMware Tools on this VM?' : `Install or start VMware Tools on ${vmIds.length} VMs?`,
    content: (
      <ul style={{ paddingInlineStart: 18, margin: 0 }}>
        <li>Outdated Tools: vCenter upgrades them.</li>
        <li>With a vCentral agent online: the agent installs and starts Tools (Linux: open-vm-tools from the package repository; Windows: the installer is mounted and runs silently, reboot postponed).</li>
        <li>Windows without an agent: the installer is mounted as a CD drive; you run setup from the console.</li>
        <li>Linux without an agent: shows the command to run in the VM.</li>
      </ul>
    ),
    okText: 'Go', width: 560, onOk: run,
  })
  return (
    <>
      <Button size={size} type={type} icon={<ToolOutlined />} loading={busy} onClick={confirm} disabled={!vmIds.length}>{label}</Button>
      <Modal open={!!result} title="VMware Tools" onCancel={() => setResult(null)} width={680}
        footer={<Button type="primary" onClick={() => setResult(null)}>Close</Button>}>
        {result?.task_id && (
          <Typography.Paragraph>
            Agents are working on it: <Link to={`/tasks/${result.task_id}`}>open the task</Link> for their output.
            vCentral shows Tools as running after the next vCenter sync.
          </Typography.Paragraph>
        )}
        <List size="small" dataSource={result?.results || []} style={{ maxHeight: 420, overflow: 'auto' }}
          renderItem={(r) => (
            <List.Item>
              <Space direction="vertical" size={0}>
                <Space><Tag color={OUTCOME[r.outcome]?.color}>{OUTCOME[r.outcome]?.label || r.outcome}</Tag><Typography.Text strong>{r.name}</Typography.Text></Space>
                <Typography.Text type="secondary" style={{ fontSize: 13 }}>{r.message}</Typography.Text>
              </Space>
            </List.Item>
          )} />
      </Modal>
    </>
  )
}

/** VMware Tools state with an explanation, and Wake up when the VM looks asleep. */
export function ToolsCell({ vm, onWoken }) {
  const { can } = useAuth()
  const s = STATES[vm.tools] || (vm.tools_status === 'guestToolsRunning' ? STATES.running : STATES.not_running)
  const tag = <Tag color={s.tag} style={{ marginInlineEnd: 0 }}>{s.label}</Tag>
  const operator = can('admin', 'operator')
  const canWake = operator && (vm.tools === 'asleep' || (vm.tools === 'not_running' && vm.os_family === 'windows'))
  const outdated = vm.tools === 'running' && OUTDATED.includes(vm.tools_version_status)
  const canInstall = operator && (vm.tools === 'not_installed' || vm.tools === 'not_running' || outdated)
  return (
    <Space size={6} wrap>
      {s.tip ? <Tooltip title={s.tip}>{tag}</Tooltip> : tag}
      {canWake && <WakeButton vmId={vm.id} onDone={onWoken} />}
      {outdated && <Tag color="gold" style={{ marginInlineEnd: 0 }}>outdated</Tag>}
      {canInstall && <InstallToolsButton vmIds={[vm.id]} label={outdated ? 'Upgrade' : vm.tools === 'not_installed' ? 'Install' : 'Start'} onDone={onWoken} />}
    </Space>
  )
}
