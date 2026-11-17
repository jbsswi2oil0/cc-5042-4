/* Engineering annotation editor — all mutations go to the kernel API;
   the page is rebuilt from /api/state, so the kernel drives the controls. */
"use strict";

const S = {
  state: null,
  tool: "select",
  selected: null,        // annotation id
  showSampling: true,
  resultFit: true,
  drag: null,            // {kind:'ann', id, dx, dy} | {kind:'entity', x0,y0,cur}
  img: null,             // decoded render.png
  glyphSel: null,        // {ann, seq}
};

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function api(path, body) {
  const opt = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  const r = await fetch(path, opt);
  if (r.status === 422) {           // kernel rejected: feedback arrives via state
    await refresh();
    return null;
  }
  return r;
}

async function refresh() {
  const r = await fetch("/api/state");
  S.state = await r.json();
  buildToolbar();
  drawVector();
  await loadResult();
  drawErrors();
  drawSamples();
  drawGlyphPanel();
}

function loadResult() {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => { S.img = img; drawResult(); resolve(); };
    img.onerror = () => resolve();
    img.src = "/api/render.png?t=" + Date.now();
  });
}

/* ------------------------------------------------------------ toolbar */
function buildToolbar() {
  const c = S.state.controls;
  const bar = $("toolbar");
  let h = "";
  for (const t of c.tools) {
    h += `<button data-tool="${t.id}" class="${S.tool === t.id ? "on" : ""}">${esc(t.label)}</button>`;
  }
  h += `<span class="sep"></span>`;
  h += `<label>像素比 <select id="dpr">` +
    c.dpr_options.map((d) =>
      `<option value="${d}" ${d === S.state.settings.dpr ? "selected" : ""}>${d}×</option>`
    ).join("") + `</select></label>`;
  h += `<label><input type="checkbox" id="hinting" ${S.state.settings.hinting ? "checked" : ""}> hinting</label>`;
  h += `<label><input type="checkbox" id="sampling" ${S.showSampling ? "checked" : ""}> 采样位置</label>`;
  h += `<label><input type="checkbox" id="fit" ${S.resultFit ? "checked" : ""}> 结果适配</label>`;
  h += `<span class="sep"></span>`;
  h += `<label>文本 <input id="f-text" size="18" value="${esc(currentText())}"></label>`;
  h += `<label>字号 <input id="f-size" size="4" value="${currentSize()}"></label>`;
  h += `<label>颜色 <input id="f-color" size="7" value="${esc(currentColor())}"></label>`;
  if (S.selected !== null) {
    h += `<button id="apply">应用修改</button><button id="del">删除注记</button>`;
  }
  bar.innerHTML = h;
  for (const t of c.tools) {
    bar.querySelector(`[data-tool="${t.id}"]`).onclick = () => {
      S.tool = t.id; buildToolbar();
    };
  }
  $("dpr").onchange = (e) =>
    api("/api/settings", { dpr: parseFloat(e.target.value) }).then(refresh);
  $("hinting").onchange = (e) =>
    api("/api/settings", { hinting: e.target.checked }).then(refresh);
  $("sampling").onchange = (e) => { S.showSampling = e.target.checked; drawResult(); };
  $("fit").onchange = (e) => { S.resultFit = e.target.checked; drawResult(); };
  if (S.selected !== null) {
    $("apply").onclick = applySelected;
    $("del").onclick = async () => {
      await api("/api/annotation/delete", { id: S.selected });
      S.selected = null; S.glyphSel = null; refresh();
    };
  }
}

function selectedAnn() {
  if (!S.state || S.selected === null) return null;
  return S.state.annotations.find((a) => a.id === S.selected) || null;
}
function currentText() { const a = selectedAnn(); return a ? a.text : "Ø12^{+0.2} note"; }
function currentSize() { const a = selectedAnn(); return a ? a.size_pt : 12; }
function currentColor() { const a = selectedAnn(); return a ? a.color : "#202020"; }

async function applySelected() {
  await api("/api/annotation/update", {
    id: S.selected,
    text: $("f-text").value,
    size: parseFloat($("f-size").value),
    color: $("f-color").value,
  });
  refresh();
}

/* --------------------------------------------------------- vector view */
function vecScale() {
  const c = $("vector");
  return c.width / S.state.document.width;   // backing px per logical px
}

