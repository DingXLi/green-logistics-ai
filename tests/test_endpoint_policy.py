"""
端点策略测试 (iter #81) —— 自动发现 + 显式豁免登记册的 fail-closed 不变量

背景
----
iter #80 (ADR-0012) 把「响应体运行时形状」冻成了 CI 常驻不变量, 但它的
端点清单是**手写的 45 条**。那份清单是 **fail-open** 的:

    新加一个端点 → 没人想起来把它加进清单 → 门禁照常绿 → 客户那边照样炸。

「守住下限」只能证明「该保的还在保」, 不能证明「新增的也在保」。

iter #81 (ADR-0013) 把它换成 fail-closed:

1. **自动发现** —— 从 FastAPI 路由表枚举全部 GET 端点, 不再手写。
2. **显式豁免登记册** —— 采不到的必须在 EXEMPTIONS 里登记 + 写明理由。
3. **不漏** —— 任何 GET 端点既没被采集也没被豁免 → 报错。
4. **不腐** —— 豁免登记册里不允许有已不存在的路由(陈旧豁免)。
5. **不空** —— 豁免理由必须来自封闭枚举, 且必须有人话说明。

跑法:
    pytest tests/test_endpoint_policy.py -v
"""

import json
import os
import subprocess
import sys
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
if os.path.join(_REPO_ROOT, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))

import endpoint_policy  # noqa: E402
from gen_payload_shapes import diff_coverage  # noqa: E402

_BASELINE_PATH = os.path.join(
    _REPO_ROOT, "tests", "http_api_payload_shapes.json")
_POLICY_SCRIPT = os.path.join(_REPO_ROOT, "scripts", "endpoint_policy.py")
_GENERATOR = os.path.join(_REPO_ROOT, "scripts", "gen_payload_shapes.py")


