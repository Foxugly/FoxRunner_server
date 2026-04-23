"""Django Ninja API entry point.

Per-app routers are defined in each app's ``api.py`` module and added
below. The NinjaAPI instance is versioned so Ninja emits
``/api/v1/openapi.json`` as the canonical contract for the Angular
client; ``FoxrunnerNinjaAPI`` overrides ``get_openapi_schema`` to
augment the live spec with the project-wide ``ErrorOut`` envelope (see
``foxrunner.openapi_extras``).
"""

from __future__ import annotations

import json
from typing import Any

from ninja import NinjaAPI

from foxrunner.auth import JWTAuth
from foxrunner.exception_handlers import install_handlers
from foxrunner.openapi_extras import attach_default_error_response, ensure_error_schema


class FoxrunnerNinjaAPI(NinjaAPI):
    """Ninja API that augments the live OpenAPI with the ErrorOut envelope.

    Subclassing keeps the augmentation in one place and makes the runtime
    spec at ``/api/v1/openapi.json`` match the file dump committed at the
    repo root. Without this, ``ErrorOut`` would only appear in the
    committed ``openapi.json`` (post-processed by
    ``scripts/export_openapi.py``) and the frontend running ``gen:api``
    against the live URL would see a stale spec.
    """

    def get_openapi_schema(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raw = super().get_openapi_schema(*args, **kwargs)
        # Round-trip through JSON so setdefault / mutation works on the
        # OpenAPISchema (Pydantic) result.
        spec = json.loads(json.dumps(raw, default=str))
        ensure_error_schema(spec)
        attach_default_error_response(spec)
        return spec


api = FoxrunnerNinjaAPI(
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
