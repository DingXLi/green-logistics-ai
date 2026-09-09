/**
 * CompareRoutesPanel - side-by-side comparison of two specific routes (iter #64)
 *
 * 数据源:
 *   GET /api/persistence/compare-routes?route_id_a=...&route_id_b=...
 *
 * 显示:
 * - Two route_id inputs + quick-pick chips (top 10 from top-routes)
 * - Side-by-side KPI table (11 metrics)
 * - 5-axis winner badges (color-coded A=blue / B=green)
 * - Absolute + pct change columns
 *
 * 用途: 让用户看到:
 *       - 两个 route row 哪个更 green (low co2_per_km)
 *       - 哪个更快 (high speed_km_per_hour)
 *       - 哪个更便宜 (low cost_per_km)
 */

import { useState, useEffect } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'
const REFRESH_MS = 60_000

const METRIC_GROUPS = [
  {
    title: '📏 Distance & Duration',
    fields: ['distance_km', 'duration_hours', 'n_stops', 'cycle_tons'],
  },
  {
    title: '💰 Cost',
    fields: ['cost_sek', 'cost_per_km', 'cost_per_hour'],
  },
  {
    title: '🌱 Sustainability',
    fields: ['co2_kg', 'co2_per_km', 'co2_per_hour'],
  },
  {
    title: '⚡ Speed',
    fields: ['speed_km_per_hour'],
  },
]

function fmt(value, key) {
  if (value === null || value === undefined) return '—'
  if (key === 'n_stops') return String(value)
  if (key === 'cycle_tons' || key === 'distance_km') return value.toFixed(1)
  if (key === 'duration_hours') return value.toFixed(2)
  if (key === 'speed_km_per_hour') return value.toFixed(1)
  if (key.includes('per_km') || key.includes('per_hour')) return value.toFixed(3)
  if (key.includes('co2')) return value.toFixed(2)
  return value.toFixed(2)
}

