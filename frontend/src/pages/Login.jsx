import { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Form, Input } from 'antd'
import { CheckCircleFilled, CodeOutlined, DesktopOutlined, IdcardOutlined, LockOutlined, MailOutlined, RadarChartOutlined, SafetyCertificateOutlined, UserOutlined } from '@ant-design/icons'
import { api } from '../api'
import { useAuth } from '../auth'
import './login.css'

const POINTS = [
  [<RadarChartOutlined key="i" />, 'Every VM in one place: power, IP, health and owner'],
  [<DesktopOutlined key="i" />, 'Windows and Linux agents with live metrics'],
  [<CodeOutlined key="i" />, 'Run scripts on many VMs at once'],
  [<SafetyCertificateOutlined key="i" />, 'SSH and Remote Desktop from the browser, fully audited'],
]

/** Animated grid of "VM" tiles for the brand panel; states and timing are random but fixed per page load. */
function Fleet() {
  const tiles = useMemo(() => Array.from({ length: 96 }, () => {
    const r = Math.random()
    return { kind: r < 0.62 ? 'on' : r < 0.9 ? 'idle' : 'warn', delay: -(Math.random() * 4.8).toFixed(2), dur: (3.6 + Math.random() * 3).toFixed(2) }
  }), [])
  return (
    <div className="auth-fleet" aria-hidden>
      {tiles.map((t, i) => <i key={i} className={t.kind} style={{ animationDelay: `${t.delay}s`, animationDuration: `${t.dur}s` }} />)}
    </div>
  )
}

/** Page shell shared by the sign-in and password-change screens. */
export function AuthShell({ children, brand = true }) {
  return (
    <div className={`auth${brand ? '' : ' single'}`}>
      <span className="auth-orb one" aria-hidden />
      <span className="auth-orb two" aria-hidden />
      {brand && (
        <section className="auth-brand">
          <Fleet />
          <span className="auth-sweep" aria-hidden />
          <div className="auth-brand-head"><img src="/favicon.svg" alt="" />vCentral</div>
          <h1>All your vSphere VMs, managed from one place.</h1>
          <p className="lead">For the admin team: see what is running, who owns it and what needs attention, then act on it.</p>
          <ul className="auth-points">
            {POINTS.map(([icon, text]) => <li key={text}><span className="ico">{icon}</span>{text}</li>)}
          </ul>
          <div className="auth-brand-foot">Admin team access only</div>
        </section>
      )}
      <main className="auth-main">{children}</main>
    </div>
  )
}

/** Card used on its own by the password-change screen (kept for its existing import). */
export function CenteredCard({ children }) {
  return (
    <AuthShell brand={false}>
      <div className="auth-card">{children}</div>
    </AuthShell>
  )
}

function SignInForm({ onSignup, signupEnabled }) {
  const { login } = useAuth()
  const [error, setError] = useState(null)
  const [attempt, setAttempt] = useState(0)
  const [loading, setLoading] = useState(false)

  const onFinish = async ({ username, password }) => {
    setLoading(true)
    setError(null)
    try {
      await login(username, password)
    } catch (e) {
      setError(e.message)
      setAttempt((n) => n + 1)
    } finally {
      setLoading(false)
    }
  }

  return (
    /* re-keyed on each failed attempt so the shake animation plays again */
    <div key={attempt} className={`auth-card${attempt ? ' shake' : ''}`}>
      <div className="mark"><img src="/favicon.svg" alt="" /></div>
      <h2>Welcome back</h2>
      <p className="sub">Sign in to vCentral</p>
      {error && <Alert className="auth-error" type="error" message={error} showIcon />}
      <Form layout="vertical" onFinish={onFinish} requiredMark={false}>
        <Form.Item name="username" rules={[{ required: true, message: 'Enter your username' }]}>
          <Input prefix={<UserOutlined />} placeholder="Username" autoComplete="username" autoFocus size="large" />
        </Form.Item>
        <Form.Item name="password" rules={[{ required: true, message: 'Enter your password' }]}>
          <Input.Password prefix={<LockOutlined />} placeholder="Password" autoComplete="current-password" size="large" />
        </Form.Item>
        <Button className="auth-submit" type="primary" htmlType="submit" block size="large" loading={loading}>
          {loading ? 'Signing in…' : 'Sign in'}
        </Button>
      </Form>
      {signupEnabled && (
        <div className="auth-switch">New here? <button type="button" onClick={onSignup}>Create an account</button></div>
      )}
      <div className="auth-note"><SafetyCertificateOutlined /> Every sign-in is recorded in the audit log</div>
    </div>
  )
}

