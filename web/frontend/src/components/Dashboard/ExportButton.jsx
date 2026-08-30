/**
 * ExportButton - DB export menu (iter #23)
 *
 * 数据源: GET /api/admin/db-export?table=X&fmt=Y
 *
 * 提供 4 种 format 下载 (iter #23 + iter #18-#20 集成):
 * - CSV (default, with optional metadata header iter #20)
 * - JSON (pretty array)
 * - NDJSON (line-delimited JSON, iter #18)
 * - Parquet (columnar binary, iter #23, snappy compressed)
 *
 * 5 个 table 可选: cycles / supplies / matches / routes / llm_decisions
 *
 * Gzip option: per-format toggle (gzip + parquet works fine for analytics)
 *
 * 默认 limit: 1000 (前端的常见 case; HF 上 10000 row API default)
 *
 * 用途: 让 ops / dev 快速下载数据 → 本地 pandas / DuckDB 分析
 */

import { useState } from 'react'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'

const TABLES = [
  { key: 'cycles', label: '📊 Cycles' },
  { key: 'supplies', label: '📦 Supplies' },
  { key: 'matches', label: '🤝 Matches' },
  { key: 'routes', label: '🚛 Routes' },
  { key: 'llm_decisions', label: '🤖 LLM Decisions' },
]

const FORMATS = [
  { key: 'csv', label: 'CSV', desc: 'Text, Excel-friendly', mime: 'text/csv' },
  { key: 'json', label: 'JSON', desc: 'Array, debug-friendly', mime: 'application/json' },
  { key: 'ndjson', label: 'NDJSON', desc: 'Line-delimited JSON', mime: 'application/x-ndjson' },
  { key: 'parquet', label: 'Parquet', desc: 'Columnar, snappy (analytics)', mime: 'application/vnd.apache.parquet' },
]

const LIMITS = [100, 1000, 10000, 50000]

export function ExportButton() {
  const [table, setTable] = useState('cycles')
  const [fmt, setFmt] = useState('csv')
  const [limit, setLimit] = useState(1000)
  const [gzip, setGzip] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const [lastResult, setLastResult] = useState(null)

  const handleDownload = async () => {
    setDownloading(true)
    setLastResult(null)
    try {
      const params = new URLSearchParams({
        table,
        fmt,
        limit: String(limit),
      })
      if (gzip) params.append('gzip', 'true')

      const url = `${API_BASE}/admin/db-export?${params.toString()}`
      const resp = await fetch(url)
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}: ${await resp.text()}`)
      }
      // Get filename from Content-Disposition (handle quoted and unquoted)
      const cd = resp.headers.get('content-disposition') || ''
      const match = cd.match(/filename="?([^"]+)"?/)
      const filename = match ? match[1] : `green_logistics_${table}_${limit}.${fmt}`

      const blob = await resp.blob()
      const size = blob.size
      const sizeKB = size < 1024 ? `${size} B` : `${(size / 1024).toFixed(1)} KB`
      const sizeMB = size / (1024 * 1024)

      // Trigger browser download
      const downloadUrl = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = downloadUrl
      a.download = filename
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(downloadUrl)

      setLastResult({
        success: true,
        filename,
        size,
        sizeHuman: sizeMB >= 1 ? `${sizeMB.toFixed(2)} MB` : sizeKB,
        encoding: resp.headers.get('content-encoding'),
      })
    } catch (e) {
      setLastResult({
        success: false,
        error: e.message || 'Download failed',
      })
    } finally {
      setDownloading(false)
    }
  }

  const selectedFormat = FORMATS.find((f) => f.key === fmt)

  return (
    <div className="chart-card">
      <div className="chart-card-header">
        <h3>📥 Export Data (iter #23)</h3>
        <span className="chart-card-sub">
          Download simulation DB → local analytics (pandas / DuckDB / polars)
        </span>
      </div>

      <div className="export-controls">
        <div className="export-row">
          <label className="export-label">Table:</label>
          <select
            className="export-select"
            value={table}
            onChange={(e) => setTable(e.target.value)}
            disabled={downloading}
          >
            {TABLES.map((t) => (
              <option key={t.key} value={t.key}>{t.label}</option>
            ))}
          </select>
        </div>

        <div className="export-row">
          <label className="export-label">Format:</label>
          <div className="export-fmt-grid">
            {FORMATS.map((f) => (
              <button
                key={f.key}
                type="button"
                className={`export-fmt-btn ${fmt === f.key ? 'active' : ''}`}
                onClick={() => setFmt(f.key)}
                disabled={downloading}
                title={f.desc}
              >
                <strong>{f.label}</strong>
                <span className="export-fmt-desc">{f.desc}</span>
              </button>
            ))}
          </div>
        </div>

        <div className="export-row">
          <label className="export-label">Limit:</label>
          <select
            className="export-select export-select-small"
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value))}
            disabled={downloading}
          >
            {LIMITS.map((l) => (
              <option key={l} value={l}>{l.toLocaleString()} rows</option>
            ))}
          </select>

          <label className="export-gzip-toggle">
            <input
              type="checkbox"
              checked={gzip}
              onChange={(e) => setGzip(e.target.checked)}
              disabled={downloading}
            />
            <span>gzip compression</span>
          </label>
        </div>

        <div className="export-row">
          <button
            className="export-download-btn"
            onClick={handleDownload}
            disabled={downloading}
          >
            {downloading ? '⏳ Downloading…' : `⬇️ Download ${selectedFormat?.label}`}
          </button>
        </div>

        {lastResult && (
          <div className="export-result">
            {lastResult.success ? (
              <span className="export-result-success">
                ✓ Downloaded <strong>{lastResult.filename}</strong> ({lastResult.sizeHuman})
                {lastResult.encoding && lastResult.encoding !== 'gzip' && (
                  <span className="export-result-meta"> · {lastResult.encoding}</span>
                )}
              </span>
            ) : (
              <span className="export-result-error">
                ✗ Failed: {lastResult.error}
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

export default ExportButton
