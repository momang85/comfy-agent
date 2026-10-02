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

// ---------- 界面语言（zh/en） ----------
// 引擎与大脑产出的中文内容（诊断/对话）不在此翻译范围内——对话语言由模型自己跟随用户。
let LANG = localStorage.getItem("comfy_agent_lang")
  || ((navigator.language || "").toLowerCase().startsWith("zh") ? "zh" : "en");

// 原地翻译：中文是键也是缺省值，en 缺失时回退中文（新增文案不翻译也不会白屏）
function t(zh, en) { return LANG === "en" && en != null ? en : zh; }

// index.html 里 data-i18n 标记的静态文案（键 = 中文原文，值 = 英文）
const I18N = {
  "🧠 ComfyUI 大脑": "🧠 ComfyUI Brain",
  "＋新建项目": "＋ New project",
  "校验": "Validate", "修复": "Repair", "提交": "Submit",
  "执行": "Run", "下载": "Download", "评估": "Evaluate",
  "空闲": "Idle", "■ 停止": "■ Stop", "发送": "Send", "队列 --": "Queue --",
  "工作流": "Workflow", "详情": "Details", "产物": "Outputs",
  "还没有工作流。发送任务后，AI 构建的工作流会实时显示在这里。":
    "No workflow yet. Send a task and the AI-built graph appears here live.",
  "滚轮缩放 · 拖拽平移 · 双击复位": "Scroll to zoom · drag to pan · double-click to fit",
  "新建项目": "New project", "项目名称": "Project name",
  "例如：头像批量出图": "e.g. avatar batch",
  "项目之间上下文完全隔离（对话/记忆/产物各自独立）。":
    "Projects are fully isolated (chat / memory / outputs).",
  "创建": "Create", "取消": "Cancel", "缺失模型下载": "Missing model download",
  "不下载": "Don't download", "取消下载": "Cancel download", "关闭": "Close",
  "大脑 API 设置": "Brain API settings",
  "API 地址": "API base URL", "大脑模型": "Brain model",
  "视觉模型": "Vision model",
  "必须支持视觉，如 glm-4.6v / qwen-vl-max":
    "must be a vision model, e.g. glm-4.6v / qwen-vl-max",
  "视觉地址与 Key 默认跟随大脑。":
    "Vision base URL and key default to the brain's settings.",
  "视觉 API 地址（可选）": "Vision API base URL (optional)",
  "视觉 Key（可选）": "Vision API key (optional)",
  "留空 = 跟随大脑": "empty = follow brain",
  "Key 仅保存在本机 .comfy-agent/settings.json（不入 git）。与大脑模型同 key 时视觉 key 留空即可。多数 provider 的视觉模型名与文本模型不同，必须单独填。":
    "The key is stored locally in .comfy-agent/settings.json (never committed). If vision uses the same key as the brain, leave the vision key empty. Most providers use a different model name for vision — fill it in separately.",
  "保存": "Save", "显示": "Show", "隐藏": "Hide",
  "切换项目（上下文完全隔离）": "Switch project (fully isolated context)",
  "新建项目": "New project",
  "中断当前任务": "Interrupt current task",
  "重扫本机节点与模型清单（刚下载模型/装了节点后点这里）":
    "Rescan local nodes & models (click after downloading models / installing nodes)",
  "GPU 显存": "GPU VRAM",
  "大脑 API 设置": "Brain API settings",
  "添加图片（支持拖拽/粘贴，单张）": "Add image (drag & drop / paste, one at a time)",
  "用自然语言描述需求，可附图片做图生图/改图":
    "Describe what you want in natural language; attach an image for img2img / editing",
  "放大": "Zoom in", "缩小": "Zoom out", "适配窗口": "Fit view", "重置视图": "Reset view",
};

