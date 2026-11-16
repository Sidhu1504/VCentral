import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Checkbox, Input, Radio, Select, Space, Typography } from 'antd'
import { api } from '../api'

/** Choose which VMs a task runs on: specific VMs, or a filter (resolved when the task starts). */
export function TargetPicker({ value, onChange, allowVms = true }) {
  const [options, setOptions] = useState([])
  const mode = value.vm_ids ? 'vms' : 'filter'
  const search = useCallback((text) => {
    api.get('/api/vms', { search: text, page_size: 30, agent: 'online' })
      .then((d) => setOptions(d.items.map((v) => ({ value: v.id, label: `${v.name}${v.primary_ip ? ` (${v.primary_ip})` : ''}` }))))
      .catch(() => {})
  }, [])
  useEffect(() => { if (mode === 'vms') search('') }, [mode, search])

  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      {allowVms && (
        <Radio.Group value={mode} optionType="button"
          onChange={(e) => onChange(e.target.value === 'vms' ? { vm_ids: [] } : { os_family: null, critical: false, search: '', only_online: false })}
          options={[{ value: 'vms', label: 'Choose VMs' }, { value: 'filter', label: 'All VMs matching…' }]} />
      )}
      {mode === 'vms' ? (
        <Select mode="multiple" showSearch filterOption={false} onSearch={search} options={options} value={value.vm_ids}
          onChange={(ids) => onChange({ vm_ids: ids })} placeholder="Search VMs with an online agent" style={{ width: '100%' }} />
      ) : (
        <Space wrap>
          <Select style={{ width: 150 }} value={value.os_family || 'all'} onChange={(v) => onChange({ ...value, os_family: v === 'all' ? null : v })}
            options={[{ value: 'all', label: 'Linux and Windows' }, { value: 'linux', label: 'Linux only' }, { value: 'windows', label: 'Windows only' }]} />
          <Input allowClear placeholder="Name or folder contains" style={{ width: 210 }} value={value.search || ''}
            onChange={(e) => onChange({ ...value, search: e.target.value })} />
          <Checkbox checked={!!value.critical} onChange={(e) => onChange({ ...value, critical: e.target.checked })}>Critical VMs only</Checkbox>
          <Checkbox checked={!!value.only_online} onChange={(e) => onChange({ ...value, only_online: e.target.checked })}>Online agents only</Checkbox>
        </Space>
      )}
    </Space>
  )
}

/** Live count of the VMs a task would run on. */
export function TargetPreview({ targets, scriptId, linux, windows, onCount }) {
  const [p, setP] = useState(null)
  const timer = useRef(null)
  const key = JSON.stringify([targets, scriptId, linux?.length, windows?.length])
  useEffect(() => {
    clearTimeout(timer.current)
    timer.current = setTimeout(() => {
      if (targets.vm_ids && !targets.vm_ids.length) { setP(null); onCount?.(0); return }
      api.post('/api/tasks/preview', { targets, script_id: scriptId ?? null, linux_script: linux || null, windows_script: windows || null })
        .then((r) => { setP(r); onCount?.(r.will_run) }).catch(() => setP(null))
    }, 400)
    return () => clearTimeout(timer.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
  if (!p) return null
  const skipped = [p.no_agent && `${p.no_agent} without agent`, p.no_script && `${p.no_script} without a script for their OS`].filter(Boolean)
  return (
    <Alert type={p.will_run ? 'info' : 'warning'} showIcon
      message={<>Runs on <b>{p.will_run}</b> VM{p.will_run === 1 ? '' : 's'} ({p.linux} Linux, {p.windows} Windows)
        {p.offline ? `, ${p.offline} currently offline (they run when they come back within an hour)` : ''}</>}
      description={<>
        {skipped.length > 0 && <div>Skipped: {skipped.join(', ')}.</div>}
        {p.names.length > 0 && <Typography.Text type="secondary">{p.names.join(', ')}{p.will_run > p.names.length ? ', …' : ''}</Typography.Text>}
      </>} />
  )
}

export function describeTargets(t) {
  if (!t) return '—'
  if (t.vm_ids) return `${t.vm_ids.length} chosen VM${t.vm_ids.length === 1 ? '' : 's'}`
  if (t.agent_ids !== undefined) return t.agent_ids ? `${t.agent_ids.length} agents` : 'All outdated agents'
  const f = t.filter || {}
  return [f.os_family ? `${f.os_family === 'linux' ? 'Linux' : 'Windows'} VMs` : 'All VMs', f.critical && 'critical',
    f.search && `matching “${f.search}”`, f.only_online && 'online'].filter(Boolean).join(', ')
}
