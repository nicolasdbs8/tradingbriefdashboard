let refreshIntervalSec = 300;
let soundEnabled = false;
let lastAlertSignature = null;
let chart = null;
let candleSeries = null;
let levelLines = [];

function initSoundToggle() {
  const toggle = document.getElementById("soundToggle");
  if (!toggle) return;
  soundEnabled = localStorage.getItem("soundEnabled") === "true";
  toggle.textContent = `Sound: ${soundEnabled ? "ON" : "OFF"}`;
  toggle.addEventListener("click", () => {
    soundEnabled = !soundEnabled;
    localStorage.setItem("soundEnabled", soundEnabled ? "true" : "false");
    toggle.textContent = `Sound: ${soundEnabled ? "ON" : "OFF"}`;
    if (soundEnabled) {
      playBeep();
    }
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

async function fetchBrief() {
  const res = await fetch("/api/brief");
  return res.json();
}

async function fetchConfig() {
  const res = await fetch("/api/config");
  return res.json();
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function setStatus(status, text, sub) {
  const bar = document.getElementById("statusBar");
  bar.classList.remove("status-watch", "status-active", "status-avoid", "status-none");
  if (status === "SETUP ACTIVE") bar.classList.add("status-active");
  else if (status === "AVOID") bar.classList.add("status-avoid");
  else if (status === "NO SETUP") bar.classList.add("status-none");
  else bar.classList.add("status-watch");
  setText("statusText", text);
  setText("statusSub", sub);
  bar.querySelector(".status-label").textContent = `STATUS: ${status}`;
}

function fmt(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return "not available";
  return n.toFixed(2);
}

function fmtPct(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return "not available";
  const rounded = Math.round(n * 10) / 10;
  if (Math.abs(rounded % 1) < 0.001) return `${rounded.toFixed(0)}%`;
  return `${rounded.toFixed(1)}%`;
}

function fmtSigned(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return "not available";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}`;
}

function setNextRefresh(now) {
  const next = new Date(now.getTime() + refreshIntervalSec * 1000);
  setText("nextRefresh", `Next refresh: ${next.toISOString().slice(11,16)} UTC`);
}

function initChart() {
  if (chart || !window.LightweightCharts) return;
  const container = document.getElementById("miniChart");
  if (!container) return;
  container.textContent = "";
  chart = LightweightCharts.createChart(container, {
    height: 140,
    layout: {
      background: { color: "transparent" },
      textColor: "#9fb0c0",
    },
    grid: {
      vertLines: { color: "rgba(255,255,255,0.05)" },
      horzLines: { color: "rgba(255,255,255,0.05)" },
    },
    rightPriceScale: {
      borderColor: "rgba(255,255,255,0.1)",
    },
    timeScale: {
      borderColor: "rgba(255,255,255,0.1)",
    },
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
  if (!candleSeries || price === null || price === undefined) return;
  const line = candleSeries.createPriceLine({
    price,
    color,
    lineWidth: 1,
    lineStyle: 2,
    title,
  });
  levelLines.push(line);
}

function render(brief) {
  if (brief.error) {
    setText("price", "Error");
    return;
  }
  const now = new Date();
  setText("lastUpdate", `Last update: ${now.toISOString().slice(11,16)} UTC`);
  setNextRefresh(now);
  setText("headerPair", `${brief.symbol} | ${brief.exchange}`);

  setText("price", fmt(brief.price));
  setText("marketBias", brief.market_bias?.bias ?? "not computed yet");
  setText("marketBiasSub", brief.market_bias?.reason ?? "not available");
  setText("criticalLevel", fmt(brief.critical_level));
  setText("criticalLevelDist", `${fmtSigned(brief.critical_level_distance_pct)}%`);
  setText("criticalLevelSource", `Source: ${brief.critical_level_source ?? "1h"}`);

  const biasBadge = document.getElementById("biasBadge");
  if (biasBadge) {
    const bias = brief.market_bias?.bias ?? "PENDING";
    biasBadge.textContent = bias;
    biasBadge.className = `badge ${bias === "TREND" ? "orange" : "gray"}`;
  }

  const scoreValue = brief.setup_score?.final ?? brief.setup_score?.total;
  if (scoreValue !== null && scoreValue !== undefined) {
    const pct = (scoreValue / 10) * 100;
    const fill = document.getElementById("setupScoreFill");
    if (fill) fill.style.width = `${pct}%`;
    setText("setupScoreValue", `${scoreValue} / 10`);
    const setupBadge = document.getElementById("setupBadge");
    if (setupBadge) {
      const label = brief.setup_score.class ?? brief.setup_score.quality ?? "pending";
      setupBadge.textContent = label;
      setupBadge.className = `badge ${label === "PRIORITY" ? "green" : label === "VALID" ? "orange" : label === "WATCHLIST" ? "gray" : "red"}`;
    }
    setText("setupGate", `Trade gate: ${brief.setup_score?.trade_gate ? "YES" : "NO"}`);
  } else {
    const fill = document.getElementById("setupScoreFill");
    if (fill) fill.style.width = "0%";
    setText("setupScoreValue", "not computed");
    const setupBadge = document.getElementById("setupBadge");
    if (setupBadge) {
      setupBadge.textContent = "pending";
      setupBadge.className = "badge gray";
    }
    setText("setupGate", "Trade gate: not available");
  }

  if (brief.directional_probability) {
    const prob = brief.directional_probability;
    setText("probLong", fmtPct(prob.long_probability_pct));
    setText("probShort", fmtPct(prob.short_probability_pct));
    setText("probEdge", `Edge ${prob.edge ?? "not available"}`);
    setText("probConfidence", `Confidence ${prob.confidence ?? "not available"}`);
    const longWidth = Math.max(0, Math.min(100, prob.long_probability_pct ?? 0));
    const shortWidth = Math.max(0, 100 - longWidth);
    const longBar = document.getElementById("probBarLong");
    const shortBar = document.getElementById("probBarShort");
    if (longBar) longBar.style.width = `${longWidth}%`;
    if (shortBar) shortBar.style.width = `${shortWidth}%`;
    const list = document.getElementById("probFactors");
    if (list) {
      list.innerHTML = "";
      (prob.factors || []).forEach((f) => {
        const item = document.createElement("li");
        const sign = f.signed_score > 0 ? "+" : "";
        item.textContent = `${f.label ?? f.name}: ${sign}${f.signed_score} (${f.reason})`;
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

  setText("playbookLong", `Sweep + reclaim -> LONG`);
  setText("playbookShort", `Break below -> SHORT continuation`);

  setText("longCondition", brief.setups?.long?.condition ?? "not available");
  setText("longEntry", fmt(brief.setups?.long?.entry));
  setText("longStop", fmt(brief.setups?.long?.stop));
  setText("longTarget", fmt(brief.setups?.long?.target));
  setText("shortCondition", brief.setups?.short?.condition ?? "not available");
  setText("shortEntry", fmt(brief.setups?.short?.entry));
  setText("shortStop", fmt(brief.setups?.short?.stop));
  setText("shortTarget", fmt(brief.setups?.short?.target));

  if (brief.tp_plan_long && brief.tp_plan_long.length >= 3) {
    setText("tp1L", `TP1 ${fmt(brief.tp_plan_long[0].price)} (${(brief.tp_plan_long[0].size_pct*100).toFixed(0)}%)`);
    setText("tp2L", `TP2 ${fmt(brief.tp_plan_long[1].price)} (${(brief.tp_plan_long[1].size_pct*100).toFixed(0)}%)`);
    setText("tp3L", `TP3 ${fmt(brief.tp_plan_long[2].price)} (${(brief.tp_plan_long[2].size_pct*100).toFixed(0)}%)`);
  }
  if (brief.tp_plan_short && brief.tp_plan_short.length >= 3) {
    setText("tp1S", `TP1 ${fmt(brief.tp_plan_short[0].price)} (${(brief.tp_plan_short[0].size_pct*100).toFixed(0)}%)`);
    setText("tp2S", `TP2 ${fmt(brief.tp_plan_short[1].price)} (${(brief.tp_plan_short[1].size_pct*100).toFixed(0)}%)`);
    setText("tp3S", `TP3 ${fmt(brief.tp_plan_short[2].price)} (${(brief.tp_plan_short[2].size_pct*100).toFixed(0)}%)`);
  }

  if (brief.derivatives) {
    setText("contextDerivatives", `Derivatives: ${brief.derivatives.oi_change_24h_pct < 0 ? "deleveraging" : "neutral"}`);
  } else {
    setText("contextDerivatives", "Derivatives: not available");
  }

  if (brief.level_event) {
    setText("marketLevelEvent", `Level event: ${brief.level_event.active_event || "none"}`);
  } else {
    setText("marketLevelEvent", "Level event: none");
  }

  setText("contextLiquidity", `Liquidity distance: ${fmt(brief.liquidity_distance?.below_pct)}% / ${fmt(brief.liquidity_distance?.above_pct)}%`);
  setText("contextCapital", `Capital: ${fmt(brief.capital?.total)} total / ${fmt(brief.capital?.active)} active`);

  setText("marketContext", `Price context: ${brief.market_bias?.reason ?? "not available"}`);
  setText("marketLiquidity", `Liquidity: ${brief.liquidity_distance?.asymmetry ?? "not available"}`);
  setText("marketVolatility", `Volatility: ${brief.market_state?.volatility ?? "not available"}`);
  const tf = brief.trade?.filters;
  setText(
    "marketFilters",
    `Filters: cost=${tf?.cost_pass ? "PASS" : "FAIL"} | vwap=${tf?.vwap_pass ? "PASS" : "FAIL"} | prob=${tf?.probability_pass ? "PASS" : "FAIL"} | inversion=${tf?.inversion_pass ? "PASS" : "FAIL"}`
  );

  let derivativesSummary = "Derivatives: not available";
  if (brief.derivatives) {
    if (brief.derivatives.funding_current_pct > 0.03) derivativesSummary = "Derivatives: long bias";
    else if (brief.derivatives.funding_current_pct < -0.03) derivativesSummary = "Derivatives: short bias";
    else if (brief.derivatives.oi_change_24h_pct < 0) derivativesSummary = "Derivatives: deleveraging";
    else derivativesSummary = "Derivatives: neutral";
  }
  setText("marketDerivatives", derivativesSummary);

  setText("execPosUsd", `${fmt(brief.position_size?.usdc)} USDC`);
  setText("execRisk", `${fmt(brief.position_size?.risk_per_trade)} USDC`);
  setText("execStop", `${fmt(brief.trade?.stop_distance_pct)}%`);
  setText("execExposureActive", `${fmt(brief.position_size?.exposure_active_pct)}%`);
  setText("execExposureTotal", `${fmt(brief.position_size?.exposure_total_pct)}%`);
  setText("execEntry", fmt(brief.trade?.entry));
  setText("execStopCandidate", fmt(brief.trade?.stop));

  const status = brief.trade?.active_setup === "NONE" ? "WATCH" : "SETUP ACTIVE";
  const action = brief.trade?.active_setup === "LONG" ? "LONG ACTIVE" : brief.trade?.active_setup === "SHORT" ? "SHORT ACTIVE" : "WAIT";
  setText("decisionStatus", `STATUS: ${status}`);
  setText("decisionAction", action);
  setText("decisionLevel", `Watch level: ${fmt(brief.critical_level)}`);

  setStatus(status, `${brief.market_bias?.reason ?? "Waiting for data"}`, `Watch ${fmt(brief.critical_level)} for trigger`);

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
    why.forEach((w) => {
      const chip = document.createElement("div");
      chip.className = "why-chip";
      chip.textContent = w;
      whyList.appendChild(chip);
    });
  }
}

async function refresh() {
  const brief = await fetchBrief();
  render(brief);
}

document.getElementById("refreshNow").addEventListener("click", async () => {
  const btn = document.getElementById("refreshNow");
  btn.disabled = true;
  const old = btn.textContent;
  btn.textContent = "Refreshing...";
  await fetch("/api/refresh", { method: "POST" });
  await refresh();
  const now = new Date();
  setText("lastUpdate", `Last update: ${now.toISOString().slice(11,16)} UTC`);
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
refresh();

fetchConfig().then((cfg) => {
  const select = document.getElementById("refreshSelect");
  if (cfg && cfg.refresh_interval) {
    refreshIntervalSec = cfg.refresh_interval;
    if (select) select.value = String(cfg.refresh_interval);
  }
  setNextRefresh(new Date());
});

initSoundToggle();