def _load_baseline():
    with open(_BASELINE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


class TestPolicyIntegrity(unittest.TestCase):
    """策略本身的完整性 —— 不依赖运行时, 纯静态检查(快)。"""

    def test_all_exemption_reasons_are_documented(self):
        """每个豁免理由都必须有一句解释 —— 理由本身也要能被评审。"""
        for reason, doc in endpoint_policy.EXEMPTION_REASONS.items():
            self.assertTrue(
                doc.strip(),
                f"豁免理由 {reason} 没有说明文字",
            )

    def test_every_exemption_has_known_reason(self):
        """豁免登记册里的理由必须在封闭枚举内。"""
        for path, entry in endpoint_policy.EXEMPTIONS.items():
            self.assertIn(
                entry.get("reason"),
                endpoint_policy.EXEMPTION_REASONS,
                f"{path} 的豁免理由 {entry.get('reason')!r} 不在封闭枚举里",
            )

    def test_every_exemption_has_human_note(self):
        """豁免必须写清「客户少了哪层保护」—— 一句人话, 不许留空。"""
        for path, entry in endpoint_policy.EXEMPTIONS.items():
            self.assertTrue(
                entry.get("note", "").strip(),
                f"{path} 的豁免没有 note —— 评审时看不懂为什么豁免",
            )

    def test_every_param_binding_targets_a_real_route(self):
        """参数绑定不能指向不存在的路由(否则是死配置)。"""
        app = endpoint_policy.get_app()
        routes = set(endpoint_policy.discover_get_routes(app))
        for path in endpoint_policy.PARAM_BINDINGS:
            self.assertIn(
                path, routes,
                f"PARAM_BINDINGS 里的 {path} 不在路由表里 —— 陈旧绑定",
            )

    def test_no_route_is_both_bound_and_exempted(self):
        """同一个路由不能既绑定参数又被豁免 —— 逻辑矛盾。"""
        overlap = set(endpoint_policy.PARAM_BINDINGS) & set(endpoint_policy.EXEMPTIONS)
        self.assertFalse(
            overlap,
            f"这些路由既在 PARAM_BINDINGS 又在 EXEMPTIONS: {sorted(overlap)}",
        )


class TestClassification(unittest.TestCase):
    """自动发现 + 分类 + fail-closed 校验(要起 app, 慢一点)。"""

    @classmethod
    def setUpClass(cls):
        cls.app = endpoint_policy.get_app()
        cls.result = endpoint_policy.classify(cls.app)

    def test_discovery_finds_all_get_routes(self):
        """自动发现必须找得到路由表里的每一个 GET 端点。"""
        routes = endpoint_policy.discover_get_routes(self.app)
        self.assertGreater(len(routes), 100, "自动发现的 GET 端点太少 —— 是不是漏了?")
        self.assertEqual(len(routes), len(set(routes)), "发现结果里有重复路由")
        self.assertEqual(routes, sorted(routes), "发现结果应该有序(可复现)")

    def test_every_route_is_covered_or_exempted(self):
        """**不漏**: 每个 GET 端点都必须有归属, 不允许沉默。"""
        covered = set(self.result["covered"])
        exempted = set(self.result["exempted"])
        routes = set(endpoint_policy.discover_get_routes(self.app))
        orphans = routes - covered - exempted
        self.assertFalse(
            orphans,
            f"这些 GET 端点既没被采集也没被豁免: {sorted(orphans)}",
        )

    def test_coverage_and_exemption_do_not_overlap(self):
        """一个路由不能同时被采集和被豁免。"""
        overlap = set(self.result["covered"]) & set(self.result["exempted"])
        self.assertFalse(overlap, f"重复归属: {sorted(overlap)}")

    def test_no_stale_exemptions(self):
        """**不腐**: 豁免登记册里不许有已经不存在的路由。"""
        self.assertFalse(
            self.result["stale"],
            f"陈旧豁免: {self.result['stale']} —— 端点被删或改名了, 请同步登记册",
        )

    def test_validation_passes_for_current_code(self):
        """当前代码必须通过 fail-closed 校验。"""
        errors = endpoint_policy.validate(self.result)
        self.assertFalse(errors, f"策略校验失败: {errors}")

    def test_validate_catches_unclassified_route(self):
        """校验器必须真的能抓到「未分类的路由」(测试测试)。"""
        fake = {
            "covered": list(self.result["covered"]),
            "exempted": dict(self.result["exempted"]),
            "unclassified": ["/api/brand-new-endpoint"],
            "routes": list(self.result["routes"]) + ["/api/brand-new-endpoint"],
            "stale": [],
            "bad_reason": [],
            "total_get_routes": self.result["total_get_routes"] + 1,
        }
        errors = endpoint_policy.validate(fake)
        self.assertTrue(errors, "校验器没抓到未分类路由 —— 门禁形同虚设")
        self.assertTrue(
            any("未分类" in e for e in errors),
            f"校验器抓错了东西: {errors}",
        )

    def test_validate_catches_stale_exemption(self):
        """校验器必须抓到陈旧豁免(死配置)。"""
        fake = dict(self.result)
        fake["stale"] = ["/api/deleted-long-ago"]
        fake["bad_reason"] = []
        errors = endpoint_policy.validate(fake)
        self.assertTrue(
            any("陈旧豁免" in e for e in errors),
            f"校验器没抓到陈旧豁免: {errors}",
        )

    def test_validate_catches_bad_reason(self):
        """校验器必须抓到不在封闭枚举里的理由。"""
        fake = dict(self.result)
        fake["bad_reason"] = [("/api/x", "因为我不高兴")]
        fake["stale"] = []
        errors = endpoint_policy.validate(fake)
        self.assertTrue(
            any("封闭枚举" in e for e in errors),
            f"校验器没抓到非法理由: {errors}",
        )

    def test_validate_catches_missing_note(self):
        """校验器必须抓到没有说明的豁免。"""
        fake = dict(self.result)
        fake["exempted"] = dict(self.result["exempted"])
        fake["exempted"]["/api/whatever"] = {"reason": "LLM_CALL", "note": "  "}
        errors = endpoint_policy.validate(fake)
        self.assertTrue(
            any("note" in e for e in errors),
            f"校验器没抓到缺 note 的豁免: {errors}",
        )


class TestCoverageDiff(unittest.TestCase):
    """覆盖范围的差异语义 —— 「端点被豁免」必须报红。"""

    def setUp(self):
        self.baseline = {
            "coverage": {
                "total_get_routes": 10,
                "covered": 8,
                "exempted": 2,
                "exempted_routes": {
                    "GET /api/old-exempt": {"reason": "LLM_CALL", "note": "x"},
                },
            },
            "endpoints": {
                "GET /api/a": {"status": 200, "shape": {"types": ["object"]}},
                "GET /api/b": {"status": 200, "shape": {"types": ["object"]}},
            },
        }

    def test_endpoint_becoming_exempt_is_breaking(self):
        """端点从「被采集」变成「被豁免」→ 🔴 破坏。

        这是 ADR-0013 的核心语义: 客户可见的响应体从此不再有契约保护,
        跟「字段被删」是同一等级的事故 —— 客户照样炸, 只是更安静。
        """
        current = {
            "coverage": {
                "total_get_routes": 10,
                "covered": 7,
                "exempted": 3,
                "exempted_routes": {
                    "GET /api/old-exempt": {"reason": "LLM_CALL", "note": "x"},
                    "GET /api/a": {"reason": "EXTERNAL_NETWORK", "note": "y"},
                },
            },
            "endpoints": {
                "GET /api/b": {"status": 200, "shape": {"types": ["object"]}},
            },
        }
        breaking, _ = diff_coverage(self.baseline, current)
        self.assertTrue(
            any("豁免" in b and "/api/a" in b for b in breaking),
            f"端点被豁免没有报红: {breaking}",
        )

    def test_new_endpoint_being_covered_is_compatible(self):
        """新增端点被采集 → 🟡 兼容(覆盖面变大是好事)。"""
        current = {
            "coverage": {
                "total_get_routes": 11,
                "covered": 9,
                "exempted": 2,
                "exempted_routes": {
                    "GET /api/old-exempt": {"reason": "LLM_CALL", "note": "x"},
                },
            },
            "endpoints": {
                "GET /api/a": {"status": 200, "shape": {"types": ["object"]}},
                "GET /api/b": {"status": 200, "shape": {"types": ["object"]}},
                "GET /api/new": {"status": 200, "shape": {"types": ["object"]}},
            },
        }
        breaking, compatible = diff_coverage(self.baseline, current)
        self.assertFalse(
            any("豁免" in b for b in breaking),
            f"新增端点不该报红: {breaking}",
        )
        self.assertTrue(
            any("GET 端点总数" in c for c in compatible),
            f"端点总数变化没有被提示: {compatible}",
        )

    def test_endpoint_becoming_covered_from_exempt_is_compatible(self):
        """端点从「被豁免」变成「被采集」→ 🟡 兼容(保护变强了)。"""
        current = {
            "coverage": {
                "total_get_routes": 10,
                "covered": 9,
                "exempted": 1,
                "exempted_routes": {},
            },
            "endpoints": {
                "GET /api/a": {"status": 200, "shape": {"types": ["object"]}},
                "GET /api/b": {"status": 200, "shape": {"types": ["object"]}},
                "GET /api/old-exempt": {"status": 200, "shape": {"types": ["object"]}},
            },
        }
        breaking, compatible = diff_coverage(self.baseline, current)
        self.assertFalse(breaking, f"豁免转采集不该报红: {breaking}")
        self.assertTrue(
            any("覆盖面变大" in c for c in compatible),
            f"豁免转采集没有被提示: {compatible}",
        )


class TestBaselineCoverage(unittest.TestCase):
    """基线本身的覆盖情况 —— 防止覆盖率悄悄退化。"""

    @classmethod
    def setUpClass(cls):
        cls.baseline = _load_baseline()

    def test_baseline_has_coverage_section(self):
        """基线必须记录覆盖范围(不然没法比「谁跑了」)。"""
        cov = self.baseline.get("coverage")
        self.assertIsNotNone(cov, "基线里没有 coverage 段")
        self.assertIn("total_get_routes", cov)
        self.assertIn("covered", cov)
        self.assertIn("exempted", cov)

    def test_baseline_coverage_is_most_of_the_routes(self):
        """覆盖率不能悄悄塌掉 —— 守住下限。"""
        cov = self.baseline["coverage"]
        ratio = cov["covered"] / max(1, cov["total_get_routes"])
        self.assertGreater(
            ratio, 0.75,
            f"覆盖率只有 {ratio:.1%} ({cov['covered']}/{cov['total_get_routes']}) —— "
            f"是不是把太多端点豁免掉了?",
        )

    def test_baseline_endpoint_count_matches_coverage(self):
        """基线里的端点数必须和 coverage 段对得上(防止只改一处)。"""
        cov = self.baseline["coverage"]
        self.assertEqual(
            len(self.baseline["endpoints"]),
            cov["covered"],
            "coverage.covered 和实际端点数不一致 —— 基线被手改过?",
        )

    def test_every_exempted_route_is_recorded_with_reason(self):
        """每个被豁免的端点都要在基线里留下理由 —— 评审能追溯。"""
        exempt = self.baseline["coverage"].get("exempted_routes") or {}
        for path, entry in exempt.items():
            self.assertIn(
                entry.get("reason"),
                endpoint_policy.EXEMPTION_REASONS,
                f"基线里 {path} 的豁免理由不在封闭枚举里",
            )
            self.assertTrue(entry.get("note", "").strip(), f"{path} 的豁免缺 note")


class TestPolicyCli(unittest.TestCase):
    """命令行入口 —— CI 会直接调它。"""

    def test_cli_reports_success(self):
        result = subprocess.run(
            [sys.executable, _POLICY_SCRIPT, "--json"],
            cwd=_REPO_ROOT, capture_output=True, text=True, timeout=300,
        )
        self.assertEqual(result.returncode, 0, f"策略校验没过:\n{result.stdout}\n{result.stderr}")
        payload = json.loads(result.stdout)
        self.assertEqual(payload["errors"], [])
        self.assertGreater(payload["total_get_routes"], 100)
        self.assertEqual(
            payload["covered_count"] + payload["exempted_count"],
            payload["total_get_routes"],
            "采集 + 豁免 != 端点总数 —— 有端点没归属",
        )


if __name__ == "__main__":
    unittest.main()
