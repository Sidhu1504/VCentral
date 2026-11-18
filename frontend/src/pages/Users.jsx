import { useCallback, useEffect, useState } from 'react'
import { Alert, App, Button, Form, Input, Modal, Popconfirm, Select, Space, Switch, Table, Tag, Tooltip } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { api } from '../api'
import { useAuth } from '../auth'
import { fmtAgo } from '../format'
import PageHeader from '../components/PageHeader'

const ROLE_TIP = {
  admin: 'Admin: everything, including users, settings, the audit log, and writing or scheduling scripts.',
  operator: 'Operator: runs library scripts, installs and manages agents, connects to VMs, edits owners and notes, marks critical VMs.',
  viewer: 'Viewer: read-only. Sees VMs, IPs, owners and agent inventory, but not task scripts or output.',
}

const ROLES = [
  { value: 'admin', label: 'Admin: everything, including users and audit log' },
  { value: 'operator', label: 'Operator: run library scripts, install and manage agents, connect to VMs, edit owners/notes, mark critical VMs' },
  { value: 'viewer', label: 'Viewer: read-only' },
]

function UserModal({ user, open, onClose, onSaved }) {
  const [form] = Form.useForm()
  const { message } = App.useApp()
  const isNew = !user

  const onOk = async () => {
    const values = await form.validateFields()
    try {
      if (isNew) await api.post('/api/users', values)
      else {
        const body = { ...values }
        if (!body.password) delete body.password
        if (!body.email) body.email = null
        await api.patch(`/api/users/${user.id}`, body)
      }
      message.success(isNew ? 'User created' : 'User updated')
      onSaved()
      onClose()
    } catch (e) {
      message.error(e.message)
    }
  }

  return (
    <Modal title={isNew ? 'Add user' : `Edit ${user.username}`} open={open} onOk={onOk} onCancel={onClose} destroyOnClose okText="Save">
      <Form form={form} layout="vertical" preserve={false}
        initialValues={user ? { full_name: user.full_name, email: user.email, role: user.role, is_active: user.is_active } : { role: 'viewer' }}>
        {isNew && (
          <Form.Item name="username" label="Username" rules={[{ required: true }, { pattern: /^[A-Za-z0-9._@-]{2,64}$/, message: 'Letters, numbers, . _ @ - only' }]}>
            <Input autoComplete="off" />
          </Form.Item>
        )}
        <Form.Item name="full_name" label="Full name"><Input /></Form.Item>
        <Form.Item name="email" label="Email" rules={[{ type: 'email' }]}><Input /></Form.Item>
        <Form.Item name="role" label="Role" rules={[{ required: true }]}><Select options={ROLES} /></Form.Item>
        {!isNew && (
          <Form.Item name="is_active" label="Active" valuePropName="checked"
            tooltip="Turn off to block sign-in without deleting the account. Changing the role, this switch or the password signs the user out everywhere.">
            <Switch />
          </Form.Item>
        )}
        <Form.Item
          name="password" label={isNew ? 'Temporary password' : 'Reset password'}
          extra="The user must choose a new password at next sign-in."
          rules={[{ required: isNew }, { min: 10, message: 'At least 10 characters' }]}
        >
          <Input.Password autoComplete="new-password" placeholder={isNew ? '' : 'Leave empty to keep current password'} />
        </Form.Item>
      </Form>
    </Modal>
  )
}

