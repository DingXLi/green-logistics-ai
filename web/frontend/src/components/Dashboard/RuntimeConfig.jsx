/**
 * RuntimeConfig.jsx — iter #45 + iter #46 (401 handling + admin token input)
 *
 * Admin panel for viewing/editing runtime config.
 * - Shows all 13 keys with current/default/modified status
 * - Edit form per key
 * - Persist toggle: in-memory only vs save to SQLite
 * - Apply batch via JSON
 * - Reset + clear_persisted
 *
 * iter #46 enhancements:
 * - 401 detection: friendly banner if /api/admin/auth/status reports
 *   auth_enabled=true AND no token was provided.
 * - Admin token input: stored in localStorage so user can paste their
 *   GL_ADMIN_TOKEN once and the panel will attach it to all admin calls.
 * - Auth-status footer: shows whether auth is enabled + endpoint count.
 */
import { useState, useEffect, useCallback } from 'react'
import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'
const TOKEN_STORAGE_KEY = 'gl_admin_token'

function fmtVal(v) {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'string') return v
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

function loadStoredToken() {
  try {
    return localStorage.getItem(TOKEN_STORAGE_KEY) || ''
  } catch {
    return ''
  }
}

function storeToken(t) {
  try {
    if (t) localStorage.setItem(TOKEN_STORAGE_KEY, t)
    else localStorage.removeItem(TOKEN_STORAGE_KEY)
  } catch {
    /* ignore */
  }
}

function authHeaders(token) {
  if (!token) return {}
  return { Authorization: `Bearer ${token}` }
}

