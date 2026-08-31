/**
 * ForecastPanel - KPI forecast dashboard (iter #26)
 *
 * 数据源: GET /api/persistence/forecast
 *
 * 显示:
 * - 顶部 4 个 KPI: horizon / history_n / last_sim_day / n_metrics
 * - 4 个 metric card (cost_sek / co2_kg / util_pct / matches):
 *   - 当前 trend (up / down / flat) + slope_per_day + R²
 *   - 预测值 (next horizon sim_days) + 95% CI
 * - Recharts LineChart: history + forecast + 95% CI band
 * - 参数 control: horizon (1-30), history_n (2-90)
 *
 * URL state (iter #25): ?forecast_horizon=7&forecast_history_n=14&forecast_metric=cost_sek
 *
 * 自动 refresh 每 60s
 *
 * 用途:
 * - 7 天成本预测 (预算规划)
 * - CO2 排放趋势预测 (环境报告)
 * - 车队利用率预测 (容量规划)
 */

import { useState, useEffect } from 'react'
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ReferenceArea,
} from 'recharts'

import { LoadingSpinner } from '../common/LoadingSpinner'
import { useUrlState } from '../../hooks/useUrlState'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'

const REFRESH_INTERVAL_MS = 60000

const METRIC_CONFIG = {
  cost_sek:  { label: '💰 Cost (SEK)',  color: '#ef4444', unit: 'SEK' },
  co2_kg:    { label: '🌱 CO₂ (kg)',    color: '#22c55e', unit: 'kg' },
  util_pct:  { label: '🚛 Utilization',  color: '#3b82f6', unit: '%' },
  matches:   { label: '🤝 Matches',     color: '#8b5cf6', unit: 'cnt' },
}

const TREND_ICONS = {
  up:   { icon: '📈', label: 'Rising',  color: '#ef4444' },
  down: { icon: '📉', label: 'Falling', color: '#22c55e' },
  flat: { icon: '➡️', label: 'Stable',  color: '#94a3b8' },
}

const HORIZON_OPTIONS = [3, 7, 14, 21, 30]
const HISTORY_OPTIONS = [7, 14, 21, 30, 60, 90]

function KpiCard({ label, value, unit, accent }) {
  return (
    <div className="kpi-card" style={{ borderTop: `3px solid ${accent}` }}>
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">
        {value}
        <span className="kpi-unit">{unit}</span>
      </div>
    </div>
  )
}

function MetricCard({ metric, data }) {
  const config = METRIC_CONFIG[metric] || { label: metric, color: '#94a3b8', unit: '' }
  const trend = TREND_ICONS[data.trend] || TREND_ICONS.flat

  // Average forecast value (for "next 7 days avg" display)
  const avgForecast = data.forecast.length > 0
    ? data.forecast.reduce((s, f) => s + f.value, 0) / data.forecast.length
    : 0

  // Last forecast value
  const lastForecast = data.forecast.length > 0
    ? data.forecast[data.forecast.length - 1].value
    : 0

  return (
    <div className="forecast-metric-card">
      <div className="forecast-metric-header">
        <h4>{config.label}</h4>
        <span
          className="forecast-trend-badge"
          style={{ backgroundColor: trend.color + '20', color: trend.color, borderLeft: `3px solid ${trend.color}` }}
        >
          {trend.icon} {trend.label}
        </span>
      </div>

      <div className="forecast-metric-stats">
        <div className="forecast-stat">
          <div className="forecast-stat-label">Slope/day</div>
          <div className="forecast-stat-value">{data.slope_per_day}</div>
        </div>
        <div className="forecast-stat">
          <div className="forecast-stat-label">R²</div>
          <div className="forecast-stat-value">{data.r_squared}</div>
        </div>
        <div className="forecast-stat">
          <div className="forecast-stat-label">Mean</div>
          <div className="forecast-stat-value">{data.mean_value}</div>
        </div>
      </div>

      <div className="forecast-metric-predictions">
        <div className="forecast-prediction-row">
          <span className="forecast-prediction-label">Avg forecast:</span>
          <strong>{avgForecast.toFixed(2)} {config.unit}</strong>
        </div>
        <div className="forecast-prediction-row">
          <span className="forecast-prediction-label">End of horizon:</span>
          <strong>{lastForecast.toFixed(2)} {config.unit}</strong>
        </div>
      </div>
    </div>
  )
}

