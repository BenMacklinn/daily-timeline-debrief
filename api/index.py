from debrief.runtime import build_debrief_server
from debrief.server import create_http_handler


class handler(create_http_handler(build_debrief_server)):
    """Vercel Python entrypoint."""
