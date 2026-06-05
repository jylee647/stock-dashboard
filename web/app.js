/* 주식 대시보드 프론트엔드 */
const API = "";
const state = {
  market: "US",
  selected: null,      // {symbol, market, name}
  period: "1d",
  chart: null,
  watchlist: [],
  alerts: JSON.parse(localStorage.getItem("alerts") || "[]"),
  pendingAlert: null,
};

const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

/* ---------- 유틸 ---------- */
function fmtNum(n, market) {
  if (n === null || n === undefined) return "-";
  if (market === "KR") return Math.round(n).toLocaleString("ko-KR");
  return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function chgClass(p) { return p > 0 ? "up" : p < 0 ? "down" : "muted"; }
function chgStr(p) { if (p === null || p === undefined) return "-"; const s = p > 0 ? "+" : ""; return `${s}${p.toFixed(2)}%`; }
function cur(market) { return market === "KR" ? "₩" : "$"; }
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden"); clearTimeout(t._tm); t._tm = setTimeout(() => t.classList.add("hidden"), 2400); }
async function api(path) { const r = await fetch(API + path); if (!r.ok) throw new Error(await r.text()); return r.json(); }

/* ---------- 초기화 ---------- */
async function init() {
  bindUI();
  await Promise.all([loadIndices(), loadWatchlist(), loadGainers()]);
  await loadMarketNews();
  renderAlerts();
  checkHealth();
  setInterval(() => { loadIndices(); loadGainers(); refreshSelectedQuote(); }, 30000);
}

function bindUI() {
  // 시장 스위치
  $$("#marketSwitch .ms-btn").forEach((b) =>
    b.addEventListener("click", () => switchMarket(b.dataset.market)));
  // 탭
  $$(".tab").forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.tab)));
  // 기간
  $$("#periodSwitch button").forEach((b) =>
    b.addEventListener("click", () => { setPeriod(b.dataset.period); }));
  // 액션
  $("#refreshGainers").addEventListener("click", loadGainers);
  $("#refreshReco").addEventListener("click", () => loadRecommend(true));
  $("#btRun").addEventListener("click", runBacktest);
  $$("#themeScope button").forEach((b) => b.addEventListener("click", () => renderThemes(b.dataset.scope)));
  $("#addWatchBtn").addEventListener("click", addCurrentToWatch);
  $("#goNewsBtn").addEventListener("click", () => { if (state.selected) showStockNews(state.selected); });
  $("#setAlertBtn").addEventListener("click", openAlertModal);
  $("#newsBackBtn").addEventListener("click", loadMarketNews);
  // 검색
  const si = $("#searchInput");
  let tmr;
  si.addEventListener("input", () => { clearTimeout(tmr); tmr = setTimeout(() => doSearch(si.value), 280); });
  document.addEventListener("click", (e) => { if (!e.target.closest(".search-wrap")) $("#searchResults").classList.add("hidden"); });
  // 알림 모달
  $("#alertCancel").addEventListener("click", () => $("#alertModal").classList.add("hidden"));
  $("#alertSave").addEventListener("click", saveAlert);
}

async function checkHealth() {
  try {
    const h = await api("/api/health");
    const pill = $("#statusPill");
    pill.style.color = "var(--up)";
    pill.title = `네이버뉴스: ${h.naver_news ? "ON" : "OFF"} / KRX로그인: ${h.krx_login ? "ON" : "OFF"}`;
  } catch { $("#statusPill").style.color = "var(--down)"; }
}

/* ---------- 시장 / 탭 ---------- */
function switchMarket(mk) {
  if (mk === state.market) return;
  state.market = mk;
  $$("#marketSwitch .ms-btn").forEach((b) => b.classList.toggle("active", b.dataset.market === mk));
  $("#gainersMarketLabel").textContent = mk === "KR" ? "(한국)" : "(미국)";
  $("#recoMarketLabel").textContent = mk === "KR" ? "(한국)" : "(미국)";
  state.recoLoaded = false;
  loadGainers();
  loadMarketNews();
  if ($("#tab-recommend").classList.contains("active")) loadRecommend();
}
function switchTab(name) {
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $$(".panel").forEach((p) => p.classList.toggle("active", p.id === "tab-" + name));
  if (name === "watchlist") renderWatchFull();
  if (name === "alerts") renderAlerts();
  if (name === "recommend" && !state.recoLoaded) loadRecommend();
  if (name === "backtest") $("#btMarketLabel").textContent = state.market === "KR" ? "(한국)" : "(미국)";
  if (name === "themes" && !state.themesLoaded) loadThemes();
}

