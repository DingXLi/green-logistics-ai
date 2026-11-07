"""
HTTP 响应体 payload 形状快照测试 (iter #80) — 把「客户真正拿到的那坨 JSON」冻住

背景
----
iter #78 (ADR-0010) 冻结了 ``agents/persistence`` 的**公开方法签名**。
iter #79 (ADR-0011) 冻结了 **HTTP 面**: 路径 / 参数 / 状态码 / response_model / 鉴权依赖。

但 ADR-0011 自己写下的**已知边界**是:

    「136 个端点里绝大多数响应是 ``Dict[str, Any]``,
      OpenAPI 里没有 response_model —— 响应体字段在 schema 层面根本看不见。」

所以还差最后一层: **响应体在运行时到底长什么样**。

这个层面对客户是**最要命**的一层, 因为下面这些事故 schema 层全部看不见:

1. **字段变 null** —— schema 说 integer, 运行时某天起变成 None,
   客户 JS 端 ``data.x.toFixed(0)`` 直接 TypeError。
2. **类型悄悄换了** —— ``total_cost_sek`` 从 float 变成 str(为了塞货币符号),
   客户的算术全错, 而且**不报错**(静默算错比报错更贵)。
3. **字段整个没了** —— 重构时把 ``co2_saved`` 删了, 客户报表那一列静默变空白。
4. **多出字段** —— 通常兼容, 但有时会泄漏内部信息(绝对路径 / 内部 id / SQL)。

分层断言(和 ADR-0010 / ADR-0011 一脉相承)
-----------------------------------------
- **破坏性 → 红**: 端点没了 / 状态码变了 / **字段被删** /
  **类型集合被加宽**(原来是 integer, 现在可能是 null) / 类型被替换。
- **兼容 → 黄(只打印)**: 新增字段 / 新增端点 / **类型集合被收窄**。
- ``unknown``(空数组没有样本) 相关的一切变化 → 黄。

理由和 ADR-0011 一模一样: **门禁一旦「什么变化都报红」, 团队就会开始无脑刷基线
—— 那它就废了。**

基线
----
``tests/http_api_payload_shapes.json`` —— 由 ``scripts/gen_payload_shapes.py``
**真起一次服务、真实调一遍端点**机械生成(不是手写)。
故意改响应体结构时:
    python scripts/gen_payload_shapes.py --write
然后在 PR 描述里写清楚: 哪些端点的响应变了 / 客户端怎么改。

跑法:
    pytest tests/test_payload_shapes.py -v
"""

import json
import os
import subprocess
import sys
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_BASELINE_PATH = os.path.join(_REPO_ROOT, "tests", "http_api_payload_shapes.json")
_GENERATOR = os.path.join(_REPO_ROOT, "scripts", "gen_payload_shapes.py")

# 基线规模下限 —— 防「有人为了让 CI 变绿而手工削掉一半基线」
# iter #81: 端点清单改成**自动发现**(见 ADR-0013), 基线从 45 个涨到 100 个。
_MIN_ENDPOINTS = 90
_MIN_FIELDS = 400

# 快照采集是子进程 (要独占 GL_DB_PATH + 独占 app import),
# 整个测试类共用一次采集结果, 避免每个用例都付 ~4s 的启动成本
_SNAPSHOT_CACHE = {}


