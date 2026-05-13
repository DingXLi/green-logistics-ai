/**
 * LogisticsNodes.jsx - 物流节点标记
 * 
 * 显示：
 * - 供应点（绿色 - 废料来源）
 * - 需求点（蓝色 - 回收厂/买家）
 * - 仓库/中转站（黄色）
 */

import { Marker, Popup } from 'react-leaflet'
import L from 'leaflet'
import { useMemo } from 'react'

// 自定义图标创建函数
const createIcon = (color, symbol) => {
  return L.divIcon({
    className: 'custom-marker',
    html: `
      <div style="
        background-color: ${color};
        width: 32px;
        height: 32px;
        border-radius: 50%;
        border: 3px solid white;
        box-shadow: 0 2px 8px rgba(0,0,0,0.3);
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 16px;
      ">
        ${symbol}
      </div>
    `,
    iconSize: [32, 32],
    iconAnchor: [16, 32],
    popupAnchor: [0, -32]
  })
}

// 节点类型对应的图标
const ICONS = {
  supply: createIcon('#22c55e', '📦'),    // 绿色 - 供应点
  demand: createIcon('#3b82f6', '🏭'),    // 蓝色 - 需求点
  depot: createIcon('#eab308', '🏬'),    // 黄色 - 仓库
  crusher: createIcon('#f97316', '⚙️')     // 橙色 - 破碎厂
}

function LogisticsNodes({ supplyPoints = [], demandPoints = [] }) {
  // 渲染供应点标记
  const supplyMarkers = useMemo(() => {
    return supplyPoints.map((point) => (
      <Marker
        key={`supply-${point.agent_id}`}
        position={[point.location.lat, point.location.lon]}
        icon={ICONS.supply}
      >
        <Popup>
          <div style={{ minWidth: '180px' }}>
            <h4 style={{ margin: '0 0 8px 0', color: '#22c55e' }}>
              📦 供应点 {point.agent_id}
            </h4>
            <table style={{ fontSize: '12px', width: '100%' }}>
              <tbody>
                <tr>
                  <td><strong>库存:</strong></td>
                  <td>{point.stock_tons.toFixed(1)} 吨</td>
                </tr>
                <tr>
                  <td><strong>物料:</strong></td>
                  <td>{point.material_type}</td>
                </tr>
                <tr>
                  <td><strong>位置:</strong></td>
                  <td>
                    {point.location.lat.toFixed(4)}, 
                    {point.location.lon.toFixed(4)}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </Popup>
      </Marker>
    ))
  }, [supplyPoints])

  // 渲染需求点标记
  const demandMarkers = useMemo(() => {
    return demandPoints.map((point, index) => (
      <Marker
        key={`demand-${point.id || index}`}
        position={[point.location.lat, point.location.lon]}
        icon={ICONS.demand}
      >
        <Popup>
          <div style={{ minWidth: '180px' }}>
            <h4 style={{ margin: '0 0 8px 0', color: '#3b82f6' }}>
              🏭 需求点 {point.id || `DEM${index + 1}`}
            </h4>
            <table style={{ fontSize: '12px', width: '100%' }}>
              <tbody>
                <tr>
                  <td><strong>需求:</strong></td>
                  <td>{point.current_demand_tons?.toFixed(1) || point.demand_tons?.toFixed(1) || 'N/A'} 吨</td>
                </tr>
                <tr>
                  <td><strong>物料:</strong></td>
                  <td>{point.preferred_materials?.join(', ') || '混合'}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </Popup>
      </Marker>
    ))
  }, [demandPoints])

  return (
    <>
      {supplyMarkers}
      {demandMarkers}
    </>
  )
}

export default LogisticsNodes
