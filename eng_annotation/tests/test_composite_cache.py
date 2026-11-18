"""合成、混合公式、负边距贴入、缓存真实性与参考字形核对。"""

import json
import os

import numpy as np
import pytest

from eng_annotation import Annotation, Drawing, RasterSettings
from eng_annotation.composite import composite_coverage


def test_source_over_formula(engine):
    """在已知背景与颜色上，中心像素必须满足显式 source-over 公式。"""
    rs = RasterSettings(pixel_ratio=1.0)
    ann = Annotation("0", 4.0, 14.0)
    canvas, layout, samples = engine.render_annotation(
        ann, rs, canvas_size_lu=(20, 20)
    )
    g = layout.glyphs[0]
    cov = g.coverage.coverage.astype(np.float32) / 255.0
    x0, y0 = g.target_box_px()[:2]
    color = np.array(ann.color, dtype=np.float32)
    bg = np.array([255, 255, 255], dtype=np.float32)
    for yy in range(cov.shape[0]):
        for xx in range(cov.shape[1]):
            a = cov[yy, xx]
            expect = color * a + bg * (1.0 - a)
            actual = canvas[y0 + yy, x0 + xx].astype(np.float32)
            assert actual == pytest.approx(expect, abs=1.0)


def test_negative_bearing_not_clipped(engine):
    """组合符负 bearing 越出基字符盒时，画布上必须保留其着墨。"""
    rs = RasterSettings(pixel_ratio=1.0)
    ann = Annotation("A\u0308", 24.0, 30.0)
    canvas, layout, samples = engine.render_annotation(
        ann, rs, canvas_size_lu=(40, 40)
    )
    mark = layout.glyphs[1]
    assert mark.coverage.bitmap_left_px < 0
    box = mark.target_box_px()
    base_box = layout.glyphs[0].target_box_px()
    # 标记经锚定后水平落在基字符着墨盒附近，而不是被负 bearing 甩到左侧
    assert box[0] >= base_box[0] - 2
    # 标记盒底边高于基字符盒底边，即标记位于基字符上半部
    assert box[3] < base_box[3]
    # 标记覆盖区域在画布内确有着墨像素（非白）
    x0, y0, x1, y1 = box
    inside = canvas[max(y0, 0):y1, max(x0, 0):x1]
    assert (inside < 250).any()


def test_partial_off_canvas_kept(engine):
    """贴在画布左边缘外的注记，进入画布的部分仍然绘制。"""
    rs = RasterSettings(pixel_ratio=1.0)
    ann = Annotation("ABC", -8.0, 14.0, size_lu=12)
    canvas, layout, samples = engine.render_annotation(
        ann, rs, canvas_size_lu=(30, 24)
    )
    first = layout.glyphs[0]
    assert first.target_box_px()[0] < 0  # 首字盒起于画布外
    assert canvas.any() and (canvas < 250).any()  # 仍有内容画进来


def test_sample_points_recorded(engine):
    rs = RasterSettings(pixel_ratio=2.0)
    _, layout, samples = engine.render_annotation(
        Annotation("M8", 12.0, 0), rs, canvas_size_lu=(40, 30)
    )
    assert len(samples) == len(layout.glyphs)
    for s, g in zip(samples, layout.glyphs):
        assert s.pen_x_px == pytest.approx(g.pen_x_px)
        assert s.baseline_y_px == pytest.approx(g.baseline_y_px)
        assert s.box_x1 > s.box_x0


def test_drawing_blends_multiple_annotations(engine):
    rs = RasterSettings(pixel_ratio=1.0)
    drawing = Drawing(
        80, 40,
        annotations=[
            Annotation("R10", 4, 16, color=(200, 0, 0)),
            Annotation("Ø20", 30, 16, color=(0, 0, 200)),
        ],
    )
    canvas, layouts, samples = engine.render_drawing(drawing, rs)
    assert canvas.shape == (40, 80, 3)
    red = (canvas[:, :, 0] > 120) & (canvas[:, :, 2] < 80)
    blue = (canvas[:, :, 2] > 120) & (canvas[:, :, 0] < 80)
    assert red.any() and blue.any()
    assert set(samples.keys()) == {0, 1}


def test_cache_roundtrip_real_raster(engine, tmp_path):
    rs = RasterSettings(pixel_ratio=2.0)
    cov1 = engine.glyph_coverage(ord("5"), 10.0, rs)
    key = engine.cache.key(ord("5"), 20.0, True, 0.0)
    npy = tmp_path / "glyph_cache" / f"{key}.npy"
    js = tmp_path / "glyph_cache" / f"{key}.json"
    assert npy.is_file() and js.is_file()
    on_disk = np.load(npy)
    assert np.array_equal(on_disk, cov1.coverage)
    meta = json.loads(js.read_text())
    assert meta["codepoint"] == ord("5") and meta["pixel_size_px"] == 20.0

    # 新引擎实例从磁盘冷启动 => 命中磁盘缓存且位图一致
    from eng_annotation import AnnotationEngine
    engine2 = AnnotationEngine(engine.font_path, cache_dir=str(tmp_path / "glyph_cache"))
    cov2 = engine2.glyph_coverage(ord("5"), 10.0, rs)
    assert engine2.cache.hits == 1 and engine2.cache.misses == 0
    assert np.array_equal(cov1.coverage, cov2.coverage)


def test_cache_key_distinguishes_hinting_and_phase(engine):
    k1 = engine.cache.key(ord("A"), 12.0, True, 0.0)
    k2 = engine.cache.key(ord("A"), 12.0, False, 0.0)
    k3 = engine.cache.key(ord("A"), 12.0, False, 0.5)
    k4 = engine.cache.key(ord("A"), 13.0, True, 0.0)
    assert len({k1, k2, k3, k4}) == 4


def test_reference_glyph_matches_freetype_directly(engine, backend):
    """内核输出与直接调用 FreeType 的参考栅格逐像素一致。"""
    ref = backend.rasterize(ord("&"), 18.0, hinting=True)
    got = engine.glyph_coverage(
        ord("&"), 18.0, RasterSettings(pixel_ratio=1.0, hinting=True)
    )
    assert np.array_equal(ref.coverage, got.coverage)
    assert ref.advance_px == got.advance_px
    assert ref.bitmap_left_px == got.bitmap_left_px
    assert ref.bitmap_top_px == got.bitmap_top_px


def test_dpr_raster_size_scales_with_ratio(engine):
    """细字号 7lu 在 1x/2x/3x 下覆盖设备像素数随比例增长，绝无放大冒充。"""
    rows = {}
    for ratio in (1.0, 2.0, 3.0):
        cov = engine.glyph_coverage(ord("1"), 7.0, RasterSettings(pixel_ratio=ratio))
        rows[ratio] = cov.rows_px
        assert cov.pixel_size_px == 7.0 * ratio
    assert rows[2.0] > rows[1.0]
    assert rows[3.0] > rows[2.0]
