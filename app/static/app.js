// infamazing-forge UI. Plain JS, no build step.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));

async function api(path, opts = {}) {
  const r = await fetch("/api" + path, {headers: {"Content-Type": "application/json"}, ...opts,
    body: opts.body !== undefined && typeof opts.body !== "string" ? JSON.stringify(opts.body) : opts.body});
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch {} throw new Error(m); }
  return r.json();
}
const post = (p, body = {}) => api(p, {method: "POST", body});
const put = (p, body) => api(p, {method: "PUT", body});
const del = (p) => api(p, {method: "DELETE"});
function toast(msg, ms = 2800) { const t = $("#toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(t._t); t._t = setTimeout(() => t.classList.remove("show"), ms); }
async function waitTask(tid, logEl, btn) {
  if (btn) btn.disabled = true;
  if (logEl) logEl.classList.remove("hidden");
  try {
    for (;;) {
      const t = await api(`/task/${tid}`);
      if (logEl) { logEl.textContent = t.log.join("\n"); logEl.scrollTop = logEl.scrollHeight; }
      if (t.status === "done") return t.result;
      if (t.status === "error") throw new Error(t.error);
      await new Promise((r) => setTimeout(r, 1500));
    }
  } finally { if (btn) btn.disabled = false; }
}
const guard = (fn) => async (...a) => { try { await fn(...a); } catch (e) { toast(e.message, 6000); } };
const fmtS = (s) => s == null ? "—" : `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;
const when = (iso) => iso ? iso.replace("T", " ").slice(0, 16) : "";
const FSTATE = {none: ["○ no folder", ""], stale: ["● folder out of date", "warn"], ok: ["✓ saved", "ok"]};

let S = {health: null, formats: [], defFormat: "", refs: [], video: null, voices: null, outliers: [], settings: null};

function show(view) {
  window.scrollTo(0, 0);
  $$("nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + view));
  ({ideas: loadIdeas, scout: loadScout, videos: loadVideos, refs: loadRefs, formats: loadFormats, settings: loadSettings}[view] || (() => {}))();
}
$$("nav button").forEach((b) => b.onclick = () => show(b.dataset.view));

async function loadFormatsList() {
  const f = await api("/formats"); S.formats = f.formats; S.defFormat = f.default;
  const opts = (sel, withEach) => (withEach ? `<option value="">each idea's format</option>` : "") +
    S.formats.map((x) => `<option value="${x.id}" ${!withEach && x.id === sel ? "selected" : ""}>${esc(x.name)} (${x.min_chars}-${x.max_chars})</option>`).join("");
  $("#gen-fmt").innerHTML = opts("", true);
  for (const s of ["#mi-fmt", "#rl-fmt", "#rc-fmt", "#rp-fmt"]) $(s).innerHTML = (s === "#mi-fmt" ? "" : `<option value="">(none)</option>`) + opts(S.defFormat);
  $("#ve-fmt").innerHTML = opts(S.defFormat);
}
const fmtName = (id) => (S.formats.find((f) => f.id === id) || {}).name || id || "—";

async function init() {
  const h = S.health = await api("/health");
  $("#health").innerHTML = ["claude", "elevenlabs", "ffmpeg", "youtube", "drive"].map((k) => `<b class="${h[k] ? "ok" : "bad"}">${k}</b>`).join("");
  if (!h.claude) toast(h.claude_msg, 8000);
  for (const s of ["#gen-llm", "#set-llm"]) $(s).innerHTML = h.llm_models.map((m) => `<option ${m === h.default_llm ? "selected" : ""}>${m}</option>`).join("");
  await loadFormatsList();
  show("ideas");
}

// ---------- reference checklists (shared)
async function refsList(force) { if (force || !S.refs.length) S.refs = (await api("/refs")).refs; return S.refs; }
async function fillRefChecklist(box, q) {
  const refs = await refsList();
  const picked = new Set($$("input:checked", $(box)).map((i) => +i.value));
  q = (q || "").toLowerCase();
  $(box).innerHTML = refs.filter((r) => !q || (r.title + " " + r.format).toLowerCase().includes(q) || picked.has(r.id)).slice(0, 200)
    .map((r) => `<label><input type="checkbox" value="${r.id}" ${picked.has(r.id) ? "checked" : ""}> ${esc(r.title)} <span class="muted">${esc(fmtName(r.format))} · ${r.body.length}</span></label>`).join("")
    || `<span class="muted">no references yet: add some in References</span>`;
}
const pickedRefs = (box) => $$("input:checked", $(box)).map((i) => +i.value);

// ---------- ideas
async function loadIdeas() {
  const all = $("#ideas-all").checked;
  const {ideas} = await api("/ideas");
  const rows = ideas.filter((i) => all || i.status === "new");
  $("#ideas-table tbody").innerHTML = rows.map((i) => `<tr data-id="${i.id}">
    <td><input type="checkbox" class="ipick" value="${i.id}" ${i.status !== "new" ? "disabled" : ""}></td>
    <td><input class="i-title" value="${esc(i.title)}"></td><td class="clip" title="${esc(i.angle)}">${esc(i.angle)}</td>
    <td><select class="i-fmt">${S.formats.map((f) => `<option value="${f.id}" ${f.id === i.format ? "selected" : ""}>${esc(f.name)}</option>`).join("")}</select></td>
    <td class="clip small" title="${esc(i.source)}">${esc(i.source)}</td>
    <td class="status-${i.status}">${i.status}${i.video_id ? ` <a href="#" class="i-open" data-v="${i.video_id}">open</a>` : ""}</td>
    <td><button class="i-skip small">${i.status === "skipped" ? "restore" : "skip"}</button> <button class="i-del danger small">✕</button></td></tr>`).join("")
    || `<tr><td colspan="7" class="muted">no ideas yet</td></tr>`;
  $$("#ideas-table tr[data-id]").forEach((tr) => {
    const id = tr.dataset.id;
    $(".i-title", tr).onchange = (e) => put(`/ideas/${id}`, {title: e.target.value});
    $(".i-fmt", tr).onchange = (e) => put(`/ideas/${id}`, {format: e.target.value});
    $(".i-skip", tr).onclick = guard(async () => { await put(`/ideas/${id}`, {status: $(".i-skip", tr).textContent === "skip" ? "skipped" : "new"}); loadIdeas(); });
    $(".i-del", tr).onclick = guard(async () => { await del(`/ideas/${id}`); loadIdeas(); });
    const o = $(".i-open", tr); if (o) o.onclick = (e) => { e.preventDefault(); openVideo(+o.dataset.v); };
  });
  fillRefChecklist("#gen-refs", $("#gen-refq").value);
  if (!$("#ol-links").value || !$("#fc-mine").value) {
    const st = await api("/settings"); S.settings = st;
    $("#ol-links").value ||= st.competitors || ""; $("#fc-mine").value ||= st.my_channel || ""; $("#wn-mine").value ||= st.my_channel || ""; $("#fc-aud").value ||= st.audience_channels || "";
  }
}
$("#ideas-all").onchange = loadIdeas;
$("#ideas-pickall").onchange = (e) => $$(".ipick:not(:disabled)").forEach((c) => c.checked = e.target.checked);
$("#gen-refq").oninput = (e) => fillRefChecklist("#gen-refs", e.target.value);
$("#bs-go").onclick = guard(async () => {
  const {task} = await post("/ideas/brainstorm", {count: +$("#bs-n").value, direction: $("#bs-dir").value, check: $("#bs-check").checked, llm_model: $("#gen-llm").value});
  await waitTask(task, $("#ideas-log"), $("#bs-go")); loadIdeas();
});
$("#mi-go").onclick = guard(async () => {
  const r = await post("/ideas", {lines: $("#mi-lines").value, format: $("#mi-fmt").value});
  $("#mi-lines").value = ""; toast(`${r.added} idea(s) added`); loadIdeas();
});
$("#ol-go").onclick = guard(async () => {
  const links = $("#ol-links").value.split("\n").map((s) => s.trim()).filter(Boolean);
  const {task} = await post("/ideas/outliers", {links, per_channel: +$("#ol-n").value, min_multiple: +$("#ol-mult").value});
  showOutliers((await waitTask(task, $("#ideas-log"), $("#ol-go"))).videos, "Competitor outliers", "adapt");
});
$("#wn-go").onclick = guard(async () => {
  const ch = $("#wn-mine").value.trim(); if (!ch) return toast("paste your channel link");
  $("#fc-mine").value ||= ch;
  const {task} = await post("/ideas/winners", {my_channel: ch, recent: +$("#ol-n").value, min_multiple: 1.5});
  showOutliers((await waitTask(task, $("#ideas-log"), $("#wn-go"))).videos, "Your winning Shorts (≥1.5× your median)", "winners");
});
function showOutliers(videos, head, mode) {
  S.outliers = videos; $("#ol-head").textContent = head; $("#bend-mode").value = mode;
  $("#ol-panel").classList.remove("hidden");
  $("#ol-tiles").innerHTML = S.outliers.map((v, i) => `<div class="tile ${i < 6 ? "on" : ""}" data-i="${i}"><span class="chk">✓</span>
    <img loading="lazy" src="${esc(v.thumb)}" style="aspect-ratio:16/9"><div class="nm" title="${esc(v.title)}">${esc(v.title)}</div>
    <div class="meta"><b>${v.multiple}×</b> · ${v.views.toLocaleString()} · ${v.age_days}d</div><div class="yt">${esc(v.channel)}</div></div>`).join("") || `<span class="muted">no outliers found</span>`;
  $$("#ol-tiles .tile").forEach((t) => t.onclick = () => t.classList.toggle("on"));
}
// ---------- competitor channel finder
const num = (n) => n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? Math.round(n / 1e3) + "K" : String(n || 0);
$("#fc-go").onclick = guard(async () => {
  const {task} = await post("/channels/find", {my_channel: $("#fc-mine").value.trim(), seed_links: $("#fc-seeds").value, audience: $("#fc-aud").value,
    use_competitors: $("#fc-snow").checked, per_channel: +$("#fc-n").value, scripts_n: +$("#fc-scripts").value, use_search: $("#fc-search").checked});
  const r = await waitTask(task, $("#ideas-log"), $("#fc-go"));
  const have = new Set(($("#ol-links").value || "").split("\n").map((x) => x.trim()));
  $("#fc-res").classList.remove("hidden");
  $("#fc-table tbody").innerHTML = r.channels.map((c, i) => `<tr data-url="${esc(c.url)}" class="${i < 8 ? "on" : ""}" style="cursor:pointer">
    <td><input type="checkbox" class="fcpick" ${i < 8 && !have.has(c.url) ? "checked" : ""}></td>
    <td><img class="thumb" style="width:28px;height:28px;border-radius:50%;vertical-align:middle" src="${esc(c.thumb)}"> <a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.title)}</a> <span class="muted small">${esc(c.handle || "")}</span></td>
    <td><b>${c.score}</b></td>
    <td class="small">${Object.entries(c.counts).map(([k, n]) => `<span class="pill ${k === "audience" ? "new" : k === "autoplay" || k === "script" ? "ok" : ""}">${k} ×${n}</span>`).join("")}<br><span class="muted">${c.seeds} seed(s)</span></td>
    <td>${num(c.subs)}</td><td>${num(c.median_views)}</td><td>${c.shorts}</td><td>${c.last_days == null ? "—" : c.last_days + "d"}</td>
    <td class="clip small" title="${esc(c.samples.join(" | "))}">${esc(c.samples[0] || "")}</td></tr>`).join("") || `<tr><td colspan="9" class="muted">nothing found: try more seeds or paste your audience list</td></tr>`;
  $$("#fc-table tbody tr[data-url]").forEach((tr) => tr.onclick = (e) => { if (e.target.tagName !== "A" && e.target.tagName !== "INPUT") $(".fcpick", tr).click(); });
});
async function addPicked() {
  const links = $$("#fc-table .fcpick:checked").map((c) => c.closest("tr").dataset.url);
  if (!links.length) { toast("pick some channels"); return false; }
  const r = await post("/channels/add", {links});
  $("#ol-links").value = r.competitors.join("\n"); toast(`${r.added} channel(s) added to competitors`);
  return true;
}
$("#fc-add").onclick = guard(addPicked);
$("#fc-add-ol").onclick = guard(async () => { if (await addPicked()) $("#ol-go").click(); });

$("#bend-go").onclick = guard(async () => {
  const videos = $$("#ol-tiles .tile.on").map((t) => S.outliers[+t.dataset.i]);
  if (!videos.length) return toast("pick some outliers");
  const {task} = await post("/ideas/bend", {videos, mode: $("#bend-mode").value, count: +$("#bend-n").value, direction: $("#bs-dir").value, check: $("#bs-check").checked, llm_model: $("#gen-llm").value});
  await waitTask(task, $("#ideas-log"), $("#bend-go")); loadIdeas();
});
$("#gen-go").onclick = guard(async () => {
  const idea_ids = $$(".ipick:checked").map((c) => +c.value);
  if (!idea_ids.length) return toast("tick some ideas first");
  const {task} = await post("/videos/generate", {idea_ids, format: $("#gen-fmt").value, ref_ids: pickedRefs("#gen-refs"),
    notes: $("#gen-notes").value, llm_model: $("#gen-llm").value, make_vo: $("#gen-vo").checked, edit: $("#gen-edit").checked});
  toast(`writing ${idea_ids.length} script(s)… this takes a few minutes each`);
  const r = await waitTask(task, $("#ideas-log"), $("#gen-go"));
  toast(`${r.ids.length} video folder(s) ready`); loadIdeas();
});

// ---------- competitor scout (COMPETITOR_SCOUT.md)
const SC_PH = {me: "your channel link (blank = saved one)", like: "any channel link or @handle", niche: "a niche, e.g. fast food brand facts"};
async function loadScout() {
  const r = await api("/scout"); S.scout = r;
  $("#sc-gl").value = r.settings.gl || "US";
  if (r.last && !$("#sc-q").dataset.init) { $("#sc-q").dataset.init = 1; $("#sc-mode").value = r.last.mode; $("#sc-q").value = r.last.query || ""; renderScout(r.last); }
  scMode();
}
function scMode() { const m = $("#sc-mode").value; $("#sc-q").placeholder = m === "me" && S.scout ? S.scout.settings.channel : SC_PH[m]; }
$("#sc-mode").onchange = scMode;
$("#sc-gl").onchange = () => put("/scout/settings", {gl: $("#sc-gl").value});
function renderScout(r) {
  S.scan = r;
  const v = r.verdict || {}, f = r.funnel || {};
  const vc = {open: "ok", competitive: "new", crowded: "bad"}[v.verdict] || "";
  $("#sc-summary").innerHTML = `<div class="panel"><b>Niche: <span class="pill ${vc}">${esc(v.verdict || "?")}</span></b>
    ${v.usual != null ? ` · a Short usually gets <b>${num(v.usual)}</b>` : ""} · ${v.hot_now || 0} breakout(s) in the last 7 days
    ${v.small_winning?.length ? `<br>small channels winning: ${esc(v.small_winning.join(", "))}` : ""}${v.new_winning?.length ? `<br>new channels winning: ${esc(v.new_winning.join(", "))}` : ""}
    <div class="small muted">${f.seeds} seeds · ${f.read} read · ${f.seen} channels seen · ${f.measured} measured · ${f.shorts_creators} active Shorts-first · ${f.kept} kept · ${when(new Date(r.at * 1000).toISOString())}</div></div>`;
  $("#sc-n").textContent = `(${(r.channels || []).length})`;
  $("#sc-table tbody").innerHTML = (r.channels || []).map((c, i) => `<tr data-i="${i}">
    <td><input type="checkbox" class="scpick" ${i < 8 ? "checked" : ""}></td>
    <td><img class="thumb" style="width:28px;height:28px;border-radius:50%;vertical-align:middle" src="${esc(c.thumb)}"> <a href="https://www.youtube.com/channel/${c.id}" target="_blank" rel="noopener">${esc(c.name)}</a>
      ${c.pinned ? '<span class="pill new">pinned</span>' : ""}<div class="small muted">${esc(c.why || "")}</div></td>
    <td><b>${c.closeness}</b></td><td>${c.fit}</td><td><span class="pill ${c.grade === "close" ? "ok" : ""}">${esc(c.grade)}</span>${c.appearances ? ` ×${c.appearances}` : ""}</td>
    <td>${num(c.subs)}</td><td>${num(c.usual)}</td><td>${num(c.best)}</td><td>${c.rising == null ? "—" : c.rising + "×"}</td><td>${c.per_week}</td>
    <td>${c.last_short_days == null ? "—" : Math.round(c.last_short_days) + "d"}</td>
    <td><button class="sc-pin small">${c.pinned ? "Unpin" : "Pin"}</button></td></tr>`).join("") || `<tr><td colspan="12" class="muted">none kept</td></tr>`;
  $$("#sc-table .sc-pin").forEach((b) => b.onclick = guard(async () => {
    const c = r.channels[+b.closest("tr").dataset.i];
    await post("/scout/pin", {link: c.id, on: !c.pinned}); c.pinned = !c.pinned; renderScout(r);
    toast(c.pinned ? `pinned ${c.name}: always kept` : `unpinned ${c.name}`);
  }));
  $("#sc-feed").innerHTML = (r.feed || []).map((h, i) => `<div class="tile ${i < 10 ? "on" : ""}" data-i="${i}"><span class="chk">✓</span>
    <img loading="lazy" src="${esc(h.thumb)}" style="aspect-ratio:16/9"><div class="nm" title="${esc(h.title)}">${esc(h.title)}</div>
    <div class="meta"><b>${h.x}×</b> · ${num(h.views)} · ${Math.round(h.age_days)}d</div><div class="yt"><a href="https://youtube.com/shorts/${h.id}" target="_blank">${esc(h.channel)}</a></div></div>`).join("") || `<span class="muted">nothing breaking out in the last 14 days</span>`;
  $$("#sc-feed .tile").forEach((t) => t.onclick = (e) => { if (e.target.tagName !== "A") t.classList.toggle("on"); });
  $("#sc-dropped-n").textContent = `(${(r.dropped || []).length})`;
  $("#sc-dropped").innerHTML = (r.dropped || []).map((d) => `${esc(d.name)}: fit ${d.fit}, ${esc(d.why)}`).join("<br>") || "none";
}
$("#sc-go").onclick = guard(async () => {
  const {task} = await post("/scout/scan", {mode: $("#sc-mode").value, query: $("#sc-q").value.trim(), refresh: $("#sc-refresh").checked, llm_model: $("#gen-llm").value});
  const r = await waitTask(task, $("#sc-log"), $("#sc-go")); renderScout(r);
  toast(`${r.channels.length} competitors · niche ${r.verdict.verdict}`);
});
$("#sc-pin-go").onclick = guard(async () => { const l = $("#sc-pin-link").value.trim(); if (!l) return; await post("/scout/pin", {link: l}); $("#sc-pin-link").value = ""; toast("pinned: kept from the next scan"); });
$("#sc-to-ol").onclick = guard(async () => {
  const links = $$("#sc-table .scpick:checked").map((c) => `https://www.youtube.com/channel/${S.scan.channels[+c.closest("tr").dataset.i].id}`);
  if (!links.length) return toast("tick some channels");
  const r = await post("/channels/add", {links}); $("#ol-links").value = r.competitors.join("\n");
  toast(`${r.added} added to the outlier finder (Ideas tab)`);
});
$("#sc-ideas").onclick = guard(async () => {
  const videos = $$("#sc-feed .tile.on").map((t) => S.scan.feed[+t.dataset.i])
    .map((h) => ({id: h.id, title: h.title, channel: h.channel, views: h.views, multiple: h.x, url: `https://youtube.com/shorts/${h.id}`}));
  if (!videos.length) return toast("pick some Shorts");
  const {task} = await post("/ideas/bend", {videos, mode: $("#sc-idea-mode").value, count: Math.max(5, videos.length), llm_model: $("#gen-llm").value});
  await waitTask(task, $("#sc-log"), $("#sc-ideas")); toast("ideas added: see the Ideas tab");
});

// ---------- videos list
async function loadVideos() {
  $("#vedit").classList.add("hidden"); $("#vlist").classList.remove("hidden");
  const {videos, statuses} = await api("/videos");
  S.statuses = statuses;
  const sf = $("#v-sfilter");
  if (sf.options.length === 1) sf.innerHTML += statuses.map((x) => `<option value="${x}">${STATUS_LABEL[x]}</option>`).join("");
  const q = ($("#v-filter").value || "").toLowerCase();
  const rows = videos.filter((v) => (!q || [v.title, v.topic, fmtName(v.format), STATUS_LABEL[v.status], v.voice_name, v.folder_state, v.chars].join(" ").toLowerCase().includes(q)) && (!sf.value || v.status === sf.value));
  $("#v-count").textContent = `${rows.length} video(s)`;
  $("#v-table tbody").innerHTML = rows.map((v) => {
    const [fl, fc] = FSTATE[v.folder_state];
    return `<tr><td><input type="checkbox" class="vpick" value="${v.id}"></td>
    <td><a href="#" data-v="${v.id}">${esc(v.title)}</a></td><td>${esc(fmtName(v.format))}</td><td>${v.chars}</td>
    <td>${v.vo_seconds ? fmtS(v.vo_seconds) + (v.vo_stale ? ' <span class="pill warn">stale</span>' : "") : "—"}</td>
    <td>${v.refs.length}</td><td>${statusSelect(v.status, `data-sv="${v.id}"`)}</td><td><span class="fstate ${fc}">${fl}</span></td><td>${when(v.updated_at)}</td></tr>`;
  }).join("") || `<tr><td colspan="9" class="muted">no videos yet: pick ideas and hit "Write scripts"</td></tr>`;
  $$("#v-table a[data-v]").forEach((a) => a.onclick = (e) => { e.preventDefault(); openVideo(+a.dataset.v); });
  $$("#v-table select[data-sv]").forEach((sel) => sel.onchange = guard(async () => { await setStatus(+sel.dataset.sv, sel.value); loadVideos(); }));
}
const STATUS_LABEL = {draft: "Draft", vo: "VO ready", "in progress": "In progress", live: "Live"};
const statusSelect = (cur, attrs = "") => `<select ${attrs} class="status-sel st-${cur.replace(" ", "-")}" style="width:auto">${(S.statuses || Object.keys(STATUS_LABEL))
  .map((x) => `<option value="${x}" ${x === cur ? "selected" : ""}>${STATUS_LABEL[x] || x}</option>`).join("")}</select>`;
async function setStatus(id, status) {
  await put(`/videos/${id}/status`, {status});
  if (status === "live") { S.refs = []; toast("live: script added to past scripts"); }
}
$("#v-sfilter").onchange = loadVideos;
$("#v-filter").oninput = loadVideos;
$("#v-pickall").onchange = (e) => $$(".vpick").forEach((c) => c.checked = e.target.checked);
$("#v-vo-batch").onclick = guard(async () => {
  const ids = $$(".vpick:checked").map((c) => +c.value);
  if (!ids.length) return toast("tick some videos");
  const {task} = await post("/videos/vo-batch", {ids});
  await waitTask(task, $("#v-log"), $("#v-vo-batch")); loadVideos();
});

// ---------- video editor
async function openVideo(id) {
  show("videos");
  const v = S.video = await api(`/videos/${id}`);
  $("#vlist").classList.add("hidden"); $("#vedit").classList.remove("hidden");
  $("#ve-title").value = v.title; $("#ve-script").value = v.script; $("#ve-fmt").value = v.format;
  renderVideo();
  loadVO();
}
function renderVideo() {
  const v = S.video;
  const [fl, fc] = FSTATE[v.folder_state];
  $("#ve-folder").textContent = fl; $("#ve-folder").className = "fstate " + fc;
  $("#ve-status").outerHTML = statusSelect(v.status, 'id="ve-status" title="live = published: the script joins past scripts"');
  $("#ve-status").onchange = guard(async (e) => { const st = e.target.value; await saveVideo(); await setStatus(v.id, st); S.video = await api(`/videos/${v.id}`); renderVideo(); });
  const f = S.formats.find((x) => x.id === $("#ve-fmt").value);
  const n = $("#ve-script").value.trim().length;
  const ok = !f || (n >= f.min_chars && n <= f.max_chars);
  $("#ve-stats").innerHTML = `<b class="${ok ? "pass" : "fail"}">${n} chars</b>${f ? ` (${f.min_chars}-${f.max_chars})` : ""} · ~${fmtS(n / (v.chars / Math.max(v.est_seconds, 1) || 17))} VO · ${STATUS_LABEL[v.status] || v.status}`;
  $("#ve-refs").innerHTML = v.refs.map((r, i) => `<div class="row small"><span class="pill ${r.kind === "youtube" || /youtu/.test(r.url) ? "new" : ""}">${r.kind === "youtube" || /youtu/.test(r.url) ? "YT" : "web"}</span>
    <a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title || r.url)}</a><span class="spacer"></span><button class="danger" data-i="${i}">✕</button></div>`).join("") || `<div class="muted small">none</div>`;
  $$("#ve-refs button").forEach((b) => b.onclick = guard(async () => { v.refs.splice(+b.dataset.i, 1); await saveVideo({refs: v.refs}); }));
  $("#ve-facts").innerHTML = (v.facts || []).map((x) => `<li>${esc(x)}</li>`).join("");
  $("#ve-notes").textContent = v.notes || "";
  const rv = v.review || {};
  $("#ve-review-out").innerHTML = !rv.kind ? `<span class="muted">not reviewed</span>` :
    `<b class="${rv.passed ? "pass" : "fail"}">${rv.kind === "blind" ? (rv.caught ? `caught in the blind test (${rv.confidence}%)` : `passed the blind test${rv.confidence ? ` (${rv.confidence}% suspicion)` : ""}`)
      : `rubric ${Object.entries(rv.scores).map(([k, n]) => `${k} ${n}`).join(" · ")}`}</b> · ${rv.rounds} rewrite round(s)
    ${(rv.notes || []).length ? `<ul>${rv.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}
    ${(rv.tells_left || []).length ? `<div>AI tells left: ${rv.tells_left.map((t) => `<span class="pill warn" title="${esc(t.text)}">${esc(t.tell)}</span>`).join("")}</div>` : ""}
    ${(rv.repeats || []).length ? `<div>Repeat guard: ${rv.repeats.map(esc).join(" · ")}</div>` : ""}`;
  $("#vo-result").innerHTML = v.vo_seconds ? `<h4>${fmtS(v.vo_seconds)} (raw ${fmtS(v.vo_raw_seconds)}) · ${esc(v.voice_name)}${v.vo_stale ? ' <span class="pill warn">script changed since</span>' : ""}</h4>
    <audio controls src="/api/videos/${v.id}/audio/clean?${Date.now()}"></audio>` : "";
}
async function saveVideo(extra = {}) {
  const v = S.video;
  const r = await put(`/videos/${v.id}`, {title: $("#ve-title").value, script: $("#ve-script").value, format: $("#ve-fmt").value, ...extra});
  S.lastDrive = r.drive || "";
  S.video = await api(`/videos/${v.id}`); $("#ve-title").value = S.video.title; renderVideo();
}
$("#ve-back").onclick = loadVideos;
$("#ve-script").oninput = () => { renderVideo(); $("#ve-folder").textContent = "● unsaved edits"; $("#ve-folder").className = "fstate warn"; };
$("#ve-fmt").onchange = renderVideo;
$("#ve-save").onclick = guard(async () => { await saveVideo(); toast(S.lastDrive.startsWith("Drive: synced") ? "saved locally + Drive" : "saved locally. " + S.lastDrive, 6000); });
$("#ve-open").onclick = guard(() => post(`/videos/${S.video.id}/open`));
$("#ve-del").onclick = guard(async () => { if (!confirm("Delete from the app? (the folder stays on disk / Drive)")) return; await del(`/videos/${S.video.id}`); loadVideos(); });
$("#ve-ref-add").onclick = guard(async () => {
  const url = $("#ve-ref-url").value.trim(); if (!url) return;
  S.video.refs.push({title: $("#ve-ref-title").value.trim(), url, kind: /youtu/.test(url) ? "youtube" : "web"});
  $("#ve-ref-url").value = $("#ve-ref-title").value = ""; await saveVideo({refs: S.video.refs});
});
$("#ve-review").onclick = guard(async () => {
  await saveVideo();
  const {task} = await post(`/videos/${S.video.id}/review`, {llm_model: $("#gen-llm").value});
  await waitTask(task, $("#ve-log"), $("#ve-review"));
  S.video = await api(`/videos/${S.video.id}`); $("#ve-script").value = S.video.script; renderVideo();
});
$("#ve-rev-go").onclick = guard(async () => {
  if (!$("#ve-rev").value.trim()) return;
  await saveVideo();
  const {task} = await post(`/videos/${S.video.id}/revise`, {instruction: $("#ve-rev").value, research: $("#ve-rev-web").checked, llm_model: $("#gen-llm").value});
  await waitTask(task, $("#ve-log"), $("#ve-rev-go"));
  S.video = await api(`/videos/${S.video.id}`); $("#ve-script").value = S.video.script; $("#ve-rev").value = ""; renderVideo();
});

// ---------- VO
async function loadVO() {
  const st = S.settings = await api("/settings");
  const vs = st.settings, d = st.deadair;
  $("#vo-defaults").innerHTML = `Kalemod default: <b>${esc(st.voice.voice_name || "none set")}</b> · ${esc(vs.model)} · stab ${vs.stability} · sim ${vs.similarity} · speed ${d.speed}${d.pitch_shift ? " (tape)" : ""}`;
  $("#vo-stab").value = vs.stability; $("#vo-sim").value = vs.similarity; $("#vo-style").value = vs.style ?? 0;
  $("#vo-speed").value = d.speed; $("#vo-minp").value = d.min_pause; $("#vo-pto").value = d.pause_to; $("#vo-thr").value = d.threshold; $("#vo-pitch").checked = !!d.pitch_shift;
  if (!S.voices) {
    try {
      S.voices = await api("/vo/voices");
      $("#vo-voice").innerHTML = `<option value="">(Kalemod default)</option>` + S.voices.voices.map((x) => `<option value="${x.voice_id}">${esc(x.display)}</option>`).join("");
      $("#vo-model").innerHTML = S.voices.models.map((m) => `<option value="${m.id}" ${m.id === vs.model ? "selected" : ""}>${esc(m.label)}</option>`).join("");
    } catch (e) { $("#vo-defaults").innerHTML += `<br><span class="fail">${esc(e.message)}</span>`; }
  }
}
$("#vo-go").onclick = guard(async () => {
  await saveVideo();
  const over = $("#vo-voice").value || $("#vo-voice").closest("div").querySelector("details").open;
  const body = over ? {voice_id: $("#vo-voice").value || null, voice_name: $("#vo-voice").value ? $("#vo-voice").selectedOptions[0].text : "",
    settings: {model: $("#vo-model").value || S.settings.settings.model, stability: +$("#vo-stab").value, similarity: +$("#vo-sim").value, style: +$("#vo-style").value},
    deadair: {speed: +$("#vo-speed").value, min_pause: +$("#vo-minp").value, pause_to: +$("#vo-pto").value, threshold: +$("#vo-thr").value, pitch_shift: $("#vo-pitch").checked}} : {};
  const {task} = await post(`/videos/${S.video.id}/vo`, body);
  await waitTask(task, $("#ve-log"), $("#vo-go"));
  S.video = await api(`/videos/${S.video.id}`); renderVideo(); toast("VO saved in the video folder");
});

// ---------- references
async function loadRefs() {
  const refs = await refsList(true);
  const q = ($("#refs-q").value || "").toLowerCase();
  const rows = refs.filter((r) => !q || (r.title + r.body).toLowerCase().includes(q));
  $("#refs-count").textContent = `${rows.length} reference(s)`;
  $("#refs-table tbody").innerHTML = rows.slice(0, 500).map((r) => `<tr><td>${r.url ? `<a href="${esc(r.url)}" target="_blank">${esc(r.title)}</a>` : esc(r.title)}</td>
    <td class="clip" title="${esc(r.body)}">${esc(r.body)}</td>
    <td><select data-id="${r.id}" class="r-fmt"><option value="">—</option>${S.formats.map((f) => `<option value="${f.id}" ${f.id === r.format ? "selected" : ""}>${esc(f.name)}</option>`).join("")}</select></td>
    <td>${esc(r.source)}${r.views ? ` · ${r.views.toLocaleString()}` : ""}</td><td>${r.body.length}</td><td>${when(r.used_at)}</td>
    <td><button class="danger" data-del="${r.id}">✕</button></td></tr>`).join("");
  $$(".r-fmt").forEach((s) => s.onchange = () => put(`/refs/${s.dataset.id}`, {format: s.value}));
  $$("#refs-table [data-del]").forEach((b) => b.onclick = guard(async () => { await del(`/refs/${b.dataset.del}`); loadRefs(); }));
}
$("#refs-q").oninput = loadRefs;
$("#rl-go").onclick = guard(async () => {
  const urls = $("#rl-urls").value.split("\n").map((s) => s.trim()).filter(Boolean);
  const {task} = await post("/refs/links", {urls, format: $("#rl-fmt").value, tidy: $("#rl-tidy").checked, llm_model: $("#gen-llm").value});
  const r = await waitTask(task, $("#refs-log"), $("#rl-go")); toast(`${r.added} added`); $("#rl-urls").value = ""; loadRefs();
});
$("#rc-go").onclick = guard(async () => {
  const f = $("#rc-file").files[0]; if (!f) return toast("pick a CSV");
  const r = await post("/refs/csv", {text: await f.text(), format: $("#rc-fmt").value});
  toast(`${r.added} past script(s) imported (title: ${r.title_col}, script: ${r.script_col})`, 5000); loadRefs();
});
$("#rp-go").onclick = guard(async () => {
  await post("/refs", {title: $("#rp-title").value, body: $("#rp-body").value, format: $("#rp-fmt").value});
  $("#rp-title").value = $("#rp-body").value = ""; loadRefs();
});

// ---------- formats
async function loadFormats() {
  await loadFormatsList();
  const cur = $("#fm-pick").value || S.defFormat;
  $("#fm-pick").innerHTML = S.formats.map((f) => `<option value="${f.id}" ${f.id === cur ? "selected" : ""}>${esc(f.name)}${f.id === S.defFormat ? " ★" : ""}</option>`).join("");
  await showFormat();
  await refsList(true); fillRefChecklist("#nf-refs", $("#nf-refq").value);
}
async function showFormat() { const id = $("#fm-pick").value; if (id) $("#fm-body").value = (await api(`/formats/${id}`)).body; }
$("#fm-pick").onchange = showFormat;
$("#fm-save").onclick = guard(async () => { await put(`/formats/${$("#fm-pick").value}`, {body: $("#fm-body").value}); toast("saved"); loadFormats(); });
$("#fm-default").onclick = guard(async () => { await put("/settings", {default_format: $("#fm-pick").value}); toast("default format set"); loadFormats(); });
$("#fm-del").onclick = guard(async () => { if (!confirm("Delete this format file?")) return; await del(`/formats/${$("#fm-pick").value}`); $("#fm-pick").value = ""; loadFormats(); });
$("#nf-refq").oninput = (e) => fillRefChecklist("#nf-refs", e.target.value);
const nfId = () => $("#nf-id").value.trim().toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-|-$/g, "");
$("#nf-blank").onclick = guard(async () => {
  const id = nfId(); if (!id) return toast("give it an id");
  await put(`/formats/${id}`, {body: `# ${$("#nf-name").value || id}\nOne-line description.\nchars: 750-825\n\nStructure, as one paragraph:\n1. \n`});
  await loadFormats(); $("#fm-pick").value = id; showFormat();
});
$("#nf-learn").onclick = guard(async () => {
  const id = nfId(), ref_ids = pickedRefs("#nf-refs");
  if (!id || !ref_ids.length) return toast("id + at least one reference");
  const {task} = await post("/formats/from-refs", {id, name: $("#nf-name").value || id, ref_ids, notes: $("#nf-notes").value, llm_model: $("#gen-llm").value});
  await waitTask(task, $("#nf-log"), $("#nf-learn")); await loadFormats(); $("#fm-pick").value = id; showFormat();
});

