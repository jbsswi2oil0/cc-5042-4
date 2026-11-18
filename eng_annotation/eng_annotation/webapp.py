"""工程注记编辑窗口的服务层（无第三方 Web 框架）。

控件状态（注记列表、像素比、hinting）保存在内核侧的 Drawing/RasterSettings；
浏览器只负责显示与回传操作。任何渲染失败都被捕获并以 JSON 错误返回，
由前端把失败反馈回“工程注记”面板，而不是让页面静默失败。
"""

from __future__ import annotations

import base64
import io
import os
from dataclasses import asdict

import numpy as np
from PIL import Image

from .engine import AnnotationEngine, EngineError
from .models import Annotation, Drawing, RasterSettings

DEFAULT_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "web", "static")


class AnnotationApp:
    """内核驱动的应用状态机，UI 操作全部经此转发到 AnnotationEngine。"""

    def __init__(self, font_path: str = DEFAULT_FONT, cache_dir: str | None = None):
        self.engine = AnnotationEngine(font_path, cache_dir)
        self.drawing = Drawing(
            width_lu=420.0,
            height_lu=260.0,
            annotations=[
                Annotation("M8-^{0.5}", 24, 60, size_lu=14,
                           color=(20, 20, 20), name="螺纹标注"),
                Annotation("Ø40 ±0.02", 24, 120, size_lu=12,
                           color=(180, 30, 30), name="直径公差"),
                Annotation("R12_{min}", 24, 175, size_lu=11.5,
                           color=(30, 60, 160), name="圆角下限"),
            ],
        )
        self.settings = RasterSettings(pixel_ratio=2.0, hinting=True)
        self.selected = 0
        self.last_run: dict = {"status": "idle", "warnings": []}

    # ---- 状态序列化 -----------------------------------------------------
    def state(self) -> dict:
        return {
            "font": self.engine.font_path,
            "family": self.engine.backend.family_name,
            "drawing": {
                "width_lu": self.drawing.width_lu,
                "height_lu": self.drawing.height_lu,
            },
            "settings": asdict(self.settings),
            "selected": self.selected,
            "annotations": [
                {
                    "index": i,
                    "name": a.name,
                    "text": a.text,
                    "x_lu": a.x_lu,
                    "y_lu": a.y_lu,
                    "size_lu": a.size_lu,
                    "color": list(a.color),
                }
                for i, a in enumerate(self.drawing.annotations)
            ],
        }

    # ---- 渲染 -----------------------------------------------------------
    def render(self) -> dict:
        canvas, layouts, samples = self.engine.render_drawing(
            self.drawing, self.settings
        )
        png = _png_b64(canvas)
        ann_data = []
        warnings: list[dict] = []
        for idx, layout in enumerate(layouts):
            ann = self.drawing.annotations[idx]
            diag = _glyph_diagnostics(
                layout, samples[idx], self.settings.pixel_ratio,
                self.settings.subpixel_position,
            )
            for w in diag["anomalies"]:
                warnings.append({"index": idx, "name": ann.name, **w})
            ann_data.append(
                {
                    "index": idx,
                    "name": ann.name,
                    "width_lu": round(layout.width_lu, 3),
                    "ascent_lu": round(layout.ascent_lu, 3),
                    "descent_lu": round(layout.descent_lu, 3),
                    "device_size_px": round(layout.device_size_px, 3),
                    "glyphs": [
                        {
                            "text": g.text,
                            "pen_x_lu": round(g.pen_x_lu, 3),
                            "baseline_y_lu": round(g.baseline_y_lu, 3),
                            "pen_x_px": round(g.pen_x_px, 3),
                            "baseline_y_px": round(g.baseline_y_px, 3),
                            "pen_x_base_px": g.pen_x_base_px,
                            "baseline_y_base_px": g.baseline_y_base_px,
                            "subpixel_frac_x": g.subpixel_frac_x,
                            "subpixel_frac_y": g.subpixel_frac_y,
                            "advance_lu": round(g.advance_lu, 4),
                            "combining": g.is_combining,
                            "scale": round(g.scale, 3),
                        }
                        for g in layout.glyphs
                    ],
                    "samples": [
                        {
                            "text": s.text,
                            "pen_x_px": round(s.pen_x_px, 2),
                            "baseline_y_px": round(s.baseline_y_px, 2),
                            "box": [s.box_x0, s.box_y0, s.box_x1, s.box_y1],
                            "on_canvas": s.on_canvas,
                        }
                        for s in samples[idx]
                    ],
                    "diagnostics": diag,
                }
            )
        self.last_run = {
            "status": "ok" if not warnings else "warning",
            "pixel_ratio": self.settings.pixel_ratio,
            "annotations": len(ann_data),
            "glyphs": sum(len(a["glyphs"]) for a in ann_data),
            "warnings": warnings,
            "cache": {
                "entries": len(self.engine.cache),
                "hits": self.engine.cache.hits,
                "misses": self.engine.cache.misses,
            },
        }
        return {
            "image": png,
            "image_size_px": list(canvas.shape[1::-1]),
            "pixel_ratio": self.settings.pixel_ratio,
            "annotations": ann_data,
            "cache": self.last_run["cache"],
            "run": self.last_run,
        }

    def render_glyph(self, codepoint: int, size_lu: float) -> dict:
        """单字覆盖查看：返回放大展示用 PNG 与全部独立度量。"""
        cov = self.engine.glyph_coverage(
            codepoint, size_lu, self.settings
        )
        if cov.width_px == 0:
            raise EngineError(f"U+{codepoint:04X} 无可视覆盖（空格/缺字）")
        # 放大 4 倍仅用于界面看清像素结构；元数据保持原设备像素
        zoom = 4
        big = np.kron(cov.coverage, np.ones((zoom, zoom), dtype=np.uint8))
        rgb = np.stack([big, big, big], axis=-1)
        return {
            "codepoint": codepoint,
            "char": chr(codepoint),
            "metrics": {
                "pixel_size_px": cov.pixel_size_px,
                "width_px": cov.width_px,
                "rows_px": cov.rows_px,
                "bitmap_left_px": cov.bitmap_left_px,
                "bitmap_top_px": cov.bitmap_top_px,
                "advance_px": round(cov.advance_px, 4),
                "x_min_bearing_px": round(cov.x_min_bearing_px, 4),
                "y_max_bearing_px": round(cov.y_max_bearing_px, 4),
                "ink_box_px": list(cov.ink_box_px()),
                "hinted": cov.hinted,
                "subpixel_frac_x": cov.subpixel_frac_x,
                "subpixel_frac_y": cov.subpixel_frac_y,
            },
            "image": _png_b64(rgb),
            "zoom": zoom,
        }

    # ---- 控件操作（失败抛 EngineError，由 handler 回传前端） ------------
    def update_settings(self, patch: dict) -> None:
        ratio = float(patch.get("pixel_ratio", self.settings.pixel_ratio))
        if ratio <= 0:
            raise EngineError("pixel_ratio 必须为正数")
        self.settings = RasterSettings(
            pixel_ratio=ratio,
            hinting=bool(patch.get("hinting", self.settings.hinting)),
            subpixel_position=bool(
                patch.get("subpixel_position", self.settings.subpixel_position)
            ),
        )

    def move_annotation(self, index: int, dx_lu: float, dy_lu: float) -> None:
        ann = self._require(index)
        self.drawing.annotations[index] = ann.moved(dx_lu, dy_lu)

    def set_position(self, index: int, x_lu: float, y_lu: float) -> None:
        ann = self._require(index)
        ann.x_lu, ann.y_lu = float(x_lu), float(y_lu)

    def update_annotation(self, index: int, patch: dict) -> None:
        ann = self._require(index)
        text = patch.get("text", ann.text)
        if not text:
            raise EngineError("注记文本不能为空")
        size_lu = float(patch.get("size_lu", ann.size_lu))
        if size_lu <= 0:
            raise EngineError("字号必须为正数")
        color = patch.get("color", list(ann.color))
        ann.text = text
        ann.size_lu = size_lu
        ann.color = (int(color[0]), int(color[1]), int(color[2]))
        ann.name = patch.get("name", ann.name)

    def add_annotation(self, text: str, x_lu: float, y_lu: float,
                       size_lu: float) -> int:
        if not text:
            raise EngineError("注记文本不能为空")
        ann = Annotation(text, float(x_lu), float(y_lu), float(size_lu))
        self.drawing.annotations.append(ann)
        self.selected = len(self.drawing.annotations) - 1
        return self.selected

    def delete_annotation(self, index: int) -> None:
        self._require(index)
        del self.drawing.annotations[index]
        self.selected = max(0, min(self.selected,
                                   len(self.drawing.annotations) - 1))

    def select(self, index: int) -> None:
        self._require(index)
        self.selected = index

    def _require(self, index: int) -> Annotation:
        if not 0 <= index < len(self.drawing.annotations):
            raise EngineError(f"注记索引越界: {index}")
        return self.drawing.annotations[index]


