/**
 * CarbonScenarios - 碳税情景分析 (iter #13 + iter #39 analytics upgrade)
 *
 * 数据源: GET /api/optimize/carbon-scenarios
 *
 * iter #39 新增 (analytics):
 * - 每 scenario 多 delta_from_baseline_pct + co2_delta_from_baseline_pct
 * - response 顶层多 baseline_carbon_price_sek_per_kg, breakeven_price_sek_per_kg,
 *   breakeven_gap_sek
 *
 * UI 新增:
 * - Custom carbon price input (let user add 1-3 自定义 scenarios)
 * - Pareto scatter plot (cost vs CO2, 4 个 pareto 点 / scenario, colored by tax)
 * - Delta % column (cost% vs baseline, CO2% vs baseline)
 * - Breakeven KPI card (closest convergence point)
 * - "Add custom" / "Reset" buttons
 */

import { useState, useEffect } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  ScatterChart, Scatter, ZAxis, ReferenceLine,
} from 'recharts'

import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'

const DEFAULT_PRICES = [0, 1.5, 3, 5]
const SCENARIO_LABELS = {
  '0': 'No tax',
  '1.5': 'EU ETS',
  '3': '2030 mid',
  '5': 'Aggressive',
}

function labelFor(price) {
  return SCENARIO_LABELS[String(price)] || `${price} SEK/kg`
}

function priceColor(price) {
  if (price === 0) return '#22c55e'   // green - no tax
  if (price <= 1.5) return '#3b82f6'  // blue - low tax
  if (price <= 3) return '#f59e0b'    // amber - medium
  return '#ef4444'                     // red - aggressive
}

