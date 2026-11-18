"""字形栅格化与独立度量测试。

核对要点：真实栅格而非放大、非整数字号、hinting 开关差异、
负 bearing 与组合记号不被裁、灰度级别真实存在。
"""

import numpy as np
import pytest

from eng_annotation import RasterSettings
from eng_annotation.font_backend import FontLoadError


def test_coverage_is_real_grayscale(engine):
    cov = engine.glyph_coverage(ord("0"), 12.0, RasterSettings(1.0))
    assert cov.coverage.dtype == np.uint8
    assert cov.coverage.ndim == 2
    # 灰度抗锯齿：除 0/255 外必须存在中间灰度，证明不是二值掩码
    uniq = set(np.unique(cov.coverage).tolist())
    middle = [v for v in uniq if 20 < v < 235]
    assert middle, f"缺少中间灰度级: {sorted(uniq)[:10]}"
    assert cov.coverage.max() == 255


def test_high_dpi_regrids_instead_of_upscaling(engine):
    """2x 下栅格覆盖的像素尺寸应≈2倍，而不是 1x 位图被放大。"""
    s1 = engine.glyph_coverage(ord("0"), 12.0, RasterSettings(1.0))
    s2 = engine.glyph_coverage(ord("0"), 12.0, RasterSettings(2.0))
    assert s2.pixel_size_px == 24.0
    assert s1.pixel_size_px == 12.0
    # 设备像素字号翻倍 => 覆盖行数接近翻倍（允许 1px 网格取整容差）
    assert abs(s2.rows_px - 2 * s1.rows_px) <= 1
    assert s2.rows_px > s1.rows_px
    # 放大一张低分辨率位图不会凭空增加灰度结构；这里直接比对信息密度
    up = np.kron(s1.coverage, np.ones((2, 2), dtype=np.uint8))
    # 重新栅格化的图与放大图不得逐块相同
    if up.shape == s2.coverage.shape:
        assert not np.array_equal(up, s2.coverage)


def test_non_integer_pixel_size(backend):
    # 无 hinting 路径下，非整数设备字号真实作用于轮廓，前进量是浮点且单调
    c1 = backend.rasterize(ord("0"), 11.5, hinting=False)
    c2 = backend.rasterize(ord("0"), 12.0, hinting=False)
    c3 = backend.rasterize(ord("0"), 11.0, hinting=False)
    assert c1.pixel_size_px == 11.5
    assert c3.advance_px < c1.advance_px < c2.advance_px
    # 三张覆盖互不相同，证明 11.5 不是被舍入成 11 或 12 后栅格化
    assert not np.array_equal(c1.coverage, c2.coverage)
    assert not np.array_equal(c1.coverage, c3.coverage)
    # 即便 hinting 把步进对齐到整数像素，非整数字号的覆盖仍应独立缓存
    h1 = backend.rasterize(ord("0"), 11.5, hinting=True)
    h2 = backend.rasterize(ord("0"), 12.0, hinting=True)
    assert h1.pixel_size_px != h2.pixel_size_px


def test_hinting_changes_small_size_raster(backend):
    """hinting 开关在小字号下必须产生可比较的不同栅格，且轮廓度量保持接近。"""
    hinted = backend.rasterize(ord("8"), 9.0, hinting=True)
    smooth = backend.rasterize(ord("8"), 9.0, hinting=False)
    assert hinted.hinted is True and smooth.hinted is False
    # 二者不是同一张位图（hinting 改写了网格对齐）
    assert not np.array_equal(hinted.coverage, smooth.coverage)
    # 但 bearing/前进量等布局度量应保持在亚像素~1px 的合理偏差内，
    # 防止用过度锐化/位移掩盖定位错误
    assert abs(hinted.advance_px - smooth.advance_px) <= 1.0
    assert abs(hinted.bitmap_top_px - smooth.bitmap_top_px) <= 1


def test_hinting_forced_off_at_subpixel(backend):
    cov = backend.rasterize(ord("A"), 12.0, hinting=True, subpixel_frac_x=0.37)
    assert cov.hinted is False
    # 相位吸附到 FT_Set_Transform 的 1/64 网格：0.37 → 24/64 = 0.375，
    # 报告值即真正作用的位移，而不是未经量化的请求值。
    assert cov.subpixel_frac_x == pytest.approx(0.375)
    assert 0.0 <= cov.subpixel_frac_x < 1.0
    # 亚像素相位不同 => 覆盖不同
    other = backend.rasterize(ord("A"), 12.0, hinting=False, subpixel_frac_x=0.8)
    assert not np.array_equal(cov.coverage, other.coverage)


def test_vertical_subpixel_phase_applied(backend):
    """纵向相位必须真实平移覆盖（此前被忽略，基线只随整数像素跳变）。"""
    ref = backend.rasterize(ord("H"), 12.0, hinting=False)
    half = backend.rasterize(ord("H"), 12.0, hinting=False, subpixel_frac_y=0.5)
    assert ref.subpixel_frac_y == 0.0
    assert half.subpixel_frac_y == pytest.approx(0.5)
    # 纵向半像素平移改变了覆盖（不是返回同一张图）
    assert not np.array_equal(ref.coverage, half.coverage)
    # 纵向相位非零时强制无 hinting
    hinted = backend.rasterize(ord("H"), 12.0, hinting=True, subpixel_frac_y=0.5)
    assert hinted.hinted is False


def test_negative_bearing_combining_mark_preserved(backend):
    """组合分音符 U+0308 具有负 bitmap_left，覆盖盒必须原样保留。"""
    base = backend.rasterize(ord("A"), 24.0, hinting=True)
    mark = backend.rasterize(0x0308, 24.0, hinting=True)
    assert mark.bitmap_left_px < 0, "DejaVu 的 U+0308 应为负 left bearing"
    assert mark.advance_px == 0.0  # 组合记号不前进
    # 覆盖宽度为正，负偏移没有导致空覆盖或被裁
    assert mark.width_px > 0 and mark.rows_px > 0
    assert mark.coverage.any()
    # 独立的浮点 bearing 与整数 bitmap 偏移是两份数据
    assert isinstance(mark.x_min_bearing_px, float)
    assert mark.bitmap_left_px == int(mark.x_min_bearing_px)
    assert base.codepoint != mark.codepoint


def test_ink_box_respects_baseline(backend):
    cov = backend.rasterize(ord("j"), 16.0, hinting=True)
    x0, y0, x1, y1 = cov.ink_box_px()
    # j 有下垂部，着墨盒应延伸到基线以下（y0 < 0）
    assert y0 < 0
    assert x1 > x0 and y1 > y0


def test_space_glyph_has_advance_no_ink(backend):
    cov = backend.rasterize(ord(" "), 12.0, hinting=True)
    assert cov.advance_px > 0
    assert cov.width_px == 0 and cov.rows_px == 0


def test_invalid_font_path_raises():
    with pytest.raises(FontLoadError):
        from eng_annotation.font_backend import FontBackend
        FontBackend("/no/such/font.ttf")


def test_independent_metrics_data(backend):
    """前进量、基线 bearing、覆盖边界是三份可独立读取的数据。"""
    cov = backend.rasterize(ord("H"), 14.0, hinting=True)
    assert cov.advance_px > 0
    assert cov.y_max_bearing_px > 0
    box = cov.ink_box_px()
    # 着墨盒宽度不大于前进量（H 是方正字形）
    assert (box[2] - box[0]) <= cov.advance_px + 1
