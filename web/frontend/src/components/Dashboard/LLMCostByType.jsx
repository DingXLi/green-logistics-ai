/**
 * LLMCostByType.jsx — iter #48
 *
 * LLM usage breakdown by decision_type.
 * Shows which call type (demand_prediction, supply_prediction, etc.)
 * uses LLM the most + fallback rate per type.
 *
 * Data source: GET /api/persistence/llm-cost-by-type
 *
 * Renders:
 * - Table: decision_type / n_total / n_llm / n_fallback / llm_rate_pct /
 *   avg_multiplier / avg_confidence / n_unique_targets
 * - Color-coded bars for llm_rate_pct
 * - Sortable by n_total (default DESC)
 */
import { useState, useEffect, useMemo } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'
const REFRESH_INTERVAL_MS = 60000

function llmRateColor(pct) {
  if (pct == null) return '#94a3b8'
  if (pct >= 80) return '#10b981'  // high LLM usage = green
  if (pct >= 50) return '#facc15'  // medium = yellow
  if (pct >= 20) return '#f59e0b'  // low = orange
  return '#ef4444'                 // mostly fallback = red
}

function trendIcon(value) {
  if (value == null) return '—'
  if (value >= 1.2) return '📈'
  if (value <= 0.8) return '📉'
  return '➖'
}

export function LLMCostByType() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [since, setSince] = useState('')
  const [until, setUntil] = useState('')
  const [sortKey, setSortKey] = useState('n_total')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    const params = new URLSearchParams()
    if (since) params.set('since_sim_day', since)
    if (until) params.set('until_sim_day', until)
    const url = `${API_BASE}/persistence/llm-cost-by-type${params.toString() ? '?' + params.toString() : ''}`
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
  }, [since, until])

  // Auto-refresh every 60s
  useEffect(() => {
    const id = setInterval(() => {
      const params = new URLSearchParams()
      if (since) params.set('since_sim_day', since)
      if (until) params.set('until_sim_day', until)
      const url = `${API_BASE}/persistence/llm-cost-by-type${params.toString() ? '?' + params.toString() : ''}`
      fetch(url)
        .then(r => r.json())
        .then(d => setData(d))
        .catch(e => setError(e.message))
    }, REFRESH_INTERVAL_MS)
    return () => clearInterval(id)
  }, [since, until])

  const sorted = useMemo(() => {
    if (!data?.by_type) return []
    const arr = [...data.by_type]
    arr.sort((a, b) => {
      const av = a[sortKey] ?? 0
      const bv = b[sortKey] ?? 0
      if (typeof av === 'string') return av.localeCompare(bv)
      return bv - av
    })
    return arr
  }, [data, sortKey])

  if (loading && !data) return <LoadingSpinner label="loading LLM cost by type..." />
  if (error) return <div className="error-banner">⚠️ {error}</div>
  if (!data) return <div className="empty">No data available.</div>

  const { by_type } = data
  const totalCalls = by_type.reduce((s, r) => s + (r.n_total || 0), 0)
  const totalLlm = by_type.reduce((s, r) => s + (r.n_llm || 0), 0)
  const totalFallback = by_type.reduce((s, r) => s + (r.n_fallback || 0), 0)
  const overallRate = totalCalls > 0 ? (totalLlm / totalCalls * 100) : 0

  return (
    <div className="chart-card">
      <h3>🤖 LLM Cost by Decision Type <span className="iter-badge">iter #48</span></h3>
      <p className="chart-subtitle">
        LLM usage breakdown by call type. High fallback rate (red) means
        the LLM was unavailable for that type and deterministic logic took over.
      </p>

      <div className="lct-controls">
        <label className="lct-label">
          Since sim_day:
          <input
            type="number"
            className="lct-input"
            placeholder="(start)"
            value={since}
            onChange={e => setSince(e.target.value)}
            style={{ width: '90px' }}
          />
        </label>
        <label className="lct-label">
          Until sim_day:
          <input
            type="number"
            className="lct-input"
            placeholder="(end)"
            value={until}
            onChange={e => setUntil(e.target.value)}
            style={{ width: '90px' }}
          />
        </label>
        {(since || until) && (
          <button
            className="lct-clear"
            onClick={() => { setSince(''); setUntil('') }}
          >
            clear window
          </button>
        )}
      </div>

      <div className="cs-kpi-row">
        <div className="cs-kpi">
          <div className="kpi-label">Total Calls</div>
          <div className="kpi-value">{totalCalls}</div>
          <div className="kpi-sub">across {by_type.length} types</div>
        </div>
        <div className="cs-kpi">
          <div className="kpi-label">LLM Hits</div>
          <div className="kpi-value" style={{ color: '#10b981' }}>{totalLlm}</div>
          <div className="kpi-sub">real Gemini calls</div>
        </div>
        <div className="cs-kpi">
          <div className="kpi-label">Fallbacks</div>
          <div className="kpi-value" style={{ color: '#ef4444' }}>{totalFallback}</div>
          <div className="kpi-sub">deterministic</div>
        </div>
        <div className="cs-kpi">
          <div className="kpi-label">LLM Rate</div>
          <div className="kpi-value" style={{ color: llmRateColor(overallRate) }}>
            {overallRate.toFixed(1)}%
          </div>
          <div className="kpi-sub">overall</div>
        </div>
      </div>

      {by_type.length === 0 ? (
        <div className="empty">No LLM decisions in this window.</div>
      ) : (
        <div className="lct-table-wrapper">
          <table className="lct-table">
            <thead>
              <tr>
                <th onClick={() => setSortKey('decision_type')} className="lct-sortable">
                  Decision Type {sortKey === 'decision_type' && '↓'}
                </th>
                <th onClick={() => setSortKey('n_total')} className="lct-sortable">
                  Total {sortKey === 'n_total' && '↓'}
                </th>
                <th>LLM</th>
                <th>Fallback</th>
                <th onClick={() => setSortKey('llm_rate_pct')} className="lct-sortable">
                  LLM Rate {sortKey === 'llm_rate_pct' && '↓'}
                </th>
                <th>Avg Mult</th>
                <th>Avg Conf</th>
                <th>Targets</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((row, i) => {
                const rate = row.llm_rate_pct
                return (
                  <tr key={i}>
                    <td><code>{row.decision_type}</code></td>
                    <td className="lct-num">{row.n_total}</td>
                    <td className="lct-num">{row.n_llm}</td>
                    <td className="lct-num">{row.n_fallback}</td>
                    <td>
                      <div className="lct-bar-cell">
                        <div
                          className="lct-bar"
                          style={{
                            width: `${Math.min(100, rate)}%`,
                            background: llmRateColor(rate),
                          }}
                        />
                        <span className="lct-bar-label">{rate.toFixed(1)}%</span>
                      </div>
                    </td>
                    <td className="lct-num">
                      {trendIcon(row.avg_multiplier)} {row.avg_multiplier?.toFixed(2) ?? '—'}
                    </td>
                    <td className="lct-num">
                      {row.avg_confidence != null
                        ? `${(row.avg_confidence * 100).toFixed(0)}%`
                        : '—'}
                    </td>
                    <td className="lct-num">{row.n_unique_targets}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      <div className="lct-footnote">
        Avg multiplier (LLM output) and avg confidence (0-1) help diagnose
        whether LLM predictions are reasonable. Multiplier &lt;0.8 or &gt;1.2
        warrants investigation. 60s auto-refresh.
      </div>
    </div>
  )
}
