/**
 * ThemeToggle - light / dark / system theme selector (iter #51)
 *
 * 用法:
 *   <ThemeToggle />                // compact mode (icon button)
 *   <ThemeToggle showLabel />      // 3-option selector with labels
 *
 * Compact mode: 一个按钮, 点击在 light/dark 间切换
 * Selector mode: 3 个按钮 (☀️ light / 🌙 dark / 💻 system)
 *
 * 数据源: useTheme() hook
 *
 * 设计:
 *   - compact mode 不让用户"卡住"在 system (always toggles to opposite)
 *   - selector mode 给 power user 显式选 system
 *   - 当前 mode 在 compact 模式下显示一个 indicator dot
 */

import { useTheme } from '../../hooks/useTheme'

function _icon(resolved) {
  return resolved === 'dark' ? '🌙' : '☀️'
}

function _label(resolved) {
  return resolved === 'dark' ? 'Dark' : 'Light'
}

export function ThemeToggle({ showLabel = false }) {
  const { theme, resolvedTheme, setTheme, toggle } = useTheme()

  if (!showLabel) {
    return (
      <button
        type="button"
        onClick={toggle}
        title={`Theme: ${theme} (resolved: ${resolvedTheme}) — click to toggle`}
        aria-label={`Switch theme (current: ${_label(resolvedTheme)})`}
        className="theme-toggle theme-toggle--compact"
      >
        <span className="theme-toggle-icon">{_icon(resolvedTheme)}</span>
        {theme === 'system' && <span className="theme-toggle-dot" aria-hidden="true" />}
      </button>
    )
  }

  return (
    <div className="theme-toggle theme-toggle--selector" role="group" aria-label="Theme selector">
      <button
        type="button"
        onClick={() => setTheme('light')}
        className={`theme-toggle-btn ${theme === 'light' ? 'active' : ''}`}
        title="Light mode"
      >
        <span aria-hidden="true">☀️</span>
        <span>Light</span>
      </button>
      <button
        type="button"
        onClick={() => setTheme('dark')}
        className={`theme-toggle-btn ${theme === 'dark' ? 'active' : ''}`}
        title="Dark mode"
      >
        <span aria-hidden="true">🌙</span>
        <span>Dark</span>
      </button>
      <button
        type="button"
        onClick={() => setTheme('system')}
        className={`theme-toggle-btn ${theme === 'system' ? 'active' : ''}`}
        title="Follow OS / browser setting"
      >
        <span aria-hidden="true">💻</span>
        <span>System</span>
      </button>
    </div>
  )
}