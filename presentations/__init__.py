from flask import Blueprint

presentations_bp = Blueprint(
    "presentations",
    __name__,
    template_folder="templates",
    static_folder="static",
)

from presentations import routes  # noqa: E402, F401
from presentations import routes_blocks  # noqa: E402, F401
