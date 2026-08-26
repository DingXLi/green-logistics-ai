/**
 * CarbonScenarios - 碳税情景对比表 + bar chart
 *
 * 数据源: GET /api/optimize/carbon-scenarios
 *
 * 显示 4 个碳税场景下 cost-optimal vs co2-optimal 的对比。
 * 帮助决策者理解碳价敏感度: "碳价涨 3 倍, cost 涨多少?"
 */
import { useState, useEffect } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'

const SCENARIO_LABELS = {
  '0.0': 'No tax',
  '1.5': 'EU ETS',
  '3.0': '2030 mid',
  '5.0': 'Aggressive',
}

export function CarbonScenarios() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchScenarios = () => {
    setLoading(true)
    fetch(`${API_BASE}/optimize/carbon-scenarios?carbon_prices=0,1.5,3,5&time_limit_seconds=3`)
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
    fetchScenarios()
  }, [])

  if (loading) return <div className="empty">Computing carbon scenarios (each runs 4 OR-Tools solves)…</div>
  if (error) return <div className="error-banner">⚠️ {error}</div>
  if (!data || !data.scenarios || data.scenarios.length === 0) {
    return <div className="empty">No scenario data available.</div>
  }

  // 准备 BarChart 数据: x = carbon price, y = cost_sek
  const chartData = data.scenarios.map(s => ({
    name: SCENARIO_LABELS[String(s.carbon_price_sek_per_kg)] || `${s.carbon_price_sek_per_kg} SEK/kg`,
    'Cost-optimal (SEK)': s.cost_optimal?.cost_sek || 0,
    'CO₂-optimal (SEK)': s.co2_optimal?.cost_sek || 0,
    'Cost-opt CO₂ (kg)': s.cost_optimal?.co2_kg || 0,
    'CO₂-opt CO₂ (kg)': s.co2_optimal?.co2_kg || 0,
  }))

  return (
    <div className="chart-card">
      <div className="card-header-row">
        <h3>💰 Carbon Tax Scenarios</h3>
        <button className="refresh-btn" onClick={fetchScenarios} disabled={loading}>
          {loading ? '⏳' : '🔄'} Recompute
        </button>
      </div>
      <p className="chart-subtitle">
        For each carbon price, solve cost-optimal vs CO₂-optimal routing.
        Shows how much cost rises as carbon price climbs.
      </p>

      <div className="scenarios-table-wrap">
        <table className="scenarios-table">
          <thead>
            <tr>
              <th>Scenario</th>
              <th>Price (SEK/kg)</th>
              <th>Cost-opt cost</th>
              <th>Cost-opt CO₂</th>
              <th>CO₂-opt cost</th>
              <th>CO₂-opt CO₂</th>
              <th>Δ cost</th>
            </tr>
          </thead>
          <tbody>
            {data.scenarios.map((s, i) => {
              const co = s.cost_optimal || {}
              const copt = s.co2_optimal || {}
              const deltaCost =
                co.cost_sek && copt.cost_sek
                  ? copt.cost_sek - co.cost_sek
                  : 0
              return (
                <tr key={s.carbon_price_sek_per_kg}>
                  <td><strong>{SCENARIO_LABELS[String(s.carbon_price_sek_per_kg)] || `${s.carbon_price_sek_per_kg} SEK/kg`}</strong></td>
                  <td>{s.carbon_price_sek_per_kg}</td>
                  <td>{co.cost_sek?.toLocaleString() ?? '—'}</td>
                  <td>{co.co2_kg?.toLocaleString() ?? '—'}</td>
                  <td>{copt.cost_sek?.toLocaleString() ?? '—'}</td>
                  <td>{copt.co2_kg?.toLocaleString() ?? '—'}</td>
                  <td className={deltaCost > 0 ? 'negative' : 'positive'}>
                    {deltaCost > 0 ? '+' : ''}{Math.round(deltaCost).toLocaleString()} SEK
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <ResponsiveContainer width="100%" height={260}>
        <BarChart data={chartData} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
          <XAxis dataKey="name" stroke="#64748b" fontSize={11} />
          <YAxis stroke="#64748b" fontSize={11} />
          <Tooltip />
          <Legend />
          <Bar dataKey="Cost-optimal (SEK)" fill="#22c55e" />
          <Bar dataKey="CO₂-optimal (SEK)" fill="#dc2626" />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}