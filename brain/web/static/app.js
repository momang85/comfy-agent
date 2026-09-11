/* ComfyUI 大脑 Web UI 前端逻辑（原生 JS，无依赖） */
"use strict";

const $ = (sel) => document.querySelector(sel);
const chatEl = $("#chat"), graphEl = $("#graph"),
      emptyEl = $("#graphempty"), metaEl = $("#graphmeta"),
      detailEl = $("#nodedetail"), evalsEl = $("#evals"),
      memoryEl = $("#memory"), galleryEl = $("#gallery"),
      statusEl = $("#statusline"), vramEl = $("#vram"),
      queueEl = $("#queue"), inputEl = $("#input"),
      formEl = $("#inputform"), lightbox = $("#lightbox"),
      projectSel = $("#projectsel"), stopBtn = $("#stopbtn"),
      chatPane = $("#chatpane"), attachBtn = $("#attachbtn"),
      fileInput = $("#fileinput"), attachBar = $("#attachbar");

const SVGNS = "http://www.w3.org/2000/svg";
const MAX_ATTACH_BYTES = 25 * 1024 * 1024;
let draft = null;
let nodeStates = {};
let currentMsg = null;
let activeProject = null;        // {id, name}
let pendingImage = null;         // 待发送附件 {file, previewUrl, name}

// ---------- 项目隔离 ----------
async function loadProjects() {
  const r = await fetch("/api/projects");
  const d = await r.json();
  let list = d.projects || [];
  // 「默认项目」（历史资产所在地）置顶；其余按名称排
  list.sort((a, b) => {
    if (a.id === "project") return -1;
    if (b.id === "project") return 1;
    return (a.name || "").localeCompare(b.name || "", "zh");
  });
  projectSel.innerHTML = "";
  list.forEach((p) => {
    const o = document.createElement("option");
    o.value = p.id;
    o.textContent = p.name;
    projectSel.appendChild(o);
  });
  if (!activeProject && list.length) {
    // 记住上次使用的项目；首次默认「默认项目」（历史资产所在地）
    const last = localStorage.getItem("comfy_agent_project");
    const target = list.find((p) => p.id === last) || list[0];
    switchProject(target.id);
  }
}

async function switchProject(pid) {
  activeProject = { id: pid };
  localStorage.setItem("comfy_agent_project", pid);
  projectSel.value = pid;
  // 清空本项目视角的所有面板
  chatEl.innerHTML = "";
  currentMsg = null;
  graphEl.innerHTML = "";
  emptyEl.classList.remove("hidden");
  metaEl.textContent = "";
  detailEl.classList.add("hidden");
  detailEl.innerHTML = "";
  evalsEl.innerHTML = "";
  memoryEl.innerHTML = "";
  refreshGallery();
  const r = await fetch("/api/status?project=" + encodeURIComponent(pid));
  const st = await r.json();
  if (st.project) activeProject = st.project;
  if (st.draft) renderGraph(st.draft);
  if (st.stage) setStage(st.stage);
}

$("#newproject").addEventListener("click", async () => {
  const name = prompt("新项目名称：");
  if (!name || !name.trim()) return;
  const r = await fetch("/api/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: name.trim() }),
  });
  const d = await r.json();
  if (d.ok) {
    await loadProjects();
    switchProject(d.project.id);
  }
});

projectSel.addEventListener("change", () => switchProject(projectSel.value));

// ---------- 图片附件（点击/拖拽/粘贴，单张） ----------
function setPendingImage(file) {
  if (!file) return;
  if (!file.type.startsWith("image/")) {
    alert("仅支持图片文件（PNG/JPEG/WebP）");
    return;
  }
  if (file.size > MAX_ATTACH_BYTES) {
    alert("图片不能超过 25MB");
    return;
  }
  if (pendingImage) URL.revokeObjectURL(pendingImage.previewUrl);
  pendingImage = { file, previewUrl: URL.createObjectURL(file), name: file.name };
  renderAttachBar();
}

