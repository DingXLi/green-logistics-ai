import { useState, useEffect } from 'react'
import './App.css'

const API_BASE = 'http://localhost:8000/api'

function App() {
  const [status, setStatus] = useState(null)
  const [fleet, setFleet] = useState(null)
  const [loading, setLoading] = useState(true)
  const [lastOptimization, setLastOptimization] = useState(null)

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 30000) // 每 30 秒刷新
    return () => clearInterval(interval)
  }, [])

  const fetchData = async () => {
    try {
      const [statusRes, fleetRes] = await Promise.all([
        fetch(`${API_BASE}/status`),
        fetch(`${API_BASE}/fleet`)
      ])
      
      const statusData = await statusRes.json()
      const fleetData = await fleetRes.json()
      
      setStatus(statusData)
      setFleet(fleetData)
      setLoading(false)
    } catch (error) {
      console.error('Failed to fetch data:', error)
      setLoading(false)
    }
  }

  const runOptimization = async () => {
    try {
      const res = await fetch(`${API_BASE}/optimize`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ run_simulation: false })
      })
      
      const data = await res.json()
      setLastOptimization(data)
      fetchData() // 刷新数据
    } catch (error) {
      console.error('Optimization failed:', error)
    }
  }

  if (loading) {
    return <div className="loading">Loading...</div>
  }

  return (
    <div className="App">
      <header className="App-header">
        <h1>🦞 Green Logistics AI</h1>
        <p>多智能体系统 - 绿色物流优化</p>
      </header>

      <main className="App-main">
        {/* 系统状态卡片 */}
        <section className="card">
          <h2>系统状态</h2>
          {status && (
            <div className="stats-grid">
              <div className="stat">
                <div className="stat-value">{status.supply_points}</div>
                <div className="stat-label">供应点</div>
              </div>
              <div className="stat">
                <div className="stat-value">{status.fleet_status?.total_vehicles || 0}</div>
                <div className="stat-label">车辆总数</div>
              </div>
              <div className="stat">
                <div className="stat-value">{status.fleet_status?.available || 0}</div>
                <div className="stat-label">可用车辆</div>
              </div>
              <div className="stat">
                <div className="stat-value">{status.demand_points || 0}</div>
                <div className="stat-label">需求点</div>
              </div>
            </div>
          )}
        </section>

        {/* 车队利用率 */}
        {fleet && (
          <section className="card">
            <h2>车队状态</h2>
            <div className="fleet-info">
              <div className="progress-bar">
                <div 
                  className="progress-fill"
                  style={{ width: `${fleet.utilization_rate}%` }}
                />
              </div>
              <div className="fleet-stats">
                <span>利用率：{fleet.utilization_rate.toFixed(1)}%</span>
                <span>行驶中：{fleet.en_route}</span>
                <span>可用：{fleet.available}</span>
              </div>
            </div>
          </section>
        )}

        {/* 优化控制 */}
        <section className="card">
          <h2>优化控制</h2>
          <button className="optimize-btn" onClick={runOptimization}>
            🚀 运行优化
          </button>
          
          {lastOptimization && (
            <div className="optimization-result">
              <h3>最近优化结果</h3>
              <p>ID: {lastOptimization.optimization_id}</p>
              <p>匹配数：{lastOptimization.matches_count}</p>
              <p>总吨位：{lastOptimization.total_tons.toFixed(2)} t</p>
              <p>总成本：{lastOptimization.total_cost_sek.toFixed(2)} SEK</p>
              <p>碳排放：{lastOptimization.total_co2_kg.toFixed(2)} kg CO₂</p>
            </div>
          )}
        </section>

        {/* 地图占位符 */}
        <section className="card map-placeholder">
          <h2>物流地图</h2>
          <div className="map-container">
            <p>🗺️ 地图组件开发中...</p>
            <p>将显示：</p>
            <ul>
              <li>供应点位置</li>
              <li>需求点位置</li>
              <li>车辆实时位置</li>
              <li>优化路线</li>
            </ul>
          </div>
        </section>
      </main>

      <footer className="App-footer">
        <p>University of Borås - Industrial Engineering and Management</p>
        <p>实习项目 © 2026</p>
      </footer>
    </div>
  )
}

export default App
