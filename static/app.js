(function () {
  const form = document.getElementById("run-form");
  const submitBtn = document.getElementById("submit-btn");
  const formError = document.getElementById("form-error");
  const logsEl = document.getElementById("logs");
  const jobMeta = document.getElementById("job-meta");
  const previewHead = document.getElementById("preview-head");
  const previewBody = document.getElementById("preview-body");
  const previewEmpty = document.getElementById("preview-empty");
  const downloadLink = document.getElementById("download-link");
  const rowCountEl = document.getElementById("row-count");
  const discoverBlock = document.getElementById("discover-block");
  const csvBlock = document.getElementById("csv-block");
  const manualQuery = document.getElementById("manual_query");

  let pollTimer = null;

  const providerHints = {
    auto: "Tries OSM first, then Google Places if GOOGLE_PLACES_API_KEY is set, then LeadFinder. Google runs before LeadFinder so listings are usually real sites.",
    leadfinder: "Free public API. No key needed. Returns ~5 leads per request (10 req/day).",
    osm: "OpenStreetMap Overpass. Free, no key. Best for well-mapped cities.",
    google: "Google Places API (New). Best quality. Needs GOOGLE_PLACES_API_KEY in .env.local.",
  };

  function setSourceUI() {
    const src = form.querySelector('input[name="source"]:checked').value;
    const isCsv = src === "csv";
    discoverBlock.classList.toggle("hidden", isCsv);
    csvBlock.classList.toggle("hidden", !isCsv);
    document.getElementById("discover_only").closest("label").style.display = isCsv
      ? "none"
      : "";
  }

  const providerSel = document.getElementById("provider-select");
  const providerHintEl = document.getElementById("provider-hint");
  if (providerSel && providerHintEl) {
    providerSel.addEventListener("change", () => {
      providerHintEl.textContent = providerHints[providerSel.value] || "";
    });
  }

  function setManualEnabled() {
    const mode = form.querySelector('input[name="discover_mode"]:checked');
    manualQuery.disabled = !mode || mode.value !== "manual";
  }

  form.querySelectorAll('input[name="source"]').forEach((el) => {
    el.addEventListener("change", setSourceUI);
  });
  form.querySelectorAll('input[name="discover_mode"]').forEach((el) => {
    el.addEventListener("change", setManualEnabled);
  });
  setSourceUI();
  setManualEnabled();

  function esc(s) {
    const d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function renderPreview(rows) {
    previewHead.innerHTML = "";
    previewBody.innerHTML = "";
    if (!rows || !rows.length) {
      previewEmpty.classList.remove("hidden");
      return;
    }
    previewEmpty.classList.add("hidden");
    const keys = Object.keys(rows[0]);
    const hr = document.createElement("tr");
    keys.forEach((k) => {
      const th = document.createElement("th");
      th.textContent = k;
      hr.appendChild(th);
    });
    previewHead.appendChild(hr);
    rows.forEach((row) => {
      const tr = document.createElement("tr");
      keys.forEach((k) => {
        const td = document.createElement("td");
        td.innerHTML = esc(row[k] || "");
        tr.appendChild(td);
      });
      previewBody.appendChild(tr);
    });
  }

  function stopPoll() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  async function pollJob(jobId) {
    const r = await fetch("/api/job/" + encodeURIComponent(jobId));
    const data = await r.json();
    if (!r.ok) {
      jobMeta.textContent = data.error || "Job error";
      stopPoll();
      submitBtn.disabled = false;
      return;
    }
    jobMeta.textContent =
      "Job: " +
      jobId.slice(0, 8) +
      "… — " +
      data.status +
      (data.places_query ? " — Query: " + data.places_query : "");
    logsEl.textContent = (data.logs || []).join("\n");
    logsEl.scrollTop = logsEl.scrollHeight;

    if (data.status === "done") {
      stopPoll();
      submitBtn.disabled = false;
      rowCountEl.textContent =
        data.row_count != null ? "(" + data.row_count + " rows)" : "";
      renderPreview(data.preview_rows || []);
      if (data.download_url) {
        downloadLink.href = data.download_url;
        downloadLink.classList.remove("hidden");
      }
    } else if (data.status === "error") {
      stopPoll();
      submitBtn.disabled = false;
      formError.textContent = data.error || "Unknown error";
      formError.classList.remove("hidden");
    }
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    formError.classList.add("hidden");
    formError.textContent = "";
    downloadLink.classList.add("hidden");
    submitBtn.disabled = true;
    stopPoll();
    logsEl.textContent = "";
    renderPreview([]);
    rowCountEl.textContent = "";
    jobMeta.textContent = "Starting…";

    if (form.querySelector('input[name="discover_mode"]:checked')?.value === "manual") {
      manualQuery.disabled = false;
    }
    const fd = new FormData(form);
    try {
      const r = await fetch("/api/run", { method: "POST", body: fd });
      const data = await r.json();
      if (!data.ok) {
        formError.textContent = data.error || "Request failed";
        formError.classList.remove("hidden");
        submitBtn.disabled = false;
        return;
      }
      const jobId = data.job_id;
      jobMeta.textContent = "Job: " + jobId.slice(0, 8) + "… — running";
      pollTimer = setInterval(() => pollJob(jobId), 1500);
      pollJob(jobId);
    } catch (err) {
      formError.textContent = String(err);
      formError.classList.remove("hidden");
      submitBtn.disabled = false;
    }
  });
})();