export function RuntimeConfig() {
  const [data, setData] = useState(null)
  const [overrides, setOverrides] = useState(null)
  const [authStatus, setAuthStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [authRequired, setAuthRequired] = useState(false)
  const [token, setToken] = useState(loadStoredToken)
  const [tokenInput, setTokenInput] = useState('')
  const [editingKey, setEditingKey] = useState(null)
  const [editValue, setEditValue] = useState('')
  const [persistOnSave, setPersistOnSave] = useState(false)

  const fetchAll = useCallback(() => {
    setLoading(true)
    setError(null)
    // iter #46: probe auth status first (public endpoint)
    fetch(`${API_BASE}/admin/auth/status`)
      .then(r => r.json())
      .then(st => {
        setAuthStatus(st)
        const authEnabled = !!st?.auth_enabled
        const headers = authHeaders(token)
        // iter #46: include auth headers only when token is set
        const reqInit = Object.keys(headers).length ? { headers } : {}
        return Promise.all([
          fetch(`${API_BASE}/admin/runtime-config`, reqInit),
          fetch(`${API_BASE}/admin/runtime-config/overrides`, reqInit),
        ]).then(responses => {
          const [cfgResp, ovrResp] = responses
          // iter #46: detect 401 specifically
          if (cfgResp.status === 401 || ovrResp.status === 401) {
            setAuthRequired(true)
            setData(null)
            setOverrides(null)
            setLoading(false)
            return null
          }
          if (!cfgResp.ok) {
            throw new Error(`runtime-config HTTP ${cfgResp.status}: ${cfgResp.statusText}`)
          }
          if (!ovrResp.ok) {
            throw new Error(`overrides HTTP ${ovrResp.status}: ${ovrResp.statusText}`)
          }
          setAuthRequired(false)
          return Promise.all([cfgResp.json(), ovrResp.json()])
        })
      })
      .then(result => {
        if (!result) return
        const [cfg, ovr] = result
        setData(cfg)
        setOverrides(ovr)
        setLoading(false)
      })
      .catch(e => {
        setError(e.message)
        setLoading(false)
      })
  }, [token])

  useEffect(() => {
    fetchAll()
    const id = setInterval(fetchAll, 60000)
    return () => clearInterval(id)
  }, [fetchAll])

  const handleSaveToken = () => {
    storeToken(tokenInput.trim())
    setToken(tokenInput.trim())
    setTokenInput('')
    // fetchAll will re-run via useEffect since `token` is a dependency
  }

  const handleClearToken = () => {
    storeToken('')
    setToken('')
  }

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
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
          ...authHeaders(token),
        },
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

  const handleApplyBatch = async () => {
    const raw = window.prompt(
      'Paste JSON object of {key: value} overrides. Existing values will be replaced in-memory (not persisted unless you tick persist=true per key).'
    )
    if (!raw) return
    let parsed
    try {
      parsed = JSON.parse(raw)
    } catch (err) {
      setError(`Invalid JSON: ${err.message}`)
      return
    }
    try {
      const resp = await fetch(`${API_BASE}/admin/runtime-config/apply`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...authHeaders(token),
        },
        body: JSON.stringify({ overrides: parsed, persist: false }),
      })
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}))
        throw new Error(err.detail || `HTTP ${resp.status}`)
      }
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
        headers: authHeaders(token),
      })
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      fetchAll()
    } catch (e) {
      setError(e.message)
    }
  }

  if (loading && !data && !authRequired) {
    return (
      <div className="rc-panel">
        <div className="rc-header">
          <h3>⚙️ Runtime Config <span className="iter-badge">iter #46</span></h3>
        </div>
        <LoadingSpinner label="loading runtime config..." />
      </div>
    )
  }

  // iter #46: dedicated 401 auth-required UI (replaces bare error banner)
  if (authRequired) {
    return (
      <div className="rc-panel">
        <div className="rc-header">
          <h3>⚙️ Runtime Config <span className="iter-badge">iter #46</span></h3>
        </div>
        <div className="rc-auth-banner">
          <div className="rc-auth-title">🔒 Admin token required</div>
          <div className="rc-auth-detail">
            The server is running with <code>GL_ADMIN_TOKEN</code> set, so the
            runtime-config endpoints return 401 without a valid token.
            Paste your token below to unlock this panel (stored only in your
            browser's localStorage).
          </div>
          <div className="rc-auth-row">
            <input
              type="password"
              className="rc-auth-input"
              placeholder="paste GL_ADMIN_TOKEN"
              value={tokenInput}
              onChange={e => setTokenInput(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter') handleSaveToken()
              }}
            />
            <button
              className="rc-btn-save"
              onClick={handleSaveToken}
              disabled={!tokenInput.trim()}
            >
              Save token
            </button>
          </div>
          <div className="rc-auth-hint">
            Hint: <code>GET /api/admin/auth/status</code> reports
            whether auth is enabled and lists protected endpoints.
          </div>
        </div>
        {authStatus && (
          <div className="rc-auth-status">
            <span>
              🔐 Auth enabled: <strong>{authStatus.auth_enabled ? 'yes' : 'no'}</strong>
            </span>
            {authStatus.token_preview && (
              <span> · token preview: <code>{authStatus.token_preview}</code></span>
            )}
            <span>
              {' '}· protected endpoints: <strong>{authStatus.protected_endpoint_count ?? '?'}</strong>
            </span>
          </div>
        )}
      </div>
    )
  }

  if (error) {
    return (
      <div className="rc-panel">
        <div className="rc-header">
          <h3>⚙️ Runtime Config <span className="iter-badge">iter #46</span></h3>
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
        <h3>⚙️ Runtime Config <span className="iter-badge">iter #46</span></h3>
        <div className="rc-header-actions">
          <button className="refresh-btn" onClick={fetchAll} disabled={loading}>
            {loading ? 'Refreshing...' : '🔄 Refresh'}
          </button>
          <button className="rc-btn-edit" onClick={handleApplyBatch}>
            Apply batch
          </button>
          <button className="rc-btn-warn" onClick={() => handleReset(false)}>
            Reset (in-memory)
          </button>
          <button className="rc-btn-danger" onClick={() => handleReset(true)}>
            Reset + Clear persisted
          </button>
        </div>
      </div>

      {authStatus?.auth_enabled && (
        <div className="rc-auth-mini">
          🔐 Auth enabled — token loaded
          {authStatus.token_preview && (
            <> (<code>{authStatus.token_preview}</code>)</>
          )}
          <button className="rc-token-clear" onClick={handleClearToken}>
            clear
          </button>
        </div>
      )}

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
              {overrideList.map((o, i) => (
                <tr key={i}>
                  <td><code>{o.key}</code></td>
                  <td>{fmtVal(o.value)}</td>
                  <td>{o.updated_at || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="rc-section">
        <h4>🔧 All Keys</h4>
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
            {items.map((item, i) => {
              const isEditing = editingKey === item.key
              return (
                <tr key={i} className={item.modified ? 'rc-row-modified' : ''}>
                  <td><code>{item.key}</code></td>
                  <td>{item.type}</td>
                  <td>
                    {isEditing ? (
                      <input
                        type="text"
                        className="rc-edit-input"
                        value={editValue}
                        onChange={e => setEditValue(e.target.value)}
                      />
                    ) : (
                      <span style={{ color: item.modified ? '#f59e0b' : '#cbd5e1' }}>
                        {fmtVal(item.value)}
                      </span>
                    )}
                  </td>
                  <td><span style={{ color: '#64748b' }}>{fmtVal(item.default)}</span></td>
                  <td>
                    {item.modified
                      ? <span className="rc-badge rc-badge-modified">modified</span>
                      : <span className="rc-badge rc-badge-default">default</span>}
                  </td>
                  <td>
                    {isEditing ? (
                      <div className="rc-edit-actions">
                        <label className="rc-persist-toggle">
                          <input
                            type="checkbox"
                            checked={persistOnSave}
                            onChange={e => setPersistOnSave(e.target.checked)}
                          />
                          persist
                        </label>
                        <button
                          className="rc-btn-save"
                          onClick={() => handleSave(item.key)}
                        >
                          Save
                        </button>
                        <button
                          className="rc-btn-cancel"
                          onClick={() => { setEditingKey(null); setEditValue('') }}
                        >
                          Cancel
                        </button>
                      </div>
                    ) : (
                      <button className="rc-btn-edit" onClick={() => handleEdit(item)}>
                        Edit
                      </button>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