function renderAttachBar() {
  attachBar.innerHTML = "";
  attachBar.classList.toggle("hidden", !pendingImage);
  if (!pendingImage) return;
  const chip = document.createElement("div");
  chip.className = "attachchip";
  chip.innerHTML = `<img src="${pendingImage.previewUrl}" alt="">
    <span class="fname">${escapeHtml(pendingImage.name)}</span>
    <button type="button" class="x" title="移除">✕</button>`;
  chip.querySelector(".x").addEventListener("click", () => {
    URL.revokeObjectURL(pendingImage.previewUrl);
    pendingImage = null;
    renderAttachBar();
  });
  attachBar.appendChild(chip);
}

attachBtn.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => {
  setPendingImage(fileInput.files[0] || null);
  fileInput.value = "";
});
chatPane.addEventListener("dragover", (e) => {
  e.preventDefault();
  chatPane.classList.add("dragover");
});
chatPane.addEventListener("drop", (e) => {
  e.preventDefault();
  chatPane.classList.remove("dragover");
  const f = [...(e.dataTransfer?.files || [])].find((x) => x.type.startsWith("image/"));
  if (f) setPendingImage(f);
});
document.addEventListener("paste", (e) => {
  const f = [...(e.clipboardData?.files || [])].find((x) => x.type.startsWith("image/"));
  if (f) setPendingImage(f);
});

// ---------- 画廊 ----------
async function refreshGallery() {
  if (!activeProject) return;
  try {
    const r = await fetch("/api/status?project=" +
                          encodeURIComponent(activeProject.id));
    const st = await r.json();
    renderGallery(st.outputs || []);
  } catch (e) { /* 后端未就绪 */ }
}

function renderGallery(outputs) {
  galleryEl.innerHTML = "";
  if (!outputs || !outputs.length) {
    const tip = document.createElement("div");
    tip.className = "gallery-tip";
    tip.textContent = "该项目暂无产物。历史资产在「默认项目」，可从顶部下拉切换。";
    galleryEl.appendChild(tip);
    return;
  }
  (outputs || []).forEach((o) => {
    const d = document.createElement("div");
    d.className = "item";
    d.innerHTML = o.kind === "video"
      ? `<video src="${o.path}" muted preload="metadata"></video>`
      : `<img src="${o.path}" loading="lazy">`;
    d.innerHTML += `<div class="tag">${o.new ? "🆕 " : ""}${escapeHtml(o.name)}</div>`;
    d.addEventListener("click", () => {
      if (o.kind === "image") {
        lightbox.querySelector("img").src = o.path;
        lightbox.classList.remove("hidden");
      } else {
        const v = d.querySelector("video");
        v.controls = true;
        v.play().catch(() => {});
      }
    });
    galleryEl.appendChild(d);
  });
}

lightbox.addEventListener("click", () => lightbox.classList.add("hidden"));

// ---------- 工作流图（分层布局 + 缩放拖拽） ----------
let view = { x: 0, y: 0, scale: 1 };
let fitPending = true;

function familyColor(cls) {
  const c = cls.toLowerCase();
  if (/loader|checkpoint|unet/.test(c)) return "#3f7fd9";
  if (/clip|encode|condition|guider/.test(c)) return "#9b6bd9";
  if (/sampler|scheduler|sigmas|noise/.test(c)) return "#3fb96a";
  if (/vae|latent/.test(c)) return "#3fb9b6";
  if (/save|preview|createvideo/.test(c)) return "#e8933a";
  if (/loadimage|load_image|loadvideo/.test(c)) return "#e8933a";
  if (/control|canny|pose|depth/.test(c)) return "#e86a9a";
  return "#5a6880";
}

function graphEdges(wf) {
  const edges = [];
  for (const [nid, node] of Object.entries(wf)) {
    for (const [inp, val] of Object.entries(node.inputs || {})) {
      if (Array.isArray(val) && typeof val[0] === "string" && val[0] in wf) {
        edges.push({ src: val[0], dst: nid, label: inp });
      }
    }
  }
  return edges;
}

