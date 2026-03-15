let refreshIntervalSec = 300;
let soundEnabled = false;
let notifyEnabled = false;
let lastAlertSignature = null;
let chart = null;
let candleSeries = null;
let levelLines = [];
let lastRenderTs = Date.now();
let selectedSymbol = localStorage.getItem("selectedSymbol") || "BTC/USDC";
let scannerFilter = "all";
let scannerSort = localStorage.getItem("scannerSort") || "opportunity_desc";
let quoteCurrency = "USDC";
let latestModelPrice = null;
let latestLivePrice = null;
let latestLiveUpdatedAt = null;
let currentPairAttention = null;
let universeAttention = null;
let lastAttentionState = null;
let unreadEvents = 0;
let detailRefreshTimer = null;
const BASE_TITLE = "Trading Brief Dashboard";

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

function initNotifyToggle() {
  const toggle = document.getElementById("notifyToggle");
  if (!toggle) return;
  notifyEnabled = localStorage.getItem("notifyEnabled") === "true";
  toggle.textContent = `Notify: ${notifyEnabled ? "ON" : "OFF"}`;
  toggle.addEventListener("click", async () => {
    const next = !notifyEnabled;
    if (next && "Notification" in window && Notification.permission === "default") {
      try {
        await Notification.requestPermission();
      } catch (err) {
        // ignore permission errors
      }
    }
    if (next && "Notification" in window && Notification.permission === "denied") {
      notifyEnabled = false;
    } else {
      notifyEnabled = next;
    }
    localStorage.setItem("notifyEnabled", notifyEnabled ? "true" : "false");
    toggle.textContent = `Notify: ${notifyEnabled ? "ON" : "OFF"}`;
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
  const sortSelect = document.getElementById("scannerSort");
  if (sortSelect) {
    sortSelect.value = scannerSort;
    sortSelect.addEventListener("change", async () => {
      scannerSort = sortSelect.value || "opportunity_desc";
      localStorage.setItem("scannerSort", scannerSort);
      await refreshScanner();
    });
  }
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

function buildFaviconDataUrl(color) {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><circle cx="32" cy="32" r="28" fill="${color}"/><circle cx="24" cy="22" r="8" fill="rgba(255,255,255,0.35)"/></svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

function setFaviconColor(color) {
  let link = document.getElementById("dynamicFavicon");
  if (!link) {
    link = document.createElement("link");
    link.id = "dynamicFavicon";
    link.rel = "icon";
    document.head.appendChild(link);
  }
  link.href = buildFaviconDataUrl(color);
}

function attentionStateFrom(action, status, gateOpen, symbol) {
  if (action === "LONG ACTIVE" || action === "SHORT ACTIVE") {
    return { tone: "green", icon: "🟢", label: action, color: "#22c55e", symbol };
  }
  if (action === "WATCH") {
    return { tone: "orange", icon: "🟠", label: "WATCH", color: "#f59e0b", symbol };
  }
  if (!gateOpen && status === "NO SETUP") {
    return { tone: "red", icon: "🔴", label: "BLOCKED", color: "#ef4444", symbol };
  }
  return { tone: "gray", icon: "⚪", label: action || "WAIT", color: "#64748b", symbol };
}

function isMajorAttentionTransition(prevState, nextState) {
  if (!prevState) return false;
  const becameActive = prevState.label !== nextState.label && (nextState.label === "LONG ACTIVE" || nextState.label === "SHORT ACTIVE");
  const becameWatch = prevState.label !== "WATCH" && nextState.label === "WATCH";
  const changedTone = prevState.tone !== nextState.tone && (nextState.tone === "green" || nextState.tone === "orange");
  return becameActive || becameWatch || changedTone;
}

function applyAttentionState(next) {
  if (!next) return;
  setFaviconColor(next.color);
  if (document.hidden && isMajorAttentionTransition(lastAttentionState, next)) {
    unreadEvents += 1;
    if (notifyEnabled && "Notification" in window && Notification.permission === "granted") {
      try {
        new Notification(`${next.label} on ${symbol}`, {
          body: `State changed to ${next.label}. Open dashboard for details.`,
          silent: true,
        });
      } catch (err) {
        // ignore notification errors
      }
    }
  }
  const unreadPrefix = unreadEvents > 0 ? `(${unreadEvents}) ` : "";
  document.title = `${unreadPrefix}${next.icon} [${next.label}] ${next.symbol} · ${BASE_TITLE}`;
  lastAttentionState = next;
}

function syncAttentionUI() {
  const source = universeAttention || currentPairAttention;
  if (!source) return;
  applyAttentionState(source);
}

function updateAttentionUI(action, status, gateOpen, symbol) {
  currentPairAttention = attentionStateFrom(action, status, gateOpen, symbol);
  syncAttentionUI();
}

function buildUniverseAttention(rows) {
  if (!rows || !rows.length) return null;
  const ranked = [...rows].sort((a, b) => {
    const rank = (row) => {
      const isEngine = row.fast_mode !== true;
      if (isEngine && (row.action === "LONG ACTIVE" || row.action === "SHORT ACTIVE")) return 0;
      if (isEngine && row.gate_open) return 1;
      if (isEngine && row.action === "WATCH") return 2;
      if (row.interesting && hasNumber(row.opportunity_score) && Number(row.opportunity_score) >= 70) return 3;
      if (row.interesting) return 4;
      return 5;
    };
    const ra = rank(a);
    const rb = rank(b);
    if (ra !== rb) return ra - rb;
    const oppA = hasNumber(a.opportunity_score) ? Number(a.opportunity_score) : -1;
    const oppB = hasNumber(b.opportunity_score) ? Number(b.opportunity_score) : -1;
    if (oppA !== oppB) return oppB - oppA;
    return String(a.symbol || "").localeCompare(String(b.symbol || ""));
  });
  const best = ranked[0];
  if (!best) return null;
  const isEngine = best.fast_mode !== true;
  if (isEngine && (best.action === "LONG ACTIVE" || best.action === "SHORT ACTIVE")) {
    return { tone: "green", icon: "🟢", label: `${best.action}`, color: "#22c55e", symbol: best.symbol };
  }
  if (isEngine && best.gate_open) {
    return { tone: "green", icon: "🟢", label: "GATE OPEN", color: "#16a34a", symbol: best.symbol };
  }
  if (isEngine && best.action === "WATCH") {
    return { tone: "orange", icon: "🟠", label: "WATCH", color: "#f59e0b", symbol: best.symbol };
  }
  if (best.interesting && hasNumber(best.opportunity_score) && Number(best.opportunity_score) >= 70) {
    return { tone: "orange", icon: "🟠", label: "SCAN HOT", color: "#f59e0b", symbol: best.symbol };
  }
  if (best.interesting) {
    return { tone: "orange", icon: "🟠", label: "SCAN WATCH", color: "#f59e0b", symbol: best.symbol };
  }
  return { tone: "gray", icon: "⚪", label: "SCAN OK", color: "#64748b", symbol: best.symbol || selectedSymbol };
}

async function fetchBrief(symbol = null) {
  const params = new URLSearchParams();
  if (symbol) params.set("symbol", symbol);
  params.set("_ts", String(Date.now()));
  const res = await fetch(`/api/brief?${params.toString()}`, { cache: "no-store" });
  return res.json();
}

async function fetchConfig() {
  const res = await fetch("/api/config");
  return res.json();
}

async function fetchScannerList() {
  const res = await fetch(`/api/scanner/list?_ts=${Date.now()}`, { cache: "no-store" });
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
  return `${fmt(n)} ${quoteCurrency}`;
}

function fmtPriceCompact(n, fallback = "pending") {
  if (!hasNumber(n)) return fallback;
  const v = Number(n);
  if (Math.abs(v) >= 10000) {
    return v.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 });
  }
  return v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtPriceAdaptive(n, fallback = "pending") {
  if (!hasNumber(n)) return fallback;
  const v = Number(n);
  const abs = Math.abs(v);
  let decimals = 2;
  if (abs >= 10000) decimals = 0;
  else if (abs >= 1000) decimals = 1;
  else if (abs >= 1) decimals = 2;
  else if (abs >= 0.1) decimals = 4;
  else if (abs >= 0.01) decimals = 5;
  else if (abs >= 0.001) decimals = 6;
  else decimals = 7;
  return v.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

function fmtPriceForPriceCard(n, fallback = "pending") {
  if (!hasNumber(n)) return fallback;
  const v = Number(n);
  const abs = Math.abs(v);
  let decimals = 2;
  if (abs >= 1000) decimals = 2;
  else if (abs >= 1) decimals = 3;
  else if (abs >= 0.1) decimals = 4;
  else if (abs >= 0.01) decimals = 5;
  else decimals = 6;
  return v.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

function fmtUsdcCompact(n, fallback = "pending") {
  if (!hasNumber(n)) return fallback;
  return `${fmtPriceAdaptive(n)} ${quoteCurrency}`;
}

function fmtUsdcPriceCard(n, fallback = "pending") {
  if (!hasNumber(n)) return fallback;
  return `${fmtPriceForPriceCard(n)} ${quoteCurrency}`;
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

function fmtLevelQuality(q) {
  if (!q || !hasNumber(q.score)) return "Q--";
  return `Q${Number(q.score).toFixed(0)}`;
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

function deriveDerivativesState(derivatives) {
  if (!derivatives) return { state: "PENDING", tone: "gray" };
  const oi1 = hasNumber(derivatives.oi_change_1h_pct) ? Number(derivatives.oi_change_1h_pct) : null;
  const oi4 = hasNumber(derivatives.oi_change_4h_pct) ? Number(derivatives.oi_change_4h_pct) : null;
  if (oi1 !== null && oi4 !== null) {
    if (oi1 > 0 && oi4 > 0) return { state: "RELEVERAGING", tone: "green" };
    if (oi1 < 0 && oi4 < 0) return { state: "DELEVERAGING", tone: "orange" };
    return { state: "MIXED", tone: "blue" };
  }
  if (hasNumber(derivatives.oi_change_24h_pct) && Number(derivatives.oi_change_24h_pct) < 0) {
    return { state: "DELEVERAGING", tone: "orange" };
  }
  return { state: "NEUTRAL", tone: "gray" };
}

function buildMarketSummary(liquidityRaw, volRaw, derivativesState, levelEventLabel, longProbPct, shortProbPct) {
  const liq = String(liquidityRaw || "pending").toLowerCase();
  const vol = String(volRaw || "pending").toLowerCase();
  const der = String(derivativesState || "pending").toUpperCase();
  const evt = String(levelEventLabel || "NONE").toUpperCase();

  let reading = "Mixed market conditions with no clear edge yet.";
  let action = "Wait for stronger confirmation before acting.";

  if (evt.includes("CONFIRMED")) {
    reading = "A structural event is confirmed and can become actionable.";
    action = "Focus on playbook direction and execute only if trade gate is open.";
    return { reading, action };
  }

  if (evt.includes("DETECTED")) {
    reading = "A potential structural event is detected, but not fully confirmed.";
    action = "Stay in watch mode and wait for confirmation.";
    return { reading, action };
  }

  if (liq === "bearish" && (vol === "down" || vol === "flat") && (der === "DELEVERAGING" || der === "BEARISH")) {
    reading = "Sellers dominate, momentum is fading, and positioning is risk-off.";
    action = "Avoid forcing entries and wait for a clean reclaim or confirmed break setup.";
    return { reading, action };
  }

  if (liq === "bullish" && (vol === "up" || vol === "flat") && (der === "RELEVERAGING" || der === "BULLISH" || der === "NEUTRAL")) {
    reading = "Buy-side conditions are supportive with stable to improving momentum.";
    action = "Prioritize long scenarios only if gate and trigger conditions align.";
    if (hasNumber(longProbPct) && hasNumber(shortProbPct) && Number(shortProbPct) - Number(longProbPct) >= 15) {
      reading = "Context looks supportive, but directional probability currently favors SHORT.";
      action = "Treat long bias as conditional and wait for stronger long confirmation.";
    }
    return { reading, action };
  }

  if (der === "DELEVERAGING") {
    reading = "Open interest is contracting, which usually means weaker conviction.";
    action = "Reduce aggressiveness and wait for cleaner directional confirmation.";
    return { reading, action };
  }
  if (der === "RELEVERAGING") {
    reading = "Open interest is rebuilding on short-term horizons.";
    action = "Favor continuation setups, but only with clean trigger confirmation.";
    return { reading, action };
  }
  if (der === "MIXED") {
    reading = "Open interest is mixed across 1h/4h, so conviction is still uneven.";
    action = "Trade smaller or wait for alignment before increasing risk.";
    return { reading, action };
  }

  return { reading, action };
}

function buildStatusActionLine(status, currentAction, criticalLevel) {
  if (status === "SETUP ACTIVE" && currentAction === "LONG ACTIVE") return "execute LONG plan";
  if (status === "SETUP ACTIVE" && currentAction === "SHORT ACTIVE") return "execute SHORT plan";
  if (status === "AVOID") return "avoid entries until conditions improve";
  if (status === "NO SETUP") return "stand by, no setup active";
  if (currentAction === "WATCH") return `wait for trigger at ${fmtPriceAdaptive(criticalLevel, "--")} ${quoteCurrency}`;
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

function setupDeltaText(entry, stop, target) {
  if (!hasNumber(entry) || !hasNumber(stop) || !hasNumber(target) || Number(entry) === 0) {
    return "Stop distance: pending | Target distance: pending";
  }
  const stopAbs = Math.abs(Number(entry) - Number(stop));
  const targetAbs = Math.abs(Number(target) - Number(entry));
  const stopPct = (stopAbs / Math.abs(Number(entry))) * 100;
  const targetPct = (targetAbs / Math.abs(Number(entry))) * 100;
  return `Stop distance: ${fmtPriceAdaptive(stopAbs, "--")} (${fmt(stopPct)}%) | Target distance: ${fmtPriceAdaptive(targetAbs, "--")} (${fmt(targetPct)}%)`;
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
    cost_warn: "Costs elevated vs stop distance",
    vwap_mismatch: "VWAP condition not met",
    probability_below_threshold: "Directional probability below threshold",
    probability_below_heads_up_threshold: "Heads-up probability below threshold",
    liquidity_too_far: "Price too far from trigger zone",
    inversion_not_confirmed_2bars: "Inversion not confirmed (2 bars)",
    setup_score_below_threshold: "Setup score below threshold",
    no_active_setup: "No active setup",
    no_active_event: "No active level event",
  };
  if (txt.startsWith("cost_fail:") || txt.startsWith("cost_warn:")) {
    const prefix = txt.startsWith("cost_warn:") ? "cost_warn:" : "cost_fail:";
    const detail = txt.slice(prefix.length).trim();
    return `Costs too high vs stop distance (${detail || "details pending"})`;
  }
  if (map[txt]) return map[txt];
  const parts = txt.split(";").map((part) => {
    const p = part.trim();
    if (p.startsWith("cost_fail:") || p.startsWith("cost_warn:")) {
      const prefix = p.startsWith("cost_warn:") ? "cost_warn:" : "cost_fail:";
      const detail = p.slice(prefix.length).trim();
      return `Costs too high vs stop distance (${detail || "details pending"})`;
    }
    return map[p] || p;
  });
  return parts.join(" | ");
}

function buildCostHint(brief, reason, costReason, estimatedCostPct, stopDistancePct) {
  const filters = brief?.trade?.filters || {};
  const ratioL = hasNumber(filters.cost_ratio_long) ? Number(filters.cost_ratio_long) : null;
  const ratioS = hasNumber(filters.cost_ratio_short) ? Number(filters.cost_ratio_short) : null;
  const threshold = hasNumber(filters.cost_ratio_threshold) ? Number(filters.cost_ratio_threshold) : null;
  const activeSetup = String(brief?.trade?.active_setup || "NONE");
  if ((ratioL !== null || ratioS !== null) && threshold !== null) {
    const statusOf = (ratio) => {
      if (ratio === null) return "--";
      if (ratio > Math.max(1.0, threshold * 2)) return "BAD";
      if (ratio > threshold) return "WARN";
      return "OK";
    };
    const longStatus = statusOf(ratioL);
    const shortStatus = statusOf(ratioS);
    const longTxt = ratioL !== null ? `${ratioL.toFixed(2)} ${longStatus}` : "--";
    const shortTxt = ratioS !== null ? `${ratioS.toFixed(2)} ${shortStatus}` : "--";
    if (activeSetup === "LONG" || activeSetup === "SHORT") {
      return `Cost L/S: ${longTxt} | ${shortTxt} (thr ${threshold.toFixed(2)}) • active ${activeSetup}`;
    }
    return `Cost L/S: ${longTxt} | ${shortTxt} (thr ${threshold.toFixed(2)})`;
  }
  const txt = [String(reason || ""), String(costReason || "")].join(" ; ");
  const ratioMatch = txt.match(/cost\/stop\s+([0-9.]+)\s*>\s*([0-9.]+)/i);
  if (ratioMatch) {
    const ratio = Number(ratioMatch[1]);
    const threshold = Number(ratioMatch[2]);
    const status = ratio > threshold ? "WARN" : "OK";
    return `Cost ratio: ${ratio.toFixed(2)} / ${threshold.toFixed(2)} (${status})`;
  }
  const rrMatch = txt.match(/rr_net\s+([0-9.]+)\s*<\s*([0-9.]+)/i);
  if (rrMatch) {
    const rr = Number(rrMatch[1]);
    const min = Number(rrMatch[2]);
    return `Net R/R: ${rr.toFixed(2)} / ${min.toFixed(2)} (LOW)`;
  }
  if (hasNumber(estimatedCostPct) && hasNumber(stopDistancePct) && Number(stopDistancePct) > 0) {
    const ratio = Number(estimatedCostPct) / Number(stopDistancePct);
    const status = ratio > 0.35 ? "HIGH" : ratio > 0.28 ? "WARN" : "OK";
    return `Cost ratio: ${ratio.toFixed(2)} (${status})`;
  }
  if (/cost_fail/i.test(txt) || /cost_warn/i.test(txt) || /cost\/stop/i.test(txt)) {
    return "Cost ratio: above threshold";
  }
  return "Cost filter: not limiting";
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
  if (scannerFilter === "interesting") {
    return rows.filter((row) => row.interesting);
  }
  if (scannerFilter === "open") {
    return rows.filter((row) => row.gate_open);
  }
  if (scannerFilter === "active") {
    return rows.filter((row) => row.action === "LONG ACTIVE" || row.action === "SHORT ACTIVE");
  }
  if (scannerFilter === "watch") {
    return rows.filter((row) => row.action === "WATCH");
  }
  if (scannerFilter === "movers") {
    return rows.filter((row) => hasNumber(row.change_24h_pct) && Math.abs(Number(row.change_24h_pct)) >= 2.0);
  }
  if (scannerFilter === "liquid") {
    return rows.filter((row) => hasNumber(row.volume_usd) && Number(row.volume_usd) >= 100_000_000);
  }
  if (scannerFilter === "tight") {
    return rows.filter((row) => hasNumber(row.spread_pct) && Number(row.spread_pct) <= 0.06);
  }
  if (scannerFilter === "fresh") {
    return rows.filter((row) => hasNumber(row.freshness_sec) && Number(row.freshness_sec) <= 45);
  }
  return rows;
}

function applyScannerSort(rows) {
  const sorted = [...rows];
  sorted.sort((a, b) => {
    const n = (v, d = 0) => (hasNumber(v) ? Number(v) : d);
    const s = (v) => String(v || "");
    if (scannerSort === "engine_score_desc") return n(b.score, -1) - n(a.score, -1);
    if (scannerSort === "volume_desc") return n(b.volume_usd, -1) - n(a.volume_usd, -1);
    if (scannerSort === "move_abs_desc") return Math.abs(n(b.change_24h_pct, 0)) - Math.abs(n(a.change_24h_pct, 0));
    if (scannerSort === "spread_asc") return n(a.spread_pct, 999) - n(b.spread_pct, 999);
    if (scannerSort === "freshness_asc") return n(a.freshness_sec, 999999) - n(b.freshness_sec, 999999);
    if (scannerSort === "symbol_asc") return s(a.symbol).localeCompare(s(b.symbol));
    if (scannerSort === "symbol_desc") return s(b.symbol).localeCompare(s(a.symbol));
    return n(b.opportunity_score, -1) - n(a.opportunity_score, -1);
  });
  return sorted;
}

function renderScanner(data) {
  const summary = data?.summary || {};
  const allRows = data?.rows || [];
  universeAttention = buildUniverseAttention(allRows);
  syncAttentionUI();
  setText("scanUniverse", String(summary.universe_size ?? 0));
  setText("scanOpenGates", String(summary.open_gates ?? 0));
  setText("scanInteresting", String(summary.interesting ?? 0));
  setText("scanActiveSetups", String(summary.active_setups ?? 0));
  const selectedRow = allRows.find((row) => row.symbol === selectedSymbol);
  const livePrice = hasNumber(selectedRow?.live_price) ? selectedRow?.live_price : latestLivePrice;
  if (hasNumber(livePrice)) {
    let deltaSuffix = "";
    if (hasNumber(latestModelPrice) && Number(latestModelPrice) !== 0) {
      const deltaPct = ((Number(livePrice) - Number(latestModelPrice)) / Number(latestModelPrice)) * 100;
      const sign = deltaPct >= 0 ? "+" : "";
      if (Math.abs(deltaPct) >= 0.01) {
        deltaSuffix = ` (${sign}${deltaPct.toFixed(2)}%)`;
      } else if (Math.abs(deltaPct) > 0) {
        deltaSuffix = ` (${sign}${(deltaPct * 100).toFixed(1)} bps)`;
      } else {
        deltaSuffix = " (unchanged)";
      }
    }
    setText("priceLive", `Live: ${fmtUsdcPriceCard(livePrice)}${deltaSuffix}`);
  } else {
    setText("priceLive", "Live: unavailable");
  }

  const list = document.getElementById("scannerList");
  if (!list) return;
  const rows = applyScannerSort(applyScannerFilter(allRows));
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
    if (row.fast_mode) {
      badge.textContent = String(row.opportunity_label || "SCAN").replace("POTENTIAL ", "SCAN ");
    } else {
      badge.textContent = row.action || row.status || "PENDING";
    }
    top.appendChild(symbol);
    top.appendChild(badge);

    const labelLine = document.createElement("div");
    labelLine.className = "scanner-tag";
    labelLine.textContent = row.opportunity_label || (row.fast_mode ? "FAST SCAN" : "DETAILED");

    const meta = document.createElement("div");
    meta.className = "scanner-meta";
    const score = document.createElement("span");
    score.className = "scanner-score";
    if (row.fast_mode) {
      score.textContent = hasNumber(row.opportunity_score) ? `Opp ${Number(row.opportunity_score).toFixed(0)}/100` : "Opp pending";
    } else {
      score.textContent = hasNumber(row.score) ? `Score ${Number(row.score).toFixed(1)}/10` : "Score pending";
    }
    const dist = document.createElement("span");
    if (row.fast_mode) {
      const move = hasNumber(row.change_24h_pct) ? Number(row.change_24h_pct) : null;
      dist.className = `scanner-distance ${hasNumber(move) && move >= 0 ? "near" : "far"}`;
      dist.textContent = hasNumber(move) ? `${move >= 0 ? "+" : ""}${move.toFixed(2)}%` : "--";
    } else {
      const isNear = hasNumber(row.trigger_distance_pct) && Math.abs(Number(row.trigger_distance_pct)) <= 0.35;
      dist.className = `scanner-distance ${isNear ? "near" : "far"}`;
      dist.textContent = hasNumber(row.trigger_distance_pct) ? fmtSignedPct(row.trigger_distance_pct) : "--";
    }
    meta.appendChild(score);
    meta.appendChild(dist);

    card.appendChild(top);
    card.appendChild(labelLine);
    if (row.fast_mode && hasNumber(row.range_pos_pct)) {
      const range = document.createElement("div");
      range.className = "scanner-range";
      const fill = document.createElement("div");
      fill.className = "scanner-range-fill";
      fill.style.width = `${Math.max(0, Math.min(100, Number(row.range_pos_pct)))}%`;
      range.appendChild(fill);
      card.appendChild(range);
    }
    const stats = document.createElement("div");
    stats.className = "scanner-stats";
    const volumeTxt = hasNumber(row.volume_usd) ? `Vol ${(Number(row.volume_usd) / 1_000_000).toFixed(0)}M` : "Vol --";
    const spreadTxt = hasNumber(row.spread_pct) ? `Spread ${Number(row.spread_pct).toFixed(2)}%` : "Spread --";
    const freshTxt = hasNumber(row.freshness_sec) ? `Fresh ${Number(row.freshness_sec)}s` : "Fresh --";
    stats.textContent = `${volumeTxt} | ${spreadTxt} | ${freshTxt}`;
    card.appendChild(stats);
    card.appendChild(meta);
    card.addEventListener("click", async () => {
      setSelectedSymbol(row.symbol);
      await refresh(true);
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
  if (brief.symbol && String(brief.symbol).includes("/")) {
    const parts = String(brief.symbol).split("/");
    quoteCurrency = parts[1] || "USDC";
  } else {
    quoteCurrency = "USDC";
  }

  latestModelPrice = brief.price;
  latestLivePrice = hasNumber(brief.live_price) ? brief.live_price : null;
  latestLiveUpdatedAt = hasNumber(brief.live_updated_at) ? brief.live_updated_at : null;
  setText("price", fmtUsdcPriceCard(brief.price, "waiting for data"));
  const biasReasonRaw = compactContext(brief.market_bias?.reason ?? "pending");
  const biasMain = biasReasonRaw.toUpperCase();
  const biasKind = brief.market_bias?.bias ?? "PENDING";
  setText("marketBias", biasMain);
  setText("marketBiasSub", `Bias type: ${biasKind}`);
  setText("priceSub", "Model price (15m close)");
  setText("criticalLevel", fmtUsdcCompact(brief.critical_level, "not available"));
  setText("criticalLevelDist", `Distance: ${fmtSignedPct(brief.critical_level_distance_pct)}`);
  setText("criticalLevelType", `Trigger type: ${deriveTriggerType(brief.critical_level_distance_pct)}`);
  const srSourceRaw = String(brief.sr_levels_source || "config");
  const srMode =
    srSourceRaw === "manual_override" ? "MANUAL" : srSourceRaw === "auto_generated" ? "AUTO" : "CONFIG";
  const levelQuality = fmtLevelQuality(brief.critical_level_quality);
  const regime = String(brief.critical_regime || "range_pullback").replace("_", " ").toUpperCase();
  setText(
    "criticalLevelSource",
    `Source: ${String(brief.critical_level_source ?? "1h").toUpperCase()} | ${srMode} | ${levelQuality} | ${regime}`
  );

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
      addLevelLine(levels.critical_long, "rgba(34,197,94,0.75)", "Critical L");
      addLevelLine(levels.critical_short, "rgba(239,68,68,0.75)", "Critical S");
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
  setText("longDelta", setupDeltaText(brief.setups?.long?.entry, brief.setups?.long?.stop, brief.setups?.long?.target));
  setText("shortDelta", setupDeltaText(brief.setups?.short?.entry, brief.setups?.short?.stop, brief.setups?.short?.target));
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

  const derivativesSignal = deriveDerivativesState(brief.derivatives);
  const derivativesState = derivativesSignal.state;
  const derivativesTone = derivativesSignal.tone;
  setBadge("marketDerivativesBadge", derivativesState, derivativesTone);

  const levelEvent = deriveLevelEventBadge(brief);
  setBadge("marketLevelEventBadge", levelEvent.label, levelEvent.tone);
  const longProbPct = brief.directional_probability?.long_probability_pct;
  const shortProbPct = brief.directional_probability?.short_probability_pct;
  const marketSummary = buildMarketSummary(
    liquidityRaw,
    volRaw,
    derivativesState,
    levelEvent.label,
    longProbPct,
    shortProbPct
  );
  setText("marketSummaryReading", `Reading: ${marketSummary.reading}`);
  setText("marketSummaryAction", `Now: ${marketSummary.action}`);

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
  const costReason = brief.trade?.filters?.cost_reason;
  const stopDistancePct = hasNumber(brief.trade?.stop_distance_pct) ? Number(brief.trade.stop_distance_pct) : null;
  setText("execCost", hasNumber(estimatedCostPct) ? `${fmt(estimatedCostPct)}%` : "pending");
  setText("setupCostHint", buildCostHint(brief, gateReason, costReason, estimatedCostPct, stopDistancePct));
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
  updateAttentionUI(action, status, Boolean(gateOpen), brief.symbol || selectedSymbol);

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
  if (derivativesState === "DELEVERAGING") why.push("OI decreasing");
  if (derivativesState === "RELEVERAGING") why.push("OI rebuilding");
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

async function refresh(forceServerRecalc = false) {
  if (forceServerRecalc) {
    await fetch("/api/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol: selectedSymbol }),
    });
  }
  const brief = await fetchBrief(selectedSymbol);
  render(brief);
  if (brief?.symbol) {
    setSelectedSymbol(brief.symbol);
  }
}

function setupDetailRefreshTimer() {
  if (detailRefreshTimer) clearInterval(detailRefreshTimer);
  const ms = Math.max(60, Number(refreshIntervalSec || 300)) * 1000;
  detailRefreshTimer = setInterval(() => {
    refresh(true);
  }, ms);
}

document.getElementById("refreshNow").addEventListener("click", async () => {
  const btn = document.getElementById("refreshNow");
  btn.disabled = true;
  const old = btn.textContent;
  btn.textContent = "Refreshing...";
  await refresh(true);
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
  setupDetailRefreshTimer();
});

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
setupDetailRefreshTimer();

fetchConfig().then((cfg) => {
  const select = document.getElementById("refreshSelect");
  if (cfg && cfg.refresh_interval) {
    refreshIntervalSec = cfg.refresh_interval;
    if (select) select.value = String(cfg.refresh_interval);
  }
  setNextRefresh(new Date());
  setupDetailRefreshTimer();
});

initSoundToggle();
initNotifyToggle();
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    unreadEvents = 0;
    if (universeAttention || currentPairAttention) syncAttentionUI();
    else document.title = BASE_TITLE;
  }
});

