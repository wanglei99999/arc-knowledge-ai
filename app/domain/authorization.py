from dataclasses import dataclass


@dataclass(frozen=True)
class UserContext:
    tenant_id: str
    user_id: str
    membership_id: str = ""
    tenant_role: str = "MEMBER"
    email: str = ""
    tenant_name: str = ""


class AuthorizationError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


ROLE_RANK = {"VIEWER": 1, "EDITOR": 2, "MANAGER": 3}
ROLE_CAPABILITIES = {
    "VIEWER": frozenset({"space.read"}),
    "EDITOR": frozenset(
        {"space.read", "document.upload", "document.delete_own", "document.retry_own"}
    ),
    "MANAGER": frozenset(
        {
            "space.read",
            "space.manage",
            "document.upload",
            "document.delete_own",
            "document.retry_own",
            "document.delete_any",
            "document.retry_any",
        }
    ),
}


def permits(role: str, action: str, membership_id: str, owner: str | None = None) -> bool:
    capabilities = ROLE_CAPABILITIES.get(role, frozenset())
    if action in ("document.delete", "document.retry"):
        return action + "_any" in capabilities or (
            action + "_own" in capabilities and bool(owner) and str(owner) == membership_id
        )
    return action in capabilities


def decorate_access(row: dict) -> dict:
    sources = row["permission_sources"]
    role = max((s["role"] for s in sources), key=lambda r: ROLE_RANK.get(r, 0))
    return {**row, "effective_role": role, "capabilities": sorted(ROLE_CAPABILITIES[role])}
