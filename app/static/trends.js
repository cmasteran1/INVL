"use strict";

// --- Trends -----------------------------------------------------------------
// Per-item charts of the count over time, plus a calendar of low days.
//
// The hub stores every change with a UTC timestamp and leaves the bucketing to
// us, so hours, days and months here follow THIS computer's clock and time
// zone: "9 AM" and "Monday" mean what the viewer expects, and daylight-saving
// days get their real 23 or 25 hours.
//
// Loaded before app.js; uses its el()/api() helpers and `items` at call time.

const TREND_SCALES = [
  { key: "day", label: "Day", hint: "Hour by hour" },
  { key: "week", label: "Week", hint: "Day by day" },
  { key: "month", label: "Month", hint: "Day by day" },
  { key: "year", label: "Year", hint: "Month by month" },
  { key: "all", label: "All time", hint: "Since tracking began" },
];
const HOUR_MS = 3600000;
const DAY_MS = 24 * HOUR_MS;
const SVGNS = "http://www.w3.org/2000/svg";

const trend = {
  scale: "day",
  anchor: null,     // a local Date inside the period shown (null = today)
  calMonth: null,   // first of the month the calendar shows (null = anchor's)
  itemId: null,
  stamp: null,      // the item's last_updated that the cache belongs to
  cache: new Map(), // range key -> Promise of a prepared /timeline response
  token: 0,         // drops renders overtaken by a newer one
  height: 0,        // last rendered height, held while the next one loads
  rerender: null,
};

// --- Local-time date math ---------------------------------------------------
// Built from y/m/d components (never by adding 24h of milliseconds), so a day
// is always midnight to midnight on the local clock.
const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
const startOfWeek = (d) => addDays(startOfDay(d), -d.getDay()); // Sunday
const startOfMonth = (d) => new Date(d.getFullYear(), d.getMonth(), 1);
const addMonths = (d, n) => new Date(d.getFullYear(), d.getMonth() + n, 1);
const sameDay = (a, b) => a && b && startOfDay(a).getTime() === startOfDay(b).getTime();

const fmtDate = (d, opts) => d.toLocaleDateString([], opts);
const fmtClock = (d) => d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
const signed = (n) => (n > 0 ? `+${n}` : n < 0 ? `−${-n}` : "0");

