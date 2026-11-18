"""布局测试：三尺度分离、浮点累计笔位、上下标、DPR 一致性。"""

import pytest

from eng_annotation import Annotation, RasterSettings
from eng_annotation.layout import parse_markup


def test_parse_markup_segments():
    segs = parse_markup("M8-^{0.5}_2")
    kinds = [(s.text, s.kind) for s in segs]
    assert ("M8-", "normal") in kinds
    assert ("0.5", "super") in kinds
    assert ("2", "sub") in kinds


def test_escaped_markup_literal():
    segs = parse_markup(r"2\^3")
    assert len(segs) == 1 and segs[0].text == "2^3"


def test_unclosed_brace_raises(engine):
    with pytest.raises(Exception):
        engine.layout_annotation(
            Annotation("x^{2", 0, 10), RasterSettings()
        )


def test_three_scales_kept_separate(engine):
    rs = RasterSettings(pixel_ratio=2.5, hinting=True)
    layout = engine.layout_annotation(
        Annotation("123", 7.0, 30.0, size_lu=7.0), rs
    )
    assert layout.logical_size_lu == 7.0
    assert layout.device_size_px == pytest.approx(17.5)
    assert layout.pixel_ratio == 2.5
    for g in layout.glyphs:
        # 设备笔位 = 逻辑笔位 × ratio，逐字可核对
        assert g.pen_x_px == pytest.approx(g.pen_x_lu * 2.5)
        assert g.baseline_y_px == pytest.approx(g.baseline_y_lu * 2.5)
        # 覆盖确实按设备像素字号栅格化
        assert g.coverage.pixel_size_px == pytest.approx(17.5)


def test_pen_positions_are_floating_not_per_glyph_rounded(engine):
    """笔位在浮点空间累计；无 hinting 下相邻笔位差必须保持非整数。"""
    rs = RasterSettings(pixel_ratio=1.0, hinting=False, subpixel_position=True)
    layout = engine.layout_annotation(Annotation("00000000", 11.5, 0), rs)
    pens = [g.pen_x_px for g in layout.glyphs]
    diffs = [b - a for a, b in zip(pens, pens[1:])]
    # 11.5px 无 hinting 数字前进量为非整数浮点
    assert any(abs(d - round(d)) > 1e-9 for d in diffs)
    # 审计序列单调递增；行尾 = 最后笔位 + 末字前进量 = 布局总宽
    assert list(pens) == sorted(pens)
    last = layout.glyphs[-1]
    assert layout.advance_cursor_lu[-1] + last.advance_lu == pytest.approx(
        layout.width_lu, abs=1e-9
    )


def test_cumulative_rounding_error_quantified(engine):
    """对比“浮点累计”与“逐字取整累计”，显式量化累计行长误差。

    验收要求：逐字独立取整造成的累计误差必须被测量，而不是被忽略。
    取无 hinting 布局，其前进量为真实浮点，取整漂移才不会被 hinting
    本身的整数对齐掩盖。
    """
    rs = RasterSettings(pixel_ratio=1.0, hinting=False)
    text = "0123456789" * 5
    layout = engine.layout_annotation(Annotation(text, 11.5, 0), rs)
    exact_cursors = layout.advance_cursor_lu
    rounded_cursors = []
    acc = 0.0
    for g in layout.glyphs:
        rounded_cursors.append(acc)
        acc += round(g.advance_lu)  # 模拟“每字独立取整”
    drift = [r - e for r, e in zip(rounded_cursors, exact_cursors)]
    max_drift = max(abs(d) for d in drift)
    # 50 个非整数前进量累计，逐字取整必然漂移；内核采用浮点累计，
    # 这里证明该误差真实存在且达到亚像素以上，必须在验收中显式核对
    assert max_drift >= 0.75, f"预期累计取整漂移≥0.75px，实测 {max_drift}"
    # 两种累计方式的最终行长同样存在可量化差异
    rounded_total = sum(round(g.advance_lu) for g in layout.glyphs)
    assert abs(rounded_total - layout.width_lu) > 0.5
    # 内核自身宽度等于精确浮点累计（笔位序列 + 末字前进），无取整漂移
    exact_total = exact_cursors[-1] + layout.glyphs[-1].advance_lu
    assert layout.width_lu == pytest.approx(exact_total, abs=1e-9)


