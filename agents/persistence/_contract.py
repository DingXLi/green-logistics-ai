"""跨模块共享的查询契约（实现在对应领域 mixin）。"""

from typing import Any, Dict, List, Optional


class SharedQueryMixin:
    """声明被多个领域 mixin 复用的公共查询（实现见 CycleMixin / AnalyticsMixin）。"""

    def get_cycle_history(
        self,
        limit: int = 50,
        sim_day_min: Optional[int] = None,
        sim_day_max: Optional[int] = None,
        has_matches_only: bool = False,
    ) -> List[Dict[str, Any]]:
        ...

    def get_cycle_detail(
        self,
        cycle_id: str,
        match_limit: Optional[int] = None,
        match_offset: int = 0,
        route_limit: Optional[int] = None,
        route_offset: int = 0,
    ) -> Optional[Dict[str, Any]]:
        ...

    def get_summary(self) -> Dict[str, Any]:
        ...