/* ---------- 테마 전망 ---------- */
async function loadThemes() {
  $("#themesBody").innerHTML = `<div class="loading">테마 전망 불러오는 중…</div>`;
  try {
    const d = await api("/api/themes");
    state.themes = d; state.themesLoaded = true;
    const src = d._source === "live" ? "실시간(자동 갱신)" : d._source === "seed" ? "초기 시드" : "데이터 없음";
    $("#themesMeta").textContent = `· ${d.generatedAt || "-"} · ${src}`;
    $("#themesNote").textContent = d.crossCheckNote || "";
    $("#themesDisc").textContent = "※ 국내·해외 뉴스를 교차검증한 알고리즘/AI 요약이며 투자 자문이 아닙니다. 투자 판단과 책임은 본인에게 있습니다.";
    renderThemes(state.themeScope || "short");
  } catch (e) { $("#themesBody").innerHTML = `<div class="loading">테마 전망 로딩 실패</div>`; }
}

function renderThemes(scope) {
  state.themeScope = scope;
  $$("#themeScope button").forEach((b) => b.classList.toggle("active", b.dataset.scope === scope));
  const grp = (state.themes && state.themes[scope]) || { horizons: [] };
  const box = $("#themesBody");
  if (!grp.horizons || !grp.horizons.length) { box.innerHTML = `<div class="loading">표시할 테마가 없습니다.</div>`; return; }
  box.innerHTML = grp.horizons.map((hz) => `
    <div class="theme-horizon">
      <div class="theme-hz-title">${hz.label}</div>
      ${(hz.themes || []).map(themeCard).join("") || `<div class="muted" style="padding:4px 0 10px">해당 기간 테마 없음</div>`}
    </div>`).join("");
  box.querySelectorAll(".t-stock").forEach((el) => el.addEventListener("click", () => {
    const s = { symbol: el.dataset.sym, market: el.dataset.mk, name: el.dataset.name };
    if (s.market !== state.market) switchMarket(s.market);
    switchTab("dashboard"); selectStock(s);
  }));
}

function themeCard(t) {
  const kr = ((t.stocks || {}).KR || []).map((s) =>
    `<div class="t-stock" data-sym="${s.code || ""}" data-mk="KR" data-name="${(s.name || "").replace(/"/g, "&quot;")}">
      <span class="ts-name">${s.name || ""}</span><span class="ts-sym">${s.code || ""}</span><span class="ts-why">${s.why || ""}</span></div>`).join("") || `<div class="muted" style="font-size:12px">-</div>`;
  const us = ((t.stocks || {}).US || []).map((s) =>
    `<div class="t-stock" data-sym="${s.ticker || ""}" data-mk="US" data-name="${(s.name || "").replace(/"/g, "&quot;")}">
      <span class="ts-name">${s.name || ""}</span><span class="ts-sym">${s.ticker || ""}</span><span class="ts-why">${s.why || ""}</span></div>`).join("") || `<div class="muted" style="font-size:12px">-</div>`;
  const conf = t.confidence || "";
  return `<div class="theme-card">
    <h4>${t.theme || ""}</h4>
    <div class="t-meta">${conf ? `<span class="t-chip conf-${conf}">신뢰도 ${conf}</span>` : ""}
      ${(t.drivers || []).map((d) => `<span class="t-chip">${d}</span>`).join("")}</div>
    <div class="t-sum">${t.summary || ""}</div>
    <div class="t-stocks">
      <div class="t-stockcol"><h5>🇰🇷 국내</h5>${kr}</div>
      <div class="t-stockcol"><h5>🇺🇸 해외</h5>${us}</div>
    </div></div>`;
}

