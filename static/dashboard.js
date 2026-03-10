let refreshIntervalSec = 300;
let soundEnabled = false;
let lastAlertSignature = null;
let chart = null;
let candleSeries = null;
let levelLines = [];
let lastRenderTs = Date.now();
let selectedSymbol = localStorage.getItem("selectedSymbol") || "BTC/USDC";
let scannerFilter = "all";

function initSoundToggle() {
  const toggle = document.getElementById("soundToggle");
  if (!toggle) return;
  soundEnabled = localStorage.getItem("soundEnabled") === "true";
  toggle.textContent = `Sound: ${soundEnabled ? "ON" : "OFF"}`;
  toggle.addEventListener("click", () => {
    soundEnabled = !soundEnabled;
    localStorage.setItem("soundEnabled", soundEnabled ? "true" : "false");
    toggle.textContent = `Sound: ${soundEnabled ? "ON" : "OFF"}`;
    if (soundEnabled) playBeep();
  });
}

function initScannerFilters() {
  const buttons = document.querySelectorAll(".scan-filter");
  buttons.forEach((btn) => {
    btn.addEventListener("click", async () => {
      scannerFilter = btn.dataset.filter || "all";
      buttons.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      await refreshScanner();
    });
  });
}

function playBeep() {
  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) return;
    const ctx = new AudioCtx();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = 880;
    gain.gain.value = 0.08;
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.25);
    osc.onended = () => ctx.close();
  } catch (err) {
    // ignore audio errors
  }
}

function shouldAlert(brief) {
  const score = Number(brief.setup_score?.final ?? 0);
  const gate = Boolean(brief.setup_score?.trade_gate);
  const activeSetup = brief.trade?.active_setup ?? "NONE";
  const activeEvent = brief.level_event?.active_event ?? "none";
  const eventOk = activeEvent === "sweep_reclaim" || activeEvent === "break";
  return score >= 7 && gate && activeSetup !== "NONE" && eventOk;
}

function buildAlertSignature(brief) {
  const score = Number(brief.setup_score?.final ?? 0);
  const activeSetup = brief.trade?.active_setup ?? "NONE";
  const activeEvent = brief.level_event?.active_event ?? "none";
  return `${activeSetup}:${activeEvent}:${score.toFixed(1)}`;
}

async function fetchBrief(symbol = null) {
  const query = symbol ? `?symbol=${encodeURIComponent(symbol)}` : "";
  const res = await fetch(`/api/brief${query}`);
  return res.json();
}

async function fetchConfig() {
  const res = await fetch("/api/config");
  return res.json();
}

async function fetchScannerList() {
  const res = await fetch("/api/scanner/list");
  return res.json();
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function setStatus(status, text, sub) {
  const bar = document.getElementById("statusBar");
  if (!bar) return;
  bar.classList.remove("status-watch", "status-active", "status-avoid", "status-none");
  let badgeTone = "gray";
  if (status === "SETUP ACTIVE") bar.classList.add("status-active");
  else if (status === "AVOID") {
    bar.classList.add("status-avoid");
    badgeTone = "red";
  } else if (status === "NO SETUP") {
    bar.classList.add("status-none");
  } else {
    bar.classList.add("status-watch");
    badgeTone = "orange";
  }
  if (status === "SETUP ACTIVE") badgeTone = "green";
  const context = text && String(text).trim() ? text : "waiting for data";
  const action = sub && String(sub).trim() ? sub : "pending";
  setText("statusText", `Context: ${context}`);
  setText("statusSub", `Action: ${action}`);
  setBadge("statusBadge", status, badgeTone);
}

function hasNumber(n) {
  return n !== null && n !== undefined && !Number.isNaN(n);
}

function fmt(n) {
  if (!hasNumber(n)) return "not available";
  return Number(n).toFixed(2);
}

function fmtUsdc(n, fallback = "pending") {
  if (!hasNumber(n)) return fallback;
  return `${fmt(n)} USDC`;
}

function fmtPriceCompact(n, fallback = "pending") {
  if (!hasNumber(n)) return fallback;
  const v = Number(n);
  if (Math.abs(v) >= 10000) {
    return v.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 });
  }
  return v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtUsdcCompact(n, fallback = "pending") {
  if (!hasNumber(n)) return fallback;
  return `${fmtPriceCompact(n)} USDC`;
}

function fmtOr(n, fallback = "--") {
  return hasNumber(n) ? fmt(n) : fallback;
}

function fmtPct(n, fallback = "not available") {
  if (!hasNumber(n)) return fallback;
  const rounded = Math.round(Number(n) * 10) / 10;
  if (Math.abs(rounded % 1) < 0.001) return `${rounded.toFixed(0)}%`;
  return `${rounded.toFixed(1)}%`;
}

