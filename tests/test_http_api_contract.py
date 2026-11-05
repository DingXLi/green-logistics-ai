"""
HTTP API 契约测试 (iter #79) — 把「客户调用的那层接口」变成 CI 里的常驻不变量

背景
----
iter #78 (ADR-0010) 冻结了 ``agents/persistence`` 的**公开方法签名**。
但客户不调用 Python 方法 —— 客户调 **HTTP**。

``web/backend/main.py`` 现在有 136 个 HTTP 端点 + 1 个 WebSocket，
而绝大多数端点的响应是 ``Dict[str, Any]``：OpenAPI 里**根本没有** response_model，
所以「响应体字段变了」在 OpenAPI 层面完全看不见。

这个文件把两类真正会伤到客户的破坏变成机器可复现的断言：

1. **路由面变了** —— 端点被删 / 路径改名 / 状态码没了 / 参数被删或变必填
   → 客户的 curl、前端 fetch、客户自己的集成代码直接 404 / 422。
2. **鉴权面变了** —— 某个 ``/api/admin/*`` 端点不小心掉了 ``Depends(require_admin)``
   → 这不是「破坏」，这是**安全漏洞**。

分层断言（关键设计）
--------------------
- **破坏性变更 → 红**：端点被删 / 状态码被删 / 参数被删或变必填 / 参数类型变了 /
  请求体 schema 变了 / response model 换人 / 响应模型字段被删或变必填 / **鉴权被摘掉**。
- **兼容性变更 → 黄（只打印，不让 CI 变红）**：新增端点 / 新增可选参数 /
  新增状态码 / 新增响应字段 / 给端点补上鉴权 / 标记 deprecated。

理由很直接：**契约门禁一旦「什么变化都报红」，团队就会开始无脑刷基线 —— 那它就废了。**
这条是 ADR-0010 里「分层断言」思想在 HTTP 层的延续。

基线
----
``tests/http_api_contract.json`` —— 由 ``scripts/gen_http_contract.py`` **机械抽取**
（不是手写）。想故意改 HTTP 面时：
    python scripts/gen_http_contract.py --write
然后在 PR 描述里写清楚「哪些端点变了 / 为什么 / 客户端怎么改」。

跑法：
    pytest tests/test_http_api_contract.py -v
"""

import json
import os
import subprocess
import sys
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_CONTRACT_PATH = os.path.join(_REPO_ROOT, "tests", "http_api_contract.json")
_GENERATOR = os.path.join(_REPO_ROOT, "scripts", "gen_http_contract.py")

# 唯一允许「公开」的 admin 路径 —— 它本身就是「告诉别人哪些端点要 token」的发现端点，
# 保护它会让客户端无法自检是否配置了 GL_ADMIN_TOKEN（见 main.py iter #36 注释）。
_PUBLIC_ADMIN_PATHS = {"/api/admin/auth/status"}

# 基线规模下限 —— 防「有人为了让 CI 变绿而手工削掉一半基线」
_MIN_HTTP_ROUTES = 130
_MIN_PROTECTED_ROUTES = 25


