/**
 * RouteLayer.jsx - Optimization Route Visualization Layer
 * 
 * Features:
 * - Display routes from pickup to delivery points
 * - Color by objective (profit/carbon/cost)
 * - Animated direction indicators
 */

import { Polyline, Popup } from 'react-leaflet'
import { useMemo } from 'react'

const OBJECTIVE_COLORS = {
  profit: '#22c55e',
  carbon: '#3b82f6',
  cost: '#f97316',
  balanced: '#8b5cf6'
}

const createRouteStyle = (objective) => ({
  color: OBJECTIVE_COLORS[objective] || OBJECTIVE_COLORS.balanced,
  weight: 4,
  opacity: 0.8,
  dashArray: objective === 'carbon' ? '10, 10' : null
})

function RouteLayer({ optimizationResult }) {
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

  const routePolylines = useMemo(() => {
    return routes.map((route) => {
      if (!route.waypoints || route.waypoints.length < 2) return null

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
                    <td><strong>Distance:</strong></td>
                    <td>{route.totalDistance.toFixed(1)} km</td>
                  </tr>
                  <tr>
                    <td><strong>Cost:</strong></td>
                    <td>{route.totalCost.toFixed(0)} SEK</td>
                  </tr>
                  <tr>
                    <td><strong>CO₂:</strong></td>
                    <td>{route.totalCO2.toFixed(1)} kg</td>
                  </tr>
                  <tr>
                    <td><strong>Load:</strong></td>
                    <td>{route.load.toFixed(1)} tons</td>
                  </tr>
                  <tr>
                    <td><strong>Stops:</strong></td>
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

  if (routes.length === 0) {
    return null
  }

  return <>{routePolylines}</>
}

export default RouteLayer