function fmtSignedPct(n, fallback = "--") {
  if (!hasNumber(n)) return fallback;
  const rounded = Math.round(Number(n) * 100) / 100;
  const sign = rounded > 0 ? "+" : "";
  return `${sign}${rounded.toFixed(2)}%`;
}

function fmtLevel(n, fallback = "--") {
  if (!hasNumber(n)) return fallback;
  return Number(n).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 });
}

function compactContext(reason) {
  if (!reason) return "pending";
  return String(reason)
    .replace(/\bzone\b/gi, "")
    .replace(/\s+/g, " ")
    .trim();
}

function deriveTriggerType(distancePct) {
  if (!hasNumber(distancePct)) return "pending";
  if (Number(distancePct) > 0) return "Resistance";
  if (Number(distancePct) < 0) return "Support";
  return "Neutral";
}

function deriveLevelEventBadge(brief) {
  const event = brief.level_event || {};
  const active = event.active_event || "none";
  const breakDetected = Boolean(event.break_confirmed || active === "break");
  const sweepDetected = Boolean(event.sweep_detected || event.reclaim_confirmed || active === "sweep_reclaim");

  if (active === "break" || breakDetected) {
    const confirmed = Boolean(event.break_confirmed || active === "break");
    const exploitable = Boolean(brief.setup_score?.trade_gate) && brief.trade?.active_setup === "SHORT";
    if (confirmed) return { label: "BREAK CONFIRMED", tone: exploitable ? "red" : "orange" };
    return { label: "BREAK DETECTED", tone: "gray" };
  }

  if (active === "sweep_reclaim" || sweepDetected) {
    const confirmed = Boolean(event.reclaim_confirmed || active === "sweep_reclaim");
    const exploitable = Boolean(brief.setup_score?.trade_gate) && brief.trade?.active_setup === "LONG";
    if (confirmed) return { label: "SWEEP CONFIRMED", tone: exploitable ? "green" : "orange" };
    return { label: "SWEEP DETECTED", tone: "gray" };
  }

  return { label: "NONE", tone: "gray" };
}

function buildStatusActionLine(status, currentAction, criticalLevel) {
  if (status === "SETUP ACTIVE" && currentAction === "LONG ACTIVE") return "execute LONG plan";
  if (status === "SETUP ACTIVE" && currentAction === "SHORT ACTIVE") return "execute SHORT plan";
  if (status === "AVOID") return "avoid entries until conditions improve";
  if (status === "NO SETUP") return "stand by, no setup active";
  if (currentAction === "WATCH") return `wait for trigger at ${fmtLevel(criticalLevel)} USDC`;
  return "wait";
}

function buildTpRuleText(side, entry, tp1, sizeUsd, estimatedCostPct) {
  if (!hasNumber(entry) || !tp1 || !hasNumber(tp1.price) || !hasNumber(tp1.size_pct) || !hasNumber(sizeUsd) || sizeUsd <= 0) {
    return "Rule: TP plan pending";
  }
  const sign = side === "SHORT" ? -1 : 1;
  const movePct = ((sign * (Number(tp1.price) - Number(entry))) / Number(entry)) * 100;
  const closedNotional = Number(sizeUsd) * Number(tp1.size_pct);
  const grossLocked = closedNotional * (Math.abs(movePct) / 100);
  let text = `Rule: TP1 ${fmtUsdcCompact(tp1.price)} (${(Number(tp1.size_pct) * 100).toFixed(0)}%) -> SL BE ${fmtUsdcCompact(entry)} | Lock +${fmt(grossLocked)} USDC`;
  if (hasNumber(estimatedCostPct)) {
    const netApprox = Math.max(0, grossLocked - closedNotional * (Number(estimatedCostPct) / 100));
    text += ` | Net~ +${fmt(netApprox)} USDC`;
  }
  return text;
}

function computeRR(entry, stop, target) {
  if (!hasNumber(entry) || !hasNumber(stop) || !hasNumber(target)) return null;
  const risk = Math.abs(Number(entry) - Number(stop));
  if (risk <= 0) return null;
  const reward = Math.abs(Number(target) - Number(entry));
  if (reward < 0) return null;
  return reward / risk;
}

function clampPct(n) {
  if (!hasNumber(n)) return 0;
  return Math.max(0, Math.min(100, Number(n)));
}

