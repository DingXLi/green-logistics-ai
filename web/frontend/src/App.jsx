import { useState, useEffect } from 'react'
import { MapContainer } from './components/Map'
import './App.css'

const API_BASE = 'http://localhost:8000/api'

function App() {
  const [status, setStatus] = useState(null)
  const [fleet, setFleet] = useState(null)
  const [loading, setLoading] = useState(true)
  const [lastOptimization, setLastOptimization] = useState(null)
  const [activeTab, setActiveTab] = useState('map') // 'map' | 'dashboard'

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 30000)
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
      fetchData()
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
        
        {/* 标签页切换 */}
        <nav className="tab-nav">
          <button 
            className={`tab-btn ${activeTab === 'map' ? 'active' : ''}`}
            onClick={() => setActiveTab('map')}
          >
            🗺️ 地图
          </button>
          <button 
            className={`tab-btn ${activeTab === 'dashboard' ? 'active' : ''}`}
            onClick={() => setActiveTab('dashboard')}
          >
            📊 仪表盘
          </button>
        </nav>
      </header>

      <main className="App-main">
        {activeTab === 'map' ? (
          <>
            {/* 地图视图 */}
            <section className="card map-card">
              <div className="map-container">
                <MapContainer optimizationResult={lastOptimization} />
              </div>
            </section>

            {/* 地图下方状态栏 */}
            <section className="card status-bar">
              <div className="stats-grid">
                <div className="stat">
                  <div className="stat-value">{status?.supply_points || 0}</div>
                  <div className="stat-label">供应点</div>
                </div>
                <div className="stat">
                  <div className="stat-value">{status?.demand_points || 0}</div>
                  <div className="stat-label">需求点</div>
                </div>
                <div className="stat">
                  <div className="stat-value">{fleet?.total_vehicles || 0}</div>
                  <div className="stat-label">车辆</div>
                </div>
                <div className="stat">
                  <div className="stat-value">{fleet?.utilization_rate?.toFixed(0) || 0}%</div>
                  <div className="stat-label">利用率</div>
                </div>
              </div>
            </section>
          </>
        ) : (
          <>
            {/* 传统仪表盘视图 */}
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
          </>
        )}

        {/* 优化控制 - 两个视图都显示 */}
        <section className="card">
          <h2>🚀 优化控制</h2>
          <button className="optimize-btn" onClick={runOptimization}>
            运行优化
          </button>
          
          {lastOptimization && (
            <div className="optimization-result">
              <h3>最近优化结果</h3>
              <div className="result-grid">
                <div className="result-item">
                  <span className="result-label">ID</span>
                  <span className="result-value">{lastOptimization.optimization_id}</span>
                </div>
                <div className="result-item">
                  <span className="result-label">匹配数</span>
                  <span className="result-value">{lastOptimization.matches_count}</span>
                </div>
                <div className="result-item">
                  <span className="result-label">总吨位</span>
                  <span className="result-value">{lastOptimization.total_tons.toFixed(2)} t</span>
                </div>
                <div className="result-item">
                  <span className="result-label">总成本</span>
                  <span className="result-value">{lastOptimization.total_cost_sek.toFixed(2)} SEK</span>
                </div>
                <div className="result-item">
                  <span className="result-label">碳排放</span>
                  <span className="result-value">{lastOptimization.total_co2_kg.toFixed(2)} kg CO₂</span>
                </div>
              </div>
            </div>
          )}
        </section>
      </main>

      <footer className="App-footer">
        <p>Green Logistics AI © 2026</p>
      </footer>
    </div>
  )
}

export default App
