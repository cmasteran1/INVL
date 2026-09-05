"use strict";

// --- State ------------------------------------------------------------------
let items = [];
let selectedItemId = null;
let searchTerm = "";
const REFRESH_MS = 4000;

// --- Helpers ----------------------------------------------------------------
const $ = (sel) => document.querySelector(sel);
const el = (tag, props = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") n.className = v;
    else if (k === "html") n.innerHTML = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) n.setAttribute(k, v);
  }
  for (const kid of kids) {
    if (kid == null) continue;
    n.append(kid.nodeType ? kid : document.createTextNode(kid));
  }
  return n;
};

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  setConn(true);
  if (!res.ok) {
    let detail;
    try { detail = (await res.json()).detail; } catch { detail = res.statusText; }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  if (res.status === 204) return null;
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res.text();
}

function setConn(ok) {
  // Connection indicator dot was removed from the UI; keep this a safe no-op so
  // callers throughout the code don't need to change.
  const dot = $("#conn-status");
  if (!dot) return;
  dot.classList.toggle("online", ok);
  dot.classList.toggle("offline", !ok);
}

let toastTimer;
function toast(msg, isError = false) {
  let t = $("#toast");
  if (!t) { t = el("div", { id: "toast", class: "toast" }); document.body.append(t); }
  t.textContent = msg;
  t.classList.toggle("error", isError);
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2600);
}

