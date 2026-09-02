/**
 * PerturbationImpactPanel - visualize how shocks moved KPIs (iter #38)
 *
 * 数据源: GET /api/persistence/perturbation-impact
 *
 * 展示:
 * - Summary KPIs: avg_delta / max_delta / n_cycles_with_perturbation
 * - Line chart: base_seasonal_factor_avg vs seasonal_factor_avg over time
 * - Bar chart: delta (perturbation contribution) per cycle
 *
 * Useful for ops to verify their perturbations are actually having the
 * expected effect (vs being masked by other factors).
 */

import { useState, useEffect } from 'react'
import {
  ResponsiveContainer,
  ComposedChart,
  Line,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ReferenceLine,
} from 'recharts'

import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'
const REFRESH_INTERVAL_MS = 60000

function summarize(cycles, summary) {
  if (!cycles || cycles.length === 0) return null
  return {
    totalCycles: summary.n_cycles_total,
    perturbedCycles: summary.n_cycles_with_perturbation,
    avgDelta: summary.avg_delta ?? 0,
    maxDelta: summary.max_delta ?? 0,
    minDelta: summary.min_delta ?? 0,
    maxMultiplier: summary.max_total_multiplier ?? 1,
    windowStart: summary.window_start,
    windowEnd: summary.window_end,
  }
}

