"use strict";

const monitorsEl = document.getElementById("monitors");

const state = {
  provider: "-",
  mode: "demo",
  monitors: new Map(), // session_id -> { el, lang, esSource, esTarget, part, trans }
  sessions: new Map(),
};

// ---------------------------------------------------------------- api
async function fetchSessions() {
  const res = await fetch("/api/sessions");
  if (!res.ok) throw new Error("GET /api/sessions falló");
  return res.json();
}

// ---------------------------------------------------------------- monitor
function badge(mode) {
  return mode === "LIVE"
    ? '<span class="badge badge-live">● LIVE — Gemini</span>'
    : '<span class="badge badge-demo">● DEMO — Replay</span>';
}

function createMonitor(s) {
  const el = document.createElement("section");
  el.className = "panel";
  el.dataset.sid = s.id;
  el.innerHTML = `
    <div class="panel-head">
      <div>
        <div class="title">${escapeHtml(s.title)}</div>
        <div class="sid">${escapeHtml(s.id)} · ${s.source_lang} → ${s.target_langs.join(", ")}</div>
      </div>
      ${badge(s.mode)}
    </div>
    <div class="lang-chips" style="padding:10px 16px 0"></div>
    <div class="panel-body">
      <div class="translated ghost">Esperando subtítulos…</div>
      <div class="original empty">…</div>
    </div>
    <div class="panel-foot">
      <span class="conn offline">conectando…</span>
      <span class="ms">pipeline p50: —</span>
      <span class="segments">segmentos: 0</span>
      <span class="pstatus"></span>
    </div>`;
  monitorsEl.appendChild(el);

  const mon = {
    sid: s.id,
    el,
    lang: s.target_langs.filter((l) => l !== s.source_lang)[0] || s.source_lang,
    source: s.source_lang,
    parts: null, // EventSource original
    trans: null, // EventSource target
    partialText: "",
    langEl: el.querySelector(".lang-chips"),
  };

  buildChips(mon, s);
  connect(mon);
  return mon;
}

function buildChips(mon, s) {
  const langs = [s.source_lang, ...s.target_langs];
  mon.langEl.innerHTML = "";
  for (const l of [...new Set(langs)]) {
    const b = document.createElement("button");
    b.className = "chip" + (l === mon.lang ? " active" : "");
    b.textContent = l;
    b.onclick = () => {
      mon.lang = l;
      disconnect(mon);
      connect(mon);
      for (const c of mon.langEl.children) c.classList.toggle("active", c.textContent === l);
    };
    mon.langEl.appendChild(b);
  }
}

function connect(mon) {
  const el = mon.el;
  const connEl = el.querySelector(".conn");
  const translatedEl = el.querySelector(".translated");
  const originalEl = el.querySelector(".original");
  connEl.textContent = "conectando…";
  connEl.className = "conn offline";

  const onMsg = (e) => {
    let data;
    try { data = JSON.parse(e.data); } catch { return; }
    if (data.lang === mon.lang) {
      if (data.kind === "translation") {
        translatedEl.textContent = data.text;
        translatedEl.classList.remove("ghost");
      } else if (data.kind === "final") {
        mon.partialText = "";
        setOriginal(originalEl, data.text, false);
      } else if (data.kind === "partial") {
        mon.partialText = data.text;
        setOriginal(originalEl, data.text, true);
      }
    } else if (data.kind === "final" && !mon.sourceStream) {
      setOriginal(originalEl, data.text, false);
    }
  };
  const onOpen = () => { connEl.textContent = "online"; connEl.className = "conn online"; };
  const onErr = () => { connEl.textContent = "reconectando…"; connEl.className = "conn offline"; };

  // origen (para mostrar la transcripción original)
  const src = new EventSource(`/stream/${encodeURIComponent(mon.sid)}?lang=${encodeURIComponent(mon.source)}`);
  src.onopen = onOpen; src.onerror = onErr;
  src.addEventListener("message", onMsg);
  mon.src = src;
  mon.streams = [src];

  // destino (traducción) cuando diffiere del origen
  if (mon.lang !== mon.source) {
    const tr = new EventSource(`/stream/${encodeURIComponent(mon.sid)}?lang=${encodeURIComponent(mon.lang)}`);
    tr.onopen = onOpen; tr.onerror = onErr;
    tr.addEventListener("message", onMsg);
    mon.tr = tr;
    mon.streams.push(tr);
  }
}

function disconnect(mon) {
  for (const s of mon.streams || []) s.close();
  mon.streams = [];
  mon.src = mon.tr = null;
  mon.partialText = "";
}

function setOriginal(el, text, isPartial) {
  el.innerHTML = isPartial
    ? `<span class="partial">${escapeHtml(text)}</span>`
    : escapeHtml(text);
  el.classList.toggle("empty", !text);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// ---------------------------------------------------------------- paint
function paintMeta(payload) {
  const badgeEl = document.getElementById("mode-badge");
  state.provider = payload.provider;
  state.mode = payload.mode;
  badgeEl.textContent = payload.mode === "live" ? "● LIVE — Gemini" : "● DEMO — Replay";
  badgeEl.className = payload.mode === "live" ? "badge badge-live" : "badge badge-demo";
  document.getElementById("provider-desc").textContent =
    payload.mode === "live" ? "Gemini Live + Flash-Lite" : "reproducción de una corrida real · sin API key";
}

function paintSessions(payload) {
  const seen = new Set();
  for (const s of payload.sessions) {
    seen.add(s.id);
    let mon = state.monitors.get(s.id);
    if (!mon) {
      mon = createMonitor(s);
      state.monitors.set(s.id, mon);
    }
    updateFoot(mon, s);
  }
  // remover sesiones que ya no existen
  for (const [sid, mon] of state.monitors) {
    if (!seen.has(sid)) {
      disconnect(mon);
      mon.el.remove();
      state.monitors.delete(sid);
    }
  }
  const emptyEl = monitorsEl.querySelector(".empty");
  if (payload.sessions.length === 0 && !emptyEl) {
    const d = document.createElement("div");
    d.className = "empty";
    d.textContent = "No hay sesiones. Creá una en /api/sessions o agregá samples/*.wav + .replay.json.";
    monitorsEl.appendChild(d);
  } else if (payload.sessions.length > 0 && emptyEl) {
    emptyEl.remove();
  }
  document.getElementById("foot-status").textContent =
    `${payload.sessions.length} sesión(es) activa(s) · único proceso, estado en memoria`;
}

function updateFoot(mon, s) {
  const el = mon.el;
  const m = s.metrics || {};
  const p = m.p50_ms || {};
  const pipeline = p.pipeline;
  el.querySelector(".ms").textContent = `pipeline p50: ${fmtLat(pipeline)}`;
  el.querySelector(".segments").textContent = `segmentos: ${s.segments}`;
  const pstatus = el.querySelector(".pstatus");
  pstatus.textContent = `estado: ${s.status}`;
  pstatus.className = "pstatus " + (s.status === "running" ? "ok" : s.status === "error" ? "error" : "stop");
}

function fmtLat(v) {
  return v == null ? "—" : `${Math.round(v)} ms`;
}

// ---------------------------------------------------------------- loop
async function tick() {
  try {
    const payload = await fetchSessions();
    paintMeta(payload);
    paintSessions(payload);
  } catch (err) {
    document.getElementById("foot-status").textContent = "API no disponible: " + err.message;
  }
}

tick();
setInterval(tick, 3000);