export default function Users() {
  const { user: me } = useAuth()
  const { message } = App.useApp()
  const [users, setUsers] = useState([])
  const [loading, setLoading] = useState(false)
  const [modal, setModal] = useState({ open: false, user: null })

  const load = useCallback(() => {
    setLoading(true)
    api.get('/api/users').then(setUsers, (e) => message.error(e.message)).finally(() => setLoading(false))
  }, [message])
  useEffect(load, [load])

  const approve = async (u) => {
    try {
      await api.post(`/api/users/${u.id}/approve`)
      message.success(`${u.username} can now sign in (Viewer)`)
      load()
    } catch (e) {
      message.error(e.message)
    }
  }
  const pending = users.filter((u) => u.pending_approval)

  const remove = async (u) => {
    try {
      await api.del(`/api/users/${u.id}`)
      message.success('User deleted')
      load()
    } catch (e) {
      message.error(e.message)
    }
  }

  return (
    <>
      <PageHeader title="Users"
        description="Who can sign in to vCentral and what they may do. Approve sign-up requests, change roles, reset passwords or disable accounts here."
        extra={<Button type="primary" icon={<PlusOutlined />} onClick={() => setModal({ open: true, user: null })}>Add user</Button>} />
      {pending.length > 0 && (
        <Alert type="warning" showIcon style={{ marginBottom: 16 }}
          message={`${pending.length} sign-up request${pending.length === 1 ? '' : 's'} waiting for approval`}
          description="People who signed up on the login page. Approved accounts get the Viewer role (read-only); you can change the role afterwards with Edit. Reject deletes the request." />
      )}
      <Table
        rowKey="id" size="small" loading={loading} dataSource={[...pending, ...users.filter((u) => !u.pending_approval)]} pagination={false} scroll={{ x: 800 }}
        columns={[
          { title: 'Username', dataIndex: 'username' },
          { title: 'Name', dataIndex: 'full_name' },
          { title: 'Email', dataIndex: 'email' },
          { title: 'Role', dataIndex: 'role', render: (r) => <Tooltip title={ROLE_TIP[r]}><Tag color={r === 'admin' ? 'gold' : r === 'operator' ? 'blue' : 'default'}>{r}</Tag></Tooltip> },
          {
            title: 'Status', dataIndex: 'is_active',
            render: (a, u) => (u.pending_approval
              ? <Tooltip title="Signed up on the login page and cannot sign in until an admin approves."><Tag color="orange">Waiting for approval</Tag></Tooltip>
              : !a ? <Tooltip title="Cannot sign in. Edit the user and turn Active back on to allow it."><Tag>Disabled</Tag></Tooltip>
                : u.must_change_password ? <Tooltip title="Has a temporary password and must choose a new one at the next sign-in."><Tag color="warning">Must set password</Tag></Tooltip>
                  : <Tag color="success">Active</Tag>),
          },
          { title: 'Last sign-in', dataIndex: 'last_login_at', render: fmtAgo },
          {
            title: '', key: 'actions', width: 200,
            render: (_, u) => u.pending_approval ? (
              <Space>
                <Tooltip title="Lets this person sign in as a Viewer (read-only). Change the role afterwards with Edit if needed.">
                  <Button size="small" type="primary" onClick={() => approve(u)}>Approve</Button>
                </Tooltip>
                <Popconfirm title={`Reject ${u.username}'s sign-up?`} description="The request is deleted." okText="Reject" okButtonProps={{ danger: true }} onConfirm={() => remove(u)}>
                  <Tooltip title="Deletes this sign-up request. The person can sign up again while sign-up is allowed.">
                    <Button size="small" danger>Reject</Button>
                  </Tooltip>
                </Popconfirm>
              </Space>
            ) : (
              <Space>
                <Tooltip title="Change name, email or role, disable the account, or reset the password.">
                  <Button size="small" onClick={() => setModal({ open: true, user: u })}>Edit</Button>
                </Tooltip>
                {u.id !== me.id && (
                  <Popconfirm title={`Delete ${u.username}?`} description="Their past actions stay in the audit log." okText="Delete" okButtonProps={{ danger: true }} onConfirm={() => remove(u)}>
                    <Tooltip title="Removes the account permanently. To block sign-in but keep the account, use Edit and turn off Active.">
                      <Button size="small" danger>Delete</Button>
                    </Tooltip>
                  </Popconfirm>
                )}
              </Space>
            ),
          },
        ]}
      />
      {modal.open && <UserModal open user={modal.user} onClose={() => setModal({ open: false, user: null })} onSaved={load} />}
    </>
  )
}