class TestHttpApiContract(unittest.TestCase):
    """iter #79: HTTP API 契约冻结"""

    @classmethod
    def setUpClass(cls):
        from web.backend.main import app

        cls.app = app
        with open(_CONTRACT_PATH, encoding="utf-8") as fh:
            cls.baseline = json.load(fh)

    # ---------- 核心：HTTP 面与基线一致（只看破坏性变更） ----------

    def test_http_surface_matches_committed_baseline(self):
        """当前 HTTP 面必须与仓库里的基线快照一致（破坏性变更为空）。

        覆盖：端点增删、状态码增删、参数增删/必填/类型、请求体 schema、
        response model、响应模型字段、以及**鉴权依赖被摘掉**。
        """
        sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
        from gen_http_contract import diff_contracts, extract_contract

        current = extract_contract(self.app)
        breaking, compatible = diff_contracts(self.baseline, current)

        if breaking:
            self.fail(
                "HTTP API 出现了破坏性变更（这是一次真实的接口破坏）：\n"
                + "\n".join(breaking)
                + "\n\n如果这是**故意**的变更，请跑：\n"
                "    python scripts/gen_http_contract.py --write\n"
                "并在 PR 描述里说明：哪些端点变了 / 为什么 / 客户端怎么改。"
            )

        if compatible:
            print("\n[兼容变更（仅提示，不阻断 CI）]")
            for line in compatible:
                print(f"  {line}")

    def test_generator_reports_no_drift(self):
        """脚本自己跑一遍也必须报 OK（保证 CLI 路径和测试路径结论一致）。"""
        result = subprocess.run(
            [sys.executable, _GENERATOR],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=300,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"gen_http_contract.py 报出 HTTP 面漂移：\n{result.stdout}\n{result.stderr}",
        )
        self.assertIn("OK: HTTP API matches baseline", result.stdout)

    # ---------- 基线自身的规模守卫 ----------

    def test_baseline_covers_expected_surface(self):
        """基线必须仍然是「136 个 HTTP 端点 + 1 个 WebSocket」的规模。

        防的是：有人为了让 CI 变绿，手工把基线 JSON 削掉一半。
        契约的规模本身也是契约的一部分。
        """
        self.assertEqual(self.baseline["http_route_count"], len(self.baseline["routes"]))
        self.assertGreaterEqual(self.baseline["http_route_count"], _MIN_HTTP_ROUTES)
        self.assertEqual(
            len(self.baseline["websocket_routes"]), len(self.baseline["websocket_routes"])
        )
        self.assertGreaterEqual(len(self.baseline["websocket_routes"]), 1)

    def test_baseline_is_not_trivially_empty(self):
        """再一道保险：基线里必须有真实内容，不能是空壳。"""
        self.assertGreater(len(self.baseline["routes"]), _MIN_HTTP_ROUTES)
        self.assertGreater(len(self.baseline["models"]), 0)
        keys = {f"{r['method']} {r['path']}" for r in self.baseline["routes"]}
        for required in (
            "GET /health",
            "GET /api/dashboard-summary",
            "POST /api/optimize",
            "GET /api/fleet",
            "GET /api/admin/auth/status",
            "GET /api/persistence/export/cycles.csv",
            "GET /api/persistence/forecast",
            "GET /api/scheduler/status",
        ):
            self.assertIn(required, keys, f"基线缺少关键端点 {required}")

    # ---------- 安全不变量（这是契约测试的额外价值） ----------

    def test_every_admin_endpoint_is_protected(self):
        """``/api/admin/*`` 下除了公开的 auth/status，**必须**都挂着 require_admin。

        这条不是「契约」而是「安全不变量」：
        以后有人新加一个 admin 端点却忘了加依赖 → CI 当场变红，
        而不是等它在生产上裸奔。
        """
        unprotected = []
        for route in self.baseline["routes"]:
            if not route["path"].startswith("/api/admin/"):
                continue
            if route["path"] in _PUBLIC_ADMIN_PATHS:
                continue
            if not route["admin_protected"]:
                unprotected.append(f"{route['method']} {route['path']}")
        self.assertFalse(
            unprotected,
            f"这些 admin 端点没有鉴权依赖（安全漏洞）：{unprotected}",
        )

    def test_protected_route_count_is_plausible(self):
        """受保护端点数量下限 —— 防「基线里的 admin_protected 全被改成 false」。"""
        protected = [r for r in self.baseline["routes"] if r["admin_protected"]]
        self.assertGreaterEqual(
            len(protected),
            _MIN_PROTECTED_ROUTES,
            f"受保护端点只剩 {len(protected)} 个 —— 基线是不是被削过了？",
        )

    def test_protected_endpoint_discovery_matches_actual_dependencies(self):
        """``/api/admin/auth/status`` 里公布的清单，必须等于**实际**受保护的端点。

        iter #79 的起因：iter #36 那份手写清单（19 条）已经和真实依赖漂了
        （实际 29 个受保护路由，清单里漏了 4 个路径）—— 漏报的是
        ``/api/admin/persistence/cache*``、``/api/admin/runtime-config/reset``、
        ``/api/admin/simulate/batch*``。

        这条断言把「清单」和「行为」绑死：清单现在是**从路由表推导**出来的，
        所以结构上不可能再漂。
        """
        from web.backend.main import _get_protected_endpoints

        advertised = set(_get_protected_endpoints())
        actual = {r["path"] for r in self.baseline["routes"] if r["admin_protected"]}

        missing = actual - advertised
        self.assertFalse(
            missing,
            f"受保护但没公布的端点（客户端/审计会看漏）：{sorted(missing)}",
        )

        phantom = advertised - actual
        self.assertFalse(
            phantom,
            f"公布了但实际没保护的端点（清单在说谎）：{sorted(phantom)}",
        )

    # ---------- 结构守卫 ----------

    def test_no_duplicate_method_path(self):
        """同一个 (method, path) 不能出现两次 —— 出现即路由表有歧义。"""
        seen = {}
        for route in self.baseline["routes"]:
            key = (route["method"], route["path"])
            self.assertNotIn(
                key, seen,
                f"重复路由：{key[0]} {key[1]}（FastAPI 会用先注册的那个，后面的永远不生效）",
            )
            seen[key] = True

    def test_every_route_declares_at_least_one_success_status(self):
        """每个端点都必须声明至少一个成功状态码（200/201/204）。"""
        for route in self.baseline["routes"]:
            success = {"200", "201", "202", "204"}
            self.assertTrue(
                success & set(route["status_codes"]),
                f"{route['method']} {route['path']} 没有成功状态码：{route['status_codes']}",
            )

    def test_websocket_routes_are_frozen(self):
        """WebSocket 路由也要冻结 —— 它是前端实时更新的通道，改名一样会断。"""
        from fastapi.routing import APIWebSocketRoute

        actual = sorted(
            str(r.path) for r in self.app.routes
            if isinstance(r, APIWebSocketRoute)
        )
        self.assertEqual(
            actual,
            sorted(self.baseline["websocket_routes"]),
            "WebSocket 路由变了（前端实时通道会断）",
        )
        self.assertIn("/ws/cycle-updates", actual)

    def test_response_models_have_stable_fields(self):
        """具名响应模型的字段必须冻结（字段被删 = 客户端拿不到值了）。"""
        models = self.baseline["models"]
        self.assertIn("FleetStatusResponse", models)
        fields = models["FleetStatusResponse"]["fields"]
        for name in ("total_vehicles", "available", "utilization_rate"):
            self.assertIn(name, fields, f"FleetStatusResponse 缺少字段 {name}")
        self.assertEqual(models["FleetStatusResponse"]["field_count"], len(fields))

    def test_admin_status_endpoint_is_the_only_public_admin_path(self):
        """公开的 admin 路径清单本身也要被守卫（防止有人往里加东西）。"""
        public_admin = {
            r["path"] for r in self.baseline["routes"]
            if r["path"].startswith("/api/admin/") and not r["admin_protected"]
        }
        self.assertEqual(
            public_admin,
            _PUBLIC_ADMIN_PATHS,
            "公开的 admin 路径集合变了 —— 新增公开 admin 端点必须走安全评审",
        )


if __name__ == "__main__":
    unittest.main()
