/**
 * TopFacilitiesPanel - top real Sweden facilities by supply-distance metrics (iter #59)
 *
 * 数据源:
 *   GET /api/persistence/top-facilities?metric=...&city=...&facility_type=...
 *
 * 显示:
 * - Metric selector (8 distance/efficiency metrics)
 * - City filter (Göteborg / Borås / Stockholm)
 * - Facility type filter
 * - Material filter
 * - sim_day window filters
 * - Top N facilities with rank, ID, name, city, value, totals
 *
 * 用途: 让用户看到:
 *       - 哪些 facility 由最近供应商供货 (low avg_distance)
 *       - 哪些 facility 被大量供货 (high total_matched_tons)
 *       - 哪些 facility 利用率最高 (high utilization_pct vs declared capacity)
 *       - 哪些 facility 难以触及 (high avg_distance)
 *       - 按 city / facility_type 做区域对比
 */

import { useState, useEffect } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'
const REFRESH_MS = 60_000

const METRICS = [
  { key: 'avg_distance', label: '📏 Avg distance', unit: 'km', lower_better: true },
  { key: 'min_distance', label: '⬇️ Min distance', unit: 'km', lower_better: true },
  { key: 'max_distance', label: '⬆️ Max distance', unit: 'km', lower_better: true },
  { key: 'total_matched_tons', label: '📦 Matched', unit: 't', lower_better: false },
  { key: 'match_count', label: '🔢 Match count', unit: '', lower_better: false },
  { key: 'match_rate', label: '🔄 Match rate', unit: 'm/cyc', lower_better: false },
  { key: 'utilization_pct', label: '⚙️ Utilization', unit: '%', lower_better: false },
  { key: 'co2_per_ton', label: '🌱 CO2/ton', unit: 'kg', lower_better: true },
]

const CITY_OPTIONS = [
  { value: '', label: '— All cities —' },
  { value: 'Göteborg', label: 'Göteborg' },
  { value: 'Borås', label: 'Borås' },
  { value: 'Stockholm', label: 'Stockholm' },
]

const FACILITY_TYPE_OPTIONS = [
  { value: '', label: '— All types —' },
  { value: 'recycling_center', label: 'Recycling center' },
  { value: 'metal_recovery', label: 'Metal recovery' },
  { value: 'paper_mill', label: 'Paper mill' },
  { value: 'harbor_cargo', label: 'Harbor cargo' },
  { value: 'construction_recycling', label: 'Construction recycling' },
]