export function CompareRoutesPanel() {
  const [routeIdA, setRouteIdA] = useState('')
  const [routeIdB, setRouteIdB] = useState('')
  const [quickRoutes, setQuickRoutes] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [data, setData] = useState(null)

  // Fetch top-routes for quick-pick chips
  useEffect(() => {
    const fetchTop = async () => {
      try {
        const res = await fetch(`${API_BASE}/persistence/top-routes?limit=10`)
        if (res.ok) {
          const json = await res.json()
          setQuickRoutes(json.top_routes || [])
          // Default to first two routes if available
          if ((json.top_routes || []).length >= 2) {
            setRouteIdA(String(json.top_routes[0].route_id))
            setRouteIdB(String(json.top_routes[1].route_id))
          }
        }
      } catch (e) {
        // non-fatal
      }
    }
    fetchTop()
  }, [])

  const fetchData = async (
    a = routeIdA, b = routeIdB
  ) => {
    if (!a || !b) {
      setData(null)
      return
    }
    if (a === b) {
      setError('route_id_a and route_id_b must be different')
      return
    }
    try {
      const params = new URLSearchParams({ route_id_a: a, route_id_b: b })
      const res = await fetch(`${API_BASE}/persistence/compare-routes?${params}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const json = await res.json()
      setData(json)
      setError(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchData()
    const id = setInterval(() => {
      fetchData(routeIdA, routeIdB)
    }, REFRESH_MS)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    fetchData(routeIdA, routeIdB)
  }, [routeIdA, routeIdB])

  if (loading && !data) {
    return <LoadingSpinner size="md" label="Loading route comparison…" />
  }

  if (error || !data) {
    return (
      <div className="card compare-routes-panel">
        <h3>🛣️ Compare Routes</h3>
        <div className="empty-state">
          {error ? `Failed to fetch: ${error}` : 'No data yet.'}
        </div>
      </div>
    )
  }

  const a = data.route_a || {}
  const b = data.route_b || {}
  const diffs = data.differences || {}
  const winner = data.winner || {}

  return (
    <div className="card compare-routes-panel">
      <div className="card-header-row">
        <h3>🛣️ Compare Routes</h3>
      </div>

      <div className="filter-row">
        <label>Route A id:</label>
        <input
          type="number"
          className="filter-input filter-input-narrow"
          placeholder="e.g. 1"
          value={routeIdA}
          onChange={(e) => setRouteIdA(e.target.value)}
        />
        <div className="quick-picks">
          {quickRoutes.slice(0, 5).map((r) => (
            <button
              key={`a-${r.route_id}`}
              className={`chip-pick ${routeIdA === String(r.route_id) ? 'active' : ''}`}
              onClick={() => setRouteIdA(String(r.route_id))}
              title={`cycle ${r.cycle_id} / ${r.vehicle_id}`}
            >
              #{r.route_id}
            </button>
          ))}
        </div>
      </div>

      <div className="filter-row">
        <label>Route B id:</label>
        <input
          type="number"
          className="filter-input filter-input-narrow"
          placeholder="e.g. 2"
          value={routeIdB}
          onChange={(e) => setRouteIdB(e.target.value)}
        />
        <div className="quick-picks">
          {quickRoutes.slice(5, 10).map((r) => (
            <button
              key={`b-${r.route_id}`}
              className={`chip-pick ${routeIdB === String(r.route_id) ? 'active' : ''}`}
              onClick={() => setRouteIdB(String(r.route_id))}
              title={`cycle ${r.cycle_id} / ${r.vehicle_id}`}
            >
              #{r.route_id}
            </button>
          ))}
        </div>
      </div>

      {/* Winner badges */}
      {Object.keys(winner).length > 0 && (
        <div className="winner-badges">
          {Object.entries(winner).map(([axis, w]) => {
            if (!w) return null
            const matColor = w.route_id === a.route_id ? 'badge-a' : 'badge-b'
            const matLabel = w.route_id === a.route_id ? 'A' : 'B'
            const axisLabel = axis.replace(/_/g, ' ')
            return (
              <span key={axis} className={`winner-badge ${matColor}`}>
                {matLabel} wins {axisLabel}
                {w.by_pct !== null && w.by_pct !== undefined ? ` (+${w.by_pct}%)` : ''}
              </span>
            )
          })}
        </div>
      )}

      {/* Identity row */}
      <div className="identity-row">
        <div className="identity-cell identity-a">
          <div className="identity-label">Route A</div>
          <div className="identity-value">
            #{a.route_id} · cycle {a.cycle_id || '—'}
          </div>
          <div className="identity-meta">
            {a.vehicle_id || '—'} · day {a.sim_day} · hr {a.sim_hour}
          </div>
        </div>
        <div className="identity-cell identity-b">
          <div className="identity-label">Route B</div>
          <div className="identity-value">
            #{b.route_id} · cycle {b.cycle_id || '—'}
          </div>
          <div className="identity-meta">
            {b.vehicle_id || '—'} · day {b.sim_day} · hr {b.sim_hour}
          </div>
        </div>
      </div>

      {/* Side-by-side KPI table */}
      {METRIC_GROUPS.map((group) => (
        <div key={group.title} className="metric-group">
          <h4>{group.title}</h4>
          <table className="data-table compare-r-table">
            <thead>
              <tr>
                <th>Metric</th>
                <th className="numeric header-a">A</th>
                <th className="numeric header-b">B</th>
                <th className="numeric">Δ (B − A)</th>
                <th className="numeric">%</th>
              </tr>
            </thead>
            <tbody>
              {group.fields.map((field) => {
                const va = a[field]
                const vb = b[field]
                const abs = diffs.absolute?.[field]
                const pct = diffs.pct_change?.[field]
                return (
                  <tr key={field}>
                    <td className="metric-name">{field}</td>
                    <td className="numeric cell-a">{fmt(va, field)}</td>
                    <td className="numeric cell-b">{fmt(vb, field)}</td>
                    <td className="numeric diff-cell">
                      {abs !== null && abs !== undefined
                        ? (abs >= 0 ? '+' : '') + fmt(abs, field)
                        : '—'}
                    </td>
                    <td className="numeric pct-cell">
                      {pct !== null && pct !== undefined
                        ? (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%'
                        : '—'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ))}

      <div className="card-footnote">
        iter #64 · auto-refresh 60s
      </div>
    </div>
  )
}