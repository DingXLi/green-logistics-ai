/**
 * CarbonSavingsPanel - CO2 savings vs traditional baseline (iter #54)
 *
 * 数据源: GET /api/persistence/carbon-savings
 *
 * 显示:
 * - 3 KPI 卡片: CO2 saved / savings % / co2 per ton (vs baseline)
 * - Baseline 选择 dropdown (truck_heavy / truck_medium / truck_light /
 *   traditional_baseline / optimized_fleet)
 * - 进度条: actual CO2 vs baseline CO2
 *
 * 用途: 让用户看到:
 *       - 多智能体优化相对传统 transport 的 CO2 savings
 *       - 选择不同的 baseline (e.g., light truck vs heavy truck)
 */

import { useState, useEffect } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'
const REFRESH_MS = 60_000

const DEFAULT_BASELINE = 'traditional_baseline'

function _safeNum(v, decimals = 0) {
  if (v === null || v === undefined) return '—'
  return Number(v).toFixed(decimals)
}

function _savingsColor(pct) {
  if (pct === null || pct === undefined) return '#64748b'
  if (pct >= 80) return '#16a34a'  // bright green
  if (pct >= 50) return '#22c55e'  // green
  if (pct >= 20) return '#f59e0b'  // orange
  return '#dc2626'  // red
}

function _factorLabel(key) {
  const labels = {
    truck_heavy: '🚛 Heavy Truck',
    truck_medium: '🚚 Medium Truck',
    truck_light: '🚐 Light Truck',
    traditional_baseline: '🏚️ Traditional (Mixed)',
    optimized_fleet: '✅ Optimized Fleet',
  }
  return labels[key] || key
}

export function CarbonSavingsPanel() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [baseline, setBaseline] = useState(DEFAULT_BASELINE)

  const fetchData = async (b = baseline) => {
    try {
      setLoading(true)
      const res = await fetch(`${API_BASE}/persistence/carbon-savings?baseline_factor_key=${b}`)
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
    fetchData(baseline)
    const id = setInterval(() => fetchData(baseline), REFRESH_MS)
    return () => clearInterval(id)
  }, [baseline])

  if (loading && !data) {
    return <LoadingSpinner size="md" label="Loading carbon savings…" />
  }

  if (error || !data) {
    return (
      <div className="card carbon-savings-panel">
        <h3>🌱 Carbon Savings (iter #54)</h3>
        <div className="empty-state">
          {error ? `Failed to fetch: ${error}` : 'No data yet. Run simulations to see CO2 savings.'}
        </div>
      </div>
    )
  }

  // Compute progress percentage (actual / baseline)
  const actualRatio = data.baseline_co2_kg > 0
    ? (data.actual_co2_kg / data.baseline_co2_kg) * 100
    : 0

  return (
    <div className="card carbon-savings-panel">
      <div className="card-header-row">
        <h3>🌱 Carbon Savings vs Baseline</h3>
        <div className="card-controls">
          <select
            className="baseline-select"
            value={baseline}
            onChange={(e) => setBaseline(e.target.value)}
            aria-label="Baseline transport mode"
          >
            {(data.available_factors || []).map((f) => (
              <option key={f} value={f}>{_factorLabel(f)}</option>
            ))}
          </select>
        </div>
      </div>

      <div className="kpi-grid">
        <div
          className="kpi-card"
          style={{ borderTop: `3px solid ${_savingsColor(data.savings_pct)}` }}
        >
          <div className="kpi-label">CO₂ Saved</div>
          <div className="kpi-value">
            {_safeNum(data.savings_co2_kg, 0)}
            <span className="kpi-unit"> kg</span>
          </div>
          <div className="kpi-sub">
            over {data.n_cycles} cycle{data.n_cycles !== 1 ? 's' : ''}
          </div>
        </div>

        <div
          className="kpi-card"
          style={{ borderTop: `3px solid ${_savingsColor(data.savings_pct)}` }}
        >
          <div className="kpi-label">Savings %</div>
          <div className="kpi-value">
            {data.savings_pct !== null && data.savings_pct !== undefined
              ? data.savings_pct.toFixed(1)
              : '—'}
            <span className="kpi-unit">%</span>
          </div>
          <div className="kpi-sub">vs {_factorLabel(data.baseline_factor_key)}</div>
        </div>

        <div className="kpi-card" style={{ borderTop: '3px solid #3b82f6' }}>
          <div className="kpi-label">CO₂ per ton (actual)</div>
          <div className="kpi-value">
            {_safeNum(data.co2_per_ton_actual_kg, 2)}
            <span className="kpi-unit"> kg/t</span>
          </div>
          <div className="kpi-sub">
            baseline: {_safeNum(data.co2_per_ton_baseline_kg, 2)} kg/t
          </div>
        </div>

        <div className="kpi-card" style={{ borderTop: '3px solid #8b5cf6' }}>
          <div className="kpi-label">Distance Tracked</div>
          <div className="kpi-value">
            {_safeNum(data.total_distance_km, 0)}
            <span className="kpi-unit"> km</span>
          </div>
          <div className="kpi-sub">
            {data.total_tons.toFixed(1)} t transported
          </div>
        </div>
      </div>

      <div className="savings-bar-wrapper">
        <div className="savings-bar-label">
          Actual CO₂ vs Baseline CO₂ ({_safeNum(data.baseline_co2_kg, 0)} kg baseline)
        </div>
        <div className="savings-bar-track">
          <div
            className="savings-bar-fill"
            style={{
              width: `${Math.min(100, actualRatio)}%`,
              background: actualRatio <= 30 ? '#16a34a' : actualRatio <= 60 ? '#22c55e' : '#f59e0b',
            }}
            title={`Actual: ${data.actual_co2_kg} kg (${actualRatio.toFixed(1)}% of baseline)`}
          />
          <span className="savings-bar-text">
            {actualRatio.toFixed(1)}% of baseline
          </span>
        </div>
        <div className="savings-bar-meta">
          Actual: <strong>{_safeNum(data.actual_co2_kg, 0)} kg</strong> · 
          Baseline: <strong>{_safeNum(data.baseline_co2_kg, 0)} kg</strong> · 
          Saved: <strong style={{ color: _savingsColor(data.savings_pct) }}>
            {_safeNum(data.savings_co2_kg, 0)} kg
          </strong>
        </div>
      </div>

      <div className="card-footnote">
        Source: EEA 2023 emission factors (well-to-wheel) · 
        {data.baseline_factor_kg_per_ton_km} kg CO₂/ton-km baseline · 
        iter #54 · auto-refresh 60s
      </div>
    </div>
  )
}