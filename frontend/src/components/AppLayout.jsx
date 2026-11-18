import { useCallback, useEffect, useState } from 'react'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { App, Button, Drawer, Dropdown, Grid, Layout, Menu, Space, Tag, Tooltip, Typography } from 'antd'
import {
  AuditOutlined, CloudServerOutlined, HddOutlined, ClusterOutlined, CodeOutlined, IdcardOutlined, PlayCircleOutlined, RocketOutlined, ScheduleOutlined, DashboardOutlined, HistoryOutlined, KeyOutlined, LogoutOutlined, MenuOutlined,
  FileTextOutlined, MoonOutlined, SettingOutlined, SunOutlined, SyncOutlined, TeamOutlined, UserOutlined, ApartmentOutlined,
} from '@ant-design/icons'
import { api } from '../api'
import { useAuth } from '../auth'
import { useThemeMode } from '../theme'
import { fmtAgo } from '../format'
import ChangePasswordModal from './ChangePasswordModal'
import QuickSearch from './QuickSearch'

const { Sider, Header, Content } = Layout

/** Media query state that is correct on the first render (no collapse/expand flicker). */
function useMedia(query) {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const mq = window.matchMedia(query)
    const onChange = () => setMatches(mq.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [query])
  return matches
}

function SyncStatus() {
  const { can } = useAuth()
  const { message } = App.useApp()
  const [status, setStatus] = useState(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => api.get('/api/system/status').then(setStatus).catch(() => {}), [])
  useEffect(() => {
    load()
    const t = setInterval(load, 15000)
    return () => clearInterval(t)
  }, [load])

  const sync = status?.sync || {}
  const pending = status?.sync_request?.pending
  const syncNow = async () => {
    setBusy(true)
    try {
      await api.post('/api/system/sync')
      message.info('Sync requested; results appear in a few seconds')
      setTimeout(load, 4000)
    } catch (e) {
      message.error(e.message)
    } finally {
      setBusy(false)
    }
  }

  let tag
  if (!sync.status) tag = <Tag>Waiting for first sync</Tag>
  else if (sync.status === 'error')
    tag = (
      <Tooltip title={sync.last_error}>
        <Tag color="error">Sync failed {fmtAgo(sync.last_error_at)}</Tag>
      </Tooltip>
    )
  else
    tag = (
      <Tooltip title={`${sync.vm_count} VMs from ${status?.vcenter_host} in ${sync.duration_seconds}s`}>
        <Tag color="success">Synced {fmtAgo(sync.last_finished)}</Tag>
      </Tooltip>
    )

  return (
    <Space size={4}>
      {tag}
      {can('admin', 'operator') && (
        <Tooltip title="Sync with vCenter now">
          <Button size="small" icon={<SyncOutlined spin={busy || pending} />} onClick={syncNow} disabled={pending} />
        </Tooltip>
      )}
    </Space>
  )
}

