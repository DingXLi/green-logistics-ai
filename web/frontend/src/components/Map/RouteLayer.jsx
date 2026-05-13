/**
 * RouteLayer.jsx - 优化路线可视化层
 * 
 * 功能：
 * - 显示起点到终点的路线
 * - 按目标着色（利润/碳排/成本）
 * - 动画效果显示运输方向
 */

import { Polyline, Popup } from 'react-leaflet'
import { useMemo } from 'react'

// 优化目标对应的颜色
const OBJECTIVE_COLORS = {
  profit: '#22c55e',     // 绿色 - 利润优先
  carbon: '#3b82f6',      // 蓝色 - 碳排优先
  cost: '#f97316',        // 橙色 - 成本优先
  balanced: '#8b5cf6'     // 紫色 - 均衡
}

// 路线样式
const createRouteStyle = (objective) => ({
  color: OBJECTIVE_COLORS[objective] || OBJECTIVE_COLORS.balanced,
  weight: 4,
  opacity: 0.8,
  dashArray: objective === 'carbon' ? '10, 10' : null  // 碳排优先用虚线
})

function RouteLayer({ optimizationResult }) {
  // 从优化结果中提取路线
  const routes = useMemo(() => {
    if (!optimizationResult?.route_optimization?.routes) {
      return []
    }

    return optimizationResult.route_optimization.routes.map((route, index) => ({
      id: route.vehicle_id || `Route ${index + 1}`,
      waypoints: route.waypoints || [],
      objective: optimizationResult.route_optimization.objective || 'balanced',
      totalDistance: route.total_distance_km || 0,
      totalCost: route.total_cost_sek || 0,
      totalCO2: route.total_co2_kg || 0,
      load: route.cargo_tons || 0
    }))
  }, [optimizationResult])

  // 渲染路线
  const routePolylines = useMemo(() => {
    return routes.map((route) => {
      // 跳过无效路线
      if (!route.waypoints || route.waypoints.length < 2) return null

      // 将 waypoints 转换为 [lat, lon] 数组
      const positions = route.waypoints.map(wp => [wp.lat, wp.lon])

      return (
        <Polyline
          key={route.id}
          positions={positions}
          pathOptions={createRouteStyle(route.objective)}
        >
          <Popup>
            <div style={{ minWidth: '160px' }}>
              <h4 style={{ margin: '0 0 8px 0' }}>
                🚚 {route.id}
              </h4>
              <table style={{ fontSize: '11px', width: '100%' }}>
                <tbody>
                  <tr>
                    <td><strong>距离:</strong></td>
                    <td>{route.totalDistance.toFixed(1)} km</td>
                  </tr>
                  <tr>
                    <td><strong>成本:</strong></td>
                    <td>{route.totalCost.toFixed(0)} SEK</td>
                  </tr>
                  <tr>
                    <td><strong>碳排:</strong></td>
                    <td>{route.totalCO2.toFixed(1)} kg</td>
                  </tr>
                  <tr>
                    <td><strong>载重:</strong></td>
                    <td>{route.load.toFixed(1)} 吨</td>
                  </tr>
                  <tr>
                    <td><strong>停靠点:</strong></td>
                    <td>{route.waypoints.length}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </Popup>
        </Polyline>
      )
    })
  }, [routes])

  // 没有路线时返回 null
  if (routes.length === 0) {
    return null
  }

  return <>{routePolylines}</>
}

export default RouteLayer
