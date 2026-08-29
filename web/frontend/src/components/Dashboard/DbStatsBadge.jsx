/**
 * DbStatsBadge - 显示 DB 大小 / 表行数 / 索引数 (iter #17)
 *
 * 数据源: GET /api/admin/db-stats
 *
 * 显示:
 * - DB size in MB (顶部主数字)
 * - 6 张表的行数 (cycles / supplies / demands / matches / routes / llm_decisions)
 * - index 数量
 * - time range (oldest / newest cycle)
 *
 * 用途: 让 ops / dev 一眼知道:
 *       - DB 是否快满了 (size)
 *       - 哪个表涨得最快 (rows)
 *       - index 是否合理 (n_indexes)
 */

import { useState, useEffect } from 'react'

import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'

const TABLE_LABELS = {
  optimization_cycles: 'Cycles',
  supply_offers: 'Supplies',
  demand_requests: 'Demands',
  matches: 'Matches',
  routes: 'Routes',
  llm_decisions: 'LLM',
}

// Table 颜色 (按表格类型)
const TABLE_COLORS = {
  optimization_cycles: '#3b82f6',  // 蓝
  supply_offers: '#22c55e',         // 绿
  demand_requests: '#f59e0b',       // 橙
  matches: '#8b5cf6',               // 紫
  routes: '#ef4444',                // 红
  llm_decisions: '#06b6d4',         // 青
}

function formatBytes(bytes) {
  if (bytes == null) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

function formatDate(isoStr) {
  if (!isoStr) return '—'
  try {
    return new Date(isoStr).toLocaleString('sv-SE', { 
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' 
    })
  }catch { return '—'
  }
}

export function DbStatsBadge() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    let cancelled = false
    const fetchData = () => {
      fetch(`${API_BASE}/admin/db-stats`)
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
    }
    fetchData()
    const interval = setInterval(fetchData, 30000)  // refresh every 30s
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [])

  if (loading) return <LoadingSpinner label="Loading DB stats…" />
  if (error) return <div className="error-banner">⚠️ {error}</div>
  if (!data) return <div className="empty">No DB stats.</div>

  const totalRows = data.total_rows || 0
  const nIndexes = (data.indexes || []).length
  const nTables = Object.keys(data.table_counts || {}).length

  return (
    <div className="db-stats-card">
      <div className="db-stats-header" onClick={() => setExpanded(!expanded)}>
        <div className="db-stats-summary">
          <span className="db-stats-icon">🗄️</span>
          <div className="db-stats-main">
            <div className="db-stats-value">{formatBytes(data.db_size_bytes)}</div>
            <div className="db-stats-meta">
              {totalRows.toLocaleString()} rows · {nTables} tables · {nIndexes} indexes
            </div>
          </div>
        </div>
        <button className="db-stats-toggle" aria-label="Toggle details">
          {expanded ? '▲' : '▼'}
        </button>
      </div>

      {expanded && (
        <div className="db-stats-details">
          <div className="db-stats-tables">
            <h4>Table rows</h4>
            {Object.entries(data.table_counts || {}).map(([table, count]) => {
              const pct = totalRows > 0 ? (count / totalRows * 100) : 0
              const color = TABLE_COLORS[table] || '#9ca3af'
              return (
                <div key={table} className="db-stats-table-row">
                  <div className="db-stats-table-label">
                    <span className="db-stats-table-dot" style={{ backgroundColor: color }}></span>
                    {TABLE_LABELS[table] || table}
                  </div>
                  <div className="db-stats-table-bar-wrap">
                    <div 
                      className="db-stats-table-bar" 
                      style={{ 
                        width: `${pct}%`, 
                        backgroundColor: color 
                      }}
                    />
                  </div>
                  <div className="db-stats-table-count">{count.toLocaleString()}</div>
                </div>
              )
            })}
          </div>

          {data.time_range && (data.time_range.oldest_cycle || data.time_range.newest_cycle) && (
            <div className="db-stats-time-range">
              <h4>Time range</h4>
              <div className="db-stats-time-row">
                <span>Oldest cycle:</span>
                <code>{formatDate(data.time_range.oldest_cycle)}</code>
              </div>
              <div className="db-stats-time-row">
                <span>Newest cycle:</span>
                <code>{formatDate(data.time_range.newest_cycle)}</code>
              </div>
            </div>
          )}

          {data.db_path && (
            <div className="db-stats-path">
              <span>Path:</span>
              <code>{data.db_path}</code>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default DbStatsBadge