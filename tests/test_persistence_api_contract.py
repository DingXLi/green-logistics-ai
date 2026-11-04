"""
公开 API 契约测试 (iter #78) — 把「重构不改公开 API」变成 CI 里的常驻不变量

背景
----
ADR-0009 把 ``agents/persistence.py``(9,949 行)拆成 ``agents/persistence/`` 包，
当时验证「公开 API 一个字没改」的手段是**一次性人工双向比对**老类 vs 新类的
114 个方法。

一次性比对的问题：
- 结论只在那一天成立 —— 下一个 PR 悄悄改一个默认值，**没有任何东西会报警**；
- 比对结果没留在仓库里 —— 面试时拿不出机器可复现的证据。

所以这里把契约固化下来：
- ``tests/persistence_api_contract.json`` —— 基线快照（由脚本机械抽取，不是手写）；
- ``tests/test_persistence_api_contract.py`` —— 本文件，每次 CI 都比对。

**刻意不比对的东西**：MRO、方法归属哪个 mixin、内部模块怎么切。
那是**重构自由**，不是 API 破坏 —— 换个模块放一个方法不该让 CI 变红。
但「方法搬到哪个 mixin 了」会被打印出来，方便评审时一眼看见。

想故意改公开 API 时（加方法 / 改默认值），流程是：
    python scripts/gen_api_contract.py --write
然后在 PR 描述里写清楚「哪些方法变了、为什么、下游调用点怎么改」。

跑法：
    pytest tests/test_persistence_api_contract.py -v
"""

import json
import os
import subprocess
import sys
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_CONTRACT_PATH = os.path.join(_REPO_ROOT, "tests", "persistence_api_contract.json")
_GENERATOR = os.path.join(_REPO_ROOT, "scripts", "gen_api_contract.py")

# 拆分后所有领域 mixin 都必须出现在 Persistence 的 MRO 里。
# 少一个 mixin = 少一批方法 = API 缩水，这条会在下面单独断言。
_EXPECTED_DOMAIN_MIXINS = {
    "CycleMixin",
    "LlmMixin",
    "AnalyticsMixin",
    "ExportMixin",
    "StatsMixin",
    "AnomalyMixin",
    "MaintenanceMixin",
    "ConfigMixin",
    "ForecastMixin",
    "PerturbationMixin",
    "ComparisonMixin",
    "SolverMixin",
}


