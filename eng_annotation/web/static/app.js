/* 工程注记编辑窗口前端：所有状态变更都 POST 给内核，再用内核返回的
   真实栅格 PNG 与采样数据刷新界面。前端本身不做任何文字栅格化。 */

const $ = (id) => document.getElementById(id);
let STATE = null;
let RENDER = null;

async function api(path, body) {
  const opt = body
    ? { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body) }
    : {};
  const resp = await fetch(path, opt);
  const data = await resp.json();
  if (!resp.ok || data.error) {
    showError(data.error || `请求失败 ${resp.status}`);
    throw new Error(data.error || "api error");
  }
  hideError();
  return data;
}

function showError(msg) {
  const el = $("error-banner");
  el.textContent = "工程注记内核反馈：" + msg;
  el.classList.remove("hidden");
}
function hideError() { $("error-banner").classList.add("hidden"); }

async function refresh(full = true) {
  if (full) STATE = await api("/api/state");
  RENDER = await api("/api/render");
  renderAll();
}

function renderAll() {
  $("font-info").textContent =
    `字体 ${STATE.family} · 逻辑画布 ${STATE.drawing.width_lu}×${STATE.drawing.height_lu} · ` +
    `当前 DPR ${RENDER.pixel_ratio} · 设备位图 ${RENDER.image_size_px[0]}×${RENDER.image_size_px[1]}px`;
  $("cache-info").textContent =
    `缓存 ${RENDER.cache.entries} 项（命中 ${RENDER.cache.hits} / 未命中 ${RENDER.cache.misses}）`;

  const img = $("sheet");
  img.src = "data:image/png;base64," + RENDER.image;
  img.onload = drawOverlay;

  renderAnnotationList();
  renderEditor();
  renderLayoutTables();
}

function renderAnnotationList() {
  const ul = $("ann-list");
  ul.innerHTML = "";
  STATE.annotations.forEach((a, i) => {
    const li = document.createElement("li");
    li.textContent = `${a.name || ("注记" + i)}  「${a.text}」  @(${a.x_lu}, ${a.y_lu})  ${a.size_lu}lu`;
    if (i === STATE.selected) li.classList.add("selected");
    li.onclick = async () => { await api("/api/select", { index: i }); await refresh(); };
    ul.appendChild(li);
  });
}

function renderEditor() {
  const a = STATE.annotations[STATE.selected];
  if (!a) return;
  $("ed-text").value = a.text;
  $("ed-size").value = a.size_lu;
  $("ed-x").value = a.x_lu;
  $("ed-y").value = a.y_lu;
  const [r, g, b] = a.color;
  $("ed-color").value = "#" + [r, g, b].map(v => v.toString(16).padStart(2, "0")).join("");
}

function renderLayoutTables() {
  const wrap = $("layout-tables");
  wrap.innerHTML = "";
  RENDER.annotations.forEach((ann) => {
    const card = document.createElement("div");
    card.className = "layout-card";
    let rows = ann.glyphs.map((g, i) => {
      const s = ann.samples[i];
      return `<tr>
        <td>${g.text === " " ? "␠" : g.text}</td>
        <td>${g.pen_x_lu}</td><td>${g.baseline_y_lu}</td>
        <td>${g.pen_x_px}</td><td>${g.baseline_y_px}</td>
        <td>${g.advance_lu}</td>
        <td>${s ? s.box.join(",") : "-"}</td>
        <td>${g.combining ? "组合" : ""}${g.scale !== 1 ? "×" + g.scale : ""}</td>
      </tr>`;
    }).join("");
    card.innerHTML = `<h3>${ann.name || ann.index} ｜ 逻辑宽 ${ann.width_lu}lu ｜ ` +
      `设备字号 ${ann.device_size_px}px ｜ 上沿 ${ann.ascent_lu} 下沿 ${ann.descent_lu}</h3>
      <table><thead><tr>
        <th>字</th><th>笔位x(lu)</th><th>基线y(lu)</th>
        <th>笔位x(px)</th><th>基线y(px)</th><th>前进(lu)</th>
        <th>覆盖盒(px)</th><th>标记</th>
      </tr></thead><tbody>${rows}</tbody></table>`;
    wrap.appendChild(card);
  });
}