function setGateVisualState(gateOpen, reason) {
  const body = document.body;
  if (!body) return;
  body.classList.remove("gate-open", "gate-blocked");
  body.classList.add(gateOpen ? "gate-open" : "gate-blocked");
  const gateBadge = document.getElementById("setupGateBadge");
  if (gateBadge) gateBadge.classList.toggle("xl", !gateOpen);
  setText("setupGateReason", `Blocked reason: ${gateOpen ? "none" : humanizeBlockedReason(reason)}`);
}

function humanizeBlockedReason(reason) {
  if (!reason) return "pending";
  const txt = String(reason).trim();
  const map = {
    cost_fail: "Costs too high vs stop distance",
    vwap_mismatch: "VWAP condition not met",
    probability_below_threshold: "Directional probability below threshold",
    probability_below_heads_up_threshold: "Heads-up probability below threshold",
    liquidity_too_far: "Price too far from trigger zone",
    inversion_not_confirmed_2bars: "Inversion not confirmed (2 bars)",
    setup_score_below_threshold: "Setup score below threshold",
    no_active_setup: "No active setup",
    no_active_event: "No active level event",
  };
  if (map[txt]) return map[txt];
  const parts = txt.split(";").map((part) => map[part.trim()] || part.trim());
  return parts.join(" | ");
}

function syncTpDetails(gateOpen, activeSetup) {
  const longDetails = document.getElementById("longTpDetails");
  const shortDetails = document.getElementById("shortTpDetails");
  if (!longDetails || !shortDetails) return;
  if (!gateOpen) {
    longDetails.open = false;
    shortDetails.open = false;
    return;
  }
  if (activeSetup === "LONG") {
    longDetails.open = true;
    shortDetails.open = false;
    return;
  }
  if (activeSetup === "SHORT") {
    longDetails.open = false;
    shortDetails.open = true;
    return;
  }
  longDetails.open = false;
  shortDetails.open = false;
}

function setNextRefresh(now) {
  const next = new Date(now.getTime() + refreshIntervalSec * 1000);
  setText("nextRefresh", `Next refresh: ${next.toISOString().slice(11, 16)} UTC`);
}

function updateRefreshProgress() {
  const bar = document.getElementById("refreshProgressBar");
  if (!bar || refreshIntervalSec <= 0) return;
  const elapsedSec = Math.max(0, (Date.now() - lastRenderTs) / 1000);
  const pct = Math.max(0, Math.min(100, (elapsedSec / refreshIntervalSec) * 100));
  bar.style.width = `${pct}%`;
}

function updateLastUpdateFreshness() {
  const el = document.getElementById("lastUpdate");
  if (!el) return;
  el.classList.remove("fresh-green", "fresh-orange", "fresh-red");
  const ageSec = Math.max(0, (Date.now() - lastRenderTs) / 1000);
  if (ageSec < 60) el.classList.add("fresh-green");
  else if (ageSec > 180) el.classList.add("fresh-orange");
  if (ageSec > 300) el.classList.add("fresh-red");
}

function setBadge(id, label, tone = "gray") {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = label;
  el.className = `badge ${tone}`;
}

function symbolBadgeTone(row) {
  if (row.action === "LONG ACTIVE") return "green";
  if (row.action === "SHORT ACTIVE") return "red";
  if (row.gate_open) return "blue";
  if (row.status === "WATCH") return "orange";
  return "gray";
}

function setSelectedSymbol(symbol) {
  selectedSymbol = symbol;
  localStorage.setItem("selectedSymbol", symbol);
  const url = new URL(window.location.href);
  url.searchParams.set("symbol", symbol);
  window.history.replaceState({}, "", url.toString());
}

function applyScannerFilter(rows) {
  if (scannerFilter === "open") {
    return rows.filter((row) => row.gate_open);
  }
  if (scannerFilter === "near") {
    return rows.filter((row) => hasNumber(row.trigger_distance_pct) && Math.abs(Number(row.trigger_distance_pct)) <= 0.35);
  }
  return rows;
}

