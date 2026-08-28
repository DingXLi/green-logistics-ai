/**
 * CycleHistory - 历史 cycle 列表 + 详情展开 (iter #11)
 *
 * 数据源:
 *   GET /api/persistence/cycle-history?limit=...
 *   GET /api/persistence/cycle-detail/{cycle_id}
 *
 * 显示:
 *   - 可排序的 cycle 表格 (cycle_id, sim_day, matches, tons, cost, CO2, routes)
 *   - 点击行展开 → 显示该 cycle 的完整 supply/demand/match/route
 *   - 过滤: has_matches_only, sim_day 范围
 *
 * 用途: 用户回顾历史 cycle, 验证 optimizer 的稳定性。
 */

import { useState, useEffect } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'

function formatTs(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('sv-SE', {
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit',
    })
  } catch {
    return iso
  }
}

export function CycleHistory() {
  const [cycles, setCycles] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [hasMatchesOnly, setHasMatchesOnly] = useState(false)
  const [limit, setLimit] = useState(50)
  const [expandedCycleId, setExpandedCycleId] = useState(null)
  const [detail, setDetail] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)

  // fetch cycle list
  useEffect(() => {
    setLoading(true)
    setError(null)
    const url = new URL(`${API_BASE}/persistence/cycle-history`)
    url.searchParams.set('limit', String(limit))
    if (hasMatchesOnly) url.searchParams.set('has_matches_only', 'true')
    let cancelled = false
    fetch(url.toString())
      .then(r => r.json())
      .then(d => {
        if (!cancelled) {
          setCycles(Array.isArray(d) ? d : [])
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
  }, [limit, hasMatchesOnly])

  // fetch detail on expand
  useEffect(() => {
    if (!expandedCycleId) {
      setDetail(null)
      return
    }
    setDetailLoading(true)
    let cancelled = false
    fetch(`${API_BASE}/persistence/cycle-detail/${encodeURIComponent(expandedCycleId)}`)
      .then(r => r.json())
      .then(d => {
        if (!cancelled) {
          setDetail(d)
          setDetailLoading(false)
        }
      })
      .catch(e => {
        if (!cancelled) {
          setDetail({ error: e.message })
          setDetailLoading(false)
        }
      })
    return () => { cancelled = true }
  }, [expandedCycleId])

  if (loading) return <LoadingSpinner label="Loading cycle history…" />
  if (error) return <div className="error-banner">⚠️ {error}</div>

  const totalMatches = cycles?.reduce((s, c) => s + (c.n_matches || 0), 0) || 0
  const totalTons = cycles?.reduce((s, c) => s + (c.total_tons || 0), 0) || 0
  const totalCost = cycles?.reduce((s, c) => s + (c.total_cost_sek || 0), 0) || 0

  return (
    <div className="chart-card">
      <h3>📜 Cycle History</h3>
      <p className="chart-subtitle">
        Past optimization cycles (newest first). Click a row to expand
        supply/demand/match/route details.
      </p>

      {/* 过滤控件 */}
      <div className="history-controls">
        <label className="history-checkbox">
          <input
            type="checkbox"
            checked={hasMatchesOnly}
            onChange={e => setHasMatchesOnly(e.target.checked)}
          />
          Only with matches
        </label>
        <label className="history-limit">
          Limit:
          <select value={limit} onChange={e => setLimit(Number(e.target.value))}>
            <option value={20}>20</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
            <option value={200}>200</option>
          </select>
        </label>
        <div className="history-stats">
          <span>{cycles?.length || 0} cycles</span>
          <span>· {totalMatches} matches</span>
          <span>· {totalTons.toFixed(1)} t</span>
          <span>· {totalCost.toFixed(0)} SEK</span>
        </div>
      </div>

      {cycles.length === 0 ? (
        <div className="empty">
          No cycles recorded yet. Run <code>/api/optimize</code> or wait for scheduler.
        </div>
      ) : (
        <div className="history-table-wrap">
          <table className="history-table">
            <thead>
              <tr>
                <th></th>
                <th>Cycle ID</th>
                <th>Day</th>
                <th>Hour</th>
                <th>Wall Time</th>
                <th>Offers</th>
                <th>Demands</th>
                <th>Matches</th>
                <th>Routes</th>
                <th>Tons</th>
                <th>Cost (SEK)</th>
                <th>CO₂ (kg)</th>
                <th>Util %</th>
                <th>Status</th>
                <th>Dur (ms)</th>
              </tr>
            </thead>
            <tbody>
              {cycles.map(c => {
                const isExpanded = expandedCycleId === c.cycle_id
                return (
                  <CycleRow
                    key={c.cycle_id}
                    cycle={c}
                    expanded={isExpanded}
                    onToggle={() => setExpandedCycleId(isExpanded ? null : c.cycle_id)}
                  />
                )
              })}
            </tbody>
          </table>

          {/* 详情展开 */}
          {expandedCycleId && (
            <div className="history-detail">
              {detailLoading ? (
                <LoadingSpinner label="Loading cycle detail…" />
              ) : detail?.error ? (
                <div className="error-banner">⚠️ {detail.error}</div>
              ) : detail ? (
                <CycleDetailView detail={detail} />
              ) : null}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function CycleRow({ cycle, expanded, onToggle }) {
  const statusClass = (cycle.solver_status || 'unknown').toLowerCase()
  return (
    <>
      <tr
        className={`history-row ${expanded ? 'expanded' : ''} ${cycle.n_matches === 0 ? 'no-matches' : ''}`}
        onClick={onToggle}
      >
        <td className="history-toggle">{expanded ? '▼' : '▶'}</td>
        <td className="cycle-id-cell" title={cycle.cycle_id}>
          {cycle.cycle_id.length > 12
            ? cycle.cycle_id.slice(0, 8) + '…'
            : cycle.cycle_id}
        </td>
        <td>{cycle.sim_day}</td>
        <td>{cycle.sim_hour}</td>
        <td className="ts-cell">{formatTs(cycle.wall_timestamp)}</td>
        <td>{cycle.n_supply_offers || 0}</td>
        <td>{cycle.n_demand_requests || 0}</td>
        <td className={cycle.n_matches > 0 ? 'has-matches' : 'no-matches'}>
          {cycle.n_matches || 0}
        </td>
        <td>{cycle.n_routes || 0}</td>
        <td>{(cycle.total_tons || 0).toFixed(1)}</td>
        <td>{(cycle.total_cost_sek || 0).toFixed(0)}</td>
        <td>{(cycle.total_co2_kg || 0).toFixed(1)}</td>
        <td>{cycle.fleet_utilization_pct?.toFixed(0) || '—'}</td>
        <td>
          <span className={`solver-status ${statusClass}`}>
            {cycle.solver_status || '—'}
          </span>
        </td>
        <td>{cycle.wall_duration_ms || 0}</td>
      </tr>
    </>
  )
}

function CycleDetailView({ detail }) {
  const { cycle, supply_offers, demand_requests, matches, routes } = detail
  return (
    <div className="cycle-detail-content">
      <h4>Cycle {cycle.cycle_id} — full breakdown</h4>
      <div className="detail-meta">
        <span><b>sim_day:</b> {cycle.sim_day}</span>
        <span><b>sim_hour:</b> {cycle.sim_hour}</span>
        <span><b>month:</b> {cycle.seasonal_month}</span>
        <span><b>seasonal_factor:</b> {cycle.seasonal_factor_avg?.toFixed(2)}</span>
        <span><b>wall_time:</b> {formatTs(cycle.wall_timestamp)}</span>
      </div>

      <div className="detail-section">
        <h5>🚛 Supply offers ({supply_offers.length})</h5>
        {supply_offers.length === 0 ? (
          <div className="empty small">No supply offers</div>
        ) : (
          <table className="detail-table">
            <thead>
              <tr>
                <th>Supply ID</th>
                <th>Material</th>
                <th>Tons</th>
                <th>Quality</th>
                <th>Moisture %</th>
                <th>Location</th>
              </tr>
            </thead>
            <tbody>
              {supply_offers.slice(0, 10).map((s, i) => (
                <tr key={i}>
                  <td>{s.supply_id}</td>
                  <td>{s.material_type}</td>
                  <td>{(s.available_tons || 0).toFixed(1)}</td>
                  <td>{s.quality_score?.toFixed(0) || '—'}</td>
                  <td>{s.moisture_percent?.toFixed(0) || '—'}</td>
                  <td className="coord">
                    {s.location_lat?.toFixed(3)}, {s.location_lon?.toFixed(3)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {supply_offers.length > 10 && (
          <div className="more-note">… and {supply_offers.length - 10} more</div>
        )}
      </div>

      <div className="detail-section">
        <h5>🏗️ Demand requests ({demand_requests.length})</h5>
        {demand_requests.length === 0 ? (
          <div className="empty small">No demand requests</div>
        ) : (
          <table className="detail-table">
            <thead>
              <tr>
                <th>Demand ID</th>
                <th>Name</th>
                <th>Material</th>
                <th>Required Tons</th>
                <th>Priority</th>
                <th>Deadline</th>
              </tr>
            </thead>
            <tbody>
              {demand_requests.slice(0, 10).map((d, i) => (
                <tr key={i}>
                  <td>{d.demand_id}</td>
                  <td>{d.name || '—'}</td>
                  <td>{d.material_type}</td>
                  <td>{(d.required_tons || 0).toFixed(1)}</td>
                  <td>{d.priority || '—'}</td>
                  <td>{d.deadline || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {demand_requests.length > 10 && (
          <div className="more-note">… and {demand_requests.length - 10} more</div>
        )}
      </div>

      <div className="detail-section">
        <h5>🤝 Matches ({matches.length})</h5>
        {matches.length === 0 ? (
          <div className="empty small">No matches — supply/demand didn't align</div>
        ) : (
          <table className="detail-table">
            <thead>
              <tr>
                <th>Supply</th>
                <th>Demand</th>
                <th>Material</th>
                <th>Tons</th>
                <th>Distance (km)</th>
                <th>Profit (SEK)</th>
              </tr>
            </thead>
            <tbody>
              {matches.slice(0, 15).map((m, i) => (
                <tr key={i}>
                  <td>{m.supply_id}</td>
                  <td>{m.demand_id}</td>
                  <td>{m.material_type}</td>
                  <td>{(m.tons || 0).toFixed(1)}</td>
                  <td>{(m.distance_km || 0).toFixed(1)}</td>
                  <td className="profit-cell">{(m.estimated_profit_sek || 0).toFixed(0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {matches.length > 15 && (
          <div className="more-note">… and {matches.length - 15} more</div>
        )}
      </div>

      <div className="detail-section">
        <h5>🛣️ Routes ({routes.length})</h5>
        {routes.length === 0 ? (
          <div className="empty small">No routes planned</div>
        ) : (
          <table className="detail-table">
            <thead>
              <tr>
                <th>Vehicle</th>
                <th>Stops</th>
                <th>Distance (km)</th>
                <th>Duration (h)</th>
                <th>Cost (SEK)</th>
                <th>CO₂ (kg)</th>
              </tr>
            </thead>
            <tbody>
              {routes.slice(0, 10).map((r, i) => (
                <tr key={i}>
                  <td>{r.vehicle_id}</td>
                  <td className="stops-cell" title={r.stops?.join(' → ')}>
                    {r.stops?.length || 0} stops
                  </td>
                  <td>{(r.distance_km || 0).toFixed(1)}</td>
                  <td>{(r.duration_hours || 0).toFixed(1)}</td>
                  <td>{(r.cost_sek || 0).toFixed(0)}</td>
                  <td>{(r.co2_kg || 0).toFixed(1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {routes.length > 10 && (
          <div className="more-note">… and {routes.length - 10} more</div>
        )}
      </div>
    </div>
  )
}

export default CycleHistory
