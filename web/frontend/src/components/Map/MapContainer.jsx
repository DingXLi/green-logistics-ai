/**
 * MapContainer.jsx - 物流地图主组件
 * 
 * 功能：
 * - 瑞典地图初始化（中心：斯德哥尔摩）
 * - 缩放控件
 * - 实时刷新车辆位置
 */

import { useState, useEffect } from 'react'
import { MapContainer as LeafletMap, TileLayer, ZoomControl } from 'react-leaflet'
import LogisticsNodes from './LogisticsNodes'
import VehicleLayer from './VehicleLayer'
import RouteLayer from './RouteLayer'
import MapLegend from './Legend'
import 'leaflet/dist/leaflet.css'

const API_BASE = 'http://localhost:8000/api'

// 瑞典中心坐标
const SWEDEN_CENTER = [62.173, 16.545]
const DEFAULT_ZOOM = 5

function MapContainer({ optimizationResult }) {
  const [supplyPoints, setSupplyPoints] = useState([])
  const [demandPoints, setDemandPoints] = useState([])
  const [vehicleSnapshot, setVehicleSnapshot] = useState([])
  const [loading, setLoading] = useState(true)

  // 获取供应点数据
  const fetchSupplyPoints = async () => {
    try {
      const res = await fetch(`${API_BASE}/supply-points`)
      const data = await res.json()
      setSupplyPoints(data)
    } catch (error) {
      console.error('Failed to fetch supply points:', error)
    }
  }

  // 获取需求点数据
  const fetchDemandPoints = async () => {
    try {
      const res = await fetch(`${API_BASE}/status`)
      const data = await res.json()
      // 从 API 响应中提取需求点（如果有的话）
      if (data.demand_status) {
        setDemandPoints(data.demand_status)
      }
    } catch (error) {
      console.error('Failed to fetch demand points:', error)
    }
  }

  // 获取车队快照
  const fetchFleetSnapshot = async () => {
    try {
      const res = await fetch(`${API_BASE}/fleet-snapshot`)
      const data = await res.json()
      setVehicleSnapshot(data.vehicles || [])
    } catch (error) {
      console.error('Failed to fetch fleet snapshot:', error)
    }
  }

  // 初始加载 + 定时刷新
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

    // 每 10 秒刷新车辆位置
    const interval = setInterval(fetchFleetSnapshot, 10000)
    return () => clearInterval(interval)
  }, [])

  // 当有新的优化结果时，刷新数据
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
        {/* OSM 图层 - 免费无需 API key */}
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        
        {/* 缩放控件 - 右上角 */}
        <ZoomControl position="topright" />

        {/* 物流节点层 */}
        <LogisticsNodes 
          supplyPoints={supplyPoints} 
          demandPoints={demandPoints}
        />

        {/* 车辆位置层 */}
        <VehicleLayer vehicles={vehicleSnapshot} />

        {/* 路线可视化层 */}
        <RouteLayer optimizationResult={optimizationResult} />

        {/* 图例 */}
        <MapLegend />
      </LeafletMap>

      {loading && (
        <div className="map-loading-overlay">
          <span>🗺️ 加载地图数据...</span>
        </div>
      )}
    </div>
  )
}

export default MapContainer
