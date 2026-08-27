/**
 * LoadingSpinner - 通用加载中组件 (iter #7)
 *
 * 用途:
 * - Suspense fallback (替代空 div)
 * - API fetch 加载状态
 * - 长操作 (OR-Tools 求解、OSM 距离下载)
 *
 * Props:
 * - size: "sm" | "md" | "lg" (default "md")
 * - label: 文字提示 (default "Loading…")
 * - inline: 是否内联 (vs block)
 */

export function LoadingSpinner({ size = 'md', label = 'Loading…', inline = false }) {
  const sizeMap = {
    sm: 16,
    md: 28,
    lg: 48,
  }
  const px = sizeMap[size] || 28

  const wrapperStyle = inline
    ? { display: 'inline-flex', alignItems: 'center', gap: '8px' }
    : {
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: '12px',
        padding: '24px',
        color: 'var(--muted, #666)',
      }

  return (
    <div className="loading-spinner" style={wrapperStyle} role="status" aria-live="polite">
      <div
        className="spinner-ring"
        style={{
          width: `${px}px`,
          height: `${px}px`,
          border: `${Math.max(2, px / 8)}px solid var(--muted, #e0e0e0)`,
          borderTopColor: 'var(--accent, #2e7d32)',
          borderRadius: '50%',
          animation: 'spin 0.9s linear infinite',
        }}
      />
      {label && (
        <span style={{ fontSize: px < 20 ? '12px' : '14px', color: 'inherit' }}>
          {label}
        </span>
      )}
    </div>
  )
}

export default LoadingSpinner