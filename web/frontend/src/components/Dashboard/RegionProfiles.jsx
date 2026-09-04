/**
 * RegionProfiles.jsx — iter #47
 *
 * Region/city profile panel showing the 3 Swedish cities covered by the
 * simulation. Pulls from /api/regions (iter #47).
 *
 * Renders:
 * - 3 city cards: name, population, daily waste, industry focus
 * - Pie chart of estimated daily waste per city
 * - Bar chart of population vs construction_share_pct
 * - Map hint with lat/lon for each city
 */
import { useState, useEffect } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  PieChart, Pie, Cell,
} from 'recharts'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'
const COLORS = ['#3b82f6', '#10b981', '#f59e0b']

function formatNumber(n) {
  if (n == null) return '—'
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M'
  if (n >= 1_000) return (n / 1_000).toFixed(0) + 'k'
  return String(n)
}

export function RegionProfiles() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    fetch(`${API_BASE}/regions`)
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
  }, [])

  if (loading) return <LoadingSpinner label="loading region profiles..." />
  if (error) return <div className="error-banner">⚠️ {error}</div>
  if (!data) return <div className="empty">No data available.</div>

  const { regions, total_population, total_estimated_daily_waste_tons } = data

  // Pie chart data: daily waste per city
  const pieData = regions.map(r => ({
    name: r.city,
    value: r.estimated_daily_waste_tons,
  }))

  // Bar chart: population + construction_share
  const barData = regions.map(r => ({
    city: r.city,
    population: r.population,
    construction_pct: r.construction_share_pct,
  }))

  return (
    <div className="chart-card">
      <h3>🌍 Region Profiles <span className="iter-badge">iter #47</span></h3>
      <p className="chart-subtitle">
        Swedish cities covered by the simulation. Data: SCB kommunstatistik 2023 +
        Avfall Sverige 2023 report.
      </p>

      <div className="rp-summary">
        <div className="cs-kpi">
          <div className="kpi-label">Regions</div>
          <div className="kpi-value">{regions.length}</div>
          <div className="kpi-sub">cities</div>
        </div>
        <div className="cs-kpi">
          <div className="kpi-label">Total Pop</div>
          <div className="kpi-value" style={{ color: '#3b82f6' }}>
            {formatNumber(total_population)}
          </div>
          <div className="kpi-sub">residents</div>
        </div>
        <div className="cs-kpi">
          <div className="kpi-label">Daily Waste</div>
          <div className="kpi-value" style={{ color: '#10b981' }}>
            {total_estimated_daily_waste_tons.toFixed(0)}t
          </div>
          <div className="kpi-sub">across all cities</div>
        </div>
      </div>

      <div className="rp-cards">
        {regions.map((r, i) => (
          <div key={r.city} className="rp-card" style={{ borderLeft: `4px solid ${COLORS[i]}` }}>
            <div className="rp-card-header">
              <h4 className="rp-card-title">{r.city}</h4>
              <span className="rp-card-pop">{formatNumber(r.population)} residents</span>
            </div>
            <div className="rp-card-body">
              <div className="rp-card-row">
                <span className="rp-card-label">Daily waste:</span>
                <span className="rp-card-value">{r.estimated_daily_waste_tons}t</span>
              </div>
              <div className="rp-card-row">
                <span className="rp-card-label">Per capita:</span>
                <span className="rp-card-value">{r.per_capita_waste_kg} kg/yr</span>
              </div>
              <div className="rp-card-row">
                <span className="rp-card-label">Construction:</span>
                <span className="rp-card-value">{r.construction_share_pct}%</span>
              </div>
              <div className="rp-card-row">
                <span className="rp-card-label">Industry:</span>
                <span className="rp-card-value rp-card-industry">{r.industry_focus}</span>
              </div>
              <div className="rp-card-row rp-card-coords">
                <span className="rp-card-label">Coords:</span>
                <code>
                  {r.lat?.toFixed(4)}, {r.lon?.toFixed(4)}
                </code>
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className="rp-charts">
        <div className="rp-chart-block">
          <h4>📊 Daily Waste by City (tons)</h4>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={pieData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
              <XAxis dataKey="name" stroke="#94a3b8" />
              <YAxis stroke="#94a3b8" />
              <Tooltip
                contentStyle={{ background: '#0f172a', border: '1px solid #1e293b' }}
                labelStyle={{ color: '#cbd5e1' }}
              />
              <Bar dataKey="value" fill="#10b981" />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="rp-chart-block">
          <h4>📈 Waste Distribution</h4>
          <ResponsiveContainer width="100%" height={220}>
            <PieChart>
              <Pie
                data={pieData}
                dataKey="value"
                nameKey="name"
                outerRadius={70}
                label={({ name, value }) => `${name}: ${value}t`}
              >
                {pieData.map((_, i) => (
                  <Cell key={i} fill={COLORS[i]} />
                ))}
              </Pie>
              <Tooltip
                contentStyle={{ background: '#0f172a', border: '1px solid #1e293b' }}
              />
            </PieChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="rp-footnote">
        Daily waste = population × per_capita_waste_kg / 365 / 1000 (mixed household + C&D).
        Per-city material breakdown computed by get_baseline_demand() in
        data/swedish_waste_stats.py.
      </div>
    </div>
  )
}