function layout(wf) {
  const nodes = Object.keys(wf);
  const edges = graphEdges(wf);
  const indeg = {}, adj = {};
  nodes.forEach((n) => { indeg[n] = 0; adj[n] = []; });
  edges.forEach((e) => { indeg[e.dst]++; adj[e.src].push(e.dst); });
  const layer = {}, q = [];
  nodes.forEach((n) => { if (!indeg[n]) q.push(n); });
  let li = 0;
  while (q.length) {
    const batch = q.splice(0, q.length);
    batch.forEach((n) => { layer[n] = li; });
    batch.forEach((n) => adj[n].forEach((m) => {
      if (--indeg[m] === 0) q.push(m);
    }));
    li++;
  }
  const byLayer = {};
  Object.entries(layer).forEach(([n, l]) => {
    (byLayer[l] = byLayer[l] || []).push(n);
  });
  const W = 200, H = 64, GX = 40, GY = 60;
  const pos = {};
  const maxCol = Math.max(...Object.values(byLayer).map((a) => a.length), 1);
  Object.entries(byLayer).forEach(([l, arr]) => {
    arr.forEach((n, i) => {
      pos[n] = { x: GX + l * (W + 24),
                 y: GY + (i - (arr.length - 1) / 2) * (H + 36) };
    });
  });
  return { pos, edges, maxLayer: li, maxCol };
}

function renderGraph(wf) {
  draft = wf;
  if (!wf || !Object.keys(wf).length) {
    graphEl.innerHTML = "";
    emptyEl.classList.remove("hidden");
    metaEl.textContent = "";
    return;
  }
  emptyEl.classList.add("hidden");
  const { pos, edges, maxLayer, maxCol } = layout(wf);
  const W = 200, H = 64;
  const width = 80 + maxLayer * 224, height = Math.max(300, maxCol * 110 + 120);
  graphEl.setAttribute("viewBox", `0 0 ${width} ${height}`);
  graphEl.innerHTML = "";
  if (fitPending) { fitPending = false; fitView(); }

  const vp = document.createElementNS(SVGNS, "g");
  vp.setAttribute("id", "viewport");
  applyView(vp);
  edges.forEach((e) => {
    const a = pos[e.src], b = pos[e.dst];
    const mx = (a.x + W + b.x) / 2;
    const path = document.createElementNS(SVGNS, "path");
    path.setAttribute("class", "gedge");
    path.setAttribute("d",
      `M ${a.x + W} ${a.y + H / 2} C ${mx} ${a.y + H / 2}, ${mx} ${b.y + H / 2}, ${b.x} ${b.y + H / 2}`);
    vp.appendChild(path);
    const t = document.createElementNS(SVGNS, "text");
    t.setAttribute("class", "gedge-label");
    t.setAttribute("x", mx); t.setAttribute("y", (a.y + b.y) / 2 + H / 4 - 6);
    t.setAttribute("text-anchor", "middle");
    t.textContent = e.label;
    vp.appendChild(t);
  });
  Object.entries(wf).forEach(([nid, node]) => {
    const p = pos[nid], cls = node.class_type || "?";
    const gn = document.createElementNS(SVGNS, "g");
    gn.setAttribute("class", "gnode");
    gn.dataset.nid = nid;
    gn.setAttribute("transform", `translate(${p.x},${p.y})`);
    const rect = document.createElementNS(SVGNS, "rect");
    rect.setAttribute("width", W); rect.setAttribute("height", H);
    rect.setAttribute("fill", familyColor(cls) + "33");
    gn.appendChild(rect);
    const t1 = document.createElementNS(SVGNS, "text");
    t1.setAttribute("class", "t1"); t1.setAttribute("x", 10); t1.setAttribute("y", 28);
    t1.textContent = cls.length > 24 ? cls.slice(0, 23) + "…" : cls;
    gn.appendChild(t1);
    const t2 = document.createElementNS(SVGNS, "text");
    t2.setAttribute("class", "t2"); t2.setAttribute("x", 10); t2.setAttribute("y", 48);
    t2.textContent = `#${nid} · ${Object.keys(node.inputs || {}).length} 输入`;
    gn.appendChild(t2);
    if (nodeStates[nid]) {
      if (nodeStates[nid].fail) gn.classList.add("fail");
      if (nodeStates[nid].exec) gn.classList.add("exec");
      if (nodeStates[nid].flash) gn.classList.add("flash");
    }
    gn.addEventListener("click", (e) => {
      if (e._dragged) return;
      showNodeDetail(nid, node);
    });
    vp.appendChild(gn);
  });
  graphEl.appendChild(vp);
  metaEl.textContent = `${Object.keys(wf).length} 节点 · ${edges.length} 连线`;
}

