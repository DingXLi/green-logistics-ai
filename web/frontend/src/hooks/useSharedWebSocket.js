/**
 * useSharedWebSocket - 跨 tab 共享 WebSocket 连接 (iter #14)
 *
 * 设计:
 * - 多个 tab 同时打开 dashboard 时, 只让 1 个 tab 真正维持 WS 连接 ("leader")
 * - Leader 通过 BroadcastChannel 把消息 / 状态变化转发给其他 tab ("followers")
 * - Followers 监听 channel, 复用 leader 的 lastMessage / connected 状态
 * - Leader 断开 (close tab / refresh) → followers 中第一个感知到的会尝试接管
 *
 * 用法 (替换 useWebSocket):
 *   const { lastMessage, connected, reconnecting, isLeader, ... } = useSharedWebSocket(
 *     '/ws/cycle-updates',
 *     { onMessage: (msg) => { ... } }
 *   )
 *
 * 浏览器不支持 BroadcastChannel 时降级到 useWebSocket (无共享)。
 */

import { useEffect, useRef, useState, useCallback } from 'react'
import { useWebSocket } from './useWebSocket'

const CHANNEL_NAME = 'green-logistics-ws'
const HEARTBEAT_MS = 5000       // leader 每 5s 发心跳
const LEADER_TIMEOUT_MS = 12000 // 12s 没心跳 → 接管

function makeChannel() {
  if (typeof BroadcastChannel === 'undefined') return null
  try {
    return new BroadcastChannel(CHANNEL_NAME)
  } catch (e) {
    return null
  }
}

export function useSharedWebSocket(path, options = {}) {
  const channelRef = useRef(null)
  const isLeaderRef = useRef(false)
  const [isLeader, setIsLeader] = useState(false)
  const [tabId] = useState(() =>
    `tab-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  )

  // 我们使用真正的 useWebSocket, 但是只在 leader 模式下
  // Followers 通过 channel 收数据
  const [sharedLastMessage, setSharedLastMessage] = useState(null)
  const [sharedConnected, setSharedConnected] = useState(false)
  const [sharedReconnecting, setSharedReconnecting] = useState(false)
  const [sharedReconnectAttempts, setSharedReconnectAttempts] = useState(0)
  const [sharedLastError, setSharedLastError] = useState(null)

  // Leader: 真开 WS
  const ws = useWebSocket(path, {
    ...options,
    onMessage: (msg) => {
      // Leader: 收到 message → 转发给 followers + 调用原 onMessage
      if (channelRef.current) {
        try {
          channelRef.current.postMessage({ type: 'message', payload: msg })
        } catch (e) { /* ignore */ }
      }
      setSharedLastMessage(msg)
      if (options.onMessage) options.onMessage(msg)
    },
  })

  // Listen to channel
  useEffect(() => {
    const channel = makeChannel()
    channelRef.current = channel
    if (!channel) {
      // No BroadcastChannel support → fall back to direct WS
      isLeaderRef.current = true
      setIsLeader(true)
      return
    }

    // Heartbeat tracking
    let lastHeartbeat = Date.now()
    let heartbeatInterval = null
    let takeoverInterval = null

    const onMessage = (event) => {
      const msg = event.data
      if (!msg || typeof msg !== 'object') return

      if (msg.type === 'heartbeat' && msg.tabId !== tabId) {
        // Other tab is leader, update heartbeat
        lastHeartbeat = Date.now()
        if (isLeaderRef.current) {
          // We thought we were leader but got heartbeat → demote
          isLeaderRef.current = false
          setIsLeader(false)
        }
      } else if (msg.type === 'state' && msg.tabId !== tabId) {
        // Leader 广播的状态变化
        setSharedConnected(!!msg.connected)
        setSharedReconnecting(!!msg.reconnecting)
        setSharedReconnectAttempts(msg.reconnectAttempts || 0)
        setSharedLastError(msg.lastError || null)
      } else if (msg.type === 'message' && msg.tabId !== tabId && !isLeaderRef.current) {
        // Follower 收到 leader 转发的 message
        setSharedLastMessage(msg.payload)
        if (options.onMessage) options.onMessage(msg.payload)
      } else if (msg.type === 'resign' && msg.tabId !== tabId) {
        // Leader 主动辞职 (tab 关闭)
        lastHeartbeat = 0  // 强制 follower 接管
      }
    }
    channel.addEventListener('message', onMessage)

    // Election: 第一个 mount 的 tab 当 leader
    // 简单实现: 每个 tab 都尝试当 leader, 但先 listen 100ms 看有无其他 tab
    const electionTimeout = setTimeout(() => {
      // 先 broadcast 一次询问
      try {
        channel.postMessage({ type: 'heartbeat', tabId })
      } catch (e) { /* ignore */ }
      // 等 100ms 看是否有其他 tab 回应
      setTimeout(() => {
        if (!isLeaderRef.current && Date.now() - lastHeartbeat > LEADER_TIMEOUT_MS) {
          isLeaderRef.current = true
          setIsLeader(true)
        }
      }, 150)
    }, 50)

    // Leader: send heartbeat + state every 5s
    heartbeatInterval = setInterval(() => {
      if (isLeaderRef.current) {
        try {
          channel.postMessage({
            type: 'heartbeat',
            tabId,
            connected: ws.connected,
            reconnecting: ws.reconnecting,
          })
          channel.postMessage({
            type: 'state',
            tabId,
            connected: ws.connected,
            reconnecting: ws.reconnecting,
            reconnectAttempts: ws.reconnectAttempts,
            lastError: ws.lastError,
          })
        } catch (e) { /* ignore */ }
      } else {
        // Follower: check if leader is still alive
        if (Date.now() - lastHeartbeat > LEADER_TIMEOUT_MS) {
          // Leader dead, try to take over
          isLeaderRef.current = true
          setIsLeader(true)
        }
      }
    }, HEARTBEAT_MS)

    // Resign on unmount if we were leader
    return () => {
      clearTimeout(electionTimeout)
      if (heartbeatInterval) clearInterval(heartbeatInterval)
      if (takeoverInterval) clearInterval(takeoverInterval)
      if (isLeaderRef.current) {
        try {
          channel.postMessage({ type: 'resign', tabId })
        } catch (e) { /* ignore */ }
      }
      channel.removeEventListener('message', onMessage)
      try {
        channel.close()
      } catch (e) { /* ignore */ }
    }
  }, [tabId])

  return {
    // If we are leader, use real WS state; if follower, use shared state
    connected: isLeader ? ws.connected : sharedConnected,
    lastMessage: isLeader ? ws.lastMessage : sharedLastMessage,
    reconnecting: isLeader ? ws.reconnecting : sharedReconnecting,
    reconnectAttempts: isLeader ? ws.reconnectAttempts : sharedReconnectAttempts,
    lastError: isLeader ? ws.lastError : sharedLastError,
    isLeader,  // 暴露给 UI 显示 "Leader"/"Follower" badge (optional)
    sendPing: ws.sendPing,  // only leader can ping
  }
}

export default useSharedWebSocket
