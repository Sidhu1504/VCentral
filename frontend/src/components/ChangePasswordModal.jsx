import { App, Form, Input, Modal } from 'antd'
import { api } from '../api'
import { useAuth } from '../auth'

export function PasswordFields() {
  return (
    <>
      <Form.Item name="current_password" label="Current password" rules={[{ required: true }]}>
        <Input.Password autoComplete="current-password" />
      </Form.Item>
      <Form.Item name="new_password" label="New password" rules={[{ required: true, min: 10, message: 'At least 10 characters' }]}>
        <Input.Password autoComplete="new-password" />
      </Form.Item>
      <Form.Item
        name="confirm"
        label="Confirm new password"
        dependencies={['new_password']}
        rules={[
          { required: true },
          ({ getFieldValue }) => ({
            validator: (_, v) => (!v || v === getFieldValue('new_password') ? Promise.resolve() : Promise.reject(new Error('Passwords do not match'))),
          }),
        ]}
      >
        <Input.Password autoComplete="new-password" />
      </Form.Item>
    </>
  )
}

export async function submitPasswordChange(values, setUser) {
  const user = await api.post('/api/auth/change-password', {
    current_password: values.current_password,
    new_password: values.new_password,
  })
  setUser(user)
}

export default function ChangePasswordModal({ open, onClose }) {
  const [form] = Form.useForm()
  const { setUser } = useAuth()
  const { message } = App.useApp()

  const onOk = async () => {
    const values = await form.validateFields()
    try {
      await submitPasswordChange(values, setUser)
      message.success('Password changed')
      form.resetFields()
      onClose()
    } catch (e) {
      message.error(e.message)
    }
  }

  return (
    <Modal title="Change password" open={open} onOk={onOk} onCancel={onClose} okText="Change password" destroyOnClose>
      <Form form={form} layout="vertical" preserve={false}>
        <PasswordFields />
      </Form>
    </Modal>
  )
}
