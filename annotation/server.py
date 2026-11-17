"""HTTP front-end for the annotation kernel (stdlib only).

The browser is the vector editing window; every mutation goes through the
kernel, and kernel failures are returned to the page and kept in the
kernel's error log.
"""
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from .kernel import AnnotationKernel

ROUTES = []


def route(method, pattern):
    rx = re.compile("^" + pattern + "$")

    def deco(fn):
        ROUTES.append((method, rx, fn))
        return fn
    return deco


class KernelApp:
    def __init__(self, kernel, webroot):
        self.kernel = kernel
        self.webroot = webroot

    @route("GET", r"/api/state")
    def state(self, q, body):
        return 200, "application/json", json.dumps(
            self.kernel.state(), ensure_ascii=False).encode()

    @route("GET", r"/api/render\.png")
    def render_png(self, q, body):
        return 200, "image/png", self.kernel.render_png()

    @route("GET", r"/api/glyph\.png")
    def glyph_png(self, q, body):
        try:
            ann, seq = int(q["ann"][0]), int(q["seq"][0])
        except (KeyError, ValueError, IndexError):
            return 400, "application/json", b'{"error":"want ann and seq"}'
        png, info = self.kernel.glyph_coverage(ann, seq)
        if png is None:
            return 404, "application/json", json.dumps(
                {"error": "not found", "detail": info}).encode()
        return 200, "image/png", png

    @route("POST", r"/api/annotation")
    def add_annotation(self, q, body):
        r = self.kernel.add_annotation(
            body.get("x", 100), body.get("y", 100), body.get("size", 12),
            body.get("text", ""), body.get("color", "#202020"))
        return self._reply(r)

    @route("POST", r"/api/annotation/move")
    def move(self, q, body):
        r = self.kernel.move_annotation(body.get("id"), body.get("x"),
                                        body.get("y"))
        return self._reply(r)

    @route("POST", r"/api/annotation/update")
    def update(self, q, body):
        r = self.kernel.update_annotation(
            body.get("id"), text=body.get("text"),
            size_pt=body.get("size"), color=body.get("color"))
        return self._reply(r)

    @route("POST", r"/api/annotation/delete")
    def delete(self, q, body):
        r = self.kernel.delete_annotation(body.get("id"))
        return self._reply(r)

    @route("POST", r"/api/entity")
    def entity(self, q, body):
        r = self.kernel.add_entity(body.get("kind"), body.get("coords", []),
                                   body.get("color", "#808080"))
        return self._reply(r)

    @route("POST", r"/api/settings")
    def settings(self, q, body):
        out = {}
        if "dpr" in body:
            out["dpr"] = self.kernel.set_dpr(body["dpr"])
        if "hinting" in body:
            out["hinting"] = self.kernel.set_hinting(body["hinting"])
        return 200, "application/json", json.dumps(out).encode()

    def _reply(self, kernel_result):
        # kernel returns None on failure; the failure itself is already in
        # the kernel error log, which /api/state feeds back to the page.
        if kernel_result is None:
            return 422, "application/json", json.dumps(
                {"ok": False, "errors": self.kernel.errors[-3:]},
                ensure_ascii=False).encode()
        return 200, "application/json", json.dumps(
            {"ok": True, "result": kernel_result},
            ensure_ascii=False).encode()


STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/editor.js": ("editor.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8")}


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, code, ctype, payload):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def _dispatch(self, method):
            parsed = urlparse(self.path)
            path, query = parsed.path, parse_qs(parsed.query)
            if method == "GET" and path in STATIC:
                name, ctype = STATIC[path]
                try:
                    with open(app.webroot + "/" + name, "rb") as fh:
                        return self._send(200, ctype, fh.read())
                except OSError:
                    return self._send(404, "text/plain", b"missing asset")
            body = {}
            if method == "POST":
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b"{}"
                try:
                    body = json.loads(raw.decode() or "{}")
                except (ValueError, UnicodeDecodeError):
                    return self._send(400, "application/json",
                                      b'{"error":"bad json"}')
            for m, rx, fn in ROUTES:
                if m == method and rx.match(path):
                    try:
                        code, ctype, payload = fn(app, query, body)
                    except Exception as e:  # kernel failure -> feedback log
                        app.kernel._log_error("http %s" % path, e)
                        code, ctype, payload = 500, "application/json", \
                            json.dumps({"error": str(e)}).encode()
                    return self._send(code, ctype, payload)
            self._send(404, "application/json", b'{"error":"no route"}')

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

    return Handler


def serve(kernel, webroot, host, port):
    app = KernelApp(kernel, webroot)
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    return httpd
