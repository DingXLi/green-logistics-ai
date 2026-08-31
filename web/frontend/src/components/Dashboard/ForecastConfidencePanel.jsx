/**
 * ForecastConfidencePanel - Multi-method forecast confidence (iter #30)
 *
 * 数据源: GET /api/persistence/forecast-confidence
 */

import { useState, useEffect } from 'react'
import {
  ResponsiveContainer,
  ComposedChart,
  Line,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from 'recharts'
import { LoadingSpinner } from '../common/LoadingSpinner'
import { useUrlState } from '../../hooks/useUrlState'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'
const METHODS = [
  { key: 'linear', label: 'Linear' },
  { key: 'moving_average', label: 'Moving Avg' },
  { key: 'exponential_smoothing', label: 'Exp. Smoothing' },
]
const METRICS = [
  { key: 'cost_sek', label: '💰 Cost' },
  { key: 'co2_kg', label: '🌱 CO₂' },
  { key: 'util_pct', label: '🚛 Util' },
  { key: 'matches', label: '🤝 Matches' },
]

function Kpi({ label, value, unit, accent }) {
  return <div className="kpi-card" style={{ borderTop: `3px solid ${accent}` }}>
    <div className="kpi-label">{label}</div>
    <div className="kpi-value">{value}{unit && <span className="kpi-unit">{unit}</span>}</div>
  </div>
}

export function ForecastConfidencePanel() {
  const [metric] = useUrlState('confidence_metric', 'cost_sek', 'str')
  const [horizon] = useUrlState('confidence_horizon', 7, 'int')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    const fetchData = async () => {
      try {
        const methods = METHODS.map(m => m.key).join(',')
        const resp = await fetch(
          `${API_BASE}/persistence/forecast-confidence?horizon=${horizon}&metrics=${metric}&methods=${methods}`
        )
        if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`)
        if (!cancelled) { setData(await resp.json()); setError(null) }
      } catch (e) {
        if (!cancelled) setError(e.message || 'fetch failed')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    fetchData()
    const interval = setInterval(fetchData, 60000)
    return () => { cancelled = true; clearInterval(interval) }
  }, [horizon, metric])

  if (loading) return <LoadingSpinner label="Loading forecast confidence…" />
  if (error) return <div className="chart-card"><h3>📐 Forecast Confidence</h3><div className="error-banner">⚠️ {error}</div></div>
  if (!data) return null

  const item = data.confidence?.[metric]
  if (!item) return <div className="chart-card"><h3>📐 Forecast Confidence</h3><div className="empty-state">No confidence data available.</div></div>
  const chartData = item.forecast || []
  const avgDispersion = chartData.length
    ? chartData.reduce((s, p) => s + (p.dispersion_pct || 0), 0) / chartData.length
    : 0
  const final = chartData[chartData.length - 1]
  const methodLabel = item.best_method

  return (
    <div className="chart-card">
      <div className="chart-card-header">
        <h3>📐 Forecast Confidence (iter #30)</h3>
        <span className="chart-card-sub">Ensemble of {data.methods.length} methods · {horizon}-day horizon</span>
      </div>
      <div className="forecast-method-selector" style={{ marginTop: '0.75rem', display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
        <span style={{ color: '#94a3b8', fontSize: '0.9rem', alignSelf: 'center' }}>Metric:</span>
        {METRICS.map(m => (
          <button key={m.key} type="button" className={`forecast-method-btn ${metric === m.key ? 'active' : ''}`}
            onClick={() => {
              const url = new URL(window.location.href)
              url.searchParams.set('confidence_metric', m.key)
              window.history.pushState({}, '', url.toString())
              window.dispatchEvent(new Event('popstate'))
            }}
            style={{ padding: '0.4rem 0.8rem', borderRadius: '4px', border: '1px solid #475569', background: metric === m.key ? '#3b82f6' : '#1e293b', color: metric === m.key ? '#fff' : '#94a3b8', cursor: 'pointer', fontSize: '0.85rem' }}>
            {m.label}
          </button>
        ))}
      </div>
      <div className="kpi-grid" style={{ marginTop: '1rem' }}>
        <Kpi label="Ensemble final" value={final ? final.mean : '—'} unit="" accent="#3b82f6" />
        <Kpi label="95% range" value={final ? `${final.lower_95} → ${final.upper_95}` : '—'} unit="" accent="#8b5cf6" />
        <Kpi label="Avg dispersion" value={`${avgDispersion.toFixed(2)}%`} unit="" accent={avgDispersion > 20 ? '#ef4444' : '#22c55e'} />
        <Kpi label="Best method" value={methodLabel || '—'} unit="" accent="#f59e0b" />
      </div>
      {chartData.length > 0 && <div className="chart-row" style={{ height: 250, marginTop: '1rem' }}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={chartData} margin={{ top: 5, right: 30, left: 60, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#475569" />
            <XAxis dataKey="sim_day" stroke="#94a3b8" />
            <YAxis stroke="#94a3b8" />
            <Tooltip contentStyle={{ background: '#1e293b', border: '1px solid #475569' }} />
            <Legend />
            <Area type="monotone" dataKey="upper_95" name="Upper 95%" stroke="none" fill="#8b5cf6" fillOpacity={0.15} />
            <Area type="monotone" dataKey="lower_95" name="Lower 95%" stroke="none" fill="#8b5cf6" fillOpacity={0.15} />
            <Line type="monotone" dataKey="mean" name="Ensemble mean" stroke="#3b82f6" strokeWidth={2} dot={{ r: 3 }} />
            <Line type="monotone" dataKey="dispersion_pct" name="Dispersion %" stroke="#f59e0b" strokeDasharray="4 4" yAxisId={0} dot={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>}
    </div>
  )
}

export default ForecastConfidencePanel