// ---------- settings
async function loadSettings() {
  const s = S.settings = await api("/settings");
  $("#set-vo").innerHTML = `voice <b>${esc(s.voice.voice_name || "none")}</b><br>${esc(s.settings.model)} · stability ${s.settings.stability} · similarity ${s.settings.similarity} · style ${s.settings.style ?? 0}<br>
    speed ${s.deadair.speed}${s.deadair.pitch_shift ? " (pitch follows)" : ""} · min pause ${s.deadair.min_pause} → ${s.deadair.pause_to} · threshold ${s.deadair.threshold}`;
  $("#set-sf").textContent = `story-forge: ${s.story_forge_dir}` + (s.kalemod_imported ? ` · imported ${when(s.kalemod_imported)}` : " · not imported yet");
  $("#set-drive").value = s.drive.auto ? "" : (s.drive.dir || ""); $("#set-drive").placeholder = s.drive.auto ? `auto: ${s.drive.dir}` : "G:\\My Drive\\Infamazing\\FORGE"; $("#set-drive-url").value = s.drive_url || "";
  const l = s.drive.last || {};
  $("#set-drive-st").innerHTML = (s.drive.ok ? `<span class="pass">${s.drive.auto ? "found automatically" : "folder found"}: ${esc(s.drive.dir)}</span>` : s.drive.dir ? `<span class="fail">folder not found</span>` : "not set: videos are only saved locally") +
    (l.at ? ` · last sync ${when(l.at)} ${esc(l.folder || "")}${l.error ? ` <span class="fail">${esc(l.error)}</span>` : ""}` : "");
  $("#set-global").value = s.global;
}
$("#set-import").onclick = guard(async () => { await post("/settings/import-kalemod"); S.voices = null; toast("Kalemod settings imported"); loadSettings(); });
$("#set-drive-save").onclick = guard(async () => { await put("/settings", {drive_dir: $("#set-drive").value.trim(), drive_url: $("#set-drive-url").value.trim()}); toast("saved"); loadSettings(); });
$("#set-drive-detect").onclick = guard(async () => { const r = await post("/drive/detect"); toast(r.dir ? "found " + r.dir : "no Drive folder with \"infamazing\" in its name found. Paste the path instead.", 6000); loadSettings(); });
$("#set-drive-all").onclick = guard(async () => { const {task} = await post("/drive/sync-all"); const r = await waitTask(task, $("#set-log"), $("#set-drive-all")); toast(`${r.synced} folder(s) synced`); });
$("#set-keys").onclick = guard(async () => { const b = {default_llm: $("#set-llm").value}; if ($("#set-yt").value) b.yt_api_key = $("#set-yt").value; await put("/settings", b); toast("saved"); });
$("#set-global-save").onclick = guard(async () => { await put("/settings", {global: $("#set-global").value}); toast("saved"); });

init().catch((e) => toast(e.message, 8000));
