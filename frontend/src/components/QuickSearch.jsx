import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Empty, Input, Modal, Space, Spin, Tag, Typography } from 'antd'
import { SearchOutlined } from '@ant-design/icons'
import { api } from '../api'
import { OsIcon, PowerTag } from '../pages/VMs'

const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform)

/** Ctrl+K (⌘K on Mac) from any page: find a VM by name, IP, hostname, owner or folder and open it. */
export default function QuickSearch({ compact }) {
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(false)
  const [active, setActive] = useState(0)
  const seq = useRef(0)
  const inputRef = useRef(null)

  useEffect(() => {
    const onKey = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setOpen(true)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => {
    if (!open) return
    const term = q.trim()
    if (!term) { setItems([]); return }
    const id = ++seq.current
    setLoading(true)
    const t = setTimeout(() => {
      api.get('/api/vms', { search: term, page_size: 8, status: 'all' })
        .then((d) => { if (id === seq.current) { setItems(d.items); setActive(0) } }, () => {})
        .finally(() => { if (id === seq.current) setLoading(false) })
    }, 180)
    return () => clearTimeout(t)
  }, [q, open])

  const close = () => { setOpen(false); setQ(''); setItems([]) }
  const go = (vm) => { if (vm) { close(); navigate(`/vms/${vm.id}`) } }
  const onKeyDown = (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => Math.min(a + 1, items.length - 1)) }
    if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)) }
    if (e.key === 'Enter') go(items[active])
  }

  return (
    <>
      {compact
        ? <Button type="text" icon={<SearchOutlined />} onClick={() => setOpen(true)} aria-label="Search VMs" />
        : (
          <Button onClick={() => setOpen(true)} icon={<SearchOutlined />} style={{ width: 240, justifyContent: 'flex-start', color: 'inherit', opacity: 0.85 }}>
            <span style={{ flex: 1, textAlign: 'left' }}>Search VMs…</span>
            <Tag style={{ marginInlineEnd: 0, fontSize: 11 }}>{isMac ? '⌘' : 'Ctrl'} K</Tag>
          </Button>
        )}
      <Modal open={open} onCancel={close} footer={null} closable={false} width={600} styles={{ body: { padding: 0 } }} destroyOnClose
        style={{ top: 80 }} afterOpenChange={(o) => o && inputRef.current?.focus()}>
        <Input ref={inputRef} autoFocus size="large" variant="borderless" prefix={<SearchOutlined />} placeholder="VM name, IP address, hostname, owner or folder"
          value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onKeyDown} style={{ padding: '14px 16px' }}
          suffix={loading ? <Spin size="small" /> : null} />
        <div style={{ borderTop: '1px solid rgba(128,128,128,0.2)', maxHeight: 420, overflow: 'auto' }}>
          {q.trim() && !loading && !items.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No VM matches" style={{ padding: 24 }} />}
          {items.map((vm, i) => (
            <div key={vm.id} role="option" aria-selected={i === active} onMouseEnter={() => setActive(i)} onClick={() => go(vm)}
              style={{ padding: '10px 16px', cursor: 'pointer', background: i === active ? 'rgba(59,130,246,0.12)' : undefined,
                display: 'flex', alignItems: 'center', gap: 12 }}>
              <OsIcon family={vm.os_family} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {vm.name}{vm.is_deleted && <Tag style={{ marginLeft: 8 }}>deleted</Tag>}
                </div>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {[vm.primary_ip || vm.last_known_ip, vm.owner, vm.folder].filter(Boolean).join(' · ') || '—'}
                </Typography.Text>
              </div>
              <PowerTag state={vm.power_state} />
            </div>
          ))}
          {!q.trim() && (
            <Space style={{ padding: '12px 16px' }}>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>↑ ↓ to choose · Enter to open · Esc to close</Typography.Text>
            </Space>
          )}
        </div>
      </Modal>
    </>
  )
}
