"""编辑窗口运行状态与异常标注的回归。

界面必须能看见运行状态，并在栅格文字 / 采样位置不一致时明确标出异常，
而不是静默或用以默认值填充的表格遮错。这里直接驱动 AnnotationApp 内核。
"""

import numpy as np
import pytest

from eng_annotation.webapp import AnnotationApp
from eng_annotation.models import RasterSettings


@pytest.fixture()
def app(tmp_path, font_path):
    return AnnotationApp(font_path, cache_dir=str(tmp_path / "glyph_cache"))


def test_render_reports_run_status_ok(app):
    result = app.render()
    run = result["run"]
    assert run["status"] == "ok"
    assert run["warnings"] == []
    assert run["glyphs"] > 0
    # 每条注记都带逐字诊断行，界面表格与断言共用
    for ann in result["annotations"]:
        assert len(ann["diagnostics"]["rows"]) == len(ann["glyphs"])
        assert ann["diagnostics"]["anomalies"] == []


def test_row_recomputes_pen_from_base_plus_phase(app):
    """诊断行由整数基准 + 相位重算，而不是回显默认值。"""
    app.set_position(0, 24.3, 60.37)
    result = app.render()
    for ann in result["annotations"]:
        for g, row in zip(ann["glyphs"], ann["diagnostics"]["rows"]):
            # 重建值 = (基准 + 相位) / DPR
            dpr = result["pixel_ratio"]
            expect_x = (row["pen_x_base_px"] + row["subpixel_frac_x"]) / dpr
            expect_y = (
                row["baseline_y_base_px"] + row["subpixel_frac_y"]
            ) / dpr
            assert row["pen_recomputed_lu"] == pytest.approx(expect_x, abs=1e-6)
            assert row["baseline_recomputed_lu"] == pytest.approx(
                expect_y, abs=1e-6
            )


def test_baseline_snap_regression_is_flagged(app, monkeypatch):
    """强制把基线吸附到整数（旧缺陷）时，诊断必须报异常而非通过。"""
    import eng_annotation.layout as layout_mod

    def snapped(pen_px):
        return int(np.floor(pen_px)), 0.0

    monkeypatch.setattr(layout_mod, "split_pen_px", snapped)
    app.set_position(0, 24.0, 60.3)
    result = app.render()
    assert result["run"]["status"] == "warning"
    kinds = {w["kind"] for w in result["run"]["warnings"]}
    assert "sample_baseline_mismatch" in kinds
    assert result["run"]["warnings"], "异常必须进入运行状态，不能静默"


def test_diagnostics_clean_across_modes_and_dpr(font_path):
    for subpixel in (True, False):
        for ratio in (1.0, 1.5, 2.0, 2.5, 3.0):
            app = AnnotationApp(font_path)
            app.update_settings({
                "pixel_ratio": ratio, "hinting": True,
                "subpixel_position": subpixel,
            })
            app.set_position(0, 24.3, 60.37)
            result = app.render()
            assert result["run"]["warnings"] == [], (
                subpixel, ratio, result["run"]["warnings"]
            )


def test_anomaly_payload_has_actionable_fields(app, monkeypatch):
    import eng_annotation.layout as layout_mod

    monkeypatch.setattr(
        layout_mod, "split_pen_px", lambda p: (int(np.floor(p)), 0.0)
    )
    app.set_position(0, 24.0, 60.3)
    result = app.render()
    for w in result["run"]["warnings"]:
        assert set(("kind", "text", "detail", "index", "name")) <= set(w)
        assert w["detail"]


def test_glyph_viewer_reports_vertical_phase(app):
    app.update_settings({"pixel_ratio": 2.0, "subpixel_position": True})
    # 直接以半像素相位取覆盖，查看器指标应包含 y 相位
    cov = app.engine.glyph_coverage(
        ord("H"), 12.0, RasterSettings(2.0, False, True), 0.0, 0.5
    )
    assert cov.subpixel_frac_y == pytest.approx(0.5)
    app.render_glyph  # 存在性
    data = app.render_glyph(ord("H"), 12.0)
    assert "subpixel_frac_y" in data["metrics"]
