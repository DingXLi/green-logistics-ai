/**
 * useDashboardSummary - React hook 调用 /api/dashboard-summary (iter #12)
 *
 * 替代 Dashboard.jsx 中 N 个并行 fetch 调用, 改为单次 aggregator 调用。
 *
 * 用法:
 *   const { data, loading, error, refetch } = useDashboardSummary({
 *     pollIntervalMs: 10000,
 *   })
 *
 * 返回 data 形状 (来自后端):
 *   {
 *     timestamp, health, summary, efficiency, fleet, last_cycle, scheduler
 *   }
 */

import { useState, useEffect, useCallback, useRef } from 'react'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'

export function useDashboardSummary(options = {}) {
  const { pollIntervalMs = 0, autoRefresh = false } = options
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const mountedRef = useRef(true)

  const fetchOnce = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/dashboard-summary`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json()
      if (!mountedRef.current) return
      setData(d)
      setError(null)
    } catch (e) {
      if (!mountedRef.current) return
      setError(e.message)
    } finally {
      if (mountedRef.current) setLoading(false)
    }
  }, [])

  const refetch = useCallback(async () => {
    setLoading(true)
    await fetchOnce()
  }, [fetchOnce])

  useEffect(() => {
    mountedRef.current = true
    fetchOnce()
    return () => {
      mountedRef.current = false
    }
  }, [fetchOnce])

  // optional polling
  useEffect(() => {
    if (!autoRefresh || pollIntervalMs <= 0) return
    const id = setInterval(fetchOnce, pollIntervalMs)
    return () => clearInterval(id)
  }, [autoRefresh, pollIntervalMs, fetchOnce])

  return { data, loading, error, refetch }
}

export default useDashboardSummary
