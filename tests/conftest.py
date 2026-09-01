"""
Test-time configuration / fixtures (iter #16)

Why this file exists:
- 项目不是 installable package, tests 走 ``from optimization...`` ``from data...``
  等 import 会撞 ModuleNotFoundError。
- GitHub Actions 在 green-logistics-ai/ 根目录跑 pytest,
  CWD 自动让 ``optimization``/``data``/``agents``/``web`` 处于 sys.path。
- 本地开发用 venv 时 CWD 已经在根目录, 但有些测试 runner 不会自动把根目录
  放进 sys.path, 跑 ``pytest tests/`` 就会 ``ModuleNotFoundError: No module named
  'optimization'``。
- 这个 conftest.py 在 collection 时把项目根目录 (``Path(__file__).parent.parent``)
  推进 sys.path, 同时显式 expose ``agents / optimization / data / synthetic / web``
  作为可 import package 符号, 解决 8 个 test_* 文件的 collection error。

设计原则:
- 不创建任何 fixture (避免和已有测试冲突)
- 不修改任何环境变量
- 一次插桩, 永久生效
- 不影响测试并行 / 测试顺序
"""

from __future__ import annotations

import sys
from pathlib import Path

# 项目根目录 = tests/ 的 parent
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 把项目根目录放 sys.path 最前面 (覆盖任何 install 同名 module)
_ROOT_STR = str(_PROJECT_ROOT)
if _ROOT_STR not in sys.path:
    sys.path.insert(0, _ROOT_STR)

# 同时插桩子 package, 防止某些 runner 把根目录当 namespace package 而 __init__.py 缺失
# (实际项目根目录没有 __init__.py, 但子目录大多有 — 见 optimization/__init__.py 等)
for _sub in ("agents", "optimization", "data", "synthetic", "web"):
    _sub_path = _PROJECT_ROOT / _sub
    if _sub_path.is_dir() and str(_sub_path) not in sys.path:
        sys.path.insert(0, str(_sub_path))


# ============================================
# iter #33: Auto-cleanup fixture for module-level WebSocketBroadcaster singleton
# ============================================
# 问题: ws_broadcaster 是 module-level singleton (web/backend/main.py),
# 多个 WS test 文件共享同一实例。如果某个 test 只 connect() 不 disconnect(),
# _clients set 会积累, 后续 test 的 stats() 计数错误。
#
# 解决方案: 在每个 WS 相关 test 前自动清空 _clients + _client_meta + reset_stats()。
# 只对 WS 相关文件生效 (性能优化, 避免每个 test 都加载 web.backend.main)。
#
# 注: reset_stats() 本身不清 _clients 是生产设计 (不应丢活动连接) — 这里用
# 直接访问 _clients.clear() 是 test-only 的 workaround。
def pytest_collection_modifyitems(config, items):
    """收集阶段不需要做什么; 真正的 fixture 定义在下面。"""
    pass


import pytest


@pytest.fixture(autouse=True)
def _reset_ws_broadcaster_singleton(request):
    """
    自动 fixture: 对所有 WS 相关测试文件, 在每个 test 前后重置 broadcaster。
    避免 module-level singleton 状态泄漏。
    """
    # 只对 WS 相关测试文件生效
    if "test_ws" not in str(request.fspath):
        yield
        return

    # 尝试导入 broadcaster; 如果 web.backend 未安装则跳过
    try:
        from web.backend.main import ws_broadcaster
    except (ImportError, Exception):
        yield
        return

    # Setup: 完全清空 (含 live _clients)
    ws_broadcaster._clients.clear()
    ws_broadcaster._client_meta.clear()
    ws_broadcaster.reset_stats()

    yield

    # Teardown: 也清空, 避免影响后续测试文件
    ws_broadcaster._clients.clear()
    ws_broadcaster._client_meta.clear()
    ws_broadcaster.reset_stats()