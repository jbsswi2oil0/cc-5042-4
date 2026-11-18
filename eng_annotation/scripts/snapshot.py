#!/usr/bin/env python3
"""无头快照：与编辑窗口同一个 AnnotationApp 内核，直接产出 PNG 与数据。

用于无显示环境（CI/容器）下验证界面同源的渲染结果；输出的是内核真实
栅格位图，不是任何系统文本截图。

用法：
    python3 scripts/snapshot.py --out ./data/snapshots
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import base64  # noqa: E402

from eng_annotation.webapp import AnnotationApp, DEFAULT_FONT  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "snapshots"))
    ap.add_argument("--font", default=DEFAULT_FONT)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    cache_dir = os.path.join(os.path.dirname(args.out), "glyph_cache")
    app = AnnotationApp(args.font, cache_dir)
    summary = {}
    for ratio in (1.0, 1.5, 2.0, 3.0):
        app.update_settings({"pixel_ratio": ratio, "hinting": True})
        result = app.render()
        png_path = os.path.join(args.out, f"sheet_dpr{ratio}.png")
        with open(png_path, "wb") as fh:
            fh.write(base64.b64decode(result["image"]))
        summary[f"dpr{ratio}"] = {
            "path": png_path,
            "device_size_px": result["image_size_px"],
            "cache": result["cache"],
            "run_status": result["run"]["status"],
            "warnings": result["run"]["warnings"],
        }

    # 工程注记旧样例：把三条默认注记移到分数逻辑位置，按 DPR 跑到底，
    # 记录每 DPR 的运行状态与逐字采样；异常会进入 run_status 而非静默。
    original = [(a.x_lu, a.y_lu) for a in app.drawing.annotations]
    fractional = [(24.3, 60.37), (24.3, 120.4), (24.3, 175.35)]
    for i, (x, y) in enumerate(fractional):
        app.set_position(i, x, y)
    summary["fractional_sample"] = {}
    for ratio in (1.0, 1.5, 2.0, 2.5, 3.0):
        app.update_settings({
            "pixel_ratio": ratio, "hinting": True,
            "subpixel_position": True,
        })
        result = app.render()
        png_path = os.path.join(args.out, f"sheet_fractional_dpr{ratio}.png")
        with open(png_path, "wb") as fh:
            fh.write(base64.b64decode(result["image"]))
        first_rows = [
            {
                "text": row["text"],
                "base_y": row["baseline_y_base_px"],
                "frac_y": row["subpixel_frac_y"],
                "recomputed_lu": row["baseline_recomputed_lu"],
            }
            for ann in result["annotations"]
            for row in ann["diagnostics"]["rows"][:1]
        ]
        summary["fractional_sample"][f"dpr{ratio}"] = {
            "path": png_path,
            "device_size_px": result["image_size_px"],
            "run_status": result["run"]["status"],
            "warning_count": len(result["run"]["warnings"]),
            "first_rows": first_rows,
        }
    for i, (x, y) in enumerate(original):
        app.set_position(i, x, y)

    # hinting 开关对比（细字号 9lu）
    for hinted in (True, False):
        app.update_settings({"pixel_ratio": 1.0, "hinting": hinted})
        g = app.render_glyph(ord("8"), 9.0)
        p = os.path.join(args.out, f"glyph8_9px_hinting{hinted}.png")
        with open(p, "wb") as fh:
            fh.write(base64.b64decode(g["image"]))
        summary.setdefault("hinting_compare", {})[str(hinted)] = {
            "path": p, "metrics": g["metrics"],
        }

    # 组合记号负 bearing 单字
    app.update_settings({"pixel_ratio": 1.0, "hinting": True})
    g = app.render_glyph(0x0308, 24.0)
    p = os.path.join(args.out, "combining_diaeresis_24px.png")
    with open(p, "wb") as fh:
        fh.write(base64.b64decode(g["image"]))
    summary["combining"] = {"path": p, "metrics": g["metrics"]}

    with open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
