/**
 * SeasonalHeatmap - 月度废料季节因子热图
 *
 * 数据源: GET /api/seasonal-factors
 *
 * 显示 12 个月 × 6 个 material 的 factor 热图,
 * 当前 sim_month 高亮, hover 显示数值。
 *
 * factor 语义:
 *   1.0 = baseline (无扰动)
 *   >1.0 = 高峰 (例如 concrete 6 月 = 1.4)
 *   <1.0 = 低谷 (例如 concrete 1 月 = 0.4)
 */
import { useState, useEffect, useMemo } from 'react'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'

const MONTH_NAMES = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'
]

// factor → 颜色 (蓝=低, 白=中, 红=高)
function factorToColor(f) {
  if (f >= 1.3) return '#dc2626'      // deep red (peak)
  if (f >= 1.1) return '#f87171'      // red
  if (f >= 0.95) return '#fef3c7'     // cream (baseline-ish)
  if (f >= 0.7) return '#bfdbfe'      // light blue
  return '#60a5fa'                    // blue (trough)
}

export function SeasonalHeatmap({ currentMonth = null }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [hover, setHover] = useState(null)

  useEffect(() => {
    let cancelled = false
    fetch(`${API_BASE}/seasonal-factors`)
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

  const { materials, factorsByMonth } = useMemo(() => {
    if (!data) return { materials: [], factorsByMonth: {} }
    const months = Object.keys(data.factors_by_month).map(Number).sort()
    const firstMonth = data.factors_by_month[months[0]]
    const mats = Object.keys(firstMonth).sort()
    return {
      materials: mats,
      factorsByMonth: data.factors_by_month,
    }
  }, [data])

  if (loading) return <div className="empty">Loading seasonal factors…</div>
  if (error) return <div className="error-banner">⚠️ {error}</div>
  if (!data) return null

  return (
    <div className="chart-card">
      <h3>📅 Seasonal Factors — Avfall Sverige 2023</h3>
      <p className="chart-subtitle">
        Monthly multiplier per waste material. Red = peak season, blue = trough.
        {currentMonth && (
          <strong> Current sim month: {MONTH_NAMES[currentMonth - 1]}</strong>
        )}
      </p>
      <div className="seasonal-heatmap">
        <table>
          <thead>
            <tr>
              <th></th>
              {MONTH_NAMES.map((name, i) => (
                <th
                  key={name}
                  className={currentMonth === i + 1 ? 'current-month' : ''}
                >
                  {name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {materials.map(material => (
              <tr key={material}>
                <th className="material-label">{material}</th>
                {Array.from({ length: 12 }, (_, i) => {
                  const month = i + 1
                  const f = factorsByMonth[month]?.[material]
                  const isHover = hover?.material === material && hover?.month === month
                  return (
                    <td
                      key={month}
                      className={`factor-cell ${currentMonth === month ? 'current-month' : ''} ${isHover ? 'hover' : ''}`}
                      style={{ backgroundColor: f != null ? factorToColor(f) : '#f1f5f9' }}
                      onMouseEnter={() => setHover({ material, month, factor: f })}
                      onMouseLeave={() => setHover(null)}
                      title={`${material} ${MONTH_NAMES[i]}: ${f?.toFixed(2)}`}
                    >
                      {isHover ? f?.toFixed(2) : ''}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
        <div className="seasonal-legend">
          <span>← Low (0.4)</span>
          <span className="legend-bar">
            <span style={{ background: '#60a5fa' }} />
            <span style={{ background: '#bfdbfe' }} />
            <span style={{ background: '#fef3c7' }} />
            <span style={{ background: '#f87171' }} />
            <span style={{ background: '#dc2626' }} />
          </span>
          <span>High (1.4) →</span>
        </div>
      </div>
    </div>
  )
}