function showNodeDetail(nid, node) {
  const rows = Object.entries(node.inputs || {}).map(([k, v]) =>
    `<div class="kv">${k}: ${Array.isArray(v) ? "←#" + v[0] : JSON.stringify(v)}</div>`).join("");
  detailEl.classList.remove("hidden");
  detailEl.innerHTML =
    `<h4>#${nid} ${node.class_type}</h4>${rows || "<div class='kv'>无输入</div>"}`;
}

// ---- 缩放/平移 ----
function applyView(vp) {
  vp.setAttribute("transform",
    `translate(${view.x},${view.y}) scale(${view.scale})`);
}

function setView(nx, ny, ns) {
  view.x = nx; view.y = ny; view.scale = Math.min(4, Math.max(0.3, ns));
  const vp = graphEl.querySelector("#viewport");
  if (vp) applyView(vp);
}

function fitView() {
  const bb = graphEl.viewBox.baseVal;
  const w = graphEl.clientWidth, h = graphEl.clientHeight;
  const s = Math.min(w / bb.width, h / bb.height, 1.2);
  setView((w - bb.width * s) / 2, (h - bb.height * s) / 2, s);
}

graphEl.addEventListener("wheel", (e) => {
  e.preventDefault();
  const rect = graphEl.getBoundingClientRect();
  const mx = e.clientX - rect.left, my = e.clientY - rect.top;
  const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
  const ns = Math.min(4, Math.max(0.3, view.scale * factor));
  // 以鼠标为中心缩放
  view.x = mx - (mx - view.x) * (ns / view.scale);
  view.y = my - (my - view.y) * (ns / view.scale);
  view.scale = ns;
  const vp = graphEl.querySelector("#viewport");
  if (vp) applyView(vp);
}, { passive: false });

let dragging = null;   // {sx, sy, vx, vy, moved}
graphEl.addEventListener("pointerdown", (e) => {
  if (e.target.closest(".gnode")) return;   // 节点点击/拖拽不触发画布平移
  dragging = { sx: e.clientX, sy: e.clientY, vx: view.x, vy: view.y,
               moved: false };
  graphEl.setPointerCapture(e.pointerId);
});
graphEl.addEventListener("pointermove", (e) => {
  if (!dragging) return;
  const dx = e.clientX - dragging.sx, dy = e.clientY - dragging.sy;
  if (Math.abs(dx) + Math.abs(dy) > 4) dragging.moved = true;
  if (dragging.moved) setView(dragging.vx + dx, dragging.vy + dy, view.scale);
});
graphEl.addEventListener("pointerup", () => { dragging = null; });
graphEl.addEventListener("dblclick", () => fitView());

$("#gz-in").addEventListener("click", () => setView(view.x, view.y, view.scale * 1.25));
$("#gz-out").addEventListener("click", () => setView(view.x, view.y, view.scale / 1.25));
$("#gz-fit").addEventListener("click", () => fitView());
$("#gz-reset").addEventListener("click", () => setView(0, 0, 1));

// ---------- 对话流 ----------
function addMsg(role, text, imageUrl) {
  const d = document.createElement("div");
  d.className = "msg " + role;
  d.innerHTML = `<div class="bubble"></div>`;
  const bubble = d.querySelector(".bubble");
  if (text) bubble.textContent = text;
  if (imageUrl) {
    const img = document.createElement("img");
    img.className = "bubble-img";
    img.src = imageUrl;
    img.alt = "附件图片";
    img.addEventListener("click", () => {
      lightbox.querySelector("img").src = imageUrl;
      lightbox.classList.remove("hidden");
    });
    bubble.appendChild(img);
  }
  chatEl.appendChild(d);
  chatEl.scrollTop = chatEl.scrollHeight;
  return bubble;
}

