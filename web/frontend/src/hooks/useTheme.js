/**
 * useTheme - React hook for light/dark/system theme switching (iter #51)
 *
 * 用法:
 *   const { theme, resolvedTheme, setTheme, toggle } = useTheme()
 *
 * 行为:
 *   - 三种 theme: 'light' | 'dark' | 'system'
 *   - 'system' 跟随 prefers-color-scheme (OS / browser 设置)
 *   - 选择持久化到 localStorage (key: 'green-logistics-theme')
 *   - 解析后的 theme 写到 <html data-theme="..."> 属性, 触发 CSS 变量切换
 *   - 监听 prefers-color-scheme 变化, 'system' 模式自动跟随
 *
 * 设计:
 *   - 客户端 only, 无 SSR
 *   - 默认 'system' (初次访问用户用 OS 偏好)
 *   - 切换瞬间生效 (避免 flash of wrong theme)
 *
 * CSS 集成 (App.css):
 *   :root { --bg-primary: #f8fafc; ... }
 *   [data-theme="dark"] { --bg-primary: #0f172a; ... }
 *   @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { ... } }
 */

import { useEffect, useState, useCallback } from 'react'

const STORAGE_KEY = 'green-logistics-theme'
const VALID_THEMES = ['light', 'dark', 'system']

function _readStoredTheme() {
  if (typeof window === 'undefined') return 'system'
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    if (stored && VALID_THEMES.includes(stored)) return stored
  } catch (_) {
    // localStorage 不可用 (隐私模式 etc.) — silent fallback
  }
  return 'system'
}

function _systemPrefersDark() {
  if (typeof window === 'undefined') return false
  try {
    return window.matchMedia('(prefers-color-scheme: dark)').matches
  } catch (_) {
    return false
  }
}

function _resolve(theme) {
  if (theme === 'system') return _systemPrefersDark() ? 'dark' : 'light'
  return theme
}

function _applyToDOM(resolved) {
  if (typeof document === 'undefined') return
  document.documentElement.setAttribute('data-theme', resolved)
}

export function useTheme() {
  const [theme, setThemeState] = useState(() => _readStoredTheme())
  const [resolvedTheme, setResolvedTheme] = useState(() => _resolve(_readStoredTheme()))

  // Apply theme to <html data-theme="...">
  useEffect(() => {
    const resolved = _resolve(theme)
    _applyToDOM(resolved)
    setResolvedTheme(resolved)
  }, [theme])

  // Listen to system theme change when in 'system' mode
  useEffect(() => {
    if (theme !== 'system') return undefined
    if (typeof window === 'undefined') return undefined
    let mql
    try {
      mql = window.matchMedia('(prefers-color-scheme: dark)')
    } catch (_) {
      return undefined
    }
    const handler = () => {
      const resolved = _resolve('system')
      _applyToDOM(resolved)
      setResolvedTheme(resolved)
    }
    // modern browsers
    if (mql.addEventListener) {
      mql.addEventListener('change', handler)
      return () => mql.removeEventListener('change', handler)
    }
    // legacy fallback
    if (mql.addListener) {
      mql.addListener(handler)
      return () => mql.removeListener(handler)
    }
    return undefined
  }, [theme])

  const setTheme = useCallback((next) => {
    if (!VALID_THEMES.includes(next)) return
    setThemeState(next)
    try {
      if (typeof window !== 'undefined') {
        window.localStorage.setItem(STORAGE_KEY, next)
      }
    } catch (_) {
      // ignore
    }
  }, [])

  const toggle = useCallback(() => {
    setTheme(resolvedTheme === 'dark' ? 'light' : 'dark')
  }, [resolvedTheme, setTheme])

  return { theme, resolvedTheme, setTheme, toggle, themes: VALID_THEMES }
}