function ForecastChart({ metric, data }) {
  const config = METRIC_CONFIG[metric] || { label: metric, color: '#94a3b8' }

  // Combine history + forecast into one chart dataset
  const chartData = [
    ...data.history.map(h => ({
      sim_day: h.sim_day,
      actual: h.value,
      forecast: null,
      lower: null,
      upper: null,
    })),
    ...data.forecast.map(f => ({
      sim_day: f.sim_day,
      actual: null,
      forecast: f.value,
      lower: f.lower_95,
      upper: f.upper_95,
    })),
  ]

  if (chartData.length === 0) return null

  return (
    <div className="forecast-chart">
      <h5>{config.label} — History + Forecast</h5>
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={chartData} margin={{ top: 5, right: 30, left: 60, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#475569" />
          <XAxis
            dataKey="sim_day"
            stroke="#94a3b8"
            label={{ value: 'sim_day', position: 'insideBottom', offset: -5, fill: '#94a3b8' }}
          />
          <YAxis stroke="#94a3b8" />
          <Tooltip
            contentStyle={{ background: '#1e293b', border: '1px solid #475569' }}
            labelStyle={{ color: '#94a3b8' }}
          />
          <Legend />
          {/* CI band */}
          {data.forecast.length > 0 && (
            <ReferenceArea
              x1={data.forecast[0].sim_day}
              x2={data.forecast[data.forecast.length - 1].sim_day}
              fill="#1e293b"
              fillOpacity={0.3}
              label={{ value: 'Forecast', fill: '#94a3b8', fontSize: 11 }}
            />
          )}
          <Line
            type="monotone"
            dataKey="actual"
            stroke={config.color}
            strokeWidth={2}
            name="Actual"
            dot={{ r: 3 }}
            connectNulls={false}
          />
          <Line
            type="monotone"
            dataKey="forecast"
            stroke={config.color}
            strokeWidth={2}
            strokeDasharray="5 5"
            name="Forecast"
            dot={{ r: 3 }}
            connectNulls={false}
          />
          <Line
            type="monotone"
            dataKey="upper"
            stroke={config.color}
            strokeWidth={1}
            strokeOpacity={0.4}
            strokeDasharray="2 2"
            name="Upper 95%"
            dot={false}
            connectNulls={false}
          />
          <Line
            type="monotone"
            dataKey="lower"
            stroke={config.color}
            strokeWidth={1}
            strokeOpacity={0.4}
            strokeDasharray="2 2"
            name="Lower 95%"
            dot={false}
            connectNulls={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

export function ForecastPanel() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  // iter #25: URL-synced params
  const [horizon] = useUrlState('forecast_horizon', 7, 'int')
  const [historyN] = useUrlState('forecast_history_n', 14, 'int')

  const fetchForecast = async () => {
    try {
      const url = `${API_BASE}/persistence/forecast?horizon=${horizon}&history_n=${historyN}`
      const resp = await fetch(url)
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}: ${await resp.text()}`)
      }
      const json = await resp.json()
      setData(json)
      setError(null)
    } catch (e) {
      setError(e.message || 'fetch failed')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchForecast()
    const interval = setInterval(fetchForecast, REFRESH_INTERVAL_MS)
    return () => clearInterval(interval)
  }, [horizon, historyN])

  if (loading) return <LoadingSpinner label="Loading forecast…" />
  if (error) {
    return (
      <div className="chart-card">
        <h3>🔮 KPI Forecast (iter #26)</h3>
        <div className="error-banner">⚠️ {error}</div>
      </div>
    )
  }
  if (!data) return null

  const { last_sim_day, forecast_sim_days, metrics = {} } = data
  const metricKeys = Object.keys(metrics)

  return (
    <div className="chart-card">
      <div className="chart-card-header">
        <h3>🔮 KPI Forecast (iter #26)</h3>
        <span className="chart-card-sub">
          Linear regression on last {historyN} sim_days · {horizon}-day horizon
        </span>
      </div>

      {/* Top KPI cards */}
      <div className="kpi-grid">
        <KpiCard label="Horizon" value={horizon} unit=" days" accent="#3b82f6" />
        <KpiCard label="History" value={historyN} unit=" days" accent="#8b5cf6" />
        <KpiCard label="Last sim_day" value={last_sim_day ?? '—'} unit="" accent="#06b6d4" />
        <KpiCard
          label="Metrics"
          value={metricKeys.length}
          unit=""
          accent="#22c55e"
        />
      </div>

      {data.note && (
        <div className="info-banner" style={{ marginTop: '0.5rem' }}>
          ℹ️ {data.note}
        </div>
      )}

      {metricKeys.length === 0 ? (
        <div className="empty-state">
          🔮 Not enough data to forecast. Need at least 2 historical sim_days.
        </div>
      ) : (
        <>
          {/* Metric cards grid */}
          <div className="forecast-metric-grid">
            {metricKeys.map((metric) => (
              <MetricCard key={metric} metric={metric} data={metrics[metric]} />
            ))}
          </div>

          {/* Forecast range summary */}
          <div className="forecast-range">
            <span className="forecast-range-label">Forecast range:</span>{' '}
            <strong>
              sim_day {forecast_sim_days[0]} → {forecast_sim_days[forecast_sim_days.length - 1]}
            </strong>
            <span className="forecast-range-meta">
              ({forecast_sim_days.length} days · dashed line in charts below)
            </span>
          </div>

          {/* Charts for each metric */}
          {metricKeys.map((metric) => (
            <ForecastChart key={`chart-${metric}`} metric={metric} data={metrics[metric]} />
          ))}
        </>
      )}
    </div>
  )
}

export default ForecastPanel