function ensureAiMsg() {
  if (!currentMsg) {
    currentMsg = { root: document.createElement("div"), content: "" };
    currentMsg.root.className = "msg ai";
    currentMsg.root.innerHTML =
      `<details class="reasoning"><summary>思考过程</summary><div class="rbody"></div></details>
       <div class="bubble"></div>`;
    chatEl.appendChild(currentMsg.root);
  }
  return currentMsg;
}

function appendDelta(channel, delta) {
  const m = ensureAiMsg();
  if (channel === "reasoning") {
    m.root.querySelector(".rbody").textContent += delta;
  } else {
    // 原始流保留在 m.raw；显示层隐藏工具调用代码块（调用本身由工具卡呈现）
    m.raw = (m.raw || "") + delta;
    m.content = stripToolFences(m.raw);
    m.root.querySelector(".bubble").textContent = m.content;
  }
  chatEl.scrollTop = chatEl.scrollHeight;
}

// 三反引号围栏标记（用字符码构造，避免源码出现反引号字面量）
const FENCE = String.fromCharCode(96).repeat(3);
const TOOL_KEY_RE = /"(tool|task|action)"\s*:/;

// 隐藏流式正文里的工具调用代码块；未闭合的围栏在流式期间同样隐藏
function stripToolFences(raw) {
  const s = String(raw || "");
  const out = [];
  let last = 0;
  for (;;) {
    const i = s.indexOf(FENCE, last);
    if (i < 0) break;
    const j = s.indexOf(FENCE, i + FENCE.length);
    out.push(s.slice(last, i));
    if (j < 0) {                       // 未闭合：正在流式传输的工具块
      const body = s.slice(i + FENCE.length);
      if (TOOL_KEY_RE.test(body)) {
        out.push("\n[已提交工具调用]");
        return out.join("");
      }
      out.push(body);
      return out.join("");
    }
    const body = s.slice(i + FENCE.length, j);
    out.push(TOOL_KEY_RE.test(body) ? "\n[已提交工具调用]\n"
                                    : s.slice(i, j + FENCE.length));
    last = j + FENCE.length;
  }
  out.push(s.slice(last));
  return out.join("");
}

