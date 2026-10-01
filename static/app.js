// Image Organizer AI — frontend logic (vanilla JS, no build step).
const $ = (id) => document.getElementById(id);

let cwd = "";
let selectedFolder = "";
let lastDryRunJob = null;   // params of last dry run, for "run it for real"
let lastRealLogId = null;
let pollTimer = null;

/* ---------------- folder browser ---------------- */

async function browse(path) {
  const res = await fetch("/api/browse?path=" + encodeURIComponent(path || ""));
  const data = await res.json();
  cwd = data.cwd || "";
  $("cwd").textContent = data.roots ? "(roots)" : cwd || "/";
  $("btn-up").disabled = data.roots || !data.parent;
  $("btn-up").onclick = () => browse(data.parent);
  const list = $("dir-list");
  list.innerHTML = "";
  if (data.roots) {
    for (const r of data.entries) {
      const li = document.createElement("li");
      li.className = "root";
      li.textContent = "📁 " + r.name;
      li.onclick = () => browse(r.path);
      list.appendChild(li);
    }
  } else {
    for (const d of data.entries) {
      const li = document.createElement("li");
      li.textContent = "📁 " + d.name;
      li.ondblclick = () => browse(d.path);
      li.onclick = () => { selectFolder(d.path); };
      const open = document.createElement("button");
      open.textContent = "→";
      open.title = "Open";
      open.style.cssText = "float:right;padding:2px 10px;";
      open.onclick = (e) => { e.stopPropagation(); browse(d.path); };
      li.appendChild(open);
      list.appendChild(li);
    }
    // also allow selecting the current folder itself
    const li = document.createElement("li");
    li.innerHTML = "<i>✔ Use this folder: " + escapeHtml(cwd) + "</i>";
    li.onclick = () => selectFolder(cwd);
    list.appendChild(li);
  }
}

function selectFolder(path) {
  selectedFolder = path;
  $("selected").textContent = path;
  $("btn-start").disabled = false;
  $("scan-result").textContent = "";
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

/* ---------------- options ---------------- */

async function loadModels() {
  const res = await fetch("/api/models");
  const models = await res.json();
  const sel = $("opt-model");
  sel.innerHTML = "";
  for (const m of models) {
    const o = document.createElement("option");
    o.value = m.name;
    o.textContent = m.name + (m.cached ? " ✓ downloaded" : " (will download)");
    sel.appendChild(o);
  }
}

$("opt-conf").oninput = (e) => { $("conf-val").textContent = Number(e.target.value).toFixed(2); };

/* ---------------- scan ---------------- */

$("btn-scan").onclick = async () => {
  if (!selectedFolder) return;
  $("scan-result").textContent = "counting…";
  const res = await fetch("/api/scan", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ path: selectedFolder, recursive: $("opt-recursive").checked }),
  });
  const data = await res.json();
  $("scan-result").textContent = data.error ? "error: " + data.error
    : `${data.total} image${data.total === 1 ? "" : "s"} found`;
};

/* ---------------- organize ---------------- */

function gatherParams(dryRunOverride) {
  return {
    path: selectedFolder,
    recursive: $("opt-recursive").checked,
    mode: document.querySelector('input[name="mode"]:checked').value,
    dry_run: dryRunOverride !== undefined ? dryRunOverride : $("opt-dryrun").checked,
    conf: parseFloat($("opt-conf").value),
    model: $("opt-model").value,
  };
}

$("btn-start").onclick = () => startOrganize(gatherParams());
$("btn-real").onclick = () => startOrganize(gatherParams(false));

async function startOrganize(params) {
  if (!params.path) return;
  resetProgress();
  $("progress-card").hidden = false;
  $("progress-card").scrollIntoView({ behavior: "smooth" });
  const res = await fetch("/api/organize", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(params),
  });
  const data = await res.json();
  if (data.error) { $("progress-text").textContent = "error: " + data.error; return; }
  lastDryRunJob = params.dry_run ? params : null;
  pollTimer = setInterval(() => pollJob(data.job_id), 600);
}

function resetProgress() {
  clearInterval(pollTimer);
  $("bar-fill").style.width = "0%";
  $("progress-text").textContent = "";
  $("results").hidden = true;
  $("plan-wrap").hidden = true;
  $("errors").hidden = true;
  $("undo-row").hidden = true;
}

