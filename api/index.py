from http.server import BaseHTTPRequestHandler

from debrief.runtime import build_debrief_server
from debrief.server import create_http_handler

_impl = create_http_handler(build_debrief_server)


class handler(BaseHTTPRequestHandler):
    server_version = "DailyTimelineDebrief/0.1"

    def log_message(self, format: str, *args) -> None:
        _impl.log_message(self, format, *args)

    def do_GET(self) -> None:
        _impl.do_GET(self)

    def do_POST(self) -> None:
        _impl.do_POST(self)

    def do_DELETE(self) -> None:
        _impl.do_DELETE(self)
