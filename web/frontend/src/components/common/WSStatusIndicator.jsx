/**
 * WSStatusIndicator - WebSocket 连接状态指示器 (iter #13)
 *
 * 用法:
 *   <WSStatusIndicator
 *     connected={wsConnected}
 *     reconnecting={wsReconnecting}
 *     attempts={wsAttempts}
 *     lastError={wsLastError}
 *   />
 *
 * 显示:
 *   - 🟢 Connected (默认, 实时推送)
 *   - 🟡 Reconnecting... (attempt N, max 30s backoff)
 *   - 🔴 Disconnected (多次重连失败)
 */

export function WSStatusIndicator({
  connected,
  reconnecting,
  attempts = 0,
  lastError = null,
}) {
  let status = 'disconnected'
  let label = '🔴 Disconnected'
  let className = 'ws-status disconnected'

  if (connected) {
    status = 'connected'
    label = '🟢 Live'
    className = 'ws-status connected'
  } else if (reconnecting) {
    status = 'reconnecting'
    label = `🟡 Reconnecting (${attempts})`
    className = 'ws-status reconnecting'
  }

  return (
    <span
      className={className}
      title={
        lastError
          ? `${label}: ${lastError}`
          : label
      }
    >
      {label}
    </span>
  )
}

export default WSStatusIndicator
