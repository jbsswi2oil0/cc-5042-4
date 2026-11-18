"""灰度覆盖到 RGB 位图的合成。

混合公式显式给出（标准 source-over，coverage 视为线性 alpha）：

    a = coverage / 255
    out = src * a + dst * (1 - a)

覆盖贴入时按笔位浮点坐标统一 floor 取整一次；落在画布外的部分被裁，
落在画布内的部分（包括负 bearing 造成的越界盒）一律保留，绝不因
bitmap_left 为负而平移或丢弃字形。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .models import PlacedGlyph, TextLayout


@dataclass(frozen=True)
class SamplePoint:
    """UI 与验收用的采样位置记录。"""

    text: str
    pen_x_px: float
    baseline_y_px: float
    box_x0: int
    box_y0: int
    box_x1: int
    box_y1: int
    on_canvas: bool


def _paste_coverage(
    canvas: np.ndarray,
    coverage: np.ndarray,
    box: tuple[int, int, int, int],
    color: tuple[int, int, int],
) -> tuple[int, int, int, int] | None:
    """把一张灰度覆盖按目标盒 source-over 贴入画布，返回画布内有效盒。"""
    x0, y0, x1, y1 = box
    h, w = canvas.shape[:2]
    cx0, cy0 = max(x0, 0), max(y0, 0)
    cx1, cy1 = min(x1, w), min(y1, h)
    if cx0 >= cx1 or cy0 >= cy1:
        return None
    # 源切片必须与画布内目标区域一一对应：负坐标部分在源上同样裁掉，
    # 而不是把源左上角对齐到画布原点（那会平移字形、丢掉负 bearing）
    sx0, sy0 = cx0 - x0, cy0 - y0
    sx1 = sx0 + (cx1 - cx0)
    sy1 = sy0 + (cy1 - cy0)
    patch = coverage[sy0:sy1, sx0:sx1].astype(np.float32) / 255.0
    region = canvas[cy0:cy1, cx0:cx1].astype(np.float32)
    src = np.asarray(color, dtype=np.float32).reshape(1, 1, 3)
    alpha = patch[..., None]
    blended = src * alpha + region * (1.0 - alpha)
    canvas[cy0:cy1, cx0:cx1] = np.clip(blended, 0, 255).astype(np.uint8)
    return (cx0, cy0, cx1, cy1)


def composite_coverage(
    layout: TextLayout,
    canvas_size_px: tuple[int, int],
    color: tuple[int, int, int],
    background: tuple[int, int, int] = (255, 255, 255),
) -> tuple[np.ndarray, list[SamplePoint]]:
    """把单个布局渲染成 RGB 设备位图，并返回每字采样位置。"""
    width_px, height_px = canvas_size_px
    canvas = np.empty((height_px, width_px, 3), dtype=np.uint8)
    canvas[:, :] = np.asarray(background, dtype=np.uint8).reshape(1, 1, 3)

    samples: list[SamplePoint] = []
    for glyph in layout.glyphs:
        box = glyph.target_box_px()
        if glyph.coverage is None or box is None:
            # 空格/缺字：仍记录笔位采样点，保证布局可审计
            samples.append(
                SamplePoint(
                    glyph.text, glyph.pen_x_px, glyph.baseline_y_px,
                    0, 0, 0, 0, on_canvas=False,
                )
            )
            continue
        on = _paste_coverage(canvas, glyph.coverage.coverage, box, color)
        samples.append(
            SamplePoint(
                glyph.text,
                glyph.pen_x_px,
                glyph.baseline_y_px,
                box[0], box[1], box[2], box[3],
                on_canvas=on is not None,
            )
        )
    return canvas, samples


def blend_annotations(
    layouts: list[tuple[TextLayout, tuple[int, int, int]]],
    canvas_size_px: tuple[int, int],
    background: tuple[int, int, int] = (255, 255, 255),
) -> tuple[np.ndarray, dict[int, list[SamplePoint]]]:
    """多注记依次合成到同一画布（后写覆盖先写，source-over 逐字混合）。"""
    width_px, height_px = canvas_size_px
    canvas = np.empty((height_px, width_px, 3), dtype=np.uint8)
    canvas[:, :] = np.asarray(background, dtype=np.uint8).reshape(1, 1, 3)
    all_samples: dict[int, list[SamplePoint]] = {}
    for idx, (layout, color) in enumerate(layouts):
        points: list[SamplePoint] = []
        for glyph in layout.glyphs:
            box = glyph.target_box_px()
            if glyph.coverage is None or box is None:
                points.append(
                    SamplePoint(glyph.text, glyph.pen_x_px, glyph.baseline_y_px,
                                0, 0, 0, 0, on_canvas=False)
                )
                continue
            on = _paste_coverage(canvas, glyph.coverage.coverage, box, color)
            points.append(
                SamplePoint(glyph.text, glyph.pen_x_px, glyph.baseline_y_px,
                            box[0], box[1], box[2], box[3], on is not None)
            )
        all_samples[idx] = points
    return canvas, all_samples
