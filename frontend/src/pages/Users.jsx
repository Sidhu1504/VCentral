import { useCallback, useEffect, useState } from 'react'
import { Alert, App, Button, Form, Input, Modal, Popconfirm, Select, Space, Switch, Table, Tag, Typography } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { api } from '../api'
import { useAuth } from '../auth'
import { fmtAgo } from '../format'

const ROLES = [
  { value: 'admin', label: 'Admin: everything, including users and audit log' },
  { value: 'operator', label: 'Operator: install agents, mark critical VMs, edit owners/notes, trigger sync' },
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
        {!isNew && <Form.Item name="is_active" label="Active" valuePropName="checked"><Switch /></Form.Item>}
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
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 16 }} wrap>
        <Typography.Title level={3} style={{ margin: 0 }}>Users</Typography.Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setModal({ open: true, user: null })}>Add user</Button>
      </Space>
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
          { title: 'Role', dataIndex: 'role', render: (r) => <Tag color={r === 'admin' ? 'gold' : r === 'operator' ? 'blue' : 'default'}>{r}</Tag> },
          { title: 'Status', dataIndex: 'is_active', render: (a, u) => (u.pending_approval ? <Tag color="orange">Waiting for approval</Tag> : !a ? <Tag>Disabled</Tag> : u.must_change_password ? <Tag color="warning">Must set password</Tag> : <Tag color="success">Active</Tag>) },
          { title: 'Last sign-in', dataIndex: 'last_login_at', render: fmtAgo },
          {
            title: '', key: 'actions', width: 200,
            render: (_, u) => u.pending_approval ? (
              <Space>
                <Button size="small" type="primary" onClick={() => approve(u)}>Approve</Button>
                <Popconfirm title={`Reject ${u.username}'s sign-up?`} description="The request is deleted." okText="Reject" okButtonProps={{ danger: true }} onConfirm={() => remove(u)}>
                  <Button size="small" danger>Reject</Button>
                </Popconfirm>
              </Space>
            ) : (
              <Space>
                <Button size="small" onClick={() => setModal({ open: true, user: u })}>Edit</Button>
                {u.id !== me.id && (
                  <Popconfirm title={`Delete ${u.username}?`} okText="Delete" okButtonProps={{ danger: true }} onConfirm={() => remove(u)}>
                    <Button size="small" danger>Delete</Button>
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
