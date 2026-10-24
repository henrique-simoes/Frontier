// Frontier Radar SPA. No build step.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const api = async (path, opts) => { const r = await fetch(path, opts); if (!r.ok) throw new Error(r.status); return r.json(); };
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

const SIGNALS = {
  semantic: ["Far from prior work", "--s-semantic"], combinatorial: ["Unusual combination", "--s-combinatorial"],
  emergence: ["Early in a wave", "--s-emergence"], periphery: ["Outside usual labs", "--s-periphery"],
  traction: ["Fast uptake", "--s-traction"], recency: ["Freshness", "--s-recency"],
  attention: ["Researcher attention", "--s-attention"],
  llm_novelty: ["Reviewer: novelty", "--s-llm"], llm_impact: ["Reviewer: impact", "--s-llm"],
  llm_rigor: ["Reviewer: rigor", "--s-llm"], tournament: ["Daily tournament", "--s-llm"], class_prior: ["Novelty class", "--s-llm"],
};
const LABELS = [["frontier", "1", "Frontier"], ["solid", "2", "Solid"], ["incremental", "3", "Incremental"], ["hype", "4", "Hype"]];
const state = { days: 3, kind: "", modality: "", small: false, unrated: false, items: [], focus: -1, watch: "active,candidate", history: [] };

function scoreColor(s) {
  // perceptual ramp: slate -> blue -> violet -> amber
  if (s >= 80) return "linear-gradient(135deg,#ff9f43,#e0582c)";
  if (s >= 70) return "linear-gradient(135deg,#a78bfa,#7c3aed)";
  if (s >= 55) return "linear-gradient(135deg,#7c9cff,#4a6cf7)";
  return "linear-gradient(135deg,#94a3b8,#64748b)";
}
const ago = (iso) => {
  if (!iso) return "";
  const d = (Date.now() - new Date(iso)) / 864e5;
  if (d < 1 / 24) return `${Math.max(1, Math.round(d * 1440))}m ago`;
  return d < 1 ? `${Math.round(d * 24)}h ago` : d < 60 ? `${Math.round(d)}d ago` : iso.slice(0, 10);
};

