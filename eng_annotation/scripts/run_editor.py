#!/usr/bin/env python3
"""工程注记矢量编辑窗口启动入口。

用法：
    python3 scripts/run_editor.py [--port 8765] [--font /path/font.ttf]
                                 [--cache ./data/glyph_cache]

然后在浏览器打开 http://127.0.0.1:8765 。
所有控件操作经 HTTP 调用 eng_annotation 内核，渲染失败会在页面顶部
红色横幅中反馈回工程注记面板。
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eng_annotation.server import main  # noqa: E402
from eng_annotation.webapp import DEFAULT_FONT  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="工程图注记矢量编辑窗口")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--font", default=os.environ.get("ENG_ANNOTATION_FONT",
                                                     DEFAULT_FONT))
    ap.add_argument("--cache", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "glyph_cache"))
    args = ap.parse_args()
    main(args.host, args.port, args.font, args.cache)
