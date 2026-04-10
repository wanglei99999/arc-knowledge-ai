from __future__ import annotations

from fastapi import Header, HTTPException, status


async def require_tenant(
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
) -> str:
    """
    FastAPI Depends：从请求头提取 tenant_id。

    Phase 0：只做非空验证。
    Phase 3：接入 JWT 验证 + 租户存在性检查。
    """
    if not x_tenant_id.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-Tenant-Id header is required",
        )
    return x_tenant_id