function fmtDuration(ms) {
  if (ms <= 0) return "None";
  const mins = Math.round(ms / 60000);
  if (mins < 60) return `${Math.max(1, mins)} min`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ${mins % 60}m`;
  return `${Math.floor(hrs / 24)}d ${hrs % 24}h`;
}

function trendAnchor() { return trend.anchor || startOfDay(new Date()); }

function setTrend(scale, anchor) {
  trend.scale = scale;
  trend.anchor = anchor ? startOfDay(anchor) : null;
  trend.calMonth = null;
  if (trend.rerender) trend.rerender();
}

// --- Data -------------------------------------------------------------------
function prepTimeline(data) {
  data.events = data.events.map((e) => ({ ...e, ms: Date.parse(e.t) }));
  data.thresholds = data.thresholds.map((x) => ({ ms: Date.parse(x.t), value: x.value }));
  data.firstMs = data.first_t ? Date.parse(data.first_t) : null;
  return data;
}

function fetchTimeline(itemId, start, end) {
  const key = start ? `${start.getTime()}-${end.getTime()}` : "all";
  if (!trend.cache.has(key)) {
    const qs = start
      ? `?start=${encodeURIComponent(start.toISOString())}&end=${encodeURIComponent(end.toISOString())}`
      : "";
    const p = api(`/api/items/${itemId}/timeline${qs}`).then(prepTimeline);
    p.catch(() => trend.cache.delete(key));
    trend.cache.set(key, p);
  }
  return trend.cache.get(key);
}

// The low threshold in effect at a moment. Before the first recorded value the
// real one is unknown (older hubs didn't log it at creation), so the earliest
// known value is the best guess.
function thresholdAt(data, ms) {
  const th = data.thresholds;
  if (!th.length) return data.current_threshold;
  let v = th[0].value;
  for (const x of th) {
    if (x.ms <= ms) v = x.value;
    else break;
  }
  return v;
}

// --- Periods and buckets ----------------------------------------------------
function trendPeriod(scale, anchor, firstMs) {
  switch (scale) {
    case "day": { const s = startOfDay(anchor); return { start: s, end: addDays(s, 1), unit: "hour" }; }
    case "week": { const s = startOfWeek(anchor); return { start: s, end: addDays(s, 7), unit: "day" }; }
    case "month": { const s = startOfMonth(anchor); return { start: s, end: addMonths(s, 1), unit: "day" }; }
    case "year": {
      const y = anchor.getFullYear();
      return { start: new Date(y, 0, 1), end: new Date(y + 1, 0, 1), unit: "month" };
    }
    default: { // all time: pick a bucket size that keeps the bars readable
      const today = startOfDay(new Date());
      const from = startOfDay(firstMs != null ? new Date(firstMs) : today);
      let start = from;
      const days = (addDays(today, 1) - start) / DAY_MS;
      const unit = days <= 2 ? "hour" : days <= 92 ? "day" : days <= 730 ? "week" : "month";
      if (unit === "week") start = startOfWeek(start);
      if (unit === "month") start = startOfMonth(start);
      return { start, end: addDays(today, 1), unit, from };
    }
  }
}

function makeBuckets(start, end, unit) {
  const out = [];
  for (let s = start; s < end;) {
    const e = unit === "hour" ? new Date(s.getTime() + HOUR_MS)
      : unit === "day" ? addDays(s, 1)
      : unit === "week" ? addDays(s, 7)
      : addMonths(s, 1);
    out.push({ start: s, end: e, s: s.getTime(), e: e.getTime() });
    s = e;
  }
  return out;
}

// Walk the changes once, filling each bucket with its opening/closing count,
// lowest point, amounts added and used, and time spent at or below the low
// threshold. Buckets are contiguous and sorted, as are the events.
function fillBuckets(data, buckets, nowMs) {
  let level = data.start_count; // null until the item existed
  const evs = data.events;
  let i = 0;
  while (i < evs.length && evs[i].ms < buckets[0].s) level = evs[i++].count;
  for (const b of buckets) {
    Object.assign(b, { open: level, min: level, added: 0, used: 0, changes: 0, lowMs: 0, low: false });
    b.future = b.s >= nowMs;
    const stop = Math.min(b.e, nowMs);
    let cur = b.s;
    const accrue = (to) => {
      if (level == null || to <= cur) return;
      const th = thresholdAt(data, cur);
      if (th != null && level <= th) { b.lowMs += to - cur; b.low = true; }
    };
    while (i < evs.length && evs[i].ms < b.e) {
      const ev = evs[i++];
      accrue(Math.min(ev.ms, stop));
      if (ev.reason !== "item_created") {
        if (ev.delta > 0) b.added += ev.delta; else b.used -= ev.delta;
        b.changes++;
      }
      level = ev.count;
      cur = ev.ms;
      b.min = b.min == null ? level : Math.min(b.min, level);
      const th = thresholdAt(data, ev.ms);
      if (th != null && level <= th) b.low = true;
    }
    accrue(stop);
    b.close = level;
    b.tracked = !b.future && b.min != null;
    b.out = b.tracked && b.min === 0;
  }
  return buckets;
}

// Step-line points of the count across [s, e), stopping at now.
function levelPoints(data, s, e, nowMs) {
  const pts = [];
  const endMs = Math.min(e, nowMs);
  let level = data.start_count;
  for (const ev of data.events) {
    if (ev.ms < s) { level = ev.count; continue; }
    if (ev.ms >= e) break;
    if (!pts.length && level != null) pts.push({ ms: s, count: level });
    pts.push({ ms: ev.ms, count: ev.count });
  }
  if (!pts.length && level != null && s < endMs) pts.push({ ms: s, count: level });
  if (pts.length && endMs > pts[pts.length - 1].ms) {
    pts.push({ ms: endMs, count: pts[pts.length - 1].count });
  }
  return pts;
}

// --- Labels -----------------------------------------------------------------
function tickLabel(b, unit, scale) {
  const d = b.start;
  if (unit === "hour") return d.toLocaleTimeString([], { hour: "numeric" });
  if (unit === "day") {
    if (scale === "week") return fmtDate(d, { weekday: "short", day: "numeric" });
    if (scale === "month") return String(d.getDate());
    return fmtDate(d, { month: "short", day: "numeric" });
  }
  if (unit === "week") return fmtDate(d, { month: "short", day: "numeric" });
  return scale === "year" ? fmtDate(d, { month: "short" }) : fmtDate(d, { month: "short", year: "2-digit" });
}

function bucketTitle(b, unit) {
  if (unit === "hour") {
    return `${fmtDate(b.start, { weekday: "short", month: "short", day: "numeric" })} · ` +
      `${fmtClock(b.start)} – ${fmtClock(b.end)}`;
  }
  if (unit === "day") return fmtDate(b.start, { weekday: "long", month: "long", day: "numeric", year: "numeric" });
  if (unit === "week") return `Week of ${fmtDate(b.start, { month: "long", day: "numeric", year: "numeric" })}`;
  return fmtDate(b.start, { month: "long", year: "numeric" });
}

function periodTitle(scale, p) {
  if (scale === "day") return fmtDate(p.start, { weekday: "long", month: "long", day: "numeric", year: "numeric" });
  if (scale === "week") {
    const last = addDays(p.start, 6);
    return `${fmtDate(p.start, { month: "short", day: "numeric" })} – ` +
      `${fmtDate(last, { month: "short", day: "numeric", year: "numeric" })}`;
  }
  if (scale === "month") return fmtDate(p.start, { month: "long", year: "numeric" });
  if (scale === "year") return String(p.start.getFullYear());
  return `${fmtDate(p.from, { month: "short", day: "numeric", year: "numeric" })} – today`;
}

// Step the anchor one period back (-1) or forward (+1).
function stepAnchor(scale, anchor, dir) {
  if (scale === "day") return addDays(anchor, dir);
  if (scale === "week") return addDays(anchor, 7 * dir);
  if (scale === "month") return addMonths(anchor, dir);
  return new Date(anchor.getFullYear() + dir, 0, 1);
}

// Clicking a bar zooms into it; an hour is as fine as it goes.
const DRILL = { day: "day", week: "week", month: "month" };

// --- Rendering --------------------------------------------------------------
function svgEl(tag, attrs = {}, text) {
  const n = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (text != null) n.textContent = text;
  return n;
}

function niceStep(v) {
  if (v <= 1) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return Math.max(1, m * p);
  return 10 * p;
}

// A bar with a 4px rounded data end and a square end on the zero line.
function barPath(x, base, w, h, up) {
  if (h <= 0) return "";
  const r = Math.min(4, w / 2, h);
  const tip = up ? base - h : base + h;
  const s = up ? 1 : -1; // corners curve back toward the baseline
  return `M${x},${base} V${tip + s * r} Q${x},${tip} ${x + r},${tip} ` +
    `H${x + w - r} Q${x + w},${tip} ${x + w},${tip + s * r} V${base} Z`;
}

async function renderTrends(host, it) {
  const token = ++trend.token;
  if (trend.itemId !== it.item_id || trend.stamp !== it.last_updated) {
    trend.cache.clear();
    trend.itemId = it.item_id;
    trend.stamp = it.last_updated;
  }
  trend.rerender = () => {
    const fresh = (typeof items !== "undefined" && items.find((x) => x.item_id === it.item_id)) || it;
    renderTrends(host, fresh);
  };
  // Hold the old height while the new view loads so the drawer doesn't jump.
  if (trend.height) host.style.minHeight = `${trend.height}px`;

  const anchor = trendAnchor();
  const nowMs = Date.now();
  let data, calData, period;
  try {
    if (trend.scale === "all") {
      data = await fetchTimeline(it.item_id);
      period = trendPeriod("all", anchor, data.firstMs);
    } else {
      period = trendPeriod(trend.scale, anchor);
      data = await fetchTimeline(it.item_id, period.start, period.end);
    }
    const calMonth = trend.calMonth || startOfMonth(anchor);
    calData = await fetchTimeline(it.item_id, calMonth, addMonths(calMonth, 1));
  } catch (e) {
    if (token !== trend.token) return;
    host.replaceChildren(el("div", { class: "muted" }, `Couldn't load history: ${e.message}`));
    return;
  }
  if (token !== trend.token) return;

  const view = el("div", { class: "trends" });
  host.replaceChildren(view);
  view.append(scaleBar(), navBar(period, data.firstMs, nowMs));

  const buckets = fillBuckets(data, makeBuckets(period.start, period.end, period.unit), nowMs);
  const pts = levelPoints(data, period.start.getTime(), buckets[buckets.length - 1].e, nowMs);

  if (!pts.length && !buckets.some((b) => b.tracked)) {
    view.append(el("div", { class: "trend-empty" },
      period.start.getTime() > nowMs ? "This period hasn't happened yet."
        : data.firstMs && data.firstMs >= period.end.getTime()
          ? `No history yet for this period. Tracking began ${fmtDate(new Date(data.firstMs), { month: "long", day: "numeric", year: "numeric" })}.`
          : "No history for this period."));
  } else {
    view.append(statTiles(data, buckets, pts, period, nowMs));
    const chartHost = el("div", { class: "chart-wrap" });
    view.append(chartLegend(), chartHost);
    drawChart(chartHost, data, buckets, pts, period, nowMs);
    view.append(bucketTable(buckets, period.unit));
  }

  view.append(calendar(calData, trend.calMonth || startOfMonth(anchor), period, nowMs));
  view.append(el("div", { class: "trend-tz muted" },
    `Times are this computer's local time (${Intl.DateTimeFormat().resolvedOptions().timeZone}). ` +
    "A counter that was asleep or offline reports its presses when it reconnects, so they appear at that time."));

  host.style.minHeight = "";
  trend.height = host.offsetHeight;
}

