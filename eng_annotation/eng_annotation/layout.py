"""文本布局引擎。

尺度约定（三者严格分离）：

* 逻辑单位 lu：注记设计坐标与逻辑字号；
* 设备像素 px：pen_x_px = pen_x_lu × pixel_ratio，覆盖只在 px 空间栅格化；
* 字体尺度：设备像素字号 device_size = logical_size × ratio × 段缩放，
  以非整数浮点传给 FreeType 重新栅格化。

累计行长在浮点空间维护，逐字不取整；取整只发生在覆盖贴入位图那一刻，
因此可审计“每字独立取整”会带来的累计误差（见 tests）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .font_backend import FontBackend, _is_combining
from .models import PlacedGlyph, TextLayout, split_pen_px
from .raster_cache import GlyphCache


@dataclass(frozen=True)
class LayoutSettings:
    pixel_ratio: float = 1.0
    hinting: bool = True
    subpixel_position: bool = True
    # 上/下标相对基准字号的缩放与基线抬升（em 比例）
    superscript_scale: float = 0.65
    superscript_raise: float = 0.45
    subscript_scale: float = 0.65
    subscript_drop: float = 0.18
    kerning: bool = True


# 简易标记：^ 后跟随 {...} 或单字符为上标，_ 同理为下标，\ 转义。
@dataclass
class _Segment:
    text: str
    kind: str  # 'normal' | 'super' | 'sub'


def parse_markup(text: str) -> list[_Segment]:
    segments: list[_Segment] = []
    i = 0
    buf: list[str] = []

    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text) and text[i + 1] in "^_{}":
            buf.append(text[i + 1])
            i += 2
            continue
        if ch in "^_":
            if buf:
                segments.append(_Segment("".join(buf), "normal"))
                buf.clear()
            kind = "super" if ch == "^" else "sub"
            i += 1
            if i < len(text) and text[i] == "{":
                j = text.find("}", i + 1)
                if j == -1:
                    raise ValueError(f"标记未闭合: {text!r}")
                segments.append(_Segment(text[i + 1:j], kind))
                i = j + 1
            elif i < len(text):
                segments.append(_Segment(text[i], kind))
                i += 1
            continue
        buf.append(ch)
        i += 1
    if buf:
        segments.append(_Segment("".join(buf), "normal"))
    return segments


class LayoutEngine:
    def __init__(self, cache: GlyphCache, settings: LayoutSettings) -> None:
        self.cache = cache
        self.backend: FontBackend = cache.backend
        self.settings = settings

    def layout(
        self,
        text: str,
        logical_size_lu: float,
        origin_x_lu: float = 0.0,
        baseline_y_lu: float = 0.0,
    ) -> TextLayout:
        s = self.settings
        ratio = s.pixel_ratio
        base_device_px = logical_size_lu * ratio
        metrics = self.backend.metrics_for_size(base_device_px)

        placed: list[PlacedGlyph] = []
        cursor_lu = 0.0  # 浮点逻辑笔位，全程不取整
        base_pen_lu = 0.0  # 最近一个基字符笔位，供组合记号叠印
        base_cov = None    # 最近基字符覆盖（含独立 bearing 度量）
        cursors: list[float] = []
        prev_cp: int | None = None

        for seg in parse_markup(text):
            if seg.kind == "super":
                seg_scale, dy_lu = (
                    s.superscript_scale,
                    s.superscript_raise * logical_size_lu,
                )
            elif seg.kind == "sub":
                seg_scale, dy_lu = (
                    s.subscript_scale,
                    -s.subscript_drop * logical_size_lu,
                )
            else:
                seg_scale, dy_lu = 1.0, 0.0

            device_px = base_device_px * seg_scale
            for ch in seg.text:
                cp = ord(ch)
                combining = _is_combining(cp)
                kern_lu = 0.0

                if not combining and s.kerning and prev_cp is not None:
                    kern_lu = (
                        self.backend.kerning_px(prev_cp, cp, device_px) / ratio
                    )
                if not combining:
                    cursor_lu += kern_lu

                # 先栅格化取覆盖。普通字形的横纵笔位都带亚像素相位；组合
                # 记号横向不做亚像素平移——其位置由整数着墨盒中心决定（见
                # 下），但纵向沿用基线的相位，使标记与基字符在同一亚像素
                # 基线上对齐，不因分数基线而错开。
                cov = None
                if self.backend.has_glyph(cp):
                    if s.subpixel_position:
                        probe_y_px = (baseline_y_lu + dy_lu) * ratio
                        _, probe_frac_y = split_pen_px(probe_y_px)
                        if combining:
                            frac_x = 0.0
                        else:
                            probe_x_px = (origin_x_lu + cursor_lu) * ratio
                            _, frac_x = split_pen_px(probe_x_px)
                    else:
                        frac_x = 0.0
                        probe_frac_y = 0.0
                    cov = self.cache.get(
                        cp, device_px, s.hinting, frac_x, probe_frac_y,
                    )

                if combining:
                    # FreeType 不暴露 GPOS mark attachment，按 Unicode 组合
                    # 标记的标准近似处理：让标记着墨盒水平中心对齐基字符着墨
                    # 盒中心。标记笔位 = 基字符笔位 + 中心差，负 bearing 由
                    # target_box 原样保留，不会被裁或把标记甩出。
                    if base_cov is not None and cov is not None:
                        b0, _, b1, _ = base_cov.ink_box_px()
                        m0, _, m1, _ = cov.ink_box_px()
                        if m1 > m0:
                            center_shift_px = ((b0 + b1) - (m0 + m1)) / 2.0
                            mark_offset_lu = center_shift_px / ratio
                        else:
                            mark_offset_lu = 0.0
                    else:
                        mark_offset_lu = 0.0
                    pen_lu = base_pen_lu + mark_offset_lu
                else:
                    pen_lu = cursor_lu

                abs_pen_lu = origin_x_lu + pen_lu
                abs_baseline_lu = baseline_y_lu + dy_lu
                pen_px = abs_pen_lu * ratio
                baseline_px = abs_baseline_lu * ratio

                # 贴入基准必须与栅格化时使用的相位一致：普通字形用同一
                # split 拆出的整数基准；组合记号横向未做相位平移（位置由
                # 整数着墨盒中心决定），故基准取 floor，纵向仍按基线相位。
                if not s.subpixel_position:
                    # 亚像素关闭：覆盖按整数相位栅格化，贴入取 floor 且相位记 0。
                    pen_base_px = int(math.floor(pen_px))
                    baseline_base_px = int(math.floor(baseline_px))
                    place_frac_x = 0.0
                    place_frac_y = 0.0
                elif combining:
                    pen_base_px = int(math.floor(pen_px))
                    place_frac_x = 0.0
                    baseline_base_px, place_frac_y = split_pen_px(baseline_px)
                else:
                    pen_base_px, place_frac_x = split_pen_px(pen_px)
                    baseline_base_px, place_frac_y = split_pen_px(baseline_px)

                placed.append(
                    PlacedGlyph(
                        codepoint=cp,
                        text=ch,
                        pen_x_lu=abs_pen_lu,
                        baseline_y_lu=abs_baseline_lu,
                        pen_x_px=pen_px,
                        baseline_y_px=baseline_px,
                        advance_lu=(cov.advance_px / ratio) if cov else 0.0,
                        kerning_lu=kern_lu,
                        coverage=cov,
                        is_combining=combining,
                        scale=seg_scale,
                        dy_lu=dy_lu,
                        pen_x_base_px=pen_base_px,
                        baseline_y_base_px=baseline_base_px,
                        subpixel_frac_x=place_frac_x,
                        subpixel_frac_y=place_frac_y,
                    )
                )
                cursors.append(pen_lu)
                if not combining:
                    base_pen_lu = pen_lu
                    base_cov = cov
                    if cov is not None:
                        cursor_lu += cov.advance_px / ratio
                prev_cp = cp

        width_lu = cursor_lu
        return TextLayout(
            text=text,
            font_path=self.backend.font_path,
            logical_size_lu=logical_size_lu,
            pixel_ratio=ratio,
            device_size_px=base_device_px,
            hinted=s.hinting,
            glyphs=tuple(placed),
            width_lu=width_lu,
            ascent_lu=metrics.ascent_px / ratio,
            descent_lu=metrics.descent_px / ratio,
            line_gap_lu=metrics.line_gap_px / ratio,
            advance_cursor_lu=tuple(cursors),
        )
