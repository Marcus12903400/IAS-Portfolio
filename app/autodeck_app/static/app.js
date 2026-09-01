/* AutoDeck 0.4.2 review page */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const api = {
    async get(url) { const r = await fetch(url); const j = await r.json(); if (!r.ok) throw new Error(j.error || r.statusText); return j; },
    async post(url, body) {
      const opts = { method: "POST" };
      if (body instanceof FormData) opts.body = body;
      else { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify(body || {}); }
      const r = await fetch(url, opts); const j = await r.json(); if (!r.ok) throw new Error(j.error || r.statusText); return j;
    },
  };

  // ------------------------------------------------------------ state
  const state = { scan: null, runId: null, overlays: null, meshLoadedFor: null, job: null, jobSince: 0, layers: {}, xray: false, shading: "smooth" };

  function setStatus(text, cls) { const el = $("status-bar"); el.textContent = text; el.className = "status " + (cls || ""); }
  function log(line, isError) {
    const el = $("log"); const span = document.createElement("span");
    span.textContent = line + "\n"; if (isError) span.className = "err";
    el.appendChild(span); el.scrollTop = el.scrollHeight;
  }

  // ------------------------------------------------------------ three.js
  let renderer, scene, camera, controls, meshObject = null, overlayGroup, meshMaterial, sun;
  const bounds = { min: null, max: null };

  function init3D() {
    const canvas = $("gl");
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    renderer.setPixelRatio(window.devicePixelRatio || 1);
    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x14171c);   // dark ground so the light mesh reads clearly
    camera = new THREE.PerspectiveCamera(45, 1, 1, 1e7);
    camera.up.set(0, 0, 1);
    camera.position.set(-3000, -3000, 2500);
    controls = new THREE.OrbitControls(camera, canvas);
    controls.enableDamping = true;
    scene.add(new THREE.HemisphereLight(0xffffff, 0x30363f, 0.75));
    sun = new THREE.DirectionalLight(0xffffff, 0.85); scene.add(sun);
    const sun2 = new THREE.DirectionalLight(0xffffff, 0.25); sun2.position.set(-1, 1, 1); scene.add(sun2);
    updateLight();
    overlayGroup = new THREE.Group(); scene.add(overlayGroup);
    meshMaterial = new THREE.MeshStandardMaterial({ color: 0xd8d8dc, roughness: 0.7, metalness: 0.0, side: THREE.DoubleSide,
      polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 2, transparent: true, opacity: 1.0 });
    resize();
    window.addEventListener("resize", resize);
    (function animate() { requestAnimationFrame(animate); controls.update(); renderer.render(scene, camera); })();
  }

  function resize() {
    const host = $("view3d"); const w = host.clientWidth || 800, h = host.clientHeight || 600;
    renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
  }

  // -------------------------------------------------- mesh shading controls
  function updateLight() {
    if (!sun) return;
    const az = ((parseFloat($("opt-light").value) || 45) * Math.PI) / 180;
    const el = ((parseFloat($("opt-light-el").value) || 35) * Math.PI) / 180;
    const r = Math.cos(el);
    sun.position.set(r * Math.cos(az), r * Math.sin(az), Math.sin(el));
  }

  function heightColor(t) {
    // navy -> teal -> warm white
    const stops = [[0.16, 0.32, 0.55], [0.30, 0.74, 0.70], [0.97, 0.95, 0.91]];
    const s = t <= 0.5 ? 0 : 1, u = t <= 0.5 ? t * 2 : (t - 0.5) * 2;
    return [0, 1, 2].map((k) => stops[s][k] + (stops[s + 1][k] - stops[s][k]) * u);
  }

  function applyShading(mode) {
    state.shading = mode;
    if (!meshObject) return;
    const g = meshObject.geometry;
    if (mode === "smooth" || mode === "flat") {
      meshMaterial.vertexColors = false;
      meshMaterial.color.set(0xd8d8dc);
      meshMaterial.flatShading = mode === "flat";
    } else {
      const pos = g.getAttribute("position");
      let colors = g.getAttribute("color");
      if (!colors || colors.count !== pos.count) {
        colors = new THREE.BufferAttribute(new Float32Array(pos.count * 3), 3);
        g.setAttribute("color", colors);
      }
      if (mode === "height") {
        g.computeBoundingBox();
        const z0 = g.boundingBox.min.z, span = Math.max(g.boundingBox.max.z - z0, 1e-6);
        for (let i = 0; i < pos.count; i++) {
          const c = heightColor((pos.getZ(i) - z0) / span);
          colors.setXYZ(i, c[0], c[1], c[2]);
        }
      } else {  // slope: floors light, walls dark
        const nrm = g.getAttribute("normal");
        for (let i = 0; i < pos.count; i++) {
          const nz = Math.abs(nrm.getZ(i));
          const v = 0.22 + 0.78 * nz * nz;
          colors.setXYZ(i, v, v * 1.02, Math.min(1, v * 1.08));
        }
      }
      colors.needsUpdate = true;
      meshMaterial.vertexColors = true;
      meshMaterial.color.set(0xffffff);
      meshMaterial.flatShading = false;
    }
    meshMaterial.needsUpdate = true;
  }

  function fitView() {
    if (!bounds.min) return;
    const min = bounds.min, max = bounds.max;
    const center = new THREE.Vector3((min[0] + max[0]) / 2, (min[1] + max[1]) / 2, (min[2] + max[2]) / 2);
    const size = Math.max(max[0] - min[0], max[1] - min[1], max[2] - min[2]) || 1000;
    controls.target.copy(center);
    camera.position.set(center.x - size * 0.9, center.y - size * 0.9, center.z + size * 0.75);
    camera.near = size / 1000; camera.far = size * 50; camera.updateProjectionMatrix();
    controls.update();
  }

  function extendBounds(points, scale) {
    scale = scale || 1;
    for (const p of points) {
      if (!Array.isArray(p) || !isFinite(p[0]) || !isFinite(p[1])) continue;
      const x = p[0] * scale, y = p[1] * scale, z = (p[2] || 0) * scale;
      if (!bounds.min) { bounds.min = [x, y, z]; bounds.max = [x, y, z]; continue; }
      bounds.min[0] = Math.min(bounds.min[0], x); bounds.min[1] = Math.min(bounds.min[1], y); bounds.min[2] = Math.min(bounds.min[2], z);
      bounds.max[0] = Math.max(bounds.max[0], x); bounds.max[1] = Math.max(bounds.max[1], y); bounds.max[2] = Math.max(bounds.max[2], z);
    }
  }

  async function loadMesh(force) {
    const scan = state.scan;
    if (!scan || !scan.preview_ready) return;
    if (!force && state.meshLoadedFor === scan.sha256) return;
    const r = await fetch("/api/scan/mesh"); if (!r.ok) return;
    const buf = await r.arrayBuffer();
    const head = new Uint32Array(buf, 0, 4);
    if (head[0] !== 0x4D455348) { log("bad mesh stream", true); return; }
    const nv = head[1], nf = head[2];
    const positions = new Float32Array(buf, 16, nv * 3);
    const indices = new Uint32Array(buf, 16 + nv * 12, nf * 3);
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geometry.setIndex(new THREE.BufferAttribute(indices, 1));
    geometry.computeVertexNormals();
    if (meshObject) { scene.remove(meshObject); meshObject.geometry.dispose(); }
    meshObject = new THREE.Mesh(geometry, meshMaterial);
    const mm = (state.overlays && state.overlays.mm_per_unit) || unitScale(scan.units_detected);
    meshObject.scale.set(mm, mm, mm);
    scene.add(meshObject);
    applyShading(state.shading);
    state.meshLoadedFor = scan.sha256;
    $("view3d-empty").hidden = true;
    bounds.min = null; bounds.max = null;
    const bb = scan.preview; extendBounds([bb.bbox_min, bb.bbox_max], mm);
    fitView();
    log(`Mesh preview: ${nf.toLocaleString()} faces (of ${bb.face_count.toLocaleString()}), scale ${mm} mm/unit`);
  }

  function unitScale(u) { return ({ mm: 1, cm: 10, m: 1000, in: 25.4 })[u] || 1; }

  function rescaleMesh() {
    if (!meshObject || !state.overlays) return;
    const mm = state.overlays.mm_per_unit || 1;
    meshObject.scale.set(mm, mm, mm);
  }

  function buildLayerObjects() {
    while (overlayGroup.children.length) { const c = overlayGroup.children.pop(); c.geometry && c.geometry.dispose(); }
    state.layers = {};
    if (!state.overlays) return;
    for (const layer of state.overlays.layers) {
      const color = new THREE.Color(layer.color);
      let obj;
      if (layer.kind === "points") {
        const pts = new Float32Array(layer.world.flat());
        const g = new THREE.BufferGeometry(); g.setAttribute("position", new THREE.BufferAttribute(pts, 3));
        obj = new THREE.Points(g, new THREE.PointsMaterial({ color, size: 7, sizeAttenuation: false, depthTest: !state.xray }));
      } else {
        const segs = [];
        for (const poly of layer.world) {
          for (let i = 0; i + 1 < poly.length; i++) { segs.push(poly[i][0], poly[i][1], poly[i][2], poly[i + 1][0], poly[i + 1][1], poly[i + 1][2]); }
        }
        const g = new THREE.BufferGeometry(); g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(segs), 3));
        obj = new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color, depthTest: !state.xray }));
      }
      obj.visible = !!layer.on; obj.renderOrder = 2;
      overlayGroup.add(obj);
      state.layers[layer.id] = { obj, layer };
      if (layer.kind === "points") extendBounds(layer.world);
      else for (const poly of layer.world) extendBounds(poly);
    }
  }

  function renderLayerPanel() {
    const host = $("layers"); host.innerHTML = "";
    if (!state.overlays) { host.textContent = "No run open"; host.className = "muted"; return; }
    host.className = "";
    for (const layer of state.overlays.layers) {
      const row = document.createElement("label"); row.className = "layer";
      const cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = !!layer.on;
      cb.addEventListener("change", () => { layer.on = cb.checked; const e = state.layers[layer.id]; if (e) e.obj.visible = cb.checked; renderFlat(); });
      const sw = document.createElement("span"); sw.className = "swatch"; sw.style.background = layer.color;
      const name = document.createElement("span"); name.textContent = layer.label;
      const count = document.createElement("span"); count.className = "count"; count.textContent = layer.count;
      row.append(cb, sw, name, count); host.appendChild(row);
    }
  }

  // ------------------------------------------------------------ flat view (SVG)
  function renderFlat() {
    const svg = $("flat"); svg.innerHTML = "";
    if (!state.overlays) { $("flat-empty").hidden = false; return; }
    $("flat-empty").hidden = true;
    let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
    const grow = (p) => { minx = Math.min(minx, p[0]); maxx = Math.max(maxx, p[0]); miny = Math.min(miny, p[1]); maxy = Math.max(maxy, p[1]); };
    for (const layer of state.overlays.layers) {
      if (layer.kind === "points") { layer.flat.forEach(grow); }
      else for (const poly of layer.flat) poly.forEach(grow);
    }
    if (!isFinite(minx)) return;
    const pad = 150;
    svg.setAttribute("viewBox", `${minx - pad} ${-(maxy + pad)} ${maxx - minx + 2 * pad} ${maxy - miny + 2 * pad}`);
    const ns = "http://www.w3.org/2000/svg";
    const g = document.createElementNS(ns, "g");
    const strokeScale = (maxx - minx) / 1400;
    for (const layer of state.overlays.layers) {
      if (!layer.on) continue;
      if (layer.kind === "points") {
        for (const p of layer.flat) {
          const c = document.createElementNS(ns, "circle");
          c.setAttribute("cx", p[0]); c.setAttribute("cy", -p[1]); c.setAttribute("r", 6 * strokeScale);
          c.setAttribute("fill", "none"); c.setAttribute("stroke", layer.color); c.setAttribute("stroke-width", 2 * strokeScale);
          g.appendChild(c);
        }
        continue;
      }
      for (const poly of layer.flat) {
        const el = document.createElementNS(ns, "polyline");
        el.setAttribute("points", poly.map((p) => `${p[0]},${-p[1]}`).join(" "));
        el.setAttribute("fill", "none"); el.setAttribute("stroke", layer.color);
        el.setAttribute("stroke-width", (layer.id.startsWith("raw") ? 1.2 : layer.id === "pattern" ? 0.9 : 2) * strokeScale);
        el.setAttribute("vector-effect", "non-scaling-stroke");
        g.appendChild(el);
      }
    }
    for (const panel of state.overlays.panels) {
      if (!panel.bbox_flat) continue;
      const t = document.createElementNS(ns, "text");
      t.setAttribute("x", panel.bbox_flat[0][0]); t.setAttribute("y", -(panel.bbox_flat[1][1] + 30));
      t.setAttribute("class", "flat-label"); t.setAttribute("font-size", 28 * strokeScale);
      t.textContent = `Panel ${panel.id} (${panel.role})`;
      g.appendChild(t);
    }
    svg.appendChild(g);
  }

  // ------------------------------------------------------------ data refresh
  async function refreshState() {
    const s = await api.get("/api/state");
    state.scan = s.scan; state.runId = s.run_id;
    renderScanInfo(); renderFiles(s.run_files);
    $("btn-outline").disabled = !(s.scan && s.scan.preview_ready) || !!s.active_job;
    $("btn-autofit").disabled = !s.run_id || !!s.active_job;
    $("btn-ingest").disabled = !s.run_id || !!s.active_job;
    if (s.active_job && (!state.job || state.job !== s.active_job.job_id)) trackJob(s.active_job.job_id);
    return s;
  }

  function renderScanInfo() {
    const el = $("scan-info"); const scan = state.scan;
    if (!scan) { el.textContent = "No scan loaded"; el.className = "info muted"; return; }
    el.className = "info";
    const mb = (scan.size_bytes / 1e6).toFixed(0);
    const units = scan.units_detected ? `units in file: ${scan.units_detected}` : "no units in file (choose below)";
    const pv = scan.preview ? ` · ${scan.preview.face_count.toLocaleString()} faces` : "";
    el.innerHTML = `<b>${scan.name}</b> · ${mb} MB${pv}<br>${units}<br><span class="muted">${scan.path}</span>`;
  }

  function renderFiles(files) {
    const el = $("files");
    if (!state.runId) { el.textContent = "Open or make a run first"; el.className = "info muted"; return; }
    el.className = "info files";
    const order = ["final_auto.dxf", "final.dxf", "outline.3dm", "auto_cam.3dm", "outline.dxf", "autofit_report.md", "outline_report.md", "final_report.md", "calibration_report.md"];
    const labels = { "final_auto.dxf": "final_auto.dxf (auto-fit, for VCarve)", "final.dxf": "final.dxf (from your drawing)", "outline.3dm": "outline.3dm (draw on this in Rhino)",
      "auto_cam.3dm": "auto_cam.3dm (auto-fit, review in Rhino)", "outline.dxf": "outline.dxf", "autofit_report.md": "auto-fit report",
      "outline_report.md": "outline report", "final_report.md": "ingest report", "calibration_report.md": "calibration report" };
    el.innerHTML = `<div class="muted">run ${state.runId}</div>`;
    for (const name of order) {
      const a = document.createElement("a");
      if (files && files[name]) {
        a.href = `/api/file/${encodeURIComponent(state.runId)}/${name}` + (name.endsWith(".md") ? "?inline=1" : "");
        if (name.endsWith(".md")) a.target = "_blank";
        a.textContent = "⬇ " + (labels[name] || name);
      } else { a.className = "missing"; a.textContent = "· " + (labels[name] || name) + " (not yet)"; }
      el.appendChild(a);
    }
  }

  async function refreshRuns() {
    const r = await api.get("/api/runs"); const host = $("runs"); host.innerHTML = "";
    if (!r.runs.length) { host.innerHTML = '<div class="muted">none yet</div>'; return; }
    for (const run of r.runs) {
      const div = document.createElement("div"); div.className = "run" + (run.run_id === state.runId ? " current" : "");
      const bits = [`${run.panels} panels`, run.pattern || (run.teak ? "teak" : null), run.files["final_auto.dxf"] ? "auto-fit DXF" : (run.files["auto_cam.3dm"] ? "auto-fit" : null),
        run.files["final.dxf"] ? "drawn DXF" : null, run.input_exists ? null : "scan file missing"].filter(Boolean).join(" · ");
      div.innerHTML = `<div class="name">${run.run_id}</div><div class="meta">${run.modified} · ${bits}</div>`;
      if (!run.input_exists) div.title = "The scan this run was made from is not at its recorded path; the run cannot be reopened.";
      div.addEventListener("click", () => run.input_exists && startJob("/api/run/open", { run_id: run.run_id }, `Opening ${run.run_id}`));
      host.appendChild(div);
    }
  }

  async function refreshOverlays() {
    try {
      const ov = await api.get("/api/overlays");
      state.overlays = ov;
      // "#only=final_auto_file" (comma-separated ids): a bookmarkable verification view
      const only = new URLSearchParams((location.hash || "").replace(/^#/, "")).get("only");
      if (only) { const keep = only.split(","); for (const l of state.overlays.layers) l.on = keep.includes(l.id); }
      buildLayerObjects(); renderLayerPanel(); renderFlat(); rescaleMesh(); renderPanelQuality();
    } catch (e) { state.overlays = null; renderLayerPanel(); renderFlat(); renderPanelQuality(); }
  }

  function renderPanelQuality() {
    const el = $("panel-quality");
    if (!el) return;
    if (!state.overlays || !state.overlays.panels || !state.overlays.panels.length) {
      el.textContent = "No run open"; el.className = "info muted"; return;
    }
    el.className = "info";
    el.innerHTML = "";
    for (const p of state.overlays.panels) {
      const f = p.flatten || {};
      const how = f.strategy === "rigid-planar" ? "flat panel, measured in its own plane"
        : (f.strategy ? "unrolled (true development)" : "");
      const bits = [];
      if (typeof f.tilt_deg === "number") bits.push(`tilt ${f.tilt_deg.toFixed(1)}°`);
      if (typeof f.area_change_percent === "number") bits.push(`area ${f.area_change_percent >= 0 ? "+" : ""}${f.area_change_percent.toFixed(2)}%`);
      if (typeof f.edge_strain_p95_percent === "number") bits.push(`strain p95 ${f.edge_strain_p95_percent.toFixed(2)}%`);
      const warn = f.status && f.status !== "GOOD" ? ` · <b>${f.status}</b>` : "";
      const div = document.createElement("div");
      div.innerHTML = `<b>Panel ${p.id}</b> (${p.role}) — ${how}${warn}<br><span class="muted">${bits.join(" · ") || "no metrics"}</span>`;
      div.style.marginBottom = "6px";
      el.appendChild(div);
    }
  }

  async function refreshAll() {
    await refreshState(); await refreshOverlays(); await loadMesh(false); await refreshRuns();
    if (!meshObject && bounds.min) fitView();
  }

  // ------------------------------------------------------------ jobs
  async function startJob(url, body, label) {
    try {
      setStatus(label + "…", "busy");
      const r = await api.post(url, body);
      log("— " + label);
      trackJob(r.job_id);
    } catch (e) { setStatus(e.message, "error"); log(e.message, true); }
  }

  function trackJob(jobId) {
    state.job = jobId; state.jobSince = 0;
    $("btn-outline").disabled = true; $("btn-autofit").disabled = true; $("btn-ingest").disabled = true;
    const poll = async () => {
      try {
        const j = await api.get(`/api/jobs/${jobId}?since=${state.jobSince}`);
        for (const line of j.log) log(line, line.includes("ERROR"));
        state.jobSince = j.log_length;
        if (j.status === "done" || j.status === "error") {
          state.job = null;
          setStatus(j.status === "done" ? `${j.kind} finished in ${j.elapsed_s}s` : `${j.kind} failed`, j.status === "done" ? "" : "error");
          await refreshAll();
          return;
        }
        setStatus(`${j.kind} running (${j.elapsed_s}s)`, "busy");
      } catch (e) { log(e.message, true); }
      setTimeout(poll, 700);
    };
    poll();
  }

  // ------------------------------------------------------------ wiring
  function wire() {
    const dz = $("dropzone");
    ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("over"); }));
    ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("over"); }));
    dz.addEventListener("drop", (e) => { const f = e.dataTransfer.files && e.dataTransfer.files[0]; if (f) uploadScan(f); });
    $("file-input").addEventListener("change", (e) => { const f = e.target.files[0]; if (f) uploadScan(f); e.target.value = ""; });
    $("btn-pick").addEventListener("click", async () => {
      try {
        setStatus("Choose the scan in the file dialog…", "busy");
        const r = await api.post("/api/scan/pick", {});
        if (r.cancelled) { setStatus("Ready"); return; }
        log("— Scan: " + r.scan.name); trackJob(r.job_id);
      } catch (e) { setStatus(e.message, "error"); log(e.message, true); }
    });
    const patternDefaults = { teak: { size: 63.5, hint: "Teak: spacing on centre." },
      diamond: { size: 152.4, hint: "Diamond stitch: long diagonal (the short one is half)." },
      hex: { size: 152.4, hint: "Hexagons: width across the flats." },
      none: { size: "", hint: "No pattern." } };
    $("opt-pattern").addEventListener("change", () => {
      const d = patternDefaults[$("opt-pattern").value] || patternDefaults.teak;
      $("opt-pattern-size").value = d.size; $("opt-pattern-size").disabled = $("opt-pattern").value === "none";
      $("pattern-hint").textContent = d.hint;
    });
    $("btn-outline").addEventListener("click", () => startJob("/api/run/outline",
      { units: $("opt-units").value, layout: $("opt-layout").value,
        pattern: $("opt-pattern").value, pattern_size: $("opt-pattern-size").value }, "Outline"));
    $("btn-autofit").addEventListener("click", () => startJob("/api/run/autofit", {}, "Auto-fit"));
    $("btn-ingest").addEventListener("click", async () => {
      const f = $("ingest-input").files[0]; if (!f) { setStatus("choose the .3dm you drew on", "error"); return; }
      const fd = new FormData(); fd.append("file", f); startJob("/api/run/ingest", fd, "Ingest " + f.name);
    });
    $("btn-fit").addEventListener("click", fitView);
    $("opt-xray").addEventListener("change", (e) => { state.xray = e.target.checked; for (const id in state.layers) { state.layers[id].obj.material.depthTest = !state.xray; state.layers[id].obj.material.needsUpdate = true; } });
    $("opt-opacity").addEventListener("input", (e) => { meshMaterial.opacity = parseFloat(e.target.value); });
    $("opt-shading").addEventListener("change", (e) => applyShading(e.target.value));
    $("opt-light").addEventListener("input", updateLight);
    $("opt-light-el").addEventListener("input", updateLight);
    document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
    // hash: legacy "#flat", or params like "#tab=flat&shade=slope&light=20&lightel=25"
    const hp = new URLSearchParams((location.hash || "").replace(/^#/, ""));
    if (location.hash === "#flat" || hp.get("tab") === "flat") showTab("flat");
    if (hp.get("shade")) $("opt-shading").value = hp.get("shade");
    if (hp.get("light")) $("opt-light").value = hp.get("light");
    if (hp.get("lightel")) $("opt-light-el").value = hp.get("lightel");
    applyShading($("opt-shading").value);
    updateLight();
  }

  function showTab(tab) {
    document.querySelectorAll("#tabs button").forEach((x) => x.classList.toggle("active", x.dataset.tab === tab));
    const flat = tab === "flat";
    $("viewflat").hidden = !flat; $("view3d").style.display = flat ? "none" : "";
    if (!flat) resize();
  }

  async function uploadScan(file) {
    try {
      setStatus(`Uploading ${file.name} (${(file.size / 1e6).toFixed(0)} MB)…`, "busy");
      log(`— Uploading ${file.name}`);
      const fd = new FormData(); fd.append("file", file);
      const r = await api.post("/api/scan/upload", fd);
      trackJob(r.job_id);
    } catch (e) { setStatus(e.message, "error"); log(e.message, true); }
  }

  init3D(); wire();
  refreshAll().catch((e) => log(e.message, true));
  setInterval(() => { if (!state.job) refreshState().catch(() => {}); }, 4000);
})();