function scaleBar() {
  const bar = el("div", { class: "seg", role: "tablist" });
  for (const s of TREND_SCALES) {
    bar.append(el("button", {
      class: "seg-btn" + (trend.scale === s.key ? " active" : ""),
      role: "tab",
      "aria-selected": String(trend.scale === s.key),
      title: s.hint,
      onclick: () => setTrend(s.key, trend.anchor),
    }, s.label));
  }
  return bar;
}

function navBar(period, firstMs, nowMs) {
  const row = el("div", { class: "trend-nav" });
  const title = el("div", { class: "trend-period" }, periodTitle(trend.scale, period));
  if (trend.scale === "all") { row.append(title); return row; }
  const anchor = trendAnchor();
  const prev = stepAnchor(trend.scale, anchor, -1);
  const next = stepAnchor(trend.scale, anchor, +1);
  const prevEnd = trendPeriod(trend.scale, prev).end.getTime();
  const nextStart = trendPeriod(trend.scale, next).start.getTime();
  const isCurrent = period.start.getTime() <= nowMs && nowMs < period.end.getTime();
  row.append(
    el("button", {
      class: "btn btn-sm nav-arrow", title: "Previous", "aria-label": "Previous",
      ...(firstMs == null || prevEnd <= firstMs ? { disabled: "" } : {}),
      onclick: () => setTrend(trend.scale, prev),
    }, "‹"),
    title,
    el("button", {
      class: "btn btn-sm nav-arrow", title: "Next", "aria-label": "Next",
      ...(nextStart > nowMs ? { disabled: "" } : {}),
      onclick: () => setTrend(trend.scale, next),
    }, "›"),
  );
  if (!isCurrent) {
    row.append(el("button", { class: "btn btn-sm", onclick: () => setTrend(trend.scale, null) },
      trend.scale === "day" ? "Today" : `This ${trend.scale}`));
  }
  return row;
}

