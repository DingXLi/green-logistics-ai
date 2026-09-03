/**
 * SweetSpot.jsx — iter #41
 *
 * Visualizes the Pareto-frontier sweet-spot finder endpoint
 * (/api/optimize/sweet-spot). Operators can dial in their
 * preference between cost and CO2 reduction, see the recommended
 * carbon tax price, and explore the score curve across all
 * candidate prices.
 */
import { useState, useEffect } from 'react'
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, ReferenceDot, ReferenceLine, Legend,
} from 'recharts'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'

function fmtPrice(p) {
  if (p === null || p === undefined) return '—'
  return `${p.toFixed(2)} SEK/kg`
}

function fmtSek(v) {
  if (v === null || v === undefined) return '—'
  return `${Math.round(v).toLocaleString()} SEK`
}

function fmtKg(v) {
  if (v === null || v === undefined) return '—'
  return `${v.toFixed(1)} kg`
}

export function SweetSpot() {
  const [weightCost, setWeightCost] = useState(0.5)
  const [weightCo2, setWeightCo2] = useState(0.5)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchSweetSpot = (wC = weightCost, wCO2 = weightCo2) => {
    setLoading(true)
    setError(null)
    const params = new URLSearchParams({
      weight_cost: String(wC),
      weight_co2: String(wCO2),
      time_limit_seconds: '2',
    })
    fetch(`${API_BASE}/optimize/sweet-spot?${params}`)
      .then(r => r.json())
      .then(d => {
        setData(d)
        setLoading(false)
      })
      .catch(e => {
        setError(e.message)
        setLoading(false)
      })
  }

  useEffect(() => {
    fetchSweetSpot(weightCost, weightCo2)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleWeightChange = (newCost) => {
    const newCo2 = Math.max(0, Math.min(1, 1.0 - newCost))
    setWeightCost(newCost)
    setWeightCo2(newCo2)
    fetchSweetSpot(newCost, newCo2)
  }

  if (loading && !data) {
    return (
      <div className="carbon-scenarios-panel">
        <div className="cs-header">
          <h3>🎯 Sweet-Spot Finder (iter #41)</h3>
        </div>
        <LoadingSpinner label="Finding sweet spot..." />
      </div>
    )
  }

  if (error) {
    return (
      <div className="carbon-scenarios-panel">
        <div className="cs-header">
          <h3>🎯 Sweet-Spot Finder (iter #41)</h3>
        </div>
        <div className="cs-error">Error: {error}</div>
      </div>
    )
  }

  const sweetSpot = data?.sweet_spot
  const scenarios = data?.scenarios || []
  const chartData = scenarios.map(s => ({
    price: s.carbon_price_sek_per_kg,
    score: s.score !== null ? Number((s.score * 100).toFixed(2)) : null,
    co2: s.co2_kg !== null ? Number(s.co2_kg.toFixed(2)) : null,
    cost: s.cost_sek !== null ? Number(s.cost_sek.toFixed(0)) : null,
    isSweet: s.is_sweet_spot,
  }))

  return (
    <div className="carbon-scenarios-panel">
      <div className="cs-header">
        <h3>🎯 Sweet-Spot Finder <span className="iter-badge">iter #41</span></h3>
        <button className="refresh-btn" onClick={() => fetchSweetSpot()} disabled={loading}>
          {loading ? 'Refreshing...' : '🔄 Refresh'}
        </button>
      </div>

      <div className="cs-section">
        <div className="cs-control-row">
          <label className="cs-label">
            <span>Cost priority: <strong>{weightCost.toFixed(2)}</strong></span>
            <input
              type="range"
              min="0"
              max="1"
              step="0.05"
              value={weightCost}
              onChange={e => handleWeightChange(parseFloat(e.target.value))}
              className="cs-slider"
            />
          </label>
          <label className="cs-label">
            <span>CO₂ priority: <strong>{weightCo2.toFixed(2)}</strong></span>
            <input
              type="range"
              min="0"
              max="1"
              step="0.05"
              value={weightCo2}
              onChange={e => handleWeightChange(1 - parseFloat(e.target.value))}
              className="cs-slider"
            />
          </label>
        </div>
      </div>

      {!sweetSpot ? (
        <div className="cs-empty">
          {data?.reason || 'No scenarios computed — try running a cycle first.'}
        </div>
      ) : (
        <>
          <div className="cs-kpi-row">
            <div className="cs-kpi sweet-spot-kpi">
              <div className="kpi-label">Recommended Price</div>
              <div className="kpi-value">{fmtPrice(sweetSpot.carbon_price_sek_per_kg)}</div>
              <div className="kpi-sub">lowest weighted score ({sweetSpot.score?.toFixed(4)})</div>
            </div>
            <div className="cs-kpi">
              <div className="kpi-label">Cost at Sweet Spot</div>
              <div className="kpi-value">{fmtSek(sweetSpot.cost_sek)}</div>
              <div className="kpi-sub">fuel/operating cost</div>
            </div>
            <div className="cs-kpi">
              <div className="kpi-label">CO₂ at Sweet Spot</div>
              <div className="kpi-value">{fmtKg(sweetSpot.co2_kg)}</div>
              <div className="kpi-sub">vs range [{data.co2_range_kg[0]?.toFixed(1)}, {data.co2_range_kg[1]?.toFixed(1)}] kg</div>
            </div>
            <div className="cs-kpi">
              <div className="kpi-label">Valid Scenarios</div>
              <div className="kpi-value">{data.n_valid_scenarios} / {data.n_scenarios}</div>
              <div className="kpi-sub">candidates evaluated</div>
            </div>
          </div>

          <div className="cs-chart-wrap" style={{ height: 280 }}>
            <ResponsiveContainer>
              <LineChart data={chartData} margin={{ top: 20, right: 30, left: 20, bottom: 5 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#333" />
                <XAxis
                  dataKey="price"
                  type="number"
                  domain={[0, 'dataMax']}
                  tickFormatter={v => v.toFixed(1)}
                  label={{ value: 'Carbon Price (SEK/kg)', position: 'insideBottom', offset: -5, fill: '#888' }}
                  stroke="#888"
                />
                <YAxis
                  domain={[0, 100]}
                  tickFormatter={v => `${v}%`}
                  label={{ value: 'Score (lower = better)', angle: -90, position: 'insideLeft', fill: '#888' }}
                  stroke="#888"
                />
                <Tooltip
                  contentStyle={{ backgroundColor: '#1e1e1e', border: '1px solid #444' }}
                  labelFormatter={v => `Price: ${v} SEK/kg`}
                  formatter={(value, name) => name === 'Score' ? `${value}%` : value}
                />
                <Legend />
                <ReferenceLine y={0} stroke="#666" strokeDasharray="2 2" />
                <Line
                  type="monotone"
                  dataKey="score"
                  name="Score"
                  stroke="#8884d8"
                  strokeWidth={2}
                  dot={(props) => {
                    const { cx, cy, payload } = props
                    if (payload.isSweet) {
                      return <circle cx={cx} cy={cy} r={8} fill="#ffd700" stroke="#ffeb3b" strokeWidth={2} />
                    }
                    return <circle cx={cx} cy={cy} r={3} fill="#8884d8" />
                  }}
                />
                {sweetSpot && (
                  <ReferenceDot
                    x={sweetSpot.carbon_price_sek_per_kg}
                    y={(sweetSpot.score || 0) * 100}
                    r={10}
                    fill="#ffd700"
                    stroke="#fff"
                    strokeWidth={2}
                    label={{ value: '⭐ Sweet Spot', position: 'top', fill: '#ffd700', fontWeight: 'bold' }}
                  />
                )}
              </LineChart>
            </ResponsiveContainer>
          </div>

          <div className="cs-section">
            <h4>Candidate Prices</h4>
            <table className="cs-table">
              <thead>
                <tr>
                  <th>Carbon Price</th>
                  <th>Cost (SEK)</th>
                  <th>CO₂ (kg)</th>
                  <th>Score</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {scenarios.map((s, i) => (
                  <tr key={i} className={s.is_sweet_spot ? 'sweet-spot-row' : ''}>
                    <td>{fmtPrice(s.carbon_price_sek_per_kg)}</td>
                    <td>{fmtSek(s.cost_sek)}</td>
                    <td>{fmtKg(s.co2_kg)}</td>
                    <td>{s.score !== null ? `${(s.score * 100).toFixed(1)}%` : '—'}</td>
                    <td>
                      {s.is_sweet_spot ? (
                        <span className="badge-sweet">⭐ Recommended</span>
                      ) : (
                        <span className="badge-candidate">candidate</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="cs-footnote">
            💡 The sweet-spot price minimizes a weighted sum of normalized cost and CO₂
            across {data.n_scenarios} candidate carbon prices. Adjust the sliders above
            to reflect your operational priorities.
            {!data.use_real_roads && ' Distance matrix is using haversine fallback.'}
          </div>
        </>
      )}
    </div>
  )
}

export default SweetSpot