// ---- 采样位置叠加 -------------------------------------------------------
function drawOverlay() {
  const cv = $("overlay");
  const img = $("sheet");
  // 叠加层与设备位图同尺寸，CSS 统一缩放显示
  cv.width = img.naturalWidth;
  cv.height = img.naturalHeight;
  const ctx = cv.getContext("2d");
  ctx.clearRect(0, 0, cv.width, cv.height);
  if (!$("show-samples").checked) return;
  const ratio = RENDER.pixel_ratio;
  RENDER.annotations.forEach((ann, idx) => {
    const selected = idx === STATE.selected;
    ctx.strokeStyle = selected ? "rgba(47,102,210,0.95)" : "rgba(210,90,40,0.7)";
    ctx.fillStyle = ctx.strokeStyle;
    ctx.lineWidth = selected ? 2 : 1;
    ann.samples.forEach((s) => {
      if (s.box[2] > s.box[0]) {
        ctx.strokeRect(s.box[0] + 0.5, s.box[1] + 0.5,
                       s.box[2] - s.box[0] - 1, s.box[3] - s.box[1] - 1);
      }
      // 笔位采样点（设备像素浮点位置，画十字）
      ctx.beginPath();
      ctx.moveTo(s.pen_x_px - 3, s.baseline_y_px);
      ctx.lineTo(s.pen_x_px + 3, s.baseline_y_px);
      ctx.moveTo(s.pen_x_px, s.baseline_y_px - 3);
      ctx.lineTo(s.pen_x_px, s.baseline_y_px + 3);
      ctx.stroke();
    });
    // 逻辑基线（设备像素 = 逻辑 × DPR）
    const first = ann.samples[0];
    if (first) {
      ctx.setLineDash([6, 4]);
      ctx.beginPath();
      ctx.moveTo(0, first.baseline_y_px);
      ctx.lineTo(cv.width, first.baseline_y_px);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  });
}

// ---- 拖动注记（逻辑坐标，内核重栅格化） ---------------------------------
function attachDrag() {
  const cv = $("overlay");
  cv.classList.add("interactive");
  let drag = null;

  cv.addEventListener("mousedown", (e) => {
    const rect = cv.getBoundingClientRect();
    const px = (e.clientX - rect.left) * (cv.width / rect.width);
    const py = (e.clientY - rect.top) * (cv.height / rect.height);
    const ratio = RENDER.pixel_ratio;
    let hit = -1;
    RENDER.annotations.forEach((ann, idx) => {
      const s0 = ann.samples[0], sN = ann.samples[ann.samples.length - 1];
      if (!s0) return;
      const x0 = s0.pen_x_px - 4;
      const x1 = (sN.box[2] || sN.pen_x_px) + 4;
      const y0 = Math.min(...ann.samples.map(s => s.box[1] || s.baseline_y_px));
      const y1 = Math.max(...ann.samples.map(s => s.box[3] || s.baseline_y_px));
      if (px >= x0 && px <= x1 && py >= y0 && py <= y1) hit = idx;
    });
    if (hit < 0) return;
    drag = { idx: hit, startX: e.clientX, startY: e.clientY,
             ann: STATE.annotations[hit] };
  });

  window.addEventListener("mousemove", async (e) => {
    if (!drag) return;
    const rect = cv.getBoundingClientRect();
    const dxLu = ((e.clientX - drag.startX) * (cv.width / rect.width)) /
                 RENDER.pixel_ratio;
    const dyLu = ((e.clientY - drag.startY) * (cv.height / rect.height)) /
                 RENDER.pixel_ratio;
    await api("/api/position", {
      index: drag.idx,
      x_lu: drag.ann.x_lu + dxLu,
      y_lu: drag.ann.y_lu + dyLu,
    });
    await refresh();
    drag.ann = STATE.annotations[drag.idx];
  });

  window.addEventListener("mouseup", () => { drag = null; });
}

// ---- 控件绑定 -----------------------------------------------------------
function bindControls() {
  $("pixel-ratio").onchange = async (e) => {
    await api("/api/settings", { pixel_ratio: parseFloat(e.target.value) });
    await refresh();
  };
  $("hinting").onchange = async (e) => {
    await api("/api/settings", { hinting: e.target.checked });
    await refresh();
  };
  $("subpixel").onchange = async (e) => {
    await api("/api/settings", { subpixel_position: e.target.checked });
    await refresh();
  };
  $("show-samples").onchange = drawOverlay;
  $("re-render").onclick = () => refresh();

  $("ed-apply").onclick = async () => {
    const hex = $("ed-color").value;
    const color = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16));
    await api("/api/annotation", {
      index: STATE.selected,
      text: $("ed-text").value,
      size_lu: parseFloat($("ed-size").value),
      color,
    });
    await refresh();
  };
  $("ed-delete").onclick = async () => {
    await api("/api/delete", { index: STATE.selected });
    await refresh();
  };
  document.querySelectorAll("[data-move]").forEach(btn => {
    const moves = { "(-8,0)": [-8, 0], "(8,0)": [8, 0],
                    "(0,-8)": [0, -8], "(0,8)": [0, 8] };
    btn.onclick = async () => {
      const [dx, dy] = moves[btn.dataset.move];
      await api("/api/move", { index: STATE.selected, dx_lu: dx, dy_lu: dy });
      await refresh();
    };
  });

  $("gl-show").onclick = async () => {
    const ch = $("gl-char").value;
    if (!ch) return showError("请输入一个字符");
    try {
      const data = await api("/api/glyph", {
        codepoint: ch.codePointAt(0),
        size_lu: parseFloat($("gl-size").value),
      });
      $("gl-image").src = "data:image/png;base64," + data.image;
      const m = data.metrics;
      $("gl-metrics").innerHTML = Object.entries(m).map(
        ([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("");
    } catch (err) { /* 错误已由 api() 上屏 */ }
  };
}

bindControls();
attachDrag();
refresh();