function relTime(iso) {
  if (!iso) return "—";
  const then = new Date(iso.replace(" ", "T"));
  const secs = Math.round((Date.now() - then.getTime()) / 1000);
  if (secs < 5) return "just now";
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

function fmtTime(iso) {
  if (!iso) return "—";
  return new Date(iso.replace(" ", "T")).toLocaleString();
}

// True while a field inside the detail panel has focus, so the periodic refresh
// can skip rebuilding the panel and clobbering what the user is typing.
function detailIsBeingEdited() {
  const detail = $("#detail");
  const active = document.activeElement;
  return detail && active && detail.contains(active) &&
    ["INPUT", "SELECT", "TEXTAREA"].includes(active.tagName);
}

// --- Items table ------------------------------------------------------------
async function loadItems() {
  try {
    items = await api("/api/items");
    renderTable();
    // Don't rebuild the detail panel while the user is typing in it — the
    // periodic refresh would otherwise wipe an in-progress entry (e.g. "Set
    // exact"). Skip the re-render whenever a field inside the panel has focus.
    if (selectedItemId && !detailIsBeingEdited()) renderDetail(selectedItemId);
    await loadUnknown();
  } catch (e) {
    setConn(false);
  }
}

function renderTable() {
  const body = $("#items-body");
  body.innerHTML = "";
  const term = searchTerm.toLowerCase();
  const filtered = items.filter((it) =>
    !term ||
    (it.item_code || "").toLowerCase().includes(term) ||
    (it.item_name || "").toLowerCase().includes(term) ||
    (it.location || "").toLowerCase().includes(term)
  );
  if (filtered.length === 0) {
    body.append(el("tr", {}, el("td", { colspan: "9", class: "empty" },
      items.length === 0 ? "No items yet. Click “+ Add Item” to start." : "No matches.")));
    return;
  }
  for (const it of filtered) {
    const dev = it.device;
    // An offline device may be sitting on presses the hub has never been told
    // about — it sleeps with its radio off and cannot be polled. Show how old
    // this number is rather than presenting a stale count as current.
    const devCell = dev
      ? el("span", { class: "dev-state" },
          el("span", { class: `dot ${dev.online ? "on" : "off"}` }),
          dev.nickname || dev.device_id,
          dev.online ? null
                     : el("span", { class: "stale",
                                    title: "Asleep or unreachable. Any presses since then are held on the device and arrive when it next reports." },
                          `synced ${relTime(dev.last_seen)}`))
      : el("span", { class: "dev-state muted" }, el("span", { class: "dot none" }), "—");

    const tr = el("tr", {
      class: it.item_id === selectedItemId ? "selected" : "",
      onclick: () => selectItem(it.item_id),
    },
      el("td", {}, el("span", { class: `pill ${it.status}` }, it.status)),
      el("td", {}, el("span", { class: "code-chip" }, it.item_code)),
      el("td", {}, it.item_name),
      el("td", {}, it.location || "—"),
      el("td", { class: "num count" }, String(it.current_count)),
      el("td", {}, it.unit_name || "—"),
      el("td", { class: "num" }, it.low_threshold ?? "—"),
      el("td", { class: "muted" }, relTime(it.last_updated)),
      el("td", {}, devCell),
    );
    body.append(tr);
  }
}

function selectItem(id) {
  selectedItemId = id;
  renderTable();
  renderDetail(id);
}

// --- Detail drawer ----------------------------------------------------------
async function renderDetail(id) {
  const it = items.find((x) => x.item_id === id);
  if (!it) { $("#detail").classList.add("hidden"); return; }
  const panel = $("#detail");
  panel.classList.remove("hidden");

  const dev = it.device;
  panel.innerHTML = "";
  panel.append(
    el("div", { class: "detail-head" },
      el("div", {},
        el("h2", {}, el("span", { class: "code-chip" }, it.item_code), " ", it.item_name),
        el("div", { class: "detail-sub" }, `${it.location || "No location"} · ${it.unit_name || "units"}`)),
      el("button", { class: "close-x", title: "Close", onclick: () => { selectedItemId = null; panel.classList.add("hidden"); renderTable(); } }, "×")),
    el("div", { class: "bigcount" }, String(it.current_count)),
    el("div", {}, el("span", { class: `pill ${it.status}` }, it.status),
      it.low_threshold != null ? el("span", { class: "muted" }, `  threshold ${it.low_threshold}`) : null),
  );

  // Metadata
  const meta = el("dl", { class: "meta-grid" });
  meta.append(
    el("dt", {}, "Last updated"), el("dd", {}, fmtTime(it.last_updated)),
    el("dt", {}, "Device"),
    el("dd", {}, dev
      ? el("span", { class: "dev-state" }, el("span", { class: `dot ${dev.online ? "on" : "off"}` }),
          `${dev.nickname || dev.device_id} (${dev.online ? "online" : "offline"})`)
      : el("span", { class: "muted" }, "Unassigned")),
  );
  if (dev) {
    meta.append(el("dt", {}, "Last seen"), el("dd", {}, relTime(dev.last_seen)));
    if (dev.battery_level != null)
      meta.append(el("dt", {}, "Battery"), el("dd", {}, `${dev.battery_level}%`));
  }
  panel.append(meta);

  // --- Adjust controls
  panel.append(el("div", { class: "section-title" }, "Adjust Count"));
  const amount = el("input", { type: "number", value: "1", min: "1" });
  const setVal = el("input", { type: "number", placeholder: `now ${it.current_count}`, min: "0" });

  const doAdjust = async (mode, sign) => {
    try {
      if (mode === "set" && setVal.value.trim() === "") {
        toast("Enter a count to set", true);
        return;
      }
      const body = mode === "delta"
        ? { mode, amount: sign * Math.abs(parseInt(amount.value || "0", 10)) }
        : { mode, count: parseInt(setVal.value, 10) };
      await api(`/api/items/${id}/adjust`, { method: "POST", body: JSON.stringify(body) });
      toast("Count updated");
      await loadItems();
    } catch (e) { toast(e.message, true); }
  };

  panel.append(
    el("div", { class: "adjust-row" },
      el("button", { class: "btn btn-sm btn-danger", onclick: () => doAdjust("delta", -1) }, "−"),
      amount,
      el("button", { class: "btn btn-sm btn-primary", onclick: () => doAdjust("delta", +1) }, "+")),
    el("div", { class: "adjust-row", style: "margin-top:8px;" },
      el("span", { class: "muted" }, "Set exact:"),
      setVal,
      el("button", { class: "btn btn-sm", onclick: () => doAdjust("set") }, "Set count")),
  );

  // --- Edit metadata / device assignment / deactivate
  panel.append(
    el("div", { class: "adjust-row", style: "margin-top:14px;" },
      el("button", { class: "btn btn-sm", onclick: () => openItemModal(it) }, "Edit details"),
      el("button", { class: "btn btn-sm", onclick: () => openDevicesModal(it.item_id) }, "Assign device"),
      el("button", {
        class: "btn btn-sm btn-danger",
        onclick: async () => {
          if (!confirm(`Remove "${it.item_name}" (${it.item_code}) from the list? `
            + `Its history is kept, and any assigned device is left registered but unassigned.`)) return;
          try {
            await api(`/api/items/${it.item_id}`, { method: "DELETE" });
            selectedItemId = null;
            $("#detail").classList.add("hidden");
            toast("Item removed");
            await loadItems();
          } catch (e) { toast(e.message, true); }
        },
      }, "Delete item")),
  );

  // --- History
  panel.append(el("div", { class: "section-title" }, "History"));
  const histWrap = el("div", {}, el("div", { class: "muted" }, "Loading…"));
  panel.append(histWrap);
  try {
    const hist = await api(`/api/items/${id}/history`);
    histWrap.innerHTML = "";
    if (hist.length === 0) { histWrap.append(el("div", { class: "muted" }, "No history yet.")); }
    else {
      const tbl = el("table", { class: "history" },
        el("thead", {}, el("tr", {},
          el("th", {}, "When"), el("th", {}, "Src"), el("th", { class: "num" }, "Δ"),
          el("th", { class: "num" }, "Before"), el("th", { class: "num" }, "After"), el("th", {}, "Reason"))));
      const tb = el("tbody");
      for (const h of hist) {
        const d = h.delta;
        tb.append(el("tr", {},
          el("td", {}, fmtTime(h.timestamp)),
          el("td", {}, el("span", { class: "src-tag" }, (h.source || "").replace("device_button", "device").replace("software", "mgr"))),
          el("td", { class: "num " + (d > 0 ? "delta-pos" : d < 0 ? "delta-neg" : "") }, d == null ? "—" : (d > 0 ? `+${d}` : `${d}`)),
          el("td", { class: "num muted" }, h.count_before ?? "—"),
          el("td", { class: "num" }, h.count_after ?? "—"),
          el("td", { class: "muted" }, h.reason || "—")));
      }
      tbl.append(tb);
      histWrap.append(tbl);
    }
  } catch (e) { histWrap.innerHTML = ""; histWrap.append(el("div", { class: "muted" }, "Failed to load history.")); }
}

// --- Unknown device banner --------------------------------------------------
async function loadUnknown() {
  let unknown = [];
  try { unknown = await api("/api/devices/unknown"); } catch { return; }
  const banner = $("#unknown-banner");
  if (!unknown.length) { banner.classList.add("hidden"); return; }
  banner.classList.remove("hidden");
  banner.innerHTML = "";
  banner.append(el("h3", {}, `🔎 ${unknown.length} new device${unknown.length > 1 ? "s" : ""} found on your network`));
  for (const u of unknown) {
    banner.append(el("div", { class: "banner-row" },
      el("code", {}, u.device_id),
      el("span", { class: "muted" }, `seen ${u.attempts}× · last ${relTime(u.last_seen)}`),
      el("span", { class: "spacer" }),
      el("button", { class: "btn btn-sm btn-primary", onclick: () => openRegisterModal(u.device_id) }, "Register"),
      el("button", { class: "btn btn-sm", onclick: async () => { await api(`/api/devices/unknown/${u.device_id}`, { method: "DELETE" }); loadUnknown(); } }, "Dismiss")));
  }
}

// --- Modals -----------------------------------------------------------------
function openModal(node) {
  const m = $("#modal");
  m.innerHTML = "";
  m.append(node);
  $("#modal-overlay").classList.remove("hidden");
}
function closeModal() { $("#modal-overlay").classList.add("hidden"); }

$("#modal-overlay").addEventListener("click", (e) => {
  if (e.target.id === "modal-overlay") closeModal();
});

function field(labelText, input) {
  return el("label", { class: "field" }, labelText, input);
}

function openItemModal(existing = null) {
  const isEdit = !!existing;
  const code = el("input", { maxlength: "5", placeholder: "ABCDE", value: existing?.item_code || "", style: "text-transform:uppercase;" });
  const name = el("input", { placeholder: "Large Cups", value: existing?.item_name || "" });
  const loc = el("input", { placeholder: "Dry Storage", value: existing?.location || "" });
  const unit = el("input", { placeholder: "sleeve", value: existing?.unit_name || "" });
  const thresh = el("input", { type: "number", min: "0", placeholder: "5", value: existing?.low_threshold ?? "" });
  const startCount = el("input", { type: "number", min: "0", value: "0" });

  const form = el("div", { class: "modal-form" },
    el("div", { class: "row-2" }, field("Code (max 5)", code), field("Low threshold", thresh)),
    field("Item name", name),
    el("div", { class: "row-2" }, field("Location", loc), field("Unit", unit)),
    isEdit ? null : field("Starting count", startCount),
    el("div", { class: "modal-actions" },
      el("button", { class: "btn", onclick: closeModal }, "Cancel"),
      el("button", { class: "btn btn-primary", onclick: save }, isEdit ? "Save" : "Create")));

  async function save() {
    try {
      const payload = {
        item_code: code.value.trim().toUpperCase(),
        item_name: name.value.trim(),
        location: loc.value.trim() || null,
        unit_name: unit.value.trim() || null,
        low_threshold: thresh.value === "" ? null : parseInt(thresh.value, 10),
      };
      if (!payload.item_code || !payload.item_name) { toast("Code and name are required", true); return; }
      if (isEdit) {
        await api(`/api/items/${existing.item_id}`, { method: "PATCH", body: JSON.stringify(payload) });
      } else {
        payload.current_count = parseInt(startCount.value || "0", 10);
        await api("/api/items", { method: "POST", body: JSON.stringify(payload) });
      }
      closeModal();
      toast(isEdit ? "Item updated" : "Item created");
      await loadItems();
    } catch (e) { toast(e.message, true); }
  }

  openModal(el("div", {}, el("h2", {}, isEdit ? "Edit Item" : "Add Item"), form));
}

async function openRegisterModal(prefillId = "") {
  let itemsList = [];
  try { itemsList = await api("/api/items"); } catch {}
  const did = el("input", { placeholder: "ESP32_A4F09C21", value: prefillId });
  const nick = el("input", { placeholder: "Front shelf counter" });
  const key = el("input", { placeholder: "(optional shared secret)" });
  const assign = el("select", {}, el("option", { value: "" }, "— Unassigned —"),
    ...itemsList.map((it) => el("option", { value: String(it.item_id) }, `${it.item_code} · ${it.item_name}`)));

  async function save() {
    try {
      const payload = {
        device_id: did.value.trim(),
        nickname: nick.value.trim() || null,
        device_key: key.value.trim() || null,
        assigned_item_id: assign.value ? parseInt(assign.value, 10) : null,
      };
      if (!payload.device_id) { toast("Device ID is required", true); return; }
      await api("/api/devices", { method: "POST", body: JSON.stringify(payload) });
      closeModal();
      toast("Device registered");
      await loadItems();
    } catch (e) { toast(e.message, true); }
  }

  openModal(el("div", {}, el("h2", {}, "Register Device"),
    el("div", { class: "modal-form" },
      field("Device ID (MAC)", did),
      field("Nickname", nick),
      field("Device key", key),
      field("Assign to item", assign),
      el("div", { class: "modal-actions" },
        el("button", { class: "btn", onclick: closeModal }, "Cancel"),
        el("button", { class: "btn btn-primary", onclick: save }, "Register")))));
}

async function openDevicesModal(focusItemId = null) {
  let devices = [], itemsList = [];
  try { [devices, itemsList] = await Promise.all([api("/api/devices"), api("/api/items")]); }
  catch (e) { toast(e.message, true); return; }

  const list = el("div", { class: "device-list" });
  if (devices.length === 0) list.append(el("div", { class: "muted" }, "No devices registered yet."));

  for (const d of devices) {
    const sel = el("select", {},
      el("option", { value: "" }, "— Unassigned —"),
      ...itemsList.map((it) => el("option", {
        value: String(it.item_id),
        ...(d.assigned_item_id === it.item_id ? { selected: "selected" } : {}),
      }, `${it.item_code} · ${it.item_name}`)));

    sel.addEventListener("change", async () => {
      try {
        await api(`/api/devices/${d.device_id}/assign`, {
          method: "POST",
          body: JSON.stringify({ item_id: sel.value ? parseInt(sel.value, 10) : null }),
        });
        toast("Assignment updated");
        await loadItems();
      } catch (e) { toast(e.message, true); }
    });

    const forget = el("button", {
      class: "btn btn-sm btn-danger", title: "Forget this device",
      onclick: async () => {
        if (!confirm(`Forget ${d.nickname || d.device_id}? It will be unassigned and removed. `
          + `If it's still powered on it will re-appear as a newly found device.`)) return;
        try {
          await api(`/api/devices/${d.device_id}`, { method: "DELETE" });
          toast("Device forgotten");
          await loadItems();
          openDevicesModal(focusItemId);   // reopen with the fresh list
        } catch (e) { toast(e.message, true); }
      },
    }, "Forget");

    list.append(el("div", { class: "device-card" },
      el("span", { class: `dot ${d.online ? "on" : "off"}`, style: "width:9px;height:9px;border-radius:50%;background:" + (d.online ? "var(--ok)" : "var(--muted)") }),
      el("div", { class: "grow" },
        el("div", { class: "did" }, d.nickname || d.device_id),
        el("div", { class: "muted", style: "font-size:11px;" }, `${d.device_id} · last seen ${relTime(d.last_seen)}`)),
      sel, forget));
  }

  openModal(el("div", {}, el("h2", {}, "Devices"),
    list,
    el("div", { class: "modal-actions" },
      el("button", { class: "btn", onclick: closeModal }, "Close"),
      el("button", { class: "btn btn-primary", onclick: () => openRegisterModal() }, "+ Register Device"))));
}

// --- Footer / about ---------------------------------------------------------
async function loadAbout() {
  const footer = $("#footer");
  try {
    const a = await api("/api/about");
    footer.innerHTML = "";
    // Setup problems that would otherwise be invisible: the packaged app has no
    // console, so this banner is the only place a broken mDNS advertisement or a
    // wrong port ever gets reported.
    for (const w of (a.warnings || [])) {
      footer.append(el("div", { class: "footer-warning" }, w));
    }
    footer.append(
      el("span", {}, `Inventory Hub v${a.version}`),
      el("span", {}, `${a.items} items · ${a.devices} devices`),
      el("span", {}, "Data: ", el("code", {}, a.db_path)),
      el("span", { class: "snap", title: "Save a timestamped backup into the data folder",
        onclick: snapshotBackup }, "Snapshot backup"),
    );
  } catch { /* leave footer empty if unreachable */ }
}

async function snapshotBackup() {
  try {
    const r = await api("/api/backup/snapshot", { method: "POST" });
    toast("Backup saved to data folder");
    console.log("Snapshot:", r.path);
  } catch (e) { toast(e.message, true); }
}

// --- Wire up ----------------------------------------------------------------
$("#btn-add-item").addEventListener("click", () => openItemModal());
$("#btn-refresh").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  try {
    await Promise.all([loadItems(), loadUnknown(), loadAbout()]);
    toast("Refreshed");
  } finally {
    btn.disabled = false;
  }
});
$("#btn-devices").addEventListener("click", () => openDevicesModal());
$("#search").addEventListener("input", (e) => { searchTerm = e.target.value; renderTable(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

loadItems();
loadAbout();
setInterval(loadItems, REFRESH_MS);
setInterval(loadAbout, 30000);
