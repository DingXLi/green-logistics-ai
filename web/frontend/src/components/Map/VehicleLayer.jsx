/**
 * VehicleLayer.jsx - Real-time Vehicle Position Layer
 * 
 * Features:
 * - Display fleet current positions
 * - Color by status (en_route/available/charging/idle)
 * - Click for details
 * - Trail history (optional)
 */

import { Marker, Popup, Polyline } from 'react-leaflet'
import L from 'leaflet'
import { useMemo, useState } from 'react'

const STATUS_COLORS = {
  en_route: '#3b82f6',
  available: '#22c55e',
  charging: '#eab308',
  idle: '#9ca3af'
}

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
  const [showTrails, setShowTrails] = useState(false)

  const vehicleMarkers = useMemo(() => {
    return vehicles.map((vehicle) => {
      if (!vehicle.latitude || !vehicle.longitude) return null
      
      return (
        <Marker
          key={vehicle.vehicle_id}
          position={[vehicle.latitude, vehicle.longitude]}
          icon={createVehicleIcon(vehicle.status, vehicle.heading)}
        >
          <Popup>
            <div style={{ minWidth: '200px' }}>
              <h4 style={{ margin: '0 0 8px 0', color: '#2563eb' }}>
                🚛 {vehicle.vehicle_id}
              </h4>
              <table style={{ fontSize: '11px', width: '100%' }}>
                <tbody>
                  <tr>
                    <td><strong>Status:</strong></td>
                    <td style={{ color: STATUS_COLORS[vehicle.status] }}>
                      {getStatusText(vehicle.status)}
                    </td>
                  </tr>
                  <tr>
                    <td><strong>Load:</strong></td>
                    <td>{vehicle.cargo_load?.toFixed(1) || 0} tons</td>
                  </tr>
                  <tr>
                    <td><strong>Battery:</strong></td>
                    <td>{vehicle.battery_level || 100}%</td>
                  </tr>
                  <tr>
                    <td><strong>Speed:</strong></td>
                    <td>{vehicle.speed || 0} km/h</td>
                  </tr>
                  <tr>
                    <td><strong>CO₂ Rate:</strong></td>
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
          Show Trails
        </label>
      </div>
    </>
  )
}

function getStatusText(status) {
  const statusMap = {
    en_route: '🚛 En Route',
    available: '✅ Available',
    charging: '🔌 Charging',
    idle: '⏸️ Idle'
  }
  return statusMap[status] || status
}

export default VehicleLayer
