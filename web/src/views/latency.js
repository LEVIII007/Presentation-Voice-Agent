import "../latency.css";

import { BACKEND_URL } from "../api.js";
import { h, mount } from "../dom.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const SERIES = [
  { key: "stt", label: "STT", color: "#5fd28d" },
  { key: "llm", label: "LLM", color: "#4da6ff" },
  { key: "tts", label: "TTS", color: "#f2b55f" },
];

function svg(tag, attrs = {}) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    el.setAttribute(key, String(value));
  }
  return el;
}

function fmtMs(value) {
  return value == null ? "-" : `${value.toFixed(1)} ms`;
}

function fmtSpan(seconds) {
  if (seconds == null) return "-";
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds % 60);
  return `${minutes}m ${remainder}s`;
}

function fmtDateTime(value) {
  if (!value) return "-";
  return new Date(value).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

async function request(path) {
  const res = await fetch(`${BACKEND_URL}${path}`);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch {}
    throw new Error(detail);
  }
  return res.json();
}

function statsRow(session) {
  const row = h("div", { class: "latency-stats" });
  for (const series of SERIES) {
    const stats = session.stats?.[series.key];
    if (!stats?.count) continue;
    row.append(
      h(
        "div",
        { class: "latency-stat-pill" },
        h("span", { class: "latency-stat-dot", style: `background:${series.color}` }),
        `${series.label}: avg ${fmtMs(stats.avg_ms)} | p95 ${fmtMs(stats.p95_ms)} | max ${fmtMs(stats.max_ms)} | n=${stats.count}`,
      ),
    );
  }
  if (!row.childElementCount) {
    row.append(h("div", { class: "latency-stat-pill" }, "No latency samples yet."));
  }
  return row;
}

