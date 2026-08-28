/**
 * WSStatusIndicator - WebSocket 连接状态指示器 (iter #13/14)
 *
 * 用法:
 *   <WSStatusIndicator
 *     connected={wsConnected}
 *     reconnecting={wsReconnecting}
 *     attempts={wsAttempts}
 *     lastError={wsLastError}
 *     isLeader={wsIsLeader}        // iter #14: 显示 leader/follower
 *   />
 *
 * 显示:
 *   - 🟢 Live [👑 Leader]   (本 tab 维持 WS)
 *   - 🟢 Live [👥 Follower] (从其他 tab 接收)
 *   - 🟡 Reconnecting (N)   (attempt N, max 30s backoff)
 *   - 🔴 Disconnected
 */

export function WSStatusIndicator({
  connected,
  reconnecting,
  attempts = 0,
  lastError = null,
  isLeader = true,
}) {
  let label = '🔴 Disconnected'
  let className = 'ws-status disconnected'

  if (connected) {
    const role = isLeader ? '[👑 Leader]' : '[👥 Follower]'
    label = `🟢 Live ${role}`
    className = 'ws-status connected'
  } else if (reconnecting) {
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
