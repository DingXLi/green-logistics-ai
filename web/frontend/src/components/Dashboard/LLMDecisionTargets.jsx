/**
 * LLMDecisionTargets.jsx — iter #49
 *
 * Per-target LLM call stats panel. Shows which DEM/SUP targets
 * get the most LLM attention.
 *
 * Data source: GET /api/persistence/llm-decision-targets
 *
 * Renders:
 * - Sortable table: target_id / decision_type / n_calls / n_llm / n_fallback
 * - Color-coded LLM rate bar
 * - Avg multiplier + confidence
 * - Optional decision_type filter
 * - 60s auto-refresh
 */
import { useState, useEffect, useMemo } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'
const REFRESH_INTERVAL_MS = 60000

function llmRateColor(pct) {
  if (pct == null) return '#94a3b8'
  if (pct >= 80) return '#10b981'
  if (pct >= 50) return '#facc15'
  if (pct >= 20) return '#f59e0b'
  return '#ef4444'
}

function trendIcon(value) {
  if (value == null) return '—'
  if (value >= 1.2) return '📈'
  if (value <= 0.8) return '📉'
  return '➖'
}

export function LLMDecisionTargets() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [decisionType, setDecisionType] = useState('')
  const [limit, setLimit] = useState(50)
  const [sortKey, setSortKey] = useState('n_calls')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    const params = new URLSearchParams()
    if (decisionType) params.set('decision_type', decisionType)
    params.set('limit', String(limit))
    const url = `${API_BASE}/persistence/llm-decision-targets?${params.toString()}`
    fetch(url)
      .then(r => r.json())
      .then(d => {
        if (!cancelled) {
          setData(d)
          setLoading(false)
        }
      })
      .catch(e => {
        if (!cancelled) {
          setError(e.message)
          setLoading(false)
        }
      })
    return () => { cancelled = true }
  }, [decisionType, limit])

  // Auto-refresh every 60s
  useEffect(() => {
    const id = setInterval(() => {
      const params = new URLSearchParams()
      if (decisionType) params.set('decision_type', decisionType)
      params.set('limit', String(limit))
      const url = `${API_BASE}/persistence/llm-decision-targets?${params.toString()}`
      fetch(url)
        .then(r => r.json())
        .then(d => setData(d))
        .catch(e => setError(e.message))
    }, REFRESH_INTERVAL_MS)
    return () => clearInterval(id)
  }, [decisionType, limit])

  const sorted = useMemo(() => {
    if (!data?.targets) return []
    const arr = [...data.targets]
    arr.sort((a, b) => {
      const av = a[sortKey] ?? 0
      const bv = b[sortKey] ?? 0
      if (typeof av === 'string') return av.localeCompare(bv)
      return bv - av
    })
    return arr
  }, [data, sortKey])

  if (loading && !data) return <LoadingSpinner label="loading LLM decision targets..." />
  if (error) return <div className="error-banner">⚠️ {error}</div>
  if (!data) return <div className="empty">No data available.</div>

  const { targets, n_targets } = data
  const totalCalls = targets.reduce((s, r) => s + (r.n_calls || 0), 0)
  const totalLlm = targets.reduce((s, r) => s + (r.n_real_llm || 0), 0)
  const overallRate = totalCalls > 0 ? (totalLlm / totalCalls * 100) : 0

  return (
    <div className="chart-card">
      <h3>🎯 LLM Decision Targets <span className="iter-badge">iter #49</span></h3>
      <p className="chart-subtitle">
        Which DEM/SUP targets get the most LLM attention?
        Useful for diagnosing LLM cost concentration on specific nodes.
      </p>

      <div className="ldt-controls">
        <label className="ldt-label">
          Decision type:
          <select
            className="ldt-select"
            value={decisionType}
            onChange={e => setDecisionType(e.target.value)}
          >
            <option value="">(all)</option>
            <option value="demand_prediction">demand_prediction</option>
            <option value="supply_prediction">supply_prediction</option>
            <option value="supply_collection">supply_collection</option>
          </select>
        </label>
        <label className="ldt-label">
          Limit:
          <input
            type="number"
            className="ldt-input"
            value={limit}
            onChange={e => setLimit(Math.max(1, Math.min(500, parseInt(e.target.value) || 50)))}
            style={{ width: '70px' }}
          />
        </label>
      </div>

      <div className="cs-kpi-row">
        <div className="cs-kpi">
          <div className="kpi-label">Targets</div>
          <div className="kpi-value">{n_targets}</div>
          <div className="kpi-sub">unique IDs</div>
        </div>
        <div className="cs-kpi">
          <div className="kpi-label">Total Calls</div>
          <div className="kpi-value">{totalCalls}</div>
          <div className="kpi-sub">across all targets</div>
        </div>
        <div className="cs-kpi">
          <div className="kpi-label">LLM Rate</div>
          <div className="kpi-value" style={{ color: llmRateColor(overallRate) }}>
            {overallRate.toFixed(1)}%
          </div>
          <div className="kpi-sub">real vs fallback</div>
        </div>
      </div>

      {targets.length === 0 ? (
        <div className="empty">No decision targets found.</div>
      ) : (
        <div className="ldt-table-wrapper">
          <table className="ldt-table">
            <thead>
              <tr>
                <th onClick={() => setSortKey('target_id')} className="ldt-sortable">
                  Target {sortKey === 'target_id' && '↓'}
                </th>
                <th onClick={() => setSortKey('decision_type')} className="ldt-sortable">
                  Type {sortKey === 'decision_type' && '↓'}
                </th>
                <th onClick={() => setSortKey('n_calls')} className="ldt-sortable">
                  Calls {sortKey === 'n_calls' && '↓'}
                </th>
                <th>LLM</th>
                <th>Fallback</th>
                <th>LLM Rate</th>
                <th>Avg Mult</th>
                <th>Avg Conf</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((row, i) => {
                const rate = row.n_calls > 0 ? (row.n_real_llm / row.n_calls * 100) : 0
                return (
                  <tr key={i}>
                    <td><code>{row.target_id}</code></td>
                    <td><span className="ldt-decision-type">{row.decision_type}</span></td>
                    <td className="ldt-num">{row.n_calls}</td>
                    <td className="ldt-num" style={{ color: '#10b981' }}>{row.n_real_llm}</td>
                    <td className="ldt-num" style={{ color: '#ef4444' }}>{row.n_fallback}</td>
                    <td>
                      <div className="ldt-bar-cell">
                        <div
                          className="ldt-bar"
                          style={{
                            width: `${Math.min(100, rate)}%`,
                            background: llmRateColor(rate),
                          }}
                        />
                        <span className="ldt-bar-label">{rate.toFixed(0)}%</span>
                      </div>
                    </td>
                    <td className="ldt-num">
                      {trendIcon(row.avg_multiplier)} {row.avg_multiplier?.toFixed(2) ?? '—'}
                    </td>
                    <td className="ldt-num">
                      {row.avg_confidence != null
                        ? `${(row.avg_confidence * 100).toFixed(0)}%`
                        : '—'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      <div className="ldt-footnote">
        Sorted by n_calls DESC. Each row shows cumulative LLM call stats
        for a single target across all sim_days. 60s auto-refresh.
      </div>
    </div>
  )
}
