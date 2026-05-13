/**
 * LogisticsNodes.jsx - Logistics Node Markers
 * 
 * Displays:
 * - Supply points (green - waste sources)
 * - Demand points (blue - recycling plants/buyers)
 * - Depots/Warehouses (yellow)
 */

import { Marker, Popup } from 'react-leaflet'
import L from 'leaflet'
import { useMemo } from 'react'

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

const ICONS = {
  supply: createIcon('#22c55e', '📦'),
  demand: createIcon('#3b82f6', '🏭'),
  depot: createIcon('#eab308', '🏬'),
  crusher: createIcon('#f97316', '⚙️')
}

function LogisticsNodes({ supplyPoints = [], demandPoints = [] }) {
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
              📦 Supply Point {point.agent_id}
            </h4>
            <table style={{ fontSize: '12px', width: '100%' }}>
              <tbody>
                <tr>
                  <td><strong>Stock:</strong></td>
                  <td>{point.stock_tons.toFixed(1)} tons</td>
                </tr>
                <tr>
                  <td><strong>Material:</strong></td>
                  <td>{point.material_type}</td>
                </tr>
                <tr>
                  <td><strong>Location:</strong></td>
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
              🏭 Demand Point {point.id || `DEM${index + 1}`}
            </h4>
            <table style={{ fontSize: '12px', width: '100%' }}>
              <tbody>
                <tr>
                  <td><strong>Demand:</strong></td>
                  <td>{point.current_demand_tons?.toFixed(1) || point.demand_tons?.toFixed(1) || 'N/A'} tons</td>
                </tr>
                <tr>
                  <td><strong>Materials:</strong></td>
                  <td>{point.preferred_materials?.join(', ') || 'Mixed'}</td>
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
