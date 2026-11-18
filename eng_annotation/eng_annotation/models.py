"""内核数据模型。

所有距离字段都显式标注单位（``_lu`` = 逻辑单位，``_px`` = 设备像素，
``_em`` = 字体设计单位 em）。三类数据严格分开，避免把逻辑坐标、
设备像素和字体内部尺度混用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass(frozen=True)
class RasterSettings:
    """栅格化开关。

    pixel_ratio: 设备像素 / 逻辑单位（DPR），可为 1.0、1.5、2.0、3.0 等。
    hinting: True 时使用 FreeType 自动 hinting（小字清晰）；
             False 时走无 hinting 路径，字形轮廓不被网格对齐改写。
    subpixel_position: 真时允许亚像素笔位（与无 hinting 配合，见 layout）。
    lcd_rgb: 真时输出水平 LCD 子像素覆盖（3 倍宽度的分量），默认灰度。
    """

    pixel_ratio: float = 1.0
    hinting: bool = True
    subpixel_position: bool = True
    lcd_rgb: bool = False

    def __post_init__(self) -> None:
        if self.pixel_ratio <= 0:
            raise ValueError("pixel_ratio 必须为正数")
        if self.lcd_rgb:
            raise NotImplementedError("LCD 子像素模式留待后续，当前交付灰度覆盖")


@dataclass(frozen=True)
class GlyphCoverage:
    """单个字形的真实灰度覆盖与独立度量。

    coverage: 形状 (rows, width) 的 uint8 数组，0=背景 255=完全着墨。
              这是 FreeType 在当前设备像素字号下直接栅格化的结果，
              不是从低分辨率位图放大得到的。
    bitmap_left_px / bitmap_top_px: 覆盖左上角相对笔位原点（基线）的
              整数设备像素偏移；上为正。组合标记可能为负（负 bearing），
              此时覆盖向左/向上越出笔位盒，合成时不得裁掉。
    advance_px: 字形前进量，浮点设备像素（未取整）。
    x_min_bearing_px / y_max_bearing_px: FreeType 给出的浮点 bearing，
              与整数化的 bitmap 偏移分开保存，供布局与测试核对。
    """

    codepoint: int
    glyph_index: int
    pixel_size_px: float
    coverage: np.ndarray
    bitmap_left_px: int
    bitmap_top_px: int
    advance_px: float
    x_min_bearing_px: float
    y_max_bearing_px: float
    hinted: bool
    subpixel_frac_x: float = 0.0

    @property
    def width_px(self) -> int:
        return int(self.coverage.shape[1])

    @property
    def rows_px(self) -> int:
        return int(self.coverage.shape[0])

    def ink_box_px(self) -> tuple[int, int, int, int]:
        """覆盖的着墨边界 (x0, y0, x1, y1)，设备像素，相对笔位原点。

        全空白字形（空格）返回 None 盒。
        """
        if self.coverage.size == 0 or not self.coverage.any():
            return (0, 0, 0, 0)
        cols = np.any(self.coverage, axis=0)
        rows = np.any(self.coverage, axis=1)
        x0 = int(np.argmax(cols))
        x1 = self.width_px - int(np.argmax(cols[::-1]))
        y0 = int(np.argmax(rows))
        y1 = self.rows_px - int(np.argmax(rows[::-1]))
        # 数组行号转为相对基线的 y：顶行 = bitmap_top，向下递减
        return (
            self.bitmap_left_px + x0,
            self.bitmap_top_px - y1,
            self.bitmap_left_px + x1,
            self.bitmap_top_px - y0,
        )


@dataclass(frozen=True)
class PlacedGlyph:
    """布局产出的一个已定位字形。

    pen_x_lu / baseline_y_lu: 笔位原点的逻辑坐标（浮点，不取整）。
    pen_x_px / baseline_y_px: 同一笔位换算到设备像素后的浮点坐标。
    每个字形保留浮点笔位；取整只在覆盖贴入位图时按统一规则发生，
    布局层的累计前进量始终在浮点空间累加，不做逐字取整。
    """

    codepoint: int
    text: str
    pen_x_lu: float
    baseline_y_lu: float
    pen_x_px: float
    baseline_y_px: float
    advance_lu: float
    kerning_lu: float
    coverage: Optional[GlyphCoverage]
    is_combining: bool
    scale: float  # 相对基准字号的缩放（上下标 < 1）
    dy_lu: float  # 相对基准基线的抬升（上标正、下标负）

    def target_box_px(self) -> Optional[tuple[int, int, int, int]]:
        """该字形覆盖在设备位图上应贴入的整数像素盒（含负坐标）。"""
        if self.coverage is None:
            return None
        c = self.coverage
        x0 = int(np.floor(self.pen_x_px + c.bitmap_left_px + 0.0))
        y0 = int(np.floor(self.baseline_y_px - c.bitmap_top_px + 0.0))
        return (x0, y0, x0 + c.width_px, y0 + c.rows_px)


@dataclass(frozen=True)
class TextLayout:
    """一段注记文本的完整布局结果。"""

    text: str
    font_path: str
    logical_size_lu: float
    pixel_ratio: float
    device_size_px: float
    hinted: bool
    glyphs: tuple[PlacedGlyph, ...]
    width_lu: float
    ascent_lu: float
    descent_lu: float
    line_gap_lu: float
    # 逐字浮点前进序列（逻辑单位），用于审计累计行长，不做逐字取整
    advance_cursor_lu: tuple[float, ...]

    @property
    def height_lu(self) -> float:
        return self.ascent_lu - self.descent_lu


@dataclass
class Annotation:
    """一条工程注记。

    x_lu / y_lu: 注记锚点（首字笔位、基线）的逻辑坐标，可自由移动。
    size_lu: 逻辑字号（em 高度，逻辑单位），允许非整数。
    color: (R, G, B) 0-255。
    """

    text: str
    x_lu: float
    y_lu: float
    size_lu: float = 12.0
    color: tuple[int, int, int] = (0, 0, 0)
    name: str = ""

    def moved(self, dx_lu: float, dy_lu: float) -> "Annotation":
        return Annotation(
            self.text,
            self.x_lu + dx_lu,
            self.y_lu + dy_lu,
            self.size_lu,
            self.color,
            self.name,
        )


@dataclass
class Drawing:
    """工程图面：逻辑画布尺寸 + 注记集合。"""

    width_lu: float
    height_lu: float
    background: tuple[int, int, int] = (255, 255, 255)
    annotations: list[Annotation] = field(default_factory=list)