def _collect_snapshot() -> dict:
    """跑一次生成器, 拿当前运行时快照(带缓存)。"""
    if "snapshot" not in _SNAPSHOT_CACHE:
        result = subprocess.run(
            [sys.executable, _GENERATOR, "--json"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            raise AssertionError(
                f"gen_payload_shapes.py --json 执行失败:\n{result.stdout}\n{result.stderr}"
            )
        _SNAPSHOT_CACHE["snapshot"] = json.loads(result.stdout)
    return _SNAPSHOT_CACHE["snapshot"]


def _load_baseline() -> dict:
    with open(_BASELINE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _count_fields(node: dict) -> int:
    """数一个形状节点里有多少个字段(递归)。"""
    total = len(node.get("fields") or {})
    for child in (node.get("fields") or {}).values():
        total += _count_fields(child)
    if node.get("items"):
        total += _count_fields(node["items"])
    return total


def _find_null_fields(node: dict, path: str, out: list) -> None:
    """找出所有「类型集合里含 null」的字段路径 —— schema 看不见的那一面。"""
    if "null" in (node.get("types") or []):
        out.append(path)
    for key, child in (node.get("fields") or {}).items():
        _find_null_fields(child, f"{path}.{key}", out)
    if node.get("items"):
        _find_null_fields(node["items"], f"{path}[]", out)


class TestPayloadShapesContract(unittest.TestCase):
    """iter #80: 运行时响应体形状冻结"""

    @classmethod
    def setUpClass(cls):
        cls.baseline = _load_baseline()

    # ---------- 核心：运行时形状与基线一致 ----------

    def test_runtime_payload_shapes_match_baseline(self):
        """当前运行时响应体形状必须与仓库基线一致(无破坏性变更)。

        这是本文件的主断言: 端点少一个 / 状态码变了 / 字段被删 /
        类型被加宽(原来是 integer, 现在可能是 null) —— 任何一条都会红。
        """
        sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
        from gen_payload_shapes import diff_shapes

        breaking, compatible = diff_shapes(self.baseline, _collect_snapshot())

        if breaking:
            self.fail(
                "运行时响应体形状出现了破坏性变更(这是一次真实的接口破坏):\n"
                + "\n".join(breaking)
                + "\n\n如果这是**故意**的变更，请跑：\n"
                "    python scripts/gen_payload_shapes.py --write\n"
                "并在 PR 描述里说明: 哪些端点的响应变了 / 客户端怎么改。"
            )

        if compatible:
            print("\n[兼容变更（仅提示，不阻断 CI）]")
            for line in compatible:
                print(f"  {line}")

    def test_generator_cli_reports_no_drift(self):
        """CLI 路径也必须报 OK(保证脚本路径和测试路径结论一致)。"""
        result = subprocess.run(
            [sys.executable, _GENERATOR],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=600,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"gen_payload_shapes.py 报出响应体形状漂移:\n{result.stdout}\n{result.stderr}",
        )
        self.assertIn("OK: 运行时响应体形状与基线一致", result.stdout)

    def test_snapshot_is_deterministic(self):
        """同一份代码跑两次, 快照必须**字节级一致**。

        这条比看起来更重要: 快照门禁如果不可复现, 它就只是一条随机噪音的门禁 ——
        团队会很快学会无视它, 于是真破坏也会被一起无视。
        (确定性来自: 固定 fixture DB + 固定 seed 的世界引导 + 只记结构不记值)
        """
        first = subprocess.run(
            [sys.executable, _GENERATOR, "--json"],
            cwd=_REPO_ROOT, capture_output=True, text=True, timeout=600,
        )
        second = subprocess.run(
            [sys.executable, _GENERATOR, "--json"],
            cwd=_REPO_ROOT, capture_output=True, text=True, timeout=600,
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(
            first.stdout,
            second.stdout,
            "两次采集的快照不一致 —— 快照不可复现, 这个门禁就是噪音",
        )

    # ---------- 基线自身的规模守卫 ----------

    def test_baseline_covers_expected_endpoints(self):
        """基线必须覆盖主要端点面, 且规模不能被削。"""
        endpoints = self.baseline["endpoints"]
        self.assertGreaterEqual(len(endpoints), _MIN_ENDPOINTS)
        keys = set(endpoints)
        for required in (
            "GET /health",
            "GET /api/dashboard-summary",
            "GET /api/fleet",
            "GET /api/persistence/summary",
            "GET /api/persistence/forecast",
            "GET /api/persistence/cycle-history",
            "GET /api/admin/auth/status",
            "GET /api/admin/db-stats",
        ):
            self.assertIn(required, keys, f"基线缺少关键端点 {required}")

    def test_baseline_covers_majority_of_get_routes(self):
        """iter #81: 基线必须覆盖**大部分** GET 端点。

        手写清单时代这个比例是 45/119 = 38% —— 门禁只管了不到四成,
        剩下的端点随便改都不会有人知道。现在自动发现把它拉到 100/119 = 84%。
        这个断言防的是「有人把清单改回手写小集合」。
        """
        coverage = self.baseline.get("coverage")
        self.assertIsNotNone(coverage, "基线里没有 coverage 段 (ADR-0013)")
        total = coverage["total_get_routes"]
        covered = coverage["covered"]
        self.assertGreaterEqual(
            covered / max(1, total),
            0.75,
            f"覆盖率只有 {covered}/{total} —— 是不是退回手写清单了?",
        )

    def test_baseline_is_not_trivially_empty(self):
        """基线必须有真实内容(字段数下限), 不能是空壳。"""
        endpoints = self.baseline["endpoints"]
        total_fields = sum(_count_fields(ep["shape"]) for ep in endpoints.values())
        self.assertGreaterEqual(
            total_fields,
            _MIN_FIELDS,
            f"基线只有 {total_fields} 个字段 —— 是不是被削过了?",
        )
        for key, ep in endpoints.items():
            self.assertTrue(ep["shape"].get("types"), f"{key} 的形状是空的")

    def test_every_collected_endpoint_returns_200(self):
        """快照里的每个端点都必须真的返回 200。

        端点挂了(500)却还留在快照里 = 门禁对「端点彻底坏掉」是瞎的。
        """
        bad = [
            f"{key} -> {ep['status']}"
            for key, ep in self.baseline["endpoints"].items()
            if ep["status"] != 200
        ]
        self.assertFalse(bad, f"这些端点没返回 200: {bad}")

    def test_nullable_fields_are_explicitly_tracked(self):
        """「哪些字段可能是 null」必须被快照显式记下来。

        这正是 OpenAPI(schema)层看不见、而客户天天踩的那一面:
        schema 说 integer, 运行时可能是 null。
        """
        nulls: list = []
        for key, ep in self.baseline["endpoints"].items():
            _find_null_fields(ep["shape"], key, nulls)
        self.assertGreater(
            len(nulls),
            0,
            "快照里一个 nullable 字段都没有 —— 可空字段是不是根本没被记录?",
        )

    # ---------- 差异语义的单测(「测试测试」) ----------

    def test_diff_flags_removed_field_as_breaking(self):
        """字段被删 → 破坏性(客户拿不到值了)。"""
        sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
        from gen_payload_shapes import diff_shapes

        before = {"endpoints": {"GET /x": {"status": 200, "shape": {
            "types": ["object"], "fields": {"a": {"types": ["integer"]}}}}}}
        after = {"endpoints": {"GET /x": {"status": 200, "shape": {
            "types": ["object"], "fields": {}}}}}
        breaking, compatible = diff_shapes(before, after)
        self.assertTrue(any("字段被删" in line for line in breaking))
        self.assertFalse(compatible)

    def test_diff_flags_added_field_as_compatible(self):
        """新增字段 → 兼容(只提示)。"""
        sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
        from gen_payload_shapes import diff_shapes

        before = {"endpoints": {"GET /x": {"status": 200, "shape": {
            "types": ["object"], "fields": {}}}}}
        after = {"endpoints": {"GET /x": {"status": 200, "shape": {
            "types": ["object"], "fields": {"a": {"types": ["integer"]}}}}}}
        breaking, compatible = diff_shapes(before, after)
        self.assertFalse(breaking)
        self.assertTrue(any("新增字段" in line for line in compatible))

    def test_diff_flags_type_widening_as_breaking(self):
        """类型被加宽(integer → integer|null)→ 破坏性。

        这是本快照最有价值的一条: **客户原来的类型假设失效了**
        (JS 端 data.x.toFixed(0) 会直接炸)。
        """
        sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
        from gen_payload_shapes import diff_shapes

        before = {"endpoints": {"GET /x": {"status": 200, "shape": {
            "types": ["object"], "fields": {"a": {"types": ["integer"]}}}}}}
        after = {"endpoints": {"GET /x": {"status": 200, "shape": {
            "types": ["object"], "fields": {"a": {"types": ["integer", "null"]}}}}}}
        breaking, _ = diff_shapes(before, after)
        self.assertTrue(any("加宽" in line for line in breaking))

    def test_diff_flags_type_replacement_as_breaking(self):
        """类型被替换(integer → string)→ 破坏性(静默算错比报错更贵)。"""
        sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
        from gen_payload_shapes import diff_shapes

        before = {"endpoints": {"GET /x": {"status": 200, "shape": {
            "types": ["object"], "fields": {"a": {"types": ["integer"]}}}}}}
        after = {"endpoints": {"GET /x": {"status": 200, "shape": {
            "types": ["object"], "fields": {"a": {"types": ["string"]}}}}}}
        breaking, _ = diff_shapes(before, after)
        self.assertTrue(any("替换" in line for line in breaking))

    def test_diff_flags_type_narrowing_as_compatible(self):
        """类型被收窄(integer|null → integer)→ 兼容(对客户是安全方向)。"""
        sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
        from gen_payload_shapes import diff_shapes

        before = {"endpoints": {"GET /x": {"status": 200, "shape": {
            "types": ["object"], "fields": {"a": {"types": ["integer", "null"]}}}}}}
        after = {"endpoints": {"GET /x": {"status": 200, "shape": {
            "types": ["object"], "fields": {"a": {"types": ["integer"]}}}}}}
        breaking, compatible = diff_shapes(before, after)
        self.assertFalse(breaking)
        self.assertTrue(any("收窄" in line for line in compatible))

    def test_diff_treats_unknown_as_compatible(self):
        """空数组的 unknown ↔ 具体类型 → 只提示(数据出现了不是破坏)。"""
        sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
        from gen_payload_shapes import diff_shapes

        before = {"endpoints": {"GET /x": {"status": 200, "shape": {
            "types": ["array"], "items": {"types": ["unknown"]}}}}}
        after = {"endpoints": {"GET /x": {"status": 200, "shape": {
            "types": ["array"], "items": {
                "types": ["object"], "fields": {"a": {"types": ["integer"]}}}}}}}
        breaking, compatible = diff_shapes(before, after)
        self.assertFalse(breaking)
        self.assertTrue(compatible)

    def test_shape_of_merges_array_items(self):
        """数组里 item 的形状要**合并** —— 有的 item 有某字段、有的没有,
        必须表现为并集, 而不是「碰巧这次都有」被忽略。"""
        sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
        from gen_payload_shapes import shape_of

        shape = shape_of([{"a": 1}, {"a": 2, "b": "x"}])
        self.assertEqual(shape["types"], ["array"])
        item = shape["items"]
        self.assertEqual(item["types"], ["object"])
        self.assertEqual(sorted(item["fields"]), ["a", "b"])

    def test_shape_of_distinguishes_bool_from_int(self):
        """bool 是 int 的子类 —— 判断顺序错了, True 会被记成 integer。"""
        sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
        from gen_payload_shapes import shape_of

        self.assertEqual(shape_of(True)["types"], ["boolean"])
        self.assertEqual(shape_of(1)["types"], ["integer"])
        self.assertEqual(shape_of(1.5)["types"], ["number"])
        self.assertEqual(shape_of(None)["types"], ["null"])


if __name__ == "__main__":
    unittest.main()
