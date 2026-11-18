import { Space, Tooltip, Typography } from 'antd'
import { QuestionCircleOutlined } from '@ant-design/icons'

/** Page title, one line on what the page is for, and the page's main buttons on the right. */
export default function PageHeader({ title, description, extra }) {
  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
        <Typography.Title level={3} style={{ margin: 0 }}>{title}</Typography.Title>
        {extra && <Space wrap>{extra}</Space>}
      </div>
      {description && <Typography.Paragraph type="secondary" style={{ margin: '6px 0 0', maxWidth: 1100 }}>{description}</Typography.Paragraph>}
    </div>
  )
}

/** A label (column header, card title, statistic) with a small help icon that explains it on hover. */
export function HelpLabel({ children, tip }) {
  return (
    <Space size={4}>
      {children}
      <Tooltip title={tip}>
        <QuestionCircleOutlined style={{ opacity: 0.45, cursor: 'help', fontSize: '0.9em' }} aria-label="What is this?" />
      </Tooltip>
    </Space>
  )
}
