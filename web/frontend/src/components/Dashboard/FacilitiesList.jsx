/**
 * FacilitiesList - 真实瑞典废料设施列表
 *
 * 数据源: GET /api/facilities
 *
 * 显示真实设施 (Renova/Ragn-Sells/Stena/Swerock 等公司) +
 * facility_type 过滤 + city 过滤。
 * 让用户能看到 supply chain 的实际下游节点。
 */
import { useState, useEffect } from 'react'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'

const FACILITY_TYPE_LABELS = {
  'recycling_center': '♻️ Recycling center',
  'harbor_cargo': '⚓ Harbor cargo',
  'metal_recovery': '🔩 Metal recovery',
  'paper_mill': '📄 Paper mill',
  'textile_recycling': '👕 Textile recycling',
  'concrete_recycling': '🏗️ Concrete recycling',
  'waste_to_energy': '🔥 Waste-to-energy',
  'plastic_recycling': '🧴 Plastic recycling',
}

const CITY_COLORS = {
  'Borås': '#3b82f6',
  'Göteborg': '#10b981',
  'Stockholm': '#f59e0b',
}

export function FacilitiesList() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [cityFilter, setCityFilter] = useState('')
  const [typeFilter, setTypeFilter] = useState('')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    let url = `${API_BASE}/facilities`
    const params = new URLSearchParams()
    if (cityFilter) params.set('city', cityFilter)
    if (typeFilter) params.set('facility_type', typeFilter)
    if (params.toString()) url += `?${params}`

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
  }, [cityFilter, typeFilter])

  if (loading) return <div className="empty">Loading facilities…</div>
  if (error) return <div className="error-banner">⚠️ {error}</div>
  if (!data) return null

  const typeOptions = Object.keys(data.facility_type_counts).sort()
  const cityOptions = ['Borås', 'Göteborg', 'Stockholm']

  return (
    <div className="chart-card">
      <h3>🏭 Real Swedish Facilities ({data.total})</h3>
      <p className="chart-subtitle">
        Real recycling/waste facilities operating in the Borås / Göteborg / Stockholm region.
        Source: Avfall Sverige 2023 + company public data + OSM.
      </p>

      <div className="facility-filters">
        <label>
          City:
          <select value={cityFilter} onChange={e => setCityFilter(e.target.value)}>
            <option value="">All ({data.total_available})</option>
            {cityOptions.map(c => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        </label>
        <label>
          Type:
          <select value={typeFilter} onChange={e => setTypeFilter(e.target.value)}>
            <option value="">All ({Object.keys(data.facility_type_counts).length})</option>
            {typeOptions.map(t => (
              <option key={t} value={t}>
                {FACILITY_TYPE_LABELS[t] || t} ({data.facility_type_counts[t]})
              </option>
            ))}
          </select>
        </label>
        {(cityFilter || typeFilter) && (
          <button
            className="clear-filters-btn"
            onClick={() => { setCityFilter(''); setTypeFilter('') }}
          >
            Clear filters
          </button>
        )}
      </div>

      <div className="facilities-grid">
        {data.facilities.map(f => (
          <div
            key={f.id}
            className="facility-card"
            style={{ borderLeftColor: CITY_COLORS[f.city] || '#94a3b8' }}
          >
            <div className="facility-card-header">
              <strong>{f.name}</strong>
              <span className={`city-badge city-${f.city.toLowerCase().replace('ö', 'o')}`}>
                {f.city}
              </span>
            </div>
            <div className="facility-card-type">
              {FACILITY_TYPE_LABELS[f.facility_type] || f.facility_type}
            </div>
            <div className="facility-card-meta">
              <span>📍 {f.lat.toFixed(4)}, {f.lon.toFixed(4)}</span>
              <span>⚖️ {f.processing_capacity_tons_per_day} t/day</span>
            </div>
            <div className="facility-card-materials">
              {f.preferred_materials.map(m => (
                <span key={m} className="material-chip">{m}</span>
              ))}
            </div>
            <div className="facility-card-operator">Operator: {f.operator}</div>
          </div>
        ))}
        {data.facilities.length === 0 && (
          <div className="empty">No facilities match the filters.</div>
        )}
      </div>
    </div>
  )
}