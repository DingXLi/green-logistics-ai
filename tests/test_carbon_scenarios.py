"""
Tests for carbon price scenarios endpoint / API logic.

These tests avoid spinning up the full FastAPI app (which requires database,
coordinator init, world bootstrap, etc.) and instead test the pure
helper logic + schema validation directly.

iter #86 (ADR-0018): 端点测试改成**注入 fake coordinator**(而不是依赖
ambient 全局 coordinator), 于是状态码是确定的 —— 断言可以精确到唯一结果,
也**不可能**因为「结果不是 200」而整段跳过断言。
"""

from fastapi.testclient import TestClient

from fake_world import build_fake_coordinator


def _client_with_fake_coordinator(monkeypatch):
    """注入 fake coordinator, 返回一个 TestClient。

    刻意**不**用 ``with TestClient(app)``: 走 lifespan 会触发 startup_event,
    它里面有 ``global coordinator; coordinator = MultiAgentCoordinator(...)``
    → 会把我们注入的 fake **整个换掉**(ADR-0017 记过这个坑)。
    """
    from web.backend import main as backend_main

    monkeypatch.setattr(backend_main, "coordinator", build_fake_coordinator())
    return TestClient(backend_main.app)


def test_carbon_scenarios_endpoint_validates_carbon_prices(monkeypatch):
    """无效的 carbon_prices 字符串应该返回 400。"""
    client = _client_with_fake_coordinator(monkeypatch)
    try:
        # 错误格式
        r = client.get("/api/optimize/carbon-scenarios?carbon_prices=abc,xyz")
        assert r.status_code == 400

        # 太多价格
        r = client.get("/api/optimize/carbon-scenarios?carbon_prices=1,2,3,4,5,6,7,8,9")
        assert r.status_code == 400

        # 空字符串走默认 fallback (返回 200 + 4 默认场景)
        # iter #74: 带 time_limit_seconds=1 + use_real_roads=false —— 这个用例只验证
        # 「参数为空 → 默认 4 场景」, 不需要真实路网距离矩阵（OSM 矩阵占掉 ~70s,
        # 会撞上 CI 的 --timeout=60 而假失败）
        r = client.get(
            "/api/optimize/carbon-scenarios?carbon_prices="
            "&time_limit_seconds=1&use_real_roads=false"
        )
        assert r.status_code == 200
        assert len(r.json()["scenarios"]) == 4
    finally:
        client.close()


def test_carbon_scenarios_returns_expected_schema(monkeypatch):
    """返回结构必须包含 n_scenarios 和每 scenario 的 cost/co2 optimal。

    iter #86 (ADR-0018): 原来写的是 ``assert r.status_code in (200, 503)`` ——
    「有 matches」和「coordinator 未启」都算过, 端点整个 500 了测试也一样绿。
    现在自己注入 fake coordinator → 端点**确定**返回 200 → 断言锁死。
    """
    client = _client_with_fake_coordinator(monkeypatch)
    try:
        # 2 个价格 + time_limit=1s + 不走真实路网 → 快且确定
        r = client.get(
            "/api/optimize/carbon-scenarios"
            "?carbon_prices=0,1.5&time_limit_seconds=1&use_real_roads=false"
        )
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data["scenarios"], list)
        assert len(data["scenarios"]) == 2
        for s in data["scenarios"]:
            assert "carbon_price_sek_per_kg" in s
            assert "cost_optimal" in s
            assert "co2_optimal" in s
            # cost-optimal 应该是 cost_weight=1 (纯成本)
            # co2-optimal 应该是 co2_weight=1 (纯碳)
            assert s["cost_optimal"] is not None
            assert s["co2_optimal"] is not None
            assert s["cost_optimal"]["cost_sek"] is not None or \
                   s["cost_optimal"]["n_routes"] == 0
            # 默认 4 pareto points
            assert len(s["pareto"]) == 4
    finally:
        client.close()


def test_carbon_scenarios_without_coordinator_returns_503(monkeypatch):
    """没有 coordinator → **精确** 503(旧断言里那半个「503」分支, 现在被真正证明了)。"""
    from web.backend import main as backend_main

    monkeypatch.setattr(backend_main, "coordinator", None)
    client = TestClient(backend_main.app)
    try:
        r = client.get(
            "/api/optimize/carbon-scenarios"
            "?carbon_prices=0,1.5&time_limit_seconds=1&use_real_roads=false"
        )
        assert r.status_code == 503
        assert "not initialized" in r.json()["detail"].lower()
    finally:
        client.close()


def test_carbon_scenarios_default_prices(monkeypatch):
    """不传 carbon_prices 应该用 4 个默认场景 [0, 1.5, 3, 5]。"""
    client = _client_with_fake_coordinator(monkeypatch)
    try:
        r = client.get(
            "/api/optimize/carbon-scenarios"
            "?time_limit_seconds=1&use_real_roads=false"
        )
        assert r.status_code == 200
        data = r.json()
        prices = [s["carbon_price_sek_per_kg"] for s in data["scenarios"]]
        assert prices == [0.0, 1.5, 3.0, 5.0]
    finally:
        client.close()


def test_carbon_scenarios_zero_price_still_optimizes(monkeypatch):
    """碳价=0 时也应该能算（CO2-optimal 还是会有解，只是 cost 可能很高）。"""
    client = _client_with_fake_coordinator(monkeypatch)
    try:
        r = client.get(
            "/api/optimize/carbon-scenarios"
            "?carbon_prices=0&time_limit_seconds=1&use_real_roads=false"
        )
        assert r.status_code == 200
        data = r.json()
        assert len(data["scenarios"]) == 1
        assert data["scenarios"][0]["carbon_price_sek_per_kg"] == 0.0
    finally:
        client.close()