def _glyph_diagnostics(
    layout, samples, pixel_ratio: float, subpixel_position: bool = True
) -> dict:
    """逐字核对栅格文字与采样位置的一致性，标出异常供界面高亮。

    核对两类不变量（与内核修补后的计算对应，不是靠默认值遮错）：

    * 采样位置：覆盖盒必须恰好落在整数基准 + bitmap 偏移处，且设备笔位
      换算回逻辑单位与 pen_x_lu/baseline_y_lu 一致；
    * 栅格文字：启用亚像素笔位时，贴入相位必须等于覆盖实际相位，否则说明
      基线/笔位又被吸附回整数（基线漂移的回归信号）。

    返回保留 6 位小数的中间量，界面表格与断言共用，不使用近似默认值。
    """
    anomalies: list[dict] = []
    rows = []
    for g, s in zip(layout.glyphs, samples):
        cov = g.coverage
        # 由“整数基准 + 亚相位”重建设备笔位，再换算回逻辑单位；这是栅格与
        # 采样共同使用的量，而不是把 pen_x_lu 原样回显成默认值。
        recon_x_px = g.pen_x_base_px + g.subpixel_frac_x
        recon_y_px = g.baseline_y_base_px + g.subpixel_frac_y
        recon_x_lu = round(recon_x_px / pixel_ratio, 6)
        recon_y_lu = round(recon_y_px / pixel_ratio, 6)
        row = {
            "text": g.text,
            "pen_x_base_px": g.pen_x_base_px,
            "baseline_y_base_px": g.baseline_y_base_px,
            "subpixel_frac_x": round(g.subpixel_frac_x, 6),
            "subpixel_frac_y": round(g.subpixel_frac_y, 6),
            "pen_recomputed_lu": recon_x_lu,
            "baseline_recomputed_lu": recon_y_lu,
        }
        if cov is None:
            rows.append(row)
            continue

        # 1) 采样盒必须等于整数基准 + 覆盖 bitmap 偏移（与栅格化共用基准）。
        expect_x0 = g.pen_x_base_px + cov.bitmap_left_px
        expect_y0 = g.baseline_y_base_px - cov.bitmap_top_px
        box_ok = (s.box_x0, s.box_y0) == (expect_x0, expect_y0)
        row["box_expected"] = [expect_x0, expect_y0,
                               expect_x0 + cov.width_px,
                               expect_y0 + cov.rows_px]
        row["box_actual"] = [s.box_x0, s.box_y0, s.box_x1, s.box_y1]
        if not box_ok:
            anomalies.append({
                "kind": "sample_box_mismatch",
                "text": g.text,
                "detail": f"采样盒 {row['box_actual']} ≠ 基准+偏移 {row['box_expected']}",
            })

        # 2) 采样笔位换回逻辑坐标必须与布局笔位一致。放置参考按模式而定：
        # 亚像素模式下是量化后的笔位（误差 ≤1/64）；整数模式下是有意的逐字
        # floor 吸附，因此与浮点笔位相差可到 1px 属预期，不与亚像素混判。
        # 组合记号的横向位置由整数着墨盒中心决定（无亚像素平移），x 不参与。
        if subpixel_position:
            tol = 0.5 / 64.0 + 1e-6
        else:
            tol = 1.0 + 1e-6
        if not g.is_combining and abs(recon_x_px - g.pen_x_px) > tol:
            anomalies.append({
                "kind": "sample_pen_x_mismatch",
                "text": g.text,
                "detail": (
                    f"由基准+相位重建的笔位 {recon_x_px:.6f}px 与布局 "
                    f"{g.pen_x_px:.6f}px 偏差超出{'1/64' if subpixel_position else '1'} 像素"
                ),
            })
        if abs(recon_y_px - g.baseline_y_px) > tol:
            anomalies.append({
                "kind": "sample_baseline_mismatch",
                "text": g.text,
                "detail": (
                    f"由基准+相位重建的基线 {recon_y_px:.6f}px 与布局 "
                    f"{g.baseline_y_px:.6f}px 偏差超出{'1/64' if subpixel_position else '1'} 像素"
                ),
            })

        # 3) 相位一致性只在启用亚像素笔位时成立：关闭亚像素是显式的整数
        # 栅格模式，贴入相位本应为 0。组合记号横向恒为整数相位。
        if subpixel_position:
            if not g.is_combining and \
                    abs(g.subpixel_frac_x - cov.subpixel_frac_x) > 1e-9:
                anomalies.append({
                    "kind": "pen_phase_drift",
                    "text": g.text,
                    "detail": (
                        f"贴入相位 x={g.subpixel_frac_x} ≠ 栅格相位 "
                        f"x={cov.subpixel_frac_x}"
                    ),
                })
            if abs(g.subpixel_frac_y - cov.subpixel_frac_y) > 1e-9:
                anomalies.append({
                    "kind": "baseline_phase_drift",
                    "text": g.text,
                    "detail": (
                        f"贴入相位 y={g.subpixel_frac_y} ≠ 栅格相位 "
                        f"y={cov.subpixel_frac_y}（基线被吸附整数？）"
                    ),
                })
        rows.append(row)
    return {"rows": rows, "anomalies": anomalies}


def _png_b64(rgb: np.ndarray) -> str:
    buf = io.BytesIO()
    Image.fromarray(rgb, mode="RGB").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")
