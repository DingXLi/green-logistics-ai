/**
 * useWebSocket - React hook 订阅后端 WS 推送
 *
 * 用法:
 *   const { lastMessage, connected, reconnecting, reconnectAttempts, sendPing }
 *     = useWebSocket('/ws/cycle-updates', { onMessage: (msg) => { ... } })
 *
 * 特性 (iter #13):
 *   - 自动重连 (指数退避: 1s → 2s → 4s → 8s → max 30s)
 *   - 组件卸载自动断开
 *   - sendPing() 让客户端主动 ping server (测连通性)
 *   - lastMessage.data 自动解析 JSON
 *   - 新增 reconnecting + reconnectAttempts + lastError 给 UI 显示状态
 */
import { useEffect, useRef, useState, useCallback } from 'react'

export function useWebSocket(path, options = {}) {
  const { onMessage, onConnect, onDisconnect, autoReconnect = true } = options

  const [connected, setConnected] = useState(false)
  const [lastMessage, setLastMessage] = useState(null)
  const [reconnecting, setReconnecting] = useState(false)  // iter #13
  const [reconnectAttempts, setReconnectAttempts] = useState(0)  // iter #13
  const [lastError, setLastError] = useState(null)  // iter #13
  const wsRef = useRef(null)
  const reconnectTimeoutRef = useRef(null)
  const reconnectAttemptRef = useRef(0)
  const mountedRef = useRef(true)

  const resolveUrl = useCallback((p) => {
    // API_BASE = "https://lidingx-green-logistics.hf.space/api"
    // path = "/ws/cycle-updates" → ws://... 或 https://... 转 ws://...
    const base = import.meta.env.VITE_API_BASE || ''
    if (!base) {
      // 本地 dev: 直接连 localhost
      const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
      return `${proto}://${window.location.host}${p}`
    }
    // 把 http(s)://host/api 转成 ws(s)://host, path 已经在 WS endpoint 里
    const wsBase = base.replace(/^http/, 'ws').replace(/\/api$/, '')
    return `${wsBase}${p}`
  }, [])

  const sendPing = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send('ping')
    }
  }, [])

  useEffect(() => {
    mountedRef.current = true

    const connect = () => {
      if (!mountedRef.current) return
      const url = resolveUrl(path)
      try {
        const ws = new WebSocket(url)
        wsRef.current = ws

        ws.onopen = () => {
          if (!mountedRef.current) {
            ws.close()
            return
          }
          setConnected(true)
          setReconnecting(false)
          setLastError(null)
          reconnectAttemptRef.current = 0
          setReconnectAttempts(0)
          if (onConnect) onConnect()
        }

        ws.onmessage = (event) => {
          if (!mountedRef.current) return
          // pong 是 plain text, 其他是 JSON
          if (event.data === 'pong') return
          try {
            const data = JSON.parse(event.data)
            setLastMessage(data)
            if (onMessage) onMessage(data)
          } catch (e) {
            // 忽略非 JSON 消息
          }
        }

        ws.onerror = (event) => {
          // iter #13: 记录错误给 UI
          setLastError(`WebSocket error (attempt ${reconnectAttemptRef.current + 1})`)
        }

        ws.onclose = () => {
          if (!mountedRef.current) return
          setConnected(false)
          if (onDisconnect) onDisconnect()
          if (autoReconnect) {
            reconnectAttemptRef.current += 1
            setReconnectAttempts(reconnectAttemptRef.current)
            setReconnecting(true)
            const delay = Math.min(
              30000,
              1000 * Math.pow(2, reconnectAttemptRef.current - 1)
            )
            reconnectTimeoutRef.current = setTimeout(connect, delay)
          }
        }
      } catch (e) {
        setLastError(`Failed to create WebSocket: ${e.message}`)
        // 创建 WS 失败也走重连
        if (autoReconnect) {
          reconnectAttemptRef.current += 1
          setReconnectAttempts(reconnectAttemptRef.current)
          setReconnecting(true)
          const delay = Math.min(30000, 1000 * Math.pow(2, reconnectAttemptRef.current - 1))
          reconnectTimeoutRef.current = setTimeout(connect, delay)
        }
      }
    }

    connect()

    return () => {
      mountedRef.current = false
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current)
      }
      if (wsRef.current) {
        wsRef.current.close()
      }
    }
  }, [path, onMessage, onConnect, onDisconnect, autoReconnect, resolveUrl])

  return {
    connected,
    lastMessage,
    reconnecting,           // iter #13: UI 可显示 "Reconnecting..."
    reconnectAttempts,      // iter #13: 重连次数
    lastError,              // iter #13: 最近一次错误
    sendPing,
  }
}