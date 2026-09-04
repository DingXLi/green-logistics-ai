/**
 * AnomalousCycles.jsx — iter #47
 *
 * Statistical anomaly detection dashboard panel.
 * Shows cycles with KPIs that deviate significantly from the historical mean.
 *
 * Data source: GET /api/persistence/anomalous-cycles?z_threshold=2.0
 *
 * Renders:
 * - KPI cards: n_anomalous / n_total_cycles / z_threshold
 * - Sortable table (severity DESC, z_score DESC by default)
 * - Severity color: red (high) / orange (medium) / yellow (low)
 * - Per-row expandable: shows all metric anomalies with z-score + deviation
 * - Adjustable z-threshold slider (1.5 - 4.0)
 */
import { useState, useEffect, useMemo } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'
const REFRESH_INTERVAL_MS = 60000

const SEVERITY_META = {
  high:   { color: '#ef4444', bg: '#7f1d1d', icon: '🚨', label: 'HIGH' },
  medium: { color: '#f97316', bg: '#7c2d12', icon: '⚠️', label: 'MED'  },
  low:    { color: '#facc15', bg: '#713f12', icon: '⚡', label: 'LOW'  },
}

function severityMeta(sev) {
  return SEVERITY_META[sev] || { color: '#94a3b8', bg: '#1e293b', icon: '?', label: sev || '?' }
}

function deviationPct(value, mean) {
  if (mean == null || mean === 0) return null
  return round(((value - mean) / mean) * 100, 1)
}

function round(n, d = 2) {
  if (n == null) return null
  return Math.round(n * 10 ** d) / 10 ** d
}

