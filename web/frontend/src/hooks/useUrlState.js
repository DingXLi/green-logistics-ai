/**
 * useUrlState - sync React state with URL query params (iter #25)
 *
 * 让组件状态可分享 / 收藏:
 * - state 变化自动写到 URL ?key=value
 * - 浏览器 back/forward 也能恢复 state (via popstate)
 * - 多个 component 用不同 key 互不干扰
 *
 * 用法:
 *   const [tab, setTab] = useUrlState('tab', 'overview')
 *   // 自动 read ?tab=overview from URL, write on setTab()
 *
 * 优势 vs location.hash:
 * - 支持多个 param (e.g., ?tab=overview&n_periods=8)
 * - 类型自动 parse (int, bool, string)
 * - SSR 安全 (typeof window check)
 *
 * 边缘 cases:
 * - URL 无 param → 使用 default
 * - URL 有 param 但 invalid → fallback 到 default
 * - 同 key 多次 update → batch (history.replaceState 不创建 history entry)
 * - 不同 key update → 各自更新, single replaceState
 */

import { useState, useEffect, useCallback, useRef } from 'react'

/**
 * Read initial value from URL or fallback to default.
 */
function readUrlState(key, defaultValue, parse) {
  if (typeof window === 'undefined') return defaultValue
  try {
    const params = new URLSearchParams(window.location.search)
    const raw = params.get(key)
    if (raw === null) return defaultValue
    return parse(raw)
  } catch (e) {
    // Invalid URL or parse error → fallback
    return defaultValue
  }
}

/**
 * Parse string to typed value.
 */
function makeParser(type) {
  if (type === 'int') return (v) => {
    const n = parseInt(v, 10)
    return Number.isFinite(n) ? n : null
  }
  if (type === 'float') return (v) => {
    const n = parseFloat(v)
    return Number.isFinite(n) ? n : null
  }
  if (type === 'bool') return (v) => {
    if (v === 'true' || v === '1') return true
    if (v === 'false' || v === '0') return false
    return null
  }
  // default: string
  return (v) => v
}

function stringifyValue(v) {
  if (v === null || v === undefined) return null
  return String(v)
}

/**
 * useUrlState hook.
 *
 * @param {string} key - URL param name (e.g., 'tab')
 * @param {any} defaultValue - fallback if URL param missing
 * @param {string} type - 'string' | 'int' | 'float' | 'bool'
 * @returns {[value, setValue]} - similar to useState
 */
export function useUrlState(key, defaultValue, type = 'string') {
  const parser = makeParser(type)
  const [value, setValueInternal] = useState(() => {
    const fromUrl = readUrlState(key, defaultValue, parser)
    return fromUrl === null ? defaultValue : fromUrl
  })

  // Track if this is the initial mount (avoid double-write)
  const initialMount = useRef(true)

  // Update URL when state changes
  useEffect(() => {
    if (typeof window === 'undefined') return
    try {
      const params = new URLSearchParams(window.location.search)
      const currentRaw = params.get(key)
      const newRaw = stringifyValue(value)

      if (currentRaw === newRaw) return  // no change

      if (newRaw === null || newRaw === String(defaultValue)) {
        // Remove param (URL cleanliness)
        params.delete(key)
      } else {
        params.set(key, newRaw)
      }

      // Build new URL
      const newSearch = params.toString()
      const newUrl = `${window.location.pathname}${newSearch ? '?' + newSearch : ''}${window.location.hash}`

      // Use replaceState (not pushState) to avoid history pollution
      // Each state change doesn't create a back-button entry
      window.history.replaceState({}, '', newUrl)
    } catch (e) {
      // Ignore URL update errors (e.g., in tests)
    }
  }, [key, value, defaultValue])

  // Listen for popstate (browser back/forward)
  useEffect(() => {
    if (typeof window === 'undefined') return
    const onPopState = () => {
      const fromUrl = readUrlState(key, defaultValue, parser)
      const newVal = fromUrl === null ? defaultValue : fromUrl
      setValueInternal((prev) => (prev === newVal ? prev : newVal))
    }
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [key, defaultValue, parser])

  const setValue = useCallback((newValue) => {
    setValueInternal(newValue)
  }, [])

  return [value, setValue]
}

/**
 * useUrlStateObject - manage multiple URL params at once.
 *
 * @param {string[]} keys - param names
 * @param {object} defaults - { key: defaultValue }
 * @returns {[object, function]} - [{ k1: v1, k2: v2 }, setValue({ k1: v1 })]
 */
export function useUrlStateMulti(keys, defaults) {
  const [state, setStateInternal] = useState(() => {
    const initial = {}
    for (const key of keys) {
      const parser = makeParser(typeof defaults[key] === 'number' ? (Number.isInteger(defaults[key]) ? 'int' : 'float') : 'string')
      const fromUrl = readUrlState(key, defaults[key], parser)
      initial[key] = fromUrl === null ? defaults[key] : fromUrl
    }
    return initial
  })

  useEffect(() => {
    if (typeof window === 'undefined') return
    try {
      const params = new URLSearchParams(window.location.search)
      let changed = false
      for (const key of keys) {
        const currentRaw = params.get(key)
        const newRaw = stringifyValue(state[key])
        if (currentRaw !== newRaw) {
          changed = true
          if (newRaw === null || newRaw === String(defaults[key])) {
            params.delete(key)
          } else {
            params.set(key, newRaw)
          }
        }
      }
      if (!changed) return
      const newSearch = params.toString()
      const newUrl = `${window.location.pathname}${newSearch ? '?' + newSearch : ''}${window.location.hash}`
      window.history.replaceState({}, '', newUrl)
    } catch (e) {
      // ignore
    }
  }, [keys, state, defaults])

  // popstate listener
  useEffect(() => {
    if (typeof window === 'undefined') return
    const onPopState = () => {
      const newState = {}
      for (const key of keys) {
        const parser = makeParser(typeof defaults[key] === 'number' ? (Number.isInteger(defaults[key]) ? 'int' : 'float') : 'string')
        const fromUrl = readUrlState(key, defaults[key], parser)
        newState[key] = fromUrl === null ? defaults[key] : fromUrl
      }
      setStateInternal((prev) => {
        // Only update if changed
        for (const k of keys) {
          if (prev[k] !== newState[k]) return newState
        }
        return prev
      })
    }
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [keys, defaults])

  const setState = useCallback((updates) => {
    setStateInternal((prev) => ({ ...prev, ...updates }))
  }, [])

  return [state, setState]
}

export default useUrlState
