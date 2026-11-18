"""FreeType 字体后端。

职责边界：只负责在给定的设备像素字号下把字形栅格化成真实灰度覆盖，
并把前进量、基线 bearing、覆盖边界作为独立数据返回。
这里不做任何位图放大——高 DPI 支持来自以更大的设备像素字号重新栅格化。
"""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass

import freetype
import numpy as np

from .models import GlyphCoverage, PHASE_UNITS, quantize_phase

# Unicode 组合记号区间的最小判定（覆盖工程注记用到的重音/上下标组合符）。
def _is_combining(codepoint: int) -> bool:
    return (
        0x0300 <= codepoint <= 0x036F
        or 0x1AB0 <= codepoint <= 0x1AFF
        or 0x1DC0 <= codepoint <= 0x1DFF
        or 0x20D0 <= codepoint <= 0x20FF
        or 0xFE20 <= codepoint <= 0xFE2F
    )


class FontLoadError(RuntimeError):
    pass


@dataclass(frozen=True)
class FontMetrics:
    ascent_px: float
    descent_px: float  # 负值
    line_gap_px: float
    units_per_em: int


class FontBackend:
    def __init__(self, font_path: str) -> None:
        if not os.path.isfile(font_path):
            raise FontLoadError(f"字体文件不存在: {font_path}")
        self.font_path = font_path
        try:
            self._face = freetype.Face(font_path)
        except freetype.FT_Exception as exc:  # pragma: no cover - 依赖系统库
            raise FontLoadError(f"无法打开字体 {font_path}: {exc}") from exc
        if self._face.num_faces > 1 and self._face.face_index != 0:
            pass
        self._null_matrix = ctypes.POINTER(freetype.Matrix)()
        self._size_cache: dict[float, bool] = {}

    # ---- 基础信息 -------------------------------------------------------
    @property
    def family_name(self) -> str:
        name = self._face.family_name
        if isinstance(name, bytes):
            return name.decode("utf-8", errors="replace")
        return str(name)

    @property
    def has_kerning(self) -> bool:
        return bool(self._face.face_flags & freetype.FT_FACE_FLAG_KERNING)

    def metrics_for_size(self, pixel_size_px: float) -> FontMetrics:
        self._set_size(pixel_size_px)
        size = self._face.size
        return FontMetrics(
            ascent_px=size.ascender / 64.0,
            descent_px=size.descender / 64.0,
            line_gap_px=(size.height - (size.ascender - size.descender)) / 64.0,
            units_per_em=self._face.units_per_EM,
        )

    def char_index(self, codepoint: int) -> int:
        return self._face.get_char_index(codepoint)

    def has_glyph(self, codepoint: int) -> bool:
        return self.char_index(codepoint) != 0

    # ---- 字距 -----------------------------------------------------------
    def kerning_px(
        self, left_cp: int, right_cp: int, pixel_size_px: float
    ) -> float:
        """返回水平字距（设备像素，浮点）。无 kern 表时为 0。"""
        if not self.has_kerning:
            return 0.0
        self._set_size(pixel_size_px)
        left = self.char_index(left_cp)
        right = self.char_index(right_cp)
        if left == 0 or right == 0:
            return 0.0
        k = self._face.get_kerning(left, right, freetype.FT_KERNING_DEFAULT)
        return k.x / 64.0

    # ---- 栅格化 ---------------------------------------------------------
    def rasterize(
        self,
        codepoint: int,
        pixel_size_px: float,
        hinting: bool,
        subpixel_frac_x: float = 0.0,
        subpixel_frac_y: float = 0.0,
    ) -> GlyphCoverage:
        """在设备像素字号下栅格化单个字形。

        subpixel_frac_x / subpixel_frac_y: 笔位的亚像素小数部分，恒在
        [0, 1)。二者都通过 FT_Set_Transform 平移轮廓（横向向右、纵向向上，
        FreeType 的 y 轴朝上）。任一非零即按规则强制无 hinting：hinting 会
        把轮廓吸附回整数网格，抵消亚像素平移。纵向相位此前被忽略，导致基线
        只随整数像素跳变——这正是本函数显式处理 y 相位的动因。
        """
        # 相位先量化到 FT_Set_Transform 的 1/64 网格，后续判断与缓存键
        # 都使用同一量化值，避免 0 与极小正数被分别当成“有无 hinting”。
        if not (0.0 <= subpixel_frac_x < 1.0 and 0.0 <= subpixel_frac_y < 1.0):
            raise ValueError("subpixel_frac_x/y 必须落在 [0, 1)")
        frac_x = quantize_phase(subpixel_frac_x)
        frac_y = quantize_phase(subpixel_frac_y)
        self._set_size(pixel_size_px)

        use_hinting = hinting and frac_x == 0.0 and frac_y == 0.0
        flags = freetype.FT_LOAD_RENDER | freetype.FT_LOAD_TARGET_NORMAL
        if not use_hinting:
            flags |= freetype.FT_LOAD_NO_HINTING

        # FT_Set_Transform 的 delta 为 26.6 固定点设备像素；设备 y 轴向下，
        # 而 FreeType 内部 y 轴向上，故纵向位移取负。
        delta = freetype.Vector(
            int(round(frac_x * PHASE_UNITS)),
            -int(round(frac_y * PHASE_UNITS)),
        )
        shifted = frac_x != 0.0 or frac_y != 0.0
        self._apply_transform(delta)
        try:
            self._face.load_char(codepoint, flags)
        except freetype.FT_Exception as exc:
            raise FontLoadError(
                f"栅格化失败 U+{codepoint:04X} @ {pixel_size_px}px: {exc}"
            ) from exc
        finally:
            if shifted:
                self._apply_transform(freetype.Vector(0, 0))

        slot = self._face.glyph
        bitmap = slot.bitmap
        coverage = self._bitmap_to_array(bitmap)
        glyph_index = self.char_index(codepoint)

        return GlyphCoverage(
            codepoint=codepoint,
            glyph_index=glyph_index,
            pixel_size_px=pixel_size_px,
            coverage=coverage,
            bitmap_left_px=int(slot.bitmap_left),
            bitmap_top_px=int(slot.bitmap_top),
            advance_px=slot.metrics.horiAdvance / 64.0,
            x_min_bearing_px=slot.metrics.horiBearingX / 64.0,
            y_max_bearing_px=slot.metrics.horiBearingY / 64.0,
            hinted=use_hinting,
            subpixel_frac_x=frac_x,
            subpixel_frac_y=frac_y,
        )

    # ---- 内部 -----------------------------------------------------------
    def _set_size(self, pixel_size_px: float) -> None:
        if pixel_size_px <= 0:
            raise ValueError("pixel_size_px 必须为正数")
        key = round(float(pixel_size_px), 4)
        if self._size_cache.get(key):
            return
        # set_char_size(0, char_height in 26.6 px)：直接指定设备像素字号，
        # 非整数字号（如 11.5px）原样传入。
        self._face.set_char_size(0, int(round(key * 64.0)))
        self._size_cache.clear()
        self._size_cache[key] = True

    def _apply_transform(self, delta: freetype.Vector) -> None:
        freetype.raw.FT_Set_Transform(
            self._face._FT_Face,
            self._null_matrix,
            ctypes.byref(delta),
        )

    @staticmethod
    def _bitmap_to_array(bitmap: "freetype.Bitmap") -> np.ndarray:
        if bitmap.pixel_mode != freetype.FT_PIXEL_MODE_GRAY:
            # 当前只请求 8-bit 灰度；遇到其他模式直接报错而非隐式转换。
            raise FontLoadError(
                f"不支持的像素模式 {bitmap.pixel_mode}，仅支持 8-bit 灰度"
            )
        width, rows, pitch = bitmap.width, bitmap.rows, bitmap.pitch
        if width == 0 or rows == 0:
            return np.zeros((0, 0), dtype=np.uint8)
        raw = np.frombuffer(bytes(bitmap.buffer), dtype=np.uint8)
        if pitch >= width:
            flat = raw.reshape(rows, pitch)[:, :width]
        else:  # pragma: no cover - 灰度位图 pitch 不会小于宽度
            flat = np.zeros((rows, width), dtype=np.uint8)
            flat[:, :pitch] = raw.reshape(rows, pitch)
        return np.ascontiguousarray(flat.copy())
