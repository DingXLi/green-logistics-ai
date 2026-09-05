/**
 * WeatherWidget.jsx — iter #50
 *
 * SMHI weather forecast widget showing current + 24h forecast.
 * Defaults to Borås depot coords.
 *
 * Data source: GET /api/weather?lat=X&lon=Y
 *
 * Renders:
 * - Current temperature + precipitation + wind + humidity
 * - 24h average forecast
 * - Auto-refresh every 30 min (weather changes slowly)
 * - City selector: Borås / Göteborg / Stockholm
 */
import { useState, useEffect, useMemo } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'
const REFRESH_INTERVAL_MS = 30 * 60 * 1000  // 30 min

const CITIES = [
  { name: 'Borås', lat: 57.7089, lon: 14.1618 },
  { name: 'Göteborg', lat: 57.7089, lon: 11.9746 },
  { name: 'Stockholm', lat: 59.3293, lon: 18.0686 },
]

function tempColor(t) {
  if (t == null) return '#94a3b8'
  if (t < 0) return '#3b82f6'   // cold = blue
  if (t < 10) return '#06b6d4'  // cool = cyan
  if (t < 20) return '#10b981'  // mild = green
  if (t < 30) return '#f59e0b'  // warm = orange
  return '#ef4444'              // hot = red
}

function weatherEmoji(summary) {
  if (!summary) return '🌡️'
  const s = summary.toLowerCase()
  if (s.includes('rain') || s.includes('wet')) return '🌧️'
  if (s.includes('snow') || s.includes('cold')) return '❄️'
  if (s.includes('hot') || s.includes('warm')) return '☀️'
  if (s.includes('mild') || s.includes('dry')) return '🌤️'
  if (s.includes('cloud')) return '☁️'
  return '🌡️'
}

export function WeatherWidget() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [cityIdx, setCityIdx] = useState(0)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    const city = CITIES[cityIdx]
    fetch(`${API_BASE}/weather?lat=${city.lat}&lon=${city.lon}`)
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
  }, [cityIdx])

  // Auto-refresh every 30 min
  useEffect(() => {
    const id = setInterval(() => {
      const city = CITIES[cityIdx]
      fetch(`${API_BASE}/weather?lat=${city.lat}&lon=${city.lon}`)
        .then(r => r.json())
        .then(d => setData(d))
        .catch(e => setError(e.message))
    }, REFRESH_INTERVAL_MS)
    return () => clearInterval(id)
  }, [cityIdx])

  if (loading && !data) return <LoadingSpinner label="loading weather..." />
  if (error && !data) return <div className="error-banner">⚠️ {error}</div>
  if (!data) return <div className="empty">No weather data.</div>

  const current = data.current || {}
  const next24 = data.next_24h_avg || {}
  const city = CITIES[cityIdx]
  const isFallback = data.source === 'fallback'

  return (
    <div className="chart-card ww-card">
      <h3>
        {weatherEmoji(data.summary)} Weather — {city.name}
        <span className="iter-badge">iter #50</span>
      </h3>
      <p className="chart-subtitle">
        SMHI 9-day hourly forecast for the {city.name} depot location.
        Refreshes every 30 min. Source: {data.source}
        {isFallback && ' (offline fallback)'}
      </p>

      <div className="ww-controls">
        <label className="ww-label">
          City:
          <select
            className="ww-select"
            value={cityIdx}
            onChange={e => setCityIdx(parseInt(e.target.value))}
          >
            {CITIES.map((c, i) => (
              <option key={c.name} value={i}>{c.name}</option>
            ))}
          </select>
        </label>
        <span className="ww-summary">{data.summary || '—'}</span>
      </div>

      <div className="ww-current">
        <div className="ww-temp" style={{ color: tempColor(current.t) }}>
          {current.t != null ? `${current.t.toFixed(1)}°C` : '—'}
        </div>
        <div className="ww-current-details">
          <div className="ww-detail-row">
            <span className="ww-detail-label">💧 Precip:</span>
            <span className="ww-detail-value">
              {current.pmean != null ? `${current.pmean.toFixed(2)} mm/h` : '—'}
            </span>
          </div>
          <div className="ww-detail-row">
            <span className="ww-detail-label">💨 Wind:</span>
            <span className="ww-detail-value">
              {current.ws != null ? `${current.ws.toFixed(1)} m/s` : '—'}
            </span>
          </div>
          <div className="ww-detail-row">
            <span className="ww-detail-label">💦 Humidity:</span>
            <span className="ww-detail-value">
              {current.rh != null ? `${current.rh}%` : '—'}
            </span>
          </div>
        </div>
      </div>

      <div className="ww-forecast">
        <h4>Next 24h Average</h4>
        <div className="ww-forecast-row">
          <span className="ww-detail-label">🌡️ Temp:</span>
          <span className="ww-detail-value" style={{ color: tempColor(next24.temperature_c) }}>
            {next24.temperature_c != null ? `${next24.temperature_c.toFixed(1)}°C` : '—'}
          </span>
          <span className="ww-detail-label">💧 Precip:</span>
          <span className="ww-detail-value">
            {next24.precipitation_mm_h != null ? `${next24.precipitation_mm_h.toFixed(2)} mm/h` : '—'}
          </span>
          <span className="ww-detail-label">💨 Wind:</span>
          <span className="ww-detail-value">
            {next24.wind_m_s != null ? `${next24.wind_m_s.toFixed(1)} m/s` : '—'}
          </span>
        </div>
      </div>

      {data.timestamp && (
        <div className="ww-footnote">
          Updated: {new Date(data.timestamp).toLocaleString()}
        </div>
      )}
    </div>
  )
}
