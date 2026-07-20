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

from debrief.cache import load_scrape, remove_row_image, reorder_row_images, update_row_images
from debrief.filenames import fast_facts_pdf_filename
from debrief.image_urls import canonical_image_url, fetch_image_bytes, needs_image_proxy
from debrief.models import DailyDebrief, ImageResult, ScrapeCache
from debrief.pdf import write_pdf
from debrief.research import fetch_topic_images
from debrief.scrape_day import fetch_scrape_preview, scrape_live_day
from debrief.generate_day import generate_debrief_from_scrape
from debrief.synthesize import synthesize_alternative_fact

_IMAGE_ROUTE = re.compile(r"^/api/images/([^/]+)/([^/]+)/?$")
_IMAGE_ORDER_ROUTE = re.compile(r"^/api/images/([^/]+)/([^/]+)/order/?$")
_IMAGE_PROXY_ROUTE = "/api/image-proxy"
_SCRAPE_TODAY_ROUTE = "/api/scrape/today"
_SCRAPE_PREVIEW_ROUTE = "/api/scrape/preview"
_GENERATE_DEBRIEF_ROUTE = "/api/debrief/generate"
_REGENERATE_FACT_ROUTE = "/api/facts/regenerate"


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
                from debrief.render import render_empty_preview

                html = render_empty_preview(date_iso=app.date_iso)
                self._serve_html(html)
                return

            if path.startswith("/api/"):
                if path == _IMAGE_PROXY_ROUTE:
                    self._serve_image_proxy(parsed.query)
                    return
                if path == _SCRAPE_PREVIEW_ROUTE:
                    try:
                        payload = app.fetch_scrape_preview()
                    except ValueError as exc:
                        self._json_response(400, {"error": str(exc)})
                        return
                    except Exception as exc:
                        self._json_response(500, {"error": str(exc)})
                        return
                    self._json_response(200, payload)
                    return
                self._json_response(404, {"error": "Not found"})
                return

            relative = path.lstrip("/")
            candidate = (output_dir / relative).resolve()
            if not str(candidate).startswith(str(output_dir)):
                self._json_response(403, {"error": "Forbidden"})
                return
            if relative == "debrief.pdf" and not candidate.is_file():
                try:
                    app.ensure_debrief_pdf()
                except FileNotFoundError:
                    self._json_response(404, {"error": "No debrief found"})
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
                    body = self._read_json_body()
                except (json.JSONDecodeError, ValueError) as exc:
                    self._json_response(400, {"error": str(exc)})
                    return
                row_labels = body.get("rows")
                if row_labels is not None:
                    if not isinstance(row_labels, list) or not all(
                        isinstance(label, str) and label.strip() for label in row_labels
                    ):
                        self._json_response(400, {"error": "rows must be a list of row labels"})
                        return
                    row_labels = [label.strip() for label in row_labels]
                generate = body.get("generate", False)
                if not isinstance(generate, bool):
                    self._json_response(400, {"error": "generate must be a boolean"})
                    return
                try:
                    payload = app.scrape_today(row_labels=row_labels, generate=generate)
                except RuntimeError as exc:
                    message = str(exc)
                    status = 409 if "already in progress" in message.lower() else 400
                    self._json_response(status, {"error": message})
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
                    message = str(exc)
                    status = 409 if "already in progress" in message.lower() else 400
                    self._json_response(status, {"error": message})
                    return
                except FileNotFoundError as exc:
                    self._json_response(404, {"error": str(exc)})
                    return
                except Exception as exc:
                    self._json_response(500, {"error": str(exc)})
                    return
                self._json_response(200, payload)
                return

            if path == _REGENERATE_FACT_ROUTE:
                try:
                    body = self._read_json_body()
                except (json.JSONDecodeError, ValueError) as exc:
                    self._json_response(400, {"error": str(exc)})
                    return

                row_label = body.get("row")
                existing_facts = body.get("existing_facts")
                if not isinstance(row_label, str) or not row_label.strip():
                    self._json_response(400, {"error": "row must be a non-empty string"})
                    return
                if (
                    not isinstance(existing_facts, list)
                    or len(existing_facts) > 6
                    or not all(
                        isinstance(fact, str) and len(fact) <= 62 for fact in existing_facts
                    )
                ):
                    self._json_response(
                        400,
                        {
                            "error": (
                                "existing_facts must be a list of up to six strings, "
                                "each no longer than 62 characters"
                            )
                        },
                    )
                    return

                try:
                    fact = app.regenerate_fact(row_label.strip(), existing_facts)
                except (FileNotFoundError, KeyError) as exc:
                    self._json_response(404, {"error": str(exc)})
                    return
                except ValueError as exc:
                    self._json_response(400, {"error": str(exc)})
                    return
                except Exception as exc:
                    self._json_response(500, {"error": str(exc)})
                    return

                self._json_response(200, {"row": row_label.strip(), "fact": fact})
                return

            order_match = _IMAGE_ORDER_ROUTE.match(path)
            if order_match:
                self._handle_image_order(app, order_match)
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

        def do_PUT(self) -> None:
            parsed = urlparse(self.path)
            path = _normalize_path(parsed.path)
            app = self._app()

            match = _IMAGE_ORDER_ROUTE.match(path)
            if not match:
                self._json_response(404, {"error": "Not found"})
                return

            self._handle_image_order(app, match)

        def _handle_image_order(
            self,
            app: DebriefServer,
            match: re.Match[str],
        ) -> None:
            date_iso, row_label = match.groups()
            if date_iso != app.date_iso:
                self._json_response(
                    404,
                    {
                        "error": f"Served date is {app.date_iso}, not {date_iso}. Reload the page.",
                    },
                )
                return

            try:
                body = self._read_json_body()
            except (json.JSONDecodeError, ValueError) as exc:
                self._json_response(400, {"error": str(exc)})
                return

            urls = body.get("urls")
            if not isinstance(urls, list) or not all(
                isinstance(url, str) and url.startswith(("http://", "https://")) for url in urls
            ):
                self._json_response(400, {"error": "urls must be a list of image URLs"})
                return

            try:
                images = app.reorder_row_images(row_label, urls)
            except KeyError as exc:
                self._json_response(404, {"error": str(exc)})
                return
            except ValueError as exc:
                self._json_response(400, {"error": str(exc)})
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
            image_url = canonical_image_url(unquote(params.get("url", [""])[0]).strip())
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

        def _read_json_body(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            if not raw:
                return {}
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
            return payload

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
            if path.name == "debrief.pdf":
                filename = fast_facts_pdf_filename(self._app().date_iso)
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _serve_html(self, html: str) -> None:
            data = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
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
        self._latest_scrape: ScrapeCache | None = None

    def refresh_preview(self) -> None:
        return None

    def ensure_debrief_pdf(self) -> Path:
        pdf_path = self.output_dir / "debrief.pdf"
        if pdf_path.exists():
            return pdf_path

        json_path = self.output_dir / "debrief.json"
        if not json_path.exists():
            raise FileNotFoundError(f"No debrief found at {json_path}")

        daily = DailyDebrief.model_validate(json.loads(json_path.read_text(encoding="utf-8")))
        return write_pdf(daily, pdf_path)

    def regenerate_fact(self, row_label: str, existing_facts: list[str]) -> str:
        json_path = self.output_dir / "debrief.json"
        if not json_path.exists():
            raise FileNotFoundError(f"No generated rundown found at {json_path}")

        daily = DailyDebrief.model_validate(json.loads(json_path.read_text(encoding="utf-8")))
        row = next((entry for entry in daily.rows if entry.row == row_label), None)
        if row is None:
            raise KeyError(f"Unknown rundown row {row_label!r}")

        return synthesize_alternative_fact(
            row,
            existing_facts,
            model=self.model,
            reasoning_effort=self.reasoning_effort,
        )

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
        self.refresh_preview()
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
        self.refresh_preview()
        return row.research.images

    def reorder_row_images(self, row_label: str, image_urls: list[str]) -> list[ImageResult]:
        images = reorder_row_images(
            self.cache_dir,
            self.date_iso,
            row_label,
            image_urls,
        )
        self.refresh_preview()
        return images

    def fetch_scrape_preview(self) -> dict:
        return fetch_scrape_preview()

    def scrape_today(
        self,
        *,
        row_labels: list[str] | None = None,
        generate: bool = False,
    ) -> dict:
        with self._scrape_lock:
            if self._scrape_in_progress:
                raise RuntimeError("A scrape is already in progress.")
            self._scrape_in_progress = True

        try:
            # Persist to disk so /api/debrief/generate can recover on a fresh
            # serverless instance (in-memory _latest_scrape alone is not enough).
            result = scrape_live_day(
                cache_base=self.cache_dir,
                output_base=self.output_base,
                row_labels=row_labels,
                skip_search=self.skip_search,
                skip_tweets=self.skip_tweets,
                search_provider=self.search_provider,
                model=self.model,
                reasoning_effort=self.reasoning_effort,
                save_cache=True,
            )
            self.date_iso = result.date_iso
            self.output_dir = self.output_base / result.date_iso
            self._latest_scrape = result.scrape
            payload = {
                "date": result.date,
                "date_iso": result.date_iso,
                "rows": len(result.scrape.rows),
                "researched_rows": result.researched_rows,
                "posts": result.scrape.post_count,
            }
            # Run GPT in the same request on Vercel so generate cannot land on
            # a different cold instance with an empty /tmp and no scrape state.
            if generate:
                generated = self.generate_debrief()
                payload["generated_rows"] = generated["rows"]
            return payload
        finally:
            with self._scrape_lock:
                self._scrape_in_progress = False

    def generate_debrief(self) -> dict:
        with self._debrief_lock:
            if self._debrief_in_progress:
                raise RuntimeError("Debrief generation is already in progress.")
            self._debrief_in_progress = True

        try:
            scrape = self._latest_scrape
            if scrape is None:
                from debrief.cache import scrape_exists

                if scrape_exists(self.cache_dir, self.date_iso):
                    scrape = load_scrape(self.cache_dir, self.date_iso)
                    self._latest_scrape = scrape
                else:
                    raise RuntimeError("Choose sections before generating a rundown.")
            result = generate_debrief_from_scrape(
                scrape=scrape,
                output_base=self.output_base,
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

    print(f"Serving rundown UI at {url} ({date_iso})")
    print("Use Choose sections to research selected rows and generate the rundown.")
    print("Press Ctrl+C to stop.")

    if open_browser:
        webbrowser.open(url)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
