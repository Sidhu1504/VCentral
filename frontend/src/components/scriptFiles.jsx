import { useRef, useState } from 'react'
import { App, Button, Form, Input, Typography } from 'antd'
import { UploadOutlined } from '@ant-design/icons'

export const MAX_SCRIPT = 200000

const LINUX_EXT = ['.sh', '.bash', '.py', '.txt']
const WINDOWS_EXT = ['.ps1', '.bat', '.cmd', '.txt']
const ext = (name) => (name.match(/\.[^.]+$/)?.[0] || '').toLowerCase()

/** OS a file is for, from its extension (null for .txt or unknown). */
export function osForFile(name) {
  const e = ext(name)
  if (['.sh', '.bash', '.py'].includes(e)) return 'linux'
  if (['.ps1', '.bat', '.cmd'].includes(e)) return 'windows'
  return null
}

async function decode(file) {
  const bytes = new Uint8Array(await file.arrayBuffer())
  let text
  if (bytes[0] === 0xff && bytes[1] === 0xfe) text = new TextDecoder('utf-16le').decode(bytes.subarray(2)) // Windows "Unicode"
  else if (bytes[0] === 0xfe && bytes[1] === 0xff) text = new TextDecoder('utf-16be').decode(bytes.subarray(2))
  else text = new TextDecoder('utf-8').decode(bytes).replace(/^﻿/, '')
  if (text.includes('\u0000')) throw new Error(`${file.name} looks like a binary file, not a script`)
  return text
}

/**
 * Read a script file for one OS. Linux scripts get Unix line endings (Windows ones break bash).
 * Python (Linux) and batch files (Windows) are wrapped so the agent's bash / PowerShell runs them.
 */
export async function readScriptFile(file, os) {
  const e = ext(file.name)
  const allowed = os === 'linux' ? LINUX_EXT : WINDOWS_EXT
  if (!allowed.includes(e)) {
    throw new Error(`${os === 'linux' ? 'Linux' : 'Windows'} accepts ${allowed.join(', ')} files`)
  }
  let text = await decode(file)
  if (os === 'linux') {
    text = text.replace(/\r\n?/g, '\n')
    if (e === '.py') {
      if (/^VCENTRAL_PY_EOF$/m.test(text)) throw new Error('The file contains the line VCENTRAL_PY_EOF, which vCentral uses itself')
      text = `# Uploaded from ${file.name}: runs with python3\npython3 - <<'VCENTRAL_PY_EOF'\n${text.replace(/\n$/, '')}\nVCENTRAL_PY_EOF\n`
    }
  } else if (e === '.bat' || e === '.cmd') {
    text = text.replace(/\r?\n/g, '\r\n')
    if (/^'@/m.test(text)) throw new Error("The file has a line starting with '@, which cannot be wrapped for PowerShell")
    text = [
      `# Uploaded from ${file.name}: runs with cmd.exe`,
      "$file = Join-Path $env:TEMP ('vcentral-' + [guid]::NewGuid().ToString() + '.cmd')",
      "Set-Content -LiteralPath $file -Encoding Default -Value @'",
      text.replace(/\r\n$/, ''),
      "'@",
      '& cmd.exe /c $file',
      '$code = $LASTEXITCODE',
      'Remove-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue',
      'exit $code',
      '',
    ].join('\r\n')
  }
  if (text.length > MAX_SCRIPT) throw new Error(`${file.name} is too large (max ${MAX_SCRIPT.toLocaleString()} characters)`)
  return text
}

export function downloadText(filename, text, type = 'text/plain') {
  const url = URL.createObjectURL(new Blob([text], { type }))
  const a = Object.assign(document.createElement('a'), { href: url, download: filename })
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export const safeFileName = (name) => name.replace(/[^\w.-]+/g, '-').replace(/^-+|-+$/g, '') || 'script'

/** Script text box with "Upload file" and drag-and-drop. Use inside Form.Item (value/onChange). */
export function ScriptBox({ os, value, onChange, rows = 8, placeholder, style, onFileName }) {
  const { message } = App.useApp()
  const input = useRef(null)
  const [over, setOver] = useState(false)
  const load = async (file) => {
    if (!file) return
    try {
      onChange?.(await readScriptFile(file, os))
      onFileName?.(file.name)
      message.success(`Loaded ${file.name}`)
    } catch (e) {
      message.error(e.message)
    }
  }
  return (
    <div
      onDragOver={(e) => { e.preventDefault(); setOver(true) }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); load(e.dataTransfer.files?.[0]) }}
      style={{ position: 'relative' }}>
      <Input.TextArea rows={rows} value={value} onChange={(e) => onChange?.(e.target.value)} placeholder={placeholder}
        style={{ ...style, outline: over ? '2px dashed #3b82f6' : undefined, outlineOffset: 2 }} spellCheck={false} />
      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 4 }}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          Drop a {os === 'linux' ? '.sh, .py' : '.ps1, .bat, .cmd'} or .txt file here, or
          <Button type="link" size="small" icon={<UploadOutlined />} onClick={() => input.current?.click()} style={{ paddingInline: 4 }}>upload a file</Button>
        </Typography.Text>
        {value && <Typography.Text type="secondary" style={{ fontSize: 12 }}>{value.length.toLocaleString()} characters</Typography.Text>}
      </div>
      <input ref={input} type="file" style={{ display: 'none' }} accept={(os === 'linux' ? LINUX_EXT : WINDOWS_EXT).join(',')}
        onChange={(e) => { load(e.target.files?.[0]); e.target.value = '' }} />
    </div>
  )
}

/** {{NAME}} placeholders, mirrored from the server (backend/app/scriptvars.py). */
export function findVariables(...texts) {
  const seen = []
  for (const t of texts) {
    for (const m of (t || '').matchAll(/\{\{\s*([A-Za-z][A-Za-z0-9_]{0,31})\s*\}\}/g)) {
      const n = m[1].toUpperCase()
      if (!seen.includes(n)) seen.push(n)
    }
  }
  return seen
}

/** Value boxes for a script's variables (form field: variables.NAME). */
export function VariableInputs({ names }) {
  if (!names.length) return null
  return (
    <div style={{ padding: '12px 14px 0', marginBottom: 16, borderRadius: 8, border: '1px dashed rgba(59,130,246,0.5)' }}>
      <Typography.Paragraph type="secondary" style={{ marginBottom: 10, fontSize: 13 }}>
        This script asks for {names.length === 1 ? 'a value' : 'values'}. Each value is passed to the VM as plain text,
        never as a command.
      </Typography.Paragraph>
      {names.map((n) => (
        <Form.Item key={n} name={['variables', n]} label={<code>{n}</code>} rules={[{ required: true, message: `Enter ${n}` }, { max: 1000 }]}>
          <Input placeholder={`Value for ${n}`} />
        </Form.Item>
      ))}
    </div>
  )
}
