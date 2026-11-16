import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { App as AntApp, ConfigProvider, Spin, theme } from 'antd'
import { AuthProvider, useAuth } from './auth'
import { ThemeModeProvider, useThemeMode } from './theme'
import AppLayout from './components/AppLayout'
import Login from './pages/Login'
import ForcePasswordChange from './pages/ForcePasswordChange'
import Dashboard from './pages/Dashboard'
import VMs from './pages/VMs'
import VMDetail from './pages/VMDetail'
import Network from './pages/Network'
import Agents from './pages/Agents'
import Activity from './pages/Activity'
import Audit from './pages/Audit'
import Users from './pages/Users'
import Settings from './pages/Settings'
import Onboarding from './pages/Onboarding'
import Tasks from './pages/Tasks'
import TaskDetail from './pages/TaskDetail'
import Scripts from './pages/Scripts'
import Schedules from './pages/Schedules'
import Owners from './pages/Owners'

function Root() {
  const { user, can } = useAuth()
  if (user === undefined) return <Spin fullscreen />
  if (user === null) return <Login />
  if (user.must_change_password) return <ForcePasswordChange />
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<Dashboard />} />
        <Route path="vms" element={<VMs />} />
        <Route path="vms/:id" element={<VMDetail />} />
        <Route path="network" element={<Network />} />
        <Route path="agents" element={<Agents />} />
        <Route path="onboarding" element={<Onboarding />} />
        <Route path="owners" element={<Owners />} />
        <Route path="tasks" element={<Tasks />} />
        <Route path="tasks/:id" element={<TaskDetail />} />
        {can('admin', 'operator') && <Route path="scripts" element={<Scripts />} />}
        {can('admin', 'operator') && <Route path="schedules" element={<Schedules />} />}
        <Route path="activity" element={<Activity />} />
        {can('admin') && <Route path="audit" element={<Audit />} />}
        {can('admin') && <Route path="users" element={<Users />} />}
        {can('admin', 'operator') && <Route path="settings" element={<Settings />} />}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}

function Themed() {
  const { dark } = useThemeMode()
  return (
    <ConfigProvider
      theme={{
        algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm,
        token: { colorPrimary: dark ? '#3987e5' : '#2a78d6', borderRadius: 6 },
      }}
    >
      <AntApp>
        <BrowserRouter>
          <AuthProvider>
            <Root />
          </AuthProvider>
        </BrowserRouter>
      </AntApp>
    </ConfigProvider>
  )
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ThemeModeProvider>
      <Themed />
    </ThemeModeProvider>
  </React.StrictMode>,
)
