"""
OpenStreetMap 数据加载器

功能：
- 下载瑞典地区的 OSM 路网数据
- 提取物流节点（供应点、需求点、仓库）
- 计算道路距离矩阵
"""

from typing import List, Dict, Any, Tuple, Optional
from dataclasses import dataclass
import numpy as np
from loguru import logger

try:
    import osmnx as ox
    import networkx as nx
    OSMNX_AVAILABLE = True
except ImportError:
    OSMNX_AVAILABLE = False
    logger.warning("OSMnx not installed. Using fallback distance calculation.")


@dataclass
class LogisticsNode:
    """物流节点"""
    id: str
    name: str
    node_type: str  # supply, crusher, depot, demand
    lat: float
    lon: float
    capacity_tons: float = 0.0
    metadata: Dict[str, Any] = None


class OSMLoader:
    """
    OSM 数据加载器
    
    用于获取瑞典地区的路网和计算距离矩阵
    """
    
    def __init__(self, region: str = "Sweden"):
        self.region = region
        self.graph: Optional[nx.MultiDiGraph] = None
        self.nodes: List[LogisticsNode] = []
    
    def download_road_network(
        self,
        place_name: str = "Sweden",
        network_type: str = "drive"
    ) -> bool:
        """
        下载路网数据
        
        Args:
            place_name: 地区名称 (e.g., "Sweden", "Borås, Sweden")
            network_type: 路网类型 (drive, walk, bike)
        
        Returns:
            是否成功
        """
        if not OSMNX_AVAILABLE:
            logger.warning("OSMnx not available, skipping download")
            return False
        
        try:
            logger.info(f"下载 {place_name} 的路网数据...")
            self.graph = ox.graph_from_place(place_name, network_type=network_type)
            logger.info(f"路网下载完成：{len(self.graph.nodes)} 节点，{len(self.graph.edges)} 边")
            return True
        except Exception as e:
            logger.error(f"路网下载失败：{e}")
            return False
    
    def add_node(self, node: LogisticsNode):
        """添加物流节点"""
        self.nodes.append(node)
        logger.debug(f"添加节点：{node.id} ({node.node_type})")
    
    def get_nearest_node_id(self, lat: float, lon: float) -> Optional[int]:
        """获取最近的图节点 ID"""
        if self.graph is None:
            return None
        
        try:
            return ox.distance.nearest_nodes(self.graph, lon, lat)
        except Exception:
            return None
    
    def calculate_distance_matrix(self) -> np.ndarray:
        """
        计算所有节点间的距离矩阵
        
        Returns:
            距离矩阵 (km), shape: (n_nodes, n_nodes)
        """
        n = len(self.nodes)
        matrix = np.zeros((n, n))
        
        if self.graph is None and OSMNX_AVAILABLE:
            # 如果没有图，尝试下载
            self.download_road_network("Borås, Sweden")
        
        if self.graph is not None:
            # 使用实际道路距离
            for i in range(n):
                for j in range(n):
                    if i != j:
                        dist = self._get_road_distance(
                            self.nodes[i].lat, self.nodes[i].lon,
                            self.nodes[j].lat, self.nodes[j].lon
                        )
                        matrix[i, j] = dist
        else:
            # 回退到直线距离
            for i in range(n):
                for j in range(n):
                    if i != j:
                        matrix[i, j] = self._haversine_distance(
                            self.nodes[i].lat, self.nodes[i].lon,
                            self.nodes[j].lat, self.nodes[j].lon
                        )
        
        logger.info(f"距离矩阵计算完成：{n}x{n}")
        return matrix
    
    def _get_road_distance(
        self,
        lat1: float, lon1: float,
        lat2: float, lon2: float
    ) -> float:
        """获取两点间的道路距离 (km)"""
        if self.graph is None:
            return self._haversine_distance(lat1, lon1, lat2, lon2)
        
        try:
            node1 = ox.distance.nearest_nodes(self.graph, lon1, lat1)
            node2 = ox.distance.nearest_nodes(self.graph, lon2, lat2)
            
            route = nx.shortest_path(
                self.graph,
                node1, node2,
                weight='length'
            )
            
            # 计算路线总长度 (m -> km)
            distance_m = 0
            for i in range(len(route) - 1):
                u, v = route[i], route[i + 1]
                data = self.graph.get_edge_data(u, v, 0)
                if data and 'length' in data:
                    distance_m += data['length']
            
            return distance_m / 1000
        except Exception as e:
            logger.warning(f"道路距离计算失败，使用直线距离：{e}")
            return self._haversine_distance(lat1, lon1, lat2, lon2)
    
    @staticmethod
    def _haversine_distance(
        lat1: float, lon1: float,
        lat2: float, lon2: float
    ) -> float:
        """Haversine 公式计算直线距离 (km)"""
        import numpy as np
        
        R = 6371  # 地球半径 (km)
        
        lat1_rad = np.radians(lat1)
        lat2_rad = np.radians(lat2)
        delta_lat = np.radians(lat2 - lat1)
        delta_lon = np.radians(lon2 - lon1)
        
        a = np.sin(delta_lat / 2) ** 2 + \
            np.cos(lat1_rad) * np.cos(lat2_rad) * np.sin(delta_lon / 2) ** 2
        c = 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))
        
        return R * c
    
    def create_sample_nodes_sweden(self) -> List[LogisticsNode]:
        """
        创建瑞典示例物流节点
        
        包括：
        - 供应点（废料来源）
        - 破碎厂
        - 仓库
        - 需求点
        """
        nodes = [
            # 仓库 (Depot)
            LogisticsNode(
                id="DEPOT01",
                name="Borås Central Depot",
                node_type="depot",
                lat=57.7089,
                lon=14.1618,
                capacity_tons=500
            ),
            
            # 供应点 (Supply)
            LogisticsNode(
                id="SUP001",
                name="Borås Waste Center",
                node_type="supply",
                lat=57.7200,
                lon=14.1800,
                capacity_tons=50
            ),
            LogisticsNode(
                id="SUP002",
                name="Gothenburg Industrial",
                node_type="supply",
                lat=57.7089,
                lon=11.9746,
                capacity_tons=80
            ),
            LogisticsNode(
                id="SUP003",
                name="Jönköping Recycling",
                node_type="supply",
                lat=57.7826,
                lon=14.1618,
                capacity_tons=40
            ),
            
            # 破碎厂 (Crusher)
            LogisticsNode(
                id="CRUSH01",
                name="Borås Crusher Plant",
                node_type="crusher",
                lat=57.6900,
                lon=14.1400,
                capacity_tons=200
            ),
            LogisticsNode(
                id="CRUSH02",
                name="Gothenburg Processing",
                node_type="crusher",
                lat=57.6500,
                lon=11.9000,
                capacity_tons=300
            ),
            
            # 需求点 (Demand)
            LogisticsNode(
                id="DEM001",
                name="Stockholm Construction",
                node_type="demand",
                lat=59.3293,
                lon=18.0686,
                capacity_tons=150
            ),
            LogisticsNode(
                id="DEM002",
                name="Malmö Development",
                node_type="demand",
                lat=55.6050,
                lon=13.0007,
                capacity_tons=100
            )
        ]
        
        for node in nodes:
            self.add_node(node)
        
        logger.info(f"创建 {len(nodes)} 个示例节点")
        return nodes
    
    def export_nodes_geojson(self, filepath: str) -> bool:
        """导出节点为 GeoJSON 格式"""
        import json

        features = []
        for node in self.nodes:
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [node.lon, node.lat]
                },
                "properties": {
                    "id": node.id,
                    "name": node.name,
                    "type": node.node_type,
                    "capacity_tons": node.capacity_tons
                }
            })

        geojson = {
            "type": "FeatureCollection",
            "features": features
        }

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(geojson, f, indent=2, ensure_ascii=False)
            logger.info(f"节点导出完成：{filepath}")
            return True
        except Exception as e:
            logger.error(f"导出失败：{e}")
            return False


