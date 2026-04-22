"""Django Ninja API entry point.

Each app contributes a Router; the target Claude will flesh them out to
reach functional parity with the FastAPI backend under ``api/``. The
NinjaAPI instance is versioned so Ninja emits ``/api/v1/openapi.json``
as the canonical contract for the Angular client.
"""

from __future__ import annotations

from ninja import NinjaAPI

from foxrunner.auth import JWTAuth
from foxrunner.exception_handlers import install_handlers

api = NinjaAPI(
    title="FoxRunner API",
    version="1.0.0",
    description="API de pilotage du scheduler et des scenarios FoxRunner.",
    auth=JWTAuth(),
    urls_namespace="foxrunner_api_v1",
    openapi_url="/openapi.json",
    docs_url="/docs",
)

install_handlers(api)


# Register per-app routers. The routers are defined in each app's ``api.py``
# module and remain mostly empty until migration phases start filling them.
from accounts.api import router as accounts_router  # noqa: E402
from catalog.api import router as catalog_router  # noqa: E402
from ops.admin_api import router as admin_router  # noqa: E402
from ops.api import router as ops_router  # noqa: E402
from ops.graph_api import router as graph_router  # noqa: E402

api.add_router("", accounts_router)
api.add_router("", catalog_router)
api.add_router("", ops_router)
api.add_router("", admin_router)
api.add_router("", graph_router)