// ---------------------------------------------------------------- feed
function bar(explain) {
  const parts = Object.entries(explain || {}).filter(([, v]) => v > 0);
  const tot = parts.reduce((a, [, v]) => a + v, 0) || 1;
  return `<div class="bar" title="What drives this score">${parts.map(([k, v]) =>
    `<i style="width:${(100 * v) / tot}%;background:${css((SIGNALS[k] || [, "--muted"])[1])}" title="${esc((SIGNALS[k] || [k])[0])}"></i>`).join("")}</div>`;
}
function card(it, i) {
  const who = it.orgs.length ? it.orgs.join(", ") : it.authors.slice(0, 3).join(", ") + (it.n_authors > 3 ? " et al." : "");
  const chips = [...(it.modalities || []), ...(it.areas || [])].slice(0, 4).map((c) => `<span class="chip">${esc(c)}</span>`).join("");
  return `<div class="card${i === state.focus ? " focus" : ""}" data-i="${i}" data-id="${esc(it.id)}">
    <div class="score" style="background:${scoreColor(it.score)}">${Math.round(it.score)}<small>${it.judged ? "judged" : "quant"}</small></div>
    <div>
      <div class="title">${esc(it.title)}</div>
      <div class="meta"><span>${esc(it.kind)}</span><span>${esc(who)}</span><span>${ago(it.published || it.discovered)}</span><span>${esc(it.sources.join(" · "))}</span></div>
      ${it.what_is_new ? `<div class="new"><b>New</b>${esc(it.what_is_new)}</div>` : `<div class="sum">${esc(it.summary)}</div>`}
      <div class="row">${bar(it.explain)}${it.novelty_class ? `<span class="chip cls">${esc(it.novelty_class.replaceAll("_", " "))}</span>` : ""}${chips}
        <div class="rate">${LABELS.map(([l, k, t]) => `<button data-rate="${l}" class="${it.rating === l ? "on" : ""}" title="${t} (${k})">${t}</button>`).join("")}</div>
      </div>
    </div></div>`;
}
async function loadFeed() {
  const p = new URLSearchParams({ days: state.days, kind: state.kind, modality: state.modality, small: state.small, unrated: state.unrated });
  state.items = await api(`/api/feed?${p}`);
  state.focus = Math.min(state.focus, state.items.length - 1);
  $("#feed").innerHTML = state.items.length ? state.items.map(card).join("")
    : `<div class="empty">Nothing here yet. Widen the window or press <b>Run now</b>.</div>`;
}
async function rate(id, label) {
  const it = state.items.find((x) => x.id === id);
  const next = it && it.rating === label ? "clear" : label;
  await api("/api/rate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id, label: next }) });
  if (it) it.rating = next === "clear" ? null : label;
  $("#feed").innerHTML = state.items.map(card).join("");
}

// ---------------------------------------------------------------- drawer
function openDrawer(html) { $("#drawerBody").innerHTML = html; $("#drawer").classList.add("open"); $("#drawer").setAttribute("aria-hidden", "false"); }
function closeDrawer() { $("#drawer").classList.remove("open"); $("#drawer").setAttribute("aria-hidden", "true"); }
const nodeLink = (id, label) => `<a data-open="${esc(id)}">${esc(label || id)}</a>`;

async function showItem(id) {
  const it = await api(`/api/item/${encodeURIComponent(id)}`);
  const sig = Object.entries(it.quant).filter(([k]) => SIGNALS[k]).map(([k, v]) =>
    `<span>${SIGNALS[k][0]}</span><div class="track"><i style="width:${100 * v}%;background:${css(SIGNALS[k][1])}"></i></div><span class="num">${v.toFixed(2)}</span>`).join("");
  const rels = it.relations.map((r) => `<div class="rel"><span class="r">${r.dir === "in" ? "←" : "→"} ${esc(r.rel)}</span>
     ${nodeLink(r.id, r.label)} <span class="chip">${esc(r.type || "")}</span>${r.score ? `<span class="num">${Math.round(r.score)}</span>` : ""}</div>`).join("");
  openDrawer(`
    <div class="meta"><span class="chip cls">${esc((it.novelty_class || "unjudged").replaceAll("_", " "))}</span><span>${esc(it.kind)}</span><span>${ago(it.published)}</span></div>
    <h2>${esc(it.title)}</h2>
    <div class="actions"><a href="${esc(it.url)}" target="_blank" rel="noopener">Open source ↗</a>
      ${it.obsidian ? `<a href="${esc(it.obsidian)}">Open in Obsidian</a>` : ""}
      <button data-graph="${esc(it.id)}">Show in graph</button>
      <button data-ask="Explain what is new in [${esc(it.id)}] and how it relates to prior work in the radar.">Ask about this</button></div>
    <div class="score" style="background:${scoreColor(it.score)};width:auto;height:auto;padding:8px 12px;display:inline-block">Frontier ${Math.round(it.score)}</div>
    ${it.what_is_new ? `<h4>What's new</h4><p>${esc(it.what_is_new)}</p>` : ""}
    ${it.claims.length ? `<h4>Claims</h4><ul>${it.claims.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>` : ""}
    ${it.skeptic ? `<h4>Skeptic</h4><p class="skeptic">${esc(it.skeptic)}</p>` : ""}
    <h4>Signals</h4><div class="signals">${sig}</div>
    <h4>Summary</h4><p>${esc(it.full_summary || "—")}</p>
    ${it.neighbors.length ? `<h4>Closest prior work in the radar</h4>${it.neighbors.map((n) => `<div class="rel"><span class="r">≈</span>${nodeLink(n.id, n.title)}<span class="num">${Math.round(n.score || 0)}</span></div>`).join("")}` : ""}
    <h4>Relationships (${it.relations.length})</h4>${rels || "<p class='meta'>none yet</p>"}`);
}
async function showEntity(id) {
  const e = await api(`/api/entity/${encodeURIComponent(id)}`);
  const handles = Object.entries(e.handles || {}).map(([k, v]) => `<span class="chip">${esc(k)}: ${esc(v)}</span>`).join(" ");
  openDrawer(`
    <div class="meta"><span class="chip cls">${esc(e.type)}</span><span>${esc(e.status)}</span><span>depth ${e.depth}</span></div>
    <h2>${esc(e.name)}</h2>
    <div class="actions">${e.obsidian ? `<a href="${esc(e.obsidian)}">Open in Obsidian</a>` : ""}<button data-graph="${esc(e.id)}">Show in graph</button>
      <button data-ask="What has ${esc(e.name)} [${esc(e.id)}] been doing recently, and who are they connected to?">Ask about this</button></div>
    <div class="signals"><span>Yield (hit rate)</span><div class="track"><i style="width:${100 * e.yield}%;background:${css("--s-llm")}"></i></div><span class="num">${e.yield.toFixed(2)}</span>
      <span>Centrality</span><div class="track"><i style="width:${100 * (e.centrality || 0)}%;background:${css("--s-semantic")}"></i></div><span class="num">${(e.centrality || 0).toFixed(2)}</span>
      <span>Prior</span><div class="track"><i style="width:${100 * e.prior}%;background:${css("--s-periphery")}"></i></div><span class="num">${e.prior.toFixed(2)}</span></div>
    <h4>Watching via</h4><p>${handles || "<span class='meta'>no handle yet: Claude Code can resolve it</span>"}</p>
    ${e.via_label ? `<h4>Discovered via</h4><p>${nodeLink(e.discovered_via, e.via_label)}</p>` : ""}
    <h4>Items (${e.items.length})</h4>${e.items.map((i) => `<div class="rel"><span class="num" style="min-width:28px">${Math.round(i.score)}</span>${nodeLink(i.id, i.title)}</div>`).join("") || "<p class='meta'>none yet</p>"}
    <h4>Connections</h4>${e.links.filter((l) => l.name).map((l) => `<div class="rel"><span class="r">${esc(l.rel)}</span>${nodeLink(l.src === e.id ? l.dst : l.src, l.name)}<span class="meta">${esc(l.evidence || "")}</span></div>`).join("") || "<p class='meta'>none</p>"}`);
}
const openNode = (id) => (/^(person|org|topic):/.test(id) ? showEntity(id) : showItem(id));

// ---------------------------------------------------------------- graph
let cy;
async function loadGraph(focus = "") {
  const g = await api(`/api/graph?${new URLSearchParams({ focus, days: state.days })}`);
  $("#graphTitle").textContent = focus ? `Neighbourhood of ${focus}` : "Today's frontier and the people/orgs behind it";
  const color = (n) => (n.kind === "item" ? css("--accent") : n.kind === "topic" ? css("--s-combinatorial")
    : n.sub === "person" ? css("--s-emergence") : css("--accent-2"));
  const els = [
    ...g.nodes.map((n) => ({ data: { ...n, size: n.kind === "item" ? 14 + n.score / 4 : n.kind === "topic" ? 16 + Math.min(n.score, 60) / 2 : 10 + n.score / 8, color: color(n) } })),
    ...g.edges.map((e, i) => ({ data: { id: `e${i}`, source: e.source, target: e.target, rel: e.rel } })),
  ];
  if (cy) cy.destroy();
  cy = cytoscape({
    container: $("#cy"), elements: els, wheelSensitivity: 0.25,
    style: [
      { selector: "node", style: { "background-color": "data(color)", width: "data(size)", height: "data(size)", label: "data(label)",
          "font-size": 9, color: css("--text"), "text-wrap": "ellipsis", "text-max-width": 140, "text-valign": "bottom", "text-margin-y": 4,
          "text-background-color": css("--panel"), "text-background-opacity": 0.7, "text-background-padding": 1 } },
      { selector: "node[kind = 'item']", style: { shape: "round-rectangle" } },
      { selector: "node[kind = 'topic']", style: { shape: "diamond", "font-size": 11, "font-weight": 600 } },
      { selector: "edge[rel = 'about']", style: { "line-color": css("--panel-2"), "target-arrow-shape": "none" } },
      { selector: "edge", style: { width: 1, "line-color": css("--line"), "curve-style": "bezier", "target-arrow-shape": "triangle",
          "target-arrow-color": css("--line"), "arrow-scale": 0.6 } },
      { selector: "edge[rel = 'cites']", style: { "line-style": "dashed" } },
      { selector: ":selected", style: { "border-width": 3, "border-color": css("--accent-2") } },
    ],
    layout: { name: "cose", animate: false, nodeRepulsion: 9000, idealEdgeLength: 70, padding: 30 },
  });
  cy.on("tap", "node", (ev) => { if (ev.target.data("kind") !== "topic") openNode(ev.target.id()); });
  cy.on("dbltap", "node", (ev) => loadGraph(ev.target.id()));
}

// ---------------------------------------------------------------- watch, activity, brief, stats
async function loadWatch() {
  const rows = await api(`/api/watchlist?status=${state.watch}`);
  $("#watchTable").innerHTML = `<thead><tr><th>Name</th><th>Type</th><th>Status</th><th>Yield</th><th>Hits</th><th>Centrality</th><th>Depth</th><th>Via</th></tr></thead><tbody>${
    rows.map((r) => `<tr data-open="${esc(r.id)}"><td>${esc(r.name)}</td><td>${esc(r.type)}</td><td>${esc(r.status)}</td>
      <td class="num">${r.yield.toFixed(2)}</td><td class="num">${r.hits}</td><td class="num">${(r.centrality || 0).toFixed(2)}</td>
      <td class="num">${r.depth}</td><td class="meta">${esc((r.discovered_via || "").slice(0, 40))}</td></tr>`).join("")}</tbody>`;
}
async function loadActivity() {
  const ev = await api("/api/activity");
  $("#activity").innerHTML = ev.map((e) => `<li><time>${esc(e.ts.replace("T", " ").slice(0, 16))}</time><span class="kind ${esc(e.kind)}">${esc(e.kind)}</span>
    <span>${e.ref ? nodeLink(e.ref, e.message) : esc(e.message)}</span></li>`).join("");
}
async function loadBrief() {
  const b = await api("/api/brief");
  const md = b.markdown.replace(/\[\[([^\]|]+)\|([^\]]+)\]\]/g, "**$2**").replace(/\[\[([^\]]+)\]\]/g, "**$1**");
  $("#brief").innerHTML = DOMPurify.sanitize(marked.parse(md));
}
async function loadStats() {
  const s = await api("/api/stats");
  $("#sideStats").innerHTML = [["today", "new today"], ["frontier", "frontier 2d"], ["watched", "watching"], ["edges", "links"]]
    .map(([k, l]) => `<div class="stat"><b>${(s[k] ?? 0).toLocaleString()}</b><span>${l}</span></div>`).join("") +
    `<div class="stat" style="grid-column:span 2"><span>last run ${s.last_run ? ago(s.last_run.finished || s.last_run.started) : "never"} · LLM ${esc(s.llm)} · X ${esc(s.x)}</span></div>`;
  $("#runBtn").classList.toggle("busy", s.running);
  $("#runBtn").textContent = s.running ? "Running…" : "Run now";
}

// ---------------------------------------------------------------- chat
function openChat(q) {
  $("#chat").classList.add("open"); $("#chat").setAttribute("aria-hidden", "false");
  $("#chatInput").focus();
  if (q) { $("#chatInput").value = q; $("#chatForm").requestSubmit(); }
}
function renderAnswer(text) {
  const html = DOMPurify.sanitize(marked.parse(text));
  return html.replace(/\[((?:arxiv|gh|hf|url|s2|person|org|topic):[^\]\s]+)\]/g, (_, id) => `<span class="cite" data-open="${esc(id)}">${esc(id)}</span>`);
}
async function ask(q) {
  const log = $("#chatLog");
  log.insertAdjacentHTML("beforeend", `<div class="msg user">${esc(q)}</div><div class="msg bot"><span class="meta">Searching the radar…</span></div>`);
  const bot = log.lastElementChild;
  log.scrollTop = log.scrollHeight;
  const res = await fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: q, history: state.history.slice(-6) }) });
  const reader = res.body.getReader(); const dec = new TextDecoder();
  let buf = "", text = "", sources = [];
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const raw = buf.slice(0, idx); buf = buf.slice(idx + 2);
      const ev = (raw.match(/^event: (.*)$/m) || [, "message"])[1];
      const data = JSON.parse((raw.match(/^data: (.*)$/m) || [, "null"])[1]);
      if (ev === "sources") sources = data;
      else if (ev === "message") { text += data; bot.innerHTML = renderAnswer(text); log.scrollTop = log.scrollHeight; }
    }
  }
  bot.insertAdjacentHTML("beforeend", `<div class="srcs">${sources.slice(0, 10).map((s) => `<span class="cite" data-open="${esc(s.id)}" title="${esc(s.label)}">${esc(s.label.slice(0, 36))}</span>`).join("")}</div>`);
  state.history.push({ role: "user", content: q }, { role: "assistant", content: text });
}

// ---------------------------------------------------------------- routing & events
function route() {
  closeDrawer();
  const v = (location.hash.match(/#\/(\w+)/) || [, "today"])[1];
  $$(".view").forEach((s) => s.classList.toggle("hidden", s.id !== `view-${v}`));
  $$(".nav").forEach((a) => a.classList.toggle("active", a.dataset.view === v));
  ({ today: loadFeed, graph: () => loadGraph(), watch: loadWatch, activity: loadActivity, brief: loadBrief }[v] || loadFeed)();
}
function seg(id, key, attr, after) {
  $(id).addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    $$(`${id} button`).forEach((x) => x.classList.toggle("on", x === b));
    state[key] = b.dataset[attr]; after();
  });
}
seg("#daysSeg", "days", "days", loadFeed);
seg("#kindSeg", "kind", "kind", loadFeed);
seg("#watchSeg", "watch", "status", loadWatch);
$("#modality").onchange = (e) => { state.modality = e.target.value; loadFeed(); };
$("#small").onchange = (e) => { state.small = e.target.checked; loadFeed(); };
$("#unrated").onchange = (e) => { state.unrated = e.target.checked; loadFeed(); };
$("#graphReset").onclick = () => loadGraph();
$("#drawerClose").onclick = closeDrawer;
$("#askOpen").onclick = () => openChat();
$("#chatClose").onclick = () => { $("#chat").classList.remove("open"); $("#chat").setAttribute("aria-hidden", "true"); };
$("#chatForm").onsubmit = (e) => { e.preventDefault(); const q = $("#chatInput").value.trim(); if (q) { $("#chatInput").value = ""; ask(q); } };
$("#runBtn").onclick = async () => { await api("/api/run", { method: "POST" }); loadStats(); };

document.addEventListener("click", (e) => {
  const t = e.target;
  if (t.closest("[data-rate]")) { e.stopPropagation(); const c = t.closest(".card"); return rate(c.dataset.id, t.closest("[data-rate]").dataset.rate); }
  if (t.closest("[data-open]")) { e.preventDefault(); return openNode(t.closest("[data-open]").dataset.open); }
  if (t.closest("[data-graph]")) { location.hash = "#/graph"; setTimeout(() => loadGraph(t.closest("[data-graph]").dataset.graph), 50); return; }
  if (t.closest("[data-ask]")) return openChat(t.closest("[data-ask]").dataset.ask);
  if (t.closest("[data-q]")) return openChat(t.closest("[data-q]").dataset.q);
  const c = t.closest(".card"); if (c) { state.focus = +c.dataset.i; showItem(c.dataset.id); }
});
document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); return openChat(); }
  if (e.key === "Escape") { closeDrawer(); $("#chat").classList.remove("open"); return; }
  if (["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)) return;
  const n = state.items.length; if (!n) return;
  if (e.key === "j" || e.key === "k") {
    state.focus = Math.max(0, Math.min(n - 1, state.focus + (e.key === "j" ? 1 : -1)));
    $("#feed").innerHTML = state.items.map(card).join("");
    $(`.card[data-i="${state.focus}"]`)?.scrollIntoView({ block: "nearest" });
  } else if (e.key === "Enter" && state.focus >= 0) showItem(state.items[state.focus].id);
  else if ("1234".includes(e.key) && e.key && state.focus >= 0) rate(state.items[state.focus].id, LABELS[+e.key - 1][0]);
});
window.addEventListener("hashchange", route);
route(); loadStats(); setInterval(loadStats, 20000);
