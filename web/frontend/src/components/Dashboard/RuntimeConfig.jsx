/**
 * RuntimeConfig.jsx — iter #45
 *
 * Admin panel for viewing/editing runtime config.
 * - Shows all 13 keys with current/default/modified status
 * - Edit form per key
 * - Persist toggle: in-memory only vs save to SQLite
 * - Apply batch via JSON
 * - Reset + clear_persisted
 */
import { useState, useEffect } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'

function fmtVal(v) {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'string') return v
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

export function RuntimeConfig() {
  const [data, setData] = useState(null)
  const [overrides, setOverrides] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [editingKey, setEditingKey] = useState(null)
  const [editValue, setEditValue] = useState('')
  const [persistOnSave, setPersistOnSave] = useState(false)

  const fetchAll = () => {
    setLoading(true)
    Promise.all([
      fetch(`${API_BASE}/admin/runtime-config`).then(r => r.json()),
      fetch(`${API_BASE}/admin/runtime-config/overrides`).then(r => r.json()),
    ])
      .then(([cfg, ovr]) => {
        setData(cfg)
        setOverrides(ovr)
        setLoading(false)
      })
      .catch(e => {
        setError(e.message)
        setLoading(false)
      })
  }

  useEffect(() => {
    fetchAll()
    const id = setInterval(fetchAll, 60000)
    return () => clearInterval(id)
  }, [])

  const handleEdit = (item) => {
    setEditingKey(item.key)
    setEditValue(String(item.value))
    setPersistOnSave(false)
  }

  const handleSave = async (key) => {
    try {
      const params = new URLSearchParams({
        key,
        value: editValue,
        persist: String(persistOnSave),
      })
      const resp = await fetch(`${API_BASE}/admin/runtime-config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: params,
      })
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}))
        throw new Error(err.detail || `HTTP ${resp.status}`)
      }
      setEditingKey(null)
      setEditValue('')
      fetchAll()
    } catch (e) {
      setError(e.message)
    }
  }

  const handleReset = async (clearPersisted) => {
    if (!window.confirm(
      clearPersisted
        ? 'Reset all runtime config to defaults AND clear all persisted overrides from SQLite?'
        : 'Reset all runtime config to defaults (in-memory only)?'
    )) {
      return
    }
    try {
      const params = new URLSearchParams()
      if (clearPersisted) params.set('clear_persisted', 'true')
      const resp = await fetch(`${API_BASE}/admin/runtime-config/reset?${params}`, {
        method: 'POST',
      })
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      fetchAll()
    } catch (e) {
      setError(e.message)
    }
  }

  if (loading && !data) {
    return (
      <div className="rc-panel">
        <div className="rc-header">
          <h3>⚙️ Runtime Config (iter #45)</h3>
        </div>
        <LoadingSpinner label="Loading runtime config..." />
      </div>
    )
  }

  if (error) {
    return (
      <div className="rc-panel">
        <div className="rc-header">
          <h3>⚙️ Runtime Config (iter #45)</h3>
        </div>
        <div className="rc-error">Error: {error}</div>
      </div>
    )
  }

  const items = data?.items || []
  const overrideList = overrides?.overrides || []
  const nModified = items.filter(i => i.modified).length

  return (
    <div className="rc-panel">
      <div className="rc-header">
        <h3>⚙️ Runtime Config <span className="iter-badge">iter #45</span></h3>
        <div className="rc-header-actions">
          <button className="refresh-btn" onClick={fetchAll} disabled={loading}>
            {loading ? 'Refreshing...' : '🔄 Refresh'}
          </button>
          <button className="rc-btn-warn" onClick={() => handleReset(false)}>
            Reset (in-memory)
          </button>
          <button className="rc-btn-danger" onClick={() => handleReset(true)}>
            Reset + Clear Persisted
          </button>
        </div>
      </div>

      <div className="cs-kpi-row">
        <div className="cs-kpi">
          <div className="kpi-label">Total Keys</div>
          <div className="kpi-value">{items.length}</div>
          <div className="kpi-sub">tunable params</div>
        </div>
        <div className="cs-kpi">
          <div className="kpi-label">Modified</div>
          <div className="kpi-value" style={{ color: nModified > 0 ? '#f59e0b' : '#10b981' }}>
            {nModified}
          </div>
          <div className="kpi-sub">non-default values</div>
        </div>
        <div className="cs-kpi">
          <div className="kpi-label">Persisted</div>
          <div className="kpi-value">{overrideList.length}</div>
          <div className="kpi-sub">saved to SQLite</div>
        </div>
      </div>

      {overrideList.length > 0 && (
        <div className="rc-section">
          <h4>📌 Persisted Overrides (survive restart)</h4>
          <table className="cs-table">
            <thead>
              <tr>
                <th>Key</th>
                <th>Value</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {overrideList.map(o => (
                <tr key={o.key}>
                  <td><strong>{o.key}</strong></td>
                  <td>{fmtVal(o.parsed_value)}</td>
                  <td style={{ fontSize: '0.75rem', color: '#94a3b8' }}>
                    {o.updated_at}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="rc-section">
        <h4>All Config Keys ({items.length})</h4>
        <table className="cs-table">
          <thead>
            <tr>
              <th>Key</th>
              <th>Type</th>
              <th>Current</th>
              <th>Default</th>
              <th>Status</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {items.map(item => (
              <tr key={item.key} className={item.modified ? 'rc-modified' : ''}>
                <td>
                  <strong>{item.key}</strong>
                </td>
                <td><code style={{ fontSize: '0.75rem' }}>{item.type}</code></td>
                <td>
                  {editingKey === item.key ? (
                    <input
                      type="text"
                      value={editValue}
                      onChange={e => setEditValue(e.target.value)}
                      className="rc-edit-input"
                      autoFocus
                    />
                  ) : (
                    <span className={item.modified ? 'rc-current-modified' : ''}>
                      {fmtVal(item.value)}
                    </span>
                  )}
                </td>
                <td style={{ color: '#94a3b8' }}>{fmtVal(item.default)}</td>
                <td>
                  {item.modified ? (
                    <span className="badge-sweet">modified</span>
                  ) : (
                    <span className="badge-candidate">default</span>
                  )}
                </td>
                <td>
                  {editingKey === item.key ? (
                    <div className="rc-edit-actions">
                      <label className="rc-persist-toggle">
                        <input
                          type="checkbox"
                          checked={persistOnSave}
                          onChange={e => setPersistOnSave(e.target.checked)}
                        />
                        persist
                      </label>
                      <button className="rc-btn-save" onClick={() => handleSave(item.key)}>
                        ✓
                      </button>
                      <button className="rc-btn-cancel" onClick={() => setEditingKey(null)}>
                        ✕
                      </button>
                    </div>
                  ) : (
                    <button className="rc-btn-edit" onClick={() => handleEdit(item)}>
                      Edit
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="cs-footnote">
        💡 <strong>In-memory changes</strong> reset on restart. <strong>Persist</strong>
        saves to SQLite (survives restart). Use "Reset + Clear Persisted" to
        roll back all saved overrides. The "type" column tells you what
        values are valid (boolean accepts true/false; integer/float accept numbers).
      </div>
    </div>
  )
}

export default RuntimeConfig
