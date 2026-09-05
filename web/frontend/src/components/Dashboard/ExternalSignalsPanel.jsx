/**
 * ExternalSignalsPanel - Eurostat economic signals overview (iter #51)
 *
 * 数据源:
 *   GET /api/signals/external -> {
 *     country, fetched_at,
 *     construction: {latest_time, latest_value, source, multiplier},
 *     industrial:   {latest_time, latest_value, source, multiplier},
 *     business_confidence: {latest_time, latest_value, source, multiplier},
 *     composite_demand_multiplier, composite_supply_multiplier,
 *   }
 *
 * 展示:
 *   - 3 KPI 卡片 (construction / industrial / business confidence)
 *   - 每卡显示 latest_value + multiplier + source 标签
 *   - Composite demand/supply multiplier bar
 *   - Auto-refresh every 5 min
 *
 * 设计:
 *   - multiplier 越接近 1.0 = "正常" → 灰色
 *   - multiplier > 1.05 = "boost" → 绿色
 *   - multiplier < 0.95 = "drag"  → 橙色
 *   - source 标签: 'eurostat' (live) | 'cache' (recent) | 'fallback' (offline)
 */

import { useEffect, useState } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

// API base: Vite env var > localhost fallback
const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'

const REFRESH_MS = 5 * 60 * 1000  // 5 min

function _multiplierColor(m) {
  if (m > 1.05) return { bg: 'rgba(34, 197, 94, 0.15)', text: '#16a34a', label: 'Boost' }
  if (m < 0.95) return { bg: 'rgba(245, 158, 11, 0.15)', text: '#ea580c', label: 'Drag' }
  return { bg: 'rgba(100, 116, 139, 0.15)', text: '#64748b', label: 'Neutral' }
}

function _sourceBadge(source) {
  if (source === 'eurostat') return { icon: '🟢', label: 'Live', color: '#22c55e' }
  if (source === 'cache') return { icon: '🟡', label: 'Cached', color: '#eab308' }
  return { icon: '⚪', label: 'Offline', color: '#94a3b8' }
}

function SignalCard({ title, indicator }) {
  const { latest_value, latest_time, source, multiplier } = indicator
  const mColor = _multiplierColor(multiplier)
  const sBadge = _sourceBadge(source)
  const isBalance = title === 'Business Confidence'

  return (
    <div className="ext-signal-card">
      <div className="ext-signal-header">
        <span className="ext-signal-title">{title}</span>
        <span
          className="ext-signal-source"
          title={`Source: ${source}`}
          style={{ color: sBadge.color }}
        >
          {sBadge.icon} {sBadge.label}
        </span>
      </div>

      <div className="ext-signal-value">
        {latest_value !== null && latest_value !== undefined
          ? Number(latest_value).toFixed(isBalance ? 1 : 1)
          : '—'}
        {isBalance && (
          <span className="ext-signal-unit" style={{ fontSize: '0.7em', marginLeft: 4 }}>
            balance
          </span>
        )}
      </div>

      <div className="ext-signal-time">{latest_time || '—'}</div>

      <div
        className="ext-signal-multiplier"
        style={{ background: mColor.bg, color: mColor.text }}
      >
        <span className="ext-signal-mult-label">{mColor.label}</span>
        <span className="ext-signal-mult-value">×{multiplier.toFixed(3)}</span>
      </div>
    </div>
  )
}

export function ExternalSignalsPanel() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchData = async () => {
    try {
      const res = await fetch(`${API_BASE}/signals/external`)
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
    fetchData()
    const id = setInterval(fetchData, REFRESH_MS)
    return () => clearInterval(id)
  }, [])

  if (loading) {
    return (
      <div className="card ext-signals-panel">
        <LoadingSpinner size="md" label="Loading Eurostat signals…" />
      </div>
    )
  }

  if (error || !data) {
    return (
      <div className="card ext-signals-panel">
        <h3>🌐 External Economic Signals</h3>
        <div className="ext-signals-error">
          ⚠️ Failed to fetch external signals: {error || 'no data'}
        </div>
      </div>
    )
  }

  return (
    <div className="card ext-signals-panel">
      <div className="ext-signals-header">
        <h3>🌐 External Economic Signals</h3>
        <div className="ext-signals-meta">
          <span>Eurostat · {data.country}</span>
          <span className="ext-signals-time">fetched {new Date(data.fetched_at).toLocaleTimeString()}</span>
        </div>
      </div>

      <div className="ext-signals-grid">
        <SignalCard title="Construction" indicator={data.construction} />
        <SignalCard title="Industrial" indicator={data.industrial} />
        <SignalCard title="Business Confidence" indicator={data.business_confidence} />
      </div>

      <div className="ext-signals-composite">
        <div className="ext-signal-composite-item">
          <div className="ext-signal-comp-label">Composite Demand Mult.</div>
          <div className="ext-signal-comp-bar">
            <div
              className="ext-signal-comp-fill"
              style={{
                width: `${Math.min(100, (data.composite_demand_multiplier / 1.44) * 100)}%`,
                background: data.composite_demand_multiplier > 1.0 ? '#22c55e' : '#f59e0b',
              }}
            />
            <span className="ext-signal-comp-value">×{data.composite_demand_multiplier.toFixed(3)}</span>
          </div>
        </div>
        <div className="ext-signal-composite-item">
          <div className="ext-signal-comp-label">Composite Supply Mult.</div>
          <div className="ext-signal-comp-bar">
            <div
              className="ext-signal-comp-fill"
              style={{
                width: `${Math.min(100, (data.composite_supply_multiplier / 1.44) * 100)}%`,
                background: data.composite_supply_multiplier > 1.0 ? '#22c55e' : '#f59e0b',
              }}
            />
            <span className="ext-signal-comp-value">×{data.composite_supply_multiplier.toFixed(3)}</span>
          </div>
        </div>
      </div>
    </div>
  )
}