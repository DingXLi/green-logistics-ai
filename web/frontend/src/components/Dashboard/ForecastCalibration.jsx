/**
 * ForecastCalibration.jsx — iter #42
 *
 * Shows forecast accuracy stats (MAE / MAPE / RMSE / bias)
 * from /api/persistence/forecast-calibration. Operators can
 * see at a glance "how good have our forecasts been?".
 */
import { useState, useEffect } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'

function fmtNum(v, suffix = '') {
  if (v === null || v === undefined) return '—'
  return `${v.toFixed(2)}${suffix}`
}

function biasLabel(bias) {
  if (bias === null || bias === undefined) return { label: '—', color: '#94a3b8' }
  if (bias > 0.5) return { label: 'under-predicts', color: '#ef4444' }
  if (bias < -0.5) return { label: 'over-predicts', color: '#3b82f6' }
  return { label: 'balanced', color: '#10b981' }
}

export function ForecastCalibration() {
  const [metric, setMetric] = useState('')
  const [method, setMethod] = useState('')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchCalibration = (m = metric, mth = method) => {
    setLoading(true)
    setError(null)
    const params = new URLSearchParams()
    if (m) params.set('metric', m)
    if (mth) params.set('method', mth)
    fetch(`${API_BASE}/persistence/forecast-calibration?${params}`)
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
    fetchCalibration('', '')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleFilterChange = (newMetric, newMethod) => {
    setMetric(newMetric)
    setMethod(newMethod)
    fetchCalibration(newMetric, newMethod)
  }

  if (loading && !data) {
    return (
      <div className="fcst-calib-panel">
        <div className="fc-header">
          <h3>📊 Forecast Calibration (iter #42)</h3>
        </div>
        <LoadingSpinner label="Loading calibration stats..." />
      </div>
    )
  }

  if (error) {
    return (
      <div className="fcst-calib-panel">
        <div className="fc-header">
          <h3>📊 Forecast Calibration (iter #42)</h3>
        </div>
        <div className="fc-error">Error: {error}</div>
      </div>
    )
  }

  const overall = data?.overall || {}
  const byMetric = data?.by_metric || {}
  const byMethod = data?.by_method || {}
  const bias = biasLabel(overall.bias)

  return (
    <div className="fcst-calib-panel">
      <div className="fc-header">
        <h3>📊 Forecast Calibration <span className="iter-badge">iter #42</span></h3>
        <button className="refresh-btn" onClick={() => fetchCalibration()} disabled={loading}>
          {loading ? 'Refreshing...' : '🔄 Refresh'}
        </button>
      </div>

      <div className="fc-controls">
        <label className="fc-label">
          <span>Metric:</span>
          <select
            value={metric}
            onChange={e => handleFilterChange(e.target.value, method)}
            className="fc-select"
          >
            <option value="">all</option>
            <option value="cost_sek">cost_sek</option>
            <option value="co2_kg">co2_kg</option>
            <option value="util_pct">util_pct</option>
            <option value="matches">matches</option>
          </select>
        </label>
        <label className="fc-label">
          <span>Method:</span>
          <select
            value={method}
            onChange={e => handleFilterChange(metric, e.target.value)}
            className="fc-select"
          >
            <option value="">all</option>
            <option value="linear">linear</option>
            <option value="moving_average">moving_average</option>
            <option value="exponential_smoothing">exponential_smoothing</option>
          </select>
        </label>
        <div className="fc-stats-inline">
          <span>Total: <strong>{data.n_total_predictions}</strong></span>
          <span>Evaluated: <strong>{data.n_evaluated}</strong></span>
          <span>Pending: <strong>{data.n_pending}</strong></span>
        </div>
      </div>

      {overall.n_evaluated === 0 ? (
        <div className="fc-empty">
          No evaluated predictions yet. Run a forecast now, then wait for the
          predicted sim_days to occur to accumulate accuracy data.
        </div>
      ) : (
        <>
          <div className="cs-kpi-row">
            <div className="cs-kpi">
              <div className="kpi-label">MAE</div>
              <div className="kpi-value">{fmtNum(overall.mae)}</div>
              <div className="kpi-sub">mean abs error</div>
            </div>
            <div className="cs-kpi">
              <div className="kpi-label">RMSE</div>
              <div className="kpi-value">{fmtNum(overall.rmse)}</div>
              <div className="kpi-sub">penalizes outliers</div>
            </div>
            <div className="cs-kpi">
              <div className="kpi-label">MAPE</div>
              <div className="kpi-value">{overall.mape_pct !== null ? `${fmtNum(overall.mape_pct)}%` : '—'}</div>
              <div className="kpi-sub">% error (lower = better)</div>
            </div>
            <div className="cs-kpi">
              <div className="kpi-label">Bias</div>
              <div className="kpi-value" style={{ color: bias.color }}>{fmtNum(overall.bias)}</div>
              <div className="kpi-sub" style={{ color: bias.color }}>{bias.label}</div>
            </div>
          </div>

          {Object.keys(byMetric).length > 0 && (
            <div className="fc-section">
              <h4>By metric ({Object.keys(byMetric).length})</h4>
              <div style={{ overflowX: 'auto' }}>
                <table className="cs-table">
                  <thead>
                    <tr>
                      <th>Metric</th>
                      <th>N</th>
                      <th>MAE</th>
                      <th>RMSE</th>
                      <th>MAPE%</th>
                      <th>Bias</th>
                      <th>Min%</th>
                      <th>Max%</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(byMetric).map(([m, s]) => (
                      <tr key={m}>
                        <td><strong>{m}</strong></td>
                        <td>{s.n_evaluated}</td>
                        <td>{fmtNum(s.mae)}</td>
                        <td>{fmtNum(s.rmse)}</td>
                        <td>{s.mape_pct !== null ? `${fmtNum(s.mape_pct)}%` : '—'}</td>
                        <td style={{ color: biasLabel(s.bias).color }}>{fmtNum(s.bias)}</td>
                        <td>{s.min_pct_err !== null ? `${fmtNum(s.min_pct_err)}%` : '—'}</td>
                        <td>{s.max_pct_err !== null ? `${fmtNum(s.max_pct_err)}%` : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {Object.keys(byMethod).length > 0 && (
            <div className="fc-section">
              <h4>By method ({Object.keys(byMethod).length})</h4>
              <div style={{ overflowX: 'auto' }}>
                <table className="cs-table">
                  <thead>
                    <tr>
                      <th>Method</th>
                      <th>N</th>
                      <th>MAE</th>
                      <th>RMSE</th>
                      <th>MAPE%</th>
                      <th>Bias</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(byMethod).map(([m, s]) => (
                      <tr key={m}>
                        <td><strong>{m}</strong></td>
                        <td>{s.n_evaluated}</td>
                        <td>{fmtNum(s.mae)}</td>
                        <td>{fmtNum(s.rmse)}</td>
                        <td>{s.mape_pct !== null ? `${fmtNum(s.mape_pct)}%` : '—'}</td>
                        <td style={{ color: biasLabel(s.bias).color }}>{fmtNum(s.bias)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          <div className="cs-footnote">
            💡 MAE = mean abs error; RMSE penalizes outliers (larger error² contribution).
            Bias &gt; 0 means model under-predicts; bias &lt; 0 means over-predicts.
            "Pending" = predictions whose target sim_day hasn't happened yet.
          </div>
        </>
      )}
    </div>
  )
}

export default ForecastCalibration