function sizeVectorCanvas() {
  const d = S.state.document;
  const c = $("vector");
  const dprWin = window.devicePixelRatio || 1;
  c.style.width = d.width + "px";
  c.style.height = d.height + "px";
  c.width = Math.round(d.width * dprWin);
  c.height = Math.round(d.height * dprWin);
}

function drawVector() {
  const c = $("vector"), ctx = c.getContext("2d");
  sizeVectorCanvas();
  const k = vecScale();
  ctx.setTransform(k, 0, 0, k, 0, 0);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, S.state.document.width, S.state.document.height);
  ctx.strokeStyle = "#cccccc";
  ctx.strokeRect(0.5, 0.5, S.state.document.width - 1, S.state.document.height - 1);
  for (const e of S.state.entities) drawEntity(ctx, e);
  if (S.drag && S.drag.kind === "entity" && S.drag.cur) {
    drawEntity(ctx, { kind: S.tool, coords: [...S.drag.p0, ...S.drag.cur], color: "#3a7" });
  }
  for (const a of S.state.annotations) drawAnnLabel(ctx, a);
}

function drawEntity(ctx, e) {
  ctx.strokeStyle = e.color;
  ctx.lineWidth = 1;
  ctx.beginPath();
  if (e.kind === "line") {
    ctx.moveTo(e.coords[0], e.coords[1]);
    ctx.lineTo(e.coords[2], e.coords[3]);
  } else {
    ctx.rect(e.coords[0], e.coords[1],
             e.coords[2] - e.coords[0], e.coords[3] - e.coords[1]);
  }
  ctx.stroke();
}

function annLogicalSize(a) {
  const diag = (S.state.diagnostics || []).find((d) => d.id === a.id);
  const dpr = S.state.settings.dpr;
  const w = diag && diag.pen_final !== undefined ? diag.pen_final / dpr
                                                 : a.text.length * a.size_pt;
  return [Math.max(w, 8), a.size_pt * 96 / 72 * 1.4];
}

function drawAnnLabel(ctx, a) {
  const [w, h] = annLogicalSize(a);
  const bad = a.status === "error";
  const sel = a.id === S.selected;
  ctx.strokeStyle = bad ? "#d33" : sel ? "#1f6feb" : "#bbbbbb";
  ctx.setLineDash(bad ? [4, 3] : []);
  ctx.strokeRect(a.x, a.y - h * 0.8, w, h);
  ctx.setLineDash([]);
  ctx.fillStyle = bad ? "#d33" : "#1f6feb";
  ctx.beginPath();
  ctx.arc(a.x, a.y, 2.5, 0, 7);
  ctx.fill();
  ctx.fillStyle = bad ? "#d33" : "#333";
  ctx.font = "11px sans-serif";
  ctx.fillText((bad ? "✗ " : "") + a.text, a.x + 4, a.y - h * 0.8 - 3);
}

function vecPos(ev) {
  const k = vecScale();
  return [ev.offsetX / k, ev.offsetY / k];
}

function hitAnn(x, y) {
  for (let i = S.state.annotations.length - 1; i >= 0; i--) {
    const a = S.state.annotations[i];
    const [w, h] = annLogicalSize(a);
    if (x >= a.x - 4 && x <= a.x + w + 4 && y >= a.y - h * 0.8 - 4 && y <= a.y + h * 0.2 + 4)
      return a;
  }
  return null;
}

function bindVector() {
  const c = $("vector");
  c.addEventListener("mousedown", (ev) => {
    const [x, y] = vecPos(ev);
    if (S.tool === "select") {
      const a = hitAnn(x, y);
      S.selected = a ? a.id : null;
      S.glyphSel = null;
      if (a) S.drag = { kind: "ann", id: a.id, dx: a.x - x, dy: a.y - y, moved: false };
      buildToolbar(); drawVector(); drawSamples();
    } else if (S.tool === "add") {
      api("/api/annotation", {
        x, y, size: parseFloat($("f-size").value) || 12,
        text: $("f-text").value, color: $("f-color").value,
      }).then(refresh);
    } else if (S.tool === "line" || S.tool === "rect") {
      S.drag = { kind: "entity", p0: [x, y], cur: [x, y] };
    }
  });
  c.addEventListener("mousemove", (ev) => {
    if (!S.drag) return;
    const [x, y] = vecPos(ev);
    if (S.drag.kind === "ann") {
      const a = S.state.annotations.find((t) => t.id === S.drag.id);
      if (a) { a.x = x + S.drag.dx; a.y = y + S.drag.dy; S.drag.moved = true; drawVector(); }
    } else {
      S.drag.cur = [x, y]; drawVector();
    }
  });
  c.addEventListener("mouseup", (ev) => {
    if (!S.drag) return;
    const [x, y] = vecPos(ev);
    if (S.drag.kind === "ann" && S.drag.moved) {
      const a = S.state.annotations.find((t) => t.id === S.drag.id);
      api("/api/annotation/move", { id: S.drag.id, x: a.x, y: a.y }).then(refresh);
    } else if (S.drag.kind === "entity") {
      const [x0, y0] = S.drag.p0;
      if (Math.hypot(x - x0, y - y0) > 3) {
        api("/api/entity", { kind: S.tool, coords: [x0, y0, x, y] }).then(refresh);
      }
    }
    S.drag = null;
  });
}