class TestPersistenceApiContract(unittest.TestCase):
    """iter #78: Persistence 公开 API 契约冻结"""

    @classmethod
    def setUpClass(cls):
        from agents.persistence import Persistence

        cls.Persistence = Persistence
        with open(_CONTRACT_PATH, encoding="utf-8") as fh:
            cls.baseline = json.load(fh)

    # ---------- 核心：公开 API 与基线逐字一致 ----------

    def test_public_api_matches_committed_baseline(self):
        """当前公开 API 必须与仓库里的基线快照完全一致。

        覆盖：方法增删、参数名、参数默认值、返回注解、公开常量的增删与形状。
        这是整个契约测试的核心断言。
        """
        sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
        from gen_api_contract import diff_contracts, extract_contract

        current = extract_contract(self.Persistence)
        drift = diff_contracts(self.baseline, current)

        if drift:
            self.fail(
                "公开 API 与基线不一致（这是一次 API 破坏）：\n"
                + "\n".join(drift)
                + "\n\n如果这是**故意**的变更，请跑：\n"
                "    python scripts/gen_api_contract.py --write\n"
                "并在 PR 描述里说明：哪些方法变了 / 为什么 / 下游调用点怎么改。"
            )

    def test_generator_reports_no_drift(self):
        """脚本自己跑一遍也必须报 OK（保证 CLI 路径和测试路径结论一致）。"""
        result = subprocess.run(
            [sys.executable, _GENERATOR],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"gen_api_contract.py 报出 API 漂移：\n{result.stdout}\n{result.stderr}",
        )
        self.assertIn("OK: public API matches baseline", result.stdout)

    # ---------- 契约的规模守卫（防止基线本身被削掉） ----------

    def test_baseline_covers_expected_surface(self):
        """基线必须仍然是「104 个方法 + 4 个常量」的规模。

        防的是：有人为了让 CI 变绿，手工把基线 JSON 削掉一半。
        契约的规模本身也是契约的一部分。
        """
        self.assertEqual(self.baseline["method_count"], 104)
        self.assertEqual(self.baseline["constant_count"], 4)
        self.assertEqual(len(self.baseline["public"]["methods"]), 104)
        self.assertEqual(len(self.baseline["public"]["constants"]), 4)

    def test_baseline_is_not_trivially_empty(self):
        """再一道保险：基线里必须有真实内容，不能是空壳。"""
        methods = self.baseline["public"]["methods"]
        self.assertGreater(len(methods), 50)
        # 抽查几个必须存在的核心方法
        for name in ("begin_cycle", "commit_cycle", "record_supply",
                     "get_summary", "get_cycle_history", "vacuum"):
            self.assertIn(name, methods, f"基线缺少核心方法 {name}")

    # ---------- 公开常量不能悄悄变形 ----------

    def test_public_constants_present_and_shaped(self):
        """4 个公开类常量必须在，且形状（键集合 / 长度）与基线一致。"""
        constants = self.baseline["public"]["constants"]
        for name in ("SEASON_MONTHS", "SEASON_EMOJI", "SEASON_NAMES",
                     "CO2_FACTORS_KG_PER_TON_KM"):
            self.assertIn(name, constants, f"基线缺少常量 {name}")
            actual = getattr(self.Persistence, name)
            self.assertEqual(
                sorted(actual.keys()), constants[name]["keys"],
                f"常量 {name} 的键集合变了",
            )
            self.assertEqual(
                len(actual), constants[name]["length"],
                f"常量 {name} 的长度变了",
            )

    def test_season_keys_are_the_four_quarters(self):
        """季节常量的键必须是四季 —— 这是业务语义，不只是形状。"""
        for name in ("SEASON_MONTHS", "SEASON_EMOJI", "SEASON_NAMES"):
            value = getattr(self.Persistence, name)
            self.assertEqual(
                sorted(value.keys()),
                ["fall", "spring", "summer", "winter"],
                f"{name} 的季节键不对",
            )

    # ---------- 拆分结构守卫（mixin 骨架不能少） ----------

    def test_all_domain_mixins_present_in_mro(self):
        """12 个领域 mixin 必须都在 MRO 里 —— 少一个就等于少一批公开方法。"""
        mro = {c.__name__ for c in self.Persistence.__mro__}
        missing = _EXPECTED_DOMAIN_MIXINS - mro
        self.assertFalse(
            missing,
            f"MRO 里少了领域 mixin: {sorted(missing)} —— 对应的公开方法会消失",
        )

    def test_base_and_shared_mixins_present(self):
        """存储核心 + 共享契约 + 预测数学三个基础 mixin 必须在。"""
        mro = {c.__name__ for c in self.Persistence.__mro__}
        for name in ("PersistenceBase", "SharedQueryMixin", "ForecastMathMixin"):
            self.assertIn(name, mro, f"MRO 里少了 {name}")

    def test_mixin_order_is_stable(self):
        """mixin 的**顺序**也被冻结 —— 顺序变了，方法解析顺序就变了。

        这不是公开 API 本身，但它是「哪个实现生效」的前提：
        `SharedQueryMixin` 里那 3 个方法是签名契约（函数体是 `...`），
        实现分别在 CycleMixin / AnalyticsMixin。
        顺序颠倒会让契约 stub 抢在实现前面 —— 那是真 bug，不是风格问题。
        """
        expected = [
            "Persistence",
            "CycleMixin",
            "LlmMixin",
            "AnalyticsMixin",
            "ExportMixin",
            "StatsMixin",
            "AnomalyMixin",
            "MaintenanceMixin",
            "ConfigMixin",
            "ForecastMixin",
            "PerturbationMixin",
            "ComparisonMixin",
            "SolverMixin",
            "PersistenceBase",
            "ForecastMathMixin",
            "SharedQueryMixin",
            "object",
        ]
        actual = [c.__name__ for c in self.Persistence.__mro__]
        self.assertEqual(actual, expected, "mixin 顺序变了（方法解析顺序会跟着变）")

    def test_shared_contract_methods_resolve_to_real_implementations(self):
        """`_contract.py` 里的 3 个签名契约必须解析到**真实现**，不能解析到 stub。

        这是 mixin 架构最隐蔽的失败模式：
        `SharedQueryMixin.get_summary` 的函数体是 `...`（返回 None）。
        如果 MRO 顺序变了，`db.get_summary()` 会静默返回 None 而不是报 AttributeError。
        """
        expectations = {
            "get_cycle_history": "CycleMixin",
            "get_cycle_detail": "CycleMixin",
            "get_summary": "AnalyticsMixin",
        }
        for method_name, expected_owner in expectations.items():
            func = getattr(self.Persistence, method_name)
            owner = func.__qualname__.split(".")[0]
            self.assertEqual(
                owner, expected_owner,
                f"{method_name} 解析到了 {owner}，期望 {expected_owner}"
                f"（stub 抢在实现前面会让它静默返回 None）",
            )

    # ---------- 公开面里不该出现意外成员 ----------

    def test_no_public_callables_outside_contract(self):
        """类上不应出现基线之外的公开可调用成员（防止随手加的 helper 泄进公开面）。"""
        from agents.persistence import Persistence

        baseline_methods = set(self.baseline["public"]["methods"])
        actual = {
            n for n in dir(Persistence)
            if not n.startswith("_") and callable(inspect_getattr_static(Persistence, n))
        }
        extra = actual - baseline_methods
        self.assertFalse(
            extra,
            f"出现了基线之外的公开成员: {sorted(extra)} —— "
            f"要么写进基线（--write），要么改成下划线私有",
        )


def inspect_getattr_static(cls, name):
    """小helper：拿静态属性，避免触发 property 的 __get__（会走真实逻辑）。"""
    import inspect

    return inspect.getattr_static(cls, name)


if __name__ == "__main__":
    unittest.main()
