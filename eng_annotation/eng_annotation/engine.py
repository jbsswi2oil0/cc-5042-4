"""工程注记引擎：内核对外的唯一门面。

UI 与脚本都只通过本类驱动渲染；任何失败都以 EngineError 的形式返回，
由界面层呈现回“工程注记”控件，而不是吞掉异常。
"""

from __future__ import annotations

import os

import numpy as np
from PIL import Image

from .composite import SamplePoint, blend_annotations, composite_coverage
from .font_backend import FontBackend, FontLoadError
from .layout import LayoutEngine, LayoutSettings
from .models import Annotation, Drawing, RasterSettings, TextLayout
from .raster_cache import GlyphCache


class EngineError(RuntimeError):
    pass


class AnnotationEngine:
    def __init__(
        self,
        font_path: str,
        cache_dir: str | None = None,
    ) -> None:
        try:
            self.backend = FontBackend(font_path)
        except FontLoadError as exc:
            raise EngineError(str(exc)) from exc
        self.font_path = font_path
        self.cache = GlyphCache(self.backend, cache_dir)

    def make_layout_engine(self, settings: RasterSettings) -> LayoutEngine:
        return LayoutEngine(
            self.cache,
            LayoutSettings(
                pixel_ratio=settings.pixel_ratio,
                hinting=settings.hinting,
                subpixel_position=settings.subpixel_position,
            ),
        )

    def layout_annotation(
        self, ann: Annotation, settings: RasterSettings
    ) -> TextLayout:
        engine = self.make_layout_engine(settings)
        try:
            return engine.layout(ann.text, ann.size_lu, ann.x_lu, ann.y_lu)
        except (FontLoadError, ValueError) as exc:
            raise EngineError(f"注记 {ann.name or ann.text!r} 布局失败: {exc}") from exc

    def render_annotation(
        self,
        ann: Annotation,
        settings: RasterSettings,
        canvas_size_lu: tuple[float, float] | None = None,
        padding_lu: float = 4.0,
    ) -> tuple[np.ndarray, TextLayout, list[SamplePoint]]:
        layout = self.layout_annotation(ann, settings)
        if canvas_size_lu is None:
            w_lu = layout.width_lu + 2 * padding_lu
            h_lu = layout.height_lu + 2 * padding_lu
            # 把整段注记平移到画布内：x 留 padding，基线放在 ascent+padding
            shifted = Annotation(
                ann.text,
                padding_lu,
                padding_lu + layout.ascent_lu,
                ann.size_lu, ann.color, ann.name,
            )
            layout = self.layout_annotation(shifted, settings)
        else:
            w_lu, h_lu = canvas_size_lu
        size_px = (
            max(1, int(round(w_lu * settings.pixel_ratio))),
            max(1, int(round(h_lu * settings.pixel_ratio))),
        )
        canvas, samples = composite_coverage(
            layout, size_px, ann.color,
        )
        return canvas, layout, samples

    def render_drawing(
        self, drawing: Drawing, settings: RasterSettings
    ) -> tuple[np.ndarray, list[TextLayout], dict[int, list[SamplePoint]]]:
        layouts: list[tuple[TextLayout, tuple[int, int, int]]] = []
        text_layouts: list[TextLayout] = []
        for ann in drawing.annotations:
            layout = self.layout_annotation(ann, settings)
            layouts.append((layout, ann.color))
            text_layouts.append(layout)
        size_px = (
            max(1, int(round(drawing.width_lu * settings.pixel_ratio))),
            max(1, int(round(drawing.height_lu * settings.pixel_ratio))),
        )
        canvas, samples = blend_annotations(layouts, size_px, drawing.background)
        return canvas, text_layouts, samples

    # ---- 单字覆盖（UI 查看 + 验收参考） --------------------------------
    def glyph_coverage(
        self,
        codepoint: int,
        logical_size_lu: float,
        settings: RasterSettings,
        subpixel_frac_x: float = 0.0,
        subpixel_frac_y: float = 0.0,
    ):
        device_px = logical_size_lu * settings.pixel_ratio
        return self.cache.get(
            codepoint, device_px, settings.hinting,
            subpixel_frac_x, subpixel_frac_y,
        )

    @staticmethod
    def save_png(canvas: np.ndarray, path: str) -> str:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        Image.fromarray(canvas, mode="RGB").save(path)
        return path
