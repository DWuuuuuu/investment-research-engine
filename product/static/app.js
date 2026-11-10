const MARKET_PAGES = { us: ["macro", "markets", "filter", "quant", "stocks", "etfs", "siglab", "pipeline", "portfolio"], cn: ["cn_macro", "filter", "quant", "ashare", "research", "siglab", "pipeline", "portfolio"] };
const MARKET_KEY = "market_v1";
function initialMarket() {
  try { return localStorage.getItem(MARKET_KEY) || "us"; } catch { return "us"; }
}
const _initMarket = initialMarket();
const state = {
  summary: null,
  page: MARKET_PAGES[_initMarket][0],
  view: "radar",
  macroView: "cycle",
  market: _initMarket,
};

const els = {
  marketCount:       document.querySelector("#marketCount"),
  snapshotCount:     document.querySelector("#snapshotCount"),
  lastUpdate:        document.querySelector("#lastUpdate"),
  sourceCount:       document.querySelector("#sourceCount"),
  categorySelect:    document.querySelector("#categorySelect"),
  sourceSelect:      document.querySelector("#sourceSelect"),
  searchInput:       document.querySelector("#searchInput"),
  viewSelect:        document.querySelector("#viewSelect"),
  lookbackInput:     document.querySelector("#lookbackInput"),
  probInput:         document.querySelector("#probInput"),
  limitInput:        document.querySelector("#limitInput"),
  maxPerEventInput:  document.querySelector("#maxPerEventInput"),
  minDeltaInput:     document.querySelector("#minDeltaInput"),
  minDeltaRow:       document.querySelector("#minDeltaRow"),
  allCategoriesInput:document.querySelector("#allCategoriesInput"),
  statusText:        document.querySelector("#statusText"),
  tableEyebrow:      document.querySelector("#tableEyebrow"),
  tableTitle:        document.querySelector("#tableTitle"),
  tableMeta:         document.querySelector("#tableMeta"),
  content:           document.querySelector("#content"),
  refreshBtn:        document.querySelector("#refreshBtn"),
  refreshBtnText:    document.querySelector("#refreshBtnText"),
  reloadBtn:         document.querySelector("#reloadBtn"),
  detailPanel:       document.querySelector("#detailPanel"),
  detailSource:      document.querySelector("#detailSource"),
  detailTitle:       document.querySelector("#detailTitle"),
  detailBody:        document.querySelector("#detailBody"),
  closeDetailBtn:    document.querySelector("#closeDetailBtn"),
  macroSubNav:       document.querySelector("#macroSubNav"),
  statsGrid:         document.querySelector(".stats-grid"),
  controls:          document.querySelector(".controls"),
  workspace:         document.querySelector(".workspace"),
};

// ── Utilities ─────────────────────────────────────────────────────────────

function setStatus(message, active = false) {
  els.statusText.textContent = message;
  els.statusText.classList.toggle("is-active", active);
}

function setRefreshLoading(isLoading) {
  els.refreshBtn.disabled = isLoading;
  els.refreshBtn.classList.toggle("is-loading", isLoading);
  els.refreshBtnText.textContent = isLoading ? "Refreshing…" : "Refresh live data";
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok && !payload.summary) throw new Error(payload.error || `Request failed: ${response.status}`);
  return payload;
}

// ── Ticker search autocomplete ──────────────────────────────────────────────
// Generic dropdown wired to any text input: debounced search, keyboard nav,
// click-to-select. Used by Stock Screen, ETF Screen, and A-Share inputs.

function setupTickerAutocomplete(inputEl, searchUrlBase, onSelect) {
  if (!inputEl || inputEl.dataset.acBound) return;
  inputEl.dataset.acBound = "1";

  const wrap = inputEl.parentElement;
  if (getComputedStyle(wrap).position === "static") wrap.style.position = "relative";

  const dropdown = document.createElement("div");
  dropdown.className = "ticker-ac-dropdown";
  dropdown.style.display = "none";
  wrap.appendChild(dropdown);

  let debounceTimer = null;
  let activeIndex = -1;
  let currentResults = [];
  let requestSeq = 0;

  function hide() {
    dropdown.style.display = "none";
    dropdown.innerHTML = "";
    activeIndex = -1;
    currentResults = [];
  }

  function highlight() {
    dropdown.querySelectorAll(".ticker-ac-item").forEach((item, i) => {
      item.classList.toggle("active", i === activeIndex);
    });
    const activeEl = dropdown.querySelector(".ticker-ac-item.active");
    if (activeEl) activeEl.scrollIntoView({ block: "nearest" });
  }

  function select(r) {
    hide();
    onSelect(r);
  }

  function show(results) {
    currentResults = results;
    activeIndex = -1;
    if (!results.length) { hide(); return; }
    dropdown.innerHTML = results.map((r, i) => `
      <div class="ticker-ac-item" data-idx="${i}">
        <span class="ticker-ac-symbol">${escapeHtml(r.symbol)}</span>
        <span class="ticker-ac-name">${escapeHtml(r.name)}</span>
        <span class="ticker-ac-type">${escapeHtml(r.exchange || r.type || "")}</span>
      </div>`).join("");
    dropdown.style.left   = `${inputEl.offsetLeft}px`;
    dropdown.style.top    = `${inputEl.offsetTop + inputEl.offsetHeight}px`;
    dropdown.style.width  = `${inputEl.offsetWidth}px`;
    dropdown.style.display = "block";
    dropdown.querySelectorAll(".ticker-ac-item").forEach(item => {
      item.addEventListener("mousedown", e => {
        e.preventDefault(); // fire before input blur
        select(currentResults[Number(item.dataset.idx)]);
      });
    });
  }

  inputEl.addEventListener("input", () => {
    clearTimeout(debounceTimer);
    const q = inputEl.value.trim();
    if (q.length < 1) { hide(); return; }
    const seq = ++requestSeq;
    debounceTimer = setTimeout(async () => {
      try {
        const data = await fetchJson(`${searchUrlBase}${encodeURIComponent(q)}`);
        if (seq !== requestSeq) return; // stale response
        show(data.results || []);
      } catch {
        if (seq === requestSeq) hide();
      }
    }, 220);
  });

  inputEl.addEventListener("keydown", e => {
    if (dropdown.style.display !== "block") return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      activeIndex = Math.min(activeIndex + 1, currentResults.length - 1);
      highlight();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      activeIndex = Math.max(activeIndex - 1, 0);
      highlight();
    } else if (e.key === "Enter" && activeIndex >= 0) {
      e.preventDefault();
      e.stopImmediatePropagation(); // prevent a plain Enter-to-submit listener on the same input from double-firing
      select(currentResults[activeIndex]);
    } else if (e.key === "Escape") {
      hide();
    }
  });

  inputEl.addEventListener("blur", () => setTimeout(hide, 150));
}

function fmt(value) {
  if (value == null || Number.isNaN(Number(value))) return "n/a";
  const n = Number(value);
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (Math.abs(n) >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toFixed(n % 1 === 0 ? 0 : 2);
}

function fmtProb(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function fmtChange(value) {
  if (value == null || Number.isNaN(Number(value))) return null;
  const pts = Number(value) * 100;
  return `${pts >= 0 ? "↑" : "↓"} ${Math.abs(pts).toFixed(1)} pts`;
}

function fmtDate(value) {
  if (!value) return "n/a";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function escapeHtml(v) {
  return String(v || "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}

function probClass(value) {
  if (value == null || Number.isNaN(Number(value))) return "none";
  const p = Number(value);
  if (p >= 0.65) return "high";
  if (p >= 0.35) return "mid";
  return "low";
}

function changeClass(value) {
  if (value == null || Number.isNaN(Number(value))) return "new";
  return Number(value) >= 0 ? "up" : "down";
}

function catPillClass(cat) {
  const safe = (cat || "uncategorized").replace(/[^a-z0-9_]/gi, "_");
  return `cat-${safe}`;
}

// ── Market navigation (US / China A-Share) ──────────────────────────────────

// Pipeline nav tab: hidden until it has at least one position (across either
// market), so an unused workflow tool doesn't clutter the nav. Fail-open (show
// it) on error/unknown rather than hide a real feature due to a network blip.
let _pipelineNonEmpty = true;

function applyMarketLayout() {
  document.querySelectorAll(".market-nav-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.market === state.market);
  });
  document.querySelectorAll(".page-nav-btn, .page-nav-label, .page-nav-sublabel").forEach(btn => {
    btn.style.display = btn.dataset.market === state.market ? "" : "none";
  });
  if (!_pipelineNonEmpty) {
    document.querySelectorAll('.page-nav-btn[data-page="pipeline"]').forEach(btn => { btn.style.display = "none"; });
  }
}

async function _refreshPipelineNavVisibility() {
  try {
    const d = await fetchJson("/api/positions");
    _pipelineNonEmpty = (d.positions || []).length > 0;
  } catch {
    _pipelineNonEmpty = true;
  }
  applyMarketLayout();
}

function switchMarket(market) {
  if (state.market === market) return;
  state.market = market;
  try { localStorage.setItem(MARKET_KEY, market); } catch {}
  applyMarketLayout();
  switchPage(MARKET_PAGES[market][0]);
}

// ── Page navigation ────────────────────────────────────────────────────────

function applyPageLayout() {
  const isMacro   = state.page === "macro";
  const isStocks  = state.page === "stocks";
  const isEtfs    = state.page === "etfs";
  const isAshare  = state.page === "ashare";
  const isCnMacro = state.page === "cn_macro";
  const isFilter  = state.page === "filter";
  const isQuant   = state.page === "quant";
  const isResearch = state.page === "research";
  const isSiglab  = state.page === "siglab";
  const isPipeline = state.page === "pipeline";
  const isPortfolio = state.page === "portfolio";
  // Filter & Quant render their own full-width canvas (workspace hidden in their
  // loaders) — treat them as full-width too so the prediction-market stats grid,
  // sidebar controls & Refresh button don't leak above them.
  const isFullWidth = isMacro || isStocks || isEtfs || isAshare || isCnMacro || isFilter || isQuant || isResearch || isSiglab || isPipeline || isPortfolio;
  // Top-level nav active state
  document.querySelectorAll(".page-nav-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.page === state.page);
  });
  // Workspace: full-width on macro/deploy/stocks, sidebar on markets
  els.workspace.classList.toggle("no-sidebar", isFullWidth);
  els.controls.style.display  = isFullWidth ? "none" : "";
  // Stats grid is prediction-market specific
  els.statsGrid.style.display = isFullWidth ? "none" : "";
  // Macro sub-nav (Cycle / Valuation tabs)
  els.macroSubNav.hidden = !isMacro;
  // Refresh button only makes sense for prediction markets
  els.refreshBtn.style.display = isFullWidth ? "none" : "";
}

async function loadMacroView(view) {
  state.macroView = view;
  // Update sub-tab active state
  document.querySelectorAll(".view-tab").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.view === view);
  });
  setStatus("Loading…", true);
  try {
    if (view === "cycle") {
      const payload = await fetchJson("/api/macro");
      renderCycle(payload);
    } else if (view === "sentiment") {
      const payload = await fetchJson("/api/sentiment");
      renderSentiment(payload);
    } else if (view === "index") {
      await renderMarketIndex();
    } else if (view === "cp3") {
      await renderCp3();
    } else if (view === "fomc") {
      await renderFomcPath();
    } else {
      const payload = await fetchJson("/api/valuation");
      renderValuation(payload);
    }
    setStatus("Ready");
  } catch (err) {
    setStatus(err.message);
  }
}

function switchPage(page) {
  // Hide full-width canvases when leaving those pages
  if (state.page === "stocks"   && page !== "stocks")   hideStocks();
  if (state.page === "etfs"     && page !== "etfs")     hideEtfs();
  if (state.page === "ashare"   && page !== "ashare")   hideAshare();
  if (state.page === "cn_macro" && page !== "cn_macro") hideCnMacro();
  if (state.page === "filter"   && page !== "filter")   hideFilter();
  if (state.page === "quant"    && page !== "quant")    hideQuant();
  if (state.page === "research" && page !== "research") hideResearch();
  if (state.page === "siglab"   && page !== "siglab")   hideSiglab();
  if (state.page === "pipeline" && page !== "pipeline") hidePipeline();
  if (state.page === "portfolio" && page !== "portfolio") hidePortfolio();
  state.page = page;
  els.detailPanel.hidden = true;
  applyPageLayout();
  if (page === "macro") {
    loadMacroView(state.macroView);
  } else if (page === "stocks") {
    loadStocksPage();
  } else if (page === "etfs") {
    loadEtfsPage();
  } else if (page === "ashare") {
    loadAsharePage();
  } else if (page === "cn_macro") {
    loadCnMacroPage();
  } else if (page === "filter") {
    loadFilterPage();
  } else if (page === "quant") {
    loadQuantPage();
  } else if (page === "research") {
    loadResearchPage();
  } else if (page === "siglab") {
    loadSiglabPage();
  } else if (page === "pipeline") {
    loadPipelinePage();
  } else if (page === "portfolio") {
    loadPortfolioPage();
  } else {
    reloadAll();
  }
}

// ── ETF Screener page ───────────────────────────────────────────────────────

const ETF_DEFAULT_WATCHLIST = ["VOO", "VTI", "QQQ", "SMH", "SCHD", "GLD", "VGSH"];
const ETF_WATCHLIST_KEY   = "etf_watchlist_v1";
const ETF_COMPARE_KEY     = "etf_compare_v1";

function etfWatchlist() {
  try { return JSON.parse(localStorage.getItem(ETF_WATCHLIST_KEY)) || ETF_DEFAULT_WATCHLIST; }
  catch { return [...ETF_DEFAULT_WATCHLIST]; }
}
function saveEtfWatchlist(list) { localStorage.setItem(ETF_WATCHLIST_KEY, JSON.stringify(list)); }

function etfCompareList() {
  try { return JSON.parse(localStorage.getItem(ETF_COMPARE_KEY)) || []; }
  catch { return []; }
}
function saveEtfCompare(list) { localStorage.setItem(ETF_COMPARE_KEY, JSON.stringify(list)); }

function getEtfCanvas() {
  let c = document.getElementById("etfCanvas");
  if (!c) { c = document.createElement("div"); c.id = "etfCanvas"; document.querySelector("main").appendChild(c); }
  return c;
}

function hideEtfs() {
  const c = document.getElementById("etfCanvas");
  if (c) c.style.display = "none";
  els.workspace.style.display = "";
}

// ── ETF page shell ──────────────────────────────────────────────────────────
function loadEtfsPage() {
  const canvas = getEtfCanvas();
  canvas.style.display = "";
  els.workspace.style.display = "none";

  canvas.innerHTML = `
  <div class="etf-page">

    <!-- Search + add -->
    <div class="etf-topbar">
      <div class="etf-topbar-left">
        <div class="etf-page-title">ETF Screener</div>
        <div class="etf-page-sub">Score any ETF on cost, size, fund quality, returns, and structure</div>
      </div>
      <div class="etf-search-row">
        <input id="etfSearchInput" class="etf-search-input" type="text"
               placeholder="Add ETF to watchlist…" autocomplete="off"
               spellcheck="false" maxlength="12">
        <button id="etfAddBtn" class="etf-add-btn">+ Add</button>
        <button id="etfCompareBtn" class="etf-compare-btn">Compare</button>
      </div>
    </div>

    <!-- Watchlist grid -->
    <div class="etf-section-header">
      <span class="etf-section-title">Your Watchlist</span>
      <span id="etfWatchlistCount" class="etf-section-count"></span>
    </div>
    <div id="etfGrid" class="etf-grid"></div>

    <!-- Compare panel (hidden by default) -->
    <div id="etfComparePanel" class="etf-compare-panel" style="display:none"></div>

    <!-- Detail panel -->
    <div id="etfDetailPanel" class="etf-detail-panel" style="display:none"></div>

  </div>`;

  // Wire up events
  setupTickerAutocomplete(document.getElementById("etfSearchInput"), "/api/us_search?q=", r => {
    document.getElementById("etfSearchInput").value = r.symbol;
    etfAddToWatchlist();
  });
  document.getElementById("etfAddBtn").addEventListener("click", etfAddToWatchlist);
  document.getElementById("etfSearchInput").addEventListener("keydown", e => {
    if (e.key === "Enter") etfAddToWatchlist();
  });
  document.getElementById("etfCompareBtn").addEventListener("click", toggleEtfCompare);

  renderEtfGrid();
}

// ── Watchlist grid ──────────────────────────────────────────────────────────
function renderEtfGrid() {
  const list = etfWatchlist();
  const grid = document.getElementById("etfGrid");
  const countEl = document.getElementById("etfWatchlistCount");
  if (!grid) return;
  countEl.textContent = `${list.length} funds`;

  // Render skeleton cards immediately
  grid.innerHTML = list.map(ticker => `
    <div class="etf-card etf-card-loading" id="etfcard-${ticker}" data-ticker="${ticker}">
      <div class="etf-card-ticker">${escapeHtml(ticker)}</div>
      <div class="etf-card-name etf-card-shimmer">Loading…</div>
      <div class="etf-card-metrics etf-card-shimmer"></div>
      <button class="etf-card-remove" data-ticker="${ticker}" title="Remove">×</button>
    </div>`).join("");

  // Remove buttons
  grid.querySelectorAll(".etf-card-remove").forEach(btn => {
    btn.addEventListener("click", e => {
      e.stopPropagation();
      etfRemoveFromWatchlist(btn.dataset.ticker);
    });
  });

  // Fetch each card's data (fires in background; each updates independently)
  list.forEach(ticker => fetchEtfCard(ticker));
}

async function fetchEtfCard(ticker) {
  const card = document.getElementById(`etfcard-${ticker}`);
  if (!card) return;
  try {
    const d = await fetchJson(`/api/screener?ticker=${encodeURIComponent(ticker)}`);
    if (d.error || d.quote_type === undefined) {
      card.className = "etf-card etf-card-error";
      card.innerHTML = `
        <div class="etf-card-ticker">${escapeHtml(ticker)}</div>
        <div class="etf-card-name">⚠ ${escapeHtml(d.error || "Not an ETF")}</div>
        <button class="etf-card-remove" data-ticker="${ticker}" title="Remove">×</button>`;
      card.querySelector(".etf-card-remove").addEventListener("click", e => {
        e.stopPropagation(); etfRemoveFromWatchlist(ticker);
      });
      return;
    }
    renderEtfCardContent(card, d);
    card.addEventListener("click", () => openEtfDetail(d));
  } catch (err) {
    card.className = "etf-card etf-card-error";
    card.innerHTML = `<div class="etf-card-ticker">${escapeHtml(ticker)}</div>
      <div class="etf-card-name">⚠ ${escapeHtml(err.message)}</div>`;
  }
}

function renderEtfCardContent(card, d) {
  const vd = d.verdict || {};
  const vCls = { pass: "etf-v-pass", partial: "etf-v-partial", fail: "etf-v-fail", unknown: "etf-v-na" };
  const vIcon = { pass: "✓", partial: "~", fail: "✗", unknown: "—" };
  const vcls = vCls[vd.overall] || "etf-v-na";
  const vico = vIcon[vd.overall] || "—";

  const er = d.expense_ratio != null ? `${d.expense_ratio}%` : "—";
  const ret5 = d.ret_5y != null ? `${d.ret_5y}%` : "—";
  const ret3 = d.ret_3y != null ? `${d.ret_3y}%` : "—";
  const yld  = d.yield_pct != null ? `${d.yield_pct}%` : "—";

  // ER colouring
  const erVCls = d.er_verdict === "excellent" ? "etf-metric-green"
               : d.er_verdict === "acceptable" ? "etf-metric-amber"
               : "etf-metric-red";

  card.className = `etf-card ${vcls}`;
  card.dataset.loaded = "1";
  card.innerHTML = `
    <div class="etf-card-header-row">
      <div class="etf-card-ticker">${escapeHtml(d.ticker)}</div>
      <span class="etf-card-badge ${vcls}">${vico} ${vd.passed ?? "?"}/${vd.scorable ?? "?"}</span>
    </div>
    <div class="etf-card-name">${escapeHtml(d.name)}</div>
    <div class="etf-card-cat">${escapeHtml(d.category || "—")} · ${escapeHtml(d.fund_family || "—")}</div>
    <div class="etf-card-divider"></div>
    <div class="etf-card-metrics">
      <div class="etf-metric"><span class="etf-metric-label">AUM</span><span class="etf-metric-val">${escapeHtml(d.aum_fmt || "—")}</span></div>
      <div class="etf-metric"><span class="etf-metric-label">ER</span><span class="etf-metric-val ${erVCls}">${er}</span></div>
      <div class="etf-metric"><span class="etf-metric-label">5yr</span><span class="etf-metric-val">${ret5}</span></div>
      <div class="etf-metric"><span class="etf-metric-label">3yr</span><span class="etf-metric-val">${ret3}</span></div>
      <div class="etf-metric"><span class="etf-metric-label">Yield</span><span class="etf-metric-val">${yld}</span></div>
      <div class="etf-metric"><span class="etf-metric-label">Age</span><span class="etf-metric-val">${d.fund_age_years != null ? d.fund_age_years + "y" : "—"}</span></div>
    </div>
    <button class="etf-card-remove" data-ticker="${d.ticker}" title="Remove">×</button>`;

  card.querySelector(".etf-card-remove").addEventListener("click", e => {
    e.stopPropagation(); etfRemoveFromWatchlist(d.ticker);
  });
}

// ── Watchlist management ────────────────────────────────────────────────────
async function etfAddToWatchlist() {
  const input = document.getElementById("etfSearchInput");
  if (!input) return;
  const ticker = input.value.trim().toUpperCase();
  if (!ticker) return;

  const list = etfWatchlist();
  if (list.includes(ticker)) {
    input.value = "";
    // Scroll to existing card
    const existing = document.getElementById(`etfcard-${ticker}`);
    if (existing) existing.scrollIntoView({ behavior: "smooth", block: "center" });
    return;
  }

  list.push(ticker);
  saveEtfWatchlist(list);
  input.value = "";

  // Append skeleton card then fetch
  const grid = document.getElementById("etfGrid");
  const el = document.createElement("div");
  el.className = "etf-card etf-card-loading";
  el.id = `etfcard-${ticker}`;
  el.dataset.ticker = ticker;
  el.innerHTML = `<div class="etf-card-ticker">${escapeHtml(ticker)}</div>
    <div class="etf-card-name etf-card-shimmer">Loading…</div>
    <button class="etf-card-remove" data-ticker="${ticker}" title="Remove">×</button>`;
  el.querySelector(".etf-card-remove").addEventListener("click", e => {
    e.stopPropagation(); etfRemoveFromWatchlist(ticker);
  });
  grid.appendChild(el);
  document.getElementById("etfWatchlistCount").textContent = `${list.length} funds`;

  await fetchEtfCard(ticker);
}

function etfRemoveFromWatchlist(ticker) {
  const list = etfWatchlist().filter(t => t !== ticker);
  saveEtfWatchlist(list);
  const card = document.getElementById(`etfcard-${ticker}`);
  if (card) card.remove();
  document.getElementById("etfWatchlistCount").textContent = `${list.length} funds`;

  // Close detail if it's for this ticker
  const dp = document.getElementById("etfDetailPanel");
  if (dp && dp.dataset.ticker === ticker) dp.style.display = "none";
}

// ── Detail panel ─────────────────────────────────────────────────────────────
function openEtfDetail(d) {
  const dp = document.getElementById("etfDetailPanel");
  if (!dp) return;
  dp.style.display = "";
  dp.dataset.ticker = d.ticker;
  renderEtfDetail(d, dp);
  dp.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderEtfDetail(d, container) {
  const vd = d.verdict || {};
  const checks = d.checks || [];

  const vCls = { pass: "sc-v-pass", partial: "sc-v-partial", fail: "sc-v-fail", unknown: "sc-v-na" };
  const vIcon = { pass: "✓", partial: "~", fail: "✗", unknown: "—" };
  function vBadge(key, lbl) {
    const cls = vCls[key] || "sc-v-na";
    const ico = vIcon[key] || "—";
    return `<span class="sc-verdict-badge ${cls}">${ico} ${lbl || key}</span>`;
  }

  // ── Header ─────────────────────────────────────────────────────────────────
  const header = `
  <div class="etf-detail-header">
    <div class="etf-detail-title-row">
      <span class="etf-detail-ticker">${escapeHtml(d.ticker)}</span>
      <span class="etf-detail-name">${escapeHtml(d.name)}</span>
      <span class="sc-etf-badge">ETF</span>
    </div>
    <div class="etf-detail-meta">
      <span class="sc-chip">${escapeHtml(d.category || "—")}</span>
      <span class="sc-chip sc-chip-dim">${escapeHtml(d.fund_family || "—")}</span>
      <span class="sc-chip sc-chip-dim">Since ${escapeHtml(d.inception_date || "—")}</span>
      <span class="sc-chip sc-chip-dim">AUM ${escapeHtml(d.aum_fmt || "—")}</span>
    </div>
    <div class="etf-detail-right">
      <span class="etf-detail-price">$${d.price != null ? d.price.toFixed(2) : "—"}</span>
      ${vBadge(vd.overall || "unknown", `${vd.passed ?? "?"}/${vd.scorable ?? "?"} passed`)}
    </div>
  </div>`;

  // ── Scorecard table ─────────────────────────────────────────────────────────
  const barPct = vd.scorable ? Math.round(vd.passed / vd.scorable * 100) : 0;
  const checkRows = checks.map(c => {
    const icon = c.pass === true ? "✓" : c.pass === false ? "✗" : "—";
    const cls  = c.pass === true ? "sc-pass" : c.pass === false ? "sc-fail" : "sc-na";
    const thr  = `${c.direction === "above" ? ">" : "<"} ${c.threshold}${c.unit}`;
    return `<tr class="${cls}">
      <td class="sc-row-icon">${icon}</td>
      <td class="sc-row-name">${escapeHtml(c.name)}${c.period ? `<span class="sc-row-period">${escapeHtml(c.period)}</span>` : ""}</td>
      <td class="sc-row-val">${escapeHtml(c.display)}</td>
      <td class="sc-row-thr">${escapeHtml(thr)}</td>
      <td class="sc-row-note">${escapeHtml(c.note)}${c.caveat ? `<span class="sc-row-caveat">⚠ ${escapeHtml(c.caveat)}</span>` : ""}</td>
    </tr>`;
  }).join("");

  const scorecard = `
  <div class="sc-card">
    <div class="sc-card-header">
      <div class="sc-card-title">5-Dimension Scorecard</div>
      ${vBadge(vd.overall || "unknown")}
      <span class="sc-score">${vd.passed ?? 0}/${vd.scorable ?? "?"} passed</span>
    </div>
    <div class="sc-g1-bar-wrap"><div class="sc-g1-bar" style="width:${barPct}%"></div></div>
    <table class="sc-table">
      <thead><tr><th></th><th>Dimension</th><th>Value</th><th>Threshold</th><th>Context</th></tr></thead>
      <tbody>${checkRows}</tbody>
    </table>
  </div>`;

  // ── Performance & cost ──────────────────────────────────────────────────────
  const erCls = d.er_verdict === "excellent" ? "sc-v-pass"
              : d.er_verdict === "acceptable" ? "sc-v-partial" : "sc-v-fail";
  const retCls = r => r == null ? "" : r > 10 ? "sc-v-pass" : r > 0 ? "sc-v-partial" : "sc-v-fail";

  function perfCard(label, val, unit, cls, sub) {
    return `<div class="sc-val-card ${cls || ""}">
      <div class="sc-val-label">${label}</div>
      <div class="sc-val-value">${val != null ? val : "—"}<span class="sc-val-unit">${unit}</span></div>
      ${sub ? `<div class="sc-val-thr">${escapeHtml(sub)}</div>` : ""}
    </div>`;
  }

  const perf = `
  <div class="sc-card">
    <div class="sc-card-header"><div class="sc-card-title">Performance & Cost</div></div>
    <div class="sc-val-grid sc-etf-perf-grid">
      <div class="sc-val-card ${erCls}">
        <div class="sc-val-label">Expense Ratio</div>
        <div class="sc-val-value">${d.expense_ratio != null ? d.expense_ratio : "—"}<span class="sc-val-unit">%</span></div>
        <div class="sc-val-verdict">${d.er_verdict || "—"}</div>
        <div class="sc-val-thr">Category benchmark ≤${d.er_good_threshold}% · flag >${d.er_flag_threshold}%</div>
      </div>
      ${perfCard("YTD Return",    d.ret_ytd, "%", retCls(d.ret_ytd))}
      ${perfCard("3-Year Avg",    d.ret_3y,  "%", retCls(d.ret_3y))}
      ${perfCard("5-Year Avg",    d.ret_5y,  "%", retCls(d.ret_5y))}
      ${perfCard("Dividend Yield",d.yield_pct, "%", "")}
      ${perfCard("Turnover",      d.turnover_rate, "%", "",
        d.turnover_rate > 50 ? "High — prefer tax-advantaged account" :
        d.turnover_rate > 10 ? "Moderate" : "Excellent tax efficiency")}
    </div>
  </div>`;

  // ── Top holdings ────────────────────────────────────────────────────────────
  let holdHtml = "";
  if ((d.top_holdings || []).length > 0) {
    const maxPct = Math.max(...d.top_holdings.map(h => h.pct));
    const rows = d.top_holdings.map((h, i) => `
      <div class="sc-holding-row">
        <span class="sc-holding-rank">${i + 1}</span>
        <span class="sc-holding-sym">${escapeHtml(h.symbol)}</span>
        <span class="sc-holding-name">${escapeHtml(h.name)}</span>
        <div class="sc-holding-bar-wrap">
          <div class="sc-holding-bar" style="width:${Math.round(h.pct/maxPct*100)}%"></div>
        </div>
        <span class="sc-holding-pct">${h.pct.toFixed(2)}%</span>
      </div>`).join("");
    const concCls = (d.top10_weight || 0) > 55 ? "sc-v-fail"
                  : (d.top10_weight || 0) > 35 ? "sc-v-partial" : "sc-v-pass";
    holdHtml = `<div class="sc-card">
      <div class="sc-card-header">
        <div class="sc-card-title">Top Holdings</div>
        <span class="sc-verdict-badge ${concCls}">Top-10: ${d.top10_weight != null ? d.top10_weight + "%" : "—"}</span>
      </div>
      <div class="sc-holdings-list">${rows}</div>
    </div>`;
  }

  // ── Sector breakdown ────────────────────────────────────────────────────────
  let secHtml = "";
  if (d.sector_weights && Object.keys(d.sector_weights).length > 0) {
    const sorted = Object.entries(d.sector_weights).sort((a, b) => b[1] - a[1]);
    const maxW = sorted[0][1];
    const rows = sorted.map(([s, w]) => {
      const lbl = s.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
      return `<div class="sc-sector-row">
        <span class="sc-sector-name">${escapeHtml(lbl)}</span>
        <div class="sc-holding-bar-wrap">
          <div class="sc-holding-bar sc-sector-bar" style="width:${Math.round(w/maxW*100)}%"></div>
        </div>
        <span class="sc-holding-pct">${w.toFixed(1)}%</span>
      </div>`;
    }).join("");
    secHtml = `<div class="sc-card">
      <div class="sc-card-header"><div class="sc-card-title">Sector Weights</div></div>
      <div class="sc-holdings-list">${rows}</div>
    </div>`;
  }

  // ── Notes textarea ──────────────────────────────────────────────────────────
  const noteKey = `etf_note_${d.ticker}`;
  const notes = `<div class="sc-card">
    <div class="sc-card-header"><div class="sc-card-title">Investment Notes</div></div>
    <p class="sc-card-sub">Role in portfolio, thesis, risks, sizing target.</p>
    <textarea id="etfNoteArea" class="sc-moat-note" rows="3"
              placeholder="e.g. SMH — satellite 5% of NW, 10Y+ AI infrastructure thesis. Acceptable high concentration given semiconductor cycle conviction…">${escapeHtml(localStorage.getItem(noteKey) || "")}</textarea>
  </div>`;

  const priceHtml = renderPriceChartCard();
  const momHtml   = "";  // merged into the Price History chart
  container.innerHTML = `<div class="etf-detail-inner">${header}${priceHtml}${scorecard}${holdHtml}${secHtml}${perf}${momHtml}${notes}</div>`;

  setupPriceChart(container, d.price_history || [], d.beta ?? null, d.ticker, d.benchmark_history || [], d.benchmark_symbol, d.benchmark_name);
  // setupMomentum removed — oscillators now live in the Price History chart

  const noteEl = document.getElementById("etfNoteArea");
  if (noteEl) noteEl.addEventListener("input", () => localStorage.setItem(noteKey, noteEl.value));
}

// ── Comparison table ─────────────────────────────────────────────────────────
let etfDetailCache = {};  // ticker → data

function cacheEtfData(d) { if (d && d.ticker) etfDetailCache[d.ticker] = d; }

// When a card loads, cache its data
const _origFetchCard = fetchEtfCard;
async function fetchEtfCard(ticker) {
  const card = document.getElementById(`etfcard-${ticker}`);
  if (!card) return;
  try {
    const d = await fetchJson(`/api/screener?ticker=${encodeURIComponent(ticker)}`);
    if (!d.error && d.quote_type) {
      cacheEtfData(d);
      renderEtfCardContent(card, d);
      card.addEventListener("click", () => { cacheEtfData(d); openEtfDetail(d); });
    } else {
      card.className = "etf-card etf-card-error";
      card.innerHTML = `<div class="etf-card-ticker">${escapeHtml(ticker)}</div>
        <div class="etf-card-name">⚠ ${escapeHtml(d.error || "Not an ETF")}</div>
        <button class="etf-card-remove" data-ticker="${ticker}" title="Remove">×</button>`;
      card.querySelector(".etf-card-remove").addEventListener("click", e => {
        e.stopPropagation(); etfRemoveFromWatchlist(ticker);
      });
    }
  } catch (err) {
    card.className = "etf-card etf-card-error";
    card.innerHTML = `<div class="etf-card-ticker">${escapeHtml(ticker)}</div>
      <div class="etf-card-name">⚠ ${escapeHtml(err.message)}</div>`;
  }
}

function toggleEtfCompare() {
  const panel = document.getElementById("etfComparePanel");
  if (!panel) return;
  const isVisible = panel.style.display !== "none";
  if (isVisible) { panel.style.display = "none"; return; }

  const list = etfWatchlist();
  const loaded = list.filter(t => etfDetailCache[t]);
  if (loaded.length < 2) {
    panel.style.display = "";
    panel.innerHTML = `<div class="etf-compare-wait">
      ⏳ Wait for at least 2 funds in your watchlist to finish loading, then click Compare again.
    </div>`;
    return;
  }

  renderCompareTable(panel, loaded.map(t => etfDetailCache[t]).filter(Boolean));
  panel.style.display = "";
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderCompareTable(container, etfs) {
  const rows = [
    { label: "Category",          fn: d => d.category || "—",              fmt: null },
    { label: "Fund Family",        fn: d => d.fund_family || "—",           fmt: null },
    { label: "AUM",                fn: d => d.aum_fmt || "—",               fmt: null },
    { label: "Expense Ratio",      fn: d => d.expense_ratio,                fmt: v => v != null ? v + "%" : "—",
      score: d => d.er_verdict === "excellent" ? "cmp-green" : d.er_verdict === "acceptable" ? "cmp-amber" : "cmp-red" },
    { label: "Fund Age",           fn: d => d.fund_age_years,               fmt: v => v != null ? v + "y" : "—",
      score: d => (d.fund_age_years || 0) >= 10 ? "cmp-green" : (d.fund_age_years || 0) >= 5 ? "cmp-amber" : "cmp-red" },
    { label: "Turnover",           fn: d => d.turnover_rate,                fmt: v => v != null ? v + "%" : "—",
      score: d => (d.turnover_rate || 0) < 10 ? "cmp-green" : (d.turnover_rate || 0) < 50 ? "cmp-amber" : "cmp-red" },
    { label: "Top-10 Conc.",       fn: d => d.top10_weight,                 fmt: v => v != null ? v + "%" : "—",
      score: d => (d.top10_weight || 0) < 35 ? "cmp-green" : (d.top10_weight || 0) < 55 ? "cmp-amber" : "cmp-red" },
    { label: "YTD Return",         fn: d => d.ret_ytd,                      fmt: v => v != null ? v + "%" : "—",
      score: d => (d.ret_ytd || 0) > 10 ? "cmp-green" : (d.ret_ytd || 0) > 0 ? "cmp-amber" : "cmp-red" },
    { label: "3-Year Avg Return",  fn: d => d.ret_3y,                       fmt: v => v != null ? v + "%" : "—",
      score: d => (d.ret_3y || 0) > 15 ? "cmp-green" : (d.ret_3y || 0) > 7 ? "cmp-amber" : "cmp-red" },
    { label: "5-Year Avg Return",  fn: d => d.ret_5y,                       fmt: v => v != null ? v + "%" : "—",
      score: d => (d.ret_5y || 0) > 12 ? "cmp-green" : (d.ret_5y || 0) > 6 ? "cmp-amber" : "cmp-red" },
    { label: "Dividend Yield",     fn: d => d.yield_pct,                    fmt: v => v != null ? v + "%" : "—" },
    { label: "Score",              fn: d => `${(d.verdict||{}).passed ?? "?"}/${(d.verdict||{}).scorable ?? "?"}`,
      fmt: null,
      score: d => (d.verdict||{}).overall === "pass" ? "cmp-green"
                : (d.verdict||{}).overall === "partial" ? "cmp-amber" : "cmp-red" },
  ];

  const headerCells = etfs.map(d =>
    `<th class="cmp-etf-header" data-ticker="${d.ticker}">
      <div class="cmp-etf-tick">${escapeHtml(d.ticker)}</div>
      <div class="cmp-etf-fam">${escapeHtml(d.fund_family || "")}</div>
    </th>`).join("");

  const bodyRows = rows.map(row => {
    const cells = etfs.map(d => {
      const raw = row.fn(d);
      const display = row.fmt ? row.fmt(raw) : String(raw);
      const cls = row.score ? row.score(d) : "";
      return `<td class="cmp-cell ${cls}">${escapeHtml(display)}</td>`;
    }).join("");
    return `<tr><td class="cmp-row-label">${escapeHtml(row.label)}</td>${cells}</tr>`;
  }).join("");

  container.innerHTML = `
  <div class="sc-card etf-compare-card">
    <div class="sc-card-header">
      <div class="sc-card-title">Side-by-Side Comparison</div>
      <button id="etfCloseCompare" class="etf-close-compare">✕ Close</button>
    </div>
    <div class="etf-compare-scroll">
      <table class="cmp-table">
        <thead><tr><th class="cmp-label-header">Metric</th>${headerCells}</tr></thead>
        <tbody>${bodyRows}</tbody>
      </table>
    </div>
  </div>`;

  document.getElementById("etfCloseCompare").addEventListener("click", () => {
    container.style.display = "none";
  });

  // Click header → open detail
  container.querySelectorAll(".cmp-etf-header").forEach(th => {
    th.style.cursor = "pointer";
    th.addEventListener("click", () => {
      const d = etfDetailCache[th.dataset.ticker];
      if (d) openEtfDetail(d);
    });
  });
}

// ── Stocks page ────────────────────────────────────────────────────────────

function getStocksCanvas() {
  let canvas = document.getElementById("stocksCanvas");
  if (!canvas) {
    canvas = document.createElement("div");
    canvas.id = "stocksCanvas";
    document.querySelector("main").appendChild(canvas);
  }
  return canvas;
}

const SC_WATCHLIST_KEY     = "sc_watchlist_v1";
const SC_WATCHLIST_DEFAULT = ["META", "NEM", "SCHM", "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN"];

function scWatchlist() {
  try { return JSON.parse(localStorage.getItem(SC_WATCHLIST_KEY)) || [...SC_WATCHLIST_DEFAULT]; }
  catch { return [...SC_WATCHLIST_DEFAULT]; }
}
function saveScWatchlist(list) { localStorage.setItem(SC_WATCHLIST_KEY, JSON.stringify(list)); }

function renderScWatchlist() {
  const wrap = document.getElementById("scWatchlistPills");
  if (!wrap) return;
  const list = [...scWatchlist()].sort();
  const countEl = document.getElementById("scWatchlistCount");
  if (countEl) countEl.textContent = list.length ? `${list.length}` : "";
  if (!list.length) {
    wrap.innerHTML = `<span class="wl-empty">No tickers yet — add one above.</span>`;
    return;
  }
  wrap.innerHTML = list.map(t => `
    <span class="wl-chip" data-ticker="${escapeHtml(t)}" role="button" tabindex="0">
      <span class="wl-chip-t">${escapeHtml(t)}</span>
      <span class="wl-chip-x" data-ticker="${escapeHtml(t)}" title="Remove">×</span>
    </span>`).join("");

  wrap.querySelectorAll(".wl-chip").forEach(chip => {
    chip.addEventListener("click", e => {
      if (e.target.classList.contains("wl-chip-x")) {
        e.stopPropagation();
        saveScWatchlist(scWatchlist().filter(t => t !== e.target.dataset.ticker));
        renderScWatchlist();
        return;
      }
      document.getElementById("scTickerInput").value = chip.dataset.ticker;
      runScreen();
    });
  });
}

function scAddToWatchlist() {
  const addInput = document.getElementById("scWatchlistAdd");
  if (!addInput) return;
  const ticker = addInput.value.trim().toUpperCase().replace(/[^A-Z0-9.-]/g, "");
  if (!ticker) return;
  const list = scWatchlist();
  if (!list.includes(ticker)) {
    list.push(ticker);
    saveScWatchlist(list);
    renderScWatchlist();
  }
  addInput.value = "";
}

function loadStocksPage() {
  const canvas = getStocksCanvas();
  canvas.style.display = "";
  els.workspace.style.display = "none";

  // Only build the shell once; keep any existing result
  if (!canvas.querySelector(".sc-search-bar")) {
    canvas.innerHTML = `
    <div class="sc-page">
      <div class="sc-search-bar">
        <div class="sc-search-title">Stock Screener</div>
        <div class="sc-search-sub">Run a ticker through the 3-Gate quality + valuation system</div>
        <div class="sc-search-row">
          <input id="scTickerInput" class="sc-input" type="text"
                 placeholder="Enter ticker — e.g. META, NEM, GOOGL" autocomplete="off"
                 spellcheck="false" maxlength="12">
          <button id="scScreenBtn" class="sc-btn">Screen</button>
        </div>
        <div class="sc-watchlist-panel">
          <div class="sc-watchlist-head">
            <span class="sc-watchlist-label">Watchlist</span>
            <span id="scWatchlistCount" class="wl-count"></span>
            <div class="sc-watchlist-add-wrap">
              <input id="scWatchlistAdd" class="sc-watchlist-add-input" type="text"
                     placeholder="Add ticker…" maxlength="12" spellcheck="false" autocomplete="off">
              <button id="scWatchlistAddBtn" class="sc-watchlist-add-btn">+</button>
            </div>
          </div>
          <div id="scWatchlistPills" class="sc-watchlist-pills"></div>
        </div>
      </div>
      <div id="scResult"></div>
    </div>`;

    setupTickerAutocomplete(document.getElementById("scTickerInput"), "/api/us_search?q=", r => {
      document.getElementById("scTickerInput").value = r.symbol;
      runScreen();
    });
    document.getElementById("scScreenBtn").addEventListener("click", runScreen);
    document.getElementById("scTickerInput").addEventListener("keydown", e => {
      if (e.key === "Enter") runScreen();
    });
    document.getElementById("scWatchlistAddBtn").addEventListener("click", scAddToWatchlist);
    document.getElementById("scWatchlistAdd").addEventListener("keydown", e => {
      if (e.key === "Enter") scAddToWatchlist();
    });
  }

  renderScWatchlist();
}

function hideStocks() {
  const canvas = document.getElementById("stocksCanvas");
  if (canvas) canvas.style.display = "none";
  els.workspace.style.display = "";
}

async function runScreen() {
  const ticker = document.getElementById("scTickerInput").value.trim().toUpperCase();
  if (!ticker) return;
  const resultEl = document.getElementById("scResult");
  const btn = document.getElementById("scScreenBtn");
  btn.disabled = true;
  btn.textContent = "Screening…";
  resultEl.innerHTML = `<div class="sc-loading">Fetching data for <strong>${escapeHtml(ticker)}</strong>…</div>`;
  try {
    const data = await fetchJson(`/api/screener?ticker=${encodeURIComponent(ticker)}`);
    if (data.error) {
      resultEl.innerHTML = `<div class="sc-error">⚠ ${escapeHtml(data.error)}</div>`;
    } else {
      renderScreenResult(data, resultEl);
    }
  } catch (err) {
    resultEl.innerHTML = `<div class="sc-error">⚠ ${escapeHtml(err.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Screen";
  }
}

// ── A-Share Screener page ───────────────────────────────────────────────────
// Backend (ashare_screener.py) returns the same shape as /api/screener, so we
// reuse renderScreenResult() directly — no duplicate frontend rendering code.

function getAshareCanvas() {
  let canvas = document.getElementById("ashareCanvas");
  if (!canvas) {
    canvas = document.createElement("div");
    canvas.id = "ashareCanvas";
    document.querySelector("main").appendChild(canvas);
  }
  return canvas;
}

const ASHARE_WATCHLIST_KEY     = "ashare_watchlist_v1";
const ASHARE_WATCHLIST_DEFAULT = ["600519", "300750", "000001", "601318", "000858", "600036"];

function ashareWatchlist() {
  try { return JSON.parse(localStorage.getItem(ASHARE_WATCHLIST_KEY)) || [...ASHARE_WATCHLIST_DEFAULT]; }
  catch { return [...ASHARE_WATCHLIST_DEFAULT]; }
}
function saveAshareWatchlist(list) { localStorage.setItem(ASHARE_WATCHLIST_KEY, JSON.stringify(list)); }

// A six-digit code is not something anyone recognises on sight, so every pill
// carries the company name next to it. Names are kept in localStorage: they
// change about never, and a watchlist that renders blank for a second on every
// page load is worse than one that shows a slightly stale name.
const ASHARE_NAME_KEY = "ashare_names_v1";

function ashareNames() {
  try { return JSON.parse(localStorage.getItem(ASHARE_NAME_KEY)) || {}; }
  catch { return {}; }
}

async function fetchAshareNames(codes) {
  const known = ashareNames();
  const missing = [...new Set(codes)].filter(c => c && !known[c]);
  if (!missing.length) return false;
  try {
    const d = await fetchJson(`/api/ashare_names?codes=${encodeURIComponent(missing.join(","))}`);
    const found = d && d.names ? d.names : {};
    if (!Object.keys(found).length) return false;
    localStorage.setItem(ASHARE_NAME_KEY, JSON.stringify({ ...known, ...found }));
    return true;
  } catch { return false; }       // the code alone still works as a pill
}

function renderAshareWatchlist() {
  const wrap = document.getElementById("ashareWatchlistPills");
  if (!wrap) return;
  const list = ashareWatchlist();
  const names = ashareNames();
  if (!list.length) {
    wrap.innerHTML = `<span class="wl-empty">No codes yet — add one above.</span>`;
    return;
  }
  // Same chip as the US watchlist, which was already styled; the A-share one
  // was using a class with no CSS behind it at all.
  wrap.innerHTML = list.map(t => {
    const name = names[t] || names[t.split(".")[0]] || "";
    return `
    <span class="wl-chip${name ? " wl-chip-named" : ""}" data-ticker="${escapeHtml(t)}"
          role="button" tabindex="0" title="${escapeHtml(name ? `${t} · ${name}` : t)}">
      <span class="wl-chip-t">${escapeHtml(t)}</span>
      ${name ? `<span class="wl-chip-name">${escapeHtml(name)}</span>` : ""}
      <span class="wl-chip-x" data-ticker="${escapeHtml(t)}" title="Remove">×</span>
    </span>`;
  }).join("");

  // Fill in whatever is still unnamed, then redraw once — never in a loop, since
  // a code the vendor does not know stays unnamed and would redraw forever.
  fetchAshareNames(list).then(gained => { if (gained) renderAshareWatchlist(); });

  wrap.querySelectorAll(".wl-chip").forEach(chip => {
    chip.addEventListener("click", e => {
      if (e.target.classList.contains("wl-chip-x")) {
        e.stopPropagation();
        saveAshareWatchlist(ashareWatchlist().filter(t => t !== e.target.dataset.ticker));
        renderAshareWatchlist();
        return;
      }
      document.getElementById("ashareTickerInput").value = chip.dataset.ticker;
      runAshareScreen();
    });
  });
}

async function ashareAddToWatchlist() {
  const addInput = document.getElementById("ashareWatchlistAdd");
  if (!addInput) return;
  let ticker = addInput.value.trim();
  if (!ticker) return;
  // The box takes a name as well as a code. Store the code either way, so the
  // watchlist holds one kind of thing and every chip can be labelled.
  if (!/^\d{6}(\.(SH|SZ|BJ))?$/i.test(ticker)) {
    try {
      const d = await fetchJson(`/api/ashare_search?q=${encodeURIComponent(ticker)}`);
      const hit = (d.results || [])[0];
      if (hit && hit.symbol) ticker = hit.symbol;
    } catch { /* keep what was typed; it still resolves when screened */ }
  }
  const list = ashareWatchlist();
  if (!list.includes(ticker)) {
    list.push(ticker);
    saveAshareWatchlist(list);
    renderAshareWatchlist();
  }
  addInput.value = "";
}

function loadAsharePage() {
  const canvas = getAshareCanvas();
  canvas.style.display = "";
  els.workspace.style.display = "none";

  if (!canvas.querySelector(".sc-search-bar")) {
    canvas.innerHTML = `
    <div class="sc-page">
      <div class="sc-search-bar">
        <div class="sc-search-title">A-Share Screener</div>
        <div class="sc-search-sub">Mainland China equities (SSE / SZSE) — same 3-Gate quality + valuation system, powered by Tushare</div>
        <div class="sc-search-row">
          <input id="ashareTickerInput" class="sc-input" type="text"
                 placeholder="6-digit code or name — e.g. 600519, 贵州茅台" autocomplete="off"
                 spellcheck="false" maxlength="20">
          <button id="ashareScreenBtn" class="sc-btn">Screen</button>
        </div>
        <div class="sc-watchlist-row">
          <span class="sc-watchlist-label">Watchlist</span>
          <div id="ashareWatchlistPills" class="sc-watchlist-pills"></div>
          <div class="sc-watchlist-add-wrap">
            <input id="ashareWatchlistAdd" class="sc-watchlist-add-input" type="text"
                   placeholder="Add code or name…" maxlength="20" spellcheck="false" autocomplete="off">
            <button id="ashareWatchlistAddBtn" class="sc-watchlist-add-btn">+</button>
          </div>
        </div>
      </div>
      <div id="ashareResult"></div>
    </div>`;

    setupTickerAutocomplete(document.getElementById("ashareTickerInput"), "/api/ashare_search?q=", r => {
      document.getElementById("ashareTickerInput").value = r.symbol;
      runAshareScreen();
    });
    document.getElementById("ashareScreenBtn").addEventListener("click", runAshareScreen);
    document.getElementById("ashareTickerInput").addEventListener("keydown", e => {
      if (e.key === "Enter") runAshareScreen();
    });
    document.getElementById("ashareWatchlistAddBtn").addEventListener("click", ashareAddToWatchlist);
    document.getElementById("ashareWatchlistAdd").addEventListener("keydown", e => {
      if (e.key === "Enter") ashareAddToWatchlist();
    });
  }

  renderAshareWatchlist();
}

function hideAshare() {
  const canvas = document.getElementById("ashareCanvas");
  if (canvas) canvas.style.display = "none";
  els.workspace.style.display = "";
}

async function runAshareScreen() {
  const ticker = document.getElementById("ashareTickerInput").value.trim();
  if (!ticker) return;
  const resultEl = document.getElementById("ashareResult");
  const btn = document.getElementById("ashareScreenBtn");
  btn.disabled = true;
  btn.textContent = "Screening…";
  resultEl.innerHTML = `<div class="sc-loading">Fetching data for <strong>${escapeHtml(ticker)}</strong>…</div>`;
  try {
    const data = await fetchJson(`/api/ashare_screener?ticker=${encodeURIComponent(ticker)}`);
    if (data.error) {
      resultEl.innerHTML = `<div class="sc-error">⚠ ${escapeHtml(data.error)}</div>`;
    } else {
      renderScreenResult(data, resultEl);
    }
  } catch (err) {
    resultEl.innerHTML = `<div class="sc-error">⚠ ${escapeHtml(err.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Screen";
  }
}

// ── China Macro / Cycle Diagnosis page ──────────────────────────────────────
// Reuses renderIndicatorCard() and REGIME_ICONS from the US Macro view —
// same indicator-card shape, just sourced from Tushare instead of FRED.

function getCnMacroCanvas() {
  let canvas = document.getElementById("cnMacroCanvas");
  if (!canvas) {
    canvas = document.createElement("div");
    canvas.id = "cnMacroCanvas";
    document.querySelector("main").appendChild(canvas);
  }
  return canvas;
}

function hideCnMacro() {
  const canvas = document.getElementById("cnMacroCanvas");
  if (canvas) canvas.style.display = "none";
  els.workspace.style.display = "";
}

async function loadCnMacroPage() {
  const canvas = getCnMacroCanvas();
  canvas.style.display = "";
  els.workspace.style.display = "none";
  canvas.innerHTML = `<div class="cn-macro-page"><div class="sc-loading">Loading China macro data…</div></div>`;

  try {
    const data = await fetchJson("/api/cn_macro");
    if (data.error) {
      canvas.innerHTML = `<div class="cn-macro-page"><div class="sc-error">⚠ ${escapeHtml(data.error)}</div></div>`;
      return;
    }
    renderCnMacro(data, canvas);
  } catch (err) {
    canvas.innerHTML = `<div class="cn-macro-page"><div class="sc-error">⚠ ${escapeHtml(err.message)}</div></div>`;
  }
}

function renderCnMacro(data, canvas) {
  const regime = data.regime;
  const risk = data.recession_risk;

  const dataWarning = !data.data_live
    ? `<div class="no-fred-banner">⚠ TUSHARE_TOKEN not set — China macro data unavailable.</div>`
    : "";

  const riskFactors = (risk.factors || []).map(f => `<li>${escapeHtml(f)}</li>`).join("");
  const hero = `
    <div class="cycle-hero">
      <div class="cycle-hero-left">
        <p class="eyebrow cycle-regime-label">Current cycle regime</p>
        <div>
          <span class="regime-badge ${escapeHtml(regime.color)}">
            ${REGIME_ICONS[regime.id] || ""} ${escapeHtml(regime.label)}
          </span>
        </div>
        <p class="cycle-regime-description">${escapeHtml(regime.description)}</p>
        <p class="cycle-implication">${escapeHtml(regime.implication)}</p>
      </div>
      <div class="cycle-hero-right">
        <div class="recession-block">
          <div class="recession-label-row">
            <span class="recession-title">Growth risk</span>
            <span class="recession-badge ${escapeHtml(risk.level)}">${escapeHtml(risk.label)} (${risk.score}/${risk.max_score})</span>
          </div>
          ${riskFactors
            ? `<ul class="recession-factors">${riskFactors}</ul>`
            : `<p style="font-size:12px;color:var(--muted);margin:0">No elevated risk factors detected.</p>`
          }
        </div>
      </div>
    </div>`;

  const shortInds = data.short_cycle || [];
  const longInds  = data.long_cycle || [];   // CSI 300 P/E, P/B — a Valuation lens, not "long-cycle"

  // MECE split (2026-08-02, mirrors US Macro): Cycle Diagnosis (regime + growth/
  // inflation/credit indicators) · Market Valuation (CSI 300 multiples) · Market
  // Sentiment (Fear & Greed + QVIX). Client-side tab switch — all data already
  // fetched in one /api/cn_macro payload (+ cn_fear/cn_vix loaded independently).
  canvas.innerHTML = `
  <div class="cn-macro-page">
    <div class="section-heading">
      <div>
        <p class="eyebrow">CHINA A-SHARE</p>
        <h2>China Macro</h2>
      </div>
      <p style="margin:0;color:var(--muted);font-size:12px">as of ${escapeHtml(data.as_of)}</p>
    </div>
    ${dataWarning}
    <nav class="view-tabs cn-macro-tabs" aria-label="China Macro views">
      <button class="view-tab active" data-cnview="cycle"     type="button">Cycle Diagnosis</button>
      <button class="view-tab"        data-cnview="valuation" type="button">Market Valuation</button>
      <button class="view-tab"        data-cnview="sentiment" type="button">Market Sentiment</button>
    </nav>
    <div class="cn-macro-view" data-view="cycle">
      ${hero}
      <div class="macro-section-title"><span>Cycle Indicators <span class="macro-section-sub">(growth, inflation, credit — PMI/social-financing/M2/LPR)</span></span></div>
      <div class="indicator-grid">${shortInds.map(renderIndicatorCard).join("")}</div>
    </div>
    <div class="cn-macro-view" data-view="valuation" hidden>
      <div class="macro-section-title"><span>CSI 300 Valuation <span class="macro-section-sub">(is the market cheap or expensive vs. its own history)</span></span></div>
      <div class="indicator-grid">${longInds.map(renderIndicatorCard).join("")}</div>
    </div>
    <div class="cn-macro-view" data-view="sentiment" hidden>
      <div class="macro-section-title"><span>Market Sentiment <span class="macro-section-sub">(A-share Fear &amp; Greed · composite of 6 behavioural legs)</span></span></div>
      <div id="cnFearBox"><div class="cn-fear-loading">Loading sentiment gauge…</div></div>
      <div id="cnVixBox"></div>
      <div class="macro-section-title"><span>Institutional Money Flow <span class="macro-section-sub">(whole-market large-order net flow, by sector)</span></span></div>
      <div id="cnFlowBox"><div class="cn-fear-loading">Loading…</div></div>
      <div class="macro-section-title"><span>Daily Limit Breadth <span class="macro-section-sub">(涨停/跌停 — how many names closed at the limit today, by board)</span></span></div>
      <div id="cnLimitBox"><div class="cn-fear-loading">Loading…</div></div>
      <div class="macro-section-title"><span>Market Breadth <span class="macro-section-sub">(Weinstein Momentum Index — 200-day MA of net advances−declines)</span></span></div>
      <div id="cnBreadthBox"><div class="cn-fear-loading">Loading…</div></div>
      <div class="macro-section-title"><span>Momentum Concentration <span class="macro-section-sub">(is the momentum trade crowded into one sector?)</span></span></div>
      <div id="cnMomConcBox"><div class="cn-fear-loading">Loading…</div></div>
      <div id="cnMomConcChartBox"><div class="cn-fear-loading">Loading chart…</div></div>
    </div>
  </div>`;

  canvas.querySelectorAll("[data-cnview]").forEach(btn => btn.addEventListener("click", () => {
    canvas.querySelectorAll("[data-cnview]").forEach(b => b.classList.toggle("active", b === btn));
    canvas.querySelectorAll(".cn-macro-view").forEach(v => { v.hidden = v.dataset.view !== btn.dataset.cnview; });
  }));

  loadCnFear();
  loadCnVix();
  loadCnFlow();
  loadCnLimit();
  loadCnBreadth();
  loadMomentumConcentration("cn", "cnMomConcBox");
  loadMomentumConcentrationSeries("cn", "cnMomConcChartBox");
}

const _RATING_BUCKET_CLASS = { positive: "positive", neutral: "neutral", negative: "danger", unrated: "neutral", other: "neutral" };

function _fmtCooldown(min) {
  if (!min) return "shortly";
  return min >= 60 ? `~${(min / 60).toFixed(1)}h` : `~${min}min`;
}
function _fmtGap(sec) {
  if (!sec || sec < 90) return "minute";
  return `${Math.round(sec / 60)} minutes`;
}
const _RESEARCH_STATE = { days: 7, tsCode: "" };

function renderCnReports(d, box) {
  if (d.status !== "ready") {
    box.innerHTML = `<div class="sc-error">⚠ ${escapeHtml(d.error || "Unavailable")}</div>`;
    return;
  }
  const cs = d.cache_status || {};
  const fmtDay = v => (v && v.length === 8) ? `${v.slice(0, 4)}-${v.slice(4, 6)}-${v.slice(6)}` : "—";
  // Reports arrive newest-first, so what matters is how far back they reach —
  // a count of "windows" told the reader nothing about whether the week they
  // were looking at was actually loaded.
  const reach = cs.covered_from
    ? (cs.complete ? `All ${cs.horizon_days} days are loaded.`
                   : `Loaded back to ${fmtDay(cs.covered_from)} so far (${cs.covered_days} of ${cs.horizon_days} days).`)
    : "Nothing loaded yet.";
  const statusNote = (cs.error && !cs.building)
    ? `${reach} ⚠ Paused: ${cs.error} Will retry automatically in ${_fmtCooldown(cs.cooldown_remaining_min)}; what is already loaded is kept.`
    : cs.building
      ? `${reach} Fetching the rest in the background, most recent first — Tushare limits this account to one request every ${_fmtGap(cs.request_gap_s)}.`
      : cs.ready
        ? `${reach} Last refreshed ${cs.built_at ? new Date(cs.built_at * 1000).toLocaleString() : "—"}.`
        : `Not loaded yet.`;
  const truncNote = (cs.truncated_days || []).length
    ? ` ⚠ ${cs.truncated_days.map(fmtDay).join(", ")}: more reports were filed than one request returns, so that day is incomplete.` : "";
  // An empty table means two different things: nothing was published, or the
  // days being asked about have not been fetched. Say which.
  const windowStart = (() => { const t = new Date(); t.setDate(t.getDate() - d.days);
    return `${t.getFullYear()}${String(t.getMonth() + 1).padStart(2, "0")}${String(t.getDate()).padStart(2, "0")}`; })();
  const windowLoaded = cs.covered_from && cs.covered_from <= windowStart;
  const emptyText = !cs.covered_from ? "Not loaded yet — see the note below."
    : windowLoaded ? "No reports were published in this window."
    : `No reports loaded for this window yet — only ${fmtDay(cs.covered_from)} onward is in so far.`;
  const reports = d.reports || [];
  const rows = reports.map(r => {
    const cls = _RATING_BUCKET_CLASS[r.rating_bucket] || "neutral";
    return `<tr>
      <td class="cnlim-num">${escapeHtml(r.report_date || "—")}</td>
      <td>${escapeHtml(r.name || r.ts_code || "—")}</td>
      <td>${escapeHtml(r.title || "—")}</td>
      <td>${escapeHtml(r.org || "—")}</td>
      <td><span class="filter-group-note" style="color:var(--${cls === "positive" ? "change-up" : cls === "danger" ? "change-down" : "muted"})">${escapeHtml(r.rating || "无")}</span></td>
      <td>${escapeHtml(r.classify === "首次关注" || r.classify === "首份报告" || r.classify === "首次评级" ? "🆕 " + r.classify : (r.report_type || "—"))}</td>
    </tr>`;
  }).join("");
  box.innerHTML = `
    <div class="cnlim-table-wrap">
      <table class="cnlim-table">
        <thead><tr><th>Date</th><th>Stock</th><th>Title</th><th>Institution</th><th>Rating</th><th>Type</th></tr></thead>
        <tbody>${rows || `<tr><td colspan="6" class="filter-group-note">${escapeHtml(emptyText)}</td></tr>`}</tbody>
      </table>
    </div>
    <div class="filter-group-note" style="margin-top:8px">${d.count} report${d.count === 1 ? "" : "s"} in the last ${d.days} day${d.days === 1 ? "" : "s"}${d.truncated ? ` (showing the newest ${reports.length})` : ""}. ${escapeHtml(statusNote + truncNote)}</div>`;
}

function getResearchCanvas() {
  let canvas = document.getElementById("researchCanvas");
  if (!canvas) {
    canvas = document.createElement("div");
    canvas.id = "researchCanvas";
    document.querySelector("main").appendChild(canvas);
  }
  return canvas;
}

function hideResearch() {
  const canvas = document.getElementById("researchCanvas");
  if (canvas) canvas.style.display = "none";
  els.workspace.style.display = "";
}

async function loadResearchPage() {
  const canvas = getResearchCanvas();
  canvas.style.display = "";
  els.workspace.style.display = "none";
  canvas.innerHTML = `
    <div class="cn-macro-page">
      <div class="section-heading">
        <div>
          <p class="eyebrow">CHINA A-SHARE</p>
          <h2>Sell-Side Research</h2>
        </div>
      </div>
      <p class="overall-description">Structured brokerage-report metadata (title, rating, institution, multi-year forecast) from Tushare's report_rc — <strong>not</strong> a link to the actual report text or PDF; that field doesn't exist in this interface, verified live before building this. Rating vocabulary is set independently by each brokerage (mixes 买入/增持/推荐-style and Buy/Overweight-style scales in the same feed) and is bucketed here into positive/neutral/negative — 无 means the report didn't carry a rating at all.</p>
      <div class="filter-group" style="max-width:520px">
        <label>Window
          <select id="researchDays">
            <option value="1">Today</option>
            <option value="3">Last 3 days</option>
            <option value="7" selected>Last 7 days</option>
            <option value="14">Last 14 days</option>
            <option value="30">Last 30 days</option>
          </select>
        </label>
        <label>Stock (ts_code, e.g. 600519.SH) — optional
          <input type="text" id="researchTsCode" placeholder="leave blank for whole market">
        </label>
      </div>
      <div id="researchBox"><div class="cn-fear-loading">Loading…</div></div>
    </div>`;

  const daysSel = document.getElementById("researchDays");
  const tsInput = document.getElementById("researchTsCode");
  daysSel.value = String(_RESEARCH_STATE.days);
  tsInput.value = _RESEARCH_STATE.tsCode;
  daysSel.addEventListener("change", () => { _RESEARCH_STATE.days = parseInt(daysSel.value, 10); loadResearchDigest(); });
  tsInput.addEventListener("change", () => { _RESEARCH_STATE.tsCode = tsInput.value.trim().toUpperCase(); loadResearchDigest(); });

  loadResearchDigest();
}

async function loadResearchDigest() {
  const box = document.getElementById("researchBox");
  if (!box) return;
  try {
    const params = new URLSearchParams({ days: String(_RESEARCH_STATE.days) });
    if (_RESEARCH_STATE.tsCode) params.set("ts_code", _RESEARCH_STATE.tsCode);
    const d = await fetchJson(`/api/cn_reports?${params}`);
    renderCnReports(d, box);
    const cs = d.cache_status || {};
    if (state.page !== "research") return;
    if (cs.building) {
      // Each request is minutes apart; there is nothing new to draw every 8 seconds.
      setTimeout(loadResearchDigest, Math.min(60, Math.max(8, (cs.request_gap_s || 60) / 4)) * 1000);
    } else if (cs.error && cs.cooldown_remaining_min != null) {
      // Paused: come back when the pause ends, so the page does not sit on a stale notice.
      setTimeout(loadResearchDigest, (cs.cooldown_remaining_min + 1) * 60 * 1000);
    } else if (cs.ready && !cs.complete) {
      setTimeout(loadResearchDigest, 60 * 1000);
    }
  } catch (err) {
    box.innerHTML = `<div class="sc-error">⚠ ${escapeHtml(err.message)}</div>`;
  }
}

// ── Weinstein Momentum Index (MI) — A-share market breadth ──────────────────
// MI = 200-day moving average of daily net (advances − declines). Weinstein's
// primary rule is the ZERO-LINE CROSSING; the index is overlaid on a second
// axis because the divergence read is inherently a two-series comparison.
function _miChartSvg(series) {
  const pts = series.filter(p => p.mi != null);
  if (pts.length < 2) return '<p class="sc-chart-empty">Not enough history yet.</p>';
  const W = 720, H = 200, PL = 10, PR = 58, PT = 12, PB = 22;
  const plotW = W - PL - PR, plotH = H - PT - PB;
  const mis = pts.map(p => p.mi);
  let mlo = Math.min(...mis, 0), mhi = Math.max(...mis, 0);
  const mpad = (mhi - mlo) * 0.1 || 1; mlo -= mpad; mhi += mpad;
  const yM = v => PT + plotH - (v - mlo) / (mhi - mlo) * plotH;
  const idxPts = pts.filter(p => p.idx != null);
  const ilo = idxPts.length ? Math.min(...idxPts.map(p => p.idx)) : 0;
  const ihi = idxPts.length ? Math.max(...idxPts.map(p => p.idx)) : 1;
  const yI = v => PT + plotH - (v - ilo) / ((ihi - ilo) || 1) * plotH;
  const x = i => PL + (i / (pts.length - 1)) * plotW;

  const miPath = pts.map((p, i) => `${x(i).toFixed(1)},${yM(p.mi).toFixed(1)}`).join(' ');
  const idxPath = pts.map((p, i) => p.idx == null ? null : `${x(i).toFixed(1)},${yI(p.idx).toFixed(1)}`)
                     .filter(Boolean).join(' ');
  const zeroY = yM(0);
  const ticks = [0, 0.5, 1].map(f => {
    const v = mhi - (mhi - mlo) * f, y = PT + plotH * f;
    return `<text x="${W - PR + 6}" y="${(y + 3.5).toFixed(1)}" class="adv-tick">${Math.round(v)}</text>`;
  }).join('');
  const n = Math.min(5, pts.length);
  const xt = Array.from({length: n}, (_, k) => {
    const i = Math.round(k * (pts.length - 1) / (n - 1 || 1));
    return `<text x="${x(i).toFixed(1)}" y="${H - 6}" text-anchor="middle" class="adv-tick">${String(pts[i].d).slice(2)}</text>`;
  }).join('');

  return `<svg class="mi-svg" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">
    <line x1="${PL}" y1="${zeroY.toFixed(1)}" x2="${W - PR}" y2="${zeroY.toFixed(1)}" stroke="currentColor" stroke-width="1" stroke-dasharray="4,3" opacity="0.45"/>
    <text x="${W - PR + 6}" y="${(zeroY + 3.5).toFixed(1)}" class="adv-tick" style="font-weight:700">0</text>
    ${idxPath ? `<polyline points="${idxPath}" fill="none" stroke="#94a3b8" stroke-width="1.2" opacity="0.85"/>` : ''}
    <polyline points="${miPath}" fill="none" stroke="#2563eb" stroke-width="1.8" stroke-linejoin="round"/>
    ${ticks}${xt}
  </svg>`;
}

function renderCnBreadth(d, box) {
  const cs = d.cache_status || {};
  if (d.status === 'building') {
    box.innerHTML = `<p class="filter-group-note">Building the 2-year breadth history (one-time)… ${cs.progress || 0}/${cs.total || '?'} sessions. Needs ${cs.mi_window || 200} sessions before the first MI value exists.</p>`;
    return;
  }
  if (d.status !== 'ready') {
    box.innerHTML = `<div class="sc-error">⚠ ${escapeHtml(d.error || 'Unavailable')}</div>`;
    return;
  }
  const bull = d.sign === 'positive';
  const cls = bull ? 'positive' : d.sign === 'negative' ? 'danger' : 'neutral';
  const div = d.divergence;
  const divHtml = div
    ? `<div class="mi-diverge mi-diverge-${div.type}"><b>${div.type === 'bearish' ? '⚠ Bearish' : 'Bullish'} divergence</b> — ${escapeHtml(div.detail)}.</div>`
    : `<div class="filter-group-note">No index/MI divergence detected on the current window.</div>`;
  const L = d.latest || {};

  box.innerHTML = `
    <p class="overall-description">Weinstein's <b>Momentum Index</b>: the ${cs.mi_window || 200}-day moving average of daily net (advances − declines) across the whole A-share market. His primary rule is the <b>zero-line crossing</b> — above zero bullish, below bearish — and the reading is considered stronger the longer it holds one side of zero and the further it travels before crossing back. He notes it "can be late on bottoms". This is a descriptive breadth state, <b>not a buy/sell instruction</b>, and no backtest has been run.</p>
    <div class="sc-vol-strip stage-strip">
      <div class="sc-vol-stat"><span class="sc-vs-label">Momentum Index</span><span class="sc-vs-val ${cls === 'positive' ? 'stage-pos' : cls === 'danger' ? 'stage-neg' : ''}">${d.mi > 0 ? '+' : ''}${d.mi}</span></div>
      <div class="sc-vol-stat"><span class="sc-vs-label">Side of zero</span><span class="sc-vs-val">${bull ? 'Above ▲' : 'Below ▼'}</span></div>
      <div class="sc-vol-stat"><span class="sc-vs-label">Sessions since cross</span><span class="sc-vs-val">${d.last_cross_date ? d.sessions_since_cross : `<span class="mi-nocross">no crossing in window</span>`}</span></div>
      <div class="sc-vol-stat"><span class="sc-vs-label">Furthest from zero this run</span><span class="sc-vs-val">${d.extreme_since_cross == null ? '—' : (d.extreme_since_cross > 0 ? '+' : '') + d.extreme_since_cross}${d.extreme_since_cross_date ? ` <span class="mi-sub">${escapeHtml(d.extreme_since_cross_date)}</span>` : ''}</span></div>
      <div class="sc-vol-stat"><span class="sc-vs-label">Latest session A/D</span><span class="sc-vs-val">${L.adv} / ${L.dec}</span></div>
      <div class="sc-vol-stat"><span class="sc-vs-label">Net breadth</span><span class="sc-vs-val ${L.net > 0 ? 'stage-pos' : 'stage-neg'}">${L.net > 0 ? '+' : ''}${L.net}</span></div>
    </div>
    ${divHtml}
    <div class="mi-legend"><span class="adv-lg"><i style="background:#2563eb"></i>Momentum Index (left)</span><span class="adv-lg"><i style="background:#94a3b8"></i>中证全指 (right, scaled)</span></div>
    ${_miChartSvg(d.series || [])}
    <p class="overall-description">As of ${escapeHtml(d.as_of || '—')}${d.last_cross_date ? ` · last zero crossing ${escapeHtml(d.last_cross_date)}` : " · MI has not crossed zero anywhere in the available window"}. Every listed A-share is counted including ST names (A/D measures the whole market, and price-limit bands don't affect an up/down count). The cumulative <b>A/D Line</b> is deliberately not shown: this cache holds current constituents only, and survivorship bias compounds permanently in a cumulative total — the MI's 200-day window cannot accumulate it. The divergence test is a mechanisation of a rule the book states qualitatively; its window and thresholds are choices, not values from Weinstein.</p>`;
}

async function loadCnBreadth() {
  const box = document.getElementById('cnBreadthBox');
  if (!box) return;
  try {
    const d = await fetchJson('/api/cn_breadth?days=400');
    renderCnBreadth(d, box);
    if (d.status === 'building') setTimeout(loadCnBreadth, 10000);
  } catch (err) {
    box.innerHTML = `<div class="sc-error">⚠ ${escapeHtml(err.message)}</div>`;
  }
}

// ── Weinstein Momentum Index — US market breadth ─────────────────────────────
// Two universes, same arithmetic. "all" (every US common stock) is the default
// and the faithful reading — Weinstein used the whole NYSE list, and breadth is
// only informative when it CAN disagree with the cap-weighted index. "largecap"
// is the fixed top-500-by-market-cap subset; it is NOT the S&P 500 (real index
// membership is a committee decision with no point-in-time source here).
let _usBreadthScope = "all";

function renderUsBreadth(d, box) {
  const cs = d.cache_status || {};
  const toggle = `<div class="cn-fear-range mi-scope">
      <button class="cn-fear-rbtn${_usBreadthScope === "all" ? " active" : ""}" data-scope="all">All US stocks</button>
      <button class="cn-fear-rbtn${_usBreadthScope === "largecap" ? " active" : ""}" data-scope="largecap">Top ${cs.largecap_n || 500} by market cap</button>
    </div>`;
  const wire = () => box.querySelectorAll("[data-scope]").forEach(b =>
    b.addEventListener("click", () => {
      if (_usBreadthScope === b.dataset.scope) return;
      _usBreadthScope = b.dataset.scope;
      loadUsBreadth();
    }));

  if (d.status === "building") {
    const pct = cs.total ? Math.round((cs.progress || 0) / cs.total * 100) : 0;
    box.innerHTML = `<p class="filter-group-note">Building the 2-year US breadth history (one-time)… ${cs.progress || 0}/${cs.total || "?"} download batches (${pct}%). Needs ${cs.mi_window || 200} sessions before the first MI value exists.</p>`;
    return;
  }
  if (d.status !== "ready") {
    box.innerHTML = `<div class="sc-error">⚠ ${escapeHtml(d.error || "Unavailable")}</div>`;
    return;
  }
  const bull = d.sign === "positive";
  const div = d.divergence;
  const divHtml = div
    ? `<div class="mi-diverge mi-diverge-${div.type}"><b>${div.type === "bearish" ? "⚠ Bearish" : "Bullish"} divergence</b> — ${escapeHtml(div.detail)}.</div>`
    : `<div class="filter-group-note">No index/MI divergence detected on the current window.</div>`;
  const L = d.latest || {};
  const scopeNote = _usBreadthScope === "largecap"
    ? `The <b>top ${cs.largecap_n || 500} by market cap today, held fixed</b> across the window — a large-cap breadth read. This is <b>not the S&amp;P 500</b>: real index membership is a committee decision and this project has no point-in-time source for it. Note this scope is the more survivorship-biased of the two — "in the top 500 today" is a far stronger filter than "listed today".`
    : `<b>Every US-listed common stock</b> (${d.universe_size} counted in the latest session) — the faithful reading. Weinstein used all NYSE issues, and breadth only tells you something when it <em>can</em> disagree with the cap-weighted index.`;

  box.innerHTML = `
    <p class="overall-description">Weinstein's <b>Momentum Index</b>: the ${cs.mi_window || 200}-day moving average of daily net (advances − declines). His primary rule is the <b>zero-line crossing</b> — above zero bullish, below bearish — and the reading is considered stronger the longer it holds one side of zero and the further it travels before crossing back. He notes it "can be late on bottoms". Descriptive breadth state, <b>not a buy/sell instruction</b>; no backtest has been run.</p>
    ${toggle}
    <p class="filter-group-note mi-scope-note">${scopeNote}</p>
    <div class="sc-vol-strip stage-strip">
      <div class="sc-vol-stat"><span class="sc-vs-label">Momentum Index</span><span class="sc-vs-val ${bull ? "stage-pos" : "stage-neg"}">${d.mi > 0 ? "+" : ""}${d.mi}</span></div>
      <div class="sc-vol-stat"><span class="sc-vs-label">Side of zero</span><span class="sc-vs-val">${bull ? "Above ▲" : "Below ▼"}</span></div>
      <div class="sc-vol-stat"><span class="sc-vs-label">Sessions since cross</span><span class="sc-vs-val">${d.last_cross_date ? d.sessions_since_cross : `<span class="mi-nocross">no crossing in window</span>`}</span></div>
      <div class="sc-vol-stat"><span class="sc-vs-label">Furthest from zero this run</span><span class="sc-vs-val">${d.extreme_since_cross == null ? "—" : (d.extreme_since_cross > 0 ? "+" : "") + d.extreme_since_cross}${d.extreme_since_cross_date ? ` <span class="mi-sub">${escapeHtml(d.extreme_since_cross_date)}</span>` : ""}</span></div>
      <div class="sc-vol-stat"><span class="sc-vs-label">Latest session A/D</span><span class="sc-vs-val">${L.adv} / ${L.dec}</span></div>
      <div class="sc-vol-stat"><span class="sc-vs-label">Net breadth</span><span class="sc-vs-val ${L.net > 0 ? "stage-pos" : "stage-neg"}">${L.net > 0 ? "+" : ""}${L.net}</span></div>
    </div>
    ${divHtml}
    <div class="mi-legend"><span class="adv-lg"><i style="background:#2563eb"></i>Momentum Index (left)</span><span class="adv-lg"><i style="background:#94a3b8"></i>SPY (right, scaled)</span></div>
    ${_miChartSvg(d.series || [])}
    <p class="overall-description">As of ${escapeHtml(d.as_of || "—")}${d.last_cross_date ? ` · last zero crossing ${escapeHtml(d.last_cross_date)}` : " · MI has not crossed zero anywhere in the available window"}${d.last_session_may_be_intraday ? ' · <b>latest session is still intraday</b> (read before the US close), so the A/D tiles are provisional' : ""}. Prices are split- and dividend-adjusted, so ex-dividend days aren't miscounted as declines; a stock's first bar and any bar after a data gap is counted in neither column. Both scopes hold <b>today's</b> constituents fixed, so names delisted during the window never contribute their declines — a 200-day average cannot let that bias accumulate the way a cumulative A/D Line would, but it is present in every reading. The divergence test is a mechanisation of a rule the book states qualitatively; its window and thresholds are choices, not Weinstein's.</p>`;
  wire();
}

async function loadUsBreadth() {
  const box = document.getElementById("usBreadthBox");
  if (!box) return;
  try {
    const d = await fetchJson(`/api/us_breadth?days=400&scope=${_usBreadthScope}`);
    renderUsBreadth(d, box);
    if (d.status === "building") setTimeout(loadUsBreadth, 10000);
  } catch (err) {
    box.innerHTML = `<div class="sc-error">⚠ ${escapeHtml(err.message)}</div>`;
  }
}

// ── Cross-asset market context — Weinstein stages on major assets ────────────
// Same indicator code path as every stock page (stage_analysis._one), so the
// readings are directly comparable. Ordered by Mansfield RS = leadership order.
function renderMarketContext(d, box) {
  if (!d || d.status !== "ready") {
    box.innerHTML = `<div class="sc-error">⚠ ${escapeHtml((d && d.error) || "Unavailable")}</div>`;
    return;
  }
  const num = (v, dp = 2, sign = false) =>
    v == null ? "—" : `${sign && v > 0 ? "+" : ""}${(+v).toFixed(dp)}`;
  const px = v => v == null ? "—" : (+v >= 1000 ? (+v).toLocaleString(undefined, {maximumFractionDigits: 0}) : (+v).toFixed(2));
  const cls = v => v == null ? "" : (+v > 0 ? "stage-pos" : +v < 0 ? "stage-neg" : "");

  // Leadership order: strongest relative strength first; the benchmark (null
  // RS, since RS against itself is definitionally zero) is pinned last.
  const rows = [...d.rows].sort((a, b) => {
    if (a.mansfield_rs == null) return 1;
    if (b.mansfield_rs == null) return -1;
    return b.mansfield_rs - a.mansfield_rs;
  });

  const body = rows.map(r => {
    const st = r.stage, se = r.stage_ema;
    const lbl = ["", "1 Basing", "2 Advancing", "3 Topping", "4 Declining"];
    const disagree = st && se && st !== se
      ? ` <span class="mc-ema-flag" title="The EMA-based classification disagrees with the SMA one — the EMA weights recent weeks more, so it turns sooner. Weinstein's rule is the SMA.">EMA: ${lbl[se]}</span>` : "";
    const isBench = r.ticker === d.benchmark;
    return `<tr${isBench ? ' class="mc-bench"' : ""}>
      <td class="mc-name"><b>${escapeHtml(r.name)}</b><span class="mc-tkr">${escapeHtml(r.ticker)}</span></td>
      <td class="mc-class">${escapeHtml(r.asset_class)}</td>
      <td><span class="stage-pill${st ? " stage-s" + st : ""}">${st ? lbl[st] : "n/a"}</span>${disagree}</td>
      <td class="mc-r">${px(r.price)}</td>
      <td class="mc-r">${px(r.sma30)}</td>
      <td class="mc-r ${cls(r.price_vs_sma30)}">${num(r.price_vs_sma30, 1, true)}%</td>
      <td class="mc-r ${cls(r.sma30_slope)}">${num(r.sma30_slope, 1, true)}%</td>
      <td class="mc-r">${px(r.ema30)}</td>
      <td class="mc-r ${cls(r.price_vs_ema30)}">${num(r.price_vs_ema30, 1, true)}%</td>
      <td class="mc-r ${cls(r.mansfield_rs)}">${r.mansfield_rs == null ? '<span class="mc-na">benchmark</span>' : num(r.mansfield_rs, 2, true)}</td>
    </tr>`;
  }).join("");

  box.innerHTML = `
    <p class="overall-description">Weinstein's method is <b>top-down</b> — judge the market before the sector and the sector before the stock. Each asset below gets exactly the same treatment every stock page gets: the weekly <b>30-period SMA and EMA</b>, the 13-week slope of each, the four-stage classification, and Mansfield relative strength vs ${escapeHtml(d.benchmark)}. Sorted strongest relative strength first. Descriptive trend state, <b>not a buy/sell instruction</b>; no backtest has been run.</p>
    <div class="mc-wrap"><table class="mc-table">
      <thead><tr>
        <th>Asset</th><th>Class</th><th>Stage (30w SMA)</th>
        <th class="mc-r">Price</th><th class="mc-r">30w SMA</th><th class="mc-r">vs SMA</th>
        <th class="mc-r">SMA slope 13w</th><th class="mc-r">30w EMA</th><th class="mc-r">vs EMA</th>
        <th class="mc-r">RS vs ${escapeHtml(d.benchmark)}</th>
      </tr></thead>
      <tbody>${body}</tbody>
    </table></div>
    <p class="overall-description">Week of ${escapeHtml(d.as_of)}${d.partial_last_bar ? " · <b>the latest weekly bar is still forming</b> — price uses it, volume excludes it (volume accumulates through the week, price does not)" : ""}. <b>Oil is the WTI front-month futures contract</b> — the price of oil, not what an oil ETF would have returned: ETFs hold a rolling futures position, and over this window WTI front-month returned <b>+15.3%</b> against USO's <b>+78.1%</b> because the market was in backwardation, so the two can sit in different stages at once. Bitcoin trades 7 days a week, so its weekly bar spans more calendar time than the equity bars beside it. Prices are split- and dividend-adjusted, making the ETF rows total-return series. Mansfield RS is measured against ${escapeHtml(d.benchmark)} for every row, so for non-equity assets it is a cross-asset relative-strength read — a coherent question, but not the one Weinstein asked (he compared a stock to its own market). Stage 1 vs 3 is separated by the prior-leg slope, an explicit inference rather than a formula from the book.</p>`;
}

async function loadMarketContext() {
  const box = document.getElementById("marketContextBox");
  if (!box) return;
  try {
    renderMarketContext(await fetchJson("/api/market_context"), box);
  } catch (err) {
    box.innerHTML = `<div class="sc-error">⚠ ${escapeHtml(err.message)}</div>`;
  }
}



// ── FOMC Rate Path ──────────────────────────────────────────────────────────
// Market-implied Fed decision probabilities. NOT CME FedWatch — see
// product/fomc_path.py for why CME's own numbers are not obtainable.
const _FOMC_LEGS = [
  ["cut_50",  "Cut >25",  "fomc-cut"],
  ["cut_25",  "Cut 25",   "fomc-cut"],
  ["hold",    "Hold",     "fomc-hold"],
  ["hike_25", "Hike 25",  "fomc-hike"],
  ["hike_50", "Hike >25", "fomc-hike"],
];

function fomcNum(n, digits = 0) {
  return n == null ? "—" : (n * 100).toFixed(digits) + "%";
}

function fomcRow(m) {
  if (m.state !== "ok") {
    return `<tr class="fomc-row-off"><td>${escapeHtml(m.title || m.event || "?")}</td>
      <td colspan="8" class="fomc-dim">${escapeHtml(m.error || "unavailable")}</td></tr>`;
  }
  const p = m.probs || {};
  // A raw mid-sum far from 1, or a wide leg spread, means the quote is thin —
  // flag it rather than letting a normalised number look authoritative.
  const thin = (m.raw_sum != null && Math.abs(m.raw_sum - 1) > 0.06)
            || (m.max_spread != null && m.max_spread > 0.05);
  const bar = _FOMC_LEGS.map(([k, lbl, cls]) => {
    const v = p[k] || 0;
    return v < 0.005 ? "" :
      `<span class="fomc-seg ${cls}" style="width:${(v * 100).toFixed(2)}%" title="${lbl}: ${fomcNum(v, 1)}"></span>`;
  }).join("");
  const lean = m.expected_bp > 2 ? "fomc-hike" : m.expected_bp < -2 ? "fomc-cut" : "fomc-hold";
  return `<tr>
    <td class="fomc-when"><b>${escapeHtml(m.meeting_date || "—")}</b>
      ${thin ? `<span class="fomc-thin" title="raw mid-sum ${m.raw_sum}, widest leg spread ${fomcNum(m.max_spread, 0)} — thinly quoted">thin</span>` : ""}</td>
    <td class="fomc-bar-cell"><span class="fomc-bar">${bar}</span></td>
    <td class="fomc-n fomc-cut">${fomcNum(m.p_cut)}</td>
    <td class="fomc-n fomc-hold">${fomcNum(m.p_hold)}</td>
    <td class="fomc-n fomc-hike"><b>${fomcNum(m.p_hike)}</b></td>
    <td class="fomc-n ${lean}">${m.expected_bp > 0 ? "+" : ""}${m.expected_bp}</td>
    <td class="fomc-n">${m.cum_bp > 0 ? "+" : ""}${m.cum_bp}</td>
    <td class="fomc-n">${escapeHtml(m.implied_range || "—")}</td>
    <td class="fomc-n fomc-dim">${m.open_interest == null ? "—" : Math.round(m.open_interest).toLocaleString()}</td>
  </tr>`;
}

async function renderFomcPath() {
  els.tableEyebrow.textContent = "Market context";
  els.tableTitle.textContent = "FOMC Rate Path";
  els.tableMeta.textContent = "";
  els.content.innerHTML = `<div class="cn-fear-loading">Loading…</div>`;
  let d;
  try { d = await fetchJson("/api/fomc_path"); }
  catch (err) { els.content.innerHTML = `<div class="sc-error">⚠ ${escapeHtml(err.message)}</div>`; return; }
  if (d.status !== "ready") {
    els.content.innerHTML = `<div class="sc-error">⚠ ${escapeHtml(d.error || "unavailable")}</div>`; return;
  }

  const c = d.current || {};
  const nx = d.next_meeting;
  const fc = d.futures_check || {};
  const gapOk = fc.state === "ok" && fc.agrees;

  const headline = !nx ? "" : `
    <div class="fomc-headline">
      <div class="fomc-hl-main">
        <span class="fomc-hl-lbl">Next meeting · ${escapeHtml(nx.meeting_date || "")}</span>
        <span class="fomc-hl-big ${nx.p_hike >= nx.p_hold ? "fomc-hike" : "fomc-hold"}">${fomcNum(nx.p_hike, 1)}</span>
        <span class="fomc-hl-sub">probability of a <b>hike</b> · ${fomcNum(nx.p_hold, 1)} hold · ${fomcNum(nx.p_cut, 1)} cut</span>
      </div>
      <div class="fomc-hl-side">
        <div><span class="fomc-dim">Current target</span> <b>${escapeHtml(c.target_range || "—")}</b></div>
        <div><span class="fomc-dim">Effective rate</span> <b>${c.effr == null ? "—" : c.effr.toFixed(2) + "%"}</b>
          <span class="fomc-dim">${escapeHtml(c.effr_date || "")}</span></div>
        <div><span class="fomc-dim">Priced by ${escapeHtml((d.meetings.filter(m=>m.state==="ok").slice(-1)[0]||{}).meeting_date || "")}</span>
          <b class="${(d.meetings.filter(m=>m.state==="ok").slice(-1)[0]||{}).cum_bp >= 0 ? "fomc-hike" : "fomc-cut"}">${(() => {
            const l = d.meetings.filter(m=>m.state==="ok").slice(-1)[0]; return l ? (l.cum_bp>0?"+":"")+l.cum_bp+" bp" : "—"; })()}</b></div>
      </div>
    </div>`;

  const futures = fc.state !== "ok" ? `<div class="fomc-xcheck fomc-xcheck-off">
        Futures cross-check unavailable — ${escapeHtml(fc.error || fc.note || "no data")}</div>` : `
    <div class="fomc-xcheck ${gapOk ? "fomc-xcheck-ok" : "fomc-xcheck-warn"}">
      <b>Independent cross-check${gapOk ? " — agrees" : " — DISAGREES"}:</b>
      30-Day Fed Funds futures (<code>${escapeHtml(fc.contract || "ZQ")}</code>, the contract CME's own
      FedWatch is built from) imply an average effective rate of <b>${fc.implied_avg_effr}%</b> for
      ${escapeHtml(fc.compare_month || "the front month")}. The Kalshi distribution implies
      <b>${fc.kalshi_implied_avg_effr}%</b> over the same month — a gap of
      <b>${fc.gap_bp > 0 ? "+" : ""}${fc.gap_bp} bp</b>.
      ${fc.split ? `<span class="fomc-dim">(${fc.split.days_at_prior_rate} of ${fc.split.days_in_month} days sit at the pre-meeting rate, which is why the front contract mostly reads the earlier decision.)</span>` : ""}
      ${gapOk ? "" : `<div class="fomc-warn-line">A wide gap means one of the two is mispriced or stale — treat <b>both</b> readings with suspicion rather than picking the one you prefer.</div>`}
    </div>`;

  els.content.innerHTML = `
    <p class="overall-description">What the market expects the Fed to do at each scheduled meeting, and how much
      tightening or easing is priced cumulatively. <b>Expected bp</b> is the probability-weighted move for that
      meeting; <b>cumulative</b> adds up every meeting to that point and drives the implied target range.</p>
    ${headline}
    ${futures}
    <div class="fomc-table-wrap">
      <table class="fomc-table">
        <thead><tr>
          <th>Meeting</th><th>Distribution</th>
          <th class="fomc-n">Cut</th><th class="fomc-n">Hold</th><th class="fomc-n">Hike</th>
          <th class="fomc-n">Exp. bp</th><th class="fomc-n">Cum. bp</th>
          <th class="fomc-n">Implied range</th><th class="fomc-n">Open int.</th>
        </tr></thead>
        <tbody>${(d.meetings || []).map(fomcRow).join("")}</tbody>
      </table>
    </div>
    <div class="fomc-legend">
      ${_FOMC_LEGS.filter((x,i)=>i!==0&&i!==4).map(([k,lbl,cls])=>`<span class="fomc-key"><span class="fomc-seg ${cls}"></span>${escapeHtml(lbl)}</span>`).join("")}
      <span class="fomc-dim">· widths are normalised probabilities</span>
    </div>
    <details class="fomc-caveats"><summary>Sources and caveats</summary>
      <ul>
        <li><b>Primary:</b> ${escapeHtml(d.source.primary)}</li>
        <li><b>Cross-check:</b> ${escapeHtml(d.source.cross_check)}</li>
        <li><b>Anchor:</b> ${escapeHtml(d.source.anchor)}</li>
        ${(d.caveats || []).map(x => `<li>${escapeHtml(x)}</li>`).join("")}
      </ul></details>`;
}

// ── CP3 · Distribution / effort-vs-result (DISPLAY ONLY) ────────────────────
// Spec: research/market_top_spec.md section 5. This panel reports a reading, not
// an instruction. CP3 is gated behind CP1/CP2 in the protocol and neither is
// implementable on the current data stack, so nothing here may drive exposure.
const _CP3_LEVEL_CLS = { noise: "cp3-noise", warning: "cp3-warn",
                         pressure: "cp3-press", elevated: "cp3-elev" };
const _CP3 = { market: "us", volBar: "prior_day" };
function _cp3Ord(n) {           // 1st / 2nd / 3rd / 4th, with the teens exception
  const t = n % 100, u = n % 10;
  if (t >= 11 && t <= 13) return "th";
  return u === 1 ? "st" : u === 2 ? "nd" : u === 3 ? "rd" : "th";
}

function cp3Card(it) {
  if (it.state !== "ok") {
    const why = escapeHtml(it.error || it.note || "unavailable");
    return `<div class="cp3-card cp3-card-off">
      <div class="cp3-card-hd"><b>${escapeHtml(it.name || it.symbol)}</b>
        <span class="cp3-pill cp3-unknown">${it.state === "insufficient_history" ? "insufficient history" : "unavailable"}</span></div>
      <p class="cp3-note">${why}</p></div>`;
  }
  const down = it.d_total - it.d_churn;          // closed down ≥0.2%
  const flat = it.d_churn;                       // went nowhere, weak close
  const pctl = it.d_pctl == null ? null : Math.round(it.d_pctl * 100);
  const hi = pctl != null && pctl >= 90;
  const pctlNote = pctl == null ? "" : `<p class="cp3-pctl${hi ? " cp3-pctl-hi" : ""}">
      That is the <b>${pctl}<sup>${_cp3Ord(pctl)}</sup> percentile</b> of this index's own history —
      typical is ${it.d_median_own_history}.
      <span class="cp3-dim">${it.d_history_n.toLocaleString()} windows${it.d_history_from ? ` since ${escapeHtml(it.d_history_from.slice(0, 4))}` : ""}</span></p>`;
  const ev = (it.events || []).filter(e => e.alive).slice(-10).reverse().map(e =>
    `<li><span class="cp3-ev-d">${escapeHtml(e.d)}</span>
       <span class="cp3-ev-k cp3-ev-${e.kind}">${e.kind === "dist" ? "down" : "flat"}</span>
       <span class="cp3-ev-n">${e.ret_pct > 0 ? "+" : ""}${e.ret_pct}%</span>
       <span class="cp3-ev-n">volume ×${e.vol_ratio ?? "—"}</span>
       <span class="cp3-ev-n">closed ${e.rho == null ? "—" : Math.round(e.rho * 100) + "% up the range"}</span></li>`).join("");
  const retired = (it.events || []).filter(e => !e.alive).length;
  // The same question over a quarter instead of a day: did heavy volume buy any
  // net progress?
  const q = { CHURN: ["cp3-er-bad", "heavy volume, no net progress"],
              DECLINE: ["cp3-er-bad", "net decline"],
              CLEAR: ["cp3-er-ok", "progress in line with volume"],
              insufficient_history: ["cp3-unknown", "insufficient history"] }[it.er_state]
            || ["cp3-unknown", it.er_state];
  return `<div class="cp3-card">
    <div class="cp3-card-hd"><b>${escapeHtml(it.name)}</b> <span class="cp3-dim">${escapeHtml(it.short)}</span></div>
    <div class="cp3-big"><span class="cp3-d">${it.d_total}</span>
      <span class="cp3-d-lbl">of the last 25 sessions</span></div>
    <div class="cp3-split">
      <span class="cp3-split-i"><b>${down}</b> closed <span class="cp3-ev-dist">down</span> ≥0.2%</span>
      <span class="cp3-split-i"><b>${flat}</b> went <span class="cp3-ev-churn">nowhere</span> on a weak close</span>
    </div>
    ${pctlNote}
    <div class="cp3-sub">
      <span class="cp3-chip ${q[0]}">Over 13 weeks: <b>${q[1]}</b> —
        volume ×${it.effort ?? "—"} vs the prior 13 weeks, price ${it.result_pct > 0 ? "+" : ""}${it.result_pct}%</span>
    </div>
    ${ev ? `<details class="cp3-events"><summary>Which days${retired ? ` · ${retired} since retired` : ""}</summary><ul>${ev}</ul></details>` : ""}
    <p class="cp3-asof">as of ${escapeHtml(it.as_of)} · ${it.bars.toLocaleString()} completed sessions${it.partial_bar_dropped ? " · today's partial session excluded" : ""}${it.unknown_volume_bars ? ` · ⚠ ${it.unknown_volume_bars} session(s) with unknown volume` : ""}</p>
    ${it.staleness_note ? `<p class="cp3-stale">⚠ ${escapeHtml(it.staleness_note)}</p>` : ""}
  </div>`;
}

async function renderCp3() {
  els.tableEyebrow.textContent = "Market context";
  els.tableTitle.textContent = "Distribution Days";
  els.tableMeta.textContent = "";
  els.content.innerHTML = `<div class="cn-fear-loading">Loading…</div>`;
  let d;
  try { d = await fetchJson(`/api/cp3?vol_bar=${encodeURIComponent(_CP3.volBar)}`); }
  catch (err) { els.content.innerHTML = `<div class="sc-error">⚠ ${escapeHtml(err.message)}</div>`; return; }

  const items = (d.items || []).filter(i => i.market === _CP3.market);
  const mktTabs = (d.markets || []).map(m =>
    `<button class="cp3-seg-btn${m.k === _CP3.market ? " active" : ""}" data-cp3mkt="${m.k}">${escapeHtml(m.label)}</button>`).join("");
  const vbTabs = [["prior_day", "the day before"], ["sma25", "its 25-day average"]].map(([k, lbl]) =>
    `<button class="cp3-seg-btn${k === _CP3.volBar ? " active" : ""}" data-cp3vb="${k}">${escapeHtml(lbl)}</button>`).join("");

  els.content.innerHTML = `
    <p class="overall-description"><b>How many of the last 25 sessions went badly on rising volume?</b>
      Two cases are counted: the index <b>closed down at least 0.2%</b>, or it <b>went nowhere</b> (within ±0.2%)
      and closed in the lower half of its range — both while trading <b>more volume than the reference bar</b>.
      Heavy volume that buys no upward progress is what institutional selling into a rally looks like.
      A day drops out of the count after 25 sessions, or as soon as the index trades 5% above that day's close —
      if the market has run past where the selling happened, the sellers were wrong.</p>
    <div class="cp3-controls">
      <div class="cp3-field"><span class="cp3-field-lbl">Market</span><span class="cp3-seg">${mktTabs}</span></div>
      <div class="cp3-field"><span class="cp3-field-lbl">"Rising volume" means more than</span><span class="cp3-seg">${vbTabs}</span>
        <span class="cp3-dim cp3-field-hint">The day before is the stricter, more sensitive test and is the published
          rule for down days; the 25-day average is the published rule for flat days. They disagree materially, so both
          are shown rather than one being picked.</span></div>
    </div>
    <div class="cp3-grid">${items.map(cp3Card).join("")}</div>
    <div class="cp3-banner">
      <b>Read the percentile, not the raw count.</b> Measured over the full history of each index — back to 1950 for the
      S&amp;P — a count of 4 or more occurs in <b>59–74%</b> of all 25-session windows, and down-days alone make up
      roughly 17–20% of every session. So the raw number crosses the textbook "warning" level most of the time, in
      rising markets included. What carries information is whether the count is unusual <i>for this index</i>.
    </div>
    <details class="cp3-caveats"><summary>Method and caveats</summary>
      <ul>
        <li>Cash indexes only. ETF volume includes creation/redemption flow rather than pure directional trading, and
          the Dow's reported "volume" is a 30-name aggregate, not exchange tape volume.</li>
        <li>Index volume carries the triple-witching fingerprint — 1.6–1.8× its own 50-day median on the four expiry
          Fridays a year against 1.00× on ordinary sessions, with the mirror collapse on half-days. That is strong
          evidence it is real tape volume, though not a reconciliation against an exchange-published figure.</li>
        <li>Only completed sessions are counted. Volume accumulates through a session while price does not, so a live
          bar always reads as light volume — which here would hide a selling day rather than show it.</li>
        <li>A-share volume is reported in lots rather than shares. Every test here is a ratio, so the unit is irrelevant.</li>
        <li><b>This is descriptive, not a signal.</b> Tested on its own as a sell trigger, clusters of these days have
          historically been negative-edge — shorting them lost money and fading them made money. Treat it as one input
          among several, never as an instruction. Full numbers in <code>docs/CP3_CALIBRATION.md</code>.</li>
      </ul></details>`;

  els.content.querySelectorAll("[data-cp3mkt]").forEach(b =>
    b.addEventListener("click", () => { _CP3.market = b.dataset.cp3mkt; renderCp3(); }));
  els.content.querySelectorAll("[data-cp3vb]").forEach(b =>
    b.addEventListener("click", () => { _CP3.volBar = b.dataset.cp3vb; renderCp3(); }));
}

// ── Market Index panel ───────────────────────────────────────────────────────
// Drives the SAME chart engine the stock pages use (drawAdvancedChart), so the
// indicator controls, moving averages, volume, relative strength and oscillator
// panel behave identically. Nothing is re-implemented here — this only picks an
// instrument and hands over the series.
const _MI = { symbol: "SPY", period: "1Y", mode: "candle", interval: "D",
              maType: "sma", osc: "none", boll: false, showRS: false,
              volMa: null,           // null = O'Neil default (50d / 10w)
              data: null, list: null };
const _MI_PERIODS = ["3M", "6M", "YTD", "1Y", "3Y", "5Y"];

function _miFromDate(period, rows) {
  if (!rows.length) return null;
  const now = new Date();
  let cut;
  if (period === "3M") cut = new Date(now.getFullYear(), now.getMonth() - 3, now.getDate());
  else if (period === "6M") cut = new Date(now.getFullYear(), now.getMonth() - 6, now.getDate());
  else if (period === "YTD") cut = new Date(now.getFullYear(), 0, 1);
  else if (period === "1Y") cut = new Date(now.getFullYear() - 1, now.getMonth(), now.getDate());
  else if (period === "3Y") cut = new Date(now.getFullYear() - 3, now.getMonth(), now.getDate());
  else return rows[0].d;
  return cut.toISOString().slice(0, 10);
}

async function renderMarketIndex() {
  els.tableEyebrow.textContent = "Market context";
  els.tableTitle.textContent = "Market Index";
  els.tableMeta.textContent = "";
  els.content.innerHTML = `<div class="cn-fear-loading">Loading…</div>`;

  if (!_MI.list) {
    try { _MI.list = (await fetchJson("/api/index_list")).instruments || []; }
    catch (err) { els.content.innerHTML = `<div class="sc-error">⚠ ${escapeHtml(err.message)}</div>`; return; }
  }
  const groups = [...new Set(_MI.list.map(i => i.grp))];
  const picker = groups.map(g => `<optgroup label="${escapeHtml(g)}">`
      + _MI.list.filter(i => i.grp === g)
          .map(i => `<option value="${escapeHtml(i.t)}"${i.t === _MI.symbol ? " selected" : ""}>${escapeHtml(i.name)}</option>`).join("")
      + `</optgroup>`).join("");

  els.content.innerHTML = `
    <p class="overall-description">The same chart engine the stock pages use, pointed at an index or asset instead of a single company — candles, your own moving averages, volume, relative strength versus the S&amp;P 500, and an optional oscillator. Descriptive context, <b>not a buy/sell signal</b>; no backtest has been run.</p>
    <div class="mi-controls">
      <div class="mi-row">
        <label class="mi-field mi-field-wide">Instrument<select id="miSymbol">${picker}</select></label>
        <div class="mi-field"><span class="mi-field-lbl">Period</span>
          <span class="mi-seg" id="miPeriods">${_MI_PERIODS.map(p =>
            `<button class="mi-seg-btn${p === _MI.period ? " active" : ""}" data-miperiod="${p}">${p}</button>`).join("")}</span>
        </div>
        <div class="mi-field"><span class="mi-field-lbl">Bars</span>
          <span class="mi-seg">
            <button class="mi-seg-btn${_MI.interval === "D" ? " active" : ""}" data-miint="D" title="Daily bars">Daily</button>
            <button class="mi-seg-btn${_MI.interval === "W" ? " active" : ""}" data-miint="W" title="Weekly bars — Weinstein's native cadence">Weekly</button>
          </span>
        </div>
      </div>
      <div class="mi-row">
        <div class="mi-field"><span class="mi-field-lbl">Moving averages</span>
          <span class="mi-seg">
            <button class="mi-seg-btn${_MI.maType === "sma" ? " active" : ""}" data-mima="sma">SMA</button>
            <button class="mi-seg-btn${_MI.maType === "ema" ? " active" : ""}" data-mima="ema">EMA</button>
          </span>
        </div>
        <div class="mi-field"><span class="mi-field-lbl">Price MA periods</span>
          <span class="mi-ma-inputs" title="Editable — counted in the displayed interval">
            <input type="number" class="sc-ma-in" id="miMa0" min="2" max="400" step="1">
            <input type="number" class="sc-ma-in" id="miMa1" min="2" max="400" step="1">
            <input type="number" class="sc-ma-in" id="miMa2" min="2" max="400" step="1">
          </span>
        </div>
        <div class="mi-field"><span class="mi-field-lbl">Volume MA</span>
          <span class="mi-ma-inputs" title="Period of the volume moving average — the navy line across the volume bars, and the baseline the '% vs avg' figure is measured against. Counted in the displayed interval. Blank uses O'Neil's default: 50 on daily bars, 10 on weekly.">
            <input type="number" class="sc-ma-in" id="miVolMa" min="2" max="400" step="1" placeholder="50">
          </span>
        </div>
        <div class="mi-field"><span class="mi-field-lbl">Panels</span>
          <span class="mi-seg">
            <button class="mi-seg-btn${_MI.boll ? " active" : ""}" id="miBoll" title="Bollinger Bands (20, 2σ) — a price overlay on the candles">Bollinger</button>
            <button class="mi-seg-btn${_MI.showRS ? " active" : ""}" id="miRS" title="Relative-strength panel: this instrument divided by the S&P 500. Off by default here — for the S&P itself it is flat by definition.">Rel. strength</button>
          </span>
        </div>
        <label class="mi-field mi-field-wide">Oscillator panel
          <select class="sc-osc-sel" id="miOsc" title="Computed on the displayed interval — on weekly bars RSI(14) means 14 weeks">
            <option value="none">None</option>
            <option value="rsi">RSI (14)</option>
            <option value="macd">MACD (12/26/9)</option>
            <option value="stoch">Stochastic (14/3)</option>
            <option value="roc">Rate of Change (12)</option>
            <option value="aroon">Aroon (25)</option>
          </select>
        </label>
      </div>
    </div>
    <div id="miMeta" class="mi-meta"></div>
    <div id="miChart"><div class="cn-fear-loading">Loading price history…</div></div>
    <div id="scAdvReadout" class="sc-adv-readout"></div>
    <p class="overall-description" id="miNote"></p>`;

  const sel = document.getElementById("miSymbol");
  sel.addEventListener("change", () => { _MI.symbol = sel.value; _MI.data = null; _miLoad(); });
  els.content.querySelectorAll("[data-miperiod]").forEach(b => b.addEventListener("click", () => {
    _MI.period = b.dataset.miperiod;
    els.content.querySelectorAll("[data-miperiod]").forEach(x => x.classList.toggle("active", x === b));
    _miDraw();
  }));
  els.content.querySelectorAll("[data-miint]").forEach(b => b.addEventListener("click", () => {
    _MI.interval = b.dataset.miint;
    els.content.querySelectorAll("[data-miint]").forEach(x => x.classList.toggle("active", x === b));
    _miSyncMaInputs(); _miDraw();
  }));
  els.content.querySelectorAll("[data-mima]").forEach(b => b.addEventListener("click", () => {
    _MI.maType = b.dataset.mima;
    els.content.querySelectorAll("[data-mima]").forEach(x => x.classList.toggle("active", x === b));
    _miDraw();
  }));
  document.getElementById("miBoll").addEventListener("click", e => {
    _MI.boll = !_MI.boll; e.currentTarget.classList.toggle("active", _MI.boll); _miDraw();
  });
  document.getElementById("miRS").addEventListener("click", e => {
    _MI.showRS = !_MI.showRS; e.currentTarget.classList.toggle("active", _MI.showRS); _miDraw();
  });
  const oscSel = document.getElementById("miOsc");
  oscSel.value = _MI.osc;
  oscSel.addEventListener("change", () => { _MI.osc = oscSel.value; _miDraw(); });
  ["miMa0", "miMa1", "miMa2", "miVolMa"].forEach(id =>
    document.getElementById(id).addEventListener("change", _miDraw));
  _miSyncMaInputs();
  await _miLoad();
}

function _miSyncMaInputs() {
  const d = _ADV_DEFAULT_PERIODS[_MI.interval] || [50, 150, 200];
  ["miMa0", "miMa1", "miMa2"].forEach((id, i) => {
    const el = document.getElementById(id);
    if (el) el.value = d[i];
  });
  // The volume default differs by interval (50 daily / 10 weekly), so the
  // placeholder has to follow it or it would advertise the wrong fallback.
  const vm = document.getElementById("miVolMa");
  if (vm) vm.placeholder = _MI.interval === "W" ? "10" : "50";
}

async function _miLoad() {
  const box = document.getElementById("miChart");
  if (box) box.innerHTML = `<div class="cn-fear-loading">Loading ${escapeHtml(_MI.symbol)}…</div>`;
  try {
    _MI.data = await fetchJson(`/api/index_history?symbol=${encodeURIComponent(_MI.symbol)}`);
  } catch (err) {
    if (box) box.innerHTML = `<div class="sc-error">⚠ ${escapeHtml(err.message)}</div>`;
    return;
  }
  _miDraw();
}

function _miDraw() {
  const d = _MI.data, box = document.getElementById("miChart");
  if (!box) return;
  if (!d || d.status !== "ready") {
    box.innerHTML = `<div class="sc-error">⚠ ${escapeHtml((d && d.error) || "Unavailable")}</div>`;
    return;
  }
  const rows = d.prices || [];
  const benchMap = Object.fromEntries((d.benchmark || []).map(b => [b.d, b.c]));
  const periods = ["miMa0", "miMa1", "miMa2"]
    .map(id => parseInt(document.getElementById(id)?.value, 10))
    .filter(n => Number.isFinite(n) && n >= 2);

  drawAdvancedChart(box, rows, benchMap, {
    interval: _MI.interval, maType: _MI.maType,
    fromDate: _miFromDate(_MI.period, rows),
    zoomKey: _MI.symbol,              // a new instrument is a new view, not a pan
    benchSymbol: d.benchmark_symbol, benchLabel: d.benchmark_name,
    periods: periods.length ? periods : undefined,
    osc: _MI.osc, boll: _MI.boll, showRS: _MI.showRS,
    volMa: parseInt(document.getElementById("miVolMa")?.value, 10) || null,
    epsData: null,                    // an index has no earnings line
  });

  const last = rows[rows.length - 1] || {};
  const first = rows.find(r => r.d >= (_miFromDate(_MI.period, rows) || rows[0].d)) || rows[0];
  const chg = first && first.c ? (last.c / first.c - 1) * 100 : null;
  const meta = document.getElementById("miMeta");
  if (meta) meta.innerHTML = `<span class="mi-meta-name">${escapeHtml(d.name)}</span>`
    + `<span class="mi-meta-px">${last.c == null ? "—" : (+last.c).toLocaleString(undefined, {maximumFractionDigits: 2})}</span>`
    + (chg == null ? "" : `<span class="mi-meta-chg ${chg >= 0 ? "stage-pos" : "stage-neg"}">${chg >= 0 ? "+" : ""}${chg.toFixed(2)}% over ${_MI.period}</span>`)
    + `<span class="mi-meta-sub">${rows.length} daily bars · through ${escapeHtml(last.d || "—")}</span>`;

  const note = document.getElementById("miNote");
  if (note) note.innerHTML =
    (!_MI.showRS
      ? `Relative strength is off — turn on <b>Rel. strength</b> above to add a panel showing this instrument ÷ ${escapeHtml(d.benchmark_name)}.`
      : d.is_benchmark
        ? "This <b>is</b> the benchmark, so its relative-strength panel is flat by definition — RS compares an instrument to the S&amp;P 500."
        : `Relative strength is this instrument ÷ <b>${escapeHtml(d.benchmark_name)}</b>, the raw ratio rather than the Mansfield-normalised form used on the Stage screen.`)
    + (d.no_volume
      ? " <b>This is an index, not a traded fund, so it has no volume</b> — the volume panel is empty by nature, not by a data failure."
      : "")
    + " Oscillator periods are counted in the displayed interval, so on weekly bars RSI(14) means 14 <em>weeks</em>; the label says which.";
}

function renderCnLimit(d, box) {
  if (d.status !== "ready") {
    box.innerHTML = `<div class="sc-error">⚠ ${escapeHtml(d.error || "Unavailable")}</div>`;
    return;
  }
  const pctileTag = (p) => {
    if (!p || p.pctile == null) return "";
    const hot = p.pctile >= 90 ? " cnlim-pctile-hot" : "";
    return `<span class="cnlim-pctile${hot}">${p.pctile}th pctile (${p.n_days}d)</span>`;
  };
  const rows = (d.boards || []).map(b => `
    <tr>
      <td class="cnlim-board">${escapeHtml(b.board)}</td>
      <td class="cnlim-num">${b.n_eligible}</td>
      <td class="cnlim-num cnlim-up">${b.up_share}%<span class="cnlim-count">(${b.up_count})</span>${pctileTag(b.up_pctile)}</td>
      <td class="cnlim-num cnlim-up">${b.up_locked_share}%<span class="cnlim-count">(${b.up_locked_count})</span></td>
      <td class="cnlim-num cnlim-down">${b.down_share}%<span class="cnlim-count">(${b.down_count})</span>${pctileTag(b.down_pctile)}</td>
      <td class="cnlim-num cnlim-down">${b.down_locked_share}%<span class="cnlim-count">(${b.down_locked_count})</span></td>
    </tr>`).join("");
  const hs = d.history_status || {};
  const histNote = hs.ready
    ? `Benchmarked against its own trailing ~2-year history (rolling percentile shown next to Limit-up/Limit-down).`
    : hs.building
      ? `Building the ~2-year history benchmark in the background (${hs.progress || 0}/${hs.total || "?"} days) — percentiles will appear once it's done; this can take several minutes and only needs to run once.`
      : `Historical benchmark not built yet.`;
  box.innerHTML = `
    <p class="overall-description">How many names closed at the daily price limit today, board by board — Main/ChiNext/STAR/BSE have different limit bands (10%/20%/20%/30%), so pooling them into one market-wide number distorts the reading (confirmed: an 8x spread in touch rates across boards on one sampled date). "Locked" (一字) means the stock opened AND closed at the limit all day — untradeable, a sharper signal than an ordinary close-at-limit. This is a descriptive state, not a buy/sell signal — limit heat is horizon-dependent (moderate heat can predict continuation, extreme heat can signal exhaustion).</p>
    <div class="cnlim-table-wrap">
      <table class="cnlim-table">
        <thead><tr>
          <th>Board</th><th>N</th><th>Limit-up</th><th>Locked up (一字)</th><th>Limit-down</th><th>Locked down (一字)</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <p class="overall-description">As of ${escapeHtml(d.date || "—")}. Close-based only (Tushare's own daily classification) — doesn't yet capture names that touched the limit intraday but failed to close there. ${escapeHtml(histNote)} The "locked" columns don't have a percentile yet — their counts are usually too small (often single digits) for a naive percentile to mean much; that needs a sparse-count-aware method, not built here yet.</p>`;
}

async function loadCnLimit() {
  const box = document.getElementById("cnLimitBox");
  if (!box) return;
  try {
    const d = await fetchJson("/api/cn_limit");
    renderCnLimit(d, box);
  } catch (err) {
    box.innerHTML = `<div class="sc-error">⚠ ${escapeHtml(err.message)}</div>`;
  }
}

function _fmtYuan(v) {
  if (v == null) return "—";
  const abs = Math.abs(v);
  const s = abs >= 1e8 ? `¥${(v / 1e8).toFixed(1)}亿` : `¥${(v / 1e4).toFixed(0)}万`;
  return v > 0 ? `+${s}` : (v < 0 ? `-${s.replace("-", "")}` : s);
}

// unsigned magnitude — for gross buy/sell totals, which are never "net" and
// shouldn't carry a misleading + prefix like they're their own bullish signal
function _fmtYuanAbs(v) {
  if (v == null) return "—";
  const abs = Math.abs(v);
  return abs >= 1e8 ? `¥${(abs / 1e8).toFixed(1)}亿` : `¥${(abs / 1e4).toFixed(0)}万`;
}

function renderCnFlow(d, box) {
  if (d.status !== "ready") {
    box.innerHTML = `<div class="sc-error">⚠ ${escapeHtml(d.error || "Unavailable")}</div>`;
    return;
  }
  const mf = d.main_force;
  const rs = d.retail_soi;
  const card = (label, value, sub, desc) => {
    const cls = value == null ? "neutral" : value > 0 ? "positive" : "danger";
    return `<div class="indicator-card ${cls}">
      <div class="indicator-card-header"><span class="indicator-card-label">${escapeHtml(label)}</span></div>
      <div class="indicator-value ${cls}">${_fmtYuan(value)}</div>
      <div class="indicator-reading">${escapeHtml(sub)}</div>
      <details class="ind-detail"><summary>ⓘ What it means</summary><p>${escapeHtml(desc)}</p></details>
    </div>`;
  };
  const cards = [
    mf ? card("Whole-Market Large-Order Flow", mf.net_yuan,
      `${mf.n_stocks} stocks · buy ${_fmtYuanAbs(mf.buy_yuan)} / sell ${_fmtYuanAbs(mf.sell_yuan)}`,
      "Net flow from large + extra-large orders (主力资金) summed across the whole A-share market for the day — the standard CN proxy for institutional-sized trading vs. retail odd-lot activity. Positive = big money net buying.") : "",
    rs ? card("Retail-Skewed Small-Order Flow", rs.net_yuan,
      `SOI ${rs.soi != null ? (rs.soi > 0 ? "+" : "") + (rs.soi * 100).toFixed(1) + "%" : "—"} · buy ${_fmtYuanAbs(rs.buy_yuan)} / sell ${_fmtYuanAbs(rs.sell_yuan)}`,
      "Net flow from small orders (≤¥5万, 小单) — a proxy for retail-sized trading, NOT verified retail identity (institutions split large orders into small ones too; this is a fixed-nominal-size-band inference, per the Indicators Pool's CN-RET1 spec). SOI = net ÷ gross small-order flow, −100% to +100%. Compare against the large-order card above: when they diverge (e.g. institutions selling while small orders buy), that's a distribution-vs-absorption pattern worth noting.") : "",
  ].join("");

  const sf = d.sector_flow || [];
  const inflow = sf.slice(0, 6);
  const outflow = sf.slice(-6).reverse();
  const sectorRows = (items) => {
    const maxAbs = Math.max(1, ...items.map(x => Math.abs(x.net_yuan)));
    return items.map(x => {
      const pct = Math.min(100, Math.abs(x.net_yuan) / maxAbs * 100);
      const color = x.net_yuan >= 0 ? "#16a34a" : "#dc2626";
      return `<div class="momconc-row">
        <span class="momconc-ind">${escapeHtml(x.industry)}</span>
        <div class="momconc-bar-track"><div class="momconc-bar" style="width:${pct.toFixed(0)}%;background:${color}"></div></div>
        <span class="momconc-pct">${_fmtYuan(x.net_yuan)}</span>
      </div>`;
    }).join("");
  };
  const sectorHtml = sf.length ? `
    <div class="momconc-chart-row" style="margin-top:12px">
      <div><p class="momconc-chart-title">Top institutional inflow sectors</p><div class="momconc-chart-wrap">${sectorRows(inflow)}</div></div>
      <div><p class="momconc-chart-title">Top institutional outflow sectors</p><div class="momconc-chart-wrap">${sectorRows(outflow)}</div></div>
    </div>` : "";

  box.innerHTML = `<div class="indicator-grid">${cards}</div>
    <p class="overall-description">As of ${escapeHtml(d.date || "—")}. A single-day flow read (no rolling history/percentile yet), separate from the Fear &amp; Greed composite above — this is about who's net buying, not overall positioning intensity.</p>
    ${sectorHtml}`;
}

async function loadCnFlow() {
  const box = document.getElementById("cnFlowBox");
  if (!box) return;
  try {
    const d = await fetchJson("/api/cn_flow");
    renderCnFlow(d, box);
  } catch (err) {
    box.innerHTML = `<div class="sc-error">⚠ ${escapeHtml(err.message)}</div>`;
  }
}

async function loadCnVix() {
  const box = document.getElementById("cnVixBox");
  if (!box) return;
  try {
    const d = await fetchJson("/api/cn_vix");
    if (d.status !== "ready") { box.innerHTML = ""; return; }
    renderCnVix(d, box);
  } catch { box.innerHTML = ""; }
}

let _cnVix = { hist: [], range: "2Y" };
const _CN_VIX_RANGES = { "6M": 26, "1Y": 52, "2Y": 104, "5Y": 260, "All": 99999 };

function renderCnVix(d, box) {
  const skColor = d.skew > 0.02 ? "#dc2626" : d.skew < -0.02 ? "#16a34a" : "var(--muted)";
  _cnVix.hist = d.history || [];
  const rangeBtns = Object.keys(_CN_VIX_RANGES).map(r =>
    `<button class="cn-fear-rbtn${r === _cnVix.range ? " active" : ""}" data-range="${r}">${r}</button>`).join("");
  box.innerHTML = `
    <div class="cn-vix-card">
      <div class="cn-vix-head">
        <div class="cn-vix-title">China VIX (QVIX) <span class="cn-vix-sub">— 50ETF option-implied vol · standalone reference (not in the composite)</span></div>
        <div class="cn-vix-stats">
          <div class="cn-vix-stat"><span class="cn-vix-lab">QVIX (30-day IV)</span><strong>${d.qvix}</strong><span class="cn-vix-ctx">${d.level_word} · ${d.level_pct}th pctile</span></div>
          <div class="cn-vix-stat"><span class="cn-vix-lab">Put-call skew</span><strong style="color:${skColor}">${d.skew > 0 ? "+" : ""}${d.skew}</strong><span class="cn-vix-ctx">${escapeHtml(d.skew_read)}</span></div>
          <div class="cn-vix-stat"><span class="cn-vix-lab">As of</span><strong>${escapeHtml(d.date)}</strong><span class="cn-vix-ctx">weekly (Fri)</span></div>
        </div>
      </div>
      <div class="cn-chart-head">
        <div class="cn-chart-title">QVIX over time <span class="cn-chart-sub">— 30-day implied volatility, weekly</span></div>
        <div class="cn-fear-range" id="cnVixRange">${rangeBtns}</div>
      </div>
      <div class="cn-chart-wrap">
        <svg id="cnVixChart" viewBox="0 0 1000 300"></svg>
        <div id="cnVixTip" class="cn-chart-tip" hidden></div>
      </div>
      <details class="cn-vix-explain"><summary>ⓘ What QVIX is &amp; how to read it</summary>
        <div>
          <p><strong>What it is:</strong> a China VIX built from scratch off the 50ETF (510050) option chain — the 30-day implied volatility the options market is pricing, from a 3-strike near-the-money average (Black-76 ATM vol, near/next expiries interpolated to 30 days). <strong>This is a simplified proxy, not CBOE's model-free variance-swap construction</strong> (which integrates the full OTM strike chain, weighted by 1/K² — a different, more involved calculation than a near-ATM average). Validated against the official SSE iVIX on its 2015–2018 overlap (level corr 0.98) before it was frozen — that correlation is a level check, not proof the two methodologies compute the same thing.</p>
          <p><strong>Level = intensity, and it is DIRECTION-BLIND.</strong> High QVIX means the market expects big moves — of <em>either</em> sign. Unlike the US VIX, high A-share implied vol is <em>not</em> a contrarian buy: its forward-return quintiles are anti-contrarian (buying high A-share vol has historically lost). So don't read a high QVIX as "fear = opportunity" on its own.</p>
          <p><strong>Skew = direction (the useful part).</strong> Put-call skew = IV(5%-OTM put) − IV(5%-OTM call). <span style="color:#dc2626;font-weight:700">Positive</span> = puts bid = genuine hedging/fear; <span style="color:#16a34a;font-weight:700">negative</span> = calls bid = euphoria/upside-chasing (e.g. it went deeply negative at the Oct-2024 blow-off top). This recovers the direction the level can't.</p>
          <p><strong>Why it's a standalone reference, not a composite leg:</strong> the 6-leg Fear &amp; Greed index already fuses intensity + direction, so adding QVIX added nothing. Use QVIX/skew as the eyeball overlay that explains <em>why</em> the composite is where it is — regime magnitude and option-market positioning context.</p>
        </div>
      </details>
    </div>`;
  box.querySelectorAll("#cnVixRange .cn-fear-rbtn").forEach(b => b.addEventListener("click", () => {
    _cnVix.range = b.dataset.range;
    box.querySelectorAll("#cnVixRange .cn-fear-rbtn").forEach(x => x.classList.toggle("active", x === b));
    _cnVixDrawChart();
  }));
  _cnVixDrawChart();
}

function _cnVixDrawChart() {
  const svg = document.getElementById("cnVixChart");
  if (!svg) return;
  const W = 1000, H = 300, L = 44, R = 16, T = 16, B = 34, pw = W - L - R, ph = H - T - B;
  const n = _CN_VIX_RANGES[_cnVix.range] || 99999;
  const data = _cnVix.hist.slice(-n);
  if (data.length < 2) { svg.innerHTML = ""; return; }
  const qs = data.map(p => p.q);
  let mn = Math.min(...qs), mx = Math.max(...qs);
  const pad = (mx - mn) * 0.08 || 1; mn = Math.max(0, mn - pad); mx += pad;
  const x = (i) => L + i / (data.length - 1) * pw;
  const y = (q) => T + (mx - q) / (mx - mn) * ph;
  const NS = ` vector-effect="non-scaling-stroke"`;
  let g = "";
  // y gridlines (5 ticks)
  for (let k = 0; k <= 4; k++) {
    const val = mn + (mx - mn) * k / 4, yy = y(val).toFixed(1);
    g += `<line x1="${L}" y1="${yy}" x2="${W - R}" y2="${yy}" stroke="var(--line)" stroke-width="0.7"${NS}/>`;
    g += `<text x="${L - 7}" y="${(+yy + 4).toFixed(1)}" class="cn-ax-y">${Math.round(val)}</text>`;
  }
  const nT = 6;
  for (let k = 0; k < nT; k++) {
    const i = Math.round(k / (nT - 1) * (data.length - 1));
    const [yr, mo] = data[i].d.split("-");
    const xx = x(i).toFixed(1);
    g += `<line x1="${xx}" y1="${T}" x2="${xx}" y2="${T + ph}" stroke="var(--line)" stroke-width="0.5"${NS}/>`;
    g += `<text x="${xx}" y="${H - 10}" class="cn-ax-x" text-anchor="middle">${_MON[+mo]} '${yr.slice(2)}</text>`;
  }
  const pts = data.map((p, i) => `${x(i).toFixed(1)},${y(p.q).toFixed(1)}`);
  const area = `${L},${(T + ph).toFixed(1)} ${pts.join(" ")} ${(L + pw).toFixed(1)},${(T + ph).toFixed(1)}`;
  svg.innerHTML = `
    <defs><linearGradient id="cnVixGrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#7c3aed" stop-opacity="0.26"/><stop offset="100%" stop-color="#7c3aed" stop-opacity="0"/>
    </linearGradient></defs>
    ${g}
    <polygon points="${area}" fill="url(#cnVixGrad)"/>
    <polyline points="${pts.join(" ")}" fill="none" stroke="#7c3aed" stroke-width="1.75" stroke-linejoin="round"${NS}/>
    <g id="cnVixCross" style="display:none">
      <line id="cnVixCrossL" y1="${T}" y2="${T + ph}" stroke="var(--muted)" stroke-width="1" stroke-dasharray="3 3"${NS}/>
      <circle id="cnVixCrossD" r="3.5" fill="#7c3aed" stroke="#fff" stroke-width="1.5"/>
    </g>
    <rect x="${L}" y="${T}" width="${pw}" height="${ph}" fill="transparent" style="cursor:crosshair" id="cnVixHit"/>`;
  const hit = svg.querySelector("#cnVixHit"), cross = svg.querySelector("#cnVixCross");
  const cl = svg.querySelector("#cnVixCrossL"), cd = svg.querySelector("#cnVixCrossD"), tip = document.getElementById("cnVixTip");
  hit.addEventListener("mousemove", (e) => {
    const rect = svg.getBoundingClientRect();
    let i = Math.round(((e.clientX - rect.left) / rect.width * W - L) / pw * (data.length - 1));
    i = Math.max(0, Math.min(data.length - 1, i));
    const p = data[i];
    cross.style.display = "";
    cl.setAttribute("x1", x(i)); cl.setAttribute("x2", x(i));
    cd.setAttribute("cx", x(i)); cd.setAttribute("cy", y(p.q));
    const sk = p.s > 0.02 ? "puts bid (fear)" : p.s < -0.02 ? "calls bid (greed)" : "balanced";
    tip.hidden = false;
    tip.innerHTML = `<strong>${p.d}</strong><br>QVIX ${p.q} · skew ${p.s > 0 ? "+" : ""}${p.s}<br><span style="color:var(--muted)">${sk}</span>`;
    const wr = svg.parentElement.getBoundingClientRect();
    let lft = e.clientX - wr.left + 12; if (lft > wr.width - 120) lft = e.clientX - wr.left - 120;
    tip.style.left = lft + "px"; tip.style.top = (y(p.q) / H * wr.height - 10) + "px";
  });
  hit.addEventListener("mouseleave", () => { cross.style.display = "none"; tip.hidden = true; });
}

let _cnFear = { hist: [], range: "2Y" };
let _cnFearPollTimer = null;

async function loadCnFear() {
  const box = document.getElementById("cnFearBox");
  if (!box) return;
  try {
    const d = await fetchJson("/api/cn_fear");
    if (d.status === "building") {
      box.innerHTML = `<div class="cn-fear-loading">${escapeHtml(d.note || "Building…")}</div>`;
      clearTimeout(_cnFearPollTimer);
      _cnFearPollTimer = setTimeout(loadCnFear, 8000);   // first build takes a while; poll
      return;
    }
    if (d.status !== "ready") { box.innerHTML = `<div class="cn-fear-loading">⚠ ${escapeHtml(d.error || "unavailable")}</div>`; return; }
    renderCnFear(d, box);
    // Auto-refresh: if the cache was stale, a background rebuild was just kicked
    // off server-side — poll once so the fresh reading swaps in with no button.
    clearTimeout(_cnFearPollTimer);
    if (d.stale && document.getElementById("cnFearBox")) _cnFearPollTimer = setTimeout(loadCnFear, 12000);
  } catch (err) {
    box.innerHTML = `<div class="cn-fear-loading">⚠ ${escapeHtml(err.message)}</div>`;
  }
}

const _CN_FEAR_RANGES = { "6M": 126, "1Y": 252, "2Y": 504, "5Y": 1260, "All": 99999 };

function renderCnFear(d, box) {
  const v = d.value, ang = -90 + v / 100 * 180;
  const arc = (from, to, color) => {
    const a0 = (-90 + from / 100 * 180) * Math.PI / 180, a1 = (-90 + to / 100 * 180) * Math.PI / 180;
    const R = 92, cx = 110, cy = 104;
    const x0 = cx + R * Math.sin(a0), y0 = cy - R * Math.cos(a0);
    const x1 = cx + R * Math.sin(a1), y1 = cy - R * Math.cos(a1);
    return `<path d="M ${x0.toFixed(1)} ${y0.toFixed(1)} A ${R} ${R} 0 0 1 ${x1.toFixed(1)} ${y1.toFixed(1)}" fill="none" stroke="${color}" stroke-width="16"/>`;
  };
  const nx = 110 + 74 * Math.sin(ang * Math.PI / 180), ny = 104 - 74 * Math.cos(ang * Math.PI / 180);
  const chg = (x) => x == null ? "" : `${x > 0 ? "+" : ""}${x}`;
  const legs = (d.legs || []).map(l => {
    const c = l.value >= 80 ? "#dc2626" : l.value >= 60 ? "#ea580c" : l.value >= 40 ? "#a16207" : l.value >= 20 ? "#65a30d" : "#16a34a";
    return `<div class="cn-leg"><span class="cn-leg-lab">${escapeHtml(l.label)}</span>
      <span class="cn-leg-bar"><span class="cn-leg-fill" style="width:${l.value}%;background:${c}"></span></span>
      <span class="cn-leg-val">${l.value}</span></div>`;
  }).join("");
  _cnFear.hist = d.history || [];
  const rangeBtns = Object.keys(_CN_FEAR_RANGES).map(r =>
    `<button class="cn-fear-rbtn${r === _cnFear.range ? " active" : ""}" data-range="${r}">${r}</button>`).join("");

  box.innerHTML = `
    <div class="cn-fear-card">
      <div class="cn-fear-main">
        <div class="cn-fear-gauge">
          <svg viewBox="0 0 220 128">
            ${arc(0, 20, "#16a34a")}${arc(20, 40, "#65a30d")}${arc(40, 60, "#a16207")}${arc(60, 80, "#ea580c")}${arc(80, 100, "#dc2626")}
            <line x1="110" y1="104" x2="${nx.toFixed(1)}" y2="${ny.toFixed(1)}" stroke="var(--text)" stroke-width="3"/>
            <circle cx="110" cy="104" r="5" fill="var(--text)"/>
          </svg>
          <div class="cn-fear-num" style="color:${d.color}">${v}</div>
          <div class="cn-fear-zone" style="color:${d.color}">${escapeHtml(d.zone)}</div>
          <div class="cn-fear-meta">${escapeHtml(d.date)} · 1d ${chg(d.change_1d)} · 20d ${chg(d.change_20d)}${d.stale ? " · refreshing…" : ""}</div>
        </div>
        <div class="cn-fear-side">
          <div class="cn-fear-read">${escapeHtml(d.read)}</div>
          <div class="cn-legs">${legs}</div>
          <div class="cn-legs-note">Each bar is a component leg's 5-year percentile (0 = calmest in 5y, 100 = most extreme/greedy). The gauge is their equal-weight average.</div>
          <details class="cn-legs-explain"><summary>ⓘ What each leg means &amp; how to read it</summary><div>
            ${(d.legs || []).map(l => `<p><strong>${escapeHtml(l.label)}</strong> — ${escapeHtml(l.desc || "")}</p>`).join("")}
            <p class="cn-legs-frame">The composite deliberately fuses <em>intensity</em> legs (leverage, leverage-flow, turnover — how hot &amp; participatory the market is) with <em>direction</em> legs (price-strength, momentum — which way it's going). No A-share intensity gauge is directional on its own, so pairing them is what makes one number readable.</p>
          </div></details>
        </div>
      </div>
      <div class="cn-chart-head">
        <div class="cn-chart-title">Fear &amp; Greed index over time <span class="cn-chart-sub">— the composite value each trading day</span></div>
        <div class="cn-fear-range">${rangeBtns}</div>
      </div>
      <div class="cn-chart-wrap">
        <svg id="cnFearChart" viewBox="0 0 1000 320"></svg>
        <div id="cnFearTip" class="cn-chart-tip" hidden></div>
      </div>
      <div class="cn-fear-foot">
        <p><strong>How to read it:</strong> the line is today's sentiment gauge (0 = extreme fear, 100 = extreme greed) plotted back through history. The <span class="cn-fear-lbl" style="color:#16a34a">green dashed line at 25</span> and <span class="cn-fear-lbl" style="color:#dc2626">red dashed line at 75</span> mark <strong>positioning-intensity extremes</strong>, not a validated buy/de-risk timing signal (see note below). Hover any point for its date &amp; value; use the buttons to zoom the window.</p>
        <p class="cn-fear-foot-note">${escapeHtml(d.extremes.note)}</p>
      </div>
    </div>`;

  box.querySelectorAll(".cn-fear-rbtn").forEach(b => b.addEventListener("click", () => {
    _cnFear.range = b.dataset.range;
    box.querySelectorAll(".cn-fear-rbtn").forEach(x => x.classList.toggle("active", x === b));
    _cnFearDrawChart();
  }));
  _cnFearDrawChart();
}

const _MON = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function _cnFearDrawChart() {
  const svg = document.getElementById("cnFearChart");
  if (!svg) return;
  const W = 1000, H = 320, L = 40, R = 16, T = 16, B = 34;   // hi-res viewBox → crisp scaling
  const pw = W - L - R, ph = H - T - B;
  const n = _CN_FEAR_RANGES[_cnFear.range] || 99999;
  const data = _cnFear.hist.slice(-n);
  if (data.length < 2) { svg.innerHTML = ""; return; }
  const x = (i) => L + i / (data.length - 1) * pw;
  const y = (val) => T + (100 - val) / 100 * ph;
  const NS = ` vector-effect="non-scaling-stroke"`;   // strokes stay 1px however the SVG scales

  // subtle zone shading (fear = greenish opportunity, greed = reddish caution)
  let g = `<rect x="${L}" y="${y(100).toFixed(1)}" width="${pw}" height="${(y(75) - y(100)).toFixed(1)}" fill="#dc2626" opacity="0.05"/>`;
  g += `<rect x="${L}" y="${y(25).toFixed(1)}" width="${pw}" height="${(y(0) - y(25)).toFixed(1)}" fill="#16a34a" opacity="0.06"/>`;

  // y gridlines + labels
  [0, 25, 50, 75, 100].forEach(t => {
    const yy = y(t).toFixed(1);
    const thresh = (t === 25 || t === 75);
    const dash = thresh ? ` stroke-dasharray="2 3"` : "";
    const col = t === 25 ? "#16a34a" : t === 75 ? "#dc2626" : "var(--line)";
    g += `<line x1="${L}" y1="${yy}" x2="${W - R}" y2="${yy}" stroke="${col}" stroke-width="${thresh ? 1 : 0.75}"${dash}${NS}/>`;
    g += `<text x="${L - 7}" y="${(+yy + 3).toFixed(1)}" class="cn-ax-y">${t}</text>`;
  });
  g += `<text x="${W - R - 3}" y="${(y(75) - 4).toFixed(1)}" class="cn-ax-lbl" fill="#dc2626" text-anchor="end">75</text>`;
  g += `<text x="${W - R - 3}" y="${(y(25) - 4).toFixed(1)}" class="cn-ax-lbl" fill="#16a34a" text-anchor="end">25</text>`;

  // x date ticks (~6) — CONSISTENT "Mon 'YY" across every zoom range
  const nTicks = 6;
  for (let k = 0; k < nTicks; k++) {
    const idx = Math.round(k / (nTicks - 1) * (data.length - 1));
    const [yr, mo] = data[idx].d.split("-");
    const lab = `${_MON[+mo]} '${yr.slice(2)}`;
    const xx = x(idx).toFixed(1);
    g += `<line x1="${xx}" y1="${T}" x2="${xx}" y2="${T + ph}" stroke="var(--line)" stroke-width="0.5"${NS}/>`;
    g += `<text x="${xx}" y="${H - 10}" class="cn-ax-x" text-anchor="middle">${lab}</text>`;
  }

  // gradient area fill under the line + the line itself
  const linePts = data.map((p, i) => `${x(i).toFixed(1)},${y(p.v).toFixed(1)}`);
  const area = `${L},${(T + ph).toFixed(1)} ${linePts.join(" ")} ${(L + pw).toFixed(1)},${(T + ph).toFixed(1)}`;
  svg.innerHTML = `
    <defs>
      <linearGradient id="cnFearGrad" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="var(--accent)" stop-opacity="0.28"/>
        <stop offset="100%" stop-color="var(--accent)" stop-opacity="0"/>
      </linearGradient>
    </defs>
    ${g}
    <polygon points="${area}" fill="url(#cnFearGrad)"/>
    <polyline points="${linePts.join(" ")}" fill="none" stroke="var(--accent)" stroke-width="1.75" stroke-linejoin="round"${NS}/>
    <g id="cnFearCross" style="display:none">
      <line id="cnFearCrossL" y1="${T}" y2="${T + ph}" stroke="var(--muted)" stroke-width="1" stroke-dasharray="3 3"${NS}/>
      <circle id="cnFearCrossD" r="3.5" fill="var(--accent)" stroke="#fff" stroke-width="1.5"/>
    </g>
    <rect x="${L}" y="${T}" width="${pw}" height="${ph}" fill="transparent" style="cursor:crosshair" id="cnFearHit"/>`;

  const hit = svg.querySelector("#cnFearHit");
  const cross = svg.querySelector("#cnFearCross");
  const crossL = svg.querySelector("#cnFearCrossL");
  const crossD = svg.querySelector("#cnFearCrossD");
  const tip = document.getElementById("cnFearTip");
  hit.addEventListener("mousemove", (e) => {
    const rect = svg.getBoundingClientRect();
    const vbX = (e.clientX - rect.left) / rect.width * W;
    let i = Math.round((vbX - L) / pw * (data.length - 1));
    i = Math.max(0, Math.min(data.length - 1, i));
    const p = data[i];
    cross.style.display = "";
    crossL.setAttribute("x1", x(i)); crossL.setAttribute("x2", x(i));
    crossD.setAttribute("cx", x(i)); crossD.setAttribute("cy", y(p.v));
    const zone = p.v < 20 ? "Extreme Fear" : p.v < 40 ? "Fear" : p.v < 60 ? "Neutral" : p.v < 80 ? "Greed" : "Extreme Greed";
    tip.hidden = false;
    tip.innerHTML = `<strong>${p.d}</strong><br>${p.v} · ${zone}`;
    const wrapRect = svg.parentElement.getBoundingClientRect();
    let lft = e.clientX - wrapRect.left + 12;
    if (lft > wrapRect.width - 96) lft = e.clientX - wrapRect.left - 96;
    tip.style.left = lft + "px";
    tip.style.top = (y(p.v) / H * wrapRect.height - 8) + "px";
  });
  hit.addEventListener("mouseleave", () => { cross.style.display = "none"; tip.hidden = true; });
}

// ══ Stock Filter page ═══════════════════════════════════════════════════════

// Peter Lynch's stock types (Turnaround skipped — can't be screened reliably in
// bulk) + two pure factor styles (Deep Value, Momentum). Static criteria live
// inline; market-aware ones (size/growth differ US vs A-share) use functions.
const FILTER_PRESETS = {
  fast_grower: { label: "Fast Grower", c: {} },   // small, fast-growing, PEG-disciplined
  stalwart:    { label: "Stalwart",    c: {} },   // large, steady 8–15% growth, fair price
  slow_grower: { label: "Slow Grower", c: {} },   // large, mature, high dividend, low growth
  cyclical:    { label: "Cyclical",    c: {} },   // in cyclical industries
  asset_play:  { label: "Asset Play",  c: { pb_max: 1.0, eps_positive: true, sort: "pb", sort_dir: "asc" } },
  deep_value:  { label: "Deep Value",  c: { pe_max: 12, pb_max: 1.2, eps_positive: true, sort: "pb", sort_dir: "asc" } },
  momentum:    { label: "Momentum",    c: { eps_positive: true, mom_min: 30, sort: "market_cap", sort_dir: "desc" } },
};

// Fast Grower — small-mid, growing fast, cheap vs. that growth (PEG-disciplined).
function _fastGrowerCriteria(market) {
  if (market === "cn")
    return { mcap_min: 5, mcap_max: 50, eps_positive: true, pe_max: 30, pb_max: 5, sort: "market_cap", sort_dir: "asc" };
  return { mcap_min: 0.3, mcap_max: 10, eps_positive: true, growth_min: 20, peg_max: 1.5, sort: "impl_growth", sort_dir: "desc" };
}
// Stalwart — large, established, steady moderate growth, fair price.
function _stalwartCriteria(market) {
  if (market === "cn")
    return { mcap_min: 50, eps_positive: true, pe_max: 22, sort: "market_cap", sort_dir: "desc" };
  return { mcap_min: 10, eps_positive: true, growth_min: 8, growth_max: 15, pe_max: 22, sort: "market_cap", sort_dir: "desc" };
}
// Slow Grower — large, mature, low growth, high dividend.
function _slowGrowerCriteria(market) {
  if (market === "cn")
    return { mcap_min: 10, eps_positive: true, div_yield_min: 3, sort: "div_yield", sort_dir: "desc" };
  return { mcap_min: 10, eps_positive: true, growth_max: 8, div_yield_min: 3, sort: "div_yield", sort_dir: "desc" };
}
// Cyclical — earnings track the economy; identify the universe by industry
// (timing the trough is still on you). US uses NASDAQ industry keywords, A-share
// Tushare (Chinese) keywords — substring-matched.
function _cyclicalCriteria(market) {
  const inds = market === "cn"
    ? ["汽车", "钢铁", "航空", "石油", "煤炭", "有色", "化工", "水泥", "工程机械", "半导体", "采掘", "航运"]
    : ["auto", "steel", "airline", "oil & gas", "coal", "mining", "aluminum", "copper", "homebuild", "semiconductor", "shipping", "forest product"];
  return { eps_positive: true, industries: inds, sort: "pb", sort_dir: "asc" };
}

// Cap-size tiers — combinable with the Lynch-type presets above (e.g. "Fast Grower" + "Small-cap").
// CN breakpoints intentionally line up with Fast Grower (5–50) / Stalwart (50+) so the tiers agree.
function _smallCapCriteria(market) {
  if (market === "cn") return { mcap_min: 5,  mcap_max: 50,  sort: "market_cap", sort_dir: "asc" };
  return { mcap_min: 0.3, mcap_max: 2, sort: "market_cap", sort_dir: "asc" };
}
function _midCapCriteria(market) {
  if (market === "cn") return { mcap_min: 50, mcap_max: 200, sort: "market_cap", sort_dir: "asc" };
  return { mcap_min: 2, mcap_max: 10, sort: "market_cap", sort_dir: "asc" };
}
function _largeCapCriteria(market) {
  if (market === "cn") return { mcap_min: 200, sort: "market_cap", sort_dir: "desc" };
  return { mcap_min: 10, sort: "market_cap", sort_dir: "desc" };
}

const CAP_PRESETS = {
  small_cap: { label: "Small-cap" },
  mid_cap:   { label: "Mid-cap" },
  large_cap: { label: "Large-cap" },
};

const PRESET_TITLES = {
  fast_grower: "Lynch Fast Grower — small-mid companies growing fast (≥20%) at a reasonable price (PEG ≤ 1.5). Where the multibaggers are; riskier.",
  stalwart:    "Lynch Stalwart — large, established companies with steady 8–15% growth at a fair price (P/E ≤ 22). Solid gains + downside protection (Coca-Cola, P&G type).",
  slow_grower: "Lynch Slow Grower — large, mature, low-growth companies paying a high dividend (≥3%). Bought for income, not appreciation.",
  cyclical:    "Lynch Cyclical — companies in cyclical industries (autos, steel, airlines, energy, materials, chemicals, semis…). The screen finds the universe; buying near the trough is up to you.",
  asset_play:  "Lynch Asset Play — trades near/below book value (P/B ≤ 1); hidden value in assets. Confirm with the Hidden Assets card.",
  deep_value:  "Graham Deep Value — cheap on both earnings & assets (P/E ≤ 12 and P/B ≤ 1.2).",
  momentum:    "Momentum / relative strength — profitable companies with strong 12-month price performance (≥ +30%).",
  small_cap:   "Small-cap — market cap $300M–$2B (A-share: ¥5B–¥50B). Combine with a Lynch type, e.g. Small-cap + Fast Grower.",
  mid_cap:     "Mid-cap — market cap $2B–$10B (A-share: ¥50B–¥200B).",
  large_cap:   "Large-cap — market cap $10B+ (A-share: ¥200B+).",
  high_roe:    "High ROE (≥15%) — profitable capital efficiency. A-share only for now: ROE comes from a background-cached lookup (Tushare fina_indicator per stock, no bulk endpoint available), so this may be greyed out until the cache finishes building.",
};
// keys where combining presets should take the TIGHTER bound
const _PRESET_MAX_KEYS = ["mcap_max","pe_max","fpe_max","pb_max","ps_max","peg_max","growth_max","price_max"];
const _PRESET_MIN_KEYS = ["mcap_min","div_yield_min","growth_min","price_min","eps_min","roe_min","roe5y_min"];

let _filterMeta = { us: null, cn: null };
let _filterPollTimer = null;
let _filterBuiltMarket = null;          // which market the canvas currently holds
const _activePresets = new Set();       // combinable presets

function getFilterCanvas() {
  let c = document.getElementById("filterCanvas");
  if (!c) { c = document.createElement("div"); c.id = "filterCanvas"; document.querySelector("main").appendChild(c); }
  return c;
}

function hideFilter() {
  const c = document.getElementById("filterCanvas");
  if (c) c.style.display = "none";
  if (_filterPollTimer) { clearTimeout(_filterPollTimer); _filterPollTimer = null; }
  els.workspace.style.display = "";
}

function loadFilterPage() {
  const market = state.market;
  const canvas = getFilterCanvas();
  canvas.style.display = "";
  els.workspace.style.display = "none";
  // Persist state across tab switches: if this market's page is already built,
  // just re-show it (inputs, results & scores are preserved in the DOM).
  if (_filterBuiltMarket === market && canvas.querySelector(".filter-page")) {
    return;
  }
  _filterBuiltMarket = market;
  _activePresets.clear();
  const cur = market === "cn" ? "¥" : "$";
  const universeNote = market === "cn"
    ? "Whole A-share market (~5,600 stocks, Tushare)"
    : "Total US market (~6,400 common stocks, ≈ VTI)";

  canvas.innerHTML = `
  <div class="filter-page">
    <div class="filter-head">
      <div>
        <div class="filter-title">Stock Filter <span class="filter-market-tag">${market === "cn" ? "🇨🇳 A-Share" : "🇺🇸 US"}</span></div>
        <div class="filter-sub">${universeNote}</div>
      </div>
    </div>

    <div class="filter-presets">
      <span class="filter-presets-lbl">Presets:</span>
      ${Object.entries(FILTER_PRESETS).map(([k, p]) => `<button class="filter-preset-btn" data-preset="${k}" title="${escapeHtml(PRESET_TITLES[k] || "")}">${p.label}</button>`).join("")}
      <button class="filter-preset-btn filter-clear" data-preset="_clear">Clear</button>
      <span class="filter-presets-hint">Click multiple to combine (stocks must pass all)</span>
    </div>
    <div class="filter-presets filter-presets-row2">
      <span class="filter-presets-lbl">Size:</span>
      ${Object.entries(CAP_PRESETS).map(([k, p]) => `<button class="filter-preset-btn" data-preset="${k}" title="${escapeHtml(PRESET_TITLES[k] || "")}">${p.label}</button>`).join("")}
      ${market === "cn" ? `<button class="filter-preset-btn" data-preset="high_roe" title="${escapeHtml(PRESET_TITLES.high_roe)}">High ROE</button>` : ""}
    </div>

    <div class="filter-controls">
      <div class="filter-group">
        <div class="filter-group-title">Size &amp; Price</div>
        <label>Min market cap (${cur}B)<input type="number" id="f_mcap_min" min="0" step="0.5" placeholder="any"></label>
        <label>Max market cap (${cur}B)<input type="number" id="f_mcap_max" min="0" step="0.5" placeholder="any"></label>
        <label>Min price (${cur})<input type="number" id="f_price_min" min="0" placeholder="any"></label>
        <label>Max price (${cur})<input type="number" id="f_price_max" min="0" placeholder="any"></label>
        <label>Min 12-mo return (%)<input type="number" id="f_mom_min" step="1" placeholder="any"></label>
        <label>Max 12-mo return (%)<input type="number" id="f_mom_max" step="1" placeholder="any"></label>
      </div>

      <div class="filter-group">
        <div class="filter-group-title">Valuation</div>
        <label>Max P/E<input type="number" id="f_pe_max" min="0" placeholder="any"></label>
        ${market === "cn" ? "" : `<label>Max Forward P/E<input type="number" id="f_fpe_max" min="0" placeholder="any"></label>`}
        <label>Max P/B<input type="number" id="f_pb_max" min="0" step="0.1" placeholder="any"></label>
        ${market === "cn" ? `<label>Max P/S<input type="number" id="f_ps_max" min="0" step="0.1" placeholder="any"></label>` : ""}
        <label class="filter-check"><input type="checkbox" id="f_eps_positive"> Profitable only</label>
      </div>

      <div class="filter-group">
        <div class="filter-group-title">Income</div>
        <label>Min dividend yield (%)<input type="number" id="f_div_yield_min" min="0" step="0.1" placeholder="any"></label>
      </div>

      ${market === "cn" ? `
      <div class="filter-group">
        <div class="filter-group-title">Quality</div>
        <label>Min ROE (%)<input type="number" id="f_roe_min" min="0" step="1" placeholder="any"></label>
        <label class="checkbox-row" title="Require every one of the last 5 annual ROE figures to clear this bar — not just the latest year. Stocks with under 5 years of reports (recent IPOs) won't qualify.">
          <input type="checkbox" id="f_roe_5y"> Consistent for 5Y (not just latest)
        </label>
        <label title="Latest fiscal year's operating revenue vs the prior fiscal year (Tushare or_yoy).">Min revenue growth, past year (%)<input type="number" id="f_revgrowth_min" step="1" placeholder="any"></label>
        <label title="Latest fiscal year's operating profit (营业利润), absolute ¥, before the profit-growth/margin ratios kick in.">Min operating profit (¥B)<input type="number" id="f_opprofit_min" step="0.1" placeholder="any"></label>
        <label title="Every one of the last 3 annual net-profit YoY growth figures must clear this bar — not just the latest year. A single bad year anywhere in the window disqualifies the stock.">Min net profit growth, consistent 3Y (%)<input type="number" id="f_earngrowth3y_min" step="1" placeholder="any"></label>
        <label title="Same idea over 4 years instead of 3 — a stricter bar.">Min net profit growth, consistent 4Y (%)<input type="number" id="f_earngrowth4y_min" step="1" placeholder="any"></label>
        <label title="Every one of the last 4 REPORTED periods (quarterly cadence — Q1/H1/9M/FY, whatever the company actually filed) must clear this bar, not just the latest. Each period's netprofit_yoy is already a clean YoY comparison (e.g. H1 cumulative vs prior-year H1 cumulative) — not 4 discrete quarters in the US GAAP sense, since A-share interim filings are cumulative-YTD by convention.">Min net profit growth, consistent 4 reports (%)<input type="number" id="f_earngrowthq4_min" step="1" placeholder="any"></label>
        <label title="Same idea over the last 8 reported periods (~2 years) instead of 4 — a stricter bar.">Min net profit growth, consistent 8 reports (%)<input type="number" id="f_earngrowthq8_min" step="1" placeholder="any"></label>
        <div class="filter-group-note" id="filterRoeNote"></div>
      </div>` : ""}

      ${market === "cn" ? `
      <div class="filter-group">
        <div class="filter-group-title">Ownership</div>
        <label title="Share of the TOP-10 float holders that are institutional (funds/insurers/banks/asset managers/trusts) — NOT a full-market aggregate like a US 13F percentage, A-share disclosure doesn't give one. A narrower, top-heavy-only read.">Min institutional, top-10 (%)<input type="number" id="f_inst_min" min="0" max="100" step="1" placeholder="any"></label>
        <label>Max institutional, top-10 (%)<input type="number" id="f_inst_max" min="0" max="100" step="1" placeholder="any"></label>
        <label title="Share of the top-10 float holders that are strategic/corporate (一般企业/国资局 — controlling-shareholder-like, not portfolio institutional investors). High = ownership concentrated in a controlling group or state entity.">Max strategic/corporate, top-10 (%)<input type="number" id="f_strategic_max" min="0" max="100" step="1" placeholder="any"></label>
        <label title="Sum of the top 3 float holders' % stakes.">Max top-3 concentration (%)<input type="number" id="f_top3_max" min="0" max="100" step="1" placeholder="any"></label>
        <div class="filter-group-note" id="filterCnOwnNote"></div>
      </div>` : ""}

      ${market === "cn" ? `
      <div class="filter-group">
        <div class="filter-group-title">Sell-Side Coverage</div>
        <label title="Number of distinct sell-side reports on this stock in the trailing 180 days (sector/strategy pieces that merely tag the stock are excluded).">Min # reports, 180d<input type="number" id="f_reportcov_min" min="0" step="1" placeholder="any"></label>
        <label title="Number of distinct brokerages covering this stock in the trailing 180 days.">Min # brokerages, 180d<input type="number" id="f_reportinst_min" min="0" step="1" placeholder="any"></label>
        <label class="checkbox-row" title="Most recent report's rating buckets as positive (买入/增持/推荐-style or Buy/Overweight-style, mapped from whichever house scale the brokerage used).">
          <input type="checkbox" id="f_report_positive_only"> Latest rating positive only
        </label>
        <label class="checkbox-row" title="At least one report in the trailing 180 days was flagged 首次关注/首份报告/首次评级 — the stock just got new sell-side attention.">
          <input type="checkbox" id="f_report_initiated_only"> Recently initiated coverage only
        </label>
        <div class="filter-group-note" id="filterCnReportNote"></div>
      </div>` : ""}

      ${market === "cn" ? "" : `
      <div class="filter-group">
        <div class="filter-group-title">Growth <span class="filter-group-note">(fwd est.)</span></div>
        <label>Min EPS growth (%)<input type="number" id="f_growth_min" step="1" placeholder="any"></label>
        <label>Max EPS growth (%)<input type="number" id="f_growth_max" step="1" placeholder="any"></label>
        <label>Max PEG<input type="number" id="f_peg_max" min="0" step="0.1" placeholder="any"></label>
        <label title="Every one of the last 3 annual net-income YoY growth figures must clear this bar — not just the latest year. A loss-making year anywhere in the window disqualifies the stock (yfinance's free tier caps at 4 years of annual data, so 3Y is the max consistency window available for US).">Min net income growth, consistent 3Y (%)<input type="number" id="f_earngrowth3y_min" step="1" placeholder="any"></label>
        <div class="filter-group-note" id="filterUsEarnNote"></div>
      </div>`}

      ${market === "cn" ? "" : `
      <div class="filter-group">
        <div class="filter-group-title">Ownership</div>
        <label title="% of shares held by institutions (13F filers), from yfinance's own major_holders — quarterly data, up to ~45 days lagged.">Min institutional held (%)<input type="number" id="f_inst_min" min="0" max="100" step="1" placeholder="any"></label>
        <label>Max institutional held (%)<input type="number" id="f_inst_max" min="0" max="100" step="1" placeholder="any"></label>
        <label title="% of shares held by insiders (officers/directors). Lynch: insiders buy for only one reason.">Min insider held (%)<input type="number" id="f_insider_min" min="0" max="100" step="0.1" placeholder="any"></label>
        <label>Max insider held (%)<input type="number" id="f_insider_max" min="0" max="100" step="0.1" placeholder="any"></label>
        <label title="Number of distinct institutional holders on record — a very low count on an otherwise-institutional stock can mean thin, fragile ownership.">Min # institutional holders<input type="number" id="f_inst_holders_min" min="0" step="1" placeholder="any"></label>
        <label title="Sum of the top 3 institutional holders' % stakes — how concentrated institutional ownership is in a handful of whales.">Max top-3 concentration (%)<input type="number" id="f_top3_max" min="0" max="100" step="1" placeholder="any"></label>
        <div class="filter-group-note" id="filterOwnNote"></div>
      </div>`}

      <div class="filter-group">
        <div class="filter-group-title">MAGNET-lite <span class="filter-group-note">(experimental proxy — not the real MAGNET score)</span></div>
        <details class="filter-advanced filter-experimental">
          <summary>Show the 14 MAGNET-lite controls — experimental proxies, off by default</summary>

        <p class="filter-group-note" style="margin:0 0 8px">Cheap proxies for a subset of the MAGNET-A/US strategy spec (Indicators Pool). Uses simple-growth deltas and existing price/valuation fields, NOT the spec's exact log-growth or factor-regression formulas. No backtest, no fitted weights — the spec itself says not to register this yet. MGT and true residual-momentum (RMOM) are skipped entirely (need infrastructure this product doesn't have). Each field below is filtered as a cross-sectional "Top N%" percentile rank — computed only among stocks that have a value for that metric (unprofitable/thin-PE names are excluded from ranking, not ranked last) — matching the spec's own robustness design (percentiles, not raw thresholds, to handle fat-tailed growth/valuation ratios).</p>
        <label title="Percentile rank of: most recent annual net-profit YoY growth minus the prior year's YoY growth — a simple-growth-rate proxy for the spec's ACCEL (log-growth acceleration), not the exact formula. Only ranked among companies with BOTH of the last two years showing positive growth (not just currently profitable) — a decline-then-partial-rebound (e.g. -70% then +115%) no longer qualifies, since that's recovering from a bad year, not genuine acceleration. Underlying value capped at ±150pp before ranking.">Top N% by growth acceleration, ACCEL proxy<input type="number" id="f_accel_top_pct" min="1" max="100" step="1" placeholder="e.g. 20"></label>
        <label title="Percentile rank of: 100 / P/E — the spec's GARP valuation leg (F1-EP), EQ variant only. The EV variant (FCF/EV) isn't built. Only ranked among stocks with P/E >= 2 — a near-zero P/E blows into a nonsense 'yield' that looks like deep value but is usually distress or a data artifact.">Top N% by earnings yield, GARP proxy<input type="number" id="f_garp_top_pct" min="1" max="100" step="1" placeholder="e.g. 20"></label>
        <label title="Percentile rank of: US real price-vs-200-day-moving-average change % (Yahoo's own field); CN real 6-month point-to-point return, NOT a moving average — CN has no cheap true MA200 source, so this is a directional trend proxy only.">Top N% by trend, TIMING proxy<input type="number" id="f_timing_top_pct" min="1" max="100" step="1" placeholder="e.g. 20"></label>
        ${market === "cn" ? "" : `<label title="Percentile rank of: % of the 52-week high the price currently sits at (100 = at the high). US only — CN has no cheap true 52-week-high source without a new per-stock price-history archive.">Top N% by 52-wk-high proximity, H52 proxy<input type="number" id="f_h52_top_pct" min="1" max="100" step="1" placeholder="e.g. 20"></label>`}
        <p class="filter-group-note" style="margin:8px 0 0">Below: each metric (Net Profit, Net Margin, Revenue) has two independent, mutually-exclusive patterns — <strong>Turnaround</strong> (was negative two periods ago, now positive and widening faster than the initial swing) and <strong>Accelerating</strong> (never negative in this window, just speeding up) — each available at annual or quarterly cadence. Revenue has no Turnaround row since a revenue level is essentially never negative.</p>

        <div class="filter-subgroup-title">Net Profit</div>
        <span class="filter-group-note">Turnaround (loss → profit → widening faster than the initial swing)</span>
        <div class="checkbox-pair">
          <label class="checkbox-row" title="N(t-2) < 0 AND N(t-1) > 0 AND N(t)-N(t-1) > N(t-1)-N(t-2). Loss two years ago, turned profitable last year, and the latest improvement exceeds the initial turnaround swing itself. E.g. CRDO: NI -$28M -> +$52M -> +$472M — swing was +$81M, latest improvement was +$420M. Kept separate from ACCEL above (matches the spec's TURNAROUND sleeve, §1.1). Caveat: bottom-line NI, not EBIT — can pass on a non-operating item (tax credit, subsidy, asset gain) while the core business stays unprofitable. Cross-check Earnings Yield % (EBIT/EV) — confirmed live on 立昂微 (605358.SH): Q1'26 NI was +¥7.2M while operating profit was still -¥13.6M that same quarter.">
            <input type="checkbox" id="f_turnaround_only"> Annual
          </label>
          <label class="checkbox-row" title="Same test, last 3 reported periods instead of fiscal years. US: yfinance's quarterly_financials, genuinely discrete. CN: differenced from cumulative-YTD reports (Q2=H1-Q1, Q3=9M-H1, Q4=FY-9M), only across literal adjacent same-year periods. Same NET-income-not-EBIT caveat as the annual version.">
            <input type="checkbox" id="f_turnaround_only_q"> Quarterly
          </label>
        </div>
        <span class="filter-group-note">Accelerating (never negative in-window, still speeding up)</span>
        <div class="checkbox-pair">
          <label class="checkbox-row" title="N(t-2) > 0 AND N(t-1) > 0 AND N(t) > 0 AND N(t)-N(t-1) > N(t-1)-N(t-2). All three periods profitable — no turnaround, just an accelerating profit trajectory. Mutually exclusive with the Turnaround row above by construction. Same NET-income-not-EBIT caveat applies.">
            <input type="checkbox" id="f_profit_accel_only"> Annual
          </label>
          <label class="checkbox-row" title="Same test, last 3 reported periods instead of fiscal years.">
            <input type="checkbox" id="f_profit_accel_only_q"> Quarterly
          </label>
        </div>

        <div class="filter-subgroup-title">Net Margin (NI / Revenue)</div>
        <span class="filter-group-note">Turnaround (loss → profit → widening faster than the initial swing)</span>
        <div class="checkbox-pair">
          <label class="checkbox-row" title="Same LEVEL-based test as profit turnaround, applied to net margin instead of raw NI. Margin genuinely can go negative, so it keeps the sign-flip gate. Substantially overlaps with the profit turnaround checkbox by construction — the incremental value is the percentage-point-normalized reading. Same NET-income-not-EBIT caveat.">
            <input type="checkbox" id="f_margin_turnaround_only"> Annual
          </label>
          <label class="checkbox-row" title="Same margin test, last 3 reported periods instead of fiscal years.">
            <input type="checkbox" id="f_margin_turnaround_only_q"> Quarterly
          </label>
        </div>
        <span class="filter-group-note">Accelerating (never negative in-window, still widening)</span>
        <div class="checkbox-pair">
          <label class="checkbox-row" title="M(t-2) > 0 AND M(t-1) > 0 AND M(t) > 0 AND M(t)-M(t-1) > M(t-1)-M(t-2). All three periods positive margin — no turnaround, just accelerating margin expansion. Mutually exclusive with the Margin Turnaround row above.">
            <input type="checkbox" id="f_margin_accel_only"> Annual
          </label>
          <label class="checkbox-row" title="Same test, last 3 reported periods instead of fiscal years.">
            <input type="checkbox" id="f_margin_accel_only_q"> Quarterly
          </label>
        </div>

        <div class="filter-subgroup-title">Revenue</div>
        <span class="filter-group-note">Accelerating — no Turnaround row (a revenue level is essentially never negative)</span>
        <div class="checkbox-pair">
          <label class="checkbox-row" title="No turnaround/sign-flip gate — revenue as a raw level is virtually never negative, so this is a direct acceleration check: R(t)-R(t-1) > R(t-1)-R(t-2), on revenue dollars.">
            <input type="checkbox" id="f_revenue_accel_only"> Annual
          </label>
          <label class="checkbox-row" title="Same test, last 3 reported periods instead of fiscal years. CN uses cumulative-YTD-differenced discrete quarters; US uses yfinance's genuinely discrete quarterly_financials.">
            <input type="checkbox" id="f_revenue_accel_only_q"> Quarterly
          </label>
        </div>
      
        </details>
      </div>

      <div class="filter-group">
        <div class="filter-group-title">Stage Analysis <span class="filter-group-note">(Weinstein · weekly)</span></div>
        <p class="filter-group-note" style="margin:0 0 8px">Stan Weinstein's four-stage framework, computed on <strong>weekly</strong> bars from a 70-week price cache. Stage is a <strong>descriptive state, not a buy/sell signal</strong>. Stocks with under 56 weeks of history are unclassified and never match a stage filter — unknown is not "not Stage 2". No backtest has been run.</p>

        <span class="filter-group-note">Stage — tick any to include</span>
        <div class="checkbox-pair">
          <label class="checkbox-row" title="Basing: flat 30-week MA after a decline, price oscillating around it."><input type="checkbox" id="f_stage1"> 1 Basing</label>
          <label class="checkbox-row" title="Advancing: price above a RISING 30-week MA. Weinstein's buy zone."><input type="checkbox" id="f_stage2"> 2 Advancing</label>
        </div>
        <div class="checkbox-pair">
          <label class="checkbox-row" title="Topping: MA flattening/rolling over after an advance."><input type="checkbox" id="f_stage3"> 3 Topping</label>
          <label class="checkbox-row" title="Declining: price below a FALLING 30-week MA."><input type="checkbox" id="f_stage4"> 4 Declining</label>
        </div>

        <!-- Confirmation controls appear only for the stage(s) actually ticked.
             All 13 were shown at once before, which buried the stage picker. -->
        <div class="filter-rs-block">
          <span class="filter-group-note vol-pair-title">Relative strength — percentile rank against every other stock</span>
          <div class="vol-pair-cols">
            <label title="Keep only stocks ranking at or above this percentile. 1-99, uniform by construction: 80 means the stock outperformed 80% of the universe on the chosen construction.">
              <span class="vol-pair-head">Min RS percentile <span class="vol-pair-sub">1–99, higher is stronger</span></span>
              <span class="vol-pair-formula">cross-sectional rank of the whole universe</span>
              Min<input type="number" id="f_rs_pct_min" min="1" max="99" step="1" placeholder="e.g. 80">
            </label>
            <label title="IBD/O'Neil weights the most recent quarter DOUBLE to catch new leaders early. The academic 12-1 measure instead SKIPS the most recent period, because very short-term returns mean-revert. They are opposite emphases and rank stocks differently (Spearman 0.83).">
              <span class="vol-pair-head">Construction <span class="vol-pair-sub">the two disagree</span></span>
              <span class="vol-pair-formula">weighting of the lookback</span>
              <select id="f_rs_basis">
                <option value="ibd">IBD — 0.4×3m + 0.2×(6m, 9m, 12m)</option>
                <option value="jt">Academic 12-1 — skip most recent</option>
              </select>
            </label>
          </div>
          <span class="filter-hint"><b>Why percentile.</b> Mansfield RS (Advanced) is a <em>time-series</em> measure — "is this stock stronger than its own 52-week norm?" — and its scale is unbounded and volatility-dependent: across this universe it ran <b>−100 to +938</b>, so "+5" means different things for a utility and a biotech, and crashed-then-bounced stocks get pinned at the floor and stop discriminating. A percentile answers the <em>cross-sectional</em> question and is bounded and uniform, which is what "top 20%" needs. The two mostly agree (rank correlation <b>0.96</b>) — this changes how RS is <em>expressed</em>, not the underlying ordering. <b>Thresholds are not from Weinstein</b>; IBD conventionally treats 80+ as strong.</span>
        </div>
        <div class="filter-vol-pair">
          <span class="filter-group-note vol-pair-title">Volume — two different questions, both on a 13-week denominator so they are directly comparable</span>
          <div class="vol-pair-cols">
            <label title="BREAKOUT surge: the latest COMPLETED week's volume divided by its trailing 13-week average. Weinstein's breakout-confirmation rule — a single heavy week as price clears resistance.">
              <span class="vol-pair-head">Breakout <span class="vol-pair-sub">is something happening <em>now</em>?</span></span>
              <span class="vol-pair-formula">latest week ÷ 13-week average</span>
              Min (×)<input type="number" id="f_volratio_min" min="0" step="0.1" placeholder="e.g. 1.5">
            </label>
            <label title="SUSTAINED interest: mean weekly volume of the last 13 completed weeks divided by the mean of the 13 weeks before them. A quarter-on-quarter comparison of accumulated interest.">
              <span class="vol-pair-head">Sustained <span class="vol-pair-sub">has interest built up all <em>quarter</em>?</span></span>
              <span class="vol-pair-formula">mean of 13 weeks ÷ mean of prior 13</span>
              Min (×)<input type="number" id="f_adv_vol_mult_min" min="0" step="0.1" placeholder="e.g. 2">
            </label>
          </div>
          <div class="vol-match" id="volMatchRow" hidden>
            <span class="vol-match-label">Both set — match:</span>
            <label class="checkbox-row"><input type="radio" name="vol_match" value="all" checked> <b>both</b> tests (AND)</label>
            <label class="checkbox-row"><input type="radio" name="vol_match" value="any"> <b>either</b> test (OR)</label>
          </div>
          <span class="filter-hint">Set <b>either one alone</b> and only that test applies. Set <b>both</b> and the toggle above decides whether a stock must pass both or just one. Both exclude the in-progress week — volume accumulates through a week while price does not. <b>Breakout</b> is the <b>Vol×</b> column and is the sharper signal, but it is far noisier as a screen: measured across 5,483 stocks it moves a median <b>31% week to week</b> and flips <b>17.7%</b> of stocks across a 1.5× line every week, versus <b>4.2%</b> and <b>3.5%</b> for sustained. Use <b>sustained</b> to find stocks already in a real advance, <b>breakout</b> to time an entry — ideally with <b>Broken above 30-wk resistance</b> (Advanced). Sustained is also test 1 of the Stage 2 checklist, where its 1.5× threshold appears as a tick-box.</span>
        </div>

        <p class="filter-group-note" id="f_chk_hint" style="margin:8px 0 0">Tick a stage above to add its confirmation tests.</p>
        <span class="filter-hint filter-hint-warn">Thresholds below are set from measured universe percentiles, <b>not from Weinstein</b> — the book gives no numbers. Volume-based tests are easier to pass on thinly traded stocks, so a small-cap-heavy result is expected, not a discovery.</span>

        <div class="stage-chk-block" data-stage="1" hidden>
          <span class="filter-group-note stage-chk-title">Stage 1 — is it a real base?</span>
          <label title="0-4: prior decline <= -20%, price range <= 25%, volume not expanding, and the 60-day MA flat within +/-5%, measured over the last 13 weeks. This checklist uses a 60-day MA, not the 30-week stage line — at 30 weeks it needs 82 weeks of history and the cache holds 70.">Min base score (0–4)<input type="number" id="f_base_score_min" min="0" max="4" step="1" placeholder="e.g. 3"></label>
          <span class="filter-hint">Tests: prior decline ≤ −20% · price range ≤ 25% · volume not expanding · 60-day MA flat within ±5%, over the last 13 weeks. Uses a <b>60-day</b> MA, not the 30-week stage line — at 30 weeks the test needs 82 weeks of history and the cache holds 70, so a stock can show a clean base while its 30-week line still falls.</span>
          <div class="checkbox-pair">
            <label class="checkbox-row" title="All four base tests pass."><input type="checkbox" id="f_base_textbook_only"> Textbook base (4/4)</label>
            <label class="checkbox-row" title="Base-window volume is not expanding versus the decline that preceded it."><input type="checkbox" id="f_base_volume_dryup_only"> Volume dried up</label>
          </div>
        </div>

        <div class="stage-chk-block" data-stage="2" hidden>
          <span class="filter-group-note stage-chk-title">Stage 2 — is the advance confirmed?</span>
          <label title="How many of the four Stage 2 confirmation tests a stock must pass: (1) volume >= 1.5x the prior 13 weeks, (2) 30-week MA rising, (3) each pullback bottoming higher, (4) price above the MA in >= 80% of the last 26 weeks.">Min advance score (0–4)<input type="number" id="f_adv_score_min" min="0" max="4" step="1" placeholder="e.g. 3"></label>
          <span class="filter-hint">Of 1,917 Stage 2 stocks: <b>≥2</b> keeps 1,572 · <b>≥3</b> keeps 711 · <b>4/4</b> keeps 90. <b>≥1 does nothing</b> — test 2 is what "Stage 2" already means, so every stock scores at least 1. Start at <b>3</b>.</span>
          <span class="filter-hint">Individually: dips-held-above-MA passes 1,157 · higher-lows 931 · <b>volume ≥1.5× only 285</b>. Volume is the binding test — tick it directly for a sharper cut than a score.</span>
          <span class="filter-hint"><b>The four boxes below</b> each demand ONE specific test rather than a score. <b>Textbook advance</b> = all four at once (same as score 4). The other three let you require a single criterion — useful when you care about one signal more than the overall count.</span>
          <div class="checkbox-pair">
            <label class="checkbox-row" title="All four advance tests pass — identical to setting the score to 4."><input type="checkbox" id="f_adv_textbook_only"> Textbook advance (4/4)</label>
            <label class="checkbox-row" title="Recent 13 weeks' volume is at least 1.5x the 13 before. Only ~14% of Stage 2 stocks pass — the strongest single discriminator."><input type="checkbox" id="f_adv_volume_expanding_only"> Volume ≥1.5×</label>
          </div>
          <div class="checkbox-pair">
            <label class="checkbox-row" title="Every swing low in the last 26 weeks is higher than the one before it. A swing low is a week lower than the two weeks either side. Needs at least two to judge."><input type="checkbox" id="f_adv_higher_lows_only"> Higher lows</label>
            <label class="checkbox-row" title="Price is above the MA now AND closed above it in at least 80% of the last 26 weeks — the pullbacks held, not just today's close."><input type="checkbox" id="f_adv_price_above_only"> Dips held above MA</label>
          </div>
          <span class="filter-hint"><b>Volume ≥1.5×</b> — demand confirmation: the recent quarter traded 1.5× the prior quarter. <b>Higher lows</b> — structure: every swing low (a week lower than the two either side) sits above the previous one, so pullbacks are being bought higher each time. <b>Dips held above MA</b> — durability: price closed above the 30-week average in ≥80% of the last 26 weeks, not merely today.</span>

        </div>

        <div class="stage-chk-block" data-stage="3" hidden>
          <span class="filter-group-note stage-chk-title">Stage 3 — is it really distributing?</span>
          <label title="0-3: volume >= 1.25x the prior 13 weeks, weekly-return volatility >= 1.20x, and the 30-week MA flattening within +/-3%.">Min top score (0–3)<input type="number" id="f_top_score_min" min="0" max="3" step="1" placeholder="e.g. 2"></label>
          <span class="filter-hint">Tests: volume ≥ 1.25× · weekly-return volatility ≥ 1.20× · 30-week MA flattening within ±3%. Distribution shows up as heavy trade and widening swings while the average rolls over.</span>
          <label class="checkbox-row" title="All three distribution tests pass."><input type="checkbox" id="f_top_textbook_only"> Textbook top (3/3)</label>
        </div>

        <div class="stage-chk-block" data-stage="4" hidden>
          <span class="filter-group-note stage-chk-title">Stage 4 — how bad is the decline?</span>
          <label class="checkbox-row" title="Both core tests pass: 30-week MA falling and price below it. True for every stock labelled Stage 4."><input type="checkbox" id="f_decl_confirmed_only"> Decline confirmed</label>
          <span class="filter-hint">The two core tests <b>define</b> Stage 4, so "confirmed" is true for all 1,583 of them and filters nothing on its own. The <b>optional</b> volume test below is the discriminator — it fires on 611 (39%).</span>
          <label class="checkbox-row" title="OPTIONAL aggravating factor, excluded from the score: volume expanding (>=1.25x) into the decline.">
            <input type="checkbox" id="f_decl_volume_rising_only"> Volume rising into the decline
          </label>
        </div>

        <details class="filter-advanced">
          <summary>Advanced — relative strength, moving-average basis, resistance breakout, industry group</summary>
          <label title="Which moving average every stage test reads. SMA is Weinstein canonical; EMA is the faster-responding alternative and can disagree.">Moving-average basis
            <select id="f_stage_basis">
              <option value="sma">SMA (Weinstein canonical)</option>
              <option value="ema">EMA (faster variant)</option>
            </select>
          </label>
          <label title="Mansfield RS = ((price/benchmark) / its own 52-week average - 1) x 100. Above 0 = outperforming the broad market. US benchmark: SPY. CN: 中证全指.">Min Mansfield RS (0 = market-neutral)<input type="number" id="f_mansfield_min" step="0.5" placeholder="e.g. 0"></label>
          <label title="How far price sits above the 30-week moving average, in percent.">Min % above 30-wk MA<input type="number" id="f_price_vs_ma_min" step="1" placeholder="any"></label>
          <label title="Percent change in the 30-week moving average over the last 13 weeks. Positive = a rising average.">Min MA slope % (13wk)<input type="number" id="f_ma_slope_min" step="0.5" placeholder="e.g. 1"></label>
          <label class="checkbox-row" title="Latest close is above the highest weekly close of the prior 30 weeks — i.e. broken out of its base.">
            <input type="checkbox" id="f_above_resistance_only"> Broken above 30-wk resistance
          </label>
          <span class="filter-group-note" style="display:block;margin-top:8px">Top-down — the stock's own industry group must qualify too</span>
          <span class="filter-hint"><b>What "group" means:</b> the stock's <b>industry group</b>, analysed as if it were itself a stock. Members' weekly closes are combined into an <b>equal-weight index</b> (so it reflects the typical member, not just the largest), then run through the same 30-week SMA, slope, stage and Mansfield RS. <b>135 groups</b> are tracked; a group needs <b>≥5 members</b> to be scored. Weinstein screens top-down — market, then group, then stock — on the view that a Stage 2 stock inside a Stage 2 group is the better setup. Example: XOM sits in Integrated Oil (Stage 2, RS +29, rank 3/135, 94% of members in Stage 2), whereas NVDA is Stage 2 but its group (Semiconductors, 99 members) is only Stage 1. <em>Small groups are noisy — an 8-member index moves on one or two names, so weight its rank less than a 99-member one.</em></span>
          <div class="checkbox-pair">
            <label class="checkbox-row" title="Group is basing."><input type="checkbox" id="f_sector_stage1"> Grp 1</label>
            <label class="checkbox-row" title="Group is advancing — Weinstein's preferred backdrop."><input type="checkbox" id="f_sector_stage2"> Grp 2</label>
            <label class="checkbox-row" title="Group is topping."><input type="checkbox" id="f_sector_stage3"> Grp 3</label>
            <label class="checkbox-row" title="Group is declining."><input type="checkbox" id="f_sector_stage4"> Grp 4</label>
          </div>
          <label title="Mansfield RS of the stock's industry group vs the market.">Min industry-group RS<input type="number" id="f_sector_rs_min" step="0.5" placeholder="e.g. 0"></label>
          <label title="Keep only stocks whose industry group ranks at least this high on group RS (1 = strongest). Groups need 5+ members to be ranked.">Max industry-group rank<input type="number" id="f_sector_rank_max" min="1" step="1" placeholder="e.g. 20"></label>
        </details>
        <div class="filter-group-note" id="filterStageNote"></div>
      </div>

      <div class="filter-group">
        <div class="filter-group-title">Industry</div>
        <label>Keyword(s), comma-separated
          <input type="text" id="f_industries" list="filterIndustryList" placeholder="e.g. Semiconductors, Bank" autocomplete="off">
        </label>
        <datalist id="filterIndustryList"></datalist>
        ${market === "cn" ? "" : `<div class="filter-sectors" id="filterSectors"></div>`}
      </div>

      <div class="filter-group">
        <div class="filter-group-title">Sort &amp; Show</div>
        <label>Sort by
          <select id="f_sort">
            <option value="market_cap">Market cap</option>
            <option value="pe">P/E</option>
            <option value="pb">P/B</option>
            <option value="div_yield">Dividend yield</option>
            <option value="price">Price</option>
          </select>
        </label>
        <label>Direction
          <select id="f_sort_dir"><option value="desc">High → Low</option><option value="asc">Low → High</option></select>
        </label>
        <label>Max results<input type="number" id="f_limit" min="10" max="500" value="50"></label>
      </div>
    </div>

    <div class="filter-actions filter-actionbar">
      <button id="filterRunBtn" class="primary-btn">Run filter</button>
      <span class="filter-active-count" id="filterActiveCount">No filters set — showing the whole universe</span>
      <button class="secondary-btn filter-clear-all" id="filterClearAll" hidden type="button">Clear all</button>
      <span id="filterMetaStatus" class="filter-meta-status"></span>
    </div>

    <div id="filterResults" class="filter-results"></div>
  </div>`;

  // wire presets
  canvas.querySelectorAll(".filter-preset-btn").forEach(b => {
    b.addEventListener("click", () => togglePreset(b.dataset.preset));
  });
  document.getElementById("filterRunBtn").addEventListener("click", runFilter);
  document.getElementById("filterClearAll")?.addEventListener("click", () => {
    const clr = [...document.querySelectorAll(".filter-preset-btn")].find(b => /^Clear$/i.test(b.textContent.trim()));
    if (clr) clr.click(); else location.reload();
    setTimeout(_syncFilterActivity, 0);
  });

  loadFilterMeta(market);
}

async function loadFilterMeta(market) {
  const statusEl = document.getElementById("filterMetaStatus");
  try {
    const m = await fetchJson(`/api/filter_meta?market=${market}`);
    _filterMeta[market] = m;
    // industries datalist
    const dl = document.getElementById("filterIndustryList");
    if (dl && m.industries) dl.innerHTML = m.industries.map(i => `<option value="${escapeHtml(i)}">`).join("");
    // US sector chips
    const secWrap = document.getElementById("filterSectors");
    if (secWrap && m.sectors && m.sectors.length) {
      secWrap.innerHTML = m.sectors.map(s =>
        `<button type="button" class="filter-sector-chip" data-sector="${escapeHtml(s)}">${escapeHtml(s)}</button>`).join("");
      secWrap.querySelectorAll(".filter-sector-chip").forEach(chip =>
        chip.addEventListener("click", () => chip.classList.toggle("active")));
    }
    if (market === "us" && !m.ready) {
      statusEl.textContent = `Building market snapshot… ${m.progress || 0}/${m.total || "?"} — valuation filters ready shortly.`;
      _filterPollTimer = setTimeout(() => loadFilterMeta(market), 3000);
    } else {
      statusEl.textContent = `Universe: ${m.universe || 0} stocks ready.`;
    }
    // Stage Analysis weekly price cache — both markets.
    const stg = m.stage_status;
    const stgNote = document.getElementById("filterStageNote");
    [1,2,3,4].forEach(i => document.getElementById("f_stage"+i)
      ?.addEventListener("change", _syncStageChecklists));
    _syncStageChecklists();
    const _fc = document.querySelector(".filter-controls");
    if (_fc && !_fc.dataset.activityWired) {
      _fc.dataset.activityWired = "1";
      _fc.addEventListener("input", () => { _syncFilterActivity(); _syncVolMatch(); });
      _fc.addEventListener("change", () => { _syncFilterActivity(); _syncVolMatch(); });
      _fc.addEventListener("click", () => setTimeout(_syncFilterActivity, 0));
    }
    _syncFilterActivity();
    _syncVolMatch();
    const stgInputs = ["f_stage1","f_stage2","f_stage3","f_stage4","f_stage_basis","f_mansfield_min",
                       "f_volratio_min","f_price_vs_ma_min","f_ma_slope_min","f_above_resistance_only",
                       "f_base_score_min","f_base_textbook_only","f_base_volume_dryup_only",
                       "f_adv_score_min","f_adv_textbook_only","f_adv_volume_expanding_only",
                       "f_adv_higher_lows_only","f_adv_price_above_only","f_adv_vol_mult_min",
                       "f_top_score_min","f_top_textbook_only",
                       "f_decl_confirmed_only","f_decl_volume_rising_only",
                       "f_sector_stage1","f_sector_stage2","f_sector_stage3","f_sector_stage4",
                       "f_sector_rs_min","f_sector_rank_max"]
                      .map(id => document.getElementById(id));
    const stgReady = stg && stg.ready;
    if (stgNote) {
      stgNote.textContent = !stg ? "" : stg.error ? `⚠ ${stg.error}`
        : stgReady
          ? `Weekly cache ready — ${stg.weeks || 0} weeks to ${stg.as_of || "—"}, ${stg.stocks || 0} stocks, benchmark ${stg.benchmark || "—"}${stg.last_bar_partial ? " (current week still in progress; volume uses the last completed week)" : ""}.`
          : `Building weekly price cache (one-time)… ${stg.progress || 0}/${stg.total || "?"} — needed for stage, Mansfield RS and volume-surge filters.`;
    }
    stgInputs.forEach(el => { if (el) el.disabled = !stgReady; });
    if (stg && !stgReady && !stg.error) _filterPollTimer = setTimeout(() => loadFilterMeta(market), 8000);

    if (market === "cn") {
      const rs = m.roe_status;
      const roeNote = document.getElementById("filterRoeNote");
      const roeBtn = document.querySelector('.filter-preset-btn[data-preset="high_roe"]');
      const fundInputs = ["f_roe_min", "f_revgrowth_min", "f_opprofit_min", "f_earngrowth3y_min", "f_earngrowth4y_min", "f_earngrowthq4_min", "f_earngrowthq8_min"].map(id => document.getElementById(id));
      const roeReady = rs && rs.ready;
      if (roeNote) {
        roeNote.textContent = !rs ? "" : roeReady
          ? "Fundamentals cache ready (ROE, revenue growth, operating profit)."
          : `Building fundamentals cache (whole-market, one-time)… ${rs.progress || 0}/${rs.total || "?"} — takes a while, this ticks in the background.`;
      }
      if (roeBtn) roeBtn.disabled = !roeReady;
      fundInputs.forEach(el => { if (el) el.disabled = !roeReady; });
      if (rs && !roeReady) _filterPollTimer = setTimeout(() => loadFilterMeta(market), 5000);

      const os = m.own_status;
      const ownNote = document.getElementById("filterCnOwnNote");
      const ownInputs = ["f_inst_min", "f_inst_max", "f_strategic_max", "f_top3_max"].map(id => document.getElementById(id));
      const ownReady = os && os.ready;
      if (ownNote) {
        ownNote.textContent = !os ? "" : ownReady
          ? `Ownership cache ready (top-10 float holders as of ${os.report_date || "—"}).`
          : os.error ? `⚠ ${os.error}`
          : `Building ownership cache (whole-market, one-time)… ${os.progress || 0} stocks so far — this is quick (bulk pagination), should finish in under a minute.`;
      }
      ownInputs.forEach(el => { if (el) el.disabled = !ownReady; });
      if (os && !ownReady && !os.error) _filterPollTimer = setTimeout(() => loadFilterMeta(market), 3000);

      const rps = m.report_status;
      const reportNote = document.getElementById("filterCnReportNote");
      const reportInputs = ["f_reportcov_min", "f_reportinst_min", "f_report_positive_only", "f_report_initiated_only"].map(id => document.getElementById(id));
      // "Ready" used to mean "a cache file exists", which was true from the
      // first window on — so a count labelled 180 days was served off a week
      // of data. These filters now wait for the whole window.
      const reportReady = rps && rps.complete;
      if (reportNote) {
        const reach = !rps ? "" : rps.covered_from
          ? `${rps.covered_days} of ${rps.horizon_days} days loaded`
          : "nothing loaded yet";
        reportNote.textContent = !rps ? "" : reportReady
          ? `Sell-side coverage ready — all ${rps.horizon_days} days loaded (refreshed ${rps.built_at ? new Date(rps.built_at * 1000).toLocaleDateString() : "—"}).`
          : (rps.error && !rps.building)
            ? `Sell-side coverage: ${reach}. ⚠ Paused — ${rps.error} Retrying in ${_fmtCooldown(rps.cooldown_remaining_min)}. These filters unlock once all ${rps.horizon_days} days are in; a partial window would undercount.`
            : `Sell-side coverage: ${reach}, fetching the rest in the background (one request every ${_fmtGap(rps.request_gap_s)}). These filters unlock once all ${rps.horizon_days} days are in; a partial window would undercount.`;
      }
      reportInputs.forEach(el => { if (el) el.disabled = !reportReady; });
      if (rps && !reportReady && !rps.error) _filterPollTimer = setTimeout(() => loadFilterMeta(market), 8000);
    }
    if (market === "us") {
      const os = m.own_status;
      const ownNote = document.getElementById("filterOwnNote");
      const ownInputs = ["f_inst_min", "f_inst_max", "f_insider_min", "f_insider_max", "f_inst_holders_min", "f_top3_max"]
        .map(id => document.getElementById(id));
      const ownReady = os && os.ready;
      if (ownNote) {
        ownNote.textContent = !os ? "" : ownReady
          ? "Ownership cache ready (institutional/insider %, holder count, top-3 concentration)."
          : `Building ownership cache (whole-market, one-time, ~1hr)… ${os.progress || 0}/${os.total || "?"} — 13F data changes quarterly, so this only needs to run every few weeks.`;
      }
      ownInputs.forEach(el => { if (el) el.disabled = !ownReady; });
      if (os && !ownReady) _filterPollTimer = setTimeout(() => loadFilterMeta(market), 8000);

      const es = m.earn_status;
      const earnNote = document.getElementById("filterUsEarnNote");
      const earnInput = document.getElementById("f_earngrowth3y_min");
      const earnReady = es && es.ready;
      if (earnNote) {
        earnNote.textContent = !es ? "" : earnReady
          ? "Earnings-growth cache ready."
          : `Building earnings-growth cache (whole-market, one-time, ~1hr)… ${es.progress || 0}/${es.total || "?"} — annual filings change slowly, so this only needs to run every few weeks.`;
      }
      if (earnInput) earnInput.disabled = !earnReady;
      if (es && !earnReady) _filterPollTimer = setTimeout(() => loadFilterMeta(market), 8000);
    }
  } catch (err) {
    statusEl.textContent = `⚠ ${err.message}`;
  }
}

function _presetCriteria(key) {
  if (key === "fast_grower") return _fastGrowerCriteria(state.market);
  if (key === "stalwart")    return _stalwartCriteria(state.market);
  if (key === "slow_grower") return _slowGrowerCriteria(state.market);
  if (key === "cyclical")    return _cyclicalCriteria(state.market);
  if (key === "small_cap")   return _smallCapCriteria(state.market);
  if (key === "mid_cap")     return _midCapCriteria(state.market);
  if (key === "large_cap")   return _largeCapCriteria(state.market);
  if (key === "high_roe")    return { roe_min: 15, sort: "market_cap", sort_dir: "desc" };
  return (FILTER_PRESETS[key] || {}).c || {};
}

// Merge all active presets — for overlapping keys, take the more restrictive bound
// (min of the *_max limits, max of the *_min limits), so results pass ALL presets.
function _mergeActivePresets() {
  const merged = {};
  for (const key of _activePresets) {
    for (const [k, v] of Object.entries(_presetCriteria(key))) {
      if (k === "eps_positive") { merged[k] = merged[k] || !!v; continue; }
      if (k === "sort" || k === "sort_dir") { merged[k] = v; continue; }   // last active wins
      if (merged[k] == null) { merged[k] = v; }
      else if (_PRESET_MAX_KEYS.includes(k)) merged[k] = Math.min(merged[k], v);
      else if (_PRESET_MIN_KEYS.includes(k)) merged[k] = Math.max(merged[k], v);
      else merged[k] = v;
    }
  }
  return merged;
}

function _clearFilterInputs() {
  ["f_mcap_min","f_mcap_max","f_price_min","f_price_max","f_pe_max","f_fpe_max",
   "f_pb_max","f_ps_max","f_div_yield_min","f_growth_min","f_growth_max","f_peg_max",
   "f_mom_min","f_mom_max","f_roe_min","f_revgrowth_min","f_opprofit_min",
   "f_inst_min","f_inst_max","f_insider_min","f_insider_max","f_inst_holders_min","f_top3_max",
   "f_strategic_max","f_earngrowth3y_min","f_earngrowth4y_min","f_earngrowthq4_min","f_earngrowthq8_min",
   "f_reportcov_min","f_reportinst_min","f_accel_top_pct","f_garp_top_pct","f_timing_top_pct","f_h52_top_pct","f_industries"]
    .forEach(id => { const el = document.getElementById(id); if (el) el.value = ""; });
  const cb = document.getElementById("f_eps_positive"); if (cb) cb.checked = false;
  const roe5y = document.getElementById("f_roe_5y"); if (roe5y) roe5y.checked = false;
  const rpos = document.getElementById("f_report_positive_only"); if (rpos) rpos.checked = false;
  const rinit = document.getElementById("f_report_initiated_only"); if (rinit) rinit.checked = false;
  const turn = document.getElementById("f_turnaround_only"); if (turn) turn.checked = false;
  const turnQ = document.getElementById("f_turnaround_only_q"); if (turnQ) turnQ.checked = false;
  const revAccel = document.getElementById("f_revenue_accel_only"); if (revAccel) revAccel.checked = false;
  const revAccelQ = document.getElementById("f_revenue_accel_only_q"); if (revAccelQ) revAccelQ.checked = false;
  [1,2,3,4].forEach(i => { const el = document.getElementById("f_stage"+i); if (el) el.checked = false; });
  const sbasis = document.getElementById("f_stage_basis"); if (sbasis) sbasis.value = "sma";
  const aboveRes = document.getElementById("f_above_resistance_only"); if (aboveRes) aboveRes.checked = false;
  const _rb = document.getElementById("f_rs_basis"); if (_rb) _rb.value = "ibd";
  ["f_base_score_min","f_adv_score_min","f_top_score_min","f_adv_vol_mult_min","f_rs_pct_min"].forEach(id => {
    const e = document.getElementById(id); if (e) e.value = "";
  });
  ["f_base_textbook_only","f_base_volume_dryup_only","f_adv_textbook_only","f_adv_volume_expanding_only",
   "f_adv_higher_lows_only","f_adv_price_above_only","f_top_textbook_only",
   "f_decl_confirmed_only","f_decl_volume_rising_only"].forEach(id => {
    const e = document.getElementById(id); if (e) e.checked = false;
  });
  if (typeof _syncStageChecklists === "function") _syncStageChecklists();
  ["f_mansfield_min","f_volratio_min","f_price_vs_ma_min","f_ma_slope_min",
   "f_sector_rs_min","f_sector_rank_max"]
    .forEach(id => { const el = document.getElementById(id); if (el) el.value = ""; });
  [1,2,3,4].forEach(i => { const el = document.getElementById("f_sector_stage"+i); if (el) el.checked = false; });
  const marginTurn = document.getElementById("f_margin_turnaround_only"); if (marginTurn) marginTurn.checked = false;
  const marginTurnQ = document.getElementById("f_margin_turnaround_only_q"); if (marginTurnQ) marginTurnQ.checked = false;
  const profitAccel = document.getElementById("f_profit_accel_only"); if (profitAccel) profitAccel.checked = false;
  const profitAccelQ = document.getElementById("f_profit_accel_only_q"); if (profitAccelQ) profitAccelQ.checked = false;
  const marginAccel = document.getElementById("f_margin_accel_only"); if (marginAccel) marginAccel.checked = false;
  const marginAccelQ = document.getElementById("f_margin_accel_only_q"); if (marginAccelQ) marginAccelQ.checked = false;
}

function _applyCriteriaToInputs(c) {
  for (const [k, v] of Object.entries(c)) {
    if (k === "eps_positive") { const cb = document.getElementById("f_eps_positive"); if (cb) cb.checked = !!v; continue; }
    if (k === "sort")     { const s = document.getElementById("f_sort");     if (s) s.value = v; continue; }
    if (k === "sort_dir") { const s = document.getElementById("f_sort_dir"); if (s) s.value = v; continue; }
    if (k === "roe5y_min") {
      const el = document.getElementById("f_roe_min"); if (el) el.value = v;
      const cb = document.getElementById("f_roe_5y"); if (cb) cb.checked = true;
      continue;
    }
    const el = document.getElementById("f_" + k);
    if (el) el.value = v;
  }
}

function togglePreset(key) {
  if (key === "_clear") {
    _activePresets.clear();
    document.querySelectorAll(".filter-preset-btn").forEach(b => b.classList.remove("active"));
    _clearFilterInputs();
    const ind = document.getElementById("f_industries"); if (ind) ind.value = "";
    document.querySelectorAll(".filter-sector-chip.active").forEach(c => c.classList.remove("active"));
    return;
  }
  const CAP_KEYS = ["small_cap", "mid_cap", "large_cap"];
  if (CAP_KEYS.includes(key) && !_activePresets.has(key)) {
    // cap tiers are mutually exclusive — only one size at a time
    CAP_KEYS.forEach(k => {
      if (k !== key && _activePresets.has(k)) {
        _activePresets.delete(k);
        document.querySelector(`.filter-preset-btn[data-preset="${k}"]`)?.classList.remove("active");
      }
    });
  }
  if (_activePresets.has(key)) _activePresets.delete(key);
  else _activePresets.add(key);
  document.querySelector(`.filter-preset-btn[data-preset="${key}"]`)
    ?.classList.toggle("active", _activePresets.has(key));
  _clearFilterInputs();
  _applyCriteriaToInputs(_mergeActivePresets());
  if (_activePresets.size) runFilter();
}

function _collectFilterCriteria(market) {
  const num = (id) => { const el = document.getElementById(id); const v = el && el.value.trim(); return v ? v : null; };
  const params = new URLSearchParams();
  params.set("market", market);
  // market cap inputs are in billions → convert to absolute
  const mmin = num("f_mcap_min"), mmax = num("f_mcap_max");
  if (mmin) params.set("mcap_min", String(parseFloat(mmin) * 1e9));
  if (mmax) params.set("mcap_max", String(parseFloat(mmax) * 1e9));
  const passthrough = { pe_max:"f_pe_max", fpe_max:"f_fpe_max", pb_max:"f_pb_max", ps_max:"f_ps_max",
    div_yield_min:"f_div_yield_min", price_min:"f_price_min", price_max:"f_price_max",
    mom_min:"f_mom_min", mom_max:"f_mom_max",
    growth_min:"f_growth_min", growth_max:"f_growth_max", peg_max:"f_peg_max",
    revgrowth_min:"f_revgrowth_min",
    inst_min:"f_inst_min", inst_max:"f_inst_max", insider_min:"f_insider_min", insider_max:"f_insider_max",
    inst_holders_min:"f_inst_holders_min", top3_max:"f_top3_max", strategic_max:"f_strategic_max",
    earngrowth3y_min:"f_earngrowth3y_min", earngrowth4y_min:"f_earngrowth4y_min",
    earngrowthq4_min:"f_earngrowthq4_min", earngrowthq8_min:"f_earngrowthq8_min",
    reportcov_min:"f_reportcov_min", reportinst_min:"f_reportinst_min",
    accel_top_pct:"f_accel_top_pct", garp_top_pct:"f_garp_top_pct", timing_top_pct:"f_timing_top_pct", h52_top_pct:"f_h52_top_pct" };
  for (const [key, id] of Object.entries(passthrough)) { const v = num(id); if (v) params.set(key, v); }
  const roeVal = num("f_roe_min");
  if (roeVal) params.set(document.getElementById("f_roe_5y")?.checked ? "roe5y_min" : "roe_min", roeVal);
  // operating profit input is in ¥B → convert to absolute, same convention as market cap
  const opMin = num("f_opprofit_min");
  if (opMin) params.set("opprofit_min", String(parseFloat(opMin) * 1e9));
  if (document.getElementById("f_eps_positive")?.checked) params.set("eps_positive", "1");
  if (document.getElementById("f_report_positive_only")?.checked) params.set("report_positive_only", "1");
  if (document.getElementById("f_report_initiated_only")?.checked) params.set("report_initiated_only", "1");
  if (document.getElementById("f_turnaround_only")?.checked) params.set("turnaround_only", "1");
  if (document.getElementById("f_turnaround_only_q")?.checked) params.set("turnaround_only_q", "1");
  if (document.getElementById("f_revenue_accel_only")?.checked) params.set("revenue_accel_only", "1");
  if (document.getElementById("f_revenue_accel_only_q")?.checked) params.set("revenue_accel_only_q", "1");
  if (document.getElementById("f_margin_turnaround_only")?.checked) params.set("margin_turnaround_only", "1");
  if (document.getElementById("f_margin_turnaround_only_q")?.checked) params.set("margin_turnaround_only_q", "1");
  if (document.getElementById("f_profit_accel_only")?.checked) params.set("profit_accel_only", "1");
  if (document.getElementById("f_profit_accel_only_q")?.checked) params.set("profit_accel_only_q", "1");
  if (document.getElementById("f_margin_accel_only")?.checked) params.set("margin_accel_only", "1");
  if (document.getElementById("f_margin_accel_only_q")?.checked) params.set("margin_accel_only_q", "1");
  if (document.getElementById("f_above_resistance_only")?.checked) params.set("above_resistance_only", "1");
  // Stage checklists: numeric scores + boolean verdicts.
  [["f_base_score_min","base_score_min"],["f_adv_score_min","adv_score_min"],
   ["f_top_score_min","top_score_min"],["f_adv_vol_mult_min","adv_vol_mult_min"],
   ["f_rs_pct_min","rs_pct_min"]].forEach(([id,key]) => {
    const v = document.getElementById(id)?.value;
    if (v !== undefined && v !== "") params.set(key, v);
  });
  ["base_textbook_only","base_volume_dryup_only","adv_textbook_only","adv_volume_expanding_only",
   "adv_higher_lows_only","adv_price_above_only","top_textbook_only",
   "decl_confirmed_only","decl_volume_rising_only"].forEach(key => {
    if (document.getElementById("f_" + key)?.checked) params.set(key, "1");
  });
  if (document.getElementById("f_rs_pct_min")?.value) {
    const rb = document.getElementById("f_rs_basis")?.value;
    if (rb === "jt") params.set("rs_basis", "jt");
  }
  const _vb = document.getElementById("f_volratio_min")?.value;
  const _vs = document.getElementById("f_adv_vol_mult_min")?.value;
  if (_vb && _vs) {
    const mode = document.querySelector('input[name="vol_match"]:checked')?.value;
    if (mode === "any") params.set("vol_match", "any");
  }
  const picked = [1,2,3,4].filter(i => document.getElementById("f_stage"+i)?.checked);
  if (picked.length) params.set("stages", picked.join(","));
  const sb = document.getElementById("f_stage_basis")?.value;
  if (sb === "ema") params.set("stage_basis", "ema");
  [["mansfield_min","f_mansfield_min"],["volratio_min","f_volratio_min"],
   ["price_vs_ma_min","f_price_vs_ma_min"],["ma_slope_min","f_ma_slope_min"],
   ["sector_rs_min","f_sector_rs_min"],["sector_rank_max","f_sector_rank_max"]]
    .forEach(([k,id]) => { const v = num(id); if (v) params.set(k, v); });
  const secPicked = [1,2,3,4].filter(i => document.getElementById("f_sector_stage"+i)?.checked);
  if (secPicked.length) params.set("sector_stages", secPicked.join(","));
  const ind = num("f_industries");
  const sectorChips = [...document.querySelectorAll(".filter-sector-chip.active")].map(c => c.dataset.sector);
  if (ind) params.set("industries", ind);
  if (sectorChips.length) params.set("sectors", sectorChips.join(","));
  params.set("sort", document.getElementById("f_sort").value);
  params.set("sort_dir", document.getElementById("f_sort_dir").value);
  params.set("limit", document.getElementById("f_limit").value || "50");
  return params;
}

// Show a stage's confirmation controls only when that stage is ticked. With all
// four blocks visible at once the stage picker itself got lost in 29 controls.
function _syncStageChecklists() {
  const picked = [1, 2, 3, 4].filter(i => document.getElementById("f_stage" + i)?.checked);
  document.querySelectorAll(".stage-chk-block").forEach(b => {
    const on = picked.includes(+b.dataset.stage);
    b.hidden = !on;
    // Clearing a hidden block prevents an invisible control from silently
    // filtering results — the bug you would never find by looking at the page.
    if (!on) b.querySelectorAll("input").forEach(el => {
      if (el.type === "checkbox") el.checked = false; else el.value = "";
    });
  });
  const hint = document.getElementById("f_chk_hint");
  if (hint) hint.hidden = picked.length > 0;
}

// Live feedback on what is actually switched on. With 9 groups and ~70 controls
// there was no way to tell at a glance which filters were active — especially
// once controls moved behind disclosures, where a set value is invisible.
function _syncFilterActivity() {
  let total = 0;
  // Sort/direction/limit are DISPLAY settings, not filters — counting them made
  // a freshly loaded page claim "1 filter active" because Max results defaults
  // to 50, and put a misleading badge on Sort & Show.
  const NOT_FILTERS = new Set(["f_sort", "f_sort_dir", "f_limit"]);
  document.querySelectorAll(".filter-group").forEach(g => {
    let n = 0;
    g.querySelectorAll("input").forEach(el => {
      if (NOT_FILTERS.has(el.id)) return;
      if (el.type === "checkbox") { if (el.checked) n++; }
      else if ((el.value || "").trim() !== "") n++;
    });
    n += g.querySelectorAll(".filter-sector-chip.active").length;
    total += n;
    let badge = g.querySelector(".filter-active-badge");
    const title = g.querySelector(".filter-group-title");
    if (!badge && title) {
      badge = document.createElement("span");
      badge.className = "filter-active-badge";
      title.appendChild(badge);
    }
    if (badge) { badge.textContent = n ? `${n} active` : ""; badge.hidden = !n; }
    g.classList.toggle("filter-group-active", n > 0);
  });
  const bar = document.getElementById("filterActiveCount");
  if (bar) bar.textContent = total ? `${total} filter${total === 1 ? "" : "s"} active`
                                   : "No filters set — showing the whole universe";
  const clr = document.getElementById("filterClearAll");
  if (clr) clr.hidden = !total;
}

// The AND/OR choice only means anything when BOTH volume tests carry a value,
// so it stays hidden until then rather than sitting there as a dead control.
function _syncVolMatch() {
  const b = document.getElementById("f_volratio_min");
  const t = document.getElementById("f_adv_vol_mult_min");
  const row = document.getElementById("volMatchRow");
  if (!row || !b || !t) return;
  row.hidden = !((b.value || "").trim() !== "" && (t.value || "").trim() !== "");
}

async function runFilter() {
  const market = state.market;
  const btn = document.getElementById("filterRunBtn");
  const resEl = document.getElementById("filterResults");
  const params = _collectFilterCriteria(market);
  btn.disabled = true; btn.textContent = "Filtering…";
  try {
    const data = await fetchJson(`/api/filter?${params.toString()}`);
    if (data.status === "building") {
      const pct = data.total ? Math.round((data.progress || 0) / data.total * 100) : 0;
      resEl.innerHTML = `<div class="filter-building">
        <div class="filter-building-msg">Building US market snapshot… ${pct}% <span class="filter-building-sub">(${data.progress || 0}/${data.total || "?"} stocks · one-time, ~1 min)</span></div>
        <div class="filter-progress"><div class="filter-progress-bar" style="width:${pct}%"></div></div>
      </div>`;
      _filterPollTimer = setTimeout(runFilter, 2500);
      return;
    }
    if (data.status === "error") { resEl.innerHTML = `<div class="sc-error">⚠ ${escapeHtml(data.error || "Filter failed")}</div>`; return; }
    renderFilterResults(data, market);
  } catch (err) {
    resEl.innerHTML = `<div class="sc-error">⚠ ${escapeHtml(err.message)}</div>`;
  } finally {
    btn.disabled = false; btn.textContent = "Run filter";
  }
}

// A missing P/E means one of two DIFFERENT things and they should not look the
// same: the company lost money (P/E is undefined, not unknown), or the data is
// genuinely absent. EPS <= 0 distinguishes them — measured on the live universe,
// 36 of 39 blank-P/E rows had negative EPS and none had positive EPS with a
// blank P/E, so the test is unambiguous in practice.
function peCell(row, key = "pe") {
  const v = row[key];
  if (v != null && v !== "") return { text: (+v).toFixed(1), cls: "", title: "" };
  const eps = row.eps;
  if (eps != null && eps <= 0)
    return { text: "Loss", cls: "pe-loss",
             title: `No P/E because earnings are negative (EPS ${(+eps).toFixed(2)}). Not missing data.` };
  return { text: "—", cls: "pe-nodata", title: "P/E unavailable from the data source." };
}

function renderFilterResults(data, market) {
  const resEl = document.getElementById("filterResults");
  const cur = data.currency || (market === "cn" ? "¥" : "$");
  const rows = data.results || [];
  const isUS = market !== "cn";
  if (!rows.length) {
    resEl.innerHTML = `<div class="filter-empty">No stocks matched. Try loosening the criteria. <span class="filter-count">(scanned ${data.universe || 0})</span></div>`;
    return;
  }
  const mcap = (v) => v == null ? "—" : v >= 1e12 ? `${cur}${(v/1e12).toFixed(2)}T` : v >= 1e9 ? `${cur}${(v/1e9).toFixed(1)}B` : `${cur}${(v/1e6).toFixed(0)}M`;
  const n = (v, d=1) => v == null ? "—" : (+v).toFixed(d);
  const grw = (v) => v == null ? "—" : `${v > 0 ? "+" : ""}${(+v).toFixed(0)}%`;
  const SA = `<span class="filter-arrow">⇅</span>`;   // sort-arrow indicator
  const growthCols = isUS ? `<th class="filter-th-sort" data-sort="impl_growth" title="Analysts' expected EPS growth (P/E ÷ fwd P/E)">Fwd Grw ${SA}</th>` : "";
  // Weinstein Stage Analysis columns. These arrive WITH the filter response
  // (merged into each row server-side), so unlike the deferred score columns
  // they render immediately rather than showing a "·" placeholder.
  const stageCols = `
    <th class="filter-th-sort filter-th-stage" data-sort="stage" title="Weinstein stage on the 30-week SMA: 1 Basing, 2 Advancing, 3 Topping, 4 Declining. Descriptive state, not a buy/sell signal. Blank = under 56 weeks of history, so unclassified.">Stage ${SA}</th>
    <th class="filter-th-sort filter-th-stage" data-sort="rs_pct" title="Percentile relative strength, 1-99: how the stock's momentum ranks against every other stock in the universe. IBD construction — 0.4x the 3-month return plus 0.2x each of the 6/9/12-month returns. Bounded and uniform, unlike Mansfield RS. 80+ is conventionally treated as strong.">RS%ile ${SA}</th>
    <th class="filter-th-sort filter-th-stage" data-sort="mansfield_rs" title="Mansfield RS = ((price ÷ benchmark) ÷ its own 52-week average − 1) × 100. Above 0 = outperforming the broad market.">RS ${SA}</th>
    <th class="filter-th-sort filter-th-stage" data-sort="vol_ratio" title="BREAKOUT volume: the latest COMPLETED week's volume ÷ its trailing 13-week average — a single-week surge. Distinct from the SUSTAINED test (mean of 13 weeks vs the prior 13) behind the Chk column. The in-progress week is excluded.">Vol× ${SA}</th>
    <th class="filter-th-sort filter-th-stage" data-sort="price_vs_sma30" title="How far price sits above/below its 30-week simple moving average, in percent.">vs 30wMA ${SA}</th>
    <th class="filter-th-sort filter-th-stage" data-sort="sector_rs" title="The stock's INDUSTRY GROUP: its own Weinstein stage and Mansfield RS vs the market. Weinstein screens top-down — a Stage 2 stock inside a Stage 2 group is the higher-odds setup.">Group ${SA}</th>
    <th class="filter-th-stage" title="Confirmation checklist for the stock's OWN stage — is the label backed by a textbook pattern? Stage 1 base and Stage 2 advance score out of 4, Stage 3 top out of 3, Stage 4 decline out of 2 (its optional volume test is excluded from the score). A full score is marked ✓. Thresholds are set from measured universe percentiles, not from the book.">Chk</th>`;
  const stageCell = (r) => {
    const st = r.stage;
    // Distinguish "too new to analyse" from "history is short" from a real gap.
    const why = r.stage_data === 'missing'
      ? 'Too new for the weekly cache — a 30-week average needs 56 weeks of history.'
      : r.stage_data === 'insufficient'
        ? `Only ${r.weeks_available || 0} weeks of history; 56 are needed.`
        : 'Insufficient history';
    const lbl = st ? ['', '1 Base', '2 Adv', '3 Top', '4 Decl'][st]
                   : (r.stage_data === 'missing' ? 'new' : '—');
    const cls = st ? ' stage-s' + st : '';
    const rs = r.mansfield_rs, vr = r.vol_ratio, pv = r.price_vs_sma30;
    return `<td class="filter-td-num filter-td-stage"><span class="stage-pill${cls}${st ? '' : ' stage-pill-na'}" title="${escapeHtml(st ? (r.stage_label || why) : why)}">${lbl}</span></td>`
      + (() => { const rp = r.rs_pct;
          const cl = rp == null ? '' : rp >= 80 ? 'stage-pos' : rp <= 20 ? 'stage-neg' : '';
          return `<td class="filter-td-num ${cl}" title="${rp == null ? 'Not enough history to rank' : 'Stronger momentum than ' + Math.round(rp) + '% of the universe'}">${rp == null ? '—' : Math.round(rp)}</td>`; })()
      + `<td class="filter-td-num ${rs != null && rs > 0 ? 'stage-pos' : rs != null ? 'stage-neg' : ''}">${rs == null ? '—' : (rs > 0 ? '+' : '') + (+rs).toFixed(1)}</td>`
      + `<td class="filter-td-num ${vr != null && vr >= 1.5 ? 'stage-pos' : ''}">${vr == null ? '—' : (+vr).toFixed(2) + '×'}</td>`
      + `<td class="filter-td-num ${pv != null && pv > 0 ? 'stage-pos' : pv != null ? 'stage-neg' : ''}">${pv == null ? '—' : (pv > 0 ? '+' : '') + (+pv).toFixed(1) + '%'}</td>`
      + `<td class="filter-td-num filter-td-stage" title="${escapeHtml((r.sector_name || '') + (r.sector_stage_label ? ' — ' + r.sector_stage_label : ''))}">`
        + (r.sector_stage ? `<span class="stage-pill stage-s${r.sector_stage}">${r.sector_stage}</span>` : '—')
        + (r.sector_rs == null ? '' : ` <span class="${r.sector_rs > 0 ? 'stage-pos' : 'stage-neg'}">${r.sector_rs > 0 ? '+' : ''}${(+r.sector_rs).toFixed(0)}</span>`)
        + `</td>`
      // Checklist for the stock's OWN stage — one column rather than four, so
      // the table stays readable; the stock page shows all of them.
      + (() => {
          const map = {
            1: [r.base_testable, r.base_score, 4, r.base_textbook, 'base'],
            2: [r.adv_testable,  r.adv_score,  4, r.adv_textbook,  'advance'],
            3: [r.top_testable,  r.top_score,  3, r.top_textbook,  'top'],
            4: [r.decl_testable, r.decl_score, 2, r.decl_confirmed,'decline'],
          }[st];
          if (!map || !map[0] || map[1] == null)
            return `<td class="filter-td-num filter-td-stage" title="Not enough weekly history to run this stage's checklist.">—</td>`;
          const [, sc, outOf, full, word] = map;
          return `<td class="filter-td-num filter-td-stage ${full ? 'stage-pos' : ''}" title="${sc}/${outOf} ${escapeHtml(word)} criteria met${full ? ' — textbook' : ''}">`
               + `${sc}/${outOf}${full ? ' ✓' : ''}</td>`;
        })();
  };
  const scoreCols = `
    <th class="filter-th-sort filter-th-score" data-sort="magic_rank" title="Greenblatt Magic Formula combined rank (1 = best: cheap on EBIT/EV AND high return on capital)">Magic ${SA}</th>
    <th class="filter-th-sort filter-th-score" data-sort="f_score" title="Piotroski F-Score 0–9 (financial strength)">F ${SA}</th>
    <th class="filter-th-sort filter-th-score" data-sort="earnings_yield" title="Earnings Yield = EBIT ÷ EV (higher = cheaper)">EarnYld ${SA}</th>
    <th class="filter-th-sort filter-th-score" data-sort="roc" title="Return on Capital (higher = better business)">ROC ${SA}</th>
    <th class="filter-th-sort filter-th-score" data-sort="real_growth_3y" title="Real trailing 3-yr EPS CAGR">Real 3Y ${SA}</th>
    <th class="filter-th-sort filter-th-score" data-sort="graham_mos" title="Graham margin of safety: how far price is below Graham's conservative fair value (positive = downside cushion)">Graham MoS ${SA}</th>
    ${isUS ? `<th class="filter-th-sort filter-th-score" data-sort="dcf_iv" title="Two-stage FCFE DCF intrinsic value per share (CAPM discount, default growth fading to 2.5%). Fill after scoring.">Intrinsic ${SA}</th>
    <th class="filter-th-sort filter-th-score" data-sort="dcf_mos" title="DCF margin of safety = (intrinsic − price) ÷ intrinsic. Positive = price sits below DCF fair value.">DCF MoS ${SA}</th>
    <th class="filter-th-sort filter-th-score" data-sort="beat_rate" title="Earnings beat rate: % of the last ≤8 quarters where reported EPS topped consensus. Hover a cell for the raw count and next earnings date.">Beat ${SA}</th>
    <th class="filter-th-sort filter-th-score" data-sort="est_rev" title="Estimate momentum: % change in the current-year consensus EPS estimate over the last 90 days. Positive = analysts raising numbers. Hover for the analyst forward-growth estimate.">Est↑ ${SA}</th>
    <th class="filter-th-sort filter-th-score" data-sort="target_upside" title="Analyst mean target price vs current price. Hover for # analysts, rating, insider %, short %.">Tgt↑ ${SA}</th>
    <th class="filter-th-sort filter-th-score" data-sort="rd_intensity" title="R&D intensity = R&D ÷ revenue. Reinvestment / innovation proxy (— for companies with no R&D line).">R&amp;D ${SA}</th>
    <th class="filter-th-sort filter-th-score" data-sort="net_buyback" title="Net buyback: year-over-year change in share count, sign-flipped. Positive = shares shrank (buybacks); negative = dilution.">Buyback ${SA}</th>
    <th class="filter-th-sort filter-th-score" data-sort="sbc_rev" title="Stock-based compensation ÷ revenue. A real cost that FCF hides (SBC is added back to cash flow). LOWER is better. Hover for SBC as % of FCF.">SBC ${SA}</th>
    <th class="filter-th-sort filter-th-score" data-sort="insider_net" title="Net insider buying over the last 6 months, as % of insider-held shares. Positive = insiders buying (Lynch: they buy for only one reason).">Insider ${SA}</th>` : ""}
    <th class="filter-th-sort filter-th-score qcol" data-sort="roic" title="Return on Invested Capital = NOPAT ÷ invested capital. Core quality/moat signal; higher = better business.">ROIC ${SA}</th>
    <th class="filter-th-sort filter-th-score qcol" data-sort="gp_assets" title="Gross-profitability (Novy-Marx) = gross profit ÷ total assets. Cleanest 'good business' signal; higher = more productive.">GP/A ${SA}</th>
    <th class="filter-th-sort filter-th-score qcol" data-sort="accruals" title="Accruals (Sloan) = (net income − operating cash flow) ÷ assets. LOWER is better — negative means earnings are more than cash-backed.">Accr ${SA}</th>
    <th class="filter-th-sort filter-th-score qcol" data-sort="gross_margin" title="Gross margin = gross profit ÷ revenue. Pricing power; higher = more durable margin.">GM ${SA}</th>`;
  const body = rows.map(r => `
    <tr class="filter-row" data-ticker="${escapeHtml(r.symbol)}">
      <td class="pos-td"><button class="pos-add" data-t="${escapeHtml(r.symbol)}" title="Add to Pipeline as candidate" type="button">＋</button></td>
      <td class="filter-td-sym">${escapeHtml(r.symbol)}</td>
      <td class="filter-td-name">${escapeHtml(r.name || "")}</td>
      <td class="filter-td-ind">${escapeHtml(r.industry || r.sector || "")}</td>
      <td class="filter-td-num">${mcap(r.market_cap)}</td>
      ${(() => { const c = peCell(r); return `<td class="filter-td-num ${c.cls}" title="${escapeHtml(c.title)}">${c.text}</td>`; })()}
      <td class="filter-td-num">${n(r.pb, 2)}</td>
      <td class="filter-td-num">${r.div_yield == null ? "—" : n(r.div_yield, 2) + "%"}</td>
      ${isUS ? `<td class="filter-td-num filter-td-grw">${grw(r.impl_growth)}</td>` : ""}
      ${stageCell(r)}
      <td class="filter-td-num filter-td-score" data-k="magic_rank" data-s="${escapeHtml(r.symbol)}">·</td>
      <td class="filter-td-num filter-td-score" data-k="f_score" data-s="${escapeHtml(r.symbol)}">·</td>
      <td class="filter-td-num filter-td-score" data-k="earnings_yield" data-s="${escapeHtml(r.symbol)}">·</td>
      <td class="filter-td-num filter-td-score" data-k="roc" data-s="${escapeHtml(r.symbol)}">·</td>
      <td class="filter-td-num filter-td-score" data-k="real_growth_3y" data-s="${escapeHtml(r.symbol)}">·</td>
      <td class="filter-td-num filter-td-score" data-k="graham_mos" data-s="${escapeHtml(r.symbol)}">·</td>
      ${isUS ? `<td class="filter-td-num filter-td-score" data-k="dcf_iv" data-s="${escapeHtml(r.symbol)}">·</td>
      <td class="filter-td-num filter-td-score" data-k="dcf_mos" data-s="${escapeHtml(r.symbol)}">·</td>
      <td class="filter-td-num filter-td-score" data-k="beat_rate" data-s="${escapeHtml(r.symbol)}">·</td>
      <td class="filter-td-num filter-td-score" data-k="est_rev" data-s="${escapeHtml(r.symbol)}">·</td>
      <td class="filter-td-num filter-td-score" data-k="target_upside" data-s="${escapeHtml(r.symbol)}">·</td>
      <td class="filter-td-num filter-td-score" data-k="rd_intensity" data-s="${escapeHtml(r.symbol)}">·</td>
      <td class="filter-td-num filter-td-score" data-k="net_buyback" data-s="${escapeHtml(r.symbol)}">·</td>
      <td class="filter-td-num filter-td-score" data-k="sbc_rev" data-s="${escapeHtml(r.symbol)}">·</td>
      <td class="filter-td-num filter-td-score" data-k="insider_net" data-s="${escapeHtml(r.symbol)}">·</td>` : ""}
      <td class="filter-td-num filter-td-score qcol" data-k="roic" data-s="${escapeHtml(r.symbol)}">·</td>
      <td class="filter-td-num filter-td-score qcol" data-k="gp_assets" data-s="${escapeHtml(r.symbol)}">·</td>
      <td class="filter-td-num filter-td-score qcol" data-k="accruals" data-s="${escapeHtml(r.symbol)}">·</td>
      <td class="filter-td-num filter-td-score qcol" data-k="gross_margin" data-s="${escapeHtml(r.symbol)}">·</td>
    </tr>`).join("");
  const stale = data.built_at ? `· snapshot ${_ago(data.built_at)}` : "";
  const nScore = Math.min(rows.length, isUS ? 50 : 25);
  resEl.innerHTML = `
    <div class="filter-results-head">
      <strong>${data.count}</strong> match${data.count === 1 ? "" : "es"} <span class="filter-count">of ${data.universe} scanned ${stale}</span>
      ${data.count > rows.length ? `<span class="filter-count">· showing top ${rows.length}</span>` : ""}
    </div>
    <div class="filter-toolbar">
      <button id="filterScoreBtn" class="secondary-btn filter-confirm-btn">⚡ Score shortlist (top ${nScore})</button>
      <span class="filter-confirm-note">Computes F-Score, Magic Formula rank, Earnings Yield, ROC &amp; real 3-yr growth per stock — then click a score header to rank.</span>
      <span id="filterScoreStatus" class="filter-confirm-status"></span>
      <button id="filterExportBtn" class="secondary-btn" type="button" title="Download the results table as displayed (incl. score columns once computed)">⬇ Export Excel</button>
    </div>
    <div class="mos-bar" id="mosBar">
      <span class="mos-bar-label">Margin-of-safety filters:</span>
      ${isUS ? `<label class="mos-ctrl"><input type="checkbox" id="mosDcfOn"> DCF — buy at ≤ <input type="number" id="mosDcfPct" value="80" min="10" max="300" step="5"> % of intrinsic value <span id="mosDcfImplied" class="mos-implied"></span></label>` : ""}
      <label class="mos-ctrl"><input type="checkbox" id="mosGrahamOn"> Graham MoS ≥ <input type="number" id="mosGrahamPct" value="20" min="-500" max="99" step="5"> %</label>
      <span id="mosCount" class="mos-count"></span>
    </div>
    <div class="filter-table-wrap">
    <table class="filter-table">
      <thead><tr>
        <th class="pos-th" title="Add to Pipeline"></th>
        <th>Ticker</th><th>Name</th><th>Industry</th>
        <th class="filter-th-sort" data-sort="market_cap">Mkt Cap ${SA}</th>
        <th class="filter-th-sort" data-sort="pe">P/E ${SA}</th>
        <th class="filter-th-sort" data-sort="pb">P/B ${SA}</th>
        <th class="filter-th-sort" data-sort="div_yield">Div ${SA}</th>
        ${growthCols}${stageCols}${scoreCols}
      </tr></thead>
      <tbody>${body}</tbody>
    </table>
    </div>
    <div class="filter-hint">Click any row to open the full deep-dive screen. Click a column header (<strong>Mkt Cap / P/E / P/B / Div …</strong>) to sort; click again to flip ascending ↔ descending. Score &amp; quality columns fill after "Score shortlist".</div>
    <details class="filter-defs"><summary>ⓘ Metric definitions</summary><div class="filter-defs-body">
      <p><strong>Fwd Grw</strong> — analysts' expected EPS growth (P/E ÷ forward P/E).</p>
      <p><strong>Magic</strong> — Greenblatt Magic Formula rank: Earnings Yield + Return on Capital combined (1 = best).</p>
      <p><strong>F</strong> — Piotroski F-Score (0–9): financial-strength checklist across profitability, leverage &amp; efficiency. ≥8 strong, ≤3 weak.</p>
      <p><strong>EarnYld</strong> — Earnings Yield = EBIT ÷ Enterprise Value. Higher = cheaper.</p>
      <p><strong>ROC</strong> — Greenblatt Return on Capital = EBIT ÷ (working capital + net fixed assets).</p>
      <p><strong>Real 3Y</strong> — actual trailing 3-year EPS CAGR (vs. Fwd Grw's estimate).</p>
      <p><strong>Graham MoS</strong> — Graham margin of safety: how far price sits below Graham's conservative fair value. Positive = cushion. Asset-based &amp; strict, so high-growth / asset-light names often read deeply negative.
        <details class="filter-defs-method"><summary>How it's calculated →</summary><div>
          <p class="fd-formula">Graham Number = √(22.5 × EPS × BVPS)<br>Graham MoS % = (Graham Number − Price) ÷ Graham Number × 100</p>
          <p><strong>EPS</strong> — trailing-12-month earnings per share. Must be <strong>positive</strong>, else no Graham Number exists (shows "—").</p>
          <p><strong>BVPS</strong> — book value per share = stockholders' equity ÷ shares outstanding.</p>
          <p><strong>22.5</strong> — Graham's ceiling constant = 15 × 1.5: a defensive stock shouldn't exceed <strong>15× earnings</strong> <em>and</em> <strong>1.5× book</strong> (so P/E × P/B ≤ 22.5). The formula inverts that cap into a price.</p>
          <p class="fd-eg"><strong>Example:</strong> EPS $5, BVPS $20 → √(22.5·5·20) = √2250 = $47.43. At a $38 price → MoS = (47.43−38)/47.43 = <strong>+19.9%</strong> (≈Graham's 20% threshold). At $60 → −26% (overvalued).</p>
          <p class="fd-note">Rewards <em>book value</em> relative to price → meaningful for asset-heavy / deep-value / banks; structurally pessimistic for compounders whose worth is brand &amp; cash flow, not balance sheet (why KO/COST/GOOGL read −300%+ here).</p>
        </div></details>
      </p>
      <p><strong>Intrinsic / DCF MoS</strong> (US only) — a two-stage discounted-cash-flow fair value per share; <strong>DCF MoS = (Intrinsic − Price) ÷ Intrinsic</strong>. Same engine as the deep-dive card's defaults. Cash-flow-anchored, so it's the meaningful gauge for quality/growth names.
        <details class="filter-defs-method"><summary>How it's calculated →</summary><div>
          <p><strong>1 · Base free cash flow</strong> — average of the <strong>last 3 years'</strong> Free Cash Flow (or Operating CF − CapEx) to normalize one-off years.</p>
          <p><strong>2 · Discount rate</strong> (CAPM cost of equity): <span class="fd-formula">r = Rf + β × ERP = 4.3% + β × 5.0%</span> — Rf = risk-free (~10Y Treasury), ERP = equity risk premium, β = the stock's beta. Low-β (KO ≈0.35) → ~6%; high-β → ~9–10%.</p>
          <p><strong>3 · Two-stage growth</strong> — stage-1 growth from TTM earnings, <strong>clamped 0–20%</strong> (8% if unknown), then <strong>fades linearly to a 2.5% terminal over 10 yrs</strong> (nothing outgrows the economy forever):<br>
            <span class="fd-formula">FCFₜ = FCFₜ₋₁ × (1 + gₜ) &nbsp;;&nbsp; PV = FCFₜ ÷ (1 + r)ᵗ</span></p>
          <p><strong>4 · Terminal value</strong> (Gordon, everything past yr 10): <span class="fd-formula">TV = FCF₁₀ × 1.025 ÷ (r − 2.5%)</span>, discounted back.</p>
          <p><strong>5 · Intrinsic value &amp; MoS:</strong> <span class="fd-formula">IV = (Σ PV(FCF) + PV(TV)) ÷ shares</span>; MoS = (IV − Price) ÷ IV.</p>
          <p class="fd-eg"><strong>Example — Coca-Cola:</strong> β 0.35 → r = 4.3 + 0.35×5 = 6.05%, ~6% growth fading to 2.5% → IV ≈ $88.21 vs $84.14 price → DCF MoS <strong>+4.6%</strong> (roughly fair).</p>
          <p class="fd-note">Skipped for banks/insurers (FCF isn't meaningful). Foreign ADRs with a currency mismatch (FCF in local currency vs USD price — e.g. Petrobras) are suppressed to "—" when Intrinsic/Price falls outside [0.1, 8].</p>
        </div></details>
      </p>
      <p class="filter-defs-q"><strong>Forward &amp; analyst signals</strong> (US) — what the sell-side expects, not just trailing history:</p>
      <p><strong>Est↑</strong> — estimate momentum: % change in the current fiscal-year consensus EPS estimate over the last 90 days. <em>Positive = analysts raising numbers</em> — one of the strongest near-term signals that a grower is being under-appreciated. Hover shows the analyst next-year EPS-growth estimate.</p>
      <p><strong>Tgt↑</strong> — analyst mean price target vs. current price (% upside). Treat as directional sentiment, not gospel — targets cluster near price and drift with it. Hover shows # analysts, consensus rating, insider %, and short %.</p>
      <p><strong>R&amp;D</strong> — R&amp;D ÷ revenue. Reinvestment/innovation intensity; "—" for firms with no R&amp;D line (staples, financials, many industrials).</p>
      <p><strong>Buyback</strong> — year-over-year change in share count, sign-flipped: <em>positive = shares shrank (buybacks), negative = dilution</em>. A capital-allocation signal — steady buybacks compound per-share value; persistent dilution quietly erodes it.</p>
      <p><strong>SBC</strong> — stock-based compensation ÷ revenue. <em>Lower is better.</em> SBC is a real cost that free cash flow hides (it's added back as non-cash), so high-SBC companies look cash-rich while paying employees with your dilution. ≥10% red, ≤2% green. Hover shows SBC as % of FCF — the share of "free" cash flow that isn't really free. Read together with Buyback: a green Buyback + high SBC means the company spends billions just to mop up its own comp dilution.</p>
      <p><strong>Insider</strong> — net insider buying over the last 6 months as % of insider-held shares. Positive = executives buying with their own money (Lynch: "insiders sell for many reasons, but buy for only one"). Heavy selling (&lt; −5%) flags red.</p>
      <p><strong>Beat</strong> (US) — earnings beat rate: the share of the last ≤8 reported quarters where actual EPS came in <em>above</em> Wall Street consensus (from Yahoo's earnings feed). Hover a cell for the raw count and the next earnings date. A consistent high beat rate signals either a predictable business or conservative guidance; a low one, frequent misses. Read it as an execution/quality signal, not a valuation one — and note the data is Yahoo's aggregated consensus, ≤8 quarters deep.</p>
      <p class="filter-defs-q"><strong>Graham vs DCF — keep both:</strong> Graham is a hard <em>asset floor</em> ("am I protected by book value?"), DCF a forward <em>going-concern value</em> ("what are future cash flows worth?"). Cheap on both = genuine bargain; cheap only on DCF = quality compounder; cheap only on Graham = often cheap assets + weak cash generation. The <strong>margin-of-safety filters</strong> above map "buy at ≤ X% of intrinsic value" to DCF MoS ≥ (100 − X)%, and let you set a Graham-MoS floor — independently, AND'd together. Because a conservative DCF puts most quality names <em>above</em> their intrinsic value, X can exceed 100% (e.g. 130% ⟺ DCF MoS ≥ −30%) to loosen the screen and accept paying a premium.</p>
      <p class="filter-defs-q"><strong>Quality lens</strong> — separate columns, no single verdict (sort by whichever matters):</p>
      <p><strong>ROIC</strong> — Return on Invested Capital = NOPAT ÷ (equity + debt − cash). The core moat/quality signal; higher = a better business earning above its cost of capital.</p>
      <p><strong>GP/A</strong> — Gross-profitability (Novy-Marx) = gross profit ÷ total assets. The cleanest "good business" measure — gross profit sits above SG&amp;A/R&amp;D/interest/tax distortions, so a heavy reinvestor isn't penalised. Higher = more productive.</p>
      <p><strong>Accr</strong> — Accruals (Sloan) = (net income − operating cash flow) ÷ assets. <em>Lower is better</em>: negative means earnings are more than fully cash-backed; high positive = earnings-quality red flag.</p>
      <p><strong>GM</strong> — Gross margin = gross profit ÷ revenue. Pricing power / margin durability.</p>
    </div></details>`;
  // stash row data for client-side column sorting; reset sort state for the new result set
  _filterRowData = Object.fromEntries(rows.map(r => [r.symbol, r]));
  _filterSortState = { key: null, dir: null };
  resEl.querySelectorAll(".filter-row").forEach(tr => {
    tr.addEventListener("click", (e) => { if (!e.target.closest(".pos-add")) openFullScreen(tr.dataset.ticker, market); });
  });
  resEl.querySelectorAll(".pos-add").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const t = btn.dataset.t;
      _addToPipeline(market, t, { name: _filterRowData[t]?.name, source: "filter",
        snapshot: { ..._filterRowData[t], ...(_filterScoreData[t] || {}) } }, btn);
    });
  });
  resEl.querySelectorAll(".filter-th-sort").forEach(th => {
    th.addEventListener("click", () => _sortFilterBy(th.dataset.sort));
  });
  document.getElementById("filterScoreBtn").addEventListener("click", () => scoreShortlist(market, rows.slice(0, nScore)));
  document.getElementById("filterExportBtn").addEventListener("click", () => exportFilterResults(market, rows));
  ["mosDcfOn", "mosDcfPct", "mosGrahamOn", "mosGrahamPct"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener(el.type === "checkbox" ? "change" : "input", _applyMosFilter);
  });
  _applyMosFilter();   // populate the implied-MoS readout on first render
}

// ── adjustable margin-of-safety filters over the scored shortlist ──────────────
function _applyMosFilter() {
  const tbody = document.querySelector(".filter-table tbody");
  if (!tbody) return;
  const dcfOn = document.getElementById("mosDcfOn")?.checked;
  const dcfPct = +(document.getElementById("mosDcfPct")?.value ?? 80);   // pay ≤ X% of intrinsic
  // live readout: X% of intrinsic ⟺ DCF MoS ≥ (100 − X)%  (X > 100 = accept a negative margin)
  const impliedEl = document.getElementById("mosDcfImplied");
  if (impliedEl) {
    const thr = 100 - dcfPct;
    impliedEl.textContent = `→ DCF MoS ≥ ${thr > 0 ? "+" : ""}${thr}%`;
    impliedEl.classList.toggle("mos-implied-neg", thr < 0);
  }
  const grOn = document.getElementById("mosGrahamOn")?.checked;
  const grPct = +(document.getElementById("mosGrahamPct")?.value ?? 20);
  const countEl = document.getElementById("mosCount");
  const anyScored = Object.keys(_filterScoreData).length > 0;
  let shown = 0, total = 0;
  tbody.querySelectorAll(".filter-row").forEach(tr => {
    total++;
    const sc = _filterScoreData[tr.dataset.ticker] || {};
    let pass = true;
    if (dcfOn) {
      // buy at ≤ X% of intrinsic  ⟺  DCF MoS ≥ (100 − X)%  (null = unknown = fail)
      const mos = sc.dcf_mos;
      pass = pass && (mos != null && mos >= 100 - dcfPct);
    }
    if (grOn) {
      const gm = sc.graham_mos;
      pass = pass && (gm != null && gm >= grPct);
    }
    tr.classList.toggle("mos-hidden", !pass);
    if (pass) shown++;
  });
  if (countEl) {
    if (!dcfOn && !grOn) countEl.textContent = "";
    else if (!anyScored) countEl.textContent = "⚠ Score the shortlist first to populate MoS values.";
    else countEl.textContent = `${shown} of ${total} pass the MoS filter`;
  }
}

function _csvCell(v) {
  if (v === null || v === undefined || v === "") return "";
  const s = String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

// Exports the currently-rendered Filter results (base criteria + any computed
// score columns) as a CSV — Excel opens CSV natively, and a UTF-8 BOM keeps
// A-share Chinese names/industries from garbling on Windows Excel.
function exportFilterResults(market, rows) {
  const isUS = market !== "cn";
  const cols = [
    ["symbol", "Ticker"], ["name", "Name"],
    ["industry", isUS ? "Sector/Industry" : "Industry"],
    ["market_cap", "Market Cap"], ["pe", "P/E"], ["_pe_status", "P/E Status"], ["pb", "P/B"], ["div_yield", "Div Yield %"],
    ...(isUS ? [["impl_growth", "Fwd Growth %"], ["inst_pct", "Institutional %"], ["insider_pct", "Insider %"],
      ["inst_count", "# Inst Holders"], ["top3_conc", "Top-3 Conc %"], ["earngrowth_min_3y", "Net Income Growth Min 3Y %"],
      ["accel_proxy", "MAGNET ACCEL proxy (pp)"], ["accel_pctile", "MAGNET ACCEL pctile"],
      ["garp_ey", "MAGNET GARP proxy (EY %)"], ["garp_pctile", "MAGNET GARP pctile"],
      ["timing_proxy", "MAGNET TIMING proxy (%)"], ["timing_pctile", "MAGNET TIMING pctile"],
      ["h52_proxy", "MAGNET H52 proxy (%)"], ["h52_pctile", "MAGNET H52 pctile"],
      ["turnaround_accel", "MAGNET Turnaround-then-Accel"], ["turnaround_accel_q", "MAGNET Turnaround-then-Accel (Q)"], ["revenue_accel", "MAGNET Revenue Accel"], ["revenue_accel_q", "MAGNET Revenue Accel (Q)"], ["margin_turnaround_accel", "MAGNET Margin Turnaround"], ["margin_turnaround_accel_q", "MAGNET Margin Turnaround (Q)"], ["profit_accel", "MAGNET Profit Accel"], ["profit_accel_q", "MAGNET Profit Accel (Q)"], ["margin_accel", "MAGNET Margin Accel"], ["margin_accel_q", "MAGNET Margin Accel (Q)"], ["stage", "Weinstein Stage"], ["stage_label", "Stage (label)"], ["sector_name", "Industry Group"], ["sector_stage", "Group Stage"], ["sector_rs", "Group RS"], ["sector_rs_rank", "Group RS Rank"], ["sector_pct_stage2", "Group % in Stage 2"], ["stage_ema", "Stage (EMA basis)"], ["rs_pct", "RS Percentile (IBD)"], ["rs_pct_jt", "RS Percentile (12-1)"], ["mansfield_rs", "Mansfield RS"], ["vol_ratio", "Volume Ratio (x avg)"], ["price_vs_sma30", "% vs 30wk SMA"], ["price_vs_ema30", "% vs 30wk EMA"], ["sma30_slope", "30wk SMA slope % (13wk)"], ["above_resistance", "Above 30wk Resistance"], ["weeks_available", "Weeks of History"], ["base_score", "S1 Base Score (/4)"], ["base_textbook", "S1 Textbook Base"], ["base_decline_pct", "S1 Decline into Base %"], ["base_band_pct", "S1 Base Range %"], ["base_ma_drift_pct", "S1 60d MA Drift %"], ["adv_score", "S2 Advance Score (/4)"], ["adv_textbook", "S2 Textbook Advance"], ["adv_vol_mult", "S2 Volume Expansion (x)"], ["adv_higher_lows", "S2 Higher Lows"], ["adv_above_pct", "S2 % Weeks Above MA"], ["top_score", "S3 Top Score (/3)"], ["top_textbook", "S3 Textbook Top"], ["top_vol_mult", "S3 Volume (x)"], ["top_vola_ratio", "S3 Volatility (x)"], ["decl_score", "S4 Decline Score (/2)"], ["decl_confirmed", "S4 Decline Confirmed"], ["decl_vol_mult", "S4 Volume (x)"], ["decl_opt_volume_rising", "S4 Volume Rising (optional)"]]
      : [["roe", "ROE %"], ["roe_5y_min", "ROE 5Y Min %"],
      ["rev_growth", "Revenue Growth %"], ["operate_profit", "Operating Profit ¥"], ["mom_52w", "12M Return %"],
      ["inst_pct", "Institutional % (top-10)"], ["strategic_pct", "Strategic/Corp % (top-10)"], ["top3_conc", "Top-3 Conc %"],
      ["earngrowth_min_3y", "Net Profit Growth Min 3Y %"], ["earngrowth_min_4y", "Net Profit Growth Min 4Y %"],
      ["earngrowth_min_q4", "Net Profit Growth Min 4 Reports %"], ["earngrowth_min_q8", "Net Profit Growth Min 8 Reports %"],
      ["report_coverage", "Sell-Side Reports (180d)"], ["report_inst_count", "Brokerages Covering (180d)"],
      ["report_rating_bucket", "Latest Rating"],
      ["accel_proxy", "MAGNET ACCEL proxy (pp)"], ["accel_pctile", "MAGNET ACCEL pctile"],
      ["garp_ey", "MAGNET GARP proxy (EY %)"], ["garp_pctile", "MAGNET GARP pctile"],
      ["timing_proxy", "MAGNET TIMING proxy (%)"], ["timing_pctile", "MAGNET TIMING pctile"],
      ["turnaround_accel", "MAGNET Turnaround-then-Accel"], ["turnaround_accel_q", "MAGNET Turnaround-then-Accel (Q)"], ["revenue_accel", "MAGNET Revenue Accel"], ["revenue_accel_q", "MAGNET Revenue Accel (Q)"], ["margin_turnaround_accel", "MAGNET Margin Turnaround"], ["margin_turnaround_accel_q", "MAGNET Margin Turnaround (Q)"], ["profit_accel", "MAGNET Profit Accel"], ["profit_accel_q", "MAGNET Profit Accel (Q)"], ["margin_accel", "MAGNET Margin Accel"], ["margin_accel_q", "MAGNET Margin Accel (Q)"], ["stage", "Weinstein Stage"], ["stage_label", "Stage (label)"], ["sector_name", "Industry Group"], ["sector_stage", "Group Stage"], ["sector_rs", "Group RS"], ["sector_rs_rank", "Group RS Rank"], ["sector_pct_stage2", "Group % in Stage 2"], ["stage_ema", "Stage (EMA basis)"], ["rs_pct", "RS Percentile (IBD)"], ["rs_pct_jt", "RS Percentile (12-1)"], ["mansfield_rs", "Mansfield RS"], ["vol_ratio", "Volume Ratio (x avg)"], ["price_vs_sma30", "% vs 30wk SMA"], ["price_vs_ema30", "% vs 30wk EMA"], ["sma30_slope", "30wk SMA slope % (13wk)"], ["above_resistance", "Above 30wk Resistance"], ["weeks_available", "Weeks of History"], ["base_score", "S1 Base Score (/4)"], ["base_textbook", "S1 Textbook Base"], ["base_decline_pct", "S1 Decline into Base %"], ["base_band_pct", "S1 Base Range %"], ["base_ma_drift_pct", "S1 60d MA Drift %"], ["adv_score", "S2 Advance Score (/4)"], ["adv_textbook", "S2 Textbook Advance"], ["adv_vol_mult", "S2 Volume Expansion (x)"], ["adv_higher_lows", "S2 Higher Lows"], ["adv_above_pct", "S2 % Weeks Above MA"], ["top_score", "S3 Top Score (/3)"], ["top_textbook", "S3 Textbook Top"], ["top_vol_mult", "S3 Volume (x)"], ["top_vola_ratio", "S3 Volatility (x)"], ["decl_score", "S4 Decline Score (/2)"], ["decl_confirmed", "S4 Decline Confirmed"], ["decl_vol_mult", "S4 Volume (x)"], ["decl_opt_volume_rising", "S4 Volume Rising (optional)"]]),
    ["magic_rank", "Magic Rank"], ["f_score", "F-Score"], ["earnings_yield", "Earnings Yield %"],
    ["roc", "ROC %"], ["real_growth_3y", "Real 3Y Growth %"], ["graham_mos", "Graham MoS %"],
    ...(isUS ? [
      ["dcf_iv", "DCF Intrinsic"], ["dcf_mos", "DCF MoS %"], ["beat_rate", "Earnings Beat Rate %"],
      ["est_rev", "Est Revision %"], ["target_upside", "Analyst Upside %"],
      ["rd_intensity", "R&D Intensity %"], ["net_buyback", "Net Buyback %"],
      ["sbc_rev", "SBC/Rev %"], ["insider_net", "Insider Net Buy %"],
    ] : []),
    ["roic", "ROIC %"], ["gp_assets", "GP/Assets %"], ["accruals", "Accruals %"], ["gross_margin", "Gross Margin %"],
  ];
  const lines = [cols.map(([, label]) => _csvCell(label)).join(",")];
  rows.forEach(r => {
    const base = _filterRowData[r.symbol] || r;
    const sc = _filterScoreData[r.symbol] || {};
    lines.push(cols.map(([key]) => {
      // The numeric P/E column stays numeric (blank when undefined) so
      // spreadsheet formulas keep working; the REASON goes in its own column.
      if (key === "_pe_status") {
        const v = base.pe ?? sc.pe;
        if (v != null && v !== "") return _csvCell("");
        const eps = base.eps ?? sc.eps;
        return _csvCell(eps != null && eps <= 0 ? "Loss (negative EPS)" : "No data");
      }
      return _csvCell(base[key] ?? sc[key]);
    }).join(","));
  });
  const blob = new Blob(["﻿" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${market === "cn" ? "ashare" : "us"}_filter_${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

// stashes + state for client-side column sorting
const _filterScoreData = {};        // ticker → computed scores (magic_rank, f_score, …)
let _filterRowData = {};            // ticker → base row (market_cap, pe, pb, div_yield, impl_growth)
let _filterSortState = { key: null, dir: null };
// columns where "lower is better" → first click sorts ascending; others descending
const _FILTER_SORT_ASC_FIRST = new Set(["pe", "pb", "ps", "price", "magic_rank"]);

async function scoreShortlist(market, rows) {
  const btn = document.getElementById("filterScoreBtn");
  const statusEl = document.getElementById("filterScoreStatus");
  const tickers = rows.map(r => r.symbol);
  btn.disabled = true; btn.textContent = "Scoring…";
  statusEl.textContent = `Computing fundamental scores for ${tickers.length} stocks… (per-stock financials, ~${market === "cn" ? "40" : "30"}s)`;
  try {
    const data = await fetchJson(`/api/filter_score?market=${market}&tickers=${encodeURIComponent(tickers.join(","))}`);
    const sc = data.scores || {};
    const cur = market === "cn" ? "¥" : "$";
    const fmt = {
      dcf_iv: (v) => v == null ? "—" : `${cur}${v}`,
      dcf_mos: (v) => v == null ? "—" : `${v > 0 ? "+" : ""}${v}%`,
      beat_rate: (v) => v == null ? "—" : `${v}%`,
      est_rev: (v) => v == null ? "—" : `${v > 0 ? "+" : ""}${v}%`,
      target_upside: (v) => v == null ? "—" : `${v > 0 ? "+" : ""}${v}%`,
      rd_intensity: (v) => v == null ? "—" : `${v}%`,
      net_buyback: (v) => v == null ? "—" : `${v > 0 ? "+" : ""}${v}%`,
      sbc_rev: (v) => v == null ? "—" : `${v}%`,
      insider_net: (v) => v == null ? "—" : `${v > 0 ? "+" : ""}${v}%`,
      magic_rank: (v) => v == null ? "—" : `#${v}`,
      f_score: (v) => v == null ? "—" : `${v}/9`,
      earnings_yield: (v) => v == null ? "—" : `${v}%`,
      roc: (v) => v == null ? "—" : `${v}%`,
      real_growth_3y: (v) => v == null ? "—" : `${v > 0 ? "+" : ""}${v}%`,
      graham_mos: (v) => v == null ? "—" : `${v > 0 ? "+" : ""}${v}%`,
      roic: (v) => v == null ? "—" : `${v}%`,
      gp_assets: (v) => v == null ? "—" : `${v}%`,
      accruals: (v) => v == null ? "—" : `${v > 0 ? "+" : ""}${v}%`,
      gross_margin: (v) => v == null ? "—" : `${v}%`,
    };
    let filled = 0;
    for (const [sym, info] of Object.entries(sc)) {
      _filterScoreData[sym] = info;
      if (Object.keys(info).length) filled++;
      document.querySelectorAll(`.filter-td-score[data-s="${CSS.escape(sym)}"]`).forEach(cell => {
        const k = cell.dataset.k;
        const v = info[k];
        cell.textContent = fmt[k](v);
        cell.classList.remove("vg-pos", "vg-neg", "vg-good", "vg-bad", "filter-real-na");
        if (v == null) { cell.classList.add("filter-real-na"); return; }
        let klass = "";
        if (k === "f_score") klass = v >= 7 ? "vg-good" : v <= 3 ? "vg-bad" : "";
        else if (k === "magic_rank") klass = v <= 10 ? "vg-good" : "";
        else if (k === "graham_mos" || k === "dcf_mos") klass = v >= 20 ? "vg-good" : v < 0 ? "vg-bad" : "";
        else if (k === "roic") klass = v >= 15 ? "vg-good" : v < 0 ? "vg-bad" : "";
        else if (k === "gp_assets") klass = v >= 25 ? "vg-good" : "";
        else if (k === "gross_margin") klass = v >= 40 ? "vg-good" : "";
        else if (k === "accruals") klass = v <= 0 ? "vg-pos" : v > 10 ? "vg-neg" : "";   // lower = better
        else if (k === "real_growth_3y" || k === "earnings_yield" || k === "roc") klass = v >= 0 ? "vg-pos" : "vg-neg";
        else if (k === "beat_rate") {
          klass = v >= 75 ? "vg-good" : v < 50 ? "vg-bad" : "";
          cell.title = `${info.beat_count ?? "?"}/${info.beat_n ?? "?"} of last quarters beat consensus EPS` +
                       (info.next_earnings ? ` · next earnings ${info.next_earnings}` : "");
        }
        else if (k === "est_rev") {
          klass = v > 0 ? "vg-pos" : "vg-neg";
          if (info.fwd_growth_a != null) cell.title = `Analyst next-year EPS growth estimate: ${info.fwd_growth_a > 0 ? "+" : ""}${info.fwd_growth_a}%`;
        }
        else if (k === "target_upside") {
          klass = v >= 15 ? "vg-good" : v < 0 ? "vg-bad" : "";
          const bits = [];
          if (info.n_analysts != null) bits.push(`${info.n_analysts} analysts`);
          if (info.rec_key) bits.push(`rating: ${info.rec_key}`);
          if (info.insider_pct != null) bits.push(`insiders ${info.insider_pct}%`);
          if (info.short_pct != null) bits.push(`short ${info.short_pct}%`);
          if (bits.length) cell.title = bits.join(" · ");
        }
        else if (k === "net_buyback") klass = v > 0 ? "vg-pos" : v < 0 ? "vg-neg" : "";
        else if (k === "sbc_rev") {          // lower is better
          klass = v >= 10 ? "vg-bad" : v <= 2 ? "vg-good" : "";
          if (info.sbc_fcf != null) cell.title = `SBC is ${info.sbc_fcf}% of free cash flow — that share of FCF isn't really "free"`;
        }
        else if (k === "insider_net") klass = v > 0.5 ? "vg-good" : v < -5 ? "vg-bad" : "";
        if (klass) cell.classList.add(klass);
      });
    }
    statusEl.textContent = `Done — scored ${filled}/${tickers.length}. Click a score header (Magic / F / EarnYld …) to rank.`;
    _applyMosFilter();   // re-apply any active margin-of-safety filter now that values exist
  } catch (err) {
    statusEl.textContent = `⚠ ${err.message}`;
  } finally {
    btn.disabled = false; btn.textContent = `⚡ Score shortlist (top ${rows.length})`;
  }
}

function _sortFilterBy(key) {
  const tbody = document.querySelector(".filter-table tbody");
  if (!tbody) return;
  // toggle direction if same column, else pick a sensible default
  if (_filterSortState.key === key) {
    _filterSortState.dir = _filterSortState.dir === "asc" ? "desc" : "asc";
  } else {
    _filterSortState.key = key;
    _filterSortState.dir = _FILTER_SORT_ASC_FIRST.has(key) ? "asc" : "desc";
  }
  const dir = _filterSortState.dir;
  const valOf = (tr) => {
    const t = tr.dataset.ticker;
    const base = _filterRowData[t]?.[key];
    return base != null ? base : _filterScoreData[t]?.[key];
  };
  const rows = [...tbody.querySelectorAll(".filter-row")];
  rows.sort((a, b) => {
    const va = valOf(a), vb = valOf(b);
    const na = va == null, nb = vb == null;
    if (na && nb) return 0;
    if (na) return 1;            // missing values always sink to the bottom
    if (nb) return -1;
    return dir === "asc" ? va - vb : vb - va;
  });
  rows.forEach(r => tbody.appendChild(r));
  document.querySelectorAll(".filter-th-sort").forEach(th => {
    const arrow = th.querySelector(".filter-arrow");
    if (th.dataset.sort === key) {
      th.classList.add("filter-th-active");
      if (arrow) arrow.textContent = dir === "asc" ? "▲" : "▼";
    } else {
      th.classList.remove("filter-th-active");
      if (arrow) arrow.textContent = "⇅";
    }
  });
}

function _ago(ts) {
  if (!ts) return "";
  const secs = Date.now() / 1000 - ts;
  if (secs < 90) return "just now";
  if (secs < 3600) return `${Math.round(secs/60)}m ago`;
  return `${Math.round(secs/3600)}h ago`;
}

function openFullScreen(ticker, market) {
  const targetPage = market === "cn" ? "ashare" : "stocks";
  // switch page nav active state
  document.querySelectorAll(".page-nav-btn").forEach(b =>
    b.classList.toggle("active", b.dataset.page === targetPage && b.dataset.market === market));
  switchPage(targetPage);
  // set the ticker input and run the screen
  setTimeout(() => {
    if (market === "cn") {
      const inp = document.getElementById("ashareTickerInput");
      if (inp) { inp.value = ticker; runAshareScreen(); }
    } else {
      const inp = document.getElementById("scTickerInput");
      if (inp) { inp.value = ticker; runScreen(); }
    }
  }, 60);
}

// ══ Quant Rank page (composite z-score ranking + backtest) ══════════════════

let _quantBuiltMarket = null;
let _quantResults = [];          // current ranked rows

function getQuantCanvas() {
  let c = document.getElementById("quantCanvas");
  if (!c) { c = document.createElement("div"); c.id = "quantCanvas"; document.querySelector("main").appendChild(c); }
  return c;
}
function hideQuant() {
  const c = document.getElementById("quantCanvas");
  if (c) c.style.display = "none";
  els.workspace.style.display = "";
}

function loadQuantPage() {
  const market = state.market;
  const canvas = getQuantCanvas();
  canvas.style.display = "";
  els.workspace.style.display = "none";
  if (_quantBuiltMarket === market && canvas.querySelector(".quant-page")) return;  // preserve state
  _quantBuiltMarket = market;
  const cur = market === "cn" ? "¥" : "$";
  const uni = market === "cn" ? "main-board A-shares (~4,400, ≥¥3B)" : "US common stocks (~3,700, ≥$300M)";
  canvas.innerHTML = `
  <div class="quant-page">
    <div class="filter-head">
      <div>
        <div class="filter-title">Quant Rank <span class="filter-market-tag">${market === "cn" ? "🇨🇳 A-Share" : "🇺🇸 US"}</span></div>
        <div class="filter-sub">Cross-sectional composite z-score across ${uni}. Each factor is rank-normalized; the composite is a weighted blend.</div>
      </div>
    </div>

    <div class="quant-weights">
      <div class="quant-weights-title">Factor weights</div>
      <div class="quant-weight-row">
        <label>Value <span class="quant-w-hint">cheap (E/P, B/P, S/P)</span><input type="number" id="qw_value" min="0" max="1" step="0.1" value="0.5"></label>
        <label>Growth <span class="quant-w-hint">fwd EPS growth${market === "cn" ? " · US-only" : ""}</span><input type="number" id="qw_growth" min="0" max="1" step="0.1" value="0"></label>
        <label>Momentum <span class="quant-w-hint">12-mo return</span><input type="number" id="qw_momentum" min="0" max="1" step="0.1" value="0.3"></label>
        <label>Size <span class="quant-w-hint">small-cap tilt</span><input type="number" id="qw_size" min="0" max="1" step="0.1" value="0.2"></label>
        <label>Show top<input type="number" id="qw_limit" min="10" max="300" value="50"></label>
      </div>
      <div class="quant-preset-row">
        <button class="filter-preset-btn" data-qp="value">Value tilt</button>
        <button class="filter-preset-btn" data-qp="garp" title="Growth At a Reasonable Price (Lynch): value + growth blend">GARP / Lynch</button>
        <button class="filter-preset-btn" data-qp="balanced">Balanced</button>
        <button class="filter-preset-btn" data-qp="momentum">Momentum tilt</button>
        <button id="quantRunBtn" class="primary-btn">Rank universe</button>
        <span id="quantStatus" class="filter-meta-status"></span>
      </div>
      <div class="vg-note">The base composite is a <strong>candidate generator</strong> — it can surface speculative names. Use <em>“Add quality”</em> to fold in F-Score / Magic-Formula and demote weak balance sheets, and <em>“Backtest top N”</em> to see how the basket would have performed.</div>
    </div>

    <div id="quantResults" class="filter-results"></div>
    <div id="quantBacktest" class="quant-backtest"></div>
  </div>`;

  canvas.querySelector("#quantRunBtn").addEventListener("click", runQuantRank);
  canvas.querySelectorAll(".filter-preset-btn[data-qp]").forEach(b =>
    b.addEventListener("click", () => _applyQuantPreset(b.dataset.qp)));
}

function _applyQuantPreset(key) {
  // [value, growth, momentum, size]
  const W = {
    value:    [0.6, 0.0, 0.2, 0.2],
    garp:     [0.4, 0.4, 0.0, 0.2],
    balanced: [0.4, 0.0, 0.4, 0.2],
    momentum: [0.3, 0.0, 0.5, 0.2],
  }[key] || [0.5, 0.0, 0.3, 0.2];
  document.getElementById("qw_value").value = W[0];
  document.getElementById("qw_growth").value = W[1];
  document.getElementById("qw_momentum").value = W[2];
  document.getElementById("qw_size").value = W[3];
  if (key === "garp" && state.market === "cn") {
    document.getElementById("quantStatus").textContent = "Note: A-share has no bulk forward-growth — GARP uses value+size here; get real growth via ‘Add quality’.";
  }
  runQuantRank();
}

async function runQuantRank() {
  const market = state.market;
  const btn = document.getElementById("quantRunBtn");
  const statusEl = document.getElementById("quantStatus");
  const resEl = document.getElementById("quantResults");
  document.getElementById("quantBacktest").innerHTML = "";
  const v = document.getElementById("qw_value").value || "0";
  const g = document.getElementById("qw_growth").value || "0";
  const m = document.getElementById("qw_momentum").value || "0";
  const s = document.getElementById("qw_size").value || "0";
  const lim = document.getElementById("qw_limit").value || "50";
  btn.disabled = true; btn.textContent = "Ranking…";
  statusEl.textContent = "";
  try {
    const d = await fetchJson(`/api/quant_rank?market=${market}&value=${v}&growth=${g}&momentum=${m}&size=${s}&limit=${lim}`);
    if (d.status === "building") {
      resEl.innerHTML = `<div class="filter-building"><div class="filter-building-msg">Building US market snapshot… ${d.total ? Math.round((d.progress||0)/d.total*100) : 0}% <span class="filter-building-sub">(one-time, ~1 min)</span></div></div>`;
      setTimeout(runQuantRank, 3000); return;
    }
    if (d.status === "error") { resEl.innerHTML = `<div class="sc-error">⚠ ${escapeHtml(d.error)}</div>`; return; }
    _quantResults = d.results || [];
    statusEl.textContent = `Ranked ${d.count} of ${d.universe} · weights V${d.weights.value}/G${d.weights.growth}/M${d.weights.momentum}/S${d.weights.size}`;
    renderQuantResults(d, market);
  } catch (err) {
    resEl.innerHTML = `<div class="sc-error">⚠ ${escapeHtml(err.message)}</div>`;
  } finally {
    btn.disabled = false; btn.textContent = "Rank universe";
  }
}

function renderQuantResults(d, market) {
  const resEl = document.getElementById("quantResults");
  const cur = d.currency || (market === "cn" ? "¥" : "$");
  const rows = d.results || [];
  const isUS = market !== "cn";
  const mcap = (v) => v == null ? "—" : v >= 1e12 ? `${cur}${(v/1e12).toFixed(2)}T` : v >= 1e9 ? `${cur}${(v/1e9).toFixed(1)}B` : `${cur}${(v/1e6).toFixed(0)}M`;
  const n = (v, d2=1) => v == null ? "—" : (+v).toFixed(d2);
  // factor sub-score as a small signed bar (−1.7..+1.7)
  const fbar = (z) => {
    if (z == null) return `<span class="qz-na">—</span>`;
    const pct = Math.min(100, Math.abs(z) / 1.75 * 50);
    const cls = z >= 0 ? "qz-pos" : "qz-neg";
    const side = z >= 0 ? "left:50%" : `right:50%`;
    return `<span class="qz-cell"><span class="qz-bar ${cls}" style="${side};width:${pct.toFixed(0)}%"></span><span class="qz-val">${z >= 0 ? "+" : ""}${z}</span></span>`;
  };
  const body = rows.map(r => `
    <tr class="filter-row" data-ticker="${escapeHtml(r.symbol)}">
      <td class="pos-td"><button class="pos-add" data-t="${escapeHtml(r.symbol)}" title="Add to Pipeline as candidate" type="button">＋</button></td>
      <td class="quant-td-rank">${r.rank}</td>
      <td class="filter-td-sym">${escapeHtml(r.symbol)}</td>
      <td class="filter-td-name">${escapeHtml(r.name || "")}</td>
      <td class="filter-td-ind">${escapeHtml(r.sector || "")}</td>
      <td class="filter-td-num quant-td-comp">${r.composite >= 0 ? "+" : ""}${r.composite}</td>
      <td class="quant-td-f">${fbar(r.f_value)}</td>
      <td class="quant-td-f">${fbar(r.f_growth)}</td>
      <td class="quant-td-f">${fbar(r.f_momentum)}</td>
      <td class="quant-td-f">${fbar(r.f_size)}</td>
      ${(() => { const c = peCell(r); return `<td class="filter-td-num ${c.cls}" title="${escapeHtml(c.title)}">${c.text}</td>`; })()}
      <td class="filter-td-num">${n(r.pb, 2)}</td>
      <td class="filter-td-num">${r.mom_52w == null ? "—" : (r.mom_52w > 0 ? "+" : "") + r.mom_52w + "%"}</td>
      <td class="filter-td-num filter-td-score qcol" data-k="f_score" data-s="${escapeHtml(r.symbol)}">·</td>
      <td class="filter-td-num filter-td-score qcol" data-k="roic" data-s="${escapeHtml(r.symbol)}">·</td>
      <td class="filter-td-num filter-td-score qcol" data-k="gp_assets" data-s="${escapeHtml(r.symbol)}">·</td>
      <td class="filter-td-num filter-td-score qcol" data-k="accruals" data-s="${escapeHtml(r.symbol)}">·</td>
      <td class="filter-td-num filter-td-score qcol" data-k="gross_margin" data-s="${escapeHtml(r.symbol)}">·</td>
      ${isUS ? `<td class="filter-td-num filter-td-score qcol" data-k="beat_rate" data-s="${escapeHtml(r.symbol)}">·</td>
      <td class="filter-td-num filter-td-score qcol" data-k="est_rev" data-s="${escapeHtml(r.symbol)}">·</td>
      <td class="filter-td-num filter-td-score qcol" data-k="target_upside" data-s="${escapeHtml(r.symbol)}">·</td>
      <td class="filter-td-num filter-td-score qcol" data-k="rd_intensity" data-s="${escapeHtml(r.symbol)}">·</td>
      <td class="filter-td-num filter-td-score qcol" data-k="net_buyback" data-s="${escapeHtml(r.symbol)}">·</td>` : ""}
    </tr>`).join("");
  const nEnrich = Math.min(rows.length, market === "cn" ? 25 : 40);
  resEl.innerHTML = `
    <div class="filter-toolbar">
      <button id="quantEnrichBtn" class="secondary-btn filter-confirm-btn">✓ Add quality (top ${nEnrich})</button>
      <button id="quantBacktestBtn" class="secondary-btn filter-confirm-btn">📈 Backtest top <input type="number" id="qbt_n" value="10" min="2" max="30" class="quant-bt-n"> from <input type="text" id="qbt_start" value="2021-01-01" class="quant-bt-date"></button>
      <span id="quantEnrichStatus" class="filter-confirm-status"></span>
    </div>
    <div class="filter-table-wrap">
    <table class="filter-table quant-table">
      <thead><tr>
        <th class="pos-th"></th>
        <th>#</th><th>Ticker</th><th>Name</th><th>Sector</th>
        <th title="Composite z-score (weighted blend of the factors below)">Composite</th>
        <th title="Value factor (cheapness) rank-score">Value</th>
        <th title="Growth factor (implied fwd EPS growth) rank-score — US only">Growth</th>
        <th title="Momentum factor (12-mo return) rank-score">Mom</th>
        <th title="Size factor (small-cap tilt) rank-score">Size</th>
        <th>P/E</th><th>P/B</th><th>12M</th>
        <th class="quant-th-sort qcol" data-qsort="f_score" title="Piotroski F-Score 0–9">F ▾</th>
        <th class="quant-th-sort qcol" data-qsort="roic" title="Return on Invested Capital = NOPAT ÷ (equity+debt−cash)">ROIC ▾</th>
        <th class="quant-th-sort qcol" data-qsort="gp_assets" title="Gross-profitability (Novy-Marx) = gross profit ÷ assets">GP/A ▾</th>
        <th class="quant-th-sort qcol" data-qsort="accruals" title="Accruals (Sloan) = (NI − OCF) ÷ assets — lower is better">Accr ▾</th>
        <th class="quant-th-sort qcol" data-qsort="gross_margin" title="Gross margin = gross profit ÷ revenue">GM ▾</th>
        ${isUS ? `<th class="quant-th-sort qcol" data-qsort="beat_rate" title="Earnings beat rate: % of last ≤8 quarters where reported EPS beat consensus. Hover a cell for the count &amp; next earnings date.">Beat ▾</th>
        <th class="quant-th-sort qcol" data-qsort="est_rev" title="Estimate momentum: % change in current-year consensus EPS over last 90 days. +ve = analysts raising. Hover for analyst forward-growth estimate.">Est↑ ▾</th>
        <th class="quant-th-sort qcol" data-qsort="target_upside" title="Analyst mean target vs price. Hover for # analysts, rating, insider %, short %.">Tgt↑ ▾</th>
        <th class="quant-th-sort qcol" data-qsort="rd_intensity" title="R&amp;D ÷ revenue (— when no R&amp;D line).">R&amp;D ▾</th>
        <th class="quant-th-sort qcol" data-qsort="net_buyback" title="YoY share-count change, sign-flipped: +ve = buybacks, −ve = dilution.">Buyback ▾</th>` : ""}
      </tr></thead>
      <tbody>${body}</tbody>
    </table>
    </div>
    <div class="filter-hint">Click a row for the full deep-dive. Factor bars show each stock's rank-score (−1.7 … +1.7). The <strong>quality columns</strong> (F · ROIC · GP/A · Accr · GM) appear after <strong>Add quality</strong> — click any of their headers to rank by it. <details class="filter-defs" style="display:inline"><summary>ⓘ definitions</summary><div class="filter-defs-body"><p><strong>F</strong> Piotroski 0–9 financial-strength checklist.</p><p><strong>ROIC</strong> NOPAT ÷ invested capital — returns above cost of capital.</p><p><strong>GP/A</strong> gross profit ÷ assets (Novy-Marx) — cleanest "good business" signal.</p><p><strong>Accr</strong> (NI − OCF) ÷ assets (Sloan) — <em>lower/negative is better</em>, earnings cash-backed.</p><p><strong>GM</strong> gross margin — pricing power.</p><p><strong>Beat</strong> (US) % of last ≤8 quarters beating consensus EPS — execution/quality signal; hover for count &amp; next earnings date.</p><p><strong>Est↑</strong> (US) 90-day change in current-year consensus EPS — +ve = analysts raising; hover for forward-growth estimate. <strong>Tgt↑</strong> analyst target upside (hover: analysts/rating/insider/short). <strong>R&amp;D</strong> R&amp;D÷revenue. <strong>Buyback</strong> YoY share-count change flipped (+ buyback / − dilution).</p></div></details></div>`;
  resEl.querySelectorAll(".filter-row").forEach(tr =>
    tr.addEventListener("click", (e) => { if (!e.target.closest(".pos-add")) openFullScreen(tr.dataset.ticker, market); }));
  resEl.querySelectorAll(".pos-add").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const t = btn.dataset.t;
      const row = rows.find(r => r.symbol === t) || {};
      _addToPipeline(market, t, { name: row.name, source: "quant",
        snapshot: { ...row, ...(_quantScoreData[t] || {}) } }, btn);
    });
  });
  document.getElementById("quantEnrichBtn").addEventListener("click", () => enrichQuant(market, rows.slice(0, nEnrich)));
  document.getElementById("quantBacktestBtn").addEventListener("click", (e) => {
    if (e.target.tagName === "INPUT") return;   // don't trigger when editing the inline inputs
    runQuantBacktest(market);
  });
}

async function enrichQuant(market, rows) {
  const btn = document.getElementById("quantEnrichBtn");
  const statusEl = document.getElementById("quantEnrichStatus");
  const tickers = rows.map(r => r.symbol);
  btn.disabled = true; btn.textContent = "Scoring…";
  statusEl.textContent = `Computing quality (F-Score, Magic Formula) for ${tickers.length}…`;
  try {
    const data = await fetchJson(`/api/filter_score?market=${market}&tickers=${encodeURIComponent(tickers.join(","))}`);
    const sc = data.scores || {};
    // fill the separate quality columns (no merge) — same metrics as the Filter
    const fmt = {
      f_score: v => v == null ? "—" : `${v}/9`,
      roic: v => v == null ? "—" : `${v}%`,
      gp_assets: v => v == null ? "—" : `${v}%`,
      accruals: v => v == null ? "—" : `${v > 0 ? "+" : ""}${v}%`,
      gross_margin: v => v == null ? "—" : `${v}%`,
      beat_rate: v => v == null ? "—" : `${v}%`,
      est_rev: v => v == null ? "—" : `${v > 0 ? "+" : ""}${v}%`,
      target_upside: v => v == null ? "—" : `${v > 0 ? "+" : ""}${v}%`,
      rd_intensity: v => v == null ? "—" : `${v}%`,
      net_buyback: v => v == null ? "—" : `${v > 0 ? "+" : ""}${v}%`,
    };
    let filled = 0;
    for (const [sym, info] of Object.entries(sc)) {
      _quantScoreData[sym] = info;
      if (Object.keys(info).length) filled++;
      document.querySelectorAll(`.quant-table .filter-td-score[data-s="${CSS.escape(sym)}"]`).forEach(cell => {
        const k = cell.dataset.k, v = info[k];
        cell.textContent = fmt[k] ? fmt[k](v) : (v == null ? "—" : v);
        cell.classList.remove("vg-pos", "vg-neg", "vg-good", "vg-bad", "filter-real-na");
        if (v == null) { cell.classList.add("filter-real-na"); return; }
        let klass = "";
        if (k === "f_score") klass = v >= 7 ? "vg-good" : v <= 3 ? "vg-bad" : "";
        else if (k === "roic") klass = v >= 15 ? "vg-good" : v < 0 ? "vg-bad" : "";
        else if (k === "gp_assets") klass = v >= 25 ? "vg-good" : "";
        else if (k === "gross_margin") klass = v >= 40 ? "vg-good" : "";
        else if (k === "accruals") klass = v <= 0 ? "vg-pos" : v > 10 ? "vg-neg" : "";
        else if (k === "beat_rate") {
          klass = v >= 75 ? "vg-good" : v < 50 ? "vg-bad" : "";
          cell.title = `${info.beat_count ?? "?"}/${info.beat_n ?? "?"} of last quarters beat consensus EPS` +
                       (info.next_earnings ? ` · next earnings ${info.next_earnings}` : "");
        }
        else if (k === "est_rev") {
          klass = v > 0 ? "vg-pos" : "vg-neg";
          if (info.fwd_growth_a != null) cell.title = `Analyst next-year EPS growth estimate: ${info.fwd_growth_a > 0 ? "+" : ""}${info.fwd_growth_a}%`;
        }
        else if (k === "target_upside") {
          klass = v >= 15 ? "vg-good" : v < 0 ? "vg-bad" : "";
          const bits = [];
          if (info.n_analysts != null) bits.push(`${info.n_analysts} analysts`);
          if (info.rec_key) bits.push(`rating: ${info.rec_key}`);
          if (info.insider_pct != null) bits.push(`insiders ${info.insider_pct}%`);
          if (info.short_pct != null) bits.push(`short ${info.short_pct}%`);
          if (bits.length) cell.title = bits.join(" · ");
        }
        else if (k === "net_buyback") klass = v > 0 ? "vg-pos" : v < 0 ? "vg-neg" : "";
        if (klass) cell.classList.add(klass);
      });
    }
    document.querySelector(".quant-table")?.classList.add("show-qual");   // reveal folded columns
    document.querySelectorAll(".quant-th-sort").forEach(th => { th.onclick = () => _sortQuantBy(th.dataset.qsort); });
    statusEl.textContent = `Done — ${filled} scored. Click a quality header (F / ROIC / GP/A …) to rank by it.`;
  } catch (err) {
    statusEl.textContent = `⚠ ${err.message}`;
  } finally {
    btn.disabled = false; btn.textContent = "✓ Add quality (top " + rows.length + ")";
  }
}

let _quantScoreData = {};
let _quantSort = { key: null, dir: null };
function _sortQuantBy(key) {
  const tbody = document.querySelector(".quant-table tbody");
  if (!tbody) return;
  const ascFirst = key === "accruals";     // lower is better → ascending on first click
  if (_quantSort.key === key) _quantSort.dir = _quantSort.dir === "asc" ? "desc" : "asc";
  else _quantSort.dir = ascFirst ? "asc" : "desc";
  _quantSort.key = key;
  const dir = _quantSort.dir;
  [...tbody.querySelectorAll(".filter-row")].sort((a, b) => {
    const va = _quantScoreData[a.dataset.ticker]?.[key], vb = _quantScoreData[b.dataset.ticker]?.[key];
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    return dir === "asc" ? va - vb : vb - va;
  }).forEach(r => tbody.appendChild(r));
  document.querySelectorAll(".quant-th-sort").forEach(th => th.classList.toggle("filter-th-active", th.dataset.qsort === key));
}

async function runQuantBacktest(market) {
  const nEl = document.getElementById("qbt_n"), startEl = document.getElementById("qbt_start");
  const nTop = Math.max(2, Math.min(30, parseInt(nEl.value || "10")));
  const start = (startEl.value || "2021-01-01").trim();
  const tickers = _quantResults.slice(0, nTop).map(r => r.symbol);
  const btEl = document.getElementById("quantBacktest");
  btEl.innerHTML = `<div class="sc-loading">Backtesting top ${nTop} equal-weight from ${start}… (historical prices)</div>`;
  try {
    const d = await fetchJson(`/api/backtest?market=${market}&tickers=${encodeURIComponent(tickers.join(","))}&start=${encodeURIComponent(start)}`);
    if (d.status !== "ready") { btEl.innerHTML = `<div class="sc-error">⚠ ${escapeHtml(d.error || "Backtest failed")}</div>`; return; }
    renderBacktest(d, nTop);
  } catch (err) {
    btEl.innerHTML = `<div class="sc-error">⚠ ${escapeHtml(err.message)}</div>`;
  }
}

function renderBacktest(d, nTop) {
  const btEl = document.getElementById("quantBacktest");
  const ps = d.portfolio_stats, bs = d.benchmark_stats;
  const W = 720, H = 240, padL = 44, padR = 12, padT = 14, padB = 24;
  const all = [...d.portfolio, ...d.benchmark];
  const mn = Math.min(...all), mx = Math.max(...all), rng = (mx - mn) || 1;
  const N = d.dates.length;
  const x = (i) => padL + i * (W - padL - padR) / Math.max(1, N - 1);
  const y = (v) => H - padB - (v - mn) / rng * (H - padT - padB);
  const line = (arr, col) => `<polyline points="${arr.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ")}" fill="none" stroke="${col}" stroke-width="1.8"/>`;
  const yTicks = [mn, (mn + mx) / 2, mx].map(v => `<text x="4" y="${(y(v)+3).toFixed(1)}" font-size="9" fill="var(--muted)">${((v-1)*100).toFixed(0)}%</text><line x1="${padL}" y1="${y(v).toFixed(1)}" x2="${W-padR}" y2="${y(v).toFixed(1)}" stroke="var(--panel-2)" stroke-width="0.5"/>`).join("");
  const xTicks = [0, Math.floor(N/2), N-1].map(i => `<text x="${x(i).toFixed(1)}" y="${H-6}" font-size="9" fill="var(--muted)" text-anchor="${i===0?'start':i===N-1?'end':'middle'}">${d.dates[i]}</text>`).join("");
  const statRow = (label, s, cls) => `
    <div class="qbt-stat-row ${cls}">
      <span class="qbt-stat-name">${label}</span>
      <span>tot <strong>${s.total_return >= 0 ? "+" : ""}${s.total_return}%</strong></span>
      <span>CAGR <strong>${s.cagr}%</strong></span>
      <span>vol ${s.vol}%</span>
      <span>maxDD <strong class="vg-neg">${s.max_drawdown}%</strong></span>
      <span>Sharpe <strong>${s.sharpe}</strong></span>
    </div>`;
  btEl.innerHTML = `
    <div class="sc-card vg-card">
      <div class="sc-card-header"><div class="sc-card-title">Backtest — top ${nTop}, equal-weight</div>
        <span class="fund-tag">${d.start} → ${d.end} · ${d.n_holdings} holdings</span></div>
      <svg viewBox="0 0 ${W} ${H}" class="qbt-chart">
        ${yTicks}${xTicks}
        ${line(d.benchmark, "#94a3b8")}
        ${line(d.portfolio, "#0d6b5e")}
      </svg>
      <div class="qbt-legend"><span class="qbt-leg"><i style="background:#0d6b5e"></i>Portfolio</span><span class="qbt-leg"><i style="background:#94a3b8"></i>${escapeHtml(d.benchmark_name)}</span></div>
      ${statRow("Portfolio", ps, "qbt-port")}
      ${statRow(d.benchmark_name, bs, "qbt-bench")}
      <div class="vg-note">Equal-weight buy-and-hold, dividends/splits adjusted. <strong>Caveat:</strong> the basket is today's ranking applied to the past — it uses current fundamentals, so treat this as “how these names performed,” not a clean walk-forward (which needs point-in-time data). No survivorship correction.</div>
    </div>`;
}

function renderEtfResult(d, container) {
  const vd = d.verdict || {};
  const checks = d.checks || [];

  const verdictMeta = {
    pass:     { icon: "✓", label: "Pass",         cls: "sc-v-pass"    },
    partial:  { icon: "~", label: "Partial",       cls: "sc-v-partial" },
    fail:     { icon: "✗", label: "Fail",          cls: "sc-v-fail"    },
    unknown:  { icon: "—", label: "Insufficient data", cls: "sc-v-na" },
  };
  function vBadge(key, label) {
    const m = verdictMeta[key] || verdictMeta.unknown;
    return `<span class="sc-verdict-badge ${m.cls}">${m.icon} ${label || m.label}</span>`;
  }

  // ── ETF Header ─────────────────────────────────────────────────────────────
  const header = `
  <div class="sc-header">
    <div class="sc-header-left">
      <div class="sc-ticker-name">
        <span class="sc-ticker">${escapeHtml(d.ticker)}</span>
        <span class="sc-name">${escapeHtml(d.name)}</span>
        <span class="sc-etf-badge">ETF</span>
      </div>
      <div class="sc-chips">
        <span class="sc-chip">${escapeHtml(d.category || "—")}</span>
        <span class="sc-chip sc-chip-dim">${escapeHtml(d.fund_family || "—")}</span>
        <span class="sc-chip sc-chip-dim">Since ${escapeHtml(d.inception_date || "—")}</span>
      </div>
      <p class="sc-desc">${escapeHtml(d.description || "")}</p>
    </div>
    <div class="sc-header-right">
      <div class="sc-price">$${d.price != null ? d.price.toFixed(2) : "—"}</div>
      <div class="sc-mcap">AUM ${escapeHtml(d.aum_fmt || "—")}</div>
      <div class="sc-gates-summary">
        ${vBadge(vd.overall || "unknown", `Screen: ${(verdictMeta[vd.overall] || verdictMeta.unknown).label}`)}
      </div>
    </div>
  </div>`;

  // ── 5-Dimension Scorecard ──────────────────────────────────────────────────
  const checkRows = checks.map(c => {
    const icon = c.pass === true ? "✓" : c.pass === false ? "✗" : "—";
    const cls  = c.pass === true ? "sc-pass" : c.pass === false ? "sc-fail" : "sc-na";
    const thr  = `${c.direction === "above" ? ">" : "<"} ${c.threshold}${c.unit}`;
    return `
    <tr class="${cls}">
      <td class="sc-row-icon">${icon}</td>
      <td class="sc-row-name">${escapeHtml(c.name)}${c.period ? `<span class="sc-row-period">${escapeHtml(c.period)}</span>` : ""}</td>
      <td class="sc-row-val">${escapeHtml(c.display)}</td>
      <td class="sc-row-thr">${escapeHtml(thr)}</td>
      <td class="sc-row-note">${escapeHtml(c.note)}${c.caveat ? `<span class="sc-row-caveat">⚠ ${escapeHtml(c.caveat)}</span>` : ""}</td>
    </tr>`;
  }).join("");

  const score = `${vd.passed ?? 0} / ${vd.scorable ?? "?"}`;
  const barPct = vd.scorable ? Math.round((vd.passed / vd.scorable) * 100) : 0;
  const scorecard = `
  <div class="sc-card">
    <div class="sc-card-header">
      <div class="sc-card-title">5-Dimension ETF Scorecard</div>
      ${vBadge(vd.overall || "unknown")}
      <div class="sc-score">${score} passed</div>
    </div>
    <p class="sc-card-sub">Screens across five dimensions: <strong>Cost</strong> (expense ratio — the one fee you always pay), <strong>Size</strong> (AUM — smaller funds risk closure or wide spreads), <strong>Liquidity</strong> (daily volume — affects your entry/exit cost), <strong>Age</strong> (track record — needs &gt;3 years of live history), and <strong>Diversification</strong> (top-10 concentration — avoids disguised single-stock risk).</p>
    <div class="sc-g1-bar-wrap">
      <div class="sc-g1-bar" style="width:${barPct}%"></div>
    </div>
    <table class="sc-table">
      <thead><tr><th></th><th>Dimension</th><th>Value</th><th>Threshold</th><th>Context</th></tr></thead>
      <tbody>${checkRows}</tbody>
    </table>
  </div>`;

  // ── Returns panel ──────────────────────────────────────────────────────────
  const retColor = r => {
    if (r == null) return "sc-v-na";
    if (r > 15) return "sc-v-pass";
    if (r > 7)  return "sc-v-pass";
    if (r > 0)  return "sc-v-partial";
    return "sc-v-fail";
  };
  const retCard = (label, val, unit="%" ) =>
    `<div class="sc-val-card ${retColor(val)}">
      <div class="sc-val-label">${label}</div>
      <div class="sc-val-value">${val != null ? val : "—"}<span class="sc-val-unit">${unit}</span></div>
    </div>`;

  const erClass = d.er_verdict === "excellent" ? "sc-v-pass" : d.er_verdict === "acceptable" ? "sc-v-partial" : "sc-v-fail";
  const returns = `
  <div class="sc-card">
    <div class="sc-card-header">
      <div class="sc-card-title">Performance & Cost</div>
    </div>
    <div class="sc-val-grid sc-etf-perf-grid">
      <div class="sc-val-card ${erClass}">
        <div class="sc-val-label">Expense Ratio</div>
        <div class="sc-val-value">${d.expense_ratio != null ? d.expense_ratio : "—"}<span class="sc-val-unit">%</span></div>
        <div class="sc-val-verdict">${d.er_verdict || "—"}</div>
        <div class="sc-val-thr">Category: ≤${d.er_good_threshold}% good · >${d.er_flag_threshold}% flag</div>
      </div>
      ${retCard("YTD Return",  d.ret_ytd)}
      ${retCard("3-Year Avg",  d.ret_3y)}
      ${retCard("5-Year Avg",  d.ret_5y)}
      ${retCard("Dividend Yield", d.yield_pct)}
      <div class="sc-val-card">
        <div class="sc-val-label">Turnover</div>
        <div class="sc-val-value">${d.turnover_rate != null ? d.turnover_rate : "—"}<span class="sc-val-unit">%</span></div>
        <div class="sc-val-thr">&lt;10% tax-efficient · &gt;50% use tax shelter</div>
      </div>
    </div>
  </div>`;

  // ── Holdings ───────────────────────────────────────────────────────────────
  let holdingsHtml = "";
  if (d.top_holdings && d.top_holdings.length > 0) {
    const maxPct = Math.max(...d.top_holdings.map(h => h.pct));
    const rows = d.top_holdings.map((h, i) => {
      const barW = Math.round(h.pct / maxPct * 100);
      return `
      <div class="sc-holding-row">
        <span class="sc-holding-rank">${i + 1}</span>
        <span class="sc-holding-sym">${escapeHtml(h.symbol)}</span>
        <span class="sc-holding-name">${escapeHtml(h.name)}</span>
        <div class="sc-holding-bar-wrap">
          <div class="sc-holding-bar" style="width:${barW}%"></div>
        </div>
        <span class="sc-holding-pct">${h.pct.toFixed(2)}%</span>
      </div>`;
    }).join("");

    const concClass = (d.top10_weight || 0) > 55 ? "sc-v-fail" : (d.top10_weight || 0) > 35 ? "sc-v-partial" : "sc-v-pass";
    holdingsHtml = `
    <div class="sc-card">
      <div class="sc-card-header">
        <div class="sc-card-title">Top Holdings</div>
        <span class="sc-verdict-badge ${concClass}">Top-10: ${d.top10_weight}%</span>
      </div>
      <div class="sc-holdings-list">${rows}</div>
    </div>`;
  }

  // ── Sector breakdown ────────────────────────────────────────────────────────
  let sectorHtml = "";
  if (d.sector_weights && Object.keys(d.sector_weights).length > 0) {
    const sorted = Object.entries(d.sector_weights).sort((a, b) => b[1] - a[1]);
    const maxW = sorted[0][1];
    const rows = sorted.map(([sec, w]) => {
      const label = sec.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
      const barW  = Math.round(w / maxW * 100);
      return `
      <div class="sc-sector-row">
        <span class="sc-sector-name">${escapeHtml(label)}</span>
        <div class="sc-holding-bar-wrap">
          <div class="sc-holding-bar sc-sector-bar" style="width:${barW}%"></div>
        </div>
        <span class="sc-holding-pct">${w.toFixed(1)}%</span>
      </div>`;
    }).join("");
    sectorHtml = `
    <div class="sc-card">
      <div class="sc-card-header"><div class="sc-card-title">Sector Weights</div></div>
      <div class="sc-holdings-list">${rows}</div>
    </div>`;
  }

  // ── Notes ───────────────────────────────────────────────────────────────────
  const noteKey = `sc_etf_note_${d.ticker}`;
  const savedNote = localStorage.getItem(noteKey) || "";
  const notes = `
  <div class="sc-card">
    <div class="sc-card-header"><div class="sc-card-title">Your Thesis Notes</div></div>
    <p class="sc-card-sub">Why are you holding this? What would make you sell?
      <span class="sc-note-prompts"> · Thematic: does it actually hold what the theme implies? · Factor: is reconstitution frequent? · Dividend: is yield from income or price decline?</span></p>
    <textarea id="scEtfNote" class="sc-moat-note" rows="3"
              placeholder="e.g. SMH — semiconductor concentration is acceptable given 10Y+ thesis on AI infrastructure capex…">${escapeHtml(savedNote)}</textarea>
  </div>`;

  const priceHtml = renderPriceChartCard() + renderStageCard(d.stage_analysis ?? null, d.top_down ?? null);
  const momHtml   = "";  // merged into the Price History chart
  const valGrowthHtml = renderValGrowthSection(d.val_growth ?? null, "$");
  const cycleHtml = renderCycleSection(d.cycle);
  container.innerHTML =
    // Orientation
    header + priceHtml
    // Core ETF decision: cost/size/structure, then what it actually holds
    + scorecard + holdingsHtml + sectorHtml
    // Performance, underlying valuation, exposure context
    + returns + valGrowthHtml + cycleHtml
    // Timing + notes
    + momHtml + notes;

  setupPriceChart(container, d.price_history || [], d.beta ?? null, d.ticker, d.benchmark_history || [], d.benchmark_symbol, d.benchmark_name);
  // setupMomentum removed — oscillators now live in the Price History chart

  const noteEl = document.getElementById("scEtfNote");
  if (noteEl) noteEl.addEventListener("input", () => localStorage.setItem(noteKey, noteEl.value));
}

// ── Red-flag strip: aggregate every fatal-flaw signal already computed into one
//    glanceable bar so a good composite score can't hide a disqualifier. ────────
function renderRedFlags(d) {
  const fs = d.fundamental_scores || {};
  const flags = [];
  const ben = fs.beneish;
  if (ben && ben.available && ben.flag)
    flags.push({ t: `Beneish M ${ben.m} — earnings-manipulation profile`, k: "hard" });
  const alt = fs.altman;
  if (alt && alt.applicable !== false && alt.z != null && alt.z < 1.8)
    flags.push({ t: `Altman Z ${alt.z} — distress zone`, k: "hard" });
  const pio = fs.piotroski;
  if (pio && pio.score != null && pio.score <= 3)
    flags.push({ t: `F-Score ${pio.score}/9 — weak financial strength`, k: "soft" });
  const eps = d.earnings?.eps_trailing;
  if (eps != null && eps <= 0)
    flags.push({ t: "Loss-making (negative trailing EPS)", k: "soft" });
  const fcf = d.cashflow_margins?.cashflow?.fcf_latest ?? d.cashflow?.fcf_latest;
  if (fcf != null && fcf < 0)
    flags.push({ t: "Negative free cash flow", k: "soft" });
  const ic = d.debt_structure?.interest_coverage;
  if (ic != null && ic < 3 && !d.is_financial)
    flags.push({ t: ic < 0 ? `Interest coverage ${ic}× — EBIT negative, can't cover interest`
                           : `Interest coverage ${ic}× — thin cushion`, k: ic < 1.5 ? "hard" : "soft" });
  const de = d.debt_structure?.debt_ebitda;
  if (de != null && de > 4 && !d.is_financial)
    flags.push({ t: `Net debt ${de}× EBITDA — high leverage`, k: "soft" });
  const pr = d.capital_return?.payout_ratio_pct;
  if (pr != null && pr > 100)
    flags.push({ t: `Payout ratio ${pr}% — dividend exceeds earnings`, k: "soft" });
  const spread = d.dcf?.econ_profit_spread;
  if (spread != null && spread < 0)
    flags.push({ t: `ROIC ${spread}pp below cost of capital — value-destructive growth`, k: "soft" });

  window._lastRedFlags = flags.map(f => f.t);   // stash for the decision footer's disqualifiers
  if (!flags.length)
    return `<div class="rf-strip rf-clean"><span class="rf-icon">✓</span> No red flags across forensic, distress, leverage &amp; payout checks</div>`;
  const items = flags.map(f => `<span class="rf-item ${f.k === "hard" ? "rf-hard" : "rf-soft"}">${f.k === "hard" ? "⛔" : "⚠"} ${escapeHtml(f.t)}</span>`).join("");
  const nHard = flags.filter(f => f.k === "hard").length;
  return `<div class="rf-strip ${nHard ? "rf-has-hard" : ""}">
    <span class="rf-label">Red flags (${flags.length})</span>${items}
  </div>`;
}

// Jump links for the stock page. Clicking scrolls; scrolling highlights. The
// anchor is a button, not an <a href="#...">, so a click never rewrites the URL
// of a single-page app that does not read it back.
function setupSectionNav(container) {
  const nav = container.querySelector(".sc-secnav");
  if (!nav) return;
  const links = [...nav.querySelectorAll(".sc-secnav-link")];
  const target = id => container.querySelector(`#sec-${CSS.escape(id)}`);
  links.forEach(btn => btn.addEventListener("click", () => {
    const el = target(btn.dataset.sec);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  }));
  if (typeof IntersectionObserver !== "function") return;
  const seen = new Map();
  const obs = new IntersectionObserver(entries => {
    entries.forEach(e => seen.set(e.target.id, e));
    // The topmost section currently on screen wins; if none is, keep the last.
    const visible = [...seen.values()].filter(e => e.isIntersecting)
      .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
    if (!visible.length) return;
    const id = visible[0].target.id.replace(/^sec-/, "");
    links.forEach(b => b.classList.toggle("active", b.dataset.sec === id));
  }, { rootMargin: "-80px 0px -60% 0px", threshold: 0 });
  container.querySelectorAll(".sc-section").forEach(sec => obs.observe(sec));
}

function renderScreenResult(d, container) {
  // Dispatch to ETF renderer if needed
  if (d.quote_type === "ETF" || d.quote_type === "MUTUALFUND") {
    renderEtfResult(d, container);
    return;
  }

  const g1 = d.gate1 || {};
  const g2 = d.gate2 || {};
  const g3 = d.gate3 || {};
  const raw = d.raw || {};

  const verdictMeta = {
    pass:       { icon: "✓", label: "Pass",          cls: "sc-v-pass"    },
    partial:    { icon: "~", label: "Partial",        cls: "sc-v-partial" },
    fail:       { icon: "✗", label: "Fail",           cls: "sc-v-fail"    },
    expensive:  { icon: "↑", label: "Expensive",      cls: "sc-v-fail"    },
    fair:       { icon: "≈", label: "Fair Value",     cls: "sc-v-partial" },
    watch:      { icon: "⚠", label: "Watch",          cls: "sc-v-partial" },
    pass_val:   { icon: "✓", label: "Undervalued",    cls: "sc-v-pass"    },
    unknown:    { icon: "—", label: "Insufficient data", cls: "sc-v-na"  },
  };

  function vBadge(key, overrideLabel) {
    const m = verdictMeta[key] || verdictMeta.unknown;
    return `<span class="sc-verdict-badge ${m.cls}">${m.icon} ${overrideLabel || m.label}</span>`;
  }

  // map gate3 verdict key
  const g3verdictKey = g3.verdict === "pass" ? "pass_val" : (g3.verdict || "unknown");

  // ── Company header ──────────────────────────────────────────────────────
  const mcapSym = d.currency && d.currency !== "USD" ? d.currency : "$";
  const mcap = d.market_cap
    ? (d.market_cap >= 1e12 ? `${mcapSym}${(d.market_cap/1e12).toFixed(2)}T`
      : d.market_cap >= 1e9  ? `${mcapSym}${(d.market_cap/1e9).toFixed(1)}B`
      : `${mcapSym}${(d.market_cap/1e6).toFixed(0)}M`)
    : "—";
  const header = `
  <div class="sc-header">
    <div class="sc-header-left">
      <div class="sc-ticker-name">
        <span class="sc-ticker">${escapeHtml(d.ticker)}</span>
        <span class="sc-name">${escapeHtml(d.name)}</span>
      </div>
      <div class="sc-chips">
        <span class="sc-chip">${escapeHtml(d.sector)}</span>
        <span class="sc-chip sc-chip-dim">${escapeHtml(d.industry)}</span>
      </div>
      <p class="sc-desc">${escapeHtml(d.description)}</p>
    </div>
    <div class="sc-header-right">
      <div class="sc-price">${d.currency} ${d.price != null ? d.price.toLocaleString() : "—"}</div>
      <div class="sc-mcap">Mkt Cap ${mcap}</div>
      <div class="sc-gates-summary">
        <div class="sc-gate-chip">${vBadge(g1.verdict, "Gate 1: " + (verdictMeta[g1.verdict]?.label || g1.verdict))}</div>
        <div class="sc-gate-chip">${vBadge("unknown", "Gate 2: You assess")}</div>
        <div class="sc-gate-chip">${vBadge(g3verdictKey, "Gate 3: " + (verdictMeta[g3verdictKey]?.label || g3.verdict))}</div>
      </div>
    </div>
  </div>`;

  // ── Gate 1 ──────────────────────────────────────────────────────────────
  const g1Rows = (g1.checks || []).map(c => {
    const icon  = c.pass === true ? "✓" : c.pass === false ? "✗" : "—";
    const cls   = c.pass === true ? "sc-pass" : c.pass === false ? "sc-fail" : "sc-na";
    const thr   = `${c.direction === "above" ? ">" : "<"} ${c.threshold}${c.unit}`;
    return `
    <tr class="${cls}">
      <td class="sc-row-icon">${icon}</td>
      <td class="sc-row-name">${escapeHtml(c.name)}${c.period ? `<span class="sc-row-period">${escapeHtml(c.period)}</span>` : ""}</td>
      <td class="sc-row-val">${escapeHtml(c.display)}</td>
      <td class="sc-row-thr">${escapeHtml(thr)}</td>
      <td class="sc-row-note">${escapeHtml(c.note)}${c.caveat ? `<span class="sc-row-caveat">⚠ ${escapeHtml(c.caveat)}</span>` : ""}</td>
    </tr>`;
  }).join("");

  const g1Score = `${g1.passed ?? 0} / ${g1.scorable ?? "?"}`;
  const gate1 = `
  <div class="sc-card">
    <div class="sc-card-header">
      <div class="sc-card-title">Gate 1 — Quantitative Quality</div>
      ${vBadge(g1.verdict)}
      <div class="sc-score">${g1Score} passed</div>
    </div>
    <p class="sc-card-sub">Checks whether the business generates returns well above its cost of capital. All thresholds are absolute minimums — not industry-relative.</p>
    <div class="sc-g1-bar-wrap">
      <div class="sc-g1-bar" style="width:${g1.scorable ? Math.round(g1.passed/g1.scorable*100) : 0}%"></div>
    </div>
    <table class="sc-table">
      <thead><tr>
        <th></th><th>Metric</th><th>Value</th><th>Min bar</th><th>Why it matters</th>
      </tr></thead>
      <tbody>${g1Rows}</tbody>
    </table>
    ${(d.quarterly_growth && (d.quarterly_growth.rows || []).length)
      ? `<p class="sc-card-sub g1-xref">The EPS and revenue growth rows above are the latest
           quarter only. Every quarter is in <b>Growth, quarter by quarter</b>, under
           What the business earns.</p>` : ""}
  </div>`;

  // ── Gate 2 (qualitative) ────────────────────────────────────────────────
  const moatKey = `sc_moat_${d.ticker}`;
  const savedMoats = JSON.parse(localStorage.getItem(moatKey) || "{}");
  const moatNoteKey = `sc_moat_note_${d.ticker}`;
  const savedNote = localStorage.getItem(moatNoteKey) || "";
  const hints = g2.moat_hints || [];

  const moats = [
    { id: "network",    icon: "🔗", label: "Network Effects",
      desc: "Each new user makes the product more valuable (Visa, Meta, Mastercard, Uber)" },
    { id: "switching",  icon: "🔒", label: "Switching Costs",
      desc: "Cost to leave > benefit of leaving (MSFT, Salesforce, Oracle, Adobe)" },
    { id: "intangible", icon: "💡", label: "Intangible Assets",
      desc: "Patents, brands, regulatory licences (GOOGL, Moody's, Novo Nordisk)" },
    { id: "cost",       icon: "⚙", label: "Cost Advantage",
      desc: "Structurally cheaper than all rivals (Amazon AWS, Costco, TSMC)" },
    { id: "scale",      icon: "🏗", label: "Efficient Scale",
      desc: "Market too small for profitable new entrants (railroads, pipelines, niche SaaS)" },
  ];

  const moatCheckboxes = moats.map(m => {
    const isChecked = savedMoats[m.id] || false;
    const hinted    = hints.includes(m.id) ? ' <span class="sc-moat-hint">suggested</span>' : "";
    return `
    <label class="sc-moat-row">
      <input type="checkbox" class="sc-moat-cb" data-moat="${m.id}" ${isChecked ? "checked" : ""}>
      <span class="sc-moat-icon">${m.icon}</span>
      <div class="sc-moat-body">
        <span class="sc-moat-label">${m.label}${hinted}</span>
        <span class="sc-moat-desc">${m.desc}</span>
      </div>
    </label>`;
  }).join("");

  const gate2 = `
  <div class="sc-card">
    <div class="sc-card-header">
      <div class="sc-card-title">Gate 2 — Moat Assessment</div>
      <span class="sc-verdict-badge sc-v-na">Manual review</span>
    </div>
    <p class="sc-card-sub">
      Only ~26% of stocks have a durable moat. Check any sources that apply — your assessment is saved per ticker.
    </p>
    <div class="sc-moat-list">${moatCheckboxes}</div>
  </div>`;

  // ── Gate 3 (valuation) ──────────────────────────────────────────────────
  const g3MetricCards = Object.values(g3.metrics || {}).map(m => {
    const vKey = m.verdict === "undervalued" ? "pass_val" : m.verdict || "unknown";
    const vm   = verdictMeta[vKey] || verdictMeta.unknown;
    return `
    <div class="sc-val-card ${vm.cls}">
      <div class="sc-val-label">${escapeHtml(m.label)}</div>
      ${m.period ? `<div class="sc-val-period">${escapeHtml(m.period)}</div>` : ""}
      <div class="sc-val-value">${m.value != null ? m.value : "—"}<span class="sc-val-unit">${escapeHtml(m.unit)}</span></div>
      <div class="sc-val-verdict">${vm.icon} ${vm.label}</div>
      <div class="sc-val-thr">${escapeHtml(m.thresholds)}</div>
    </div>`;
  }).join("");

  // Supporting data
  const extras = [
    ["Trailing P/E",   raw.trailing_pe, "×"],
    ["Price/Book",     raw.price_book,  "×"],
    ["FCF (most recent FY)", raw.fcf_annual ? (raw.fcf_annual >= 1e9 ? `$${(raw.fcf_annual/1e9).toFixed(1)}B` : `$${(raw.fcf_annual/1e6).toFixed(0)}M`) : null, ""],
    ["Employees",      raw.employees ? raw.employees.toLocaleString() : null, ""],
  ].filter(r => r[1] != null).map(r =>
    `<div class="sc-extra"><span class="sc-extra-key">${r[0]}</span><span class="sc-extra-val">${r[1]}${r[2]}</span></div>`
  ).join("");

  const gate3 = `
  <div class="sc-card">
    <div class="sc-card-header">
      <div class="sc-card-title">Gate 3 — Valuation</div>
      ${vBadge(g3verdictKey)}
    </div>
    <p class="sc-card-sub">
      Entry rule: buy only when at least 2 metrics say "undervalued" and
      price ≤ 75% of intrinsic value (25% margin of safety).
    </p>
    <div class="sc-val-grid">${g3MetricCards}</div>
    ${extras ? `<div class="sc-extras">${extras}</div>` : ""}
  </div>`;

  // ── Overall verdict + Fly List reminder ────────────────────────────────
  const allPass = g1.verdict === "pass" && g3.verdict === "pass";
  const anyFail = g1.verdict === "fail" || g3.verdict === "expensive";
  const overallV  = allPass ? "pass" : anyFail ? "fail" : "partial";
  const overallVM = verdictMeta[overallV];

  const flyList = `
  <div class="sc-card sc-flylist">
    <div class="sc-card-header">
      <div class="sc-card-title">Overall Verdict</div>
      ${vBadge(overallV)}
    </div>
    <p class="sc-flylist-note">
      ${overallV === "pass"
        ? "This stock passes the quantitative gates. Before buying, complete the Self-Check Checklist below."
        : overallV === "fail"
        ? "This stock fails one or more gates. Do not proceed to the checklist until it passes."
        : "Partial pass — investigate further before proceeding to the checklist."}
    </p>
  </div>`;

  // ── Self-Check Checklist (Munger test + Fly List) ──────────────────────
  const checklistHtml = `
  <details class="sc-card sc-checklist">
    <summary class="sc-checklist-summary">
      <span class="sc-card-title">Self-Check Checklist</span>
      <span class="sc-checklist-hint">Munger test + Fly List — click to expand</span>
    </summary>
    <p class="sc-card-sub">
      Qualitative checks to complete before buying — separate from the quantitative gates above.
    </p>

    <div class="sc-checklist-section">
      <div class="sc-checklist-section-title">Munger Test</div>
      <p class="sc-checklist-prompt">
        <em>"Name 3 ways a competitor could destroy this business in 10 years — and explain why each fails."</em>
      </p>
      <div class="sc-moat-notes-wrap">
        <label class="sc-moat-note-label">
          Your answer
          <textarea id="scMoatNote" class="sc-moat-note" rows="3"
                    placeholder="e.g. Competitor could undercut on price, but Apple's ecosystem switching costs prevent churn…">${escapeHtml(savedNote)}</textarea>
        </label>
      </div>
    </div>

    <div class="sc-checklist-section">
      <div class="sc-checklist-section-title">Fly List</div>
      <div class="sc-flylist-items">
        ${[
          "Summarise the business in one sentence. Describe it in three sentences.",
          "Name the single biggest existential threat. Explain why the moat survives it.",
          "What are the 2–3 variables that will drive results over the next 5 years?",
          "Is it currently at ≤75% of intrinsic value (margin of safety)?",
          "Can you avoid a >20% permanent loss at this entry price?",
          "Are you willing to hold for at least 10 years?",
          "If the market closes for 10 years, can you sleep?",
          "If it drops 50%, will you buy more — not panic-sell?",
          "Pre-mortem: Imagine it's 2 years later and the position has failed. Write 3 specific reasons why."
        ].map((q, i) => `
          <label class="sc-fly-row">
            <input type="checkbox" class="sc-fly-cb" data-q="${i}">
            <span class="sc-fly-q">${q}</span>
          </label>`).join("")}
      </div>
    </div>
  </details>`;

  const priceChartHtml = renderPriceChartCard();
  const stageHtml = renderStageCard(d.stage_analysis ?? null, d.top_down ?? null);
  const earningsHtml = renderEarningsSection(d.earnings);
  const momHtml   = "";  // merged into the Price History chart
  const lynchHtml = renderLynchSection(d.lynch_data ?? null);
  const currSym = d.currency && d.currency !== "USD" ? d.currency : "$";
  const valGrowthHtml = renderValGrowthSection(d.val_growth ?? null, currSym);
  const debtHtml  = renderDebtStructureSection(d.debt_structure ?? null, currSym);
  const hiddenHtml = renderHiddenAssetsSection(d.hidden_assets ?? null, currSym);
  const cfMarginHtml = renderCashflowMarginsSection(d.cashflow_margins ?? null, currSym);
  const revProfitHtml = renderRevProfitSection(d.revenue_profit ?? null, currSym);
  // Levels first, then the growth rates off them — the same section, in that order.
  const quarterlyGrowthHtml = renderQuarterlyGrowth(d.quarterly_growth, currSym);
  // What is expected next — placed directly under the price chart, so the
  // price is read against what it is pricing in. A-shares: Tushare broker
  // reports and company guidance. US: Yahoo's analyst targets and estimates.
  const forecastsHtml = renderForecastsSection(d.forecasts ?? null, currSym);
  const fundScoresHtml = renderFundamentalScoresSection(d.fundamental_scores ?? null, currSym);
  const compHtml  = renderCompetitiveSection(d);
  const dcfHtml   = renderDcfSection(d.dcf ?? null, currSym);
  const flowHtml  = renderFlowSection(d.flow_data ?? null, currSym);
  const capitalReturnHtml = renderCapitalReturnSection(d.capital_return ?? null, currSym);
  const fearHtml  = renderFearScoreSection(d.fear_score ?? null);
  const tpHtml    = renderTakeProfitSection(d.take_profit ?? null);
  const cycleHtml = renderCycleSection(d.cycle);
  const redFlagsHtml = renderRedFlags(d);
  // ── Page layout ─────────────────────────────────────────────────────────
  // Two dozen cards in one scroll is a pile, not a report. They are grouped
  // into the order the question is actually answered in — what is this, what
  // does it earn, is the earning durable, can it survive, what is it worth,
  // what is the market doing, decide — and each group gets a heading and a
  // jump link. A group with nothing in it is dropped, heading and all.
  // [id, nav chip, heading, one-line summary, cards]. The chip is a short noun
  // so seven of them fit one sticky row; the heading says what the section
  // answers.
  const sections = [
    ["what-it-is", "Overview", "What this is",
      forecastsHtml ? "price, what is expected of it, trend and anything that should stop you reading on"
                    : "price, trend and anything that should stop you reading on",
      priceChartHtml + forecastsHtml + stageHtml + redFlagsHtml],
    ["performance", "Performance", "What the business earns",
      "revenue, profit, cash and how all three compare with its peers",
      revProfitHtml + quarterlyGrowthHtml + earningsHtml + renderPeerCard(d) + cfMarginHtml + lynchHtml],
    ["quality", "Quality", "Whether the earning lasts",
      "returns on capital, the moat, competitive position",
      gate1 + gate2 + compHtml + fundScoresHtml],
    ["strength", "Balance sheet", "Whether it survives a bad year",
      "debt, maturities and what the assets are worth",
      debtHtml + hiddenHtml],
    ["value", "Valuation", "What it is worth",
      "the valuation gate, intrinsic value and what comes back to shareholders",
      gate3 + valGrowthHtml + `<div id="valPctileBox"></div>` + dcfHtml + capitalReturnHtml],
    ["context", "Market", "What the market is doing",
      "flows, the industry cycle and sentiment",
      flowHtml + cycleHtml + momHtml + fearHtml],
    ["decision", "Decision", "The decision",
      "the verdict, your exit plan and the checks only you can make",
      tpHtml + flyList + checklistHtml + renderDecisionFooter(d)],
  ].filter(sec => (sec[4] || "").trim());

  const nav = `<nav class="sc-secnav">${sections.map(([id, chip], i) =>
    `<button type="button" class="sc-secnav-link${i === 0 ? " active" : ""}" data-sec="${id}">
       <span class="sc-secnav-num">${i + 1}</span>${escapeHtml(chip)}</button>`).join("")}</nav>`;

  container.innerHTML =
    header + renderListingBasis(d.listing_basis) + nav
    + sections.map(([id, chip, title, sub, html], i) => `
      <section class="sc-section" id="sec-${id}">
        <header class="sc-section-head">
          <span class="sc-section-num">${i + 1}</span>
          <h3 class="sc-section-title">${escapeHtml(title)}</h3>
          <span class="sc-section-sub">${escapeHtml(sub)}</span>
        </header>
        ${html}
      </section>`).join("");

  setupSectionNav(container);

  loadValPctile(state.market, d.ticker);
  const peerCard = container.querySelector(".peer-card");
  if (peerCard) loadPeerCard(peerCard);
  setupRevProfit(container);
  setupDecisionFooter(container, d);
  setupPriceChart(container, d.price_history || [], raw.beta ?? null, d.ticker, d.benchmark_history || [], d.benchmark_symbol, d.benchmark_name);
  // setupMomentum removed — oscillators now live in the Price History chart
  setupTakeProfit(container);

  // ── Competitive Position (on-demand peer analysis) ──────────────────────
  const compBtn = container.querySelector("#compAnalyzeBtn");
  if (compBtn) compBtn.addEventListener("click", () => loadCompetitive(compBtn.dataset.market, compBtn.dataset.ticker));

  // ── DCF: live-recompute on assumption changes ───────────────────────────
  if (container.querySelector("#dcf_rate")) {
    ["dcf_rate", "dcf_g1", "dcf_fade", "dcf_term"].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.addEventListener("input", _recomputeDcf);
    });
    const reset = document.getElementById("dcfReset");
    if (reset && _dcfData) reset.addEventListener("click", () => {
      document.getElementById("dcf_rate").value = _dcfData.cost_of_equity;
      document.getElementById("dcf_g1").value = _dcfData.default_stage1_growth;
      document.getElementById("dcf_fade").value = _dcfData.default_fade_years;
      document.getElementById("dcf_term").value = _dcfData.default_terminal;
      _recomputeDcf();
    });
    _recomputeDcf();
  }

  // ── Persist Gate 2 moat checkboxes + notes ──────────────────────────────
  container.querySelectorAll(".sc-moat-cb").forEach(cb => {
    cb.addEventListener("change", () => {
      const state_ = {};
      container.querySelectorAll(".sc-moat-cb").forEach(c => {
        state_[c.dataset.moat] = c.checked;
      });
      localStorage.setItem(moatKey, JSON.stringify(state_));
    });
  });
  const noteEl = document.getElementById("scMoatNote");
  if (noteEl) {
    noteEl.addEventListener("input", () => {
      localStorage.setItem(moatNoteKey, noteEl.value);
    });
  }
}

// ── Price Chart ───────────────────────────────────────────────────────────

// ── Stage Analysis card (stock detail, sits under the price chart) ──────────
// Reads the SAME memoised whole-market map the screener uses, so the detail
// page and the Filter results can never disagree.
//
// Note the two different "relative strength" numbers on this page: the chart's
// RS sub-panel plots the RAW ratio (price / benchmark, e.g. 0.2196), while
// Mansfield RS below is that ratio normalised against its own 52-week average
// (e.g. -3.54). Same input, different transform — the screener filters on the
// Mansfield form, so it is spelled out here rather than left to collide.
// Weinstein screens top-down: market -> industry group -> stock. Rendered as
// a chain so alignment (or the lack of it) is visible at a glance — a Stage 2
// stock inside a Stage 4 group is a materially different proposition.
function renderTopDown(td) {
  if (!td || !td.market) return '';
  const cell = (label, v, name) => {
    const st = v && v.stage;
    const cls = st ? ' stage-s' + st : '';
    const txt = st ? ['', '1 Basing', '2 Advancing', '3 Topping', '4 Declining'][st] : 'n/a';
    const sub = name ? `<span class="td-sub">${escapeHtml(name)}</span>` : '';
    return `<div class="td-step"><span class="td-label">${label}</span>`
         + `<span class="stage-pill${cls}">${txt}</span>${sub}</div>`;
  };
  const g = td.group, gn = td.group_name;
  const meta = g && g.mansfield_rs != null
    ? `<span class="td-note">Group RS <b>${g.mansfield_rs > 0 ? '+' : ''}${(+g.mansfield_rs).toFixed(2)}</b>`
      + (g.rs_rank ? ` · rank <b>${g.rs_rank}/${g.rs_rank_of}</b>` : '')
      + (g.pct_stage2 != null ? ` · <b>${g.pct_stage2}%</b> of ${g.members} members in Stage 2` : '')
      + (g.members != null && g.members < 8 ? ` · <em>thin group — read with care</em>` : '')
      + `</span>` : '';
  const m = td.market;
  const mkt = m && m.sma30 != null
    ? `<span class="td-note">Market <b>${escapeHtml(td.market_name || "benchmark")}</b>: `
      + `30w SMA <b>${(+m.sma30).toFixed(2)}</b> (${m.price_vs_sma30 > 0 ? "+" : ""}${(+m.price_vs_sma30).toFixed(1)}%, ${escapeHtml(m.sma30_slope_dir || "—")})`
      + (m.ema30 != null ? ` · 30w EMA <b>${(+m.ema30).toFixed(2)}</b> (${m.price_vs_ema30 > 0 ? "+" : ""}${(+m.price_vs_ema30).toFixed(1)}%)` : "")
      + `</span>` : '';
  const aligned = td.stock && td.stock.stage === 2 && g && g.stage === 2;
  return `<div class="td-chain">
      ${cell('Market', td.market, null)}<span class="td-arrow">›</span>
      ${cell('Industry', g, gn)}<span class="td-arrow">›</span>
      ${cell('Stock', td.stock, null)}
      ${aligned ? '<span class="td-aligned">stock &amp; group both advancing</span>' : ''}
    </div>${meta}${mkt}`;
}

function renderStageCard(sa, td) {
  if (!sa) {
    return `
    <div class="sc-card">
      <div class="sc-card-header"><div class="sc-card-title">Stage Analysis <span class="sc-card-sub">(Weinstein · weekly)</span></div></div>
      <p class="sc-chart-empty">Weekly price cache not built yet, or under 30 weeks of history for this name.</p>
    </div>`;
  }
  const st = sa.stage, meta = sa.meta || {};
  const pill = st
    ? `<span class="stage-pill stage-s${st} stage-pill-lg">${escapeHtml(sa.stage_label || '')}</span>`
    : `<span class="stage-pill stage-pill-lg">Unclassified — ${sa.weeks_available || 0} weeks of history</span>`;
  const emaSt = sa.stage_ema;
  const agree = st && emaSt && st === emaSt;
  const emaNote = emaSt
    ? `<span class="stage-ema-note">EMA basis: <b>${escapeHtml(sa.stage_ema_label || '')}</b>${agree ? ' (agrees)' : ' — differs from the SMA reading'}</span>`
    : '';
  const num = (v, d, suf) => v == null ? '—' : `${v > 0 && suf === '%' ? '+' : ''}${(+v).toFixed(d)}${suf || ''}`;
  // The raw index code is meaningless on screen; map the ones we actually use.
  const benchName = (c) => !c ? '' : (String(c).startsWith('000985') ? '中证全指 · CSI All-Share'
                       : c === '^GSPC' ? 'S&P 500' : c);
  const cls = (v, good) => v == null ? '' : (good(v) ? ' stage-pos' : ' stage-neg');
  const tile = (label, val, klass, title) =>
    `<div class="sc-vol-stat" title="${escapeHtml(title)}"><span class="sc-vs-label">${label}</span><span class="sc-vs-val${klass || ''}">${val}</span></div>`;

  const row = (ok, label, val) =>
    `<span class="bq-item ${ok ? 'bq-yes' : 'bq-no'}"><i>${ok ? '✓' : '✗'}</i>${label} <b>${val}</b></span>`;

  // Stage 1 base checklist. A flat average alone is not a base — Weinstein's
  // Stage 1 is a sequence: a real decline, then a range, then volume drying up,
  // then the average levelling. Each is shown separately so a partial base is
  // visible as partial rather than being rounded up to "Basing".
  const bq = sa.base_testable ? (() => {
    const t = sa.base_thresholds || {};
    return `<div class="bq-strip">
      <span class="bq-head">Stage 1 base checklist <b>${sa.base_score}/4</b>${sa.base_textbook ? ' <span class="bq-badge">textbook base</span>' : ''}</span>
      ${row(sa.base_c1_prior_decline, `prior decline (≤${t.decline_max}%)`, num(sa.base_decline_pct, 1, '%'))}
      ${row(sa.base_c2_range_bound, `range-bound (≤${t.band_max}%)`, num(sa.base_band_pct, 1, '%'))}
      ${row(sa.base_c3_volume_dryup, `volume dry-up (≤${t.vol_max}%)`, num(sa.base_vol_trend_pct, 1, '%'))}
      ${row(sa.base_c4_ma_flat, `${sa.base_ma_weeks}w MA flat (±${t.ma_flat_max}%)`, num(sa.base_ma_drift_pct, 1, '%'))}
      <span class="bq-note">measured over the last ${sa.base_weeks} weeks against the ${sa.base_ma_weeks}-week (60-day) average — deliberately shorter than the 30-week stage line, which could not be tested within the cache depth. A stock can therefore show a clean base while its 30-week line still falls.</span>
    </div>`;
  })() : (sa.base_note ? `<div class="bq-strip"><span class="bq-note">Base checklist unavailable — ${escapeHtml(sa.base_note)}.</span></div>` : '');

  // Stage 2 confirmation checklist. A rising average alone is not the signal —
  // the advance should be backed by demand (volume expanding), by structure
  // (each pullback bottoming higher), and by price holding above the average
  // THROUGH its dips, not merely at the latest close.
  const aq = sa.adv_testable ? (() => {
    const t = sa.adv_thresholds || {};
    const hl = sa.adv_higher_lows === null ? 'too few swing lows' : (sa.adv_higher_lows ? 'rising' : 'not rising');
    return `<div class="bq-strip bq-strip-adv">
      <span class="bq-head">Stage 2 confirmation <b>${sa.adv_score}/4</b>${sa.adv_textbook ? ' <span class="bq-badge">textbook advance</span>' : ''}</span>
      ${row(sa.adv_c1_volume_expanding, `volume expanding (≥${t.vol_mult_min}×)`, sa.adv_vol_mult == null ? '—' : (+sa.adv_vol_mult).toFixed(2) + '×')}
      ${row(sa.adv_c2_ma_rising, `${sa.adv_ma_weeks}w MA rising (>${t.slope_min}%)`, num(sa.adv_ma_slope_pct, 1, '%'))}
      ${row(sa.adv_c3_higher_lows, `higher lows (${sa.adv_swing_lows} found)`, hl)}
      ${row(sa.adv_c4_price_above, `price &amp; dips above MA (≥${t.above_pct_min}%)`, num(sa.adv_above_pct, 0, '%'))}
      <span class="bq-note">measured over the last ${sa.adv_weeks} weeks against the ${sa.adv_ma_weeks}-week average — the same line the stage label uses. Volume compares the recent ${Math.round(sa.adv_weeks/2)} weeks against the ${Math.round(sa.adv_weeks/2)} before them; note that a ≥${t.vol_mult_min}× multiple is easier for thinly traded names, so read it alongside liquidity.</span>
    </div>`;
  })() : '';

  // Stage 3 — distribution: heavy volume, widening swings, average rolling flat.
  const tq = sa.top_testable ? (() => {
    const t = sa.top_thresholds || {};
    return `<div class="bq-strip" data-stage="3">
      <span class="bq-head">Stage 3 top checklist <b>${sa.top_score}/3</b>${sa.top_textbook ? ' <span class="bq-badge bq-badge-warn">textbook top</span>' : ''}</span>
      ${row(sa.top_c1_high_volume, `high volume (≥${t.vol_mult_min}×)`, sa.top_vol_mult == null ? '—' : (+sa.top_vol_mult).toFixed(2) + '×')}
      ${row(sa.top_c2_high_volatility, `swings widening (≥${t.vola_ratio_min}×)`, sa.top_vola_ratio == null ? '—' : (+sa.top_vola_ratio).toFixed(2) + '×')}
      ${row(sa.top_c3_ma_flat, `${sa.top_ma_weeks}w MA flattening (±${t.ma_flat_max}%)`, num(sa.top_ma_slope_pct, 1, '%'))}
      <span class="bq-note">volume and volatility both compare the recent ${Math.round(sa.top_weeks/2)} weeks against the ${Math.round(sa.top_weeks/2)} before them; volatility is the stdev of weekly returns (currently ${num(sa.top_weekly_vol_pct,1,'%')}).</span>
    </div>`;
  })() : '';

  // Stage 4 — two CORE tests; rising volume is an optional aggravating factor.
  const dq = sa.decl_testable ? (() => {
    const t = sa.decl_thresholds || {};
    return `<div class="bq-strip" data-stage="4">
      <span class="bq-head">Stage 4 decline checklist <b>${sa.decl_score}/2</b>${sa.decl_confirmed ? ' <span class="bq-badge bq-badge-down">decline confirmed</span>' : ''}</span>
      ${row(sa.decl_c1_ma_falling, `${sa.decl_ma_weeks}w MA falling (<${t.slope_max}%)`, num(sa.decl_ma_slope_pct, 1, '%'))}
      ${row(sa.decl_c2_price_below, 'price below the MA', num(sa.decl_price_vs_ma_pct, 1, '%'))}
      <span class="bq-item bq-opt"><i>${sa.decl_opt_volume_rising ? '!' : '·'}</i>optional: volume rising (≥${t.vol_mult_min}×) <b>${sa.decl_vol_mult == null ? '—' : (+sa.decl_vol_mult).toFixed(2) + '×'}</b></span>
      <span class="bq-note">the two core tests are what define Stage 4, so they pass for every stock carrying the label. Rising volume is kept OUT of the score — plenty of declines simply bleed away on thin trade — and is flagged only as an aggravating factor.</span>
    </div>`;
  })() : '';

  // Show the checklist for the CURRENT stage, plus any other that scores full
  // marks — that is precisely where a transition shows itself (e.g. a stock
  // still labelled Stage 4 whose base checklist has gone 4/4).
  const strips = {1: bq, 2: aq, 3: tq, 4: dq};
  const full = {1: sa.base_textbook, 2: sa.adv_textbook, 3: sa.top_textbook, 4: false};
  const shown = [];
  if (st && strips[st]) shown.push(strips[st]);
  [1, 2, 3].forEach(k => { if (k !== st && full[k] && strips[k]) shown.push(strips[k]); });
  const checklists = shown.join('');

  return `
  <div class="sc-card">
    <div class="sc-card-header">
      <div class="sc-card-title">Stage Analysis <span class="sc-card-sub">(Weinstein · weekly)</span></div>
      <div class="stage-head-right">${pill}${emaNote}</div>
    </div>
    ${renderTopDown(td)}
    ${checklists}
    <div class="sc-vol-strip stage-strip">
      ${tile('Mansfield RS', num(sa.mansfield_rs, 2, ''), cls(sa.mansfield_rs, v => v > 0),
             'Relative strength vs ' + (benchName(meta.benchmark) || 'the benchmark') + ', normalised against its own 52-week average. Above 0 = outperforming. This is NOT the raw ratio plotted in the chart panel above.')}
      ${tile('vs 30-wk SMA', num(sa.price_vs_sma30, 2, '%'), cls(sa.price_vs_sma30, v => v > 0),
             'How far the latest weekly close sits above/below the 30-week simple moving average.')}
      ${tile('30-wk SMA slope', num(sa.sma30_slope, 2, '%') + (sa.sma30_slope_dir ? ` <span class="stage-dir">${sa.sma30_slope_dir}</span>` : ''),
             cls(sa.sma30_slope, v => v > 0), 'Change in the 30-week SMA over the last 13 weeks. Stage 2 requires a rising average.')}
      ${tile('Volume', sa.vol_ratio == null ? '—' : (+sa.vol_ratio).toFixed(2) + '×',
             sa.vol_ratio != null && sa.vol_ratio >= 1.5 ? ' stage-pos' : '',
             'BREAKOUT volume \\u2014 the latest COMPLETED week\\u2019s volume divided by its trailing 13-week average. A single-week surge (Weinstein\\u2019s breakout rule), distinct from the SUSTAINED test in the Stage 2 checklist which compares the mean of 13 weeks against the prior 13. The in-progress week is excluded, since a partial week always looks like low volume.')}
      ${tile('vs 30-wk resistance', num(sa.dist_to_resistance, 2, '%'), cls(sa.dist_to_resistance, v => v > 0),
             'Distance to the highest weekly CLOSE of the prior 30 weeks (close-based, not intraday high). Positive = broken out.')}
    </div>
    <p class="sc-card-note">30-wk SMA <b>${sa.sma30 == null ? '—' : (+sa.sma30).toFixed(2)}</b> · 30-wk EMA <b>${sa.ema30 == null ? '—' : (+sa.ema30).toFixed(2)}</b> · resistance <b>${sa.resistance == null ? '—' : (+sa.resistance).toFixed(2)}</b> · ${sa.weeks_available || 0} weekly bars to ${escapeHtml(meta.as_of || '—')}${meta.benchmark ? ' · benchmark ' + escapeHtml(benchName(meta.benchmark)) : ''}.
    Stage is a <b>descriptive state, not a buy/sell signal</b>; no backtest has been run.</p>
  </div>`;
}

// Defaults are the O'Neil/Weinstein working view: candles, weekly bars, a
// multi-year window. The old defaults (line chart, 1 month) hid volume, the
// index overlay, relative strength and the earnings line behind a toggle most
// people never pressed, and a one-month window cannot show a 30-week base.
const _ADV_DEFAULT_PERIOD = '3Y';

function renderPriceChartCard() {
  const periods = ['1M', '3M', '6M', 'YTD', '1Y', '3Y', '5Y'];
  const btns = periods.map(p =>
    `<button class="sc-period-btn${p === _ADV_DEFAULT_PERIOD ? ' active' : ''}" data-period="${p}">${p}</button>`
  ).join('');
  return `
  <div class="sc-card sc-pricechart">
    <div class="sc-card-header">
      <div class="sc-card-title">Price History</div>
      <div class="sc-price-meta">
        <span class="sc-price-chg-badge" id="scPriceChgBadge"></span>
        <div class="sc-live-price" id="scLivePrice" style="display:none">
          <span class="sc-live-dot" id="scLiveDot"></span>
          <span class="sc-live-val" id="scLiveVal"></span>
          <span class="sc-live-chg" id="scLiveChg"></span>
        </div>
        <div class="sc-period-btns">${btns}</div>
        <div class="sc-adv-btns">
          <button class="sc-adv-btn" data-advmode="line" title="Simple line chart">Line</button>
          <button class="sc-adv-btn active" data-advmode="candle" title="Candles + index overlay + volume + relative strength + earnings line">Candles</button>
        </div>
        <div class="sc-adv-btns" id="scAdvOpts">
          <button class="sc-adv-btn" data-advint="D" title="Daily bars">D</button>
          <button class="sc-adv-btn active" data-advint="W" title="Weekly bars (Weinstein's and O'Neil's native cadence)">W</button>
          <button class="sc-adv-btn active" data-advma="sma" title="Simple moving average (Weinstein canonical)">SMA</button>
          <button class="sc-adv-btn" data-advma="ema" title="Exponential moving average">EMA</button>
          <span class="sc-ma-inputs" title="Price moving-average periods — editable, counted in the displayed interval">
            <input type="number" class="sc-ma-in" id="scMa0" min="2" max="400" step="1">
            <input type="number" class="sc-ma-in" id="scMa1" min="2" max="400" step="1">
            <input type="number" class="sc-ma-in" id="scMa2" min="2" max="400" step="1">
          </span>
          <span class="sc-ma-inputs sc-volma-wrap" title="Volume moving-average period — the navy line across the volume bars, and the baseline the '% vs avg' figure is measured against. Counted in the displayed interval. Blank uses O'Neil's default: 50 on daily bars, 10 on weekly.">
            <span class="sc-volma-lbl">Vol MA</span>
            <input type="number" class="sc-ma-in" id="scVolMa" min="2" max="400" step="1" placeholder="50">
          </span>
          <button class="sc-adv-btn" id="scBollBtn" title="Bollinger Bands (20, 2σ) — a price overlay, so it sits on the candles">BB</button>
          <select class="sc-osc-sel" id="scOscSel" title="Oscillator panel — computed on the displayed interval">
            <option value="none">No oscillator</option>
            <option value="rsi">RSI (14)</option>
            <option value="macd">MACD (12/26/9)</option>
            <option value="stoch">Stochastic (14/3)</option>
            <option value="roc">Rate of Change (12)</option>
            <option value="aroon">Aroon (25)</option>
          </select>
        </div>
      </div>
    </div>
    <div class="sc-vol-strip" id="scVolStrip">
      <div class="sc-vol-stat">
        <span class="sc-vs-label">Daily Volatility</span>
        <span class="sc-vs-val" id="scDailyVolVal">—</span>
      </div>
      <div class="sc-vol-divider"></div>
      <div class="sc-vol-stat">
        <span class="sc-vs-label">Ann. Volatility</span>
        <span class="sc-vs-val" id="scVolVal">—</span>
        <span class="sc-vs-badge" id="scVolBadge"></span>
      </div>
      <div class="sc-vol-divider"></div>
      <div class="sc-vol-stat">
        <span class="sc-vs-label">Max Drawdown</span>
        <span class="sc-vs-val sc-vs-red" id="scDdVal">—</span>
      </div>
      <div class="sc-vol-divider"></div>
      <div class="sc-vol-stat">
        <span class="sc-vs-label">Beta (vs S&amp;P)</span>
        <span class="sc-vs-val" id="scBetaVal">—</span>
        <span class="sc-vs-badge" id="scBetaBadge"></span>
      </div>
    </div>
    <div class="sc-chart-hover-row" id="scChartHoverRow" style="display:none">
      <span class="sc-tt-date" id="scTtDate"></span>
      <span class="sc-tt-price" id="scTtPrice"></span>
      <span class="sc-tt-chg" id="scTtChg"></span>
      <span class="sc-tt-vix" id="scTtVix" style="display:none"></span>
    </div>
    <div class="sc-chart-wrap" id="scChartWrap">
      <div class="sc-chart-loading">Loading…</div>
    </div>
    <div class="sc-adv-readout" id="scAdvReadout"></div>
  </div>`;
}

// ── Advanced (Seeking-Alpha-style) chart ────────────────────────────────────
// Three stacked panels on one shared date axis: candlesticks + moving
// averages, a volume histogram, then relative strength (price / benchmark).
// Hand-rolled SVG, matching the existing line chart's approach.
//
// All VALUE axes sit on the RIGHT (price, volume, RS) following charting
// convention — it also keeps the left edge clear for panel titles, which is
// what made the earlier left-axis version crowded and hard to read.
//
// The Weinstein average is 150-day on daily bars / 30-week on weekly bars —
// the same line, since his 30-week SMA is ~150 trading days. It is starred
// ONLY when a configured period actually equals it; if the user customises the
// periods away from that, nothing is starred rather than mislabelling a line.
// Fast / key / slow. The old middle colour was fuchsia (#d946ef), which shouted
// over the candles; emerald sits in the same value range without competing.
const _ADV_COLORS = ['#2563eb', '#059669', '#f59e0b'];
const _ADV_DEFAULT_PERIODS = { D: [50, 150, 200], W: [10, 30, 40] };
const _ADV_KEY_N = { D: 150, W: 30 };

function _advMaSeries(vals, n, type) {
  const out = new Array(vals.length).fill(null);
  if (!n || vals.length < n) return out;
  if (type === 'ema') {
    const a = 2 / (n + 1);
    let e = vals.slice(0, n).reduce((x, y) => x + y, 0) / n;
    out[n - 1] = e;
    for (let i = n; i < vals.length; i++) { e = a * vals[i] + (1 - a) * e; out[i] = e; }
  } else {
    let run = 0;
    for (let i = 0; i < vals.length; i++) {
      run += vals[i];
      if (i >= n) run -= vals[i - n];
      if (i >= n - 1) out[i] = run / n;
    }
  }
  return out;
}

// Daily -> weekly OHLCV. Week keyed by its Monday; bar LABELLED by its last
// trading day (week-end), which reads correctly on a date axis.
function _advToWeekly(bars) {
  const out = [];
  let cur = null, curKey = null;
  for (const b of bars) {
    if (b.c == null) continue;
    const dt = new Date(b.d + 'T00:00:00Z');
    const dow = (dt.getUTCDay() + 6) % 7;
    const mon = new Date(dt); mon.setUTCDate(dt.getUTCDate() - dow);
    const key = mon.toISOString().slice(0, 10);
    if (key !== curKey) {
      if (cur) out.push(cur);
      curKey = key;
      // `n` counts the daily bars folded into this week, which is how the
      // caller knows whether the final week is still in progress.
      cur = { d: b.d, o: b.o ?? b.c, h: b.h ?? b.c, l: b.l ?? b.c, c: b.c, v: b.v || 0, n: 1 };
    } else {
      cur.h = Math.max(cur.h, b.h ?? b.c);
      cur.l = Math.min(cur.l, b.l ?? b.c);
      cur.c = b.c; cur.d = b.d; cur.v += (b.v || 0); cur.n++;
    }
  }
  if (cur) out.push(cur);
  return out;
}

function _advFmtVol(v) {
  if (v == null) return '—';
  const a = Math.abs(v);
  if (a >= 1e9) return (v / 1e9).toFixed(1) + 'B';
  if (a >= 1e6) return (v / 1e6).toFixed(1) + 'M';
  if (a >= 1e3) return (v / 1e3).toFixed(0) + 'K';
  return String(Math.round(v));
}
function _advFmtPx(v) { return v >= 1000 ? (v / 1000).toFixed(2) + 'k' : v.toFixed(2); }
function _advFmtRs(v) { return v >= 1 ? v.toFixed(2) : v.toFixed(4); }

// Nudge right-edge value badges apart so overlapping series stay legible.
function _advSpread(items, minGap, loY, hiY) {
  const s = items.slice().sort((a, b) => a.y - b.y);
  for (let i = 1; i < s.length; i++) {
    if (s[i].y - s[i - 1].y < minGap) s[i].y = s[i - 1].y + minGap;
  }
  const over = s.length ? s[s.length - 1].y - hiY : 0;
  if (over > 0) s.forEach(o => { o.y = Math.max(loY, o.y - over); });
  return s;
}

// ── Oscillators for the merged chart ────────────────────────────────────────
// Computed on the DISPLAYED interval series, so on weekly bars RSI(14) really
// is 14 weeks. The label carries the unit ("RSI 14w" vs "RSI 14d") rather than
// letting a daily-looking name sit over weekly math — the old Momentum card
// was daily-only and had no interval concept at all.
function _advEma(vals, n) {
  const a = 2 / (n + 1), out = new Array(vals.length).fill(null);
  let e = null;
  for (let i = 0; i < vals.length; i++) {
    const v = vals[i];
    if (v == null) continue;
    e = (e == null) ? v : a * v + (1 - a) * e;
    out[i] = e;
  }
  return out;
}

function _advOscillator(bars, kind, unit) {
  const c = bars.map(b => b.c);
  const hi = bars.map(b => b.h ?? b.c), lo = bars.map(b => b.l ?? b.c);
  const N = c.length;
  const u = unit;                                   // 'w' or 'd'
  const nil = new Array(N).fill(null);

  if (kind === 'rsi') {
    const n = 14, out = nil.slice();
    let ag = 0, al = 0;
    for (let i = 1; i < N; i++) {
      const d = c[i] - c[i - 1];
      const g = d > 0 ? d : 0, l = d < 0 ? -d : 0;
      if (i <= n) { ag += g / n; al += l / n; if (i === n) out[i] = al === 0 ? 100 : 100 - 100 / (1 + ag / al); }
      else { ag = (ag * (n - 1) + g) / n; al = (al * (n - 1) + l) / n; out[i] = al === 0 ? 100 : 100 - 100 / (1 + ag / al); }
    }
    return { series: [{ v: out, c: '#7c3aed', w: 1.5 }], lo: 0, hi: 100,
             guides: [30, 50, 70], label: `RSI 14${u}`, fmt: v => v.toFixed(1) };
  }
  if (kind === 'macd') {
    const e12 = _advEma(c, 12), e26 = _advEma(c, 26);
    const macd = c.map((_, i) => (e12[i] == null || e26[i] == null) ? null : e12[i] - e26[i]);
    const sig = _advEma(macd.map(v => v == null ? null : v), 9);
    const histo = macd.map((v, i) => (v == null || sig[i] == null) ? null : v - sig[i]);
    const all = [...macd, ...sig, ...histo].filter(v => v != null);
    return { series: [{ v: macd, c: '#2563eb', w: 1.5 }, { v: sig, c: '#dc2626', w: 1.2 }],
             bars: histo, lo: Math.min(...all), hi: Math.max(...all), guides: [0],
             label: `MACD 12/26/9 ${u}`, fmt: v => v.toFixed(2) };
  }
  if (kind === 'stoch') {
    const n = 14, k = nil.slice();
    for (let i = n - 1; i < N; i++) {
      const hh = Math.max(...hi.slice(i - n + 1, i + 1)), ll = Math.min(...lo.slice(i - n + 1, i + 1));
      k[i] = hh === ll ? 50 : (c[i] - ll) / (hh - ll) * 100;
    }
    const d = k.map((_, i) => {
      if (i < n + 1) return null;
      const w = k.slice(i - 2, i + 1).filter(v => v != null);
      return w.length === 3 ? w.reduce((a, b) => a + b, 0) / 3 : null;
    });
    return { series: [{ v: k, c: '#0891b2', w: 1.5 }, { v: d, c: '#f59e0b', w: 1.2 }],
             lo: 0, hi: 100, guides: [20, 50, 80], label: `Stochastic 14/3 ${u}`, fmt: v => v.toFixed(1) };
  }
  if (kind === 'roc') {
    const n = 12, out = nil.slice();
    for (let i = n; i < N; i++) if (c[i - n]) out[i] = (c[i] / c[i - n] - 1) * 100;
    const all = out.filter(v => v != null);
    return { series: [{ v: out, c: '#16a34a', w: 1.5 }], lo: Math.min(...all, 0), hi: Math.max(...all, 0),
             guides: [0], label: `Rate of Change 12${u}`, fmt: v => v.toFixed(1) + '%' };
  }
  if (kind === 'aroon') {
    const n = 25, up = nil.slice(), dn = nil.slice();
    for (let i = n; i < N; i++) {
      const wH = hi.slice(i - n, i + 1), wL = lo.slice(i - n, i + 1);
      up[i] = (n - (n - wH.indexOf(Math.max(...wH)))) / n * 100;
      dn[i] = (n - (n - wL.indexOf(Math.min(...wL)))) / n * 100;
    }
    return { series: [{ v: up, c: '#16a34a', w: 1.4 }, { v: dn, c: '#dc2626', w: 1.4 }],
             lo: 0, hi: 100, guides: [30, 50, 70], label: `Aroon 25${u}`, fmt: v => v.toFixed(0) };
  }
  return null;
}

// Bollinger Bands are a PRICE overlay, not an oscillator — the old Momentum
// card rendered them as their own chart, which detached the bands from the
// candles they describe.
function _advBollinger(bars, n = 20, k = 2) {
  const c = bars.map(b => b.c), N = c.length;
  const mid = new Array(N).fill(null), up = new Array(N).fill(null), dn = new Array(N).fill(null);
  for (let i = n - 1; i < N; i++) {
    const w = c.slice(i - n + 1, i + 1);
    const m = w.reduce((a, b) => a + b, 0) / n;
    const sd = Math.sqrt(w.reduce((a, b) => a + (b - m) * (b - m), 0) / n);
    mid[i] = m; up[i] = m + k * sd; dn[i] = m - k * sd;
  }
  return { mid, up, dn, n, k };
}

const _ADV_MIN_BARS = 12;         // floor on the zoom window — below this the
                                  // moving averages have nothing to sit on
const _ADV_ZOOM_STEP = 1.25;      // one wheel notch

function drawAdvancedChart(wrap, allDaily, benchMap, opts) {
  const interval = opts.interval === 'W' ? 'W' : 'D';
  const maType = opts.maType === 'ema' ? 'ema' : 'sma';
  const benchLabel = opts.benchLabel || opts.benchSymbol || 'benchmark';
  const periods = (opts.periods && opts.periods.length ? opts.periods : _ADV_DEFAULT_PERIODS[interval])
    .map(Number).filter(n => n >= 2 && n <= 400);

  const full = interval === 'W' ? _advToWeekly(allDaily) : allDaily.filter(b => b.c != null);
  if (full.length < 2) { wrap.innerHTML = '<p class="sc-chart-empty">Not enough data for this period.</p>'; return; }

  // Moving averages are computed on the FULL series and only then sliced to the
  // visible window — a short window (e.g. 1M) would otherwise have no 150-day
  // average at all, or one computed off just the visible bars.
  const closes = full.map(b => b.c);
  const keyN = _ADV_KEY_N[interval];
  const maSeries = periods.map((n, i) => ({
    n, c: _ADV_COLORS[i % _ADV_COLORS.length], key: n === keyN,
    s: _advMaSeries(closes, n, maType),
  }));

  let i0 = 0;
  if (opts.fromDate) { const k = full.findIndex(b => b.d >= opts.fromDate); i0 = k > 0 ? k : 0; }
  const baseI0 = i0;                       // the period-derived window, i.e. "reset"
  let i1 = full.length;

  // ── Zoom ──────────────────────────────────────────────────────────────
  // Held on the wrap element rather than in module state so every chart on the
  // page keeps its own, and so it survives the innerHTML rebuild that each
  // redraw performs.
  //
  // Stored as DATES, not bar indices. That is the whole point: zoom into
  // March–June on the daily chart, switch to weekly, and you are still looking
  // at March–June rather than at the same *number* of bars covering five years.
  // So `interval` is deliberately NOT part of the key.
  //
  // Instrument and period DO invalidate it — those are requests for a different
  // view, not a pan within the current one.
  const zKey = [opts.zoomKey || '', opts.fromDate || ''].join('|');
  if (wrap._advZoomKey !== zKey) { wrap._advZoom = null; wrap._advZoomKey = zKey; }
  wrap._advRedraw = () => drawAdvancedChart(wrap, allDaily, benchMap, opts);
  if (wrap._advZoom) {
    const za = full.findIndex(b => b.d >= wrap._advZoom.from);
    const zb = full.findIndex(b => b.d > wrap._advZoom.to);
    if (za >= 0) i0 = za;
    i1 = zb > 0 ? zb : full.length;
    if (i1 - i0 < _ADV_MIN_BARS) i1 = Math.min(full.length, i0 + _ADV_MIN_BARS);
    if (i1 - i0 < _ADV_MIN_BARS) i0 = Math.max(0, i1 - _ADV_MIN_BARS);
  }
  const zoomed = !!wrap._advZoom;

  const vis = full.slice(i0, i1);
  if (vis.length < 2) { wrap.innerHTML = '<p class="sc-chart-empty">Not enough data for this period.</p>'; return; }

  const rs = vis.map(b => { const bc = benchMap[b.d]; return bc ? b.c / bc : null; });
  // Callers can suppress the RS panel. On the Market Index page the instrument
  // often IS the benchmark (SPY), where RS is flat by definition and the panel
  // is just wasted height.
  const hasRS = opts.showRS !== false && rs.filter(v => v != null).length > 1;

  // Geometry — value axes on the right; titles on the left.
  const osc = opts.osc && opts.osc !== 'none' ? _advOscillator(vis, opts.osc, interval === 'W' ? 'w' : 'd') : null;
  // The viewBox used to be a fixed 780 while the SVG stretched to ~1300px, so
  // EVERYTHING scaled ~1.7x — 9.5px labels rendered at ~16px, which is why the
  // MA numbers looked oversized against the lines. Sizing the viewBox to the
  // container makes it 1:1, so declared type size is the type size you see.
  // Match the container so the SVG renders 1:1 and type is never magnified or
  // shrunk. The floor is low enough that a narrow column still maps 1:1 rather
  // than scaling a too-wide viewBox down into unreadable 4px text.
  const W = Math.round(Math.max(360, Math.min(wrap.clientWidth || 900, 1500)));
  const PL = 14, PR = 74, PT = 14, PB = 28, GAP = 20;
  const HP = Math.round(Math.max(200, Math.min(300, W * 0.21)));
  const HV = 52 + 12, HR = hasRS ? 64 : 0, HO = osc ? 64 : 0;   // +12 for the rate label beneath the bars
  const H = PT + HP + GAP + HV + (hasRS ? GAP + HR : 0) + (osc ? GAP + HO : 0) + PB;
  const plotW = W - PL - PR;
  const AX = W - PR + 6;                       // right-hand axis text x
  const yP0 = PT, yP1 = PT + HP;
  const yV0 = yP1 + GAP, yV1 = yV0 + HV;
  const yR0 = yV1 + GAP, yR1 = yR0 + HR;
  const yO0 = (hasRS ? yR1 : yV1) + GAP, yO1 = yO0 + HO;

  let lo = Math.min(...vis.map(b => b.l ?? b.c)), hi = Math.max(...vis.map(b => b.h ?? b.c));
  maSeries.forEach(m => m.s.slice(i0).forEach(v => { if (v != null) { lo = Math.min(lo, v); hi = Math.max(hi, v); } }));
  // Bollinger is a price overlay; its bands must be inside the panel range.
  const boll = opts.boll ? _advBollinger(vis) : null;
  if (boll) {
    boll.up.forEach(v => { if (v != null) hi = Math.max(hi, v); });
    boll.dn.forEach(v => { if (v != null) lo = Math.min(lo, v); });
  }
  const rawLo = lo;
  const padP = (hi - lo) * 0.07 || 1; lo -= padP; hi += padP;
  // A wide range with a low near zero drove `lo` NEGATIVE, which printed a
  // negative price on the axis and silently disabled the log scale (log needs
  // lo > 0). Prices cannot be negative, so floor the padded low.
  if (lo <= 0) lo = rawLo > 0 ? rawLo * 0.5 : 1e-6;

  // ── Earnings line (O'Neil): TTM EPS plotted at a FIXED multiple, identical
  // for every stock, so the vertical price-to-line gap means the same thing on
  // every chart. A per-stock "best fit" would refit that gap away, which is
  // precisely what makes a best-fit earnings line uninformative.
  const epsD = opts.epsData && opts.epsData.status === 'ready' ? opts.epsData : null;
  const MULT = epsD ? (epsD.fixed_multiple || 20) : 20;
  let epsAtBar = [], epsReportAt = [];
  if (epsD && epsD.points && epsD.points.length) {
    // Step the line forward from each ANNOUNCEMENT date — never the fiscal
    // period end, which would draw earnings the market did not yet know.
    // Carry the quarter's own figures through, not just the running TTM — the
    // crosshair reports EPS vs estimate on the bar a result landed.
    const pts = epsD.points.map(p => ({
      d: p.d.length === 8 ? `${p.d.slice(0,4)}-${p.d.slice(4,6)}-${p.d.slice(6)}` : p.d,
      ttm: p.ttm, q: p.q, est: p.est, surprise: p.surprise }));
    let k = -1;
    epsAtBar = vis.map(b => {
      while (k + 1 < pts.length && pts[k + 1].d <= b.d) k++;
      return k >= 0 ? pts[k].ttm : null;
    });
    // Which bar each report LANDED on, so the crosshair can show the quarter's
    // own EPS and its surprise rather than only the running TTM.
    let j = 0;
    epsReportAt = vis.map((b, i) => {
      const prevD = i > 0 ? vis[i - 1].d : '';
      let hit = null;
      while (j < pts.length && pts[j].d <= b.d) {
        if (pts[j].d > prevD) hit = pts[j];   // landed inside THIS bar's span
        j++;
      }
      return hit;
    });
  }
  const epsPlot = epsAtBar.map(v => (v == null || v <= 0) ? null : v * MULT);
  const epsVals = epsPlot.filter(v => v != null);
  // Fit the line into the panel when that costs little; otherwise clamp it to
  // the edge and say so, rather than crushing the price bars to fit.
  let epsOffScale = false;
  if (epsVals.length) {
    const eLo = Math.min(...epsVals), eHi = Math.max(...epsVals);
    if (eLo >= lo / 3 && eHi <= hi * 3) { lo = Math.min(lo, eLo * 0.97); hi = Math.max(hi, eHi * 1.03); }
    else { epsOffScale = true; }
  }

  // Log scale: O'Neil's default on weekly charts, so equal PERCENTAGE moves get
  // equal vertical distance and an early base is not visually crushed by a
  // later 10x advance.
  const useLog = opts.logScale !== false && lo > 0;
  const tf = v => useLog ? Math.log(Math.max(v, 1e-9)) : v;
  const tLo = tf(lo), tHi = tf(hi);
  const yP = v => yP1 - (tf(v) - tLo) / ((tHi - tLo) || 1) * HP;
  const n = vis.length, step = plotW / n;
  const cx = i => PL + step * (i + 0.5);
  // Body width tracks the slot. The old hard 10px cap meant that on short
  // windows (1M daily ~ 30px slots) bodies filled only a THIRD of their slot,
  // which read as thin candles floating in wide gaps. 0.66 of the slot is the
  // conventional ratio; the 22px ceiling only guards absurdly few bars.
  const bw = Math.max(1, Math.min(step * 0.66, 22));
  const wickW = bw >= 6 ? 1.6 : 1;

  const UP = '#16a34a', DN = '#dc2626';
  const candles = vis.map((b, i) => {
    const o = b.o ?? b.c, c = b.c;
    const h = b.h ?? Math.max(o, c), l = b.l ?? Math.min(o, c);
    const up = c >= o, col = up ? UP : DN, x = cx(i);
    const yo = yP(o), yc = yP(c);
    const top = Math.min(yo, yc), bh = Math.max(1, Math.abs(yc - yo));
    return `<line x1="${x.toFixed(2)}" y1="${yP(h).toFixed(2)}" x2="${x.toFixed(2)}" y2="${yP(l).toFixed(2)}" stroke="${col}" stroke-width="${wickW}"/>`
         + `<rect x="${(x - bw / 2).toFixed(2)}" y="${top.toFixed(2)}" width="${bw.toFixed(2)}" height="${bh.toFixed(2)}" fill="${col}" opacity="${up ? 0.9 : 1}"/>`;
  }).join('');

  // Benchmark REBASED to the stock's price at the window start, so the two
  // lines share an origin and their divergence is the out/under-performance.
  // This is the magnitude view; the RS panel below is the trend view. Both are
  // kept deliberately — O'Neil's key tell (RS making a new high while price is
  // still based) is only visible in the ratio, not in the overlay.
  let idxPath = '', idxBadge = '', idxRebased = [];
  {
    const firstIdx = vis.findIndex(b => benchMap[b.d] != null);
    if (firstIdx >= 0) {
      const b0 = benchMap[vis[firstIdx].d], p0 = vis[firstIdx].c;
      idxRebased = vis.map(b => { const bc = benchMap[b.d]; return bc ? bc / b0 * p0 : null; });
      const pts = [];
      idxRebased.forEach((v, i) => {
        if (v == null) return;
        const y = Math.max(yP0 + 1, Math.min(yP1 - 1, yP(v)));
        pts.push(`${cx(i).toFixed(2)},${y.toFixed(2)}`);
      });
      if (pts.length > 1) {
        idxPath = `<polyline points="${pts.join(' ')}" fill="none" stroke="#0891b2" stroke-width="1.3" `
                + `stroke-linejoin="round" opacity="0.85"/>`;
        const lv = [...idxRebased].reverse().find(v => v != null);
      }
    }
  }

  const maPaths = maSeries.map(m => {
    const pts = [];
    for (let i = 0; i < n; i++) { const v = m.s[i0 + i]; if (v != null) pts.push(`${cx(i).toFixed(2)},${yP(v).toFixed(2)}`); }
    return pts.length < 2 ? '' :
      `<polyline points="${pts.join(' ')}" fill="none" stroke="${m.c}" stroke-width="${m.key ? 2.2 : 1.3}" opacity="${m.key ? 1 : 0.8}" stroke-linejoin="round"/>`;
  }).join('');

  // Right-edge value labels. Price, every MA, the rebased index and the earnings
  // line all compete for the same strip, so they are collected into ONE list and
  // spread together — previously the index and earnings badges were positioned
  // independently and could sit on top of an MA badge.
  // Only the price is filled solid; the rest are "ghost" chips so the strip
  // reads as one quiet column instead of a stack of coloured blocks.
  const last = vis[n - 1];
  const badgeItems = [{ y: yP(last.c), t: _advFmtPx(last.c), c: last.c >= (last.o ?? last.c) ? UP : DN, solid: true }];
  maSeries.forEach(m => {
    const v = m.s[full.length - 1];
    if (v != null) badgeItems.push({ y: yP(v), t: _advFmtPx(v), c: m.c });
  });
  {
    const lv = idxRebased.length ? [...idxRebased].reverse().find(v => v != null) : null;
    if (lv != null) badgeItems.push({ y: yP(lv), t: _advFmtPx(lv), c: '#0891b2' });
  }
  if (epsVals.length && !epsOffScale) {
    const lv = [...epsPlot].reverse().find(v => v != null);
    if (lv != null) badgeItems.push({ y: yP(lv), t: _advFmtPx(lv), c: '#b45309' });
  }
  // Badges size to their TEXT rather than filling the whole right margin. The
  // old fixed 64px chips blanketed the price-axis labels, which live in the
  // same lane; now they only cover the ticks they actually sit on, and those
  // ticks are suppressed rather than drawn underneath.
  const BH = 14;
  const bWidth = t => Math.min(PR - 8, Math.max(30, String(t).length * 5.9 + 10));
  const placed = _advSpread(badgeItems, BH + 3, yP0 + BH / 2, yP1 - BH / 2);
  const badgeYs = placed.map(b => b.y);
  const badges = placed.map(b => {
    const w = bWidth(b.t), x = W - PR + 4;
    return `<rect x="${x}" y="${(b.y - BH / 2).toFixed(1)}" width="${w.toFixed(1)}" height="${BH}" rx="3" `
      + `fill="${b.solid ? b.c : 'var(--card, #ffffff)'}" stroke="${b.c}" stroke-width="${b.solid ? 0 : 1}"/>`
      + `<text x="${(x + w / 2).toFixed(1)}" y="${(b.y + 3.4).toFixed(1)}" text-anchor="middle" `
      + `class="adv-badge-t" fill="${b.solid ? '#ffffff' : b.c}">${b.t}</text>`;
  }).join('');

  const vmax = Math.max(...vis.map(b => b.v || 0)) || 1;
  const volBars = vis.map((b, i) => {
    const v = b.v || 0; if (!v) return '';
    const hgt = Math.max(0.5, v / vmax * (HV - 12));   // reserve the label strip
    const up = (b.c ?? 0) >= (b.o ?? b.c ?? 0);
    return `<rect x="${(cx(i) - bw / 2).toFixed(2)}" y="${(yV1 - hgt).toFixed(2)}" width="${bw.toFixed(2)}" height="${hgt.toFixed(2)}" fill="${up ? UP : DN}" opacity="0.5"/>`;
  }).join('');

  // Volume moving average, O'Neil convention: 50-day / 10-week. The rate label
  // reads volume against THIS average, which is what "big volume" means.
  // Volume MA period. Defaults to O'Neil's convention (50 days / 10 weeks) but
  // is overridable, and is counted in the DISPLAYED interval like every other
  // period on this chart — 10 on weekly bars means 10 weeks, not 10 days.
  const _vmOpt = Number(opts.volMa);
  const volN = Number.isFinite(_vmOpt) && _vmOpt >= 2
    ? Math.min(400, Math.round(_vmOpt))
    : (interval === 'W' ? 10 : 50);
  const volAvg = vis.map((_, i) => {
    if (i0 + i < volN - 1) return null;
    // Skip bars with unknown volume rather than counting them as zero — a null
    // dragged into the mean understates the average and makes real expansion
    // look ordinary.
    let t = 0, cnt = 0;
    for (let k = i0 + i - volN + 1; k <= i0 + i; k++) {
      const bv = full[k] && full[k].v;
      if (bv == null) continue;
      t += bv; cnt++;
    }
    return cnt ? t / cnt : null;
  });
  const volAvgPath = (() => {
    const pts = [];
    volAvg.forEach((v, i) => { if (v != null) pts.push(`${cx(i).toFixed(2)},${(yV1 - Math.min(HV - 12, v / vmax * (HV - 12))).toFixed(2)}`); });
    return pts.length > 1 ? `<polyline points="${pts.join(' ')}" fill="none" stroke="#1e3a8a" stroke-width="1.1" opacity="0.9"/>` : '';
  })();
  // Volume accumulates through a period while price does not, so an
  // in-progress bar always reads as collapsing volume. On a Monday a weekly bar
  // holds one day of trade and showed "-98% vs avg" — a calendar artifact, not
  // a volume signal. Read the rate off the last COMPLETED bar instead; the
  // same rule stage_analysis already applies to vol_ratio.
  // Weekly: the final bucket holds fewer than 5 sessions.
  // Daily: the bar is today's and the US session has not closed (20:00 UTC =
  //   16:00 ET, ignoring DST — deliberately conservative). Leaving this as
  //   `false` meant an intraday bar read "-88% vs avg" half an hour after the
  //   open, which is the clock talking, not volume.
  const _nowUTC = new Date();
  const _todayUTC = _nowUTC.toISOString().slice(0, 10);
  const lastIsPartial = interval === 'W'
    ? (vis[n - 1] && vis[n - 1].n != null && vis[n - 1].n < 5)
    : (vis[n - 1] && vis[n - 1].d === _todayUTC && _nowUTC.getUTCHours() < 20);
  const rateIdx = lastIsPartial && n >= 2 ? n - 2 : n - 1;
  const lastAvg = volAvg[rateIdx] != null ? volAvg[rateIdx] : [...volAvg].reverse().find(v => v != null);
  const lastVol = vis[rateIdx] && vis[rateIdx].v;
  const volRate = (lastAvg && lastVol) ? Math.round((lastVol / lastAvg - 1) * 100) : null;
  const volRateNote = lastIsPartial ? (interval === 'W' ? ' (last full week)' : ' (last full session)') : '';

  // Relative strength = price / benchmark. Raw ratio (the Seeking-Alpha style
  // you asked for), NOT the Mansfield-normalised form the Stage screen uses.
  let rsPath = '', rsAxis = '', rsBadge = '';
  if (hasRS) {
    const vals = rs.filter(v => v != null);
    let rlo = Math.min(...vals), rhi = Math.max(...vals);
    const padR = (rhi - rlo) * 0.14 || (rhi || 1) * 0.05; rlo -= padR; rhi += padR;
    const yR = v => yR1 - (v - rlo) / (rhi - rlo) * HR;
    const pts = [];
    rs.forEach((v, i) => { if (v != null) pts.push(`${cx(i).toFixed(2)},${yR(v).toFixed(2)}`); });
    rsPath = `<polyline points="${pts.join(' ')}" fill="none" stroke="#0f172a" stroke-width="1.4" stroke-linejoin="round"/>`;
    rsAxis = `<text x="${AX}" y="${yR0 + 9}" class="adv-tick">${_advFmtRs(rhi)}</text>`
           + `<text x="${AX}" y="${yR1}" class="adv-tick">${_advFmtRs(rlo)}</text>`;
    const lastRs = [...rs].reverse().find(v => v != null);
    if (lastRs != null) {
      const y = Math.min(yR1 - 7, Math.max(yR0 + 7, yR(lastRs)));
      rsBadge = `<rect x="${W - PR + 3}" y="${(y - 7.5).toFixed(1)}" width="${PR - 10}" height="15" rx="3.5" fill="#0f172a"/>`
              + `<text x="${W - PR + 3 + (PR - 10) / 2}" y="${(y + 3.6).toFixed(1)}" text-anchor="middle" class="adv-badge-t" fill="#fff">${_advFmtRs(lastRs)}</text>`;
    }
  }

  // Bollinger bands: shaded envelope + midline, drawn under the candles.
  let bollPath = '';
  if (boll) {
    const up = [], dnRev = [], mid = [];
    boll.up.forEach((v, i) => { if (v != null) up.push(`${cx(i).toFixed(2)},${yP(v).toFixed(2)}`); });
    for (let i = boll.dn.length - 1; i >= 0; i--) {
      const v = boll.dn[i]; if (v != null) dnRev.push(`${cx(i).toFixed(2)},${yP(v).toFixed(2)}`);
    }
    boll.mid.forEach((v, i) => { if (v != null) mid.push(`${cx(i).toFixed(2)},${yP(v).toFixed(2)}`); });
    if (up.length > 1) {
      bollPath = `<polygon points="${up.concat(dnRev).join(' ')}" fill="#6366f1" opacity="0.09"/>`
               + `<polyline points="${up.join(' ')}" fill="none" stroke="#6366f1" stroke-width="1" opacity="0.55"/>`
               + `<polyline points="${dnRev.slice().reverse().join(' ')}" fill="none" stroke="#6366f1" stroke-width="1" opacity="0.55"/>`
               + `<polyline points="${mid.join(' ')}" fill="none" stroke="#6366f1" stroke-width="1" stroke-dasharray="3,3" opacity="0.7"/>`;
    }
  }

  // Oscillator panel (RSI / MACD / Stochastic / ROC / Aroon).
  let oscSvg = '';
  if (osc) {
    const oLo = osc.lo, oHi = osc.hi, rng = (oHi - oLo) || 1;
    const yO = v => yO1 - (v - oLo) / rng * HO;
    const guides = (osc.guides || []).map(g =>
      `<line x1="${PL}" y1="${yO(g).toFixed(1)}" x2="${W - PR}" y2="${yO(g).toFixed(1)}" stroke="currentColor" stroke-width="0.5" stroke-dasharray="3,3" opacity="0.22"/>`
      + `<text x="${AX}" y="${(yO(g) + 3.5).toFixed(1)}" class="adv-tick">${osc.fmt(g)}</text>`).join('');
    let histo = '';
    if (osc.bars) {
      const zero = yO(0);
      histo = osc.bars.map((v, i) => v == null ? '' :
        `<rect x="${(cx(i) - bw / 2).toFixed(2)}" y="${Math.min(zero, yO(v)).toFixed(2)}" width="${bw.toFixed(2)}" height="${Math.max(0.5, Math.abs(yO(v) - zero)).toFixed(2)}" fill="${v >= 0 ? UP : DN}" opacity="0.45"/>`).join('');
    }
    const lines = osc.series.map(sr => {
      const pts = [];
      sr.v.forEach((v, i) => { if (v != null) pts.push(`${cx(i).toFixed(2)},${yO(v).toFixed(2)}`); });
      return pts.length > 1 ? `<polyline points="${pts.join(' ')}" fill="none" stroke="${sr.c}" stroke-width="${sr.w}" stroke-linejoin="round"/>` : '';
    }).join('');
    const drew = osc.series.some(sr => sr.v.some(v => v != null));
    oscSvg = drew ? (guides + histo + lines)
      : `<text x="${PL + 2}" y="${(yO0 + HO / 2).toFixed(1)}" class="adv-tick" style="font-style:italic">`
        + `${escapeHtml(osc.label)} needs more history than the ${vis.length} bars in view — not computed</text>`;
  }

  const gridY = [0, 0.25, 0.5, 0.75, 1].map(f => {
    const v = useLog ? Math.exp(tHi - (tHi - tLo) * f) : lo + (hi - lo) * (1 - f);
    const y = yP0 + HP * f;
    // A tick hidden under a badge is just noise — drop its label, keep the line.
    const covered = badgeYs.some(by => Math.abs(by - y) < BH);
    return `<line x1="${PL}" y1="${y.toFixed(1)}" x2="${W - PR}" y2="${y.toFixed(1)}" stroke="currentColor" stroke-width="1" opacity="0.07"/>`
         + (covered ? '' : `<text x="${AX}" y="${(y + 3.5).toFixed(1)}" class="adv-tick">${_advFmtPx(v)}</text>`);
  }).join('');

  const nT = Math.min(7, n);
  const xTicks = Array.from({ length: nT }, (_, k) => {
    const i = Math.round(k * (n - 1) / (nT - 1 || 1));
    return `<text x="${cx(i).toFixed(1)}" y="${H - 8}" text-anchor="middle" class="adv-tick">${vis[i].d.slice(2).replace(/-/g, '/')}</text>`;
  }).join('');

  const title = (x, y, t) => `<text x="${x}" y="${y}" class="adv-ptitle">${t}</text>`;
  const sep = y => `<line x1="${PL}" y1="${y}" x2="${W - PR}" y2="${y}" stroke="currentColor" stroke-width="0.5" opacity="0.18"/>`;

  // Dotted earnings line. Clamped to the panel edge when off-scale so it stays
  // visible and is explicitly labelled, rather than silently vanishing.
  let epsPath = '', epsBadge = '';
  if (epsVals.length) {
    const pts = [];
    epsPlot.forEach((v, i) => {
      if (v == null) return;
      const y = Math.max(yP0 + 1, Math.min(yP1 - 1, yP(v)));
      pts.push(`${cx(i).toFixed(2)},${y.toFixed(2)}`);
    });
    if (pts.length > 1) {
      epsPath = `<polyline points="${pts.join(' ')}" fill="none" stroke="#b45309" stroke-width="1.5" `
              + `stroke-dasharray="3,3" stroke-linejoin="round" opacity="${epsOffScale ? 0.55 : 0.95}"/>`;
    }
  }
  // Mark each STEP of the earnings line — the point where a new quarterly
  // report took effect — with a dot and its TTM EPS value. The line is a step
  // function, so the steps are the only places anything actually happened;
  // labelling every bar would just repeat the same number.
  let epsSteps = '';
  if (epsVals.length) {
    const parts = [];
    let lastLabelX = -1e9;
    for (let i = 1; i < n; i++) {
      const cur = epsAtBar[i], prev = epsAtBar[i - 1];
      if (cur == null || prev == null || cur === prev) continue;
      const plotted = epsPlot[i];
      if (plotted == null) continue;
      const x = cx(i);
      const y = Math.max(yP0 + 2, Math.min(yP1 - 2, yP(plotted)));
      parts.push(`<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.6" fill="#b45309" stroke="var(--card, #fff)" stroke-width="0.8"/>`);
      // Labels are skipped when they would collide; the dot still marks the
      // step, so nothing is lost — the value is in the crosshair readout.
      if (x - lastLabelX >= 44) {
        lastLabelX = x;
        const up = cur >= prev;
        const ly = up ? Math.max(yP0 + 9, y - 6) : Math.min(yP1 - 3, y + 11);
        parts.push(`<text x="${x.toFixed(1)}" y="${ly.toFixed(1)}" text-anchor="middle" class="adv-eps-step">${cur.toFixed(2)}</text>`);
      }
    }
    epsSteps = parts.join('');
  }

  const lastEpsTtm = epsAtBar.length ? [...epsAtBar].reverse().find(v => v != null) : null;
  const curPE = (lastEpsTtm && lastEpsTtm > 0) ? (vis[n - 1].c / lastEpsTtm) : null;

  const keyed = maSeries.find(m => m.key);
  // An indicator that cannot be computed from the available history is not
  // drawn — but silence looks like "the line is off-screen". Mark it instead:
  // a 300-week average on 70 weeks of data should say so, not just vanish.
  const barsAvail = full.length;
  const unit = interval === 'W' ? 'w' : 'd';
  const legend = maSeries.map(m => {
    const ok = m.n <= barsAvail;
    return `<span class="adv-lg${ok ? '' : ' adv-lg-na'}"${ok ? '' : ` title="Needs ${m.n} ${interval === 'W' ? 'weeks' : 'sessions'} of history; only ${barsAvail} available, so this average is not drawn."`}>`
      + `<i style="background:${m.c}"></i>${m.n}${unit}${m.key ? ' ★' : ''}${ok ? '' : ' — n/a'}</span>`;
  }).join('')
    + (keyed ? `<span class="adv-lg-note">★ Weinstein ${interval === 'W' ? '30-week' : '150-day'} ${maType.toUpperCase()}</span>`
             : `<span class="adv-lg-note">custom periods — no Weinstein ${interval === 'W' ? '30w' : '150d'} line</span>`)
    + (idxPath ? `<span class="adv-lg"><i style="background:#0891b2"></i>${escapeHtml(benchLabel)} (rebased)</span>` : '')
    + (boll ? (boll.n <= barsAvail
        ? `<span class="adv-lg"><i style="background:#6366f1"></i>Bollinger ${boll.n}/${boll.k}</span>`
        : `<span class="adv-lg adv-lg-na" title="Needs ${boll.n} bars; only ${barsAvail} available.">Bollinger — n/a</span>`) : '')
    + (volN <= barsAvail
        ? `<span class="adv-lg"><i style="background:#1e3a8a"></i>Volume ${volN}-${interval === 'W' ? 'week' : 'day'} average</span>`
        : `<span class="adv-lg adv-lg-na" title="Needs ${volN} bars; only ${barsAvail} available.">Volume ${volN}-${interval === 'W' ? 'week' : 'day'} average — n/a</span>`)
    + (epsVals.length ? `<span class="adv-lg"><i style="background:#b45309"></i>Earnings ${MULT}× TTM EPS <span class="adv-lg-note">· dots = each quarterly report, labelled with TTM EPS</span></span>` : '')
    + (epsOffScale ? `<span class="adv-lg-note adv-lg-warn">earnings line off-scale at ${MULT}× (P/E ${curPE ? curPE.toFixed(0) : '—'}) — clamped to edge</span>` : '')
    + `<span class="adv-lg-note">${useLog ? 'log' : 'linear'} scale</span>`;

  // Earnings strength readout. Growth from a non-positive base is shown as a
  // turnaround flag, never a percentage — "+300%" off a loss is meaningless.
  const gTxt = (v, flag) => flag ? `<span class="adv-eps-flag">${flag}</span>`
    : (v == null ? '—' : `<b class="${v >= 0 ? 'stage-pos' : 'stage-neg'}">${v > 0 ? '+' : ''}${v}%</b>`);
  const epsStrip = epsD ? `<div class="adv-eps-strip">
      <span>${epsD.basis_label}${epsD.currency ? ` · ${epsD.currency}` : ''}</span>
      <span>TTM EPS <b>${epsD.latest_ttm != null ? epsD.latest_ttm.toFixed(2) : '—'}</b></span>
      <span>1y ${gTxt(epsD.growth_1y, epsD.growth_1y_flag)}</span>
      <span>2y ${gTxt(epsD.growth_2y, epsD.growth_2y_flag)}</span>
      <span>P/E <b>${curPE ? curPE.toFixed(1) : (epsD.negative_ttm ? 'n/a (loss)' : '—')}</b></span>
      <span class="adv-eps-note">${epsVals.length
        ? `line drawn at a fixed ${MULT}× for every stock, so the price-to-line gap is the P/E`
        : (epsD.negative_ttm
            ? 'no line drawn — a negative TTM EPS has no meaningful multiple'
            : 'no line drawn — not enough consecutive quarters')}</span>
    </div>` : '';

  const zoomBar = `
    <div class="adv-zoom">
      ${zoomed ? `<span class="adv-zoom-on">Zoomed · ${escapeHtml(vis[0].d)} → ${escapeHtml(vis[n - 1].d)}
          <span class="adv-zoom-n">${n} bars</span></span>
        <button type="button" class="adv-zoom-reset">Reset</button>`
        : `<span class="adv-zoom-hint">Scroll to zoom · drag to pan · double-click to reset</span>`}
    </div>`;

  wrap.innerHTML = `
    <div class="adv-legend">${legend}</div>
    ${zoomBar}
    ${epsStrip}
    <svg class="sc-price-svg adv-svg" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">
      ${gridY}
      ${bollPath}${candles}${idxPath}${maPaths}${epsPath}${epsSteps}${badges}
      ${sep(yV1 - 12)}${volBars}${volAvgPath}
      ${title(PL + 2, yV0 + 9, `Volume · ${volN}${interval === 'W' ? 'w' : 'd'} avg`)}<text x="${AX}" y="${yV0 + 9}" class="adv-tick">${_advFmtVol(vmax)}</text>
      ${volRate != null ? `<text x="${PL + 2}" y="${yV1 + 11}" class="adv-tick" style="font-weight:700;fill:${volRate >= 0 ? UP : DN}">${volRate >= 0 ? '+' : ''}${volRate}% vs avg${volRateNote}</text>` : ''}
      ${hasRS ? sep(yR1) + rsPath + rsAxis + rsBadge + title(PL + 2, yR0 + 9, 'RS vs ' + benchLabel) : ''}
      ${osc ? sep(yO1) + oscSvg + title(PL + 2, yO0 + 9, osc.label) : ''}
      ${xTicks}
      <line class="pc-hline" x1="0" y1="${yP0}" x2="0" y2="${osc ? yO1 : (hasRS ? yR1 : yV1)}" stroke="#64748b" stroke-width="1" stroke-dasharray="4,3" opacity="0"/>
      <rect class="pc-harea" x="${PL}" y="${yP0}" width="${plotW}" height="${(osc ? yO1 : (hasRS ? yR1 : yV1)) - yP0}" fill="transparent" style="cursor:grab"/>
    </svg>`;

  const svgEl = wrap.querySelector('.adv-svg');
  const hLine = svgEl.querySelector('.pc-hline');
  const hArea = svgEl.querySelector('.pc-harea');
  hArea.addEventListener('mousemove', e => {
    const r = svgEl.getBoundingClientRect();
    const sx = (e.clientX - r.left) / r.width * W;
    let i = Math.max(0, Math.min(n - 1, Math.floor((sx - PL) / step)));
    const b = vis[i];
    hLine.setAttribute('x1', cx(i)); hLine.setAttribute('x2', cx(i)); hLine.setAttribute('opacity', '0.6');
    const el = (wrap.closest('.sc-pricechart') || document).querySelector('#scAdvReadout')
             || document.getElementById('scAdvReadout');
    if (el) {
      const up = (b.c ?? 0) >= (b.o ?? b.c ?? 0);
      el.innerHTML = `<b>${b.d}</b> &nbsp;O <b>${_advFmtPx(b.o ?? b.c)}</b> &nbsp;H <b>${_advFmtPx(b.h ?? b.c)}</b> &nbsp;L <b>${_advFmtPx(b.l ?? b.c)}</b> `
        + `&nbsp;C <b style="color:${up ? UP : DN}">${_advFmtPx(b.c)}</b> &nbsp;· Vol <b>${_advFmtVol(b.v)}</b>`
        + (rs[i] != null ? ` &nbsp;· RS <b>${_advFmtRs(rs[i])}</b>` : '')
        + (idxRebased[i] != null ? ` &nbsp;· ${escapeHtml(benchLabel)} <b>${_advFmtPx(idxRebased[i])}</b>`
            + ` <b style="color:${b.c >= idxRebased[i] ? UP : DN}">(${b.c >= idxRebased[i] ? '+' : ''}${((b.c / idxRebased[i] - 1) * 100).toFixed(1)}% vs mkt)</b>` : '')
        + (epsReportAt[i] ? (() => { const R = epsReportAt[i];
            const sp = R.surprise == null ? '' :
              ` <b style="color:${R.surprise >= 0 ? UP : DN}">(${R.surprise >= 0 ? '+' : ''}${R.surprise}% vs est ${R.est ?? '—'})</b>`;
            const qv = (R.q == null || isNaN(+R.q)) ? '—' : (+R.q).toFixed(2);
            return ` &nbsp;· <b style="color:#b45309">REPORTED</b> Q EPS <b>${qv}</b>${sp}`; })() : '')
        + (epsAtBar[i] != null ? ` &nbsp;· TTM EPS <b>${epsAtBar[i].toFixed(2)}</b>`
            + (epsAtBar[i] > 0 ? ` &nbsp;· P/E <b>${(b.c / epsAtBar[i]).toFixed(1)}</b>` : ' <b>(loss)</b>') : '');
    }
  });
  hArea.addEventListener('mouseleave', () => {
    hLine.setAttribute('opacity', '0');
    const el = (wrap.closest('.sc-pricechart') || document).querySelector('#scAdvReadout')
             || document.getElementById('scAdvReadout');
    if (el) el.innerHTML = '';
  });

  // ── Zoom + pan ────────────────────────────────────────────────────────
  // Wheel zooms about the cursor, drag pans, double-click resets. Windows are
  // committed as dates so they survive the redraw and an interval switch.
  const applyWindow = (a, b) => {
    // Guard non-finite input. A wrap that is hidden or not yet laid out reports
    // a zero-width rect, which makes the cursor fraction NaN and propagates all
    // the way to full[NaN].d — a throw on every wheel event.
    if (!Number.isFinite(a) || !Number.isFinite(b)) return;
    a = Math.round(a); b = Math.round(b);
    if (b - a < _ADV_MIN_BARS) {                 // hold the floor, keep the anchor
      const mid = (a + b) / 2;
      a = Math.round(mid - _ADV_MIN_BARS / 2);
      b = a + _ADV_MIN_BARS;
    }
    // Clamp to the PERIOD window, not to the whole series. The period selector
    // defines the universe; zoom and pan move within it. Wheel-zoom already
    // stopped at this boundary, so letting a drag slide past it into older data
    // was the inconsistent half — a 1Y chart could be panned back to 2021.
    if (a < baseI0) { b += (baseI0 - a); a = baseI0; }
    if (b > full.length) { a -= (b - full.length); b = full.length; }
    a = Math.max(baseI0, Math.min(a, full.length - 1));
    b = Math.min(full.length, Math.max(b, a + 1));
    // Back at (or beyond) the period's own window means "not zoomed" — drop the
    // state rather than pinning an equivalent explicit range, so the Reset
    // affordance disappears when there is nothing left to reset.
    if (a <= baseI0 && b >= full.length) wrap._advZoom = null;
    else wrap._advZoom = { from: full[a].d, to: full[b - 1].d };
    wrap._advRedraw();
  };

  svgEl.addEventListener('wheel', e => {
    const r = svgEl.getBoundingClientRect();
    if (!r.width) return;                        // not laid out — nothing to anchor on
    e.preventDefault();
    const frac = Math.max(0, Math.min(1, ((e.clientX - r.left) / r.width * W - PL) / plotW));
    const anchor = i0 + frac * n;                // bar under the cursor stays put
    const len = Math.max(_ADV_MIN_BARS,
                         Math.round(n * (e.deltaY > 0 ? _ADV_ZOOM_STEP : 1 / _ADV_ZOOM_STEP)));
    if (e.deltaY > 0 && len >= full.length - baseI0) { wrap._advZoom = null; wrap._advRedraw(); return; }
    applyWindow(anchor - frac * len, anchor - frac * len + len);
  }, { passive: false });

  hArea.addEventListener('mousedown', e => {
    if (e.button !== 0 || wrap._advDrag) return;
    e.preventDefault();
    const r = svgEl.getBoundingClientRect();
    if (!r.width) return;
    const perPx = n / (plotW * r.width / W);     // bars per screen pixel
    const st = { x: e.clientX, i0, i1, perPx, moved: false };
    wrap._advDrag = st;
    svgEl.style.cursor = 'grabbing';
    const onMove = ev => {
      const dBars = Math.round((st.x - ev.clientX) * st.perPx);
      if (!dBars && !st.moved) return;
      st.moved = true;
      // Let applyWindow clamp at the edges — it shifts the window while
      // preserving its length, which is what a drag into the boundary should do.
      // Rejecting the gesture outright (the first version did) silently killed
      // every drag whose delta overshot the data, so panning toward the most
      // recent bar did nothing at all.
      applyWindow(st.i0 + dBars, st.i1 + dBars);  // redraw replaces the DOM; these
    };                                            // listeners are on window, so
    const onUp = () => {                          // they survive it
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      wrap._advDrag = null;
      const s = wrap.querySelector('.adv-svg');
      if (s) s.style.cursor = '';
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  });

  svgEl.addEventListener('dblclick', () => {
    if (!wrap._advZoom) return;
    wrap._advZoom = null;
    wrap._advRedraw();
  });

  const rst = wrap.querySelector('.adv-zoom-reset');
  if (rst) rst.addEventListener('click', () => { wrap._advZoom = null; wrap._advRedraw(); });
}

function setupPriceChart(container, prices, beta, ticker, bench, benchSym, benchName) {
  const chartWrap = container.querySelector('#scChartWrap');
  // Advanced-chart state. Default stays 'line' so the existing behaviour is
  // untouched until the user opts in.
  let chartMode = 'candle', advInterval = 'W', advMaType = 'sma';
  let advOsc = 'none', advBoll = false;
  const benchMap = {};
  (bench || []).forEach(b => { if (b && b.d != null && b.c) benchMap[b.d] = b.c; });
  const benchSymbol = benchSym || 'SPY';
  // Earnings line data. Fetched ASYNC and drawn when it lands, so the ~3s
  // yfinance earnings call never blocks the chart from rendering.
  let advEpsData = null;
  const advMarket = /^\d{6}\.(SZ|SH|BJ)$/i.test(ticker || '') ? 'cn' : 'us';
  // Human-readable benchmark label — a bare code like "000985.SH" means
  // nothing on screen, so prefer the name the backend supplies.
  const benchLabel = benchName || benchSym || 'SPY';
  // MA periods are per-interval: sensible defaults differ for daily vs weekly.
  const advPeriods = { D: [50, 150, 200], W: [10, 30, 40] };
  const badge = container.querySelector('#scPriceChgBadge');
  const hoverRow = container.querySelector('#scChartHoverRow');
  const ttDate = container.querySelector('#scTtDate');
  const ttPrice = container.querySelector('#scTtPrice');
  const ttChg = container.querySelector('#scTtChg');
  const dailyVolVal = container.querySelector('#scDailyVolVal');
  const volVal = container.querySelector('#scVolVal');
  const volBadge = container.querySelector('#scVolBadge');
  const ddVal = container.querySelector('#scDdVal');
  const betaVal = container.querySelector('#scBetaVal');
  const betaBadge = container.querySelector('#scBetaBadge');
  const vixToggleBtn = container.querySelector('#scVixToggle');
  const ttVix = container.querySelector('#scTtVix');
  const livePriceEl = container.querySelector('#scLivePrice');
  const liveDot = container.querySelector('#scLiveDot');
  const liveVal = container.querySelector('#scLiveVal');
  const liveChg = container.querySelector('#scLiveChg');

  // ── Live price polling ─────────────────────────────────────────────────
  let liveTimer = null;
  function isMarketOpen() {
    const now = new Date();
    const et = new Date(now.toLocaleString('en-US', { timeZone: 'America/New_York' }));
    const day = et.getDay(); // 0=Sun 6=Sat
    if (day === 0 || day === 6) return false;
    const mins = et.getHours() * 60 + et.getMinutes();
    return mins >= 570 && mins < 960; // 9:30am–4:00pm ET
  }

  async function fetchLivePrice() {
    if (!ticker) return;
    try {
      const res = await fetch(`/api/live_price?ticker=${encodeURIComponent(ticker)}`);
      const json = await res.json();
      if (json.price == null || !livePriceEl) return;
      const chgPct = json.prev_close ? (json.price - json.prev_close) / json.prev_close * 100 : null;
      const up = chgPct != null ? chgPct >= 0 : null;
      liveVal.textContent = `$${json.price.toFixed(2)}`;
      if (chgPct != null) {
        liveChg.textContent = `${up ? '+' : ''}${chgPct.toFixed(2)}%`;
        liveChg.className = 'sc-live-chg ' + (up ? 'sc-live-up' : 'sc-live-dn');
      } else {
        liveChg.textContent = '';
      }
      const open = isMarketOpen();
      liveDot.className = 'sc-live-dot ' + (open ? 'sc-live-dot-open' : 'sc-live-dot-closed');
      liveDot.title = open ? 'Market open' : 'Market closed';
      livePriceEl.style.display = 'flex';
    } catch (e) { /* silently ignore */ }
  }

  function startLivePolling() {
    if (!ticker) return;
    fetchLivePrice();
    liveTimer = setInterval(fetchLivePrice, 30000);
  }

  // Stop polling when container is removed from DOM
  const liveObserver = new MutationObserver(() => {
    if (!document.contains(container)) {
      if (liveTimer) clearInterval(liveTimer);
      liveObserver.disconnect();
    }
  });
  liveObserver.observe(document.body, { childList: true, subtree: true });

  startLivePolling();

  // VIX overlay state
  let showVix = false;
  let vixCache = null;

  async function fetchVix() {
    if (vixCache !== null) return vixCache;
    try {
      const res = await fetch('/api/vix_history');
      const json = await res.json();
      vixCache = json.vix || [];
    } catch(e) { vixCache = []; }
    return vixCache;
  }

  function filterVixToWindow(vixData, data) {
    if (!vixData || !data.length) return [];
    const lo = data[0].d, hi = data[data.length - 1].d;
    return vixData.filter(p => p.d >= lo && p.d <= hi);
  }

  if (!chartWrap) return;
  if (!prices || prices.length < 2) {
    chartWrap.innerHTML = '<p class="sc-chart-empty">No price history available.</p>';
    return;
  }

  // ── Volatility helpers ──────────────────────────────────────────────────
  function dailyReturnsStd(data) {
    if (data.length < 5) return null;
    const rets = [];
    for (let i = 1; i < data.length; i++) rets.push(Math.log(data[i].c / data[i-1].c));
    const mean = rets.reduce((a, b) => a + b, 0) / rets.length;
    const variance = rets.reduce((s, r) => s + (r - mean) ** 2, 0) / (rets.length - 1);
    return Math.sqrt(variance);
  }

  function annualizedVol(data) {
    const std = dailyReturnsStd(data);
    return std != null ? std * Math.sqrt(252) * 100 : null;
  }

  function maxDrawdown(data) {
    if (data.length < 2) return null;
    let peak = data[0].c, maxDD = 0;
    for (const p of data) {
      if (p.c > peak) peak = p.c;
      const dd = (p.c - peak) / peak * 100;
      if (dd < maxDD) maxDD = dd;
    }
    return maxDD;
  }

  function volLabel(v) {
    if (v == null) return ['—', ''];
    if (v < 15)  return [v.toFixed(1) + '%', 'vol-low'];
    if (v < 25)  return [v.toFixed(1) + '%', 'vol-mod'];
    if (v < 40)  return [v.toFixed(1) + '%', 'vol-high'];
    return [v.toFixed(1) + '%', 'vol-vhigh'];
  }
  const VOL_LABELS = { 'vol-low': 'Low', 'vol-mod': 'Moderate', 'vol-high': 'High', 'vol-vhigh': 'Very High' };

  function betaLabel(b) {
    if (b == null) return ['—', ''];
    if (b < 0.5)  return [b.toFixed(2), 'beta-low'];
    if (b < 1.0)  return [b.toFixed(2), 'beta-def'];
    if (b < 1.5)  return [b.toFixed(2), 'beta-above'];
    return [b.toFixed(2), 'beta-high'];
  }
  const BETA_LABELS = { 'beta-low': 'Defensive', 'beta-def': 'Market-like', 'beta-above': 'Above market', 'beta-high': 'Aggressive' };

  function updateVolStats(data) {
    const dailyStd = dailyReturnsStd(data);
    const vol = dailyStd != null ? dailyStd * Math.sqrt(252) * 100 : null;
    const dd  = maxDrawdown(data);
    if (dailyVolVal) dailyVolVal.textContent = dailyStd != null ? (dailyStd * 100).toFixed(2) + '%' : '—';
    const [volTxt, volCls] = volLabel(vol);
    if (volVal)   volVal.textContent = volTxt;
    if (volBadge) { volBadge.textContent = VOL_LABELS[volCls] || ''; volBadge.className = 'sc-vs-badge ' + volCls; }
    if (ddVal)    { ddVal.textContent = dd != null ? dd.toFixed(1) + '%' : '—'; }

    // Beta (static — doesn't change with period)
    const [betaTxt, betaCls] = betaLabel(beta);
    if (betaVal)   betaVal.textContent = betaTxt;
    if (betaBadge) { betaBadge.textContent = BETA_LABELS[betaCls] || ''; betaBadge.className = 'sc-vs-badge ' + betaCls; }
  }

  function filterByPeriod(period) {
    const now = new Date();
    let cutoff;
    if      (period === '1M')  { cutoff = new Date(now); cutoff.setMonth(now.getMonth() - 1); }
    else if (period === '3M')  { cutoff = new Date(now); cutoff.setMonth(now.getMonth() - 3); }
    else if (period === '6M')  { cutoff = new Date(now); cutoff.setMonth(now.getMonth() - 6); }
    else if (period === 'YTD') { cutoff = new Date(now.getFullYear(), 0, 1); }
    else if (period === '1Y')  { cutoff = new Date(now); cutoff.setFullYear(now.getFullYear() - 1); }
    else if (period === '3Y')  { cutoff = new Date(now); cutoff.setFullYear(now.getFullYear() - 3); }
    else                       { cutoff = new Date(now); cutoff.setFullYear(now.getFullYear() - 5); }
    return prices.filter(p => new Date(p.d + 'T00:00:00') >= cutoff);
  }

  function fmtAxisDate(str, period) {
    const d = new Date(str + 'T00:00:00');
    if (period === '1M' || period === '3M')             return d.toLocaleDateString('en-US', {month:'short', day:'numeric'});
    if (period === '6M' || period === 'YTD' || period === '1Y') return d.toLocaleDateString('en-US', {month:'short', year:'2-digit'});
    return d.toLocaleDateString('en-US', {month:'short', year:'numeric'});
  }
  function fmtPrice(v) { return v >= 1000 ? `$${(v/1000).toFixed(2)}k` : `$${v.toFixed(2)}`; }

  function drawChart(data, period) {
    if (data.length < 2) {
      chartWrap.innerHTML = '<p class="sc-chart-empty">Not enough data for this period.</p>';
      if (badge) { badge.textContent = ''; badge.className = 'sc-price-chg-badge'; }
      return;
    }

    // Candle mode renders the 3-panel advanced chart instead. It needs the FULL
    // series (moving averages must warm up on data preceding the visible
    // window) plus the window start date, so it takes `prices`, not `data`.
    if (chartMode === 'candle') {
      drawAdvancedChart(chartWrap, prices, benchMap, {
        interval: advInterval, maType: advMaType,
        fromDate: data[0] && data[0].d, benchSymbol, benchLabel,
        zoomKey: ticker,                  // a new stock is a new view, not a pan
        periods: advPeriods[advInterval],
        epsData: advEpsData, osc: advOsc, boll: advBoll,
        volMa: parseInt(document.getElementById("scVolMa")?.value, 10) || null,
      });
      return;
    }

    // Expand right padding when VIX axis is visible
    const vixSeries = showVix ? filterVixToWindow(vixCache, data) : null;
    const hasVix = vixSeries && vixSeries.length > 1;

    const W = 680, H = 160, PL = 54, PT = 8, PB = 26;
    const PR = hasVix ? 46 : 10;
    const plotW = W - PL - PR, plotH = H - PT - PB;
    const closes = data.map(p => p.c);
    const minC = Math.min(...closes), maxC = Math.max(...closes);
    const pad = (maxC - minC) * 0.06 || 1;
    const lo = minC - pad * 0.2, hi = maxC + pad;
    const rng = hi - lo;

    const xOf = i => PL + (i / (data.length - 1)) * plotW;
    const yOf = c => PT + plotH - ((c - lo) / rng) * plotH;
    const pts = data.map((p, i) => [xOf(i), yOf(p.c)]);

    const linePath = 'M ' + pts.map(([x,y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' L ');
    const areaPath =
      `M ${PL},${PT + plotH} L ` +
      pts.map(([x,y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' L ') +
      ` L ${pts[pts.length-1][0].toFixed(1)},${PT + plotH} Z`;

    const first = data[0].c, last = data[data.length-1].c;
    const chgPct = (last - first) / first * 100;
    const isUp = chgPct >= 0;
    const col = isUp ? '#059669' : '#dc2626';

    if (badge) {
      badge.textContent = `${isUp?'+':''}${chgPct.toFixed(1)}%`;
      badge.className = 'sc-price-chg-badge ' + (isUp ? 'chg-up' : 'chg-down');
    }

    updateVolStats(data);

    // Y-axis labels
    const fmtY = v => v >= 1000 ? `$${(v/1000).toFixed(1)}k` : `$${v.toFixed(0)}`;
    const yTicks = [minC, (minC + maxC) / 2, maxC].map(v =>
      `<text x="${PL-5}" y="${yOf(v).toFixed(1)}" text-anchor="end" dominant-baseline="middle" font-size="10" fill="#9ca3af">${fmtY(v)}</text>`
    ).join('');

    // X-axis labels (5 ticks)
    const xIdxs = [0, Math.round(data.length*0.25), Math.round(data.length*0.5), Math.round(data.length*0.75), data.length-1];
    const xTicks = xIdxs.map(i => {
      const ci = Math.min(i, data.length - 1);
      return `<text x="${xOf(ci).toFixed(1)}" y="${H - 6}" text-anchor="middle" font-size="10" fill="#9ca3af">${fmtAxisDate(data[ci].d, period)}</text>`;
    }).join('');

    // ── VIX overlay ────────────────────────────────────────────────────────
    let vixZones = '', vixLine = '', vixRightAxis = '', vixByDate = {};
    if (hasVix) {
      const loMs = new Date(data[0].d).getTime();
      const hiMs = new Date(data[data.length - 1].d).getTime();
      const msRange = hiMs - loMs || 1;
      const xVixOf = d => PL + (new Date(d).getTime() - loMs) / msRange * plotW;

      const vixVals = vixSeries.map(p => p.v);
      const vixMin = Math.min(...vixVals), vixMax = Math.max(...vixVals);
      const vixPad = (vixMax - vixMin) * 0.08 || 2;
      const vixLo = Math.max(0, vixMin - vixPad), vixHi = vixMax + vixPad;
      const vixRng = vixHi - vixLo;
      const yVixOf = v => PT + plotH - ((v - vixLo) / vixRng) * plotH;

      // Build date lookup for hover
      vixByDate = Object.fromEntries(vixSeries.map(p => [p.d, p.v]));

      // Horizontal fear-zone bands (behind stock chart)
      const CALM = 15, ELEV = 25;
      function zoneRect(vLo, vHi, color) {
        const y1 = Math.min(yVixOf(vHi), PT + plotH);
        const y2 = Math.max(yVixOf(vLo), PT);
        const h = y2 - y1;
        if (h <= 0) return '';
        return `<rect x="${PL}" y="${y1.toFixed(1)}" width="${plotW}" height="${h.toFixed(1)}" fill="${color}" opacity="0.05"/>`;
      }
      vixZones = [
        zoneRect(0, CALM, '#10b981'),
        zoneRect(CALM, ELEV, '#f59e0b'),
        zoneRect(ELEV, 100, '#ef4444'),
      ].join('');

      // Reference lines at 15 and 25
      const refLines = [
        [CALM,  '#f59e0b', 0.35],
        [ELEV,  '#ef4444', 0.35],
      ].map(([v, stroke, op]) => {
        const ry = yVixOf(v);
        if (ry < PT || ry > PT + plotH) return '';
        return `<line x1="${PL}" y1="${ry.toFixed(1)}" x2="${PL + plotW}" y2="${ry.toFixed(1)}" stroke="${stroke}" stroke-width="0.7" stroke-dasharray="3,3" opacity="${op}"/>`;
      }).join('');

      // VIX path
      const vixPts = vixSeries.map(p => [xVixOf(p.d), yVixOf(p.v)]);
      const vixPath = 'M ' + vixPts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' L ');
      vixLine = `${refLines}<path d="${vixPath}" fill="none" stroke="#f59e0b" stroke-width="1.5" stroke-dasharray="5,2.5" stroke-linejoin="round" stroke-linecap="round" opacity="0.9"/>`;

      // Right Y-axis labels
      const vixTickPts = [vixMin, (vixMin + vixMax) / 2, vixMax];
      const vixTickSvg = vixTickPts.map(v => {
        const y = yVixOf(v);
        if (y < PT - 2 || y > PT + plotH + 2) return '';
        return `<text x="${W - PR + 5}" y="${y.toFixed(1)}" text-anchor="start" dominant-baseline="middle" font-size="9" fill="#f59e0b" opacity="0.8">${v.toFixed(0)}</text>`;
      }).join('');
      vixRightAxis = `<text x="${W - PR + 5}" y="${PT + 4}" text-anchor="start" font-size="8" fill="#f59e0b" opacity="0.65" font-weight="600">VIX</text>${vixTickSvg}`;
    }

    const gid = 'pcg' + period.replace(/[^a-z0-9]/gi,'');
    chartWrap.innerHTML = `
    <svg class="sc-price-svg" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${col}" stop-opacity="0.20"/>
          <stop offset="100%" stop-color="${col}" stop-opacity="0.01"/>
        </linearGradient>
      </defs>
      ${vixZones}
      <path d="${areaPath}" fill="url(#${gid})"/>
      <path d="${linePath}" fill="none" stroke="${col}" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/>
      ${vixLine}
      ${yTicks}${xTicks}${vixRightAxis}
      <line class="pc-hline" x1="0" y1="${PT}" x2="0" y2="${PT+plotH}" stroke="${col}" stroke-width="1" stroke-dasharray="4,3" opacity="0"/>
      <circle class="pc-hdot" cx="0" cy="0" r="4" fill="${col}" stroke="white" stroke-width="1.5" opacity="0"/>
      <rect class="pc-harea" x="${PL}" y="${PT}" width="${plotW}" height="${plotH}" fill="transparent" style="cursor:crosshair"/>
    </svg>`;

    // Hover
    const svg   = chartWrap.querySelector('.sc-price-svg');
    const hLine = svg.querySelector('.pc-hline');
    const hDot  = svg.querySelector('.pc-hdot');
    const hArea = svg.querySelector('.pc-harea');

    hArea.addEventListener('mousemove', e => {
      const rect = svg.getBoundingClientRect();
      const svgX = (e.clientX - rect.left) / rect.width * W;
      const idx = Math.round((svgX - PL) / plotW * (data.length - 1));
      const ci = Math.max(0, Math.min(data.length - 1, idx));
      const [px, py] = pts[ci];

      hLine.setAttribute('x1', px.toFixed(1)); hLine.setAttribute('x2', px.toFixed(1));
      hLine.setAttribute('opacity', '0.45');
      hDot.setAttribute('cx', px.toFixed(1)); hDot.setAttribute('cy', py.toFixed(1));
      hDot.setAttribute('opacity', '1');

      if (hoverRow) {
        const dp = data[ci];
        const dc = dp.c - first;
        const dp_pct = dc / first * 100;
        const dpUp = dc >= 0;
        hoverRow.style.display = 'flex';
        ttDate.textContent = new Date(dp.d + 'T00:00:00').toLocaleDateString('en-US', {weekday:'short', month:'short', day:'numeric', year:'numeric'});
        ttPrice.textContent = fmtPrice(dp.c);
        ttChg.textContent = `${dpUp?'+':''}${dp_pct.toFixed(1)}%`;
        ttChg.className = 'sc-tt-chg ' + (dpUp ? 'tt-up' : 'tt-down');
        if (ttVix) {
          const vv = vixByDate[dp.d];
          if (hasVix && vv != null) {
            const vixColor = vv < 15 ? '#10b981' : vv < 25 ? '#f59e0b' : '#ef4444';
            ttVix.style.display = 'inline';
            ttVix.style.color = vixColor;
            ttVix.textContent = `VIX ${vv.toFixed(1)}`;
          } else {
            ttVix.style.display = 'none';
          }
        }
      }
    });
    hArea.addEventListener('mouseleave', () => {
      hLine.setAttribute('opacity', '0'); hDot.setAttribute('opacity', '0');
      if (hoverRow) hoverRow.style.display = 'none';
    });
  }

  container.querySelectorAll('.sc-period-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      container.querySelectorAll('.sc-period-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      drawChart(filterByPeriod(btn.dataset.period), btn.dataset.period);
    });
  });

  // ── Advanced-chart toggles ────────────────────────────────────────────────
  function activePeriod() {
    const b = container.querySelector('.sc-period-btn.active');
    return b ? b.dataset.period : _ADV_DEFAULT_PERIOD;
  }
  function redraw() { const p = activePeriod(); drawChart(filterByPeriod(p), p); }
  function pickGroup(sel, btn) {
    container.querySelectorAll(sel).forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
  }
  const advOpts = container.querySelector('#scAdvOpts');
  const advReadout = container.querySelector('#scAdvReadout');
  container.querySelectorAll('[data-advmode]').forEach(btn => {
    btn.addEventListener('click', () => {
      chartMode = btn.dataset.advmode;
      pickGroup('[data-advmode]', btn);
      if (advOpts) advOpts.style.display = chartMode === 'candle' ? '' : 'none';
      if (advReadout && chartMode !== 'candle') advReadout.innerHTML = '';
      // The VIX overlay belongs to the line chart only.
      if (vixToggleBtn) vixToggleBtn.style.display = chartMode === 'candle' ? 'none' : '';
      redraw();
    });
  });
  function syncMaInputs() {
    advPeriods[advInterval].forEach((n, i) => {
      const el = container.querySelector('#scMa' + i);
      if (el) el.value = n;
    });
  }
  syncMaInputs();
  container.querySelectorAll('[data-advint]').forEach(btn => {
    btn.addEventListener('click', () => {
      advInterval = btn.dataset.advint; pickGroup('[data-advint]', btn);
      syncMaInputs();          // daily and weekly keep their own period set
      syncVolMaPlaceholder();  // the volume default differs by interval too
      redraw();
    });
  });
  [0, 1, 2].forEach(i => {
    const el = container.querySelector('#scMa' + i);
    if (!el) return;
    el.addEventListener('change', () => {
      const v = parseInt(el.value, 10);
      if (!Number.isFinite(v) || v < 2 || v > 400) { syncMaInputs(); return; }  // reject, restore
      advPeriods[advInterval][i] = v;
      redraw();
    });
  });
  container.querySelectorAll('[data-advma]').forEach(btn => {
    btn.addEventListener('click', () => { advMaType = btn.dataset.advma; pickGroup('[data-advma]', btn); redraw(); });
  });
  // The volume-MA default differs by interval (50 daily / 10 weekly), so the
  // placeholder has to follow the interval or it advertises the wrong fallback.
  function syncVolMaPlaceholder() {
    const el = container.querySelector('#scVolMa');
    if (el) el.placeholder = advInterval === 'W' ? '10' : '50';
  }

  const volMaEl = container.querySelector('#scVolMa');
  if (volMaEl) volMaEl.addEventListener('change', redraw);
  syncVolMaPlaceholder();
  const oscSel = container.querySelector('#scOscSel');
  if (oscSel) oscSel.addEventListener('change', () => { advOsc = oscSel.value; redraw(); });
  const bollBtn = container.querySelector('#scBollBtn');
  if (bollBtn) bollBtn.addEventListener('click', () => {
    advBoll = !advBoll; bollBtn.classList.toggle('active', advBoll); redraw();
  });

  if (vixToggleBtn) {
    vixToggleBtn.addEventListener('click', async () => {
      showVix = !showVix;
      vixToggleBtn.classList.toggle('active', showVix);
      if (showVix && vixCache === null) {
        vixToggleBtn.textContent = '…';
        await fetchVix();
        vixToggleBtn.textContent = 'VIX';
      }
      const activeBtn = container.querySelector('.sc-period-btn.active');
      const activePeriod = activeBtn ? activeBtn.dataset.period : '1M';
      drawChart(filterByPeriod(activePeriod), activePeriod);
    });
  }

  drawChart(filterByPeriod(_ADV_DEFAULT_PERIOD), _ADV_DEFAULT_PERIOD);

  // Earnings line arrives after the chart is already on screen.
  (async () => {
    if (!ticker) return;
    try {
      const d = await fetchJson(`/api/earnings_line?market=${advMarket}&symbol=${encodeURIComponent(ticker)}`);
      // Only redraw if the earnings line would actually be shown; in line mode
      // it is not drawn, and the stored data is picked up on the next switch.
      if (d && d.status === 'ready') { advEpsData = d; if (chartMode === 'candle') redraw(); }
    } catch (_) { /* chart stands on its own without it */ }
  })();
}




// ── Listing basis banner ────────────────────────────────────────────────────
// Surfaces what fx_normalize.py did to put a foreign listing on one basis, and
// any consistency check that failed. Silent conversion would be as misleading
// as no conversion: the reader needs to know the numbers were adjusted.
function renderListingBasis(lb) {
  if (!lb || (!(lb.notes || []).length && !(lb.warnings || []).length)) return "";
  const warn = (lb.warnings || []).map(w => `<li class="lb-warn">⚠ ${escapeHtml(w)}</li>`).join("");
  const notes = (lb.notes || []).map(n => `<li>${escapeHtml(n)}</li>`).join("");
  const div = lb.dividend_basis && lb.dividend_basis.ok && lb.dividend_basis.payments
    ? `<li>Dividend yield from payments actually made per trading unit (${escapeHtml(lb.dividend_basis.note || "")}).</li>` : "";
  return `<div class="lb-banner${warn ? " lb-banner-warn" : ""}">
    <div class="lb-title">${warn ? "Data basis — check before relying on these numbers" : "Data basis — adjusted for this listing"}</div>
    <ul>${warn}${notes}${div}</ul></div>`;
}

// ── Same-quarter growth, quarter by quarter (under Gate 1) ─────────────────
const _QG_REASON = {
  negative_base:  "n/m — the year-ago quarter was a loss",
  turned_to_loss: "n/m — swung from profit to loss",
  restatement_mix: "n/a — the quarter spans a restatement, so it cannot be derived on one accounting basis",
  negative_discrete_revenue: "n/a — filings are inconsistent (derived quarterly revenue would be negative)",
  missing_prior_filing: "n/a — a year-to-date filing needed to derive this quarter is missing (common for recent listings)",
  superseded_by_restatement: "n/a — the company later restated this year's figures, and the quarter on file is the superseded one",
  units_suspect: "n/a — the reported EPS and the consensus look like different units in the vendor's earnings calendar",
  no_prior_period: "n/a — no year-ago quarter on file",
};

function renderQuarterlyGrowth(qg, currency) {
  if (!qg || !(qg.rows || []).length) return "";
  const cur = currency === "¥" ? "¥" : "$";
  const fmtEps = v => v == null ? "—" : `${cur}${(+v).toFixed(2)}`;
  const fmtRev = v => v == null ? "—" : v >= 1e9 ? `${cur}${(v / 1e9).toFixed(2)}B` : `${cur}${(v / 1e6).toFixed(0)}M`;
  const yoy = (v, reason, trend) => {
    if (v == null) {
      return reason ? `<span class="qg-nm" title="${escapeHtml(_QG_REASON[reason] || reason)}">${reason.startsWith("negative_base") || reason === "turned_to_loss" ? "n/m" : "n/a"}</span>` : "—";
    }
    const arrow = trend === "up" ? `<span class="qg-tr qg-tr-up" title="Faster than the previous quarter's YoY">▲</span>`
                : trend === "down" ? `<span class="qg-tr qg-tr-down" title="Slower than the previous quarter's YoY">▼</span>` : "";
    return `<span class="${v >= 0 ? "qg-pos" : "qg-neg"}">${v > 0 ? "+" : ""}${v.toFixed(1)}%</span>${arrow}`;
  };
  // A tiny bar per quarter makes acceleration visible at a glance.
  const vals = qg.rows.map(r => r.eps_yoy).filter(v => v != null);
  const span = Math.max(1, ...vals.map(v => Math.abs(v)));
  const bar = r => r.eps_yoy == null ? "" :
    `<span class="qg-bar ${r.eps_yoy >= 0 ? "qg-bar-pos" : "qg-bar-neg"}" style="width:${Math.max(2, Math.abs(r.eps_yoy) / span * 60).toFixed(0)}px"></span>`;
  const rows = qg.rows.map(r => `
    <tr class="${r.restated ? "qg-restated" : ""}">
      <td class="qg-q"><b>${escapeHtml(r.label)}</b>${r.restated ? ` <span class="qg-flag" title="This comparison straddles a restatement — the two quarters are on different accounting bases. Read with caution.">⚠ restated</span>` : ""}</td>
      <td class="qg-n">${fmtEps(r.eps)}</td>
      <td class="qg-n qg-dim">${fmtEps(r.eps_prior)}</td>
      <td class="qg-n">${yoy(r.eps_yoy, r.eps_yoy_reason, r.eps_yoy_trend)}</td>
      <td class="qg-barcell">${bar(r)}</td>
      <td class="qg-n">${fmtRev(r.rev)}</td>
      <td class="qg-n">${yoy(r.rev_yoy, r.rev_yoy_reason, r.rev_yoy_trend)}</td>
    </tr>`).join("");
  return `
  <div class="sc-card qg-card">
    <div class="sc-card-header">
      <div class="sc-card-title">Growth, quarter by quarter</div>
      <span class="fund-tag">each quarter vs the same quarter a year earlier</span>
    </div>
    <div class="qg-wrap">
      <p class="sc-card-sub qg-sub-line">EPS and revenue against the year-ago quarter, on the
        company's own fiscal calendar. ▲▼ marks a quarter growing faster or slower than the one
        before it — the shape of an acceleration, which the levels above do not show.</p>
      <div class="qg-table-wrap">
        <table class="qg-table">
          <thead><tr><th>Quarter</th><th class="qg-n">EPS</th><th class="qg-n">Year-ago</th><th class="qg-n">EPS YoY</th><th></th>
            <th class="qg-n">Revenue</th><th class="qg-n">Rev YoY</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      <details class="qg-basis"><summary>Basis and limits</summary><ul>
        ${Object.entries(qg.basis || {}).map(([k, v]) => `<li><b>${escapeHtml(k)}:</b> ${escapeHtml(v)}</li>`).join("")}
        ${(qg.limits || []).map(x => `<li>${escapeHtml(x)}</li>`).join("")}
      </ul></details>
    </div>
  </div>`;
}

// ── Peer Comparison ─────────────────────────────────────────────────────────
// Key ratios for the stock and its industry peers, every row on ONE basis.
// See product/peers.py for why margins are recomputed rather than taken from
// vendor summary fields.
const _PEER_COLS = {
  market_cap:    { lbl: "Mkt cap",        kind: "cap" },
  rev_growth_q:  { lbl: "Rev growth",     kind: "pct", better: "high" },
  eps_growth_q:  { lbl: "EPS growth",     kind: "pct", better: "high" },
  eps_growth_fy: { lbl: "EPS growth FY",  kind: "pct", better: "high" },
  gross_margin:  { lbl: "Gross margin",   kind: "pct", better: "high" },
  op_margin:     { lbl: "Op. margin",     kind: "pct", better: "high" },
  net_margin:    { lbl: "Net margin",     kind: "pct", better: "high" },
  roe:           { lbl: "ROE",            kind: "pct", better: "high" },
  roe_fy:        { lbl: "ROE",            kind: "pct", better: "high" },
  pe_ttm:        { lbl: "P/E",            kind: "x" },
  pe_fwd:        { lbl: "Fwd P/E",        kind: "x" },
};
const _PEER_REASON = {
  negative_base:   "n/m — prior period was a loss, so a growth percentage is meaningless",
  turned_to_loss:  "n/m — swung from profit to loss, so a growth percentage is meaningless",
  negative_equity: "n/m — shareholders' equity is negative (typically from buybacks), so ROE is meaningless",
  restated:        "withheld — the year-ago period was restated, so growth would compare two different accounting bases",
};

function _peerStoreKey(market, subject) { return `peers:${market}:${subject}`; }
function _peerPrefs(market, subject) {
  try { return JSON.parse(localStorage.getItem(_peerStoreKey(market, subject)) || "{}"); }
  catch { return {}; }
}
function _peerSave(market, subject, prefs) {
  try { localStorage.setItem(_peerStoreKey(market, subject), JSON.stringify(prefs)); } catch {}
}

function renderPeerCard(d) {
  const market = d.currency === "¥" ? "cn" : "us";
  return `
  <div class="sc-card peer-card" data-market="${market}" data-ticker="${escapeHtml(d.ticker)}">
    <div class="sc-card-header">
      <div class="sc-card-title">Peer Comparison</div>
      <span class="fund-tag">key financials vs industry peers</span>
    </div>
    <div class="peer-body"><div class="sc-loading">Loading peers…</div></div>
  </div>`;
}

function _peerFmt(r, col, currency) {
  const v = r[col];
  const kind = _PEER_COLS[col].kind;
  if (v == null) {
    const reason = r[col + "_reason"] || (col === "roe" ? r.roe_reason : null);
    return reason ? `<span class="peer-nm" title="${escapeHtml(_PEER_REASON[reason] || reason)}">${reason === "restated" ? "n/a" : "n/m"}</span>` : "—";
  }
  if (kind === "cap") {
    const s = currency === "¥" ? "¥" : "$";
    return v >= 1e12 ? `${s}${(v / 1e12).toFixed(2)}T` : v >= 1e9 ? `${s}${(v / 1e9).toFixed(1)}B` : `${s}${(v / 1e6).toFixed(0)}M`;
  }
  if (kind === "x") return v <= 0 ? "—" : `${v.toFixed(1)}×`;
  return `${v > 0 && col.includes("growth") ? "+" : ""}${v.toFixed(1)}%`;
}

async function loadPeerCard(card) {
  const market = card.dataset.market, ticker = card.dataset.ticker;
  const body = card.querySelector(".peer-body");
  const prefs = _peerPrefs(market, ticker.toUpperCase());
  const qs = new URLSearchParams({ market, ticker,
    add: (prefs.add || []).join(","), drop: (prefs.drop || []).join(",") });
  body.innerHTML = `<div class="sc-loading">Loading peers… <span class="peer-dim">(first load fetches every peer's statements, ~10–20s; cached after)</span></div>`;
  let d;
  try { d = await fetchJson(`/api/peers?${qs}`); }
  catch (err) { body.innerHTML = `<div class="sc-error">⚠ ${escapeHtml(err.message)}</div>`; return; }
  if (d.status !== "ready") { body.innerHTML = `<div class="sc-error">⚠ ${escapeHtml(d.error || "unavailable")}</div>`; return; }

  const cols = d.columns;
  const subj = d.rows[0];
  const med = d.median || {};
  const cmp = (r, c) => {
    const spec = _PEER_COLS[c];
    if (!spec.better || r[c] == null || med[c] == null) return "";
    const diff = r[c] - med[c];
    if (Math.abs(diff) < 0.05) return "";
    return (spec.better === "high" ? diff > 0 : diff < 0) ? "peer-up" : "peer-down";
  };
  const flagBadges = r => (r.flags || []).map(f => ({
    cross_currency: `<span class="peer-flag" title="Reports in ${escapeHtml(r.fin_currency || "?")}, trades in ${escapeHtml(r.currency || "?")}. Ratios are unit-free; the P/E mixes currencies unless the vendor converted.">FX</span>`,
    non_operating_gain: `<span class="peer-flag" title="Net margin exceeds operating margin — large non-operating items (one-off gains, equity-method income). Net margin overstates the core business.">non-op</span>`,
    period_mismatch: `<span class="peer-flag peer-flag-warn" title="Latest filing is for a different period than the other rows — not directly comparable.">period</span>`,
    pre_ipo: `<span class="peer-flag peer-flag-warn" title="Listed ${escapeHtml((r.pre_ipo || {}).listed || "")}, after the balance sheet behind ${((r.pre_ipo || {}).affects || []).map(k => k === "roe_fy" ? "last-FY ROE" : "ROE").join(" and ")}. That equity base excludes the IPO proceeds, which exaggerates ROE in either direction — profits or losses divided by a smaller base. It will move sharply once the new capital is on the books.">pre-IPO</span>`,
    restated: `<span class="peer-flag peer-flag-warn" title="The year-ago period was restated. Growth is withheld because it would compare two accounting bases; margins and ROE use the current period only and are unaffected.">restated</span>`,
    op_above_gross: `<span class="peer-flag peer-flag-warn" title="Operating margin above gross margin is impossible on one period — a vendor data error.">data?</span>`,
  }[f] || "")).join("");

  const openCls = "peer-open";
  const row = (r, isSubj) => {
    if (r.state !== "ok") {
      return `<tr class="peer-row-off"><td class="peer-co"><b>${escapeHtml(r.symbol)}</b> <span class="peer-dim">${escapeHtml(r.name || "")}</span></td>
        <td colspan="${cols.length}" class="peer-dim">${escapeHtml(r.note || r.error || r.state)}</td></tr>`;
    }
    return `<tr class="${isSubj ? "peer-subject" : ""}">
      <td class="peer-co">
        ${isSubj ? `<b>${escapeHtml(r.symbol)}</b>` : `<a href="#" class="${openCls}" data-sym="${escapeHtml(r.symbol)}"><b>${escapeHtml(r.symbol)}</b></a>`}
        <span class="peer-name" title="${r.period_end ? "Figures to " + escapeHtml(r.period_end) : ""}">${escapeHtml(r.name || "")}</span>${flagBadges(r)}
        ${isSubj ? "" : `<button type="button" class="peer-x" data-drop="${escapeHtml(r.symbol)}" title="Remove from this comparison">×</button>`}
      </td>
      ${cols.map(c => `<td class="peer-n ${isSubj ? "" : ""} ${cmp(r, c)}">${_peerFmt(r, c, d.currency)}</td>`).join("")}
    </tr>`;
  };

  // Header sub-labels name the actual period ("H1 2026, annualised",
  // "FY2025", "TTM to Jul 2026"), never a bare "YTD" / "annualised".
  const PL = d.period_labels || {};
  const head = cols.map(c => {
    let sub = "";
    if (c === "rev_growth_q" || c === "eps_growth_q") sub = market === "cn" ? `${PL.latest || "YTD"} vs year-ago` : "latest Q vs year-ago Q";
    else if (c === "eps_growth_fy") sub = "last fiscal year";
    else if (["gross_margin", "op_margin", "net_margin"].includes(c)) sub = market === "cn" ? (PL.latest || "YTD") : (PL.ttm || "TTM");
    else if (c === "roe") sub = market === "cn" ? `${PL.latest || "latest"}, annualised` : (PL.ttm || "TTM");
    else if (c === "roe_fy") sub = PL.fy || "last full year";
    else if (c === "pe_ttm") sub = "TTM";
    else if (c === "pe_fwd") sub = "next FY consensus";
    return `<th class="peer-n">${_PEER_COLS[c].lbl}${sub ? `<span class="peer-th-sub">${sub}</span>` : ""}</th>`;
  }).join("");

  const period = market === "cn"
    ? (d.common_period ? `reporting period ${d.common_period.slice(0, 4)}-${d.common_period.slice(4, 6)}-${d.common_period.slice(6)}` : "")
    : (subj.period_end ? `TTM to ${subj.period_end}` : "");
  const custom = (prefs.add || []).length || (prefs.drop || []).length;

  const cls = d.classification || {};
  const reclassified = cls.ok === false;

  body.innerHTML = `
    ${reclassified ? `<p class="peer-reclass"><b>Industry classification overridden.</b> ${escapeHtml(cls.note || "")}</p>` : ""}
    ${cls.scale_warning ? `<p class="peer-reclass"><b>Peers are far smaller.</b> ${escapeHtml(cls.scale_warning)}</p>` : ""}
    <p class="vg-note"><b>${escapeHtml(reclassified ? "Closest business matches" : (d.industry || "Industry"))}</b> ·
      ${reclassified ? `${cls.pool_size} companies matched` : `${d.industry_size} listed names`} · peers chosen as
      <b>${escapeHtml(d.selection_rule)}</b>${period ? ` · ${escapeHtml(period)}` : ""}.
      ${reclassified
        ? `Matched on each company's own business description and on which companies investors view alongside ${escapeHtml(subj.symbol)}`
        : "Same-industry classification"}, not a curated competitor list — add or remove tickers below to make it one.
      Green / red shows above or below the <b>peer median</b>.</p>
    <div class="peer-table-wrap">
      <table class="peer-table">
        <thead><tr><th>Company</th>${head}</tr></thead>
        <tbody>
          ${row(subj, true)}
          ${d.rows.slice(1).map(r => row(r, false)).join("")}
          <tr class="peer-median"><td class="peer-co"><b>Peer median</b> <span class="peer-dim">excl. ${escapeHtml(subj.symbol)}</span></td>
            ${cols.map(c => `<td class="peer-n">${c === "market_cap" ? "" : (med[c] == null ? "—" : _peerFmt({ [c]: med[c] }, c, d.currency))}</td>`).join("")}</tr>
        </tbody>
      </table>
    </div>
    <div class="peer-controls">
      <input type="text" class="sc-input peer-add-input" placeholder="${market === "cn" ? "Add peers, e.g. 000858, 600809" : "Add peers, e.g. AMD, QCOM"}">
      <button type="button" class="secondary-btn peer-add-btn">Add</button>
      ${custom ? `<button type="button" class="peer-reset">Reset to default peers</button>` : ""}
    </div>
    <details class="peer-basis"><summary>How each number is calculated</summary>
      <ul>${Object.entries(d.basis || {}).map(([k, v]) => `<li><b>${escapeHtml(k.replace(/_/g, " "))}:</b> ${escapeHtml(v)}</li>`).join("")}
        <li><b>n/m:</b> not meaningful — growth off a loss, or ROE on negative equity. Hover the cell for the reason.</li>
        ${reclassified ? `<li><b>peer set:</b> ${escapeHtml(cls.industry || "the vendor industry")} companies scored ${cls.industry_fit} against ${escapeHtml(subj.symbol)}'s business description, the matched companies ${cls.match_fit} — so the vendor's cohort was rejected and replaced.</li>` : ""}
        ${market === "us" ? `<li>Margins and ROE are computed from the income statement and balance sheet rather than the vendor's summary fields, which mix periods (the latest-quarter operating margin next to trailing-twelve-month gross margin) and can show an operating margin above gross margin.</li>` : ""}
      </ul></details>`;

  // Open a peer's own page.
  body.querySelectorAll(`.${openCls}`).forEach(a => a.addEventListener("click", e => {
    e.preventDefault();
    const sym = a.dataset.sym;
    const id = market === "cn" ? "ashareTickerInput" : "scTickerInput";
    const inp = document.getElementById(id);
    if (!inp) return;
    inp.value = market === "cn" ? sym : sym.toUpperCase();
    (market === "cn" ? runAshareScreen : runScreen)();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }));
  const S = ticker.toUpperCase();
  body.querySelectorAll(".peer-x").forEach(b => b.addEventListener("click", () => {
    const p = _peerPrefs(market, S);
    p.drop = [...new Set([...(p.drop || []), b.dataset.drop])];
    p.add = (p.add || []).filter(x => x !== b.dataset.drop);
    _peerSave(market, S, p); loadPeerCard(card);
  }));
  const addIt = () => {
    const raw = body.querySelector(".peer-add-input").value;
    const syms = raw.split(/[\s,]+/).map(x => x.trim().toUpperCase()).filter(Boolean);
    if (!syms.length) return;
    const p = _peerPrefs(market, S);
    p.add = [...new Set([...(p.add || []), ...syms])];
    p.drop = (p.drop || []).filter(x => !syms.includes(x));
    _peerSave(market, S, p); loadPeerCard(card);
  };
  body.querySelector(".peer-add-btn").addEventListener("click", addIt);
  body.querySelector(".peer-add-input").addEventListener("keydown", e => { if (e.key === "Enter") addIt(); });
  const rs = body.querySelector(".peer-reset");
  if (rs) rs.addEventListener("click", () => { _peerSave(market, S, {}); loadPeerCard(card); });
}

// ── Earnings & Valuation Section ──────────────────────────────────────────

function renderEarningsSection(earnings) {
  if (!earnings) return "";
  const q = earnings.quarters || [];
  const hasMetrics = earnings.trailing_pe || earnings.forward_pe || earnings.eps_trailing;
  if (q.length === 0 && !hasMetrics) return "";

  // Next earnings banner
  let nextBanner = "";
  if (earnings.next_earnings_date) {
    const daysLabel = earnings.days_to_next != null
      ? ` · ${earnings.days_to_next} days away` : "";
    nextBanner = `<div class="sc-earn-next">
      <span class="sc-earn-next-icon">📅</span>
      <span class="sc-earn-next-label">Next Earnings</span>
      <span class="sc-earn-next-date">${earnings.next_earnings_date}</span>
      <span class="sc-earn-next-days">${daysLabel}</span>
    </div>`;
  }

  // Beat rate badge
  const total = (earnings.beat_count || 0) + (earnings.miss_count || 0);
  let beatBadge = "";
  if (total > 0) {
    const pct = Math.round(earnings.beat_count / total * 100);
    const cls = pct >= 75 ? "earn-beat-high" : pct >= 50 ? "earn-beat-mid" : "earn-beat-low";
    beatBadge = `<span class="sc-earn-beat ${cls}" title="Last ${total} reported quarters with both an actual and an estimate. In-line prints are excluded.">Beat ${earnings.beat_count}/${total} (${pct}%)</span>`;
  }

  // Valuation metrics tiles
  // Every tile below comes from Yahoo's `info` and is GAAP. The quarterly
  // actual/estimate series further down is the ADJUSTED (non-GAAP) figure that
  // analysts forecast against. For AVGO the two differ by ~25% (TTM adjusted
  // 9.76 vs GAAP 7.83), so the basis is now stated on every tile rather than
  // leaving two incompatible EPS numbers sitting next to each other unlabelled.
  const metricDefs = [
    ["Trailing EPS", "GAAP, TTM", earnings.eps_trailing != null ? `$${earnings.eps_trailing}` : null],
    ["Forward EPS",  "consensus, next FY", earnings.eps_forward  != null ? `$${earnings.eps_forward}`  : null],
    ["Trailing P/E", "on GAAP EPS", earnings.trailing_pe  != null ? `${earnings.trailing_pe}×`  : null],
    ["Forward P/E",  "on consensus EPS", earnings.forward_pe   != null ? `${earnings.forward_pe}×`   : null],
    ["PEG Ratio",    "vendor-supplied", earnings.peg_ratio    != null ? `${earnings.peg_ratio}`      : null],
    ["EPS Growth",   "latest quarter, YoY", earnings.eg_yoy       != null ? `${earnings.eg_yoy > 0 ? "+" : ""}${earnings.eg_yoy}%` : null],
    // Revenue growth is deliberately absent: revenue belongs to the Revenue &
    // Profit card, and a third copy of the same number invites the three to
    // disagree the moment one of them changes basis.
  ].filter(m => m[2] != null);

  const metricsHtml = metricDefs.map(([label, basis, val]) => `
    <div class="sc-earn-metric">
      <span class="sc-earn-metric-label">${label}</span>
      <span class="sc-earn-metric-val">${val}</span>
      <span class="sc-earn-metric-basis">${basis}</span>
    </div>`).join("");

  if (q.length === 0) {
    return `<div class="sc-card sc-earnings">
      <div class="sc-card-header">
        <div class="sc-card-title">Earnings &amp; Valuation</div>
        ${beatBadge}
      </div>
      ${nextBanner ? `<div class="sc-earn-top">${nextBanner}</div>` : ""}
      <div class="sc-earn-metrics">${metricsHtml}</div>
    </div>`;
  }

  // ── EPS bar chart ─────────────────────────────────────────────────────────
  const epsVals = q.flatMap(r => [r.eps_act, r.eps_est].filter(v => v != null));
  const epsRawMax = epsVals.length > 0 ? Math.max(...epsVals) : 1;
  const epsRawMin = Math.min(0, ...(epsVals.length > 0 ? epsVals : [0]));
  const epsRange = epsRawMax - epsRawMin || 1;

  const epsChartBars = [...q].reverse().map(r => {
    const actH = r.eps_act != null ? Math.max(2, Math.round((r.eps_act - epsRawMin) / epsRange * 100)) : 0;
    const estH = r.eps_est != null ? Math.round((r.eps_est - epsRawMin) / epsRange * 100) : null;
    const barCls = r.beat === true ? "earn-bar-beat" : r.beat === false ? "earn-bar-miss" : "earn-bar-neutral";
    const valCls = r.beat === true ? "earn-val-beat" : r.beat === false ? "earn-val-miss" : "";
    const tooltip = [
      r.eps_act != null ? `Act: $${r.eps_act}` : "",
      r.eps_est != null ? `Est: $${r.eps_est}` : "",
      r.surprise_pct != null ? `${r.surprise_pct > 0 ? "+" : ""}${r.surprise_pct}%` : "",
    ].filter(Boolean).join(" | ");
    return `
    <div class="sc-earn-bar-group">
      <div class="sc-earn-bar-area" title="${tooltip}">
        <div class="sc-earn-bar-act ${barCls}" style="height:${actH}%"></div>
        ${estH != null ? `<div class="sc-earn-bar-est" style="bottom:${estH}%"></div>` : ""}
      </div>
      <div class="sc-earn-bar-lbl">${r.label}</div>
      <div class="sc-earn-bar-val ${valCls}">${r.eps_act != null ? "$"+r.eps_act : "—"}</div>
    </div>`;
  }).join("");

  // ── Summary table ─────────────────────────────────────────────────────────
  const tableRows = q.map(r => {
    const beatCls  = r.beat === true ? "earn-row-beat" : r.beat === false ? "earn-row-miss" : "";
    const surpCls  = r.beat === true ? "earn-td-beat"  : r.beat === false ? "earn-td-miss"  : "";
    const surpTxt  = r.units_suspect
      ? `<span class="qg-nm" title="Reported EPS and consensus appear to be in different units in the vendor's earnings calendar (a common ADR data fault). Excluded from the beat rate.">units?</span>`
      : r.surprise_pct != null ? `${r.surprise_pct > 0 ? "+" : ""}${r.surprise_pct}%` : "—";
    return `<tr class="${beatCls}">
      <td>${r.label}</td>
      <td>${r.eps_est != null ? "$"+r.eps_est : "—"}</td>
      <td>${r.eps_act != null ? "$"+r.eps_act : "—"}</td>
      <td class="${surpCls}">${surpTxt}</td>
    </tr>`;
  }).join("");

  return `
  <div class="sc-card sc-earnings">
    <div class="sc-card-header">
      <div class="sc-card-title">Earnings &amp; Valuation</div>
      ${beatBadge}
    </div>

    <div class="sc-earn-top">
      ${nextBanner}
    </div>

    <div class="sc-earn-metrics">${metricsHtml}</div>
    <p class="sc-card-sub sc-earn-pointer">This card is about earnings against
      consensus and what the market pays for them. Revenue and profit levels are
      in <b>Revenue &amp; Profit</b>, above.</p>

    <div class="sc-earn-charts sc-earn-charts-single">
      <div class="sc-earn-chart-block">
        <div class="sc-earn-chart-title">EPS — Actual vs Estimate <span class="sc-earn-basis">adjusted / non-GAAP</span></div>
        <div class="sc-earn-bars">${epsChartBars}</div>
      </div>
    </div>

    <table class="sc-earn-table">
      <thead><tr>
        <th>Quarter</th><th>EPS Est</th><th>EPS Act</th><th>Surprise</th>
      </tr></thead>
      <tbody>${tableRows}</tbody>
    </table>
  </div>`;
}

// ── Valuation vs. Growth Section ───────────────────────────────────────────

// ── Valuation vs. own history (Inefficiency Radar archetype #1) ──────────────
async function loadValPctile(market, ticker) {
  const box = document.getElementById("valPctileBox");
  if (!box) return;
  box.innerHTML = `<div class="sc-card"><div class="vp-loading">Computing valuation vs. own history…</div></div>`;
  try {
    const d = await fetchJson(`/api/valuation_pctile?market=${market}&ticker=${encodeURIComponent(ticker)}&years=5`);
    if (d.status !== "ready") { box.innerHTML = ""; return; }
    renderValPctile(d, box);
  } catch { box.innerHTML = ""; }
}

function renderValPctile(d, box) {
  const cur = d.market === "cn" ? "¥" : "$";
  const fmt = (m, v) => v == null ? "—" : (m.key === "divyield" ? `${v}%` : `${v}×`);
  // one row per metric: current value + a range bar (min→max) with median + current markers
  const rows = (d.metrics || []).map(m => {
    const span = (m.max - m.min) || 1;
    const posCur = Math.max(0, Math.min(100, (m.current - m.min) / span * 100));
    const posMed = Math.max(0, Math.min(100, (m.median - m.min) / span * 100));
    const zc = m.zone === "cheap" ? "#16a34a" : m.zone === "expensive" ? "#dc2626" : "#a16207";
    const read = m.direction === "low"
      ? `cheaper than <strong>${m.cheaper_than}%</strong> of its 5-yr range`
      : `higher yield than <strong>${m.cheaper_than}%</strong> of its 5-yr range`;
    return `
      <div class="vp-row">
        <div class="vp-head">
          <span class="vp-label">${escapeHtml(m.label)}</span>
          <span class="vp-cur">${fmt(m, m.current)}</span>
          <span class="vp-badge" style="color:${zc};background:${zc}1a">${m.zone}</span>
        </div>
        <div class="vp-bar">
          <span class="vp-bar-fill" style="background:${zc}22"></span>
          <span class="vp-median" style="left:${posMed.toFixed(1)}%" title="5-yr median ${fmt(m, m.median)}"></span>
          <span class="vp-marker" style="left:${posCur.toFixed(1)}%;background:${zc}"></span>
        </div>
        <div class="vp-scale"><span>${fmt(m, m.min)}</span><span class="vp-read">${read}</span><span>${fmt(m, m.max)}</span></div>
      </div>`;
  }).join("");
  const cav = (d.caveats || []).length ? `<div class="vp-caveat">⚠ ${escapeHtml(d.caveats[0])}</div>` : "";
  box.innerHTML = `
    <div class="sc-card vp-card">
      <div class="sc-card-header"><div class="sc-card-title">Valuation vs. own history</div>
        <span class="vp-window">${d.window.from} → ${d.window.to}</span></div>
      <p class="vp-note">${escapeHtml(d.note)}</p>
      ${rows}
      ${cav}
    </div>`;
}

function renderValGrowthSection(vg, currSym = '$') {
  if (!vg) return "";
  const eg = vg.earnings_growth || {};
  const pg = vg.pe_growth || {};
  const pk = vg.peg || {};
  const ip = vg.industry_pe || {};
  const isEtf = vg.is_etf;

  // Nothing to show for an ETF with no P/E anywhere → skip entirely
  if (isEtf && ip.stock_pe == null && ip.typical_pe == null) return "";

  const pctTxt = (v) => v == null ? "—" : `${v > 0 ? "+" : ""}${v}%`;
  const signCls = (v) => v == null ? "" : v > 0 ? "vg-pos" : v < 0 ? "vg-neg" : "";

  // Verdict → colour class + wording
  const VERDICT = {
    cheap:    ["vg-good",  "Cheap vs. growth"],
    fair:     ["vg-okay",  "Fairly priced"],
    full:     ["vg-warn",  "Fully priced"],
    expensive:["vg-bad",   "Expensive vs. growth"],
    discount: ["vg-good",  "Discount to peers"],
    inline:   ["vg-okay",  "In line with peers"],
    premium:  ["vg-warn",  "Premium to peers"],
    rich:     ["vg-bad",   "Rich vs. peers"],
    unknown:  ["vg-unk",   "—"],
  };

  // ── tiny SVG sparkline (line + point labels) ──────────────────────────────
  function spark(series, key, fmt, color) {
    const pts = (series || []).filter(p => p[key] != null);
    if (pts.length < 2) return "";
    const W = 260, H = 74, padX = 26, padY = 14;
    const vals = pts.map(p => p[key]);
    const mn = Math.min(...vals), mx = Math.max(...vals);
    const rng = (mx - mn) || 1;
    const x = (i) => padX + i * (W - 2 * padX) / (pts.length - 1);
    const y = (v) => H - padY - (v - mn) / rng * (H - 2 * padY);
    const poly = pts.map((p, i) => `${x(i).toFixed(1)},${y(p[key]).toFixed(1)}`).join(" ");
    const dots = pts.map((p, i) => `<circle cx="${x(i).toFixed(1)}" cy="${y(p[key]).toFixed(1)}" r="2.5" fill="${color}"/>`).join("");
    const labels = pts.map((p, i) => {
      const anchor = i === 0 ? "start" : i === pts.length - 1 ? "end" : "middle";
      return `<text x="${x(i).toFixed(1)}" y="${(y(p[key]) - 6).toFixed(1)}" text-anchor="${anchor}" font-size="9.5" fill="var(--muted)">${fmt(p[key])}</text>`;
    }).join("");
    const years = pts.map((p, i) => {
      const anchor = i === 0 ? "start" : i === pts.length - 1 ? "end" : "middle";
      return `<text x="${x(i).toFixed(1)}" y="${H - 2}" text-anchor="${anchor}" font-size="9" fill="var(--muted)">${String(p.year).slice(2)}</text>`;
    }).join("");
    return `<svg viewBox="0 0 ${W} ${H}" class="vg-spark" preserveAspectRatio="none">
      <polyline points="${poly}" fill="none" stroke="${color}" stroke-width="1.6"/>
      ${dots}${labels}${years}
    </svg>`;
  }

  // ── 1. Earnings growth ────────────────────────────────────────────────────
  let egBlock = "";
  if (!isEtf) {
    const tiles = [
      ["EPS YoY (TTM)", eg.eps_yoy],
      ["EPS CAGR 3Y",   eg.eps_cagr_3y],
      ["EPS CAGR 5Y",   eg.eps_cagr_5y],
    ].map(([l, v]) => `
      <div class="vg-tile">
        <span class="vg-tile-lbl">${l}</span>
        <span class="vg-tile-val ${signCls(v)}">${pctTxt(v)}</span>
      </div>`).join("");
    const epsSpark = spark(eg.eps_series, "eps", (v) => `${currSym}${v}`, "var(--accent, #6366f1)");
    egBlock = `
      <div class="vg-block">
        <div class="vg-block-title">1 · Earnings Growth Rate</div>
        <div class="vg-tiles">${tiles}</div>
        ${epsSpark ? `<div class="vg-spark-wrap"><div class="vg-spark-cap">Annual EPS (${currSym})</div>${epsSpark}</div>` : ""}
      </div>`;
  }

  // ── 2. P/E growth rate ────────────────────────────────────────────────────
  let peBlock = "";
  if (pg.pe_series && pg.pe_series.length >= 2) {
    const tiles = [
      ["Current P/E",  pg.pe_current != null ? `${pg.pe_current}×` : "—", ""],
      ["Avg P/E (hist)", pg.pe_avg != null ? `${pg.pe_avg}×` : "—", ""],
      ["P/E CAGR",     pctTxt(pg.pe_cagr), signCls(pg.pe_cagr)],
      ["Re-rating vs avg", pctTxt(pg.pe_rerating), signCls(pg.pe_rerating)],
    ].map(([l, v, c]) => `
      <div class="vg-tile">
        <span class="vg-tile-lbl">${l}</span>
        <span class="vg-tile-val ${c}">${v}</span>
      </div>`).join("");
    const peSpark = spark(pg.pe_series, "pe", (v) => `${v}×`, "#f59e0b");
    const rerNote = pg.pe_rerating == null ? "" :
      pg.pe_rerating > 15 ? `<div class="vg-note">Multiple has <strong>expanded</strong> ${pctTxt(pg.pe_rerating)} above its own history — more of the return is coming from re-rating than earnings.</div>` :
      pg.pe_rerating < -15 ? `<div class="vg-note">Multiple has <strong>compressed</strong> ${pctTxt(pg.pe_rerating)} vs. its history — cheaper than its typical level.</div>` :
      `<div class="vg-note">Multiple is roughly in line with its own history.</div>`;
    peBlock = `
      <div class="vg-block">
        <div class="vg-block-title">2 · P/E Multiple Growth Rate</div>
        <div class="vg-tiles">${tiles}</div>
        ${peSpark ? `<div class="vg-spark-wrap"><div class="vg-spark-cap">Year-end P/E multiple</div>${peSpark}</div>` : ""}
        ${rerNote}
      </div>`;
  }

  // ── 3. PEG & PEGY ─────────────────────────────────────────────────────────
  let pegBlock = "";
  if (pk.peg != null || pk.pegy != null) {
    const [pegCls, pegWord]   = VERDICT[pk.peg_verdict]  || VERDICT.unknown;
    const [pegyCls, pegyWord] = VERDICT[pk.pegy_verdict] || VERDICT.unknown;
    const gv = pk.growth_pct != null ? `${pk.growth_pct}%` : "—";
    const dv = pk.div_yield_pct != null ? `${pk.div_yield_pct}%` : "0%";
    pegBlock = `
      <div class="vg-block">
        <div class="vg-block-title">3 · P/E vs. Growth (PEG &amp; PEGY)</div>
        <div class="vg-gauges">
          <div class="vg-gauge ${pegCls}">
            <div class="vg-gauge-head">PEG</div>
            <div class="vg-gauge-val">${pk.peg != null ? pk.peg : "—"}</div>
            <div class="vg-gauge-word">${pegWord}</div>
            <div class="vg-gauge-formula">P/E ${pk.pe ?? "—"} ÷ growth ${gv}</div>
          </div>
          <div class="vg-gauge ${pegyCls}">
            <div class="vg-gauge-head">PEGY</div>
            <div class="vg-gauge-val">${pk.pegy != null ? pk.pegy : "—"}</div>
            <div class="vg-gauge-word">${pegyWord}</div>
            <div class="vg-gauge-formula">P/E ${pk.pe ?? "—"} ÷ (growth ${gv} + yield ${dv})</div>
          </div>
        </div>
        <div class="vg-note">${pk.lynch_note || ""} <em>Growth basis: ${pk.growth_basis || "—"}.</em></div>
      </div>`;
  }

  // ── 4. Industry P/E — live peer median (primary) + long-run anchor ────────
  let indBlock = "";
  if (ip.typical_pe != null || ip.stock_pe != null || ip.peer_median != null) {
    const stockPe = ip.stock_pe, typPe = ip.typical_pe, peerPe = ip.peer_median;
    const hasPeer = peerPe != null;
    // Headline badge uses the live peer comparison when available
    const headVerdict = ip.headline_verdict || ip.verdict;
    const [headCls, headWord] = VERDICT[headVerdict] || VERDICT.unknown;
    const headPrem = hasPeer ? ip.peer_premium_pct : ip.premium_pct;
    const headRef  = hasPeer ? "peers" : "typical";
    const headBadge = headPrem != null
      ? `<span class="vg-badge ${headCls}">${pctTxt(headPrem)} vs. ${headRef} · ${headWord}</span>` : "";

    const scale = Math.max(stockPe || 0, typPe || 0, peerPe || 0) || 1;
    const W = (v) => v != null ? Math.round(v / scale * 100) : 0;
    const [stockCls] = VERDICT[headVerdict] || VERDICT.unknown;

    const peerRow = hasPeer ? `
          <div class="vg-ind-row">
            <span class="vg-ind-name">Peer median <span class="vg-ind-n">(n=${ip.peer_n})</span></span>
            <div class="vg-ind-track"><div class="vg-ind-fill vg-ind-peer" style="width:${W(peerPe)}%"></div></div>
            <span class="vg-ind-num">${peerPe}×</span>
          </div>` : "";
    const typRow = typPe != null ? `
          <div class="vg-ind-row">
            <span class="vg-ind-name">Long-run typical</span>
            <div class="vg-ind-track"><div class="vg-ind-fill vg-ind-typ" style="width:${W(typPe)}%"></div></div>
            <span class="vg-ind-num">${typPe}×</span>
          </div>` : "";
    const fwdRow = ip.forward_pe != null ? `
          <div class="vg-ind-row">
            <span class="vg-ind-name">Forward P/E</span>
            <div class="vg-ind-track"><div class="vg-ind-fill vg-ind-fwd" style="width:${W(ip.forward_pe)}%"></div></div>
            <span class="vg-ind-num">${ip.forward_pe}×</span>
          </div>` : "";

    const note = hasPeer
      ? `Peer median is a <strong>live</strong> trailing-P/E median of ${ip.peer_n} ${escapeHtml(ip.peer_basis || "sector")} names${vg.is_etf ? "" : ""} (updated on load). The long-run typical is a static anchor shown for context.`
      : `Typical P/E is a long-run sector/industry benchmark — a live peer median wasn't available for this name, so use it as a rough anchor.`;

    indBlock = `
      <div class="vg-block">
        <div class="vg-block-title">4 · Industry P/E ${hasPeer ? "— Live Peer Median" : ""}</div>
        <div class="vg-ind-head">
          <span class="vg-ind-label">${escapeHtml(ip.peer_basis || ip.label || "—")}</span>
          ${headBadge}
        </div>
        <div class="vg-ind-bars">
          <div class="vg-ind-row">
            <span class="vg-ind-name">This stock</span>
            <div class="vg-ind-track"><div class="vg-ind-fill vg-ind-stock ${stockCls}" style="width:${W(stockPe)}%"></div></div>
            <span class="vg-ind-num">${stockPe != null ? stockPe + "×" : "—"}</span>
          </div>
          ${peerRow}${typRow}${fwdRow}
        </div>
        <div class="vg-note">${note}</div>
      </div>`;
  }

  if (!egBlock && !peBlock && !pegBlock && !indBlock) return "";

  return `
  <div class="sc-card vg-card">
    <div class="sc-card-header">
      <div class="sc-card-title">Valuation vs. Growth</div>
    </div>
    <div class="vg-grid">
      ${egBlock}${peBlock}${pegBlock}${indBlock}
    </div>
  </div>`;
}

// ── Debt Structure Section ─────────────────────────────────────────────────

function renderDebtStructureSection(ds, currSym = '$') {
  if (!ds) return "";
  if (ds.total_debt == null && ds.st_pct == null && ds.long_term_debt == null) return "";

  function money(v) {
    if (v == null) return "—";
    const a = Math.abs(v), s = v < 0 ? "-" : "";
    if (a >= 1e12) return `${s}${currSym}${(a/1e12).toFixed(2)}T`;
    if (a >= 1e9)  return `${s}${currSym}${(a/1e9).toFixed(1)}B`;
    if (a >= 1e6)  return `${s}${currSym}${(a/1e6).toFixed(0)}M`;
    return `${s}${currSym}${a.toFixed(0)}`;
  }
  // verdict helpers (lower-is-better vs higher-is-better)
  const vLow  = (v, g, o, w) => v == null ? "vg-unk" : v <= g ? "vg-good" : v <= o ? "vg-okay" : v <= w ? "vg-warn" : "vg-bad";
  const vHigh = (v, g, o, w) => v == null ? "vg-unk" : v >= g ? "vg-good" : v >= o ? "vg-okay" : v >= w ? "vg-warn" : "vg-bad";

  // ── Overview tiles ────────────────────────────────────────────────────────
  const ov = [
    ["Total Debt",  money(ds.total_debt), ""],
    ["Net Debt",    money(ds.net_debt),   ds.net_debt != null && ds.net_debt < 0 ? "vg-pos" : ""],
    ["Cash & Equiv.", money(ds.cash),     ""],
    ["Debt / Equity", ds.debt_to_equity != null ? `${ds.debt_to_equity}×` : "—",
       vLow(ds.debt_to_equity, 0.5, 1.0, 2.0)],
  ].map(([l, v, c]) => `
    <div class="vg-tile"><span class="vg-tile-lbl">${l}</span><span class="vg-tile-val ${c}">${v}</span></div>`).join("");

  // ── Maturity split bar ────────────────────────────────────────────────────
  let splitBlock = "";
  if (ds.st_pct != null || ds.lt_pct != null) {
    const st = ds.st_pct || 0, lt = ds.lt_pct || 0;
    splitBlock = `
      <div class="ds-split">
        <div class="ds-split-title">Maturity Profile</div>
        <div class="ds-split-bar">
          ${st > 0 ? `<div class="ds-split-seg ds-seg-st" style="width:${st}%" title="Short-term ${st}%"></div>` : ""}
          ${lt > 0 ? `<div class="ds-split-seg ds-seg-lt" style="width:${lt}%" title="Long-term ${lt}%"></div>` : ""}
        </div>
        <div class="ds-split-legend">
          <span class="ds-leg"><i class="ds-dot ds-seg-st"></i> Short-term ${ds.st_pct != null ? ds.st_pct + "%" : "—"} · ${money(ds.current_debt)}</span>
          <span class="ds-leg"><i class="ds-dot ds-seg-lt"></i> Long-term ${ds.lt_pct != null ? ds.lt_pct + "%" : "—"} · ${money(ds.long_term_debt)}</span>
        </div>
        ${ds.capital_leases != null ? `<div class="vg-note">Incl. capital lease obligations of ${money(ds.capital_leases)}.</div>` : ""}
        ${ds.broader_debt ? `<div class="vg-note">⚠ Counting finance-lease liabilities as well, debt is <b>${money(ds.broader_debt.amount)}</b> — ${money(ds.broader_debt.gap)} more than shown here. Gate 1's Debt / EBITDA uses that wider figure.</div>` : ""}
      </div>`;
  }

  // ── Leverage & coverage tiles ─────────────────────────────────────────────
  const lev = [
    ["Debt / EBITDA",     ds.debt_ebitda != null ? `${ds.debt_ebitda}×` : "—", vLow(ds.debt_ebitda, 1.5, 3, 4)],
    ["Interest Coverage", ds.interest_coverage != null ? `${ds.interest_coverage}×` : "—", vHigh(ds.interest_coverage, 8, 4, 2)],
    ["Debt / Assets",     ds.debt_to_assets != null ? `${ds.debt_to_assets}%` : "—", vLow(ds.debt_to_assets, 30, 50, 65)],
    ["Current Ratio",     ds.current_ratio != null ? `${ds.current_ratio}×` : "—", vHigh(ds.current_ratio, 1.5, 1, 0.8)],
    ["Cost of Debt",      ds.cost_of_debt != null ? `${ds.cost_of_debt}%` : "—", ds.is_financial ? "" : vLow(ds.cost_of_debt, 4, 6, 9)],
    ["Quick Ratio",       ds.quick_ratio != null ? `${ds.quick_ratio}×` : "—", vHigh(ds.quick_ratio, 1, 0.7, 0.5)],
  ].map(([l, v, c]) => `
    <div class="vg-tile"><span class="vg-tile-lbl">${l}</span><span class="vg-tile-val ${c}">${v}</span></div>`).join("");

  // ── Total debt trend ──────────────────────────────────────────────────────
  let trendBlock = "";
  const tr = (ds.debt_series || []).filter(p => p.debt != null);
  if (tr.length >= 2) {
    const W = 300, H = 78, padX = 30, padY = 16;
    const vals = tr.map(p => p.debt);
    const mn = Math.min(0, ...vals), mx = Math.max(...vals), rng = (mx - mn) || 1;
    const x = (i) => padX + i * (W - 2 * padX) / (tr.length - 1);
    const y = (v) => H - padY - (v - mn) / rng * (H - 2 * padY);
    const bars = tr.map((p, i) => {
      const bw = 20, bx = x(i) - bw / 2, by = y(p.debt), bh = (H - padY) - by;
      const down = i > 0 && p.debt < tr[i-1].debt;
      return `<rect x="${bx.toFixed(1)}" y="${by.toFixed(1)}" width="${bw}" height="${Math.max(1,bh).toFixed(1)}" rx="2" fill="${down ? '#16a34a' : '#94a3b8'}"/>
        <text x="${x(i).toFixed(1)}" y="${(by-4).toFixed(1)}" text-anchor="middle" font-size="9" fill="var(--muted)">${money(p.debt)}</text>
        <text x="${x(i).toFixed(1)}" y="${H-3}" text-anchor="middle" font-size="9" fill="var(--muted)">${String(p.year).slice(2)}</text>`;
    }).join("");
    const first = tr[0].debt, last = tr[tr.length-1].debt;
    const chg = first > 0 ? Math.round((last/first - 1) * 100) : null;
    const chgTxt = chg == null ? "" : `<span class="${chg <= 0 ? 'vg-pos' : 'vg-neg'}">${chg > 0 ? "+" : ""}${chg}% over ${tr.length} yrs</span>`;
    trendBlock = `
      <div class="ds-trend">
        <div class="ds-split-title">Total Debt Trend ${chgTxt}</div>
        <svg viewBox="0 0 ${W} ${H}" class="vg-spark" preserveAspectRatio="none">${bars}</svg>
      </div>`;
  }

  const finNote = ds.is_financial
    ? `<div class="vg-note ds-fin-note">⚠ Financial-sector company: deposits and other funding are excluded from "Total Debt", so leverage ratios (Debt/EBITDA, interest coverage, cost of debt) understate true balance-sheet leverage. Read these alongside capital ratios (CET1, Tier-1) instead.</div>`
    : "";

  return `
  <div class="sc-card vg-card ds-card">
    <div class="sc-card-header"><div class="sc-card-title">Debt Structure</div></div>
    <div class="vg-tiles ds-overview">${ov}</div>
    <div class="vg-grid ds-grid">
      <div class="vg-block">
        <div class="vg-block-title">Maturity &amp; Composition</div>
        ${splitBlock}${trendBlock}
      </div>
      <div class="vg-block">
        <div class="vg-block-title">Leverage &amp; Coverage</div>
        <div class="vg-tiles">${lev}</div>
      </div>
    </div>
    ${finNote}
  </div>`;
}

// ── Hidden Assets & Asset Backing Section ──────────────────────────────────

function renderHiddenAssetsSection(ha, currSym = '$') {
  if (!ha) return "";
  const hasAny = ["land","buildings","investment_real_estate","net_ppe","cash_st_inv","tangible_book"]
    .some(k => ha[k] != null);
  if (!hasAny) return "";

  function money(v) {
    if (v == null) return "—";
    const a = Math.abs(v), s = v < 0 ? "-" : "";
    if (a >= 1e12) return `${s}${currSym}${(a/1e12).toFixed(2)}T`;
    if (a >= 1e9)  return `${s}${currSym}${(a/1e9).toFixed(1)}B`;
    if (a >= 1e6)  return `${s}${currSym}${(a/1e6).toFixed(0)}M`;
    return `${s}${currSym}${a.toFixed(0)}`;
  }
  const tile = (l, v, c = "") => `
    <div class="vg-tile"><span class="vg-tile-lbl">${l}</span><span class="vg-tile-val ${c}">${v}</span></div>`;

  // ── 1. Real estate & fixed assets (only tiles with real data) ─────────────
  const reTiles = [
    ["Land (at cost)",         ha.land,      "ha-land"],
    ["Buildings",              ha.buildings, ""],
    ["Investment Real Estate", ha.investment_real_estate, "ha-land"],
    ["Construction in Prog.",  ha.cip,       ""],
    ["Net PP&E",               ha.net_ppe,   ""],
    ["Gross PP&E",             ha.gross_ppe, ""],
  ].filter(t => t[1] != null).map(t => tile(t[0], money(t[1]), t[2])).join("");

  // depreciation gauge
  let depBlock = "";
  if (ha.dep_pct != null) {
    const depCls = ha.dep_pct >= 60 ? "vg-good" : ha.dep_pct >= 40 ? "vg-okay" : "";
    depBlock = `
      <div class="ha-dep">
        <div class="ds-split-title">Accumulated Depreciation
          <span class="${depCls}">${ha.dep_pct}% depreciated</span></div>
        <div class="ds-split-bar">
          <div class="ds-split-seg ha-dep-used" style="width:${ha.dep_pct}%" title="Depreciated ${ha.dep_pct}%"></div>
          <div class="ds-split-seg ha-dep-left" style="width:${(100-ha.dep_pct).toFixed(1)}%" title="Net book ${(100-ha.dep_pct).toFixed(1)}%"></div>
        </div>
        <div class="vg-note">Higher depreciation means assets are carried far below their original cost — if they're still productive (or it's land/buildings that appreciate), true worth can exceed book.</div>
      </div>`;
  }

  // ── 2. Net cash position ──────────────────────────────────────────────────
  const ncCls = ha.net_cash != null && ha.net_cash > 0 ? "vg-pos" : "";
  const ncPctCls = ha.net_cash_pct_mcap != null && ha.net_cash_pct_mcap >= 25 ? "vg-good"
                 : ha.net_cash_pct_mcap != null && ha.net_cash_pct_mcap > 0 ? "vg-okay" : "";
  const cashTiles = [
    tile("Cash & ST Invest.", money(ha.cash_st_inv)),
    tile("Total Debt", money(ha.total_debt)),
    tile("Net Cash", money(ha.net_cash), ncCls),
    tile("Net Cash / Mkt Cap", ha.net_cash_pct_mcap != null ? `${ha.net_cash_pct_mcap}%` : "—", ncPctCls),
  ].join("");

  // ── 3. Book & backing ─────────────────────────────────────────────────────
  const pbCls = ha.pb != null && ha.pb < 1 ? "vg-good" : ha.pb != null && ha.pb < 2 ? "vg-okay" : "";
  const ptCls = ha.p_tangible_book != null && ha.p_tangible_book < 1.2 ? "vg-good"
              : ha.p_tangible_book != null && ha.p_tangible_book < 2.5 ? "vg-okay" : "";
  const bookTiles = [
    tile("Book Value (equity)", money(ha.book_value)),
    tile("Tangible Book", money(ha.tangible_book)),
    tile("P / Book", ha.pb != null ? `${ha.pb}×` : "—", pbCls),
    tile("P / Tangible Book", ha.p_tangible_book != null ? `${ha.p_tangible_book}×` : "—", ptCls),
    ha.lt_investments != null ? tile("LT Investments / Stakes", money(ha.lt_investments)) : "",
    ha.goodwill_intangibles != null ? tile("Goodwill + Intangibles", money(ha.goodwill_intangibles)) : "",
  ].join("");

  // ── Signals ───────────────────────────────────────────────────────────────
  const ICON = { cash: "💵", dep: "🏭", book: "📉", re: "🏢" };
  const sigHtml = (ha.signals || []).length ? `
    <div class="ha-signals">
      ${ha.signals.map(s => `<div class="ha-signal ha-sig-${s.kind}"><span class="ha-sig-ico">${ICON[s.kind] || "⚑"}</span><span>${escapeHtml(s.text)}</span></div>`).join("")}
    </div>` : "";

  return `
  <div class="sc-card vg-card ha-card">
    <div class="sc-card-header">
      <div class="sc-card-title">Hidden Assets &amp; Asset Backing</div>
      <span class="ha-lynch-tag">Lynch “Asset Play” lens</span>
    </div>
    ${sigHtml}
    <div class="vg-grid">
      <div class="vg-block">
        <div class="vg-block-title">Real Estate &amp; Fixed Assets</div>
        <div class="vg-tiles">${reTiles}</div>
        ${depBlock}
      </div>
      <div class="vg-block">
        <div class="vg-block-title">Net Cash Position</div>
        <div class="vg-tiles">${cashTiles}</div>
        <div class="vg-block-title" style="margin-top:14px">Book Value Backing</div>
        <div class="vg-tiles">${bookTiles}</div>
      </div>
    </div>
    <div class="vg-note ha-caveat">These are <strong>systematic signals</strong>, not an appraisal. Real book values (land, buildings, stakes) are carried at <em>historical cost</em> and are often understated — but the true market value of hidden real estate or subsidiaries can only be confirmed in 10-K / annual-report footnotes. Some assets (e.g. timberland, mineral rights) may sit in line-items this feed doesn't break out.</div>
  </div>`;
}

// ── Cash Flow & Margins Section ────────────────────────────────────────────


// ════════════════════════════════════════════════════════════════════════════
// Revenue & Profit — the levels, not the ratios.
// Three series on one axis: revenue bars with operating and net profit drawn
// over them. The gap between the two lines is where interest, tax and one-off
// gains live — Nebius posted net income of +$584m on an operating loss of
// $111m, which a revenue-and-net-profit chart would have drawn as a good
// quarter.
// ════════════════════════════════════════════════════════════════════════════
const _RP_COL = { operating: "#0d6b5e", net: "#1e3a8a" };

function _rpMoney(v, currSym) {
  if (v == null) return "—";
  const a = Math.abs(v), s = v < 0 ? "−" : "";
  if (a >= 1e12) return `${s}${currSym}${(a / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `${s}${currSym}${(a / 1e9).toFixed(a >= 1e10 ? 1 : 2)}B`;
  if (a >= 1e6) return `${s}${currSym}${(a / 1e6).toFixed(0)}M`;
  if (a >= 1e3) return `${s}${currSym}${(a / 1e3).toFixed(0)}K`;
  return `${s}${currSym}${a.toFixed(0)}`;
}

function _rpPct(v, reason) {
  if (v == null) {
    const why = { negative_base: "the earlier period was a loss",
                  turned_to_loss: "swung from profit to loss",
                  restatement_mix: "the company restated between these two filings — the periods are on different accounting bases" }[reason];
    return why ? `<span class="peer-nm" title="${escapeHtml(why)}">n/m</span>` : "—";
  }
  return `<span class="${v >= 0 ? "vg-pos" : "vg-neg"}">${v > 0 ? "+" : ""}${v}%</span>`;
}

function _rpChart(rows, currSym) {
  const vals = rows.flatMap(r => [r.revenue, r.operating, r.net]).filter(v => v != null);
  if (!vals.length) return "";
  const W = 740, H = 236, padL = 54, padR = 10, padT = 14, padB = 28;
  const mx = Math.max(0, ...vals), mn = Math.min(0, ...vals), rng = (mx - mn) || 1;
  const n = rows.length, band = (W - padL - padR) / n;
  const cx = i => padL + band * (i + 0.5);
  const y = v => H - padB - (v - mn) / rng * (H - padT - padB);
  const barW = Math.min(38, band * 0.58);
  const y0 = y(0);

  const bars = rows.map((r, i) => {
    if (r.revenue == null) return "";
    const top = Math.min(y(r.revenue), y0), h = Math.abs(y(r.revenue) - y0);
    // A period reported on a restated basis is drawn hatched: the bar is real,
    // the step up or down from the previous bar is not a business change.
    const broken = !!r.basis_break;
    return `<rect x="${(cx(i) - barW / 2).toFixed(1)}" y="${top.toFixed(1)}" width="${barW.toFixed(1)}"
      height="${Math.max(0.5, h).toFixed(1)}" fill="${broken ? "url(#rpHatch)" : "var(--accent)"}"
      fill-opacity="${broken ? 1 : 0.16}"
      stroke="${broken ? "var(--prob-low)" : "var(--accent)"}" stroke-opacity="${broken ? 1 : 0.35}"
      stroke-width="${broken ? 1.2 : 0.8}"
      ${broken ? 'stroke-dasharray="3 2"' : ""}>
      <title>${escapeHtml(r.label)} · revenue ${_rpMoney(r.revenue, currSym)}${
        r.net != null ? ` · net ${_rpMoney(r.net, currSym)}` : ""}${
        r.net_margin != null ? ` (${r.net_margin}% margin)` : ""}${
        broken ? " — restated basis, not comparable with the previous period" : ""}</title></rect>`;
  }).join("");

  const line = (key) => {
    const pts = rows.map((r, i) => (r[key] == null ? null : [cx(i), y(r[key])])).filter(Boolean);
    if (pts.length < 1) return "";
    const poly = pts.length >= 2
      ? `<polyline points="${pts.map(p => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ")}"
          fill="none" stroke="${_RP_COL[key]}" stroke-width="2"/>` : "";
    const dots = rows.map((r, i) => r[key] == null ? "" :
      `<circle cx="${cx(i).toFixed(1)}" cy="${y(r[key]).toFixed(1)}" r="2.8" fill="${_RP_COL[key]}">
        <title>${escapeHtml(r.label)} · ${key === "net" ? "net" : "operating"} ${_rpMoney(r[key], currSym)}</title></circle>`).join("");
    return poly + dots;
  };

  // Only drawn when something is negative — a zero line on an all-positive
  // chart is just the axis.
  const zero = mn < 0
    ? `<line x1="${padL}" y1="${y0.toFixed(1)}" x2="${W - padR}" y2="${y0.toFixed(1)}"
        stroke="var(--line-strong)" stroke-width="1" stroke-dasharray="3 3"/>
       <text x="${padL - 6}" y="${(y0 + 3).toFixed(1)}" text-anchor="end" font-size="9" fill="var(--muted)">0</text>` : "";

  // Gridlines at the top, the middle and the baseline: without them a bar
  // chart with one axis label is a shape, not a measurement.
  const ticks = [...new Set([mx, mn < 0 ? mn : 0, mn + rng / 2])].filter(v => Number.isFinite(v));
  const axis = ticks.map(v => `
    <line x1="${padL}" y1="${y(v).toFixed(1)}" x2="${W - padR}" y2="${y(v).toFixed(1)}"
      stroke="var(--line)" stroke-width="1"/>
    <text x="${padL - 6}" y="${(y(v) + 3).toFixed(1)}" text-anchor="end" font-size="9"
      fill="var(--muted)">${_rpMoney(v, currSym)}</text>`).join("");

  const every = n > 9 ? 2 : 1;
  const labels = rows.map((r, i) => (i % every && i !== n - 1) ? "" :
    `<text x="${cx(i).toFixed(1)}" y="${H - 8}" text-anchor="middle" font-size="9"
      fill="var(--muted)">${escapeHtml(r.label.replace(/^FY/, "'").replace(/^Q(\d) (\d{2})(\d{2})$/, "Q$1 '$3"))}</text>`).join("");

  const defs = rows.some(r => r.basis_break)
    ? `<defs><pattern id="rpHatch" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
        <rect width="5" height="5" fill="var(--prob-low-bg)"/>
        <line x1="0" y1="0" x2="0" y2="5" stroke="var(--prob-low)" stroke-width="1.2" stroke-opacity=".5"/>
       </pattern></defs>` : "";
  return `<svg viewBox="0 0 ${W} ${H}" class="rp-chart">${defs}${axis}${zero}${bars}${line("operating")}${line("net")}${labels}</svg>`;
}

function _rpTable(rows, currSym, kind, marginLabel = "Net margin") {
  const body = [...rows].reverse().map(r => `
    <tr><td>${escapeHtml(r.label)}</td>
      <td class="rp-n">${_rpMoney(r.revenue, currSym)}${r.restated_level
        ? `<span class="rp-flag" title="${r.restated_level === "single_quarter_statement"
            ? "From the company's restated single-quarter statement — the year-to-date filings either side are of different vintages"
            : "Restated half-year less restated second quarter — the first quarter was never refiled on the restated basis"}">†</span>` : ""}</td>
      <td class="rp-n">${_rpPct(r.rev_yoy, r.rev_yoy_reason)}${r.base_restated && r.base_restated.revenue != null
        ? `<span class="rp-flag" title="Measured against the prior year as the company restated it in this year's report (${_rpMoney(r.base_restated.revenue, currSym)}), not the originally filed figure shown in the row below">†</span>` : ""}</td>
      <td class="rp-n">${_rpMoney(r.operating, currSym)}</td>
      <td class="rp-n">${_rpMoney(r.net, currSym)}${(r.flags || []).length
        ? `<span class="rp-flag" title="${escapeHtml(r.flags.includes("net_above_revenue")
            ? "net profit exceeds revenue — a one-off gain, or a bad figure"
            : "most of the bottom line did not come from operations")}">⚠</span>` : ""}</td>
      <td class="rp-n">${r.net_margin == null ? "—" : `${r.net_margin}%`}</td></tr>`).join("");
  return `<table class="rp-table">
    <thead><tr><th>Period</th><th class="rp-n">Revenue</th><th class="rp-n">${kind === "annual" ? "vs prior year" : "vs year-ago quarter"}</th>
      <th class="rp-n">Operating profit</th><th class="rp-n">Net profit</th><th class="rp-n">${escapeHtml(marginLabel)}</th></tr></thead>
    <tbody>${body}</tbody></table>`;
}

function _rpPanel(block, currSym, kind, marginLabel = "Net margin") {
  if (!block || !block.rows || !block.rows.length) return "";
  const t = block.revenue, nt = block.net;
  const per = kind === "annual" ? "year" : "quarter";
  const vs = kind === "annual" ? "vs prior year" : "vs the same quarter last year";
  const tiles = `
    <div class="vg-tile"><span class="vg-tile-lbl">Revenue, latest ${per}</span>
      <span class="vg-tile-val">${_rpMoney(t.latest, currSym)}</span>
      <span class="cm-sub">${_rpPct(t.yoy, t.yoy_reason)} ${vs}</span></div>
    <div class="vg-tile"><span class="vg-tile-lbl">Revenue growth</span>
      <span class="vg-tile-val">${_rpPct(t.cagr, t.cagr_reason)}</span>
      <span class="cm-sub">${t.span ? `a year, compounded, ${escapeHtml(t.span.from)} → ${escapeHtml(t.span.to)}` : "—"}</span></div>
    <div class="vg-tile"><span class="vg-tile-lbl">Net profit, latest ${per}</span>
      <span class="vg-tile-val">${_rpMoney(nt.latest, currSym)}</span>
      <span class="cm-sub">${_rpPct(nt.yoy, nt.yoy_reason)} ${vs}</span></div>
    <div class="vg-tile"><span class="vg-tile-lbl">${escapeHtml(marginLabel)}</span>
      <span class="vg-tile-val">${block.rows[block.rows.length - 1].net_margin == null ? "—"
        : `${block.rows[block.rows.length - 1].net_margin}%`}</span>
      <span class="cm-sub">latest ${per}</span></div>`;
  const warn = (block.warnings || []).map(w =>
    `<p class="peer-reclass rp-warn">${escapeHtml(w.text)}</p>`).join("");
  return `<div class="rp-panel" data-rp-panel="${kind}">
    <div class="vg-tiles rp-tiles">${tiles}</div>
    ${warn}
    <div class="rp-chart-wrap">${_rpChart(block.rows, currSym)}</div>
    <div class="rp-legend">
      <span class="cm-leg"><i style="background:var(--accent-light);border:1px solid var(--line-strong)"></i>Revenue</span>
      <span class="cm-leg"><i style="background:${_RP_COL.operating}"></i>Operating profit</span>
      <span class="cm-leg"><i style="background:${_RP_COL.net}"></i>Net profit</span>
      ${block.rows.some(r => r.basis_break)
        ? `<span class="cm-leg"><i style="background:var(--prob-low-bg);border:1px dashed var(--prob-low)"></i>restated basis</span>` : ""}
    </div>
    <details class="peer-basis"><summary>All ${kind === "annual" ? "years" : "quarters"} as a table</summary>
      <div class="rp-table-wrap">${_rpTable(block.rows, currSym, kind, marginLabel)}</div></details>
  </div>`;
}

function setupRevProfit(container) {
  const card = container.querySelector(".rp-card");
  if (!card) return;
  card.querySelectorAll(".rp-tab").forEach(btn => btn.addEventListener("click", () => {
    card.querySelectorAll(".rp-tab").forEach(b => b.classList.toggle("active", b === btn));
    card.querySelectorAll(".rp-panel").forEach(p => { p.hidden = p.dataset.rpPanel !== btn.dataset.rp; });
  }));
}

function renderRevProfitSection(rp, currSym = '$') {
  if (!rp || rp.error || (!rp.annual && !rp.quarterly)) return "";
  const have = [["annual", "Annual"], ["quarterly", "Quarterly"]].filter(([k]) => rp[k] && rp[k].rows && rp[k].rows.length);
  if (!have.length) return "";
  const first = have[0][0];
  const tabs = have.length > 1
    ? `<div class="rp-tabs">${have.map(([k, lbl]) =>
        `<button type="button" class="rp-tab${k === first ? " active" : ""}" data-rp="${k}">${lbl}</button>`).join("")}</div>`
    : "";
  const ttm = rp.ttm && rp.ttm.revenue != null
    ? `<p class="vg-note rp-ttm"><b>Last twelve months</b> (${escapeHtml(rp.ttm.label)}): revenue
        ${_rpMoney(rp.ttm.revenue, currSym)}, operating profit ${_rpMoney(rp.ttm.operating, currSym)},
        net profit ${_rpMoney(rp.ttm.net, currSym)}${rp.ttm.net_margin != null ? ` — a ${rp.ttm.net_margin}% net margin` : ""}.
        Added up from the last four quarters; the company never filed this figure.</p>` : "";
  const panels = have.map(([k]) => {
    const html = _rpPanel(rp[k], currSym, k, rp.net_margin_label || "Net margin");
    return k === first ? html : html.replace('<div class="rp-panel"', '<div class="rp-panel" hidden');
  }).join("");
  const limits = (rp.limits || []).length
    ? `<details class="peer-basis"><summary>How these figures are built</summary><ul>
        ${Object.entries(rp.basis || {}).map(([k, v]) =>
          `<li><b>${escapeHtml(k)}:</b> ${escapeHtml(v)}</li>`).join("")}
        ${(rp.limits || []).map(v => `<li>${escapeHtml(v)}</li>`).join("")}
       </ul></details>` : "";
  return `
  <div class="sc-card rp-card">
    <div class="sc-card-header">
      <div class="sc-card-title">Revenue &amp; Profit</div>
      <span class="fund-tag">how big the business is, and what it earns</span>
    </div>
    ${tabs}${panels}${ttm}${limits}
  </div>`;
}


// ════════════════════════════════════════════════════════════════════════════
// Forecasts (A-shares) — two sources that must not be blurred: what BROKERS
// expect (Tushare report_rc, an outside opinion) and what the COMPANY has said
// (业绩预告, a number it will be held to). Two blocks, never averaged.
// ════════════════════════════════════════════════════════════════════════════
const _FC_DAY = v => (v && v.length === 8) ? `${v.slice(0, 4)}-${v.slice(4, 6)}-${v.slice(6)}` : "—";

function _fcRange(c, currSym, isEps) {
  // median, with the spread across brokers underneath
  if (!c || c.state === "none") return "—";
  if (c.state === "inconsistent")
    return `<span class="peer-nm" title="The brokers' figures differ by more than ten times — a unit error in the feed, with no way to tell whose. Not published.">n/a</span>`;
  const f = v => isEps ? `${currSym}${(+v).toFixed(2)}` : _rpMoney(v, currSym);
  const spread = c.state === "consensus" && c.low !== c.high
    ? `<span class="fc-spread">${f(c.low)} – ${f(c.high)}</span>` : "";
  const one = c.state === "single" ? `<span class="fc-spread" title="Only one broker forecast this year — a single view, not a consensus">1 broker</span>` : "";
  const cut = (c.excluded || []).length
    ? `<span class="rp-flag" title="${escapeHtml(c.excluded.join(", "))} left out: more than ten times off the median, which is a unit error rather than a view">⚠</span>` : "";
  return `<b>${f(c.median)}</b>${cut}${spread}${one}`;
}

function _fcGuidance(g, currSym) {
  if (!g || !(g.items || []).length) return "";
  const rng = it => it.np_low == null ? "—"
    : it.np_low === it.np_high ? _rpMoney(it.np_low, currSym)
    : `${_rpMoney(it.np_low, currSym)} – ${_rpMoney(it.np_high, currSym)}`;
  const chg = it => {
    if (it.growth_low == null) return _rpPct(null, it.growth_reason);
    const f = v => `${v > 0 ? "+" : ""}${v}%`;
    return `<span class="${it.growth_low >= 0 ? "vg-pos" : "vg-neg"}">${it.growth_low === it.growth_high ? f(it.growth_low) : `${f(it.growth_low)} to ${f(it.growth_high)}`}</span>`;
  };
  const outcome = it => it.pending ? `<span class="fc-pending">not yet reported</span>`
    : it.actual == null ? "reported"
    : `${_rpMoney(it.actual, currSym)} <span class="fc-out fc-out-${it.outcome}">${
        it.outcome === "within" ? "in range" : it.outcome === "above" ? "above range" : "below range"}</span>`;
  const rows = g.items.map(it => `
    <tr><td><b>${escapeHtml(it.period)}</b>${it.revised ? ` <span class="qg-flag" title="The company changed these figures after first announcing them on ${_FC_DAY(it.first_announced)}">revised</span>` : ""}</td>
      <td>${escapeHtml(it.type || "—")}${it.type_en ? ` <span class="peer-dim">${escapeHtml(it.type_en)}</span>` : ""}</td>
      <td class="rp-n">${rng(it)}</td>
      <td class="rp-n">${chg(it)}</td>
      <td class="rp-n">${_FC_DAY(it.announced)}</td>
      <td class="rp-n">${outcome(it)}</td></tr>`).join("");
  return `
    <div class="fc-block">
      <div class="fc-block-title">Company guidance <span class="fc-src">业绩预告 · the company's own pre-announcement</span></div>
      <div class="rp-table-wrap"><table class="rp-table">
        <thead><tr><th>Period</th><th>Direction</th><th class="rp-n">Guided net profit</th>
          <th class="rp-n">vs year before</th><th class="rp-n">Announced</th><th class="rp-n">Reported</th></tr></thead>
        <tbody>${rows}</tbody></table></div>
      <p class="vg-note">Periods are year-to-date: H1 is January–June, 9M January–September. Net profit attributable to shareholders (归母净利润).</p>
    </div>`;
}

function _fcBroker(b, currSym, err) {
  const title = `<div class="fc-block-title">Broker forecasts <span class="fc-src">券商盈利预测 · Tushare report_rc</span></div>`;
  // Not loaded, or nothing found: the headline tile already says which, in
  // full. A second, greyer copy of the same sentence down here adds nothing.
  if (!b || b.status !== "ready") return "";
  const cov = b.coverage || {};
  const reach = cov.complete ? `Looked through all ${cov.horizon_days} days of broker reports.`
    : `Only reports since ${_FC_DAY(cov.covered_from)} are loaded so far (${cov.covered_days} of ${cov.horizon_days} days); the rest is still being fetched.`;
  const y1 = b.first_year;
  const rows = b.years.map(y => `
    <tr><td><b>${escapeHtml(y.label)}</b><span class="peer-dim"> · ${y.n_brokers} broker${y.n_brokers === 1 ? "" : "s"}</span></td>
      <td class="rp-n">${_fcRange(y.revenue, currSym)}</td>
      <td class="rp-n">${_rpPct(y.rev_growth, y.rev_growth_reason)}</td>
      <td class="rp-n">${_fcRange(y.net_profit, currSym)}</td>
      <td class="rp-n">${_rpPct(y.np_growth, y.np_growth_reason)}</td>
      <td class="rp-n">${_fcRange(y.eps, currSym, true)}</td>
      <td class="rp-n">${y.pe != null ? `${y.pe}×` : y.pe_reason ? `<span class="peer-nm" title="A loss is forecast, so there is no multiple">n/m</span>` : "—"}</td></tr>`).join("");

  const brokers = b.brokers.map(x => {
    const cls = x.rating_bucket === "positive" ? "vg-pos" : x.rating_bucket === "negative" ? "vg-neg" : "peer-dim";
    const tgt = x.target_low == null && x.target_high == null ? "—"
      : x.target_low != null && x.target_high != null && x.target_low !== x.target_high
        ? `${currSym}${x.target_low}–${x.target_high}` : `${currSym}${x.target_high ?? x.target_low}`;
    return `<tr><td>${escapeHtml(x.org)}</td><td class="rp-n">${_FC_DAY(x.date)}</td>
      <td><span class="${cls}">${escapeHtml(x.rating)}</span></td>
      <td class="rp-n">${_rpMoney(x.np, currSym)}</td>
      <td class="rp-n">${x.eps == null ? "—" : `${currSym}${(+x.eps).toFixed(2)}`}</td>
      <td class="rp-n">${tgt}</td>
      <td class="fc-title-cell" title="${escapeHtml(x.title || "")}">${escapeHtml(x.title || "—")}</td></tr>`;
  }).join("");

  return `
    <div class="fc-block">
      ${title}
      <p class="vg-note fc-count"><b>${b.n_brokers} broker${b.n_brokers === 1 ? "" : "s"}</b>, ${b.n_reports} report${b.n_reports === 1 ? "" : "s"}
        between ${_FC_DAY(b.from)} and ${_FC_DAY(b.to)}${b.ratings ? ` · ratings: <span class="vg-pos">${b.ratings.positive} positive</span>, ${b.ratings.neutral} neutral, <span class="vg-neg">${b.ratings.negative} negative</span>${b.ratings.unrated ? `, ${b.ratings.unrated} gave none` : ""}` : ""}${
        b.targets && !b.targets.n ? ` · none gave a target price` : ""}.</p>
      <div class="rp-table-wrap"><table class="rp-table fc-table">
        <thead><tr><th>Year</th><th class="rp-n">Revenue</th><th class="rp-n">growth</th>
          <th class="rp-n">Net profit</th><th class="rp-n">growth</th><th class="rp-n">EPS</th>
          <th class="rp-n" title="Today's market value divided by that year's forecast profit">P/E at today's price</th></tr></thead>
        <tbody>${rows}</tbody></table></div>
      <p class="vg-note">Each figure is the <b>median across brokers</b>, with the lowest and highest beneath it; a broker counts once
        per year, by its most recent report. ${b.years[0] && b.years[0].vs ? `FY${y1}E growth is against the ${escapeHtml(b.years[0].vs.replace(/^FY/, "FY"))}; later years against the year before.` : ""}
        ${reach}</p>
      <details class="peer-basis"><summary>Broker by broker (${b.brokers.length})</summary>
        <div class="rp-table-wrap"><table class="rp-table">
          <thead><tr><th>Broker</th><th class="rp-n">Latest report</th><th>Rating</th>
            <th class="rp-n">FY${y1}E net profit</th><th class="rp-n">FY${y1}E EPS</th><th class="rp-n">Target</th><th>Report</th></tr></thead>
          <tbody>${brokers}</tbody></table></div></details>
      <details class="peer-basis"><summary>What this is, and what it leaves out</summary><ul>
        <li><b>Onshore consensus, not the whole street.</b> The feed is almost entirely mainland brokers; Goldman, Morgan Stanley, UBS and similar are largely absent.</li>
        <li><b>P/E is recomputed</b> from consensus net profit and today's market value. The feed's own P/E field is each broker's figure at its own price on its own day, and is sometimes plainly wrong, so it is not used.</li>
        <li><b>Net profit is attributable to shareholders</b> (归母净利润) — which is why the first year is compared with that line of the last annual report, not with net profit including minorities.</li>
        <li><b>Targets are rare.</b> Most reports carry none; the tile says how many did.</li>
        <li><b>Ratings are each broker's own scale</b>, grouped into positive / neutral / negative. Brokers publish almost no negative ratings, so a wall of "buy" is the baseline, not a signal.</li>
        <li>Metadata only: Tushare provides no link to, or text of, the reports themselves.</li>
      </ul></details>
    </div>`;
}

// The headline: the two or three numbers the card exists for, at a size that
// can be read from across the room. Everything below it is the working.
function _fcHero(f, currSym) {
  const items = [];
  const g = f.guidance, g0 = g && g.items && g.items[0];
  // Guidance earns the headline only while it is news: for a period not yet
  // reported, or the one just reported. Moutai's FY2024 guidance is history.
  if (g0 && (g0.pending || (g.latest_reported_end && g0.end_date >= g.latest_reported_end))) {
    const rng = g0.np_low == null ? "—" : g0.np_low === g0.np_high ? _rpMoney(g0.np_low, currSym)
      : `${_rpMoney(g0.np_low, currSym)} – ${_rpMoney(g0.np_high, currSym)}`;
    const chip = g0.pending ? `<span class="fc-chip fc-chip-pending">not yet reported</span>`
      : g0.actual != null ? `<span class="fc-chip fc-chip-${g0.outcome}">reported ${_rpMoney(g0.actual, currSym)} · ${
          g0.outcome === "within" ? "in range" : g0.outcome === "above" ? "above range" : "below range"}</span>` : "";
    items.push(`<div class="fc-hero-item${g0.pending ? " fc-hero-key" : ""}">
      <span class="fc-hero-lbl">Company guidance · ${escapeHtml(g0.period)} net profit</span>
      <span class="fc-hero-val">${rng}</span>
      <span class="fc-hero-sub">${escapeHtml(g0.type || "")}${g0.type_en ? ` · ${escapeHtml(g0.type_en)}` : ""} · announced ${_FC_DAY(g0.announced)}</span>
      ${chip}</div>`);
  }
  const b = f.broker;
  if (b && b.status === "ready" && b.years.length) {
    const y = b.years[0], y2 = b.years[1], np = y.net_profit;
    const val = np.median != null ? _rpMoney(np.median, currSym) : "n/a";
    const growth = y.np_growth != null
      ? `<span class="${y.np_growth >= 0 ? "vg-pos" : "vg-neg"}">${y.np_growth > 0 ? "+" : ""}${y.np_growth}%</span> vs ${escapeHtml(y.vs || "last year")}`
      : (y.np_growth_reason ? `growth n/m vs ${escapeHtml(y.vs || "last year")}` : "");
    items.push(`<div class="fc-hero-item fc-hero-key">
      <span class="fc-hero-lbl">Brokers expect · FY${y.year} net profit</span>
      <span class="fc-hero-val">${val}</span>
      <span class="fc-hero-sub">${growth}${np.state === "consensus" && np.low !== np.high
        ? `${growth ? " · " : ""}range ${_rpMoney(np.low, currSym)} – ${_rpMoney(np.high, currSym)}` : ""}</span>
      <span class="fc-chip">${np.state === "single" ? "1 broker — not a consensus" : `median of ${np.n} brokers`}</span></div>`);
    if (y.pe != null) items.push(`<div class="fc-hero-item">
      <span class="fc-hero-lbl">Price ÷ FY${y.year} forecast profit</span>
      <span class="fc-hero-val">${y.pe}×</span>
      <span class="fc-hero-sub">at today's price${y2 && y2.pe != null ? ` · ${y2.pe}× on FY${y2.year}` : ""}</span></div>`);
    const r = b.ratings, rv = b.revisions;
    if (r) items.push(`<div class="fc-hero-item">
      <span class="fc-hero-lbl">Brokers positive</span>
      <span class="fc-hero-val">${r.positive}<span class="fc-hero-of"> of ${b.n_brokers}</span></span>
      <span class="fc-hero-sub">${rv && rv.n ? `<span class="vg-pos">▲ ${rv.up}</span> raised · <span class="vg-neg">▼ ${rv.down}</span> cut their FY${rv.year} profit` : `${r.neutral} neutral · ${r.negative} negative`}</span></div>`);
    const t = b.targets;
    if (t && t.n) items.push(`<div class="fc-hero-item">
      <span class="fc-hero-lbl">Target price</span>
      <span class="fc-hero-val">${currSym}${t.median}</span>
      <span class="fc-hero-sub">${t.upside_pct != null ? `<span class="${t.upside_pct >= 0 ? "vg-pos" : "vg-neg"}">${t.upside_pct > 0 ? "+" : ""}${t.upside_pct}%</span> vs today · ` : ""}only ${t.n} of ${t.of} brokers gave one</span></div>`);
  } else {
    const cov = (b && b.coverage) || {};
    const paused = cov.cooldown_remaining_min > 0;
    const msg = !b ? "unavailable" : !cov.covered_from ? (paused ? "waiting on quota" : "loading…") : cov.complete ? "none published" : "none found yet";
    const sub = !b ? escapeHtml(f.broker_error || "")
      : !cov.covered_from ? (paused
          ? `Tushare's request quota for broker reports is used up — nothing is loaded yet. Next attempt in about ${cov.cooldown_remaining_min >= 90 ? Math.round(cov.cooldown_remaining_min / 60) + " hours" : Math.round(cov.cooldown_remaining_min) + " minutes"}.`
          : "Broker reports are still being fetched from Tushare — nothing is loaded yet.")
      : cov.complete ? `No broker forecast for this stock in the last ${cov.horizon_days} days.`
      : `Loaded back to ${_FC_DAY(cov.covered_from)} so far (${cov.covered_days} of ${cov.horizon_days} days) — it may be covered further back.`;
    items.push(`<div class="fc-hero-item fc-hero-wait">
      <span class="fc-hero-lbl">Broker forecasts</span>
      <span class="fc-hero-val">${msg}</span>
      <span class="fc-hero-sub">${sub}</span></div>`);
  }
  return items.length ? `<div class="fc-hero">${items.join("")}</div>` : "";
}

// Actual years beside forecast years, on one axis: an expected figure means
// little until it is standing next to what the company has already done.
function _fcBars(title, pts, currSym, fmt = null, who = "brokers") {
  const money = fmt || (v => _rpMoney(v, currSym));
  const have = pts.filter(p => p.v != null);
  if (have.length < 2 || !have.some(p => p.est)) return "";
  const W = 380, H = 200, padL = 12, padR = 12, padT = 26, padB = 26;
  const all = have.flatMap(p => [p.v, p.lo, p.hi]).filter(v => v != null);
  const mx = Math.max(0, ...all), mn = Math.min(0, ...all), rng = (mx - mn) || 1;
  const band = (W - padL - padR) / have.length, bw = Math.min(46, band * 0.6);
  const cx = i => padL + band * (i + 0.5);
  const y = v => H - padB - (v - mn) / rng * (H - padT - padB);
  const y0 = y(0);
  const firstEst = have.findIndex(p => p.est);
  const bars = have.map((p, i) => {
    const top = Math.min(y(p.v), y0), h = Math.max(1, Math.abs(y(p.v) - y0));
    const whisk = p.est && p.lo != null && p.hi != null && p.lo !== p.hi
      ? `<line x1="${cx(i).toFixed(1)}" y1="${y(p.hi).toFixed(1)}" x2="${cx(i).toFixed(1)}" y2="${y(p.lo).toFixed(1)}" stroke="var(--text)" stroke-width="1.2"/>
         <line x1="${(cx(i) - 5).toFixed(1)}" y1="${y(p.hi).toFixed(1)}" x2="${(cx(i) + 5).toFixed(1)}" y2="${y(p.hi).toFixed(1)}" stroke="var(--text)" stroke-width="1.2"/>
         <line x1="${(cx(i) - 5).toFixed(1)}" y1="${y(p.lo).toFixed(1)}" x2="${(cx(i) + 5).toFixed(1)}" y2="${y(p.lo).toFixed(1)}" stroke="var(--text)" stroke-width="1.2"/>` : "";
    // A loss hangs below the baseline, where its label would land on the year
    // labels — so a negative figure is printed above the baseline instead.
    const labelY = p.v >= 0 ? Math.min(top, p.hi != null ? y(p.hi) : top) - 6 : y0 - 6;
    return `<rect x="${(cx(i) - bw / 2).toFixed(1)}" y="${top.toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" rx="2"
        fill="${p.est ? "var(--accent)" : "#94a3b8"}" fill-opacity="${p.est ? 0.9 : 0.75}">
        <title>${escapeHtml(p.label)} · ${money(p.v)}${p.est && p.lo != null ? ` (${who}: ${money(p.lo)} – ${money(p.hi)})` : ""}</title></rect>
      ${whisk}
      <text x="${cx(i).toFixed(1)}" y="${labelY.toFixed(1)}" text-anchor="middle" font-size="11" font-weight="700"
        fill="${p.est ? "var(--accent)" : "var(--text)"}">${money(p.v)}</text>
      <text x="${cx(i).toFixed(1)}" y="${H - 8}" text-anchor="middle" font-size="10.5" font-weight="${p.est ? 700 : 400}"
        fill="${p.est ? "var(--accent)" : "var(--muted)"}">${escapeHtml(p.label)}</text>`;
  }).join("");
  const sepX = padL + band * firstEst;
  const sep = firstEst > 0 ? `<line x1="${sepX.toFixed(1)}" y1="${padT - 12}" x2="${sepX.toFixed(1)}" y2="${H - padB}" stroke="var(--line-strong)" stroke-dasharray="3 3"/>
      <text x="${(sepX + 5).toFixed(1)}" y="${padT - 14}" font-size="9" fill="var(--accent)" font-weight="700">FORECAST →</text>
      <text x="${(sepX - 5).toFixed(1)}" y="${padT - 14}" font-size="9" fill="var(--muted)" text-anchor="end">REPORTED</text>` : "";
  const zero = `<line x1="${padL}" y1="${y0.toFixed(1)}" x2="${W - padR}" y2="${y0.toFixed(1)}" stroke="var(--line-strong)" stroke-width="1"/>`;
  return `<div class="fc-chart-box"><div class="fc-chart-title">${title}</div>
    <svg viewBox="0 0 ${W} ${H}" class="fc-chart">${zero}${sep}${bars}</svg></div>`;
}

function _fcCharts(f, currSym) {
  const b = f.broker;
  if (!b || b.status !== "ready") return "";
  const hist = f.history || [];
  const series = (actKey, estKey) => [
    ...hist.map(h => ({ label: `FY${h.year}`, v: h[actKey], est: false })),
    ...b.years.filter(y => y[estKey].median != null)
      .map(y => ({ label: `FY${y.year}E`, v: y[estKey].median, lo: y[estKey].low, hi: y[estKey].high, est: true })),
  ];
  const a = _fcBars("Net profit <span class=\"fc-src\">attributable to shareholders</span>", series("net_parent", "net_profit"), currSym);
  const r = _fcBars("Revenue", series("revenue", "revenue"), currSym);
  if (!a && !r) return "";
  return `<div class="fc-charts">${a}${r}</div>
    <p class="vg-note fc-chart-note">Grey bars are reported years; green bars are the median of broker forecasts, with the line through each showing the lowest and highest broker.</p>`;
}

// ── US forecasts: what analysts expect (us_forecasts.py, Yahoo Finance) ─────
const _UF_SYM = { USD: "$", TWD: "NT$", CNY: "¥", HKD: "HK$", EUR: "€", JPY: "¥", GBP: "£", KRW: "₩",
                  CAD: "C$", AUD: "A$", INR: "₹", BRL: "R$", CHF: "CHF ", SEK: "SEK ", DKK: "DKK " };
const _ufSym = c => _UF_SYM[c] ?? (c ? `${c} ` : "$");
function _ufPx(v, s) {
  if (v == null) return "—";
  const a = Math.abs(v), dp = a < 100 ? 2 : 0;
  return `${v < 0 ? "−" : ""}${s}${a.toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp })}`;
}
function _ufDay(iso, withYear) {
  if (!iso) return "—";
  const d = new Date(`${iso}T00:00:00`);
  const other = withYear ?? d.getFullYear() !== new Date().getFullYear();
  return d.toLocaleDateString("en-US", other ? { month: "short", day: "numeric", year: "numeric" } : { month: "short", day: "numeric" });
}
const _ufAge = n => n === 0 ? "today" : n === 1 ? "yesterday" : n < 60 ? `${n} days ago` : `${Math.round(n / 30.4)} months ago`;
const _ufSigned = (v, suffix = "%") => v == null ? "—"
  : `<span class="${v >= 0 ? "vg-pos" : "vg-neg"}">${v > 0 ? "+" : ""}${v}${suffix}</span>`;

function _ufHero(f, sym) {
  const items = [], t = f.targets, fm = f.firms, r = f.ratings;
  const years = (f.estimates || []).filter(e => e.annual), y0 = years[0], y1 = years[1];
  if (t) {
    const mid = t.median ?? t.mean;
    const stale = fm && fm.stale
      ? `<span class="fc-chip fc-chip-pending" title="A consensus is an average of targets set at different times. This is the age of the most recent change Yahoo names.">newest named update ${_ufAge(fm.newest_age_days)}${
          fm.since_newest_pct != null ? ` · stock ${fm.since_newest_pct > 0 ? "+" : ""}${fm.since_newest_pct}% since` : ""}</span>` : "";
    items.push(`<div class="fc-hero-item fc-hero-key">
      <span class="fc-hero-lbl">Analyst target price · ${t.basis}</span>
      <span class="fc-hero-val">${_ufPx(mid, sym)}</span>
      <span class="fc-hero-sub">${_ufSigned(t.upside_pct)} vs today's ${_ufPx(f.price, sym)}${
        t.low != null && t.high != null ? ` · range ${_ufPx(t.low, sym)} – ${_ufPx(t.high, sym)}` : ""}${
        t.basis === "median" && t.mean != null ? ` · mean ${_ufPx(t.mean, sym)}` : ""}</span>
      <span class="fc-chip">${t.n ? `${t.n} analyst${t.n === 1 ? "" : "s"}` : "analyst count not given"}</span>${stale}</div>`);
  }
  if (r) {
    const b = r.before;
    items.push(`<div class="fc-hero-item">
      <span class="fc-hero-lbl">Analysts positive</span>
      <span class="fc-hero-val">${r.positive}<span class="fc-hero-of"> of ${r.n}</span></span>
      <span class="fc-hero-sub">${r.neutral} hold · ${r.negative} sell${
        b ? ` · ${b.months_ago} month${b.months_ago === 1 ? "" : "s"} ago: ${b.positive} of ${b.n}` : ""}</span></div>`);
  }
  if (y0 && y0.eps) {
    const e = y0.eps, rv = y0.revisions_30, es = _ufSym(f.estimate_currency);
    const g = e.growth != null ? `${_ufSigned(e.growth)} vs ${_ufPx(e.year_ago, es)} last year`
      : e.year_ago != null ? `vs ${_ufPx(e.year_ago, es)} last year` : "";
    items.push(`<div class="fc-hero-item fc-hero-key">
      <span class="fc-hero-lbl">Expected · ${escapeHtml(y0.label)} EPS</span>
      <span class="fc-hero-val">${_ufPx(e.avg, es)}</span>
      <span class="fc-hero-sub">${g}${e.low != null && e.high != null && e.low !== e.high
        ? `${g ? " · " : ""}range ${_ufPx(e.low, es)} – ${_ufPx(e.high, es)}` : ""}</span>
      <span class="fc-chip">${e.n ? `mean of ${e.n} analyst${e.n === 1 ? "" : "s"}` : "analysts' mean"}${
        rv && (rv.up || rv.down) ? ` · last 30 days: ▲ ${rv.up} raised, ▼ ${rv.down} cut` : ""}</span></div>`);
    if (y0.pe != null) items.push(`<div class="fc-hero-item">
      <span class="fc-hero-lbl">Price ÷ ${escapeHtml(y0.label)} EPS</span>
      <span class="fc-hero-val">${y0.pe}×</span>
      <span class="fc-hero-sub">at today's price${y1 && y1.pe != null ? ` · ${y1.pe}× on ${escapeHtml(y1.label)}` : ""}</span></div>`);
    else if (y0.pe_reason) items.push(`<div class="fc-hero-item">
      <span class="fc-hero-lbl">Price ÷ ${escapeHtml(y0.label)} EPS</span>
      <span class="fc-hero-val">n/m</span>
      <span class="fc-hero-sub">a loss is forecast, so there is no multiple${y1 && y1.pe != null ? ` · ${y1.pe}× on ${escapeHtml(y1.label)}` : ""}</span></div>`);
  }
  const ne = f.next_earnings;
  if (ne) {
    const es = _ufSym(f.estimate_currency);
    items.push(`<div class="fc-hero-item">
      <span class="fc-hero-lbl">Next earnings</span>
      <span class="fc-hero-val">${_ufDay(ne.date)}</span>
      <span class="fc-hero-sub">${ne.days === 0 ? "today" : ne.days === 1 ? "tomorrow" : `in ${ne.days} days`}${
        ne.estimated ? " · date not yet confirmed" : ""}${ne.eps_avg != null ? ` · expected EPS ${_ufPx(ne.eps_avg, es)}` : ""}</span></div>`);
  }
  return items.length ? `<div class="fc-hero">${items.join("")}</div>` : "";
}

function _ufEstimates(f, sym) {
  const est = f.estimates || [];
  if (!est.length) return "";
  const es = _ufSym(f.estimate_currency), rs = _ufSym(f.revenue_currency);
  const cell = (e, fmt) => !e ? "—" : `<b>${fmt(e.avg)}</b>${e.low != null && e.high != null && e.low !== e.high
    ? `<span class="fc-spread">${fmt(e.low)} – ${fmt(e.high)}</span>` : ""}`;
  const was = e => {
    const tr = e.eps_trend;
    if (!tr || tr.d90 == null) return "—";
    const arrow = tr.direction_90 === "up" ? `<span class="vg-pos">▲</span>` : tr.direction_90 === "down" ? `<span class="vg-neg">▼</span>` : "＝";
    return `${_ufPx(tr.d90, es)} ${arrow}${tr.change_90_pct != null && tr.direction_90 !== "flat"
      ? ` <span class="fc-spread">${tr.change_90_pct > 0 ? "+" : ""}${tr.change_90_pct}% since</span>` : ""}`;
  };
  const rev = e => { const v = e.revisions_30; return !v ? "—" : `<span class="vg-pos">▲ ${v.up}</span> <span class="vg-neg">▼ ${v.down}</span>`; };
  const rows = est.map(e => `
    <tr><td><b>${escapeHtml(e.label)}</b>${e.end ? `<span class="fc-spread">to ${_ufDay(e.end, true)}${e.eps && e.eps.n ? ` · ${e.eps.n} analysts` : ""}</span>` : ""}</td>
      <td class="rp-n">${cell(e.eps, v => _ufPx(v, es))}</td>
      <td class="rp-n">${e.eps ? _rpPct(e.eps.growth, e.eps.growth_reason) : "—"}</td>
      <td class="rp-n">${cell(e.revenue, v => _rpMoney(v, rs))}</td>
      <td class="rp-n">${e.revenue ? _rpPct(e.revenue.growth, e.revenue.growth_reason) : "—"}</td>
      <td class="rp-n">${e.pe != null ? `${e.pe}×` : e.pe_reason ? `<span class="peer-nm" title="A loss is forecast, so there is no multiple">n/m</span>` : "—"}</td>
      <td class="rp-n">${was(e)}</td>
      <td class="rp-n">${rev(e)}</td></tr>`).join("");
  return `
    <div class="fc-block">
      <div class="fc-block-title">Estimates <span class="fc-src">EPS and revenue, quarter and fiscal year</span></div>
      <div class="rp-table-wrap"><table class="rp-table fc-table">
        <thead><tr><th>Period</th><th class="rp-n">EPS</th><th class="rp-n" title="Against the same period a year before">growth</th>
          <th class="rp-n">Revenue</th><th class="rp-n" title="Against the same period a year before">growth</th>
          <th class="rp-n" title="Today's price divided by that year's expected EPS">P/E today</th>
          <th class="rp-n" title="The EPS estimate 90 days ago, and which way it has moved since">EPS 90d ago</th>
          <th class="rp-n" title="Analysts who raised or cut their EPS estimate in the last 30 days">Revised 30d</th></tr></thead>
        <tbody>${rows}</tbody></table></div>
      <p class="vg-note">Each figure is the mean across analysts, with the lowest and highest beneath it. EPS is on the analysts' own basis,
        usually adjusted — not the GAAP figure in the company's filings — and the year before is the figure Yahoo pairs with the estimate.
        ${f.currency_note ? escapeHtml(f.currency_note) : ""}${f.revenue_currency && f.revenue_currency !== f.currency
          ? ` Revenue estimates are in ${escapeHtml(f.revenue_currency)}.` : ""}</p>
    </div>`;
}

function _ufFirms(f, sym) {
  const fm = f.firms;
  if (!fm || !fm.items.length) return "";
  const t = f.targets;
  const row = x => {
    const cls = x.rating_bucket === "positive" ? "vg-pos" : x.rating_bucket === "negative" ? "vg-neg" : "";
    const act = x.changed_rating && x.from_rating ? `${x.action} from ${escapeHtml(x.from_rating)}` : (x.action || "");
    return `<tr class="${x.age_days > 120 ? "fc-old" : ""}"><td>${escapeHtml(x.firm)}</td>
      <td class="rp-n">${_ufDay(x.date)}<span class="fc-spread">${_ufAge(x.age_days)}</span></td>
      <td><span class="${cls}">${escapeHtml(x.rating || "—")}</span>${act ? `<span class="fc-spread">${escapeHtml(act)}</span>` : ""}</td>
      <td class="rp-n">${x.target == null ? "—" : `<b>${_ufPx(x.target, sym)}</b>`}${x.prior_target != null ? `<span class="fc-spread">was ${_ufPx(x.prior_target, sym)}</span>` : ""}</td>
      <td class="rp-n">${_ufSigned(x.target_vs_price_pct)}</td>
      <td class="rp-n">${_ufSigned(x.stock_since_pct)}${x.price_then != null ? `<span class="fc-spread">from ${_ufPx(x.price_then, sym)}</span>` : ""}</td></tr>`;
  };
  const head = `<thead><tr><th>Firm</th><th class="rp-n">Last change</th><th>Rating</th><th class="rp-n">Target</th>
      <th class="rp-n" title="That target against today's price">Target vs today</th>
      <th class="rp-n" title="What the share price has done since the day of that change">Stock since</th></tr></thead>`;
  const first = fm.items.slice(0, 8), rest = fm.items.slice(8);
  return `
    <div class="fc-block">
      <div class="fc-block-title">Firm by firm <span class="fc-src">each firm's most recent rating or target change, last 12 months</span></div>
      <p class="vg-note fc-count">${t && t.n ? `The consensus above covers <b>${t.n} analyst${t.n === 1 ? "" : "s"}</b>; ` : ""}Yahoo names <b>${fm.n} firm${fm.n === 1 ? "" : "s"}</b> here.
        The most recent change was <b>${_ufDay(fm.newest)}</b> (${_ufAge(fm.newest_age_days)})${
        fm.since_newest_pct != null ? `, and the stock is ${_ufSigned(fm.since_newest_pct)} since` : ""}.</p>
      <div class="rp-table-wrap"><table class="rp-table fc-table">${head}<tbody>${first.map(row).join("")}</tbody></table></div>
      ${rest.length ? `<details class="peer-basis"><summary>${rest.length} more firm${rest.length === 1 ? "" : "s"}</summary>
        <div class="rp-table-wrap"><table class="rp-table fc-table">${head}<tbody>${rest.map(row).join("")}</tbody></table></div></details>` : ""}
      <p class="vg-note">A target is a view as of its date. Rows more than four months old are greyed: where the stock has moved far since,
        the target was set for a different price.</p>
    </div>`;
}

function _ufSurprises(f) {
  const s = f.surprises;
  if (!s || !s.items.length) return "";
  const es = _ufSym(f.estimate_currency);
  const rows = s.items.map(x => `
    <tr><td><b>${escapeHtml(x.label)}</b></td>
      <td class="rp-n">${_ufPx(x.estimate, es)}</td><td class="rp-n">${_ufPx(x.actual, es)}</td>
      <td class="rp-n">${x.diff > 0 ? "+" : x.diff < 0 ? "−" : ""}${es}${Math.abs(x.diff).toFixed(2)}${x.surprise_pct != null ? `<span class="fc-spread">${x.surprise_pct > 0 ? "+" : ""}${x.surprise_pct}%</span>` : ""}</td>
      <td class="rp-n"><span class="fc-out fc-out-${x.result === "beat" ? "above" : x.result === "miss" ? "below" : "within"}">${x.result}</span></td></tr>`).join("");
  return `
    <div class="fc-block">
      <div class="fc-block-title">How the last ${s.n} quarter${s.n === 1 ? "" : "s"} landed <span class="fc-src">reported EPS against the estimate going in</span></div>
      <p class="vg-note fc-count">Beat in <b>${s.beats} of ${s.n}</b>${s.misses ? `, missed in ${s.misses}` : ""}.</p>
      <div class="rp-table-wrap"><table class="rp-table">
        <thead><tr><th>Quarter</th><th class="rp-n">Estimate</th><th class="rp-n">Reported</th><th class="rp-n">Difference</th><th class="rp-n">Result</th></tr></thead>
        <tbody>${rows}</tbody></table></div>
    </div>`;
}

function renderUsForecastsSection(f, currSym = "$") {
  const sym = currSym || _ufSym(f.currency);
  const es = _ufSym(f.estimate_currency), rs = _ufSym(f.revenue_currency);
  const rev = _fcBars(`Revenue${f.revenue_currency && f.revenue_currency !== f.currency ? ` <span class="fc-src">${escapeHtml(f.revenue_currency)}</span>` : ""}`,
    f.revenue_path || [], rs, null, "analysts");
  const eps = _fcBars(`EPS <span class="fc-src">analysts' basis, usually adjusted</span>`, f.eps_path || [], es, v => _ufPx(v, es), "analysts");
  const charts = rev || eps ? `<div class="fc-charts">${rev}${eps}</div>
    <p class="vg-note fc-chart-note">Grey bars are reported years; green bars are the mean analyst estimate, with the line through each showing the lowest and highest analyst.</p>` : "";
  return `
  <div class="sc-card fc-card">
    <div class="sc-card-header">
      <div class="sc-card-title">Forecasts</div>
      <span class="fund-tag">what analysts expect — targets, ratings and estimates · Yahoo Finance</span>
    </div>
    ${_ufHero(f, sym)}${charts}
    ${_ufEstimates(f, sym)}${_ufFirms(f, sym)}${_ufSurprises(f)}
    <details class="peer-basis"><summary>What this is, and what it leaves out</summary><ul>
      <li><b>One source.</b> Every number here is Yahoo Finance's. Other sites poll a different set of analysts on different dates, so their average, low and high targets — and sometimes the Buy/Hold verdict — will differ from these.</li>
      <li><b>A consensus mixes dates.</b> The target summary averages views set weeks or months apart. The firm-by-firm table dates each one and shows what the stock has done since.</li>
      <li><b>The named list is incomplete.</b> Yahoo gives a consensus over more analysts than it names, so the table cannot be reconciled to the summary.</li>
      <li><b>EPS is not GAAP.</b> Analysts mostly forecast adjusted earnings; these are not comparable with the reported EPS elsewhere on this page.</li>
      <li><b>Growth off a loss is not given</b> — "n/m" where last year's figure was negative.</li>
      <li><b>Conflicts of interest are not in the data.</b> A covering firm may also underwrite or sell the company's shares; check the company's filings.</li>
      <li>Targets are conventionally twelve-month views. They tend to follow the share price rather than lead it.</li>
    </ul></details>
  </div>`;
}

function renderForecastsSection(f, currSym = '¥') {
  if (!f) return "";
  if (f.market === "us") return renderUsForecastsSection(f, currSym);
  const guidance = _fcGuidance(f.guidance, currSym);
  const broker = _fcBroker(f.broker, currSym, f.broker_error);
  return `
  <div class="sc-card fc-card">
    <div class="sc-card-header">
      <div class="sc-card-title">Forecasts</div>
      <span class="fund-tag">what brokers expect, and what the company has said itself</span>
    </div>
    ${_fcHero(f, currSym)}${_fcCharts(f, currSym)}
    ${guidance}${broker}
  </div>`;
}

function renderCashflowMarginsSection(cm, currSym = '$') {
  if (!cm || !cm.margins || !cm.cashflow) return "";
  const M = cm.margins, C = cm.cashflow;

  function money(v) {
    if (v == null) return "—";
    const a = Math.abs(v), s = v < 0 ? "-" : "";
    if (a >= 1e12) return `${s}${currSym}${(a/1e12).toFixed(2)}T`;
    if (a >= 1e9)  return `${s}${currSym}${(a/1e9).toFixed(1)}B`;
    if (a >= 1e6)  return `${s}${currSym}${(a/1e6).toFixed(0)}M`;
    return `${s}${currSym}${a.toFixed(0)}`;
  }
  const dBadge = (d) => d == null ? "" :
    `<span class="cm-delta ${d >= 0 ? 'vg-pos' : 'vg-neg'}">${d > 0 ? "▲" : d < 0 ? "▼" : "▬"}${Math.abs(d)}pp</span>`;
  const pctTxt = (v) => v == null ? "—" : `${v > 0 ? "+" : ""}${v}%`;

  // ── Multi-line margin trend chart ─────────────────────────────────────────
  const MARGIN_DEFS = [
    ["Gross",     M.gross,     "#6366f1"],
    ["Operating", M.operating, "#0d6b5e"],
    ["Pre-tax",   M.pretax,    "#f59e0b"],
    ["Net",       M.net,       "#dc2626"],
    ["FCF",       M.fcf,       "#0891b2"],
  ].filter(d => d[1] && d[1].series && d[1].series.length >= 2);

  let marginChart = "";
  if (MARGIN_DEFS.length) {
    const allPts = MARGIN_DEFS.flatMap(d => d[1].series);
    const years = [...new Set(allPts.map(p => p.year))].sort();
    const vals = allPts.map(p => p.margin);
    const mn = Math.min(0, ...vals), mx = Math.max(...vals), rng = (mx - mn) || 1;
    const W = 320, H = 150, padX = 30, padTop = 12, padBot = 22;
    const x = (yr) => padX + years.indexOf(yr) * (W - 2 * padX) / Math.max(1, years.length - 1);
    const y = (v) => H - padBot - (v - mn) / rng * (H - padTop - padBot);
    const lines = MARGIN_DEFS.map(([lbl, m, col]) => {
      const pts = m.series.map(p => `${x(p.year).toFixed(1)},${y(p.margin).toFixed(1)}`).join(" ");
      const last = m.series[m.series.length - 1];
      return `<polyline points="${pts}" fill="none" stroke="${col}" stroke-width="1.8"/>
        <circle cx="${x(last.year).toFixed(1)}" cy="${y(last.margin).toFixed(1)}" r="2.6" fill="${col}"/>`;
    }).join("");
    const yearLabels = years.map((yr, i) => {
      const anchor = i === 0 ? "start" : i === years.length - 1 ? "end" : "middle";
      return `<text x="${x(yr).toFixed(1)}" y="${H - 6}" text-anchor="${anchor}" font-size="9" fill="var(--muted)">'${String(yr).slice(2)}</text>`;
    }).join("");
    const legend = MARGIN_DEFS.map(([lbl, m, col]) =>
      `<span class="cm-leg"><i style="background:${col}"></i>${lbl} ${m.latest}%</span>`).join("");
    marginChart = `
      <div class="cm-chart-wrap">
        <svg viewBox="0 0 ${W} ${H}" class="cm-chart">${lines}${yearLabels}</svg>
        <div class="cm-legend">${legend}</div>
      </div>`;
  }

  // ── Margin tiles (Pre-tax highlighted per request) ────────────────────────
  const marginTiles = [
    ["Gross Margin",     M.gross,     false],
    ["Operating Margin", M.operating, false],
    ["Pre-tax Margin",   M.pretax,    true],
    ["Net Margin",       M.net,       false],
    ["FCF Margin",       M.fcf,       false],
  ].filter(t => t[1] && t[1].latest != null).map(([l, m, hi]) => `
    <div class="vg-tile ${hi ? 'cm-tile-hi' : ''}">
      <span class="vg-tile-lbl">${l}</span>
      <span class="vg-tile-val">${m.latest}% ${dBadge(m.delta_yoy)}</span>
      ${m.delta_period != null ? `<span class="cm-sub">${m.delta_period >= 0 ? "+" : ""}${m.delta_period}pp vs ${m.series[0].year}</span>` : ""}
    </div>`).join("");

  // ── Cash-flow grouped bar chart (OCF vs FCF) ──────────────────────────────
  let cfChart = "";
  const ocf = C.ocf_series || [], fcf = C.fcf_series || [];
  if (ocf.length >= 2 || fcf.length >= 2) {
    const years = [...new Set([...ocf, ...fcf].map(p => p.year))].sort();
    const allV = [...ocf, ...fcf].map(p => p.val);
    const mn = Math.min(0, ...allV), mx = Math.max(0, ...allV), rng = (mx - mn) || 1;
    const W = 320, H = 150, padX = 8, padTop = 16, padBot = 20;
    const bandW = (W - 2 * padX) / years.length;
    const zeroY = H - padBot - (0 - mn) / rng * (H - padTop - padBot);
    const y = (v) => H - padBot - (v - mn) / rng * (H - padTop - padBot);
    const ocfMap = Object.fromEntries(ocf.map(p => [p.year, p.val]));
    const fcfMap = Object.fromEntries(fcf.map(p => [p.year, p.val]));
    const bars = years.map((yr, i) => {
      const cx = padX + i * bandW + bandW / 2;
      const bw = Math.min(16, bandW / 2.6);
      let out = "";
      if (ocfMap[yr] != null) { const by = y(ocfMap[yr]); out += `<rect x="${(cx-bw-1).toFixed(1)}" y="${Math.min(by,zeroY).toFixed(1)}" width="${bw}" height="${Math.abs(by-zeroY).toFixed(1)}" rx="1.5" fill="#0d6b5e"/>`; }
      if (fcfMap[yr] != null) { const by = y(fcfMap[yr]); out += `<rect x="${(cx+1).toFixed(1)}" y="${Math.min(by,zeroY).toFixed(1)}" width="${bw}" height="${Math.abs(by-zeroY).toFixed(1)}" rx="1.5" fill="#5ec4b0" opacity="0.85"/>`; }
      out += `<text x="${cx.toFixed(1)}" y="${H-6}" text-anchor="middle" font-size="9" fill="var(--muted)">'${String(yr).slice(2)}</text>`;
      return out;
    }).join("");
    cfChart = `
      <div class="cm-chart-wrap">
        <svg viewBox="0 0 ${W} ${H}" class="cm-chart">
          <line x1="${padX}" y1="${zeroY.toFixed(1)}" x2="${W-padX}" y2="${zeroY.toFixed(1)}" stroke="var(--panel-2)" stroke-width="1"/>
          ${bars}
        </svg>
        <div class="cm-legend">
          <span class="cm-leg"><i style="background:#0d6b5e"></i>Operating CF</span>
          <span class="cm-leg"><i style="background:#5ec4b0"></i>Free CF</span>
        </div>
      </div>`;
  }

  const cfTiles = [
    ["Operating CF", money(C.ocf_latest), ""],
    ["Free CF",      money(C.fcf_latest), ""],
    ["Capex",        money(C.capex_latest), ""],
    ["OCF Growth",   pctTxt(C.ocf_cagr), C.ocf_cagr == null ? "" : C.ocf_cagr >= 0 ? "vg-pos" : "vg-neg"],
    ["FCF Growth",   pctTxt(C.fcf_cagr), C.fcf_cagr == null ? "" : C.fcf_cagr >= 0 ? "vg-pos" : "vg-neg"],
  ].map(([l, v, c]) => `
    <div class="vg-tile"><span class="vg-tile-lbl">${l}</span><span class="vg-tile-val ${c}">${v}</span></div>`).join("");

  return `
  <div class="sc-card vg-card cm-card">
    <div class="sc-card-header"><div class="sc-card-title">Cash Flow &amp; Margins</div></div>
    <div class="vg-grid">
      <div class="vg-block">
        <div class="vg-block-title">Profit Margins &amp; Trend</div>
        <div class="vg-tiles">${marginTiles}</div>
        ${marginChart}
      </div>
      <div class="vg-block">
        <div class="vg-block-title">Cash Flow &amp; Growth</div>
        <div class="vg-tiles">${cfTiles}</div>
        ${cfChart}
      </div>
    </div>
    <div class="vg-note">Margins are annual (revenue-based); the multi-year lines show whether profitability is <strong>expanding or compressing</strong>. Growth rates are CAGRs across the reported window. FCF = operating cash flow − capex.</div>
  </div>`;
}

// ── Fundamental Scorecard (F-Score, Altman Z, Beneish M, Graham, Magic) ────

function renderFundamentalScoresSection(fs, currSym = '$') {
  if (!fs) return "";
  const P = fs.piotroski, A = fs.altman, B = fs.beneish, G = fs.graham, V = fs.value;

  const info = (title, body) => `
    <details class="fund-info"><summary>ⓘ What is this?</summary><div class="fund-info-body">${body}</div></details>`;

  // ── Piotroski F-Score ─────────────────────────────────────────────────────
  let fCard = "";
  if (P) {
    const cls = P.verdict === "strong" ? "vg-good" : P.verdict === "moderate" ? "vg-okay" : "vg-bad";
    const comps = P.components.map(c =>
      `<div class="fund-comp ${c.unscoreable ? 'fund-na' : c.pass ? 'fund-pass' : 'fund-fail'}">
         <span class="fund-comp-ico">${c.unscoreable ? "?" : c.pass ? "✓" : "✗"}</span>
         <span class="fund-comp-name">${escapeHtml(c.name)}</span>
         <span class="fund-comp-detail">${c.unscoreable ? "Missing data — not scoreable, counted as not-passed below" : escapeHtml(c.detail)}</span>
       </div>`).join("");
    const naNote = P.unscoreable > 0
      ? `<div class="fund-metric-line fund-na-note">⚠ ${P.unscoreable} of 9 checks couldn't be evaluated (missing data) and count as not-passed in the score above — see which ones below.</div>`
      : "";
    fCard = `
      <div class="fund-metric">
        <div class="fund-metric-head">
          <span class="fund-metric-name">Piotroski F-Score</span>
          <span class="fund-score ${cls}">${P.score}<span class="fund-score-max">/9</span></span>
        </div>
        <div class="fund-metric-line">A 9-point checklist of financial strength — <strong>${P.verdict}</strong>. Higher is healthier; ≥8 is excellent, ≤3 is a red flag.</div>
        ${naNote}
        <details class="fund-info"><summary>See the 9 checks &amp; how it works</summary>
          <div class="fund-info-body">
            <p>Devised by accounting professor Joseph Piotroski (2000). Each of 9 yes/no tests scores 1 point across three areas — <em>profitability, leverage/liquidity, and operating efficiency</em>. It's most powerful for separating genuinely improving companies from value traps.</p>
            <div class="fund-comps">${comps}</div>
          </div>
        </details>
      </div>`;
  }

  // ── Altman Z-Score ────────────────────────────────────────────────────────
  let zCard = "";
  if (A) {
    if (A.applicable === false) {
      zCard = `<div class="fund-metric">
        <div class="fund-metric-head"><span class="fund-metric-name">Altman Z-Score</span><span class="fund-score vg-unk">n/a</span></div>
        <div class="fund-metric-line">${escapeHtml(A.note || "Not applicable.")}</div></div>`;
    } else if (A.z != null) {
      const zc = A.zone === "safe" ? "vg-good" : A.zone === "grey" ? "vg-okay" : "vg-bad";
      const zlabel = A.zone === "safe" ? "Safe zone" : A.zone === "grey" ? "Grey zone" : "Distress zone";
      zCard = `
        <div class="fund-metric">
          <div class="fund-metric-head"><span class="fund-metric-name">Altman Z-Score</span><span class="fund-score ${zc}">${A.z} <span class="fund-zone">${zlabel}</span></span></div>
          <div class="fund-metric-line">Bankruptcy-risk gauge. <strong>&gt; 2.99 = safe</strong>, 1.81–2.99 = grey, <strong>&lt; 1.81 = distress</strong> (elevated failure risk within ~2 years).</div>
          <details class="fund-info"><summary>How it works</summary><div class="fund-info-body">
            <p>Edward Altman (1968) combined 5 balance-sheet/earnings ratios into one solvency score:</p>
            <p class="fund-formula">Z = 1.2·(WC/Assets) + 1.4·(Retained&nbsp;Earnings/Assets) + 3.3·(EBIT/Assets) + 0.6·(Mkt&nbsp;Equity/Liabilities) + 1.0·(Sales/Assets)</p>
            <p>A low Z is the classic "cheap for a reason" warning — it flags balance-sheet fragility a low P/E won't show. (Calibrated for non-financial firms.)</p>
          </div></details>
        </div>`;
    }
  }

  // ── Beneish M-Score ───────────────────────────────────────────────────────
  let mCard = "";
  if (B && B.available) {
    const mc = B.flag ? "vg-bad" : "vg-good";
    mCard = `
      <div class="fund-metric">
        <div class="fund-metric-head"><span class="fund-metric-name">Beneish M-Score</span><span class="fund-score ${mc}">${B.m} <span class="fund-zone">${B.flag ? "⚠ flag" : "clean"}</span></span></div>
        <div class="fund-metric-line">Earnings-manipulation detector. <strong>Above −1.78 flags</strong> a higher likelihood the books are being managed; below −1.78 looks clean.</div>
        <details class="fund-info"><summary>How it works</summary><div class="fund-info-body">
          <p>Messod Beneish (1999) built an 8-variable model from things manipulators tend to distort — ballooning receivables vs. sales, deteriorating margins, rising accruals, aggressive capitalization. It famously flagged Enron before its collapse.</p>
          <p>Treat a flag as "look closer," not a verdict — capital-intensive or fast-restructuring firms can trip it innocently.</p>
        </div></details>
      </div>`;
  }

  // ── Graham Number & Margin of Safety ──────────────────────────────────────
  let gCard = "";
  if (G && G.number != null) {
    const gc = G.verdict === "undervalued" ? "vg-good" : G.verdict === "fair" ? "vg-okay" : "vg-bad";
    const mos = G.margin_of_safety_pct;
    const mosTxt = mos == null ? "—" : `${mos > 0 ? "+" : ""}${mos}%`;
    gCard = `
      <div class="fund-metric">
        <div class="fund-metric-head"><span class="fund-metric-name">Graham Number</span><span class="fund-score ${gc}">${currSym}${G.number}</span></div>
        <div class="fund-metric-line">Graham's conservative "max fair price." Price ${currSym}${G.price} is <strong>${mosTxt}</strong> vs. it (positive = margin of safety).</div>
        <details class="fund-info"><summary>How it works</summary><div class="fund-info-body">
          <p>Benjamin Graham's rule-of-thumb ceiling for a defensive investor:</p>
          <p class="fund-formula">Graham Number = √(22.5 × EPS × Book&nbsp;Value&nbsp;per&nbsp;Share)</p>
          <p>The 22.5 = a P/E of 15 × a P/B of 1.5, Graham's twin caps. It's <em>deliberately strict</em> — most quality growth companies trade far above it, so read a negative margin as "not Graham-cheap," not "bad company." Best used for value/asset plays.</p>
          <p class="fund-sub">EPS ${currSym}${G.eps} · Book value/share ${currSym}${G.bvps}</p>
        </div></details>
      </div>`;
  } else if (G && G.note) {
    gCard = `<div class="fund-metric"><div class="fund-metric-head"><span class="fund-metric-name">Graham Number</span><span class="fund-score vg-unk">n/a</span></div><div class="fund-metric-line">${escapeHtml(G.note)}</div></div>`;
  }

  // ── Value / Magic Formula tiles ───────────────────────────────────────────
  let vCard = "";
  if (V) {
    const tiles = [
      ["Earnings Yield", V.earnings_yield != null ? `${V.earnings_yield}%` : "—", "EBIT ÷ Enterprise Value — the pre-tax yield you'd earn owning the whole business. Higher = cheaper. Half of Greenblatt's Magic Formula."],
      ["Return on Capital", V.roc != null ? `${V.roc}%` : "—", "EBIT ÷ (working capital + fixed assets) — how efficiently the core business turns capital into profit. Higher = better quality. The other half of the Magic Formula."],
      ["FCF Yield", V.fcf_yield != null ? `${V.fcf_yield}%` : "—", "Free cash flow ÷ market cap — the real cash return relative to price. > ~5% is attractive."],
      ["EV / EBIT", V.ev_ebit != null ? `${V.ev_ebit}×` : "—", "Enterprise value ÷ operating profit — a cleaner cheapness gauge than P/E (accounts for debt & cash). Lower = cheaper."],
    ];
    vCard = `
      <div class="fund-value">
        <div class="fund-value-grid">
          ${tiles.map(([l, v, tip]) => `<div class="fund-vtile" title="${escapeHtml(tip)}"><span class="fund-vtile-lbl">${l}</span><span class="fund-vtile-val">${v}</span></div>`).join("")}
        </div>
        <details class="fund-info"><summary>Magic Formula &amp; these value metrics explained</summary><div class="fund-info-body">
          <p>Joel Greenblatt's <strong>Magic Formula</strong> ranks companies on two things together: <strong>Earnings Yield</strong> (buy it cheap) and <strong>Return on Capital</strong> (buy a good business). Stocks strong on <em>both</em> historically beat the market. <strong>FCF Yield</strong> and <strong>EV/EBIT</strong> are complementary cheapness gauges that, unlike P/E, account for debt and real cash generation.</p>
        </div></details>
      </div>`;
  }

  const healthCards = [fCard, zCard, mCard].filter(Boolean).join("");
  const valueCards = [gCard].filter(Boolean).join("");
  if (!healthCards && !valueCards && !vCard) return "";

  return `
  <div class="sc-card vg-card fund-card">
    <div class="sc-card-header">
      <div class="sc-card-title">Fundamental Scorecard</div>
      <span class="fund-tag">Composite quality, solvency &amp; value scores</span>
    </div>
    ${healthCards ? `<div class="fund-group-title">Financial Health &amp; Red Flags</div><div class="fund-grid">${healthCards}</div>` : ""}
    ${(valueCards || vCard) ? `<div class="fund-group-title">Intrinsic Value &amp; Earnings Power</div>${valueCards ? `<div class="fund-grid">${valueCards}</div>` : ""}${vCard}` : ""}
  </div>`;
}

// ── Intrinsic Value (DCF + reverse-DCF) ────────────────────────────────────

let _dcfData = null;

// Two-stage FCFE: growth fades linearly g1→gTerm over `fade` years, discounted at r.
function _dcfEquityValue(baseFcf, g1, fade, gTerm, r) {
  let fcf = baseFcf, pv = 0;
  for (let t = 1; t <= fade; t++) {
    const g = fade > 1 ? g1 + (gTerm - g1) * (t - 1) / (fade - 1) : g1;
    fcf = fcf * (1 + g / 100);
    pv += fcf / Math.pow(1 + r / 100, t);
  }
  if (r > gTerm) {   // Gordon terminal value at end of horizon
    const tv = fcf * (1 + gTerm / 100) / ((r - gTerm) / 100);
    pv += tv / Math.pow(1 + r / 100, fade);
  }
  return pv;
}

function _reverseDcfGrowth(baseFcf, shares, price, fade, gTerm, r) {
  const target = price * shares;              // equity value the market is paying
  let lo = -30, hi = 80;
  for (let i = 0; i < 70; i++) {
    const mid = (lo + hi) / 2;
    if (_dcfEquityValue(baseFcf, mid, fade, gTerm, r) < target) lo = mid; else hi = mid;
  }
  return (lo + hi) / 2;
}

function renderDcfSection(dcf, currSym = "$") {
  if (!dcf) return "";
  if (dcf.applicable === false) {
    return `<div class="sc-card vg-card">
      <div class="sc-card-header"><div class="sc-card-title">Intrinsic Value (DCF)</div></div>
      <div class="vg-note">${escapeHtml(dcf.note || "DCF not applicable.")}</div></div>`;
  }
  _dcfData = dcf;
  _dcfCurSym = currSym;
  const money = (v) => v == null ? "—" : v >= 1e12 ? `${currSym}${(v/1e12).toFixed(2)}T` : v >= 1e9 ? `${currSym}${(v/1e9).toFixed(1)}B` : `${currSym}${(v/1e6).toFixed(0)}M`;
  const spread = dcf.econ_profit_spread;
  const spreadCls = spread == null ? "" : spread > 0 ? "vg-pos" : "vg-neg";

  const tiles = [
    ["Base FCF (norm.)", money(dcf.base_fcf), ""],
    ["Owner earnings", money(dcf.owner_earnings), ""],
    ["Cost of equity (CAPM)", `${dcf.cost_of_equity}%`, ""],
    ["WACC", `${dcf.wacc}%`, ""],
    ["ROIC", dcf.roic != null ? `${dcf.roic}%` : "—", ""],
    ["ROIC − WACC", spread != null ? `${spread > 0 ? "+" : ""}${spread}pp` : "—", spreadCls],
  ].map(([l, v, c]) => `<div class="vg-tile"><span class="vg-tile-lbl">${l}</span><span class="vg-tile-val ${c}">${v}</span></div>`).join("");

  return `
  <div class="sc-card vg-card dcf-card">
    <div class="sc-card-header">
      <div class="sc-card-title">Intrinsic Value (DCF)</div>
      <span class="fund-tag">two-stage FCFE + reverse-DCF</span>
    </div>
    <div class="vg-tiles dcf-byproducts">${tiles}</div>

    <div class="vg-grid dcf-grid">
      <div class="vg-block">
        <div class="vg-block-title">Assumptions <span class="dcf-reset" id="dcfReset">↺ reset</span></div>
        <div class="dcf-inputs">
          <label>Discount rate (%)<input type="number" id="dcf_rate" step="0.1" value="${dcf.cost_of_equity}"></label>
          <label>Stage-1 growth (%/yr)<input type="number" id="dcf_g1" step="0.5" value="${dcf.default_stage1_growth}"></label>
          <label>Fade to terminal (yrs)<input type="number" id="dcf_fade" min="2" max="20" step="1" value="${dcf.default_fade_years}"></label>
          <label>Terminal growth (%)<input type="number" id="dcf_term" step="0.1" value="${dcf.default_terminal}"></label>
        </div>
        <div class="vg-note">Growth fades linearly from stage-1 to terminal over the horizon. Discount rate defaults to CAPM cost of equity (β ${dcf.beta}, Rf ${dcf.rf}%, ERP ${dcf.erp}%).</div>
      </div>

      <div class="vg-block dcf-output-block">
        <div class="vg-block-title">Valuation</div>
        <div class="dcf-outputs">
          <div class="dcf-big"><span class="dcf-big-lbl">Intrinsic value / share</span><span class="dcf-big-val" id="dcf_intrinsic">—</span></div>
          <div class="dcf-big"><span class="dcf-big-lbl">Margin of safety</span><span class="dcf-big-val" id="dcf_mos">—</span></div>
        </div>
        <div class="dcf-price-line">Current price <strong>${currSym}${dcf.price}</strong></div>
        <div class="dcf-reverse" id="dcf_reverse">—</div>
      </div>
    </div>
    <div class="vg-note ha-caveat">A DCF is only as good as its inputs — adjust the assumptions and watch the margin of safety move. The <strong>reverse-DCF</strong> flips it around: it shows the growth the current price already assumes, so you can judge whether the market's expectation is plausible against the company's real track record.</div>
  </div>`;
}

function _recomputeDcf() {
  if (!_dcfData) return;
  const r = parseFloat(document.getElementById("dcf_rate").value);
  const g1 = parseFloat(document.getElementById("dcf_g1").value);
  const fade = Math.max(2, Math.min(20, parseInt(document.getElementById("dcf_fade").value) || 10));
  const term = parseFloat(document.getElementById("dcf_term").value);
  const curSym = _dcfCurSym || "$";
  const intrinsicEl = document.getElementById("dcf_intrinsic");
  const mosEl = document.getElementById("dcf_mos");
  const revEl = document.getElementById("dcf_reverse");
  if ([r, g1, term].some(v => Number.isNaN(v)) || !(r > term)) {
    intrinsicEl.textContent = "—"; mosEl.textContent = "—";
    revEl.textContent = r <= term ? "⚠ discount rate must exceed terminal growth." : "";
    return;
  }
  const eqVal = _dcfEquityValue(_dcfData.base_fcf, g1, fade, term, r);
  const perShare = eqVal / _dcfData.shares;
  const price = _dcfData.price;
  const mos = (perShare - price) / perShare * 100;
  intrinsicEl.textContent = `${curSym}${perShare.toFixed(2)}`;
  mosEl.textContent = `${mos >= 0 ? "+" : ""}${mos.toFixed(0)}%`;
  mosEl.className = "dcf-big-val " + (mos >= 25 ? "vg-good" : mos >= 0 ? "vg-okay" : "vg-bad");
  const implied = _reverseDcfGrowth(_dcfData.base_fcf, _dcfData.shares, price, fade, term, r);
  const real = _dcfData.default_stage1_growth;
  const verdict = implied > 25 ? "aggressive — the price bakes in very high growth" :
                  implied < 3 ? "modest — the price assumes little growth (cushion)" :
                  "moderate";
  revEl.innerHTML = `<strong>Reverse-DCF:</strong> at these settings the current price implies ~<strong>${implied.toFixed(1)}%/yr</strong> FCF growth for ${fade} years (${verdict}).`;
}

let _dcfCurSym = "$";

// ── Competitive Position (moat vs. listed peers) ───────────────────────────

function renderCompetitiveSection(d) {
  const ticker = d.ticker;
  const market = d.currency === "¥" ? "cn" : "us";
  return `
  <div class="sc-card vg-card comp-card">
    <div class="sc-card-header">
      <div class="sc-card-title">Competitive Position</div>
      <span class="fund-tag">vs. listed industry peers</span>
    </div>
    <div id="compBody">
      <p class="vg-note">Where this company stands among <strong>listed peers in the same industry</strong> — market-cap &amp; revenue rank/share, the multi-year <strong>share trend</strong> (gaining or losing ground), and gross margin vs. peers. <em>Listed peers only — excludes private &amp; foreign competitors, so it's a competitive-position proxy, not true market share.</em></p>
      <button id="compAnalyzeBtn" class="secondary-btn" data-market="${market}" data-ticker="${escapeHtml(ticker)}">Analyze competitive position</button>
      <span id="compStatus" class="filter-confirm-status"></span>
    </div>
  </div>`;
}

async function loadCompetitive(market, ticker) {
  const body = document.getElementById("compBody");
  const status = document.getElementById("compStatus");
  const btn = document.getElementById("compAnalyzeBtn");
  btn.disabled = true; btn.textContent = "Analyzing…";
  status.textContent = "Fetching peer financials… (~20s)";
  try {
    const d = await fetchJson(`/api/competitive?market=${market}&ticker=${encodeURIComponent(ticker)}`);
    if (d.status === "building") {
      status.textContent = "Building the peer universe snapshot (one-time, ~1 min) — click again shortly.";
      btn.disabled = false; btn.textContent = "Analyze competitive position";
      return;
    }
    if (d.status !== "ready") {
      status.textContent = `⚠ ${d.error || "Failed"}`;
      btn.disabled = false; btn.textContent = "Analyze competitive position";
      return;
    }
    body.innerHTML = renderCompetitiveResult(d);
  } catch (err) {
    status.textContent = `⚠ ${err.message}`;
    btn.disabled = false; btn.textContent = "Analyze competitive position";
  }
}

function renderCompetitiveResult(d) {
  const cur = d.currency || "$";
  const money = (v) => v == null ? "—" : v >= 1e12 ? `${cur}${(v/1e12).toFixed(2)}T` : v >= 1e9 ? `${cur}${(v/1e9).toFixed(1)}B` : `${cur}${(v/1e6).toFixed(0)}M`;
  const pct = (v) => v == null ? "—" : `${v}%`;

  // share-trend sparkline
  let trendBlock = "";
  const ss = d.share_series || [];
  if (ss.length >= 2) {
    const W = 240, H = 66, padX = 24, padY = 12;
    const vals = ss.map(p => p.share);
    const mn = Math.min(...vals), mx = Math.max(...vals), rng = (mx - mn) || 1;
    const x = (i) => padX + i * (W - 2*padX) / (ss.length - 1);
    const y = (v) => H - padY - (v - mn) / rng * (H - 2*padY);
    const poly = ss.map((p, i) => `${x(i).toFixed(1)},${y(p.share).toFixed(1)}`).join(" ");
    const dots = ss.map((p, i) => `<circle cx="${x(i).toFixed(1)}" cy="${y(p.share).toFixed(1)}" r="2.4" fill="var(--accent)"/><text x="${x(i).toFixed(1)}" y="${(y(p.share)-6).toFixed(1)}" text-anchor="middle" font-size="9" fill="var(--muted)">${p.share}%</text><text x="${x(i).toFixed(1)}" y="${H-2}" text-anchor="middle" font-size="8.5" fill="var(--muted)">'${String(p.year).slice(2)}</text>`).join("");
    const tr = d.share_trend;
    const trBadge = tr == null ? "" : `<span class="vg-badge ${tr >= 0 ? "vg-good" : "vg-bad"}">${tr >= 0 ? "▲ gaining" : "▼ losing"} ${tr > 0 ? "+" : ""}${tr}pp over ${ss.length} yrs</span>`;
    trendBlock = `
      <div class="vg-block">
        <div class="vg-block-title">Revenue-share trend ${trBadge}</div>
        <svg viewBox="0 0 ${W} ${H}" class="vg-spark" style="overflow:visible"><polyline points="${poly}" fill="none" stroke="var(--accent)" stroke-width="1.8"/>${dots}</svg>
        <div class="vg-note">Share of revenue across ${d.panel_n || "the"} peers reporting in <strong>both</strong> the first &amp; last year (a fixed panel, so new listings don't distort the trend). Rising = winning share (widening moat); falling = losing ground — the most reliable signal here.</div>
      </div>`;
  }

  const gmDelta = (d.gross_margin != null && d.peer_gm_median != null) ? +(d.gross_margin - d.peer_gm_median).toFixed(1) : null;
  const gmCls = gmDelta == null ? "" : gmDelta >= 0 ? "vg-pos" : "vg-neg";

  const tiles = [
    ["Market-cap rank", d.mcap_rank ? `#${d.mcap_rank} / ${d.n_peers}` : "—"],
    ["Mkt-cap share", pct(d.mcap_share)],
    ["Revenue rank", d.rev_rank ? `#${d.rev_rank} / ${d.rev_peers}` : "—"],
    ["Revenue share", pct(d.rev_share)],
  ].map(([l, v]) => `<div class="vg-tile"><span class="vg-tile-lbl">${l}</span><span class="vg-tile-val">${v}</span></div>`).join("");

  const scale = Math.max(...(d.top_peers || []).map(p => p.mcap_share || 0), 1);
  const peerRows = (d.top_peers || []).map(p => `
    <div class="comp-peer-row ${p.is_target ? "comp-peer-target" : ""}">
      <span class="comp-peer-sym">${escapeHtml(p.symbol.split(".")[0])}${p.is_target ? " ◄" : ""}</span>
      <span class="comp-peer-name">${escapeHtml((p.name || "").slice(0, 22))}</span>
      <div class="comp-peer-track"><div class="comp-peer-fill ${p.is_target ? "comp-fill-target" : ""}" style="width:${Math.round((p.mcap_share||0)/scale*100)}%"></div></div>
      <span class="comp-peer-share">${p.mcap_share != null ? p.mcap_share + "%" : "—"}</span>
    </div>`).join("");

  return `
    <div class="comp-head">
      <span class="comp-industry">${escapeHtml(d.industry)}</span>
      <span class="comp-npeers">${d.n_peers} listed peers</span>
    </div>
    <div class="vg-tiles">${tiles}</div>
    <div class="vg-grid" style="margin-top:14px">
      ${trendBlock}
      <div class="vg-block">
        <div class="vg-block-title">Gross margin vs. peers</div>
        <div class="vg-tiles">
          <div class="vg-tile"><span class="vg-tile-lbl">This company</span><span class="vg-tile-val">${pct(d.gross_margin)}</span></div>
          <div class="vg-tile"><span class="vg-tile-lbl">Peer median</span><span class="vg-tile-val">${pct(d.peer_gm_median)}</span></div>
          <div class="vg-tile"><span class="vg-tile-lbl">vs. peers</span><span class="vg-tile-val ${gmCls}">${gmDelta == null ? "—" : (gmDelta >= 0 ? "+" : "") + gmDelta + "pp"}</span></div>
        </div>
        <div class="vg-note">Higher-than-peer gross margin signals pricing power — a hallmark of a durable moat (Buffett's test).</div>
      </div>
    </div>
    <div class="vg-block-title" style="margin-top:14px">Largest listed peers by market cap</div>
    <div class="comp-peers">${peerRows}</div>
    <div class="vg-note ha-caveat">This measures position among <strong>listed peers in our universe only</strong> — it excludes private firms, foreign-listed rivals, and adjacent substitutes, so the absolute share overstates true dominance. Read the <strong>trend</strong> and <strong>margin-vs-peers</strong> as the trustworthy moat signals.</div>`;
}

// ── Industry Cycle Section ─────────────────────────────────────────────────

function renderCycleSection(cycle) {
  if (!cycle) return "";

  const { lifecycle, business_cycle: biz, sector_indicators: inds, sector, is_etf } = cycle;

  // ── Lifecycle Phase box (stocks only) ──────────────────────────────────────
  let lifecycleHtml = "";
  if (lifecycle && !is_etf) {
    const phaseColorCls = {
      positive: "cycle-positive",
      caution:  "cycle-caution",
      warning:  "cycle-warning",
      neutral:  "cycle-neutral",
    }[lifecycle.phase_color] || "cycle-neutral";

    const signalRows = (lifecycle.signals || []).map(s => {
      const trendIcon = s.status === "positive" ? "↑" : s.status === "warning" ? "↓" : "→";
      return `
      <div class="sc-cycle-signal status-${escapeHtml(s.status)}">
        <span class="sc-cycle-sig-name">${escapeHtml(s.name)}</span>
        <span class="sc-cycle-sig-val">${escapeHtml(s.value)}</span>
        <span class="sc-cycle-sig-trend">${escapeHtml(s.trend)}</span>
        <span class="sc-cycle-sig-note">${escapeHtml(s.trend_note)}</span>
      </div>`;
    }).join("");

    lifecycleHtml = `
    <div class="sc-cycle-phase-box ${phaseColorCls}">
      <div class="sc-cycle-phase-label">Lifecycle Phase</div>
      <div class="sc-cycle-phase-name">${escapeHtml(lifecycle.phase)}</div>
      <p class="sc-cycle-phase-note">${escapeHtml(lifecycle.phase_note)}</p>
      <div class="sc-cycle-phase-sizing">
        <div class="sc-cycle-sizing-item">
          <span class="sc-cycle-sizing-label">Max Position Size</span>
          <span class="sc-cycle-sizing-val">${escapeHtml(lifecycle.max_position)}</span>
        </div>
        <div class="sc-cycle-sizing-item">
          <span class="sc-cycle-sizing-label">Entry Approach</span>
          <span class="sc-cycle-sizing-val">${escapeHtml(lifecycle.entry_note)}</span>
        </div>
      </div>
    </div>
    <div class="sc-cycle-subheading">Lifecycle Signals</div>
    <div class="sc-cycle-signals">${signalRows}</div>`;
  }

  // ── Business Cycle Context ─────────────────────────────────────────────────
  let bizHtml = "";
  if (biz && biz.phase_rank && Object.keys(biz.phase_rank).length > 0) {
    const phaseLabels = {
      early:     "Early Cycle",
      mid:       "Mid Cycle",
      late:      "Late Cycle",
      recession: "Recession",
    };
    const rankLabel = { strong: "★ Strong", above: "↑ Above avg", neutral: "= Neutral", below: "↓ Below avg", avoid: "✗ Avoid" };
    const cells = Object.entries(biz.phase_rank).map(([phase, rank]) => `
      <div class="sc-biz-cell rank-${escapeHtml(rank)}">
        <span class="sc-biz-cell-phase">${phaseLabels[phase] || escapeHtml(phase)}</span>
        <span class="sc-biz-cell-rank">${rankLabel[rank] || escapeHtml(rank)}</span>
      </div>`).join("");

    bizHtml = `
    <div class="sc-cycle-subheading">Business Cycle Context — ${escapeHtml(sector || "—")}</div>
    <div class="sc-biz-cycle-grid">${cells}</div>
    <p class="sc-biz-cycle-note">${escapeHtml(biz.cycle_note)}</p>
    <p class="sc-biz-cycle-caveat">${escapeHtml(biz.caveat || "")}</p>`;
  }

  // ── Sector Leading Indicators ──────────────────────────────────────────────
  let indHtml = "";
  if (inds && inds.length > 0) {
    const indItems = inds.map(ind => `
    <div class="sc-cycle-ind">
      <div class="sc-cycle-ind-name">📡 ${escapeHtml(ind.name)}</div>
      <div class="sc-cycle-ind-desc">${escapeHtml(ind.description)}</div>
      <div class="sc-cycle-ind-thr">${escapeHtml(ind.thresholds)}</div>
      <div class="sc-cycle-ind-source">Source: ${escapeHtml(ind.source)}</div>
    </div>`).join("");
    indHtml = `
    <div class="sc-cycle-subheading">Sector Leading Indicators</div>
    <div class="sc-cycle-indicators">${indItems}</div>`;
  }

  if (!lifecycleHtml && !bizHtml && !indHtml) return "";

  const etfNote = is_etf
    ? `<p class="sc-card-sub">Sector mapped to <strong>${escapeHtml(sector || "Diversified")}</strong> from ETF category. Lifecycle phase assessment requires individual company analysis — business cycle context and sector indicators apply to the fund's primary exposure.</p>`
    : "";

  return `
  <div class="sc-card">
    <div class="sc-card-header">
      <div class="sc-card-title">Industry Cycle</div>
      <span class="sc-verdict-badge sc-v-na">${escapeHtml(sector || "Diversified")}</span>
    </div>
    ${etfNote}
    ${lifecycleHtml}
    ${bizHtml}
    ${indHtml}
  </div>`;
}

// ── Lynch Classification Section ──────────────────────────────────────────

function renderLynchSection(lx) {
  if (!lx) return '';

  const colorMap = {
    fg: { border: '#16a34a', badge: '#16a34a', badgeBg: '#dcfce7', stripe: '#16a34a' },
    sw: { border: '#2563eb', badge: '#2563eb', badgeBg: '#dbeafe', stripe: '#2563eb' },
    sg: { border: '#b45309', badge: '#b45309', badgeBg: '#fef3c7', stripe: '#b45309' },
    cy: { border: '#ea580c', badge: '#ea580c', badgeBg: '#ffedd5', stripe: '#ea580c' },
    ap: { border: '#7c3aed', badge: '#7c3aed', badgeBg: '#ede9fe', stripe: '#7c3aed' },
    tu: { border: '#dc2626', badge: '#dc2626', badgeBg: '#fee2e2', stripe: '#dc2626' },
  };
  const c = colorMap[lx.color] || colorMap.sw;

  const sig = lx.signals || {};
  const signalPills = [
    sig.rev_cagr_3y  != null ? `Rev CAGR 3Y: <b>${sig.rev_cagr_3y > 0 ? '+' : ''}${sig.rev_cagr_3y}%</b>` : null,
    sig.div_yield > 0 && sig.div_yield < 25 ? `Div yield: <b>${sig.div_yield}%</b>` : null,
    sig.market_cap_b != null ? `Mkt cap: <b>$${sig.market_cap_b}B</b>` : null,
    sig.beta         != null ? `Beta: <b>${sig.beta}</b>` : null,
    sig.has_losses               ? `<span style="color:#f87171">Losing money</span>` : null,
  ].filter(Boolean).join(' · ');

  const ratHtml = (lx.rationale || []).map(r =>
    `<li>${r}</li>`
  ).join('');

  const watchHtml = (lx.watch || []).map(w =>
    `<li>${w}</li>`
  ).join('');

  const ic = lx.industry_cycle || {};
  const cc = lx.corp_cycle || {};

  const stageColor = s => {
    if (s === 'Emerging')          return '#a855f7';
    if (s === 'Growth')            return '#22c55e';
    if (s === 'Mature')            return '#3b82f6';
    if (s === 'Declining')         return '#ef4444';
    if (s === 'Early Stage')       return '#f97316';
    if (s === 'Rapid Growth')      return '#22c55e';
    if (s === 'Maturity')          return '#3b82f6';
    if (s === 'Cash Cow')          return '#ca8a04';
    if (s === 'Decline / Turnaround') return '#ef4444';
    return '#6b7280';
  };

  return `
  <div class="sc-card lynch-card" style="border-color:${c.border}30;border-left:4px solid ${c.stripe}">
    <div class="lynch-header">
      <div class="lynch-title-row">
        <span class="lynch-emoji">${lx.emoji}</span>
        <div>
          <div class="lynch-type-label">Peter Lynch Classification</div>
          <div class="lynch-type-name" style="color:${c.badge}">${lx.type}</div>
        </div>
        <span class="lynch-badge" style="background:${c.badgeBg};color:${c.badge};border-color:${c.border}30">${lx.type.toUpperCase()}</span>
      </div>
      <div class="lynch-signals">${signalPills}</div>
      ${ratHtml ? `<ul class="lynch-rationale">${ratHtml}</ul>` : ''}
    </div>

    <div class="lynch-cycles">
      <div class="lynch-cycle-box">
        <div class="lynch-cycle-label">Industry Lifecycle</div>
        <div class="lynch-cycle-stage" style="color:${stageColor(ic.stage)}">${ic.stage || '—'}</div>
        <div class="lynch-cycle-desc">${ic.desc || ''}</div>
        <div class="lynch-cycle-impl">${ic.impl || ''}</div>
      </div>
      <div class="lynch-cycle-box">
        <div class="lynch-cycle-label">Corporate Lifecycle</div>
        <div class="lynch-cycle-stage" style="color:${stageColor(cc.stage)}">${cc.stage || '—'}</div>
        <div class="lynch-cycle-desc">${cc.desc || ''}</div>
        <div class="lynch-cycle-impl">${cc.impl || ''}</div>
      </div>
    </div>

    <div class="lynch-details">
      <div class="lynch-detail-row">
        <span class="lynch-detail-icon">⏱</span>
        <div><span class="lynch-detail-key">Holding period</span><br>${lx.holding}</div>
      </div>
      <div class="lynch-detail-row">
        <span class="lynch-detail-icon">🚨</span>
        <div><span class="lynch-detail-key">Sell when</span><br>${lx.sell_trigger}</div>
      </div>
      ${watchHtml ? `
      <div class="lynch-detail-row">
        <span class="lynch-detail-icon">👁</span>
        <div><span class="lynch-detail-key">What to watch</span><ul class="lynch-watch">${watchHtml}</ul></div>
      </div>` : ''}
      <div class="lynch-detail-row lynch-risk-row">
        <span class="lynch-detail-icon">⚠️</span>
        <div><span class="lynch-detail-key">Key risk</span><br>${lx.risk}</div>
      </div>
    </div>
  </div>`;
}

// ── Fear Score Section ────────────────────────────────────────────────────

function renderFearScoreSection(fs) {
  if (!fs) return '';

  const score = fs.score;

  // SVG semicircle gauge
  const R = 70, cx = 100, cy = 90;
  const toRad = deg => deg * Math.PI / 180;
  // arc from 180° (left) to 0° (right), needle angle: 180° - score*1.8°
  function arcPt(deg) {
    return [cx + R * Math.cos(toRad(deg)), cy - R * Math.sin(toRad(deg))];
  }
  // Gradient arc segments: 5 colour stops over 180°
  const zones = [
    { label: 'Extreme Greed', color: '#10b981', from: 180, to: 144 },
    { label: 'Greed',         color: '#84cc16', from: 144, to: 117 },
    { label: 'Neutral',       color: '#f59e0b', from: 117, to:  90 },
    { label: 'Fear',          color: '#f97316', from:  90, to:  63 },
    { label: 'Extreme Fear',  color: '#ef4444', from:  63, to:   0 },
  ];
  function arcPath(fromDeg, toDeg, r) {
    const [x1, y1] = arcPt(fromDeg);
    const [x2, y2] = arcPt(toDeg);
    const large = Math.abs(fromDeg - toDeg) > 180 ? 1 : 0;
    const sweep = fromDeg > toDeg ? 1 : 0;
    return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${r} ${r} 0 ${large} ${sweep} ${x2.toFixed(2)} ${y2.toFixed(2)}`;
  }
  const trackArcs = zones.map(z =>
    `<path d="${arcPath(z.from, z.to, R)}" fill="none" stroke="${z.color}" stroke-width="10" stroke-linecap="butt" opacity="0.25"/>`
  ).join('');
  // Active arc from 180° to needle angle (score 0→0°-end, 100→180°-end)
  const needleDeg = 180 - score * 1.8;
  const activeColor = score >= 75 ? '#ef4444' : score >= 55 ? '#f97316' : score >= 45 ? '#f59e0b' : score >= 25 ? '#84cc16' : '#10b981';
  const activeArc = `<path d="${arcPath(180, needleDeg, R)}" fill="none" stroke="${activeColor}" stroke-width="10" stroke-linecap="round"/>`;
  // Needle
  const [nx, ny] = arcPt(needleDeg);
  const needle = `<line x1="${cx}" y1="${cy}" x2="${nx.toFixed(2)}" y2="${ny.toFixed(2)}" stroke="${activeColor}" stroke-width="2.5" stroke-linecap="round"/>
  <circle cx="${cx}" cy="${cy}" r="4" fill="${activeColor}"/>`;

  const gauge = `<svg viewBox="0 0 200 100" xmlns="http://www.w3.org/2000/svg" class="fs-gauge">
    ${trackArcs}${activeArc}${needle}
    <text x="${cx}" y="${cy - 14}" text-anchor="middle" class="fs-score-num">${score}</text>
    <text x="${cx}" y="${cy - 2}" text-anchor="middle" class="fs-score-lbl">${fs.label}</text>
  </svg>`;

  // Zone tick labels
  const zoneTicks = `<div class="fs-zone-ticks">
    <span style="color:#10b981">Extreme Greed</span>
    <span style="color:#84cc16">Greed</span>
    <span style="color:#f59e0b">Neutral</span>
    <span style="color:#f97316">Fear</span>
    <span style="color:#ef4444">Extreme Fear</span>
  </div>`;

  // Component bars
  const barRows = fs.components.map(c => {
    const barColor = c.fear >= 65 ? '#ef4444' : c.fear >= 45 ? '#f59e0b' : '#10b981';
    return `<div class="fs-comp-row">
      <div class="fs-comp-meta">
        <span class="fs-comp-label">${escapeHtml(c.label)}</span>
        <span class="fs-comp-raw">${escapeHtml(c.raw_label)}</span>
      </div>
      <div class="fs-comp-bar-wrap">
        <div class="fs-comp-bar" style="width:${c.fear}%;background:${barColor}"></div>
      </div>
      <span class="fs-comp-fear" style="color:${barColor}">${c.fear}</span>
      <span class="fs-comp-weight">${c.weight}%</span>
    </div>`;
  }).join('');

  const signalCls = score >= 55 ? 'fs-signal-fear' : score >= 45 ? 'fs-signal-neutral' : 'fs-signal-greed';

  return `<div class="sc-card sc-fear">
    <div class="sc-card-header"><div class="sc-card-title">Fear Score</div></div>
    <div class="fs-body">
      <div class="fs-gauge-col">
        ${gauge}
        ${zoneTicks}
        <div class="fs-signal ${signalCls}">${escapeHtml(fs.signal)}</div>
      </div>
      <div class="fs-comp-col">
        <div class="fs-comp-heading">SIGNAL BREAKDOWN <span class="fs-comp-hint">(fear 0–100 · weight)</span></div>
        ${barRows}
      </div>
    </div>
  </div>`;
}

// ── Take-Profit Lab Section ───────────────────────────────────────────────

function renderTakeProfitSection(tp) {
  if (!tp || !Object.keys(tp).length) return '';
  const horizons = Object.keys(tp).filter(k => tp[k]);

  const clsBadge = cls => ({
    hold:     `<span class="tp-badge tp-hold">◎ Hold Forever</span>`,
    tier:     `<span class="tp-badge tp-tier">⊘ Tiered Exit Preferred</span>`,
    trailing: `<span class="tp-badge tp-trailing">⊘ Trailing Stop Preferred</span>`,
  }[cls] ?? '');

  const statusDot = s => ({
    holding: `<span class="tp-dot tp-dot-hold" title="Still holding">●</span>`,
    exited:  `<span class="tp-dot tp-dot-exit" title="Would have exited">◉</span>`,
    partial: `<span class="tp-dot tp-dot-partial" title="Partial exits taken">◑</span>`,
  }[s] ?? '');

  function renderHorizon(d) {
    const best = [...d.strategies].sort((a, b) => b.ret - a.ret)[0];
    const rows = d.strategies.map(s => {
      const retCls = s.ret >= 0 ? 'tp-pos' : 'tp-neg';
      const isBest = s.name === best.name ? ' tp-best-row' : '';
      const maxDdCell = s.max_dd != null
        ? `<td class="tp-dd ${s.max_dd > 25 ? 'tp-neg' : ''}">${s.max_dd}%</td>`
        : `<td class="tp-dd tp-muted">—</td>`;
      return `<tr class="${isBest}">
        <td class="tp-strategy-name">${statusDot(s.status)} ${escapeHtml(s.name)}</td>
        <td class="tp-ret ${retCls}">${s.ret >= 0 ? '+' : ''}${s.ret}%</td>
        <td class="tp-cagr ${retCls}">${s.cagr >= 0 ? '+' : ''}${s.cagr}%/yr</td>
        ${maxDdCell}
        <td class="tp-note">${escapeHtml(s.note)}</td>
      </tr>`;
    }).join('');

    const rcAlert = d.is_rc
      ? `<div class="tp-rc-alert">⚠ Rollercoaster: peaked at +${d.peak_gain}% — now at +${d.strategies[0].ret}%. Paper gains surrendered.</div>`
      : `<div class="tp-rc-ok">✓ No rollercoaster — peak gain +${d.peak_gain}% mostly preserved.</div>`;

    const levels = d.levels.map(l =>
      `<span class="tp-level"><span class="tp-level-lbl">${escapeHtml(l.label)}</span><strong>$${l.price}</strong></span>`
    ).join('');

    return `
    <div class="tp-entry-info">
      Simulated entry <strong>${d.entry_date}</strong> at <strong>$${d.entry}</strong> · holding <strong>${d.years}y</strong>
    </div>
    ${clsBadge(d.cls)}
    ${rcAlert}
    <table class="tp-table">
      <thead><tr><th>Strategy</th><th>Return</th><th>CAGR</th><th>Max DD</th><th>Notes</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <div class="tp-levels-row">
      <span class="tp-levels-lbl">Next profit targets (from $${d.current})</span>
      ${levels}
    </div>`;
  }

  const tabBtns = horizons.map((h, i) =>
    `<button class="tp-tab${i === 0 ? ' active' : ''}" data-horizon="${h}">${h}</button>`
  ).join('');

  return `
  <div class="sc-card sc-tp" id="scTpCard">
    <div class="sc-card-header">
      <div class="sc-card-title">Take-Profit Lab</div>
      <div class="tp-tabs">${tabBtns}<button class="tp-tab tp-tab-guide" data-horizon="guide">Guide ⓘ</button></div>
    </div>
    <div class="tp-body" id="scTpBody"></div>
  </div>
  <script type="application/json" id="scTpData">${JSON.stringify(tp)}</script>`;
}

function setupTakeProfit(container) {
  const card = container.querySelector('#scTpCard');
  if (!card) return;
  const dataEl = container.querySelector('#scTpData');
  if (!dataEl) return;
  const tp = JSON.parse(dataEl.textContent);
  const body = card.querySelector('#scTpBody');
  const tabs = card.querySelectorAll('.tp-tab');

  function renderHorizon(d) {
    const best = [...d.strategies].sort((a, b) => b.ret - a.ret)[0];
    const clsBadge = cls => ({
      hold:     `<span class="tp-badge tp-hold">◎ Hold Forever</span>`,
      tier:     `<span class="tp-badge tp-tier">⊘ Tiered Exit Preferred</span>`,
      trailing: `<span class="tp-badge tp-trailing">⊘ Trailing Stop Preferred</span>`,
    }[cls] ?? '');
    const statusDot = s => ({
      holding: `<span class="tp-dot tp-dot-hold">●</span>`,
      exited:  `<span class="tp-dot tp-dot-exit">◉</span>`,
      partial: `<span class="tp-dot tp-dot-partial">◑</span>`,
    }[s] ?? '');

    const rows = d.strategies.map(s => {
      const retCls = s.ret >= 0 ? 'tp-pos' : 'tp-neg';
      const isBest = s.name === best.name ? ' tp-best-row' : '';
      const maxDdCell = s.max_dd != null
        ? `<td class="tp-dd ${s.max_dd > 25 ? 'tp-neg' : ''}">${s.max_dd}%</td>`
        : `<td class="tp-dd tp-muted">—</td>`;
      return `<tr class="${isBest}">
        <td class="tp-strategy-name">${statusDot(s.status)} ${escapeHtml(s.name)}</td>
        <td class="tp-ret ${retCls}">${s.ret >= 0 ? '+' : ''}${s.ret}%</td>
        <td class="tp-cagr ${retCls}">${s.cagr >= 0 ? '+' : ''}${s.cagr}%/yr</td>
        ${maxDdCell}
        <td class="tp-note">${escapeHtml(s.note)}</td>
      </tr>`;
    }).join('');

    const rcAlert = d.is_rc
      ? `<div class="tp-rc-alert">⚠ Rollercoaster: peaked at +${d.peak_gain}% — now at +${d.strategies[0].ret}%. Paper gains surrendered.</div>`
      : `<div class="tp-rc-ok">✓ No rollercoaster — peak gain +${d.peak_gain}% mostly preserved.</div>`;

    const levels = (d.levels || []).map(l =>
      `<span class="tp-level"><span class="tp-level-lbl">${escapeHtml(l.label)}</span><strong>$${l.price}</strong></span>`
    ).join('');

    body.innerHTML = `
    <div class="tp-entry-info">Simulated entry <strong>${d.entry_date}</strong> at <strong>$${d.entry}</strong> · <strong>${d.years}y</strong></div>
    ${clsBadge(d.cls)}
    ${rcAlert}
    <table class="tp-table">
      <thead><tr><th>Strategy</th><th>Return</th><th>CAGR</th><th>Max DD</th><th>Notes</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <div class="tp-levels-row">
      <span class="tp-levels-lbl">Next targets (from $${d.current})</span>${levels}
    </div>`;
  }

  function renderGuide() {
    body.innerHTML = `
    <div class="tp-guide">
      <div class="tp-guide-section">
        <div class="tp-guide-heading">Exit Strategies</div>
        <div class="tp-guide-grid">
          <div class="tp-guide-card">
            <div class="tp-guide-name">◎ Hold Forever</div>
            <div class="tp-guide-desc">No exit plan — ride all drawdowns indefinitely. Best for highest-conviction compounders where you trust the business over a decade.</div>
            <div class="tp-guide-pro">✓ Captures full compounding</div>
            <div class="tp-guide-con">✗ Requires stomach for 30–50% drawdowns</div>
          </div>
          <div class="tp-guide-card">
            <div class="tp-guide-name">⊘ Trailing Stop 15%</div>
            <div class="tp-guide-desc">Sell everything if price falls 15% from its all-time high since entry. Tight protection — ideal for positions where you want to lock in early gains.</div>
            <div class="tp-guide-pro">✓ Limits downside sharply</div>
            <div class="tp-guide-con">✗ Often shaken out by normal corrections</div>
          </div>
          <div class="tp-guide-card">
            <div class="tp-guide-name">⊘ Trailing Stop 25%</div>
            <div class="tp-guide-desc">More breathing room — only exits on a 25%+ pullback from peak. Better suited to volatile growth names that routinely correct 20%.</div>
            <div class="tp-guide-pro">✓ Survives most corrections</div>
            <div class="tp-guide-con">✗ Still triggers in bear markets</div>
          </div>
          <div class="tp-guide-card">
            <div class="tp-guide-name">◑ Tiered ⅓+⅓ at +25%/+75%</div>
            <div class="tp-guide-desc">Sell ⅓ of position at +25% gain, another ⅓ at +75%, hold the final third forever. Locks in real cash while keeping long-term exposure.</div>
            <div class="tp-guide-pro">✓ Balances certainty and upside</div>
            <div class="tp-guide-con">✗ Reduces total gain if stock 10×s</div>
          </div>
          <div class="tp-guide-card">
            <div class="tp-guide-name">◉ Exit All at +50%</div>
            <div class="tp-guide-desc">Hard milestone — once up 50%, take everything off the table. Simple, disciplined, no judgement required at the moment of decision.</div>
            <div class="tp-guide-pro">✓ Removes emotion at exit</div>
            <div class="tp-guide-con">✗ Caps gains on multi-baggers</div>
          </div>
        </div>
      </div>
      <div class="tp-guide-section">
        <div class="tp-guide-heading">Classification Logic</div>
        <div class="tp-guide-rules">
          <div class="tp-guide-rule">
            <span class="tp-badge tp-hold" style="margin-right:0.5rem">◎ Hold</span>
            Max drawdown &lt; 18% <em>and</em> no rollercoaster detected. The position has been well-behaved — no reason to layer in stops.
          </div>
          <div class="tp-guide-rule">
            <span class="tp-badge tp-tier" style="margin-right:0.5rem">⊘ Tiered</span>
            Drawdown &gt; 35% <em>or</em> rollercoaster triggered (peak gain surrendered by 15%+). High volatility warrants taking chips off the table in stages.
          </div>
          <div class="tp-guide-rule">
            <span class="tp-badge tp-trailing" style="margin-right:0.5rem">⊘ Trailing</span>
            Drawdown between 25–35%, no rollercoaster. Meaningful pullback — a trailing stop lets the position recover but exits if the trend breaks.
          </div>
        </div>
        <div class="tp-guide-footnote">⚠ Rollercoaster = peaked at &gt;+20% gain, then gave back more than 15 percentage points. Classic paper-gain trap.</div>
      </div>
      <div class="tp-guide-section">
        <div class="tp-guide-heading">Metrics Explained</div>
        <div class="tp-guide-rules">
          <div class="tp-guide-rule"><strong>Return</strong> — Total gain/loss from simulated entry to today (or exit date if triggered).</div>
          <div class="tp-guide-rule"><strong>CAGR</strong> — Annualised return. Accounts for how long you held — a +50% return over 5 years is very different from 1 year.</div>
          <div class="tp-guide-rule"><strong>Max DD</strong> — Largest peak-to-trough drawdown since entry. Only shown for Hold (trailing stops exit before the worst drawdown).</div>
        </div>
      </div>
    </div>`;
  }

  tabs.forEach(btn => {
    btn.addEventListener('click', () => {
      tabs.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      if (btn.dataset.horizon === 'guide') renderGuide();
      else renderHorizon(tp[btn.dataset.horizon]);
    });
  });

  // Render first available horizon
  const first = Object.keys(tp)[0];
  if (first) renderHorizon(tp[first]);
}

// ── Capital Return Section (Dividends + Buybacks) ──────────────────────────

function renderCapitalReturnSection(cr, currSym = '$') {
  if (!cr) return '';
  const div = cr.dividend || { has_dividend: false };
  const bb  = cr.buyback  || { has_buybacks: false };
  if (!div.has_dividend && !bb.has_buybacks) return '';

  const fmtVal = v => {
    if (v == null) return '—';
    const av = Math.abs(v);
    if (av >= 1e9)  return `${currSym}${(v/1e9).toFixed(2)}B`;
    if (av >= 1e6)  return `${currSym}${(v/1e6).toFixed(1)}M`;
    return `${currSym}${v.toLocaleString()}`;
  };
  const fmtShares = v => {
    if (v == null) return '—';
    if (v >= 1e9) return `${(v/1e9).toFixed(2)}B`;
    if (v >= 1e6) return `${(v/1e6).toFixed(1)}M`;
    return v.toLocaleString();
  };

  // ── Simple SVG bar chart helper ──────────────────────────────────────
  function barChart(items, labelKey, valKey, opts = {}) {
    if (!items || !items.length) return '<p class="cr-empty">No history available.</p>';
    const W = 600, H = 110, padL = 4, padR = 4, padB = 20, padT = 6;
    const n = items.length;
    const gap = 6;
    const barW = (W - padL - padR - gap * (n - 1)) / n;
    const maxV = Math.max(...items.map(it => it[valKey]), 0.0001);
    const curYear = new Date().getFullYear();
    const bars = items.map((it, i) => {
      const x = padL + i * (barW + gap);
      const h = Math.max(2, (it[valKey] / maxV) * (H - padT - padB));
      const y = H - padB - h;
      const isLast = i === n - 1;
      const isYTD = opts.markYTD && Number(it[labelKey]) === curYear;
      const fill = opts.color || 'var(--accent)';
      const labelRaw = String(it[labelKey]).replace('FY', '');
      const label = isYTD ? `${labelRaw}*` : labelRaw;
      return `
        <rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${h.toFixed(1)}"
              rx="2" fill="${isLast && opts.highlightLast ? (opts.highlightColor || fill) : fill}"
              opacity="${isLast ? 1 : 0.55}" ${isYTD ? 'stroke="var(--muted)" stroke-dasharray="2,2" stroke-width="1"' : ''}>
          <title>${escapeHtml(String(it[labelKey]))}${isYTD ? ' (year to date)' : ''}: ${opts.fmt ? opts.fmt(it[valKey]) : it[valKey]}</title>
        </rect>
        <text x="${(x + barW / 2).toFixed(1)}" y="${H - 5}" text-anchor="middle" font-size="9" fill="var(--muted)">${escapeHtml(label)}</text>`;
    }).join('');
    return `<svg viewBox="0 0 ${W} ${H}" class="cr-bar-chart" preserveAspectRatio="none">${bars}</svg>`;
  }

  const trendBadgeClass = t => {
    if (t === 'Growing' || t === 'Accelerating') return 'cr-trend-good';
    if (t === 'Flat' || t === 'Steady' || t === 'Recently Initiated') return 'cr-trend-neutral';
    if (t === 'Declining' || t === 'Slowing') return 'cr-trend-warn';
    if (t === 'Cut' || t === 'Paused') return 'cr-trend-bad';
    return 'cr-trend-neutral';
  };

  // ── Dividend sub-card ─────────────────────────────────────────────────
  let dividendHtml = '';
  if (div.has_dividend) {
    const chart = barChart(div.history || [], 'year', 'total', {
      fmt: v => `${currSym}${v.toFixed(2)}/sh`,
      highlightLast: true,
      markYTD: true,
    });
    dividendHtml = `
    <div class="cr-subcard">
      <div class="cr-subhead">
        <span class="cr-subtitle">Dividend</span>
        <span class="cr-trend-badge ${trendBadgeClass(div.trend)}">${div.trend}</span>
      </div>
      <div class="cr-stats">
        <div class="cr-stat"><span class="cr-stat-lbl">Annual rate</span><span class="cr-stat-val">${currSym}${div.rate}/sh</span></div>
        <div class="cr-stat"><span class="cr-stat-lbl">Yield</span><span class="cr-stat-val">${div.yield_pct != null ? div.yield_pct + '%' : '—'}</span></div>
        <div class="cr-stat"><span class="cr-stat-lbl">Payout ratio</span><span class="cr-stat-val"${div.payout_note ? ` title="${escapeHtml(div.payout_note)}"` : ''}>${div.payout_ratio_pct != null ? div.payout_ratio_pct + '%' : (div.payout_note ? 'n/m' : '—')}</span></div>
        <div class="cr-stat"><span class="cr-stat-lbl">Growth streak</span><span class="cr-stat-val">${div.consecutive_growth_years != null ? div.consecutive_growth_years + (div.consecutive_growth_years > 0 ? ' yrs' : '') : '—'}</span></div>
        <div class="cr-stat"><span class="cr-stat-lbl">CAGR 5Y</span><span class="cr-stat-val">${div.cagr_5y_pct != null ? div.cagr_5y_pct + '%' : '—'}</span></div>
        <div class="cr-stat"><span class="cr-stat-lbl">CAGR 10Y</span><span class="cr-stat-val">${div.cagr_10y_pct != null ? div.cagr_10y_pct + '%' : '—'}</span></div>
      </div>
      ${div.yield_basis ? `<div class="cr-basis">Yield basis: ${escapeHtml(div.yield_basis)}</div>` : ''}
      ${div.payout_note ? `<div class="cr-basis">Payout: ${escapeHtml(div.payout_note)}</div>` : ''}
      <div class="cr-chart-wrap">
        <div class="cr-chart-label">Dividend per share by year${div.history?.some(h => h.year === new Date().getFullYear()) ? ' (* = year to date, not yet complete)' : ''}</div>
        ${chart}
      </div>
      <div class="cr-meta-row">
        ${div.ex_div_date ? `<span>Ex-div date: <strong>${div.ex_div_date}</strong></span>` : ''}
        ${div.last_div_value != null ? `<span>Last payment: <strong>${currSym}${div.last_div_value}</strong></span>` : ''}
      </div>
    </div>`;
  } else {
    dividendHtml = `
    <div class="cr-subcard">
      <div class="cr-subhead"><span class="cr-subtitle">Dividend</span><span class="cr-trend-badge cr-trend-neutral">No Dividend</span></div>
      <p class="cr-empty">Company does not currently pay a dividend.</p>
    </div>`;
  }

  // ── Buyback sub-card ─────────────────────────────────────────────────
  let buybackHtml = '';
  if (bb.has_buybacks) {
    const chart = barChart(bb.annual || [], 'period', 'amount', {
      fmt: v => fmtVal(v),
      highlightLast: true,
      color: '#7c3aed',
    });
    const reductionStr = bb.reduction_3y_pct != null
      ? `${bb.reduction_3y_pct > 0 ? '-' : '+'}${Math.abs(bb.reduction_3y_pct)}%`
      : '—';
    buybackHtml = `
    <div class="cr-subcard">
      <div class="cr-subhead">
        <span class="cr-subtitle">Buyback</span>
        <span class="cr-trend-badge ${trendBadgeClass(bb.trend)}">${bb.trend}</span>
      </div>
      <div class="cr-stats">
        <div class="cr-stat"><span class="cr-stat-lbl">Latest annual</span><span class="cr-stat-val">${fmtVal(bb.latest_amount)}</span></div>
        <div class="cr-stat"><span class="cr-stat-lbl">Buyback yield</span><span class="cr-stat-val">${bb.buyback_yield_pct != null ? bb.buyback_yield_pct + '%' : '—'}</span></div>
        <div class="cr-stat"><span class="cr-stat-lbl">Shares out (now)</span><span class="cr-stat-val">${fmtShares(bb.shares_now)}</span></div>
        <div class="cr-stat"><span class="cr-stat-lbl">Shares out (3Y ago)</span><span class="cr-stat-val">${fmtShares(bb.shares_3y_ago)}</span></div>
        <div class="cr-stat"><span class="cr-stat-lbl">Share count Δ (3Y)</span><span class="cr-stat-val ${bb.reduction_3y_pct > 0 ? 'cr-good' : bb.reduction_3y_pct < 0 ? 'cr-bad' : ''}"${bb.reduction_note ? ` title="${escapeHtml(bb.reduction_note)}"` : ''}>${reductionStr}</span></div>
      </div>
      <div class="cr-chart-wrap">
        <div class="cr-chart-label">Annual repurchases by fiscal year (latest bar highlighted)</div>
        ${chart}
      </div>
    </div>`;
  } else {
    buybackHtml = `
    <div class="cr-subcard">
      <div class="cr-subhead"><span class="cr-subtitle">Buyback</span><span class="cr-trend-badge cr-trend-neutral">None</span></div>
      <p class="cr-empty">No share repurchase activity reported.</p>
    </div>`;
  }

  return `
  <div class="sc-card sc-capital-return">
    <div class="sc-card-header">
      <div class="sc-card-title">Capital Return (Dividends &amp; Buybacks)</div>
    </div>
    <p class="sc-card-sub">
      How the company returns cash to shareholders, and whether that return is growing, steady, or shrinking.
    </p>
    <div class="cr-grid">
      ${dividendHtml}
      ${buybackHtml}
    </div>
  </div>`;
}

// ── Institutional Flow Section ────────────────────────────────────────────

function renderFlowSection(flow, currSym = '$') {
  if (!flow) return '';

  const fmtVal = v => {
    if (v >= 1e12) return `${currSym}${(v/1e12).toFixed(2)}T`;
    if (v >= 1e9)  return `${currSym}${(v/1e9).toFixed(2)}B`;
    if (v >= 1e6)  return `${currSym}${(v/1e6).toFixed(1)}M`;
    return `${currSym}${v.toLocaleString()}`;
  };
  const fmtShares = v => {
    if (v >= 1e9)  return `${(v/1e9).toFixed(2)}B`;
    if (v >= 1e6)  return `${(v/1e6).toFixed(1)}M`;
    if (v >= 1e3)  return `${(v/1e3).toFixed(0)}K`;
    return v.toString();
  };
  // Passive index managers restructure sub-funds regularly; pctChange=100 is a
  // filing artifact for them, not a genuinely new position.
  const PASSIVE_MANAGERS = ['vanguard','blackrock','ishares','state street','ssga',
    'geode','northern trust','dimensional','invesco','schwab','fidelity','jpmorgan'];
  const isPassive = name => PASSIVE_MANAGERS.some(p => name.toLowerCase().includes(p));

  const chgBadge = (pct, name) => {
    if (pct === 100) {
      return isPassive(name)
        ? `<span class="fl-badge fl-flat">— n/a</span>`
        : `<span class="fl-badge fl-new">NEW</span>`;
    }
    if (pct > 5)  return `<span class="fl-badge fl-added">▲ +${pct.toFixed(1)}%</span>`;
    if (pct < -5) return `<span class="fl-badge fl-reduced">▼ ${pct.toFixed(1)}%</span>`;
    return `<span class="fl-badge fl-flat">◆ ${pct > 0 ? '+' : ''}${pct.toFixed(1)}%</span>`;
  };

  // Concentration: top-3 holders combined %
  const top3pct = (flow.top_holders || []).slice(0, 3).reduce((s, h) => s + h.pct_held, 0);

  const ownershipBar = `
  <div class="fl-ownership-bar">
    <div class="fl-own-strip" style="width:${flow.inst_pct}%" title="Institutional ${flow.inst_pct}%"></div>
    <div class="fl-own-strip fl-own-insider" style="width:${flow.insider_pct}%" title="Insider ${flow.insider_pct}%"></div>
  </div>`;

  const ret12mStr = flow.ret_12m != null
    ? `<span class="${flow.ret_12m >= 0 ? 'fl-up' : 'fl-dn'}">${flow.ret_12m >= 0 ? '+' : ''}${flow.ret_12m}%</span>`
    : '—';

  const etfPills = (flow.etf_exposure || []).map(e =>
    `<span class="fl-etf-pill"><strong>${e.etf}</strong> ${e.weight_pct}% <span class="fl-etf-rank">#${e.rank}</span></span>`
  ).join('');

  const summaryStats = `
  <div class="fl-summary">
    <div class="fl-stat"><span class="fl-stat-lbl">Institutional</span><span class="fl-stat-val">${flow.inst_pct}%</span></div>
    <div class="fl-stat"><span class="fl-stat-lbl">Insider</span><span class="fl-stat-val">${flow.insider_pct}%</span></div>
    <div class="fl-stat"><span class="fl-stat-lbl">Float held</span><span class="fl-stat-val">${flow.inst_float}%</span></div>
    <div class="fl-stat"><span class="fl-stat-lbl">Inst. holders</span><span class="fl-stat-val">${flow.inst_count.toLocaleString()}</span></div>
    <div class="fl-stat"><span class="fl-stat-lbl">Top-3 conc.</span><span class="fl-stat-val">${top3pct.toFixed(1)}%</span></div>
    <div class="fl-stat"><span class="fl-stat-lbl">12M return</span><span class="fl-stat-val">${ret12mStr}</span></div>
  </div>
  ${etfPills ? `<div class="fl-etf-row"><span class="fl-etf-label">Passive exposure</span>${etfPills}</div>` : ''}`;

  const rows = (flow.top_holders || []).map((h, i) => `
  <tr>
    <td class="fl-rank">${i + 1}</td>
    <td class="fl-holder-name">${escapeHtml(h.name)}</td>
    <td class="fl-pct">${h.pct_held}%</td>
    <td class="fl-shares">${fmtShares(h.shares)}</td>
    <td class="fl-value">${fmtVal(h.value)}</td>
    <td class="fl-chg">${chgBadge(h.pct_change, h.name)}</td>
  </tr>`).join('');

  const asOf = flow.top_holders?.[0]?.date ?? '';

  // ── Insider trading sub-section ───────────────────────────────────────
  const isum = flow.insider_summary;
  let insiderHtml = '';
  if (isum) {
    const net = isum.net_shares;
    const netBadge = net != null
      ? (net > 0
          ? `<span class="ins-net-badge ins-net-buy">▲ Net buying</span>`
          : net < 0
            ? `<span class="ins-net-badge ins-net-sell">▼ Net selling</span>`
            : `<span class="ins-net-badge ins-net-flat">◆ Flat</span>`)
      : '';
    const pctNetStr = isum.pct_net != null ? `${(isum.pct_net * 100).toFixed(2)}%` : '—';

    const insiderSummaryStats = `
    <div class="fl-summary ins-summary">
      <div class="fl-stat"><span class="fl-stat-lbl">Buys (6M)</span><span class="fl-stat-val ins-buy">${isum.buy_shares != null ? fmtShares(isum.buy_shares) : '—'} <span class="ins-count">(${isum.buy_count ?? 0})</span></span></div>
      <div class="fl-stat"><span class="fl-stat-lbl">Sells (6M)</span><span class="fl-stat-val ins-sell">${isum.sell_shares != null ? fmtShares(isum.sell_shares) : '—'} <span class="ins-count">(${isum.sell_count ?? 0})</span></span></div>
      <div class="fl-stat"><span class="fl-stat-lbl">Net shares</span><span class="fl-stat-val">${net != null ? fmtShares(Math.abs(net)) : '—'} ${netBadge}</span></div>
      <div class="fl-stat"><span class="fl-stat-lbl">% of holdings</span><span class="fl-stat-val">${pctNetStr}</span></div>
      <div class="fl-stat"><span class="fl-stat-lbl">Total insider shares</span><span class="fl-stat-val">${isum.total_held != null ? fmtShares(isum.total_held) : '—'}</span></div>
    </div>`;

    const kindBadge = kind => {
      if (kind === 'Buy')      return `<span class="ins-kind ins-kind-buy">Buy</span>`;
      if (kind === 'Sell')     return `<span class="ins-kind ins-kind-sell">Sell</span>`;
      if (kind === 'Award')    return `<span class="ins-kind ins-kind-award">Award</span>`;
      if (kind === 'Exercise') return `<span class="ins-kind ins-kind-exercise">Exercise</span>`;
      return `<span class="ins-kind ins-kind-other">Other</span>`;
    };

    const txnRows = (flow.insider_txns || []).map(tx => `
    <tr>
      <td class="ins-date">${tx.date}</td>
      <td class="ins-name">${escapeHtml(tx.insider)}</td>
      <td class="ins-position">${escapeHtml(tx.position)}</td>
      <td class="ins-kind-cell">${kindBadge(tx.kind)}</td>
      <td class="ins-shares">${tx.shares != null ? fmtShares(tx.shares) : '—'}</td>
      <td class="ins-value">${tx.value ? fmtVal(tx.value) : '—'}</td>
    </tr>`).join('');

    insiderHtml = `
    <div class="sc-flow-divider"></div>
    <div class="sc-card-header sc-flow-subheader">
      <div class="sc-card-title sc-card-title-sm">Insider Trading</div>
      <div class="fl-asof">Last 6 months</div>
    </div>
    ${insiderSummaryStats}
    ${txnRows ? `
    <table class="fl-table ins-table">
      <thead>
        <tr><th>Date</th><th>Insider</th><th>Position</th><th>Type</th><th>Shares</th><th>Value</th></tr>
      </thead>
      <tbody>${txnRows}</tbody>
    </table>` : '<p class="ins-empty">No recent insider transactions reported.</p>'}`;
  }

  return `
  <div class="sc-card sc-flow">
    <div class="sc-card-header">
      <div class="sc-card-title">Institutional Flow</div>
      ${asOf ? `<div class="fl-asof">13F as of ${asOf}</div>` : ''}
    </div>
    ${summaryStats}
    ${ownershipBar}
    <table class="fl-table">
      <thead>
        <tr>
          <th>#</th><th>Institution</th><th>% Held</th><th>Shares</th><th>Value</th><th>QoQ Chg</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
    ${insiderHtml}
  </div>`;
}

// ── Momentum Indicators Section ───────────────────────────────────────────

function renderMomentumSection(prices) {
  if (!prices || prices.length < 30) return '';
  const periods = ['1M','3M','6M','YTD','1Y','3Y','5Y'];
  const periodBtns = periods.map(p =>
    `<button class="sc-mom-period-btn${p === '1Y' ? ' active' : ''}" data-period="${p}">${p}</button>`
  ).join('');
  return `
  <div class="sc-card sc-momentum">
    <div class="sc-card-header">
      <div class="sc-card-title">Momentum Indicators</div>
      <div class="sc-mom-tabs" id="scMomTabs">
        <button class="sc-mom-tab active" data-mom="ma">Moving Averages</button>
        <button class="sc-mom-tab" data-mom="rsi">RSI (14)</button>
        <button class="sc-mom-tab" data-mom="macd">MACD</button>
        <button class="sc-mom-tab" data-mom="bb">Bollinger Bands</button>
        <button class="sc-mom-tab" data-mom="roc">Rate of Change</button>
        <button class="sc-mom-tab" data-mom="stoch">Stochastic</button>
        <button class="sc-mom-tab" data-mom="aroon">Aroon (Trend)</button>
      </div>
    </div>
    <div class="sc-mom-period-bar" id="scMomPeriodBar">
      <span class="sc-mom-period-label">Horizon</span>
      ${periodBtns}
    </div>
    <div class="sc-mom-body" id="scMomBody"></div>
  </div>`;
}

function setupMomentum(container, prices, currSym = '$') {
  if (!prices || prices.length < 30) return;
  const tabs       = container.querySelector('#scMomTabs');
  const body       = container.querySelector('#scMomBody');
  const periodBar  = container.querySelector('#scMomPeriodBar');
  if (!tabs || !body) return;

  // ── Full history — always used for indicator computation ────────────
  const closes = prices.map(p => p.c);
  const dates  = prices.map(p => p.d);
  const N      = closes.length;

  // View state — controls the displayed window only
  let viewStart = 0;
  let vN = N;  // length of visible window; xOf uses this

  function getViewStart(period) {
    const now = new Date();
    let cutoff;
    if      (period === '1M')  cutoff = new Date(now.getFullYear(), now.getMonth() - 1,  now.getDate());
    else if (period === '3M')  cutoff = new Date(now.getFullYear(), now.getMonth() - 3,  now.getDate());
    else if (period === '6M')  cutoff = new Date(now.getFullYear(), now.getMonth() - 6,  now.getDate());
    else if (period === 'YTD') cutoff = new Date(now.getFullYear(), 0, 1);
    else if (period === '1Y')  cutoff = new Date(now.getFullYear() - 1, now.getMonth(), now.getDate());
    else if (period === '3Y')  cutoff = new Date(now.getFullYear() - 3, now.getMonth(), now.getDate());
    else return 0;
    const cutStr = cutoff.toISOString().slice(0, 10);
    const idx = dates.findIndex(d => d >= cutStr);
    return idx >= 0 ? idx : 0;
  }

  function setView(period) {
    viewStart = getViewStart(period);
    vN = N - viewStart;
  }
  setView('1Y');

  // MA visibility state — persists across period/tab changes
  const activeMAns = new Set([20, 50, 100, 200]);

  // ── Math helpers — always compute on the FULL closes array ──────────
  function sma(n) {
    return closes.map((_, i) => {
      if (i < n - 1) return null;
      let s = 0; for (let j = i - n + 1; j <= i; j++) s += closes[j];
      return s / n;
    });
  }

  function ema(n) {
    const a = 2 / (n + 1), res = new Array(N).fill(null);
    let init = 0;
    for (let i = 0; i < N; i++) {
      if (i < n - 1) { init += closes[i]; continue; }
      if (i === n - 1) { res[i] = (init + closes[i]) / n; continue; }
      res[i] = closes[i] * a + res[i - 1] * (1 - a);
    }
    return res;
  }

  function emaOf(vals, n) {
    const a = 2 / (n + 1), res = new Array(vals.length).fill(null);
    const firstI = vals.findIndex(v => v !== null);
    if (firstI < 0 || firstI + n > vals.length) return res;
    let sum = 0;
    for (let i = firstI; i < firstI + n; i++) {
      if (vals[i] === null) return res;
      sum += vals[i];
    }
    res[firstI + n - 1] = sum / n;
    for (let i = firstI + n; i < vals.length; i++) {
      if (vals[i] === null) continue;
      res[i] = vals[i] * a + res[i - 1] * (1 - a);
    }
    return res;
  }

  function rsiVals(n = 14) {
    const res = new Array(N).fill(null);
    if (N < n + 1) return res;
    let ag = 0, al = 0;
    for (let i = 1; i <= n; i++) {
      const d = closes[i] - closes[i - 1];
      if (d > 0) ag += d; else al -= d;
    }
    ag /= n; al /= n;
    res[n] = al === 0 ? 100 : 100 - 100 / (1 + ag / al);
    for (let i = n + 1; i < N; i++) {
      const d = closes[i] - closes[i - 1];
      ag = (ag * (n - 1) + Math.max(d, 0)) / n;
      al = (al * (n - 1) + Math.max(-d, 0)) / n;
      res[i] = al === 0 ? 100 : 100 - 100 / (1 + ag / al);
    }
    return res;
  }

  function bollingerBands(n = 20, k = 2) {
    const mid = sma(n);
    const upper = [], lower = [];
    for (let i = 0; i < N; i++) {
      if (mid[i] === null) { upper.push(null); lower.push(null); continue; }
      let v = 0;
      for (let j = i - n + 1; j <= i; j++) v += (closes[j] - mid[i]) ** 2;
      const sd = Math.sqrt(v / n);
      upper.push(mid[i] + k * sd);
      lower.push(mid[i] - k * sd);
    }
    return { mid, upper, lower };
  }

  // ── SVG primitives — xOf uses vN (the view window length) ──────────
  const W = 680, PL = 54, PR = 10, PT = 8, PB = 26;

  function xOf(i) { return PL + (i / (vN - 1)) * (W - PL - PR); }

  function seriesPath(vals, yOf) {
    let d = '';
    for (let i = 0; i < vals.length; i++) {
      if (vals[i] === null) continue;
      d += `${d === '' || vals[i - 1] === null ? 'M' : 'L'} ${xOf(i).toFixed(1)},${yOf(vals[i]).toFixed(1)} `;
    }
    return d.trim();
  }

  function xAxis(H) {
    const vd = dates.slice(viewStart);
    const idxs = [0, Math.round(vN * 0.25), Math.round(vN * 0.5), Math.round(vN * 0.75), vN - 1];
    return idxs.map((i, k) => {
      const ci = Math.min(i, vN - 1);
      const dt = new Date(vd[ci] + 'T00:00:00');
      const md = dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
      const yy = dt.toLocaleDateString('en-US', { year: '2-digit' });
      const label = `${md} '${yy}`;
      const anchor = k === 0 ? 'start' : k === idxs.length - 1 ? 'end' : 'middle';
      return `<text x="${xOf(ci).toFixed(1)}" y="${H - 6}" text-anchor="${anchor}" font-size="10" fill="#9ca3af">${label}</text>`;
    }).join('');
  }

  // ── Tab: Moving Averages ────────────────────────────────────────────
  function renderMA() {
    const vc   = closes.slice(viewStart);   // view closes
    const last = vc[vc.length - 1];
    const allMaDefs = [
      { n: 20,  label: 'MA20',  col: '#818cf8' },
      { n: 50,  label: 'MA50',  col: '#f59e0b' },
      { n: 100, label: 'MA100', col: '#10b981' },
      { n: 200, label: 'MA200', col: '#ef4444' },
    ].filter(d => N >= d.n);  // available if full history is long enough

    // Compute on full history, slice to view window for display
    const maData = allMaDefs.map(d => ({
      ...d,
      vals: sma(d.n).slice(viewStart),   // view-window slice
      on: activeMAns.has(d.n)
    }));
    const activeData = maData.filter(m => m.on);

    // Toggle buttons for available MAs
    const toggleBtns = maData.map(m =>
      `<button class="sc-ma-toggle${m.on ? ' on' : ''}" data-ma="${m.n}" style="--ma-col:${m.col}">${m.label}</button>`
    ).join('');

    const H = 170, plotH = H - PT - PB;

    // Y range: view price + all active MA values (view slice)
    const rangeVals = [vc, ...activeData.map(m => m.vals)].flat().filter(v => v !== null);
    const lo = Math.min(...rangeVals), hi = Math.max(...rangeVals);
    const pad = (hi - lo) * 0.05 || 1;
    const yOf = v => PT + plotH - ((v - (lo - pad)) / ((hi + pad) - (lo - pad))) * plotH;

    const pricePath = vc.map((c, i) => `${i === 0 ? 'M' : 'L'} ${xOf(i).toFixed(1)},${yOf(c).toFixed(1)}`).join(' ');
    const maLines = activeData.map(m =>
      `<path d="${seriesPath(m.vals, yOf)}" fill="none" stroke="${m.col}" stroke-width="1.4" stroke-linejoin="round" opacity="0.9"/>`
    ).join('');

    const yTks = [lo, (lo + hi) / 2, hi].map(v => {
      const lbl = v >= 1000 ? `${currSym}${(v/1000).toFixed(1)}k` : `${currSym}${v.toFixed(0)}`;
      return `<text x="${PL-5}" y="${yOf(v).toFixed(1)}" text-anchor="end" dominant-baseline="middle" font-size="9.5" fill="#9ca3af">${lbl}</text>`;
    }).join('');

    // Stats strip — only active MAs (m.vals is view-sliced, use last element)
    const stats = activeData.map(m => {
      const mv = m.vals[m.vals.length - 1];
      if (mv == null) return '';
      const pct = (last - mv) / mv * 100;
      const up = pct >= 0;
      return `<div class="sc-mom-stat">
        <span class="sc-mom-legend-dot" style="background:${m.col}"></span>
        <span class="sc-mom-stat-lbl">${m.label}</span>
        <span class="sc-mom-stat-val">${currSym}${mv.toFixed(2)}</span>
        <span class="sc-mom-stat-chg ${up ? 'mom-up' : 'mom-dn'}">${up ? '+' : ''}${pct.toFixed(1)}%</span>
      </div>`;
    }).join('') || '<div class="sc-mom-stat"><span class="sc-mom-hint">Select at least one MA to display.</span></div>';

    // Golden/Death cross badge (always from MA50 vs MA200, regardless of toggle)
    const ma50full  = maData.find(m => m.n === 50);
    const ma200full = maData.find(m => m.n === 200);
    let crossBadge = '';
    if (ma50full && ma200full) {
      const vLen = ma50full.vals.length;
      const v50 = ma50full.vals[vLen-1], v200 = ma200full.vals[vLen-1];
      const p50 = ma50full.vals[vLen-2], p200 = ma200full.vals[vLen-2];
      if (v50 && v200 && p50 && p200) {
        if (p50 < p200 && v50 >= v200)      crossBadge = '<span class="sc-mom-badge mom-bull">Golden Cross ↑</span>';
        else if (p50 > p200 && v50 <= v200) crossBadge = '<span class="sc-mom-badge mom-bear">Death Cross ↓</span>';
        else if (v50 > v200)                crossBadge = '<span class="sc-mom-badge mom-bull">MA50 &gt; MA200</span>';
        else                                crossBadge = '<span class="sc-mom-badge mom-bear">MA50 &lt; MA200</span>';
      }
    }

    // Crossover markers — only for active MAs ≥ 50
    function triUp(cx, cy, col, label) {
      const [x, y] = [+cx, +cy];
      return `<polygon points="${x},${y-6} ${x-4},${y+3} ${x+4},${y+3}" fill="${col}" opacity="0.85"><title>↑ Price crossed above ${label}</title></polygon>`;
    }
    function triDn(cx, cy, col, label) {
      const [x, y] = [+cx, +cy];
      return `<polygon points="${x},${y+6} ${x-4},${y-3} ${x+4},${y-3}" fill="${col}" opacity="0.85"><title>↓ Price crossed below ${label}</title></polygon>`;
    }

    // Crossovers: scan full-data arrays, only emit markers within view window
    // m.vals is already the view slice; use full sma for prev-day continuity at viewStart
    const crossMarkers = activeData.filter(m => m.n >= 50).flatMap(m => {
      const fullVals = sma(m.n);  // full N-length for prev-day comparison at boundary
      const out = [];
      for (let vi = 0; vi < vN; vi++) {
        const fi = viewStart + vi;          // full-array index
        const mv = fullVals[fi], pv = fullVals[fi - 1];
        if (mv === null || pv === null || fi < 1) continue;
        const cx = xOf(vi).toFixed(1);
        const cy = yOf(vc[vi]).toFixed(1);
        if (closes[fi-1] <= pv && closes[fi] > mv) out.push(triUp(cx, (+cy + 7).toFixed(1), '#10b981', m.label));
        else if (closes[fi-1] >= pv && closes[fi] < mv) out.push(triDn(cx, (+cy - 7).toFixed(1), '#ef4444', m.label));
      }
      return out;
    }).join('');

    const hasMarkers = crossMarkers.length > 0;
    const crossLegend = hasMarkers ? `
      <span class="sc-mom-legend-item"><svg width="9" height="9" viewBox="0 0 9 9"><polygon points="4.5,0 0,9 9,9" fill="#10b981"/></svg> Cross above</span>
      <span class="sc-mom-legend-item"><svg width="9" height="9" viewBox="0 0 9 9"><polygon points="4.5,9 0,0 9,0" fill="#ef4444"/></svg> Cross below</span>` : '';

    const activeLegend = activeData.map(m =>
      `<span class="sc-mom-legend-item"><span class="sc-mom-legend-dot" style="background:${m.col}"></span>${m.label}</span>`
    ).join('');

    return `
    <div class="sc-ma-toggles">${toggleBtns}</div>
    <div class="sc-mom-stats">${stats}${crossBadge}</div>
    <div class="sc-mom-legend">${activeLegend}<span class="sc-mom-legend-item"><span class="sc-mom-legend-dot" style="background:var(--text);opacity:0.4"></span>Price</span>${crossLegend}</div>
    <svg class="sc-price-svg" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">
      <defs><clipPath id="maClip"><rect x="${PL}" y="${PT-8}" width="${W-PL-PR}" height="${plotH+16}"/></clipPath></defs>
      <path d="${pricePath}" fill="none" stroke="var(--text)" stroke-width="1.2" stroke-linejoin="round" opacity="0.35"/>
      ${maLines}
      <g clip-path="url(#maClip)">${crossMarkers}</g>
      ${yTks}${xAxis(H)}
    </svg>`;
  }

  function setupMAToggles() {
    body.querySelectorAll('.sc-ma-toggle').forEach(btn => {
      btn.addEventListener('click', () => {
        const n = +btn.dataset.ma;
        if (activeMAns.has(n)) activeMAns.delete(n);
        else activeMAns.add(n);
        showTab('ma');
      });
    });
  }

  // ── Tab: RSI ────────────────────────────────────────────────────────
  function renderRSI() {
    const fullRv = rsiVals(14);                  // always full N-length
    const rv   = fullRv.slice(viewStart);        // view window
    const last = rv[rv.length - 1];
    const H = 150;
    const plotH = H - PT - PB;
    const yOf = v => PT + plotH - (v / 100) * plotH;
    const y30 = yOf(30), y50 = yOf(50), y70 = yOf(70);

    const col = last > 70 ? '#ef4444' : last < 30 ? '#10b981' : '#818cf8';
    const zone = last > 70 ? 'Overbought' : last < 30 ? 'Oversold' : last > 55 ? 'Bullish' : last < 45 ? 'Bearish' : 'Neutral';
    const zoneCls = last > 70 ? 'mom-overbought' : last < 30 ? 'mom-oversold' : last > 50 ? 'mom-bull' : 'mom-bear';

    const yTks = [20, 30, 50, 70, 80].map(v =>
      `<text x="${PL - 5}" y="${yOf(v).toFixed(1)}" text-anchor="end" dominant-baseline="middle" font-size="9" fill="#9ca3af">${v}</text>`
    ).join('');

    return `
    <div class="sc-mom-stats">
      <div class="sc-mom-stat">
        <span class="sc-mom-stat-lbl">RSI (14)</span>
        <span class="sc-mom-stat-val" style="color:${col}">${last != null ? last.toFixed(1) : '—'}</span>
        <span class="sc-mom-badge ${zoneCls}">${zone}</span>
      </div>
      <div class="sc-mom-stat sc-mom-hint-row">
        <span class="sc-mom-hint">Below 30 = oversold entry zone · Above 70 = overbought, consider waiting</span>
      </div>
    </div>
    <svg class="sc-price-svg" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">
      <rect x="${PL}" y="${yOf(70).toFixed(1)}" width="${W-PL-PR}" height="${(yOf(30)-yOf(70)).toFixed(1)}" fill="#374151" opacity="0.1"/>
      <line x1="${PL}" y1="${y70.toFixed(1)}" x2="${W-PR}" y2="${y70.toFixed(1)}" stroke="#ef4444" stroke-width="0.8" stroke-dasharray="4,3" opacity="0.45"/>
      <line x1="${PL}" y1="${y50.toFixed(1)}" x2="${W-PR}" y2="${y50.toFixed(1)}" stroke="#9ca3af" stroke-width="0.6" stroke-dasharray="2,4" opacity="0.3"/>
      <line x1="${PL}" y1="${y30.toFixed(1)}" x2="${W-PR}" y2="${y30.toFixed(1)}" stroke="#10b981" stroke-width="0.8" stroke-dasharray="4,3" opacity="0.45"/>
      <path d="${seriesPath(rv, yOf)}" fill="none" stroke="${col}" stroke-width="1.6" stroke-linejoin="round"/>
      ${yTks}${xAxis(H)}
    </svg>`;
  }

  // ── Tab: MACD ───────────────────────────────────────────────────────
  function renderMACD() {
    const e12 = ema(12), e26 = ema(26);
    // compute on full array, then slice to view
    const fullMacd = closes.map((_, i) =>
      e12[i] !== null && e26[i] !== null ? e12[i] - e26[i] : null
    );
    const fullSignal = emaOf(fullMacd, 9);
    const fullHist   = fullMacd.map((v, i) =>
      v !== null && fullSignal[i] !== null ? v - fullSignal[i] : null
    );
    const macdLine = fullMacd.slice(viewStart);
    const signal   = fullSignal.slice(viewStart);
    const hist     = fullHist.slice(viewStart);

    const lastM = macdLine[vN - 1], lastS = signal[vN - 1], lastH = hist[vN - 1];
    const H = 170;
    const plotH = H - PT - PB;
    const plotW = W - PL - PR;

    const allV = [...macdLine, ...signal, ...hist].filter(v => v !== null);
    if (allV.length < 2) return '<p class="sc-mom-empty">Not enough data for MACD.</p>';
    const lo = Math.min(...allV), hi = Math.max(...allV);
    const pad = (hi - lo) * 0.1 || 0.01;
    const yLo = lo - pad, yHi = hi + pad;
    const yOf = v => PT + plotH - ((v - yLo) / (yHi - yLo)) * plotH;
    const y0 = yOf(0);

    const barW = Math.max(1, plotW / vN - 0.3);
    const histBars = hist.map((v, i) => {
      if (v === null) return '';
      const y1 = v >= 0 ? yOf(v) : y0;
      const bh = Math.max(1, Math.abs(yOf(v) - y0));
      return `<rect x="${(xOf(i) - barW / 2).toFixed(1)}" y="${y1.toFixed(1)}" width="${barW.toFixed(1)}" height="${bh.toFixed(1)}" fill="${v >= 0 ? 'rgba(16,185,129,0.5)' : 'rgba(239,68,68,0.5)'}"/>`;
    }).join('');

    const yTks = [yLo, 0, yHi].map(v =>
      `<text x="${PL-5}" y="${yOf(v).toFixed(1)}" text-anchor="end" dominant-baseline="middle" font-size="9" fill="#9ca3af">${v.toFixed(2)}</text>`
    ).join('');

    const bullish = lastM > lastS;
    const crossover = lastH !== null && hist[vN - 2] !== null && Math.sign(lastH) !== Math.sign(hist[vN - 2]);

    return `
    <div class="sc-mom-stats">
      <div class="sc-mom-stat">
        <span class="sc-mom-legend-dot" style="background:#818cf8"></span>
        <span class="sc-mom-stat-lbl">MACD</span>
        <span class="sc-mom-stat-val">${lastM != null ? lastM.toFixed(3) : '—'}</span>
      </div>
      <div class="sc-mom-stat">
        <span class="sc-mom-legend-dot" style="background:#f59e0b"></span>
        <span class="sc-mom-stat-lbl">Signal</span>
        <span class="sc-mom-stat-val">${lastS != null ? lastS.toFixed(3) : '—'}</span>
      </div>
      <div class="sc-mom-stat">
        <span class="sc-mom-stat-lbl">Histogram</span>
        <span class="sc-mom-stat-val ${lastH != null ? (lastH >= 0 ? 'mom-up' : 'mom-dn') : ''}">${lastH != null ? lastH.toFixed(3) : '—'}</span>
      </div>
      <span class="sc-mom-badge ${bullish ? 'mom-bull' : 'mom-bear'}">${bullish ? 'Bullish' : 'Bearish'}${crossover ? ' · Crossover!' : ''}</span>
    </div>
    <div class="sc-mom-legend">
      <span class="sc-mom-legend-item"><span class="sc-mom-legend-dot" style="background:#818cf8"></span>MACD (12,26)</span>
      <span class="sc-mom-legend-item"><span class="sc-mom-legend-dot" style="background:#f59e0b"></span>Signal (9)</span>
      <span class="sc-mom-legend-item"><span class="sc-mom-legend-dot" style="background:#10b981"></span>Histogram</span>
    </div>
    <svg class="sc-price-svg" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">
      <line x1="${PL}" y1="${y0.toFixed(1)}" x2="${W-PR}" y2="${y0.toFixed(1)}" stroke="#9ca3af" stroke-width="0.6" stroke-dasharray="3,3" opacity="0.4"/>
      ${histBars}
      <path d="${seriesPath(macdLine, yOf)}" fill="none" stroke="#818cf8" stroke-width="1.5" stroke-linejoin="round"/>
      <path d="${seriesPath(signal, yOf)}" fill="none" stroke="#f59e0b" stroke-width="1.2" stroke-dasharray="5,2" stroke-linejoin="round"/>
      ${yTks}${xAxis(H)}
    </svg>`;
  }

  // ── Tab: Bollinger Bands ────────────────────────────────────────────
  function renderBB() {
    const { mid: fullMid, upper: fullUpper, lower: fullLower } = bollingerBands(20, 2);
    // slice to view window
    const vc    = closes.slice(viewStart);
    const mid   = fullMid.slice(viewStart);
    const upper = fullUpper.slice(viewStart);
    const lower = fullLower.slice(viewStart);

    const last = vc[vN - 1];
    const lMid = mid[vN-1], lUp = upper[vN-1], lLo = lower[vN-1];
    const pctB = lMid && lUp && lLo ? (last - lLo) / (lUp - lLo) * 100 : null;
    const bw   = lUp && lLo && lMid  ? (lUp - lLo) / lMid * 100 : null;
    const H = 170;
    const plotH = H - PT - PB;

    const allV = [...vc, ...upper, ...lower].filter(v => v !== null);
    const lo = Math.min(...allV), hi = Math.max(...allV);
    const pad = (hi - lo) * 0.04 || 1;
    const yOf = v => PT + plotH - ((v - (lo - pad)) / ((hi + pad) - (lo - pad))) * plotH;

    // Band fill area
    const upPts = upper.map((v, i) => v !== null ? `${xOf(i).toFixed(1)},${yOf(v).toFixed(1)}` : null).filter(Boolean);
    const loPts = lower.map((v, i) => v !== null ? `${xOf(i).toFixed(1)},${yOf(v).toFixed(1)}` : null).filter(Boolean);
    const bandFill = upPts.length > 1
      ? `<path d="M ${upPts.join(' L ')} L ${[...loPts].reverse().join(' L ')} Z" fill="#818cf8" opacity="0.07"/>`
      : '';

    const yTks = [lo, (lo+hi)/2, hi].map(v => {
      const lbl = v >= 1000 ? `${currSym}${(v/1000).toFixed(1)}k` : `${currSym}${v.toFixed(0)}`;
      return `<text x="${PL-5}" y="${yOf(v).toFixed(1)}" text-anchor="end" dominant-baseline="middle" font-size="9.5" fill="#9ca3af">${lbl}</text>`;
    }).join('');

    const pctBCls = pctB > 80 ? 'mom-overbought' : pctB < 20 ? 'mom-oversold' : 'mom-neutral';
    const pctBLbl = pctB > 80 ? 'Near upper band' : pctB < 20 ? 'Near lower band' : 'Mid range';

    return `
    <div class="sc-mom-stats">
      <div class="sc-mom-stat"><span class="sc-mom-stat-lbl">Upper Band</span><span class="sc-mom-stat-val">${currSym}${lUp?.toFixed(2) ?? '—'}</span></div>
      <div class="sc-mom-stat"><span class="sc-mom-stat-lbl">Middle (SMA 20)</span><span class="sc-mom-stat-val">${currSym}${lMid?.toFixed(2) ?? '—'}</span></div>
      <div class="sc-mom-stat"><span class="sc-mom-stat-lbl">Lower Band</span><span class="sc-mom-stat-val">${currSym}${lLo?.toFixed(2) ?? '—'}</span></div>
      <div class="sc-mom-stat">
        <span class="sc-mom-stat-lbl">%B</span>
        <span class="sc-mom-stat-val">${pctB != null ? pctB.toFixed(1)+'%' : '—'}</span>
        ${pctB != null ? `<span class="sc-mom-badge ${pctBCls}">${pctBLbl}</span>` : ''}
      </div>
      <div class="sc-mom-stat"><span class="sc-mom-stat-lbl">Band Width</span><span class="sc-mom-stat-val">${bw != null ? bw.toFixed(1)+'%' : '—'}</span></div>
    </div>
    <div class="sc-mom-legend">
      <span class="sc-mom-legend-item"><span class="sc-mom-legend-dot" style="background:var(--text);opacity:0.7"></span>Price</span>
      <span class="sc-mom-legend-item"><span class="sc-mom-legend-dot" style="background:#818cf8"></span>Bands (20, ±2σ)</span>
      <span class="sc-mom-legend-item"><span class="sc-mom-legend-dot" style="background:#818cf8;opacity:0.4"></span>Middle (SMA20)</span>
    </div>
    <svg class="sc-price-svg" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">
      ${bandFill}
      <path d="${seriesPath(upper, yOf)}" fill="none" stroke="#818cf8" stroke-width="1" stroke-dasharray="4,2" opacity="0.75"/>
      <path d="${seriesPath(lower, yOf)}" fill="none" stroke="#818cf8" stroke-width="1" stroke-dasharray="4,2" opacity="0.75"/>
      <path d="${seriesPath(mid, yOf)}" fill="none" stroke="#818cf8" stroke-width="0.9" opacity="0.45"/>
      <path d="${vc.map((c, i) => `${i === 0 ? 'M' : 'L'} ${xOf(i).toFixed(1)},${yOf(c).toFixed(1)}`).join(' ')}" fill="none" stroke="var(--text)" stroke-width="1.5" stroke-linejoin="round" opacity="0.85"/>
      ${yTks}${xAxis(H)}
    </svg>`;
  }

  // ── Tab: Rate of Change (momentum oscillator around 0) ──────────────
  function renderROC() {
    const n = 12;
    const full = closes.map((_, i) => (i >= n && closes[i - n]) ? (closes[i] / closes[i - n] - 1) * 100 : null);
    const rv = full.slice(viewStart);
    const last = rv[vN - 1];
    const H = 150, plotH = H - PT - PB;
    const vals = rv.filter(v => v !== null);
    if (vals.length < 2) return '<p class="sc-mom-empty">Not enough data for ROC.</p>';
    const mag = Math.max(Math.abs(Math.min(...vals)), Math.abs(Math.max(...vals))) * 1.1 || 1;
    const yOf = v => PT + plotH / 2 - (v / mag) * (plotH / 2);
    const y0 = yOf(0);
    const col = last >= 0 ? '#10b981' : '#ef4444';
    const zone = last >= 8 ? 'Strong up-momentum' : last >= 0 ? 'Up-momentum' : last <= -8 ? 'Strong down-momentum' : 'Down-momentum';
    const zoneCls = last >= 0 ? 'mom-bull' : 'mom-bear';
    const yTks = [mag, 0, -mag].map(v =>
      `<text x="${PL - 5}" y="${yOf(v).toFixed(1)}" text-anchor="end" dominant-baseline="middle" font-size="9" fill="#9ca3af">${v > 0 ? '+' : ''}${v.toFixed(0)}%</text>`).join('');
    return `
    <div class="sc-mom-stats">
      <div class="sc-mom-stat"><span class="sc-mom-stat-lbl">ROC (12-day)</span>
        <span class="sc-mom-stat-val" style="color:${col}">${last != null ? (last >= 0 ? '+' : '') + last.toFixed(1) + '%' : '—'}</span>
        <span class="sc-mom-badge ${zoneCls}">${zone}</span></div>
      <div class="sc-mom-stat sc-mom-hint-row"><span class="sc-mom-hint">Rate of Change = % price move over the last 12 days — raw momentum. Above 0 = rising; a cross through 0 flags a momentum shift. Rising ROC while price rises = healthy trend; falling ROC into new price highs = bearish divergence.</span></div>
    </div>
    <svg class="sc-price-svg" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">
      <line x1="${PL}" y1="${y0.toFixed(1)}" x2="${W - PR}" y2="${y0.toFixed(1)}" stroke="#9ca3af" stroke-width="0.8" stroke-dasharray="3,3" opacity="0.5"/>
      <path d="${seriesPath(rv, yOf)}" fill="none" stroke="${col}" stroke-width="1.6" stroke-linejoin="round"/>
      ${yTks}${xAxis(H)}
    </svg>`;
  }

  // ── Tab: Stochastic Oscillator (%K / %D, close-range) ───────────────
  function renderStoch() {
    const n = 14, dLen = 3;
    const k = new Array(N).fill(null);
    for (let i = n - 1; i < N; i++) {
      let lo = Infinity, hi = -Infinity;
      for (let j = i - n + 1; j <= i; j++) { if (closes[j] < lo) lo = closes[j]; if (closes[j] > hi) hi = closes[j]; }
      k[i] = hi === lo ? 50 : (closes[i] - lo) / (hi - lo) * 100;
    }
    const d = k.map((_, i) => {
      if (i < n - 1 + dLen - 1) return null;
      let s = 0; for (let j = i - dLen + 1; j <= i; j++) s += k[j];
      return s / dLen;
    });
    const kv = k.slice(viewStart), dv = d.slice(viewStart);
    const lastK = kv[vN - 1], lastD = dv[vN - 1];
    const H = 150, plotH = H - PT - PB;
    const yOf = v => PT + plotH - (v / 100) * plotH;
    const col = lastK > 80 ? '#ef4444' : lastK < 20 ? '#10b981' : '#818cf8';
    const zone = lastK > 80 ? 'Overbought' : lastK < 20 ? 'Oversold' : 'Neutral';
    const zoneCls = lastK > 80 ? 'mom-overbought' : lastK < 20 ? 'mom-oversold' : 'mom-bull';
    const yTks = [20, 50, 80].map(v =>
      `<text x="${PL - 5}" y="${yOf(v).toFixed(1)}" text-anchor="end" dominant-baseline="middle" font-size="9" fill="#9ca3af">${v}</text>`).join('');
    return `
    <div class="sc-mom-stats">
      <div class="sc-mom-stat"><span class="sc-mom-legend-dot" style="background:#818cf8"></span><span class="sc-mom-stat-lbl">%K</span>
        <span class="sc-mom-stat-val" style="color:${col}">${lastK != null ? lastK.toFixed(1) : '—'}</span>
        <span class="sc-mom-badge ${zoneCls}">${zone}</span></div>
      <div class="sc-mom-stat"><span class="sc-mom-legend-dot" style="background:#f59e0b"></span><span class="sc-mom-stat-lbl">%D (3)</span>
        <span class="sc-mom-stat-val">${lastD != null ? lastD.toFixed(1) : '—'}</span></div>
      <div class="sc-mom-stat sc-mom-hint-row"><span class="sc-mom-hint">Where price sits in its 14-day range. &gt;80 = overbought, &lt;20 = oversold (faster/twitchier than RSI). A %K crossing above %D from oversold is a classic buy trigger.</span></div>
    </div>
    <svg class="sc-price-svg" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">
      <rect x="${PL}" y="${yOf(80).toFixed(1)}" width="${W - PL - PR}" height="${(yOf(20) - yOf(80)).toFixed(1)}" fill="#374151" opacity="0.08"/>
      <line x1="${PL}" y1="${yOf(80).toFixed(1)}" x2="${W - PR}" y2="${yOf(80).toFixed(1)}" stroke="#ef4444" stroke-width="0.8" stroke-dasharray="4,3" opacity="0.45"/>
      <line x1="${PL}" y1="${yOf(20).toFixed(1)}" x2="${W - PR}" y2="${yOf(20).toFixed(1)}" stroke="#10b981" stroke-width="0.8" stroke-dasharray="4,3" opacity="0.45"/>
      <path d="${seriesPath(kv, yOf)}" fill="none" stroke="#818cf8" stroke-width="1.5" stroke-linejoin="round"/>
      <path d="${seriesPath(dv, yOf)}" fill="none" stroke="#f59e0b" stroke-width="1.1" stroke-dasharray="5,2" stroke-linejoin="round"/>
      ${yTks}${xAxis(H)}
    </svg>`;
  }

  // ── Tab: Aroon (trend strength & direction, close-based) ────────────
  function renderAroon() {
    const n = 25;
    const up = new Array(N).fill(null), dn = new Array(N).fill(null);
    for (let i = n; i < N; i++) {
      let hiIdx = i - n, loIdx = i - n;
      for (let j = i - n; j <= i; j++) { if (closes[j] >= closes[hiIdx]) hiIdx = j; if (closes[j] <= closes[loIdx]) loIdx = j; }
      up[i] = ((n - (i - hiIdx)) / n) * 100;
      dn[i] = ((n - (i - loIdx)) / n) * 100;
    }
    const uv = up.slice(viewStart), dv = dn.slice(viewStart);
    const lastU = uv[vN - 1], lastD = dv[vN - 1];
    const H = 150, plotH = H - PT - PB;
    const yOf = v => PT + plotH - (v / 100) * plotH;
    let zone, zoneCls;
    if (lastU > 70 && lastD < 30) { zone = 'Strong uptrend'; zoneCls = 'mom-bull'; }
    else if (lastD > 70 && lastU < 30) { zone = 'Strong downtrend'; zoneCls = 'mom-bear'; }
    else if (lastU < 50 && lastD < 50) { zone = 'Consolidating / weak trend'; zoneCls = 'mom-bear'; }
    else { zone = lastU >= lastD ? 'Up-biased' : 'Down-biased'; zoneCls = lastU >= lastD ? 'mom-bull' : 'mom-bear'; }
    const yTks = [0, 50, 100].map(v =>
      `<text x="${PL - 5}" y="${yOf(v).toFixed(1)}" text-anchor="end" dominant-baseline="middle" font-size="9" fill="#9ca3af">${v}</text>`).join('');
    return `
    <div class="sc-mom-stats">
      <div class="sc-mom-stat"><span class="sc-mom-legend-dot" style="background:#10b981"></span><span class="sc-mom-stat-lbl">Aroon Up</span>
        <span class="sc-mom-stat-val" style="color:#10b981">${lastU != null ? lastU.toFixed(0) : '—'}</span></div>
      <div class="sc-mom-stat"><span class="sc-mom-legend-dot" style="background:#ef4444"></span><span class="sc-mom-stat-lbl">Aroon Down</span>
        <span class="sc-mom-stat-val" style="color:#ef4444">${lastD != null ? lastD.toFixed(0) : '—'}</span>
        <span class="sc-mom-badge ${zoneCls}">${zone}</span></div>
      <div class="sc-mom-stat sc-mom-hint-row"><span class="sc-mom-hint">Trend-strength gauge (25-day): how recently new highs vs. new lows were made. Up near 100 &amp; Down low = strong uptrend; the reverse = downtrend; both under 50 = no trend (chop). Crossovers flag trend changes — the close-based stand-in for ADX.</span></div>
    </div>
    <svg class="sc-price-svg" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">
      <line x1="${PL}" y1="${yOf(70).toFixed(1)}" x2="${W - PR}" y2="${yOf(70).toFixed(1)}" stroke="#9ca3af" stroke-width="0.6" stroke-dasharray="2,4" opacity="0.35"/>
      <line x1="${PL}" y1="${yOf(30).toFixed(1)}" x2="${W - PR}" y2="${yOf(30).toFixed(1)}" stroke="#9ca3af" stroke-width="0.6" stroke-dasharray="2,4" opacity="0.35"/>
      <path d="${seriesPath(uv, yOf)}" fill="none" stroke="#10b981" stroke-width="1.5" stroke-linejoin="round"/>
      <path d="${seriesPath(dv, yOf)}" fill="none" stroke="#ef4444" stroke-width="1.5" stroke-linejoin="round"/>
      ${yTks}${xAxis(H)}
    </svg>`;
  }

  // ── Wire up tabs & periods ──────────────────────────────────────────
  const renderers = { ma: renderMA, rsi: renderRSI, macd: renderMACD, bb: renderBB, roc: renderROC, stoch: renderStoch, aroon: renderAroon };
  let activeTab = 'ma';

  function showTab(key) {
    activeTab = key;
    body.innerHTML = renderers[key]?.() ?? '<p class="sc-mom-empty">Not enough data for this period.</p>';
    if (key === 'ma') setupMAToggles();
  }

  tabs.querySelectorAll('.sc-mom-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      tabs.querySelectorAll('.sc-mom-tab').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      showTab(btn.dataset.mom);
    });
  });

  if (periodBar) {
    periodBar.querySelectorAll('.sc-mom-period-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        periodBar.querySelectorAll('.sc-mom-period-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        setView(btn.dataset.period);
        showTab(activeTab);
      });
    });
  }

  showTab('ma');
}

// ── Deploy view (folded into Macro as a third sub-tab; see loadMacroView) ────

function renderDeployment(d, canvas) {
  const overall   = d.overall   || {};
  const val       = d.valuation || {};
  const vix       = d.vix_signal || {};
  const season    = d.season    || {};
  const layers    = d.layers    || [];

  // ── colour helpers
  const colorClass = c => ({ positive:"dp-green", warning:"dp-amber", caution:"dp-yellow",
    danger:"dp-red", neutral:"dp-neutral" }[c] || "dp-neutral");

  // ── Signal Hero ─────────────────────────────────────────────────────────
  const heroColor = colorClass(overall.color);
  const heroHtml = `
  <section class="dp-hero ${heroColor}">
    <div class="dp-hero-label">Cash Deployment Posture</div>
    <div class="dp-hero-headline">${escapeHtml(overall.headline || "—")}</div>
    <div class="dp-hero-badge">${escapeHtml(overall.label || "—")}</div>
    <div class="dp-hero-asof">Data as of ${escapeHtml(d.as_of || "—")}</div>
  </section>`;

  // ── 3 Gauges ─────────────────────────────────────────────────────────────
  // Gauge 1: Reserve Size (Valuation)
  const valColor = colorClass(val.color);
  const erp = val.erp != null ? `${val.erp > 0 ? "+" : ""}${val.erp.toFixed(2)}%` : "—";
  const gauge1 = `
  <div class="dp-gauge ${valColor}">
    <div class="dp-gauge-title">Reserve Size Target</div>
    <div class="dp-gauge-big">${val.reserve_min ?? "—"}–${val.reserve_max ?? "—"}<span class="dp-gauge-unit">% of NW</span></div>
    <div class="dp-gauge-sub">${escapeHtml(val.label || "—")}</div>
    <div class="dp-gauge-signals">
      <div class="dp-sig-row"><span class="dp-sig-key">CAPE</span><span class="dp-sig-val">${val.cape != null ? val.cape.toFixed(1) : "—"}</span></div>
      <div class="dp-sig-row"><span class="dp-sig-key">Fwd P/E</span><span class="dp-sig-val">${val.fwd_pe != null ? val.fwd_pe.toFixed(1) : "—"}</span></div>
      <div class="dp-sig-row"><span class="dp-sig-key">ERP</span><span class="dp-sig-val">${erp}</span></div>
    </div>
  </div>`;

  // Gauge 2: VIX Signal
  const vixColor = colorClass(vix.color);
  const nt = vix.next_trigger;
  const nextTrigHtml = nt
    ? `<div class="dp-gauge-next">Next trigger: VIX <strong>${nt.vix}</strong> (${nt.distance > 0 ? "+" : ""}${nt.distance} pts) → deploy ${nt.deploy_pct}%</div>`
    : `<div class="dp-gauge-next">Already at highest trigger tier</div>`;
  const barPct = vix.deploy_pct != null ? Math.round(vix.deploy_pct / 100 * 100) : 0;
  const gauge2 = `
  <div class="dp-gauge ${vixColor}">
    <div class="dp-gauge-title">VIX Trigger Signal</div>
    <div class="dp-gauge-big">${vix.value != null ? vix.value.toFixed(1) : "—"}<span class="dp-gauge-unit">VIX</span></div>
    <div class="dp-gauge-sub">${escapeHtml(vix.label || "—")}</div>
    <div class="dp-vix-bar-wrap">
      <div class="dp-vix-bar-track"><div class="dp-vix-bar-fill" style="width:${barPct}%"></div></div>
      <span class="dp-vix-bar-label">${barPct}% of reserve</span>
    </div>
    ${nextTrigHtml}
    <div class="dp-gauge-action">${escapeHtml(vix.action || "—")}</div>
  </div>`;

  // Gauge 3: Economic Season
  const seasonColor = colorClass(season.color);
  const favor = (season.favor || []).slice(0, 3).map(f => `<li>${escapeHtml(f)}</li>`).join("");
  const avoid = (season.avoid || []).slice(0, 3).map(a => `<li>${escapeHtml(a)}</li>`).join("");
  const gauge3 = `
  <div class="dp-gauge ${seasonColor}">
    <div class="dp-gauge-title">Economic Season</div>
    <div class="dp-gauge-big">${escapeHtml(season.label || "—")}</div>
    <div class="dp-gauge-sub">${escapeHtml(season.description || "")}</div>
    <div class="dp-season-lists">
      <div class="dp-season-col dp-season-favor">
        <div class="dp-season-head">✓ Favour</div>
        <ul>${favor || "<li>—</li>"}</ul>
      </div>
      <div class="dp-season-col dp-season-avoid">
        <div class="dp-season-head">✗ Avoid</div>
        <ul>${avoid || "<li>—</li>"}</ul>
      </div>
    </div>
  </div>`;

  const gaugesHtml = `<div class="dp-gauges">${gauge1}${gauge2}${gauge3}</div>`;

  // ── Personal Calculator ──────────────────────────────────────────────────
  const savedPortfolio = localStorage.getItem("dp_portfolio") || "";
  const savedCash = localStorage.getItem("dp_cash") || "";
  const calcHtml = `
  <section class="dp-calc">
    <div class="dp-calc-title">Personal Reserve Calculator</div>
    <div class="dp-calc-row">
      <label class="dp-calc-label">
        Total Portfolio Value ($)
        <input id="dpPortfolio" class="dp-calc-input" type="number" min="0" step="1000"
               placeholder="e.g. 700000" value="${escapeHtml(savedPortfolio)}">
      </label>
      <label class="dp-calc-label">
        Current Cash / T-Bills ($)
        <input id="dpCash" class="dp-calc-input" type="number" min="0" step="1000"
               placeholder="e.g. 110000" value="${escapeHtml(savedCash)}">
      </label>
      <button class="dp-calc-btn" id="dpCalcBtn">Calculate</button>
    </div>
    <div id="dpCalcResult" class="dp-calc-result"></div>
  </section>`;

  // ── 5-Layer Strip ────────────────────────────────────────────────────────
  const layerHtml = layers.map(layer => {
    const lc = colorClass(layer.color);
    return `
    <div class="dp-layer ${lc}">
      <div class="dp-layer-num">${layer.num}</div>
      <div class="dp-layer-body">
        <div class="dp-layer-header">
          <span class="dp-layer-title">${escapeHtml(layer.title)}</span>
          <span class="dp-layer-status-badge dp-layer-${escapeHtml(layer.status)}">${escapeHtml(layer.status_label)}</span>
        </div>
        <div class="dp-layer-detail">${escapeHtml(layer.detail)}</div>
        <div class="dp-layer-action">→ ${escapeHtml(layer.action)}</div>
      </div>
    </div>`;
  }).join("");

  const layersHtml = `
  <section class="dp-layers">
    <div class="dp-section-title">5-Layer Deployment System</div>
    ${layerHtml}
  </section>`;

  canvas.innerHTML = heroHtml + gaugesHtml + calcHtml + layersHtml;

  // ── Calculator logic ─────────────────────────────────────────────────────
  function runCalc() {
    const portfolio = parseFloat(document.getElementById("dpPortfolio").value) || 0;
    const cash      = parseFloat(document.getElementById("dpCash").value) || 0;
    localStorage.setItem("dp_portfolio", document.getElementById("dpPortfolio").value);
    localStorage.setItem("dp_cash", document.getElementById("dpCash").value);

    if (!portfolio || !cash) {
      document.getElementById("dpCalcResult").innerHTML = "";
      return;
    }
    const cashPct    = cash / portfolio * 100;
    const targetMin  = val.reserve_min || 8;
    const targetMax  = val.reserve_max || 10;
    const targetMid  = (targetMin + targetMax) / 2;
    const idealCash  = portfolio * targetMid / 100;
    const excess     = cash - portfolio * targetMax / 100;
    const shortage   = portfolio * targetMin / 100 - cash;

    let statusHtml;
    if (excess > 0) {
      statusHtml = `<span class="dp-calc-amber">⚠ ${cashPct.toFixed(1)}% cash — ${fmt(Math.round(excess))} above the ${targetMax}% ceiling. Consider deploying the excess.</span>`;
    } else if (shortage > 0) {
      statusHtml = `<span class="dp-calc-green">✓ ${cashPct.toFixed(1)}% cash — ${fmt(Math.round(shortage))} below the ${targetMin}% floor. Reserve is thin; rebuild before next drawdown.</span>`;
    } else {
      statusHtml = `<span class="dp-calc-green">✓ ${cashPct.toFixed(1)}% cash — within target band (${targetMin}–${targetMax}%). No action needed.</span>`;
    }

    document.getElementById("dpCalcResult").innerHTML = `
      <div class="dp-calc-grid">
        <div class="dp-calc-stat"><span class="dp-calc-stat-label">Cash %</span><strong>${cashPct.toFixed(1)}%</strong></div>
        <div class="dp-calc-stat"><span class="dp-calc-stat-label">Target Band</span><strong>${targetMin}–${targetMax}%</strong></div>
        <div class="dp-calc-stat"><span class="dp-calc-stat-label">Ideal Cash</span><strong>$${fmt(Math.round(idealCash))}</strong></div>
        <div class="dp-calc-stat"><span class="dp-calc-stat-label">${excess > 0 ? "Excess" : "Shortfall"}</span><strong>$${fmt(Math.round(Math.abs(excess > 0 ? excess : shortage)))}</strong></div>
      </div>
      <div class="dp-calc-status">${statusHtml}</div>`;
  }

  document.getElementById("dpCalcBtn").addEventListener("click", runCalc);
  // Auto-run if values already saved
  if (savedPortfolio && savedCash) runCalc();
}

// When leaving the deploy page, hide the canvas and show the workspace again
// ── Summary ────────────────────────────────────────────────────────────────

function renderSummary(summary) {
  state.summary = summary;
  els.marketCount.textContent = fmt(summary.markets);
  els.snapshotCount.textContent = fmt(summary.snapshots);
  els.lastUpdate.textContent = fmtDate(summary.last_seen_at);
  els.sourceCount.textContent = summary.sources.map((s) => s.source).join(", ") || "—";

  const current = els.categorySelect.value;
  els.categorySelect.innerHTML = '<option value="">All investment categories</option>';
  for (const item of summary.categories) {
    const opt = document.createElement("option");
    opt.value = item.category;
    opt.textContent = `${item.category} (${item.count})`;
    els.categorySelect.appendChild(opt);
  }
  els.categorySelect.value = current;
}

// ── Radar view (grouped) ───────────────────────────────────────────────────

function groupByEvent(items) {
  const groups = new Map();
  for (const item of items) {
    const key = item.event_title || item.title;
    if (!groups.has(key)) {
      groups.set(key, {
        eventTitle: key,
        url: item.url,
        category: item.category,
        source: item.source,
        note: item.note,
        items: [],
      });
    }
    groups.get(key).items.push(item);
  }
  return [...groups.values()];
}

function probBarHtml(value) {
  const cls = probClass(value);
  const pct = value != null ? Math.max(2, Number(value) * 100).toFixed(1) : 0;
  return `<div class="prob-bar-track"><div class="prob-bar-fill ${cls}" style="width:${pct}%"></div></div>`;
}

function renderEventGroup(group) {
  const card = document.createElement("article");
  card.className = "event-group";
  card.dataset.category = group.category || "uncategorized";

  const catCls = catPillClass(group.category);
  const urlPill = group.url
    ? `<a class="pill ext" href="${escapeHtml(group.url)}" target="_blank" rel="noreferrer" tabindex="0">↗ view</a>`
    : "";

  const multiMarket = group.items.length > 1;

  const headerHtml = `
    <div class="event-group-header">
      <h3 class="event-group-title">${escapeHtml(group.eventTitle)}</h3>
      ${group.note ? `<p class="event-group-note">${escapeHtml(group.note)}</p>` : ""}
      <div class="pill-row">
        <span class="pill source">${escapeHtml(group.source)}</span>
        <span class="pill ${catCls}">${escapeHtml(group.category || "uncategorized")}</span>
        ${urlPill}
      </div>
    </div>`;

  const colHeaderHtml = `
    <div class="market-col-header${multiMarket ? "" : " no-question"}">
      ${multiMarket ? `<span class="col-label">Market</span>` : ""}
      <span class="col-label">Probability</span>
      <span class="col-label right">Change</span>
      <span class="col-label right">Volume</span>
    </div>`;

  const PERSIST_ICONS = { fresh_spike: "⚡", persistent: "✓", reversing: "↩", active: "~", stable: "" };
  const PERSIST_LABELS = { fresh_spike: "fresh spike", persistent: "persistent", reversing: "reversing", active: "active", stable: "" };

  const rowsHtml = group.items.map((item) => {
    const cls = probClass(item.latest_probability);
    const chgCls = changeClass(item.probability_change);
    const chgText = fmtChange(item.probability_change) || "new";
    const showQuestion = multiMarket && item.title && item.title !== group.eventTitle;
    const persist = item.persistence_label || "stable";
    const persistBadge = `<span class="persist-badge ${persist}">${PERSIST_ICONS[persist] || ""} ${PERSIST_LABELS[persist] || ""}</span>`;

    return `<div class="event-market-row${showQuestion ? "" : " no-question"}"
                 data-source="${escapeHtml(item.source)}"
                 data-market-id="${escapeHtml(item.market_id)}"
                 tabindex="0" role="button" aria-label="${escapeHtml(item.title)}">
      ${showQuestion ? `<div class="event-market-question" title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</div>` : ""}
      <div class="event-market-prob">
        <span class="prob-value ${cls}">${fmtProb(item.latest_probability)}</span>
        ${probBarHtml(item.latest_probability)}
        ${persistBadge}
      </div>
      <div class="event-market-change ${chgCls}">${escapeHtml(chgText)}</div>
      <div class="event-market-volume">${fmt(item.volume)}</div>
    </div>`;
  }).join("");

  card.innerHTML = headerHtml + `<div class="event-markets">${colHeaderHtml}${rowsHtml}</div>`;
  return card;
}

function renderRadar(items) {
  els.tableEyebrow.textContent = "Probability moves";
  els.tableTitle.textContent = "Radar";
  const groups = groupByEvent(items);
  els.tableMeta.textContent = `${items.length} markets · ${groups.length} events`;
  els.content.innerHTML = "";
  if (!items.length) { renderEmpty("No radar items match these filters."); return; }
  for (const group of groups) els.content.appendChild(renderEventGroup(group));
}

// ── Divergence view ────────────────────────────────────────────────────────

function deltaClass(delta) {
  if (delta >= 0.10) return "large";
  if (delta >= 0.05) return "medium";
  return "small";
}

function renderDivergenceCard(pair) {
  const card = document.createElement("article");
  card.className = "divergence-card" + (pair.alert ? " divergence-alert" : "");
  card.dataset.category = pair.category || "uncategorized";

  const catCls = catPillClass(pair.category);
  const dPct = (pair.delta * 100).toFixed(1);
  const dCls = deltaClass(pair.delta);

  // Match type badge
  let matchLabel, matchTitle;
  if (pair.match_type === "exact") {
    matchLabel = "exact"; matchTitle = "Titles match exactly across platforms";
  } else if (pair.match_type === "normalised") {
    matchLabel = "~fuzzy"; matchTitle = "Titles match after normalising dates and stop-words";
  } else {
    const pct = Math.round((pair.match_score || 0) * 100);
    matchLabel = `~jaccard ${pct}%`; matchTitle = `Token overlap similarity: ${pct}%`;
  }
  const matchBadge = `<span class="match-badge" title="${matchTitle}">${matchLabel}</span>`;

  // Alert badge when ≥10pp gap
  const alertBadge = pair.alert
    ? `<span class="alert-badge" title="Gap ≥ 10pp — statistically large cross-platform divergence">🚨 Alert</span>`
    : "";

  // Heading = event title; subtitle = specific market outcome if different
  const heading = pair.event_title && pair.event_title !== pair.title
    ? pair.event_title : pair.title;
  const subtitle = pair.event_title && pair.event_title !== pair.title
    ? `<p class="divergence-subtitle">${escapeHtml(pair.title)}</p>` : "";

  card.innerHTML = `
    <div class="divergence-card-header">
      <h3 class="divergence-card-title">${escapeHtml(heading)}</h3>
      ${subtitle}
      <div class="pill-row">
        ${alertBadge}
        <span class="pill ${catCls}">${escapeHtml(pair.category || "uncategorized")}</span>
        ${matchBadge}
      </div>
    </div>
    <div class="divergence-body">
      <div class="divergence-side polymarket"
           data-source="polymarket" data-market-id="${escapeHtml(pair.poly_market_id)}"
           role="button" tabindex="0" aria-label="Polymarket: ${escapeHtml(pair.title)}">
        <div class="divergence-side-label">Polymarket</div>
        <div class="divergence-side-prob ${probClass(pair.poly_probability)}">${fmtProb(pair.poly_probability)}</div>
        <div class="divergence-side-source">${pair.poly_url ? `<a href="${escapeHtml(pair.poly_url)}" target="_blank" rel="noreferrer" onclick="event.stopPropagation()">↗ view</a>` : ""}</div>
      </div>
      <div class="divergence-middle">
        <div class="delta-badge ${dCls}">${dPct}pp</div>
        <div class="delta-label">gap</div>
        <div class="delta-label">${pair.direction === "kals_higher" ? "KA↑" : "PM↑"}</div>
      </div>
      <div class="divergence-side kalshi"
           data-source="kalshi" data-market-id="${escapeHtml(pair.kals_market_id)}"
           role="button" tabindex="0" aria-label="Kalshi: ${escapeHtml(pair.title)}">
        <div class="divergence-side-label">Kalshi</div>
        <div class="divergence-side-prob ${probClass(pair.kals_probability)}">${fmtProb(pair.kals_probability)}</div>
        <div class="divergence-side-source">${pair.kals_url ? `<a href="${escapeHtml(pair.kals_url)}" target="_blank" rel="noreferrer" onclick="event.stopPropagation()">↗ view</a>` : ""}</div>
      </div>
    </div>`;
  return card;
}

function renderDivergence(pairs) {
  els.tableEyebrow.textContent = "Cross-platform signal";
  els.tableTitle.textContent = "Divergence";
  const alerts = pairs.filter(p => p.alert).length;
  const alertNote = alerts ? ` · 🚨 ${alerts} alert${alerts > 1 ? "s" : ""} (≥10pp)` : "";
  els.tableMeta.textContent = `${pairs.length} pair${pairs.length !== 1 ? "s" : ""}${alertNote}`;
  els.content.innerHTML = "";
  if (!pairs.length) {
    renderEmpty("No divergences found. Try lowering Min divergence or refreshing live data.");
    return;
  }
  for (const pair of pairs) els.content.appendChild(renderDivergenceCard(pair));
}

// Event delegation for divergence side clicks
els.content.addEventListener("click", (e) => {
  const side = e.target.closest(".divergence-side[data-source]");
  if (side) openDetail(side.dataset.source, side.dataset.marketId);
});

els.content.addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  const side = e.target.closest(".divergence-side[data-source]");
  if (side) openDetail(side.dataset.source, side.dataset.marketId);
});

// ── FOMC Path view ─────────────────────────────────────────────────────────

function outcomeBar(label, prob, cls) {
  const pct = (prob * 100).toFixed(1);
  const width = Math.max(2, prob * 100).toFixed(1);
  return `
    <div class="outcome-row">
      <span class="outcome-label">${label}</span>
      <div class="outcome-bar-track"><div class="outcome-bar-fill ${cls}" style="width:${width}%"></div></div>
      <span class="outcome-pct ${cls}">${pct}%</span>
    </div>`;
}

function renderMeetingCard(m) {
  const card = document.createElement("div");
  card.className = "meeting-card";
  const cutTotal = (m.cut_25 + m.cut_50);
  const hikeTotal = (m.hike_25 + m.hike_50);
  card.innerHTML = `
    <h3 class="meeting-card-title">${escapeHtml(m.meeting)}</h3>
    ${outcomeBar("Hold", m.hold, "hold")}
    ${outcomeBar("Cut ≥25", cutTotal, "cut")}
    ${outcomeBar("Hike ≥25", hikeTotal, "hike")}`;
  return card;
}

function renderFedwatch(data) {
  els.tableEyebrow.textContent = "Prediction-market implied";
  els.tableTitle.textContent = "FOMC Rate Path";
  const fc = data.fred_context || {};
  els.tableMeta.textContent = `Current rate: ${data.current_rate || "—"}${fc.dff ? ` · DFF ${fc.dff}%` : ""}`;
  els.content.innerHTML = "";

  if (!data.meetings.length && !data.year_end_rates.length) {
    renderEmpty("No FOMC market data found. Refresh live data to ingest Polymarket Fed Decision markets.");
    return;
  }

  // ── FRED cross-check panel ─────────────────────────────────────────────
  if (fc.dff || fc.t2y) {
    const crossCheck = document.createElement("div");
    crossCheck.className = "fedwatch-crosscheck";
    const cutsImplied = fc.implied_cuts_from_current;
    const cutsLabel = cutsImplied == null ? "—"
      : cutsImplied < -0.3 ? `+${Math.abs(cutsImplied).toFixed(1)} hike-equivalent`
      : cutsImplied <= 0.3 ? "~0 (hold)"
      : `~${cutsImplied.toFixed(1)} cut${cutsImplied >= 1.5 ? "s" : ""}`;
    const terminalLabel = fc.implied_terminal_rate != null
      ? `${fc.implied_terminal_rate.toFixed(2)}%`
      : "—";
    crossCheck.innerHTML = `
      <div class="crosscheck-header">
        <span class="crosscheck-title">FRED Market Cross-Check</span>
        <span class="crosscheck-note">2Y Treasury expectations hypothesis · not CME FedWatch (proprietary)</span>
      </div>
      <div class="crosscheck-grid">
        <div class="crosscheck-item">
          <div class="crosscheck-label">Effective Fed Funds (DFF)</div>
          <div class="crosscheck-value">${fc.dff != null ? fc.dff + "%" : "—"}</div>
          <div class="crosscheck-sub">${fc.dff_date || ""}</div>
        </div>
        <div class="crosscheck-item">
          <div class="crosscheck-label">2Y Treasury Yield</div>
          <div class="crosscheck-value">${fc.t2y != null ? fc.t2y + "%" : "—"}</div>
          <div class="crosscheck-sub">${fc.t2y_date || ""}</div>
        </div>
        <div class="crosscheck-item">
          <div class="crosscheck-label">2Y-Implied Terminal Rate</div>
          <div class="crosscheck-value">${terminalLabel}</div>
          <div class="crosscheck-sub">2Y yield − 0.25% term premium</div>
        </div>
        <div class="crosscheck-item">
          <div class="crosscheck-label">2Y-Implied Cuts</div>
          <div class="crosscheck-value">${cutsLabel}</div>
          <div class="crosscheck-sub">vs Polymarket: ${data.cuts_distribution?.[0]?.label || "—"} most likely</div>
        </div>
      </div>`;
    els.content.appendChild(crossCheck);
  }

  // ── Per-meeting cards ─────────────────────────────────────────────────
  if (data.meetings.length) {
    const label = document.createElement("p");
    label.className = "fedwatch-section-title";
    label.textContent = "Meeting-by-meeting (Polymarket)";
    els.content.appendChild(label);

    const grid = document.createElement("div");
    grid.className = "meeting-grid";
    for (const m of data.meetings) grid.appendChild(renderMeetingCard(m));
    els.content.appendChild(grid);
  }

  // ── Cuts distribution + year-end side-by-side ──────────────────────────
  const hasCuts = data.cuts_distribution?.length > 0;
  const hasYearEnd = data.year_end_rates?.length > 0;

  if (hasCuts || hasYearEnd) {
    const twoCol = document.createElement("div");
    twoCol.className = "fedwatch-two-col";

    if (hasCuts) {
      const cutsBlock = document.createElement("div");
      cutsBlock.innerHTML = `<p class="fedwatch-section-title">Cuts in 2026 (Polymarket)</p>`;
      const cutsCard = document.createElement("div");
      cutsCard.className = "rate-dist-card";
      const modal = data.cuts_distribution[0]?.probability || 0;
      cutsCard.innerHTML = data.cuts_distribution.slice(0, 8).map((c) => {
        const isModal = c.probability === modal;
        const width = Math.max(2, c.probability * 100).toFixed(1);
        const pct = (c.probability * 100).toFixed(1);
        return `<div class="rate-row">
          <span class="rate-label">${escapeHtml(c.label)}</span>
          <div class="rate-bar-track"><div class="rate-bar-fill${isModal ? " modal" : ""}" style="width:${width}%"></div></div>
          <span class="rate-pct${isModal ? " modal" : ""}">${pct}%</span>
        </div>`;
      }).join("");
      cutsBlock.appendChild(cutsCard);
      twoCol.appendChild(cutsBlock);
    }

    if (hasYearEnd) {
      const yrBlock = document.createElement("div");
      yrBlock.innerHTML = `<p class="fedwatch-section-title">Year-end 2026 rate (upper bound)</p>`;
      const distCard = document.createElement("div");
      distCard.className = "rate-dist-card";
      const modal2 = data.year_end_rates[0]?.probability || 0;
      distCard.innerHTML = data.year_end_rates.slice(0, 8).map((r) => {
        const isModal = r.probability === modal2;
        const width = Math.max(2, r.probability * 100).toFixed(1);
        const pct = (r.probability * 100).toFixed(1);
        return `<div class="rate-row">
          <span class="rate-label">${escapeHtml(r.rate)}</span>
          <div class="rate-bar-track"><div class="rate-bar-fill${isModal ? " modal" : ""}" style="width:${width}%"></div></div>
          <span class="rate-pct${isModal ? " modal" : ""}">${pct}%</span>
        </div>`;
      }).join("");
      yrBlock.appendChild(distCard);
      twoCol.appendChild(yrBlock);
    }

    els.content.appendChild(twoCol);
  }

  // Small disclaimer at bottom
  const note = document.createElement("p");
  note.className = "fedwatch-note";
  note.textContent = data.cme_note;
  els.content.appendChild(note);
}

// ── Earnings view ──────────────────────────────────────────────────────────

function renderEarningsCard(card) {
  const el = document.createElement("div");
  el.className = "earnings-card";

  const beatPct = card.beat_prob != null ? fmtProb(card.beat_prob) : "—";
  const missPct = card.miss_prob != null ? fmtProb(card.miss_prob) : "—";
  const beatCls = card.beat_prob != null ? "beat" : "none";
  const missCls = card.miss_prob != null ? "miss" : "none";

  const marketRows = card.markets.slice(0, 4).map((m) => `
    <div class="earnings-market-item"
         data-source="${escapeHtml(m.source)}" data-market-id="${escapeHtml(m.market_id)}"
         role="button" tabindex="0">
      <span class="earnings-market-title" title="${escapeHtml(m.title)}">${escapeHtml(m.title)}</span>
      <span class="earnings-market-prob ${probClass(m.probability)}">${fmtProb(m.probability)}</span>
    </div>`).join("");

  el.innerHTML = `
    <h3 class="earnings-company">${escapeHtml(card.company)}</h3>
    <div class="earnings-quarter">${escapeHtml(card.quarter || "Upcoming")}</div>
    <div class="earnings-probs">
      <div class="earnings-prob-item">
        <div class="earnings-prob-label">Beat</div>
        <div class="earnings-prob-value ${beatCls}">${beatPct}</div>
      </div>
      <div class="earnings-prob-item">
        <div class="earnings-prob-label">Miss</div>
        <div class="earnings-prob-value ${missCls}">${missPct}</div>
      </div>
    </div>
    ${card.markets.length ? `<div class="earnings-markets-list">${marketRows}</div>` : ""}`;

  // Click individual market rows
  el.querySelectorAll(".earnings-market-item[data-source]").forEach((row) => {
    row.addEventListener("click", () => openDetail(row.dataset.source, row.dataset.marketId));
    row.addEventListener("keydown", (e) => { if (e.key === "Enter") openDetail(row.dataset.source, row.dataset.marketId); });
  });

  return el;
}

function renderIntelCard(ic) {
  const el = document.createElement("div");
  el.className = "intel-card";

  const kindLabel = ic.summary_kind === "ipo" ? "IPO" : ic.summary_kind === "market_cap" ? "Mkt Cap" : "Market";
  const kindCls   = ic.summary_kind === "ipo" ? "intel-kind-ipo" : "intel-kind-other";
  const topProb   = Math.round(ic.top_probability * 100);

  const marketRows = ic.markets.slice(0, 3).map((m) => {
    const pct = Math.round(m.probability * 100);
    const width = Math.max(2, pct);
    return `<div class="intel-market-row"
                 data-source="${escapeHtml(m.source)}" data-market-id="${escapeHtml(m.market_id)}"
                 role="button" tabindex="0">
      <span class="intel-market-title" title="${escapeHtml(m.title)}">${escapeHtml(m.title)}</span>
      <div class="intel-bar-track"><div class="intel-bar-fill" style="width:${width}%"></div></div>
      <span class="intel-market-prob ${probClass(m.probability)}">${pct}%</span>
    </div>`;
  }).join("");

  el.innerHTML = `
    <div class="intel-card-header">
      <span class="${kindCls} intel-kind">${kindLabel}</span>
      <h3 class="intel-company">${escapeHtml(ic.company)}</h3>
      <span class="intel-top-prob ${probClass(ic.top_probability)}">${topProb}%</span>
    </div>
    <div class="intel-markets">${marketRows}</div>`;

  // Click rows to open detail
  el.querySelectorAll(".intel-market-row").forEach((row) => {
    row.addEventListener("click", () => openDetail(row.dataset.source, row.dataset.marketId));
  });
  return el;
}

function renderEarnings(data) {
  const hasEarnings = data.cards?.length > 0;
  const hasIntel = data.intel?.length > 0;

  els.tableEyebrow.textContent = hasEarnings ? "PM-implied beat/miss probability" : "Market intelligence";
  els.tableTitle.textContent = "Earnings & Markets";
  const earningsLabel = hasEarnings ? `${data.total} earnings` : "0 earnings";
  const intelLabel = hasIntel ? ` · ${data.intel_total} IPO/market signals` : "";
  els.tableMeta.textContent = earningsLabel + intelLabel;
  els.content.innerHTML = "";

  // Earnings season note
  const note = document.createElement("p");
  note.className = "earnings-note";
  note.textContent = data.earnings_season_note ||
    "Research (LBS/Yale, SSRN): PM earnings markets show ~77% accuracy vs ~62% for sell-side consensus the day before earnings.";
  els.content.appendChild(note);

  // Earnings cards section
  if (hasEarnings) {
    const label = document.createElement("p");
    label.className = "fedwatch-section-title";
    label.textContent = "Beat / Miss probabilities";
    els.content.appendChild(label);
    const grid = document.createElement("div");
    grid.className = "earnings-grid";
    for (const card of data.cards) grid.appendChild(renderEarningsCard(card));
    els.content.appendChild(grid);
  }

  // Market intelligence section (IPO + market cap)
  if (hasIntel) {
    const label2 = document.createElement("p");
    label2.className = "fedwatch-section-title";
    label2.textContent = hasEarnings ? "Market intelligence" : "IPO & market cap signals";
    els.content.appendChild(label2);

    const intelGrid = document.createElement("div");
    intelGrid.className = "intel-grid";
    for (const ic of data.intel) intelGrid.appendChild(renderIntelCard(ic));
    els.content.appendChild(intelGrid);
  }

  if (!hasEarnings && !hasIntel) {
    renderEmpty("No market data found. Refresh live data.");
  }
}

// ── Cycle Diagnosis view ───────────────────────────────────────────────────

const REGIME_ICONS = { growing: "🌱", heating: "🔥", slowing: "🌧️", stagflation: "⚡", unknown: "—" };
const STATUS_ICONS = { positive: "✓", caution: "⚠", warning: "!", danger: "✕", neutral: "–" };

function indicatorValueDisplay(ind) {
  if (ind.value == null) return "—";
  const v = Number(ind.value);
  if (Number.isNaN(v)) return "—";
  if (ind.unit === "%") return `${v.toFixed(2)}${ind.unit}`;
  if (ind.unit === "bps") return `${v.toFixed(0)} bps`;
  return `${v.toFixed(1)}${ind.unit ? " " + ind.unit : ""}`;
}

function renderIndicatorCard(ind) {
  const dotHtml = ind.is_live
    ? `<span class="live-dot" title="Live FRED data"></span>`
    : `<span class="static-dot" title="Static — updated manually"></span>`;
  const dateLabel = ind.date ? ` as of ${ind.date}` : "";
  const descHtml = ind.description
    ? `<details class="ind-detail"><summary>ⓘ What it means &amp; how to read it</summary><p>${escapeHtml(ind.description)}</p></details>`
    : '';
  return `
    <div class="indicator-card ${escapeHtml(ind.status)}">
      <div class="indicator-card-header">
        <span class="indicator-card-label">${escapeHtml(ind.label)}</span>
        <span class="tier-badge ${escapeHtml(ind.tier)}">${escapeHtml(ind.tier)}</span>
      </div>
      <div class="indicator-value ${escapeHtml(ind.status)}">${escapeHtml(indicatorValueDisplay(ind))}</div>
      <div class="indicator-reading">${escapeHtml(ind.reading)}</div>
      ${descHtml}
      <div class="indicator-date">${dotHtml}${escapeHtml(ind.source)}${escapeHtml(dateLabel)}</div>
    </div>`;
}

function renderCycle(data) {
  els.tableEyebrow.textContent = "Short-cycle (3–18M) + Long-cycle (3–10Y)";
  els.tableTitle.textContent = "Cycle Diagnosis";
  els.tableMeta.textContent = `as of ${data.as_of}`;
  els.content.innerHTML = "";

  const regime = data.regime;
  const rec = data.recession_risk;

  // FRED key warning
  if (!data.fred_live) {
    const banner = document.createElement("div");
    banner.className = "no-fred-banner";
    banner.innerHTML = `⚠ No FRED API key — live indicators unavailable. Static valuation data is shown. <a href="https://fred.stlouisfed.org/docs/api/api_key.html" target="_blank" rel="noreferrer">Get a free key</a> and restart with <code>--fred-key YOUR_KEY</code>.`;
    els.content.appendChild(banner);
  }

  // Hero: regime + recession risk
  const recFactors = (rec.factors || []).map(f => `<li>${escapeHtml(f)}</li>`).join("");
  const hero = document.createElement("div");
  hero.className = "cycle-hero";
  hero.innerHTML = `
    <div class="cycle-hero-left">
      <p class="eyebrow cycle-regime-label">Current cycle regime</p>
      <div>
        <span class="regime-badge ${escapeHtml(regime.color)}">
          ${REGIME_ICONS[regime.id] || ""} ${escapeHtml(regime.label)}
        </span>
      </div>
      <p class="cycle-regime-description">${escapeHtml(regime.description)}</p>
      <p class="cycle-implication">${escapeHtml(regime.implication)}</p>
    </div>
    <div class="cycle-hero-right">
      <div class="recession-block">
        <div class="recession-label-row">
          <span class="recession-title">Recession risk</span>
          <span class="recession-badge ${escapeHtml(rec.level)}">${escapeHtml(rec.label)} (${rec.score}/8)</span>
        </div>
        ${rec.factors.length
          ? `<ul class="recession-factors">${recFactors}</ul>`
          : `<p style="font-size:12px;color:var(--muted);margin:0">No elevated risk factors detected.</p>`
        }
      </div>
    </div>`;
  els.content.appendChild(hero);

  // VIX Deployment Signal
  const vs = data.vix_signal;
  if (vs) {
    const vixPanel = document.createElement("div");
    vixPanel.className = `vix-deploy-panel ${escapeHtml(vs.color)}`;
    const barPct = Math.min(100, Math.max(0, vs.deploy_pct));
    const vixDisplay = vs.value != null ? vs.value.toFixed(1) : "—";
    const thresholds = [
      { label: "< 15", desc: "Complacency", pct: 0,   cls: "caution" },
      { label: "15–20", desc: "Normal",       pct: 0,   cls: "neutral" },
      { label: "20–30", desc: "Anxiety",      pct: 37,  cls: "caution" },
      { label: "30–40", desc: "Fear",         pct: 62,  cls: "warning" },
      { label: "> 40",  desc: "Panic",        pct: 100, cls: "danger"  },
    ];
    const active = vs.level;
    const levelMap = { low: 0, normal: 1, elevated: 2, high: 3, panic: 4, unknown: -1 };
    const activeIdx = levelMap[active] ?? -1;
    const thresholdHtml = thresholds.map((t, i) =>
      `<div class="vix-threshold ${t.cls}${i === activeIdx ? " active" : ""}">
        <span class="vix-thr-range">${t.label}</span>
        <span class="vix-thr-desc">${t.desc}</span>
        <span class="vix-thr-pct">${t.pct === 0 ? "DCA only" : `Deploy ${t.pct === 100 ? "all" : t.pct + "%"}`}</span>
      </div>`
    ).join("");
    vixPanel.innerHTML = `
      <div class="vix-deploy-left">
        <div class="vix-deploy-header">
          <span class="vix-deploy-label">VIX Cash Deployment Signal</span>
          <span class="vix-deploy-date">${escapeHtml(vs.date || "—")}</span>
        </div>
        <div class="vix-deploy-value-row">
          <span class="vix-big-number">${vixDisplay}</span>
          <span class="vix-level-badge ${escapeHtml(vs.color)}">${escapeHtml(vs.label)}</span>
        </div>
        <div class="vix-deploy-action">${escapeHtml(vs.action)}</div>
        ${barPct > 0 ? `
        <div class="vix-deploy-bar-wrap">
          <div class="vix-deploy-bar" style="width:${barPct}%"></div>
          <span class="vix-deploy-bar-label">${barPct}% of opportunity reserve</span>
        </div>` : ""}
        <p class="vix-deploy-note">${escapeHtml(vs.note)}</p>
      </div>
      <div class="vix-deploy-right">
        <div class="vix-threshold-grid">${thresholdHtml}</div>
      </div>`;
    els.content.appendChild(vixPanel);
  }

  // Short-cycle indicators (VIX excluded — already shown in deployment panel above)
  const shortInds = (data.short_cycle || []).filter(i => !i.label.toUpperCase().includes('VIX'));
  const shortTitle = document.createElement("div");
  shortTitle.className = "macro-section-title";
  shortTitle.innerHTML = `<span>Short-Cycle Indicators <span class="macro-section-sub">(3–18 months · move before the economy does)</span></span>`;
  els.content.appendChild(shortTitle);

  const shortGrid = document.createElement("div");
  shortGrid.className = "indicator-grid";
  shortGrid.innerHTML = shortInds.map(renderIndicatorCard).join("");
  els.content.appendChild(shortGrid);

  // Long-cycle indicators
  const longInds = (data.long_cycle || []).filter(i => !i.label.toUpperCase().includes('VIX'));
  const longTitle = document.createElement("div");
  longTitle.className = "macro-section-title";
  longTitle.innerHTML = `<span>Long-Cycle Indicators <span class="macro-section-sub">(3–10 years · structural valuation & credit)</span></span>`;
  els.content.appendChild(longTitle);

  const longGrid = document.createElement("div");
  longGrid.className = "indicator-grid";
  longGrid.innerHTML = longInds.map(renderIndicatorCard).join("");
  els.content.appendChild(longGrid);
}

// ── Market Valuation view ──────────────────────────────────────────────────

function renderValuationTile(ind) {
  const valueDisplay = ind.value != null
    ? `${Number(ind.value).toFixed(ind.unit === "%" ? 1 : 2)}${ind.unit}`
    : "—";
  const dotHtml = ind.is_live
    ? `<span class="live-dot" title="Live"></span>`
    : `<span class="static-dot" title="Static"></span>`;
  const avgLine = ind.long_run_avg
    ? `<div class="valuation-tile-avg">Long-run avg: ${escapeHtml(ind.long_run_avg)}${escapeHtml(ind.unit || "")}</div>`
    : "";
  const pctLine = ind.percentile
    ? `<div class="valuation-tile-percentile">${escapeHtml(ind.percentile)}</div>`
    : "";
  return `
    <div class="valuation-tile ${escapeHtml(ind.status)}">
      <div class="valuation-tile-label">${escapeHtml(ind.label)}</div>
      <div class="valuation-tile-value ${escapeHtml(ind.status)}">${escapeHtml(valueDisplay)}</div>
      ${pctLine}
      ${avgLine}
      <div class="valuation-tile-desc">${escapeHtml(ind.description)}</div>
      <div class="valuation-tile-date">${dotHtml}${escapeHtml(ind.source)} · as of ${escapeHtml(ind.date || "—")}</div>
    </div>`;
}

// ── Market Sentiment view (third MECE lens: fear/greed & positioning) ────────
// ── Momentum Concentration — Phase-1 sector-crowding proxy (MS-R3-lite) ────────
function _momConcLegHtml(leg, title) {
  const bars = (leg.breakdown || []).map(b => `
    <div class="momconc-row">
      <span class="momconc-ind">${escapeHtml(b.industry)}</span>
      <div class="momconc-bar-track"><div class="momconc-bar" style="width:${Math.min(100, b.share)}%"></div></div>
      <span class="momconc-pct">${b.share}%</span>
    </div>`).join("");
  return `
    <div class="indicator-card ${escapeHtml(leg.status)}">
      <div class="indicator-card-header">
        <span class="indicator-card-label">${escapeHtml(title)}</span>
        <span class="tier-badge context">HHI</span>
      </div>
      <div class="indicator-value ${escapeHtml(leg.status)}">${leg.hhi}</div>
      <div class="indicator-reading">Top: ${escapeHtml(leg.top_industry)} — ${leg.top_share}% of ${leg.n} names</div>
      <div class="momconc-breakdown">${bars}</div>
    </div>`;
}

// US has no free true point-to-point 1m/3m return (Yahoo's bulk quote endpoint
// doesn't carry one) — 50d/200d are price-vs-own-moving-average proxies, not
// calendar-month returns, and are labeled that way in the tab + description.
const _MOM_CONC_HORIZON_LABELS = {
  us: [["50d", "50-Day"], ["200d", "200-Day"], ["52w", "52-Week"]],
  cn: [["1m", "1-Month"], ["3m", "3-Month"], ["6m", "6-Month"], ["12m", "12-Month"]],
};
const _momConcState = {};   // containerId → { market, horizon }

function renderMomentumConcentration(data, containerId, market) {
  const box = document.getElementById(containerId);
  if (!box) return;
  const state = _momConcState[containerId];
  if (data.horizon) state.horizon = data.horizon;

  const tabs = (_MOM_CONC_HORIZON_LABELS[market] || []).map(([key, label]) =>
    `<button class="view-tab ${key === state.horizon ? "active" : ""}" data-momhz="${key}" type="button">${label}</button>`
  ).join("");
  const tabsHtml = `<nav class="view-tabs momconc-tabs" aria-label="Momentum horizon">${tabs}</nav>`;

  let bodyHtml;
  if (data.status === "building") {
    bodyHtml = `<div class="cn-fear-loading">Building market snapshot…</div>`;
  } else if (data.status !== "ready") {
    bodyHtml = `<div class="sc-error">⚠ ${escapeHtml(data.error || "Unavailable")}</div>`;
  } else {
    const isProxy = market === "us" && (state.horizon === "50d" || state.horizon === "200d");
    const proxyNote = isProxy
      ? " This horizon is a price-vs-its-own-moving-average proxy, not a true calendar-month return (no free source for that at whole-market scale in the US)."
      : "";
    bodyHtml = `
      <p class="overall-description">Is the momentum trade crowded into one sector right now? Equal-weight sector concentration (HHI) of today's top/bottom 10% by return over the selected horizon — a single-day snapshot, not a trend, and not yet the full CoMomentum crowding measure (that needs weekly return history + a factor regression this product doesn't have yet). Diversified &lt;0.15 · moderate 0.15–0.25 · concentrated ≥0.25.${proxyNote}</p>
      <div class="indicator-grid">
        ${_momConcLegHtml(data.winner, "Winner leg (top 10%)")}
        ${_momConcLegHtml(data.loser, "Loser leg (bottom 10%)")}
      </div>`;
  }
  box.innerHTML = tabsHtml + bodyHtml;
  box.querySelectorAll("[data-momhz]").forEach(b =>
    b.addEventListener("click", () => loadMomentumConcentration(market, containerId, b.dataset.momhz)));
}

async function loadMomentumConcentration(market, containerId, horizon) {
  const state = _momConcState[containerId] || (_momConcState[containerId] = { market, horizon: null });
  if (horizon) state.horizon = horizon;
  try {
    const hzParam = state.horizon ? `&horizon=${state.horizon}` : "";
    const data = await fetchJson(`/api/momentum_concentration?market=${market}${hzParam}`);
    renderMomentumConcentration(data, containerId, market);
    if (data.status === "building") setTimeout(() => loadMomentumConcentration(market, containerId), 3000);
  } catch (err) {
    const box = document.getElementById(containerId);
    if (box) box.innerHTML = `<div class="sc-error">⚠ ${escapeHtml(err.message)}</div>`;
  }
}

// ── Momentum Concentration — across-horizon line chart (1M→12M / 50d→52w) ─────
// One fetch covers every horizon (same in-memory snapshot on the backend), so
// this loads once per page view, independent of which tab the card above is on.
const _MOM_CONC_PALETTE = ["#2f7a5c", "#c2542f", "#4470ad", "#9a6fb0", "#c99a2e", "#5b8fa8"];

function _momConcLineChartSvg(seriesBlock, hzLabels) {
  const series = seriesBlock.series || [];
  if (!series.length) return `<p class="momconc-chart-empty">Not enough data yet.</p>`;
  const W = 560, H = 190, padL = 34, padR = 24, padT = 10, padB = 22;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const n = hzLabels.length;
  const xAt = (i) => padL + (n === 1 ? plotW / 2 : plotW * i / (n - 1));
  const allVals = series.flatMap(s => s.values).filter(v => v != null);
  const maxV = Math.max(10, ...allVals) * 1.15;
  const yAt = (v) => padT + plotH - (v / maxV) * plotH;

  const gridY = [0, 0.5, 1].map(f => {
    const v = maxV * f, y = yAt(v).toFixed(1);
    return `<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" class="momconc-grid"/>
            <text x="${padL - 5}" y="${(+y + 3)}" class="momconc-axislabel" text-anchor="end">${Math.round(v)}%</text>`;
  }).join("");
  const xLabels = hzLabels.map((lab, i) =>
    `<text x="${xAt(i).toFixed(1)}" y="${H - 6}" class="momconc-axislabel" text-anchor="middle">${escapeHtml(lab)}</text>`
  ).join("");
  const lines = series.map((s, idx) => {
    const color = _MOM_CONC_PALETTE[idx % _MOM_CONC_PALETTE.length];
    const pts = s.values.map((v, i) => v == null ? null : `${xAt(i).toFixed(1)},${yAt(v).toFixed(1)}`)
      .filter(Boolean).join(" ");
    const dots = s.values.map((v, i) => v == null ? "" :
      `<circle cx="${xAt(i).toFixed(1)}" cy="${yAt(v).toFixed(1)}" r="3" fill="${color}"><title>${escapeHtml(s.industry)}: ${v}%</title></circle>`
    ).join("");
    return `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="2"/>${dots}`;
  }).join("");
  const legend = series.map((s, idx) =>
    `<span class="momconc-legend-item"><span class="momconc-legend-dot" style="background:${_MOM_CONC_PALETTE[idx % _MOM_CONC_PALETTE.length]}"></span>${escapeHtml(s.industry)}</span>`
  ).join("");

  return `<div class="momconc-chart-wrap">
    <svg viewBox="0 0 ${W} ${H}" class="momconc-chart-svg" preserveAspectRatio="xMidYMid meet">
      ${gridY}${xLabels}${lines}
    </svg>
    <div class="momconc-legend">${legend}</div>
  </div>`;
}

function renderMomentumConcentrationSeries(data, containerId) {
  const box = document.getElementById(containerId);
  if (!box) return;
  if (data.status === "building") { box.innerHTML = `<div class="cn-fear-loading">Building market snapshot…</div>`; return; }
  if (data.status !== "ready") { box.innerHTML = `<div class="sc-error">⚠ ${escapeHtml(data.error || "Unavailable")}</div>`; return; }
  const labels = data.horizons.map(h => (_MOM_CONC_HORIZON_LABELS.us.concat(_MOM_CONC_HORIZON_LABELS.cn).find(([k]) => k === h) || [h, h])[1]);
  box.innerHTML = `
    <p class="overall-description">Top-industry share of the winner/loser leg across every horizon at once — where the tabbed card above shows one horizon, this shows whether a sector's dominance is building, fresh, or fading.</p>
    <div class="momconc-chart-row">
      <div><p class="momconc-chart-title">Winner leg</p>${_momConcLineChartSvg(data.winner, labels)}</div>
      <div><p class="momconc-chart-title">Loser leg</p>${_momConcLineChartSvg(data.loser, labels)}</div>
    </div>`;
}

async function loadMomentumConcentrationSeries(market, containerId) {
  try {
    const data = await fetchJson(`/api/momentum_concentration_series?market=${market}`);
    renderMomentumConcentrationSeries(data, containerId);
    if (data.status === "building") setTimeout(() => loadMomentumConcentrationSeries(market, containerId), 3000);
  } catch (err) {
    const box = document.getElementById(containerId);
    if (box) box.innerHTML = `<div class="sc-error">⚠ ${escapeHtml(err.message)}</div>`;
  }
}

// ── US Fear & Greed — 5-leg composite, reuses the CN gauge's visual language ───
// (same .cn-fear-* classes) but has no history/percentile yet — single-day read.
function renderUsFear(d, box) {
  if (d.status !== "ready") {
    box.innerHTML = `<div class="sc-error">⚠ ${escapeHtml(d.error || "Unavailable")}</div>`;
    return;
  }
  const v = d.value, ang = -90 + v / 100 * 180;
  const arc = (from, to, color) => {
    const a0 = (-90 + from / 100 * 180) * Math.PI / 180, a1 = (-90 + to / 100 * 180) * Math.PI / 180;
    const R = 92, cx = 110, cy = 104;
    const x0 = cx + R * Math.sin(a0), y0 = cy - R * Math.cos(a0);
    const x1 = cx + R * Math.sin(a1), y1 = cy - R * Math.cos(a1);
    return `<path d="M ${x0.toFixed(1)} ${y0.toFixed(1)} A ${R} ${R} 0 0 1 ${x1.toFixed(1)} ${y1.toFixed(1)}" fill="none" stroke="${color}" stroke-width="16"/>`;
  };
  const nx = 110 + 74 * Math.sin(ang * Math.PI / 180), ny = 104 - 74 * Math.cos(ang * Math.PI / 180);
  const legs = (d.legs || []).map(l => {
    const c = l.value >= 80 ? "#dc2626" : l.value >= 60 ? "#ea580c" : l.value >= 40 ? "#a16207" : l.value >= 20 ? "#65a30d" : "#16a34a";
    return `<div class="cn-leg"><span class="cn-leg-lab">${escapeHtml(l.label)}</span>
      <span class="cn-leg-bar"><span class="cn-leg-fill" style="width:${l.value}%;background:${c}"></span></span>
      <span class="cn-leg-val">${l.value}</span></div>`;
  }).join("");

  box.innerHTML = `
    <div class="cn-fear-card">
      <div class="cn-fear-main">
        <div class="cn-fear-gauge">
          <svg viewBox="0 0 220 128">
            ${arc(0, 20, "#16a34a")}${arc(20, 40, "#65a30d")}${arc(40, 60, "#a16207")}${arc(60, 80, "#ea580c")}${arc(80, 100, "#dc2626")}
            <line x1="110" y1="104" x2="${nx.toFixed(1)}" y2="${ny.toFixed(1)}" stroke="var(--text)" stroke-width="3"/>
            <circle cx="110" cy="104" r="5" fill="var(--text)"/>
          </svg>
          <div class="cn-fear-num" style="color:${d.color}">${v}</div>
          <div class="cn-fear-zone" style="color:${d.color}">${escapeHtml(d.zone)}</div>
          <div class="cn-fear-meta">${escapeHtml(d.date)} · single-day read, no history yet</div>
        </div>
        <div class="cn-fear-side">
          <div class="cn-fear-read">${escapeHtml(d.read)}</div>
          <div class="cn-legs">${legs}</div>
          <div class="cn-legs-note">Each bar is a component leg's fixed-threshold bucket for today (0/25/50/75/100 anchors) — NOT a rolling percentile like the A-share gauge. The dial is their equal-weight average.</div>
          <details class="cn-legs-explain"><summary>ⓘ What each leg means &amp; how to read it</summary><div>
            ${(d.legs || []).map(l => `<p><strong>${escapeHtml(l.label)}</strong> — ${escapeHtml(l.desc || "")} <em>(${escapeHtml(l.raw || "")})</em></p>`).join("")}
            <p class="cn-legs-frame">${escapeHtml(d.methodology_note || "")}</p>
          </div></details>
        </div>
      </div>
    </div>`;
}

async function loadUsFear() {
  const box = document.getElementById("usFearBox");
  if (!box) return;
  try {
    const d = await fetchJson("/api/us_fear");
    renderUsFear(d, box);
  } catch (err) {
    box.innerHTML = `<div class="sc-error">⚠ ${escapeHtml(err.message)}</div>`;
  }
}

function renderSentiment(data) {
  els.tableEyebrow.textContent = "Fear · Greed · Positioning";
  els.tableTitle.textContent = "Market Sentiment";
  els.tableMeta.textContent = `as of ${data.as_of}`;
  els.content.innerHTML = "";

  if (!data.fred_live) {
    const banner = document.createElement("div");
    banner.className = "no-fred-banner";
    banner.innerHTML = `⚠ No FRED API key — live sentiment indicators unavailable. <a href="https://fred.stlouisfed.org/docs/api/api_key.html" target="_blank" rel="noreferrer">Get a free key</a> and restart with <code>--fred-key YOUR_KEY</code>.`;
    els.content.appendChild(banner);
  }

  const iconFor = { extreme_fear: "🔴", fear: "🟠", normal: "🟢", complacency: "🟡" };
  const hero = document.createElement("div");
  hero.className = "valuation-hero";
  hero.innerHTML = `
    <span class="overall-signal ${escapeHtml(data.overall)}">
      ${iconFor[data.overall] || "🟢"} VIX read: ${escapeHtml(data.overall_label)}
    </span>
    <p class="overall-description">VIX's own level only — a separate, broader 5-leg composite (below) also weighs price strength, momentum, credit stress, and safe-haven demand, and can disagree with VIX alone (e.g. calm volatility while positioning is stretched everywhere else).</p>`;
  els.content.appendChild(hero);

  const mcTitle = document.createElement("div");
  mcTitle.className = "macro-section-title";
  mcTitle.innerHTML = `<span>Cross-Asset Trend <span class="macro-section-sub">(Weinstein stages · 30-week SMA/EMA on major assets)</span></span>`;
  els.content.appendChild(mcTitle);
  const mcBox = document.createElement("div");
  mcBox.id = "marketContextBox";
  mcBox.innerHTML = `<div class="cn-fear-loading">Loading…</div>`;
  els.content.appendChild(mcBox);
  loadMarketContext();

  const fgTitle = document.createElement("div");
  fgTitle.className = "macro-section-title";
  fgTitle.innerHTML = `<span>US Fear &amp; Greed <span class="macro-section-sub">(5-leg composite, patterned after CNN's index)</span></span>`;
  els.content.appendChild(fgTitle);
  const fgBox = document.createElement("div");
  fgBox.id = "usFearBox";
  fgBox.innerHTML = `<div class="cn-fear-loading">Loading…</div>`;
  els.content.appendChild(fgBox);
  loadUsFear();

  const brTitle = document.createElement("div");
  brTitle.className = "macro-section-title";
  brTitle.innerHTML = `<span>Market Breadth <span class="macro-section-sub">(Weinstein Momentum Index — 200-day MA of net advances−declines)</span></span>`;
  els.content.appendChild(brTitle);
  const brBox = document.createElement("div");
  brBox.id = "usBreadthBox";
  brBox.innerHTML = `<div class="cn-fear-loading">Loading…</div>`;
  els.content.appendChild(brBox);
  loadUsBreadth();

  const rawTitle = document.createElement("div");
  rawTitle.className = "macro-section-title";
  rawTitle.innerHTML = `<span>Raw Indicators</span>`;
  els.content.appendChild(rawTitle);

  if (data.indicators && data.indicators.length) {
    const grid = document.createElement("div");
    grid.className = "indicator-grid";
    grid.innerHTML = data.indicators.map(renderIndicatorCard).join("");
    els.content.appendChild(grid);
  }

  const momTitle = document.createElement("div");
  momTitle.className = "macro-section-title";
  momTitle.innerHTML = `<span>Momentum Concentration <span class="macro-section-sub">(is the momentum trade crowded into one sector?)</span></span>`;
  els.content.appendChild(momTitle);
  const momBox = document.createElement("div");
  momBox.id = "usMomConcBox";
  momBox.innerHTML = `<div class="cn-fear-loading">Loading…</div>`;
  els.content.appendChild(momBox);
  loadMomentumConcentration("us", "usMomConcBox");

  const momChartBox = document.createElement("div");
  momChartBox.id = "usMomConcChartBox";
  momChartBox.innerHTML = `<div class="cn-fear-loading">Loading chart…</div>`;
  els.content.appendChild(momChartBox);
  loadMomentumConcentrationSeries("us", "usMomConcChartBox");
}

function renderValuation(data) {
  els.tableEyebrow.textContent = "S&P 500 multi-lens assessment";
  els.tableTitle.textContent = "Market Valuation";
  els.tableMeta.textContent = `as of ${data.as_of}`;
  els.content.innerHTML = "";

  if (!data.fred_live) {
    const banner = document.createElement("div");
    banner.className = "no-fred-banner";
    banner.innerHTML = `⚠ No FRED API key — live rate/spread indicators unavailable. Static valuation tiles are shown below. <a href="https://fred.stlouisfed.org/docs/api/api_key.html" target="_blank" rel="noreferrer">Get a free key</a> and restart with <code>--fred-key YOUR_KEY</code>.`;
    els.content.appendChild(banner);
  }

  // Hero: overall signal
  const hero = document.createElement("div");
  hero.className = "valuation-hero";
  hero.innerHTML = `
    <span class="overall-signal ${escapeHtml(data.overall)}">
      ${data.overall === "extreme" ? "🔴" : data.overall === "expensive" ? "🟠" : "🟢"}
      ${escapeHtml(data.overall_label)}
    </span>
    <p class="overall-description">${escapeHtml(data.overall_description)}</p>`;
  els.content.appendChild(hero);

  // Live market indicators
  if (data.live_indicators && data.live_indicators.length) {
    const liveTitle = document.createElement("div");
    liveTitle.className = "macro-section-title";
    liveTitle.innerHTML = `<span>Live Market Conditions <span class="macro-section-sub">(FRED · rates, spreads, and financial conditions)</span></span>`;
    els.content.appendChild(liveTitle);

    const liveGrid = document.createElement("div");
    liveGrid.className = "indicator-grid";
    liveGrid.innerHTML = data.live_indicators.map(renderIndicatorCard).join("");
    els.content.appendChild(liveGrid);
  }

  // Static valuation tiles
  if (data.static_indicators && data.static_indicators.length) {
    const staticTitle = document.createElement("div");
    staticTitle.className = "macro-section-title";
    staticTitle.innerHTML = `<span>Market Valuation <span class="macro-section-sub">(updated monthly · compare to long-run averages)</span></span>`;
    els.content.appendChild(staticTitle);

    const tilesEl = document.createElement("div");
    tilesEl.className = "valuation-tiles";
    tilesEl.innerHTML = data.static_indicators.map(renderValuationTile).join("");
    els.content.appendChild(tilesEl);
  }
}

// ── Signal Quality view ────────────────────────────────────────────────────

function scoreClass(score) {
  if (score >= 50) return "s-high";
  if (score >= 25) return "s-mid";
  return "s-low";
}

function renderQuality(data) {
  els.tableEyebrow.textContent = "Volume · Liquidity · Spread · History";
  els.tableTitle.textContent = "Signal Quality";
  els.tableMeta.textContent = `${data.total} markets ranked`;
  els.content.innerHTML = "";

  const note = document.createElement("p");
  note.className = "quality-note";
  note.textContent = "Score (0–100) from: volume tier (40pts) + liquidity (25pts) + bid-ask spread (15pts, Kalshi only) + snapshot depth (10pts) + cross-platform match (10pts). Full wallet signals (skilled-wallet ID, whale concentration) require Polymarket CLOB Activity API or Kalshi trade history feed.";
  els.content.appendChild(note);

  if (!data.items.length) {
    renderEmpty("No quality data available.");
    return;
  }

  const table = document.createElement("table");
  table.className = "quality-table";
  table.innerHTML = `
    <thead>
      <tr>
        <th>Score</th>
        <th>Market</th>
        <th class="right">Prob</th>
        <th class="right">Volume</th>
        <th class="right">Liquidity</th>
        <th class="right">Spread</th>
        <th>Flags</th>
      </tr>
    </thead>
    <tbody></tbody>`;

  const tbody = table.querySelector("tbody");

  for (const item of data.items) {
    const tr = document.createElement("tr");
    tr.dataset.source = item.source;
    tr.dataset.marketId = item.market_id;

    const flagsHtml = [
      ...item.flags.map((f) => `<span class="quality-flag ${f}">${f.replace(/_/g, " ")}</span>`),
      item.cross_platform ? `<span class="cross-platform-badge">✓ dual</span>` : "",
    ].filter(Boolean).join("");

    const title = item.event_title && item.event_title !== item.title
      ? `${item.event_title}: ${item.title}` : item.title;

    tr.innerHTML = `
      <td><span class="score-circle ${scoreClass(item.score)}">${item.score}</span></td>
      <td title="${escapeHtml(title)}" style="max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(title)}</td>
      <td class="right">${fmtProb(item.probability)}</td>
      <td class="right">${fmt(item.volume)}</td>
      <td class="right">${fmt(item.liquidity)}</td>
      <td class="right">${item.spread != null ? (item.spread * 100).toFixed(1) + "pp" : "—"}</td>
      <td>${flagsHtml}</td>`;

    tr.addEventListener("click", () => openDetail(item.source, item.market_id));
    tbody.appendChild(tr);
  }

  els.content.appendChild(table);
}

// ── Markets view (flat) ────────────────────────────────────────────────────

function renderMarkets(items) {
  els.tableEyebrow.textContent = "Market inventory";
  els.tableTitle.textContent = "Markets";
  els.tableMeta.textContent = `${items.length} markets`;
  els.content.innerHTML = "";
  if (!items.length) { renderEmpty("No markets match these filters."); return; }

  for (const item of items) {
    const row = document.createElement("article");
    row.className = "market-row";
    row.tabIndex = 0;
    const heading = item.event_title && item.event_title !== item.title
      ? `${item.event_title}: ${item.title}` : item.title;
    const catCls = catPillClass(item.category);
    row.innerHTML = `
      <div class="market-title">
        <h3>${escapeHtml(heading)}</h3>
        <div class="pill-row" style="margin-top:6px">
          <span class="pill source">${escapeHtml(item.source)}</span>
          <span class="pill ${catCls}">${escapeHtml(item.category || "uncategorized")}</span>
        </div>
      </div>
      <div class="metric">
        <span>Probability</span>
        <strong class="prob-value ${probClass(item.probability)}">${fmtProb(item.probability)}</strong>
        ${probBarHtml(item.probability)}
      </div>
      <div class="metric"><span>Liquidity</span><strong>${fmt(item.liquidity)}</strong></div>
      <div class="metric"><span>Updated</span><strong style="font-size:13px">${fmtDate(item.observed_at)}</strong></div>`;
    row.addEventListener("click", () => openDetail(item.source, item.market_id));
    row.addEventListener("keydown", (e) => { if (e.key === "Enter") openDetail(item.source, item.market_id); });
    els.content.appendChild(row);
  }
}

function renderEmpty(message) {
  const div = document.createElement("div");
  div.className = "empty-state";
  div.textContent = message;
  els.content.appendChild(div);
}

// ── Event delegation for radar group rows ──────────────────────────────────

els.content.addEventListener("click", (e) => {
  const row = e.target.closest(".event-market-row[data-source]");
  if (row) openDetail(row.dataset.source, row.dataset.marketId);
});

els.content.addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  const row = e.target.closest(".event-market-row[data-source]");
  if (row) openDetail(row.dataset.source, row.dataset.marketId);
});

// ── Query params ───────────────────────────────────────────────────────────

function queryParams() {
  const params = new URLSearchParams();
  params.set("limit", els.limitInput.value || "30");
  if (els.categorySelect.value) params.set("category", els.categorySelect.value);
  if (state.view === "radar") {
    params.set("lookbackHours", els.lookbackInput.value || "24");
    params.set("minProbability", els.probInput.value || "0.02");
    params.set("maxPerEvent", els.maxPerEventInput.value || "5");
    if (els.allCategoriesInput.checked) params.set("allCategories", "true");
  } else if (state.view === "divergence") {
    params.set("minDelta", els.minDeltaInput?.value || "0.05");
  } else {
    if (els.sourceSelect.value) params.set("source", els.sourceSelect.value);
    if (els.searchInput.value.trim()) params.set("q", els.searchInput.value.trim());
    params.set("sort", "volume");
  }
  return params;
}

function updateControlVisibility() {
  const v = state.view;
  if (els.minDeltaRow) els.minDeltaRow.style.display = v === "divergence" ? "" : "none";
  // Radar-specific controls
  const radarOnly = ["lookbackInput", "probInput", "maxPerEventInput", "allCategoriesInput"];
  const radarRows = radarOnly.map(id => document.querySelector(`#${id}`)?.closest(".control-row, .checkbox-row")).filter(Boolean);
  radarRows.forEach(el => { el.style.display = v === "radar" ? "" : "none"; });
}

// ── Order Flow ─────────────────────────────────────────────────────────────

function hhiColor(label) {
  return label === "High" ? "#ef4444" : label === "Medium" ? "#f59e0b" : "#22c55e";
}

function renderOrderflow(payload) {
  els.tableEyebrow.textContent = "Smart money";
  els.tableTitle.textContent = "Order Flow";

  const markets = payload.markets || [];
  const wallets = payload.top_wallets || [];
  const largeTrades = payload.global_large_trades || [];

  els.tableMeta.textContent =
    `${markets.length} markets analysed · ${largeTrades.length} large trades · ${wallets.length} active wallets`;

  const container = els.content;
  container.innerHTML = "";

  if (!markets.length) {
    renderEmpty("No order-flow data available. Refresh live data first.");
    return;
  }

  // ── Section: Market Concentration ──────────────────────────────────────
  const mSection = document.createElement("div");
  mSection.className = "of-section";
  mSection.innerHTML = `<h2 class="of-section-title">Whale Concentration by Market</h2>
    <p class="of-section-note">HHI measures how concentrated positions are. High = few whales dominate price.</p>`;

  const mGrid = document.createElement("div");
  mGrid.className = "of-market-grid";

  for (const m of markets) {
    const card = document.createElement("div");
    card.className = "of-market-card";
    const color = hhiColor(m.concentration_label);
    const pct = Math.round(m.hhi * 100);
    const topW = (m.top_wallets || []).slice(0, 3);

    const walletRows = topW.map(w =>
      `<div class="of-wallet-row">
        <span class="of-wallet-name">${escapeHtml(w.name)}</span>
        <span class="of-wallet-share">${w.share_pct}%</span>
        <span class="of-wallet-side ${w.side}">${w.side}</span>
       </div>`
    ).join("");

    const largeBadges = (m.large_trades || []).slice(0, 2).map(t =>
      `<span class="of-trade-badge ${t.side.toLowerCase()}">${t.side === "BUY" ? "▲" : "▼"} $${fmtK(t.usd)} ${escapeHtml(t.outcome)}</span>`
    ).join("");

    const urlTag = m.url
      ? `<a class="of-ext-link" href="${escapeHtml(m.url)}" target="_blank" rel="noreferrer">↗</a>` : "";

    card.innerHTML = `
      <div class="of-card-header">
        <span class="of-conc-badge" style="background:${color}">${m.concentration_label} HHI</span>
        <span class="of-hhi-val">${pct}%</span>
        ${urlTag}
      </div>
      <div class="of-card-title">${escapeHtml(m.title)}</div>
      <div class="of-card-meta">
        <span class="prob-pill ${probClass(m.probability)}">${fmtProb(m.probability)}</span>
        <span class="of-vol">Vol $${fmtK(m.volume)}</span>
      </div>
      ${walletRows ? `<div class="of-wallet-list">${walletRows}</div>` : ""}
      ${largeBadges ? `<div class="of-trade-badges">${largeBadges}</div>` : ""}
    `;
    mGrid.appendChild(card);
  }
  mSection.appendChild(mGrid);
  container.appendChild(mSection);

  // ── Section: Large Trades Feed ─────────────────────────────────────────
  if (largeTrades.length) {
    const tSection = document.createElement("div");
    tSection.className = "of-section";
    tSection.innerHTML = `<h2 class="of-section-title">Recent Large Trades ($500+)</h2>`;

    const table = document.createElement("div");
    table.className = "of-trade-table";
    table.innerHTML = `
      <div class="of-trade-head">
        <span>Trader</span><span>Market</span><span>Side</span><span>Size</span><span>Price</span>
      </div>`;

    for (const t of largeTrades.slice(0, 15)) {
      const row = document.createElement("div");
      row.className = `of-trade-row ${(t.side || "").toLowerCase()}`;
      const mUrl = t.market_url
        ? `<a href="${escapeHtml(t.market_url)}" target="_blank" rel="noreferrer" class="of-mkt-link">${escapeHtml(t.market_title)}</a>`
        : escapeHtml(t.market_title);
      row.innerHTML = `
        <span class="of-trader">${escapeHtml(t.name)}</span>
        <span class="of-mkt-title">${mUrl}</span>
        <span class="of-side-badge ${(t.side||'').toLowerCase()}">${t.side} ${escapeHtml(t.outcome)}</span>
        <span class="of-size">$${fmtK(t.usd)}</span>
        <span class="of-price">${Math.round(t.price * 100)}¢</span>
      `;
      table.appendChild(row);
    }
    tSection.appendChild(table);
    container.appendChild(tSection);
  }

  // ── Section: Top Wallets ───────────────────────────────────────────────
  if (wallets.length) {
    const wSection = document.createElement("div");
    wSection.className = "of-section";
    wSection.innerHTML = `<h2 class="of-section-title">Most Active Wallets</h2>
      <p class="of-section-note">Ranked by total notional traded across the top markets.</p>`;

    const wGrid = document.createElement("div");
    wGrid.className = "of-wallet-grid";

    wallets.forEach((w, i) => {
      const card = document.createElement("div");
      card.className = "of-wallet-card";
      const totalUsd = w.total_buy_usd + w.total_sell_usd;
      card.innerHTML = `
        <div class="of-wallet-rank">#${i + 1}</div>
        <div class="of-wallet-card-name">${escapeHtml(w.name)}</div>
        <div class="of-wallet-addr">${w.wallet.slice(0, 10)}…</div>
        <div class="of-wallet-stats">
          <span class="buy">▲ $${fmtK(w.total_buy_usd)}</span>
          <span class="sell">▼ $${fmtK(w.total_sell_usd)}</span>
        </div>
        <div class="of-wallet-markets">${w.markets_active} market${w.markets_active !== 1 ? "s" : ""}</div>
      `;
      wGrid.appendChild(card);
    });
    wSection.appendChild(wGrid);
    container.appendChild(wSection);
  }

  // ── Note ──────────────────────────────────────────────────────────────
  const note = document.createElement("p");
  note.className = "of-note";
  note.textContent = payload.note || "";
  container.appendChild(note);
}

function fmtK(n) {
  if (!n && n !== 0) return "—";
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(0) + "K";
  return String(Math.round(n));
}

// ── Load ───────────────────────────────────────────────────────────────────

async function loadSummary() {
  const summary = await fetchJson("/api/summary");
  renderSummary(summary);
}

async function loadContent() {
  state.view = els.viewSelect.value;
  updateControlVisibility();
  setStatus("Loading…", true);
  try {
    const params = queryParams();
    if (state.view === "radar") {
      const payload = await fetchJson(`/api/radar?${params}`);
      renderRadar(payload.items);
    } else if (state.view === "divergence") {
      const payload = await fetchJson(`/api/divergence?${params}`);
      renderDivergence(payload.pairs);
    } else if (state.view === "fedwatch") {
      const payload = await fetchJson("/api/fedwatch");
      renderFedwatch(payload);
    } else if (state.view === "earnings") {
      const payload = await fetchJson(`/api/earnings?${params}`);
      renderEarnings(payload);
    } else if (state.view === "orderflow") {
      setStatus("Fetching order-flow data from Polymarket…", true);
      const payload = await fetchJson("/api/orderflow");
      renderOrderflow(payload);
    } else if (state.view === "quality") {
      const payload = await fetchJson(`/api/quality?${params}`);
      renderQuality(payload);
    } else {
      const payload = await fetchJson(`/api/markets?${params}`);
      renderMarkets(payload.items);
    }
    setStatus("Ready");
  } catch (err) {
    setStatus(err.message);
  }
}

async function reloadAll() {
  try {
    await loadSummary();
    await loadContent();
  } catch (err) {
    setStatus(err.message);
  }
}

async function refreshData() {
  setRefreshLoading(true);
  setStatus("Fetching live data…", true);
  try {
    const payload = await fetchJson("/api/ingest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source: "all", limit: 200, timeout: 20 }),
    });
    renderSummary(payload.summary);
    await loadContent();
    const w = payload.warnings?.length ? ` (${payload.warnings.length} warnings)` : "";
    setStatus(`Stored ${payload.stored} snapshots.${w}`);
  } catch (err) {
    setStatus(err.message);
  } finally {
    setRefreshLoading(false);
  }
}

// ── Detail panel ───────────────────────────────────────────────────────────

async function openDetail(source, marketId) {
  setStatus("Loading market history…", true);
  try {
    const params = new URLSearchParams({ source, marketId });
    const payload = await fetchJson(`/api/history?${params}`);
    renderDetail(payload);
    els.detailPanel.hidden = false;
    setStatus("Ready");
  } catch (err) {
    setStatus(err.message);
  }
}

function renderDetail(payload) {
  const market = payload.market;
  const snaps = payload.snapshots || [];
  const heading = market
    ? (market.event_title && market.event_title !== market.title ? `${market.event_title}: ${market.title}` : market.title)
    : "Unknown market";
  els.detailSource.textContent = market ? `${market.source} · ${market.category || "uncategorized"}` : "Market";
  els.detailTitle.textContent = heading;
  const latest = snaps[snaps.length - 1] || {};
  const catCls = catPillClass(market?.category);
  els.detailBody.innerHTML = `
    <div class="detail-pills pill-row">
      <span class="pill ${catCls}">${escapeHtml(market?.category || "uncategorized")}</span>
      <span class="pill">Prob ${fmtProb(latest.probability)}</span>
      <span class="pill">Vol ${fmt(latest.volume)}</span>
      <span class="pill">${snaps.length} snapshots</span>
    </div>
    ${market?.url ? `<p style="margin:8px 0 0"><a class="external-link" href="${escapeHtml(market.url)}" target="_blank" rel="noreferrer">↗ Open on ${escapeHtml(market.source)}</a></p>` : ""}
    ${renderChart(snaps)}
    <table class="detail-table">
      <thead><tr><th>Time</th><th>Probability</th><th>Volume</th></tr></thead>
      <tbody>
        ${snaps.slice(-15).reverse().map((s) =>
          `<tr><td>${fmtDate(s.observed_at)}</td><td>${fmtProb(s.probability)}</td><td>${fmt(s.volume)}</td></tr>`
        ).join("")}
      </tbody>
    </table>`;
}

function renderChart(snaps) {
  const W = 460, H = 180;
  const pL = 40, pR = 12, pT = 14, pB = 28;
  const uW = W - pL - pR, uH = H - pT - pB;

  const pts = snaps
    .map((s) => ({ x: new Date(s.observed_at).getTime(), y: Number(s.probability) }))
    .filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y));

  if (pts.length < 2) {
    return `<div class="history-chart empty-state" style="height:80px;padding:20px">Need at least two snapshots to draw a chart.</div>`;
  }

  const minX = Math.min(...pts.map((p) => p.x));
  const maxX = Math.max(...pts.map((p) => p.x));
  const toX = (v) => pL + ((v - minX) / Math.max(1, maxX - minX)) * uW;
  const toY = (v) => pT + (1 - v) * uH;

  const line = pts.map((p, i) => `${i === 0 ? "M" : "L"}${toX(p.x).toFixed(1)} ${toY(p.y).toFixed(1)}`).join(" ");
  const area = `${line} L${toX(pts.at(-1).x).toFixed(1)} ${toY(0).toFixed(1)} L${toX(pts[0].x).toFixed(1)} ${toY(0).toFixed(1)} Z`;

  const gridLines = [0.25, 0.5, 0.75].map((pct) => `
    <line x1="${pL}" y1="${toY(pct).toFixed(1)}" x2="${W - pR}" y2="${toY(pct).toFixed(1)}"
          stroke="#dce0da" stroke-width="1" stroke-dasharray="3 3"/>
    <text x="${pL - 5}" y="${(toY(pct) + 4).toFixed(1)}" font-size="10" fill="#5c6b62" text-anchor="end">${(pct * 100).toFixed(0)}%</text>`
  ).join("");

  // Date labels: first and last
  const dateFmt = (ts) => new Date(ts).toLocaleString([], { month: "short", day: "numeric" });

  return `
    <svg class="history-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Probability history">
      <defs>
        <linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#0d6b5e" stop-opacity="0.18"/>
          <stop offset="100%" stop-color="#0d6b5e" stop-opacity="0.02"/>
        </linearGradient>
      </defs>
      <line x1="${pL}" y1="${pT}" x2="${pL}" y2="${toY(0)}" stroke="#dce0da" stroke-width="1"/>
      <line x1="${pL}" y1="${toY(0)}" x2="${W - pR}" y2="${toY(0)}" stroke="#dce0da" stroke-width="1"/>
      ${gridLines}
      <text x="${pL}" y="${H - 6}" font-size="10" fill="#5c6b62">${escapeHtml(dateFmt(minX))}</text>
      <text x="${W - pR}" y="${H - 6}" font-size="10" fill="#5c6b62" text-anchor="end">${escapeHtml(dateFmt(maxX))}</text>
      <path d="${area}" fill="url(#areaGrad)"/>
      <path d="${line}" fill="none" stroke="#0d6b5e" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
      <circle cx="${toX(pts.at(-1).x).toFixed(1)}" cy="${toY(pts.at(-1).y).toFixed(1)}" r="4" fill="#0d6b5e"/>
    </svg>`;
}

// ── Bind events ────────────────────────────────────────────────────────────

els.refreshBtn.addEventListener("click", refreshData);
els.reloadBtn.addEventListener("click", () => {
  if (state.page === "macro") loadMacroView(state.macroView);
  else reloadAll();
});
els.closeDetailBtn.addEventListener("click", () => { els.detailPanel.hidden = true; });

// Market-level navigation (US / China A-Share)
document.querySelectorAll(".market-nav-btn").forEach(btn => {
  btn.addEventListener("click", () => switchMarket(btn.dataset.market));
});

// Page-level navigation
document.querySelectorAll(".page-nav-btn").forEach(btn => {
  btn.addEventListener("click", () => switchPage(btn.dataset.page));
});

// Macro sub-tabs (Cycle Diagnosis / Market Valuation)
document.querySelectorAll(".view-tab").forEach(btn => {
  btn.addEventListener("click", () => loadMacroView(btn.dataset.view));
});

for (const el of [
  els.viewSelect, els.categorySelect, els.sourceSelect,
  els.lookbackInput, els.probInput, els.limitInput,
  els.maxPerEventInput, els.minDeltaInput, els.allCategoriesInput,
]) {
  el?.addEventListener("change", loadContent);
}

// Initialise on page load
applyPageLayout();
updateControlVisibility();

let searchTimer;
els.searchInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(loadContent, 250);
});

// ══════════════════════════════════════════════════════════════════════════════
// Signal Lab ("Validate" page) — backtest feature conditions on one stock/ETF
// ══════════════════════════════════════════════════════════════════════════════

const SIGLAB_FEATURES = {
  mom12:    { label: "12-mo return %",             markets: ["us", "cn"], def: { op: "gte", value: 30 } },
  ma200:    { label: "Gap vs 200-day MA %",        markets: ["us", "cn"], def: { op: "lte", value: 0 } },
  drawdown: { label: "Drawdown from 52-wk high %", markets: ["us", "cn"], def: { op: "lte", value: -10 } },
  rsi14:    { label: "RSI (14-day)",               markets: ["us", "cn"], def: { op: "lte", value: 30 } },
  pe:       { label: "Trailing P/E",               markets: ["us", "cn"], def: { op: "lte", value: 15 } },
  pb:       { label: "P/B",                        markets: ["cn"],       def: { op: "lte", value: 1 } },
  divyield: { label: "Dividend yield %",           markets: ["us", "cn"], def: { op: "gte", value: 3 } },
  turnover: { label: "Turnover rate %",            markets: ["cn"],       def: { op: "lte", value: 1 } },
};
const SIGLAB_PRESETS = {
  us: [
    { label: "Buy the dip (DD ≤ −10%)", conds: [{ feature: "drawdown", op: "lte", value: -10 }] },
    { label: "Yield ≥ 3%",              conds: [{ feature: "divyield", op: "gte", value: 3 }] },
    { label: "Cheap + trending",        conds: [{ feature: "pe", op: "lte", value: 15 }, { feature: "ma200", op: "gte", value: 0 }] },
  ],
  cn: [
    { label: "破净 + 动量 (PB≤1, Mom≥0)", conds: [{ feature: "pb", op: "lte", value: 1 }, { feature: "mom12", op: "gte", value: 0 }] },
    { label: "股息 ≥ 4%",                conds: [{ feature: "divyield", op: "gte", value: 4 }] },
    { label: "低估值 (PE ≤ 10)",          conds: [{ feature: "pe", op: "lte", value: 10 }] },
  ],
};

function getSiglabCanvas() {
  let c = document.getElementById("siglabCanvas");
  if (!c) { c = document.createElement("div"); c.id = "siglabCanvas"; document.querySelector("main").appendChild(c); }
  return c;
}
function hideSiglab() {
  const c = document.getElementById("siglabCanvas");
  if (c) c.style.display = "none";
  els.workspace.style.display = "";
}

let _siglabBuiltMarket = null;

function loadSiglabPage() {
  const market = state.market;
  const canvas = getSiglabCanvas();
  canvas.style.display = "";
  els.workspace.style.display = "none";
  if (_siglabBuiltMarket === market && canvas.querySelector(".siglab-page")) return;   // preserve state
  _siglabBuiltMarket = market;
  const presets = SIGLAB_PRESETS[market] || [];
  canvas.innerHTML = `
  <div class="siglab-page">
    <div class="filter-header">
      <h2>Validate <span class="filter-market-tag">${market === "cn" ? "🇨🇳 A-Share" : "🇺🇸 US"}</span></h2>
      <p class="filter-sub">Signal Lab — test whether a rule <em>actually worked</em> on one stock or ETF: "when the condition held, what happened next?" All conditions AND together.</p>
    </div>
    <div class="siglab-controls">
      <div class="siglab-row">
        <label class="siglab-lbl">Ticker</label>
        <input id="sl_ticker" type="text" placeholder="${market === "cn" ? "600036.SH" : "KO, SPY…"}" class="siglab-ticker">
        <span class="siglab-presets">${presets.map((p, i) => `<button class="secondary-btn siglab-preset" data-i="${i}" type="button">${escapeHtml(p.label)}</button>`).join("")}</span>
      </div>
      <div id="sl_conds"></div>
      <div class="siglab-row">
        <label class="siglab-lbl">Lookback</label>
        <select id="sl_lookback" class="sl-op" style="width:auto">
          <option value="3">3 yrs</option><option value="5">5 yrs</option><option value="10" selected>10 yrs</option>
        </select>
        <label class="siglab-lbl">Horizons</label>
        <span id="sl_horizons" class="siglab-hz">
          ${["1w","1m","3m","6m","12m","24m","36m"].map(h =>
            `<label class="siglab-hz-item"><input type="checkbox" value="${h}" ${["1m","3m","6m","12m"].includes(h) ? "checked" : ""}>${h}</label>`).join("")}
        </span>
      </div>
      <div class="siglab-row">
        <button id="sl_add" class="secondary-btn" type="button">+ Add condition</button>
        <button id="sl_run" class="primary-btn" type="button">▶ Run test</button>
        <span id="sl_status" class="filter-confirm-status"></span>
      </div>
    </div>
    <div id="sl_results"></div>
  </div>`;
  document.getElementById("sl_add").addEventListener("click", () => _slAddCond(market));
  document.getElementById("sl_run").addEventListener("click", () => runSiglab(market));
  document.getElementById("sl_ticker").addEventListener("keydown", e => { if (e.key === "Enter") runSiglab(market); });
  canvas.querySelectorAll(".siglab-preset").forEach(btn => btn.addEventListener("click", () => {
    document.getElementById("sl_conds").innerHTML = "";
    presets[+btn.dataset.i].conds.forEach(c => _slAddCond(market, c));
  }));
  _slAddCond(market, market === "cn" ? { feature: "pb", op: "lte", value: 1 } : { feature: "drawdown", op: "lte", value: -10 });
}

function _slAddCond(market, preset) {
  const wrap = document.getElementById("sl_conds");
  const feats = Object.entries(SIGLAB_FEATURES).filter(([, f]) => f.markets.includes(market));
  const row = document.createElement("div");
  row.className = "siglab-row siglab-cond";
  const sel = feats.map(([k, f]) => `<option value="${k}">${escapeHtml(f.label)}</option>`).join("");
  row.innerHTML = `
    <select class="sl-feat">${sel}</select>
    <select class="sl-op"><option value="gte">≥</option><option value="lte">≤</option></select>
    <input class="sl-val" type="number" step="any">
    <button class="icon-btn sl-del" type="button" title="Remove">✕</button>`;
  wrap.appendChild(row);
  const featSel = row.querySelector(".sl-feat"), opSel = row.querySelector(".sl-op"), valIn = row.querySelector(".sl-val");
  const applyDef = (k) => { const d = SIGLAB_FEATURES[k].def; opSel.value = d.op; valIn.value = d.value; };
  if (preset) { featSel.value = preset.feature; opSel.value = preset.op; valIn.value = preset.value; }
  else applyDef(featSel.value);
  featSel.addEventListener("change", () => applyDef(featSel.value));
  row.querySelector(".sl-del").addEventListener("click", () => row.remove());
}

async function runSiglab(market) {
  const ticker = document.getElementById("sl_ticker").value.trim();
  const statusEl = document.getElementById("sl_status");
  const resEl = document.getElementById("sl_results");
  const conds = [...document.querySelectorAll("#sl_conds .siglab-cond")].map(r => ({
    feature: r.querySelector(".sl-feat").value,
    op: r.querySelector(".sl-op").value,
    value: +r.querySelector(".sl-val").value,
  }));
  if (!ticker) { statusEl.textContent = "Enter a ticker first."; return; }
  if (!conds.length) { statusEl.textContent = "Add at least one condition."; return; }
  const btn = document.getElementById("sl_run");
  btn.disabled = true; statusEl.textContent = "Fetching history & computing… (~5–15s)";
  resEl.innerHTML = "";
  try {
    const lookback = document.getElementById("sl_lookback")?.value || 10;
    const horizons = [...document.querySelectorAll("#sl_horizons input:checked")].map(c => c.value).join(",");
    const d = await fetchJson(`/api/siglab?market=${market}&ticker=${encodeURIComponent(ticker)}&conds=${encodeURIComponent(JSON.stringify(conds))}&lookback=${lookback}&horizons=${horizons}`);
    if (d.status !== "ready") { statusEl.textContent = `⚠ ${d.error || "failed"}`; return; }
    statusEl.textContent = "";
    renderSiglabResult(d, resEl);
  } catch (err) {
    statusEl.textContent = `⚠ ${err.message}`;
  } finally { btn.disabled = false; }
}

function renderSiglabResult(d, resEl) {
  const fmtPct = (v) => v == null ? "—" : `${v > 0 ? "+" : ""}${v}%`;
  const hrows = (d.horizons || []).map(h => {
    const edgeCls = h.edge >= 1 ? "vg-pos" : h.edge <= -1 ? "vg-neg" : "";
    return `<tr>
      <td>${h.h}</td>
      <td class="filter-td-num">${fmtPct(h.signal_avg)}</td>
      <td class="filter-td-num">${fmtPct(h.onset_avg)}</td>
      <td class="filter-td-num">${fmtPct(h.baseline_avg)}</td>
      <td class="filter-td-num ${edgeCls}"><strong>${fmtPct(h.edge)}</strong></td>
      <td class="filter-td-num">${h.hit_rate}% <span class="filter-count">(vs ${h.base_hit}%)</span></td>
      <td class="filter-td-num">${h.n_onsets}</td>
    </tr>`;
  }).join("");

  // equity curve SVG (strategy vs buy-hold)
  const W = 720, H = 200, P = 8;
  const all = [...(d.strategy || []), ...(d.buyhold || [])];
  const mn = Math.min(...all), mx = Math.max(...all);
  const sc = (v) => H - P - (v - mn) / (mx - mn || 1) * (H - 2 * P);
  const line = (arr) => arr.map((v, i) => `${(P + i / (arr.length - 1) * (W - 2 * P)).toFixed(1)},${sc(v).toFixed(1)}`).join(" ");
  const st = d.strategy_stats, bh = d.buyhold_stats;
  const statFmt = (s) => s ? `CAGR ${fmtPct(s.cagr)} · Sharpe ${s.sharpe ?? "—"} · MaxDD ${s.maxdd}% · Total ${fmtPct(s.total)}` : "—";

  resEl.innerHTML = `
    <div class="siglab-result">
      <div class="siglab-res-head">
        <strong>${escapeHtml(d.ticker)}</strong> · ${escapeHtml((d.conditions || []).join("  AND  "))}
        <span class="filter-count">· ${d.period?.from} → ${d.period?.to} · condition true ${d.exposure_pct}% of days · ${d.n_episodes} episodes</span>
      </div>
      <div class="filter-table-wrap siglab-tbl">
      <table class="filter-table">
        <thead><tr>
          <th>Horizon</th>
          <th title="Average forward return over ALL days the condition was true (overlapping windows)">Signal avg</th>
          <th title="Average forward return measured only at episode onsets — the honest, non-overlapping sample">Onset avg</th>
          <th title="Unconditional average forward return over the same period">Baseline</th>
          <th title="Signal average minus baseline">Edge</th>
          <th title="% of signal days with a positive forward return (vs unconditional)">Hit rate</th>
          <th title="Number of independent episodes with enough forward data">n</th>
        </tr></thead>
        <tbody>${hrows}</tbody>
      </table>
      </div>
      <div class="siglab-chart-box">
        <div class="siglab-chart-title">If you had traded this rule — equity curve vs simply holding</div>
        <details class="siglab-explain"><summary>ⓘ Exactly how this is simulated</summary><div>
          <p><strong>Mechanics:</strong> each day the condition is checked at the close. If true, you are <strong>long from the NEXT day's close</strong> (no look-ahead); when it turns false, you exit to <strong>cash earning 0%</strong>. Both curves start at 1.0 on the same date.</p>
          <p><strong>Ignored on purpose:</strong> trading costs, slippage, taxes, and dividends-while-in-cash. At ${d.n_episodes} round trips over ${d.period?.from}→${d.period?.to} costs are minor but not zero — treat thin edges as noise.</p>
          <p><strong>Why it can lag buy &amp; hold even when the table shows an edge:</strong> the rule is in the market only ${d.exposure_pct}% of days — cash drag. A positive per-episode edge on low exposure often loses the CAGR race but wins on Sharpe/drawdown (risk-adjusted). Decide which you care about: better <em>entries</em> (table) or a better <em>system</em> (curve).</p>
        </div></details>
        <svg viewBox="0 0 ${W} ${H}" class="siglab-svg" preserveAspectRatio="none">
          <polyline points="${line(d.buyhold || [])}" fill="none" stroke="#9aa8a0" stroke-width="1.5"/>
          <polyline points="${line(d.strategy || [])}" fill="none" stroke="var(--accent)" stroke-width="2"/>
        </svg>
        <div class="siglab-legend">
          <span class="siglab-leg-strat">━ Rule: ${statFmt(st)}</span>
          <span class="siglab-leg-bh">━ Buy &amp; hold: ${statFmt(bh)}</span>
        </div>
      </div>
      <details class="filter-defs"><summary>⚠ How to read this honestly</summary><div class="filter-defs-body">
        ${(d.caveats || []).map(c => `<p>· ${escapeHtml(c)}</p>`).join("")}
        <p>· <strong>Onset avg</strong> is the number to trust — it counts each episode once. <strong>Signal avg</strong> counts every day of a long episode, so its sample size is inflated.</p>
        <p>· A rule that's rarely in the market (low exposure) can show a worse total return than buy-and-hold even with a positive per-episode edge — cash drag is real.</p>
      </div></details>
    </div>`;
}

// ══════════════════════════════════════════════════════════════════════════════
// Pipeline — the persistent object flowing through the six-step loop
// (candidate → watch → held → exited). Backed by /api/positions.
// ══════════════════════════════════════════════════════════════════════════════

const POS_STATUSES = ["candidate", "watch", "held", "exited"];
const POS_STATUS_META = {
  candidate: { label: "Candidates", icon: "🔍", hint: "picked by a screen — thesis not yet written" },
  watch:     { label: "Watchlist",  icon: "⏳", hint: "thesis written, waiting for the entry price" },
  held:      { label: "Held",       icon: "📌", hint: "owned — monitor the thesis, not the price" },
  exited:    { label: "Exited",     icon: "🗄", hint: "sold or passed — kept for the decision journal" },
};

async function _addToPipeline(market, ticker, { name, source, snapshot, lynchType } = {}, btn) {
  try {
    // strip nulls & huge fields from the snapshot to keep rows lean
    const snap = {};
    for (const [k, v] of Object.entries(snapshot || {})) {
      if (v != null && (typeof v === "number" || typeof v === "string") && String(v).length < 80) snap[k] = v;
    }
    const url = `/api/positions/add?market=${market}&ticker=${encodeURIComponent(ticker)}`
      + (name ? `&name=${encodeURIComponent(name)}` : "")
      + (source ? `&source=${encodeURIComponent(source)}` : "")
      + (lynchType ? `&lynch_type=${encodeURIComponent(lynchType)}` : "")
      + `&snapshot=${encodeURIComponent(JSON.stringify(snap))}`;
    const d = await fetchJson(url);
    if (d.status !== "ready") throw new Error(d.error || "failed");
    if (btn) { btn.textContent = "✓"; btn.classList.add("pos-added"); btn.title = d.existed ? "Already in Pipeline (snapshot refreshed)" : "Added to Pipeline"; }
    _refreshPipelineNavVisibility();
    return d.position;
  } catch (err) {
    if (btn) { btn.textContent = "!"; btn.title = err.message; }
    return null;
  }
}

// ── deep-dive decision footer: the Step-1 exit ritual ─────────────────────────
function _suggestTarget(d) {
  try {
    const dcf = d.dcf;
    if (dcf && dcf.applicable && dcf.shares && dcf.base_fcf > 0) {
      const iv = _dcfEquityValue(dcf.base_fcf, dcf.default_stage1_growth, dcf.default_fade_years,
                                 dcf.default_terminal, dcf.cost_of_equity) / dcf.shares;
      if (iv > 0) return { price: Math.round(iv * 0.8 * 100) / 100, basis: "80% of DCF intrinsic value" };
    }
  } catch (e) { /* fall through */ }
  const g = d.fundamental_scores?.graham;
  if (g && g.number) return { price: Math.round(g.number * 0.8 * 100) / 100, basis: "80% of Graham Number" };
  return { price: null, basis: null };
}

function renderDecisionFooter(d) {
  const tgt = _suggestTarget(d);
  const flags = window._lastRedFlags || [];
  return `
  <div class="sc-card decision-card" id="decisionCard">
    <div class="sc-card-header"><div class="sc-card-title">Decision — close the pick in writing</div>
      <span id="dfStatusChip" class="df-chip"></span></div>
    <div class="df-grid">
      <label class="df-lbl">Thesis — why own it, in ≤5 falsifiable sentences (Lynch: "know why you own it")</label>
      <textarea id="dfThesis" rows="3" placeholder="e.g. Regional leader compounding EPS ~15%/yr; PEG below 1; net cash; insiders buying. Breaks if growth < 8% or margins roll over."></textarea>
      <div class="df-row">
        <label class="df-lbl-inline">Target entry ≤</label>
        <input id="dfTarget" type="number" step="any" value="${tgt.price ?? ""}" class="df-target">
        <span class="filter-count" id="dfBasis">${tgt.basis ? "suggested: " + tgt.basis : "no DCF/Graham value available — set your own"}</span>
      </div>
      <label class="df-lbl">Disqualifiers — what would kill the thesis (prefilled from red flags)</label>
      <textarea id="dfDisq" rows="2">${flags.map(f => "· " + f).join("\n")}</textarea>
      <div class="df-row">
        <button id="dfSaveCand" class="secondary-btn" type="button">Save as Candidate</button>
        <button id="dfSaveWatch" class="primary-btn" type="button">Save &amp; move to Watchlist</button>
        <span id="dfStatus" class="filter-confirm-status"></span>
      </div>
    </div>
  </div>`;
}

function setupDecisionFooter(container, d) {
  const card = container.querySelector("#decisionCard");
  if (!card) return;
  const market = state.market;
  const ticker = d.ticker;
  const statusEl = card.querySelector("#dfStatus");
  const chip = card.querySelector("#dfStatusChip");

  // show existing pipeline record if the name is already tracked
  fetchJson(`/api/positions?market=${market}`).then(res => {
    const p = (res.positions || []).find(x => x.ticker === ticker);
    if (!p) return;
    chip.textContent = `${POS_STATUS_META[p.status]?.icon || ""} in Pipeline: ${p.status}`;
    chip.classList.add("df-chip-on");
    if (p.thesis) card.querySelector("#dfThesis").value = p.thesis;
    if (p.target_price != null) card.querySelector("#dfTarget").value = p.target_price;
    if (Array.isArray(p.disqualifiers)) card.querySelector("#dfDisq").value = p.disqualifiers.join("\n");
    else if (p.disqualifiers) card.querySelector("#dfDisq").value = p.disqualifiers;
    card.dataset.pid = p.id;
  }).catch(() => {});

  async function save(toWatch) {
    statusEl.textContent = "Saving…";
    const thesis = card.querySelector("#dfThesis").value.trim();
    const target = card.querySelector("#dfTarget").value;
    const disq = card.querySelector("#dfDisq").value.trim();
    if (toWatch && !thesis) { statusEl.textContent = "⚠ Watchlist requires a written thesis — that's the point."; return; }
    if (toWatch && !target) { statusEl.textContent = "⚠ Watchlist requires a target entry price."; return; }
    const pos = await _addToPipeline(market, ticker, {
      name: d.name, source: "deepdive",
      lynchType: d.lynch_data?.type || null,
      snapshot: { price: d.price, pe: d.pe, pb: d.pb, market_cap: d.market_cap },
    });
    if (!pos) { statusEl.textContent = "⚠ Could not save."; return; }
    const fields = { thesis, disqualifiers: disq.split("\n").filter(Boolean) };
    if (target) { fields.target_price = +target; fields.target_basis = card.querySelector("#dfBasis").textContent.replace("suggested: ", ""); }
    if (toWatch) fields.status = "watch";
    const r = await fetchJson(`/api/positions/update?id=${pos.id}&fields=${encodeURIComponent(JSON.stringify(fields))}`);
    if (r.status === "ready") {
      statusEl.textContent = toWatch ? "✓ Saved → Watchlist (see Pipeline tab)" : "✓ Saved as candidate";
      chip.textContent = `${POS_STATUS_META[r.position.status].icon} in Pipeline: ${r.position.status}`;
      chip.classList.add("df-chip-on");
    } else statusEl.textContent = `⚠ ${r.error || "failed"}`;
  }
  card.querySelector("#dfSaveCand").addEventListener("click", () => save(false));
  card.querySelector("#dfSaveWatch").addEventListener("click", () => save(true));
}

// ── Pipeline page ──────────────────────────────────────────────────────────────
function getPipelineCanvas() {
  let c = document.getElementById("pipelineCanvas");
  if (!c) { c = document.createElement("div"); c.id = "pipelineCanvas"; document.querySelector("main").appendChild(c); }
  return c;
}
function hidePipeline() {
  const c = document.getElementById("pipelineCanvas");
  if (c) c.style.display = "none";
  els.workspace.style.display = "";
}

async function loadPipelinePage() {
  const market = state.market;
  const canvas = getPipelineCanvas();
  canvas.style.display = "";
  els.workspace.style.display = "none";
  canvas.innerHTML = `<div class="pipeline-page"><div class="filter-header">
    <h2>Pipeline <span class="filter-market-tag">${market === "cn" ? "🇨🇳 A-Share" : "🇺🇸 US"}</span></h2>
    <p class="filter-sub">The object that flows through the loop: <strong>candidate → watch → held → exited</strong>. Names arrive from Filter/Quant Rank (＋) or the deep-dive decision card.</p>
    </div>
    <div id="pipelineDeployBanner"></div>
    <div id="pipelineBody">Loading…</div></div>`;
  if (market === "us") loadPipelineDeployBanner();
  try {
    const d = await fetchJson(`/api/positions?market=${market}`);
    renderPipeline(d.positions || [], market);
  } catch (err) {
    document.getElementById("pipelineBody").innerHTML = `<div class="filter-empty">⚠ ${escapeHtml(err.message)}</div>`;
  }
}

// Suggested-action line: the Deploy cash-posture signal lives here (Track/Decide),
// not in Macro (Context) — it's a synthesis of Cycle+Valuation+Sentiment, not a
// fourth peer lens, so it surfaces where you actually act on positions.
async function loadPipelineDeployBanner() {
  const box = document.getElementById("pipelineDeployBanner");
  if (!box) return;
  try {
    const d = await fetchJson("/api/deployment");
    const overall = d.overall || {};
    const colorClass = ({ positive: "dp-green", warning: "dp-amber", caution: "dp-yellow",
      danger: "dp-red", neutral: "dp-neutral" }[overall.color] || "dp-neutral");
    box.innerHTML = `
      <div class="pipe-deploy-line ${colorClass}">
        <span class="pipe-deploy-label">💰 Cash deployment posture</span>
        <strong class="pipe-deploy-headline">${escapeHtml(overall.headline || "—")}</strong>
        <span class="pipe-deploy-badge">${escapeHtml(overall.label || "—")}</span>
        <details class="pipe-deploy-detail"><summary>full breakdown</summary><div id="pipelineDeployFull"></div></details>
      </div>`;
    renderDeployment(d, document.getElementById("pipelineDeployFull"));
  } catch {
    box.innerHTML = "";
  }
}

function renderPipeline(positions, market) {
  const body = document.getElementById("pipelineBody");
  if (!positions.length) {
    body.innerHTML = `<div class="filter-empty">Nothing in the pipeline yet. Add names with the <strong>＋</strong> button on Filter / Quant Rank rows, or via the Decision card at the bottom of any deep-dive.</div>`;
    return;
  }
  const groups = POS_STATUSES.map(st => {
    const rows = positions.filter(p => p.status === st);
    if (!rows.length) return "";
    const meta = POS_STATUS_META[st];
    const trs = rows.map(p => {
      const snap = p.snapshot || {};
      const facts = ["pe", "pb", "dcf_mos", "graham_mos", "f_score"].filter(k => snap[k] != null)
        .map(k => `${k.replace("_", " ").toUpperCase()} ${snap[k]}`).join(" · ");
      return `
      <tr class="pipe-row" data-id="${p.id}">
        <td class="filter-td-sym pipe-sym" data-t="${escapeHtml(p.ticker)}">${escapeHtml(p.ticker)}</td>
        <td class="filter-td-name">${escapeHtml(p.name || "")}</td>
        <td>${p.lynch_type ? `<span class="pipe-lynch">${escapeHtml(p.lynch_type)}</span>` : "—"}</td>
        <td class="filter-td-num">${p.target_price != null ? p.target_price : "—"}</td>
        <td class="pipe-thesis">${p.thesis ? `<details><summary>${escapeHtml(p.thesis.slice(0, 60))}${p.thesis.length > 60 ? "…" : ""}</summary><div class="pipe-thesis-full">${escapeHtml(p.thesis)}${Array.isArray(p.disqualifiers) && p.disqualifiers.length ? `<div class="pipe-disq"><strong>Disqualifiers:</strong><br>${p.disqualifiers.map(escapeHtml).join("<br>")}</div>` : ""}</div></details>` : `<span class="filter-count">no thesis yet</span>`}</td>
        <td class="filter-count">${(p.added_at || "").slice(0, 10)}${p.source ? " · " + escapeHtml(p.source) : ""}</td>
        <td><select class="pipe-status">${POS_STATUSES.map(s => `<option value="${s}" ${s === p.status ? "selected" : ""}>${s}</option>`).join("")}</select></td>
        <td><button class="icon-btn pipe-del" title="Remove" type="button">✕</button></td>
      </tr>`;
    }).join("");
    return `
    <div class="pipe-group">
      <div class="pipe-group-head">${meta.icon} <strong>${meta.label}</strong> <span class="filter-count">(${rows.length}) — ${meta.hint}</span></div>
      <div class="filter-table-wrap pipe-tbl"><table class="filter-table">
        <thead><tr><th>Ticker</th><th>Name</th><th>Lynch type</th><th>Target ≤</th><th>Thesis</th><th>Added</th><th>Status</th><th></th></tr></thead>
        <tbody>${trs}</tbody></table></div>
    </div>`;
  }).join("");
  body.innerHTML = groups;

  body.querySelectorAll(".pipe-sym").forEach(el =>
    el.addEventListener("click", () => openFullScreen(el.dataset.t, market)));
  body.querySelectorAll(".pipe-status").forEach(sel =>
    sel.addEventListener("change", async () => {
      const id = sel.closest(".pipe-row").dataset.id;
      await fetchJson(`/api/positions/update?id=${id}&fields=${encodeURIComponent(JSON.stringify({ status: sel.value }))}`);
      loadPipelinePage();
    }));
  body.querySelectorAll(".pipe-del").forEach(btn =>
    btn.addEventListener("click", async () => {
      const id = btn.closest(".pipe-row").dataset.id;
      await fetchJson(`/api/positions/delete?id=${id}`);
      _refreshPipelineNavVisibility();
      loadPipelinePage();
    }));
}

// ══════════════════════════════════════════════════════════════════════════════
// Portfolio Construction — manual add / CSV upload, weighting, live aggregates
// ══════════════════════════════════════════════════════════════════════════════

const PF_KEY = "portfolio_v1";
function pfLoad() {
  try { return JSON.parse(localStorage.getItem(PF_KEY)) || {}; } catch { return {}; }
}
function pfState(market) {
  const all = pfLoad();
  return all[market] || { tickers: [], weights: {}, mode: "equal", invest: null };
}
function pfSave(market, st) {
  const all = pfLoad(); all[market] = st; localStorage.setItem(PF_KEY, JSON.stringify(all));
}
let _pfQuotes = {};   // market → { ticker → quote }

// ── named saved portfolios (separate from the live working portfolio) ─────────
const PF_SAVED_KEY = "portfolio_saved_v1";
function pfSavedAll() { try { return JSON.parse(localStorage.getItem(PF_SAVED_KEY)) || {}; } catch { return {}; } }
function pfSavedList(market) { return pfSavedAll()[market] || []; }
function pfSavedWrite(market, list) { const all = pfSavedAll(); all[market] = list; localStorage.setItem(PF_SAVED_KEY, JSON.stringify(all)); }

function pfSaveSnapshot(market) {
  const st = pfState(market);
  if (!st.tickers.length) { document.getElementById("pfStatus").textContent = "Nothing to save — add holdings first."; return; }
  const name = (prompt("Save portfolio as:", `Portfolio ${new Date().toISOString().slice(0, 10)}`) || "").trim();
  if (!name) return;
  const list = pfSavedList(market).filter(p => p.name !== name);   // overwrite same-name
  list.push({ name, savedAt: new Date().toISOString(), tickers: [...st.tickers], weights: { ...st.weights }, mode: st.mode, invest: st.invest });
  pfSavedWrite(market, list.sort((a, b) => a.name.localeCompare(b.name)));
  document.getElementById("pfStatus").textContent = `✓ Saved "${name}" (${st.tickers.length} holdings).`;
  pfRenderSavedSelect(market);
}
function pfLoadSnapshot(market, name) {
  const p = pfSavedList(market).find(x => x.name === name);
  if (!p) return;
  const cur = pfState(market);
  if (cur.tickers.length && !confirm(`Replace the current working portfolio with "${name}" (${p.tickers.length} holdings)?`)) { pfRenderSavedSelect(market); return; }
  pfSave(market, { tickers: [...p.tickers], weights: { ...(p.weights || {}) }, mode: p.mode || "equal", invest: p.invest ?? null });
  document.getElementById("pfInvest").value = p.invest ?? "";
  document.getElementById("pfStatus").textContent = `Loaded "${name}".`;
  pfRefresh(market);
}
function pfDeleteSnapshot(market, name) {
  if (!name || !confirm(`Delete saved portfolio "${name}"?`)) return;
  pfSavedWrite(market, pfSavedList(market).filter(p => p.name !== name));
  document.getElementById("pfStatus").textContent = `Deleted "${name}".`;
  pfRenderSavedSelect(market);
}
function pfRenderSavedSelect(market) {
  const sel = document.getElementById("pfSavedSelect");
  if (!sel) return;
  const list = pfSavedList(market);
  sel.innerHTML = `<option value="">${list.length ? `Load saved (${list.length})…` : "No saved portfolios"}</option>`
    + list.map(p => `<option value="${escapeHtml(p.name)}">${escapeHtml(p.name)} · ${p.tickers.length} · ${(p.savedAt || "").slice(0, 10)}</option>`).join("");
}

function getPortfolioCanvas() {
  let c = document.getElementById("portfolioCanvas");
  if (!c) { c = document.createElement("div"); c.id = "portfolioCanvas"; document.querySelector("main").appendChild(c); }
  return c;
}
function hidePortfolio() {
  const c = document.getElementById("portfolioCanvas");
  if (c) c.style.display = "none";
  els.workspace.style.display = "";
}

function loadPortfolioPage() {
  const market = state.market;
  const canvas = getPortfolioCanvas();
  canvas.style.display = "";
  els.workspace.style.display = "none";
  const cur = market === "cn" ? "¥" : "$";
  canvas.innerHTML = `
  <div class="pf-page">
    <div class="filter-header">
      <h2>Portfolio <span class="filter-market-tag">${market === "cn" ? "🇨🇳 A-Share" : "🇺🇸 US"}</span></h2>
      <p class="filter-sub">Build a portfolio by hand or from a CSV (e.g. Alpha Picks). Default weighting is equal — switch to market-cap or set custom weights.</p>
    </div>
    <div class="pf-controls">
      <div class="pf-ctrl-row">
        <div class="sc-watchlist-add-wrap">
          <input id="pfAdd" class="sc-watchlist-add-input" type="text" placeholder="${market === "cn" ? "600036.SH" : "Add ticker…"}" maxlength="12" spellcheck="false" autocomplete="off">
          <button id="pfAddBtn" class="sc-watchlist-add-btn">+</button>
        </div>
        <label class="pf-upload secondary-btn" for="pfCsv">⬆ Upload CSV</label>
        <input id="pfCsv" type="file" accept=".csv,text/csv" hidden>
        <span class="pf-weight-modes">
          <span class="filter-count">Weighting:</span>
          ${["equal", "mktcap", "custom"].map(m => `<button class="pf-mode-btn" data-mode="${m}">${{ equal: "Equal", mktcap: "Market cap", custom: "Custom" }[m]}</button>`).join("")}
          <button id="pfNormalize" class="pf-mode-btn" title="Scale custom weights to sum to 100%">Normalize</button>
        </span>
        <span class="sc-watchlist-add-wrap pf-invest-wrap">
          <span class="sc-watchlist-add-btn pf-invest-label">${cur}</span>
          <input id="pfInvest" class="sc-watchlist-add-input pf-invest-input" type="number" placeholder="invest amount" min="0" step="any">
        </span>
        <span class="pf-saved-group">
          <button id="pfSaveBtn" class="secondary-btn" title="Save the current portfolio under a name">💾 Save</button>
          <select id="pfSavedSelect" class="pf-saved-select"></select>
          <button id="pfSavedDel" class="icon-btn" title="Delete selected saved portfolio">✕</button>
        </span>
        <span class="pf-saved-group">
          <button id="pfBacktestBtn" class="secondary-btn" title="Backtest, correlation & ETF-overlap analysis">📊 Analyze</button>
          <select id="pfBtYears" class="pf-saved-select" style="max-width:88px">
            <option value="1">1y</option><option value="3">3y</option><option value="5" selected>5y</option><option value="10">10y</option>
          </select>
        </span>
        <button id="pfClear" class="secondary-btn pf-clear">Clear</button>
        <button id="pfExport" class="secondary-btn">⬇ Export CSV</button>
        <span id="pfStatus" class="filter-confirm-status"></span>
      </div>
    </div>
    <div id="pfSummary"></div>
    <div id="pfBacktest"></div>
    <div id="pfBody">Loading…</div>
  </div>`;

  const st = pfState(market);
  document.getElementById("pfInvest").value = st.invest ?? "";
  document.getElementById("pfAddBtn").addEventListener("click", () => pfAdd(market));
  document.getElementById("pfAdd").addEventListener("keydown", e => { if (e.key === "Enter") pfAdd(market); });
  document.getElementById("pfCsv").addEventListener("change", e => pfUploadCsv(market, e.target.files[0]));
  document.getElementById("pfClear").addEventListener("click", () => {
    if (!pfState(market).tickers.length || confirm("Clear the whole portfolio?")) { pfSave(market, { tickers: [], weights: {}, mode: "equal", invest: null }); pfRefresh(market); }
  });
  document.getElementById("pfExport").addEventListener("click", () => pfExport(market));
  document.getElementById("pfBacktestBtn").addEventListener("click", () => pfBacktest(market));
  document.getElementById("pfSaveBtn").addEventListener("click", () => pfSaveSnapshot(market));
  document.getElementById("pfSavedSelect").addEventListener("change", e => { if (e.target.value) pfLoadSnapshot(market, e.target.value); });
  document.getElementById("pfSavedDel").addEventListener("click", () => pfDeleteSnapshot(market, document.getElementById("pfSavedSelect").value));
  pfRenderSavedSelect(market);
  document.getElementById("pfNormalize").addEventListener("click", () => pfNormalize(market));
  document.getElementById("pfInvest").addEventListener("input", e => {
    const s = pfState(market); s.invest = e.target.value ? +e.target.value : null; pfSave(market, s); pfRenderTable(market);
  });
  canvas.querySelectorAll(".pf-mode-btn[data-mode]").forEach(btn =>
    btn.addEventListener("click", () => { const s = pfState(market); s.mode = btn.dataset.mode; pfSave(market, s); pfRenderTable(market); }));

  pfRefresh(market);
}

function pfAdd(market, raw) {
  const input = document.getElementById("pfAdd");
  const val = (raw ?? input.value).trim().toUpperCase().replace(/[^A-Z0-9.]/g, "");
  if (input && !raw) input.value = "";
  if (!val) return;
  const st = pfState(market);
  if (!st.tickers.includes(val)) { st.tickers.push(val); pfSave(market, st); pfRefresh(market); }
}

function pfUploadCsv(market, file) {
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    const text = String(reader.result || "");
    const lines = text.split(/\r?\n/).filter(l => l.trim());
    if (!lines.length) return;
    const split = (l) => l.split(",").map(c => c.trim().replace(/^"|"$/g, ""));
    const header = split(lines[0]).map(h => h.toLowerCase());
    let col = header.findIndex(h => /^(symbol|ticker|ticker symbol|code)$/.test(h));
    let start = 1;
    if (col < 0) { col = 0; start = /^[A-Za-z.]{1,8}$/.test(split(lines[0])[0]) ? 0 : 1; }  // headerless → first col
    const found = [];
    for (let i = start; i < lines.length; i++) {
      const cell = (split(lines[i])[col] || "").toUpperCase().replace(/[^A-Z0-9.]/g, "");
      if (cell && /^[A-Z]/.test(cell) && cell.length <= 10) found.push(cell);
    }
    const st = pfState(market);
    let added = 0;
    found.forEach(t => { if (!st.tickers.includes(t)) { st.tickers.push(t); added++; } });
    pfSave(market, st);
    document.getElementById("pfStatus").textContent = `Imported ${added} new ticker${added === 1 ? "" : "s"} from ${file.name} (${found.length} in file).`;
    document.getElementById("pfCsv").value = "";
    pfRefresh(market);
  };
  reader.readAsText(file);
}

async function pfRefresh(market) {
  const st = pfState(market);
  // sync active mode button
  document.querySelectorAll(".pf-mode-btn[data-mode]").forEach(b => b.classList.toggle("pf-mode-active", b.dataset.mode === st.mode));
  if (!st.tickers.length) {
    document.getElementById("pfSummary").innerHTML = "";
    document.getElementById("pfBody").innerHTML = `<div class="filter-empty">Empty portfolio. Add tickers above or upload a CSV (the <strong>Symbol</strong> column is auto-detected).</div>`;
    return;
  }
  document.getElementById("pfBody").innerHTML = `<div class="filter-empty">Fetching quotes for ${st.tickers.length} holdings…</div>`;
  try {
    const d = await fetchJson(`/api/portfolio/quote?market=${market}&tickers=${encodeURIComponent(st.tickers.join(","))}`);
    _pfQuotes[market] = {};
    (d.holdings || []).forEach(h => { _pfQuotes[market][h.symbol] = h; });
    if (d.unresolved && d.unresolved.length)
      document.getElementById("pfStatus").textContent = `⚠ Couldn't resolve: ${d.unresolved.join(", ")}`;
    pfRenderTable(market);
  } catch (err) {
    document.getElementById("pfBody").innerHTML = `<div class="filter-empty">⚠ ${escapeHtml(err.message)}</div>`;
  }
}

// weight vector for the current mode → { ticker: fraction (0..1) }
function pfWeights(market) {
  const st = pfState(market);
  const q = _pfQuotes[market] || {};
  const ts = st.tickers;
  if (st.mode === "mktcap") {
    const caps = ts.map(t => q[t]?.market_cap || 0);
    const tot = caps.reduce((a, b) => a + b, 0);
    if (tot > 0) return Object.fromEntries(ts.map((t, i) => [t, caps[i] / tot]));
  } else if (st.mode === "custom") {
    const w = ts.map(t => +(st.weights[t] ?? 0));
    const tot = w.reduce((a, b) => a + b, 0);
    if (tot > 0) return Object.fromEntries(ts.map((t, i) => [t, w[i] / tot]));
  }
  return Object.fromEntries(ts.map(t => [t, 1 / ts.length]));   // equal (and fallback)
}

function pfRenderTable(market) {
  const st = pfState(market);
  const q = _pfQuotes[market] || {};
  const cur = market === "cn" ? "¥" : "$";
  const w = pfWeights(market);
  const invest = st.invest;
  const mcap = (v) => v == null ? "—" : v >= 1e12 ? `${cur}${(v / 1e12).toFixed(2)}T` : v >= 1e9 ? `${cur}${(v / 1e9).toFixed(1)}B` : v >= 1e6 ? `${cur}${(v / 1e6).toFixed(0)}M` : "—";
  const num = (v, s = "", d = 1) => v == null ? "—" : `${(+v).toFixed(d)}${s}`;
  const editable = st.mode === "custom";

  const rows = st.tickers.map(t => {
    const h = q[t] || { symbol: t, name: t };
    const wt = w[t] || 0;
    const alloc = invest ? invest * wt : null;
    const shares = (invest && h.price) ? alloc / h.price : null;
    const wtCell = editable
      ? `<input class="pf-wt-input" data-t="${escapeHtml(t)}" type="number" min="0" step="any" value="${st.weights[t] ?? ""}" placeholder="${(wt * 100).toFixed(1)}">`
      : `${(wt * 100).toFixed(1)}%`;
    return `<tr class="pf-row" data-t="${escapeHtml(t)}">
      <td class="filter-td-sym pf-sym" data-t="${escapeHtml(t)}">${escapeHtml(t)}</td>
      <td class="filter-td-name">${escapeHtml(h.name || "")}</td>
      <td class="filter-td-ind">${escapeHtml(h.sector || "—")}</td>
      <td class="filter-td-num">${h.price == null ? "—" : cur + num(h.price, "", 2)}</td>
      <td class="filter-td-num">${num(h.pe)}</td>
      <td class="filter-td-num">${num(h.peg, "", 2)}</td>
      <td class="filter-td-num">${h.div_yield == null ? "—" : num(h.div_yield, "%", 2)}</td>
      <td class="filter-td-num">${mcap(h.market_cap)}</td>
      <td class="filter-td-num pf-wt">${wtCell}</td>
      <td class="filter-td-num">${alloc == null ? "—" : cur + Math.round(alloc).toLocaleString()}</td>
      <td class="filter-td-num">${shares == null ? "—" : (shares >= 100 ? Math.round(shares) : shares.toFixed(1))}</td>
      <td><button class="icon-btn pf-del" data-t="${escapeHtml(t)}" title="Remove">✕</button></td>
    </tr>`;
  }).join("");

  document.getElementById("pfBody").innerHTML = `
    <div class="filter-table-wrap pf-tbl"><table class="filter-table">
      <thead><tr>
        <th>Ticker</th><th>Name</th><th>Sector</th><th>Price</th><th>P/E</th><th>PEG</th><th>Div</th><th>Mkt Cap</th>
        <th title="Portfolio weight">Weight</th><th>$ Alloc</th><th>Shares</th><th></th>
      </tr></thead><tbody>${rows}</tbody>
    </table></div>
    <div class="filter-hint">${editable ? "Enter raw weights (any scale) and click <strong>Normalize</strong> to rescale to 100%. " : ""}Click a ticker to open its deep-dive. Set an invest amount to see $ allocation &amp; share counts.</div>`;

  pfRenderSummary(market, w);

  document.querySelectorAll(".pf-del").forEach(b => b.addEventListener("click", () => {
    const s = pfState(market); s.tickers = s.tickers.filter(x => x !== b.dataset.t); delete s.weights[b.dataset.t];
    pfSave(market, s); pfRefresh(market);
  }));
  document.querySelectorAll(".pf-sym").forEach(el => el.addEventListener("click", () => openFullScreen(el.dataset.t, market)));
  document.querySelectorAll(".pf-wt-input").forEach(inp => inp.addEventListener("input", () => {
    const s = pfState(market); s.weights[inp.dataset.t] = inp.value ? +inp.value : 0; pfSave(market, s);
    pfRenderSummary(market, pfWeights(market));
  }));
}

function pfRenderSummary(market, w) {
  const st = pfState(market);
  const q = _pfQuotes[market] || {};
  // weighted aggregates (re-normalize over holdings that have the metric)
  const wAvg = (key) => {
    let num = 0, den = 0;
    st.tickers.forEach(t => { const v = q[t]?.[key]; if (v != null && w[t]) { num += v * w[t]; den += w[t]; } });
    return den > 0 ? num / den : null;
  };
  const wpe = wAvg("pe"), wpeg = wAvg("peg"), wdy = wAvg("div_yield");
  const totalW = st.tickers.reduce((a, t) => a + (w[t] || 0), 0);

  // sector allocation
  const sec = {};
  st.tickers.forEach(t => { const s = q[t]?.sector || "—"; sec[s] = (sec[s] || 0) + (w[t] || 0); });
  const secRows = Object.entries(sec).sort((a, b) => b[1] - a[1]).map(([s, v]) =>
    `<div class="pf-sec-row"><span class="pf-sec-name">${escapeHtml(s)}</span>
      <span class="pf-sec-bar"><span class="pf-sec-fill" style="width:${(v * 100).toFixed(1)}%"></span></span>
      <span class="pf-sec-pct">${(v * 100).toFixed(1)}%</span></div>`).join("");

  const stat = (label, val, hint) => `<div class="pf-stat"><span class="pf-stat-label">${label}</span><strong class="pf-stat-val">${val}</strong>${hint ? `<span class="pf-stat-hint">${hint}</span>` : ""}</div>`;
  document.getElementById("pfSummary").innerHTML = `
    <div class="pf-summary">
      <div class="pf-stats">
        ${stat("Holdings", st.tickers.length)}
        ${stat("Weight sum", (totalW * 100).toFixed(1) + "%", Math.abs(totalW - 1) > 0.01 ? "⚠ not 100%" : "✓")}
        ${stat("Weighted P/E", wpe == null ? "—" : wpe.toFixed(1))}
        ${stat("Weighted PEG", wpeg == null ? "—" : wpeg.toFixed(2))}
        ${stat("Weighted yield", wdy == null ? "—" : wdy.toFixed(2) + "%")}
      </div>
      <div class="pf-sectors"><div class="pf-sec-title">Sector allocation</div>${secRows}</div>
    </div>`;
}

function pfNormalize(market) {
  const st = pfState(market);
  if (st.mode !== "custom") { document.getElementById("pfStatus").textContent = "Switch to Custom weighting first."; return; }
  const w = pfWeights(market);   // already normalized fractions
  st.weights = Object.fromEntries(st.tickers.map(t => [t, +(w[t] * 100).toFixed(2)]));
  pfSave(market, st); pfRenderTable(market);
}

function pfExport(market) {
  const st = pfState(market);
  const q = _pfQuotes[market] || {};
  const w = pfWeights(market);
  const head = ["Symbol", "Name", "Sector", "Price", "PE", "PEG", "DivYield", "MarketCap", "Weight%", "Alloc", "Shares"];
  const lines = [head.join(",")];
  st.tickers.forEach(t => {
    const h = q[t] || {}; const wt = w[t] || 0;
    const alloc = st.invest ? st.invest * wt : "";
    const shares = (st.invest && h.price) ? (st.invest * wt / h.price).toFixed(2) : "";
    lines.push([t, `"${(h.name || "").replace(/"/g, "")}"`, `"${h.sector || ""}"`, h.price ?? "", h.pe ?? "", h.peg ?? "", h.div_yield ?? "", h.market_cap ?? "", (wt * 100).toFixed(2), alloc ? Math.round(alloc) : "", shares].join(","));
  });
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = `portfolio_${market}_${new Date().toISOString().slice(0, 10)}.csv`;
  a.click(); URL.revokeObjectURL(a.href);
}

async function pfBacktest(market) {
  const st = pfState(market);
  if (!st.tickers.length) { document.getElementById("pfStatus").textContent = "Add holdings first."; return; }
  const w = pfWeights(market);
  const holdings = st.tickers.map(t => ({ symbol: t, weight: w[t] || 0 }));
  const years = document.getElementById("pfBtYears").value;
  const box = document.getElementById("pfBacktest");
  box.innerHTML = `<div class="pf-bt-card"><div class="filter-empty">Backtesting ${st.tickers.length} holdings over ${years}y… (fetching price history)</div></div>`;
  try {
    const d = await fetchJson(`/api/portfolio/backtest?market=${market}&years=${years}&holdings=${encodeURIComponent(JSON.stringify(holdings))}`);
    if (d.status !== "ready") { box.innerHTML = `<div class="pf-bt-card"><div class="filter-empty">⚠ ${escapeHtml(d.error || "failed")}</div></div>`; return; }
    pfRenderBacktest(market, d);
    // overlap runs in parallel (needs ETF holdings; renders into its own sub-card)
    const ov = document.getElementById("pfOverlap");
    if (ov) ov.innerHTML = `<div class="pf-bt-card"><div class="filter-empty">Checking stock ↔ ETF overlap…</div></div>`;
    fetchJson(`/api/portfolio/overlap?market=${market}&tickers=${encodeURIComponent(st.tickers.join(","))}`)
      .then(o => pfRenderOverlap(o)).catch(() => { if (ov) ov.innerHTML = ""; });
  } catch (err) {
    box.innerHTML = `<div class="pf-bt-card"><div class="filter-empty">⚠ ${escapeHtml(err.message)}</div></div>`;
  }
}

function _corrColor(c) {
  if (c == null) return "transparent";
  if (c >= 0) return `rgba(198,64,44,${(Math.min(c, 1) * 0.72).toFixed(2)})`;   // positive → red (concentrated)
  return `rgba(13,107,94,${(Math.min(-c, 1) * 0.72).toFixed(2)})`;               // negative → green (diversifying)
}
function pfRenderCorr(corr) {
  if (!corr || !corr.tickers || corr.tickers.length < 2) return "";
  const ts = corr.tickers, m = corr.matrix;
  const head = `<tr><th></th>${ts.map(t => `<th class="pf-corr-h">${escapeHtml(t)}</th>`).join("")}</tr>`;
  const body = ts.map((t, i) => `<tr><th class="pf-corr-h pf-corr-rh">${escapeHtml(t)}</th>${
    ts.map((_, j) => {
      const c = m[i][j];
      const diag = i === j;
      return `<td class="pf-corr-c${diag ? " pf-corr-diag" : ""}" style="${diag ? "" : `background:${_corrColor(c)}`}" title="${escapeHtml(ts[i])} · ${escapeHtml(ts[j])}: ${c}">${diag ? "" : c.toFixed(2)}</td>`;
    }).join("")}</tr>`).join("");
  const avg = corr.avg_corr;
  const avgLabel = avg == null ? "—" : avg.toFixed(2) + (avg < 0.3 ? " · well diversified" : avg < 0.6 ? " · moderate" : " · highly correlated");
  return `
    <div class="pf-corr-wrap">
      <div class="pf-bt-head"><strong>Return correlation</strong> <span class="filter-count">daily returns over the window · avg pairwise <strong>${avgLabel}</strong> · <span class="pf-corr-key pf-corr-key-r">red = move together</span> · <span class="pf-corr-key pf-corr-key-g">green = diversifying</span></span></div>
      <div class="pf-corr-scroll"><table class="pf-corr-tbl">${head}${body}</table></div>
    </div>`;
}
function pfRenderOverlap(o) {
  const box = document.getElementById("pfOverlap");
  if (!box) return;
  if (!o || o.status !== "ready") { box.innerHTML = ""; return; }
  if (o.note && o.note.includes("US-only")) { box.innerHTML = ""; return; }
  if (!o.etfs || !o.etfs.length) {
    box.innerHTML = `<div class="pf-bt-card pf-ov-clean">✓ No ETFs in the portfolio — no stock/fund overlap to worry about.</div>`;
    return;
  }
  // group stock-in-etf by stock
  const byStock = {};
  (o.stock_in_etf || []).forEach(x => { (byStock[x.stock] ||= []).push(x); });
  const stockRows = Object.entries(byStock).map(([s, arr]) =>
    `<div class="pf-ov-item"><span class="pf-ov-warn">⚠</span> You hold <strong>${escapeHtml(s)}</strong> directly <em>and</em> inside ${arr.map(x => `${escapeHtml(x.etf)} (${x.weight_in_etf}%)`).join(", ")} — double exposure.</div>`).join("");
  const pairRows = (o.etf_pairs || []).map(p =>
    `<div class="pf-ov-item"><span class="pf-ov-warn">↔</span> <strong>${escapeHtml(p.a)}</strong> &amp; <strong>${escapeHtml(p.b)}</strong> share ${p.n_shared} top holding${p.n_shared === 1 ? "" : "s"} (~${p.overlap_weight}% overlapping weight): ${p.shared.slice(0, 8).map(escapeHtml).join(", ")}${p.shared.length > 8 ? "…" : ""}.</div>`).join("");
  const clean = !stockRows && !pairRows;
  box.innerHTML = `
    <div class="pf-bt-card">
      <div class="pf-bt-head"><strong>Holdings overlap</strong> <span class="filter-count">ETFs: ${o.etfs.map(escapeHtml).join(", ")} · ${o.note || ""}</span></div>
      ${clean ? `<div class="pf-ov-clean">✓ No overlap found between your direct holdings and ETF top-10s.</div>` : (stockRows + pairRows)}
    </div>`;
}

function pfRenderBacktest(market, d) {
  const p = d.portfolio_stats || {}, b = d.benchmark_stats || {};
  const pc = (v) => v == null ? "—" : `${v > 0 ? "+" : ""}${v}%`;
  const rr = (v) => v == null ? "—" : v;
  // equity curve SVG
  const W = 720, H = 210, P = 8;
  const all = [...(d.portfolio || []), ...(d.benchmark || [])];
  const mn = Math.min(...all), mx = Math.max(...all);
  const sc = (v) => H - P - (v - mn) / (mx - mn || 1) * (H - 2 * P);
  const line = (arr) => arr.map((v, i) => `${(P + i / (arr.length - 1) * (W - 2 * P)).toFixed(1)},${sc(v).toFixed(1)}`).join(" ");
  // metric tiles: [label, port, bench, hint, better]
  const rows = [
    ["Total return", pc(p.total_return), pc(b.total_return)],
    ["CAGR", pc(p.cagr), pc(b.cagr)],
    ["Max drawdown", pc(p.max_drawdown), pc(b.max_drawdown)],
    ["MAR (CAGR÷MaxDD)", rr(p.mar), rr(b.mar)],
    ["Volatility", pc(p.vol), pc(b.vol)],
    ["Sharpe", rr(p.sharpe), rr(b.sharpe)],
    ["Sortino", rr(p.sortino), rr(b.sortino)],
    ["Beta vs bench", rr(p.beta), "1.00"],
    ["Best 12-mo", pc(p.best_12m), pc(b.best_12m)],
    ["Worst 12-mo", pc(p.worst_12m), pc(b.worst_12m)],
    ["% positive months", p.pos_months == null ? "—" : p.pos_months + "%", b.pos_months == null ? "—" : b.pos_months + "%"],
    ["Longest drawdown", p.dd_days == null ? "—" : p.dd_days + "d", b.dd_days == null ? "—" : b.dd_days + "d"],
  ];
  const tiles = rows.map(([lab, pv, bv]) => `
    <div class="pf-bt-tile">
      <span class="pf-bt-lab">${lab}</span>
      <strong class="pf-bt-p">${pv}</strong>
      <span class="pf-bt-b">${d.benchmark_name}: ${bv}</span>
    </div>`).join("");
  const exNote = (d.excluded && d.excluded.length) ? ` · excluded (short history): ${d.excluded.join(", ")}` : "";
  document.getElementById("pfBacktest").innerHTML = `
    <div class="pf-bt-card">
      <div class="pf-bt-head"><strong>Backtest</strong> <span class="filter-count">weighted buy-&-hold · ${d.window.from} → ${d.window.to} (${d.window.years}y) · ${d.n_holdings} holdings vs ${d.benchmark_name}${exNote}</span></div>
      <div class="siglab-chart-box">
        <svg viewBox="0 0 ${W} ${H}" class="siglab-svg" preserveAspectRatio="none">
          <polyline points="${line(d.benchmark || [])}" fill="none" stroke="#9aa8a0" stroke-width="1.5"/>
          <polyline points="${line(d.portfolio || [])}" fill="none" stroke="var(--accent)" stroke-width="2"/>
        </svg>
        <div class="siglab-legend"><span class="siglab-leg-strat">━ Portfolio</span><span class="siglab-leg-bh">━ ${d.benchmark_name}</span></div>
      </div>
      <div class="pf-bt-grid">${tiles}</div>
      <div class="filter-hint">Static initial weights, no rebalancing or costs, common window where all holdings have history. <strong>MAR</strong> = CAGR ÷ |Max Drawdown| (return per unit of worst loss; higher = better). <strong>Sortino</strong> only penalises downside volatility. Benchmark beta is 1.0 by definition.</div>
      ${pfRenderCorr(d.correlation)}
    </div>
    <div id="pfOverlap"></div>`;
}

// Boot into the default page (Macro)
applyMarketLayout();
switchPage(state.page);
_refreshPipelineNavVisibility();
