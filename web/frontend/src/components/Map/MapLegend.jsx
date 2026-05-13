/**
 * MapLegend.jsx - Map Legend Component
 * 
 * Displays:
 * - Node type legend
 * - Vehicle status legend
 * - Route color legend
 */

function MapLegend() {
  const legendStyle = {
    position: 'absolute',
    bottom: '30px',
    right: '10px',
    background: 'white',
    padding: '12px',
    borderRadius: '8px',
    boxShadow: '0 2px 12px rgba(0,0,0,0.15)',
    zIndex: 1000,
    fontSize: '11px',
    minWidth: '140px'
  }

  const sectionStyle = {
    marginBottom: '10px'
  }

  const sectionTitleStyle = {
    fontWeight: 'bold',
    marginBottom: '6px',
    color: '#374151',
    borderBottom: '1px solid #e5e7eb',
    paddingBottom: '4px'
  }

  const itemStyle = {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    marginBottom: '4px'
  }

  const colorBox = (color) => ({
    width: '16px',
    height: '16px',
    borderRadius: '50%',
    backgroundColor: color,
    border: '2px solid white',
    boxShadow: '0 1px 3px rgba(0,0,0,0.2)'
  })

  const vehicleIcon = (color) => ({
    width: '20px',
    height: '20px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center'
  })

  return (
    <div style={legendStyle} className="map-legend">
      <div style={sectionStyle}>
        <div style={sectionTitleStyle}>📍 Logistics Nodes</div>
        <div style={itemStyle}>
          <div style={colorBox('#22c55e')}></div>
          <span>Supply Points</span>
        </div>
        <div style={itemStyle}>
          <div style={colorBox('#3b82f6')}></div>
          <span>Demand Points</span>
        </div>
        <div style={itemStyle}>
          <div style={colorBox('#eab308')}></div>
          <span>Depots</span>
        </div>
        <div style={itemStyle}>
          <div style={colorBox('#f97316')}></div>
          <span>Crushers</span>
        </div>
      </div>

      <div style={sectionStyle}>
        <div style={sectionTitleStyle}>🚛 Vehicle Status</div>
        <div style={itemStyle}>
          <svg style={vehicleIcon('#3b82f6')} viewBox="0 0 24 24" fill="#3b82f6">
            <path d="M12 2L4 12l3 2v6h10v-6l3-2L12 2z"/>
          </svg>
          <span>En Route</span>
        </div>
        <div style={itemStyle}>
          <svg style={vehicleIcon('#22c55e')} viewBox="0 0 24 24" fill="#22c55e">
            <path d="M12 2L4 12l3 2v6h10v-6l3-2L12 2z"/>
          </svg>
          <span>Available</span>
        </div>
        <div style={itemStyle}>
          <svg style={vehicleIcon('#eab308')} viewBox="0 0 24 24" fill="#eab308">
            <path d="M12 2L4 12l3 2v6h10v-6l3-2L12 2z"/>
          </svg>
          <span>Charging</span>
        </div>
        <div style={itemStyle}>
          <svg style={vehicleIcon('#9ca3af')} viewBox="0 0 24 24" fill="#9ca3af">
            <path d="M12 2L4 12l3 2v6h10v-6l3-2L12 2z"/>
          </svg>
          <span>Idle</span>
        </div>
      </div>

      <div style={{ ...sectionStyle, marginBottom: 0 }}>
        <div style={sectionTitleStyle}>📊 Optimization Objective</div>
        <div style={itemStyle}>
          <div style={{
            width: '24px',
            height: '3px',
            backgroundColor: '#22c55e',
            borderRadius: '2px'
          }}></div>
          <span>Profit First</span>
        </div>
        <div style={itemStyle}>
          <div style={{
            width: '24px',
            height: '3px',
            backgroundColor: '#3b82f6',
            borderRadius: '2px'
          }}></div>
          <span>Carbon First</span>
        </div>
        <div style={itemStyle}>
          <div style={{
            width: '24px',
            height: '3px',
            backgroundColor: '#f97316',
            borderRadius: '2px'
          }}></div>
          <span>Cost First</span>
        </div>
        <div style={itemStyle}>
          <div style={{
            width: '24px',
            height: '3px',
            backgroundColor: '#8b5cf6',
            borderRadius: '2px'
          }}></div>
          <span>Balanced</span>
        </div>
      </div>
    </div>
  )
}

export default MapLegend
