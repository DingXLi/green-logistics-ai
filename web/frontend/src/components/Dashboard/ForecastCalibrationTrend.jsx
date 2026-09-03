/**
 * ForecastCalibrationTrend.jsx — iter #43
 *
 * Shows cumulative forecast accuracy trend (MAE / RMSE / MAPE / bias)
 * over forecast_sim_day from
 * /api/persistence/forecast-calibration/trend.
 */
import { useState, useEffect } from 'react'
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, Legend,
} from 'recharts'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'

function fmtNum(v, suffix = '') {
  if (v === null || v === undefined) return '—'
  return `${v.toFixed(2)}${suffix}`
}

export function ForecastCalibrationTrend() {
  const [metric, setMetric] = useState('')
  const [method, setMethod] = useState('')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchTrend = (m = metric, mth = method) => {
    setLoading(true)
    setError(null)
    const params = new URLSearchParams()
    if (m) params.set('metric', m)
    if (mth) params.set('method', mth)
    fetch(`${API_BASE}/persistence/forecast-calibration/trend?${params}`)
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
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
    fetchTrend('', '')
    const id = setInterval(() => fetchTrend(metric, method), 60000)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (loading && !data) {
    return (
      <div className="fcst-trend-panel">
        <div className="fct-header">
          <h3>📈 Forecast Calibration Trend (iter #43)</h3>
        </div>
        <LoadingSpinner label="Loading trend..." />
      </div>
    )
  }

  if (error) {
    return (
      <div className="fcst-trend-panel">
        <div className="fct-header">
          <h3>📈 Forecast Calibration Trend (iter #43)</h3>
        </div>
        <div className="fct-error">Error: {error}</div>
      </div>
    )
  }

  const trend = data?.trend || []
  const chartData = trend.map(t => ({
    simDay: t.bucket_sim_day,
    mae: t.cumulative_mae,
    rmse: t.cumulative_rmse,
    mape: t.cumulative_mape_pct,
    bias: t.cumulative_bias,
    n: t.n_evaluated,
  }))

  // Last bucket = current state
  const last = trend[trend.length - 1]

  return (
    <div className="fcst-trend-panel">
      <div className="fct-header">
        <h3>📈 Forecast Calibration Trend <span className="iter-badge">iter #43</span></h3>
        <button className="refresh-btn" onClick={() => fetchTrend()} disabled={loading}>
          {loading ? 'Refreshing...' : '🔄 Refresh'}
        </button>
      </div>

      <div className="fct-controls">
        <label className="fct-label">
          <span>Metric:</span>
          <select
            value={metric}
            onChange={e => { setMetric(e.target.value); fetchTrend(e.target.value, method) }}
            className="fct-select"
          >
            <option value="">all</option>
            <option value="cost_sek">cost_sek</option>
            <option value="co2_kg">co2_kg</option>
            <option value="util_pct">util_pct</option>
            <option value="matches">matches</option>
          </select>
        </label>
        <label className="fct-label">
          <span>Method:</span>
          <select
            value={method}
            onChange={e => { setMethod(e.target.value); fetchTrend(metric, e.target.value) }}
            className="fct-select"
          >
            <option value="">all</option>
            <option value="linear">linear</option>
            <option value="moving_average">moving_average</option>
            <option value="exponential_smoothing">exponential_smoothing</option>
          </select>
        </label>
        <div className="fct-stats-inline">
          <span>Buckets: <strong>{trend.length}</strong></span>
          {last && <span>Latest n: <strong>{last.n_evaluated}</strong></span>}
        </div>
      </div>

      {trend.length === 0 ? (
        <div className="fct-empty">
          No evaluated predictions yet. Run a forecast now, then wait for the
          predicted sim_days to occur to accumulate trend data.
        </div>
      ) : (
        <>
          <div className="cs-chart-wrap" style={{ height: 300 }}>
            <ResponsiveContainer>
              <LineChart data={chartData} margin={{ top: 20, right: 30, left: 20, bottom: 5 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#333" />
                <XAxis
                  dataKey="simDay"
                  type="number"
                  domain={['dataMin', 'dataMax']}
                  label={{ value: 'sim_day', position: 'insideBottom', offset: -5, fill: '#888' }}
                  stroke="#888"
                />
                <YAxis stroke="#888" />
                <Tooltip
                  contentStyle={{ backgroundColor: '#1e1e1e', border: '1px solid #444' }}
                  labelFormatter={v => `sim_day ${v}`}
                />
                <Legend />
                <Line type="monotone" dataKey="mae" name="MAE" stroke="#8884d8" strokeWidth={2} dot />
                <Line type="monotone" dataKey="rmse" name="RMSE" stroke="#82ca9d" strokeWidth={2} dot />
                <Line type="monotone" dataKey="bias" name="Bias" stroke="#ffc658" strokeWidth={2} dot strokeDasharray="5 5" />
              </LineChart>
            </ResponsiveContainer>
          </div>

          <div className="cs-section">
            <h4>Cumulative stats by sim_day</h4>
            <div style={{ overflowX: 'auto' }}>
              <table className="cs-table">
                <thead>
                  <tr>
                    <th>sim_day</th>
                    <th>n</th>
                    <th>MAE</th>
                    <th>RMSE</th>
                    <th>MAPE%</th>
                    <th>Bias</th>
                  </tr>
                </thead>
                <tbody>
                  {trend.map(t => (
                    <tr key={t.bucket_sim_day}>
                      <td><strong>{t.bucket_sim_day}</strong></td>
                      <td>{t.n_evaluated}</td>
                      <td>{fmtNum(t.cumulative_mae)}</td>
                      <td>{fmtNum(t.cumulative_rmse)}</td>
                      <td>{t.cumulative_mape_pct !== null ? `${fmtNum(t.cumulative_mape_pct)}%` : '—'}</td>
                      <td>{fmtNum(t.cumulative_bias)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="cs-footnote">
            💡 Cumulative MAE/RMSE at each sim_day show how accuracy evolves
            as more predictions get evaluated. Bias trending toward 0 means
            the model is balanced (not systematically over/under-predicting).
          </div>
        </>
      )}
    </div>
  )
}

export default ForecastCalibrationTrend