function addToolCard(tool, args, ok, summary, done = true) {
  // tool_end（done=true）时更新最近同名未完成卡片，不新增双卡片
  if (done) {
    const cards = chatEl.querySelectorAll(".toolcard");
    for (let i = cards.length - 1; i >= 0; i--) {
      if (cards[i].dataset.tool === tool && !cards[i].dataset.done) {
        if (summary) {
          // 文本节点赋值不做 HTML 转义（转义只用于 innerHTML 路径，
          // 否则卡片正文会显示 &quot; 这类实体）
          const body = summary || String(JSON.stringify(args || {})).slice(0, 140);
          cards[i].querySelector(".sum").textContent = body;
        }
        if (!ok) cards[i].classList.add("fail");
        cards[i].dataset.done = "1";
        currentMsg = null;
        return;
      }
    }
  }
  const d = document.createElement("div");
  d.className = "toolcard" + (ok ? "" : " fail");
  d.dataset.tool = tool;
  if (done) d.dataset.done = "1";
  d.innerHTML = `<div class="t">🔧 ${tool}</div>
    <div class="sum">${escapeHtml(summary || JSON.stringify(args || {}).slice(0, 140))}</div>`;
  chatEl.appendChild(d);
  chatEl.scrollTop = chatEl.scrollHeight;
  currentMsg = null;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// 大脑出错卡片（线程兜底事件）：会话仍可用，提示用户可继续发消息
function addErrorCard(message, traceback) {
  const d = document.createElement("div");
  d.className = "toolcard fail";
  d.dataset.tool = "__error__";
  d.dataset.done = "1";
  const t = document.createElement("div");
  t.className = "t";
  t.textContent = "⚠ 大脑出错（会话已恢复，可继续发消息）";
  const s = document.createElement("div");
  s.className = "sum";
  s.style.whiteSpace = "pre-wrap";
  s.textContent = String(message || "未知错误");
  d.appendChild(t);
  d.appendChild(s);
  if (traceback) {
    const det = document.createElement("details");
    const sm = document.createElement("summary");
    sm.textContent = "技术细节";
    const pre = document.createElement("div");
    pre.className = "sum";
    pre.style.whiteSpace = "pre-wrap";
    pre.textContent = String(traceback).slice(-800);
    det.appendChild(sm);
    det.appendChild(pre);
    d.appendChild(det);
  }
  chatEl.appendChild(d);
  chatEl.scrollTop = chatEl.scrollHeight;
  currentMsg = null;
}

// ---------- 顶栏 ----------
const STAGE_FLOW = ["validate", "repair", "submit", "run", "download", "evaluate"];
function setStage(stage, failed) {
  document.querySelectorAll(".stage").forEach((el) => {
    const s = el.dataset.s;
    const i = STAGE_FLOW.indexOf(s), cur = STAGE_FLOW.indexOf(stage);
    el.classList.toggle("active", i === cur && !failed);
    el.classList.toggle("fail", i === cur && failed);
    el.classList.toggle("done", i < cur);
  });
  statusEl.textContent = stage === "idle" ? "空闲" :
    stage === "completed" ? "完成" :
    stage === "thinking" ? "构建中…" :
    stage === "error" ? "出错" : `阶段: ${stage}`;
  if (stage === "completed") {
    document.querySelectorAll(".stage").forEach((el) => el.classList.add("done"));
  }
}

// 警告提示条：独立区域 + 12 秒自动消失（此前会顶掉状态行的阶段显示）
let warnTimer = null;
function showWarning(text) {
  const el = document.getElementById("warnline");
  if (!el) return;
  el.textContent = String(text || "").slice(0, 180);
  el.style.cssText = "color:#e0a800;font-size:12px;max-width:420px;" +
    "overflow:hidden;text-overflow:ellipsis;white-space:nowrap";
  el.classList.remove("hidden");
  if (warnTimer) clearTimeout(warnTimer);
  warnTimer = setTimeout(() => el.classList.add("hidden"), 12000);
}

function addEvalCard(ev) {
  const html = renderEvalCard(ev);
  // 同一 prompt_id 只保留一张评估卡（引擎强制评估 + 大脑 view_* 会重复上报）
  if (ev.prompt_id) {
    const old = evalsEl.querySelector(`[data-pid="${ev.prompt_id}"]`);
    if (old) { old.innerHTML = html; return; }
  }
  const d = document.createElement("div");
  d.className = "card";
  if (ev.prompt_id) d.dataset.pid = ev.prompt_id;
  d.innerHTML = html;
  evalsEl.prepend(d);
}

function renderEvalCard(ev) {
  const score = ev.score != null ? `（${ev.score}/10）` : "";
  const cls = ev.verdict === true ? "eval-pass" : ev.verdict === false ? "eval-fail" : "";
  return `<h4>${ev.kind === "video" || ev.kind === "video_forced" ? "🎬 视频评估" : "🖼 图像评估"}
    <span class="${cls}">${ev.verdict === true ? "通过" : ev.verdict === false ? "不达标" : "未知"}${score}</span></h4>` +
    (ev.issues || []).map((i) =>
      `<div class="issue"><span class="loc">${escapeHtml(i.location || "")}</span> ${escapeHtml(i.description || "")}</div>`).join("");
}

function addMemoryBadge(text) {
  const b = document.createElement("span");
  b.className = "mem-badge";
  b.textContent = "🧠 技能召回";
  b.title = text;
  memoryEl.appendChild(b);
}

// ---------- 停止 ----------
stopBtn.addEventListener("click", () => {
  stopBtn.disabled = true;
  stopBtn.textContent = "■ 停止中…";
  fetch("/api/interrupt", { method: "POST" })
    .catch(() => {});
  setTimeout(() => { stopBtn.disabled = false; stopBtn.textContent = "■ 停止"; }, 3000);
});

// ---------- SSE 事件分发（按项目过滤） ----------
function handleEvent(msg) {
  const { event, data, project } = msg;
  // 项目隔离：只显示当前项目事件；project=null 为系统事件（显存/队列）全局显示
  if (project && activeProject && project !== activeProject.id) return;
  switch (event) {
    case "user_message":
      // 防重复：本地提交已渲染过相同文本+附件时跳过 SSE 回显
      {
        const last = chatEl.querySelector(".msg:last-child .bubble");
        const lastMsg = last && last.closest(".msg");
        const dup = lastMsg && lastMsg.classList.contains("user") &&
                    last.textContent === data.text &&
                    Boolean(lastMsg.querySelector(".bubble-img")) === Boolean(data.image);
        if (!dup) addMsg("user", data.text, data.image && data.image.url);
      }
      break;
    case "think_delta":
      appendDelta(data.channel, data.delta);
      break;
    case "tool_start":
      addToolCard(data.tool, data.args, true, "调用中…", false);
      break;
    case "tool_end":
      addToolCard(data.tool, null, data.ok, data.summary, true);
      break;
    case "error":
      // 会话线程兜底事件：显示错误并复位阶段（不再永久停在"构建中"）
      addErrorCard(data.message, data.traceback);
      setStage("error", true);
      break;
    case "workflow_update":
      renderGraph(data.graph);
      break;
    case "stage":
      if (data.stage === "warning") {
        // 警告走独立区域（#warnline），不再覆盖阶段显示
        showWarning(String(data.detail?.warning || "警告"));
        break;
      }
      setStage(data.stage);
      if (data.stage === "validation_failed" && data.detail?.nodes) {
        data.detail.nodes.forEach((nid) => {
          nodeStates[nid] = { ...(nodeStates[nid] || {}), fail: true };
        });
        if (draft) renderGraph(draft);
      }
      break;
    case "evaluation":
      addEvalCard(data);
      setStage("evaluate", data.verdict === false);
      break;
    case "delivery": {
      // 最终回复已随流式内容显示时不再重复气泡（否则同一段话出现两次）
      const ais = chatEl.querySelectorAll(".msg.ai .bubble");
      const lastText = ais.length
        ? ais[ais.length - 1].textContent.trim() : "";
      if (!lastText || lastText !== String(data.text || "").trim()) {
        addMsg("ai", data.text || "");
      }
      currentMsg = null;
      setStage("completed");
      refreshGallery();   // 新产物自动出现（无需手动刷新页面）
      break;
    }
    case "skill_remembered":
      addMemoryBadge(`${data.task} → ${data.result}`);
      break;
    case "memory_recalled":
      addMemoryBadge("历史经验已注入");
      break;
    case "vram": {
      // 显存 + 温度（渲染期唯一可见的硬件指标；熔断状态用 hot 高亮）
      const t = (typeof data.temp_c === "number") ? ` ${Math.round(data.temp_c)}°C` : "";
      vramEl.textContent = `GPU ${data.free_gb.toFixed(1)}/${data.total_gb.toFixed(1)} GB${t}`;
      vramEl.classList.toggle("low", data.free_gb < 2);
      vramEl.classList.toggle("hot", typeof data.temp_c === "number" && data.temp_c >= 85);
      break;
    }
    case "progress": {
      // 渲染期心跳：队列位置 + 已运行时长（长任务唯一可见的进度信号）
      const sec = Number(data.elapsed_sec || 0);
      const mmss = sec >= 60 ? `${Math.floor(sec / 60)}分${sec % 60}秒` : `${sec}秒`;
      queueEl.textContent = sec > 0
        ? `队列 ${data.queue_position} · 已运行 ${mmss}`
        : `队列 ${data.queue_position}`;
      break;
    }
  }
}

// ---------- 启动 ----------
formEl.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = inputEl.value.trim();
  if ((!text && !pendingImage) || !activeProject) return;
  inputEl.value = "";
  const attach = pendingImage;   // 快照：上传完成后清空全局状态
  const bubble = addMsg("user", text, attach ? attach.previewUrl : null);
  currentMsg = null;
  const submitBtn = formEl.querySelector('button[type="submit"]');
  let imageInfo = null;
  try {
    if (attach) {
      submitBtn.disabled = true;
      const dataUrl = await new Promise((resolve, reject) => {
        const fr = new FileReader();
        fr.onload = () => resolve(fr.result);
        fr.onerror = () => reject(new Error("读取文件失败"));
        fr.readAsDataURL(attach.file);
      });
      const r = await fetch("/api/upload", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project_id: activeProject.id,
          name: attach.name,
          data: String(dataUrl).split(",")[1],
        }),
      });
      const d = await r.json();
      if (!d.ok) throw new Error(d.error || "上传失败");
      imageInfo = { url: d.url, name: d.name,
                    local_path: d.local_path, server_name: d.server_name };
      const img = bubble.querySelector(".bubble-img");
      if (img) img.src = d.url;   // 换成服务器地址，刷新后仍可显示
      if (pendingImage === attach) {
        URL.revokeObjectURL(attach.previewUrl);
        pendingImage = null;
        renderAttachBar();
      }
    }
    fetch("/api/message", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, project_id: activeProject.id,
                             image: imageInfo }),
    });
  } catch (err) {
    alert("图片上传失败：" + (err && err.message ? err.message : err));
  } finally {
    submitBtn.disabled = false;
  }
});

