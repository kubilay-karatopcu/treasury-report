from flask import Blueprint

presentations_bp = Blueprint(
    "presentations",
    __name__,
    template_folder="templates",
    static_folder="static",
)

from presentations import routes  # noqa: E402, F401
from presentations import routes_blocks  # noqa: E402, F401
from presentations import routes_concepts  # noqa: E402, F401
from presentations import routes_scope  # noqa: E402, F401  (Phase 8.a — temporary)
from presentations.catalog import api as _catalog_api  # noqa: E402, F401  (Phase 9.a)
from presentations import routes_kesif  # noqa: E402, F401  (Phase 9.a)