function renderScanner(data) {
  const summary = data?.summary || {};
  setText("scanUniverse", String(summary.universe_size ?? 0));
  setText("scanOpenGates", String(summary.open_gates ?? 0));
  setText("scanNearTrigger", String(summary.near_trigger ?? 0));
  setText("scanActiveSetups", String(summary.active_setups ?? 0));

  const list = document.getElementById("scannerList");
  if (!list) return;
  const rows = applyScannerFilter(data?.rows || []);
  list.innerHTML = "";
  if (!rows.length) {
    const empty = document.createElement("div");
    empty.className = "small";
    empty.textContent = "No pair matches current filter";
    list.appendChild(empty);
    return;
  }

  rows.forEach((row) => {
    const card = document.createElement("div");
    card.className = `scanner-row${row.symbol === selectedSymbol ? " selected" : ""}`;

    const top = document.createElement("div");
    top.className = "scanner-row-top";
    const symbol = document.createElement("div");
    symbol.className = "scanner-symbol";
    symbol.textContent = row.symbol || "N/A";
    const badge = document.createElement("span");
    badge.className = `badge ${symbolBadgeTone(row)}`;
    badge.textContent = row.action || row.status || "PENDING";
    top.appendChild(symbol);
    top.appendChild(badge);

    const meta = document.createElement("div");
    meta.className = "scanner-meta";
    const score = document.createElement("span");
    score.className = "scanner-score";
    score.textContent = hasNumber(row.score) ? `Score ${Number(row.score).toFixed(1)}/10` : "Score pending";
    const dist = document.createElement("span");
    const isNear = hasNumber(row.trigger_distance_pct) && Math.abs(Number(row.trigger_distance_pct)) <= 0.35;
    dist.className = `scanner-distance ${isNear ? "near" : "far"}`;
    dist.textContent = hasNumber(row.trigger_distance_pct) ? fmtSignedPct(row.trigger_distance_pct) : "--";
    meta.appendChild(score);
    meta.appendChild(dist);

    card.appendChild(top);
    card.appendChild(meta);
    card.addEventListener("click", async () => {
      setSelectedSymbol(row.symbol);
      await refresh();
      await refreshScanner();
    });
    list.appendChild(card);
  });
}

async function refreshScanner() {
  try {
    const data = await fetchScannerList();
    renderScanner(data);
  } catch (err) {
    const list = document.getElementById("scannerList");
    if (list) {
      list.innerHTML = "";
      const item = document.createElement("div");
      item.className = "small";
      item.textContent = "Scanner unavailable (API timeout). Retry in a few seconds.";
      list.appendChild(item);
    }
  }
}

function clearScenarioHighlight() {
  ["longSetupCard", "shortSetupCard"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.classList.remove("active-scenario");
  });
  ["playbookLong", "playbookShort", "playbookWait"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.classList.remove("active", "dimmed");
  });
}

function applyScenarioHighlight(activeSetup) {
  clearScenarioHighlight();
  const longLine = document.getElementById("playbookLong");
  const shortLine = document.getElementById("playbookShort");
  const waitLine = document.getElementById("playbookWait");
  if (activeSetup === "LONG") {
    document.getElementById("longSetupCard")?.classList.add("active-scenario");
    longLine?.classList.add("active");
    shortLine?.classList.add("dimmed");
    waitLine?.classList.add("dimmed");
    return;
  }
  if (activeSetup === "SHORT") {
    document.getElementById("shortSetupCard")?.classList.add("active-scenario");
    shortLine?.classList.add("active");
    longLine?.classList.add("dimmed");
    waitLine?.classList.add("dimmed");
    return;
  }
  waitLine?.classList.add("active");
  longLine?.classList.add("dimmed");
  shortLine?.classList.add("dimmed");
}

function applyBiasHint(activeSetup, biasReason) {
  const longCard = document.getElementById("longSetupCard");
  const shortCard = document.getElementById("shortSetupCard");
  if (!longCard || !shortCard) return;
  longCard.classList.remove("bias-hint");
  shortCard.classList.remove("bias-hint");
  if (activeSetup !== "NONE") return;
  const hasBear = /bear/i.test(String(biasReason || ""));
  const hasBull = /bull/i.test(String(biasReason || ""));
  if (hasBear) shortCard.classList.add("bias-hint");
  else if (hasBull) longCard.classList.add("bias-hint");
}

function deriveCurrentAction(brief, scoreValue) {
  const activeSetup = brief.trade?.active_setup ?? "NONE";
  if (activeSetup === "LONG") return "LONG ACTIVE";
  if (activeSetup === "SHORT") return "SHORT ACTIVE";
  const score = Number(scoreValue ?? 0);
  const gate = Boolean(brief.setup_score?.trade_gate);
  const activeEvent = brief.level_event?.active_event ?? "none";
  if (gate || activeEvent === "break" || activeEvent === "sweep_reclaim" || score >= 6) return "WATCH";
  return "WAIT";
}