function statTiles(data, buckets, pts, period, nowMs) {
  const live = period.start.getTime() <= nowMs && nowMs < period.end.getTime();
  const startVal = pts.length ? pts[0].count : null;
  const endVal = pts.length ? pts[pts.length - 1].count : null;
  const tracked = buckets.filter((b) => b.tracked);
  const used = tracked.reduce((a, b) => a + b.used, 0);
  const added = tracked.reduce((a, b) => a + b.added, 0);
  const lowMs = tracked.reduce((a, b) => a + b.lowMs, 0);
  const lowest = tracked.length ? Math.min(...tracked.map((b) => b.min)) : null;
  const hasThreshold = data.current_threshold != null || data.thresholds.some((t) => t.value != null);

  const tile = (label, value, sub) => el("div", { class: "stat" },
    el("div", { class: "stat-label" }, label),
    el("div", { class: "stat-value" }, value),
    sub ? el("div", { class: "stat-sub" }, sub) : null);

  return el("div", { class: "stats" },
    tile(trend.scale === "all" ? "First count" : "Start", startVal ?? "—"),
    tile(live ? "Now" : "End", endVal ?? "—"),
    tile("Net change", startVal == null ? "—" : signed(endVal - startVal)),
    tile("Used", used ? `−${used}` : "0"),
    tile("Added", added ? `+${added}` : "0"),
    tile("Time low", hasThreshold ? fmtDuration(lowMs) : "—",
      hasThreshold ? (lowest != null ? `lowest ${lowest}` : null) : "no threshold set"),
  );
}

