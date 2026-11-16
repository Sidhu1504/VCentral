import { Alert, Space, Tag, Tooltip, Typography } from 'antd'
import { CheckCircleFilled, CloseCircleFilled, ExclamationCircleFilled, StarFilled } from '@ant-design/icons'
import { fmtAgo, HEALTH, STATUS } from '../format'

const ICON = {
  critical: <CloseCircleFilled style={{ color: STATUS.critical }} />,
  warning: <ExclamationCircleFilled style={{ color: STATUS.warning }} />,
  ok: <CheckCircleFilled style={{ color: STATUS.good }} />,
}

/** Health with icon + label (never colour alone); tooltip lists the problems. */
export function HealthTag({ health, problems = [], showOk = false }) {
  if (!health || (health === 'ok' && !showOk)) return <Typography.Text type="secondary">—</Typography.Text>
  const h = HEALTH[health] || HEALTH.ok
  const tip = problems.length ? problems.map((p) => p.message).join(' · ') : null
  return (
    <Tooltip title={tip}>
      <Tag color={h.color} icon={ICON[health]} style={{ marginInlineEnd: 0 }}>
        {h.label}{problems.length > 1 ? ` (${problems.length})` : ''}
      </Tag>
    </Tooltip>
  )
}

export function CriticalStar({ critical }) {
  if (!critical) return null
  return (
    <Tooltip title="Critical VM: must always be up">
      <StarFilled style={{ color: STATUS.serious }} aria-label="Critical VM" />
    </Tooltip>
  )
}

/** One alert per problem, most severe first. */
export function ProblemAlerts({ problems }) {
  if (!problems?.length) return null
  const sorted = [...problems].sort((a, b) => (a.severity === 'critical' ? -1 : 1) - (b.severity === 'critical' ? -1 : 1))
  return (
    <Space direction="vertical" style={{ width: '100%', marginBottom: 16 }}>
      {sorted.map((p) => (
        <Alert key={p.code} showIcon type={p.severity === 'critical' ? 'error' : 'warning'}
          message={<><b>{HEALTH[p.severity]?.label}:</b> {p.message}</>}
          description={p.since ? `Since ${fmtAgo(p.since)}` : null} />
      ))}
    </Space>
  )
}
