import React, { useEffect, useRef } from 'react'
import * as echarts from 'echarts'

interface ReplayStats {
  tools: string[]
  buckets: string[]
  heat: number[][]
  trend: { ok: number[]; failed: number[]; rejected: number[] }
  latency: { x: number[]; ms: number[] }
  summary: {
    total: number
    ok: number
    failed: number
    rejected: number
    totalLatencyMs: number
    avgLatencyMs: number
    tools: Record<string, number>
  }
}

/**
 * P5：操作回放可视化（工具 × 时间桶热力图 + 状态趋势多系列折线）。
 */
const ReplayVisual: React.FC<{ stats: ReplayStats }> = ({ stats }) => {
  const heatRef = useRef<HTMLDivElement>(null)
  const trendRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!heatRef.current) return
    const chart = echarts.init(heatRef.current)
    const tools = stats.tools || []
    const buckets = stats.buckets || []
    chart.setOption({
      title: { text: '工具调用热力图（时间桶 × 工具）', textStyle: { fontSize: 11, color: 'var(--text-dim)' }, left: 8, top: 4 },
      tooltip: {
        formatter: (p: any) =>
          `${tools[p.value[0]] || ''} · ${buckets[p.value[1]] || ''}<br/>调用 ${p.value[2]} 次`,
      },
      grid: { left: 90, right: 16, top: 34, bottom: 40 },
      xAxis: {
        type: 'category',
        data: buckets,
        axisLabel: { fontSize: 9, color: 'var(--text-faint)', rotate: 30 },
        splitArea: { show: true },
      },
      yAxis: {
        type: 'category',
        data: tools,
        axisLabel: { fontSize: 9, color: 'var(--text-dim)' },
        splitArea: { show: true },
      },
      visualMap: {
        min: 0,
        max: Math.max(1, ...(stats.heat || []).map((c) => c[2])),
        calculable: false,
        orient: 'horizontal',
        left: 'center',
        bottom: 2,
        inRange: { color: ['#f5f7fb', '#69b1ff', '#1668dc'] },
        itemWidth: 10,
        itemHeight: 90,
      },
      series: [{
        type: 'heatmap',
        data: (stats.heat || []).map((c) => [c[1], c[0], c[2]]),
        label: { show: true, fontSize: 9, color: 'var(--text-dim)' },
        emphasis: { itemStyle: { shadowBlur: 6, shadowColor: 'rgba(0,0,0,0.3)' } },
      }],
    })
    const onResize = () => chart.resize()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      chart.dispose()
    }
  }, [stats])

  useEffect(() => {
    if (!trendRef.current) return
    const chart = echarts.init(trendRef.current)
    const t = stats.trend
    const x = (t?.ok || []).map((_, i) => i + 1)
    chart.setOption({
      title: { text: '调用状态趋势（累计）', textStyle: { fontSize: 11, color: 'var(--text-dim)' }, left: 8, top: 4 },
      tooltip: { trigger: 'axis' },
      legend: { data: ['成功', '失败', '拒绝'], right: 8, top: 4, textStyle: { fontSize: 10 } },
      grid: { left: 36, right: 12, top: 34, bottom: 28 },
      xAxis: { type: 'category', data: x, name: '步', nameTextStyle: { fontSize: 9, color: 'var(--text-faint)' }, axisLabel: { fontSize: 9, color: 'var(--text-faint)' } },
      yAxis: { type: 'value', minInterval: 1, axisLabel: { fontSize: 9, color: 'var(--text-faint)' } },
      series: [
        { name: '成功', type: 'line', data: t?.ok || [], smooth: true, showSymbol: false, lineStyle: { width: 2, color: 'var(--ok)' }, itemStyle: { color: 'var(--ok)' } },
        { name: '失败', type: 'line', data: t?.failed || [], smooth: true, showSymbol: false, lineStyle: { width: 2, color: 'var(--danger)' }, itemStyle: { color: 'var(--danger)' } },
        { name: '拒绝', type: 'line', data: t?.rejected || [], smooth: true, showSymbol: false, lineStyle: { width: 2, color: 'var(--warn)' }, itemStyle: { color: 'var(--warn)' } },
      ],
    })
    const onResize = () => chart.resize()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      chart.dispose()
    }
  }, [stats])

  const s = stats.summary
  const toolTop = Object.entries(s?.tools || {}).slice(0, 3)
    .map(([k, v]) => `${k}×${v}`).join(' · ')

  return (
    <div className="mt-2 space-y-2">
      {s && (
        <div className="text-xs p-2 rounded" style={{ background: 'var(--code-bg)' }}>
          <span style={{ color: 'var(--text-dim)' }}>
            共 {s.total} 步 · 成功 {s.ok} · 失败 {s.failed} · 拒绝 {s.rejected}
          </span>
          <span className="mx-2" style={{ color: 'var(--text-faint)' }}>|</span>
          <span style={{ color: 'var(--text-dim)' }}>总耗时 {s.totalLatencyMs}ms · 均值 {s.avgLatencyMs}ms</span>
          {toolTop && (
            <>
              <span className="mx-2" style={{ color: 'var(--text-faint)' }}>|</span>
              <span style={{ color: 'var(--accent)' }}>{toolTop}</span>
            </>
          )}
        </div>
      )}
      <div ref={heatRef} style={{ width: '100%', height: 180 }} />
      <div ref={trendRef} style={{ width: '100%', height: 150 }} />
    </div>
  )
}

export default ReplayVisual