function initChart() {
  if (chart || !window.LightweightCharts) return;
  const container = document.getElementById("miniChart");
  if (!container) return;
  container.textContent = "";
  chart = LightweightCharts.createChart(container, {
    height: 140,
    layout: { background: { color: "transparent" }, textColor: "#9fb0c0" },
    grid: {
      vertLines: { color: "rgba(255,255,255,0.05)" },
      horzLines: { color: "rgba(255,255,255,0.05)" },
    },
    rightPriceScale: { borderColor: "rgba(255,255,255,0.1)" },
    timeScale: { borderColor: "rgba(255,255,255,0.1)" },
  });
  candleSeries = chart.addCandlestickSeries({
    upColor: "#22c55e",
    downColor: "#ef4444",
    borderVisible: false,
    wickUpColor: "#22c55e",
    wickDownColor: "#ef4444",
  });
  window.addEventListener("resize", () => {
    if (!chart || !container) return;
    chart.applyOptions({ width: container.clientWidth });
  });
}

function clearLevelLines() {
  if (!candleSeries) return;
  levelLines.forEach((line) => candleSeries.removePriceLine(line));
  levelLines = [];
}

function addLevelLine(price, color, title) {
  if (!candleSeries || !hasNumber(price)) return;
  const line = candleSeries.createPriceLine({ price, color, lineWidth: 1, lineStyle: 2, title });
  levelLines.push(line);
}