export default function AppLayout() {
  const { user, logout, can } = useAuth()
  const { dark, toggle } = useThemeMode()
  const navigate = useNavigate()
  const location = useLocation()
  const screens = Grid.useBreakpoint()
  const [pwOpen, setPwOpen] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const mobile = !useMedia('(min-width: 768px)')
  const collapsed = !mobile && !useMedia('(min-width: 992px)')

  const items = [
    { key: '/', icon: <DashboardOutlined />, label: 'Dashboard' },
    { key: '/vms', icon: <ClusterOutlined />, label: 'Virtual machines' },
    { key: '/infrastructure', icon: <HddOutlined />, label: 'Infrastructure' },
    { key: '/network', icon: <ApartmentOutlined />, label: 'IP & subnets' },
    { key: '/agents', icon: <CloudServerOutlined />, label: 'Agents' },
    { key: '/onboarding', icon: <RocketOutlined />, label: 'Onboarding' },
    { key: '/owners', icon: <IdcardOutlined />, label: 'Owners' },
    { key: '/tasks', icon: <PlayCircleOutlined />, label: 'Tasks' },
    ...(can('admin', 'operator') ? [
      { key: '/scripts', icon: <CodeOutlined />, label: 'Script library' },
      { key: '/schedules', icon: <ScheduleOutlined />, label: 'Schedules' },
    ] : []),
    { key: '/reports', icon: <FileTextOutlined />, label: 'Reports' },
    { key: '/activity', icon: <HistoryOutlined />, label: 'Activity' },
    ...(can('admin', 'operator') ? [{ type: 'divider' }, { key: '/settings', icon: <SettingOutlined />, label: 'Settings' }] : []),
    ...(can('admin')
      ? [
          { key: '/users', icon: <TeamOutlined />, label: 'Users' },
          { key: '/audit', icon: <AuditOutlined />, label: 'Audit log' },
        ]
      : []),
  ]
  const selected = '/' + (location.pathname.split('/')[1] || '')
  const border = `1px solid ${dark ? '#303030' : '#f0f0f0'}`

  const brand = (showName) => (
    <div style={{ padding: showName ? '18px 20px' : '18px 0', textAlign: showName ? 'left' : 'center', fontWeight: 700, fontSize: 18, whiteSpace: 'nowrap' }}>
      <img src="/favicon.svg" alt="vCentral" width={22} style={{ verticalAlign: -5, marginRight: showName ? 10 : 0 }} />
      {showName && 'vCentral'}
    </div>
  )
  const menu = (
    <Menu
      mode="inline" selectedKeys={[selected]} items={items} style={{ borderInlineEnd: 0 }}
      onClick={({ key }) => { navigate(key); setDrawerOpen(false) }}
    />
  )

  return (
    <Layout style={{ minHeight: '100vh' }}>
      {!mobile && (
        <Sider theme="light" width={220} collapsedWidth={64} collapsed={collapsed} trigger={null}
          style={{ borderRight: border, position: 'sticky', top: 0, height: '100vh', overflow: 'auto' }}>
          {brand(!collapsed)}
          {menu}
        </Sider>
      )}
      <Drawer placement="left" width={240} open={mobile && drawerOpen} onClose={() => setDrawerOpen(false)}
        closable={false} styles={{ body: { padding: 0 } }}>
        {brand(true)}
        {menu}
      </Drawer>
      <Layout>
        <Header
          style={{
            display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 12, paddingInline: 16,
            background: dark ? '#141414' : '#fff', borderBottom: border,
          }}
        >
          {mobile && (
            <Button type="text" icon={<MenuOutlined />} onClick={() => setDrawerOpen(true)} aria-label="Open menu" style={{ marginRight: 'auto' }} />
          )}
          <div style={{ marginRight: mobile ? 0 : 'auto' }}><QuickSearch compact={!screens.md} /></div>
          <SyncStatus />
          <Tooltip title={dark ? 'Light mode' : 'Dark mode'}>
            <Button type="text" icon={dark ? <SunOutlined /> : <MoonOutlined />} onClick={toggle} aria-label="Toggle theme" />
          </Tooltip>
          <Dropdown
            menu={{
              items: [
                { key: 'role', label: <Typography.Text type="secondary">Role: {user.role}</Typography.Text>, disabled: true },
                { key: 'pw', icon: <KeyOutlined />, label: 'Change password', onClick: () => setPwOpen(true) },
                { key: 'out', icon: <LogoutOutlined />, label: 'Sign out', onClick: logout },
              ],
            }}
          >
            <Button type="text" icon={<UserOutlined />}>
              {screens.sm ? user.full_name || user.username : null}
            </Button>
          </Dropdown>
        </Header>
        <Content style={{ padding: screens.md ? 24 : 16 }}>
          <Outlet />
        </Content>
      </Layout>
      <ChangePasswordModal open={pwOpen} onClose={() => setPwOpen(false)} />
    </Layout>
  )
}
