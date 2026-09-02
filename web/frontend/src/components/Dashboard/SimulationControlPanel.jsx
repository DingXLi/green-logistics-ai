/**
 * SimulationControlPanel - run multi-day simulations on demand (iter #40)
 *
 * 数据源: POST /api/simulate/run
 *
 * UI:
 * - "Days" input (default 7, max 90)
 * - "Dry run" checkbox (default off — runs against real DB)
 * - "Run simulation" button
 * - Live status: idle / running / done / error
 * - Result summary: cycles completed, kpi totals, last sim_day
 *
 * Useful for filling analytics data (perturbation-impact / forecast /
 * cohort panels need 30+ days to be meaningful).
 */

import { useState } from 'react'

import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'

const DAY_OPTIONS = [3, 7, 14, 30, 60, 90]

export function SimulationControlPanel() {
  const [days, setDays] = useState(7)
  const [dryRun, setDryRun] = useState(false)
  const [status, setStatus] = useState('idle')  // idle | running | done | error
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)

  const handleRun = async () => {
    setError(null)
    setResult(null)
    setStatus('running')
    try {
      const params = new URLSearchParams({
        days: days.toString(),
        dry_run: dryRun.toString(),
      })
      const resp = await fetch(`${API_BASE}/simulate/run?${params}`, {
        method: 'POST',
      })
      if (!resp.ok) {
        const txt = await resp.text()
        throw new Error(`HTTP ${resp.status}: ${txt}`)
      }
      const data = await resp.json()
      setResult(data)
      setStatus('done')
    } catch (e) {
      setError(e.message)
      setStatus('error')
    }
  }

  const statusColor = {
    idle: '#94a3b8',
    running: '#f59e0b',
    done: '#22c55e',
    error: '#ef4444',
  }[status]

  const statusLabel = {
    idle: '⚪ Idle',
    running: '⏳ Running…',
    done: '✅ Done',
    error: '❌ Error',
  }[status]

  return (
    <div className="chart-card">
      <div className="chart-card-header">
        <h3>▶️ Run Simulation (iter #40)</h3>
        <span className="chart-card-sub">
          Trigger multi-day V2 simulation on demand
        </span>
      </div>

      <div style={{
        marginTop: '0.75rem',
        padding: '0.75rem',
        background: '#1e293b',
        borderRadius: '4px',
        border: '1px solid #475569',
        display: 'flex',
        gap: '0.75rem',
        alignItems: 'center',
        flexWrap: 'wrap',
      }}>
        <label style={{ display: 'flex', flexDirection: 'column', fontSize: '0.8rem', color: '#94a3b8' }}>
          Days
          <input
            type="number" min={1} max={90}
            value={days}
            onChange={e => setDays(parseInt(e.target.value, 10) || 1)}
            disabled={status === 'running'}
            style={{
              padding: '0.4rem', width: '80px',
              background: '#0f172a', color: '#e2e8f0',
              border: '1px solid #475569', borderRadius: '3px',
              marginTop: '0.2rem',
            }}
          />
        </label>
        <label style={{ display: 'flex', alignItems: 'center', fontSize: '0.85rem', color: '#cbd5e1', gap: '0.3rem' }}>
          <input
            type="checkbox"
            checked={dryRun}
            onChange={e => setDryRun(e.target.checked)}
            disabled={status === 'running'}
            style={{ marginRight: '0.2rem' }}
          />
          Dry run (no DB writes)
        </label>
        <div style={{ display: 'flex', gap: '0.3rem' }}>
          {DAY_OPTIONS.map(n => (
            <button
              key={n}
              type="button"
              onClick={() => setDays(n)}
              disabled={status === 'running'}
              style={{
                padding: '0.25rem 0.5rem',
                background: days === n ? '#3b82f6' : '#0f172a',
                color: days === n ? '#fff' : '#94a3b8',
                border: '1px solid #475569', borderRadius: '3px',
                cursor: status === 'running' ? 'not-allowed' : 'pointer',
                fontSize: '0.75rem',
              }}
            >
              {n}d
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={handleRun}
          disabled={status === 'running'}
          style={{
            padding: '0.5rem 1rem',
            background: status === 'running' ? '#475569' : '#22c55e',
            color: '#fff', border: 'none', borderRadius: '4px',
            cursor: status === 'running' ? 'not-allowed' : 'pointer',
            fontSize: '0.9rem', fontWeight: 500,
            marginLeft: 'auto',
          }}
        >
          {status === 'running' ? '⏳ Running…' : '▶ Run simulation'}
        </button>
      </div>

      <div style={{ marginTop: '0.5rem', color: statusColor, fontSize: '0.9rem' }}>
        Status: {statusLabel}
      </div>

      {error && (
        <div className="error-banner" style={{ marginTop: '0.5rem' }}>
          ⚠️ {error}
        </div>
      )}

      {result && (
        <div style={{ marginTop: '0.75rem' }}>
          <h4 style={{ margin: '0 0 0.5rem 0', color: '#cbd5e1', fontSize: '0.9rem' }}>
            ✅ Simulation completed in {result.wall_duration_seconds}s
          </h4>
          <div className="kpi-grid" style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
            gap: '0.5rem',
          }}>
            <div className="kpi-card" style={{ borderTop: '3px solid #3b82f6' }}>
              <div className="kpi-label">Cycles</div>
              <div className="kpi-value">{result.cycles_completed}</div>
            </div>
            <div className="kpi-card" style={{ borderTop: '3px solid #22c55e' }}>
              <div className="kpi-label">Total tons</div>
              <div className="kpi-value">{result.kpi_summary.total_tons.toFixed(0)}</div>
            </div>
            <div className="kpi-card" style={{ borderTop: '3px solid #f59e0b' }}>
              <div className="kpi-label">Total cost</div>
              <div className="kpi-value">{result.kpi_summary.total_cost_sek.toFixed(0)}</div>
              <div style={{ fontSize: '0.7rem', color: '#94a3b8', marginTop: '0.2rem' }}>SEK</div>
            </div>
            <div className="kpi-card" style={{ borderTop: '3px solid #8b5cf6' }}>
              <div className="kpi-label">Total CO₂</div>
              <div className="kpi-value">{result.kpi_summary.total_co2_kg.toFixed(0)}</div>
              <div style={{ fontSize: '0.7rem', color: '#94a3b8', marginTop: '0.2rem' }}>kg</div>
            </div>
            <div className="kpi-card" style={{ borderTop: '3px solid #ec4899' }}>
              <div className="kpi-label">Matches</div>
              <div className="kpi-value">{result.kpi_summary.n_matches_total}</div>
            </div>
            {result.kpi_summary.avg_fleet_utilization_pct !== null && (
              <div className="kpi-card" style={{ borderTop: '3px solid #06b6d4' }}>
                <div className="kpi-label">Avg util</div>
                <div className="kpi-value">{result.kpi_summary.avg_fleet_utilization_pct.toFixed(1)}%</div>
              </div>
            )}
          </div>
          <div style={{ marginTop: '0.5rem', fontSize: '0.8rem', color: '#94a3b8' }}>
            Sim day {result.first_sim_day ?? '?'} → {result.last_sim_day ?? '?'}.
            Dry run: {result.dry_run ? 'yes' : 'no'}.
          </div>
        </div>
      )}

      <div style={{ marginTop: '0.75rem', fontSize: '0.75rem', color: '#64748b' }}>
        💡 Use this to populate analytics panels (perturbation impact, forecast confidence, cohort retention)
        with realistic multi-day data. Each day runs an OR-Tools solve — 7 days ≈ 30s, 30 days ≈ 2 min.
      </div>
    </div>
  )
}

export default SimulationControlPanel
