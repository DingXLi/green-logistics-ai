"""
导出列契约冻结测试 (iter #82) — 把「导出文件里到底有哪些列」冻住

背景
----
iter #78 (ADR-0010) 冻结 ``agents.persistence`` 的**公开方法签名**。
iter #79 (ADR-0011) 冻结 **HTTP 面**: 路径 / 参数 / 状态码 / response_model / 鉴权依赖。
iter #80 (ADR-0012) 冻结 **响应体运行时形状**(字段名 + 类型)。
iter #81 (ADR-0013) 把端点清单改成**自动发现** + **显式豁免登记册** ——
但它把 14 个导出端点登记成了 ``NON_JSON_RESPONSE`` 豁免, 豁免 note 里写着:

    「CSV 全文导出; 字段契约是「列名」, 不是 JSON 字段形状。」

**这句话是对的, 但它是一张欠条。** 「字段契约是列名」= 列名必须有保护。
而 ADR-0013 交付之后, 那 14 个端点的列名**依然零保护** ——
前面三层契约都盖不住它:
- ADR-0010 看的是 Python 方法签名, 不看返回值内容
- ADR-0011 看的是路径/参数/状态码, 不看响应体
- ADR-0012 抽的是 JSON —— 而 CSV/Parquet/NDJSON **不是 JSON**

所以本文件补上**第四层**: 导出文件(CSV/Parquet/NDJSON/JSON)的**列契约**。

两层检查
--------
1. **每端点 vs 基线** —— 跟 ADR-0012 一样, 真起服务真调端点, 跟仓库基线比。
2. **同表跨格式一致性** —— 同一张表导出成不同格式, **列集合必须一样**。
   这一层**不需要基线**, 它是结构不变量 → 在一份全新的 checkout 上也成立。

   为什么必须有它: 只冻基线是不够的 —— 如果基线本身就是漂移的
   (19 列的 CSV vs 20 列的 Parquet 被一起冻进去), 门禁会一直绿,
   **绿得没有意义**。

分层断言(和 ADR-0010 / 0011 / 0012 / 0013 一脉相承)
-----------------------------------------------------
- **破坏性 → 红**: 端点没了 / 列被删 / 列被改名 / **CSV 列顺序变了** /
  **有序格式中间插了一列** / **Parquet 列类型变了** / **同表跨格式列集不一致**
- **兼容 → 黄(只打印, 不阻断 CI)**: 新增列(追加在末尾) / 新增端点

理由和 ADR-0011 一模一样: **门禁一旦「什么变化都报红」, 团队就会开始无脑刷基线
—— 那它就废了。**

基线
----
``tests/export_column_contracts.json`` —— 由 ``scripts/gen_export_contracts.py``
**真起一次服务、真实调一遍端点**机械生成(不是手写)。
故意改导出列时:
    python scripts/gen_export_contracts.py --write
然后在 PR 描述里写清楚: 哪些导出的列变了 / 客户端怎么改。

跑法:
    pytest tests/test_export_contracts.py -v
"""

import json
import os
import subprocess
import sys
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_BASELINE_PATH = os.path.join(_REPO_ROOT, "tests", "export_column_contracts.json")
_GENERATOR = os.path.join(_REPO_ROOT, "scripts", "gen_export_contracts.py")

# 基线规模下限 —— 防「有人为了让 CI 变绿而手工削掉一半基线」
_MIN_ENDPOINTS = 35
_MIN_TABLES = 6

# 快照采集是子进程 (要独占 GL_DB_PATH + 独占 app import),
# 整个测试类共用一次采集结果, 避免每个用例都付一次启动成本
_SNAPSHOT_CACHE = {}


