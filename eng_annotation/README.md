# 工程图注记渲染模块

基于 **FreeType（freetype-py）成熟字体栅格库**生成真实字形灰度覆盖，再按内核
计算的明确位置合成到工程图面。提供一个由同一内核驱动的矢量编辑窗口（浏览器
界面），可切换像素比、移动注记、查看单字覆盖与每字采样位置。

## 1. 三尺度严格分离

| 尺度 | 含义 | 代码体现 |
|---|---|---|
| 逻辑尺寸 lu | 注记设计坐标、逻辑字号 | `Annotation.x_lu/y_lu/size_lu` |
| 设备像素 px | 实际位图坐标 | `pen_x_px = pen_x_lu × pixel_ratio` |
| 字体尺度 | FreeType 设备像素字号，允许非整数 | `device_size = size_lu × ratio × 段缩放` |

高 DPI 的实现方式是**以更大的设备像素字号调用 FreeType 重新栅格化**，
从不把低分辨率位图放大。`test_high_dpi_regrids_instead_of_upscaling` 会
逐像素证明 2x 输出不是 1x 位图的 Kron 放大。

## 2. 模块结构

```
eng_annotation/
  models.py        数据模型：GlyphCoverage / PlacedGlyph / TextLayout / Annotation
  font_backend.py  FreeType 封装：真实灰度栅格、独立度量、亚像素 transform、hinting
  raster_cache.py  真实位图缓存（内存 + 磁盘 .npy/.json），键含字号/DPR/hinting/相位
  layout.py        浮点笔位布局、上下标标记、组合记号锚定、字距、累计行长审计
  composite.py     显式 source-over 灰度混合、负边距不裁切、采样位置记录
  engine.py        对外门面 AnnotationEngine
  webapp.py        编辑窗口应用状态机（内核驱动控件）
  server.py        标准库 http.server 路由，失败以 JSON 回传前端
web/static/        编辑窗口前端（HTML/CSS/JS，本身不做任何文字栅格化）
scripts/
  run_editor.py    启动矢量编辑窗口
  snapshot.py      无头快照（CI/无显示环境，输出与界面同源的真实栅格）
tests/             31 个 pytest 用例
```

## 3. 关键设计决策

- **独立度量数据**：前进量 `advance_px`（浮点，不取整）、基线 bearing
  `x_min/y_max_bearing_px`（浮点）与整数化的 `bitmap_left/top_px`、
  着墨边界 `ink_box_px()` 分开保存。
- **累计行长不取整**：笔位全程浮点累加，取整只在覆盖贴入位图时统一发生一次。
  `test_cumulative_rounding_error_quantified` 显式量化“逐字独立取整”的累计
  漂移（50 字约 0.8px），证明该误差被测量而非忽略。
- **负边距与组合记号**：组合记号（如 U+0308）带负 `bitmap_left`，合成时
  源/目标切片按笔位一一对应，越界部分保留不裁；标记按基字符着墨盒中心锚定。
- **灰度混合**：`out = src·a + dst·(1−a)`，a = coverage/255，逐像素可核对
  （`test_source_over_formula`）。
- **hinting 可比**：`RasterSettings.hinting` 直通 FreeType 自动 hinting 开关；
  亚像素笔位经 `FT_Set_Transform` 平移并按规则强制无 hinting。测试比较两种
  栅格差异，同时约束 bearing/前进量偏差 ≤1px，防止用锐化掩盖定位错误。
- **缓存真实栅格**：磁盘缓存存的是 FreeType 输出的 .npy 灰度图与 JSON 度量，
  不是截图；新引擎实例可冷启动命中（`test_cache_roundtrip_real_raster`）。

## 4. 安装

```bash
python3 -m pip install -r requirements.txt
# 系统需有 libfreetype 与一个 TTF 字体（Debian: libfreetype6 + fonts-dejavu-core）
```

## 5. 启动矢量编辑窗口（界面，不只是脚本）

```bash
python3 scripts/run_editor.py --port 8765
# 然后浏览器打开 http://127.0.0.1:8765
```

可选参数：`--host`、`--port`、`--font /path/font.ttf`、`--cache ./data/glyph_cache`。

界面能力（全部经 HTTP 调用内核，前端不栅格化任何文字）：

- 工具栏切换 **像素比 1.0/1.5/2.0/2.5/3.0**、hinting、亚像素笔位、采样叠加；
- 图面**拖动注记**（逻辑坐标，松手即由内核重新栅格化），右侧表单改文本/字号/
  位置/颜色，方向按钮微调；
- **单字覆盖查看**：输入字符与逻辑字号，展示该 DPR 下的真实灰度像素结构与
  前进量、bearing、着墨盒等独立度量；
- 底部表格呈现每字逻辑笔位、设备笔位、浮点前进量与覆盖采样盒；
- 任何内核失败（非法像素比、空文本、越界索引等）以页面顶部红色横幅
  “工程注记内核反馈：…”回显，不静默失败。

无显示环境用无头快照获得与界面**同一内核**的产物：

```bash
python3 scripts/snapshot.py --out data/snapshots
# 产出多 DPR 图面、hinting 开关对比、组合记号覆盖及 summary.json
```

## 6. 测试

```bash
python3 -m pytest tests/ -q
```

覆盖：细数字（7–12px）、上/下标、1.0/1.5/2.0/3.0 密度、非整数设备字号、
hinting 开关对比、亚像素相位、负 bearing 不裁切、组合记号锚定、source-over
公式、缓存往返、参考字形与直接 FreeType 调用逐像素比对、累计取整误差量化。