/* ---------- 백테스트 ---------- */
async function runBacktest() {
  const months = $("#btMonths").value, hold = $("#btHold").value, top = $("#btTop").value;
  $("#btMarketLabel").textContent = state.market === "KR" ? "(한국)" : "(미국)";
  $("#btMsg").textContent = "백테스트 계산 중… 수십 초 걸릴 수 있어요 (과거 시세 다운로드).";
  $("#btStats").innerHTML = ""; $("#btTrades").innerHTML = ""; $("#btDisclaimer").textContent = "";
  $("#btTradesHead").style.display = "none";
  if (state.btChart) { state.btChart.destroy(); state.btChart = null; }
  try {
    const d = await api(`/api/backtest?market=${state.market}&months=${months}&hold=${hold}&top=${top}`);
    if (d.error) { $("#btMsg").textContent = "⚠️ " + d.error; return; }
    $("#btMsg").textContent = `유니버스 ${d.universeSize}종목 · ${d.months}개월 · ${d.hold}거래일마다 상위 ${d.top}종목 보유`;
    const s = d.stats;
    const stat = (v, l, cls) => `<div class="bt-stat"><div class="v ${cls || ""}">${v}</div><div class="l">${l}</div></div>`;
    $("#btStats").innerHTML =
      stat((s.totalReturn > 0 ? "+" : "") + s.totalReturn + "%", "전략 누적수익", chgClass(s.totalReturn)) +
      stat((s.benchReturn > 0 ? "+" : "") + s.benchReturn + "%", d.benchName + " 누적", chgClass(s.benchReturn)) +
      stat(s.winRateVsBench + "%", "지수 대비 승률", "") +
      stat((s.avgPerTrade > 0 ? "+" : "") + s.avgPerTrade + "%", "회당 평균수익", chgClass(s.avgPerTrade)) +
      stat(s.trades + "회", "리밸런싱 횟수", "") +
      stat((s.bestTrade > 0 ? "+" : "") + s.bestTrade + "% / " + s.worstTrade + "%", "최고 / 최저", "");
    drawBtChart(d.curve, d.benchName);
    if (d.recentTrades && d.recentTrades.length) {
      $("#btTradesHead").style.display = "block";
      $("#btTrades").innerHTML = d.recentTrades.map((t) =>
        `<div class="bt-trade"><div class="tt-head"><b>${t.date}</b>
          <span class="${chgClass(t.ret)}">${t.ret > 0 ? "+" : ""}${t.ret}% <span class="muted">(지수 ${t.benchRet > 0 ? "+" : ""}${t.benchRet}%)</span></span></div>
          <div class="tt-picks">${t.picks.join(" · ")}</div></div>`).join("");
    }
    $("#btDisclaimer").textContent = "※ " + (d.disclaimer || "");
  } catch (e) { $("#btMsg").textContent = "백테스트 실패: 서버/네트워크 확인"; }
}

