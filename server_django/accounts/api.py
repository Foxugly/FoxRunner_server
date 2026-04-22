"""Ninja router for account-scoped endpoints.

Populated during migration phase 2 (users/me) and phase 3 (user-owned
catalog views). The login/logout wrappers matching the existing FastAPI
contract also live here.
"""

from __future__ import annotations

from ninja import Router

router = Router(tags=["users"])


# --- placeholders ------------------------------------------------------
# The target Claude replaces these with the real implementations. Keeping
# stubs here so the NinjaAPI instance boots cleanly.


@router.get("/users/me", include_in_schema=False)
def users_me_placeholder(request):
    return {"detail": "not_implemented"}