function render(brief) {
  if (brief.error) {
    setText("price", "Error");
    return;
  }

  const now = new Date();
  lastRenderTs = now.getTime();
  setText("lastUpdate", `Last update: ${now.toISOString().slice(11, 16)} UTC`);
  updateLastUpdateFreshness();
  setNextRefresh(now);
  updateRefreshProgress();
  setText("headerPair", `${brief.symbol} | ${brief.exchange}`);

  setText("price", fmtUsdcCompact(brief.price, "waiting for data"));
  const biasReasonRaw = compactContext(brief.market_bias?.reason ?? "pending");
  const biasMain = biasReasonRaw.toUpperCase();
  const biasKind = brief.market_bias?.bias ?? "PENDING";
  setText("marketBias", biasMain);
  setText("marketBiasSub", `Bias type: ${biasKind}`);
  setText("criticalLevel", fmtUsdcCompact(brief.critical_level, "not available"));
  setText("criticalLevelDist", `Distance: ${fmtSignedPct(brief.critical_level_distance_pct)}`);
  setText("criticalLevelType", `Trigger type: ${deriveTriggerType(brief.critical_level_distance_pct)}`);
  setText("criticalLevelSource", `Source: ${String(brief.critical_level_source ?? "1h").toUpperCase()}`);

  const hasBear = /bear/i.test(biasReasonRaw);
  const hasBull = /bull/i.test(biasReasonRaw);
  const biasBadgeText = hasBear ? "DOWN" : hasBull ? "UP" : biasKind;
  const biasTone = hasBear ? "red" : hasBull ? "green" : biasKind === "TREND" ? "orange" : "gray";
  setBadge("biasBadge", biasBadgeText, biasTone);

  const scoreValue = brief.setup_score?.final ?? brief.setup_score?.total;
  const setupClass = brief.setup_score?.class ?? brief.setup_score?.quality ?? "pending";
  const gateOpen = brief.setup_score?.trade_gate;
  const gateReason = brief.setup_score?.reason ?? "pending";
  if (hasNumber(scoreValue)) {
    const fill = document.getElementById("setupScoreFill");
    if (fill) fill.style.width = `${Math.max(0, Math.min(100, (Number(scoreValue) / 10) * 100))}%`;
    setText("setupScoreValue", `${Number(scoreValue).toFixed(1)} / 10`);
    const clsTone =
      setupClass === "PRIORITY" ? "green" : setupClass === "VALID" ? "orange" : setupClass === "WATCHLIST" ? "gray" : "red";
    setBadge("setupBadge", setupClass, clsTone);
    setBadge("setupGateBadge", gateOpen ? "OPEN" : "BLOCKED", gateOpen ? "green" : "red");
  } else {
    const fill = document.getElementById("setupScoreFill");
    if (fill) fill.style.width = "0%";
    setText("setupScoreValue", "not computed");
    setBadge("setupBadge", "PENDING", "gray");
    setBadge("setupGateBadge", "BLOCKED", "red");
  }
  setGateVisualState(Boolean(gateOpen), gateReason);

  if (brief.directional_probability) {
    const prob = brief.directional_probability;
    const longPct = Number(prob.long_probability_pct ?? 0);
    const shortPct = Number(prob.short_probability_pct ?? Math.max(0, 100 - longPct));
    setText("probLong", fmtPct(longPct));
    setText("probShort", fmtPct(shortPct));
    setText("probEdge", `Edge ${prob.edge ?? "not available"}`);
    setText("probConfidence", `Confidence ${prob.confidence ?? "not available"}`);
    const longBar = document.getElementById("probBarLong");
    const shortBar = document.getElementById("probBarShort");
    if (longBar) longBar.style.width = `${Math.max(0, Math.min(100, longPct))}%`;
    if (shortBar) shortBar.style.width = `${Math.max(0, Math.min(100, shortPct))}%`;
    const list = document.getElementById("probFactors");
    if (list) {
      list.innerHTML = "";
      (prob.factors || []).forEach((f) => {
        const item = document.createElement("li");
        const signed = Number(f.signed_score ?? 0);
        const sign = signed > 0 ? "+" : "";
        item.textContent = `${f.label ?? f.name}: ${sign}${signed} (${f.reason})`;
        list.appendChild(item);
      });
    }
  } else {
    setText("probLong", "not available");
    setText("probShort", "not available");
    setText("probEdge", "Edge not available");
    setText("probConfidence", "Confidence not available");
    const longBar = document.getElementById("probBarLong");
    const shortBar = document.getElementById("probBarShort");
    if (longBar) longBar.style.width = "0%";
    if (shortBar) shortBar.style.width = "0%";
    const list = document.getElementById("probFactors");
    if (list) list.innerHTML = "";
  }

  if (brief.mini_chart && brief.mini_chart.candles) {
    initChart();
    if (candleSeries) {
      candleSeries.setData(brief.mini_chart.candles);
      clearLevelLines();
      const levels = brief.mini_chart.levels || {};
      addLevelLine(levels.critical, "#3b82f6", "Critical");
      addLevelLine(levels.support, "#22c55e", "Support");
      addLevelLine(levels.resistance, "#ef4444", "Resistance");
      addLevelLine(levels.range_low, "rgba(34,197,94,0.5)", "Range Low");
      addLevelLine(levels.range_high, "rgba(239,68,68,0.5)", "Range High");
      chart.timeScale().fitContent();
    }
  }

  setText("playbookLong", "Sweep + reclaim -> LONG");
  setText("playbookShort", "Break below -> SHORT continuation");
  setText("longCondition", brief.setups?.long?.condition ?? "pending");
  setText("longEntry", fmtUsdcCompact(brief.setups?.long?.entry, "pending"));
  setText("longStop", fmtUsdcCompact(brief.setups?.long?.stop, "pending"));
  setText("longTarget", fmtUsdcCompact(brief.setups?.long?.target, "pending"));
  setText("shortCondition", brief.setups?.short?.condition ?? "pending");
  setText("shortEntry", fmtUsdcCompact(brief.setups?.short?.entry, "pending"));
  setText("shortStop", fmtUsdcCompact(brief.setups?.short?.stop, "pending"));
  setText("shortTarget", fmtUsdcCompact(brief.setups?.short?.target, "pending"));
  const longRR = computeRR(brief.setups?.long?.entry, brief.setups?.long?.stop, brief.setups?.long?.target);
  const shortRR = computeRR(brief.setups?.short?.entry, brief.setups?.short?.stop, brief.setups?.short?.target);
  setText("longRR", hasNumber(longRR) ? longRR.toFixed(2) : "pending");
  setText("shortRR", hasNumber(shortRR) ? shortRR.toFixed(2) : "pending");

  if (brief.tp_plan_long && brief.tp_plan_long.length >= 3) {
    setText("tp1L", `TP1 ${fmtUsdcCompact(brief.tp_plan_long[0].price)} (${(brief.tp_plan_long[0].size_pct * 100).toFixed(0)}%)`);
    setText("tp2L", `TP2 ${fmtUsdcCompact(brief.tp_plan_long[1].price)} (${(brief.tp_plan_long[1].size_pct * 100).toFixed(0)}%)`);
    setText("tp3L", `TP3 ${fmtUsdcCompact(brief.tp_plan_long[2].price)} (${(brief.tp_plan_long[2].size_pct * 100).toFixed(0)}%)`);
  }
  if (brief.tp_plan_short && brief.tp_plan_short.length >= 3) {
    setText("tp1S", `TP1 ${fmtUsdcCompact(brief.tp_plan_short[0].price)} (${(brief.tp_plan_short[0].size_pct * 100).toFixed(0)}%)`);
    setText("tp2S", `TP2 ${fmtUsdcCompact(brief.tp_plan_short[1].price)} (${(brief.tp_plan_short[1].size_pct * 100).toFixed(0)}%)`);
    setText("tp3S", `TP3 ${fmtUsdcCompact(brief.tp_plan_short[2].price)} (${(brief.tp_plan_short[2].size_pct * 100).toFixed(0)}%)`);
  }

  setText("contextCapitalTotal", `Capital total: ${fmtUsdc(brief.capital?.total, "not available")}`);
  setText("contextCapitalActive", `Capital active: ${fmtUsdc(brief.capital?.active, "not available")}`);

  const contextReason = biasReasonRaw;
  setText("marketContext", contextReason);
  const marketContextEl = document.getElementById("marketContext");
  if (marketContextEl) marketContextEl.textContent = String(contextReason || "pending").toUpperCase();

  const liquidityRaw = String(brief.liquidity_distance?.asymmetry ?? "pending");
  const liquidityText = liquidityRaw.toUpperCase();
  const liquidityTone = liquidityRaw === "bullish" ? "green" : liquidityRaw === "bearish" ? "red" : "gray";
  setBadge("marketLiquidityBadge", liquidityText, liquidityTone);

  const volRaw = String(brief.market_state?.volatility ?? "pending");
  const volText = volRaw.toUpperCase();
  const volTone = volRaw === "up" ? "orange" : volRaw === "flat" || volRaw === "normal" ? "blue" : volRaw === "down" ? "gray" : "gray";
  setBadge("marketVolatilityBadge", volText, volTone);

  let derivativesState = "NEUTRAL";
  let derivativesTone = "gray";
  if (brief.derivatives) {
    if (brief.derivatives.funding_current_pct > 0.03) {
      derivativesState = "BULLISH";
      derivativesTone = "green";
    } else if (brief.derivatives.funding_current_pct < -0.03) {
      derivativesState = "BEARISH";
      derivativesTone = "red";
    } else if (brief.derivatives.oi_change_24h_pct < 0) {
      derivativesState = "DELEVERAGING";
      derivativesTone = "orange";
    } else {
      derivativesState = "NEUTRAL";
      derivativesTone = "gray";
    }
  } else {
    derivativesState = "PENDING";
    derivativesTone = "gray";
  }
  setBadge("marketDerivativesBadge", derivativesState, derivativesTone);

  const levelEvent = deriveLevelEventBadge(brief);
  setBadge("marketLevelEventBadge", levelEvent.label, levelEvent.tone);

  setText("execPosUsd", hasNumber(brief.position_size?.usdc) ? `${fmt(brief.position_size.usdc)} USDC` : "--");
  setText("execRisk", hasNumber(brief.position_size?.risk_per_trade) ? `${fmt(brief.position_size.risk_per_trade)} USDC` : "--");
  const riskPctTotal =
    hasNumber(brief.position_size?.risk_per_trade) && hasNumber(brief.capital?.total) && Number(brief.capital.total) > 0
      ? (Number(brief.position_size.risk_per_trade) / Number(brief.capital.total)) * 100
      : null;
  setText("execRiskPct", hasNumber(riskPctTotal) ? `${fmt(riskPctTotal)}%` : "--");
  setText("execExposureActive", hasNumber(brief.position_size?.exposure_active_pct) ? `${fmt(brief.position_size.exposure_active_pct)}%` : "--");
  setText("execExposureTotal", hasNumber(brief.position_size?.exposure_total_pct) ? `${fmt(brief.position_size.exposure_total_pct)}%` : "--");
  const activeBar = document.getElementById("execExposureActiveBar");
  const totalBar = document.getElementById("execExposureTotalBar");
  if (activeBar) activeBar.style.width = `${clampPct(brief.position_size?.exposure_active_pct)}%`;
  if (totalBar) totalBar.style.width = `${clampPct(brief.position_size?.exposure_total_pct)}%`;
  const estimatedCostPct =
    brief.trade?.estimated_cost_pct ??
    brief.trade?.cost_pct ??
    brief.trade?.filters?.estimated_cost_pct ??
    brief.trade?.filters?.cost_pct;
  setText("execCost", hasNumber(estimatedCostPct) ? `${fmt(estimatedCostPct)}%` : "pending");
  const gateIsOpen = Boolean(gateOpen);
  const stopLine = document.getElementById("execStopLine");
  const entryLine = document.getElementById("execEntryLine");
  const stopCandidateLine = document.getElementById("execStopCandidateLine");
  if (!gateIsOpen) {
    setText("execStopLabel", "Awaiting trigger");
    setText("execStop", fmtUsdc(brief.critical_level, "pending"));
    if (entryLine) entryLine.classList.add("hidden-line");
    if (stopCandidateLine) stopCandidateLine.classList.add("hidden-line");
    if (stopLine) stopLine.classList.remove("hidden-line");
  } else {
    setText("execStopLabel", "Stop distance");
    setText("execStop", hasNumber(brief.trade?.stop_distance_pct) ? `${fmt(brief.trade.stop_distance_pct)}%` : "waiting trigger");
    if (entryLine) entryLine.classList.remove("hidden-line");
    if (stopCandidateLine) stopCandidateLine.classList.remove("hidden-line");
  }
  setText(
    "tpRuleL",
    buildTpRuleText(
      "LONG",
      brief.setups?.long?.entry,
      brief.tp_plan_long?.[0],
      brief.position_size?.usdc,
      estimatedCostPct
    )
  );
  setText(
    "tpRuleS",
    buildTpRuleText(
      "SHORT",
      brief.setups?.short?.entry,
      brief.tp_plan_short?.[0],
      brief.position_size?.usdc,
      estimatedCostPct
    )
  );
  setText("execEntry", hasNumber(brief.trade?.entry) ? fmtUsdc(brief.trade.entry) : "pending");
  setText("execStopCandidate", hasNumber(brief.trade?.stop) ? fmtUsdc(brief.trade.stop) : "pending");

  const activeSetup = brief.trade?.active_setup ?? "NONE";
  const action = deriveCurrentAction(brief, scoreValue);
  syncTpDetails(Boolean(gateOpen), activeSetup);
  setText("decisionAction", action);
  setText("decisionLevel", `Watch level: ${fmtUsdcCompact(brief.critical_level, "not available")}`);
  setText("decisionTriggerDistanceValue", fmtSignedPct(brief.critical_level_distance_pct));
  applyScenarioHighlight(activeSetup);
  applyBiasHint(activeSetup, biasReasonRaw);

  let status = "WATCH";
  if (activeSetup === "LONG" || activeSetup === "SHORT") status = "SETUP ACTIVE";
  else if (!Boolean(brief.setup_score?.trade_gate) && Number(scoreValue ?? 0) < 6) status = "NO SETUP";
  const statusAction = buildStatusActionLine(status, action, brief.critical_level);
  setStatus(status, contextReason, statusAction);

  if (soundEnabled && shouldAlert(brief)) {
    const sig = buildAlertSignature(brief);
    if (sig !== lastAlertSignature) {
      lastAlertSignature = sig;
      playBeep();
    }
  }

  const why = [];
  if (brief.trade?.vwap_side === "below") why.push("Price below VWAP");
  if (brief.liquidity_distance?.asymmetry === "bearish") why.push("Near lower liquidity");
  if (brief.market_bias?.bias === "TREND") why.push("Trend market");
  if (brief.derivatives && brief.derivatives.oi_change_24h_pct < 0) why.push("OI decreasing");
  const whyList = document.getElementById("whyList");
  if (whyList) {
    whyList.innerHTML = "";
    if (!why.length) {
      const chip = document.createElement("div");
      chip.className = "why-chip";
      chip.textContent = "No strong confluence";
      whyList.appendChild(chip);
    } else {
      why.forEach((w) => {
        const chip = document.createElement("div");
        chip.className = "why-chip";
        chip.textContent = w;
        whyList.appendChild(chip);
      });
    }
  }
}