# ============================================
# V3: 真实 OSM 路网封装
# ============================================
class OSMRoadNetwork:
    """
    真实 OSM 路网封装（基于 osmnx.graph_from_point + networkx）

    用法：
        rn = OSMRoadNetwork()
        rn.load_region(57.7089, 14.1618, dist_meters=50000)
        d_km = rn.shortest_path_distance(lat1, lon1, lat2, lon2)
        D = rn.get_distance_matrix([{lat,lon}, ...])
    """

    DEFAULT_TIMEOUT = 180  # Overpass HTTP 超时 (s)

    def __init__(self):
        self.graph: Optional[nx.MultiDiGraph] = None
        self.center: Optional[Tuple[float, float]] = None
        self.dist_meters: float = 0.0
        self.network_type: str = "drive"
        self._is_projected: bool = False
        self._to_proj: Optional[Any] = None  # pyproj Transformer lat/lon → projected
        self._to_lonlat: Optional[Any] = None  # pyproj Transformer projected → lon/lat

    def load_region(
        self,
        center_lat: float,
        center_lon: float,
        dist_meters: int = 50000,
        network_type: str = "drive",
        timeout: int = DEFAULT_TIMEOUT,
        simplify: bool = True,
    ) -> Dict[str, Any]:
        """
        加载以 (center_lat, center_lon) 为中心、半径 dist_meters 的路网

        Returns:
            {"n_nodes": int, "n_edges": int, "dist_meters": int,
             "network_type": str, "center": (lat, lon)}
        """
        if not OSMNX_AVAILABLE:
            raise RuntimeError(
                "osmnx not installed — pip install osmnx>=1.9.0"
            )

        # 配置 overpass 超时
        try:
            ox.settings.timeout = timeout
        except Exception:
            try:
                ox.settings.requests_timeout = timeout
            except Exception:
                pass

        logger.info(
            f"下载 OSM 路网：center=({center_lat}, {center_lon}) "
            f"radius={dist_meters}m type={network_type} timeout={timeout}s"
        )

        self.graph = ox.graph_from_point(
            center_point=(center_lat, center_lon),
            dist=dist_meters,
            dist_type="bbox",
            network_type=network_type,
            simplify=simplify,
        )
        self.center = (center_lat, center_lon)
        self.dist_meters = dist_meters
        self.network_type = network_type

        # 投影到 UTM：让 nearest_nodes 能用 scipy k-d tree 而不是 sklearn ball-tree
        # （避免未投影图需要 scikit-learn 作为可选依赖）
        try:
            self.graph = ox.project_graph(self.graph)
            self._is_projected = True
            # 建立 lat/lon → projected 的转换器
            try:
                import pyproj
                crs = self.graph.graph.get("crs")
                # crs 可以是 EPSG 字符串或 CRS 对象
                to_proj = pyproj.Transformer.from_crs(
                    "EPSG:4326", crs, always_xy=True
                )
                to_lonlat = pyproj.Transformer.from_crs(
                    crs, "EPSG:4326", always_xy=True
                )
                self._to_proj = to_proj
                self._to_lonlat = to_lonlat
                logger.debug(f"路网已投影到 {crs}（scipy k-d tree 可用）")
            except Exception as e:
                logger.warning(f"建立 pyproj Transformer 失败：{e}")
        except Exception as e:
            logger.warning(f"投影失败（继续使用未投影图）：{e}")

        n_nodes = len(self.graph.nodes)
        n_edges = len(self.graph.edges)
        logger.info(
            f"OSM 路网加载完成：{n_nodes} nodes / {n_edges} edges "
            f"(radius={dist_meters}m)"
        )
        return {
            "n_nodes": n_nodes,
            "n_edges": n_edges,
            "dist_meters": dist_meters,
            "network_type": network_type,
            "center": (center_lat, center_lon),
        }

    def _to_graph_coords(self, lat: float, lon: float) -> Tuple[float, float]:
        """lat/lon → graph 坐标系（投影图 → 米；未投影图 → lon/lat）"""
        if self._is_projected and self._to_proj is not None:
            x, y = self._to_proj.transform(lon, lat)
            return float(x), float(y)
        return float(lon), float(lat)

    def shortest_path_distance(
        self,
        lat1: float, lon1: float,
        lat2: float, lon2: float,
    ) -> float:
        """
        两点间最短道路距离 (km)

        找不到路径时回退到 haversine 直线距离。
        """
        if self.graph is None:
            raise RuntimeError("Graph not loaded. Call load_region() first.")

        # 投影图需要 lat/lon → 米
        x1, y1 = self._to_graph_coords(lat1, lon1)
        x2, y2 = self._to_graph_coords(lat2, lon2)

        n1 = ox.distance.nearest_nodes(self.graph, x1, y1)
        n2 = ox.distance.nearest_nodes(self.graph, x2, y2)

        if n1 == n2:
            return 0.0

        try:
            dist_m = nx.shortest_path_length(
                self.graph, n1, n2, weight="length"
            )
            return float(dist_m) / 1000.0
        except nx.NetworkXNoPath:
            logger.warning(
                f"No path between OSM nodes {n1} and {n2}; "
                f"falling back to haversine"
            )
            return OSMLoader._haversine_distance(lat1, lon1, lat2, lon2)
        except Exception as e:
            logger.warning(f"shortest_path_distance failed: {e}; using haversine")
            return OSMLoader._haversine_distance(lat1, lon1, lat2, lon2)

    def get_distance_matrix(
        self,
        locations: List[Dict[str, float]],
    ) -> np.ndarray:
        """
        给定 location 列表 [{lat, lon}, ...]，返回 NxN 距离矩阵 (km)

        对角线为 0，非对角线为道路最短路径长度。
        """
        if self.graph is None:
            raise RuntimeError("Graph not loaded. Call load_region() first.")
        if not locations:
            return np.zeros((0, 0), dtype=np.float64)

        n = len(locations)
        matrix = np.zeros((n, n), dtype=np.float64)
        for i in range(n):
            for j in range(i + 1, n):
                d = self.shortest_path_distance(
                    locations[i]["lat"], locations[i]["lon"],
                    locations[j]["lat"], locations[j]["lon"],
                )
                matrix[i, j] = d
                matrix[j, i] = d  # 对称
        logger.info(f"距离矩阵计算完成：{n}x{n}")
        return matrix

    def is_loaded(self) -> bool:
        return self.graph is not None


# ============================================
# 使用示例
# ============================================
if __name__ == "__main__":
    # 创建加载器
    loader = OSMLoader(region="Sweden")
    
    # 创建示例节点
    nodes = loader.create_sample_nodes_sweden()
    
    # 计算距离矩阵
    distance_matrix = loader.calculate_distance_matrix()
    
    print("\n" + "="*60)
    print("物流节点")
    print("="*60)
    for node in nodes:
        print(f"{node.id}: {node.name} ({node.node_type})")
    
    print("\n" + "="*60)
    print("距离矩阵 (km)")
    print("="*60)
    print(distance_matrix)
    
    # 导出 GeoJSON
    loader.export_nodes_geojson("data/sweden_logistics_nodes.geojson")
