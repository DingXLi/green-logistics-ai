/**
 * LlmCostForecastPanel - LLM usage/cost forecast (iter #29)
 *
 * 数据源: GET /api/persistence/llm-cost-forecast
 *
 * 显示:
 * - Method selector (linear / moving_average / exponential_smoothing)
 * - KPI: forecast decisions / LLM calls / fallback / avg confidence
 * - Line chart: n_decisions / llm_n / fallback_n (future sim_days)
 */

import { useState, useEffect } from 'react'
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from 'recharts'

import { LoadingSpinner } from '../common/LoadingSpinner'
import { useUrlState } from '../../hooks/useUrlState'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'
const REFRESH_INTERVAL_MS = 60000
const METHODS = [
  { key: 'linear', label: '📈 Linear' },
  { key: 'moving_average', label: '➡️ Moving Avg' },
  { key: 'exponential_smoothing', label: '🌀 Exp. Smoothing' },
]

function Kpi({ label, value, unit, accent }) {
  return (
    <div className="kpi-card" style={{ borderTop: `3px solid ${accent}` }}>
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{value}{unit && <span className="kpi-unit">{unit}</span>}</div>
    </div>
  )
}

export function LlmCostForecastPanel() {
  const [method] = useUrlState('llm_forecast_method', 'linear', 'str')
  const [horizon] = useUrlState('llm_forecast_horizon', 7, 'int')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    const fetchForecast = async () => {
      try {
        const resp = await fetch(
          `${API_BASE}/persistence/llm-cost-forecast?horizon=${horizon}&method=${method}`
        )
        if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`)
        if (!cancelled) { setData(await resp.json()); setError(null) }
      } catch (e) {
        if (!cancelled) setError(e.message || 'fetch failed')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    fetchForecast()
    const interval = setInterval(fetchForecast, REFRESH_INTERVAL_MS)
    return () => { cancelled = true; clearInterval(interval) }
  }, [horizon, method])

  if (loading) return <LoadingSpinner label="Loading LLM cost forecast…" />
  if (error) return <div className="chart-card"><h3>🔮 LLM Cost Forecast (iter #29)</h3><div className="error-banner">⚠️ {error}</div></div>
  if (!data) return null

  const metrics = data.metrics || {}
  const chartData = (metrics.n_decisions?.forecast || []).map((point, index) => ({
    sim_day: point.sim_day,
    decisions: point.value,
    llm: metrics.llm_n?.forecast?.[index]?.value ?? null,
    fallback: metrics.fallback_n?.forecast?.[index]?.value ?? null,
  }))
  const sumMetric = (name) => (metrics[name]?.forecast || []).reduce((s, p) => s + (p.value || 0), 0)
  const avgConfidence = metrics.avg_confidence?.forecast?.length
    ? metrics.avg_confidence.forecast.reduce((s, p) => s + p.value, 0) / metrics.avg_confidence.forecast.length
    : 0
  const methodLabel = METHODS.find(m => m.key === method)?.label || method

  return (
    <div className="chart-card">
      <div className="chart-card-header">
        <h3>🔮 LLM Cost Forecast (iter #29)</h3>
        <span className="chart-card-sub">
          {methodLabel} · {horizon}-sim_day horizon · 95% CI available in API
        </span>
      </div>

      <div className="forecast-method-selector" style={{ marginTop: '1rem', display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
        <span className="forecast-control-label" style={{ alignSelf: 'center', color: '#94a3b8', fontSize: '0.9rem' }}>Method:</span>
        {METHODS.map(m => (
          <button
            key={m.key}
            type="button"
            className={`forecast-method-btn ${method === m.key ? 'active' : ''}`}
            onClick={() => {
              const url = new URL(window.location.href)
              url.searchParams.set('llm_forecast_method', m.key)
              window.history.pushState({}, '', url.toString())
              window.dispatchEvent(new Event('popstate'))
            }}
            style={{ padding: '0.4rem 0.8rem', borderRadius: '4px', border: '1px solid #475569', background: method === m.key ? '#3b82f6' : '#1e293b', color: method === m.key ? '#fff' : '#94a3b8', cursor: 'pointer', fontSize: '0.85rem' }}
          >
            {m.label}
          </button>
        ))}
      </div>

      {data.note && <div className="info-banner" style={{ marginTop: '0.75rem' }}>ℹ️ {data.note}</div>}

      <div className="kpi-grid" style={{ marginTop: '1rem' }}>
        <Kpi label="Forecast decisions" value={sumMetric('n_decisions').toFixed(0)} unit="" accent="#3b82f6" />
        <Kpi label="LLM calls" value={sumMetric('llm_n').toFixed(0)} unit="" accent="#22c55e" />
        <Kpi label="Fallback" value={sumMetric('fallback_n').toFixed(0)} unit="" accent="#f59e0b" />
        <Kpi label="Avg confidence" value={avgConfidence.toFixed(2)} unit="" accent="#8b5cf6" />
      </div>

      {chartData.length > 0 ? (
        <div className="chart-row" style={{ height: 250, marginTop: '1rem' }}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 5, right: 30, left: 60, bottom: 5 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#475569" />
              <XAxis dataKey="sim_day" stroke="#94a3b8" />
              <YAxis stroke="#94a3b8" />
              <Tooltip contentStyle={{ background: '#1e293b', border: '1px solid #475569' }} />
              <Legend />
              <Line type="monotone" dataKey="decisions" name="Decisions" stroke="#3b82f6" strokeWidth={2} dot={{ r: 3 }} />
              <Line type="monotone" dataKey="llm" name="LLM" stroke="#22c55e" strokeWidth={2} dot={{ r: 3 }} />
              <Line type="monotone" dataKey="fallback" name="Fallback" stroke="#f59e0b" strokeWidth={2} dot={{ r: 3 }} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <div className="empty-state">🔮 Need at least 2 LLM decision days to forecast.</div>
      )}
    </div>
  )
}

export default LlmCostForecastPanel
