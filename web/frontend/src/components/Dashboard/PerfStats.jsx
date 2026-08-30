/**
 * PerfStats - API performance monitoring dashboard (iter #22)
 *
 * 数据源: GET /api/admin/perf-stats?top=10
 *
 * 显示:
 * - 顶部 4 个 KPI: total_requests / total_errors / error_rate_pct / avg_ms
 * - Top 10 最慢 endpoint (avg_ms DESC)
 *   - endpoint / n_calls / avg / min / max / p50 / p95 / p99 / last_ms
 * - 颜色编码: error_rate 红 / p95 > 1000ms 红 / 500ms < p95 < 1000ms 橙 / < 500ms 绿
 *
 * 自动 refresh 每 15s
 *
 * 用途: production observability — 哪个 endpoint 慢 / 哪个错误率高?
 */

import { useState, useEffect } from 'react'
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from 'recharts'

import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'

const REFRESH_INTERVAL_MS = 15000

function formatMs(ms) {
  if (ms == null) return '—'
  if (ms < 1) return `${(ms * 1000).toFixed(0)} µs`
  if (ms < 1000) return `${ms.toFixed(0)} ms`
  return `${(ms / 1000).toFixed(2)} s`
}

function latencyColor(ms) {
  if (ms == null) return '#94a3b8'
  if (ms < 500) return '#22c55e'   // 绿
  if (ms < 1000) return '#f59e0b'  // 橙
  return '#ef4444'                  // 红
}

function latencyBadgeClass(ms) {
  if (ms == null) return 'perf-badge perf-badge-neutral'
  if (ms < 500) return 'perf-badge perf-badge-fast'
  if (ms < 1000) return 'perf-badge perf-badge-medium'
  return 'perf-badge perf-badge-slow'
}

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

export function PerfStats() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchStats = async () => {
    try {
      const resp = await fetch(`${API_BASE}/admin/perf-stats?top=10`)
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`)
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
    fetchStats()
    const interval = setInterval(fetchStats, REFRESH_INTERVAL_MS)
    return () => clearInterval(interval)
  }, [])

  if (loading) return <LoadingSpinner label="Loading perf stats…" />
  if (error) {
    return (
      <div className="chart-card">
        <h3>⚡ API Performance</h3>
        <div className="error-banner">⚠️ {error}</div>
      </div>
    )
  }
  if (!data) return null

  const {
    total_requests = 0,
    total_errors = 0,
    error_rate_pct = 0,
    endpoints = [],
    buffer_size_per_endpoint = 100,
  } = data

  // Compute avg ms across endpoints (weighted by n_calls)
  const totalCalls = endpoints.reduce((s, e) => s + e.n_calls, 0)
  const avgMs = totalCalls > 0
    ? endpoints.reduce((s, e) => s + e.avg_ms * e.n_calls, 0) / totalCalls
    : 0

  // Find slowest endpoint
  const slowest = endpoints[0]

  return (
    <div className="chart-card">
      <div className="chart-card-header">
        <h3>⚡ API Performance (iter #22)</h3>
        <span className="chart-card-sub">
          Last {buffer_size_per_endpoint} calls per endpoint · Auto-refresh 15s
        </span>
      </div>

      <div className="kpi-grid">
        <KpiCard label="Total Requests" value={total_requests.toLocaleString()} unit=" req" accent="#3b82f6" />
        <KpiCard label="Errors (5xx)" value={total_errors.toLocaleString()} unit=" err" accent="#ef4444" />
        <KpiCard
          label="Error Rate"
          value={error_rate_pct.toFixed(2)}
          unit=" %"
          accent={error_rate_pct > 5 ? '#ef4444' : error_rate_pct > 1 ? '#f59e0b' : '#22c55e'}
        />
        <KpiCard
          label="Avg Latency"
          value={formatMs(avgMs)}
          unit=""
          accent={latencyColor(avgMs)}
        />
      </div>

      {slowest && (
        <div className="perf-slowest-banner" style={{ borderLeft: `4px solid ${latencyColor(slowest.p95_ms)}` }}>
          <span className="perf-slowest-label">🐢 Slowest endpoint:</span>{' '}
          <code className="perf-endpoint-name">{slowest.endpoint}</code>
          <span className="perf-slowest-stats">
            {' '}— p95: <strong style={{ color: latencyColor(slowest.p95_ms) }}>{formatMs(slowest.p95_ms)}</strong>,
            {' '}avg: <strong>{formatMs(slowest.avg_ms)}</strong>,
            {' '}calls: <strong>{slowest.n_calls}</strong>
          </span>
        </div>
      )}

      {endpoints.length === 0 ? (
        <div className="empty-state">
          📊 No requests tracked yet. Trigger a few API calls and they'll appear here.
        </div>
      ) : (
        <>
          {/* Recharts bar chart: avg vs p95 vs p99 per endpoint */}
          <div className="chart-row" style={{ height: 280, marginTop: '1rem' }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={endpoints.slice(0, 10)}
                layout="vertical"
                margin={{ top: 5, right: 30, left: 180, bottom: 5 }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke="#475569" />
                <XAxis type="number" stroke="#94a3b8" tickFormatter={(v) => `${v}ms`} />
                <YAxis
                  type="category"
                  dataKey="endpoint"
                  stroke="#94a3b8"
                  width={170}
                  tick={{ fontSize: 11 }}
                />
                <Tooltip
                  contentStyle={{ background: '#1e293b', border: '1px solid #475569' }}
                  formatter={(v) => formatMs(v)}
                />
                <Legend />
                <Bar dataKey="avg_ms" name="Avg" fill="#3b82f6" />
                <Bar dataKey="p95_ms" name="p95" fill="#f59e0b" />
                <Bar dataKey="p99_ms" name="p99" fill="#ef4444" />
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* Detail table */}
          <table className="perf-table">
            <thead>
              <tr>
                <th>Endpoint</th>
                <th>Calls</th>
                <th>Avg</th>
                <th>p50</th>
                <th>p95</th>
                <th>p99</th>
                <th>Min</th>
                <th>Max</th>
                <th>Last</th>
              </tr>
            </thead>
            <tbody>
              {endpoints.map((ep) => (
                <tr key={ep.endpoint}>
                  <td>
                    <code className="perf-endpoint-name">{ep.endpoint}</code>
                  </td>
                  <td>{ep.n_calls}</td>
                  <td>
                    <span className={latencyBadgeClass(ep.avg_ms)}>{formatMs(ep.avg_ms)}</span>
                  </td>
                  <td>{formatMs(ep.p50_ms)}</td>
                  <td>
                    <span className={latencyBadgeClass(ep.p95_ms)}>{formatMs(ep.p95_ms)}</span>
                  </td>
                  <td>
                    <span className={latencyBadgeClass(ep.p99_ms)}>{formatMs(ep.p99_ms)}</span>
                  </td>
                  <td>{formatMs(ep.min_ms)}</td>
                  <td>{formatMs(ep.max_ms)}</td>
                  <td>{formatMs(ep.last_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  )
}

export default PerfStats