def _collect_snapshot() -> dict:
    """跑一次生成器, 拿当前运行时快照(带缓存)。"""
    if "snapshot" not in _SNAPSHOT_CACHE:
        result = subprocess.run(
            [sys.executable, _GENERATOR, "--json"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=900,
        )
        if result.returncode != 0:
            raise AssertionError(
                f"gen_export_contracts.py --json 执行失败:\n{result.stdout}\n{result.stderr}"
            )
        _SNAPSHOT_CACHE["snapshot"] = json.loads(result.stdout)
    return _SNAPSHOT_CACHE["snapshot"]


def _load_baseline() -> dict:
    with open(_BASELINE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


class TestExportColumnContracts(unittest.TestCase):
    """iter #82: 导出列契约冻结"""

    @classmethod
    def setUpClass(cls):
        cls.baseline = _load_baseline()

    # ---------- 核心：运行时列契约与基线一致 ----------

    def test_runtime_export_contracts_match_baseline(self):
        """当前运行时导出列契约必须与仓库基线一致(无破坏性变更)。

        这是本文件的主断言: 端点少一个 / 列被删 / 列被改名 /
        CSV 列顺序变了 / Parquet 列类型变了 —— 任何一条都会红。
        """
        sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
        from gen_export_contracts import diff_contracts

        breaking, compatible = diff_contracts(self.baseline, _collect_snapshot())

        if breaking:
            self.fail(
                "导出列契约出现了破坏性变更(这是一次真实的导出契约破坏):\n"
                + "\n".join(breaking)
                + "\n\n如果这是**故意**的变更，请跑：\n"
                "    python scripts/gen_export_contracts.py --write\n"
                "并在 PR 描述里说明: 哪些导出的列变了 / 客户端怎么改。"
            )

        if compatible:
            print("\n[兼容变更（仅提示，不阻断 CI）]")
            for line in compatible:
                print(f"  {line}")

    def test_cross_format_consistency(self):
        """同一张表, 不同格式的列集合必须完全一致。

        **这一层不需要基线** —— 它是结构不变量, 所以在一份全新的 checkout 上
        也成立。只冻基线是不够的: 如果基线本身就是漂移的, 门禁会一直绿,
        **绿得没有意义**。

        这一条是 iter #82 的真正价值: 它当场抓出了三个真 bug ——
        - ``export/cycles.csv`` 少一列 ``n_routes``(CSV 导出比 JSON/Parquet 少)
        - ``db-export?table=supplies&fmt=parquet`` 全是 string 类型
          (CSV 往返把数字全变成字符串 → 客户 ``sum()`` 静默算错)
        - ``db-export?table=llm_decisions&fmt=csv`` 返回 placeholder 假 CSV
        """
        sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
        from gen_export_contracts import check_cross_format_consistency

        problems = check_cross_format_consistency(_collect_snapshot())
        if problems:
            self.fail(
                "同一张表在不同导出格式下列集合不一致(客户换个格式下载就少列/多列):\n"
                + "\n".join(f"  - {p}" for p in problems)
            )

    def test_generator_cli_reports_no_drift(self):
        """CLI 路径也必须报 OK(保证脚本路径和测试路径结论一致)。"""
        result = subprocess.run(
            [sys.executable, _GENERATOR],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=900,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"gen_export_contracts.py 报出导出列契约漂移:\n{result.stdout}\n{result.stderr}",
        )
        self.assertIn("OK: 导出列契约与基线一致", result.stdout)

    def test_snapshot_is_deterministic(self):
        """同一份代码跑两次, 快照必须**字节级一致**。

        这条比看起来更重要: 快照门禁如果不可复现, 它就只是一条随机噪音的门禁 ——
        团队会很快学会无视它, 于是真破坏也会被一起无视。
        (确定性来自: 固定 fixture DB + 固定 seed 的世界引导 + 只记结构不记值)
        """
        first = subprocess.run(
            [sys.executable, _GENERATOR, "--json"],
            cwd=_REPO_ROOT, capture_output=True, text=True, timeout=900,
        )
        second = subprocess.run(
            [sys.executable, _GENERATOR, "--json"],
            cwd=_REPO_ROOT, capture_output=True, text=True, timeout=900,
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(
            first.stdout,
            second.stdout,
            "两次采集的快照不一致 —— 快照不可复现, 这个门禁就是噪音",
        )

    # ---------- 基线自身的规模守卫 ----------

    def test_baseline_covers_expected_exports(self):
        """基线必须覆盖主要导出面, 且规模不能被削。"""
        endpoints = self.baseline["endpoints"]
        self.assertGreaterEqual(len(endpoints), _MIN_ENDPOINTS)
        keys = set(endpoints)
        for required in (
            "GET /api/persistence/export/cycles.csv",
            "GET /api/persistence/export/cycles.parquet",
            "GET /api/persistence/export/cycles.ndjson",
            "GET /api/persistence/export/cycles.json",
            "GET /api/persistence/export/supplies.csv",
            "GET /api/persistence/export/matches.csv",
            "GET /api/persistence/export/routes.csv",
            "GET /api/persistence/export/perturbed-supplies.csv",
            "GET /api/persistence/export/cycle-detail/{cycle_id}.csv",
            "GET /api/admin/db-export?table=cycles&fmt=csv",
            "GET /api/admin/db-export?table=llm_decisions&fmt=parquet",
        ):
            self.assertIn(required, keys, f"基线缺少关键导出面 {required}")

    def test_baseline_covers_multiple_tables(self):
        """基线必须覆盖多张逻辑表(每张表至少两个格式 → 可做跨格式比对)。"""
        tables = {e.get("table") for e in self.baseline["endpoints"].values()}
        tables.discard(None)
        self.assertGreaterEqual(
            len(tables),
            _MIN_TABLES,
            f"基线只覆盖了 {tables} —— 是不是退回手写小集合了?",
        )

    def test_baseline_has_no_failed_endpoints(self):
        """基线里不能有非 200 的导出端点(否则等于把「坏了」也冻进契约)。"""
        failed = sorted(
            key for key, e in self.baseline["endpoints"].items()
            if e.get("status") != 200
        )
        self.assertEqual(
            failed, [],
            f"基线里有导出端点返回非 200 —— 契约不该冻结一个坏掉的端点: {failed}",
        )

    # ---------- 差异语义的单元测试（「测试测试」） ----------

    def test_diff_semantics_column_deleted_is_breaking(self):
        """列被删 → 破坏性(客户 df['col'] 直接 KeyError)。"""
        sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
        from gen_export_contracts import diff_contracts

        before = {"endpoints": {"GET /x.csv": {
            "format": "csv", "status": 200, "columns": ["a", "b", "c"]}}}
        after = {"endpoints": {"GET /x.csv": {
            "format": "csv", "status": 200, "columns": ["a", "c"]}}}
        breaking, _ = diff_contracts(before, after)
        self.assertTrue(any("被删" in line for line in breaking), breaking)

    def test_diff_semantics_column_added_is_compatible(self):
        """新增列 → 兼容(只提示, 不阻断 CI)。"""
        sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
        from gen_export_contracts import diff_contracts

        before = {"endpoints": {"GET /x.csv": {
            "format": "csv", "status": 200, "columns": ["a", "b"]}}}
        after = {"endpoints": {"GET /x.csv": {
            "format": "csv", "status": 200, "columns": ["a", "b", "c"]}}}
        breaking, compatible = diff_contracts(before, after)
        self.assertEqual(breaking, [])
        self.assertTrue(any("新增列" in line for line in compatible), compatible)

    def test_diff_semantics_csv_reorder_is_breaking(self):
        """CSV 列顺序变了 → 破坏性(位置型读取器静默读错列)。"""
        sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
        from gen_export_contracts import diff_contracts

        before = {"endpoints": {"GET /x.csv": {
            "format": "csv", "status": 200, "columns": ["a", "b", "c"]}}}
        after = {"endpoints": {"GET /x.csv": {
            "format": "csv", "status": 200, "columns": ["c", "a", "b"]}}}
        breaking, _ = diff_contracts(before, after)
        self.assertTrue(any("顺序" in line for line in breaking), breaking)

    def test_diff_semantics_middle_insert_is_breaking(self):
        """有序格式中间插一列 → 破坏性(后面所有列位置右移)。"""
        sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
        from gen_export_contracts import diff_contracts

        before = {"endpoints": {"GET /x.csv": {
            "format": "csv", "status": 200, "columns": ["a", "c"]}}}
        after = {"endpoints": {"GET /x.csv": {
            "format": "csv", "status": 200, "columns": ["a", "b", "c"]}}}
        breaking, _ = diff_contracts(before, after)
        self.assertTrue(any("插了一列" in line for line in breaking), breaking)

    def test_diff_semantics_parquet_type_change_is_breaking(self):
        """Parquet 列类型变了 → 破坏性(客户端算术静默失效)。"""
        sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
        from gen_export_contracts import diff_contracts

        before = {"endpoints": {"GET /x.parquet": {
            "format": "parquet", "status": 200,
            "columns": [{"name": "tons", "type": "number"}]}}}
        after = {"endpoints": {"GET /x.parquet": {
            "format": "parquet", "status": 200,
            "columns": [{"name": "tons", "type": "string"}]}}}
        breaking, _ = diff_contracts(before, after)
        self.assertTrue(any("类型" in line for line in breaking), breaking)

    def test_diff_semantics_json_key_order_is_not_breaking(self):
        """JSON 键顺序变了 → 不是破坏性(JSON 对象无序)。"""
        sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
        from gen_export_contracts import diff_contracts

        before = {"endpoints": {"GET /x.json": {
            "format": "json", "status": 200, "keys": ["a", "b"]}}}
        after = {"endpoints": {"GET /x.json": {
            "format": "json", "status": 200, "keys": ["b", "a"]}}}
        breaking, _ = diff_contracts(before, after)
        self.assertEqual(breaking, [])

    def test_diff_semantics_endpoint_removed_is_breaking(self):
        """导出端点没了 → 破坏性(客户下载 URL 直接 404)。"""
        sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
        from gen_export_contracts import diff_contracts

        before = {"endpoints": {"GET /x.csv": {
            "format": "csv", "status": 200, "columns": ["a"]}}}
        after = {"endpoints": {}}
        breaking, _ = diff_contracts(before, after)
        self.assertTrue(any("不在契约里了" in line for line in breaking), breaking)

    def test_cross_format_check_detects_missing_column(self):
        """跨格式一致性检查能抓到「同一张表两种格式下列数不同」。"""
        sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
        from gen_export_contracts import check_cross_format_consistency

        snapshot = {"endpoints": {
            "GET /e/t.csv": {"format": "csv", "status": 200, "table": "t",
                             "columns": ["a", "b"]},
            "GET /e/t.parquet": {"format": "parquet", "status": 200, "table": "t",
                                 "columns": [{"name": "a", "type": "string"},
                                             {"name": "b", "type": "string"},
                                             {"name": "c", "type": "number"}]},
        }}
        problems = check_cross_format_consistency(snapshot)
        self.assertTrue(problems, "少一列没被抓到")
        self.assertIn("少了", problems[0])

    def test_cross_format_check_ignores_single_format_tables(self):
        """只有一种格式的表不参与跨格式比对(没有可比对象)。"""
        sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
        from gen_export_contracts import check_cross_format_consistency

        snapshot = {"endpoints": {
            "GET /e/only.csv": {"format": "csv", "status": 200, "table": "only",
                                "columns": ["a"]},
        }}
        self.assertEqual(check_cross_format_consistency(snapshot), [])


if __name__ == "__main__":
    unittest.main()