def test_dpr_logical_layout_invariant(engine):
    """同一逻辑注记在不同 DPR 下，逻辑布局宽度保持一致。

    无 hinting 时轮廓不做网格对齐，逻辑宽度应只受浮点舍入影响。
    """
    widths = {}
    for ratio in (1.0, 1.5, 2.0, 3.0):
        layout = engine.layout_annotation(
            Annotation("R10 ±0.02", 12.0, 0),
            RasterSettings(pixel_ratio=ratio, hinting=False),
        )
        widths[ratio] = layout.width_lu
    base = widths[1.0]
    for ratio, w in widths.items():
        # FreeType 26.6 固定点在不同设备字号下有约 1/(64*ratio) px 的
        # 量化噪声；逻辑宽度偏差应远小于 1 逻辑单位（这里取 0.12，约 1% 字身）
        assert w == pytest.approx(base, abs=0.12), (ratio, w, base)


def test_superscript_subscript_geometry(engine):
    layout = engine.layout_annotation(
        Annotation("H^{2}O_{3}", 16.0, 0), RasterSettings(pixel_ratio=1.0)
    )
    by_text = {g.text: g for g in layout.glyphs}
    sup = by_text["2"]
    sub = by_text["3"]
    normal = by_text["H"]
    assert sup.scale == pytest.approx(0.65)
    assert sub.scale == pytest.approx(0.65)
    assert sup.baseline_y_lu > normal.baseline_y_lu  # 上标抬升
    assert sub.baseline_y_lu < normal.baseline_y_lu  # 下标下沉
    # 上标以更小设备字号栅格化
    assert sup.coverage.pixel_size_px < normal.coverage.pixel_size_px


def test_combining_mark_does_not_advance(engine):
    # 分解序列：A + U+0308（预组合字符不会经过组合记号路径）
    layout = engine.layout_annotation(
        Annotation("A\u0308", 20.0, 0), RasterSettings(pixel_ratio=1.0)
    )
    a_glyph, mark = layout.glyphs
    assert mark.is_combining is True
    # 标记只在基字符附近做锚定偏移，绝不推进布局游标
    assert mark.advance_lu == 0.0
    assert layout.width_lu == pytest.approx(a_glyph.advance_lu)


def test_combining_mark_centered_over_base(engine):
    """组合记号着墨盒中心对齐基字符着墨盒中心（含负 bearing 情形）。"""
    layout = engine.layout_annotation(
        Annotation("o\u0308", 20.0, 0), RasterSettings(pixel_ratio=1.0)
    )
    base, mark = layout.glyphs
    assert mark.coverage.bitmap_left_px < 0  # 标记本身带负 bearing
    b0, _, b1, _ = base.coverage.ink_box_px()
    m0, _, m1, _ = mark.coverage.ink_box_px()
    base_center = base.pen_x_px + (b0 + b1) / 2.0
    mark_center = mark.pen_x_px + (m0 + m1) / 2.0
    assert mark_center == pytest.approx(base_center, abs=0.6)
    assert mark.baseline_y_px == base.baseline_y_px


def test_subpixel_fraction_never_reaches_one(engine):
    """回归：非整数 DPR 与浮点笔位下，亚像素相位必须严格 < 1。"""
    for ratio in (1.5, 2.5, 3.0):
        layout = engine.layout_annotation(
            Annotation("R12_min", 7.3, 11.7, size_lu=11.5),
            RasterSettings(pixel_ratio=ratio, subpixel_position=True),
        )
        for g in layout.glyphs:
            if g.coverage is not None and not g.is_combining:
                assert 0.0 <= g.coverage.subpixel_frac_x < 1.0


def test_annotation_move_only_changes_origin(engine):
    ann = Annotation("ABC", 10.0, 20.0, size_lu=12)
    l1 = engine.layout_annotation(ann, RasterSettings())
    l2 = engine.layout_annotation(ann.moved(5.0, -3.0), RasterSettings())
    for g1, g2 in zip(l1.glyphs, l2.glyphs):
        assert g2.pen_x_lu == pytest.approx(g1.pen_x_lu + 5.0)
        assert g2.baseline_y_lu == pytest.approx(g1.baseline_y_lu - 3.0)
    assert l2.width_lu == l1.width_lu
