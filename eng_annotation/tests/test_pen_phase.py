"""分数笔位 / 缩放 / 高 DPI 切换下的基线漂移回归。

失败证据（修复前采集，随源码入库于 data/evidence/）：
* ``pre_fix_fractional_y_scan.json``：纵向逻辑坐标从 +0.0 连续加到 +0.9，
  墨迹质心恒为 35.0672（完全不动），而横向同幅移动正常跟踪；
* ``pre_fix_cross_dpr_baseline.json``：基线固定 40.3lu 时各 DPR 的墨迹底沿
  都落在 40.0lu（被吸附到整数），而非连续基线；
* ``pre_fix_integer_position_hashes.json``：128 组整数位置/字号/DPR/hinting
  的位图哈希，用于证明修复后“旧尺寸字形质量不变”。

本模块把这些证据转成可复跑的断言，并覆盖 1/64 取整边界与缓存键。
"""

import hashlib
import json
import os

import numpy as np
import pytest

from eng_annotation import Annotation, RasterSettings, AnnotationEngine
from eng_annotation.models import PHASE_UNITS, split_pen_px, quantize_phase

EVIDENCE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "evidence",
)


def _ink(canvas: np.ndarray) -> dict:
    """灰度覆盖的墨迹质心 / 支撑边界，设备像素单位。"""
    a = np.clip((255.0 - canvas.astype(np.float64)[:, :, 0]) / 255.0, 0, 1)
    if a.sum() < 1e-9:
        return {"mass": 0.0}
    ys = np.arange(a.shape[0])[:, None]
    xs = np.arange(a.shape[1])[None, :]
    rows = np.where(a.sum(axis=1) > 1e-6)[0]
    return {
        "centroid_y": float((a * ys).sum() / a.sum()),
        "centroid_x": float((a * xs).sum() / a.sum()),
        "mass": float(a.sum()),
        "top": int(rows[0]),
        "bottom": int(rows[-1]),
    }


def _render(engine, ann, ratio, hinting=True, subpixel=True, canvas=(80, 120)):
    return engine.render_annotation(
        ann,
        RasterSettings(ratio, hinting, subpixel),
        canvas_size_lu=canvas,
    )


