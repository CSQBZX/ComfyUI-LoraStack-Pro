/**
 * LoRA Stack Pro —— 前端面板
 *
 * 一个节点内完成：逐层浏览 loras 目录、跨层搜索（文件名 / 文件夹名 / 训练标签）、
 * 预览图网格、拖拽排序、权重调节、本地元数据与 Civitai 信息查询。
 */

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const API_ROOT = "/lora_stack_pro";
const CSS_URL = new URL("./lora_stack_pro.css", import.meta.url).href;

const SVG_NS = "http://www.w3.org/2000/svg";

const ICON = {
  search: "M10.5 3a7.5 7.5 0 1 0 4.55 13.46l4.25 4.25 1.41-1.41-4.25-4.25A7.5 7.5 0 0 0 10.5 3zm0 2a5.5 5.5 0 1 1 0 11 5.5 5.5 0 0 1 0-11z",
  close: "M5.7 4.3 4.3 5.7 10.6 12l-6.3 6.3 1.4 1.4L12 13.4l6.3 6.3 1.4-1.4L13.4 12l6.3-6.3-1.4-1.4L12 10.6z",
  refresh: "M12 4V1L8 5l4 4V6a6 6 0 1 1-6 6H4a8 8 0 1 0 8-8z",
  info: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z",
  folder: "M3 5a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
  back: "M20 11H7.8l5.6-5.6L12 4l-8 8 8 8 1.4-1.4L7.8 13H20z",
  grip: "M9 4h1.5v16H9zM13.5 4H15v16h-1.5z",
  check: "M9.5 17.6 4.4 12.5l1.4-1.4 3.7 3.7 8-8 1.4 1.4z",
  chevronUp: "M12 8.4 5.6 14.8 7 16.2l5-5 5 5 1.4-1.4z",
  chevronDown: "M12 15.6 5.6 9.2 7 7.8l5 5 5-5 1.4 1.4z",
  star: "M12 17.3 6.2 20.6l1.1-6.4L2.7 9.7l6.5-.9L12 3l2.8 5.8 6.5.9-4.6 4.5 1.1 6.4z",
  gear: "M19.4 13a7.5 7.5 0 0 0 0-2l2-1.6-2-3.4-2.4 1a7.6 7.6 0 0 0-1.7-1L15 3H9l-.3 2.9a7.6 7.6 0 0 0-1.7 1l-2.4-1-2 3.4L4.6 11a7.5 7.5 0 0 0 0 2l-2 1.6 2 3.4 2.4-1c.5.4 1.1.8 1.7 1L9 21h6l.3-2.9c.6-.3 1.2-.6 1.7-1l2.4 1 2-3.4zM12 15.5A3.5 3.5 0 1 1 12 8.5a3.5 3.5 0 0 1 0 7z",
  warn: "M12 2 1 21h22L12 2zm1 15h-2v-2h2v2zm0-4h-2V9h2v4z",
  sync: "M17.65 6.35A8 8 0 1 0 20 12h-2a6 6 0 1 1-1.76-4.24L13 11h7V4z",
  folderOpen: "M20 6h-8l-2-2H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2zm0 12H4V8h16z",
  bookmark: "M17 3H7a2 2 0 0 0-2 2v16l7-3 7 3V5a2 2 0 0 0-2-2z",
  image: "M21 19V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2zM8.5 13.5l2.5 3 3.5-4.5 4.5 6H5l3.5-4.5z",
};

const SETTINGS_KEY = "loraStackPro.settings.v1";
const DEFAULT_SETTINGS = {
  hoverPreview: true,
  hoverSize: 320,
  weightStep: 0.05,
  dirSort: "name",     // name | mtime | size
  dirFilter: "",       // "" | nocover | dup
  showStackInDir: false,  // 目录视图底部再列一遍整个堆（默认关：堆就在下方，重复渲染纯属浪费）
};

const SEARCH_PAGE = 20;   // 搜索结果每批渲染数量，滚到底自动追加下一批
const DIR_PAGE = 20;      // 目录浏览每批渲染数量，滚到底自动追加下一批
const PAGE_TRIGGER_PX = 160;  // 距底部还有多少像素时触发下一批

const SORT_OPTIONS = [
  ["name", "名称"],
  ["mtime", "时间"],
  ["size", "大小"],
];

const FILTER_OPTIONS = [
  ["", "全部"],
  ["nocover", "无封面"],
  ["dup", "重名"],
];

function loadSettings() {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (!raw) return { ...DEFAULT_SETTINGS };
    const parsed = JSON.parse(raw);
    return { ...DEFAULT_SETTINGS, ...(parsed && typeof parsed === "object" ? parsed : {}) };
  } catch (err) {
    return { ...DEFAULT_SETTINGS };
  }
}

function saveSettings(settings) {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  } catch (err) {
    /* 隐私模式下可能不可写，忽略 */
  }
}

function ensureStyle() {
  if (document.querySelector('link[data-lsp-style="1"]')) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = CSS_URL;
  link.dataset.lspStyle = "1";
  document.head.appendChild(link);
}

function svg(path, size = 14) {
  const node = document.createElementNS(SVG_NS, "svg");
  node.setAttribute("viewBox", "0 0 24 24");
  node.setAttribute("width", String(size));
  node.setAttribute("height", String(size));
  node.setAttribute("fill", "currentColor");
  const p = document.createElementNS(SVG_NS, "path");
  p.setAttribute("d", path);
  node.appendChild(p);
  return node;
}

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function debounce(fn, wait) {
  let timer = null;
  return function debounced(...args) {
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(this, args), wait);
  };
}

/**
 * 标签索引的状态轮询由所有节点面板共享。
 * 一个工作流里放三个 LoRA Stack Pro，原来就是三份并发轮询、三倍 /stats 请求。
 */
const INDEX_POLL = {
  running: false,
  done: false,
  hint: "",
  listeners: new Set(),
  subscribe(handler) {
    if (typeof handler === "function") this.listeners.add(handler);
  },
  notify(hint) {
    this.hint = hint;
    for (const handler of this.listeners) {
      try {
        handler(hint);
      } catch (err) {
        /* 单个面板出错不影响其它面板 */
      }
    }
  },
};

async function pollIndexShared() {
  if (INDEX_POLL.running || INDEX_POLL.done) return;
  INDEX_POLL.running = true;
  try {
    for (let attempt = 0; attempt < 20; attempt += 1) {
      let stats = null;
      try {
        stats = await requestJSON("/stats");
      } catch (err) {
        return;
      }
      if (stats.error) {
        INDEX_POLL.done = true;
        INDEX_POLL.notify(`索引异常：${stats.error}`);
        return;
      }
      if (stats.tag_ready) {
        INDEX_POLL.done = true;
        INDEX_POLL.notify("");
        return;
      }
      INDEX_POLL.notify(`标签索引构建中 ${stats.tag_count}/${stats.lora_count}`);
      await new Promise((resolve) => setTimeout(resolve, 3000));
    }
    INDEX_POLL.notify("");
  } finally {
    INDEX_POLL.running = false;
    INDEX_POLL.listeners.clear();
  }
}

/** 把命中的关键词包进 <mark>，全部用 DOM 节点构建，不拼 HTML */
function highlightInto(container, text, tokens) {
  while (container.firstChild) container.removeChild(container.firstChild);
  if (!text) return;
  if (!tokens || !tokens.length) {
    container.textContent = text;
    return;
  }
  const low = text.toLowerCase();
  const ranges = [];
  for (const token of tokens) {
    const needle = String(token || "").toLowerCase();
    if (!needle) continue;
    let from = 0;
    while (from <= low.length) {
      const at = low.indexOf(needle, from);
      if (at < 0) break;
      ranges.push([at, at + needle.length]);
      from = at + needle.length;
    }
  }
  if (!ranges.length) {
    container.textContent = text;
    return;
  }
  ranges.sort((a, b) => a[0] - b[0]);
  const merged = [];
  for (const range of ranges) {
    const last = merged[merged.length - 1];
    if (last && range[0] <= last[1]) last[1] = Math.max(last[1], range[1]);
    else merged.push([range[0], range[1]]);
  }
  let cursor = 0;
  for (const [start, end] of merged) {
    if (start > cursor) {
      container.appendChild(document.createTextNode(text.slice(cursor, start)));
    }
    const mark = document.createElement("mark");
    mark.className = "lsp-mark";
    mark.textContent = text.slice(start, end);
    container.appendChild(mark);
    cursor = end;
  }
  if (cursor < text.length) {
    container.appendChild(document.createTextNode(text.slice(cursor)));
  }
}

