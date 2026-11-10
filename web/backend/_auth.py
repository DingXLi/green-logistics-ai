"""Green Logistics AI —— admin 鉴权（iter #83 从 main.py 外置）。

为什么外置
----------
``require_admin`` 这个 FastAPI dependency 同时被 main.py 的 admin 端点和
``routers/persistence.py`` 里的 ``/api/persistence/forecast-method-prefs``
端点使用。留在 main.py 里的话，router 就得 ``from web.backend.main import
require_admin`` —— 而 main.py 又要 import router → 循环依赖。

外置之后依赖方向是单向的：

    main.py ──▶ _auth.py
    routers/persistence.py ──▶ _auth.py

对外行为不变：``from web.backend.main import require_admin`` 仍然可用
（main.py 转手 import 回来），tests/test_admin_auth.py 照常通过。
"""

import os
from typing import Optional

from fastapi import Header, HTTPException


def _get_admin_token() -> str:
    """Return the configured admin token (fresh read, 支持运行时修改 env var)."""
    return os.environ.get("GL_ADMIN_TOKEN", "")


def _extract_admin_token(authorization: Optional[str], x_admin_token: Optional[str]) -> Optional[str]:
    """
    从 request headers 提取 admin token。

    支持两种方式 (任一即可):
    - Authorization: Bearer <token>
    - X-Admin-Token: <token>

    返回 token 字符串 (不含 "Bearer " 前缀), 或 None (没提供)。
    """
    if authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
        # 允许直接传 token (不强制 Bearer 前缀, 方便调试)
        return authorization.strip()
    if x_admin_token:
        return x_admin_token.strip()
    return None


def _check_admin_token(authorization: Optional[str], x_admin_token: Optional[str]) -> bool:
    """
    验证 admin token 是否匹配 GL_ADMIN_TOKEN。

    Returns:
        True - 访问被允许
        False - 拒绝 (401)
    """
    configured = _get_admin_token()
    if not configured:
        # 没配置 token → 不启用 auth (dev 模式)
        return True
    provided = _extract_admin_token(authorization, x_admin_token)
    if not provided:
        return False
    # 用 secrets.compare_digest 避免 timing attack
    import secrets
    try:
        return secrets.compare_digest(provided, configured)
    except (TypeError, ValueError):
        return False


async def require_admin(
    authorization: Optional[str] = Header(default=None),
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
) -> None:
    """
    FastAPI dependency: 验证 admin token。如果 GL_ADMIN_TOKEN 未设置则跳过。

    用法: ``async def endpoint(_: None = Depends(require_admin)):``
    拒绝时抛 HTTPException(401, WWW-Authenticate: Bearer)。
    """
    if not _check_admin_token(authorization, x_admin_token):
        raise HTTPException(
            status_code=401,
            detail=(
                "Admin token required (set GL_ADMIN_TOKEN and pass via "
                "Authorization: Bearer or X-Admin-Token header)"
            ),
            headers={"WWW-Authenticate": "Bearer"},
        )