function createChart(session) {
  const chartWrap = h("div", { class: "latency-chart" });
  const lines = SERIES.map((series) => ({
    ...series,
    points: session.series?.[series.key] || [],
  })).filter((series) => series.points.length);

  if (!lines.length) {
    chartWrap.append(h("div", { class: "latency-chart-empty" }, "No TTFB samples for this session yet."));
    return chartWrap;
  }

  const width = 920;
  const height = 300;
  const padding = { top: 18, right: 22, bottom: 38, left: 54 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const allPoints = lines.flatMap((series) => series.points);
  const maxX = Math.max(1, ...allPoints.map((point) => point.offset_s));
  const maxY = Math.max(250, ...allPoints.map((point) => point.ttfb_ms));
  const x = (value) => padding.left + (value / maxX) * plotWidth;
  const y = (value) => padding.top + plotHeight - (value / maxY) * plotHeight;

  const root = svg("svg", {
    class: "latency-svg",
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    "aria-label": `Latency chart for session ${session.session}`,
  });

  for (let i = 0; i <= 4; i += 1) {
    const tickValue = (maxY / 4) * i;
    const lineY = y(tickValue);
    root.append(svg("line", {
      x1: padding.left,
      y1: lineY,
      x2: width - padding.right,
      y2: lineY,
      class: "latency-grid",
    }));
    const label = svg("text", {
      x: padding.left - 10,
      y: lineY + 4,
      "text-anchor": "end",
      class: "latency-axis-text",
    });
    label.textContent = `${Math.round(tickValue)} ms`;
    root.append(label);
  }

  for (let i = 0; i <= 4; i += 1) {
    const tickValue = (maxX / 4) * i;
    const lineX = x(tickValue);
    root.append(svg("line", {
      x1: lineX,
      y1: padding.top,
      x2: lineX,
      y2: height - padding.bottom,
      class: "latency-grid latency-grid-vertical",
    }));
    const label = svg("text", {
      x: lineX,
      y: height - 12,
      "text-anchor": i === 0 ? "start" : i === 4 ? "end" : "middle",
      class: "latency-axis-text",
    });
    label.textContent = `${tickValue.toFixed(1)}s`;
    root.append(label);
  }

  root.append(svg("line", {
    x1: padding.left,
    y1: height - padding.bottom,
    x2: width - padding.right,
    y2: height - padding.bottom,
    class: "latency-axis",
  }));
  root.append(svg("line", {
    x1: padding.left,
    y1: padding.top,
    x2: padding.left,
    y2: height - padding.bottom,
    class: "latency-axis",
  }));

  for (const series of lines) {
    const polyline = svg("polyline", {
      fill: "none",
      stroke: series.color,
      "stroke-width": 2.5,
      "stroke-linecap": "round",
      "stroke-linejoin": "round",
      points: series.points.map((point) => `${x(point.offset_s)},${y(point.ttfb_ms)}`).join(" "),
    });
    root.append(polyline);

    for (const point of series.points) {
      const dot = svg("circle", {
        cx: x(point.offset_s),
        cy: y(point.ttfb_ms),
        r: 4,
        fill: series.color,
        class: "latency-point",
      });
      const title = svg("title");
      const modelLine = point.model ? `\n${point.model}` : "";
      title.textContent = `${series.label}\n${fmtMs(point.ttfb_ms)} at +${point.offset_s.toFixed(1)}s\n${point.processor}${modelLine}`;
      dot.append(title);
      root.append(dot);
    }
  }

  chartWrap.append(root);
  return chartWrap;
}

function sessionCard(session) {
  const counts = SERIES
    .map((series) => `${series.label}: ${session.stats?.[series.key]?.count || 0}`)
    .join(" | ");

  return h(
    "section",
    { class: "latency-session" },
    h(
      "div",
      { class: "latency-session-head" },
      h(
        "div",
        {},
        h("h3", {}, `Session ${session.session}`),
        h(
          "div",
          { class: "latency-session-meta" },
          `Started ${fmtDateTime(session.started_at)} | Span ${fmtSpan(session.span_s)} | Samples ${session.sample_count}`,
        ),
      ),
      h("div", { class: "latency-session-counts" }, counts),
    ),
    statsRow(session),
    createChart(session),
  );
}

export async function renderLatency() {
  const page = h("div", {
    class: "page latency-page",
    dataset: { surface: "product", subtitle: "Latency monitor" },
  }, h("div", { class: "spinner" }));
  mount(page);

  let report;
  try {
    report = await request("/api/latency/sessions");
  } catch (error) {
    mount(
      h(
        "div",
        { class: "page latency-page", dataset: { surface: "product", subtitle: "Latency monitor" } },
        h("div", { class: "banner error" }, `Could not load latency data: ${error.message}`),
        h("a", { href: "#/" }, "<- Back to decks"),
      ),
    );
    return;
  }

  const refresh = h("button", { class: "btn ghost sm", onClick: () => renderLatency() }, "Refresh");
  const sessionsWrap = h("div", { class: "latency-sessions" });

  if (!report.exists || !report.sessions.length) {
    sessionsWrap.append(
      h(
        "div",
        { class: "empty latency-empty" },
        h("h3", {}, "No latency sessions yet"),
        h("div", {}, "Run a voice session first and this page will populate from latency.jsonl."),
      ),
    );
  } else {
    for (const session of report.sessions) {
      sessionsWrap.append(sessionCard(session));
    }
  }

  page.replaceChildren(
    h(
      "div",
      { class: "page-head" },
      h(
        "div",
        {},
        h("h2", {}, "Latency by session"),
        h("div", { class: "sub" }, "TTFB samples grouped by role: STT, LLM, and TTS."),
      ),
      refresh,
    ),
    h(
      "div",
      { class: "banner info latency-banner" },
      `Sessions: ${report.totals.sessions} | Samples: ${report.totals.samples} | Duplicate hop logs inside ${report.dedupe_window_ms} ms are collapsed.`,
    ),
    sessionsWrap,
  );
}