export function PerturbationImpactPanel() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [windowSize, setWindowSize] = useState(60)

  useEffect(() => {
    let cancelled = false
    const fetchData = async () => {
      try {
        const resp = await fetch(
          `${API_BASE}/persistence/perturbation-impact?limit=${windowSize}`
        )
        if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`)
        const json = await resp.json()
        if (!cancelled) {
          setData(json)
          setError(null)
          setLoading(false)
        }
      } catch (e) {
        if (!cancelled) {
          setError(e.message || 'fetch failed')
          setLoading(false)
        }
      }
    }
    fetchData()
    const interval = setInterval(fetchData, REFRESH_INTERVAL_MS)
    return () => { cancelled = true; clearInterval(interval) }
  }, [windowSize])

  if (loading) return <LoadingSpinner label="Loading perturbation impact…" />
  if (error) {
    return (
      <div className="chart-card">
        <div className="chart-card-header">
          <h3>📈 Perturbation Impact (iter #38)</h3>
        </div>
        <div className="error-banner" style={{ marginTop: '0.75rem' }}>⚠️ {error}</div>
      </div>
    )
  }
  if (!data || !data.cycles || data.cycles.length === 0) {
    return (
      <div className="chart-card">
        <div className="chart-card-header">
          <h3>📈 Perturbation Impact (iter #38)</h3>
          <span className="chart-card-sub">No cycle data yet</span>
        </div>
        <div className="empty-state" style={{ marginTop: '0.75rem', color: '#94a3b8' }}>
          Run a simulation cycle to see perturbation impact analysis.
        </div>
      </div>
    )
  }

  const summary = summarize(data.cycles, data.summary)
  const perturbedPct = summary.totalCycles > 0
    ? ((summary.perturbedCycles / summary.totalCycles) * 100).toFixed(0)
    : 0
  const deltaColor = summary.maxDelta > 0.1 ? '#22c55e'
    : summary.minDelta < -0.1 ? '#ef4444' : '#94a3b8'

  // Build chart data: include only cycles with perturbations to keep it readable
  const perturbedCycles = data.cycles.filter(c => c.perturbation_count > 0)
  const chartData = perturbedCycles.length > 0 ? perturbedCycles : data.cycles.slice(-10)

  return (
    <div className="chart-card">
      <div className="chart-card-header">
        <h3>📈 Perturbation Impact (iter #38)</h3>
        <span className="chart-card-sub">
          How active shocks moved supply seasonal factor (base vs effective)
        </span>
      </div>

      {/* Summary KPIs */}
      <div className="kpi-grid" style={{ marginTop: '0.75rem' }}>
        <div className="kpi-card" style={{ borderTop: '3px solid #3b82f6' }}>
          <div className="kpi-label">Cycles analyzed</div>
          <div className="kpi-value">{summary.totalCycles}</div>
        </div>
        <div className="kpi-card" style={{ borderTop: '3px solid #f59e0b' }}>
          <div className="kpi-label">With perturbation</div>
          <div className="kpi-value">
            {summary.perturbedCycles}
            <span className="kpi-unit"> ({perturbedPct}%)</span>
          </div>
        </div>
        <div className="kpi-card" style={{ borderTop: `3px solid ${deltaColor}` }}>
          <div className="kpi-label">Avg delta</div>
          <div className="kpi-value" style={{ color: deltaColor }}>
            {summary.avgDelta >= 0 ? '+' : ''}{summary.avgDelta.toFixed(3)}
          </div>
        </div>
        <div className="kpi-card" style={{ borderTop: '3px solid #8b5cf6' }}>
          <div className="kpi-label">Max multiplier</div>
          <div className="kpi-value">×{summary.maxMultiplier.toFixed(2)}</div>
        </div>
      </div>

      {/* Window size selector */}
      <div style={{ marginTop: '1rem', display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
        <span style={{ color: '#94a3b8', fontSize: '0.85rem' }}>Window:</span>
        {[30, 60, 90, 180].map(n => (
          <button
            key={n}
            type="button"
            onClick={() => setWindowSize(n)}
            className={`forecast-method-btn ${windowSize === n ? 'active' : ''}`}
            style={{
              padding: '0.3rem 0.6rem',
              borderRadius: '4px',
              border: '1px solid #475569',
              background: windowSize === n ? '#3b82f6' : '#1e293b',
              color: windowSize === n ? '#fff' : '#94a3b8',
              cursor: 'pointer',
              fontSize: '0.8rem',
            }}
          >
            {n}
          </button>
        ))}
      </div>

      {/* Base vs effective chart */}
      {chartData.length > 0 && (
        <div className="chart-row" style={{ height: 260, marginTop: '1rem' }}>
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={chartData} margin={{ top: 5, right: 20, left: 50, bottom: 5 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#475569" />
              <XAxis
                dataKey="sim_day"
                stroke="#94a3b8"
                tickFormatter={(v) => `d${v}`}
              />
              <YAxis stroke="#94a3b8" domain={['auto', 'auto']} />
              <Tooltip
                contentStyle={{ background: '#1e293b', border: '1px solid #475569' }}
                formatter={(value, name) => {
                  if (name === 'delta') return [value.toFixed(3), 'Delta (perturbation effect)']
                  if (name === 'base_seasonal_factor_avg') return [value.toFixed(3), 'Base (no shock)']
                  if (name === 'seasonal_factor_avg') return [value.toFixed(3), 'Effective (with shock)']
                  return [value, name]
                }}
              />
              <Legend />
              <ReferenceLine y={1.0} stroke="#64748b" strokeDasharray="2 2" label={{ value: 'baseline', position: 'right', fill: '#64748b', fontSize: 10 }} />
              <Bar dataKey="delta" name="delta" fill="#8b5cf6" fillOpacity={0.5} />
              <Line type="monotone" dataKey="base_seasonal_factor_avg" name="Base" stroke="#22c55e" strokeWidth={2} dot={{ r: 2 }} />
              <Line type="monotone" dataKey="seasonal_factor_avg" name="Effective" stroke="#3b82f6" strokeWidth={2} dot={{ r: 2 }} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Cycle detail table (last few cycles with perturbation) */}
      {perturbedCycles.length > 0 && (
        <div style={{ marginTop: '1rem' }}>
          <h4 style={{ margin: '0 0 0.5rem 0', color: '#cbd5e1', fontSize: '0.9rem' }}>
            Recent cycles with perturbation ({perturbedCycles.length})
          </h4>
          <table style={{ width: '100%', fontSize: '0.8rem', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid #334155', color: '#94a3b8', textAlign: 'left' }}>
                <th style={{ padding: '0.3rem' }}>sim_day</th>
                <th style={{ padding: '0.3rem' }}>Base</th>
                <th style={{ padding: '0.3rem' }}>Effective</th>
                <th style={{ padding: '0.3rem' }}>Δ</th>
                <th style={{ padding: '0.3rem' }}>×</th>
                <th style={{ padding: '0.3rem' }}>offers hit</th>
              </tr>
            </thead>
            <tbody>
              {perturbedCycles.slice(-5).reverse().map(c => (
                <tr key={c.sim_day} style={{ borderBottom: '1px solid #1e293b', color: '#e2e8f0' }}>
                  <td style={{ padding: '0.3rem', fontFamily: 'monospace' }}>{c.sim_day}</td>
                  <td style={{ padding: '0.3rem', color: '#22c55e', fontFamily: 'monospace' }}>{c.base_seasonal_factor_avg.toFixed(3)}</td>
                  <td style={{ padding: '0.3rem', color: '#3b82f6', fontFamily: 'monospace' }}>{c.seasonal_factor_avg.toFixed(3)}</td>
                  <td style={{ padding: '0.3rem', color: c.delta >= 0 ? '#22c55e' : '#ef4444', fontFamily: 'monospace' }}>
                    {c.delta >= 0 ? '+' : ''}{c.delta.toFixed(3)}
                  </td>
                  <td style={{ padding: '0.3rem', color: '#f59e0b', fontFamily: 'monospace' }}>
                    ×{c.perturbation_total_multiplier.toFixed(2)}
                  </td>
                  <td style={{ padding: '0.3rem', color: '#94a3b8' }}>{c.perturbation_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

export default PerturbationImpactPanel
