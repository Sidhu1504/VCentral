import { useEffect, useRef } from 'react'
import * as echarts from 'echarts'
import { useThemeMode } from '../theme'

// Validated reference palette: categorical slots in fixed order, separate light/dark steps.
export const VIZ = {
  light: {
    series: ['#2a78d6', '#eb6834', '#1baf7a'],
    text: '#0b0b0b', textSecondary: '#52514e', muted: '#898781',
    grid: '#e1e0d9', axis: '#c3c2b7', surface: '#ffffff',
  },
  dark: {
    series: ['#3987e5', '#d95926', '#199e70'],
    text: '#f0efec', textSecondary: '#c3c2b7', muted: '#898781',
    grid: '#2c2c2a', axis: '#383835', surface: '#141414',
  },
}

export function useViz() {
  const { dark } = useThemeMode()
  return dark ? VIZ.dark : VIZ.light
}

/** Thin ECharts wrapper: builds options from the current theme and resizes with its container. */
export default function Chart({ option, height = 260 }) {
  const ref = useRef(null)
  const chart = useRef(null)

  useEffect(() => {
    chart.current = echarts.init(ref.current, null, { renderer: 'svg' })
    const ro = new ResizeObserver(() => chart.current?.resize())
    ro.observe(ref.current)
    return () => {
      ro.disconnect()
      chart.current?.dispose()
      chart.current = null
    }
  }, [])

  useEffect(() => {
    chart.current?.setOption(option, { notMerge: true })
  }, [option])

  return <div ref={ref} style={{ width: '100%', height }} role="img" />
}

/** Shared axis/tooltip styling so every chart has recessive chrome. */
export function baseOption(viz) {
  return {
    backgroundColor: 'transparent',
    textStyle: { fontFamily: 'system-ui, -apple-system, "Segoe UI", sans-serif', color: viz.textSecondary },
    grid: { left: 8, right: 16, top: 36, bottom: 8, containLabel: true },
    legend: { top: 0, left: 0, icon: 'roundRect', itemWidth: 12, itemHeight: 8, textStyle: { color: viz.textSecondary } },
    tooltip: {
      backgroundColor: viz.surface, borderColor: viz.axis, textStyle: { color: viz.text },
      axisPointer: { lineStyle: { color: viz.muted } },
    },
  }
}

export function axisStyle(viz) {
  return {
    axisLine: { lineStyle: { color: viz.axis } },
    axisTick: { show: false },
    axisLabel: { color: viz.muted },
    splitLine: { lineStyle: { color: viz.grid } },
  }
}
