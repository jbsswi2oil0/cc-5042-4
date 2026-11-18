"""标准库 HTTP 服务：把浏览器控件操作路由到 AnnotationApp 内核。"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .webapp import STATIC_DIR, AnnotationApp, EngineError


class _Handler(BaseHTTPRequestHandler):
    app: AnnotationApp = None  # 由 make_server 注入

    def log_message(self, fmt, *args):  # 静默默认访问日志
        pass

    # ---- GET: 静态页与状态/渲染 ----------------------------------------
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._send_file("index.html", "text/html; charset=utf-8")
        if path == "/static/app.js":
            return self._send_file("app.js", "application/javascript; charset=utf-8")
        if path == "/static/style.css":
            return self._send_file("style.css", "text/css; charset=utf-8")
        if path == "/api/state":
            return self._send_json(self.app.state())
        if path == "/api/render":
            try:
                return self._send_json(self.app.render())
            except EngineError as exc:
                return self._send_error(str(exc))
        self.send_error(404)

    # ---- POST: 控件操作 -------------------------------------------------
    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._send_error("请求体不是合法 JSON")
        try:
            result = self._dispatch(self.path, payload)
        except EngineError as exc:
            return self._send_error(str(exc))
        except (KeyError, TypeError, ValueError) as exc:
            return self._send_error(f"参数错误: {exc}")
        self._send_json(result or {"ok": True})

    def _dispatch(self, path: str, p: dict) -> dict:
        app = self.app
        if path == "/api/settings":
            app.update_settings(p)
        elif path == "/api/move":
            app.move_annotation(int(p["index"]),
                                float(p["dx_lu"]), float(p["dy_lu"]))
        elif path == "/api/position":
            app.set_position(int(p["index"]),
                             float(p["x_lu"]), float(p["y_lu"]))
        elif path == "/api/annotation":
            app.update_annotation(int(p["index"]), p)
        elif path == "/api/add":
            app.add_annotation(p["text"], float(p["x_lu"]),
                               float(p["y_lu"]), float(p.get("size_lu", 12)))
        elif path == "/api/delete":
            app.delete_annotation(int(p["index"]))
        elif path == "/api/select":
            app.select(int(p["index"]))
        elif path == "/api/glyph":
            return app.render_glyph(int(p["codepoint"]), float(p["size_lu"]))
        else:
            self.send_error(404)
            return {}
        return {"ok": True, "state": app.state(), "render": app.render()}

    # ---- 工具 -----------------------------------------------------------
    def _send_file(self, name: str, content_type: str) -> None:
        full = os.path.join(STATIC_DIR, name)
        if not os.path.isfile(full):
            self.send_error(404)
            return
        with open(full, "rb") as fh:
            data = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_error(self, message: str) -> None:
        self.send_response(400)
        data = json.dumps({"error": message}, ensure_ascii=False).encode("utf-8")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def make_server(host: str, port: int, app: AnnotationApp) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (_Handler,), {"app": app})
    return ThreadingHTTPServer((host, port), handler)


def main(host: str = "127.0.0.1", port: int = 8765,
         font_path: str | None = None, cache_dir: str | None = None) -> None:
    from .webapp import DEFAULT_FONT
    app = AnnotationApp(font_path or DEFAULT_FONT, cache_dir)
    server = make_server(host, port, app)
    print(f"工程注记编辑窗口已启动: http://{host}:{port}")
    print(f"字体: {app.engine.backend.family_name}  缓存: {cache_dir or '(仅内存)'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