async function refresh() {
  const brief = await fetchBrief(selectedSymbol);
  render(brief);
  if (brief?.symbol) {
    setSelectedSymbol(brief.symbol);
  }
}

document.getElementById("refreshNow").addEventListener("click", async () => {
  const btn = document.getElementById("refreshNow");
  btn.disabled = true;
  const old = btn.textContent;
  btn.textContent = "Refreshing...";
  await fetch("/api/refresh", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ symbol: selectedSymbol }),
  });
  await refresh();
  await refreshScanner();
  const now = new Date();
  setText("lastUpdate", `Last update: ${now.toISOString().slice(11, 16)} UTC`);
  setNextRefresh(now);
  btn.textContent = old;
  btn.disabled = false;
});

document.getElementById("refreshSelect").addEventListener("change", async (e) => {
  const value = parseInt(e.target.value, 10);
  refreshIntervalSec = value;
  await fetch("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_interval: value }),
  });
  setNextRefresh(new Date());
});

setInterval(refresh, 10000);
setInterval(refreshScanner, 30000);
setInterval(() => {
  updateRefreshProgress();
  updateLastUpdateFreshness();
}, 1000);

const initialSymbol = new URL(window.location.href).searchParams.get("symbol");
if (initialSymbol) {
  setSelectedSymbol(initialSymbol);
}
initScannerFilters();
refresh();
refreshScanner();

fetchConfig().then((cfg) => {
  const select = document.getElementById("refreshSelect");
  if (cfg && cfg.refresh_interval) {
    refreshIntervalSec = cfg.refresh_interval;
    if (select) select.value = String(cfg.refresh_interval);
  }
  setNextRefresh(new Date());
});

initSoundToggle();

