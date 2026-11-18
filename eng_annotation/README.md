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
  亚像素笔位经 `FT_Set_Transform` 横向 + 纵向平移并按规则强制无 hinting。
  测试比较两种栅格差异，同时约束 bearing/前进量偏差 ≤1px，防止用锐化掩盖
  定位错误。
- **缓存真实栅格**：磁盘缓存存的是 FreeType 输出的 .npy 灰度图与 JSON 度量，
  不是截图；新引擎实例可冷启动命中（`test_cache_roundtrip_real_raster`）。

### 3.1 分数笔位 / 缩放 / 高 DPI 的基线漂移（已修正）

**根因**：栅格化只在横向上用 `FT_Set_Transform` 平移亚像素相位，纵向相位
被忽略；贴入 `target_box_px` 又把浮点基线整体 `floor`。于是基线只随整数
设备像素跳变——纵向分数移动完全不改变墨迹，缩放或 DPR 切换时基线被吸附到
整数网格。相邻的缺陷是缓存键用 `%.4f` 十进制小数，`0.0` 与 `0.00004` 会
判为同键，但后者按规则走无 hinting，命中时返回了与请求不符的位图。

**修正**（不放宽为“所有位置强制吸附整数”）：

- 笔位先在 1/64 设备像素网格上量化，再拆成 `整数基准 + [0,1) 相位`
  （`models.split_pen_px/quantize_phase`），浮点噪声（如 `107.00000000000001`）
  归到最近的整数基准，不跨像素（取整边界修正）。
- 横向与纵向相位都经 `FT_Set_Transform` 施加（纵向取负，因 FT 的 y 轴朝上）；
  任一相位非零即强制无 hinting。
- 贴入位置改为 `整数基准 + bitmap 偏移`，**不再二次 floor**；贴入相位与栅格
  相位严格相等。
- 缓存键改用同一 1/64 相位网格，并把纵向相位纳入键。
- 整数位置、整数字号的字形位图逐像素不变：128 组（字符×DPR×字号×hinting）
  的哈希与修复前完全一致（`test_integer_position_glyph_quality_unchanged`）。

**失败证据**（随源码入库于 `data/evidence/`，由 `test_pen_phase.py` 复跑核对）：

| 文件 | 内容 | 修复前 |
|---|---|---|
| `pre_fix_fractional_y_scan.json` | 基线 +0.0…+0.9lu 的墨迹质心 | 恒为 35.0672（冻结） |
| `pre_fix_cross_dpr_baseline.json` | 基线 40.3lu 在各 DPR 的墨迹底沿 | 一律 40.0lu（吸附整数） |
| `pre_fix_integer_position_hashes.json` | 128 组整数位置位图哈希 | 修复后逐字节不变 |


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
- 底部表格呈现每字逻辑笔位、设备笔位、`整数基准 + 亚像素相位`、浮点前进量
  与覆盖采样盒；
- **运行状态可见**：工具栏下方常驻状态条显示 `ok / warning`、DPR、注记与字
  数；由 `_glyph_diagnostics` 逐字核对采样盒、`基准+相位` 重建笔位与相位
  一致性。不一致时状态条转为 warning，并列出「栅格文字 / 采样位置异常」
  条目（`kind`、注记、字符、明细），对应字在表格中高亮、在叠加层上加品红
  虚线框。该核对由 `整数基准 + 相位` 重算，不以默认值回显遮错。
- 任何内核失败（非法像素比、空文本、越界索引等）以页面顶部红色横幅
  “工程注记内核反馈：…”回显，不静默失败。

**图形入口**（改变像素比、移动注记后核验栅格文字与采样位置）：

1. 浏览器打开编辑器，工具栏切换 DPR（1.0/1.5/2.0/2.5/3.0）；
2. 在图面拖动某条注记到分数逻辑位置（或右侧 X/Y 输入小数）；
3. 查看状态条是否 `ok`；勾选“采样位置”叠加，采样十字与覆盖盒应与栅格文字
   贴合；底部表格的 `整数基准+相位` 一列随分数位置连续变化。

无显示环境用无头快照获得与界面**同一内核**的产物：

```bash
python3 scripts/snapshot.py --out data/snapshots
# 产出多 DPR 图面、整数与分数逻辑位置（工程注记旧样例跑到底）、hinting
# 开关对比、组合记号覆盖，以及含 run_status 的 summary.json
```

## 6. 测试

```bash
python3 -m pytest tests/ -q
```

覆盖：细数字（7–12px）、上/下标、1.0/1.5/2.0/3.0 密度、非整数设备字号、
hinting 开关对比、亚像素相位（横向 + 纵向）、负 bearing 不裁切、组合记号
锚定、source-over 公式、缓存往返、参考字形与直接 FreeType 调用逐像素比对、
累计取整误差量化。

基线漂移专项（`tests/test_pen_phase.py`、`tests/test_editor_diagnostics.py`）：
分数纵向移动墨迹跟踪、横纵双向跟踪、1 设备像素平移逐像素精确、1/64 取整
边界与浮点噪声、贴入相位 = 栅格相位、缓存键共用相位网格（含纵向）、磁盘
缓存区分纵向相位、整数位置字形质量不变（对照 `data/evidence/` 哈希）、
编辑窗口运行状态与异常标注（含强制整数吸附时必报异常）。