function SignUpForm({ onSignin, needsApproval }) {
  const { setUser } = useAuth()
  const [error, setError] = useState(null)
  const [attempt, setAttempt] = useState(0)
  const [loading, setLoading] = useState(false)
  const [done, setDone] = useState(null)

  const onFinish = async ({ confirm, ...values }) => {
    setLoading(true)
    setError(null)
    try {
      const r = await api.post('/api/auth/signup', values)
      if (r.pending_approval) setDone(values.username)
      else setUser(r.user) // signed in straight away
    } catch (e) {
      setError(e.message)
      setAttempt((n) => n + 1)
    } finally {
      setLoading(false)
    }
  }

  if (done) {
    return (
      <div className="auth-card">
        <div className="auth-done"><CheckCircleFilled /></div>
        <h2>Request sent</h2>
        <p className="sub">
          Your account <b>{done}</b> has been created with view-only access. You can sign in with the password you
          chose once an admin approves it.
        </p>
        <Button className="auth-submit" type="primary" block size="large" onClick={onSignin}>Back to sign in</Button>
      </div>
    )
  }
  return (
    <div key={attempt} className={`auth-card${attempt ? ' shake' : ''}`}>
      <div className="mark"><img src="/favicon.svg" alt="" /></div>
      <h2>Create an account</h2>
      <p className="sub">
        You get <b>view-only</b> access{needsApproval ? ' once an admin approves it' : ''}. An admin can give you more later.
      </p>
      {error && <Alert className="auth-error" type="error" message={error} showIcon />}
      <Form layout="vertical" onFinish={onFinish} requiredMark={false}>
        <Form.Item name="full_name">
          <Input prefix={<IdcardOutlined />} placeholder="Full name (optional)" autoComplete="name" autoFocus size="large" maxLength={128} />
        </Form.Item>
        <Form.Item name="username" rules={[
          { required: true, message: 'Choose a username' },
          { min: 3, message: 'At least 3 characters' },
          { pattern: /^[A-Za-z0-9._@-]+$/, message: 'Letters, numbers and . _ @ - only' },
        ]}>
          <Input prefix={<UserOutlined />} placeholder="Username" autoComplete="username" size="large" maxLength={64} />
        </Form.Item>
        <Form.Item name="email" rules={[{ type: 'email', message: 'Enter a valid email' }]}>
          <Input prefix={<MailOutlined />} placeholder="Email (optional)" autoComplete="email" size="large" />
        </Form.Item>
        <Form.Item name="password" rules={[{ required: true, message: 'Choose a password' }, { min: 10, message: 'At least 10 characters' }]}>
          <Input.Password prefix={<LockOutlined />} placeholder="Password (10+ characters)" autoComplete="new-password" size="large" />
        </Form.Item>
        <Form.Item name="confirm" dependencies={['password']} rules={[
          { required: true, message: 'Repeat the password' },
          ({ getFieldValue }) => ({ validator: (_, v) => (!v || v === getFieldValue('password') ? Promise.resolve() : Promise.reject(new Error('The passwords do not match'))) }),
        ]}>
          <Input.Password prefix={<LockOutlined />} placeholder="Repeat password" autoComplete="new-password" size="large" />
        </Form.Item>
        <Button className="auth-submit" type="primary" htmlType="submit" block size="large" loading={loading}>
          {loading ? 'Creating…' : needsApproval ? 'Request access' : 'Create account'}
        </Button>
      </Form>
      <div className="auth-switch">Already have an account? <button type="button" onClick={onSignin}>Sign in</button></div>
    </div>
  )
}

export default function Login() {
  const [mode, setMode] = useState('signin')
  const [signup, setSignup] = useState({ enabled: false, requires_approval: true })
  useEffect(() => { api.get('/api/auth/signup').then(setSignup, () => {}) }, [])

  return (
    <AuthShell>
      <div className="auth-flip" key={mode}>
        {mode === 'signup' && signup.enabled
          ? <SignUpForm needsApproval={signup.requires_approval} onSignin={() => setMode('signin')} />
          : <SignInForm signupEnabled={signup.enabled} onSignup={() => setMode('signup')} />}
      </div>
    </AuthShell>
  )
}