function fmtSize(bytes) {
  if (!bytes || bytes < 0) return "";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value < 10 && i > 0 ? value.toFixed(1) : Math.round(value)}${units[i]}`;
}

function stemOf(rel) {
  const base = String(rel || "").split(/[\\/]/).pop() || "";
  return base.replace(/\.[^.]+$/, "") || base;
}

function folderOf(rel) {
  const parts = String(rel || "").split("/");
  parts.pop();
  return parts.join("/");
}

function thumbUrlFor(rel, width = 256) {
  const size = width ? `&w=${width}` : "";
  return `${API_ROOT}/thumb?rel=${encodeURIComponent(rel)}${size}`;
}

async function requestJSON(path) {
  const response = await api.fetchApi(API_ROOT + path, { cache: "no-store" });
  let payload = null;
  try {
    payload = await response.json();
  } catch (err) {
    payload = null;
  }
  if (!response.ok) {
    const detail = payload && (payload.error || payload.message);
    const error = new Error(detail || `HTTP ${response.status}`);
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload || {};
}

async function postJSON(path, body) {
  const response = await api.fetchApi(API_ROOT + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch (err) {
    payload = null;
  }
  if (!response.ok) {
    const detail = payload && (payload.error || payload.message);
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return payload || {};
}

/* ======================================================================
   面板
   ====================================================================== */

class LoraStackPanel {
  constructor(node, nodeName) {
    this.node = node;
    this.nodeName = nodeName;
    this.modelOnly = nodeName === "LoraStackProModelOnly";

    this.items = [];
    this.currentPath = "";
    this.query = "";
    this.includeTags = false;   // 默认只搜名字：标签命中会让结果膨胀到几百条
    this.searchScope = "all";   // all / folder / lora，搜索范围
    this.view = { kind: "dir", data: null };
    this.busy = false;
    this.detail = null;
    this.detailCache = new Map();
    this.metaCache = new Map();
    this.aliases = {};          // rel -> 手动备注（只改显示，不动磁盘文件名）
    this.usage = { recent: [], favorites: [] };
    this.viewMode = "dir";      // dir | recent | fav
    this.settings = loadSettings();
    this.issues = { missing: new Set(), duplicates: {} };
    this.settingsOpen = false;
    this.presetsOpen = false;
    this.prefetchOpen = false;
    this.presets = [];
    this.collapsed = true;      // 默认折叠：平时就是一个普通的 LoRA 堆
    this._expandedHeight = 0;   // 记住展开时的高度

    this.dirSort = this.settings.dirSort || "name";
    this.dirFilter = this.settings.dirFilter || "";
    this.cursor = -1;           // 浏览区键盘游标
    this.stackCursor = -1;      // 堆面板键盘游标
    this._cardEls = [];         // 浏览区卡片元素，键盘游标只切 class 不重建
    this._rowEls = [];
    this._rowRefs = new Map();  // rel -> { row, sm, sc }，调权重时直接改数字
    this._tokens = [];          // 当前搜索的命中词，一次渲染只算一遍
    this.searchShown = 0;       // 搜索结果已渲染数量，0 表示用默认批量
    this.dirShown = 0;          // 目录浏览已渲染数量，0 表示用默认批量
    this._dirGrid = null;       // 目录 grid 容器，触底只往里 append 不重建
    this._dirMoreHint = null;   // 目录「已显示 N/M」提示条
    this._searchRendered = 0;   // 搜索结果已渲染条数（跨两组累计）
    this._searchNameGrid = null;
    this._searchTagGrid = null;
    this._searchHint = null;    // 搜索底部的「已显示 N/M」提示条
    this._searchLoading = false;
    this.collectionShown = 0;   // 收藏 / 最近视图已渲染数量
    this._collectionGrid = null;
    this._collectionList = null;
    this._collectionHint = null;
    this._pageRaf = 0;          // 触底检查的 rAF 句柄，合并同帧多次触发
    this._scrollBound = false;
    this.bases = { items: {}, families: {}, conflict: false, unknown: [] };
    this.prefetchState = null;
    this.prefetchPlan = null;
    this._prefetchTimer = null;

    this.searchDebounced = debounce((value) => this.runSearch(value), 240);
    this._status = { kind: "idle", text: "准备中…" };
    this._tagHint = "";
    this._indexHintHandler = (hint) => this.setTagHint(hint);
    this.build();
    this.initHoverPreview();
    this.initKeyboard();
    this.loadDir("");
    this.loadAliases();
    this.loadUsage();
    this.loadPresets();
    this.pollIndexState();
    this.listenExecution();
  }

  listenExecution() {
    try {
      api.addEventListener("execution_success", () => {
        clearTimeout(this._usageRefreshTimer);
        this._usageRefreshTimer = setTimeout(() => this.loadUsage(), 800);
      });
    } catch (err) {
      /* 事件不可用时忽略 */
    }
  }

  async loadUsage() {
    try {
      const data = await requestJSON("/usage");
      this.usage = {
        recent: Array.isArray(data.recent) ? data.recent : [],
        favorites: Array.isArray(data.favorites) ? data.favorites : [],
      };
      this.updateTabs();
      if (this.viewMode !== "dir") this.requestRender();
    } catch (err) {
      /* 使用记录读不到不影响主流程 */
    }
  }

  isFavorite(rel) {
    return this.usage.favorites.some((item) => (item.rel || item.path) === rel);
  }

  async toggleFavorite(rel) {
    const wasFav = this.isFavorite(rel);
    if (wasFav) {
      this.usage.favorites = this.usage.favorites.filter((item) => (item.rel || item.path) !== rel);
    } else {
      const known = this.usage.recent.find((item) => (item.rel || item.path) === rel);
      const entry = known || { rel, stem: stemOf(rel), folder: folderOf(rel), name: rel.split("/").pop() || rel, thumb: thumbUrlFor(rel) };
      this.usage.favorites = [entry, ...this.usage.favorites];
    }
    this.updateTabs();
    // 收藏只影响星标本身；收藏页的列表内容变了，交给 loadUsage 触发重绘
    if (this.viewMode === "fav") this.requestRender();
    else this.updateCardFavorite(rel);
    try {
      await postJSON("/favorite", { rel });
      await this.loadUsage();
      this.setStatus("ok", wasFav ? "已取消收藏" : "已加入收藏");
    } catch (err) {
      this.setStatus("err", `收藏操作失败: ${err.message}`);
      this.loadUsage();
    }
  }

  async loadAliases() {
    try {
      const data = await requestJSON("/aliases");
      if (data && data.aliases && typeof data.aliases === "object") {
        // 工作流里已有的备注优先，其次才用全局表
        this.aliases = { ...data.aliases, ...this.aliases };
        this.sync();
        this.requestRender();
      }
    } catch (err) {
      /* 别名表读不到不影响主流程 */
    }
  }

  aliasOf(rel) {
    return this.aliases[rel] || "";
  }

  /** 有备注就显示备注，没有就显示文件名 */
  labelOf(rel, fallback) {
    return this.aliasOf(rel) || fallback || stemOf(rel);
  }

  async commitAlias(rel, alias) {
    const value = String(alias || "").trim().slice(0, 60);
    if (this.aliasOf(rel) === value) {
      this.render();
      return;
    }
    if (value) this.aliases[rel] = value;
    else delete this.aliases[rel];
    this.sync();
    this.render();
    try {
      const response = await api.fetchApi(`${API_ROOT}/alias`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rel, alias: value }),
      });
      const payload = await response.json().catch(() => null);
      if (payload && payload.aliases) {
        this.aliases = payload.aliases;
        this.sync();
        this.render();
        this.setStatus("ok", value ? `备注已保存：${value}` : "备注已清除");
      }
    } catch (err) {
      this.setStatus("err", `备注保存失败: ${err.message}`);
    }
  }

  /** 在已选区就地改备注：回车保存、Esc 取消、清空则恢复原名 */
  startAliasEdit(nameEl, item) {
    if (!nameEl || nameEl.querySelector("input")) return;
    const current = this.aliasOf(item.rel);
    const stem = item.stem || stemOf(item.rel);

    const input = el("input", "lsp-alias-input");
    input.type = "text";
    input.value = current;
    input.placeholder = stem;
    input.maxLength = 60;
    input.title = "回车保存，Esc 取消，清空则恢复文件名";

    while (nameEl.firstChild) nameEl.removeChild(nameEl.firstChild);
    nameEl.appendChild(input);
    input.focus();
    input.select();

    let settled = false;
    const finish = (save) => {
      if (settled) return;
      settled = true;
      if (save) this.commitAlias(item.rel, input.value);
      else this.render();
    };

    input.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Enter") {
        e.preventDefault();
        finish(true);
      } else if (e.key === "Escape") {
        e.preventDefault();
        finish(false);
      }
    });
    input.addEventListener("blur", () => finish(true));
    for (const type of ["mousedown", "pointerdown", "click", "wheel"]) {
      input.addEventListener(type, (e) => e.stopPropagation());
    }
  }

  /* 后台标签索引是增量构建的，这里只做轻量轮询，不阻塞交互 */
  pollIndexState() {
    if (INDEX_POLL.done) {
      this.setTagHint("");
      return;
    }
    if (INDEX_POLL.hint) this.setTagHint(INDEX_POLL.hint);
    INDEX_POLL.subscribe(this._indexHintHandler);
    pollIndexShared();
  }

  /* ------------------------------------------------------------ 构建 DOM */
  build() {
    ensureStyle();
    const root = el("div", "lsp-root");
    root.style.position = "relative";
    this.root = root;

    // 搜索条
    const search = el("div", "lsp-search");
    const icon = svg(ICON.search, 13);
    icon.classList.add("lsp-search-icon");
    this.input = el("input");
    this.input.type = "text";
    this.input.placeholder = "搜索 LoRA / 文件夹名，空格分词";
    this.input.spellcheck = false;
    this.input.addEventListener("input", () => {
      const value = this.input.value;
      if (!value.trim()) {
        this.query = "";
        this.view = { kind: "dir", data: this.view.data };
        this.loadDir(this.currentPath);
        this.render();
        return;
      }
      this.searchDebounced(value);
    });
    this.input.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Escape") {
        // 浮层是模态的：哪怕光标还在搜索框里，Esc 也先关浮层
        if (this.detail || this.settingsOpen || this.presetsOpen || this.prefetchOpen) {
          this.handleEscape();
          return;
        }
        this.input.value = "";
        this.input.dispatchEvent(new Event("input"));
      }
    });

    const actions = el("div", "lsp-search-actions");
    this.scopeBtn = el("button", "lsp-search-scope", "全");
    this.scopeBtn.title = "搜索范围：全部 → 只看文件夹 → 只看 LoRA 文件";
    this.scopeBtn.addEventListener("click", (e) => {
      e.preventDefault();
      this.cycleSearchScope();
    });
    this.tagToggle = el("button", `lsp-search-tag${this.includeTags ? " on" : ""}`, "标签");
    this.tagToggle.title = "搜索时是否匹配训练标签（ss_tag_frequency）";
    this.tagToggle.addEventListener("click", (e) => {
      e.preventDefault();
      this.includeTags = !this.includeTags;
      this.tagToggle.classList.toggle("on", this.includeTags);
      if (this.query.trim()) this.runSearch(this.input.value);
    });
    const clear = el("button", "lsp-search-clear");
    clear.appendChild(svg(ICON.close, 10));
    clear.title = "清空搜索";
    clear.addEventListener("click", (e) => {
      e.preventDefault();
      this.input.value = "";
      this.input.dispatchEvent(new Event("input"));
      this.input.focus();
    });
    actions.append(this.scopeBtn, this.tagToggle, clear);
    search.append(icon, this.input, actions);

    // 面包屑
    const crumbs = el("div", "lsp-crumbs");
    this.crumbsEl = crumbs;

    const spacer = el("div", "lsp-crumbs-spacer");
    this.upBtn = el("button", "lsp-icon-btn");
    this.upBtn.appendChild(svg(ICON.back, 12));
    this.upBtn.title = "返回上级";
    this.upBtn.addEventListener("click", (e) => {
      e.preventDefault();
      const parent = this.currentPath.includes("/")
        ? this.currentPath.slice(0, this.currentPath.lastIndexOf("/"))
        : "";
      this.navigate(parent);
    });
    this.refreshBtn = el("button", "lsp-icon-btn");
    this.refreshBtn.appendChild(svg(ICON.refresh, 12));
    this.refreshBtn.title = "重建索引";
    this.refreshBtn.addEventListener("click", async (e) => {
      e.preventDefault();
      this.refreshBtn.classList.add("spin");
      try {
        await api.fetchApi(`${API_ROOT}/rescan`, { method: "POST" });
        // 索引重建后，之前的元数据缓存和校验结论都可能过时
        this.metaCache.clear();
        this._validatedKey = "";
        this._baseKey = "";
        await new Promise((r) => setTimeout(r, 600));
        await this.loadDir(this.currentPath);
      } catch (err) {
        this.setStatus("err", `重建索引失败: ${err.message}`);
      } finally {
        this.refreshBtn.classList.remove("spin");
      }
    });
    crumbs.append(spacer, this.upBtn, this.refreshBtn);

    // 排序 / 筛选：只影响目录视图，搜索和收藏视图各有自己的顺序
    const toolbar = el("div", "lsp-toolbar");
    this.sortButtons = {};
    const sortGroup = el("div", "lsp-btn-group");
    for (const [value, label] of SORT_OPTIONS) {
      const btn = el("button", "lsp-tiny-btn", label);
      btn.title = `按${label}排序`;
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        this.setDirSort(value);
      });
      this.sortButtons[value] = btn;
      sortGroup.appendChild(btn);
    }
    this.filterButtons = {};
    const filterGroup = el("div", "lsp-btn-group");
    for (const [value, label] of FILTER_OPTIONS) {
      const btn = el("button", "lsp-tiny-btn", label);
      btn.title = value === "nocover"
        ? "只看没有本地预览图的 LoRA"
        : (value === "dup" ? "只看存在同名文件的 LoRA" : "不筛选");
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        this.setDirFilter(value);
      });
      this.filterButtons[value] = btn;
      filterGroup.appendChild(btn);
    }
    toolbar.append(
      el("span", "lsp-toolbar-label", "排序"),
      sortGroup,
      el("span", "lsp-toolbar-label", "筛选"),
      filterGroup,
    );

    // 内容区
    this.body = el("div", "lsp-body");

    // 已选区（折叠态就是堆的全部内容）
    const selected = el("div", "lsp-selected");
    this.selBox = selected;
    this.selGuards = el("div", "lsp-guards");
    this.selList = el("div", "lsp-selected-list");
    selected.append(this.selGuards, this.selList);

    // 状态栏
    this.statusEl = el("div", "lsp-status");

    // 顶部条：折叠时只留一个浏览入口，展开时用来收起
    const topbar = el("div", "lsp-topbar");
    this.toggleBtn = el("button", "lsp-toggle-browse");
    this.toggleBtn.title = "展开/收起 LoRA 浏览面板";
    this.toggleBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      this.setCollapsed(!this.collapsed);
    });
    this.topbarLabel = el("span", "lsp-topbar-label", "已选 0");

    this.settingsBtn = el("button", "lsp-icon-btn");
    this.settingsBtn.appendChild(svg(ICON.gear, 12));
    this.settingsBtn.title = "设置";
    this.settingsBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      this.openSettings();
    });

    this.prefetchBtn = el("button", "lsp-icon-btn");
    this.prefetchBtn.appendChild(svg(ICON.sync, 12));
    this.prefetchBtn.title = "C 站补全：给缺封面 / 缺信息的 LoRA 批量补齐";
    this.prefetchBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      this.openPrefetch();
    });

    this.presetsBtn = el("button", "lsp-icon-btn");
    this.presetsBtn.appendChild(svg(ICON.bookmark, 12));
    this.presetsBtn.title = "堆预设";
    this.presetsBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      this.openPresets();
    });

    const clearBtn = el("button", "lsp-link-btn", "清空");
    clearBtn.title = "清空整个 LoRA 堆";
    clearBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (!this.items.length) return;
      this.items = [];
      this.sync();
      this.render();
    });
    topbar.append(
      this.toggleBtn,
      el("span", "lsp-spacer"),
      this.presetsBtn,
      this.prefetchBtn,
      this.settingsBtn,
      this.topbarLabel,
      clearBtn,
    );

    // 视图切换：目录 / 最近 / 收藏
    const tabs = el("div", "lsp-tabs");
    this.tabButtons = {};
    for (const [mode, label] of [["dir", "目录"], ["recent", "最近"], ["fav", "收藏"]]) {
      const btn = el("button", "lsp-tab", label);
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        this.setViewMode(mode);
      });
      this.tabButtons[mode] = btn;
      tabs.appendChild(btn);
    }

    // 浏览区：折叠时整块隐藏
    const browse = el("div", "lsp-browse");
    browse.append(search, tabs, crumbs, toolbar, this.body);

    root.append(topbar, browse, selected, this.statusEl);
    if (this.collapsed) root.classList.add("collapsed");
    this.updateTopbar();
    this.updateToolbar();

    // 详情浮层容器
    this.detailLayer = el("div");
    this.detailLayer.style.display = "none";
    root.appendChild(this.detailLayer);

    // 阻止画布交互
    for (const type of ["pointerdown", "mousedown", "mouseup", "wheel", "keydown", "contextmenu"]) {
      root.addEventListener(type, (e) => e.stopPropagation());
    }

    this.setStatus("idle", "准备中…");
  }

  /* ------------------------------------------------------------ 视图切换 */
  updateTabs() {
    if (!this.tabButtons) return;
    const counts = {
      dir: "",
      recent: this.usage.recent.length ? ` ${this.usage.recent.length}` : "",
      fav: this.usage.favorites.length ? ` ${this.usage.favorites.length}` : "",
    };
    const labels = { dir: "目录", recent: "最近", fav: "收藏" };
    for (const mode of ["dir", "recent", "fav"]) {
      const btn = this.tabButtons[mode];
      if (!btn) continue;
      btn.textContent = labels[mode] + counts[mode];
      btn.classList.toggle("active", this.viewMode === mode);
    }
  }

  setViewMode(mode) {
    if (this.viewMode === mode) return;
    this.viewMode = mode;
    this.collectionShown = 0;   // 换视图从第一批重新开始
    if (mode !== "dir") {
      this.input.value = "";
      this.query = "";
      this.view = { kind: "dir", data: this.view.data };
      this.loadUsage();
    }
    this.updateTabs();
    this.render();
    if (mode === "dir" && !this.view.data) this.loadDir(this.currentPath);
  }

  /* ------------------------------------------------------- 排序与筛选 */
  updateToolbar() {
    if (this.sortButtons) {
      for (const [value, btn] of Object.entries(this.sortButtons)) {
        btn.classList.toggle("on", this.dirSort === value);
      }
    }
    if (this.filterButtons) {
      for (const [value, btn] of Object.entries(this.filterButtons)) {
        btn.classList.toggle("on", this.dirFilter === value);
      }
    }
  }

  setDirSort(value) {
    if (this.dirSort === value) return;
    this.dirSort = value;
    this.settings.dirSort = value;
    saveSettings(this.settings);
    this.updateToolbar();
    if (this.viewMode === "dir" && !this.query) this.loadDir(this.currentPath, 0, true);
  }

  setDirFilter(value) {
    if (this.dirFilter === value) return;
    this.dirFilter = value;
    this.settings.dirFilter = value;
    saveSettings(this.settings);
    this.updateToolbar();
    if (this.viewMode === "dir" && !this.query) this.loadDir(this.currentPath, 0, true);
  }

  renderCollection(list, emptyText) {
    if (!list.length) {
      const empty = el("div", "lsp-empty");
      empty.appendChild(svg(this.viewMode === "fav" ? ICON.star : ICON.folder, 26));
      empty.appendChild(el("div", null, emptyText));
      this.body.appendChild(empty);
      return;
    }
    const grid = el("div", "lsp-grid");
    const total = list.length;
    const shown = Math.min(this.collectionShown || DIR_PAGE, total);
    for (let i = 0; i < shown; i += 1) grid.appendChild(this.card(list[i]));
    this.body.appendChild(grid);
    this._collectionGrid = grid;
    this._collectionList = list;
    if (shown < total) {
      this._collectionHint = this.moreHint(`已显示 ${shown}/${total} · 下滑继续加载`);
      this.body.appendChild(this._collectionHint);
    }
  }

  /* ------------------------------------------------------------ 权重微调 */
  weightStep() {
    const value = Number(this.settings.weightStep);
    if (!Number.isFinite(value) || value <= 0) return 0.05;
    return Math.min(1, Math.max(0.001, value));
  }

  nudgeWeights(item, direction) {
    const step = this.weightStep();
    const round = (value) => Math.round(value * 1000) / 1000;
    const clamp = (value) => Math.max(-10, Math.min(10, round(value)));
    item.sm = clamp((Number(item.sm) || 0) + direction * step);
    if (!this.modelOnly) {
      item.sc = clamp((Number(item.sc) || 0) + direction * step);
    }
    this.syncWeights();
    // 只改这一行的数字和顶部读数，不重建整行：连续点 ± 是最高频的操作
    const refs = this._rowRefs.get(item.rel);
    if (refs) {
      if (refs.sm) refs.sm.value = String(item.sm);
      if (refs.sc) refs.sc.value = String(item.sc);
      this.refreshReadout();
    } else {
      this.renderSelected();
    }
  }

  /** 设置是全局的，改完同步到画布上其它同款节点 */
  broadcastSettings() {
    const nodes = app.graph?._nodes || app.graph?.nodes || [];
    for (const node of nodes) {
      const panel = node?.__lspPanel;
      if (!panel || panel === this) continue;
      panel.settings = { ...this.settings };
      // 只同步共享状态，不触发重绘：画布上还有几个面板就重绘几次纯属浪费，
      // 各自下一次渲染自然会用上新设置
      panel.dirSort = this.settings.dirSort || "name";
      panel.dirFilter = this.settings.dirFilter || "";
      panel.updateToolbar();
    }
  }

  /* ------------------------------------------------------------ 整堆预取 */
  async prefetchAll() {
    if (this._prefetching) {
      this._prefetching = false;
      this.setStatus("warn", "已停止预取");
      return;
    }
    const rels = this.items.map((item) => item.rel);
    if (!rels.length) {
      this.setStatus("warn", "堆里没有 LoRA");
      return;
    }
    this._prefetching = true;
    this.prefetchBtn.classList.add("spin");
    let fetched = 0;
    let cached = 0;
    let failed = 0;
    try {
      for (let index = 0; index < rels.length; index += 1) {
        if (!this._prefetching) break;
        this.setStatus("idle", `预取 C 站信息 ${index + 1}/${rels.length}…`);
        try {
          const data = await requestJSON(`/civitai?rel=${encodeURIComponent(rels[index])}`);
          if (data && data.ok) {
            if (data.cached) cached += 1;
            else fetched += 1;
            this.metaCache.set(`civitai:${rels[index]}`, data);
          } else {
            failed += 1;
          }
        } catch (err) {
          failed += 1;
        }
      }
    } finally {
      this._prefetching = false;
      this.prefetchBtn.classList.remove("spin");
    }
    const parts = [];
    if (fetched) parts.push(`新获取 ${fetched}`);
    if (cached) parts.push(`已缓存 ${cached}`);
    if (failed) parts.push(`无数据或失败 ${failed}`);
    if (!parts.length) parts.push("没有可预取的项");
    this.setStatus(failed ? "warn" : "ok", `预取完成：${parts.join(" · ")}`);
  }

  /* ------------------------------------------------------------ 搜索高亮 */
  highlightTokens() {
    if (this.view.kind !== "search" || !this.view.data) return [];
    const set = new Set();
    for (const token of this.view.data.tokens || []) set.add(token);
    return [...set]
      .filter((token) => token && String(token).length)
      .map(String)
      .sort((a, b) => b.length - a.length);
  }

  /* ------------------------------------------------------------ 封面管理 */
  buildCoverPlaceholder(detail) {
    const box = el("button", "lsp-cover-empty");
    box.type = "button";
    box.appendChild(svg(ICON.image, 22));
    box.appendChild(el("div", "lsp-cover-empty-text", "没有预览图"));
    box.appendChild(el("div", "lsp-cover-empty-hint", "点击从 C 站获取预览图"));
    box.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      this.openCoverPicker(detail);
    });
    return box;
  }

  /** 展开 C 站候选图，最多 8 张 */
  buildCivitaiPicker(detail) {
    const wrap = el("div", "lsp-picker");

    const head = el("div", "lsp-tag-head");
    head.appendChild(el("span", null, "从 C 站选一张当封面"));
    const cancel = el("button", "lsp-link-btn", "取消");
    cancel.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      this.closeCoverPicker(detail);
    });
    head.appendChild(cancel);
    wrap.appendChild(head);

    if (detail.coverLoading) {
      const line = el("div", "lsp-note");
      line.append(el("span", "lsp-spinner"), document.createTextNode(" 正在从 C 站查询…"));
      wrap.appendChild(line);
      return wrap;
    }

    const civitai = detail.civitai;
    const images = civitai && civitai.ok && Array.isArray(civitai.images) ? civitai.images : [];
    if (!images.length) {
      wrap.appendChild(el("div", "lsp-note",
        civitai && civitai.error ? civitai.error : "C 站上没有这个 LoRA 的可用图片"));
      return wrap;
    }

    const grid = el("div", "lsp-cover-grid");
    for (const image of images.slice(0, 8)) {
      const busy = detail.coverDownloading === image.url;
      const cell = el("div", `lsp-cover-cell lsp-cover-remote${busy ? " busy" : ""}`);
      const thumb = el("img");
      thumb.src = `${API_ROOT}/proxy?url=${encodeURIComponent(image.url)}`;
      thumb.loading = "lazy";
      cell.appendChild(thumb);
      cell.title = busy ? "正在下载…" : "点击下载并设为封面";
      cell.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        this.pickCivitaiCover(detail, image.url);
      });
      grid.appendChild(cell);
    }
    wrap.appendChild(grid);
    wrap.appendChild(el("div", "lsp-note", `共 ${Math.min(images.length, 8)} 张，点选任意一张下载并设为封面`));
    return wrap;
  }

  async openCoverPicker(detail) {
    if (!detail) return;
    detail.coverPicker = true;
    this.renderDetail();
    const civitai = detail.civitai;
    const ready = civitai && civitai.ok && Array.isArray(civitai.images) && civitai.images.length;
    if (ready || detail.coverLoading) return;
    detail.coverLoading = true;
    this.renderDetail();
    try {
      await this.loadCivitai(detail.rel);
    } finally {
      detail.coverLoading = false;
      this.renderDetail();
    }
  }

  closeCoverPicker(detail) {
    if (!detail) return;
    detail.coverPicker = false;
    detail.coverDownloading = "";
    this.renderDetail();
  }

  async pickCivitaiCover(detail, url) {
    if (!detail || detail.coverDownloading) return;
    // 已经有封面时先确认：这一步是替换，不是首次获取
    if (detail.info && detail.info.thumb) {
      const sure = window.confirm(
        "用这张 C 站图替换当前封面？\n\n原封面图不会被删除，只是不再作为封面显示。",
      );
      if (!sure) return;
    }
    detail.coverDownloading = url;
    this.renderDetail();
    try {
      await this.fetchCover(detail.rel, url);
      detail.coverPicker = false;
    } finally {
      detail.coverDownloading = "";
      this.renderDetail();
    }
  }

  refreshThumbOf(rel) {
    const url = `${thumbUrlFor(rel)}&v=${Date.now()}`;
    for (const item of this.items) {
      if (item.rel === rel) item.thumb = url;
    }
    this.sync();
    this.render();
  }

  async setCover(rel, file) {
    try {
      const data = await postJSON("/cover", { rel, file });
      if (!data || !data.ok) {
        this.setStatus("err", (data && data.error) || "设置封面失败");
        return;
      }
      this.setStatus("ok", data.cover ? `封面已设为 ${data.cover}` : "封面已恢复自动识别");
      this.metaCache.delete(`info:${rel}`);
      this.refreshThumbOf(rel);
      if (this.detail && this.detail.rel === rel) {
        this.detail.thumbV = (this.detail.thumbV || 0) + 1;
        await this.loadLocalInfo(rel);
      }
    } catch (err) {
      this.setStatus("err", `设置封面失败: ${err.message}`);
    }
  }

  async fetchCover(rel, url) {
    this.setStatus("idle", "正在下载封面图…");
    try {
      const data = await postJSON("/cover/fetch", { rel, url });
      if (!data || !data.ok) {
        this.setStatus("err", (data && data.error) || "下载失败");
        return;
      }
      this.setStatus("ok", `已下载并设为封面：${data.file}`);
      this.metaCache.delete(`info:${rel}`);
      this.refreshThumbOf(rel);
      if (this.detail && this.detail.rel === rel) {
        this.detail.thumbV = (this.detail.thumbV || 0) + 1;
        await this.loadLocalInfo(rel);
      }
    } catch (err) {
      this.setStatus("err", `下载封面失败: ${err.message}`);
    }
  }

  /* ------------------------------------------------------------ 失效 / 重名 */
  scheduleValidate() {
    clearTimeout(this._validateTimer);
    this._validateTimer = setTimeout(() => this.validateStack(), 500);
  }

  async validateStack() {
    const rels = this.items.map((item) => item.rel);
    if (!rels.length) {
      this.issues = { missing: new Set(), duplicates: {} };
      this._validatedKey = "";
      this.applyIssuesToRows();
      this.renderStackGuards();
      return;
    }
    // 权重或顺序变化不影响「文件是否失效」「有没有同名」，同一组文件不必重复请求
    const key = rels.join("|");
    if (key === this._validatedKey) return;
    this._validatedKey = key;
    try {
      const data = await postJSON("/validate", { rels });
      this.issues = {
        missing: new Set(Array.isArray(data.missing) ? data.missing : []),
        duplicates: data.duplicates && typeof data.duplicates === "object" ? data.duplicates : {},
      };
      this.applyIssuesToRows();
      this.renderStackGuards();
    } catch (err) {
      this._validatedKey = "";      // 失败允许下次重试
    }
  }

  /* ------------------------------------------------------------ 键盘操作 */
  initKeyboard() {
    this._keyHandler = (e) => this.handleKey(e);
    document.addEventListener("keydown", this._keyHandler, false);
    const onRemoved = this.node.onRemoved;
    this.node.onRemoved = (...args) => {
      document.removeEventListener("keydown", this._keyHandler, false);
      if (this._scrollHandler && this.body) {
        this.body.removeEventListener("scroll", this._scrollHandler);
        this._scrollHandler = null;
      }
      if (this._pageRaf) {
        cancelAnimationFrame(this._pageRaf);
        this._pageRaf = 0;
      }
      // 悬停预览盒子挂在 document.body 上，不会随节点 DOM 一起消失，
      // 不显式移除的话每删一个节点就留一个含 <img> 的孤儿
      if (this.hoverTimer) {
        clearTimeout(this.hoverTimer);
        this.hoverTimer = null;
      }
      if (this.hoverBox) {
        this.hoverBox.remove();
        this.hoverBox = null;
      }
      this.stopPrefetchPolling();
      onRemoved?.apply(this.node, args);
    };
  }

  /** 只有正在操作这个节点时才吃键，避免干扰画布上的其它节点 */
  isActiveTarget() {
    const selected = app.canvas?.selected_nodes;
    if (!selected) return true;      // 老版本没有这个字段，退化为全局生效
    return !!selected[this.node.id];
  }

  handleKey(e) {
    if (!this.isActiveTarget()) return;
    if (e.ctrlKey || e.metaKey) return;    // Ctrl 组合留给 ComfyUI
    const target = e.target;
    const inField = !!target && (
      target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable === true
    );

    if (e.key === "Escape") {
      if (inField) return;                 // 输入框自己的 Esc 由它们处理
      if (this.handleEscape()) {
        e.preventDefault();
        e.stopPropagation();
      }
      return;
    }
    if (inField) return;

    if (e.key === "/") {
      this.focusSearch();
      e.preventDefault();
      return;
    }

    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      const step = e.key === "ArrowDown" ? 1 : -1;
      if (e.shiftKey) {
        if (this.moveStackCursor(step)) {
          e.preventDefault();
          e.stopPropagation();
        }
        return;
      }
      if (this.moveCursor(step)) {
        e.preventDefault();
        e.stopPropagation();
      }
      return;
    }

    if (e.key === "Enter" && !e.shiftKey && !e.altKey) {
      if (this.activateCursor()) {
        e.preventDefault();
        e.stopPropagation();
      }
      return;
    }

    // 用 x 而不是 Space：Space 在画布上是平移手势，抢过来会干扰拖动画布
    if (e.key === "x" || e.key === "X") {
      if (this.toggleStackEnabled()) {
        e.preventDefault();
        e.stopPropagation();
      }
      return;
    }

    if (e.key === "-" || e.key === "+" || e.key === "=") {
      const direction = e.key === "-" ? -1 : 1;
      if (this.nudgeStackWeights(direction)) {
        e.preventDefault();
        e.stopPropagation();
      }
    }
  }

  handleEscape() {
    if (this.prefetchOpen) { this.closePrefetch(); return true; }
    if (this.presetsOpen) { this.closePresets(); return true; }
    if (this.settingsOpen) { this.closeSettings(); return true; }
    if (this.detail) { this.closeDetail(); return true; }
    if (this.input && this.input.value) {
      this.input.value = "";
      this.input.dispatchEvent(new Event("input"));
      return true;
    }
    if (this.view.kind === "search") { this.navigate(this.currentPath); return true; }
    if (!this.collapsed) { this.setCollapsed(true); return true; }
    return false;
  }

  focusSearch() {
    if (this.collapsed) this.setCollapsed(false);
    if (this.viewMode !== "dir") this.setViewMode("dir");
    this.input?.focus();
    this.input?.select();
  }

  moveCursor(delta) {
    if (!this._cardEls.length) return false;
    const last = this._cardEls.length - 1;
    const next = this.cursor < 0
      ? (delta > 0 ? 0 : last)
      : Math.max(0, Math.min(last, this.cursor + delta));
    this.cursor = next;
    this.paintCursor();
    this._cardEls[next]?.scrollIntoView({ block: "nearest" });
    const rel = this._cardEls[next]?.dataset.rel || "";
    const label = rel.split("/").pop() || rel;
    this.setStatus("idle", label ? `游标：${label}` : "");
    return true;
  }

  moveStackCursor(delta) {
    if (!this._rowEls.length) return false;
    const last = this._rowEls.length - 1;
    const next = this.stackCursor < 0
      ? (delta > 0 ? 0 : last)
      : Math.max(0, Math.min(last, this.stackCursor + delta));
    this.stackCursor = next;
    this.paintCursor();
    this._rowEls[next]?.scrollIntoView({ block: "nearest" });
    return true;
  }

  activateCursor() {
    if (this.cursor < 0 || this.cursor >= this._cardEls.length) return false;
    const rel = this._cardEls[this.cursor].dataset.rel;
    if (!rel) return false;
    this.toggleItem({ rel });
    return true;
  }

  toggleStackEnabled() {
    if (this.stackCursor < 0 || this.stackCursor >= this.items.length) return false;
    const item = this.items[this.stackCursor];
    if (!item) return false;
    item.enabled = !item.enabled;
    this.syncWeights();
    const refs = this._rowRefs.get(item.rel);
    if (refs) {
      refs.row.classList.toggle("off", !item.enabled);
      const toggle = refs.row.querySelector(".lsp-toggle");
      if (toggle) {
        toggle.classList.toggle("on", item.enabled);
        toggle.title = item.enabled ? "点击禁用" : "点击启用";
      }
      this.refreshReadout();
    } else {
      this.renderSelected();
    }
    this.setStatus("ok", `${item.enabled ? "已启用" : "已禁用"}：${this.labelOf(item.rel)}`);
    return true;
  }

  nudgeStackWeights(direction) {
    if (!this.items.length) return false;
    if (this.stackCursor < 0 || this.stackCursor >= this.items.length) this.stackCursor = 0;
    const item = this.items[this.stackCursor];
    if (!item) return false;
    this.nudgeWeights(item, direction);
    this.paintCursor();
    this._rowEls[this.stackCursor]?.scrollIntoView({ block: "nearest" });
    return true;
  }

  /* ------------------------------------------------------------ 悬停放大预览 */
  initHoverPreview() {
    const box = el("div", "lsp-hover-preview");
    box.style.display = "none";
    document.body.appendChild(box);
    this.hoverBox = box;
    this.hoverTimer = null;
    this._hoverRel = "";

    // 事件委托：卡片是成批重建的，逐张绑三个监听器等于每次渲染都造几百个闭包
    this.body.addEventListener("mouseover", (e) => {
      if (!this.settings.hoverPreview) return;
      const img = e.target;
      if (!img || img.tagName !== "IMG") return;
      const rel = img.closest(".lsp-card")?.dataset?.rel;
      if (!rel || rel === this._hoverRel) return;
      this._hoverRel = rel;
      clearTimeout(this.hoverTimer);
      this.hoverTimer = setTimeout(() => this.showHoverPreview(rel), 260);
    });
    this.body.addEventListener("mousemove", (e) => this.positionHoverPreview(e));
    this.body.addEventListener("mouseout", (e) => {
      if (!e.target || e.target.tagName !== "IMG") return;
      this._hoverRel = "";
      this.hideHoverPreview();
    });
  }

  showHoverPreview(rel) {
    if (!this.hoverBox || !this.settings.hoverPreview) return;
    const size = Math.max(180, Math.min(520, Number(this.settings.hoverSize) || 320));
    this.hoverBox.style.width = `${size}px`;
    this.hoverBox.style.height = `${size}px`;
    while (this.hoverBox.firstChild) this.hoverBox.removeChild(this.hoverBox.firstChild);
    const img = el("img");
    img.src = thumbUrlFor(rel, Math.min(1024, Math.round(size * 1.6)));
    img.addEventListener("error", () => this.hideHoverPreview());
    this.hoverBox.appendChild(img);
    this.hoverBox.style.display = "block";
  }

  positionHoverPreview(event) {
    if (!this.hoverBox || this.hoverBox.style.display === "none") return;
    const box = this.hoverBox;
    const width = box.offsetWidth || 320;
    const height = box.offsetHeight || 320;
    const gap = 16;
    let left = event.clientX + gap;
    let top = event.clientY + gap;
    if (left + width > window.innerWidth - 8) left = event.clientX - width - gap;
    if (top + height > window.innerHeight - 8) top = window.innerHeight - height - 8;
    box.style.left = `${Math.max(8, left)}px`;
    box.style.top = `${Math.max(8, top)}px`;
  }

  hideHoverPreview() {
    clearTimeout(this.hoverTimer);
    if (this.hoverBox) this.hoverBox.style.display = "none";
  }

  /* ------------------------------------------------------------ 堆预设 */
  async loadPresets() {
    try {
      const data = await requestJSON("/presets");
      this.presets = Array.isArray(data.presets) ? data.presets : [];
      if (this.presetsOpen) this.renderPresets();
    } catch (err) {
      /* 预设读不到不影响主流程 */
    }
  }

  openPresets() {
    this.detail = null;
    this.settingsOpen = false;
    this.prefetchOpen = false;
    this.presetsOpen = true;
    this.loadPresets();
    this.renderPresets();
  }

  closePresets() {
    this.presetsOpen = false;
    this.detailLayer.style.display = "none";
    while (this.detailLayer.firstChild) this.detailLayer.removeChild(this.detailLayer.firstChild);
  }

  async persistPreset(name, id) {
    const items = this.items.map((item) => ({
      name: item.rel,
      alias: this.aliasOf(item.rel),
      sm: Number(item.sm),
      sc: Number(item.sc),
      enabled: !!item.enabled,
    }));
    try {
      const payload = await postJSON("/presets/save", { id: id || "", name, items });
      if (payload && Array.isArray(payload.presets)) this.presets = payload.presets;
      this.renderPresets();
      this.setStatus("ok", id ? `已覆盖预设「${name}」` : `已保存预设「${name}」`);
    } catch (err) {
      this.setStatus("err", `预设保存失败: ${err.message}`);
    }
  }

  async removePreset(id, name) {
    try {
      const payload = await postJSON("/presets/delete", { id });
      this.presets = payload && Array.isArray(payload.presets) ? payload.presets : [];
      this.renderPresets();
      this.setStatus("ok", `已删除预设「${name}」`);
    } catch (err) {
      this.setStatus("err", `删除失败: ${err.message}`);
    }
  }

  applyPreset(preset) {
    const entries = Array.isArray(preset.items) ? preset.items : [];
    if (!entries.length) {
      this.setStatus("warn", "这个预设是空的");
      return;
    }
    const items = [];
    for (const entry of entries) {
      const rel = String(entry.name || "").replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
      if (!rel) continue;
      if (entry.alias) this.aliases[rel] = entry.alias;
      const sm = Number(entry.sm);
      const sc = Number(entry.sc);
      items.push({
        rel,
        stem: stemOf(rel),
        folder: folderOf(rel),
        name: rel.split("/").pop() || rel,
        thumb: thumbUrlFor(rel),
        sm: Number.isFinite(sm) ? sm : 1,
        sc: Number.isFinite(sc) ? sc : 1,
        enabled: entry.enabled !== false,
      });
    }
    this.items = items;
    this.sync();
    this.render();
    this.setStatus("ok", `已套用预设「${preset.name}」（${items.length} 项）`);
  }

  renderPresets() {
    const layer = this.detailLayer;
    while (layer.firstChild) layer.removeChild(layer.firstChild);
    if (!this.presetsOpen) {
      layer.style.display = "none";
      return;
    }
    layer.style.display = "block";

    const box = el("div", "lsp-detail");

    const head = el("div", "lsp-detail-head");
    head.appendChild(el("div", "lsp-detail-title", `堆预设 · ${this.presets.length}`));
    const closeBtn = el("button", "lsp-icon-btn");
    closeBtn.appendChild(svg(ICON.close, 12));
    closeBtn.title = "关闭";
    closeBtn.addEventListener("click", (e) => {
      e.preventDefault();
      this.closePresets();
    });
    head.appendChild(closeBtn);
    box.appendChild(head);

    const body = el("div", "lsp-detail-body");

    const createRow = el("div", "lsp-preset-create");
    const input = el("input", "lsp-preset-input");
    input.type = "text";
    input.placeholder = "给当前这套组合起个名字";
    input.maxLength = 60;
    const doCreate = () => {
      const name = input.value.trim();
      if (!name) {
        this.setStatus("warn", "先给预设起个名字");
        input.focus();
        return;
      }
      if (!this.items.length) {
        this.setStatus("warn", "堆是空的，没有可保存的内容");
        return;
      }
      input.value = "";
      this.persistPreset(name, "");
    };
    input.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Enter") {
        e.preventDefault();
        doCreate();
      }
    });
    const createBtn = el("button", "lsp-btn primary", "保存当前堆");
    createBtn.addEventListener("click", (e) => {
      e.preventDefault();
      doCreate();
    });
    createRow.append(input, createBtn);
    body.appendChild(createRow);

    body.appendChild(el("div", "lsp-note", "「套用」会整体替换当前堆；「覆盖」用当前堆更新这条预设。名称相同会覆盖已有预设。"));

    if (!this.presets.length) {
      const empty = el("div", "lsp-empty");
      empty.appendChild(svg(ICON.bookmark, 24));
      empty.appendChild(el("div", null, "还没有预设。先把堆调好，在上面起个名字保存。"));
      body.appendChild(empty);
    } else {
      for (const preset of this.presets) {
        const row = el("div", "lsp-preset-item");
        const info = el("div", "lsp-preset-info");
        info.appendChild(el("div", "lsp-preset-name", preset.name));
        const count = (preset.items || []).length;
        info.appendChild(el("div", "lsp-preset-desc", `${count} 个 LoRA`));
        row.appendChild(info);

        const actions = el("div", "lsp-preset-actions");
        const applyBtn = el("button", "lsp-btn small", "套用");
        applyBtn.title = "用这个预设替换当前堆";
        applyBtn.addEventListener("click", (e) => {
          e.preventDefault();
          this.applyPreset(preset);
        });
        const overwriteBtn = el("button", "lsp-btn small", "覆盖");
        overwriteBtn.title = "用当前堆更新这条预设";
        overwriteBtn.addEventListener("click", (e) => {
          e.preventDefault();
          if (!this.items.length) {
            this.setStatus("warn", "堆是空的，没有可覆盖的内容");
            return;
          }
          if (!window.confirm(`用当前堆覆盖预设「${preset.name}」？`)) return;
          this.persistPreset(preset.name, preset.id);
        });
        const deleteBtn = el("button", "lsp-btn small danger", "删除");
        deleteBtn.title = "删除这条预设";
        deleteBtn.addEventListener("click", (e) => {
          e.preventDefault();
          if (!window.confirm(`删除预设「${preset.name}」？`)) return;
          this.removePreset(preset.id, preset.name);
        });
        actions.append(applyBtn, overwriteBtn, deleteBtn);
        row.appendChild(actions);
        body.appendChild(row);
      }
    }

    box.appendChild(body);
    layer.appendChild(box);
  }

  /* ------------------------------------------------------------ 设置面板 */
  openSettings() {
    this.detail = null;
    this.presetsOpen = false;
    this.prefetchOpen = false;
    this.settingsOpen = true;
    this.renderSettings();
  }

  closeSettings() {
    this.settingsOpen = false;
    this.detailLayer.style.display = "none";
    while (this.detailLayer.firstChild) this.detailLayer.removeChild(this.detailLayer.firstChild);
  }

  async loadThumbStats(node) {
    if (!node) return;
    try {
      const data = await requestJSON("/thumbs/stats");
      node.textContent = `${data.files || 0} 个文件 · ${fmtSize(data.bytes || 0) || "0B"}`
        + `（上限 ${fmtSize(data.limit || 0)}）`;
    } catch (err) {
      node.textContent = "统计失败";
    }
  }

  renderSettings() {
    const layer = this.detailLayer;
    while (layer.firstChild) layer.removeChild(layer.firstChild);
    if (!this.settingsOpen) {
      layer.style.display = "none";
      return;
    }
    layer.style.display = "block";

    const box = el("div", "lsp-detail");

    const head = el("div", "lsp-detail-head");
    head.appendChild(el("div", "lsp-detail-title", "设置"));
    const closeBtn = el("button", "lsp-icon-btn");
    closeBtn.appendChild(svg(ICON.close, 12));
    closeBtn.title = "关闭";
    closeBtn.addEventListener("click", (e) => {
      e.preventDefault();
      this.closeSettings();
    });
    head.appendChild(closeBtn);
    box.appendChild(head);

    const body = el("div", "lsp-detail-body");

    const setRow = (label, desc, checked, onChange) => {
      const row = el("div", "lsp-set-row");
      const text = el("div", "lsp-set-text");
      text.appendChild(el("div", "lsp-set-label", label));
      if (desc) text.appendChild(el("div", "lsp-set-desc", desc));
      const toggle = el("button", `lsp-switch${checked ? " on" : ""}`);
      toggle.appendChild(svg(ICON.check, 9));
      toggle.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        onChange(!checked);
      });
      row.append(text, toggle);
      return row;
    };

    body.appendChild(setRow(
      "悬停放大预览图",
      "鼠标停在卡片上 0.26 秒后弹出大图",
      !!this.settings.hoverPreview,
      (next) => {
        this.settings.hoverPreview = next;
        saveSettings(this.settings);
        if (!next) this.hideHoverPreview();
        this.broadcastSettings();
        this.renderSettings();
      },
    ));

    const sizeRow = el("div", "lsp-set-row");
    const sizeText = el("div", "lsp-set-text");
    sizeText.appendChild(el("div", "lsp-set-label", "放大尺寸"));
    sizeText.appendChild(el("div", "lsp-set-desc", "悬停预览的边长"));
    const chips = el("div", "lsp-set-chips");
    for (const [value, label] of [[240, "小"], [320, "中"], [420, "大"]]) {
      const chip = el("button", `lsp-chip${Number(this.settings.hoverSize) === value ? " on" : ""}`, label);
      chip.addEventListener("click", (e) => {
        e.preventDefault();
        this.settings.hoverSize = value;
        saveSettings(this.settings);
        this.broadcastSettings();
        this.renderSettings();
      });
      chips.appendChild(chip);
    }
    sizeRow.append(sizeText, chips);
    body.appendChild(sizeRow);

    const stepRow = el("div", "lsp-set-row");
    const stepText = el("div", "lsp-set-text");
    stepText.appendChild(el("div", "lsp-set-label", "权重步进"));
    stepText.appendChild(el("div", "lsp-set-desc", "点 ± 按钮时每次调整的量，M 与 C 同步"));
    const stepChips = el("div", "lsp-set-chips");
    for (const value of [0.01, 0.05, 0.1, 0.2]) {
      const chip = el("button", `lsp-chip${this.weightStep() === value ? " on" : ""}`, String(value));
      chip.addEventListener("click", (e) => {
        e.preventDefault();
        this.settings.weightStep = value;
        saveSettings(this.settings);
        this.broadcastSettings();
        this.renderSettings();
        this.renderSelected();
      });
      stepChips.appendChild(chip);
    }
    stepRow.append(stepText, stepChips);
    body.appendChild(stepRow);

    body.appendChild(setRow(
      "目录底部重复列出整个堆",
      "关掉更省渲染：堆就在面板下方，浏览区的 ✓ 角标已经标出哪些已选",
      !!this.settings.showStackInDir,
      (next) => {
        this.settings.showStackInDir = next;
        saveSettings(this.settings);
        this.broadcastSettings();
        this.renderSettings();
        this.renderBody();
      },
    ));

    const staleRow = el("div", "lsp-set-row");
    const staleText = el("div", "lsp-set-text");
    staleText.appendChild(el("div", "lsp-set-label", "清理失效封面"));
    staleText.appendChild(el("div", "lsp-set-desc", "covers.json 里指向已删除图片的记录，清掉后回归自动识别"));
    const staleBtn = el("button", "lsp-btn small", "清理");
    staleBtn.addEventListener("click", async (e) => {
      e.preventDefault();
      e.stopPropagation();
      try {
        const data = await postJSON("/covers/prune", {});
        const count = Number(data.count) || 0;
        this.setStatus(count ? "ok" : "idle", count ? `已清理 ${count} 条失效封面` : "没有失效的封面记录");
        this.metaCache.clear();
        this.loadDir(this.currentPath, 0, true);
        this.renderSettings();
      } catch (err) {
        this.setStatus("err", `清理失败: ${err.message}`);
      }
    });
    staleRow.append(staleText, staleBtn);
    body.appendChild(staleRow);

    // 缩略图缓存：原图动辄几 MB，缓存后卡片只取几十 KB
    const thumbRow = el("div", "lsp-set-row");
    const thumbText = el("div", "lsp-set-text");
    thumbText.appendChild(el("div", "lsp-set-label", "缩略图缓存"));
    const thumbDesc = el("div", "lsp-set-desc", "正在统计…");
    thumbText.appendChild(thumbDesc);
    const thumbBtn = el("button", "lsp-btn small", "清空");
    thumbBtn.title = "删掉已生成的缩略图，下次浏览会重新生成";
    thumbBtn.addEventListener("click", async (e) => {
      e.preventDefault();
      e.stopPropagation();
      try {
        const data = await postJSON("/thumbs/clear", {});
        this.setStatus("ok", `已清空 ${data.removed || 0} 个缩略图`);
        this.metaCache.clear();
        await this.loadThumbStats(thumbDesc);
      } catch (err) {
        this.setStatus("err", `清空失败: ${err.message}`);
      }
    });
    thumbRow.append(thumbText, thumbBtn);
    body.appendChild(thumbRow);
    this.loadThumbStats(thumbDesc);

    body.appendChild(el("div", "lsp-note", "设置保存在浏览器本地，对这台机器上所有 LoRA Stack Pro 节点生效。"));
    body.appendChild(el("div", "lsp-note", "注意：± 按钮只改堆里的权重，磁盘上的 LoRA 文件不会被改动。"));

    // 键盘说明：这些键只在画布上选中本节点时生效
    body.appendChild(el("div", "lsp-desc-title", "键盘操作（选中节点后生效）"));
    const keys = [
      ["/", "展开面板并聚焦搜索框"],
      ["↑ / ↓", "在浏览区卡片之间移动游标"],
      ["Enter", "把游标指向的 LoRA 加入堆 / 从堆中移出"],
      ["Shift + ↑ / ↓", "在堆里的各行之间移动游标"],
      ["- / =", "调低 / 调高堆游标那一项的权重"],
      ["x", "启用 / 禁用堆游标那一项"],
      ["Esc", "按层关闭：浮层 → 搜索 → 折叠面板"],
    ];
    const keyList = el("div", "lsp-keys");
    for (const [combo, desc] of keys) {
      const row = el("div", "lsp-key-row");
      row.appendChild(el("kbd", "lsp-kbd", combo));
      row.appendChild(el("span", null, desc));
      keyList.appendChild(row);
    }
    body.appendChild(keyList);

    box.appendChild(body);
    layer.appendChild(box);
  }

  /* ------------------------------------------------------ C 站批量补全 */
  openPrefetch() {
    this.detail = null;
    this.presetsOpen = false;
    this.settingsOpen = false;
    this.prefetchOpen = true;
    this.prefetchOptions = this.prefetchOptions || {
      only_missing_cover: true,
      skip_cached: true,
      download_cover: true,
      limit: 200,
      delay_ms: 1200,
    };
    this.loadPrefetchPlan();
    this.loadPrefetchStatus();
    this.renderPrefetch();
  }

  closePrefetch() {
    this.prefetchOpen = false;
    this.stopPrefetchPolling();
    this.detailLayer.style.display = "none";
    while (this.detailLayer.firstChild) this.detailLayer.removeChild(this.detailLayer.firstChild);
  }

  async loadPrefetchPlan() {
    try {
      this.prefetchPlan = await requestJSON("/prefetch/plan");
    } catch (err) {
      this.prefetchPlan = null;
    }
    if (this.prefetchOpen) this.renderPrefetch();
  }

  async loadPrefetchStatus() {
    try {
      this.prefetchState = await requestJSON("/prefetch/status");
    } catch (err) {
      this.prefetchState = null;
    }
    if (this.prefetchOpen) this.renderPrefetch();
    if (this.prefetchState && this.prefetchState.running) this.startPrefetchPolling();
  }

  startPrefetchPolling() {
    if (this._prefetchTimer) return;
    this._prefetchTimer = setInterval(async () => {
      let data = null;
      try {
        data = await requestJSON("/prefetch/status");
      } catch (err) {
        return;
      }
      this.prefetchState = data;
      if (this.prefetchOpen) this.renderPrefetch();
      if (!data.running) {
        this.stopPrefetchPolling();
        this.loadPrefetchPlan();
        this.loadDir(this.currentPath, 0, true);
      }
    }, 1500);
  }

  stopPrefetchPolling() {
    if (this._prefetchTimer) {
      clearInterval(this._prefetchTimer);
      this._prefetchTimer = null;
    }
  }

  async startPrefetch(options) {
    try {
      const data = await postJSON("/prefetch/start", options);
      this.prefetchState = data.status || null;
      this.setStatus("ok", "已启动批量补全，可以继续做别的事");
      this.startPrefetchPolling();
    } catch (err) {
      this.setStatus("err", `启动失败: ${err.message}`);
    }
    this.renderPrefetch();
  }

  async stopPrefetch() {
    try {
      const data = await postJSON("/prefetch/stop", {});
      this.prefetchState = data.status || this.prefetchState;
      this.setStatus("warn", "已请求停止");
    } catch (err) {
      this.setStatus("err", `停止失败: ${err.message}`);
    }
    this.renderPrefetch();
  }

  renderPrefetch() {
    const layer = this.detailLayer;
    while (layer.firstChild) layer.removeChild(layer.firstChild);
    if (!this.prefetchOpen) {
      layer.style.display = "none";
      return;
    }
    layer.style.display = "block";

    const options = this.prefetchOptions || {};
    const box = el("div", "lsp-detail");

    const head = el("div", "lsp-detail-head");
    head.appendChild(el("div", "lsp-detail-title", "C 站批量补全"));
    const closeBtn = el("button", "lsp-icon-btn");
    closeBtn.appendChild(svg(ICON.close, 12));
    closeBtn.title = "关闭";
    closeBtn.addEventListener("click", (e) => {
      e.preventDefault();
      this.closePrefetch();
    });
    head.appendChild(closeBtn);
    box.appendChild(head);

    const body = el("div", "lsp-detail-body");

    const plan = this.prefetchPlan;
    if (plan) {
      const kv = el("dl", "lsp-kv");
      const addKV = (key, value) => {
        kv.appendChild(el("dt", null, key));
        kv.appendChild(el("dd", null, value));
      };
      addKV("LoRA 总数", plan.total);
      addKV("没有预览图", plan.no_cover);
      addKV("已有 C 站信息", plan.cached);
      addKV("待处理", plan.pending);
      body.appendChild(kv);
    } else {
      const line = el("div", "lsp-note");
      line.append(el("span", "lsp-spinner"), document.createTextNode(" 正在统计…"));
      body.appendChild(line);
    }

    const state = this.prefetchState || {};
    const running = !!state.running;

    // 选项开关
    const setToggle = (label, desc, key) => {
      const row = el("div", "lsp-set-row");
      const text = el("div", "lsp-set-text");
      text.appendChild(el("div", "lsp-set-label", label));
      if (desc) text.appendChild(el("div", "lsp-set-desc", desc));
      const toggle = el("button", `lsp-switch${options[key] ? " on" : ""}`);
      toggle.appendChild(svg(ICON.check, 9));
      toggle.disabled = running;
      toggle.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        options[key] = !options[key];
        this.renderPrefetch();
      });
      row.append(text, toggle);
      return row;
    };

    body.appendChild(setToggle(
      "只补没有封面的",
      "已经能从本地图片认出封面的直接跳过，优先消灭空白卡片",
      "only_missing_cover",
    ));
    body.appendChild(setToggle(
      "跳过已有 C 站信息",
      "旁挂 .civitai.info 或插件缓存里已经有数据的，不再联网",
      "skip_cached",
    ));
    body.appendChild(setToggle(
      "顺带下载封面",
      "查到样例图就存成 <文件名>.preview.<扩展名> 并设为封面",
      "download_cover",
    ));

    // 上限与间隔
    const limitRow = el("div", "lsp-set-row");
    const limitText = el("div", "lsp-set-text");
    limitText.appendChild(el("div", "lsp-set-label", "单次上限"));
    limitText.appendChild(el("div", "lsp-set-desc", "一次最多处理多少个，跑完再点一次可以接着补"));
    const limitChips = el("div", "lsp-set-chips");
    for (const value of [50, 200, 500, 2000]) {
      const chip = el("button", `lsp-chip${Number(options.limit) === value ? " on" : ""}`, String(value));
      chip.disabled = running;
      chip.addEventListener("click", (e) => {
        e.preventDefault();
        options.limit = value;
        this.renderPrefetch();
      });
      limitChips.appendChild(chip);
    }
    limitRow.append(limitText, limitChips);
    body.appendChild(limitRow);

    const delayRow = el("div", "lsp-set-row");
    const delayText = el("div", "lsp-set-text");
    delayText.appendChild(el("div", "lsp-set-label", "每项间隔"));
    delayText.appendChild(el("div", "lsp-set-desc", "给 Civitai 留出的空档，太快容易被限流"));
    const delayChips = el("div", "lsp-set-chips");
    for (const [value, label] of [[600, "0.6s"], [1200, "1.2s"], [2500, "2.5s"]]) {
      const chip = el("button", `lsp-chip${Number(options.delay_ms) === value ? " on" : ""}`, label);
      chip.disabled = running;
      chip.addEventListener("click", (e) => {
        e.preventDefault();
        options.delay_ms = value;
        this.renderPrefetch();
      });
      delayChips.appendChild(chip);
    }
    delayRow.append(delayText, delayChips);
    body.appendChild(delayRow);

    // 进度
    if (state.planned) {
      const bar = el("div", "lsp-progress");
      const fill = el("div", "lsp-progress-fill");
      fill.style.width = `${Math.max(0, Math.min(100, Number(state.percent) || 0))}%`;
      bar.appendChild(fill);
      body.appendChild(bar);

      const parts = [
        `${state.done}/${state.planned}`,
        `新获取 ${state.ok || 0}`,
        `已缓存 ${state.cached || 0}`,
        `下载封面 ${state.covered || 0}`,
      ];
      if (state.skipped) parts.push(`跳过 ${state.skipped}`);
      if (state.failed) parts.push(`失败 ${state.failed}`);
      body.appendChild(el("div", "lsp-note", parts.join(" · ")));
      if (state.current) body.appendChild(el("div", "lsp-note", `当前：${state.current}`));
      if (state.message) body.appendChild(el("div", "lsp-note", state.message));
      if (state.error) body.appendChild(el("div", "lsp-note err", state.error));
    }

    const actions = el("div", "lsp-detail-actions");
    if (running) {
      const stopBtn = el("button", "lsp-btn danger", "停止");
      stopBtn.addEventListener("click", (e) => {
        e.preventDefault();
        this.stopPrefetch();
      });
      actions.appendChild(stopBtn);
    } else {
      const startBtn = el("button", "lsp-btn primary", "开始补全");
      startBtn.title = "后台任务，关掉这个面板也会继续跑";
      startBtn.addEventListener("click", (e) => {
        e.preventDefault();
        this.startPrefetch({ ...options });
      });
      actions.appendChild(startBtn);
    }

    const stackBtn = el("button", "lsp-btn", "只查当前堆");
    stackBtn.title = "对当前这套堆立刻查一遍 C 站，不进队列";
    stackBtn.disabled = !!this._prefetching;
    stackBtn.addEventListener("click", (e) => {
      e.preventDefault();
      this.prefetchAll();
    });
    actions.appendChild(stackBtn);

    if (this._prefetching) actions.appendChild(el("span", "lsp-spinner"));
    body.appendChild(actions);

    body.appendChild(el("div", "lsp-note",
      "任务在后端线程里跑，关掉面板甚至刷新页面都不影响；进度会在重新打开时恢复。"
      + "每项的哈希和响应都会写进 LoRA 同目录的 .civitai.info，下次直接读本地。"));
    body.appendChild(el("div", "lsp-note",
      "首次跑要算整个文件的 sha256，机械硬盘上会明显慢；固态盘上 200 个大约几分钟。"));

    box.appendChild(body);
    layer.appendChild(box);
  }
  updateTopbar() {
    if (!this.toggleBtn) return;
    // 每次 sync 都会走到这里；内容只由折叠态和堆大小决定，没变就别重建那个 SVG
    const key = `${this.collapsed}|${this.items.length}`;
    if (this._topbarKey !== key) {
      this._topbarKey = key;
      while (this.toggleBtn.firstChild) this.toggleBtn.removeChild(this.toggleBtn.firstChild);
      this.toggleBtn.appendChild(svg(this.collapsed ? ICON.chevronDown : ICON.chevronUp, 11));
      this.toggleBtn.appendChild(el("span", null, this.collapsed ? "浏览 LoRA" : "收起"));
    }
    if (this.topbarLabel) this.topbarLabel.textContent = `已选 ${this.items.length}`;
  }

  /** 节点标题栏 + 输入插槽行占掉的高度，面板只能从这之下开始 */
  chromeHeight() {
    const startY = Number(this.node?.widgets_start_y);
    if (Number.isFinite(startY) && startY > 20) return startY + 14;
    const inputs = this.modelOnly ? 1 : 2;
    return 30 + inputs * 22 + 14;
  }

  /**
   * 折叠时节点高度随条目数收缩，最少留一行的位置。
   * 这是唯一决定节点尺寸的入口，且只朝一个方向：内容多了才撑高。
   */
  collapsedHeight() {
    const rows = this.items.length ? Math.min(this.items.length, 6) : 0;
    const content = 12 + 22 + 4 + (rows ? rows * 25 : 27);
    return Math.round(content + this.chromeHeight());
  }

  applyNodeSize() {
    const node = this.node;
    if (!node?.setSize) return;
    const width = node.size?.[0] ?? 380;
    let target;
    if (this.collapsed) {
      if (!this._expandedHeight && (node.size?.[1] ?? 0) > 240) {
        this._expandedHeight = node.size[1];
      }
      target = this.collapsedHeight();
    } else {
      target = this._expandedHeight || 620;
    }
    node.setSize([width, target]);
    this.__fitWidth?.();
    node.setDirtyCanvas?.(true, true);
  }

  /** 折叠态只在内容放不下时向上撑高，不主动压缩用户拉大的窗口 */
  maybeGrowCollapsed() {
    if (!this.collapsed) return;
    const need = this.collapsedHeight();
    const node = this.node;
    if ((node.size?.[1] ?? 0) < need - 8) {
      node.setSize?.([node.size?.[0] ?? 380, need]);
      this.__fitWidth?.();
      node.setDirtyCanvas?.(true, true);
    }
  }

  setCollapsed(value) {
    const next = !!value;
    if (this.collapsed === next) return;
    this.collapsed = next;
    this.node.properties = this.node.properties || {};
    this.node.properties.lspCollapsed = next;
    this.root.classList.toggle("collapsed", next);
    this.updateTopbar();
    this.applyNodeSize();
    this.render();
  }

  restoreCollapsed() {
    const nodeHeight = this.node.size?.[1] ?? 0;
    // 只在合理范围内记住展开高度，避免把旧工作流里被撑坏的尺寸带进来
    if (nodeHeight > 1600) {
      this._expandedHeight = 620;
    } else if (!this._expandedHeight && nodeHeight > 240) {
      this._expandedHeight = nodeHeight;
    }
    const saved = this.node.properties?.lspCollapsed;
    this.collapsed = saved === undefined ? true : !!saved;
    this.root.classList.toggle("collapsed", this.collapsed);
    this.updateTopbar();
    this.applyNodeSize();
  }

  /* ------------------------------------------------------------ 数据同步 */
  widget() {
    return this.node.widgets?.find((w) => w.name === "lora_stack");
  }

  buildPayload() {
    return this.items.map((item) => ({
      name: item.rel,
      alias: this.aliasOf(item.rel),
      sm: Number(item.sm),
      sc: Number(item.sc),
      enabled: !!item.enabled,
    }));
  }

  /** 重同步：堆的成员、顺序或启停变了，需要连带校验和底模核对 */
  sync() {
    const widget = this.widget();
    if (widget) widget.value = JSON.stringify(this.buildPayload());
    this.updateTopbar();
    this.maybeGrowCollapsed();
    this.node.setDirtyCanvas?.(true, false);
    this.scheduleValidate();
    this.scheduleBaseCheck();
  }

  /**
   * 轻同步：只把权重写回 widget。
   * 权重不改变「文件是否失效」「底模是什么」，所以不触发校验和底模请求；
   * 画布重绘也合并到一次，避免连点 ± 时每点一下都重绘整个画布。
   */
  syncWeights() {
    const widget = this.widget();
    if (widget) widget.value = JSON.stringify(this.buildPayload());
    if (this._dirtyTimer) return;
    this._dirtyTimer = setTimeout(() => {
      this._dirtyTimer = null;
      this.node.setDirtyCanvas?.(true, false);
    }, 300);
  }

  restoreFromWidget(force = false) {
    // 加载工作流时 onNodeCreated 和 onConfigure 各排一次恢复，这里只认第一次
    if (this._restored && !force) return;
    this._restored = true;
    const widget = this.widget();
    if (!widget || typeof widget.value !== "string") return;
    let raw;
    try {
      raw = JSON.parse(widget.value || "[]");
    } catch (err) {
      raw = [];
    }
    if (raw && !Array.isArray(raw) && typeof raw === "object") {
      raw = raw.stack || raw.loras || raw.lora || raw.temp_lora || [];
    }
    if (!Array.isArray(raw)) raw = [];

    this.items = raw
      .map((entry) => {
        if (typeof entry === "string") entry = { name: entry };
        if (!entry || typeof entry !== "object") return null;
        const rel = String(
          entry.name || entry.lora || entry.path || entry.lora_name || entry.rel || ""
        ).replace(/\\/g, "/").replace(/^\/+/, "").trim();
        if (!rel) return null;
        const base = entry.weight ?? 1.0;
        const sm = Number(entry.sm ?? entry.strength_model ?? base);
        const sc = Number(entry.sc ?? entry.strength_clip ?? entry.text_encoder_weight ?? base);
        let enabled = entry.enabled ?? entry.on ?? true;
        if (entry.hidden === true || entry.muted === true) enabled = false;
        const alias = typeof entry.alias === "string" ? entry.alias.trim() : "";
        if (alias && !this.aliases[rel]) this.aliases[rel] = alias;
        return {
          rel,
          stem: stemOf(rel),
          folder: folderOf(rel),
          name: rel.split("/").pop() || rel,
          thumb: thumbUrlFor(rel),
          sm: Number.isFinite(sm) ? sm : 1,
          sc: Number.isFinite(sc) ? sc : 1,
          enabled: !!enabled,
        };
      })
      .filter(Boolean);
    this.render();
    // 从工作流恢复的堆同样要做失效校验和底模核对
    this.scheduleValidate();
    this.scheduleBaseCheck();
  }

  /* ------------------------------------------------------------ 数据加载 */
  async loadDir(path, depth = 0, noDescend = false) {
    const target = path || "";
    this.busy = true;
    // 已经有当前层数据时不要先清屏再重绘：那样每切一次目录都白建一遍全部缩略图
    const hadData = this.view.kind === "dir" && !!this.view.data;
    if (!hadData) {
      this.view = { kind: "dir", data: this.view.data };
      this.render();
    } else if (!noDescend) {
      this.setStatus("idle", "加载中…");
    }
    let descendTo = null;
    try {
      const query = new URLSearchParams({ path: target, sort: this.dirSort, filter: this.dirFilter });
      const data = await requestJSON(`/tree?${query.toString()}`);
      this.currentPath = data.path || "";

      // 只有唯一子文件夹、且本层没有 LoRA 时自动下钻，省掉一层层点
      if (!noDescend && depth < 8 && data.folders.length === 1 && data.loras.length === 0
          && data.folders[0].total_count > 0 && !this.query) {
        descendTo = data.folders[0].path;
      } else {
        this.dirShown = 0;   // 换层 / 换排序 / 换筛选都从第一批重新开始
        this.view = { kind: "dir", data };
        const filtered = data.filter ? `（筛选 ${data.filtered_count}/${data.lora_count}）` : "";
        this.setStatus("ok", `${data.folders.length} 个文件夹 · ${data.loras.length} 个 LoRA${filtered} · 该层含 ${data.total_count} 个`);
      }
    } catch (err) {
      if (err.status === 503) {
        this.setStatus("warn", "正在扫描 loras 目录，稍候自动重试…");
        setTimeout(() => this.loadDir(target, depth), 1500);
      } else {
        this.setStatus("err", `加载失败: ${err.message}`);
        this.view = { kind: "error", data: null };
      }
    } finally {
      this.busy = false;
    }
    if (descendTo) {
      return this.loadDir(descendTo, depth + 1);
    }
    this.render();
  }

  /** 搜索范围循环：全部 → 只看文件夹 → 只看 LoRA 文件 */
  cycleSearchScope() {
    const order = ["all", "folder", "lora"];
    const short = { all: "全", folder: "夹", lora: "件" };
    const hint = {
      all: "搜索范围：全部（文件名 + 文件夹名）",
      folder: "搜索范围：只看文件夹（结果点进去自己浏览）",
      lora: "搜索范围：只看 LoRA 文件名",
    };
    const i = order.indexOf(this.searchScope);
    this.searchScope = order[(i + 1) % order.length];
    this.scopeBtn.textContent = short[this.searchScope];
    this.scopeBtn.title = hint[this.searchScope];
    this.scopeBtn.classList.toggle("on", this.searchScope !== "all");
    if (this.query.trim()) this.runSearch(this.input.value);
  }

  async runSearch(query) {
    const value = String(query || "").trim();
    if (!value) return;
    this.query = value;
    this.searchShown = 0;      // 新一次搜索从第一批重新开始
    this.busy = true;
    this.render();
    try {
      // 只取第一页：命中上千条时后端一次全发实测约 590 KB，翻页走 offset 增量拉
      const data = await requestJSON(
        `/search?q=${encodeURIComponent(value)}&limit=${SEARCH_PAGE}&offset=0`
          + `&tags=${this.includeTags ? 1 : 0}&scope=${this.searchScope}`
      );
      this.view = {
        kind: "search",
        data,
        items: Array.isArray(data.loras) ? data.loras : [],
        nameTotal: data.name_total || 0,
        scope: this.searchScope,
      };
      const parts = [];
      if (data.total) parts.push(`命中 ${data.total} 个 LoRA`);
      if (data.folder_total) parts.push(`${data.folder_total} 个文件夹`);
      if (data.tag_total) parts.push(`${data.tag_total} 个标签相关`);
      this.setStatus(parts.length ? "ok" : "idle", parts.length ? parts.join(" · ") : "没有匹配");
    } catch (err) {
      this.setStatus("err", `搜索失败: ${err.message}`);
      this.view = { kind: "error", data: null };
    } finally {
      this.busy = false;
      this.render();
    }
  }

  navigate(path) {
    this.input.value = "";
    this.query = "";
    this.view = { kind: "dir", data: null };
    this.loadDir(path || "");
  }

  /* ------------------------------------------------------------ 已选操作 */
  indexOf(rel) {
    return this.items.findIndex((item) => item.rel === rel);
  }

  toggleItem(entry) {
    const rel = entry.rel || entry.path;
    const existing = this.indexOf(rel);
    if (existing >= 0) {
      this.items.splice(existing, 1);
    } else {
      this.items.push({
        rel,
        stem: entry.stem || stemOf(rel),
        folder: entry.folder ?? folderOf(rel),
        name: entry.name || rel.split("/").pop() || rel,
        thumb: entry.thumb || thumbUrlFor(rel),
        sm: 1,
        sc: 1,
        enabled: true,
      });
    }
    this.sync();
    this.renderSelected();
    this.updateCardSelection(rel);
    // 底部那块「当前堆中的 LoRA」是可选展示，只有开了才需要整块重刷
    if (this.settings.showStackInDir && this.view.kind === "dir") this.renderBody();
  }

  /** 只切换某张卡片的收藏星标，不重建浏览区 */
  updateCardFavorite(rel) {
    const faved = this.isFavorite(rel);
    for (const card of this._cardEls) {
      if (card.dataset.rel !== rel) continue;
      const btn = card.querySelector('[data-role="fav"]');
      if (!btn) continue;
      btn.classList.toggle("on", faved);
      btn.title = faved ? "取消收藏" : "加入收藏";
    }
  }

  /** 只切换某张卡片的选择态和角标，不重建浏览区（重建会让所有缩略图重新解码） */
  updateCardSelection(rel) {
    const selected = this.indexOf(rel) >= 0;
    for (const card of this._cardEls) {
      if (card.dataset.rel !== rel) continue;
      card.classList.toggle("selected", selected);
      const thumb = card.querySelector(".lsp-card-thumb");
      if (!thumb) continue;
      const badge = thumb.querySelector(".lsp-card-badge");
      if (selected && !badge) thumb.appendChild(el("div", "lsp-card-badge", "✓"));
      else if (!selected && badge) badge.remove();
    }
  }

  removeAt(index) {
    const item = this.items[index];
    this.items.splice(index, 1);
    this.sync();
    this.renderSelected();
    if (item) this.updateCardSelection(item.rel);
    if (this.settings.showStackInDir && this.view.kind === "dir") this.renderBody();
  }

  moveItem(from, to) {
    if (from === to || from < 0 || from >= this.items.length) return;
    const [moved] = this.items.splice(from, 1);
    const target = Math.max(0, Math.min(to, this.items.length));
    this.items.splice(target, 0, moved);
    this.sync();
    // 拖拽只改堆的顺序，浏览区不受影响
    this.renderSelected();
    if (this.settings.showStackInDir && this.view.kind === "dir") this.renderBody();
  }

  /* ------------------------------------------------------------ 渲染 */
  /** 同一帧内的多次渲染请求合并成一次：启动时几个请求各自回来不该各刷一遍全量 */
  requestRender() {
    if (this._renderPending) return;
    this._renderPending = true;
    queueMicrotask(() => {
      this._renderPending = false;
      this.render();
    });
  }

  render() {
    this.updateTopbar();
    this.renderCrumbs();
    this.renderBody();
    this.renderSelected();
    this.clampCursor();
    if (this.presetsOpen) this.renderPresets();
    else if (this.settingsOpen) this.renderSettings();
    else if (this.prefetchOpen) this.renderPrefetch();
    else if (this.detail) this.renderDetail();
  }

  /* ------------------------------------------------------- 键盘游标绘制 */
  clampCursor() {
    if (this.cursor >= this._cardEls.length) this.cursor = this._cardEls.length - 1;
    if (this.stackCursor >= this._rowEls.length) this.stackCursor = this._rowEls.length - 1;
    this.paintCursor();
  }

  paintCursor() {
    this._cardEls.forEach((node, index) => {
      node.classList.toggle("lsp-cursor", index === this.cursor);
    });
    this._rowEls.forEach((node, index) => {
      node.classList.toggle("lsp-cursor", index === this.stackCursor);
    });
  }

  renderCrumbs() {
    // 面包屑只由视图模式、路径和搜索词决定，这三样没变就不必重建这一行
    const key = `${this.viewMode}|${this.currentPath}|${this.query}|${this.view.kind}`;
    if (this._crumbKey === key) return;
    this._crumbKey = key;

    const target = this.crumbsEl;
    while (target.firstChild) target.removeChild(target.firstChild);

    if (this.viewMode !== "dir") {
      const label = this.viewMode === "recent"
        ? `最近使用 · 最多保留 10 个`
        : `收藏 · ${this.usage.favorites.length} 个`;
      target.appendChild(el("span", "lsp-crumb current", label));
      target.appendChild(el("div", "lsp-crumbs-spacer"));
      target.append(this.upBtn, this.refreshBtn);
      this.upBtn.style.visibility = "hidden";
      return;
    }

    const searching = this.view.kind === "search";
    const home = el("button", `lsp-crumb${!searching && !this.currentPath ? " current" : ""}`, "全部");
    home.addEventListener("click", () => this.navigate(""));
    target.appendChild(home);

    const crumbs = (this.view.kind === "dir" && this.view.data?.crumbs) || [];
    for (const crumb of crumbs) {
      target.appendChild(el("span", "lsp-crumb-sep", "›"));
      const isLast = crumb.path === this.currentPath;
      const button = el(
        "button",
        `lsp-crumb${isLast && !searching ? " current" : ""}`,
        crumb.name
      );
      button.title = crumb.path;
      if (!(isLast && !searching)) {
        button.addEventListener("click", () => this.navigate(crumb.path));
      }
      target.appendChild(button);
    }

    if (searching) {
      target.appendChild(el("span", "lsp-crumb-sep", "›"));
      const badge = el("span", "lsp-crumb current", `搜索：${this.query}`);
      target.appendChild(badge);
    }

    const spacer = el("div", "lsp-crumbs-spacer");
    target.append(spacer, this.upBtn, this.refreshBtn);
    this.upBtn.style.visibility = this.currentPath ? "visible" : "hidden";
  }

  renderBody() {
    const body = this.body;
    const scrollTop = body.scrollTop;
    while (body.firstChild) body.removeChild(body.firstChild);
    this._cardEls = [];
    this._dirGrid = null;
    this._dirMoreHint = null;
    this._searchRendered = 0;
    this._searchNameGrid = null;
    this._searchTagGrid = null;
    this._searchHint = null;
    this._searchLoading = false;
    this._collectionGrid = null;
    this._collectionList = null;
    this._collectionHint = null;
    this.initScroll();
    // 命中词一次算好，之前是每张卡片算一遍（30 张就是 30 次排序）
    this._tokens = this.highlightTokens();

    if (this.busy && !this.view.data) {
      const wrap = el("div", "lsp-empty");
      wrap.append(el("div", "lsp-spinner"), el("div", null, "加载中…"));
      body.appendChild(wrap);
      return;
    }

    if (this.view.kind === "error") {
      body.appendChild(el("div", "lsp-empty", "加载失败，点右上角刷新重试"));
      return;
    }

    if (this.viewMode === "recent") {
      this.renderCollection(
        this.usage.recent,
        "还没有使用记录。执行一次生成，用过的 LoRA 会出现在这里（最多保留 10 个）。",
      );
      return;
    }
    if (this.viewMode === "fav") {
      this.renderCollection(
        this.usage.favorites,
        "还没有收藏。鼠标移到卡片上，点左上角的星标。",
      );
      return;
    }

    if (this.view.kind === "search") {
      this.renderSearchResults(body);
    } else if (this.view.data) {
      this.renderDir(body, this.view.data);
    }

    body.scrollTop = scrollTop;
  }

  renderDir(body, data) {
    if (data.folders.length) {
      body.appendChild(this.sectionLabel(`文件夹 · ${data.folders.length}`));
      const row = el("div", "lsp-folder-row");
      for (const folder of data.folders) {
        row.appendChild(this.folderChip(folder));
      }
      body.appendChild(row);
    }

    if (data.loras.length) {
      body.appendChild(this.sectionLabel(`LoRA · ${data.loras.length}`));
      const grid = el("div", "lsp-grid");
      const total = data.loras.length;
      const shown = Math.min(this.dirShown || DIR_PAGE, total);
      for (let i = 0; i < shown; i += 1) grid.appendChild(this.card(data.loras[i]));
      body.appendChild(grid);
      this._dirGrid = grid;
      if (shown < total) {
        this._dirMoreHint = this.moreHint(`已显示 ${shown}/${total} · 下滑继续加载`);
        body.appendChild(this._dirMoreHint);
      } else if (total > DIR_PAGE) {
        body.appendChild(el("div", "lsp-note lsp-page-end", `已显示全部 ${total} 个`));
      }
    }

    if (!data.folders.length && !data.loras.length) {
      const empty = el("div", "lsp-empty");
      empty.appendChild(svg(ICON.folder, 26));
      if (data.filter) {
        empty.appendChild(el("div", null,
          data.filter === "nocover" ? "这一层没有缺封面的 LoRA" : "这一层没有重名文件"));
        if (data.lora_count) {
          empty.appendChild(el("div", null, `（这一层共 ${data.lora_count} 个 LoRA，筛选后为 0）`));
        }
      } else {
        empty.appendChild(el("div", null, "这个文件夹里没有 LoRA"));
        if (data.path) {
          empty.appendChild(el("div", null, `（${data.total_count} 个子项位于更深层）`));
        }
      }
      body.appendChild(empty);
    }

    if (this.items.length && this.settings.showStackInDir) {
      body.appendChild(this.sectionLabel("当前堆中的 LoRA"));
      const grid = el("div", "lsp-grid");
      for (const item of this.items) {
        grid.appendChild(this.card({ ...item, rel: item.rel }));
      }
      body.appendChild(grid);
    }
  }

  renderSearchResults(body) {
    const view = this.view;
    const data = view.data || {};
    const items = view.items || [];

    const tokens = data.tokens || [];
    const notice = el("div", "lsp-note");
    notice.textContent = `搜索「${tokens.join(" ")}」`;
    body.appendChild(notice);

    const folders = data.folders || [];
    if (folders.length) {
      const folderTotal = data.folder_total || folders.length;
      body.appendChild(this.sectionLabel(`匹配的文件夹 · ${folderTotal}`));
      const row = el("div", "lsp-folder-row");
      for (const folder of folders) {
        const chip = this.folderChip(folder);
        chip.classList.add("lsp-folder-hit");
        row.appendChild(chip);
      }
      body.appendChild(row);
    }

    const total = data.total || 0;
    this._searchRendered = 0;
    this.appendSearchItems(items);

    if (total > 0 && this._searchRendered < total) {
      this._searchHint = this.moreHint(
        `已显示 ${this._searchRendered}/${total} · 下滑继续加载`,
      );
      body.appendChild(this._searchHint);
    }

    if (!folders.length && !total) {
      const empty = el("div", "lsp-empty");
      empty.appendChild(svg(ICON.search, 26));
      empty.appendChild(el("div", null, "没有匹配的 LoRA 或文件夹"));
      if (!this.includeTags) {
        empty.appendChild(el("div", null, "试试打开搜索框的「标签」开关"));
      }
      body.appendChild(empty);
    }
  }

  /**
   * 把一批搜索结果追加到对应分组。
   * 后端已按「名称优先、标签在后」排序，`name_total` 就是分界；跨过分界时
   * 现场插入「训练标签包含」标题，所以分页不会打乱两组的先后。
   */
  appendSearchItems(items) {
    if (!items || !items.length) return;
    const view = this.view;
    const nameTotal = view.nameTotal || 0;
    const tagTotal = (view.data && view.data.tag_total) || 0;
    const body = this.body;
    // 只搜文件名时不该说「文件夹命中」
    const nameLabel = (view.scope || "all") === "lora" ? "文件名命中" : "名称 / 文件夹命中";
    let rendered = this._searchRendered || 0;

    for (const item of items) {
      if (rendered >= nameTotal) {
        if (!this._searchTagGrid) {
          body.appendChild(this.sectionLabel(`训练标签包含 · ${tagTotal}`));
          this._searchTagGrid = el("div", "lsp-grid");
          body.appendChild(this._searchTagGrid);
        }
        this._searchTagGrid.appendChild(this.card(item, true));
      } else {
        if (!this._searchNameGrid) {
          body.appendChild(this.sectionLabel(`${nameLabel} · ${nameTotal}`));
          this._searchNameGrid = el("div", "lsp-grid");
          body.appendChild(this._searchNameGrid);
        }
        this._searchNameGrid.appendChild(this.card(item));
      }
      rendered += 1;
    }

    this._searchRendered = rendered;
    this.searchShown = rendered;
    if (this._searchHint) body.appendChild(this._searchHint);  // 提示条永远压在最后
  }

  updateSearchHint() {
    const hint = this._searchHint;
    if (!hint) return;
    const total = (this.view.data && this.view.data.total) || 0;
    const rendered = this._searchRendered || 0;
    if (rendered >= total) {
      hint.textContent = `已显示全部 ${total} 个`;
      hint.classList.add("lsp-more-done");
      hint.disabled = true;
      this._searchHint = null;
    } else {
      hint.textContent = `已显示 ${rendered}/${total} · 下滑继续加载`;
    }
  }

  /** 触底提示条兼作兜底按钮：滚轮不生效时也能手动继续展开 */
  moreHint(text) {
    const hint = el("button", "lsp-btn lsp-more-hint", text);
    hint.type = "button";
    hint.title = "滚动到底自动加载，也可以直接点这里";
    hint.addEventListener("click", (e) => {
      e.preventDefault();
      this.loadNextPage();
    });
    return hint;
  }

  initScroll() {
    const body = this.body;
    if (!body || this._scrollBound) return;
    this._scrollBound = true;
    this._scrollHandler = () => {
      if (this._pageRaf) return;
      this._pageRaf = requestAnimationFrame(() => {
        this._pageRaf = 0;
        const remain = body.scrollHeight - body.scrollTop - body.clientHeight;
        if (remain <= PAGE_TRIGGER_PX) this.loadNextPage();
      });
    };
    body.addEventListener("scroll", this._scrollHandler, { passive: true });
  }

  /** 按当前视图追加下一批；只 append 不重建，已有缩略图不重新解码 */
  loadNextPage() {
    if (this.viewMode === "recent" || this.viewMode === "fav") this.appendCollectionPage();
    else if (this.view.kind === "dir") this.appendDirPage();
    else if (this.view.kind === "search") this.appendSearchPage();
  }

  appendDirPage() {
    const data = this.view.kind === "dir" ? this.view.data : null;
    const grid = this._dirGrid;
    if (!data || !grid) return;
    const total = data.loras.length;
    const shown = this.dirShown || DIR_PAGE;
    if (shown >= total) return;
    const next = Math.min(shown + DIR_PAGE, total);
    for (let i = shown; i < next; i += 1) grid.appendChild(this.card(data.loras[i]));
    this.dirShown = next;
    this.updateMoreHint(this._dirMoreHint, next, total);
    this.schedulePageCheck();
  }

  async appendSearchPage() {
    const view = this.view.kind === "search" ? this.view : null;
    if (!view || this._searchLoading) return;
    const rendered = this._searchRendered || 0;
    const total = (view.data && view.data.total) || 0;
    if (rendered >= total) return;

    this._searchLoading = true;
    try {
      const q = (view.data && view.data.query) || this.query;
      const data = await requestJSON(
        `/search?q=${encodeURIComponent(q)}&limit=${SEARCH_PAGE}&offset=${rendered}`
          + `&tags=${this.includeTags ? 1 : 0}&scope=${view.scope || this.searchScope}`
      );
      // 等待期间用户可能换了搜索词或重绘过面板，这批数据就不能再落地
      if (this.view !== view || this._searchRendered !== rendered) return;
      const got = Array.isArray(data.loras) ? data.loras : [];
      if (!got.length) {
        this._searchRendered = total;
        this.updateSearchHint();
        return;
      }
      view.items = (view.items || []).concat(got);
      this.appendSearchItems(got);
      this.updateSearchHint();
      this.schedulePageCheck();
    } catch (err) {
      this.setStatus("warn", `加载更多失败：${err.message}`);
    } finally {
      this._searchLoading = false;
    }
  }

  appendCollectionPage() {
    const grid = this._collectionGrid;
    const list = this._collectionList;
    if (!grid || !list) return;
    const total = list.length;
    const shown = this.collectionShown || DIR_PAGE;
    if (shown >= total) return;
    const next = Math.min(shown + DIR_PAGE, total);
    for (let i = shown; i < next; i += 1) grid.appendChild(this.card(list[i]));
    this.collectionShown = next;
    this.updateMoreHint(this._collectionHint, next, total);
    this.schedulePageCheck();
  }

  updateMoreHint(hint, shown, total) {
    if (!hint) return;
    if (shown >= total) {
      hint.textContent = `已显示全部 ${total} 个`;
      hint.classList.add("lsp-more-done");
      hint.disabled = true;
    } else {
      hint.textContent = `已显示 ${shown}/${total} · 下滑继续加载`;
    }
  }

  /** 一批卡片可能仍填不满视口，下一帧再判一次，别让面板看不见滚动条 */
  schedulePageCheck() {
    if (this._pageRaf) return;
    const body = this.body;
    if (!body) return;
    this._pageRaf = requestAnimationFrame(() => {
      this._pageRaf = 0;
      const remain = body.scrollHeight - body.scrollTop - body.clientHeight;
      if (remain <= PAGE_TRIGGER_PX) this.loadNextPage();
    });
  }

  sectionLabel(text) {
    return el("div", "lsp-section-label", text);
  }

  folderChip(folder) {
    const chip = el("button", "lsp-folder");
    chip.title = `${folder.path}\n${folder.total_count} 个 LoRA`;
    if (folder.thumb) {
      const img = el("img", "lsp-folder-thumb");
      img.src = folder.thumb;
      img.loading = "lazy";
      img.addEventListener("error", () => {
        img.replaceWith(this.placeholderThumb());
      });
      chip.appendChild(img);
    } else {
      chip.appendChild(this.placeholderThumb());
    }
    chip.appendChild(el("span", "lsp-folder-name", folder.name));
    chip.appendChild(el("span", "lsp-folder-count", folder.total_count));
    chip.addEventListener("click", (e) => {
      e.preventDefault();
      this.navigate(folder.path);
    });
    return chip;
  }

  placeholderThumb() {
    const box = el("div", "lsp-folder-thumb empty");
    box.appendChild(svg(ICON.folder, 12));
    return box;
  }

  card(lora, tagHit = false) {
    const rel = lora.rel || lora.path;
    const selected = this.indexOf(rel) >= 0;
    const card = el("div", `lsp-card${selected ? " selected" : ""}`);

    const thumbBox = el("div", "lsp-card-thumb");
    const thumbSrc = lora.thumb || thumbUrlFor(rel);
    const img = el("img");
    img.src = thumbSrc;
    img.loading = "lazy";
    img.alt = lora.stem || "";
    img.addEventListener("error", () => {
      img.remove();
      thumbBox.appendChild(el("div", "lsp-ph", lora.stem || rel));
    });
    thumbBox.appendChild(img);

    if (lora.dup) {
      const warn = el("div", "lsp-card-warn");
      warn.appendChild(svg(ICON.warn, 10));
      warn.title = "存在同名文件，ComfyUI 加载时可能解析到另一个";
      thumbBox.appendChild(warn);
    }

    if (selected) {
      const badge = el("div", "lsp-card-badge", "✓");
      thumbBox.appendChild(badge);
    }

    if (tagHit) {
      thumbBox.appendChild(el("span", "lsp-card-kind tag", "标签"));
    } else if (lora.match_folder && !lora.match_name) {
      thumbBox.appendChild(el("span", "lsp-card-kind folder", "文件夹"));
    }

    card.appendChild(thumbBox);

    const alias = this.aliasOf(rel) || lora.alias || "";
    const stem = lora.stem || stemOf(rel);
    const meta = el("div", "lsp-card-meta");
    const titleEl = el("div", "lsp-card-title");
    const tokens = this._tokens || [];
    if (tokens.length) highlightInto(titleEl, alias || stem, tokens);
    else titleEl.textContent = alias || stem;
    meta.appendChild(titleEl);
    // 有备注时副标题显示原文件名，方便对照
    meta.appendChild(el("div", "lsp-card-sub", alias ? stem : (lora.folder || "根目录")));
    card.appendChild(meta);

    const actions = el("div", "lsp-card-actions");
    const infoBtn = el("button", "lsp-mini-btn");
    infoBtn.appendChild(svg(ICON.info, 11));
    infoBtn.title = "查看详情与 Civitai 信息";
    infoBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      this.openDetail({ rel, stem: lora.stem, folder: lora.folder });
    });
    actions.appendChild(infoBtn);

    const faved = this.isFavorite(rel);
    const favBtn = el("button", `lsp-mini-btn${faved ? " on" : ""}`);
    favBtn.dataset.role = "fav";
    favBtn.appendChild(svg(ICON.star, 11));
    favBtn.title = faved ? "取消收藏" : "加入收藏";
    favBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      this.toggleFavorite(rel);
    });
    actions.appendChild(favBtn);
    card.appendChild(actions);

    card.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      this.toggleItem({ ...lora, rel });
    });
    card.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      e.stopPropagation();
      this.openDetail({ rel, stem: lora.stem, folder: lora.folder });
    });

    // 记下元素引用，键盘游标只切 class，不重建整块 DOM
    card.dataset.rel = rel;
    this._cardEls.push(card);

    return card;
  }

  /* --------------------------------------------------- 堆的提示条（中性） */
  renderStackGuards() {
    const box = this.selGuards;
    if (!box) return;
    while (box.firstChild) box.removeChild(box.firstChild);
    this._readoutEl = null;
    if (!this.items.length) return;

    // 只报读数，不设阈值：有些模型对 LoRA 响应很低，需要很高的权重才出效果
    const readout = el("span", "lsp-guard-readout");
    readout.title = "当前启用项的权重之和。超过 1.0 会变成橙色，那只是视觉提示，"
      + "不是警告——有的底模对 LoRA 响应很弱，高权重是正常需求。";
    this._readoutEl = readout;
    this._readoutRefs = null;      // 元素换了，内部 span 引用作废
    const line = el("div", "lsp-guard-line");
    line.appendChild(readout);
    box.appendChild(line);
    this.refreshReadout();

    // 底模混搭：只在不同已知族之间提示，识别不出底模的不参与判断
    if (this.bases.conflict && this.items.length > 1) {
      const families = Object.entries(this.bases.families || {})
        .filter(([, rels]) => Array.isArray(rels) && rels.length)
        .map(([name, rels]) => `${name}×${rels.length}`);
      const warn = el("div", "lsp-guard warn");
      warn.appendChild(svg(ICON.warn, 10));
      warn.appendChild(el("span", null, `堆里混了不同底模：${families.join(" / ")}`));
      warn.title = "混用不同底模体系的 LoRA 通常出烂图或直接报错，逐项核对后再执行";
      box.appendChild(warn);
    }

    // 同名不同路径：生态里只认文件名的节点可能解析到另一份
    const dupRels = Object.keys(this.issues.duplicates || {});
    if (dupRels.length) {
      const warn = el("div", "lsp-guard warn");
      warn.appendChild(svg(ICON.warn, 10));
      warn.appendChild(el("span", null, `堆里 ${dupRels.length} 个文件存在同名副本`));
      warn.title = dupRels.join("\n");
      box.appendChild(warn);
    }
  }

  /** 只改读数内容：span 建一次，之后仅更新文字和 class */
  refreshReadout() {
    const node = this._readoutEl;
    if (!node) return;
    const active = this.items.filter((item) => item.enabled);
    const sumModel = active.reduce((acc, item) => acc + (Number(item.sm) || 0), 0);
    const sumClip = active.reduce((acc, item) => acc + (Number(item.sc) || 0), 0);

    let refs = this._readoutRefs;
    if (!refs || refs.modelOnly !== this.modelOnly) {
      while (node.firstChild) node.removeChild(node.firstChild);
      refs = {
        modelOnly: this.modelOnly,
        m: el("span", "lsp-total"),
        c: null,
        count: el("span", "lsp-total-sep"),
      };
      node.appendChild(refs.m);
      if (!this.modelOnly) {
        node.appendChild(el("span", "lsp-total-sep", " · "));
        refs.c = el("span", "lsp-total");
        node.appendChild(refs.c);
      }
      node.appendChild(refs.count);
      this._readoutRefs = refs;
    }

    // Σ M 与 Σ C 各自判断是否超过 1.0：一个超标不该连带另一个也变色
    refs.m.textContent = `Σ M ${sumModel.toFixed(2)}`;
    refs.m.classList.toggle("hot", Math.abs(sumModel) > 1);
    if (refs.c) {
      refs.c.textContent = `Σ C ${sumClip.toFixed(2)}`;
      refs.c.classList.toggle("hot", Math.abs(sumClip) > 1);
    }
    refs.count.textContent = ` · ${active.length} 项启用`;
  }

  scheduleBaseCheck() {
    clearTimeout(this._baseTimer);
    this._baseTimer = setTimeout(() => this.checkBases(), 600);
  }

  async checkBases() {
    const rels = this.items.map((item) => item.rel);
    // 调权重也会触发 sync，但堆成员没变就不必再读一遍文件头
    const key = rels.join("|");
    if (key === this._baseKey) return;
    this._baseKey = key;
    if (rels.length < 2) {
      this.bases = { items: {}, families: {}, conflict: false, unknown: [] };
      this.renderStackGuards();
      return;
    }
    try {
      const data = await postJSON("/basemodels", { rels });
      if (this._baseKey !== key) return;      // 期间堆又变了，丢弃这次结果
      this.bases = {
        items: data.items && typeof data.items === "object" ? data.items : {},
        families: data.families && typeof data.families === "object" ? data.families : {},
        conflict: !!data.conflict,
        unknown: Array.isArray(data.unknown) ? data.unknown : [],
      };
      this.renderStackGuards();
    } catch (err) {
      /* 底模检测失败不影响堆本身 */
    }
  }

  renderSelected() {
    const list = this.selList;
    while (list.firstChild) list.removeChild(list.firstChild);
    this._rowEls = [];
    this._rowRefs = new Map();
    this.renderStackGuards();

    if (!this.items.length) {
      const empty = el("div", "lsp-empty");
      empty.style.padding = "6px 8px";
      empty.textContent = this.collapsed
        ? "点左上角「浏览 LoRA」挑几个加进来"
        : "点击上方任意 LoRA 加入堆";
      list.appendChild(empty);
      return;
    }

    let dragFrom = -1;

    this.items.forEach((item, index) => {
      const row = el("div", `lsp-sel-item${item.enabled ? "" : " off"}`);
      row.draggable = true;

      const grip = el("div", "lsp-sel-drag");
      grip.appendChild(svg(ICON.grip, 10));
      grip.title = "拖动排序";
      row.appendChild(grip);

      const thumb = el("img", "lsp-sel-thumb");
      thumb.src = item.thumb || thumbUrlFor(item.rel);
      thumb.loading = "lazy";
      thumb.addEventListener("error", () => {
        const ph = el("div", "lsp-sel-thumb ph", "?");
        thumb.replaceWith(ph);
      });
      row.appendChild(thumb);

      const alias = this.aliasOf(item.rel);
      const stem = item.stem || stemOf(item.rel);
      const name = el("div", "lsp-sel-name");
      name.appendChild(el("span", "lsp-sel-alias", alias || stem));
      if (alias) name.appendChild(el("span", "lsp-sel-orig", stem));
      // 悬停文案依赖校验结果，交给 applyIssuesToRows 统一维护
      name.addEventListener("dblclick", (e) => {
        e.stopPropagation();
        this.startAliasEdit(name, item);
      });
      name.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        e.stopPropagation();
        this.openDetail(item);
      });
      row.appendChild(name);

      const stepValue = this.weightStep();
      const stepBox = el("div", "lsp-steps");
      const minusBtn = el("button", "lsp-step-btn", "−");
      minusBtn.title = this.modelOnly ? `降低 ${stepValue}` : `降低 ${stepValue}（M 与 C 同步）`;
      minusBtn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        this.nudgeWeights(item, -1);
      });
      const plusBtn = el("button", "lsp-step-btn", "+");
      plusBtn.title = this.modelOnly ? `提高 ${stepValue}` : `提高 ${stepValue}（M 与 C 同步）`;
      plusBtn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        this.nudgeWeights(item, 1);
      });
      stepBox.append(minusBtn, plusBtn);
      row.appendChild(stepBox);

      const smLabel = el("span", "lsp-num-label", "M");
      const smInput = el("input", "lsp-num");
      smInput.type = "number";
      smInput.step = "0.05";
      smInput.value = String(item.sm);
      smInput.title = "模型权重";
      smInput.addEventListener("change", () => {
        const value = Number(smInput.value);
        item.sm = Number.isFinite(value) ? value : 1;
        smInput.value = String(item.sm);
        this.syncWeights();
        this.refreshReadout();
      });
      smInput.addEventListener("keydown", (e) => e.stopPropagation());
      row.append(smLabel, smInput);

      let scInput = null;
      if (!this.modelOnly) {
        const scLabel = el("span", "lsp-num-label", "C");
        scInput = el("input", "lsp-num");
        scInput.type = "number";
        scInput.step = "0.05";
        scInput.value = String(item.sc);
        scInput.title = "文本编码器权重";
        scInput.addEventListener("change", () => {
          const value = Number(scInput.value);
          item.sc = Number.isFinite(value) ? value : 1;
          scInput.value = String(item.sc);
          this.syncWeights();
          this.refreshReadout();
        });
        scInput.addEventListener("keydown", (e) => e.stopPropagation());
        row.append(scLabel, scInput);
      }

      const toggle = el("button", `lsp-toggle${item.enabled ? " on" : ""}`);
      toggle.title = item.enabled ? "点击禁用" : "点击启用";
      toggle.appendChild(svg(ICON.check, 9));
      toggle.addEventListener("click", (e) => {
        e.preventDefault();
        item.enabled = !item.enabled;
        this.syncWeights();
        // 只翻这一行的样式，别为了一个开关重建整个堆
        row.classList.toggle("off", !item.enabled);
        toggle.classList.toggle("on", item.enabled);
        toggle.title = item.enabled ? "点击禁用" : "点击启用";
        this.refreshReadout();
      });
      row.appendChild(toggle);

      // 失效 / 重名的角标不在这里建，交给 applyIssuesToRows 统一维护：
      // 校验是 500ms 防抖后异步回来的，只有它能变，不该为它重建整个堆

      const remove = el("button", "lsp-sel-remove");
      remove.appendChild(svg(ICON.close, 9));
      remove.title = "从堆中移除";
      remove.addEventListener("click", (e) => {
        e.preventDefault();
        this.removeAt(index);
      });
      row.appendChild(remove);

      row.addEventListener("dragstart", (e) => {
        dragFrom = index;
        row.classList.add("dragging");
        e.dataTransfer.effectAllowed = "move";
        try {
          e.dataTransfer.setData("text/plain", String(index));
        } catch (err) {
          /* 忽略 */
        }
      });
      row.addEventListener("dragend", () => {
        dragFrom = -1;
        row.classList.remove("dragging");
        for (const child of list.children) child.classList.remove("drop-target");
      });
      row.addEventListener("dragover", (e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        row.classList.add("drop-target");
      });
      row.addEventListener("dragleave", () => row.classList.remove("drop-target"));
      row.addEventListener("drop", (e) => {
        e.preventDefault();
        row.classList.remove("drop-target");
        if (dragFrom < 0) return;
        this.moveItem(dragFrom, index);
      });

      list.appendChild(row);
      this._rowEls.push(row);
      this._rowRefs.set(item.rel, { row, sm: smInput, sc: scInput, warn: null, name });
    });
    this.applyIssuesToRows();
  }

  /** 只维护失效 / 重名的角标和悬停文案，不动行本身的其他部分 */
  applyIssuesToRows() {
    for (const item of this.items) {
      const refs = this._rowRefs.get(item.rel);
      if (!refs) continue;
      const isMissing = this.issues.missing.has(item.rel);
      const dupOthers = this.issues.duplicates[item.rel] || [];
      const needed = isMissing || dupOthers.length > 0;
      refs.row.classList.toggle("missing", isMissing);

      if (needed && !refs.warn) {
        const warn = el("span", "lsp-sel-warn");
        warn.appendChild(svg(ICON.warn, 10));
        const remove = refs.row.querySelector(".lsp-sel-remove");
        refs.row.insertBefore(warn, remove || null);
        refs.warn = warn;
      } else if (!needed && refs.warn) {
        refs.warn.remove();
        refs.warn = null;
      }
      if (refs.warn) {
        refs.warn.title = isMissing ? "文件已失效" : `另有 ${dupOthers.length} 个同名文件`;
      }

      if (refs.name) {
        const alias = this.aliasOf(item.rel);
        const stem = item.stem || stemOf(item.rel);
        if (isMissing) {
          refs.name.title = `文件已失效：${item.rel}\n可能被移动或删除，请重新选择`;
        } else if (dupOthers.length) {
          refs.name.title = `${alias || stem}\n还有 ${dupOthers.length} 个同名文件，加载时可能解析到别的\n双击改备注 · 右键看详情`;
        } else {
          refs.name.title = alias
            ? `${alias}\n原名：${stem}\n双击改备注 · 右键看详情`
            : `${stem}\n双击添加备注 · 右键看详情`;
        }
      }
    }
  }

  /* ------------------------------------------------------------ 详情浮层 */
  openDetail(lora) {
    const rel = lora.rel || lora.path;
    if (!rel) return;
    // 折叠态空间不够放详情浮层，先展开
    if (this.collapsed) this.setCollapsed(false);
    this.settingsOpen = false;
    this.presetsOpen = false;
    this.prefetchOpen = false;
    this.detail = { rel, stem: lora.stem || stemOf(rel), folder: lora.folder ?? folderOf(rel), info: null, civitai: null, loading: true };
    this.detailLayer.style.display = "block";
    this.renderDetail();
    this.loadLocalInfo(rel);
  }

  closeDetail() {
    this.detail = null;
    this.detailLayer.style.display = "none";
    while (this.detailLayer.firstChild) this.detailLayer.removeChild(this.detailLayer.firstChild);
  }

  async loadLocalInfo(rel) {
    const cacheKey = `info:${rel}`;
    try {
      let info = this.metaCache.get(cacheKey);
      if (!info) {
        // hash=0：打开详情时不触发大文件全量哈希，等用户主动查 Civitai 时再算
        info = await requestJSON(`/info?rel=${encodeURIComponent(rel)}&hash=0`);
        this.metaCache.set(cacheKey, info);
      }
      if (!this.detail || this.detail.rel !== rel) return;
      this.detail.info = info;
      this.detail.loading = false;
      this.renderDetail();
      // 已有旁挂哈希时直接查 C 站（命中缓存几乎瞬时，命中不到才算文件哈希）
      if (info.sha256 && !this.detail.civitai) {
        this.loadCivitai(rel, false);
      }
    } catch (err) {
      if (!this.detail || this.detail.rel !== rel) return;
      this.detail.loading = false;
      this.detail.infoError = err.message;
      this.renderDetail();
    }
  }

  async loadDescription(detail) {
    if (!detail || detail.descLoading) return;
    detail.descLoading = true;
    detail.descFailed = false;
    this.renderDetail();
    try {
      const data = await requestJSON(`/description?rel=${encodeURIComponent(detail.rel)}`);
      if (data && data.ok && data.text) {
        detail.descText = data.text;
      } else {
        detail.descFailed = true;
        this.setStatus("warn", (data && data.error) || "没有拿到模型描述");
      }
    } catch (err) {
      detail.descFailed = true;
      this.setStatus("err", `加载描述失败: ${err.message}`);
    } finally {
      detail.descLoading = false;
      this.renderDetail();
    }
  }

  async loadCivitai(rel, force = false) {
    if (!this.detail || this.detail.rel !== rel) return;
    this.detail.civitaiLoading = true;
    this.renderDetail();
    try {
      const cacheKey = `civitai:${rel}`;
      let data = force ? null : this.metaCache.get(cacheKey);
      if (!data) {
        data = await requestJSON(`/civitai?rel=${encodeURIComponent(rel)}${force ? "&refresh=1" : ""}`);
        this.metaCache.set(cacheKey, data);
      }
      if (!this.detail || this.detail.rel !== rel) return;
      this.detail.civitai = data;
    } catch (err) {
      if (!this.detail || this.detail.rel !== rel) return;
      this.detail.civitai = { ok: false, error: err.message };
    } finally {
      if (this.detail && this.detail.rel === rel) {
        this.detail.civitaiLoading = false;
        this.renderDetail();
      }
    }
  }

  renderDetail() {
    const layer = this.detailLayer;
    while (layer.firstChild) layer.removeChild(layer.firstChild);
    if (!this.detail) {
      layer.style.display = "none";
      return;
    }
    layer.style.display = "block";

    const detail = this.detail;
    const box = el("div", "lsp-detail");

    const head = el("div", "lsp-detail-head");
    const title = el("div", "lsp-detail-title", detail.stem);
    title.title = detail.rel;
    const closeBtn = el("button", "lsp-icon-btn");
    closeBtn.appendChild(svg(ICON.close, 12));
    closeBtn.title = "关闭";
    closeBtn.addEventListener("click", (e) => {
      e.preventDefault();
      this.closeDetail();
    });
    head.append(title, closeBtn);
    box.appendChild(head);

    const body = el("div", "lsp-detail-body");

    const info = detail.info;
    const civitai = detail.civitai;
    const hasThumb = !!(info && info.thumb);

    if (detail.coverPicker) {
      // 展开 C 站候选：最多 8 张，有几张显示几张
      body.appendChild(this.buildCivitaiPicker(detail));
    } else if (hasThumb) {
      const hero = el("img", "lsp-detail-hero");
      hero.src = `${info.thumb}${detail.thumbV ? `&v=${detail.thumbV}` : ""}`;
      hero.addEventListener("error", () => {
        hero.remove();
        body.insertBefore(this.buildCoverPlaceholder(detail), body.firstChild);
      });
      body.appendChild(hero);
      const changeBtn = el("button", "lsp-link-btn", "从 C 站换封面");
      changeBtn.title = "展开 Civitai 样例图，选一张下载并设为封面";
      changeBtn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        this.openCoverPicker(detail);
      });
      body.appendChild(changeBtn);
    } else if (info) {
      // 没有本地预览图：给一个明确入口，点了才去 C 站取
      body.appendChild(this.buildCoverPlaceholder(detail));
    }

    // 指定封面指向的图片已经不在磁盘上：后端静默回落到自动识别，这里给出显式清理入口
    if (info && info.cover_stale) {
      const stale = el("div", "lsp-guard warn");
      stale.appendChild(svg(ICON.warn, 10));
      stale.appendChild(el("span", null,
        `指定的封面「${info.cover}」已不存在，当前按自动规则显示`));
      const fix = el("button", "lsp-link-btn", "清除指定");
      fix.title = "把 covers.json 里这条失效记录去掉，恢复自动识别";
      fix.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        this.setCover(detail.rel, "");
      });
      stale.appendChild(fix);
      body.appendChild(stale);
    }

    // 封面选择：本目录有多张图时让用户挑一张
    const localImages = info && Array.isArray(info.images) ? info.images : [];
    if (localImages.length > 1) {
      body.appendChild(el(
        "div",
        "lsp-desc-title",
        `封面（本目录 ${localImages.length} 张图，点选任意一张）`,
      ));
      const grid = el("div", "lsp-cover-grid");
      for (const image of localImages) {
        const cell = el("div", `lsp-cover-cell${image.current ? " on" : ""}`);
        const thumb = el("img");
        thumb.src = image.url;
        thumb.loading = "lazy";
        thumb.alt = image.file;
        cell.appendChild(thumb);
        cell.title = `${image.file}${image.current ? "（当前封面）" : ""}\n点击设为封面`;
        cell.addEventListener("click", (e) => {
          e.preventDefault();
          e.stopPropagation();
          this.setCover(detail.rel, image.file);
        });
        grid.appendChild(cell);
      }
      body.appendChild(grid);
    }

    const kv = el("dl", "lsp-kv");
    const addKV = (key, value) => {
      if (!value && value !== 0) return;
      kv.appendChild(el("dt", null, key));
      kv.appendChild(el("dd", null, value));
    };
    addKV("路径", detail.rel);
    const alias = this.aliasOf(detail.rel);
    addKV("备注", alias || "未设置");
    if (info) {
      addKV("大小", fmtSize(info.size));
      if (info.base_model) addKV("底模", info.base_model);
      if (info.output_name) addKV("训练名", info.output_name);
      if (info.network_dim) addKV("网络维度", info.network_dim);
      if (info.resolution) addKV("分辨率", info.resolution);
    }
    if (civitai && civitai.ok) {
      if (civitai.model_name) addKV("模型", civitai.model_name);
      if (civitai.version_name) addKV("版本", civitai.version_name);
      if (civitai.base_model) addKV("C站底模", civitai.base_model);
      if (civitai.downloads) addKV("下载量", civitai.downloads);
    }
    const sha = (civitai && civitai.sha256) || (info && info.sha256);
    if (sha) addKV("SHA256", sha.slice(0, 24) + "…");
    body.appendChild(kv);

    // 触发词
    const words = [];
    if (civitai && civitai.ok && civitai.trained_words) {
      for (const word of civitai.trained_words) words.push({ text: word, civitai: true });
    }
    if (info && info.triggers) {
      for (const word of info.triggers) {
        if (!words.some((w) => w.text.toLowerCase() === String(word).toLowerCase())) {
          words.push({ text: word, civitai: false });
        }
      }
    }
    if (words.length) {
      const fromCivitai = words.filter((word) => word.civitai);
      const head = el("div", "lsp-tag-head");
      head.appendChild(el(
        "span",
        null,
        fromCivitai.length
          ? "触发词 · 来自 Civitai（点击单个复制）"
          : "触发词 · 从训练元数据推断（点击单个复制）",
      ));
      const copyAll = el("button", "lsp-link-btn", "复制全部");
      copyAll.title = "把这一项的触发词复制成一行";
      copyAll.addEventListener("click", async (e) => {
        e.preventDefault();
        e.stopPropagation();
        const source = fromCivitai.length ? fromCivitai : words;
        const text = source.map((word) => word.text).join(", ");
        try {
          await navigator.clipboard.writeText(text);
          this.setStatus("ok", `已复制 ${source.length} 个触发词`);
        } catch (err) {
          this.setStatus("warn", `复制失败：${text.slice(0, 60)}`);
        }
      });
      head.appendChild(copyAll);
      body.appendChild(head);

      const tags = el("div", "lsp-tags");
      for (const word of words.slice(0, 40)) {
        const tag = el("span", `lsp-tag${word.civitai ? " civitai" : ""}`, word.text);
        tag.title = word.civitai ? "来自 Civitai" : "从训练元数据推断";
        tag.addEventListener("click", async (e) => {
          e.preventDefault();
          try {
            await navigator.clipboard.writeText(word.text);
            this.setStatus("ok", `已复制：${word.text}`);
          } catch (err) {
            this.setStatus("warn", "复制失败，请手动选择");
          }
        });
        tags.appendChild(tag);
      }
      body.appendChild(tags);
    }

    // Civitai 模型简介：by-hash 接口不带 description，需要按需单独拉一次
    if (civitai && civitai.ok) {
      if (detail.descText) {
        body.appendChild(el("div", "lsp-desc-title", "模型描述（点击展开 / 收起）"));
        const desc = el("div", "lsp-desc-body clamped", detail.descText);
        desc.title = "点击展开或收起";
        desc.addEventListener("click", (e) => {
          e.preventDefault();
          e.stopPropagation();
          desc.classList.toggle("clamped");
        });
        body.appendChild(desc);
      } else {
        const descBtn = el(
          "button",
          "lsp-btn small",
          detail.descLoading ? "加载中…" : (detail.descFailed ? "重试加载描述" : "加载模型描述"),
        );
        descBtn.disabled = !!detail.descLoading;
        descBtn.title = "从 Civitai 拉取这个模型的简介，只取一次，之后走本地缓存";
        descBtn.addEventListener("click", (e) => {
          e.preventDefault();
          this.loadDescription(detail);
        });
        body.appendChild(descBtn);
      }
    }

    // 状态 / 提示
    if (detail.infoError) {
      body.appendChild(el("div", "lsp-note err", `读取本地信息失败：${detail.infoError}`));
    }
    if (detail.loading) {
      const line = el("div", "lsp-note");
      line.append(el("span", "lsp-spinner"), document.createTextNode(" 正在读取本地元数据…"));
      body.appendChild(line);
    }

    if (civitai) {
      if (civitai.ok) {
        const note = el("div", "lsp-note");
        if (civitai.cached) {
          note.textContent = civitai.cached_beside
            ? "来自 LoRA 同目录的 .civitai.info，未联网"
            : "来自插件本地缓存，未联网";
        } else {
          note.textContent = civitai.cached_beside
            ? "已获取，并保存到 LoRA 同目录的 .civitai.info"
            : "已实时获取（写入插件缓存）";
        }
        body.appendChild(note);
        if (civitai.link) {
          const link = el("a", "lsp-btn primary", "打开 Civitai 页面");
          link.href = civitai.link;
          link.target = "_blank";
          link.rel = "noreferrer";
          link.style.textDecoration = "none";
          link.style.display = "inline-block";
          body.appendChild(link);
        }
      } else if (civitai.not_found) {
        body.appendChild(el("div", "lsp-note", "Civitai 上没有与该哈希对应的模型（可能是私有模型或被删除）"));
      } else if (civitai.error) {
        body.appendChild(el("div", "lsp-note err", civitai.error));
      }
    }

    // 操作按钮
    const actions = el("div", "lsp-detail-actions");
    const inStack = this.indexOf(detail.rel) >= 0;
    const addBtn = el("button", `lsp-btn${inStack ? "" : " primary"}`, inStack ? "从堆中移除" : "加入 LoRA 堆");
    addBtn.addEventListener("click", (e) => {
      e.preventDefault();
      this.toggleItem({ rel: detail.rel, stem: detail.stem, folder: detail.folder });
      this.renderDetail();
    });
    actions.appendChild(addBtn);

    const aliasBtn = el("button", "lsp-btn", alias ? "改备注" : "加备注");
    aliasBtn.title = "只改界面显示名，磁盘上的文件名不受影响";
    aliasBtn.addEventListener("click", async (e) => {
      e.preventDefault();
      const next = window.prompt("给这个 LoRA 起个备注名（留空则恢复文件名）", alias || "");
      if (next === null) return;
      await this.commitAlias(detail.rel, next);
      this.renderDetail();
    });
    actions.appendChild(aliasBtn);

    const revealBtn = el("button", "lsp-btn", "打开所在文件夹");
    revealBtn.title = "在系统文件管理器中定位这个 LoRA 文件";
    revealBtn.addEventListener("click", async (e) => {
      e.preventDefault();
      try {
        await postJSON("/reveal", { rel: detail.rel });
        this.setStatus("ok", "已在文件管理器中定位");
      } catch (err) {
        this.setStatus("err", `打开失败: ${err.message}`);
      }
    });
    actions.appendChild(revealBtn);

    if (!civitai || (!civitai.ok && !civitai.not_found)) {
      const fetchBtn = el("button", "lsp-btn", civitai ? "重试 Civitai" : "查询 Civitai");
      fetchBtn.disabled = !!detail.civitaiLoading;
      fetchBtn.addEventListener("click", (e) => {
        e.preventDefault();
        this.loadCivitai(detail.rel, !!civitai);
      });
      actions.appendChild(fetchBtn);
    } else if (civitai.ok) {
      const reloadBtn = el("button", "lsp-btn", "刷新 Civitai");
      reloadBtn.disabled = !!detail.civitaiLoading;
      reloadBtn.addEventListener("click", (e) => {
        e.preventDefault();
        this.loadCivitai(detail.rel, true);
      });
      actions.appendChild(reloadBtn);
    }

    if (detail.civitaiLoading) {
      actions.appendChild(el("span", "lsp-spinner"));
    }

    body.appendChild(actions);
    box.appendChild(body);
    layer.appendChild(box);
  }

  /* ------------------------------------------------------------ 状态栏 */
  setStatus(kind, text) {
    this._status = { kind, text };
    this.paintStatus();
  }

  setTagHint(hint) {
    if (this._tagHint === hint) return;
    this._tagHint = hint;
    this.paintStatus();
  }

  paintStatus() {
    const map = { ok: "ok", warn: "warn", err: "err", idle: "" };
    const status = this._status || { kind: "idle", text: "" };
    while (this.statusEl.firstChild) this.statusEl.removeChild(this.statusEl.firstChild);
    this.statusEl.appendChild(el("span", `dot ${map[status.kind] ?? ""}`));
    this.statusEl.appendChild(el("span", null, status.text));
    if (this._tagHint) {
      const hint = el("span", null, ` · ${this._tagHint}`);
      hint.style.color = "var(--lsp-accent)";
      hint.style.marginLeft = "2px";
      this.statusEl.appendChild(hint);
    }
  }
}

/* ======================================================================
   节点挂载
   ====================================================================== */

function setupNode(node, nodeName) {
  const dataWidget = node.widgets?.find((w) => w.name === "lora_stack");
  if (dataWidget) {
    dataWidget.hidden = true;
    dataWidget.type = "hidden";
    dataWidget.computeSize = () => [0, -4];
    dataWidget.draw = () => {};
    if (dataWidget.inputEl) dataWidget.inputEl.style.display = "none";
    if (dataWidget.element) dataWidget.element.style.display = "none";
  }

  const panel = new LoraStackPanel(node, nodeName);
  node.__lspPanel = panel;

  // 折叠态就是一个紧凑的普通 LoRA 堆
  node.size = [380, panel.collapsedHeight()];
  node.min_size = [300, 90];

  const domWidget = node.addDOMWidget("lora_stack_ui", "lora_stack_ui", panel.root, {
    serialize: false,
    hideOnZoom: false,
    getValue: () => undefined,
    setValue: () => {},
    // 高度完全交给前端布局系统按节点剩余空间分配。
    // 这里是「无限变高」的根因所在，务必保持克制：
    //   1. 不要设 computeSize —— 框架会把它当固定高度，再据此撑高节点
    //   2. 不要设 getMaxHeight —— 它会变成 prefHeight，反向把节点撑到上限
    //   3. 不要在 computeSize/getMinHeight 里读 node.size 反推
    // 节点尺寸只由 applyNodeSize() 里的 setSize 单方面决定。
  });
  domWidget.serialize = false;
  domWidget.options = domWidget.options || {};
  domWidget.options.serialize = false;
  domWidget.serializeValue = async () => undefined;

  // 前端给 DOM widget 的容器只设了 top/height，没设 width，
  // 绝对定位下宽度会收缩到内容宽度：节点拉宽时面板只有一小条，
  // 内容超宽时又顶出节点外。这里按节点宽度显式给定像素宽度。
  const fitWidth = () => {
    const width = Math.max(140, Math.round((node.size?.[0] ?? 380) - 20));
    panel.root.style.width = `${width}px`;
  };
  panel.__fitWidth = fitWidth;
  fitWidth();

  const onResize = node.onResize;
  node.onResize = function (size) {
    const result = onResize?.apply(this, arguments);
    fitWidth();
    const height = size?.[1] ?? this.size?.[1] ?? 0;
    // 只记录用户手动拉大的高度，供下次展开使用；不回写 widget 高度，避免正反馈
    if (!panel.collapsed && height > 300) panel._expandedHeight = height;
    // 兜底：正常绝不会到这个尺寸，出现即说明布局又成回路了，直接压回去
    if (height > 2600 && this.size) {
      this.size[1] = panel.collapsed ? panel.collapsedHeight() : 680;
      setTimeout(() => {
        fitWidth();
        this.setDirtyCanvas?.(true, true);
      }, 0);
    }
    return result;
  };

  // 旧工作流恢复
  queueMicrotask(() => {
    panel.root.classList.add("collapsed");
    panel.restoreFromWidget();
    panel.restoreCollapsed();
  });
}

app.registerExtension({
  name: "LoraStack.Pro",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== "LoraStackPro" && nodeData.name !== "LoraStackProModelOnly") return;

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const result = onNodeCreated?.apply(this, arguments);
      try {
        setupNode(this, nodeData.name);
      } catch (err) {
        console.error("[LoraStackPro] 初始化面板失败:", err);
      }
      return result;
    };

    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function (info) {
      const result = onConfigure?.apply(this, arguments);
      const self = this;
      queueMicrotask(() => {
        try {
          self.__lspPanel?.restoreFromWidget();
          self.__lspPanel?.restoreCollapsed();
        } catch (err) {
          console.error("[LoraStackPro] 恢复数据失败:", err);
        }
      });
      return result;
    };
  },
});

console.log("[LoraStackPro] 前端已加载");
