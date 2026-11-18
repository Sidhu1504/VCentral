import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Input, Popover, Space, Spin, Tag, Tooltip, Typography } from 'antd'
import { CopyOutlined, FullscreenOutlined, PoweroffOutlined } from '@ant-design/icons'
import Guacamole from 'guacamole-common-js'

const TOOLBAR = 44
const STATE = { 0: 'Starting', 1: 'Connecting', 2: 'Waiting for the VM', 3: 'Connected', 4: 'Disconnecting', 5: 'Disconnected' }
const KEY = { ctrl: 0xffe3, alt: 0xffe9, del: 0xffff, win: 0xffeb }

/** Full-window SSH / RDP session in the browser, relayed by vCentral through guacd. */
export default function RemoteConsole({ ticket, title, protocol, onClose }) {
  const holder = useRef(null)
  const screen = useRef(null)
  const clientRef = useRef(null)
  const [state, setState] = useState(0)
  const [error, setError] = useState(null)
  const [remoteClip, setRemoteClip] = useState('')
  const [localClip, setLocalClip] = useState('')

  useEffect(() => {
    const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const tunnel = new Guacamole.WebSocketTunnel(`${scheme}://${window.location.host}/api/remote/tunnel`)
    const client = new Guacamole.Client(tunnel)
    clientRef.current = client
    const display = client.getDisplay()
    const el = display.getElement()
    screen.current.appendChild(el)

    const fit = () => {
      const w = display.getWidth(), h = display.getHeight()
      const box = screen.current
      if (!w || !h || !box) return
      display.scale(Math.min(box.clientWidth / w, box.clientHeight / h, 1))
    }
    display.onresize = fit

    client.onstatechange = (s) => setState(s)
    client.onerror = (status) => setError(status.message || `Connection failed (code ${status.code})`)
    tunnel.onerror = (status) => setError(status.message || 'Lost the connection to vCentral')
    client.onclipboard = (stream, mimetype) => {
      if (!/^text\//.test(mimetype)) return
      const reader = new Guacamole.StringReader(stream)
      let text = ''
      reader.ontext = (t) => { text += t }
      reader.onend = () => {
        setRemoteClip(text)
        navigator.clipboard?.writeText(text).catch(() => {})
      }
    }

    const mouse = new Guacamole.Mouse(el)
    mouse.onEach(['mousedown', 'mouseup', 'mousemove'], (e) => client.sendMouseState(e.state, true))
    display.oncursor = (canvas, x, y) => { if (!mouse.setCursor(canvas, x, y)) display.showCursor(true) }
    const keyboard = new Guacamole.Keyboard(holder.current)
    keyboard.onkeydown = (k) => { client.sendKeyEvent(1, k); return false }
    keyboard.onkeyup = (k) => client.sendKeyEvent(0, k)
    holder.current.focus()

    // Follow the browser window: RDP and terminals resize to fit.
    let timer
    const onResize = () => {
      clearTimeout(timer)
      timer = setTimeout(() => {
        const box = screen.current
        if (box) client.sendSize(box.clientWidth, box.clientHeight)
        fit()
      }, 300)
    }
    window.addEventListener('resize', onResize)

    client.connect(`ticket=${encodeURIComponent(ticket)}`)
    return () => {
      window.removeEventListener('resize', onResize)
      clearTimeout(timer)
      keyboard.onkeydown = keyboard.onkeyup = null
      keyboard.reset()
      client.disconnect()
    }
  }, [ticket])

  const keys = (...ks) => {
    const c = clientRef.current
    ks.forEach((k) => c.sendKeyEvent(1, k))
    ks.slice().reverse().forEach((k) => c.sendKeyEvent(0, k))
    holder.current.focus()
  }
  const sendClipboard = () => {
    const stream = clientRef.current.createClipboardStream('text/plain')
    const w = new Guacamole.StringWriter(stream)
    w.sendText(localClip)
    w.sendEnd()
    holder.current.focus()
  }
  const connected = state === 3
  const ended = state === 5 || error

  const clipboard = (
    <div style={{ width: 340 }}>
      <Typography.Text type="secondary">Text to paste on the VM: put it here, click Send, then paste on the VM as usual.</Typography.Text>
      <Input.TextArea rows={4} value={localClip} onChange={(e) => setLocalClip(e.target.value)} style={{ margin: '6px 0' }} />
      <Button size="small" type="primary" disabled={!localClip} onClick={sendClipboard}>Send to VM clipboard</Button>
      {remoteClip && (
        <>
          <Typography.Paragraph type="secondary" style={{ margin: '10px 0 4px' }}>Last copied on the VM (also copied to your clipboard when the browser allows it):</Typography.Paragraph>
          <Input.TextArea rows={3} value={remoteClip} readOnly />
        </>
      )}
    </div>
  )

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 1100, background: '#000', display: 'flex', flexDirection: 'column' }}>
      <div style={{ height: TOOLBAR, display: 'flex', alignItems: 'center', gap: 12, padding: '0 12px', background: '#141414', borderBottom: '1px solid #303030', color: '#fff' }}>
        <Typography.Text strong style={{ color: '#fff' }}>{title}</Typography.Text>
        <Tag color={connected ? 'success' : ended ? 'error' : 'processing'}>{protocol.toUpperCase()} · {error ? 'Failed' : STATE[state]}</Tag>
        <div style={{ flex: 1 }} />
        <Space size={6}>
          {protocol === 'rdp' && (
            <Tooltip zIndex={1200} title="Sends Ctrl+Alt+Del to the VM, e.g. to sign in or lock it. Pressing it on your own keyboard goes to your PC instead.">
              <Button size="small" disabled={!connected} onClick={() => keys(KEY.ctrl, KEY.alt, KEY.del)}>Ctrl+Alt+Del</Button>
            </Tooltip>
          )}
          {protocol === 'rdp' && <Tooltip zIndex={1200} title="Sends the Windows key to the VM"><Button size="small" disabled={!connected} onClick={() => keys(KEY.win)}>Win</Button></Tooltip>}
          <Popover trigger="click" content={clipboard} title="Clipboard">
            <Button size="small" icon={<CopyOutlined />} disabled={!connected}>Clipboard</Button>
          </Popover>
          <Tooltip zIndex={1200} title="Full screen"><Button size="small" icon={<FullscreenOutlined />} onClick={() => holder.current.parentElement.requestFullscreen?.()} /></Tooltip>
          <Button size="small" danger icon={<PoweroffOutlined />} onClick={onClose}>{ended ? 'Close' : 'Disconnect'}</Button>
        </Space>
      </div>
      <div ref={holder} tabIndex={0} onClick={() => holder.current.focus()}
        style={{ flex: 1, position: 'relative', outline: 'none', overflow: 'hidden' }}>
        <div ref={screen} style={{ position: 'absolute', inset: 0 }} />
        {!connected && !ended && (
          <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center' }}><Spin size="large" tip={STATE[state]}><div style={{ width: 200 }} /></Spin></div>
        )}
        {ended && (
          <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', padding: 24 }}>
            <Alert type={error ? 'error' : 'info'} showIcon style={{ maxWidth: 560 }}
              message={error ? 'The session could not continue' : 'Session ended'}
              description={error || 'The connection to the VM was closed.'}
              action={<Button onClick={onClose}>Close</Button>} />
          </div>
        )}
      </div>
    </div>
  )
}

/** Window size available to the remote screen, sent before connecting. */
export function consoleSize() {
  return { width: Math.max(window.innerWidth, 320), height: Math.max(window.innerHeight - TOOLBAR, 200) }
}
