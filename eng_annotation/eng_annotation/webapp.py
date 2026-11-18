"""工程注记编辑窗口的服务层（无第三方 Web 框架）。

控件状态（注记列表、像素比、hinting）保存在内核侧的 Drawing/RasterSettings；
浏览器只负责显示与回传操作。任何渲染失败都被捕获并以 JSON 错误返回，
由前端把失败反馈回“工程注记”面板，而不是让页面静默失败。
"""

from __future__ import annotations

import base64
import io
import json
import os
from dataclasses import asdict

import numpy as np
from PIL import Image

from .engine import AnnotationEngine, EngineError
from .models import Annotation, Drawing, RasterSettings

DEFAULT_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "web", "static")


class AnnotationApp:
    """内核驱动的应用状态机，UI 操作全部经此转发到 AnnotationEngine。"""

    def __init__(self, font_path: str = DEFAULT_FONT, cache_dir: str | None = None):
        self.engine = AnnotationEngine(font_path, cache_dir)
        self.drawing = Drawing(
            width_lu=420.0,
            height_lu=260.0,
            annotations=[
                Annotation("M8-^{0.5}", 24, 60, size_lu=14,
                           color=(20, 20, 20), name="螺纹标注"),
                Annotation("Ø40 ±0.02", 24, 120, size_lu=12,
                           color=(180, 30, 30), name="直径公差"),
                Annotation("R12_{min}", 24, 175, size_lu=11.5,
                           color=(30, 60, 160), name="圆角下限"),
            ],
        )
        self.settings = RasterSettings(pixel_ratio=2.0, hinting=True)
        self.selected = 0

    # ---- 状态序列化 -----------------------------------------------------
    def state(self) -> dict:
        return {
            "font": self.engine.font_path,
            "family": self.engine.backend.family_name,
            "drawing": {
                "width_lu": self.drawing.width_lu,
                "height_lu": self.drawing.height_lu,
            },
            "settings": asdict(self.settings),
            "selected": self.selected,
            "annotations": [
                {
                    "index": i,
                    "name": a.name,
                    "text": a.text,
                    "x_lu": a.x_lu,
                    "y_lu": a.y_lu,
                    "size_lu": a.size_lu,
                    "color": list(a.color),
                }
                for i, a in enumerate(self.drawing.annotations)
            ],
        }

    # ---- 渲染 -----------------------------------------------------------
    def render(self) -> dict:
        canvas, layouts, samples = self.engine.render_drawing(
            self.drawing, self.settings
        )
        png = _png_b64(canvas)
        ann_data = []
        for idx, layout in enumerate(layouts):
            ann = self.drawing.annotations[idx]
            ann_data.append(
                {
                    "index": idx,
                    "name": ann.name,
                    "width_lu": round(layout.width_lu, 3),
                    "ascent_lu": round(layout.ascent_lu, 3),
                    "descent_lu": round(layout.descent_lu, 3),
                    "device_size_px": round(layout.device_size_px, 3),
                    "glyphs": [
                        {
                            "text": g.text,
                            "pen_x_lu": round(g.pen_x_lu, 3),
                            "baseline_y_lu": round(g.baseline_y_lu, 3),
                            "pen_x_px": round(g.pen_x_px, 3),
                            "baseline_y_px": round(g.baseline_y_px, 3),
                            "advance_lu": round(g.advance_lu, 4),
                            "combining": g.is_combining,
                            "scale": round(g.scale, 3),
                        }
                        for g in layout.glyphs
                    ],
                    "samples": [
                        {
                            "text": s.text,
                            "pen_x_px": round(s.pen_x_px, 2),
                            "baseline_y_px": round(s.baseline_y_px, 2),
                            "box": [s.box_x0, s.box_y0, s.box_x1, s.box_y1],
                            "on_canvas": s.on_canvas,
                        }
                        for s in samples[idx]
                    ],
                }
            )
        return {
            "image": png,
            "image_size_px": list(canvas.shape[1::-1]),
            "pixel_ratio": self.settings.pixel_ratio,
            "annotations": ann_data,
            "cache": {
                "entries": len(self.engine.cache),
                "hits": self.engine.cache.hits,
                "misses": self.engine.cache.misses,
            },
        }

    def render_glyph(self, codepoint: int, size_lu: float) -> dict:
        """单字覆盖查看：返回放大展示用 PNG 与全部独立度量。"""
        cov = self.engine.glyph_coverage(
            codepoint, size_lu, self.settings
        )
        if cov.width_px == 0:
            raise EngineError(f"U+{codepoint:04X} 无可视覆盖（空格/缺字）")
        # 放大 4 倍仅用于界面看清像素结构；元数据保持原设备像素
        zoom = 4
        big = np.kron(cov.coverage, np.ones((zoom, zoom), dtype=np.uint8))
        rgb = np.stack([big, big, big], axis=-1)
        return {
            "codepoint": codepoint,
            "char": chr(codepoint),
            "metrics": {
                "pixel_size_px": cov.pixel_size_px,
                "width_px": cov.width_px,
                "rows_px": cov.rows_px,
                "bitmap_left_px": cov.bitmap_left_px,
                "bitmap_top_px": cov.bitmap_top_px,
                "advance_px": round(cov.advance_px, 4),
                "x_min_bearing_px": round(cov.x_min_bearing_px, 4),
                "y_max_bearing_px": round(cov.y_max_bearing_px, 4),
                "ink_box_px": list(cov.ink_box_px()),
                "hinted": cov.hinted,
                "subpixel_frac_x": cov.subpixel_frac_x,
            },
            "image": _png_b64(rgb),
            "zoom": zoom,
        }

    # ---- 控件操作（失败抛 EngineError，由 handler 回传前端） ------------
    def update_settings(self, patch: dict) -> None:
        ratio = float(patch.get("pixel_ratio", self.settings.pixel_ratio))
        if ratio <= 0:
            raise EngineError("pixel_ratio 必须为正数")
        self.settings = RasterSettings(
            pixel_ratio=ratio,
            hinting=bool(patch.get("hinting", self.settings.hinting)),
            subpixel_position=bool(
                patch.get("subpixel_position", self.settings.subpixel_position)
            ),
        )

    def move_annotation(self, index: int, dx_lu: float, dy_lu: float) -> None:
        ann = self._require(index)
        self.drawing.annotations[index] = ann.moved(dx_lu, dy_lu)

    def set_position(self, index: int, x_lu: float, y_lu: float) -> None:
        ann = self._require(index)
        ann.x_lu, ann.y_lu = float(x_lu), float(y_lu)

    def update_annotation(self, index: int, patch: dict) -> None:
        ann = self._require(index)
        text = patch.get("text", ann.text)
        if not text:
            raise EngineError("注记文本不能为空")
        size_lu = float(patch.get("size_lu", ann.size_lu))
        if size_lu <= 0:
            raise EngineError("字号必须为正数")
        color = patch.get("color", list(ann.color))
        ann.text = text
        ann.size_lu = size_lu
        ann.color = (int(color[0]), int(color[1]), int(color[2]))
        ann.name = patch.get("name", ann.name)

    def add_annotation(self, text: str, x_lu: float, y_lu: float,
                       size_lu: float) -> int:
        if not text:
            raise EngineError("注记文本不能为空")
        ann = Annotation(text, float(x_lu), float(y_lu), float(size_lu))
        self.drawing.annotations.append(ann)
        self.selected = len(self.drawing.annotations) - 1
        return self.selected

    def delete_annotation(self, index: int) -> None:
        self._require(index)
        del self.drawing.annotations[index]
        self.selected = max(0, min(self.selected,
                                   len(self.drawing.annotations) - 1))

    def select(self, index: int) -> None:
        self._require(index)
        self.selected = index

    def _require(self, index: int) -> Annotation:
        if not 0 <= index < len(self.drawing.annotations):
            raise EngineError(f"注记索引越界: {index}")
        return self.drawing.annotations[index]


def _png_b64(rgb: np.ndarray) -> str:
    buf = io.BytesIO()
    Image.fromarray(rgb, mode="RGB").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")
