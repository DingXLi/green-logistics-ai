/**
 * VehicleLayer.jsx - 车辆实时位置层
 * 
 * 功能：
 * - 显示车队当前位置
 * - 根据状态着色（行驶中/空闲/充电中）
 * - 点击显示详情
 * - 轨迹历史（可选）
 */

import { Marker, Popup, Polyline } from 'react-leaflet'
import L from 'leaflet'
import { useMemo, useState } from 'react'

// 车辆状态颜色
const STATUS_COLORS = {
  en_route: '#3b82f6',    // 蓝色 - 行驶中
  available: '#22c55e',    // 绿色 - 可用
  charging: '#eab308',    // 黄色 - 充电中
  idle: '#9ca3af'         // 灰色 - 闲置
}

// 车辆图标
const createVehicleIcon = (status, heading = 0) => {
  const color = STATUS_COLORS[status] || STATUS_COLORS.idle
  const rotation = heading || 0
  
  return L.divIcon({
    className: 'vehicle-marker',
    html: `
      <div style="
        position: relative;
        width: 28px;
        height: 28px;
        transform: rotate(${rotation}deg);
      ">
        <svg viewBox="0 0 24 24" fill="${color}" style="width: 100%; height: 100%; filter: drop-shadow(0 2px 4px rgba(0,0,0,0.3));">
          <path d="M12 2L4 12l3 2v6h10v-6l3-2L12 2z"/>
        </svg>
      </div>
    `,
    iconSize: [28, 28],
    iconAnchor: [14, 14]
  })
}

function VehicleLayer({ vehicles = [] }) {
  const [selectedVehicle, setSelectedVehicle] = useState(null)
  const [showTrails, setShowTrails] = useState(false)

  // 车辆标记
  const vehicleMarkers = useMemo(() => {
    return vehicles.map((vehicle) => {
      // 跳过无效坐标
      if (!vehicle.latitude || !vehicle.longitude) return null
      
      return (
        <Marker
          key={vehicle.vehicle_id}
          position={[vehicle.latitude, vehicle.longitude]}
          icon={createVehicleIcon(vehicle.status, vehicle.heading)}
          eventHandlers={{
            click: () => setSelectedVehicle(vehicle)
          }}
        >
          <Popup>
            <div style={{ minWidth: '200px' }}>
              <h4 style={{ margin: '0 0 8px 0', color: '#2563eb' }}>
                🚛 {vehicle.vehicle_id}
              </h4>
              <table style={{ fontSize: '11px', width: '100%' }}>
                <tbody>
                  <tr>
                    <td><strong>状态:</strong></td>
                    <td style={{ color: STATUS_COLORS[vehicle.status] }}>
                      {getStatusText(vehicle.status)}
                    </td>
                  </tr>
                  <tr>
                    <td><strong>载重:</strong></td>
                    <td>{vehicle.cargo_load?.toFixed(1) || 0} 吨</td>
                  </tr>
                  <tr>
                    <td><strong>电量:</strong></td>
                    <td>{vehicle.battery_level || 100}%</td>
                  </tr>
                  <tr>
                    <td><strong>速度:</strong></td>
                    <td>{vehicle.speed || 0} km/h</td>
                  </tr>
                  <tr>
                    <td><strong>碳排率:</strong></td>
                    <td>{vehicle.carbon_emission_rate?.toFixed(2) || 0} kg/km</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </Popup>
        </Marker>
      )
    })
  }, [vehicles])

  // 轨迹线（如果启用）
  const trailLines = useMemo(() => {
    if (!showTrails) return null

    return vehicles
      .filter(v => v.trail && v.trail.length > 0)
      .map((vehicle) => (
        <Polyline
          key={`trail-${vehicle.vehicle_id}`}
          positions={vehicle.trail.map(p => [p.lat, p.lon])}
          color={STATUS_COLORS[vehicle.status]}
          weight={2}
          opacity={0.6}
        />
      ))
  }, [vehicles, showTrails])

  return (
    <>
      {vehicleMarkers}
      {trailLines}
      
      {/* 轨迹切换按钮 */}
      <div className="vehicle-layer-controls" style={{
        position: 'absolute',
        top: '10px',
        left: '10px',
        zIndex: 1000,
        background: 'white',
        padding: '8px',
        borderRadius: '4px',
        boxShadow: '0 2px 8px rgba(0,0,0,0.15)'
      }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: '4px', fontSize: '12px', cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={showTrails}
            onChange={(e) => setShowTrails(e.target.checked)}
          />
          显示轨迹
        </label>
      </div>
    </>
  )
}

// 状态文本映射
function getStatusText(status) {
  const statusMap = {
    en_route: '🚛 行驶中',
    available: '✅ 可用',
    charging: '🔌 充电中',
    idle: '⏸️ 闲置'
  }
  return statusMap[status] || status
}

export default VehicleLayer
