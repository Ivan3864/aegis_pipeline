let tempChart = null;
let brightChart = null;

const maxPoints = 60;
let livePaused = false;

function setPill(el, state) {
  if (!el) return;
  el.classList.remove("pill-muted", "pill-ok", "pill-warn", "pill-bad");
  if (state === "ok") el.classList.add("pill-ok");
  else if (state === "warn") el.classList.add("pill-warn");
  else if (state === "bad") el.classList.add("pill-bad");
  else el.classList.add("pill-muted");
}

function safeSetTextById(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
  return !!el;
}

function initCharts() {
  const commonOpts = {
    responsive: true,
    animation: false,
    scales: {
      x: { grid: { display: false }, ticks: { maxTicksLimit: 6, font: { size: 10 } } },
      y: { grid: { color: "rgba(148,163,184,0.3)" }, ticks: { font: { size: 10 } } }
    },
    plugins: { legend: { display: false }, tooltip: { mode: "index", intersect: false } }
  };

  const tEl = document.getElementById("temp_chart");
  if (tEl) {
    tempChart = new Chart(tEl.getContext("2d"), {
      type: "line",
      data: { labels: [], datasets: [{ data: [], borderWidth: 2, pointRadius: 0, tension: 0.25 }] },
      options: commonOpts
    });
  }

  const bEl = document.getElementById("bright_chart");
  if (bEl) {
    brightChart = new Chart(bEl.getContext("2d"), {
      type: "bar",
      data: { labels: [], datasets: [{ data: [], borderWidth: 1 }] },
      options: Object.assign({}, commonOpts, {
        scales: Object.assign({}, commonOpts.scales, {
          x: { grid: { display: false }, ticks: { maxTicksLimit: 6, font: { size: 9 } } }
        })
      })
    });
  }
}

function addPoint(chart, label, value) {
  if (!chart) return;
  const data = chart.data;
  data.labels.push(label);
  data.datasets[0].data.push(value);

  if (data.labels.length > maxPoints) {
    data.labels.shift();
    data.datasets[0].data.shift();
  }
  chart.update();
}

function riskToPillState(label) {
  const l = String(label || "").toLowerCase();
  if (l === "normal") return "ok";
  if (l === "elevated") return "warn";
  if (l === "high") return "bad";
  return "muted";
}

function setRiskUI(aiRisk, n1) {
  // prefer ai_risk, fallback to node-1.ai, fallback to ai_summary
  let score = null;
  let label = "idle";

  if (aiRisk && typeof aiRisk.score === "number") {
    score = aiRisk.score;
    label = aiRisk.label || "idle";
  } else if (n1 && n1.ai && typeof n1.ai.score === "number") {
    score = n1.ai.score;
    label = n1.ai.label || "idle";
  }

  if (score === null) {
    safeSetTextById("stat_risk", "No score");
    safeSetTextById("node1_risk", "--");
    const rp = document.getElementById("risk_pill");
    setPill(rp, "muted");
    if (rp) rp.textContent = "AI risk idle";
    return;
  }

  const scoreText = `${score} / 100 (${label})`;
  safeSetTextById("stat_risk", scoreText);
  safeSetTextById("node1_risk", scoreText);

  const rp = document.getElementById("risk_pill");
  setPill(rp, riskToPillState(label));
  if (rp) rp.textContent = `AI risk ${label}`;
}

