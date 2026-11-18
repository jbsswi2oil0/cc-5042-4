# 基线漂移失败证据与复跑核对

本目录保存“分数笔位 / 缩放 / 高 DPI 切换导致基线漂移”缺陷在**修复前**采集的
失败证据，以及修复后在编辑窗口按工程注记案例（默认三条注记移到分数逻辑
位置）跑到底的运行记录。它们随源码入库，用于避免用“所有位置强制吸附整数”
掩盖问题，并证明旧尺寸字形质量未变。

## 文件

| 文件 | 采集时机 | 含义 |
|---|---|---|
| `pre_fix_fractional_y_scan.json` | 修复前 | 基线 `+0.0…+0.9lu` 的墨迹质心序列；**恒为 35.0672**（纵向分数移动完全冻结） |
| `pre_fix_cross_dpr_baseline.json` | 修复前 | 基线固定 `40.3lu` 在 DPR 1.0/1.5/2.0/2.5/3.0 的墨迹底沿；**一律 40.0lu**（被吸附整数） |
| `pre_fix_integer_position_hashes.json` | 修复前 | 128 组（字符×DPR×字号×hinting）整数位置位图哈希、尺寸、bearing |
| `editor_dpr2.0_fractional.png` / `editor_dpr3.0_fractional.png` | 修复后 | 编辑窗口在同一内核下按分数位置渲染的图面 |
| `post_fix_editor_runs.json` | 修复后 | 上述图面对应的运行状态（`ok`）、告警数与首字 `整数基准 + 相位` 诊断行 |

## 复跑核对

```bash
python3 -m pytest tests/test_pen_phase.py tests/test_editor_diagnostics.py -q
```

- `test_failure_evidence_shows_pretfix_freeze` 读取
  `pre_fix_fractional_y_scan.json`，确认修复前质心冻结，并在当前内核上复跑
  同一扫描，确认不再冻结；
- `test_integer_position_glyph_quality_unchanged` 逐组比对
  `pre_fix_integer_position_hashes.json`，要求 128 组位图逐字节不变；
- 基线偏移与覆盖率的前后对照以墨迹质心 / 支撑边界 / 像素质量（`mass`）给出，
  见 `tests/test_pen_phase.py` 中的跟踪断言。

这些证据由源码提交一并入库；若断言失败即表示修复回退或字形质量改变。
