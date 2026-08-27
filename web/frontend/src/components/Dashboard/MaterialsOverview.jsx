/**
 * MaterialsOverview - 6 种废料 material 元数据卡片 (iter #10)
 *
 * 数据源: GET /api/materials
 *
 * 显示:
 * - Material name + 图标
 * - Sweden annual total (kt/year) + 人均 kg/year
 * - 数据来源 (SCB / Avfall Sverige / Eurostat)
 * - Seasonal pattern badge (summer_peak / stable / winter_peak)
 * - Peak month 图标
 * - Seasonal factor min/max (recharts 显示 12 月曲线)
 *
 * 用途: 让用户知道 system 支持哪些废料 + 数据来源可信度。
 */

import { useState, useEffect } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts'

import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'

const MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                     'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

// Material 图标 (按 waste type)
const MATERIAL_ICONS = {
  concrete:        '🏗️',
  metal_scrap:     '🔩',
  wood_waste:      '🪵',
  mixed_waste:     '🗑️',
  plastic:         '🧴',
  paper_cardboard: '📦',
}

// Pattern 颜色 (background gradient)
const PATTERN_COLORS = {
  summer_peak: 'linear-gradient(135deg, #fef9c3 0%, #fde68a 100%)',
  winter_peak: 'linear-gradient(135deg, #dbeafe 0%, #bae6fd 100%)',
  stable:      'linear-gradient(135deg, #f3f4f6 0%, #e5e7eb 100%)',
  unknown:     'linear-gradient(135deg, #fafafa 0%, #f0f0f0 100%)',
}

const PATTERN_LABELS = {
  summer_peak: '🌞 Summer Peak',
  winter_peak: '❄️ Winter Peak',
  stable:      '⚖️ Stable',
  unknown:     '❓ Unknown',
}

export function MaterialsOverview() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [selected, setSelected] = useState(null)

  useEffect(() => {
    let cancelled = false
    fetch(`${API_BASE}/materials`)
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

  if (loading) return <LoadingSpinner label="Loading materials…" />
  if (error) return <div className="error-banner">⚠️ {error}</div>
  if (!data || !data.materials || data.materials.length === 0) {
    return <div className="empty">No materials data.</div>
  }

  // 计算 total annual Sweden waste (kt)
  const totalKt = data.materials.reduce((s, m) => s + (m.total_kt_per_year || 0), 0)

  // 渲染选中 material 的 12 月 seasonal curve
  const renderSeasonalChart = (material) => {
    // 客户端构造 12 月曲线 (从 min/max + peak_month 推断)
    // 实际上 api/materials 没有返回 12 月详细数据, 这里用近似展示
    // 简化: 显示 seasonal_factor_min 和 max 的对比
    const peak = material.seasonal_peak_month
    const peakName = peak ? MONTH_NAMES[peak - 1] : '—'
    const minVal = material.seasonal_factor_min
    const maxVal = material.seasonal_factor_max
    return (
      <div className="material-seasonal-info">
        <div className="seasonal-range-row">
          <span className="seasonal-label">Peak Month:</span>
          <span className="seasonal-value">{peakName}</span>
        </div>
        <div className="seasonal-range-row">
          <span className="seasonal-label">Range:</span>
          <span className="seasonal-value">
            {minVal != null ? minVal.toFixed(2) : '—'} – {maxVal != null ? maxVal.toFixed(2) : '—'}
          </span>
        </div>
        <div className="seasonal-range-row">
          <span className="seasonal-label">Amplitude:</span>
          <span className="seasonal-value">
            {minVal != null && maxVal != null ? `${((maxVal - minVal) / minVal * 100).toFixed(0)}%` : '—'}
          </span>
        </div>
      </div>
    )
  }

  return (
    <div className="chart-card">
      <h3>🧪 Materials Overview</h3>
      <p className="chart-subtitle">
        6 waste materials supported by the system. Sweden annual total: <strong>{totalKt.toLocaleString()} kt/year</strong>.
      </p>

      <div className="materials-grid">
        {data.materials.map(m => {
          const isSelected = selected === m.material
          const patternColor = PATTERN_COLORS[m.seasonal_pattern] || PATTERN_COLORS.unknown
          const patternLabel = PATTERN_LABELS[m.seasonal_pattern] || m.seasonal_pattern
          const icon = MATERIAL_ICONS[m.material] || '📦'

          return (
            <div
              key={m.material}
              className={`material-card ${isSelected ? 'selected' : ''}`}
              style={{ background: patternColor }}
              onClick={() => setSelected(isSelected ? null : m.material)}
            >
              <div className="material-header">
                <div className="material-icon">{icon}</div>
                <div className="material-name">{m.material.replace('_', ' ')}</div>
              </div>

              <div className="material-stats">
                <div className="material-stat">
                  <span className="stat-num">{m.total_kt_per_year.toLocaleString()}</span>
                  <span className="stat-label">kt/year</span>
                </div>
                <div className="material-stat">
                  <span className="stat-num">{m.per_capita_kg}</span>
                  <span className="stat-label">kg/person</span>
                </div>
              </div>

              <div className="material-pattern-badge">{patternLabel}</div>

              <div className="material-source">📚 {m.source}</div>

              {isSelected && renderSeasonalChart(m)}
            </div>
          )
        })}
      </div>
    </div>
  )
}

export default MaterialsOverview