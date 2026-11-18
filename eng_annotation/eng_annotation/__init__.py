"""工程图注记渲染内核。

三个相互独立的尺度贯穿整个模块：

* 逻辑尺寸（layout units，逻辑坐标/点）——注记在图面上的设计位置与字号；
* 设备像素（device px）——实际栅格所在的位图坐标，= 逻辑尺寸 × pixel_ratio；
* 字体尺度（font scale）——FreeType 加载字形时使用的设备像素字号，
  允许非整数，且随 pixel_ratio 重新栅格化，而不是放大低位图。
"""

from .models import (
    GlyphCoverage,
    PlacedGlyph,
    TextLayout,
    Annotation,
    Drawing,
    RasterSettings,
)
from .font_backend import FontBackend, FontLoadError
from .raster_cache import GlyphCache
from .layout import LayoutEngine, LayoutSettings
from .composite import composite_coverage, blend_annotations
from .engine import AnnotationEngine

__all__ = [
    "GlyphCoverage",
    "PlacedGlyph",
    "TextLayout",
    "Annotation",
    "Drawing",
    "RasterSettings",
    "FontBackend",
    "FontLoadError",
    "GlyphCache",
    "LayoutEngine",
    "LayoutSettings",
    "composite_coverage",
    "blend_annotations",
    "AnnotationEngine",
]