// ---------- SSE 僵死检测与自愈 ----------
// 修复：服务器重启/标签页休眠后 EventSource 假死，页面永远停在旧画面
let es = null;
let lastEventTs = Date.now();

function connectSSE() {
  if (es) es.close();
  es = new EventSource("/events");
  es.onmessage = (e) => {
    lastEventTs = Date.now();
    try { handleEvent(JSON.parse(e.data)); } catch (err) {}
  };
  es.onerror = () => { /* EventSource 自动重连；stale 检查兜底 */ };
}

// SSE 静默 >45 秒（错过 3 次心跳）→ 重连 + 拉取最新状态
function healthCheck() {
  if (Date.now() - lastEventTs > 45000) {
    lastEventTs = Date.now();
    connectSSE();
    refreshGallery();
    if (activeProject) {
      fetch("/api/status?project=" + encodeURIComponent(activeProject.id))
        .then((r) => r.json())
        .then((st) => {
          if (st.draft) renderGraph(st.draft);
          if (st.stage) setStage(st.stage);
        })
        .catch(() => {});
    }
  }
}

setInterval(healthCheck, 10000);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) healthCheck();   // 标签页从休眠恢复时立即自检
});

// ---------- 设置面板 ----------
async function openSettings() {
  try {
    const r = await fetch("/api/settings");
    const d = await r.json();
    $("#set_base").value = d.effective?.base_url || "";
    $("#set_model").value = d.effective?.model || "";
    $("#set_vmodel").value = "";
    $("#set_key").value = "";
    $("#set_key").placeholder = d.effective?.api_key_masked
      ? `已设置（${d.effective.api_key_masked}），留空保持不变` : "sk-...";
  } catch (e) { /* 后端未就绪 */ }
  $("#settingsmodal").classList.remove("hidden");
}

