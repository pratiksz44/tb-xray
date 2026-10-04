const MAX_UPLOAD_MB = 20; // keep in sync with backend/config.yaml (api.max_upload_mb)
const MAX_ZIP_MB = 100; // api.max_zip_mb
const ACCEPTED_TYPES = ["image/png", "image/jpeg"];

const fileInput = document.getElementById("file");
const drop = document.getElementById("drop");
const preview = document.getElementById("preview");
const button = document.getElementById("go");
const result = document.getElementById("result");
const single = document.getElementById("single");
const batch = document.getElementById("batch");
const errorBox = document.getElementById("error");
let chosen = null;
let isZip = false;

const percent = (p) => `${(Math.min(Math.max(p, 0), 1) * 100).toFixed(1)}%`;
const duration = (ms) => (ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`);

// Total time measured in the browser, plus the backend's own time from its Server-Timing header.
function latencyText(totalMs, serverTiming) {
  const server = /app;dur=([\d.]+)/.exec(serverTiming ?? "");
  return `Analysed in ${duration(totalMs)}` + (server ? ` (server ${duration(Number(server[1]))})` : "");
}

// Build DOM nodes with textContent (never innerHTML with server data).
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function choose(file) {
  errorBox.textContent = "";
  if (!file) return;
  if (file.name.toLowerCase().endsWith(".zip")) {
    chooseZip(file);
    return;
  }
  if (!ACCEPTED_TYPES.includes(file.type)) {
    errorBox.textContent = "Please choose a PNG or JPEG image, or a ZIP of images.";
    return;
  }
  if (file.size > MAX_UPLOAD_MB * 1024 * 1024) {
    errorBox.textContent = `The file is larger than ${MAX_UPLOAD_MB} MB.`;
    return;
  }
  chosen = file;
  isZip = false;
  single.hidden = false;
  batch.hidden = true;
  if (preview.src) URL.revokeObjectURL(preview.src);
  preview.src = URL.createObjectURL(file);
  preview.hidden = false;
  button.disabled = false;
  result.replaceChildren(el("p", "small", "Ready to analyse."));
}

function chooseZip(file) {
  if (file.size > MAX_ZIP_MB * 1024 * 1024) {
    errorBox.textContent = `The ZIP file is larger than ${MAX_ZIP_MB} MB.`;
    return;
  }
  chosen = file;
  isZip = true;
  single.hidden = true;
  batch.hidden = false;
  button.disabled = false;
  batch.replaceChildren(el("p", "small", `${file.name} ready to analyse.`));
}

function showResult(data, latency) {
  if (!data.is_chest_xray) {
    result.replaceChildren(
      el("p", "verdict warn", "Image not accepted"),
      el("p", undefined, data.message),
      el("div", "small latency", latency),
    );
    return;
  }
  const isTb = data.tb_probability >= data.threshold;
  const meter = el("div", "meter");
  const fill = el("div", "fill");
  fill.style.width = percent(data.tb_probability);
  const tick = el("div", "tick");
  tick.style.left = percent(data.threshold);
  meter.append(fill, tick);
  const probability = el("div", undefined, "TB probability: ");
  probability.append(el("strong", undefined, percent(data.tb_probability)));
  result.replaceChildren(
    el("p", `verdict ${isTb ? "tb" : "ok"}`, data.prediction),
    probability,
    meter,
    el("div", "small", `Marker = decision threshold (${percent(data.threshold)})`),
    el("div", "small latency", latency),
  );
}

function batchRow(item) {
  const row = el("tr");
  row.append(el("td", "name", item.filename));
  if (item.error) {
    row.append(el("td", "verdict-cell warn", "Error"), el("td", "small", item.error));
  } else if (!item.is_chest_xray) {
    row.append(el("td", "verdict-cell warn", "Not accepted"), el("td", "small", item.message));
  } else {
    const isTb = item.tb_probability >= item.threshold;
    row.append(
      el("td", `verdict-cell ${isTb ? "tb" : "ok"}`, item.prediction),
      el("td", undefined, percent(item.tb_probability)),
    );
  }
  return row;
}

function showBatchResult(data, latency) {
  const s = data.summary;
  const summary = el(
    "p",
    "small",
    `${s.total} images · ${s.tb_suspected} TB suspected · ${s.no_tb} no TB signs · ` +
      `${s.not_accepted} not accepted · ${s.errors} errors`,
  );
  const table = el("table", "batch-table");
  const head = el("tr");
  for (const h of ["File", "Result", "TB probability"]) head.append(el("th", undefined, h));
  table.append(el("thead"), el("tbody"));
  table.tHead.append(head);
  table.tBodies[0].append(...data.results.map(batchRow));
  const wrap = el("div", "table-wrap");
  wrap.append(table);
  batch.replaceChildren(summary, el("p", "small", `${latency} for ${s.total} images`), wrap);
}

async function analyse() {
  if (!chosen) return;
  button.disabled = true;
  button.textContent = isZip ? "Analysing images… this can take a few minutes" : "Analysing…";
  errorBox.textContent = "";
  try {
    const body = new FormData();
    body.append("file", chosen, chosen.name);
    const started = performance.now();
    const res = await fetch(isZip ? "/api/predict-batch" : "/api/predict", { method: "POST", body });
    const data = await res.json().catch(() => null);
    const latency = latencyText(performance.now() - started, res.headers.get("Server-Timing"));
    if (res.ok) (isZip ? showBatchResult : showResult)(data, latency);
    else errorBox.textContent = data?.detail ?? `Request failed (${res.status})`;
  } catch {
    errorBox.textContent = "Network error. Please try again.";
  } finally {
    button.disabled = false;
    button.textContent = "Analyse";
  }
}

fileInput.addEventListener("change", () => choose(fileInput.files?.[0] ?? null));
for (const ev of ["dragover", "dragenter"]) {
  drop.addEventListener(ev, (e) => {
    e.preventDefault();
    drop.classList.add("over");
  });
}
for (const ev of ["dragleave", "drop"]) {
  drop.addEventListener(ev, (e) => {
    e.preventDefault();
    drop.classList.remove("over");
  });
}
drop.addEventListener("drop", (e) => choose(e.dataTransfer?.files[0] ?? null));
button.addEventListener("click", analyse);