export function CarbonScenarios() {
  const [prices, setPrices] = useState(DEFAULT_PRICES)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [customPriceInput, setCustomPriceInput] = useState('')
  const [showPareto, setShowPareto] = useState(true)

  const fetchScenarios = (priceList = prices) => {
    setLoading(true)
    const params = new URLSearchParams({
      carbon_prices: priceList.join(','),
      time_limit_seconds: '3',
    })
    fetch(`${API_BASE}/optimize/carbon-scenarios?${params}`)
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
    fetchScenarios(prices)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleAddCustom = () => {
    const p = parseFloat(customPriceInput)
    if (isNaN(p) || p < 0 || p > 20) {
      setError('Custom price must be a number between 0 and 20 SEK/kg')
      return
    }
    if (prices.includes(p)) {
      setError(`Price ${p} already exists`)
      return
    }
    if (prices.length >= 8) {
      setError('Max 8 scenarios (endpoint limit)')
      return
    }
    setError(null)
    const newPrices = [...prices, p].sort((a, b) => a - b)
    setPrices(newPrices)
    setCustomPriceInput('')
    fetchScenarios(newPrices)
  }

  const handleReset = () => {
    setError(null)
    setPrices(DEFAULT_PRICES)
    fetchScenarios(DEFAULT_PRICES)
  }

  if (loading && !data) return <LoadingSpinner label="Computing carbon scenarios (each runs 4 OR-Tools solves)…" />
  if (error && !data) return <div className="error-banner">⚠️ {error}</div>
  if (!data || !data.scenarios || data.scenarios.length === 0) {
    return <div className="empty">No scenario data available.</div>
  }

  // Prepare BarChart data: x = carbon price, y = cost_sek
  const chartData = data.scenarios.map(s => ({
    name: labelFor(s.carbon_price_sek_per_kg),
    'Cost-optimal (SEK)': s.cost_optimal?.cost_sek || 0,
    'CO₂-optimal (SEK)': s.co2_optimal?.cost_sek || 0,
    'Cost-opt CO₂ (kg)': s.cost_optimal?.co2_kg || 0,
    'CO₂-opt CO₂ (kg)': s.co2_optimal?.co2_kg || 0,
  }))

  // Prepare Pareto scatter data: x = cost_sek, y = co2_kg, colored by tax
  // Each scenario contributes its full pareto[] array of 4 points
  const paretoData = []
  data.scenarios.forEach(s => {
    const color = priceColor(s.carbon_price_sek_per_kg)
    const label = labelFor(s.carbon_price_sek_per_kg)
    ;(s.pareto || []).forEach(p => {
      paretoData.push({
        cost_sek: p.cost_sek,
        co2_kg: p.co2_kg,
        cost_weight: p.cost_weight,
        co2_weight: p.co2_weight,
        carbon_price: s.carbon_price_sek_per_kg,
        label: label,
        fill: color,
      })
    })
  })

  // Compute summary KPI values (iter #39: use true total cost for max)
  const maxTrueCost = Math.max(
    ...data.scenarios.map(s => s.true_total_cost_cost_opt || 0)
  )
  const lastScenario = data.scenarios[data.scenarios.length - 1]
  const totalCo2SavedPct = lastScenario?.co2_delta_from_baseline_pct
  const totalCostDeltaPct = lastScenario?.delta_from_baseline_pct

  return (
    <div className="chart-card">
      <div className="card-header-row">
        <h3>💰 Carbon Tax Scenarios (iter #39)</h3>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <button className="refresh-btn" onClick={() => fetchScenarios()} disabled={loading}>
            {loading ? '⏳' : '🔄'} Recompute
          </button>
          <button className="refresh-btn" onClick={handleReset} disabled={loading}>
            ↺ Reset
          </button>
        </div>
      </div>
      <p className="chart-subtitle">
        For each carbon price, solve cost-optimal vs CO₂-optimal routing.
        Shows how much cost rises (and CO₂ falls) as carbon tax climbs.
      </p>

      {/* iter #39: KPI cards for breakeven / sensitivity */}
      <div className="kpi-grid" style={{ marginTop: '0.75rem' }}>
        <div className="kpi-card" style={{ borderTop: '3px solid #3b82f6' }}>
          <div className="kpi-label">Breakeven price</div>
          <div className="kpi-value">
            {data.breakeven_price_sek_per_kg !== null
              ? `×${data.breakeven_price_sek_per_kg}`
              : '—'}
            <span className="kpi-unit"> SEK/kg</span>
          </div>
          {data.breakeven_gap_sek !== null && (
            <div style={{ fontSize: '0.7rem', color: '#94a3b8', marginTop: '0.25rem' }}>
              gap: {data.breakeven_gap_sek.toFixed(0)} SEK
            </div>
          )}
        </div>
        <div className="kpi-card" style={{ borderTop: '3px solid #22c55e' }}>
          <div className="kpi-label">Baseline price</div>
          <div className="kpi-value">
            {data.baseline_carbon_price_sek_per_kg !== null
              ? `×${data.baseline_carbon_price_sek_per_kg}`
              : '—'}
            <span className="kpi-unit"> SEK/kg</span>
          </div>
        </div>
        <div className="kpi-card" style={{ borderTop: '3px solid #f59e0b' }}>
          <div className="kpi-label">Max true cost</div>
          <div className="kpi-value">{maxTrueCost.toFixed(0)}</div>
          <div style={{ fontSize: '0.7rem', color: '#94a3b8', marginTop: '0.25rem' }}>
            SEK @ max tax
            {totalCostDeltaPct !== null && totalCostDeltaPct !== undefined && (
              <> (+{totalCostDeltaPct.toFixed(1)}%)</>
            )}
          </div>
        </div>
        <div className="kpi-card" style={{ borderTop: '3px solid #8b5cf6' }}>
          <div className="kpi-label">CO₂ Δ at max tax</div>
          <div className="kpi-value" style={{
            color: totalCo2SavedPct < 0 ? '#22c55e' : totalCo2SavedPct > 0 ? '#ef4444' : '#94a3b8',
          }}>
            {totalCo2SavedPct !== null && totalCo2SavedPct !== undefined
              ? `${totalCo2SavedPct >= 0 ? '+' : ''}${totalCo2SavedPct.toFixed(1)}%`
              : '—'}
          </div>
        </div>
      </div>

      {/* iter #39: Custom scenario input */}
      <div style={{ marginTop: '1rem', display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ color: '#94a3b8', fontSize: '0.85rem' }}>+ Custom price:</span>
        <input
          type="number"
          step="0.1"
          min="0"
          max="20"
          placeholder="e.g. 2.0"
          value={customPriceInput}
          onChange={e => setCustomPriceInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') handleAddCustom() }}
          style={{
            padding: '0.35rem 0.6rem',
            background: '#0f172a', color: '#e2e8f0',
            border: '1px solid #475569', borderRadius: '4px',
            width: '100px', fontSize: '0.85rem',
          }}
        />
        <button
          type="button"
          onClick={handleAddCustom}
          disabled={!customPriceInput || loading}
          style={{
            padding: '0.35rem 0.8rem',
            background: customPriceInput ? '#3b82f6' : '#475569',
            color: '#fff', border: 'none', borderRadius: '4px',
            cursor: customPriceInput ? 'pointer' : 'not-allowed',
            fontSize: '0.85rem',
          }}
        >
          Add scenario
        </button>
        <span style={{ color: '#64748b', fontSize: '0.75rem' }}>
          {prices.length} scenario{prices.length === 1 ? '' : 's'} ({prices.join(', ')} SEK/kg)
        </span>
      </div>

      {error && (
        <div className="error-banner" style={{ marginTop: '0.5rem', fontSize: '0.8rem' }}>
          ⚠️ {error}
        </div>
      )}

      {/* iter #39: Toggle for Pareto view */}
      <div style={{ marginTop: '1rem', display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
        <span style={{ color: '#94a3b8', fontSize: '0.85rem' }}>View:</span>
        <button
          type="button"
          onClick={() => setShowPareto(!showPareto)}
          style={{
            padding: '0.3rem 0.6rem',
            background: showPareto ? '#3b82f6' : '#1e293b',
            color: showPareto ? '#fff' : '#94a3b8',
            border: '1px solid #475569', borderRadius: '4px',
            cursor: 'pointer', fontSize: '0.8rem',
          }}
        >
          {showPareto ? '📈 Pareto scatter' : '📊 Cost bars'}
        </button>
      </div>

      {showPareto && paretoData.length > 0 ? (
        <div className="chart-row" style={{ height: 280, marginTop: '0.75rem' }}>
          <ResponsiveContainer width="100%" height="100%">
            <ScatterChart margin={{ top: 10, right: 30, left: 50, bottom: 30 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#475569" />
              <XAxis
                type="number"
                dataKey="cost_sek"
                name="Cost (SEK)"
                stroke="#94a3b8"
                label={{ value: 'Cost (SEK)', position: 'insideBottom', offset: -15, fill: '#94a3b8' }}
              />
              <YAxis
                type="number"
                dataKey="co2_kg"
                name="CO₂ (kg)"
                stroke="#94a3b8"
                label={{ value: 'CO₂ (kg)', angle: -90, position: 'insideLeft', fill: '#94a3b8' }}
              />
              <ZAxis range={[40, 200]} />
              <Tooltip
                contentStyle={{ background: '#1e293b', border: '1px solid #475569' }}
                cursor={{ strokeDasharray: '3 3' }}
                formatter={(value, name) => {
                  if (name === 'cost_sek') return [value.toFixed(0), 'Cost (SEK)']
                  if (name === 'co2_kg') return [value.toFixed(1), 'CO₂ (kg)']
                  return [value, name]
                }}
                labelFormatter={(_, payload) => payload?.[0]?.payload?.label || ''}
              />
              <Legend />
              {/* Group by carbon price so each tax level gets its own color */}
              {data.scenarios.map(s => (
                <Scatter
                  key={s.carbon_price_sek_per_kg}
                  name={labelFor(s.carbon_price_sek_per_kg)}
                  data={paretoData.filter(p => p.carbon_price === s.carbon_price_sek_per_kg)}
                  fill={priceColor(s.carbon_price_sek_per_kg)}
                />
              ))}
            </ScatterChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <div className="chart-row" style={{ height: 260, marginTop: '0.75rem' }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chartData} margin={{ top: 5, right: 20, left: 50, bottom: 5 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#475569" />
              <XAxis dataKey="name" stroke="#94a3b8" />
              <YAxis stroke="#94a3b8" />
              <Tooltip contentStyle={{ background: '#1e293b', border: '1px solid #475569' }} />
              <Legend />
              <Bar dataKey="Cost-optimal (SEK)" fill="#3b82f6" />
              <Bar dataKey="CO₂-optimal (SEK)" fill="#22c55e" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* iter #39: enhanced table with delta % columns */}
      <div className="scenarios-table-wrap" style={{ marginTop: '1rem' }}>
        <table className="scenarios-table">
          <thead>
            <tr>
              <th>Scenario</th>
              <th>Price (SEK/kg)</th>
              <th>True total cost</th>
              <th>Δ vs base</th>
              <th>CO₂ (kg)</th>
              <th>Δ CO₂</th>
              <th>vs co2-opt</th>
            </tr>
          </thead>
          <tbody>
            {data.scenarios.map(s => {
              const trueCost = s.true_total_cost_cost_opt
              const co2Cost = s.true_total_cost_co2_opt
              const diffVsCo2Opt = (trueCost !== null && co2Cost !== null)
                ? co2Cost - trueCost
                : null
              return (
                <tr key={s.carbon_price_sek_per_kg}>
                  <td>
                    <span style={{ display: 'inline-block', width: '8px', height: '8px', borderRadius: '50%', background: priceColor(s.carbon_price_sek_per_kg), marginRight: '0.4rem' }} />
                    {labelFor(s.carbon_price_sek_per_kg)}
                  </td>
                  <td style={{ fontFamily: 'monospace' }}>{s.carbon_price_sek_per_kg}</td>
                  <td style={{ fontFamily: 'monospace' }}>
                    {trueCost !== null && trueCost !== undefined ? trueCost.toFixed(0) : '—'}
                  </td>
                  <td style={{
                    fontFamily: 'monospace',
                    color: s.delta_from_baseline_pct > 0 ? '#f59e0b' : s.delta_from_baseline_pct < 0 ? '#22c55e' : '#94a3b8',
                  }}>
                    {s.delta_from_baseline_pct !== null && s.delta_from_baseline_pct !== undefined
                      ? `${s.delta_from_baseline_pct >= 0 ? '+' : ''}${s.delta_from_baseline_pct.toFixed(1)}%`
                      : '—'}
                  </td>
                  <td style={{ fontFamily: 'monospace' }}>
                    {s.cost_optimal?.co2_kg !== null && s.cost_optimal?.co2_kg !== undefined
                      ? s.cost_optimal.co2_kg.toFixed(1)
                      : '—'}
                  </td>
                  <td style={{
                    fontFamily: 'monospace',
                    color: s.co2_delta_from_baseline_pct < 0 ? '#22c55e' : s.co2_delta_from_baseline_pct > 0 ? '#ef4444' : '#94a3b8',
                  }}>
                    {s.co2_delta_from_baseline_pct !== null && s.co2_delta_from_baseline_pct !== undefined
                      ? `${s.co2_delta_from_baseline_pct >= 0 ? '+' : ''}${s.co2_delta_from_baseline_pct.toFixed(1)}%`
                      : '—'}
                  </td>
                  <td style={{ fontFamily: 'monospace', color: diffVsCo2Opt > 0 ? '#22c55e' : '#94a3b8' }}>
                    {diffVsCo2Opt !== null
                      ? `${diffVsCo2Opt >= 0 ? '+' : ''}${diffVsCo2Opt.toFixed(0)} SEK`
                      : '—'}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        <div style={{ fontSize: '0.75rem', color: '#64748b', marginTop: '0.5rem' }}>
          "True total cost" = fuel + tax × CO₂ for the cost-optimal routing.
          Δ vs base = % change vs the no-tax scenario (same routing strategy).
          vs co2-opt = SEK more you'd pay by staying cost-optimal vs switching to co2-optimal.
        </div>
      </div>
    </div>
  )
}

export default CarbonScenarios