export function TopFacilitiesPanel() {
  const [metric, setMetric] = useState('avg_distance')
  const [city, setCity] = useState('')
  const [facilityType, setFacilityType] = useState('')
  const [materialFilter, setMaterialFilter] = useState('')
  const [sinceDay, setSinceDay] = useState('')
  const [untilDay, setUntilDay] = useState('')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchData = async (
    m = metric,
    c = city,
    ft = facilityType,
    mat = materialFilter,
    sd = sinceDay,
    ud = untilDay
  ) => {
    try {
      const params = new URLSearchParams({ metric: m, limit: '15' })
      if (c) params.set('city', c)
      if (ft) params.set('facility_type', ft)
      if (mat) params.set('material_type', mat)
      if (sd !== '' && sd !== null && sd !== undefined) params.set('since_sim_day', String(sd))
      if (ud !== '' && ud !== null && ud !== undefined) params.set('until_sim_day', String(ud))
      const res = await fetch(`${API_BASE}/persistence/top-facilities?${params}`)
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
      fetchData(metric, city, facilityType, materialFilter, sinceDay, untilDay)
    }, REFRESH_MS)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    fetchData(metric, city, facilityType, materialFilter, sinceDay, untilDay)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [metric, city, facilityType, materialFilter, sinceDay, untilDay])

  if (loading && !data) {
    return <LoadingSpinner size="md" label="Loading top facilities…" />
  }

  if (error || !data) {
    return (
      <div className="card top-facilities-panel">
        <h3>🏭 Top Facilities by Supply Distance (iter #59)</h3>
        <div className="empty-state">
          {error ? `Failed to fetch: ${error}` : 'No facility match data yet. Run simulations to populate.'}
        </div>
      </div>
    )
  }

  const metricDef = METRICS.find((m) => m.key === metric)
  const facilities = data.top_facilities || []

  return (
    <div className="card top-facilities-panel">
      <div className="card-header-row">
        <h3>🏭 Top Facilities by Supply Distance</h3>
        <div className="card-controls">
          <span className="card-badge">
            {data.n_facilities_evaluated} facilities evaluated ·{' '}
            {data.n_facilities_returned} shown
          </span>
        </div>
      </div>

      <div className="filter-row">
        <label>Metric:</label>
        <select
          className="filter-select"
          value={metric}
          onChange={(e) => setMetric(e.target.value)}
        >
          {METRICS.map((m) => (
            <option key={m.key} value={m.key}>{m.label}</option>
          ))}
        </select>

        <label>City:</label>
        <select
          className="filter-select"
          value={city}
          onChange={(e) => setCity(e.target.value)}
        >
          {CITY_OPTIONS.map((c) => (
            <option key={c.value} value={c.value}>{c.label}</option>
          ))}
        </select>

        <label>Type:</label>
        <select
          className="filter-select"
          value={facilityType}
          onChange={(e) => setFacilityType(e.target.value)}
        >
          {FACILITY_TYPE_OPTIONS.map((ft) => (
            <option key={ft.value} value={ft.value}>{ft.label}</option>
          ))}
        </select>

        <label>Material:</label>
        <input
          type="text"
          className="filter-input"
          placeholder="filter material…"
          value={materialFilter}
          onChange={(e) => setMaterialFilter(e.target.value)}
        />

        <label>From day:</label>
        <input
          type="number"
          className="filter-input filter-input-narrow"
          placeholder="since"
          value={sinceDay}
          onChange={(e) => setSinceDay(e.target.value)}
        />

        <label>To day:</label>
        <input
          type="number"
          className="filter-input filter-input-narrow"
          placeholder="until"
          value={untilDay}
          onChange={(e) => setUntilDay(e.target.value)}
        />
      </div>

      {facilities.length === 0 ? (
        <div className="empty-state">
          No facilities match the current filter. Try clearing city/type/material or widening the day range.
        </div>
      ) : (
        <div className="table-wrapper">
          <table className="data-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Facility</th>
                <th>City</th>
                <th>Type</th>
                <th>{metricDef?.label || metric}</th>
                <th className="numeric">Matches</th>
                <th className="numeric">Required (t)</th>
                <th className="numeric">Matched (t)</th>
                <th className="numeric">Util %</th>
                <th className="numeric">Cap (t/d)</th>
              </tr>
            </thead>
            <tbody>
              {facilities.map((f, idx) => (
                <tr key={f.facility_id}>
                  <td className="rank-cell">{idx + 1}</td>
                  <td>
                    <div className="mono facility-id">{f.facility_id}</div>
                    <div className="facility-name">{f.name || '—'}</div>
                  </td>
                  <td>{f.city || '—'}</td>
                  <td>{f.facility_type || '—'}</td>
                  <td className="numeric metric-value">
                    {f.value !== null && f.value !== undefined
                      ? f.value.toFixed(2)
                      : '—'}{' '}
                    <span className="metric-unit">{metricDef?.unit || ''}</span>
                  </td>
                  <td className="numeric">{f.n_matches}</td>
                  <td className="numeric">{f.total_required_tons?.toFixed(1)}</td>
                  <td className="numeric">{f.total_matched_tons?.toFixed(1)}</td>
                  <td className="numeric">
                    {f.utilization_pct !== null && f.utilization_pct !== undefined
                      ? f.utilization_pct.toFixed(1) + '%'
                      : '—'}
                  </td>
                  <td className="numeric">
                    {f.processing_capacity_tons_per_day ?? '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="card-footnote">
        Direction: <strong>{data.direction === 'lower_is_better' ? '↓ lower = better' : '↑ higher = better'}</strong>
        {' · '}
        {data.metric_description}
        {' · '}
        iter #59 · auto-refresh 60s
      </div>
    </div>
  )
}
