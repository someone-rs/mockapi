"""mockapi — fake REST API server from a JSON file."""

from .core import Route, load_routes, match_route, render_body
from .server import serve

__version__ = "1.0.0"
__all__ = ["Route", "load_routes", "match_route", "render_body", "serve", "__version__"]