$("#settingsbtn").addEventListener("click", openSettings);
$("#set_close").addEventListener("click", () =>
  $("#settingsmodal").classList.add("hidden"));
$("#set_key_toggle").addEventListener("click", () => {
  const k = $("#set_key");
  k.type = k.type === "password" ? "text" : "password";
  $("#set_key_toggle").textContent = k.type === "password" ? "显示" : "隐藏";
});
$("#set_save").addEventListener("click", async () => {
  const body = {};
  const base = $("#set_base").value.trim();
  const key = $("#set_key").value.trim();
  const model = $("#set_model").value.trim();
  const vmodel = $("#set_vmodel").value.trim();
  if (base) body.llm_base_url = base;
  if (key) body.llm_api_key = key;
  if (model) body.llm_model = model;
  if (vmodel) body.vlm_model = vmodel;
  const r = await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const d = await r.json();
  if (d.ok) {
    $("#settingsmodal").classList.add("hidden");
    statusEl.textContent = "设置已保存并生效";
  } else {
    statusEl.textContent = "设置保存失败";
  }
});

(async function init() {
  await loadProjects();
  connectSSE();
  // 必须带当前项目参数：与 activeProject 保持一致（否则默认项目快照覆盖当前视图）
  const r = await fetch("/api/status?project=" +
                        encodeURIComponent(activeProject.id));
  const st = await r.json();
  if (st.draft) renderGraph(st.draft);
  if (st.outputs) renderGallery(st.outputs);
  if (st.stage) setStage(st.stage);
})();
