/**
 * SeasonalPerturbationPanel - manage active seasonal shocks (iter #37)
 *
 * 数据源:
 *   GET  /api/seasonal-factors?sim_day=N → active_perturbations (public)
 *   GET  /api/admin/seasonal-perturbations → all perturbations (admin)
 *   POST /api/admin/seasonal-perturbations → create new
 *   POST /api/admin/seasonal-perturbations/{id}/deactivate → soft-delete
 *   DELETE /api/admin/seasonal-perturbations/{id} → hard-delete
 *
 * 展示:
 * - 当前激活的 perturbation (来自 /api/seasonal-factors public read)
 *   → 让没 admin token 的用户也能看到当前生效的 shock
 * - 管理界面 (如果能成功调 admin endpoint):
 *   - 列出所有 perturbation
 *   - 添加新 perturbation (label + start_sim_day + end_sim_day + material + multiplier)
 *   - 删除 / 停用
 * - Auth-required state: 显示友好提示, 但 active shocks 仍可见 (公开数据)
 *
 * 自动 refresh 每 60s (跟其他 panel 一致)
 */

import { useState, useEffect } from 'react'

import { LoadingSpinner } from '../common/LoadingSpinner'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'
const REFRESH_INTERVAL_MS = 60000

// 推断当前 sim_day (从 WS cycle_update 消息 / localStorage / 默认 0)
// 不依赖外部状态: 直接用最新一次 WS 推送的 sim_day 或 fallback 0
function getCurrentSimDay() {
  try {
    const ws = window.__lastCycleMessage
    if (ws && ws.data && typeof ws.data.sim_day === 'number') {
      return ws.data.sim_day
    }
    // Fallback: try localStorage from recent cycle fetch
    const stored = localStorage.getItem('gl_last_sim_day')
    if (stored) return parseInt(stored, 10) || 0
  } catch {
    // ignore
  }
  return 0
}

const MATERIAL_OPTIONS = [
  { value: '*', label: '* (all materials)' },
  { value: 'concrete', label: 'concrete' },
  { value: 'metal_scrap', label: 'metal_scrap' },
  { value: 'wood_waste', label: 'wood_waste' },
  { value: 'mixed_waste', label: 'mixed_waste' },
  { value: 'plastic', label: 'plastic' },
  { value: 'paper_cardboard', label: 'paper_cardboard' },
]

function perturbationTypeColor(multiplier) {
  if (multiplier > 1.05) return '#22c55e'   // boost (green)
  if (multiplier < 0.95) return '#ef4444'   // dampen (red)
  return '#94a3b8'                           // neutral (grey)
}

function perturbationTypeLabel(multiplier) {
  if (multiplier > 1.05) return 'BOOST'
  if (multiplier < 0.95) return 'DAMPEN'
  return 'NEUTRAL'
}