function chartLegend() {
  const key = (cls, label) => el("span", { class: "legend-item" }, el("span", { class: `key ${cls}` }), label);
  return el("div", { class: "chart-legend" },
    key("key-level", "Count"), key("key-th", "Low threshold"),
    key("key-added", "Added"), key("key-used", "Used"));
}

function drawChart(host, data, buckets, pts, period, nowMs) {
  const W = Math.max(320, host.clientWidth || 640);
  const L = 40, R = 12;
  const A0 = 10, A1 = 150;   // count panel
  const B0 = 180, B1 = 270;  // added/used panel
  const H = 292;
  const n = buckets.length;
  const band = (W - L - R) / n;
  const bx = (i) => L + i * band;
  const tx = (ms) => {
    if (ms <= buckets[0].s) return L;
    if (ms >= buckets[n - 1].e) return L + band * n;
    let lo = 0, hi = n - 1;
    while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (buckets[mid].s <= ms) lo = mid; else hi = mid - 1; }
    const b = buckets[lo];
    return bx(lo) + band * ((ms - b.s) / (b.e - b.s));
  };

  const svg = svgEl("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, class: "trend-svg",
    role: "img", "aria-label": "Count over time, with amounts added and used" });
  const hl = svgEl("rect", { x: 0, y: A0, width: band, height: B1 - A0, class: "tl-hl", visibility: "hidden" });
  svg.append(hl);

  // Count panel scale: room for the highest count and the threshold line.
  const ths = buckets.map((b) => thresholdAt(data, b.s));
  const maxA = Math.max(4, ...pts.map((p) => p.count), ...ths.filter((t) => t != null).map((t) => t + 1));
  const stepA = niceStep(maxA / 4);
  const topA = Math.ceil(maxA / stepA) * stepA;
  const ya = (v) => A1 - (v / topA) * (A1 - A0);
  for (let v = 0; v <= topA; v += stepA) {
    svg.append(svgEl("line", { x1: L, x2: W - R, y1: ya(v), y2: ya(v), class: "tl-grid" }));
    svg.append(svgEl("text", { x: L - 6, y: ya(v) + 4, class: "tl-tick", "text-anchor": "end" }, v));
  }

  // Low zone and threshold, per bucket so a threshold change shows where it happened.
  let thPath = "";
  buckets.forEach((b, i) => {
    const th = ths[i];
    if (th == null) return;
    svg.append(svgEl("rect", { x: bx(i), y: ya(th), width: band, height: A1 - ya(th), class: "tl-lowzone" }));
    thPath += `M${bx(i)},${ya(th)} H${bx(i) + band} `;
  });
  if (thPath) {
    svg.append(svgEl("path", { d: thPath, class: "tl-th" }));
    const lastTh = ths[n - 1];
    if (lastTh != null) {
      svg.append(svgEl("text", { x: W - R - 2, y: ya(lastTh) - 4, class: "tl-thlabel", "text-anchor": "end" },
        `low ≤ ${lastTh}`));
    }
  }

  // Count as a step line: it holds until the next change.
  if (pts.length > 1) {
    let d = `M${tx(pts[0].ms)},${ya(pts[0].count)}`;
    for (let i = 1; i < pts.length; i++) d += ` H${tx(pts[i].ms)} V${ya(pts[i].count)}`;
    svg.append(svgEl("path", {
      d: `${d} V${A1} H${tx(pts[0].ms)} Z`, class: "tl-area" }));
    svg.append(svgEl("path", { d, class: "tl-line" }));
    const last = pts[pts.length - 1];
    svg.append(svgEl("circle", { cx: tx(last.ms), cy: ya(last.count), r: 4, class: "tl-dot" }));
  }

  // "Now" marker inside the current period.
  if (nowMs > buckets[0].s && nowMs < buckets[n - 1].e) {
    const x = tx(nowMs);
    svg.append(svgEl("line", { x1: x, x2: x, y1: A0, y2: B1, class: "tl-now" }));
  }

  // Added (up) / used (down) panel, one zero line.
  const tracked = buckets.filter((b) => b.tracked);
  const maxUp = Math.max(0, ...tracked.map((b) => b.added));
  const maxDown = Math.max(0, ...tracked.map((b) => b.used));
  const span = Math.max(1, maxUp + maxDown);
  const zero = maxUp === 0 && maxDown === 0 ? (B0 + B1) / 2 : B0 + (maxUp / span) * (B1 - B0);
  const per = (B1 - B0) / (maxUp === 0 && maxDown === 0 ? 2 : span);
  svg.append(svgEl("text", { x: L, y: B0 - 8, class: "tl-panel" }, "Added / used"));
  svg.append(svgEl("line", { x1: L, x2: W - R, y1: zero, y2: zero, class: "tl-zero" }));
  // The 0 label gives way when a lopsided scale puts it on top of an end label.
  const roomUp = zero - B0 >= 14, roomDown = B1 - zero >= 14;
  if (maxUp) svg.append(svgEl("text", { x: L - 6, y: B0 + 8, class: "tl-tick", "text-anchor": "end" }, `+${maxUp}`));
  if (maxDown) svg.append(svgEl("text", { x: L - 6, y: B1, class: "tl-tick", "text-anchor": "end" }, `−${maxDown}`));
  if ((roomUp || !maxUp) && (roomDown || !maxDown)) {
    svg.append(svgEl("text", { x: L - 6, y: zero + 4, class: "tl-tick", "text-anchor": "end" }, "0"));
  }
  const bw = Math.max(2, Math.min(24, band * 0.62));
  buckets.forEach((b, i) => {
    if (!b.tracked) return;
    const x = bx(i) + (band - bw) / 2;
    if (b.added) svg.append(svgEl("path", { d: barPath(x, zero - 1, bw, b.added * per - 1, true), class: "tl-added" }));
    if (b.used) svg.append(svgEl("path", { d: barPath(x, zero + 1, bw, b.used * per - 1, false), class: "tl-used" }));
  });

  // X labels, thinned to fit.
  const dayW = { week: 48, month: 22 }[trend.scale] || 40;
  const labelW = { hour: 42, day: dayW, week: 46, month: 40 }[period.unit];
  const every = Math.max(1, Math.ceil(labelW / band));
  buckets.forEach((b, i) => {
    if (i % every) return;
    svg.append(svgEl("text", { x: bx(i) + band / 2, y: H - 4, class: "tl-tick", "text-anchor": "middle" },
      tickLabel(b, period.unit, trend.scale)));
  });

  // Hover / focus / click layer: the whole column is the hit target.
  const tip = el("div", { class: "chart-tip hidden", role: "status" });
  const drill = DRILL[period.unit];
  const show = (i) => {
    const b = buckets[i];
    hl.setAttribute("x", bx(i));
    hl.setAttribute("visibility", "visible");
    tip.replaceChildren(el("div", { class: "tip-title" }, bucketTitle(b, period.unit)));
    const row = (value, label, cls) => el("div", { class: "tip-row" },
      cls ? el("span", { class: `key ${cls}` }) : null, el("strong", {}, value), el("span", { class: "muted" }, label));
    if (b.future) tip.append(el("div", { class: "muted" }, "Not here yet"));
    else if (!b.tracked) tip.append(el("div", { class: "muted" }, "Not tracked yet"));
    else {
      tip.append(row(String(b.close), b.e > nowMs ? "count now" : "count at end", "key-level"));
      const th = thresholdAt(data, b.s);
      tip.append(row(String(b.min), b.out ? "lowest · OUT of stock" : b.low ? `lowest · LOW (≤ ${th})` : "lowest"));
      tip.append(row(b.added ? `+${b.added}` : "0", "added", "key-added"));
      tip.append(row(b.used ? `−${b.used}` : "0", "used", "key-used"));
      if (b.lowMs) tip.append(row(fmtDuration(b.lowMs), "at or below low"));
      if (drill) tip.append(el("div", { class: "tip-hint" }, `Click for ${drill === "day" ? "hour by hour" : "day by day"}`));
    }
    tip.classList.remove("hidden");
    const tw = tip.offsetWidth;
    const cx = bx(i) + band / 2;
    tip.style.left = `${Math.min(W - tw - 4, Math.max(4, cx + 14 + tw > W ? cx - tw - 14 : cx + 14))}px`;
  };
  const hide = () => { hl.setAttribute("visibility", "hidden"); tip.classList.add("hidden"); };
  buckets.forEach((b, i) => {
    const hit = svgEl("rect", {
      x: bx(i), y: A0, width: band, height: H - A0, class: "tl-hit" + (drill && b.tracked ? " drill" : ""),
      tabindex: "0", "aria-label": bucketTitle(b, period.unit),
    });
    hit.addEventListener("pointerenter", () => show(i));
    hit.addEventListener("focus", () => show(i));
    hit.addEventListener("pointerleave", hide);
    hit.addEventListener("blur", hide);
    const go = () => { if (drill && b.tracked) setTrend(drill, b.start); };
    hit.addEventListener("click", go);
    hit.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
    svg.append(hit);
  });

  host.replaceChildren(svg, tip);
}