function applyI18n() {
  document.documentElement.lang = LANG;
  document.title = t("ComfyUI 大脑", "ComfyUI Brain");
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    const key = el.dataset.i18n;
    el.textContent = t(key, I18N[key] != null ? I18N[key] : key);
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
    const key = el.dataset.i18nPlaceholder;
    el.placeholder = t(key, I18N[key] != null ? I18N[key] : key);
  });
  document.querySelectorAll("[data-i18n-title]").forEach((el) => {
    const key = el.dataset.i18nTitle;
    el.title = t(key, I18N[key] != null ? I18N[key] : key);
  });
  const lb = $("#langbtn");
  if (lb) lb.textContent = LANG === "en" ? "中" : "EN";
}

$("#langbtn").addEventListener("click", () => {
  LANG = LANG === "en" ? "zh" : "en";
  localStorage.setItem("comfy_agent_lang", LANG);
  applyI18n();
  refreshGallery();   // 空态提示等已渲染文案跟随切换
});

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
  mdlRec = null;
  $("#modeldlmodal").classList.add("hidden");
  refreshModelDownloads();
}

$("#newproject").addEventListener("click", async () => {
  // 应用内输入框替代原生 prompt()：风格统一且自动化可测
  $("#np_name").value = "";
  $("#newprojectmodal").classList.remove("hidden");
  $("#np_name").focus();
});
$("#np_cancel").addEventListener("click", () =>
  $("#newprojectmodal").classList.add("hidden"));
$("#np_ok").addEventListener("click", async () => {
  const name = $("#np_name").value.trim();
  if (!name) { $("#np_name").focus(); return; }
  try {
    const r = await fetch("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    const d = await r.json();
    if (d.ok) {
      $("#newprojectmodal").classList.add("hidden");
      await loadProjects();
      switchProject(d.project.id);
    } else {
      statusEl.textContent = t("创建失败：", "Create failed: ") + (d.error || t("未知原因", "unknown"));
    }
  } catch (e) {
    statusEl.textContent = t("创建请求失败", "Create request failed");
  }
});
$("#np_name").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); $("#np_ok").click(); }
});

projectSel.addEventListener("change", () => switchProject(projectSel.value));