export function SeasonalPerturbationPanel() {
  const [simDay, setSimDay] = useState(getCurrentSimDay)
  const [active, setActive] = useState([])  // from public /api/seasonal-factors
  const [allPerturbations, setAllPerturbations] = useState([])  // from admin endpoint
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [adminAuthRequired, setAdminAuthRequired] = useState(false)
  // Add form state
  const [showForm, setShowForm] = useState(false)
  const [formLabel, setFormLabel] = useState('')
  const [formStart, setFormStart] = useState(0)
  const [formEnd, setFormEnd] = useState(30)
  const [formMaterial, setFormMaterial] = useState('*')
  const [formMultiplier, setFormMultiplier] = useState(1.5)
  const [formError, setFormError] = useState(null)
  const [formSubmitting, setFormSubmitting] = useState(false)

  const fetchData = async () => {
    try {
      // 1. Always fetch public seasonal-factors (active perturbations are public)
      const seasonalResp = await fetch(
        `${API_BASE}/seasonal-factors?sim_day=${simDay}`
      )
      if (seasonalResp.ok) {
        const seasonalData = await seasonalResp.json()
        setActive(seasonalData.active_perturbations || [])
      }
      // 2. Try admin endpoint for full list (may 401 if auth required)
      const adminResp = await fetch(`${API_BASE}/admin/seasonal-perturbations`)
      if (adminResp.status === 401) {
        setAdminAuthRequired(true)
        setAllPerturbations([])
      } else if (adminResp.ok) {
        const adminData = await adminResp.json()
        setAllPerturbations(adminData.perturbations || [])
        setAdminAuthRequired(false)
      }
      setError(null)
      setLoading(false)
    } catch (e) {
      setError(e.message || 'fetch failed')
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, REFRESH_INTERVAL_MS)
    return () => clearInterval(interval)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [simDay])

  const handleSubmit = async (e) => {
    e.preventDefault()
    setFormError(null)
    setFormSubmitting(true)
    try {
      const params = new URLSearchParams({
        label: formLabel,
        start_sim_day: formStart.toString(),
        end_sim_day: formEnd.toString(),
        material_type: formMaterial,
        multiplier: formMultiplier.toString(),
      })
      const resp = await fetch(
        `${API_BASE}/admin/seasonal-perturbations?${params}`,
        { method: 'POST' }
      )
      if (resp.status === 401) {
        setFormError('Admin token required (set GL_ADMIN_TOKEN on server)')
        setAdminAuthRequired(true)
        return
      }
      if (!resp.ok) {
        const txt = await resp.text()
        throw new Error(`HTTP ${resp.status}: ${txt}`)
      }
      // success — reset form and refresh
      setShowForm(false)
      setFormLabel('')
      await fetchData()
    } catch (e) {
      setFormError(e.message)
    } finally {
      setFormSubmitting(false)
    }
  }

  const handleDelete = async (id) => {
    try {
      const resp = await fetch(
        `${API_BASE}/admin/seasonal-perturbations/${id}`,
        { method: 'DELETE' }
      )
      if (resp.ok) await fetchData()
    } catch (e) {
      setError(`delete failed: ${e.message}`)
    }
  }

  const handleDeactivate = async (id) => {
    try {
      const resp = await fetch(
        `${API_BASE}/admin/seasonal-perturbations/${id}/deactivate`,
        { method: 'POST' }
      )
      if (resp.ok) await fetchData()
    } catch (e) {
      setError(`deactivate failed: ${e.message}`)
    }
  }

  if (loading) return <LoadingSpinner label="Loading perturbations…" />

  return (
    <div className="chart-card">
      <div className="chart-card-header">
        <h3>⚡ Seasonal Perturbations (iter #37)</h3>
        <span className="chart-card-sub">
          Active shocks that overlay the static monthly seasonal factors
        </span>
      </div>

      {error && (
        <div className="error-banner" style={{ marginTop: '0.75rem' }}>
          ⚠️ {error}
        </div>
      )}

      {/* Currently active shocks (visible to everyone) */}
      <div style={{ marginTop: '1rem' }}>
        <h4 style={{ margin: '0 0 0.5rem 0', color: '#cbd5e1', fontSize: '0.95rem' }}>
          🎯 Active at sim_day={simDay}
        </h4>
        {active.length === 0 ? (
          <div className="empty-state" style={{ color: '#94a3b8', padding: '0.5rem 0' }}>
            No perturbations active — supply/demand use baseline seasonal factors.
          </div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '0.5rem' }}>
            {active.map(p => (
              <div key={p.id}
                style={{
                  padding: '0.6rem 0.75rem',
                  borderRadius: '4px',
                  background: '#1e293b',
                  borderLeft: `4px solid ${perturbationTypeColor(p.multiplier)}`,
                  border: '1px solid #475569',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                  <span style={{ color: '#e2e8f0', fontSize: '0.9rem' }}>{p.label}</span>
                  <span style={{
                    color: perturbationTypeColor(p.multiplier),
                    fontSize: '0.75rem',
                    fontFamily: 'monospace',
                  }}>
                    ×{p.multiplier.toFixed(2)}
                  </span>
                </div>
                <div style={{ marginTop: '0.25rem', color: '#94a3b8', fontSize: '0.75rem' }}>
                  {perturbationTypeLabel(p.multiplier)} · mat={p.material_type} · days {p.start_sim_day}–{p.end_sim_day}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Admin section (only if we can read it) */}
      <div style={{ marginTop: '1.5rem', borderTop: '1px solid #334155', paddingTop: '1rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
          <h4 style={{ margin: 0, color: '#cbd5e1', fontSize: '0.95rem' }}>
            🛠 Manage perturbations (admin)
          </h4>
          {!adminAuthRequired && (
            <button
              type="button"
              onClick={() => setShowForm(!showForm)}
              style={{
                padding: '0.35rem 0.7rem',
                background: showForm ? '#475569' : '#3b82f6',
                color: '#fff',
                border: 'none',
                borderRadius: '4px',
                cursor: 'pointer',
                fontSize: '0.85rem',
              }}
            >
              {showForm ? 'Cancel' : '+ Add new'}
            </button>
          )}
        </div>

        {adminAuthRequired && (
          <div className="info-banner" style={{
            marginTop: '0.75rem', padding: '0.5rem 0.75rem',
            background: '#1e3a5f', border: '1px solid #3b82f6',
            borderRadius: '4px', color: '#bfdbfe', fontSize: '0.85rem',
          }}>
            🔒 Admin token required to manage perturbations (set GL_ADMIN_TOKEN on server).
            Active shocks above are still visible because they're exposed via /api/seasonal-factors.
          </div>
        )}

        {showForm && !adminAuthRequired && (
          <form onSubmit={handleSubmit}
            style={{
              marginTop: '0.75rem', padding: '0.75rem',
              background: '#1e293b', borderRadius: '4px', border: '1px solid #475569',
              display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: '0.5rem',
            }}
          >
            <label style={{ display: 'flex', flexDirection: 'column', fontSize: '0.8rem', color: '#94a3b8' }}>
              Label
              <input
                type="text" required maxLength={100}
                value={formLabel} onChange={e => setFormLabel(e.target.value)}
                style={{ padding: '0.4rem', background: '#0f172a', color: '#e2e8f0', border: '1px solid #475569', borderRadius: '3px', marginTop: '0.2rem' }}
              />
            </label>
            <label style={{ display: 'flex', flexDirection: 'column', fontSize: '0.8rem', color: '#94a3b8' }}>
              Start day
              <input
                type="number" required min={0}
                value={formStart} onChange={e => setFormStart(parseInt(e.target.value, 10) || 0)}
                style={{ padding: '0.4rem', background: '#0f172a', color: '#e2e8f0', border: '1px solid #475569', borderRadius: '3px', marginTop: '0.2rem' }}
              />
            </label>
            <label style={{ display: 'flex', flexDirection: 'column', fontSize: '0.8rem', color: '#94a3b8' }}>
              End day
              <input
                type="number" required min={0}
                value={formEnd} onChange={e => setFormEnd(parseInt(e.target.value, 10) || 0)}
                style={{ padding: '0.4rem', background: '#0f172a', color: '#e2e8f0', border: '1px solid #475569', borderRadius: '3px', marginTop: '0.2rem' }}
              />
            </label>
            <label style={{ display: 'flex', flexDirection: 'column', fontSize: '0.8rem', color: '#94a3b8' }}>
              Material
              <select
                value={formMaterial} onChange={e => setFormMaterial(e.target.value)}
                style={{ padding: '0.4rem', background: '#0f172a', color: '#e2e8f0', border: '1px solid #475569', borderRadius: '3px', marginTop: '0.2rem' }}
              >
                {MATERIAL_OPTIONS.map(o => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </label>
            <label style={{ display: 'flex', flexDirection: 'column', fontSize: '0.8rem', color: '#94a3b8' }}>
              Multiplier (0.1–3.0)
              <input
                type="number" required step="0.05" min={0.1} max={3.0}
                value={formMultiplier} onChange={e => setFormMultiplier(parseFloat(e.target.value) || 1.0)}
                style={{ padding: '0.4rem', background: '#0f172a', color: '#e2e8f0', border: '1px solid #475569', borderRadius: '3px', marginTop: '0.2rem' }}
              />
            </label>
            <div style={{ display: 'flex', alignItems: 'flex-end' }}>
              <button
                type="submit" disabled={formSubmitting}
                style={{
                  padding: '0.5rem 1rem',
                  background: formSubmitting ? '#475569' : '#22c55e',
                  color: '#fff', border: 'none', borderRadius: '4px',
                  cursor: formSubmitting ? 'not-allowed' : 'pointer',
                  fontSize: '0.9rem', width: '100%',
                }}
              >
                {formSubmitting ? 'Saving…' : 'Save'}
              </button>
            </div>
            {formError && (
              <div style={{ gridColumn: '1 / -1', color: '#fca5a5', fontSize: '0.85rem' }}>
                ⚠️ {formError}
              </div>
            )}
          </form>
        )}

        {!adminAuthRequired && (
          <div style={{ marginTop: '0.5rem', maxHeight: '240px', overflowY: 'auto' }}>
            {allPerturbations.length === 0 ? (
              <div style={{ color: '#94a3b8', padding: '0.5rem 0', fontSize: '0.85rem' }}>
                No perturbations configured yet.
              </div>
            ) : (
              <table style={{ width: '100%', fontSize: '0.85rem', borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid #334155', color: '#94a3b8', textAlign: 'left' }}>
                    <th style={{ padding: '0.4rem' }}>Label</th>
                    <th style={{ padding: '0.4rem' }}>Window</th>
                    <th style={{ padding: '0.4rem' }}>Material</th>
                    <th style={{ padding: '0.4rem' }}>×</th>
                    <th style={{ padding: '0.4rem' }}>Active</th>
                    <th style={{ padding: '0.4rem' }}></th>
                  </tr>
                </thead>
                <tbody>
                  {allPerturbations.map(p => (
                    <tr key={p.id} style={{ borderBottom: '1px solid #1e293b', color: '#e2e8f0' }}>
                      <td style={{ padding: '0.4rem' }}>{p.label}</td>
                      <td style={{ padding: '0.4rem', fontFamily: 'monospace' }}>{p.start_sim_day}–{p.end_sim_day}</td>
                      <td style={{ padding: '0.4rem' }}>{p.material_type}</td>
                      <td style={{ padding: '0.4rem', color: perturbationTypeColor(p.multiplier), fontFamily: 'monospace' }}>
                        {p.multiplier.toFixed(2)}
                      </td>
                      <td style={{ padding: '0.4rem', color: p.active ? '#22c55e' : '#64748b' }}>
                        {p.active ? '✓' : '—'}
                      </td>
                      <td style={{ padding: '0.4rem', textAlign: 'right' }}>
                        {p.active && (
                          <button
                            onClick={() => handleDeactivate(p.id)}
                            style={{ padding: '0.2rem 0.5rem', background: '#475569', color: '#fff', border: 'none', borderRadius: '3px', cursor: 'pointer', fontSize: '0.75rem', marginRight: '0.3rem' }}
                          >
                            Deactivate
                          </button>
                        )}
                        <button
                          onClick={() => handleDelete(p.id)}
                          style={{ padding: '0.2rem 0.5rem', background: '#7f1d1d', color: '#fff', border: 'none', borderRadius: '3px', cursor: 'pointer', fontSize: '0.75rem' }}
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

export default SeasonalPerturbationPanel
