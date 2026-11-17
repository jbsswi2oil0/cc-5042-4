#!/usr/bin/env python3
"""Launcher for the engineering annotation editor.

    python3 main.py [--host 127.0.0.1] [--port 8000] [--dpr 1.0]

Then open the printed URL in a browser. The page is the vector editing
window: drag annotations, switch pixel ratio, toggle hinting, inspect
per-glyph coverage and sampling positions.
"""
import argparse
import os

from annotation.kernel import AnnotationKernel
from annotation.server import serve

DEFAULT_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def seed(kernel):
    """Demo drawing: a part outline plus engineering annotations."""
    kernel.add_entity("rect", [60, 60, 620, 380], "#7a7a7a")
    kernel.add_entity("line", [60, 220, 620, 220], "#aaaaaa")
    kernel.add_entity("line", [340, 60, 340, 380], "#aaaaaa")
    kernel.add_entity("rect", [120, 120, 300, 190], "#4a6fa5")
    kernel.add_annotation(120, 105, 13.0, "Base plate 1111-08", "#1a1a1a")
    kernel.add_annotation(360, 105, 10.5, "Roughness Ra~{3.2} max", "#1a1a1a")
    kernel.add_annotation(360, 250, 12.75, "Area 12.5 mm^{2} per face",
                          "#7a1f1f")
    kernel.add_annotation(120, 320, 9.0, "1111111 thin-digit check", "#1a1a1a")
    kernel.add_annotation(360, 320, 14.0, "H~{2}O + x^{2} marks", "#1a1a1a")


def main():
    ap = argparse.ArgumentParser(description="engineering annotation editor")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--dpr", type=float, default=1.0)
    ap.add_argument("--font", default=DEFAULT_FONT)
    ap.add_argument("--no-seed", action="store_true")
    args = ap.parse_args()

    kernel = AnnotationKernel(args.font, dpr=args.dpr)
    if not args.no_seed:
        seed(kernel)
    kernel.render()  # warm the cache so first page load has diagnostics

    webroot = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
    httpd = serve(kernel, webroot, args.host, args.port)
    url = "http://%s:%d/" % (args.host, args.port)
    print("Engineering annotation editor")
    print("  font:     %s (%s)" % (kernel.face.path, kernel.face.family))
    print("  FreeType: %d.%d.%d" % kernel.library.version())
    print("  dpr:      %.2f (dpi %.0f)" % (kernel.dpr, kernel.dpi))
    print("  open:     %s" % url)
    print("  stop:     Ctrl+C")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        kernel.close()


if __name__ == "__main__":
    main()
