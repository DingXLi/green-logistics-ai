"""
门禁自证: 门禁视野普查必须真的会红 (iter #88 自测, 不进默认 testpaths)

这个文件验证**门禁视野普查**(ADR-0020) 会不会真的挡人。理由和
``selftest/test_loose_assert_selftest.py`` 完全一样:

    **一个只报绿的门禁没有价值。**

验证五件事
----------
1. **有牙齿** —— 把某道门禁的扫描范围收窄到只管一半, 普查必须判红。
2. **不误伤** —— 范围覆盖全集时, 普查必须绿。
3. **fail-closed** —— 关注面算不出来 / 门禁实现文件不存在 / 常量读不到 /
   登记册字段非法 → 全部判红。
4. **镜像校验** —— 两道门禁声明「完全一致」但实际范围不同 → 判红。
5. **不空转** —— 门禁自己也在仓库里, 它的代码不许有超阈值的函数
   (ADR-0020 的普查对象包括它自己; 它自己也得被别人的棘轮管住)。

所有坏数据都写在字符串里, 落到 tmp_path 的临时仓库上再扫 ——
本文件**自己不含**任何松断言, 所以可以被松断言门禁一起管。
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

import pytest

_SCRIPTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"
)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import gate_vision_census as gate  # noqa: E402

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ====================================================================
# 工具: 搭一个假仓库
# ====================================================================

def _make_repo(tmp_path, files: Dict[str, str]) -> str:
    """在 tmp_path 下造一个假仓库, 返回仓库根。"""
    for rel, content in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return str(tmp_path)


def _gate_module(scope: List[str]) -> str:
    """一份假的门禁实现: 模块级常量 SCAN_PATHS。"""
    body = "SCAN_PATHS = [\n"
    for item in scope:
        body += f'    "{item}",\n'
    body += "]\n"
    return body


def _registry(gates: List[Dict[str, Any]],
              mirrors: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    return {
        "version": 1,
        "gates": gates,
        "mirror_claims": mirrors or [],
        "known_gaps": [],
    }


def _one_gate(scope_path: str = "scripts/gate.py",
              scope_names: List[str] | None = None,
              concern: str = "PRODUCTION_PY",
              threshold: float = 100.0) -> Dict[str, Any]:
    return {
        "id": "fake-gate",
        "title": "假门禁",
        "concern": concern,
        "scope_source": {
            "kind": "module_const",
            "path": scope_path,
            "names": scope_names or ["SCAN_PATHS"],
        },
        "min_coverage_pct": threshold,
    }


@pytest.fixture()
def fake_repo(tmp_path, monkeypatch):
    """一个假仓库: agents/ 2 个文件, scripts/ 2 个文件。"""
    repo = _make_repo(tmp_path, {
        "agents/one.py": "def f():\n    return 1\n",
        "agents/two.py": "def g():\n    return 2\n",
        "scripts/gate.py": _gate_module(["agents"]),
        "scripts/helper.py": "def h():\n    return 3\n",
    })
    monkeypatch.setattr(gate, "_REPO_ROOT", repo)
    return repo


def _run(registry: Dict[str, Any]) -> Dict[str, Any]:
    registry_errors = gate.validate_registry(registry)
    result = gate.census(registry)
    return gate.build_report(registry, result, registry_errors)


# ====================================================================
# 1. 有牙齿: 范围收窄 → 必须红
# ====================================================================

def test_narrow_scope_is_red(fake_repo):
    """门禁只扫一半 → 普查判红。

    这就是本门禁存在的原因: ADR-0019 那天的 78 条松断言, 是在
    「门禁只看得见 72%」的前提下溜过去的。
    """
    report = _run(_registry([_one_gate()]))
    assert report["violations"], "范围只覆盖一半, 普查却报绿 —— 门禁没有牙齿"
    assert report["violations"][0]["gate"] == "fake-gate"
    # 半数的文件在视野外
    assert report["gates"][0]["covered"] < report["gates"][0]["total"]


def test_full_scope_is_green(fake_repo):
    """范围覆盖全集 → 普查绿。"""
    gate_mod = os.path.join(fake_repo, "scripts", "gate.py")
    with open(gate_mod, "w", encoding="utf-8") as fh:
        fh.write(_gate_module(["agents", "scripts"]))
    report = _run(_registry([_one_gate()]))
    assert not report["violations"], f"全覆盖却被判红: {report['violations']}"
    assert not report["errors"], f"全覆盖却报校验错: {report['errors']}"
    assert report["gates"][0]["coverage_pct"] == 100.0


def test_coverage_is_measured_not_declared(fake_repo):
    """视野百分比是**算出来的**, 不是登记册里抄的。

    登记册把门槛写成 10% 也骗不了门禁 —— 只要实测低于门槛就红。
    """
    report = _run(_registry([_one_gate(threshold=10.0)]))
    # 实测 50%(4 个文件里管了 2 个) —— 门槛 10% → 不算违规
    assert not report["violations"]
    assert report["gates"][0]["coverage_pct"] == 50.0
    assert report["gates"][0]["total"] == 4


# ====================================================================
# 2. fail-closed: 一律判红
# ====================================================================

def test_unknown_concern_is_red(fake_repo):
    """关注面类型不认识 → 红。"""
    registry = _registry([_one_gate(concern="NOT_A_KIND")])
    errors = gate.validate_registry(registry)
    assert any("不认识" in e for e in errors), f"未知关注面没被拦: {errors}"


def test_missing_gate_implementation_is_red(fake_repo):
    """门禁实现文件不存在 → 红(fail-closed: 读不到范围 = 视野未知)。"""
    report = _run(_registry([_one_gate(scope_path="scripts/nope.py")]))
    assert report["errors"], "门禁实现文件不存在却报绿"
    assert any("不存在" in e for e in report["errors"])


def test_unresolvable_constant_is_red(fake_repo):
    """门禁改了常量名 → 红(登记册没跟上)。"""
    gate_mod = os.path.join(fake_repo, "scripts", "gate.py")
    with open(gate_mod, "w", encoding="utf-8") as fh:
        fh.write("SCAN_DIRS = [\"agents\"]\n")
    report = _run(_registry([_one_gate()]))
    assert report["errors"], "常量读不到却报绿"
    assert any("找不到模块级常量" in e for e in report["errors"])


def test_empty_universe_is_red(fake_repo):
    """关注面算出来是空的 → 红(universe 定义写错了)。"""
    report = _run(_registry([_one_gate(concern="PY_WITH_ASSERTS")]))
    assert report["errors"], "关注面为空却报绿"
    assert any("空的" in e for e in report["errors"])


def test_duplicate_gate_id_is_red(fake_repo):
    """登记册里重复的门禁 id → 红。"""
    registry = _registry([_one_gate(), _one_gate()])
    errors = gate.validate_registry(registry)
    assert any("重复" in e for e in errors), f"重复 id 没被拦: {errors}"


def test_invalid_threshold_is_red(fake_repo):
    """门槛不在 (0, 100] → 红。"""
    registry = _registry([_one_gate(threshold=0.0)])
    errors = gate.validate_registry(registry)
    assert any("min_coverage_pct" in e for e in errors), f"非法门槛没被拦: {errors}"


def test_dangling_mirror_reference_is_red(fake_repo):
    """镜像声明引用了不存在的门禁 → 红。"""
    registry = _registry(
        [_one_gate()],
        [{"a": "fake-gate", "b": "ghost-gate", "why": "测试用"}],
    )
    errors = gate.validate_registry(registry)
    assert any("不存在的门禁" in e for e in errors), f"悬空引用没被拦: {errors}"


def test_mirror_without_why_is_red(fake_repo):
    """镜像声明没写 why → 红(没有理由的约束会被后来人删掉)。"""
    registry = _registry(
        [_one_gate()],
        [{"a": "fake-gate", "b": "fake-gate"}],
    )
    errors = gate.validate_registry(registry)
    assert any("why" in e for e in errors), f"缺 why 没被拦: {errors}"


# ====================================================================
# 3. 镜像校验: 注释里的承诺要能被验证
# ====================================================================

def test_mirror_mismatch_is_red(fake_repo):
    """两道门禁声明「完全一致」但范围不同 → 判红。

    这正是 iter #84 那句注释的风险:
    「扫描范围: 与 CI lint job 的 flake8 目标完全一致」——
    两处各写一遍, 哪天只改一边, 注释就变成谎言, 而谎言不会让 CI 变红。
    """
    gate_mod = os.path.join(fake_repo, "scripts", "gate.py")
    with open(gate_mod, "w", encoding="utf-8") as fh:
        fh.write(_gate_module(["agents", "scripts"]))

    other = dict(_one_gate())
    other["id"] = "other-gate"
    other["scope_source"] = {
        "kind": "module_const",
        "path": "scripts/gate.py",
        "names": ["SCAN_PATHS"],
    }
    registry = _registry(
        [other, _one_gate()],
        [{"a": "fake-gate", "b": "other-gate", "why": "测试用"}],
    )
    # 两道门禁读的是同一个常量 → 范围相同 → 镜像成立
    report = _run(registry)
    assert report["mirrors"][0]["equal"], "同一个常量读出来的范围应当一致"

    # 再换成不同的常量 → 镜像不成立 → 红
    other["scope_source"]["names"] = ["SCAN_DIRS"]
    gate_mod2 = os.path.join(fake_repo, "scripts", "gate.py")
    with open(gate_mod2, "w", encoding="utf-8") as fh:
        fh.write(_gate_module(["agents", "scripts"]) + "SCAN_DIRS = [\"agents\"]\n")
    report = _run(registry)
    assert not report["mirrors"][0]["equal"], "范围不同却判定镜像成立"
    assert report["errors"], "镜像不成立却报绿"


# ====================================================================
# 4. 不空转: 仓库自己的登记册必须是绿的
# ====================================================================

def test_repo_registry_is_green():
    """本仓库的真实登记册必须通过普查。

    如果这条红了, 说明有人改了门禁的扫描范围却没更新登记册 ——
    或者反过来, 登记册里的门槛被放松了。
    """
    registry = gate.load_registry()
    errors = gate.validate_registry(registry)
    assert not errors, f"登记册本身不合法: {errors}"

    result = gate.census(registry)
    report = gate.build_report(registry, result, errors)
    assert not report["violations"], f"本仓库的门禁视野不足: {report['violations']}"
    assert not report["errors"], f"本仓库的门禁视野普查报红: {report['errors']}"


def test_repo_has_at_least_one_gated_gate():
    """登记册里至少得有一道被卡视野的门禁 —— 否则这个门禁在空转。"""
    registry = gate.load_registry()
    assert len(registry.get("gates", [])) >= 1, "登记册里一道门禁都没有"


def test_census_script_itself_is_under_complexity_threshold():
    """普查脚本自己不许有超阈值的函数。

    它是「所有门禁的门禁」—— 如果它自己写得很复杂, 那它对别人
    的复杂度要求就成了空话。ADR-0020 的普查对象包括它自己。
    """
    import ast

    path = os.path.join(_SCRIPTS, "gate_vision_census.py")
    with open(path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)

    import subprocess

    proc = subprocess.run(
        [sys.executable, "-m", "flake8", "--select=C901", "--max-complexity=0",
         "--format=%(path)s\t%(row)d\t%(text)s", path],
        capture_output=True, text=True, cwd=_REPO_ROOT,
    )
    fat = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        text = parts[2]
        if "(" not in text or ")" not in text:
            continue
        value = int(text.rsplit("(", 1)[1].rstrip(")"))
        if value > 10:
            fat.append(f"{parts[0]}:{parts[1]} complexity={value}")

    assert not fat, (
        "门禁视野普查脚本自己有超阈值的函数 —— "
        f"它对别人的复杂度要求就成了空话: {fat}"
    )