# --------------------------------------------------------------------------
# 最小复现：纵向分数移动必须带动墨迹；横向作为对照
# --------------------------------------------------------------------------
def test_fractional_baseline_move_tracks_ink(engine):
    """最小复现：基线 +0.1…+0.9lu，墨迹质心必须随之单调下移。"""
    centroids = []
    for dy in (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        canvas, _, _ = _render(engine, Annotation("H", 20.0, 40.0 + dy), 1.0,
                               hinting=False)
        centroids.append(_ink(canvas)["centroid_y"])
    # 修复前该序列为常值（35.0672 × 10）；要求严格单调递增且幅度可信
    assert all(b > a for a, b in zip(centroids, centroids[1:])), centroids
    assert centroids[-1] - centroids[0] > 0.8  # 0.9lu @ DPR1 至少 0.8px


def test_horizontal_and_vertical_both_track(engine):
    """横向与纵向同一幅度移动应产生相近的墨迹位移（不再只有横向生效）。"""
    base, _, _ = _render(engine, Annotation("H", 20.0, 40.0), 1.0, hinting=False)
    b = _ink(base)
    hx, _, _ = _render(engine, Annotation("H", 20.6, 40.0), 1.0, hinting=False)
    hy, _, _ = _render(engine, Annotation("H", 20.0, 40.6), 1.0, hinting=False)
    assert abs(_ink(hx)["centroid_x"] - b["centroid_x"] - 0.6) < 0.15
    assert abs(_ink(hy)["centroid_y"] - b["centroid_y"] - 0.6) < 0.15


# --------------------------------------------------------------------------
# 高 DPI 偏移传播：1 设备像素平移必须得到 1 像素的位图平移
# --------------------------------------------------------------------------
@pytest.mark.parametrize("ratio", [1.0, 1.5, 2.0, 2.5, 3.0])
def test_one_device_pixel_translation_is_exact(engine, ratio):
    step = 1.0 / ratio
    a, _, _ = _render(engine, Annotation("H", 20.0, 60.0), ratio)
    for dx, dy, ex, ey in [(step, 0.0, 1, 0), (0.0, step, 0, 1),
                           (step, step, 1, 1)]:
        b, _, _ = _render(engine, Annotation("H", 20.0 + dx, 60.0 + dy), ratio)
        H, W = a.shape[:2]
        ax0, ax1 = max(0, -ex), min(W, W - ex)
        ay0, ay1 = max(0, -ey), min(H, H - ey)
        bx0, bx1 = max(0, ex), min(W, W + ex)
        by0, by1 = max(0, ey), min(H, H + ey)
        assert np.array_equal(a[ay0:ay1, ax0:ax1], b[by0:by1, bx0:bx1]), (
            ratio, dx, dy,
        )


# --------------------------------------------------------------------------
# 取整边界：累计浮点误差落在整数附近不得跨像素
# --------------------------------------------------------------------------
def test_split_pen_absorbs_float_noise_at_integer_boundary():
    """107.00000000000001 这类浮点误差必须归到整数基准 107，相位 0。"""
    for noisy in (107.00000000000001, 63.99999999999999, 12.000000000000002):
        base, frac = split_pen_px(noisy)
        assert base == int(round(noisy))
        assert frac == 0.0
    # base + frac 精确等于量化后的笔位，不产生半像素漂移
    for pen in (0.1, 11.5, 40.3, 60.37, 107.0, 119.328125):
        base, frac = split_pen_px(pen)
        assert abs((base + frac) - pen) <= 0.5 / PHASE_UNITS + 1e-12


def test_quantize_phase_matches_split_and_stays_in_range():
    for frac in np.linspace(0.0, 0.999999, 97):
        q = quantize_phase(float(frac))
        assert 0.0 <= q < 1.0
        assert abs(q * PHASE_UNITS - round(q * PHASE_UNITS)) < 1e-9
    # split_pen_px 产出的相位与 quantize_phase 对同一小数一致（共用 half-up）
    for pen in (40.1, 40.3, 60.37, 119.328125, 127.99):
        _, frac = split_pen_px(pen)
        assert frac == quantize_phase(pen - int(np.floor(pen)))


def test_placement_phase_equals_raster_phase(engine):
    """贴入相位必须与覆盖实际相位一致——否则基线被二次吸附。"""
    for ratio in (1.0, 1.5, 2.0, 2.5, 3.0):
        layout = engine.layout_annotation(
            Annotation("H0", 24.3, 60.37), RasterSettings(ratio, True, True)
        )
        for g in layout.glyphs:
            if g.coverage is None or g.is_combining:
                continue
            assert g.subpixel_frac_x == pytest.approx(
                g.coverage.subpixel_frac_x
            )
            assert g.subpixel_frac_y == pytest.approx(
                g.coverage.subpixel_frac_y
            )


# --------------------------------------------------------------------------
# 缓存键：1/64 相位网格一致，边界附近不再撞键返回错误位图
# --------------------------------------------------------------------------
def test_cache_key_uses_shared_phase_grid(engine):
    k0 = engine.cache.key(ord("A"), 12.0, True, 0.0, 0.0)
    k_tiny = engine.cache.key(ord("A"), 12.0, True, 0.00004, 0.0)
    k_half = engine.cache.key(ord("A"), 12.0, True, 0.5, 0.0)
    k_y = engine.cache.key(ord("A"), 12.0, True, 0.0, 0.25)
    # 0.00004 在 1/64 网格上归 0，与精确 0 同键（两者栅格结果相同）
    assert k0 == k_tiny
    # 真正的不同相位必须分开，纵向相位也参与建键
    assert len({k0, k_half, k_y}) == 3


def test_cache_hit_returns_raster_matching_request(engine):
    """边界相位请求命中缓存时，返回的 hinted 标志必须与键所代表的一致。"""
    # 先在极小相位（量化=0，hinting 生效）栅格化
    a = engine.glyph_coverage(ord("A"), 12.0, RasterSettings(1.0, True, True),
                              0.00004)
    b = engine.glyph_coverage(ord("A"), 12.0, RasterSettings(1.0, True, True),
                              0.0)
    assert a.hinted is True and b.hinted is True
    assert np.array_equal(a.coverage, b.coverage)
    # 真正的半像素相位必须命中不同的键并得到无 hinting 位图
    c = engine.glyph_coverage(ord("A"), 12.0, RasterSettings(1.0, True, True),
                              0.5)
    assert c.hinted is False
    assert not np.array_equal(a.coverage, c.coverage)


def test_disk_cache_distinguishes_vertical_phase(engine, tmp_path):
    rs = RasterSettings(pixel_ratio=1.0, hinting=False)
    base = engine.glyph_coverage(ord("H"), 12.0, rs, 0.0, 0.0)
    shifted = engine.glyph_coverage(ord("H"), 12.0, rs, 0.0, 0.5)
    assert not np.array_equal(base.coverage, shifted.coverage)
    # 冷启动新引擎逐相位命中，位图各自还原
    engine2 = AnnotationEngine(engine.font_path,
                               cache_dir=str(tmp_path / "glyph_cache"))
    base2 = engine2.glyph_coverage(ord("H"), 12.0, rs, 0.0, 0.0)
    shifted2 = engine2.glyph_coverage(ord("H"), 12.0, rs, 0.0, 0.5)
    assert np.array_equal(base.coverage, base2.coverage)
    assert np.array_equal(shifted.coverage, shifted2.coverage)


# --------------------------------------------------------------------------
# 旧尺寸字形质量保持：整数位置逐像素等于修复前参考
# --------------------------------------------------------------------------
@pytest.mark.skipif(
    not os.path.isfile(os.path.join(EVIDENCE,
                                    "pre_fix_integer_position_hashes.json")),
    reason="缺少修复前基准证据",
)
def test_integer_position_glyph_quality_unchanged(engine):
    ref = json.load(open(
        os.path.join(EVIDENCE, "pre_fix_integer_position_hashes.json"),
        encoding="utf-8",
    ))
    checked = 0
    for key, exp in ref.items():
        ch, ratio, size, hint = key.split("|")
        cov = engine.glyph_coverage(
            ord(ch), float(size),
            RasterSettings(float(ratio), hint == "True", True),
        )
        sha = hashlib.sha256(cov.coverage.tobytes()).hexdigest()[:16]
        assert sha == exp["sha"], f"位图改变: {key}"
        assert list(cov.coverage.shape) == exp["shape"], key
        assert cov.bitmap_left_px == exp["left"], key
        assert cov.bitmap_top_px == exp["top"], key
        checked += 1
    assert checked == len(ref) and checked >= 100


@pytest.mark.skipif(
    not os.path.isfile(os.path.join(EVIDENCE,
                                    "pre_fix_fractional_y_scan.json")),
    reason="缺少修复前失败证据",
)
def test_failure_evidence_shows_pretfix_freeze(engine):
    """复核失败证据本身：修复前纵向分数移动确实冻结，修复后不再冻结。"""
    pre = json.load(open(
        os.path.join(EVIDENCE, "pre_fix_fractional_y_scan.json"),
        encoding="utf-8",
    ))
    pre_centroids = {round(row[0], 1): row[1] for row in pre}
    # 证据：修复前 10 个分数位置质心完全相同
    assert len(set(round(v, 6) for v in pre_centroids.values())) == 1
    # 修复后同一扫描不再冻结
    now = []
    for dy in (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        canvas, _, _ = _render(engine, Annotation("H", 10.0, 40.0 + dy), 1.0,
                               hinting=False, canvas=(60, 90))
        now.append(_ink(canvas)["centroid_y"])
    assert len(set(round(v, 6) for v in now)) > 1
