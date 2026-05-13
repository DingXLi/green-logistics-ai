/**
 * MapContainer.jsx - Main Logistics Map Component
 * 
 * Features:
 * - Sweden map initialization (centered on Stockholm)
 * - Zoom controls
 * - Real-time vehicle position refresh
 */

import { useState, useEffect } from 'react'
import { MapContainer as LeafletMap, TileLayer, ZoomControl } from 'react-leaflet'
import LogisticsNodes from './LogisticsNodes'
import VehicleLayer from './VehicleLayer'
import RouteLayer from './RouteLayer'
import MapLegend from './MapLegend'
import 'leaflet/dist/leaflet.css'

const API_BASE = 'http://localhost:8000/api'

// Sweden center coordinates
const SWEDEN_CENTER = [62.173, 16.545]
const DEFAULT_ZOOM = 5

function MapContainer({ optimizationResult }) {
  const [supplyPoints, setSupplyPoints] = useState([])
  const [demandPoints, setDemandPoints] = useState([])
  const [vehicleSnapshot, setVehicleSnapshot] = useState([])
  const [loading, setLoading] = useState(true)

  const fetchSupplyPoints = async () => {
    try {
      const res = await fetch(`${API_BASE}/supply-points`)
      const data = await res.json()
      setSupplyPoints(data)
    } catch (error) {
      console.error('Failed to fetch supply points:', error)
    }
  }

  const fetchDemandPoints = async () => {
    try {
      const res = await fetch(`${API_BASE}/status`)
      const data = await res.json()
      if (data.demand_status) {
        setDemandPoints(data.demand_status)
      }
    } catch (error) {
      console.error('Failed to fetch demand points:', error)
    }
  }

  const fetchFleetSnapshot = async () => {
    try {
      const res = await fetch(`${API_BASE}/fleet-snapshot`)
      const data = await res.json()
      setVehicleSnapshot(data.vehicles || [])
    } catch (error) {
      console.error('Failed to fetch fleet snapshot:', error)
    }
  }

  useEffect(() => {
    const loadData = async () => {
      setLoading(true)
      await Promise.all([
        fetchSupplyPoints(),
        fetchDemandPoints(),
        fetchFleetSnapshot()
      ])
      setLoading(false)
    }

    loadData()

    // Refresh vehicle positions every 10 seconds
    const interval = setInterval(fetchFleetSnapshot, 10000)
    return () => clearInterval(interval)
  }, [])

  useEffect(() => {
    if (optimizationResult) {
      fetchSupplyPoints()
      fetchFleetSnapshot()
    }
  }, [optimizationResult])

  return (
    <div className="map-wrapper">
      <LeafletMap
        center={SWEDEN_CENTER}
        zoom={DEFAULT_ZOOM}
        zoomControl={false}
        style={{ height: '100%', width: '100%' }}
      >
        {/* OSM tile layer - free, no API key needed */}
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        
        {/* Zoom controls - top right */}
        <ZoomControl position="topright" />

        {/* Logistics nodes layer */}
        <LogisticsNodes 
          supplyPoints={supplyPoints} 
          demandPoints={demandPoints}
        />

        {/* Vehicle positions layer */}
        <VehicleLayer vehicles={vehicleSnapshot} />

        {/* Route visualization layer */}
        <RouteLayer optimizationResult={optimizationResult} />

        {/* Legend */}
        <MapLegend />
      </LeafletMap>

      {loading && (
        <div className="map-loading-overlay">
          <span>🗺️ Loading map data...</span>
        </div>
      )}
    </div>
  )
}

export default MapContainer
