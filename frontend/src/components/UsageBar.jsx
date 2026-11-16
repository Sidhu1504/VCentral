import { Progress } from 'antd'
import { fmtPct, usageColor } from '../format'

/** Utilisation bar: status colour by threshold, always paired with the number. */
export default function UsageBar({ value, width = 90 }) {
  if (value === null || value === undefined) return <span style={{ opacity: 0.45 }}>—</span>
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, minWidth: width + 44 }}>
      <Progress percent={value} showInfo={false} size={[width, 6]} strokeColor={usageColor(value)} />
      <span style={{ fontVariantNumeric: 'tabular-nums', minWidth: 36, textAlign: 'right' }}>{fmtPct(value)}</span>
    </span>
  )
}