async function pollJob(jobId) {
  const res = await fetch("/api/jobs/" + jobId);
  const job = await res.json();
  if (job.total) {
    const pct = Math.round((job.done / job.total) * 100);
    $("bar-fill").style.width = pct + "%";
    $("progress-text").textContent = `${job.done}/${job.total} — ${job.current.split("/").pop()}`;
  } else {
    $("progress-text").textContent = "warming up…";
  }
  if (job.status === "done" || job.status === "error") {
    clearInterval(pollTimer);
    if (job.status === "error") {
      $("progress-text").textContent = "error: " + job.error;
    } else {
      $("bar-fill").style.width = "100%";
      $("progress-text").textContent = job.summary.dry_run ? "dry run complete" : "done";
      showResults(job.summary);
    }
  }
}

function showResults(s) {
  $("results").hidden = false;
  const counts = $("counts");
  counts.innerHTML = "";
  const labels = { people: "🧍 People", animals: "🐾 Animals", objects: "📦 Objects", unsorted: "❓ Unsorted" };
  for (const [cat, n] of Object.entries(s.by_category)) {
    const div = document.createElement("div");
    div.className = "count " + cat;
    div.innerHTML = `<b>${n}</b><span>${labels[cat]}</span>`;
    counts.appendChild(div);
  }

  if (s.dry_run && s.plan) {
    $("plan-wrap").hidden = false;
    const tb = $("plan-table").querySelector("tbody");
    tb.innerHTML = "";
    for (const p of s.plan.slice(0, 200)) {
      const tr = document.createElement("tr");
      const det = p.detections.map((d) => `${d.class} (${d.confidence})`).join(", ") || "—";
      tr.innerHTML = `<td>${escapeHtml(p.src.split("/").pop())}</td><td>${escapeHtml(p.category)}</td><td>${escapeHtml(det)}</td>`;
      tb.appendChild(tr);
    }
    if (s.plan.length > 200) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td colspan="3" class="muted">…and ${s.plan.length - 200} more</td>`;
      tb.appendChild(tr);
    }
  }

  if (s.errors && s.errors.length) {
    $("errors").hidden = false;
    const ul = $("error-list");
    ul.innerHTML = "";
    for (const e of s.errors.slice(0, 50)) {
      const li = document.createElement("li");
      li.textContent = `${e.file.split("/").pop()}: ${e.error}`;
      ul.appendChild(li);
    }
  }

  if (!s.dry_run && s.log_file) {
    lastRealLogId = s.log_file.split("/").pop();
    $("undo-row").hidden = false;
    refreshLogs();
  }
}

/* ---------------- undo ---------------- */

$("btn-undo").onclick = async () => {
  if (!lastRealLogId) return;
  $("undo-result").textContent = "undoing…";
  const res = await fetch("/api/undo", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ log_id: lastRealLogId }),
  });
  const data = await res.json();
  $("undo-result").textContent = data.error ? "error: " + data.error
    : `restored ${data.restored} file${data.restored === 1 ? "" : "s"}` +
      (data.errors.length ? `, ${data.errors.length} issues` : "");
  refreshLogs();
};

async function refreshLogs() {
  const res = await fetch("/api/logs");
  const logs = await res.json();
  const ul = $("log-list");
  ul.innerHTML = logs.length ? "" : '<li class="muted">No runs yet.</li>';
  for (const l of logs) {
    const li = document.createElement("li");
    li.innerHTML = `<span class="grow">📝 ${escapeHtml(l.id)} — ${l.moves} files (${l.mode})<br><span class="muted">${escapeHtml(l.source || "")}</span></span>`;
    const btn = document.createElement("button");
    btn.textContent = "↩ Undo";
    btn.onclick = async () => {
      btn.disabled = true;
      const r = await fetch("/api/undo", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ log_id: l.id }),
      });
      const d = await r.json();
      btn.textContent = d.error ? "error" : `restored ${d.restored}`;
      refreshLogs();
    };
    li.appendChild(btn);
    ul.appendChild(li);
  }
}
$("btn-refresh-logs").onclick = refreshLogs;

/* ---------------- init ---------------- */
browse("");
loadModels();
refreshLogs();