/* --------------------------------------------------------- result view */
function drawResult() {
  if (!S.img) return;
  const c = $("result"), ctx = c.getContext("2d");
  const devW = S.state.document.device_width, devH = S.state.document.device_height;
  c.width = devW; c.height = devH;
  const wrap = $("result-wrap");
  if (S.resultFit) {
    const s = Math.min(1, (wrap.clientWidth - 4) / devW);
    c.style.width = Math.round(devW * s) + "px";
    $("result-scale").textContent =
      ` ${devW}×${devH} 设备px @ ${S.state.settings.dpr}× (显示 ${Math.round(s * 100)}%)`;
  } else {
    c.style.width = devW + "px";
    $("result-scale").textContent = ` ${devW}×${devH} 设备px @ ${S.state.settings.dpr}× (1:1)`;
  }
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(S.img, 0, 0);
  if (S.showSampling) drawSamplingOverlay(ctx);
  bindResultClick(c);
}

function drawSamplingOverlay(ctx) {
  for (const d of S.state.diagnostics) {
    if (!d.glyphs || !d.glyphs.length) continue;
    const [bx, by] = d.baseline;
    ctx.strokeStyle = "rgba(0,160,60,0.8)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(bx, by + 0.5);
    ctx.lineTo(bx + d.pen_final, by + 0.5);
    ctx.stroke();
    d.glyphs.forEach((g, i) => {
      const sel = S.glyphSel && S.glyphSel.ann === d.id && S.glyphSel.seq === i;
      ctx.strokeStyle = sel ? "#ff00ff" : "rgba(220,40,40,0.75)";
      ctx.strokeRect(g.origin[0] + 0.5, g.origin[1] + 0.5, g.width - 1, g.rows - 1);
      ctx.fillStyle = sel ? "#ff00ff" : "rgba(30,80,220,0.9)";
      ctx.beginPath();
      ctx.arc(g.pen[0], g.pen[1], sel ? 3 : 2, 0, 7);
      ctx.fill();
    });
  }
}

function bindResultClick(c) {
  c.onmousedown = (ev) => {
    const k = c.width / c.getBoundingClientRect().width;
    const x = ev.offsetX * k, y = ev.offsetY * k;
    let best = null, bestD = 1e9;
    for (const d of S.state.diagnostics) {
      (d.glyphs || []).forEach((g, i) => {
        const dd = Math.hypot(g.pen[0] - x, g.pen[1] - y);
        if (dd < bestD) { bestD = dd; best = { ann: d.id, seq: i }; }
      });
    }
    if (best && bestD < 30 * S.state.settings.dpr) {
      S.glyphSel = best; S.selected = best.ann;
      buildToolbar(); drawResult(); drawSamples(); drawGlyphPanel();
    }
  };
}