function drawBtChart(curve, benchName) {
  const ctx = $("#btChart").getContext("2d");
  if (state.btChart) state.btChart.destroy();
  state.btChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: curve.map((p) => p.date),
      datasets: [
        { label: "전략", data: curve.map((p) => p.strategy), borderColor: "#0071e3", backgroundColor: "#0071e322", borderWidth: 2, fill: true, pointRadius: 0, tension: 0.1 },
        { label: benchName, data: curve.map((p) => p.benchmark), borderColor: "#8e8e93", borderWidth: 1.5, borderDash: [5, 4], pointRadius: 0, tension: 0.1 },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: "#1d1d1f", usePointStyle: true } }, tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${c.parsed.y > 0 ? "+" : ""}${c.parsed.y}%` } } },
      scales: { x: { ticks: { color: "#8e8e93", maxTicksLimit: 8 }, grid: { display: false } },
        y: { ticks: { color: "#8e8e93", callback: (v) => v + "%" }, grid: { color: "#ededf0" } } },
    },
  });
}

/* ---------- 추천 ---------- */
async function loadRecommend(force) {
  const ul = $("#recoList");
  ul.innerHTML = `<li class="loading">뉴스·지수 분석 중… (수 초 걸릴 수 있어요)</li>`;
  $("#recoDisclaimer").textContent = "";
  $("#recoTrend").textContent = "";
  try {
    const d = await api(`/api/recommend?market=${state.market}&limit=10`);
    state.recoLoaded = true;
    const t = d.marketTrend;
    $("#recoTrend").innerHTML = `시장추세(지수 평균): <span class="${chgClass(t)}">${chgStr(t)}</span>`;
    if (!d.items.length) { ul.innerHTML = `<li class="loading">분석할 데이터가 없습니다.</li>`; return; }
    ul.innerHTML = d.items.map((it, i) => recoRow(it, i + 1)).join("");
    ul.querySelectorAll(".reco-item").forEach((el) => {
      const s = { symbol: el.dataset.sym, market: el.dataset.mk, name: el.dataset.name };
      el.addEventListener("click", () => { switchTab("dashboard"); selectStock(s); });
      el.querySelector(".reco-btn").addEventListener("click", (e) => { e.stopPropagation(); showStockNews(s); });
    });
    $("#recoDisclaimer").textContent = "※ " + (d.disclaimer || "");
  } catch (e) { ul.innerHTML = `<li class="loading">추천 로딩 실패: 서버/네트워크 확인</li>`; }
}

function recoRow(it, rank) {
  const comps = it.components || [];
  const rows = comps.map((c) =>
    `<div class="bd-row" title="${(c.basis || "").replace(/"/g, "&quot;")}">
      <span class="bd-label">${c.label}</span>
      <span class="bd-wt">${c.weightPct}%</span>
      <div class="bd-track"><div class="bd-fill" style="width:${c.normScore}%"></div></div>
      <span class="bd-norm">${c.normScore}점</span>
      <span class="bd-pts">+${c.points}</span>
    </div>`).join("");
  return `<li class="reco-item" data-sym="${it.symbol}" data-mk="${it.market}" data-name="${(it.name || "").replace(/"/g, "&quot;")}">
    <div class="reco-rank">${rank}</div>
    <div class="reco-main">
      <div class="reco-name">${it.name || it.symbol} <span class="sl-sym">${it.symbol}</span>
        <span class="reco-chg ${chgClass(it.changePct)}">${chgStr(it.changePct)}</span>${it.rsi != null ? ` <span class="muted" style="font-size:11px">RSI ${it.rsi}</span>` : ""}</div>
      <div class="reco-reason">${it.reason || ""}</div>
      <div class="bd-table">
        <div class="bd-head"><span class="bd-label">항목</span><span class="bd-wt">가중치</span><div class="bd-track" style="visibility:hidden"></div><span class="bd-norm">항목점수</span><span class="bd-pts">기여</span></div>
        ${rows}
        <div class="bd-total"><span>총합</span><b>${it.score}점 / 100</b></div>
      </div>
      <button class="reco-btn">📰 관련 뉴스</button>
    </div>
    <div class="reco-right">
      <div class="reco-score">${it.score}<small>점수/100</small></div>
    </div></li>`;
}

/* ---------- 지수 / 환율 ---------- */
async function loadIndices() {
  try {
    const d = await api("/api/indices");
    const all = [...(d.KR || []), ...(d.US || []), ...(d.FX || [])];
    $("#tickerStrip").innerHTML = all.map((x) => {
      const c = chgClass(x.changePct);
      const isFx = (d.FX || []).includes(x);
      return `<span class="tk"><b>${x.name}</b> <span>${x.price != null ? x.price.toLocaleString() : "-"}</span> <span class="${c}">${chgStr(x.changePct)}</span></span>`;
    }).join("");
  } catch { $("#tickerStrip").innerHTML = `<span class="muted">지수 로딩 실패 (네트워크 확인)</span>`; }
}

/* ---------- 등락률 TOP10 ---------- */
async function loadGainers() {
  const ul = $("#gainersList");
  ul.innerHTML = `<li class="loading">불러오는 중…</li>`;
  try {
    const d = await api(`/api/gainers?market=${state.market}&limit=10`);
    if (!d.items.length) { ul.innerHTML = `<li class="loading">데이터 없음</li>`; return; }
    ul.innerHTML = d.items.map((it, i) => stockRow(it, i + 1)).join("");
    ul.querySelectorAll(".sl-item").forEach((el) =>
      el.addEventListener("click", () => selectStock({ symbol: el.dataset.sym, market: el.dataset.mk, name: el.dataset.name })));
    // 첫 종목 자동 선택
    if (!state.selected) selectStock({ symbol: d.items[0].symbol, market: d.items[0].market, name: d.items[0].name });
    runAlertCheck(d.items);
  } catch (e) { ul.innerHTML = `<li class="loading">로딩 실패: 서버/네트워크 확인</li>`; }
}

function stockRow(it, rank) {
  const c = chgClass(it.changePct);
  return `<li class="sl-item" data-sym="${it.symbol}" data-mk="${it.market}" data-name="${(it.name || "").replace(/"/g, "&quot;")}">
    ${rank ? `<span class="sl-rank">${rank}</span>` : ""}
    <div class="sl-main"><div class="sl-name">${it.name || it.symbol}</div><div class="sl-sym">${it.symbol}</div></div>
    <div class="sl-right"><div class="sl-price">${cur(it.market)}${fmtNum(it.price, it.market)}</div>
    <div class="sl-chg ${c}">${chgStr(it.changePct)}</div></div></li>`;
}

/* ---------- 종목 선택 → 차트 ---------- */
async function selectStock(s) {
  state.selected = s;
  $$(".sl-item").forEach((el) => el.classList.toggle("selected", el.dataset.sym === s.symbol && el.dataset.mk === s.market));
  $("#chartName").textContent = `${s.name || s.symbol} (${s.symbol})`;
  ["addWatchBtn", "goNewsBtn", "setAlertBtn"].forEach((id) => $("#" + id).disabled = false);
  await Promise.all([loadChart(), loadQuoteLine()]);
}
function setPeriod(p) {
  state.period = p;
  $$("#periodSwitch button").forEach((b) => b.classList.toggle("active", b.dataset.period === p));
  loadChart();
}
async function refreshSelectedQuote() { if (state.selected) loadQuoteLine(); }

async function loadQuoteLine() {
  const s = state.selected; if (!s) return;
  try {
    const q = await api(`/api/quote/${encodeURIComponent(s.symbol)}?market=${s.market}`);
    const c = chgClass(q.changePct);
    $("#chartPrice").innerHTML = `<span class="big">${cur(s.market)}${fmtNum(q.price, s.market)}</span>
      <span class="${c}">${q.change != null ? (q.change > 0 ? "+" : "") + fmtNum(q.change, s.market) : "-"} (${chgStr(q.changePct)})</span>`;
  } catch { $("#chartPrice").innerHTML = `<span class="muted">시세 로딩 실패</span>`; }
}

async function loadChart() {
  const s = state.selected; if (!s) return;
  try {
    const d = await api(`/api/chart/${encodeURIComponent(s.symbol)}?market=${s.market}&period=${state.period}`);
    const labels = d.points.map((p) => p.t);
    const closes = d.points.map((p) => p.close);
    drawChart(labels, closes, s.market);
  } catch { toast("차트 로딩 실패"); }
}

function drawChart(labels, data, market) {
  const ctx = $("#mainChart").getContext("2d");
  const up = data.length && data[data.length - 1] >= data[0];
  const col = up ? "#34c759" : "#ff3b30";
  const fmtLabel = (s) => { const dt = new Date(s); return state.period === "1d" || state.period === "1w"
    ? dt.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" })
    : dt.toLocaleDateString("ko-KR", { month: "2-digit", day: "2-digit" }); };
  if (state.chart) state.chart.destroy();
  state.chart = new Chart(ctx, {
    type: "line",
    data: { labels: labels.map(fmtLabel), datasets: [{ data, borderColor: col, backgroundColor: col + "22",
      borderWidth: 2, fill: true, pointRadius: 0, tension: 0.15 }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { mode: "index", intersect: false,
        callbacks: { label: (c) => `${cur(market)}${fmtNum(c.parsed.y, market)}` } } },
      scales: { x: { ticks: { color: "#8e8e93", maxTicksLimit: 8 }, grid: { display: false } },
        y: { ticks: { color: "#8e8e93", callback: (v) => fmtNum(v, market) }, grid: { color: "#ededf0" } } },
    },
  });
}

/* ---------- 관심종목 ---------- */
async function loadWatchlist() {
  try { const d = await api("/api/watchlist"); state.watchlist = d.items; renderWatchQuick(); renderWatchFull(); } catch {}
}
async function addCurrentToWatch() {
  const s = state.selected; if (!s) return;
  await fetch("/api/watchlist", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(s) });
  toast(`⭐ ${s.name || s.symbol} 추가됨`);
  loadWatchlist();
}
async function removeWatch(symbol, market) {
  await fetch(`/api/watchlist?symbol=${encodeURIComponent(symbol)}&market=${market}`, { method: "DELETE" });
  loadWatchlist();
}
function renderWatchQuick() {
  const ul = $("#watchQuickList");
  if (!state.watchlist.length) { ul.innerHTML = `<li class="muted" style="padding:8px;">없음</li>`; return; }
  ul.innerHTML = "";
  state.watchlist.forEach((w) => hydrateWatchRow(w, ul, false));
}
function renderWatchFull() {
  const ul = $("#watchFullList");
  $("#watchEmptyHint").style.display = state.watchlist.length ? "none" : "block";
  ul.innerHTML = "";
  state.watchlist.forEach((w) => hydrateWatchRow(w, ul, true));
}
async function hydrateWatchRow(w, ul, withDelete) {
  const li = document.createElement("li");
  li.className = "sl-item";
  li.innerHTML = `<div class="sl-main"><div class="sl-name">${w.name || w.symbol}</div><div class="sl-sym">${w.symbol}</div></div>
    <div class="sl-right"><div class="sl-price">…</div><div class="sl-chg muted">-</div></div>
    ${withDelete ? `<button class="sl-del" title="삭제">✕</button>` : ""}`;
  li.querySelector(".sl-main").addEventListener("click", () => { selectStock(w); switchTab("dashboard"); });
  if (withDelete) li.querySelector(".sl-del").addEventListener("click", (e) => { e.stopPropagation(); removeWatch(w.symbol, w.market); });
  ul.appendChild(li);
  try {
    const q = await api(`/api/quote/${encodeURIComponent(w.symbol)}?market=${w.market}`);
    li.querySelector(".sl-price").textContent = `${cur(w.market)}${fmtNum(q.price, w.market)}`;
    const chg = li.querySelector(".sl-chg");
    chg.textContent = chgStr(q.changePct); chg.className = "sl-chg " + chgClass(q.changePct);
  } catch {}
}

/* ---------- 뉴스 ---------- */
async function loadMarketNews() {
  $("#newsTitle").textContent = `📰 ${state.market === "KR" ? "한국" : "미국"} 시장 뉴스`;
  $("#newsBackBtn").style.display = "none";
  const box = $("#newsList"); box.innerHTML = `<div class="loading">뉴스 불러오는 중…</div>`;
  try { const d = await api(`/api/news?market=${state.market}&limit=14`); renderNews(d.items); }
  catch { box.innerHTML = `<div class="loading">뉴스 로딩 실패</div>`; }
}
async function showStockNews(s) {
  switchTab("news");
  $("#newsTitle").textContent = `📰 ${s.name || s.symbol} 관련 뉴스`;
  $("#newsBackBtn").style.display = "inline-block";
  const box = $("#newsList"); box.innerHTML = `<div class="loading">뉴스 불러오는 중…</div>`;
  try {
    const d = await api(`/api/news/${encodeURIComponent(s.symbol)}?market=${s.market}&name=${encodeURIComponent(s.name || "")}&limit=12`);
    renderNews(d.items);
  } catch { box.innerHTML = `<div class="loading">뉴스 로딩 실패</div>`; }
}
function renderNews(items) {
  const box = $("#newsList");
  if (!items || !items.length) { box.innerHTML = `<div class="loading">표시할 뉴스가 없습니다.</div>`; return; }
  box.innerHTML = items.map((n) => `
    <a class="news-item" href="${n.link || "#"}" target="_blank" rel="noopener">
      <h4>${n.title || ""}</h4>
      ${n.summary ? `<p>${n.summary}</p>` : ""}
      <div class="news-meta"><span class="src">${n.source || ""}</span> · ${n.date || ""}</div>
    </a>`).join("");
}

/* ---------- 검색 ---------- */
async function doSearch(q) {
  const box = $("#searchResults");
  if (!q || q.trim().length < 1) { box.classList.add("hidden"); return; }
  try {
    const d = await api(`/api/search?q=${encodeURIComponent(q.trim())}`);
    if (!d.items.length) { box.innerHTML = `<div class="sr-item muted">결과 없음</div>`; box.classList.remove("hidden"); return; }
    box.innerHTML = d.items.map((it) =>
      `<div class="sr-item" data-sym="${it.symbol}" data-mk="${it.market}" data-name="${(it.name || "").replace(/"/g, "&quot;")}">
        <span>${it.name || it.symbol} <span class="sl-sym">${it.symbol}</span></span>
        <span class="sr-mk">${it.market}${it.exchange ? " · " + it.exchange : ""}</span></div>`).join("");
    box.querySelectorAll(".sr-item[data-sym]").forEach((el) => el.addEventListener("click", () => {
      const s = { symbol: el.dataset.sym, market: el.dataset.mk, name: el.dataset.name };
      if (s.market !== state.market) switchMarket(s.market);
      box.classList.add("hidden"); $("#searchInput").value = "";
      switchTab("dashboard"); selectStock(s);
    }));
    box.classList.remove("hidden");
  } catch { box.classList.add("hidden"); }
}

/* ---------- 가격 알림 ---------- */
function openAlertModal() {
  const s = state.selected; if (!s) return;
  state.pendingAlert = s;
  $("#alertModalTitle").textContent = `🔔 ${s.name || s.symbol} 알림`;
  $("#alertTarget").value = "";
  $("#alertModal").classList.remove("hidden");
}
function saveAlert() {
  const s = state.pendingAlert; if (!s) return;
  const target = parseFloat($("#alertTarget").value);
  if (isNaN(target)) { toast("목표가를 입력하세요"); return; }
  state.alerts.push({ ...s, target, direction: $("#alertDir").value, hit: false });
  localStorage.setItem("alerts", JSON.stringify(state.alerts));
  $("#alertModal").classList.add("hidden");
  toast("🔔 알림 설정됨");
  renderAlerts();
}
function renderAlerts() {
  const ul = $("#alertList");
  $("#alertEmptyHint").style.display = state.alerts.length ? "none" : "block";
  ul.innerHTML = state.alerts.map((a, i) => `
    <li><span><b>${a.name || a.symbol}</b> <span class="sl-sym">${a.symbol}</span>
      <span class="alert-badge ${a.hit ? "hit" : ""}">${a.direction === "above" ? "≥" : "≤"} ${cur(a.market)}${a.target}${a.hit ? " · 도달!" : ""}</span></span>
      <button class="sl-del" data-i="${i}">✕</button></li>`).join("");
  ul.querySelectorAll(".sl-del").forEach((b) => b.addEventListener("click", () => {
    state.alerts.splice(+b.dataset.i, 1); localStorage.setItem("alerts", JSON.stringify(state.alerts)); renderAlerts();
  }));
}
async function runAlertCheck(gainerItems) {
  if (!state.alerts.length) return;
  for (const a of state.alerts) {
    let price = null;
    const hit0 = gainerItems.find((g) => g.symbol === a.symbol && g.market === a.market);
    if (hit0) price = hit0.price;
    else { try { const q = await api(`/api/quote/${encodeURIComponent(a.symbol)}?market=${a.market}`); price = q.price; } catch {} }
    if (price == null) continue;
    const reached = a.direction === "above" ? price >= a.target : price <= a.target;
    if (reached && !a.hit) { a.hit = true; toast(`🔔 ${a.name || a.symbol} ${cur(a.market)}${price} — 목표가 도달!`); }
    if (!reached) a.hit = false;
  }
  localStorage.setItem("alerts", JSON.stringify(state.alerts));
  renderAlerts();
}

window.addEventListener("DOMContentLoaded", init);
/* end of app.js */
