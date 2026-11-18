import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Alert, App, Button, Card, Col, Progress, Row, Segmented, Space, Statistic, Table, Tag, Tooltip, Typography } from 'antd'
import { SearchOutlined } from '@ant-design/icons'
import { api } from '../api'
import { useAuth } from '../auth'
import { fmtAgo, STATUS } from '../format'
import SetOwnerModal from '../components/SetOwnerModal'
import PageHeader, { HelpLabel } from '../components/PageHeader'
import { OsIcon, PowerTag } from './VMs'

const CONFIDENCE = { high: ['success', 'High'], medium: ['processing', 'Medium'], low: ['default', 'Low'] }
const CONFIDENCE_TIP = {
  high: "A vCenter user's name is in the VM name, folder or notes, and the same user is the most active on the VM.",
  medium: "Either a vCenter user's name is in the VM name, folder or notes, or only one user has used the VM recently.",
  low: 'The most active of several users on the VM. Check the reason before accepting.',
}

export default function Owners() {
  const { can } = useAuth()
  const { message } = App.useApp()
  const [data, setData] = useState(null)
  const [selected, setSelected] = useState([])
  const [view, setView] = useState('suggested')
  const [settingOwner, setSettingOwner] = useState(false)
  const load = useCallback(() => api.get('/api/owners').then(setData, (e) => message.error(e.message)), [message])
  useEffect(() => {
    load()
    const t = setInterval(load, 10000)
    return () => clearInterval(t)
  }, [load])

  if (!data) return null
  const s = data.stats
  const rows = data.items.filter((v) => (view === 'suggested' ? v.suggestion : view === 'none' ? !v.suggestion : true))
  const highIds = data.items.filter((v) => v.confidence === 'high').map((v) => v.id)
  const accept = async (ids) => {
    try {
      const r = await api.post('/api/owners/accept', { vm_ids: ids })
      message.success(`Owner set on ${r.accepted} VM${r.accepted === 1 ? '' : 's'}`)
      setSelected([])
      load()
    } catch (e) {
      message.error(e.message)
    }
  }
  const scan = async () => {
    try {
      await api.post('/api/owners/scan')
      message.info('Looking through vCenter… results appear here in a minute')
      load()
    } catch (e) {
      message.error(e.message)
    }
  }
  const pct = s.total ? Math.round((s.known / s.total) * 100) : 0
  return (
    <>
      <PageHeader title="Owners"
        description={<>
          vCenter only keeps “who created this VM” for about 30 days, so older VMs have no owner. vCentral suggests one from
          what vCenter still knows: who has been using the VM (console, power, settings) and a vCenter user's name in the
          VM name, folder or notes. Check the reason, then accept. You can also set an owner for many VMs at once, or set
          the <code>Owner</code> attribute in vCenter.
        </>}
        extra={can('admin', 'operator') && (
          <Tooltip title="Searches vCenter's event history for suggestions now instead of waiting for the hourly search. Results appear here within about a minute.">
            <Button icon={<SearchOutlined />} loading={data.scan_pending} onClick={scan}>Find owners now</Button>
          </Tooltip>
        )} />
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} md={9}>
          <Card size="small"><Typography.Text type="secondary">VMs with a known owner</Typography.Text>
            <Progress percent={pct} format={() => `${s.known} / ${s.total}`} strokeColor={STATUS.good} />
          </Card>
        </Col>
        <Col xs={8} md={5}><Card size="small"><Statistic title="Owner unknown" value={s.unknown} /></Card></Col>
        <Col xs={8} md={5}><Card size="small"><Statistic title="With a suggestion" value={s.suggested} /></Card></Col>
        <Col xs={8} md={5}><Card size="small"><Statistic title={<HelpLabel tip={CONFIDENCE_TIP.high}>High confidence</HelpLabel>} value={s.confidence.high || 0} valueStyle={{ color: STATUS.good }} /></Card></Col>
      </Row>
      {data.last_scan?.finished_at && (
        <Alert type="info" showIcon style={{ marginBottom: 16 }}
          message={`Last search ${fmtAgo(data.last_scan.finished_at)}: ${data.last_scan.suggested} suggestions from ${data.last_scan.people_seen} vCenter users seen in the event history. It runs every hour.`} />
      )}
      <Card size="small"
        title={<Segmented value={view} onChange={(v) => { setView(v); setSelected([]) }} options={[
          { value: 'suggested', label: `With suggestion (${s.suggested})` },
          { value: 'none', label: `No suggestion (${s.unknown - s.suggested})` },
          { value: 'all', label: `All unknown (${s.unknown})` },
        ]} />}
        extra={can('admin', 'operator') && (
          <Space wrap>
            {highIds.length > 0 && view !== 'none' && (
              <Tooltip title="Sets the suggested owner on every VM with a high-confidence suggestion. You can change any of them later on the VM page.">
                <Button onClick={() => accept(highIds)}>Accept all high confidence ({highIds.length})</Button>
              </Tooltip>
            )}
            <Tooltip title="Sets the suggested owner on the selected VMs. VMs without a suggestion are left unchanged.">
              <Button type="primary" disabled={!selected.length || view === 'none'} onClick={() => accept(selected)}>Accept selected ({selected.length})</Button>
            </Tooltip>
            <Tooltip title="Type one owner for all selected VMs, e.g. a team. Owners set by hand are never changed automatically.">
              <Button disabled={!selected.length} onClick={() => setSettingOwner(true)}>Set owner for selected</Button>
            </Tooltip>
          </Space>
        )}>
        <Table rowKey="id" size="small" dataSource={rows} pagination={{ pageSize: 50, hideOnSinglePage: true, showSizeChanger: false }} scroll={{ x: 1000 }}
          rowSelection={can('admin', 'operator') ? { selectedRowKeys: selected, onChange: setSelected } : undefined}
          locale={{ emptyText: view === 'suggested' ? 'No suggestions yet. Click “Find owners now”.' : view === 'all' ? 'Every VM has a known owner.' : 'Every VM without an owner has a suggestion.' }}
          columns={[
            { title: 'VM', dataIndex: 'name', width: 240, ellipsis: true, render: (v, r) => <Space size={6}><OsIcon family={r.os_family} /><Link to={`/vms/${r.id}`}>{v}</Link></Space> },
            { title: 'Power', dataIndex: 'power_state', width: 80, render: (p) => <PowerTag state={p} /> },
            { title: 'Suggested owner', dataIndex: 'suggestion', width: 230, ellipsis: true, render: (v) => v || <Typography.Text type="secondary">—</Typography.Text> },
            { title: <HelpLabel tip="How strong the evidence for the suggestion is. Hover a value for the rule.">Confidence</HelpLabel>, dataIndex: 'confidence', width: 130, render: (c) => c && <Tooltip title={CONFIDENCE_TIP[c]}><Tag color={CONFIDENCE[c][0]}>{CONFIDENCE[c][1]}</Tag></Tooltip> },
            { title: 'Why', dataIndex: 'reason', ellipsis: { showTitle: false }, render: (r) => r ? <Tooltip title={r}>{r}</Tooltip> : '—' },
            { title: 'Folder', dataIndex: 'folder', width: 160, ellipsis: true },
          ]} />
      </Card>
      {settingOwner && <SetOwnerModal vmIds={selected} onClose={() => setSettingOwner(false)} onDone={() => { setSelected([]); load() }} />}
    </>
  )
}