/* --------------------------------------------------------- glyph panel */
async function drawGlyphPanel() {
  const info = $("glyph-info");
  const c = $("glyph"), ctx = c.getContext("2d");
  ctx.clearRect(0, 0, c.width, c.height);
  if (!S.glyphSel) { info.textContent = "在结果区点击一个字形的采样点查看覆盖"; return; }
  const { ann, seq } = S.glyphSel;
  const r = await fetch(`/api/glyph.png?ann=${ann}&seq=${seq}`);
  if (!r.ok) { info.textContent = "内核未返回该字形"; return; }
  const d = (S.state.diagnostics.find((t) => t.id === ann) || {}).glyphs[seq];
  const img = new Image();
  img.onload = () => {
    const z = Math.max(4, Math.floor(200 / Math.max(img.width, img.height)));
    const ox = 20, oy = 20;
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(img, ox, oy, img.width * z, img.height * z);
    ctx.strokeStyle = "rgba(0,0,0,0.15)";
    for (let i = 0; i <= img.width; i++) {
      ctx.beginPath(); ctx.moveTo(ox + i * z + 0.5, oy); ctx.lineTo(ox + i * z + 0.5, oy + img.height * z); ctx.stroke();
    }
    for (let i = 0; i <= img.height; i++) {
      ctx.beginPath(); ctx.moveTo(ox, oy + i * z + 0.5); ctx.lineTo(ox + img.width * z, oy + i * z + 0.5); ctx.stroke();
    }
    // baseline (red) and pen-origin vertical (blue) through the coverage box
    ctx.strokeStyle = "#d33";
    ctx.beginPath(); ctx.moveTo(0, oy + d.top * z + 0.5); ctx.lineTo(c.width, oy + d.top * z + 0.5); ctx.stroke();
    ctx.strokeStyle = "#1f6feb";
    ctx.beginPath(); ctx.moveTo(ox - d.left * z + 0.5, 0); ctx.lineTo(ox - d.left * z + 0.5, c.height); ctx.stroke();
    info.innerHTML =
      `<b>'${esc(d.char)}'</b> #${d.glyph_index} ${d.width}×${d.rows}<br>` +
      `advance ${d.advance}（线性 ${d.linear_advance}）<br>` +
      `bearing left ${d.left} / top ${d.top}<br>` +
      `pen (${d.pen[0]}, ${d.pen[1]}) → origin (${d.origin[0]}, ${d.origin[1]})<br>` +
      `cache ${d.cache_hit ? "hit" : "miss"}`;
  };
  img.src = URL.createObjectURL(await r.blob());
}

/* --------------------------------------------------------- samples table */
function drawSamples() {
  const el = $("samples");
  const id = S.selected !== null ? S.selected
    : (S.state.diagnostics[0] && S.state.diagnostics[0].id);
  const d = (S.state.diagnostics || []).find((t) => t.id === id);
  if (!d || !d.glyphs) { el.innerHTML = ""; return; }
  let h = `<div class="panel-sub">注记 #${d.id} “${esc(d.text)}” — 行长 ` +
    `<b>${d.pen_final}</b>px；逐字取整将得 ${d.naive_final}px，` +
    `累计漂移 <b>${d.rounding_drift}</b>px（内核用分数笔位，未取整）</div>`;
  h += `<table><tr><th>字</th><th>pen x</th><th>pen y</th><th>origin</th>` +
    `<th>adv</th><th>left/top</th><th>w×h</th><th>cache</th></tr>`;
  d.glyphs.forEach((g, i) => {
    const sel = S.glyphSel && S.glyphSel.ann === d.id && S.glyphSel.seq === i;
    h += `<tr data-seq="${i}" class="${sel ? "on" : ""}"><td>${esc(g.char)}</td>` +
      `<td>${g.pen[0]}</td><td>${g.pen[1]}</td>` +
      `<td>${g.origin[0]},${g.origin[1]}</td><td>${g.advance}</td>` +
      `<td>${g.left}/${g.top}</td><td>${g.width}×${g.rows}</td>` +
      `<td>${g.cache_hit ? "hit" : "miss"}</td></tr>`;
  });
  el.innerHTML = h + "</table>";
  el.querySelectorAll("tr[data-seq]").forEach((tr) => {
    tr.onclick = () => {
      S.glyphSel = { ann: d.id, seq: parseInt(tr.dataset.seq, 10) };
      drawResult(); drawSamples(); drawGlyphPanel();
    };
  });
}

/* --------------------------------------------------------- errors */
function drawErrors() {
  const el = $("errors");
  const errs = S.state.errors.slice(-8).reverse();
  el.innerHTML = errs.length
    ? errs.map((e) => `<div class="err">[${e.t}] ${esc(e.where)}: ${esc(e.message)}</div>`).join("")
    : `<div class="ok">无错误 — 内核缓存 ${S.state.cache.entries} 项（hit ${S.state.cache.hits} / miss ${S.state.cache.misses}）</div>`;
  const f = S.state.font;
  $("meta").textContent =
    `${f.family} · FreeType ${f.freetype.join(".")} · dpr ${S.state.settings.dpr} · ` +
    `hinting ${S.state.settings.hinting ? "on" : "off"}`;
}

bindVector();
refresh();
window.addEventListener("resize", () => { if (S.state) { drawVector(); drawResult(); } });