async function refresh() {
  const backendPill = document.getElementById("backend_status");

  if (livePaused) {
    setPill(backendPill, "warn");
    if (backendPill) backendPill.textContent = "Live updates paused";
    return;
  }

  try {
    const res = await fetch("/api/state", { cache: "no-store" });

    if (res.status === 401) {
      setPill(backendPill, "bad");
      if (backendPill) backendPill.textContent = "Session expired — login again";
      return;
    }
    if (!res.ok) throw new Error("HTTP " + res.status);

    const data = await res.json();

    // Backend status
    if (data.backend_ok) {
      setPill(backendPill, "ok");
      if (backendPill) backendPill.textContent = data.mqtt_connected ? "Backend online" : "Backend online (MQTT offline)";
    } else {
      setPill(backendPill, "bad");
      if (backendPill) backendPill.textContent = "Backend not ready";
    }

    const nowLabel = new Date().toLocaleTimeString();

    // ===== NODE 1 =====
    const n1 = data["node-1"];
    const node1Pill = document.getElementById("node1_pill");
    const node1StatusLabel = document.getElementById("node1_status_label");

    if (n1 && n1.sensors) {
      const t = n1.sensors.temp_c;
      const b = n1.sensors.brightness_pct;

      safeSetTextById("temp_value", (typeof t === "number") ? t.toFixed(2) + " °C" : "--");
      safeSetTextById("bright_value", (typeof b === "number") ? Math.round(b) + " %" : "--");
      safeSetTextById("node1_ts", n1.ts_iso || "--");

      safeSetTextById("stat_temp", (typeof t === "number") ? t.toFixed(1) + " °C" : "--");
      safeSetTextById("stat_bright", (typeof b === "number") ? Math.round(b) + " %" : "--");

      setPill(node1Pill, "ok");
      if (node1Pill) node1Pill.textContent = "Node 1 online";
      if (node1StatusLabel) node1StatusLabel.textContent = n1.status || "Online";

      if (typeof t === "number") addPoint(tempChart, nowLabel, t);
      if (typeof b === "number") addPoint(brightChart, nowLabel, b);
    } else {
      setPill(node1Pill, "bad");
      if (node1Pill) node1Pill.textContent = "No telemetry";
      if (node1StatusLabel) node1StatusLabel.textContent = (n1 && n1.status) ? n1.status : "No data";
      safeSetTextById("node1_ts", "--");
    }

    // ===== NODE 2 =====
    const n2 = data["node-2"];
    const node2Pill = document.getElementById("node2_pill");
    const node2StatusLabel = document.getElementById("node2_status_label");

    if (n2 && (n2.camera || n2.status)) {
      const statusText = (n2.camera && n2.camera.status) ? n2.camera.status : (n2.status || "online");

      safeSetTextById("cam_status", statusText);
      safeSetTextById("node2_ts", n2.ts_iso || "--");
      if (node2StatusLabel) node2StatusLabel.textContent = statusText;

      if (String(statusText).toLowerCase().includes("online")) setPill(node2Pill, "ok");
      else setPill(node2Pill, "warn");

      if (node2Pill) node2Pill.textContent = "Node 2 " + statusText;
    } else {
      safeSetTextById("cam_status", "offline");
      safeSetTextById("node2_ts", "--");
      if (node2StatusLabel) node2StatusLabel.textContent = "Offline";
      setPill(node2Pill, "bad");
      if (node2Pill) node2Pill.textContent = "Node 2 offline";
    }

    // ===== AI RISK (YOUR HTML IDs) =====
    setRiskUI(data.ai_risk || null, n1 || null);

  } catch (err) {
    console.log("refresh error", err);
    setPill(backendPill, "bad");
    if (backendPill) backendPill.textContent = "Backend unreachable";
  }
}

function resetCharts() {
  if (tempChart) {
    tempChart.data.labels = [];
    tempChart.data.datasets[0].data = [];
    tempChart.update();
  }
  if (brightChart) {
    brightChart.data.labels = [];
    brightChart.data.datasets[0].data = [];
    brightChart.update();
  }
}

document.addEventListener("DOMContentLoaded", () => {
  initCharts();

  const pauseBtn = document.getElementById("btn_pause");
  const resetBtn = document.getElementById("btn_reset");

  if (pauseBtn) {
    pauseBtn.addEventListener("click", () => {
      livePaused = !livePaused;
      const span = pauseBtn.querySelector("span");
      const icon = pauseBtn.querySelector("i");

      if (livePaused) {
        if (span) span.textContent = "Resume live";
        if (icon) icon.classList.replace("bx-pause-circle", "bx-play-circle");
      } else {
        if (span) span.textContent = "Pause live";
        if (icon) icon.classList.replace("bx-play-circle", "bx-pause-circle");
        refresh();
      }
    });
  }

  if (resetBtn) resetBtn.addEventListener("click", resetCharts);

  refresh();
  setInterval(refresh, 2000);
});