// ---------- 图片附件（点击/拖拽/粘贴，单张） ----------
function setPendingImage(file) {
  if (!file) return;
  if (!file.type.startsWith("image/")) {
    alert(t("仅支持图片文件（PNG/JPEG/WebP）",
            "Only image files are supported (PNG/JPEG/WebP)"));
    return;
  }
  if (file.size > MAX_ATTACH_BYTES) {
    alert(t("图片不能超过 25MB", "Images must be 25MB or smaller"));
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
    <button type="button" class="x" title="${t("移除", "Remove")}">✕</button>`;
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
  // 竞态防护：记录发起时的项目，响应回来时若已切走就丢弃
  // （实测切项目时旧请求晚到，会把上一项目的产物画进新项目画廊）
  if (!activeProject) return;
  const pid = activeProject.id;
  try {
    const r = await fetch("/api/status?project=" +
                          encodeURIComponent(pid));
    const st = await r.json();
    if (!activeProject || activeProject.id !== pid) return;
    renderGallery(st.outputs || []);
  } catch (e) { /* 后端未就绪 */ }
}

function renderGallery(outputs) {
  galleryEl.innerHTML = "";
  if (!outputs || !outputs.length) {
    const tip = document.createElement("div");
    tip.className = "gallery-tip";
    tip.textContent = t("该项目暂无产物。历史资产在「默认项目」，可从顶部下拉切换。",
      "No outputs in this project yet. Historical assets live in the “Default” project (top dropdown).");
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
    t2.textContent = `#${nid} · ${Object.keys(node.inputs || {}).length} ${t("输入", "inputs")}`;
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
  metaEl.textContent = `${Object.keys(wf).length} ${t("节点", "nodes")} · ${edges.length} ${t("连线", "edges")}`;
}

function showNodeDetail(nid, node) {
  const rows = Object.entries(node.inputs || {}).map(([k, v]) =>
    `<div class="kv">${k}: ${Array.isArray(v) ? "←#" + v[0] : JSON.stringify(v)}</div>`).join("");
  detailEl.classList.remove("hidden");
  detailEl.innerHTML =
    `<h4>#${nid} ${node.class_type}</h4>${rows || `<div class='kv'>${t("无输入", "no inputs")}</div>`}`;
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
    img.alt = t("附件图片", "attached image");
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
      `<details class="reasoning"><summary>${t("思考过程", "Thinking")}</summary><div class="rbody"></div></details>
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
        out.push("\n" + t("[已提交工具调用]", "[tool call submitted]"));
        return out.join("");
      }
      out.push(body);
      return out.join("");
    }
    const body = s.slice(i + FENCE.length, j);
    out.push(TOOL_KEY_RE.test(body) ? "\n" + t("[已提交工具调用]", "[tool call submitted]") + "\n"
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
  t.textContent = t("⚠ 大脑出错（会话已恢复，可继续发消息）",
                    "⚠ Brain error (session recovered; you can keep chatting)");
  const s = document.createElement("div");
  s.className = "sum";
  s.style.whiteSpace = "pre-wrap";
  s.textContent = String(message || t("未知错误", "unknown error"));
  d.appendChild(t);
  d.appendChild(s);
  if (traceback) {
    const det = document.createElement("details");
    const sm = document.createElement("summary");
    sm.textContent = t("技术细节", "Technical details");
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
  statusEl.textContent = stage === "idle" ? t("空闲", "Idle") :
    stage === "completed" ? t("完成", "Done") :
    stage === "thinking" ? t("构建中…", "Building…") :
    stage === "error" ? t("出错", "Error") : `${t("阶段", "Stage")}: ${stage}`;
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

// 引擎代码已更新但服务没重启：这条提示**不自动消失**，并且醒目用红色。
// 项目 6 事故就是因为服务静默跑旧代码（新增的输入声明没生效 → 连续工具失败）
function showStaleCodeWarning(on) {
  const el = document.getElementById("warnline");
  if (!el) return;
  if (!on) { el.classList.add("hidden"); return; }
  el.textContent = t("⚠ 引擎代码已更新，当前服务在跑旧代码：请重启（一键启动.bat）",
    "⚠ Engine code changed but the server is running old code: please restart (scripts/start.sh)");
  el.style.cssText = "color:#ff6b6b;font-size:12px;font-weight:600;" +
    "max-width:460px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap";
  el.classList.remove("hidden");
  if (warnTimer) clearTimeout(warnTimer);
  warnTimer = null;          // 不自动隐藏
}

function applyStaleFlag(st) {
  if (!st) return;
  if (st.stale_code === true || st.stale === true) showStaleCodeWarning(true);
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
  // 视觉不可用时必须写出来：否则"通过"只是几何检查的结论，语义根本没查
  const skipped = ev.vlm_error
    ? `<div class="issue" style="color:#e0a800">⚠ ${t("语义评估已跳过（视觉模型不可用）：",
        "Semantic check skipped (vision model unavailable): ")}${
        escapeHtml(String(ev.vlm_error).slice(0, 160))}</div>`
    : "";
  return `<h4>${ev.kind === "video" || ev.kind === "video_forced"
    ? t("🎬 视频评估", "🎬 Video evaluation") : t("🖼 图像评估", "🖼 Image evaluation")}
    <span class="${cls}">${ev.verdict === true ? t("通过", "Pass")
      : ev.verdict === false ? t("不达标", "Below bar") : t("未知", "Unknown")}${score}</span></h4>` +
    skipped +
    (ev.issues || []).map((i) =>
      `<div class="issue"><span class="loc">${escapeHtml(i.location || "")}</span> ${escapeHtml(i.description || "")}</div>`).join("");
}

function addMemoryBadge(text) {
  const b = document.createElement("span");
  b.className = "mem-badge";
  b.textContent = t("🧠 技能召回", "🧠 Skill recall");
  b.title = text;
  memoryEl.appendChild(b);
}

// ---------- 停止 ----------
stopBtn.addEventListener("click", () => {
  stopBtn.disabled = true;
  stopBtn.textContent = t("■ 停止中…", "■ Stopping…");
  fetch("/api/interrupt", { method: "POST" })
    .catch(() => {});
  setTimeout(() => { stopBtn.disabled = false; stopBtn.textContent = t("■ 停止", "■ Stop"); }, 3000);
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
      addToolCard(data.tool, data.args, true, t("调用中…", "running…"), false);
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
        showWarning(String(data.detail?.warning || t("警告", "Warning")));
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
      // 本轮成本可见：渲染几次、其中几次被判定为浪费（结构性护栏拦下的）
      if (data.task && data.task.renders != null) {
        const tsk = data.task;
        statusEl.textContent = `${t("完成", "Done")} · ${t("渲染", "renders")} ${tsk.renders}` +
          (tsk.wasted ? ` · ${t("其中拦截/浪费", "guarded/wasted")} ${tsk.wasted}` : "");
      }
      refreshGallery();   // 新产物自动出现（无需手动刷新页面）
      break;
    }
    case "skill_remembered":
      addMemoryBadge(`${data.task} → ${data.result}`);
      break;
    case "memory_recalled":
      addMemoryBadge(t("历史经验已注入", "Past experience injected"));
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
      const mmss = sec >= 60
        ? `${Math.floor(sec / 60)}${t("分", "m")}${sec % 60}${t("秒", "s")}`
        : `${sec}${t("秒", "s")}`;
      queueEl.textContent = sec > 0
        ? `${t("队列", "Queue")} ${data.queue_position} · ${t("已运行", "ran for")} ${mmss}`
        : `${t("队列", "Queue")} ${data.queue_position}`;
      break;
    }
    case "model_missing": {
      // 引擎报缺模型：提示条先给一句（随后大脑会弹下载确认框）
      const names = (data.models || []).join("、");
      showWarning(`${t("缺少模型", "Missing models")}：${names}${t("（正在查询可下载来源）",
        " (looking up download sources)")}`);
      break;
    }
    case "model_download":
      showModelDownload(data);
      break;
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
        fr.onerror = () => reject(new Error(t("读取文件失败", "failed to read file")));
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
      if (!d.ok) throw new Error(d.error || t("上传失败", "upload failed"));
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
    alert(t("图片上传失败：", "Image upload failed: ") + (err && err.message ? err.message : err));
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
          applyStaleFlag(st);
        })
        .catch(() => {});
    }
  }
}

setInterval(healthCheck, 10000);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) healthCheck();   // 标签页从休眠恢复时立即自检
});

$("#refreshworld").addEventListener("click", async () => {
  const btn = $("#refreshworld");
  btn.disabled = true;
  statusEl.textContent = t("重扫节点与模型清单…", "Rescanning nodes & models…");
  try {
    const r = await fetch("/api/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_id: activeProject ? activeProject.id : null }),
    });
    const d = await r.json();
    if (d.ok) {
      const folders = Object.entries(d.model_folders || {})
        .map(([f, n]) => `${f}:${n}`).join(" ");
      statusEl.textContent = `${t("已重扫", "Rescanned")} · ${t("节点", "nodes")} ${d.nodes} · ${folders}`;
      if (d.changed) showWarning(t("世界清单已更新（新下载的模型/新节点现在可见）",
        "World inventory updated (newly downloaded models / new nodes are now visible)"));
    } else {
      statusEl.textContent = t("重扫失败：", "Rescan failed: ") + (d.error || "");
    }
  } catch (e) {
    statusEl.textContent = t("重扫请求失败", "Rescan request failed");
  } finally {
    btn.disabled = false;
  }
});

// ---------- 缺失模型下载弹窗 ----------
// 大脑发现缺模型 → search_models → download_model 弹出本弹窗；用户点"下载"
// 后同一弹窗内变成进度条（含速度/取消），结束后关闭或展示失败原因。
let mdlRec = null;

function mdlSourceName(key) {
  return {
    manager: t("本机 Manager 目录", "Local Manager cache"),
    "hf-mirror": t("HuggingFace 镜像", "HuggingFace mirror"),
    hf: "HuggingFace",
    civitai: "Civitai",
    modelscope: "ModelScope",
  }[key] || key || t("未知", "unknown");
}

function mdlRow(k, v) {
  return `<div class="mdl-row"><span class="k">${escapeHtml(k)}</span>` +
         `<span class="v">${escapeHtml(v == null ? "" : String(v))}</span></div>`;
}

function showModelDownload(rec) {
  if (!rec || !rec.id) return;
  if (activeProject && rec.project && rec.project !== activeProject.id) return;
  // 已有下载在跑时不抢占弹窗（否则用户看不到进行中的那个）
  if (mdlRec && mdlRec.id !== rec.id && mdlRec.state === "downloading"
      && rec.state === "awaiting_confirm") {
    showWarning(`${t("已有下载进行中：", "A download is already running: ")}${mdlRec.filename}`);
    return;
  }
  mdlRec = rec;
  const body = $("#mdl_body");
  const cons = rec.consumer || {};
  body.innerHTML =
    mdlRow(t("文件", "File"), rec.filename) +
    mdlRow(t("名称", "Name"), rec.name && rec.name !== rec.filename ? rec.name : "—") +
    mdlRow(t("大小", "Size"), rec.size_text || t("未知", "unknown")) +
    mdlRow(t("来源", "Source"), mdlSourceName(rec.source)) +
    mdlRow(t("适配", "Fit"), rec.fit && rec.fit.fits ? t("适配本机", "fits this machine") : t("不适合本机", "not a fit for this machine")) +
    mdlRow(t("加载节点", "Loader node"), cons.loader
      ? `${cons.loader}${cons.loader_present === false ? t("（本机没有）", " (not installed)") : ""}`
      : (cons.candidates && cons.candidates.length ? t("未找到可用节点", "no usable node found") : t("无需加载节点", "no loader node needed"))) +
    mdlRow(t("存放目录", "Target dir"), rec.target_dir || rec.dest || "—");
  // 下完也用不了的情况必须先说清（项目 5：下了 21MB 才发现没有加载节点）
  if (cons.usable === false || rec.usable === false) {
    body.innerHTML += `<div class="mdl-warn">⚠ ${t("本机没有能加载该模型的节点：",
      "No node on this machine can load this model: ")}${
      escapeHtml(cons.note || t("详情见大脑的说明", "see the brain's note"))}。<b>${t("下载解决不了问题",
      "Downloading won't fix this")}</b>${t("，建议先让大脑改用替代链路。",
      " — ask the brain to switch to an alternative route first.")}</div>`;
  }
  const ok = $("#mdl_ok"), no = $("#mdl_no");
  const cancel = $("#mdl_cancel"), close = $("#mdl_close");
  const prog = $("#mdl_progress");
  if (rec.state === "awaiting_confirm") {
    const notes = ((rec.fit && rec.fit.notes) || []).concat(rec.warnings || []);
    if (notes.length) {
      body.innerHTML += `<div class="mdl-warn">⚠ ${escapeHtml(notes.join("；"))}</div>`;
    }
    prog.classList.add("hidden");
    ok.classList.remove("hidden");
    no.classList.remove("hidden");
    cancel.classList.add("hidden");
    close.classList.add("hidden");
  } else if (rec.state === "downloading") {
    ok.classList.add("hidden");
    no.classList.add("hidden");
    cancel.classList.remove("hidden");
    close.classList.add("hidden");
    prog.classList.remove("hidden");
    $("#mdl_bar").style.width = (rec.percent || 0) + "%";
    $("#mdl_stat").textContent =
      `${(rec.percent || 0).toFixed(1)}% · ${rec.size_text || ""}` +
      (rec.speed_text ? ` · ${rec.speed_text}` : "") +
      (rec.elapsed ? ` · ${t("已用", "elapsed")} ${rec.elapsed}s` : "");
  } else {
    ok.classList.add("hidden");
    no.classList.add("hidden");
    cancel.classList.add("hidden");
    close.classList.remove("hidden");
    prog.classList.add("hidden");
    const msg = rec.state === "done"
      ? ("✅ " + t("下载完成", "Download finished") +
         (rec.retry && rec.retry.from_missing_model
          ? t("，正在自动重跑刚才失败的任务…", " — rerunning the task that failed on it…")
          : t("，模型已就绪", " — model is ready")))
      : rec.state === "declined" ? t("已拒绝下载，大脑会按缺模型降级处理。",
          "Download declined; the brain will fall back as if the model were missing.")
      : rec.state === "canceled" ? t("已取消下载（临时文件已清理）。",
          "Download canceled (temp files cleaned up).")
      : `❌ ${t("下载失败：", "Download failed: ")}${rec.error || t("未知原因", "unknown")}。${t("大脑会按缺模型降级处理。",
          "The brain will fall back as if the model were missing.")}`;
    body.innerHTML += `<div class="mdl-warn">${escapeHtml(msg)}</div>`;
    if (rec.state === "failed") showWarning(`${t("模型下载失败：", "Model download failed: ")}${rec.error || ""}`);
  }
  $("#modeldlmodal").classList.remove("hidden");
}

async function mdlDecide(action) {
  if (!mdlRec) return;
  try {
    const r = await fetch("/api/model-download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ download_id: mdlRec.id, action }),
    });
    const d = await r.json();
    if (!d.ok) showWarning(d.error || t("操作失败", "operation failed"));
    else if (d.download) showModelDownload(d.download);
  } catch (e) {
    showWarning(t("下载操作请求失败：", "Download action request failed: ") + (e && e.message ? e.message : e));
  }
}

$("#mdl_ok").addEventListener("click", () => mdlDecide("confirm"));
$("#mdl_no").addEventListener("click", () => mdlDecide("decline"));
$("#mdl_cancel").addEventListener("click", () => mdlDecide("cancel"));
$("#mdl_close").addEventListener("click", () => {
  $("#modeldlmodal").classList.add("hidden");
  mdlRec = null;
});

// 刷新/换项目后恢复未决的下载弹窗（服务器是唯一真相源）
async function refreshModelDownloads() {
  try {
    const pid = activeProject ? activeProject.id : "";
    const r = await fetch("/api/model-downloads?project=" + encodeURIComponent(pid));
    const d = await r.json();
    const pending = (d.downloads || []).filter((x) =>
      x.state === "awaiting_confirm" || x.state === "downloading");
    if (pending.length) showModelDownload(pending[0]);
  } catch (e) { /* 后端未就绪 */ }
}

// ---------- 设置面板 ----------
// 视觉那一路的生效值必须显示出来：只写"大脑"那套时，用户改了 API 地址会以为
// 没保存（视觉其实还是旧地址 → analyze_image/评估 400）
function renderVisionNote(d) {
  const el = document.getElementById("set_vision_note");
  if (!el) return;
  const v = d.vision || {};
  const st = d.vision_state || {};
  const parts = [`${t("视觉地址", "Vision base URL")}：${v.base_source || t("跟随大脑", "follows brain")}`];
  parts.push(`${t("视觉 Key", "Vision key")}：${v.key_source || t("跟随大脑", "follows brain")}`);
  let health = t("未自检", "not checked");
  if (st.checked) {
    health = st.ok === false
      ? (st.transient
         ? `⚠ ${t("未确定（服务商瞬时报错：", "undetermined (provider transient error: ")}${(st.error || "").slice(0, 60)}）`
         : `❌ ${t("不可用（", "unavailable (")}${st.error || t("原因未知", "unknown")})`)
      : st.verified === false ? `⚠ ${t("未验证（", "unverified (")}${st.error || t("探针没取到回复", "probe got no reply")})`
      : t("✅ 可用（已实测看图）", "✅ available (verified with a real image)");
  }
  el.textContent = `${parts.join(" · ")} · ${t("自检", "self-check")}：${health}`;
  el.style.color = st.checked && st.ok === false ? "#e0a800" : "";
}

async function openSettings() {
  try {
    const r = await fetch("/api/settings");
    const d = await r.json();
    $("#set_base").value = d.effective?.base_url || "";
    $("#set_model").value = d.effective?.model || "";
    // 预填当前生效的视觉模型（此前恒为空 + 硬编码占位符，用户根本看不到实际用的什么）
    $("#set_vmodel").value = d.vision?.model || "";
    $("#set_vbase").value = "";
    $("#set_vkey").value = "";
    $("#set_key").value = "";
    $("#set_key").placeholder = d.effective?.api_key_masked
      ? `${t("已设置（", "set (")}${d.effective.api_key_masked})，${t("留空保持不变", "leave empty to keep")}` : "sk-...";
    $("#set_vkey").placeholder = d.vision?.api_key_masked
      ? `${t("已设置（", "set (")}${d.vision.api_key_masked})，${t("留空保持不变", "leave empty to keep")}` : t("留空 = 跟随大脑", "empty = follow brain");
    renderVisionNote(d);
  } catch (e) { /* 后端未就绪 */ }
  $("#settingsmodal").classList.remove("hidden");
}

$("#settingsbtn").addEventListener("click", openSettings);
$("#set_close").addEventListener("click", () =>
  $("#settingsmodal").classList.add("hidden"));
$("#set_key_toggle").addEventListener("click", () => {
  const k = $("#set_key");
  k.type = k.type === "password" ? "text" : "password";
  $("#set_key_toggle").textContent = k.type === "password" ? t("显示", "Show") : t("隐藏", "Hide");
});
$("#set_save").addEventListener("click", async () => {
  const body = {};
  const base = $("#set_base").value.trim();
  const key = $("#set_key").value.trim();
  const model = $("#set_model").value.trim();
  const vmodel = $("#set_vmodel").value.trim();
  const vbase = $("#set_vbase").value.trim();
  const vkey = $("#set_vkey").value.trim();
  if (base) body.llm_base_url = base;
  if (key) body.llm_api_key = key;
  if (model) body.llm_model = model;
  if (vmodel) body.vlm_model = vmodel;
  if (vbase) body.vlm_base_url = vbase;
  if (vkey) body.vlm_api_key = vkey;
  const r = await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const d = await r.json();
  if (d.ok) {
    $("#settingsmodal").classList.add("hidden");
    statusEl.textContent = t("设置已保存并生效", "Settings saved and in effect");
    refreshVisionNote();      // 视觉自检在服务端后台跑，稍后单独刷新提示
  } else {
    statusEl.textContent = t("设置保存失败", "Failed to save settings");
  }
});

// 保存后单独刷新视觉自检结论（探针是网络请求，不能卡住保存响应）
async function refreshVisionNote() {
  try {
    const r = await fetch("/api/settings");
    const d = await r.json();
    renderVisionNote(d);
    const st = d.vision_state || {};
    if (st.checked && st.ok === false && !st.transient) {
      showWarning(`${t("视觉不可用（", "Vision unavailable (")}${d.vision?.model || "?"}）：`
                  + `${st.error || t("原因未知", "unknown")}——${t("看图与图像评估会失败",
                      "image analysis and evaluation will fail")}`);
    }
  } catch (e) { /* 后端未就绪 */ }
}

(async function init() {
  applyI18n();
  await loadProjects();
  connectSSE();
  // 必须带当前项目参数：与 activeProject 保持一致（否则默认项目快照覆盖当前视图）
  const r = await fetch("/api/status?project=" +
                        encodeURIComponent(activeProject.id));
  const st = await r.json();
  if (st.draft) renderGraph(st.draft);
  if (st.outputs) renderGallery(st.outputs);
  if (st.stage) setStage(st.stage);
  applyStaleFlag(st);
  refreshModelDownloads();
})();
