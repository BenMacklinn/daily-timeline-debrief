from __future__ import annotations

import json
import mimetypes
import re
import threading
import webbrowser
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from debrief.cache import load_scrape, remove_row_image, scrape_exists, update_row_images
from debrief.image_urls import fetch_image_bytes, needs_image_proxy
from debrief.models import ImageResult
from debrief.research import fetch_topic_images
from debrief.scrape_day import scrape_live_day
from debrief.generate_day import generate_debrief_from_cache

_IMAGE_ROUTE = re.compile(r"^/api/images/([^/]+)/([^/]+)/?$")
_IMAGE_PROXY_ROUTE = "/api/image-proxy"
_SCRAPE_TODAY_ROUTE = "/api/scrape/today"
_GENERATE_DEBRIEF_ROUTE = "/api/debrief/generate"


def _normalize_path(path: str) -> str:
    path = unquote(path)
    if path != "/" and path.endswith("/"):
        return path.rstrip("/")
    return path


def create_http_handler(
    get_app: Callable[[], DebriefServer],
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "DailyTimelineDebrief/0.1"

        def _app(self) -> DebriefServer:
            bound = getattr(self.server, "app", None)
            if bound is not None:
                return bound
            return get_app()

        def log_message(self, format: str, *args) -> None:
            print(f"[serve] {self.address_string()} {format % args}")

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = _normalize_path(parsed.path)
            app = self._app()
            output_dir = app.output_dir.resolve()

            if path in {"", "/"}:
                preview = output_dir / "preview.html"
                if preview.is_file():
                    self._serve_file(preview)
                    return
                self._json_response(
                    404,
                    {"error": "No preview for this date. Click Scrape today."},
                )
                return

            if path.startswith("/api/"):
                if path == _IMAGE_PROXY_ROUTE:
                    self._serve_image_proxy(parsed.query)
                    return
                self._json_response(404, {"error": "Not found"})
                return

            relative = path.lstrip("/")
            candidate = (output_dir / relative).resolve()
            if not str(candidate).startswith(str(output_dir)):
                self._json_response(403, {"error": "Forbidden"})
                return
            if candidate.is_file():
                self._serve_file(candidate)
                return

            self._json_response(404, {"error": "Not found"})

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            path = _normalize_path(parsed.path)
            app = self._app()

            if path == _SCRAPE_TODAY_ROUTE:
                try:
                    payload = app.scrape_today()
                except RuntimeError as exc:
                    self._json_response(409, {"error": str(exc)})
                    return
                except ValueError as exc:
                    self._json_response(400, {"error": str(exc)})
                    return
                except Exception as exc:
                    self._json_response(500, {"error": str(exc)})
                    return
                self._json_response(200, payload)
                return

            if path == _GENERATE_DEBRIEF_ROUTE:
                try:
                    payload = app.generate_debrief()
                except RuntimeError as exc:
                    self._json_response(409, {"error": str(exc)})
                    return
                except FileNotFoundError as exc:
                    self._json_response(404, {"error": str(exc)})
                    return
                except Exception as exc:
                    self._json_response(500, {"error": str(exc)})
                    return
                self._json_response(200, payload)
                return

            match = _IMAGE_ROUTE.match(path)
            if not match:
                self._json_response(404, {"error": "Not found"})
                return

            date_iso, row_label = match.groups()
            if date_iso != app.date_iso:
                self._json_response(
                    404,
                    {"error": f"Served date is {app.date_iso}, not {date_iso}. Reload the page."},
                )
                return

            try:
                images = app.fetch_and_store_images(row_label)
            except KeyError as exc:
                self._json_response(404, {"error": str(exc)})
                return
            except Exception as exc:
                self._json_response(500, {"error": str(exc)})
                return

            self._json_response(
                200,
                {
                    "row": row_label,
                    "images": [image.model_dump(mode="json") for image in images],
                },
            )

        def do_DELETE(self) -> None:
            parsed = urlparse(self.path)
            path = _normalize_path(parsed.path)
            app = self._app()

            match = _IMAGE_ROUTE.match(path)
            if not match:
                self._json_response(404, {"error": "Not found"})
                return

            date_iso, row_label = match.groups()
            if date_iso != app.date_iso:
                self._json_response(
                    404,
                    {
                        "error": f"Served date is {app.date_iso}, not {date_iso}. Reload the page.",
                    },
                )
                return

            params = parse_qs(parsed.query)
            image_url = unquote(params.get("url", [""])[0]).strip()
            if not image_url.startswith(("http://", "https://")):
                self._json_response(400, {"error": "Missing or invalid url"})
                return

            try:
                images = app.delete_row_image(row_label, image_url)
            except KeyError as exc:
                self._json_response(404, {"error": str(exc)})
                return
            except Exception as exc:
                self._json_response(500, {"error": str(exc)})
                return

            self._json_response(
                200,
                {
                    "row": row_label,
                    "images": [image.model_dump(mode="json") for image in images],
                },
            )

        def _serve_image_proxy(self, query: str) -> None:
            params = parse_qs(query)
            url = unquote(params.get("url", [""])[0]).strip()
            if not url.startswith(("http://", "https://")):
                self._json_response(400, {"error": "Missing or invalid url"})
                return
            if not needs_image_proxy(url):
                self._json_response(400, {"error": "This image URL does not require proxying"})
                return

            fetched = fetch_image_bytes(url)
            if fetched is None:
                self._json_response(502, {"error": "Could not fetch image"})
                return

            data, content_type = fetched
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(data)

        def _serve_file(self, path: Path) -> None:
            content_type, _ = mimetypes.guess_type(str(path))
            content_type = content_type or "application/octet-stream"
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _json_response(self, status: int, payload: dict) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


class DebriefServer:
    def __init__(
        self,
        *,
        date_iso: str,
        output_dir: Path,
        cache_dir: Path,
        output_base: Path,
        skip_search: bool = False,
        skip_tweets: bool = False,
        search_provider: str = "tavily",
        model: str = "gpt-5.5",
        reasoning_effort: str = "low",
        search_fallback: bool = False,
    ) -> None:
        self.date_iso = date_iso
        self.output_dir = output_dir
        self.cache_dir = cache_dir
        self.output_base = output_base
        self.skip_search = skip_search
        self.skip_tweets = skip_tweets
        self.search_provider = search_provider
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.search_fallback = search_fallback
        self._scrape_lock = threading.Lock()
        self._scrape_in_progress = False
        self._debrief_lock = threading.Lock()
        self._debrief_in_progress = False

    def fetch_and_store_images(self, row_label: str) -> list[ImageResult]:
        scrape = load_scrape(self.cache_dir, self.date_iso)
        row = next(
            (entry for entry in scrape.rows if entry.group.label == row_label),
            None,
        )
        if row is None:
            raise KeyError(f"Unknown row {row_label!r}")

        bundle = row.research
        images = fetch_topic_images(
            topic_summary=bundle.topic_summary,
            image_queries=bundle.image_queries_used,
            search_queries=bundle.search_queries_used,
            key_entities=bundle.key_entities,
            tweets=bundle.tweets,
            posts=row.group.posts,
            row_tag=row.group.tag,
        )
        update_row_images(self.cache_dir, self.date_iso, row_label, images)
        return images

    def delete_row_image(self, row_label: str, image_url: str) -> list[ImageResult]:
        scrape = remove_row_image(
            self.cache_dir,
            self.date_iso,
            row_label,
            image_url,
        )
        row = next(
            (entry for entry in scrape.rows if entry.group.label == row_label),
            None,
        )
        if row is None:
            raise KeyError(f"Unknown row {row_label!r}")
        return row.research.images

    def scrape_today(self) -> dict:
        with self._scrape_lock:
            if self._scrape_in_progress:
                raise RuntimeError("A scrape is already in progress.")
            self._scrape_in_progress = True

        try:
            result = scrape_live_day(
                cache_base=self.cache_dir,
                output_base=self.output_base,
                skip_search=self.skip_search,
                skip_tweets=self.skip_tweets,
                search_provider=self.search_provider,
                model=self.model,
                reasoning_effort=self.reasoning_effort,
            )
        finally:
            with self._scrape_lock:
                self._scrape_in_progress = False

        self.date_iso = result.date_iso
        self.output_dir = self.output_base / result.date_iso
        return {
            "date": result.date,
            "date_iso": result.date_iso,
            "rows": len(result.scrape.rows),
            "posts": result.scrape.post_count,
        }

    def generate_debrief(self) -> dict:
        with self._debrief_lock:
            if self._debrief_in_progress:
                raise RuntimeError("Debrief generation is already in progress.")
            self._debrief_in_progress = True

        try:
            result = generate_debrief_from_cache(
                cache_base=self.cache_dir,
                output_base=self.output_base,
                date_iso=self.date_iso,
                model=self.model,
                reasoning_effort=self.reasoning_effort,
                search_fallback=self.search_fallback,
            )
        finally:
            with self._debrief_lock:
                self._debrief_in_progress = False

        return {
            "date": result.date,
            "date_iso": result.date_iso,
            "rows": result.row_count,
        }

    def make_handler(self) -> type[BaseHTTPRequestHandler]:
        app = self
        return create_http_handler(lambda: app)


def run_server(
    *,
    date_iso: str,
    output_dir: Path,
    cache_dir: Path,
    output_base: Path,
    skip_search: bool = False,
    skip_tweets: bool = False,
    search_provider: str = "tavily",
    model: str = "gpt-5.5",
    reasoning_effort: str = "low",
    search_fallback: bool = False,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    if not scrape_exists(cache_dir, date_iso):
        raise FileNotFoundError(f"No cached scrape for {date_iso}")

    preview_path = output_dir / "preview.html"
    if not preview_path.exists():
        from debrief.render import write_preview

        scrape = load_scrape(cache_dir, date_iso)
        has_debrief = (output_dir / "debrief.html").exists()
        write_preview(scrape, output_dir, has_debrief=has_debrief)

    app = DebriefServer(
        date_iso=date_iso,
        output_dir=output_dir,
        cache_dir=cache_dir,
        output_base=output_base,
        skip_search=skip_search,
        skip_tweets=skip_tweets,
        search_provider=search_provider,
        model=model,
        reasoning_effort=reasoning_effort,
        search_fallback=search_fallback,
    )
    handler = app.make_handler()
    httpd = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}/"

    print(f"Serving research UI at {url} ({date_iso})")
    print("Use Scrape today or Find images from the Research tab.")
    print("Press Ctrl+C to stop.")

    if open_browser:
        webbrowser.open(url)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
