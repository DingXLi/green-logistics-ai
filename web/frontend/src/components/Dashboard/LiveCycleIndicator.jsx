/**
 * LiveCycleIndicator - 实时显示 WS 推送的最新 cycle
 *
 * 用法:
 *   const { lastMessage } = useWebSocket('/ws/cycle-updates')
 *   <LiveCycleIndicator message={lastMessage} connected={connected} />
 */
import { useState, useEffect } from 'react'

function formatRelativeTime(iso) {
  if (!iso) return 'never'
  const then = new Date(iso).getTime()
  const now = Date.now()
  const seconds = Math.floor((now - then) / 1000)
  if (seconds < 5) return 'just now'
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  return `${Math.floor(seconds / 3600)}h ago`
}

export function LiveCycleIndicator({ message, connected }) {
  const [, setTick] = useState(0)
  // 每秒重渲染 → "5s ago" 自动更新
  useEffect(() => {
    const id = setInterval(() => setTick(t => t + 1), 1000)
    return () => clearInterval(id)
  }, [])

  const isCycleUpdate = message?.type === 'cycle_update'
  const data = message?.data

  return (
    <div className={`live-cycle-indicator ${connected ? 'connected' : 'disconnected'}`}>
      <div className="live-status-row">
        <span className={`live-dot ${connected ? 'pulse' : ''}`} />
        <span className="live-status-text">
          {connected ? '🟢 Live' : '🔴 Offline'}
        </span>
      </div>
      {isCycleUpdate && data ? (
        <div className="live-cycle-data">
          <div className="live-cycle-time">
            <strong>Last cycle:</strong>{' '}
            <span title={message.timestamp}>
              {formatRelativeTime(message.timestamp)}
            </span>
            {' '}(D{data.sim_day ?? '?'})
          </div>
          <div className="live-cycle-stats">
            <span className="stat">
              <strong>{data.n_matches ?? 0}</strong> matches
            </span>
            <span className="stat">
              <strong>{data.total_tons?.toFixed?.(1) ?? '0'}</strong> t
            </span>
            <span className="stat">
              <strong>{Math.round(data.total_cost_sek ?? 0)}</strong> SEK
            </span>
            <span className="stat">
              <strong>{Math.round(data.total_co2_kg ?? 0)}</strong> kg CO₂
            </span>
          </div>
        </div>
      ) : (
        <div className="live-cycle-data empty">
          {connected
            ? 'Waiting for first cycle_update...'
            : 'Reconnecting to WebSocket...'}
        </div>
      )}
    </div>
  )
}