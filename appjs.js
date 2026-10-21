/* LearnLoop frontend - vanilla JS, no build step */
const $ = (s, el = document) => el.querySelector(s);
const app = $("#app");
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const shuffle = a => { a = [...a]; for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; } return a; };
const norm = s => String(s).toLowerCase().replace(/[^a-z0-9 ]/g, "").replace(/\s+/g, " ").trim();

async function api(path, opts = {}) {
  const r = await fetch("/api" + path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok || data.error) throw new Error(data.error || "Request failed (" + r.status + ")");
  return data;
}
const post = (p, body) => api(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });

let course = null;        // currently opened course (full payload)
let chatHistory = [];

async function refreshStats() {
  const s = await api("/stats");
  $("#streak").textContent = "🔥 " + s.streak;
  $("#xp").textContent = "⚡ " + s.xp + " XP";
}

/* ------------------------------------------------------------------ routing */
window.addEventListener("hashchange", route);
function route() {
  refreshStats().catch(() => {});
  const m = location.hash.match(/^#\/course\/(\d+)/);
  m ? showCourse(+m[1]) : showHome();
}

/* ------------------------------------------------------------------ home */
async function showHome() {
  course = null; chatHistory = []; $("#chatFab").classList.add("hidden"); $("#chatPanel").classList.add("hidden");
  const courses = await api("/courses");
  app.innerHTML = `
    <h1>What do you want to master?</h1>
    <div class="card">
      <form id="newForm">
        <label>Topic</label>
        <input type="text" name="topic" placeholder="e.g. Photosynthesis, Python decorators, Roman history…">
        <div class="row">
          <div style="flex:1"><label>Level</label>
            <select name="level"><option>beginner</option><option>intermediate</option><option>advanced</option></select></div>
          <div style="flex:2"><label>Goal (optional)</label>
            <input type="text" name="goal" placeholder="e.g. pass my exam in 2 weeks"></div>
        </div>
        <label>Your study material (PDF, DOCX, TXT, MD)</label>
        <input type="file" name="files" multiple accept=".pdf,.docx,.txt,.md,.csv">
        <label>Web pages to include (one URL per line)</label>
        <textarea name="urls" rows="2" placeholder="https://…"></textarea>
        <label style="font-weight:600"><input type="checkbox" name="web" checked> Also research the internet for this topic</label>
        <p class="small">Lessons are built only from the material gathered here, so what you upload shapes what you learn.</p>
        <button class="btn" id="goBtn">Create course</button>
      </form>
    </div>
    <h2>Your courses</h2>
    ${courses.length ? courses.map(c => `
      <div class="card course" data-id="${c.id}">
        <div style="flex:1"><b>${esc(c.title)}</b><div class="small">${esc(c.level)} · ${c.done}/${c.total} lessons</div>
          <div class="bar" style="margin-top:8px"><i style="width:${c.total ? 100 * c.done / c.total : 0}%"></i></div></div>
        <button class="btn ghost del" data-id="${c.id}">Delete</button>
      </div>`).join("") : '<p class="small">No courses yet. Create your first one above.</p>'}`;
  $("#newForm").onsubmit = async e => {
    e.preventDefault();
    const fd = new FormData(e.target);
    fd.set("web", e.target.web.checked ? "true" : "false");
    if (!fd.get("topic").trim() && ![...e.target.files.files].length) return alert("Enter a topic or upload a file.");
    $("#goBtn").disabled = true;
    $("#goBtn").innerHTML = '<span class="spin"></span>Researching &amp; building (≈1 min)…';
    try { const r = await api("/courses", { method: "POST", body: fd }); location.hash = "#/course/" + r.id; }
    catch (err) { alert(err.message); showHome(); }
  };
  app.querySelectorAll(".course").forEach(el => el.onclick = e => { if (!e.target.classList.contains("del")) location.hash = "#/course/" + el.dataset.id; });
  app.querySelectorAll(".del").forEach(b => b.onclick = async e => {
    e.stopPropagation();
    if (confirm("Delete this course and its progress?")) { await api("/courses/" + b.dataset.id, { method: "DELETE" }); showHome(); }
  });
}

/* ------------------------------------------------------------------ course page */
let tab = "path";
async function showCourse(id) {
  course = await api("/courses/" + id);
  $("#chatFab").classList.remove("hidden");
  const o = course.outline;
  const total = o.units.reduce((n, u) => n + u.lessons.length, 0);
  const done = o.units.reduce((n, u) => n + u.lessons.filter(l => l.done).length, 0);
  app.innerHTML = `
    <a href="#/" class="small">← All courses</a>
    <h1>${esc(o.title)}</h1><p class="small">${esc(o.description || "")}</p>
    <div class="row"><div class="bar"><i style="width:${total ? 100 * done / total : 0}%"></i></div><span class="small">${done}/${total}</span></div>
    <div class="tabs">
      <button class="tab" data-t="path">Learn</button><button class="tab" data-t="quiz">Quiz</button>
      <button class="tab" data-t="mastery">Mastery</button><button class="tab" data-t="sources">Sources</button>
    </div>
    <div id="tabBody"></div>`;
  app.querySelectorAll(".tab").forEach(b => { b.classList.toggle("on", b.dataset.t === tab); b.onclick = () => { tab = b.dataset.t; showCourse(id); }; });
  ({ path: renderPath, quiz: renderQuizTab, mastery: renderMastery, sources: renderSources })[tab]();
}

function renderPath() {
  let firstOpen = true;
  $("#tabBody").innerHTML = course.outline.units.map((u, ui) => `
    <div class="unit ${ui % 2 ? "alt" : ""}"><h3>Unit ${ui + 1}: ${esc(u.title)}</h3><p>${esc(u.summary || "")}</p></div>
    <div class="path">${u.lessons.map((l, li) => {
      const prevDone = (ui === 0 && li === 0) || (li > 0 ? u.lessons[li - 1].done : course.outline.units[ui - 1].lessons.at(-1).done);
      const state = l.done ? "done" : prevDone ? "current" : "locked";
      const offset = Math.sin((li + ui) * 1.2) * 55;
      return `<div style="transform:translateX(${offset}px);text-align:center">
        <button class="node ${state}" data-u="${ui}" data-l="${li}" ${state === "locked" ? "disabled" : ""}>${l.done ? "★" : state === "locked" ? "🔒" : "▶"}</button>
        <div class="nlabel">${esc(l.title)}${l.done ? `<br><span class="small">best ${l.best}%</span>` : ""}</div></div>`;
    }).join("")}</div>`).join("");
  app.querySelectorAll(".node:not(.locked)").forEach(b => b.onclick = () => startLesson(+b.dataset.u, +b.dataset.l));
}

function renderQuizTab() {
  $("#tabBody").innerHTML = `
    <div class="card">
      <h3>Mastery quiz</h3>
      <p class="small">Fresh questions every time, weighted toward concepts you've missed.</p>
      <label>Focus on (optional)</label><input type="text" id="qFocus" placeholder="e.g. chapter 3, a specific concept">
      <label>Questions</label><select id="qN"><option>5</option><option selected>10</option><option>15</option><option>20</option></select>
      <p><button class="btn blue" id="qGo">Start quiz</button></p>
    </div>`;
  $("#qGo").onclick = async e => {
    e.target.disabled = true; e.target.innerHTML = '<span class="spin"></span>Writing your quiz…';
    try {
      const d = await post(`/courses/${course.id}/quiz`, { n: +$("#qN").value, focus: $("#qFocus").value });
      runSession({ title: "Mastery quiz", exercises: d.exercises, cards: [], kind: "quiz", hearts: 99 });
    } catch (err) { alert(err.message); renderQuizTab(); }
  };
}

function renderMastery() {
  const c = course.concepts;
  $("#tabBody").innerHTML = c.length ? `<div class="card"><h3>Concept mastery</h3>
    ${c.map(x => `<div class="mast"><span class="n">${esc(x.name)}</span><div class="bar"><i style="width:${x.mastery}%;background:${x.mastery > 75 ? "var(--g)" : x.mastery > 45 ? "var(--y)" : "var(--r)"}"></i></div><span class="small">${x.mastery}%</span></div>`).join("")}
    </div><p class="small">Red bars are your weak spots; the quiz focuses on them.</p>`
    : '<p class="small">Finish a lesson to see your mastery map.</p>';
}

function renderSources() {
  const S = course.sources;
  $("#tabBody").innerHTML = `
    <div class="card"><h3>Add more material</h3>
      <input type="file" id="sFiles" multiple accept=".pdf,.docx,.txt,.md,.csv">
      <textarea id="sUrls" rows="2" placeholder="URLs, one per line"></textarea>
      <input type="text" id="sResearch" placeholder="Research the web for… (e.g. 'common exam questions')">
      <p><button class="btn blue" id="sAdd">Add</button></p>
      <p class="small">New material is used for lessons you haven't opened yet, quizzes and chat.</p></div>
    <div class="card"><h3>${S.length} sources</h3>
    ${S.map(s => `<div class="src"><span class="tag">${esc(s.kind)}</span>${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a>` : esc(s.title)}</div>`).join("")}</div>`;
  $("#sAdd").onclick = async e => {
    const fd = new FormData();
    [...$("#sFiles").files].forEach(f => fd.append("files", f));
    fd.set("urls", $("#sUrls").value); fd.set("research", $("#sResearch").value);
    e.target.disabled = true; e.target.innerHTML = '<span class="spin"></span>Adding…';
    try { const r = await api(`/courses/${course.id}/sources`, { method: "POST", body: fd }); alert(r.added + " source(s) added."); showCourse(course.id); }
    catch (err) { alert(err.message); showCourse(course.id); }
  };
}

/* ------------------------------------------------------------------ lesson / quiz player */
async function startLesson(u, l) {
  app.insertAdjacentHTML("beforeend", `<div class="player" id="loading"><div class="pbody center" style="margin-top:30vh"><div class="big">🦉</div><p><span class="spin"></span>Preparing your lesson…</p></div></div>`);
  try {
    const d = await api(`/courses/${course.id}/lesson/${u}/${l}`);
    $("#loading").remove();
    runSession({ ...d, kind: "lesson", unit: u, idx: l, hearts: 5 });
  } catch (err) { $("#loading").remove(); alert(err.message); }
}

function runSession(s) {
  const queue = [...(s.cards || []).map(c => ({ _card: c })), ...s.exercises.map(e => ({ ...e }))];
  const totalEx = s.exercises.length;
  let pos = 0, hearts = s.hearts, correctFirst = 0, xp = 0, answered = 0;
  const results = [];
  const seen = new Set();
  const el = document.createElement("div");
  el.className = "player"; document.body.appendChild(el);

  const exit = () => { el.remove(); showCourse(course.id); };
  function frame(body, foot) {
    el.innerHTML = `<div class="ptop"><button class="x" id="pq">✕</button><div class="bar"><i style="width:${100 * answered / Math.max(1, totalEx)}%"></i></div>
      <b>${s.hearts < 99 ? "❤️ " + hearts : ""}</b></div><div class="pbody">${body}</div><div class="pfoot" id="pf"><div class="in">${foot}</div></div>`;
    $("#pq").onclick = () => confirm("Quit? Progress in this session will be lost.") && exit();
  }

  function next() {
    if (hearts <= 0) return finish(true);
    if (pos >= queue.length) return finish(false);
    const item = queue[pos];
    item._card ? showCard(item._card) : showExercise(item);
  }

  function showCard(c) {
    frame(`<h2>${esc(c.title)}</h2><div class="teach">${esc(c.body)}${c.example ? `<div class="ex">💡 ${esc(c.example)}</div>` : ""}</div>`,
      `<span></span><button class="btn" id="cont">Continue</button>`);
    $("#cont").onclick = () => { pos++; next(); };
  }

  function showExercise(ex) {
    let getAnswer, locked = false;
    let body = `<h2>${esc(ex.q)}</h2>`;
    if (ex.type === "mcq") {
      const opts = shuffle(ex.options.map((t, i) => ({ t, i })));
      body += opts.map((o, k) => `<button class="opt" data-i="${o.i}">${esc(o.t)}</button>`).join("");
    } else if (ex.type === "tf") {
      body += `<button class="opt" data-i="true">True</button><button class="opt" data-i="false">False</button>`;
    } else if (ex.type === "fill") {
      body += `<input class="fill" id="fillIn" autocomplete="off" placeholder="Type your answer">`;
    } else if (ex.type === "order") {
      body += `<div class="ans" id="ans"></div><div class="bank" id="bank"></div>`;
    } else if (ex.type === "match") {
      body += `<div class="match"><div id="mL"></div><div id="mR"></div></div>`;
    }
    frame(body, `<span></span><button class="btn" id="check" disabled>Check</button>`);
    const check = $("#check");
    const setReady = v => check.disabled = !v;
    let value = null;

    if (ex.type === "mcq" || ex.type === "tf") {
      el.querySelectorAll(".opt").forEach(b => b.onclick = () => {
        if (locked) return;
        el.querySelectorAll(".opt").forEach(x => x.classList.remove("sel")); b.classList.add("sel");
        value = b.dataset.i; setReady(true);
      });
      getAnswer = () => {
        const right = ex.type === "tf" ? String(ex.answer) : String(ex.answer);
        el.querySelectorAll(".opt").forEach(b => { if (b.dataset.i === right) b.classList.add("ok"); else if (b.classList.contains("sel")) b.classList.add("bad"); });
        return value === right;
      };
    } else if (ex.type === "fill") {
      const inp = $("#fillIn"); inp.focus();
      inp.oninput = () => setReady(inp.value.trim());
      inp.onkeydown = e => e.key === "Enter" && !check.disabled && check.click();
      getAnswer = () => { const acc = [ex.answer, ...(ex.accept || [])].map(norm); inp.disabled = true; return acc.includes(norm(inp.value)); };
    } else if (ex.type === "order") {
      const placed = [], bank = shuffle(ex.items.map((t, i) => ({ t, i })));
      const draw = () => {
        $("#ans").innerHTML = placed.map((p, k) => `<button class="tok" data-k="${k}">${esc(p.t)}</button>`).join("");
        $("#bank").innerHTML = bank.map((p, k) => `<button class="tok" data-k="${k}">${esc(p.t)}</button>`).join("");
        $("#ans").querySelectorAll(".tok").forEach(b => b.onclick = () => { if (!locked) { bank.push(placed.splice(+b.dataset.k, 1)[0]); draw(); } });
        $("#bank").querySelectorAll(".tok").forEach(b => b.onclick = () => { if (!locked) { placed.push(bank.splice(+b.dataset.k, 1)[0]); draw(); } });
        setReady(!bank.length);
      };
      draw();
      getAnswer = () => placed.every((p, k) => p.i === k);
      ex._reveal = () => `Correct order: ${ex.items.map((t, i) => (i + 1) + ". " + t).join("  ")}`;
    } else if (ex.type === "match") {
      const L = shuffle(ex.pairs.map((p, i) => ({ t: p[0], i }))), R = shuffle(ex.pairs.map((p, i) => ({ t: p[1], i })));
      let selL = null, left = ex.pairs.length, mistakes = 0;
      $("#mL").innerHTML = L.map(p => `<button class="tok" style="width:100%;margin:5px 0" data-i="${p.i}">${esc(p.t)}</button>`).join("");
      $("#mR").innerHTML = R.map(p => `<button class="tok" style="width:100%;margin:5px 0" data-i="${p.i}">${esc(p.t)}</button>`).join("");
      $("#mL").querySelectorAll(".tok").forEach(b => b.onclick = () => { $("#mL").querySelectorAll(".tok").forEach(x => x.classList.remove("sel")); b.classList.add("sel"); selL = b; });
      $("#mR").querySelectorAll(".tok").forEach(b => b.onclick = () => {
        if (!selL) return;
        if (selL.dataset.i === b.dataset.i) { selL.classList.add("gone"); b.classList.add("gone"); selL = null; if (--left === 0) setReady(true); }
        else { mistakes++; b.classList.add("wrong"); setTimeout(() => b.classList.remove("wrong"), 500); }
      });
      getAnswer = () => mistakes === 0;
      ex._reveal = () => "Pairs: " + ex.pairs.map(p => p[0] + " → " + p[1]).join("; ");
    }

    check.onclick = () => {
      locked = true;
      const ok = getAnswer();
      const firstTry = !seen.has(ex);
      seen.add(ex);
      if (firstTry) results.push({ concept: ex.concept || "general", correct: ok });
      if (ok) { if (firstTry) { correctFirst++; xp += 10; } } else { hearts--; queue.push(ex); }   // wrong -> retry at end
      if (firstTry) answered++;
      const reveal = !ok && ex._reveal ? `<div class="small">${esc(ex._reveal())}</div>` : !ok && ex.type === "fill" ? `<div class="small">Answer: ${esc(ex.answer)}</div>` : "";
      const pf = $("#pf"); pf.className = "pfoot " + (ok ? "ok" : "bad");
      pf.innerHTML = `<div class="in"><div><b>${ok ? "Correct! 🎉" : "Not quite"}</b>${reveal}<div class="small" style="color:inherit">${esc(ex.explain || "")}</div></div><button class="btn ${ok ? "" : "red"}" id="cont">Continue</button></div>`;
      $("#cont").onclick = () => { pos++; next(); };
      $("#cont").focus();
    };
  }

  async function finish(failed) {
    const score = Math.round(100 * correctFirst / Math.max(1, totalEx));
    if (!failed) xp += 10;
    try { await post(`/courses/${course.id}/result`, { kind: s.kind, unit: s.unit, idx: s.idx, results, xp, score }); } catch (e) { }
    el.innerHTML = `<div class="pbody center" style="margin-top:14vh">
      <div class="big">${failed ? "💔" : score >= 80 ? "🏆" : "👍"}</div>
      <h1>${failed ? "Out of hearts" : "Lesson complete!"}</h1>
      <p>${failed ? "Review the material and try again." : `You scored <b>${score}%</b> and earned <b>${xp} XP</b>.`}</p>
      ${s.kind === "lesson" && !failed ? `<p class="small">Wrong answers are tracked so your quizzes and tutor focus on them.</p>` : ""}
      <p><button class="btn" id="done">Continue</button>
      ${failed ? `<button class="btn ghost" id="askT">Ask the tutor</button>` : ""}</p></div>`;
    $("#done").onclick = exit;
    if ($("#askT")) $("#askT").onclick = () => { exit(); setTimeout(() => $("#chatFab").click(), 300); };
    refreshStats();
  }
  next();
}

/* ------------------------------------------------------------------ chat */
$("#chatFab").onclick = () => {
  $("#chatPanel").classList.remove("hidden");
  if (!chatHistory.length) addMsg("a", "Hi! Ask me anything about this course. I answer from your sources (and the web if you tick “search web”), then quiz you to check you got it.");
  $("#chatInput").focus();
};
$("#chatClose").onclick = () => $("#chatPanel").classList.add("hidden");
function addMsg(who, text, extra = "") {
  const d = document.createElement("div"); d.className = "msg " + who; d.textContent = text;
  if (extra) d.insertAdjacentHTML("beforeend", extra);
  $("#chatLog").appendChild(d); $("#chatLog").scrollTop = 1e9; return d;
}
$("#chatForm").onsubmit = async e => {
  e.preventDefault();
  const input = $("#chatInput"), text = input.value.trim();
  if (!text || !course) return;
  input.value = ""; addMsg("u", text);
  chatHistory.push({ role: "user", content: text });
  const wait = addMsg("a", "…");
  try {
    const r = await post(`/courses/${course.id}/chat`, { messages: chatHistory, web: $("#chatWeb").checked });
    const links = r.web.map(w => `<a href="${esc(w.url)}" target="_blank" rel="noopener">${esc(w.title)}</a>`).join(" · ");
    const refs = r.refs.length ? `<span class="refs">Sources: ${r.refs.map(x => `[S${x.n}] ${esc(x.title)}`).join("; ")}${links ? "<br>Web: " + links : ""}</span>` : "";
    wait.textContent = r.reply; wait.insertAdjacentHTML("beforeend", refs);
    chatHistory.push({ role: "assistant", content: r.reply });
  } catch (err) { wait.textContent = "Error: " + err.message; chatHistory.pop(); }
  $("#chatLog").scrollTop = 1e9;
};

route();