// The same numbers as the chart, readable without hovering.
function bucketTable(buckets, unit) {
  const rows = buckets.filter((b) => b.tracked);
  const tbl = el("table", { class: "history" },
    el("thead", {}, el("tr", {},
      el("th", {}, "Period"), el("th", { class: "num" }, "End"), el("th", { class: "num" }, "Lowest"),
      el("th", { class: "num" }, "Added"), el("th", { class: "num" }, "Used"), el("th", {}, "Low"))));
  const tb = el("tbody");
  for (const b of rows) {
    tb.append(el("tr", {},
      el("td", {}, bucketTitle(b, unit)),
      el("td", { class: "num" }, String(b.close)),
      el("td", { class: "num" }, String(b.min)),
      el("td", { class: "num" }, b.added ? `+${b.added}` : "—"),
      el("td", { class: "num" }, b.used ? `−${b.used}` : "—"),
      el("td", {}, b.out ? "OUT" : b.low ? `LOW ${fmtDuration(b.lowMs)}` : "—")));
  }
  tbl.append(tb);
  return el("details", { class: "trend-table" }, el("summary", {}, "Show as table"), tbl);
}

function calendar(data, monthStart, period, nowMs) {
  const days = fillBuckets(data, makeBuckets(monthStart, addMonths(monthStart, 1), "day"), nowMs);
  const today = new Date(nowMs);
  const nextMonth = addMonths(monthStart, 1);
  const moveTo = (m) => { trend.calMonth = m; trend.rerender(); };

  const wrap = el("div", { class: "cal" });
  wrap.append(el("div", { class: "cal-head" },
    el("div", { class: "section-title cal-title" }, "Calendar"),
    el("button", { class: "btn btn-sm nav-arrow", "aria-label": "Previous month",
      ...(data.firstMs == null || monthStart.getTime() <= data.firstMs ? { disabled: "" } : {}),
      onclick: () => moveTo(addMonths(monthStart, -1)) }, "‹"),
    el("div", { class: "cal-month" }, fmtDate(monthStart, { month: "long", year: "numeric" })),
    el("button", { class: "btn btn-sm nav-arrow", "aria-label": "Next month",
      ...(nextMonth.getTime() > nowMs ? { disabled: "" } : {}),
      onclick: () => moveTo(nextMonth) }, "›")));

  const grid = el("div", { class: "cal-grid" });
  const sunday = startOfWeek(today);
  for (let i = 0; i < 7; i++) {
    grid.append(el("div", { class: "cal-dow" }, fmtDate(addDays(sunday, i), { weekday: "short" })));
  }
  for (let i = 0; i < monthStart.getDay(); i++) grid.append(el("div", {}));

  const pS = period.start.getTime(), pE = period.end.getTime();
  for (const b of days) {
    const cls = ["cal-day"];
    if (b.out) cls.push("out"); else if (b.low) cls.push("low");
    if (!b.tracked) cls.push("blank");
    if (sameDay(b.start, today)) cls.push("today");
    if (trend.scale === "day" && sameDay(b.start, period.start)) cls.push("selected");
    else if (b.tracked && trend.scale !== "day" && trend.scale !== "all" && b.s >= pS && b.s < pE) cls.push("in-range");

    const th = thresholdAt(data, b.s);
    const title = !b.tracked ? `${bucketTitle(b, "day")}: ${b.future ? "not here yet" : "not tracked"}`
      : `${bucketTitle(b, "day")}: ended at ${b.close}, lowest ${b.min}` +
        (b.out ? " (OUT of stock)" : b.low ? ` (LOW, threshold ${th})` : "") +
        (b.changes ? `, ${b.added ? `+${b.added} ` : ""}${b.used ? `−${b.used}` : ""}`.trimEnd() : ", no changes");

    grid.append(el("button", {
      class: cls.join(" "), title, "aria-label": title,
      ...(b.tracked ? {} : { disabled: "" }),
      onclick: () => setTrend("day", b.start),
    },
      el("span", { class: "cal-num" }, String(b.start.getDate())),
      b.tracked ? el("span", { class: "cal-net" }, b.changes ? signed(b.open == null ? b.close : b.close - b.open) : "·") : null,
      b.out ? el("span", { class: "cal-tag" }, "OUT") : b.low ? el("span", { class: "cal-tag" }, "LOW") : null));
  }
  wrap.append(grid, el("div", { class: "cal-legend muted" },
    el("span", { class: "cal-swatch low" }), "LOW: at or below its threshold at some point that day",
    el("span", { class: "cal-swatch out" }), "OUT: hit zero",
    el("span", { class: "cal-hint" }, "Click a day for hour by hour")));
  return wrap;
}

// The chart is drawn to the drawer's pixel width, so redraw when that changes.
let trendResizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(trendResizeTimer);
  trendResizeTimer = setTimeout(() => {
    if (trend.rerender && document.querySelector(".trends")) trend.rerender();
  }, 150);
});