export function AnomalousCycles() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [zThreshold, setZThreshold] = useState(2.0)
  const [expandedCycle, setExpandedCycle] = useState(null)
  const [sortKey, setSortKey] = useState('severity')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fetch(`${API_BASE}/persistence/anomalous-cycles?z_threshold=${zThreshold}`)
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
  }, [zThreshold])

  // Auto-refresh every 60s
  useEffect(() => {
    const id = setInterval(() => {
      fetch(`${API_BASE}/persistence/anomalous-cycles?z_threshold=${zThreshold}`)
        .then(r => r.json())
        .then(d => setData(d))
        .catch(e => setError(e.message))
    }, REFRESH_INTERVAL_MS)
    return () => clearInterval(id)
  }, [zThreshold])

  const sorted = useMemo(() => {
    if (!data?.anomalies) return []
    const arr = [...data.anomalies]
    const sevOrder = { high: 3, medium: 2, low: 1 }
    arr.sort((a, b) => {
      if (sortKey === 'severity') {
        const sa = sevOrder[a.max_severity] || 0
        const sb = sevOrder[b.max_severity] || 0
        if (sa !== sb) return sb - sa
        return b.n_anomalies - a.n_anomalies
      }
      if (sortKey === 'sim_day') {
        return a.sim_day - b.sim_day
      }
      if (sortKey === 'n_anomalies') {
        return b.n_anomalies - a.n_anomalies
      }
      return 0
    })
    return arr
  }, [data, sortKey])

  if (loading && !data) return <LoadingSpinner label="loading anomalous cycles..." />
  if (error) return <div className="error-banner">⚠️ {error}</div>
  if (!data) return <div className="empty">No data available.</div>

  const { n_anomalous, n_total_cycles, anomalies } = data
  const highCount = anomalies.filter(a => a.max_severity === 'high').length
  const medCount = anomalies.filter(a => a.max_severity === 'medium').length
  const lowCount = anomalies.filter(a => a.max_severity === 'low').length

  return (
    <div className="chart-card">
      <h3>🚨 Anomalous Cycles <span className="iter-badge">iter #47</span></h3>
      <p className="chart-subtitle">
        Cycles with KPI outliers (z-score ≥ {zThreshold.toFixed(1)} stddev from mean).
        High severity = very unusual; investigate for solver bugs, fuel spikes, etc.
      </p>

      <div className="ac-controls">
        <label className="ac-label">
          Z-threshold:
          <input
            type="range"
            min="1.5"
            max="4.0"
            step="0.1"
            value={zThreshold}
            onChange={e => setZThreshold(parseFloat(e.target.value))}
            className="ac-slider"
          />
          <span className="ac-z-val">{zThreshold.toFixed(1)}</span>
        </label>
        <span className="ac-hint">
          Lower = more sensitive (more flags), higher = only extreme outliers.
        </span>
      </div>

      <div className="cs-kpi-row">
        <div className="cs-kpi">
          <div className="kpi-label">Anomalous</div>
          <div className="kpi-value" style={{ color: n_anomalous > 0 ? '#f59e0b' : '#10b981' }}>
            {n_anomalous}
          </div>
          <div className="kpi-sub">of {n_total_cycles || '?'} cycles</div>
        </div>
        <div className="cs-kpi">
          <div className="kpi-label">High Sev</div>
          <div className="kpi-value" style={{ color: highCount > 0 ? '#ef4444' : '#94a3b8' }}>
            {highCount}
          </div>
          <div className="kpi-sub">|z| ≥ 3.0</div>
        </div>
        <div className="cs-kpi">
          <div className="kpi-label">Med Sev</div>
          <div className="kpi-value" style={{ color: medCount > 0 ? '#f97316' : '#94a3b8' }}>
            {medCount}
          </div>
          <div className="kpi-sub">|z| ≥ 2.5</div>
        </div>
        <div className="cs-kpi">
          <div className="kpi-label">Low Sev</div>
          <div className="kpi-value" style={{ color: lowCount > 0 ? '#facc15' : '#94a3b8' }}>
            {lowCount}
          </div>
          <div className="kpi-sub">|z| ≥ {zThreshold.toFixed(1)}</div>
        </div>
      </div>

      {anomalies.length === 0 ? (
        <div className="empty">
          ✅ No anomalous cycles detected at z ≥ {zThreshold.toFixed(1)}.
          {n_total_cycles < 10 && (
            <div className="ac-empty-sub">
              Note: {n_total_cycles || 0} cycles in DB. Detection needs at least 5.
            </div>
          )}
        </div>
      ) : (
        <div className="ac-table-wrapper">
          <table className="ac-table">
            <thead>
              <tr>
                <th onClick={() => setSortKey('severity')} className="ac-sortable">
                  Sev {sortKey === 'severity' && '↓'}
                </th>
                <th onClick={() => setSortKey('sim_day')} className="ac-sortable">
                  Sim Day {sortKey === 'sim_day' && '↓'}
                </th>
                <th>Cycle ID</th>
                <th onClick={() => setSortKey('n_anomalies')} className="ac-sortable">
                  # Anomalies {sortKey === 'n_anomalies' && '↓'}
                </th>
                <th>Worst Metric</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((row, i) => {
                const sev = severityMeta(row.max_severity)
                const isExpanded = expandedCycle === row.cycle_id
                // Find the worst anomaly (highest z)
                const worst = row.anomalies.reduce(
                  (a, b) => (a.z_score > b.z_score ? a : b), row.anomalies[0]
                )
                return (
                  <tr key={i} className={`ac-row ac-row-${row.max_severity}`}>
                    <td>
                      <span
                        className="ac-sev-pill"
                        style={{ background: sev.bg, color: sev.color }}
                      >
                        {sev.icon} {sev.label}
                      </span>
                    </td>
                    <td className="ac-num">{row.sim_day}</td>
                    <td className="ac-cycle-id"><code>{row.cycle_id}</code></td>
                    <td className="ac-num">{row.n_anomalies}</td>
                    <td>
                      <span style={{ color: sev.color }}>
                        {worst.metric} (z={worst.z_score.toFixed(2)})
                      </span>
                    </td>
                    <td>
                      <button
                        className="ac-expand-btn"
                        onClick={() => setExpandedCycle(isExpanded ? null : row.cycle_id)}
                      >
                        {isExpanded ? '▼' : '▶'}
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Expanded details */}
      {expandedCycle && (() => {
        const row = anomalies.find(r => r.cycle_id === expandedCycle)
        if (!row) return null
        return (
          <div className="ac-details">
            <h4>📋 Anomaly Details: <code>{row.cycle_id}</code> (sim_day {row.sim_day})</h4>
            <table className="ac-detail-table">
              <thead>
                <tr>
                  <th>Metric</th>
                  <th>Value</th>
                  <th>Mean</th>
                  <th>Stddev</th>
                  <th>Z-Score</th>
                  <th>Deviation</th>
                  <th>Severity</th>
                </tr>
              </thead>
              <tbody>
                {row.anomalies.map((a, i) => {
                  const meta = severityMeta(a.severity)
                  const devPct = deviationPct(a.value, a.mean)
                  return (
                    <tr key={i}>
                      <td><code>{a.metric}</code></td>
                      <td className="ac-num">{a.value}</td>
                      <td className="ac-num">{a.mean}</td>
                      <td className="ac-num">{a.stddev}</td>
                      <td className="ac-num" style={{ color: meta.color, fontWeight: 600 }}>
                        {a.z_score.toFixed(2)}
                      </td>
                      <td className="ac-num" style={{ color: devPct > 0 ? '#ef4444' : '#3b82f6' }}>
                        {devPct != null ? `${devPct > 0 ? '+' : ''}${devPct}%` : '—'}
                      </td>
                      <td>
                        <span style={{ color: meta.color }}>{meta.icon} {meta.label}</span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )
      })()}

      <div className="ac-footnote">
        z-score = (value - mean) / stddev. Severity: |z| ≥ 3.0 = high (≈0.3% extreme),
        ≥ 2.5 = medium (≈1% rare), ≥ {zThreshold.toFixed(1)} = low.
        Auto-refresh every 60s.
      </div>
    </div>
  )
}
