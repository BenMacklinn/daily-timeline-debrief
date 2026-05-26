from debrief.runtime import build_debrief_server
from debrief.server import create_http_handler

_Handler = create_http_handler(build_debrief_server)


class handler(_Handler):
    pass
