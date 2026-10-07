/* AutoDeck 5 review page */
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
  const state = {
    scan: null, runId: null, overlays: null, meshLoadedFor: null, job: null, jobSince: 0,
    layers: {}, xray: false, shading: "smooth", shadingChosen: false, hasTexture: false,
    tab: "3d", seams: [], sheets: null,
    // the seam tool: choose a direction, hover, click
    seamDir: { mode: "along", angle: 45 }, placing: false, hoverSeam: null, hoverWorld: null,
    // what /api/sheets last said about the boat and where its panels sit
    boat: null, panelLayout: [], seamsWorld: [], showSeams3d: true,
    // Have this run's own seams actually arrived? Until they have, state.seams
    // being empty means "not known yet", not "there are none" -- see replanSheets.
    seamsLoaded: false,
    // Bumped by every local edit to the seam list: place, remove, clear, "as
    // placed", undo. A reply that was asked for before the bump is an answer to
    // a question the page has stopped asking, and adopting its seam list
    // wholesale would throw away everything edited while it was on the wire.
    // See seamStamp() and adoptSeams().
    seamsRevision: 0,
    // The revision of the newest local edit that seams.json has NOT been told
    // about yet, and 0 when the file and the page agree. This is the difference
    // between "the server's answer disagrees with me because I have moved on
    // since I asked" and "the server has never heard of what I just did", and
    // only the second one can put a deleted seam back -- see seamStamp().
    seamsPending: 0,
    // Where the seams on screen came from: "run" as they were saved, "placed"
    // once the user has changed them, "auto" straight out of the seam search,
    // "undone" after an undo, "cleared" after Clear all seams. Said in words
    // under the seam list AND on the sheet tab, because nothing on this page
    // used to tell the machine's answer from a hand-placed layout -- which is
    // how a set of hand-placed seams came to be read as the optimiser's output.
    seamsOrigin: "run",
    // What Clear all seams took away, so the undo beside it can put it back.
    seamsCleared: null,
  };

  function setStatus(text, cls) { const el = $("status-bar"); el.textContent = text; el.className = "status " + (cls || ""); }
  function log(line, isError) {
    const el = $("log"); const span = document.createElement("span");
    span.textContent = line + "\n"; if (isError) span.className = "err";
    el.appendChild(span); el.scrollTop = el.scrollHeight;
  }

  // "1 seam", "6 seams". The "(s)" form is how a programmer writes a count and
  // how nobody says one.
  function plural(n, word) { return `${n} ${word}${n === 1 ? "" : "s"}`; }

  // ------------------------------------------------------------ three.js
  let renderer, scene, camera, controls, meshObject = null, overlayGroup, meshMaterial, sun, meshTexture = null;
  let seamGroup = null, seamLines = null, previewLine = null;
  const raycaster = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
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
    seamGroup = new THREE.Group(); scene.add(seamGroup);
    initSeam3D();
    meshMaterial = new THREE.MeshStandardMaterial({ color: 0xd8d8dc, roughness: 0.7, metalness: 0.0, side: THREE.DoubleSide,
      polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 2, transparent: true, opacity: 1.0 });
    resize();
    window.addEventListener("resize", resize);
    // Only the 3D tab shows this canvas, and drawing a hidden one costs a whole
    // frame anyway -- on a machine with no real GPU that was 17 ms out of every
    // 17, taken from the flat view's own drawing and from the seam hover.
    (function animate() {
      requestAnimationFrame(animate);
      controls.update();
      if (!$("view3d").hidden) renderer.render(scene, camera);
    })();
  }

  function resize() {
    // The canvas fills .viewbody, not #view3d: the seam toolbar above it is part
    // of the same column, so measuring the whole view would size the canvas a
    // toolbar too tall and push its bottom edge out of sight.
    const host = $("gl").parentElement; const w = host.clientWidth || 800, h = host.clientHeight || 600;
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
    if (mode === "texture" && !state.hasTexture) mode = "smooth";
    state.shading = mode;
    $("opt-shading").value = mode;
    if (!meshObject) return;
    const g = meshObject.geometry;
    meshMaterial.map = mode === "texture" ? meshTexture : null;
    if (mode === "texture") {
      meshMaterial.vertexColors = false;
      meshMaterial.color.set(0xffffff);       // white base so the map shows unaltered
      meshMaterial.flatShading = false;
      meshMaterial.needsUpdate = true;
      return;
    }
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
    // Header word 3 was reserved and always zero; bit 0 now says a UV block
    // follows the indices. A preview cached before v5 leaves it clear.
    const hasUV = (head[3] & 1) === 1;
    const positions = new Float32Array(buf, 16, nv * 3);
    const indices = new Uint32Array(buf, 16 + nv * 12, nf * 3);
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geometry.setIndex(new THREE.BufferAttribute(indices, 1));
    if (hasUV) {
      const uvs = new Float32Array(buf, 16 + nv * 12 + nf * 12, nv * 2);
      geometry.setAttribute("uv", new THREE.BufferAttribute(uvs, 2));
    }
    geometry.computeVertexNormals();
    if (meshObject) { scene.remove(meshObject); meshObject.geometry.dispose(); }
    meshObject = new THREE.Mesh(geometry, meshMaterial);
    const mm = (state.overlays && state.overlays.mm_per_unit) || unitScale(scan.units_detected);
    meshObject.scale.set(mm, mm, mm);
    scene.add(meshObject);
    state.hasTexture = hasUV && !!(scan.preview && scan.preview.texture);
    await loadTexture();
    applyShading(state.hasTexture && !state.shadingChosen ? "texture" : state.shading);
    state.meshLoadedFor = scan.sha256;
    $("view3d-empty").hidden = true;
    bounds.min = null; bounds.max = null;
    const bb = scan.preview; extendBounds([bb.bbox_min, bb.bbox_max], mm);
    fitView();
    log(`Mesh preview: ${nf.toLocaleString()} faces (of ${bb.face_count.toLocaleString()}), scale ${mm} mm/unit`
        + (state.hasTexture ? " · textured" : ""));
  }

  // Load the scan's own diffuse texture. Kept off the renderer's global
  // outputEncoding on purpose: changing that would shift every existing
  // shading mode the user has already tuned their light sliders against.
  function loadTexture() {
    return new Promise((resolve) => {
      const select = $("opt-shading");
      const option = select.querySelector('option[value="texture"]');
      if (!state.hasTexture) {
        if (option) { option.disabled = true; option.textContent = "Texture (none in this scan)"; }
        if (meshTexture) { meshTexture.dispose(); meshTexture = null; }
        meshMaterial.map = null; meshMaterial.needsUpdate = true;
        resolve();
        return;
      }
      if (option) { option.disabled = false; option.textContent = "Texture (scan photo)"; }
      new THREE.TextureLoader().load(
        "/api/scan/texture?v=" + encodeURIComponent(state.scan.sha256),
        (texture) => {
          if (meshTexture) meshTexture.dispose();
          meshTexture = texture;
          // OBJ V runs bottom-up and three.js flips by default, which is the
          // pairing OBJLoader relies on -- so leave flipY alone.
          texture.anisotropy = renderer.capabilities.getMaxAnisotropy();
          texture.needsUpdate = true;
          resolve();
        },
        undefined,
        () => { state.hasTexture = false; log("could not load the scan texture", true); resolve(); },
      );
    });
  }

  function unitScale(u) { return ({ mm: 1, cm: 10, m: 1000, in: 25.4 })[u] || 1; }

  function rescaleMesh() {
    if (!meshObject || !state.overlays) return;
    const mm = state.overlays.mm_per_unit || 1;
    meshObject.scale.set(mm, mm, mm);
  }

  function buildLayerObjects() {
    // Materials as well as geometries: a fresh LineBasicMaterial / PointsMaterial
    // is made per layer below, so every run opened without disposing them left
    // another eight behind on the GPU.
    while (overlayGroup.children.length) {
      const c = overlayGroup.children.pop();
      c.geometry && c.geometry.dispose();
      c.material && c.material.dispose();
    }
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
    // The seams are not one of the run's overlay layers -- they come from
    // /api/sheets and change every time one is placed -- but to the user they
    // are just another thing drawn on the boat, so they belong in this list.
    const row = document.createElement("label"); row.className = "layer";
    const cb = document.createElement("input"); cb.type = "checkbox"; cb.id = "layer-seams3d"; cb.checked = state.showSeams3d;
    cb.addEventListener("change", () => { state.showSeams3d = cb.checked; if (seamGroup) seamGroup.visible = cb.checked; });
    const sw = document.createElement("span"); sw.className = "swatch"; sw.style.background = "#ff375f";
    const name = document.createElement("span"); name.textContent = "Seams on the deck";
    const count = document.createElement("span"); count.className = "count"; count.textContent = state.seams.length;
    row.append(cb, sw, name, count); host.appendChild(row);
  }

  // ------------------------------------------------------- the boat-plan frame
  //
  // The flat view is drawn in the PLACED frame -- the frame final_auto.dxf and
  // every seam is measured in -- with model y negated, because SVG y runs down.
  // Two further transforms put the deck back the way it sits in the boat, so
  // the fabricator lays seams out on something they recognise:
  //
  //   * each panel moves back by its own nest offset. Nest mode only ever
  //     TRANSLATES a panel, so placed - nest_offset is the true boat-plan
  //     arrangement, and it goes on as a per-panel translate.
  //   * the whole thing turns so the bow points up the screen. The bow
  //     direction is longitudinal_axis * bow_sign, which the API hands over
  //     ready made as boat.bow_direction. A model direction (dx, dy) draws
  //     along (dx, -dy) because of the y flip, so turning the bow to screen up,
  //     which is (0, -1), needs
  //         theta = -90deg - atan2(-by, bx)
  //     and SVG's rotate(theta) is exactly [[cos,-sin],[sin,cos]] on those
  //     coordinates, so one transform on one group does it.
  //
  // Both are presentation only. Nothing in state.seams ever leaves the placed
  // frame, so a seam is stored and cut in the same numbers whatever the view is
  // doing, and flatToModel() below is the exact inverse -- checked with numbers
  // by tools/ui_smoke.py, because getting it backwards would drop seams
  // somewhere other than where they were clicked and nothing on screen would
  // say so.
  const ZERO = [0, 0];
  const flat = { theta: 0, cos: 1, sin: 0, bow: null, offsets: {}, rings: {}, order: [], assign: {} };

  function updateFlatFrame() {
    flat.theta = 0; flat.cos = 1; flat.sin = 0; flat.bow = null; flat.offsets = {};
    const boat = state.boat;
    const bow = boat && boat.bow_direction;
    if (bow && isFinite(bow[0]) && isFinite(bow[1]) && (bow[0] || bow[1])) {
      flat.bow = bow;
      flat.theta = -90 - (Math.atan2(-bow[1], bow[0]) * 180) / Math.PI;
      const r = (flat.theta * Math.PI) / 180;
      flat.cos = Math.cos(r); flat.sin = Math.sin(r);
      for (const panel of state.panelLayout || []) flat.offsets[panel.panel_id] = panel.nest_offset_mm || ZERO;
    }
  }

  // placed mm -> SVG user units, for the panel the point belongs to.
  function modelToFlat(x, y, panelId) {
    const off = flat.offsets[panelId] || ZERO;
    const bx = x - off[0], by = -(y - off[1]);
    return { x: bx * flat.cos - by * flat.sin, y: bx * flat.sin + by * flat.cos };
  }

  // SVG user units -> placed mm, plus the panel it landed on (null off the deck).
  function flatToModel(sx, sy) {
    const bx = sx * flat.cos + sy * flat.sin;          // R(-theta)
    const by = -sx * flat.sin + sy * flat.cos;
    const mx = bx, my = -by;                            // boat-plan mm
    let pid = null;
    for (const id of flat.order) {
      const off = flat.offsets[id] || ZERO;
      if (pointInPanel(id, mx + off[0], my + off[1])) { pid = id; break; }
    }
    // Off the deck the point still has to come back as a number the API can be
    // asked about, so it borrows the nearest panel's offset and reports no
    // panel; the hover then answers "not on a panel" instead of guessing.
    const off = flat.offsets[pid === null ? nearestPanel(mx, my) : pid] || ZERO;
    return { x: mx + off[0], y: my + off[1], panel_id: pid };
  }

  function nearestPanel(mx, my) {
    let best = null, bestDistance = Infinity;
    for (const id of flat.order) {
      const off = flat.offsets[id] || ZERO, bb = flat.rings[id].bbox;
      const x = mx + off[0], y = my + off[1];
      const dx = Math.max(bb[0] - x, 0, x - bb[2]), dy = Math.max(bb[1] - y, 0, y - bb[3]);
      const d = dx * dx + dy * dy;
      if (d < bestDistance) { bestDistance = d; best = id; }
    }
    return best;
  }

  // Panel outlines for the page's own point-in-panel test. They come from the
  // fitted CAM outline when there is one, and from the raw wall line and
  // obstacles before auto-fit has run -- edges either way, never the pattern.
  // The pattern is not an edge, and the user was explicit about that.
  const RING_LAYERS = [["final_auto_file"], ["final_file"], ["raw_outer", "raw_obstacles"]];

  function isClosedRing(poly) {
    const n = poly.length;
    return n > 3 && Math.abs(poly[0][0] - poly[n - 1][0]) < 1e-6 && Math.abs(poly[0][1] - poly[n - 1][1]) < 1e-6;
  }

  function boxOf(points) {
    const bb = [Infinity, Infinity, -Infinity, -Infinity];
    for (const p of points) {
      bb[0] = Math.min(bb[0], p[0]); bb[1] = Math.min(bb[1], p[1]);
      bb[2] = Math.max(bb[2], p[0]); bb[3] = Math.max(bb[3], p[1]);
    }
    return bb;
  }

  // Which panel a drawn ring or polyline belongs to. Overlap area rather than
  // plain containment, because the fitted outline runs a few millimetres
  // outside the panel bounding box the engine reports; the nested layout keeps
  // whole panels apart, so the biggest overlap is never in doubt. The smallest
  // panel breaks a tie, so a small insert wins over a big panel it sits inside.
  function panelForBox(bb) {
    if (!state.overlays || !state.overlays.panels) return null;
    let best = null, bestOverlap = 0, bestArea = Infinity;
    const grown = [bb[0] - 1, bb[1] - 1, bb[2] + 1, bb[3] + 1];
    for (const panel of state.overlays.panels) {
      const pb = panel.bbox_flat;
      if (!pb) continue;
      const w = Math.min(grown[2], pb[1][0]) - Math.max(grown[0], pb[0][0]);
      const h = Math.min(grown[3], pb[1][1]) - Math.max(grown[1], pb[0][1]);
      if (w <= 0 || h <= 0) continue;
      const overlap = w * h, area = (pb[1][0] - pb[0][0]) * (pb[1][1] - pb[0][1]);
      if (overlap > bestOverlap * 1.000001 || (overlap > bestOverlap * 0.999999 && area < bestArea)) {
        best = panel.id; bestOverlap = overlap; bestArea = area;
      }
    }
    return best;
  }

  // The same question, for something that is only being DRAWN rather than used
  // as a hit target, where "no panel" is not an answer the drawing can use.
  //
  // A drawable that misses every panel box gets no nest offset taken off it, so
  // on the bow-up view it is drawn wherever that panel happens to have been
  // nested -- measured on the cached boat, two of the seventy-three auto-fit
  // corner markers landed 2376 mm from the deck they belong to, floating in
  // open water beside the hull, on a layer that is on by default. They miss
  // only just: both sit 2.7 and 4.0 mm outside panel 2's reported box, which is
  // ordinary slack between the engine's panel bounds and the fitted outline
  // that runs a little outside them. So a near miss falls back to the nearest
  // panel, and only something genuinely far from every panel -- further than a
  // seam gap and a piece gap put together, which is as much slack as any of
  // this geometry has a reason to have -- is left unassigned.
  const ASSIGN_REACH_MM = 30.0;

  function panelForDrawing(bb) {
    const hit = panelForBox(bb);
    if (hit !== null) return hit;
    if (!state.overlays || !state.overlays.panels) return null;
    let best = null, bestDistance = Infinity;
    for (const panel of state.overlays.panels) {
      const pb = panel.bbox_flat;
      if (!pb) continue;
      const dx = Math.max(pb[0][0] - bb[2], 0, bb[0] - pb[1][0]);
      const dy = Math.max(pb[0][1] - bb[3], 0, bb[1] - pb[1][1]);
      const d = Math.hypot(dx, dy);
      if (d < bestDistance) { best = panel.id; bestDistance = d; }
    }
    return bestDistance <= ASSIGN_REACH_MM ? best : null;
  }

  function buildHitRings() {
    flat.rings = {}; flat.order = []; flat.assign = {};
    if (!state.overlays || !state.overlays.panels) return;
    const byId = {};
    for (const layer of state.overlays.layers) byId[layer.id] = layer;
    let rings = [];
    for (const group of RING_LAYERS) {
      rings = [];
      for (const id of group) {
        const layer = byId[id];
        if (!layer || layer.kind === "points") continue;
        for (const poly of layer.flat) if (isClosedRing(poly)) rings.push(poly);
      }
      if (rings.length) break;
    }
    for (const ring of rings) {
      const bb = boxOf(ring);
      const pid = panelForBox(bb);
      if (pid === null) continue;
      const entry = flat.rings[pid] || (flat.rings[pid] = { rings: [], bbox: [Infinity, Infinity, -Infinity, -Infinity] });
      entry.rings.push(ring);
      entry.bbox = [Math.min(entry.bbox[0], bb[0]), Math.min(entry.bbox[1], bb[1]),
                    Math.max(entry.bbox[2], bb[2]), Math.max(entry.bbox[3], bb[3])];
    }
    // Smallest panel first, so a small insert that lives inside a big panel's
    // bounding box answers the lookup before the big one is even tried.
    flat.order = Object.keys(flat.rings).map(Number)
      .sort((a, b) => panelBoxArea(a) - panelBoxArea(b) || a - b);
    // Which panel each drawn polyline belongs to, worked out once per overlay
    // load: renderFlat needs it for every line it draws to know which nest
    // offset to take off, and it cannot change until the overlays reload.
    for (const layer of state.overlays.layers) {
      const source = layer.kind === "points" ? layer.flat.map((p) => [p]) : layer.flat;
      flat.assign[layer.id] = source.map((poly) => panelForDrawing(boxOf(poly)));
    }
  }

  function panelBoxArea(pid) {
    const bb = flat.rings[pid].bbox;
    return (bb[2] - bb[0]) * (bb[3] - bb[1]);
  }

  // Even-odd crossing count over every ring of the panel at once, so the
  // console and hatch cut-outs punch real holes: a point inside the outline but
  // inside a hole crosses an even number of edges and is correctly outside.
  function pointInPanel(pid, x, y) {
    const entry = flat.rings[pid];
    if (!entry) return false;
    const bb = entry.bbox;
    if (x < bb[0] || x > bb[2] || y < bb[1] || y > bb[3]) return false;
    let inside = false;
    for (const ring of entry.rings) {
      for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
        const yi = ring[i][1], yj = ring[j][1];
        if ((yi > y) !== (yj > y)) {
          const t = (y - yi) / (yj - yi);
          if (x < ring[i][0] + t * (ring[j][0] - ring[i][0])) inside = !inside;
        }
      }
    }
    return inside;
  }

  function panelForPlacedPoint(x, y) {
    for (const id of flat.order) if (pointInPanel(id, x, y)) return id;
    return null;
  }

  // ------------------------------------------------------------ flat view (SVG)
  const NS = "http://www.w3.org/2000/svg";
  // The live hover preview lives in its own group so it can be redrawn on every
  // pointer move without rebuilding the rest of the picture.
  const flatPreview = document.createElementNS(NS, "g");

  function renderFlat() {
    const svg = $("flat"); svg.innerHTML = "";
    if (!state.overlays) { $("flat-empty").hidden = false; return; }
    $("flat-empty").hidden = true;
    let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
    const grow = (q) => { minx = Math.min(minx, q.x); maxx = Math.max(maxx, q.x); miny = Math.min(miny, q.y); maxy = Math.max(maxy, q.y); };
    for (const layer of state.overlays.layers) {
      const owners = flat.assign[layer.id] || [];
      if (layer.kind === "points") layer.flat.forEach((p, i) => grow(modelToFlat(p[0], p[1], owners[i])));
      else layer.flat.forEach((poly, i) => { for (const p of poly) grow(modelToFlat(p[0], p[1], owners[i])); });
    }
    if (!isFinite(minx)) return;
    // Bow up makes the view portrait, so the longer side is what the line
    // weights should scale against; in the unrotated nest view that is the same
    // number this has always used.
    const strokeScale = Math.max(maxx - minx, maxy - miny) / 1400;
    // Enough margin for a panel label, which is drawn above the top of its own
    // panel and would otherwise be cropped off by the toolbar.
    const pad = Math.max(150, 60 * strokeScale);
    const width = maxx - minx + 2 * pad, height = maxy - miny + 2 * pad;
    svg.setAttribute("viewBox", `${minx - pad} ${miny - pad} ${width} ${height}`);
    const g = document.createElementNS(NS, "g");
    for (const layer of state.overlays.layers) {
      if (!layer.on) continue;
      const owners = flat.assign[layer.id] || [];
      if (layer.kind === "points") {
        layer.flat.forEach((p, i) => {
          const q = modelToFlat(p[0], p[1], owners[i]);
          const c = document.createElementNS(NS, "circle");
          c.setAttribute("cx", q.x); c.setAttribute("cy", q.y); c.setAttribute("r", 6 * strokeScale);
          c.setAttribute("fill", "none"); c.setAttribute("stroke", layer.color); c.setAttribute("stroke-width", 2 * strokeScale);
          g.appendChild(c);
        });
        continue;
      }
      layer.flat.forEach((poly, i) => {
        const el = document.createElementNS(NS, "polyline");
        el.setAttribute("points", poly.map((p) => { const q = modelToFlat(p[0], p[1], owners[i]); return `${q.x},${q.y}`; }).join(" "));
        el.setAttribute("fill", "none"); el.setAttribute("stroke", layer.color);
        el.setAttribute("stroke-width", (layer.id.startsWith("raw") ? 1.2 : layer.id === "pattern" ? 0.9 : 2) * strokeScale);
        el.setAttribute("vector-effect", "non-scaling-stroke");
        g.appendChild(el);
      });
    }
    for (const panel of state.overlays.panels) {
      if (!panel.bbox_flat) continue;
      const bb = panel.bbox_flat;
      // Whichever corner of the panel ends up highest on the screen. Unrotated
      // that is the top-left corner this always used; bow up, it keeps each
      // label at the forward end of its own panel instead of piling every one
      // of them up at the stern, which is where "top left in model space" lands
      // once the boat is stood on end.
      let q = null;
      for (const c of [[bb[0][0], bb[0][1]], [bb[0][0], bb[1][1]], [bb[1][0], bb[0][1]], [bb[1][0], bb[1][1]]]) {
        const p = modelToFlat(c[0], c[1], panel.id);
        if (!q || p.y < q.y - 1e-9 || (Math.abs(p.y - q.y) < 1e-9 && p.x < q.x)) q = p;
      }
      q = { x: q.x, y: q.y - 20 * strokeScale };
      const t = document.createElementNS(NS, "text");
      t.setAttribute("x", q.x); t.setAttribute("y", q.y);
      t.setAttribute("class", "flat-label"); t.setAttribute("font-size", 28 * strokeScale);
      // Only the ANCHOR is turned, by modelToFlat: the rotation is baked into
      // the coordinates rather than hung on the group, so the label travels
      // with its panel and still comes out upright. Turning the text as well
      // would rotate it twice and stand it on its end.
      t.textContent = `Panel ${panel.id} (${panel.role})`;
      g.appendChild(t);
    }
    drawSeams(g);
    svg.appendChild(g);
    drawBoatMarks(svg, minx - pad, miny - pad, width, height);
    svg.appendChild(flatPreview);
    drawFlatPreview();
  }

  // BOW at the top and STERN at the bottom. They sit outside the drawing so
  // they are always upright, and they only appear when the run actually knows
  // which end is which -- pointing the wrong way with confidence would be worse
  // than not marking it at all.
  function drawBoatMarks(svg, x0, y0, width, height) {
    if (!flat.bow) return;
    const boat = state.boat || {};
    const unsure = typeof boat.bow_confidence === "number" && boat.bow_confidence < 0.5;
    const marks = [[y0 + height * 0.04, unsure ? "▲ BOW (not certain)" : "▲ BOW"], [y0 + height * 0.975, "STERN"]];
    for (const mark of marks) {
      const t = document.createElementNS(NS, "text");
      t.setAttribute("x", x0 + width / 2); t.setAttribute("y", mark[0]);
      t.setAttribute("text-anchor", "middle"); t.setAttribute("class", "boat-mark");
      t.setAttribute("font-size", Math.max(width, height) / 42);
      t.textContent = mark[1];
      svg.appendChild(t);
    }
  }

  // ------------------------------------------------------------ seams
  // How finely a stored seam is walked to find where it crosses from one panel
  // to the next, and how many bisections then refine each crossing. 160 samples
  // and 8 bisections put a boundary within a twentieth of a millimetre on a two
  // metre seam, which is far finer than a screen pixel.
  const SEAM_SAMPLES = 160, SEAM_BISECTIONS = 8;
  // How far past the end of a chord the hovered point may sit and still count
  // as being on it. The API rounds point_flat to a hundredth of a millimetre,
  // so a little slack keeps the preview from flickering on a panel edge.
  const SEGMENT_GRACE_MM = 2.0;

  // A seam is one straight line in the PLACED frame, but in the boat-plan view
  // each panel has moved by its own nest offset, so the line has to be cut at
  // the panel boundaries and each part drawn with that panel's offset. It also
  // stops a seam being drawn across the empty water between two panels.
  function seamPiecesForDrawing(seam) {
    const whole = [{ panel_id: seam.panel_id, a: [seam.x1, seam.y1], b: [seam.x2, seam.y2] }];
    if (!flat.bow || !flat.order.length) return whole;      // nest view: nothing has moved
    const at = (t) => [seam.x1 + (seam.x2 - seam.x1) * t, seam.y1 + (seam.y2 - seam.y1) * t];
    const panelAt = (t) => { const q = at(t); return panelForPlacedPoint(q[0], q[1]); };
    const pids = [];
    for (let i = 0; i <= SEAM_SAMPLES; i++) pids.push(panelAt(i / SEAM_SAMPLES));
    const edge = (i, from) => {
      let lo = (i - 1) / SEAM_SAMPLES, hi = i / SEAM_SAMPLES;
      for (let k = 0; k < SEAM_BISECTIONS; k++) {
        const mid = (lo + hi) / 2;
        if (panelAt(mid) === from) lo = mid; else hi = mid;
      }
      return (lo + hi) / 2;
    };
    const pieces = [];
    let i = 0;
    while (i <= SEAM_SAMPLES) {
      const pid = pids[i];
      let j = i;
      while (j + 1 <= SEAM_SAMPLES && pids[j + 1] === pid) j++;
      if (pid !== null) {
        const t0 = i === 0 ? 0 : edge(i, pids[i - 1]);
        const t1 = j === SEAM_SAMPLES ? 1 : edge(j + 1, pid);
        if (t1 > t0) pieces.push({ panel_id: pid, a: at(t0), b: at(t1) });
      }
      i = j + 1;
    }
    return pieces.length ? pieces : whole;
  }

  // Each seam is drawn twice: a dark casing, then the dashed red line over it.
  // The deck this is drawn on is a solid field of parallel plank lines, and the
  // seam the user places most often -- the long one -- runs PARALLEL to them,
  // so a two pixel red dash on its own was very nearly invisible in exactly the
  // case that matters. The casing gives every seam its own dark edge whatever
  // it is lying over.
  function drawSeams(group) {
    for (const seam of state.seams) {
      for (const piece of seamPiecesForDrawing(seam)) {
        const a = modelToFlat(piece.a[0], piece.a[1], piece.panel_id);
        const b = modelToFlat(piece.b[0], piece.b[1], piece.panel_id);
        for (const [cls, width] of [["seam-casing", 6], ["seam", 2.5]]) {
          const line = document.createElementNS(NS, "line");
          line.setAttribute("x1", a.x); line.setAttribute("y1", a.y);
          line.setAttribute("x2", b.x); line.setAttribute("y2", b.y);
          line.setAttribute("class", cls);
          // Both use non-scaling-stroke, so the width is screen pixels, not mm.
          line.setAttribute("stroke-width", width);
          group.appendChild(line);
        }
      }
    }
  }

  function seamLabel(seam, index) {
    const length = seam.length_mm !== undefined ? seam.length_mm : Math.hypot(seam.x2 - seam.x1, seam.y2 - seam.y1);
    const how = { along: "along the boat", across: "across the boat",
                  angle: `${seam.angle_deg}° off the centreline` }[seam.mode];
    return `Seam ${index + 1} · ${Math.round(length)} mm${how ? " · " + how : ""}`;
  }

  function renderSeamList() {
    renderSeamSource();
    // Clearing is the way OUT of a layout that has gone wrong, so the only
    // thing that may switch it off is having nothing to clear. It is
    // deliberately not tied to the sheet plan, to a job, or to whether anything
    // fits: see setReplanBusy.
    const clear = $("btn-clear-seams");
    clear.disabled = clearBusy || !state.seams.length;
    clear.title = state.seams.length ? "" : "There are no seams on this run to clear.";
    const host = $("seam-list");
    host.innerHTML = "";
    if (!state.seams.length) {
      host.textContent = "No seams yet — pick a direction above, press Place seam, then click on the deck.";
      host.className = "info muted";
      return;
    }
    host.className = "info";
    state.seams.forEach((seam, index) => {
      const item = document.createElement("div");
      item.className = "seam-item";
      const row = document.createElement("div");
      row.className = "seam-row";
      const label = document.createElement("span");
      label.textContent = seamLabel(seam, index);
      const keep = document.createElement("label");
      keep.className = "as-placed";
      keep.title = "Leave this seam exactly where it was put — do not straighten it to the boat or onto an edge.";
      const box = document.createElement("input");
      box.type = "checkbox"; box.checked = seam.snap === false;
      box.addEventListener("change", () => { seam.snap = !box.checked; seamsChanged(); });
      keep.append(box, document.createTextNode("as placed"));
      const remove = document.createElement("button");
      remove.className = "linkbtn"; remove.textContent = "remove";
      remove.addEventListener("click", () => { state.seams.splice(index, 1); seamsChanged(); });
      row.append(label, keep, remove);
      item.appendChild(row);
      if (seam.snap_note) {
        const note = document.createElement("div");
        note.className = "note"; note.textContent = seam.snap_note;
        item.appendChild(note);
      }
      host.appendChild(item);
    });
  }

  // WHICH seam set is on screen, in words, under the list and again on the
  // Sheet layout tab.
  //
  // The seam search replaces every seam on the run, and until now nothing
  // anywhere said whether the six lines under it were its answer or the four the
  // fabricator had put there by hand. That is exactly how a hand-placed layout
  // -- with pieces 2.4 m long on a 2.03 m sheet -- was read as the optimiser's
  // output and reported as a broken optimiser. One line would have settled it.
  function seamSourceWords() {
    const n = state.seams.length;
    if (!n) {
      if (state.seamsOrigin === "cleared") return "No seams — you cleared them all.";
      if (state.seamsOrigin === "placed") return "No seams — you removed them all.";
      return "No seams saved with this run yet.";
    }
    if (state.seamsOrigin === "auto") return `${plural(n, "seam")} from the automatic layout.`;
    if (state.seamsOrigin === "undone") return `${plural(n, "seam")} you placed, put back.`;
    if (state.seamsOrigin === "placed") return `${plural(n, "seam")} you placed.`;
    return `${plural(n, "seam")} saved with this run.`;
  }

  function renderSeamSource() {
    const words = seamSourceWords();
    for (const id of ["seam-source", "sheets-source"]) {
      const el = $(id);
      if (!el) continue;
      el.hidden = !state.runId;
      el.textContent = words;
    }
  }

  // What the seams have actually bought, said where the seams are being placed.
  // The Sheet layout tab carries the same numbers, but nobody is looking at
  // that tab while deciding whether this deck needs one more join -- and "does
  // everything fit yet" is the only question the seam list cannot answer.
  function renderSeamTally() {
    const el = $("seam-tally");
    const data = state.sheets;
    if (!state.runId || !data || !data.available || !data.preview) { el.hidden = true; return; }
    el.hidden = false;
    const sheets = (data.preview.sheets || []).length;
    const pieces = data.piece_count || 0;
    const over = data.oversize || [];
    el.className = "info " + (over.length ? "bad-note" : "muted");
    el.textContent = "";
    // A piece that will not fit is not ON a sheet, so it must not be counted
    // onto one: "13 pieces on 2 sheets, 5 too big" reads as a job that is nearly
    // there, and it is a job that cannot be cut.
    const head = document.createElement("div");
    head.textContent = over.length
      ? `${over.length} of ${plural(pieces, "piece")} will not fit a 40×80″ sheet:`
      : `${plural(pieces, "piece")} on ${plural(sheets, "sheet")} — every piece fits.`;
    el.appendChild(head);
    for (const item of over) el.appendChild(oversizeLine(item));
    // A note under it, so "too big" reads as a thing to do rather than a thing
    // that has happened to you. The seam list is right above this, and removing
    // a seam is as legitimate an answer as adding one.
    if (over.length) {
      const what = document.createElement("div");
      what.className = "note";
      what.textContent = "Place a seam across each of those, or press Find the best seam layout. "
        + "Nothing here stops you removing seams first.";
      el.appendChild(what);
    }
  }

  // One piece that will not fit, named, measured, and with what would fix it.
  //
  // "5 pieces still will not fit a sheet" was true, and useless: it did not say
  // WHICH pieces, by how much, or which way they were too big -- and the way a
  // piece is too big decides which way the seam that fixes it has to run. The
  // engine has worked all three out already (`oversize_report` in sheets.py);
  // this only has to show them.
  function oversizeLine(item) {
    const div = document.createElement("div");
    div.className = "oversize";
    const where = document.createElement("b");
    const size = (typeof item.width_mm === "number" && typeof item.length_mm === "number")
      ? ` · ${Math.round(item.width_mm)} × ${Math.round(item.length_mm)} mm` : "";
    where.textContent = `${item.piece_id} on panel ${item.panel_id}${size}`;
    const by = [];
    if (item.over_length_mm > 0) by.push(`${Math.round(item.over_length_mm)} mm too long`);
    if (item.over_width_mm > 0) by.push(`${Math.round(item.over_width_mm)} mm too wide`);
    const fix = document.createElement("div");
    fix.className = "note";
    fix.textContent = (by.join(" and ") || "over the sheet size")
      + " — " + (item.hint || "needs another seam");
    div.append(where, fix);
    return div;
  }

  // A save that did not happen, said where the seams are. Kept out of the seam
  // list itself so the list goes on showing exactly what the user did: the edit
  // is NOT rolled back when the server refuses it, because losing the edit is a
  // worse outcome than an unsaved one, and an unsaved edit that says nothing is
  // worse than both.
  function seamTrouble(text, offerRetry) {
    const el = $("seam-trouble");
    if (!el) return;
    el.innerHTML = "";
    el.hidden = !text;
    if (!text) return;
    el.className = "info bad-note";
    const line = document.createElement("div");
    line.textContent = text;
    el.appendChild(line);
    if (!offerRetry) return;
    const again = document.createElement("button");
    again.className = "linkbtn"; again.textContent = "try again";
    again.addEventListener("click", () => {
      // Its OWN request, so it may switch itself off while that request is on
      // the wire -- and every way out of replanSheets rewrites this line, so it
      // comes back whether the retry worked or not.
      again.disabled = true; again.textContent = "trying…";
      replanSheets();
    });
    el.appendChild(again);
  }

  // Call this, not pushSeams, after ANY local change to state.seams. Bumping
  // the revision first is what tells a reply already on the wire that it is
  // answering the previous question; marking the edit pending is what tells a
  // reply that never heard of it apart from one that did -- see seamStamp.
  async function seamsChanged(origin) {
    state.seamsRevision++;
    state.seamsPending = state.seamsRevision;
    state.seamsOrigin = origin || "placed";
    // Any other edit puts the "undo the clear" offer away with the set it was
    // offering. Left standing while new seams are being placed, pressing it
    // would swap them for the old lot without asking -- an undo that has
    // stopped meaning undo.
    if (state.seamsOrigin !== "cleared" && state.seamsOrigin !== "undone") forgetCleared();
    await pushSeams();
  }

  function forgetCleared() {
    state.seamsCleared = null;
    const el = $("clear-result");
    if (el) { el.hidden = true; el.innerHTML = ""; }
  }

  async function pushSeams() {
    renderSeamList();
    renderFlat();
    await replanSheets();
  }

  // ------------------------------------------------------- clear every seam
  // Asked for by name. A layout that has gone wrong is got out of by taking
  // seams away, and doing that one "remove" at a time on a deck with eight of
  // them is a chore at exactly the moment the user is already annoyed.
  //
  // It throws work away, so it asks first, and the set it takes is kept so the
  // undo beside it can post it straight back -- the same undo the seam search
  // offers, in the same place, worded the same way.
  let clearBusy = false;

  async function clearAllSeams() {
    if (!state.runId || !state.seams.length || clearBusy) return;
    const count = state.seams.length;
    if (!window.confirm(`This removes all ${plural(count, "seam")} from this run. There is an Undo `
                        + "right afterwards that puts them back.\n\nGo ahead?")) return;
    // Take the copy BEFORE anything is posted: the empty list is what erases
    // seams.json, and there is nothing to recover from once it lands.
    state.seamsCleared = state.seams.map((seam) => Object.assign({}, seam));
    clearBusy = true;
    $("btn-clear-seams").disabled = true;
    try {
      state.seams = [];
      await seamsChanged("cleared");
    } finally {
      // Every path, a failed re-plan included: a Clear button left grey after an
      // error reads as a clear still running, and this is the control that has
      // to work when everything else has gone wrong.
      clearBusy = false;
      renderSeamList();
    }
    renderClearUndo(count);
  }

  function renderClearUndo(count) {
    const el = $("clear-result");
    el.hidden = false; el.className = "info"; el.innerHTML = "";
    const line = document.createElement("div");
    line.textContent = `Removed ${plural(count, "seam")}.`;
    el.appendChild(line);
    if (!state.seamsCleared || !state.seamsCleared.length) return;
    const undo = document.createElement("button");
    undo.className = "linkbtn";
    undo.textContent = `undo — put your ${plural(count, "seam")} back`;
    undo.addEventListener("click", async () => {
      undo.disabled = true; undo.textContent = "putting them back…";
      state.seams = (state.seamsCleared || []).map((seam) => Object.assign({}, seam));
      state.seamsCleared = null;
      try {
        await seamsChanged("undone");
        el.className = "info muted";
        el.textContent = `Put your ${plural(state.seams.length, "seam")} back.`;
      } finally {
        // Same rule as everywhere else here: the button comes back on every
        // path, so a server hiccup cannot leave the only way back greyed out.
        if (undo.isConnected) { undo.disabled = false; undo.textContent = `undo — put your ${plural(count, "seam")} back`; }
      }
    });
    el.appendChild(undo);
  }

  // A number box's value, held inside the range the box itself declares, with
  // whatever was out of range written back so the box and the request agree.
  //
  // A `type=number` input does NOT enforce min/max on typed text, and every one
  // of these goes out with every hover and every re-plan -- so one absurd value
  // 400'd the lot: the seam tool went dead with a raw API field name where its
  // hint should be, clicking placed nothing, and a seam removed in that state
  // vanished from the list while staying in seams.json, with nothing on screen
  // saying the two had parted company. Clamping here means a settings box can
  // never take the page and the saved cut data out of step, and the corrected
  // number is visible in the box the user typed it into.
  function boxNumber(id) {
    const el = $(id);
    const raw = el.value.trim();
    if (raw === "") return "";
    const value = Number(raw);
    // Half a number -- a lone minus sign, mid-keystroke -- is not an error to
    // correct, it is a box still being filled in. Send "leave it at the
    // default" for this request and leave what was typed alone.
    if (!isFinite(value)) return "";
    const low = el.min === "" ? -Infinity : Number(el.min);
    const high = el.max === "" ? Infinity : Number(el.max);
    const held = Math.min(Math.max(value, low), high);
    if (held !== value) el.value = String(held);
    return String(held);
  }

  function sheetSettings() {
    const grain = boxNumber("opt-grain");
    return {
      part_spacing_mm: boxNumber("opt-spacing"),
      seam_gap_mm: boxNumber("opt-seamgap"),
      grain_angle_deg: grain === "" ? null : grain,
      allow_180_rotation: $("opt-rot180").checked,
      seam_axis_priority: $("opt-axis-priority").checked,
      seam_axis_snap_deg: boxNumber("opt-axis-snap"),
      seam_snap_enabled: $("opt-snap-enabled").checked,
      seam_snap_angle_deg: boxNumber("opt-snap-angle"),
    };
  }

  // Every control sheetSettings() reads, so "is anything set by hand?" can be
  // answered without a second copy of the defaults: the DOM already keeps each
  // control's shipped value in defaultValue / defaultChecked, and reading it
  // back from there cannot drift out of step with index.html.
  //
  // It has to be asked because GET /api/sheets re-plans with the ENGINE's
  // defaults, not with what these controls say. Fetching it with a grain angle
  // typed in redrew the nesting for a DIFFERENT grain -- the picture and the
  // settings box disagreed, and the DXFs are written from the settings.
  const SHEET_CONTROLS = ["opt-spacing", "opt-seamgap", "opt-grain", "opt-rot180",
                          "opt-axis-priority", "opt-axis-snap", "opt-snap-enabled", "opt-snap-angle"];

  function sheetSettingsAreDefault() {
    return SHEET_CONTROLS.every((id) => {
      const el = $(id);
      return el.type === "checkbox" ? el.checked === el.defaultChecked : el.value === el.defaultValue;
    });
  }

  // ---------------------------------------------- choose direction, hover, click
  // Every place the direction can be chosen -- the left panel and the two view
  // toolbars -- writes to state.seamDir and then redraws the others, so all
  // three read the same whichever one was touched.
  const DIRECTION_CONTROLS = [
    { pick: "seam-dir-side", angle: "seam-angle-side", row: "seam-angle-row-side" },
    { pick: "seam-dir-flat", angle: "seam-angle-flat", row: "seam-angle-row-flat" },
    { pick: "seam-dir-3d", angle: "seam-angle-3d", row: "seam-angle-row-3d" },
  ];
  const PLACE_BUTTONS = ["btn-seam", "btn-seam-flat", "btn-seam-3d"];
  const PLACE_HINTS = ["seam-hint", "seam-hint-flat", "seam-hint-3d"];

  function directionWords() {
    const dir = state.seamDir;
    if (dir.mode === "along") return "Along the boat, bow to stern";
    if (dir.mode === "across") return "Across the boat";
    return `${dir.angle}° off the boat centreline`;
  }

  function setSeamDirection(mode, angle) {
    if (mode) state.seamDir.mode = mode;
    // A seam is a line, not an arrow, so 150 and -30 are the same diagonal and
    // one number in 0..180 covers every diagonal there is -- which is exactly
    // what the engine does with it. Folding it HERE is what keeps the box, the
    // label and the seam that gets placed all saying the same thing: typing -30
    // placed a 150 degree seam while all three boxes and the seam row went on
    // reading -30.
    if (angle !== undefined && angle !== null && isFinite(angle)) {
      state.seamDir.angle = ((Number(angle) % 180) + 180) % 180;
    }
    for (const set of DIRECTION_CONTROLS) {
      $(set.pick).value = state.seamDir.mode;
      $(set.angle).value = state.seamDir.angle;
      $(set.row).hidden = state.seamDir.mode !== "angle";
    }
    clearHoverPreview();
    if (state.placing) setPlaceHint(`${directionWords()} — move over the deck, click to place. Escape stops.`);
  }

  function setPlaceHint(text) { for (const id of PLACE_HINTS) $(id).textContent = text; }

  function setPlacing(on) {
    const armed = !!on && !!state.runId;
    try {
      state.placing = armed;
      for (const id of PLACE_BUTTONS) {
        const button = $(id);
        button.classList.toggle("active", armed);
        button.textContent = armed ? "Done placing" : "Place seam";
      }
      $("flat").classList.toggle("placing", armed);
      $("gl").classList.toggle("placing", armed);
      // Both ways, not just on the way out. Arming has to start from nothing
      // hovered as well: a chord left over from the last time the tool was up
      // would be committed by the very first click, at whatever spot the
      // pointer was over when it was pressed.
      clearHoverPreview();
      // Pressing Place seam with nothing open used to look like a dead button.
      // Say which of the two it is instead.
      setPlaceHint(armed
        ? `${directionWords()} — move over the deck, click to place. Escape stops.`
        : (on ? "Open a run first — there is no deck to put a seam on yet."
              : "Pick a direction, press Place seam, then hover the deck and click where the join goes."));
    } finally {
      // Orbit eats every pointer move, so it has to be off while placing -- and
      // it has to come back on every way out of here, a throw above included,
      // or the 3D view is dead until the page is reloaded.
      if (controls) controls.enabled = !state.placing;
    }
  }

  // One request in flight at a time, no faster than HOVER_MS, and only the
  // newest answer is ever drawn: this fires on every pointer move, and a queue
  // of stale replies would leave the preview trailing behind the pointer.
  const HOVER_MS = 40;
  const hover = { pending: null, inflight: false, seq: 0, shown: -1, last: 0, timer: 0 };

  function hoverAt(point, isWorld) {
    if (!state.placing) return;
    hover.pending = { point: point, world: !!isWorld };
    pumpHover();
  }

  function pumpHover() {
    if (hover.inflight || !hover.pending || !state.placing) return;
    const wait = HOVER_MS - (performance.now() - hover.last);
    if (wait > 0) {
      // A trailing timer, not a dropped move: the pointer's LAST position is the
      // one the user is looking at, so it must go out even if it arrived inside
      // the throttle window.
      if (!hover.timer) hover.timer = setTimeout(() => { hover.timer = 0; pumpHover(); }, wait);
      return;
    }
    const job = hover.pending; hover.pending = null;
    hover.inflight = true; hover.last = performance.now();
    const seq = ++hover.seq;
    const body = Object.assign({
      mode: state.seamDir.mode,
      angle_deg: state.seamDir.mode === "angle" ? state.seamDir.angle : null,
      world: job.world,
    }, sheetSettings());
    if (job.world) body.point_world = job.point; else body.point_flat = job.point;
    // `seq > hover.shown` orders the replies; `state.placing` asks the other
    // question, which is whether the tool is still armed at all. Escape, a tab
    // switch and the pointer leaving the view all disarm it synchronously, but
    // none of them can call back a request already on the wire -- and without
    // this guard that request's answer put the orange line back on the deck of a
    // disarmed tool, told the user to click, and left state.hoverSeam holding a
    // chord that the NEXT arming would commit at the first click, wherever the
    // pointer happened to be.
    api.post("/api/seam/hover", body)
      .then((result) => { if (seq > hover.shown && state.placing) { hover.shown = seq; showHover(result); } })
      .catch((e) => {
        if (seq > hover.shown && state.placing) { hover.shown = seq; clearHoverPreview(); setPlaceHint(e.message); }
      })
      .then(() => {
        hover.inflight = false;
        pumpHover();
        // A click that arrived while this was on the wire asked to be held; now
        // the preview matches the pointer again, it can go through.
        if (hover.placeWhenReady && !hover.inflight && !hover.pending && state.placing) {
          hover.placeWhenReady = false;
          placeHovered();
        }
      });
  }

  // The chord under the pointer. The hovered point lies on the seam line, so
  // the piece that contains it is the one whose parameter range brackets it.
  // With the pointer inside a cut-out no piece does, and nothing is previewed --
  // which is the honest answer, because there is no deck there to join.
  function segmentUnder(result) {
    const p = result.point_flat;
    if (!p) return -1;
    let best = -1, bestGap = Infinity;
    (result.segments || []).forEach((s, i) => {
      const dx = s.x2 - s.x1, dy = s.y2 - s.y1;
      const length2 = dx * dx + dy * dy;
      if (length2 < 1e-12) return;
      const t = ((p[0] - s.x1) * dx + (p[1] - s.y1) * dy) / length2;
      const gap = (t < 0 ? -t : t > 1 ? t - 1 : 0) * Math.sqrt(length2);
      if (gap < bestGap) { bestGap = gap; best = i; }
    });
    return bestGap <= SEGMENT_GRACE_MM ? best : -1;
  }

  function showHover(result) {
    const index = segmentUnder(result);
    state.hoverSeam = index >= 0 ? result.segments[index] : null;
    state.hoverWorld = index >= 0 && result.world ? result.world[index] : null;
    drawFlatPreview();
    draw3dPreview(state.hoverWorld);
    if (result.reason) setPlaceHint(result.reason);
    else if (!state.hoverSeam) setPlaceHint(`${directionWords()} — that spot is not on a panel.`);
    else setPlaceHint(`${directionWords()} · ${Math.round(state.hoverSeam.length_mm)} mm on panel `
      + `${state.hoverSeam.panel_id} — click to place it.`);
  }

  function clearHoverPreview() {
    state.hoverSeam = null; state.hoverWorld = null;
    hover.placeWhenReady = false;
    drawFlatPreview(); draw3dPreview(null);
  }

  function drawFlatPreview() {
    while (flatPreview.firstChild) flatPreview.removeChild(flatPreview.firstChild);
    const seam = state.hoverSeam;
    if (!seam || !state.placing) return;
    const a = modelToFlat(seam.x1, seam.y1, seam.panel_id);
    const b = modelToFlat(seam.x2, seam.y2, seam.panel_id);
    const line = document.createElementNS(NS, "line");
    line.setAttribute("x1", a.x); line.setAttribute("y1", a.y);
    line.setAttribute("x2", b.x); line.setAttribute("y2", b.y);
    line.setAttribute("class", "seam-preview");
    line.setAttribute("stroke-width", 3);
    flatPreview.appendChild(line);
  }

  // A buffer big enough for any chord across this deck at the 20 mm step the
  // API lifts at. Only the used vertices and the draw range are touched per
  // move, so nothing is allocated while the pointer is going.
  const PREVIEW_POINTS = 2048;

  function initSeam3D() {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(PREVIEW_POINTS * 3), 3));
    geometry.setDrawRange(0, 0);
    // depthTest follows the x-ray switch, exactly like every overlay layer, and
    // for a reason that is easy to get wrong: WebGL does not update the depth
    // buffer while the depth test is OFF, so a line drawn with depthTest false
    // leaves the background's depth behind it and the mesh -- which is a
    // transparent material, so it renders after everything opaque -- then
    // paints straight over it. The seam was in the scene and simply invisible.
    previewLine = new THREE.Line(geometry,
      new THREE.LineBasicMaterial({ color: 0xff9f0a, depthTest: !state.xray }));
    previewLine.renderOrder = 5; previewLine.frustumCulled = false; previewLine.visible = false;
    scene.add(previewLine);
    seamLines = new THREE.LineSegments(new THREE.BufferGeometry(),
      new THREE.LineBasicMaterial({ color: 0xff375f, depthTest: !state.xray }));
    seamLines.renderOrder = 4; seamLines.frustumCulled = false;
    seamGroup.add(seamLines);
  }

  function draw3dPreview(polyline) {
    if (!previewLine) return;
    if (!polyline || polyline.length < 2) { previewLine.visible = false; return; }
    const attribute = previewLine.geometry.getAttribute("position");
    const count = Math.min(polyline.length, PREVIEW_POINTS);
    for (let i = 0; i < count; i++) attribute.setXYZ(i, polyline[i][0], polyline[i][1], polyline[i][2]);
    attribute.needsUpdate = true;
    previewLine.geometry.setDrawRange(0, count);
    previewLine.visible = true;
  }

  // Every committed seam drawn on the deck itself, rebuilt whenever the seam
  // set changes, so a seam shows up on the boat the moment it is placed --
  // without a refresh or a trip through another tab.
  function buildSeam3D() {
    if (!seamLines) return;
    const points = [];
    for (const entry of state.seamsWorld || []) {
      for (const piece of entry.world || []) {
        for (let i = 0; i + 1 < piece.length; i++) {
          points.push(piece[i][0], piece[i][1], piece[i][2], piece[i + 1][0], piece[i + 1][1], piece[i + 1][2]);
        }
      }
    }
    seamLines.geometry.dispose();
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(points), 3));
    seamLines.geometry = geometry;
    seamGroup.visible = state.showSeams3d;
  }

  // three.js reports the hit in world units and meshObject.scale is
  // mm_per_unit, so the point is ALREADY in the millimetre world frame
  // /api/seam/hover expects. Do not scale it again.
  function pickWorld(event) {
    if (!meshObject) return null;
    const rect = renderer.domElement.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    ndc.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    ndc.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(ndc, camera);
    const hit = raycaster.intersectObject(meshObject, false)[0];
    return hit ? [hit.point.x, hit.point.y, hit.point.z] : null;
  }

  // svgPoint undoes only the viewBox; flatToModel undoes the y flip, the bow-up
  // rotation and the panel's nest offset.
  function svgPoint(svg, event) {
    const pt = svg.createSVGPoint();
    pt.x = event.clientX; pt.y = event.clientY;
    const m = svg.getScreenCTM();
    if (!m) return null;
    return pt.matrixTransform(m.inverse());
  }

  async function placeHovered() {
    // A click can land while a newer hover is still on the wire. Committing the
    // previous answer would put the seam somewhere the preview never showed, so
    // it waits for the one in flight and places that instead.
    if (hover.inflight || hover.pending) { hover.placeWhenReady = true; return; }
    const seam = state.hoverSeam;
    if (!seam) { setPlaceHint("Nothing to place there — move onto the deck first."); return; }
    const dir = state.seamDir;
    state.seams.push({
      x1: seam.x1, y1: seam.y1, x2: seam.x2, y2: seam.y2,
      panel_id: seam.panel_id,
      mode: dir.mode, angle_deg: dir.mode === "angle" ? dir.angle : null, snap: true,
      // The chord the user actually clicked, kept exactly as the API sent it.
      // Every later correction is recomputed from `raw`, so rounding it here
      // would knock the seam off the direction it was placed in.
      raw: [seam.x1, seam.y1, seam.x2, seam.y2],
    });
    clearHoverPreview();
    setPlaceHint(`Placed. ${directionWords()} — click again for another, Escape to stop.`);
    await seamsChanged();
  }

  function wireSeamPlacing() {
    const svg = $("flat");
    svg.addEventListener("pointermove", (event) => {
      if (!state.placing) return;
      const p = svgPoint(svg, event);
      if (!p) return;
      const m = flatToModel(p.x, p.y);
      hoverAt([m.x, m.y], false);
    });
    svg.addEventListener("pointerleave", () => { if (state.placing) clearHoverPreview(); });
    svg.addEventListener("click", (event) => {
      if (!state.placing) return;
      event.preventDefault();
      placeHovered();
    });

    const canvas = $("gl");
    canvas.addEventListener("pointermove", (event) => {
      if (!state.placing) return;
      const p = pickWorld(event);
      if (!p) { clearHoverPreview(); setPlaceHint(`${directionWords()} — that is not on the boat.`); return; }
      hoverAt(p, true);
    });
    canvas.addEventListener("pointerleave", () => { if (state.placing) clearHoverPreview(); });
    canvas.addEventListener("click", (event) => {
      if (!state.placing) return;
      event.preventDefault();
      placeHovered();
    });
  }

  // ------------------------------------------------------------ sheet layout
  // The mark a request going out puts on itself, so its answer can be judged
  // when it lands.
  //
  //   rev    the revision the seam list was on when the request was sent
  //   told   whether the request CARRIED the seam list. A POST to
  //          /api/sheets/seams does: the server is being told what the seams
  //          are, so its answer is about exactly those. A GET of /api/sheets
  //          does not: it answers with whatever is in seams.json, which is the
  //          page's seams only if the page has already saved them.
  //   clean  whether every local edit had reached seams.json when it was sent.
  //
  // That last one is the whole of this fix, and it has to be taken when the
  // request is SENT rather than when it lands. Measured on the way back it
  // reads true again the moment the re-plan finishes, and the reply to a GET
  // fired a tenth of a second after a "remove" was READ off the disk before
  // that re-plan wrote it: four seams, answering a page that has three, landing
  // after the save that made it three, and adopted whole. That put the removed
  // seam back on screen with the shortened list already on disk, and the next
  // edit posted the resurrected four straight back over it. Reproduced here on
  // the real server: remove a seam, switch to the Sheet layout tab, and it
  // comes back -- which is what "it was stopping me from deleting bad seams"
  // looks like from the other side of the screen.
  function seamStamp(told) {
    return { rev: state.seamsRevision, told: !!told, clean: !state.seamsPending };
  }

  // May this reply replace the seam list outright?
  function stampIsCurrent(stamp) {
    if (!stamp) return true;                       // an unstamped caller means "take it"
    // Something has been edited since it was asked for: the newer list stands,
    // whatever the reply says.
    if (stamp.rev !== state.seamsRevision) return false;
    // A POST is an answer about the list it carried. A GET is only an answer
    // about the page's own seams if the page had nothing unsaved when it asked.
    return stamp.told || stamp.clean;
  }

  // Take the server's seam list, unless the page has moved on since it was
  // asked for.
  //
  // The hint after a click says "click again for another", and a fabricator does
  // exactly that -- two or three seams a second apart, while the first re-plan
  // is still on the wire. Every one of those clicks pushed a seam onto
  // state.seams, and this function used to replace the whole list with the
  // server's answer to the question asked BEFORE they were placed. The seams
  // vanished from the screen, and the queued re-post then wrote the shortened
  // list to seams.json, so they were gone from the job as well, with nothing in
  // the log to say so.
  //
  // The reply carries the stamp its request went out under -- see seamStamp --
  // and is only taken WHOLE when that stamp says it can be. Otherwise the local
  // list stands and only the CORRECTIONS the reply carries for seams it did
  // know about are taken, matched on seam id because a just-placed seam has not
  // been given one yet. The queued re-plan then asks again with the full list,
  // so nothing is lost and nothing is stale for long.
  // The fields the USER sets on a seam, as against the ones the corrector works
  // out from them. Taking a row is only "taking the correction" while these
  // still agree: a reply that does not answer the current list was computed
  // from the PREVIOUS value of them, so where they differ the local seam is the
  // newer one and the reply has nothing to say about it.
  //
  // Ticking "as placed" on a deck with eight seams was losing itself exactly
  // here. The re-plan for the previous edit was still on the wire, so the tick
  // queued behind it; that reply then came back with snap still true, was
  // merged in as a correction, and the queued re-plan posted the seam with the
  // tick undone. The seam went on being straightened, the box went on showing
  // "as placed", and the two never met.
  const SEAM_INTENT = ["snap", "mode", "angle_deg"];

  function adoptSeams(rows, answersCurrentSeams) {
    const fresh = rows.map((s) => ({
      seam_id: s.seam_id, x1: s.x1, y1: s.y1, x2: s.x2, y2: s.y2, panel_id: s.panel_id,
      snap: s.snap !== false, raw: s.raw || null, mode: s.mode || "",
      angle_deg: s.angle_deg === undefined ? null : s.angle_deg,
      length_mm: s.length_mm, direction_deg: s.direction_deg,
      snap_note: s.snap_note || "", snap_applied: !!s.snap_applied,
    }));
    if (answersCurrentSeams) { state.seams = fresh; return; }
    const byId = new Map();
    for (const row of fresh) if (row.seam_id) byId.set(row.seam_id, row);
    state.seams = state.seams.map((seam) => {
      const row = seam.seam_id && byId.get(seam.seam_id);
      if (!row) return seam;
      return SEAM_INTENT.every((key) => row[key] === seam[key]) ? row : seam;
    });
  }

  function applySheets(payload, askedAt) {
    state.sheets = payload;
    state.boat = (payload && payload.boat) || null;
    state.panelLayout = (payload && payload.panels) || [];
    state.seamsWorld = (payload && payload.seams_world) || [];
    if (payload && Array.isArray(payload.seams)) {
      // Everything that says where a seam came from is carried across, not just
      // its endpoints: `raw` is the user's own line and the next edit is
      // recomputed from it, so dropping it would let a seam creep every time
      // anything on this page changed.
      state.seamsLoaded = true;
      adoptSeams(payload.seams, stampIsCurrent(askedAt));
      renderSeamList();
    }
    updateFlatFrame();
    buildSeam3D();
    renderBoatNote();
    renderSeamTally();
    renderSheets();
    renderFlat();
    renderLayerPanel();
    setJobButtons(!!state.job, null);
  }

  // Where the user will see it: what this run knows about the boat direction,
  // and plainly when it knows nothing -- with no axis nothing can be squared to
  // the boat and the seam view cannot be turned bow up either.
  function renderBoatNote() {
    const el = $("boat-axis-note");
    const boat = state.boat;
    if (!state.runId || !boat) { el.hidden = true; return; }
    el.hidden = false;
    if (!boat.axis) {
      el.className = "info bad-note";
      el.textContent = "This run has no boat direction, so seams cannot be squared to the boat and this view "
        + "cannot be turned bow up. Run the outline again with a pattern, or type a grain angle "
        + "under Settings.";
      return;
    }
    const sure = boat.bow_sign && (typeof boat.bow_confidence !== "number" || boat.bow_confidence >= 0.5);
    const bits = [`The boat runs at ${boat.axis_deg}° in the flat layout, and every seam is squared to that.`];
    if (!boat.bow_sign) bits.push("Which end is the bow was not worked out, so the view is left as it is.");
    else if (!sure) bits.push("Which end is the bow is a guess — check the BOW mark before you trust it.");
    else bits.push("Bow found, so the seam view is drawn bow up.");
    for (const warning of boat.warnings || []) bits.push(warning);
    el.className = "info " + (sure ? "muted" : "warn-note");
    el.textContent = bits.join(" ");
  }

  // GET /api/sheets re-plans with the ENGINE's defaults, not with what the
  // Settings box says. With a grain angle typed in, drawing that answer put a
  // nesting for a DIFFERENT grain on screen -- for the second or two until the
  // re-plan landed -- while the box went on showing 45 and the DXFs would be cut
  // from the box. So the default answer is only ever DRAWN when the box really
  // is at its defaults; otherwise the fetch is skipped and the re-plan, which
  // carries the settings, is the only thing that paints.
  // The run's own seam list out of a reply whose nesting is not wanted. It is
  // the same adoption applySheets does, minus every picture -- so a page that
  // must wait for its re-plan still knows what seams the run has, and
  // replanSheets is allowed to send them.
  function adoptRunSeams(payload, askedAt) {
    if (!payload || !Array.isArray(payload.seams)) return;
    state.seamsLoaded = true;
    adoptSeams(payload.seams, stampIsCurrent(askedAt));
    renderSeamList();
  }

  async function refreshSheets(replan) {
    if (!state.runId) { renderSheets(); renderSeamTally(); return; }
    try {
      const own = !sheetSettingsAreDefault();
      // Stamped `told: false`: this asks the server what is in seams.json, and
      // tells it nothing. Its answer may only replace the seam list when the
      // page had nothing unsaved at the moment it asked -- see seamStamp.
      const askedAt = seamStamp(false);
      const payload = await api.get("/api/sheets");
      // With anything under Settings changed by hand, this answer is a nesting
      // for DIFFERENT settings: /api/sheets re-plans with the engine's defaults,
      // so a grain angle typed into the box gets a picture drawn at the detected
      // angle instead, for the second or two until the re-plan lands. The box
      // says 45, the picture says 3.7, and the DXFs come from the box. Only the
      // seam list is taken from it then; the re-plan below paints everything.
      if (own) adoptRunSeams(payload, askedAt);
      else applySheets(payload, askedAt);
      if (replan || own) await replanSheets();
    } catch (e) { log(e.message, true); }
  }

  // One replan at a time, always with the newest settings. Every option on this
  // page calls it on change and the nesting takes about a second, so without
  // the guard a few quick edits would race and whichever answer came back last
  // would win, whether or not it was the latest question.
  let replanBusy = false, replanAgain = false;

  async function replanSheets() {
    if (!state.runId) return;
    // A re-plan POSTS state.seams and the server writes back exactly what it is
    // sent, so an empty list is an erase. Between opening a run and its seams
    // arriving, state.seams is empty and not yet the truth -- posting it there
    // would wipe a hand-placed layout, and there is no undo for that.
    //
    // Removing the LAST seam is a different thing and has to work. It does:
    // seamsLoaded is set by the first reply that carries a seam list, and a
    // seam cannot be removed before one has been seen, so a genuine "delete the
    // last seam" never reaches this guard -- only a page that has never heard
    // back about this run at all does. And that also means nothing it did was
    // ever saved, so an empty page owes the file nothing. What it must NOT do
    // is return in silence: the pending mark would stay set for ever, and every
    // later reply would be treated as an answer that had not heard of an edit
    // that no longer exists, so the run's own seams could never load again.
    if (!state.seamsLoaded && !state.seams.length) {
      if (state.seamsPending) {
        state.seamsPending = 0;
        seamTrouble("That seam had not been saved to the run yet, so there was nothing to remove. "
                    + "Fetching the seams this run really has.");
        refreshSheets(false);
      }
      return;
    }
    if (replanBusy) { replanAgain = true; return; }
    replanBusy = true; setReplanBusy(true);
    try {
      do {
        replanAgain = false;
        // Stamped `told: true`: this request CARRIES the seam list, so its
        // answer is about exactly these seams whatever is on the disk.
        const askedAt = seamStamp(true);
        applySheets(await api.post("/api/sheets/seams",
                                   Object.assign({ seams: state.seams }, sheetSettings())), askedAt);
        // seams.json now holds everything the page had when this went out. An
        // edit made since has a higher revision and is still owed, so the mark
        // only clears for the edits this request actually carried.
        if (state.seamsPending && state.seamsPending <= askedAt.rev) state.seamsPending = 0;
        seamTrouble(null);
      } while (replanAgain);
    } catch (e) {
      // Nothing is rolled back. The seam list goes on showing exactly what the
      // user did, seamsPending goes on saying it is not saved, and this says so
      // in words with a way to send it again. Quietly throwing the edit away
      // because the server hiccuped is the worst outcome available here, and
      // keeping it while saying nothing is the second worst.
      log(e.message, true);
      seamTrouble("Your seams are still here, but the sheet layout could not be worked out just now, "
                  + `so this change is not saved to the run yet — ${e.message}`, true);
    }
    finally { replanBusy = false; setReplanBusy(false); }
  }

  // Everything on this page that a job or a re-plan is allowed to switch off,
  // and it is a short list on purpose.
  //
  // NOTHING that edits the seam set belongs here: not remove, not Clear all
  // seams, not the direction chooser, not Place seam. A layout that will not fit
  // is got out of by taking seams away, so the controls that take them away are
  // exactly the ones that must never be disabled because the layout will not
  // fit. "The sheets do not fit" is not a reason to switch anything off; being
  // busy with an engine job is, because a second job only comes back as a 409.
  // A control may also switch ITSELF off while its own request is on the wire,
  // and each of those puts itself back on every path, errors included.
  function setReplanBusy(busy) {
    const blocked = busy || !!state.job || !state.runId;
    const button = $("btn-replan");
    button.disabled = blocked;
    button.textContent = busy ? "Recalculating…" : "Recalculate";
    // The search reads seams.json off the disk and writes it back. A re-plan
    // still on the wire writes the same file, so starting the search under one
    // would let the old seams land on top of the answer with nothing on screen
    // to say so. Same guard, so the button simply waits for the second it takes.
    //
    // And it needs a boat direction to search in: the whole search is bands cut
    // along and across the boat. Without one the engine falls back to the panel
    // frame's +Y and says so -- "probably wrong, set the grain angle before
    // cutting" -- and a button that lays out a whole deck against a direction
    // the engine itself disowns is not a button to offer.
    const noAxis = !!state.boat && !state.boat.axis;
    $("btn-optimise").disabled = blocked || noAxis;
    $("btn-optimise").title = noAxis
      ? "This run has no boat direction, so there is no along or across the boat to lay seams out on. "
        + "Run the outline again with a pattern, or type a grain angle under Settings."
      : "";
    // Export writes the sheet DXFs by re-planning from seams.json, and
    // sheet_preview only writes that file once the nest it is doing now
    // finishes. Pressed mid-re-plan it would cut the PREVIOUS seam set, with the
    // new one already on screen.
    $("btn-sheets").disabled = blocked || !(state.sheets && state.sheets.available);
  }

  // The same named, measured list of pieces that will not fit as the seam list
  // carries, on the tab where the sheets themselves are. The fabricator looking
  // at a sheet with a piece missing off it needs to know WHICH piece and by how
  // much, on that tab, without going back to the other column to read it.
  function renderSheetNotes() {
    const el = $("sheets-notes");
    if (!el) return;
    const over = (state.sheets && state.sheets.available && state.sheets.oversize) || [];
    el.innerHTML = "";
    el.hidden = !over.length;
    if (!over.length) return;
    const head = document.createElement("div");
    head.textContent = `${plural(over.length, "piece")} will not fit a 40×80″ sheet, so `
      + `${over.length === 1 ? "it is" : "they are"} not on one:`;
    el.appendChild(head);
    for (const item of over) el.appendChild(oversizeLine(item));
  }

  function renderSheets() {
    const svg = $("sheets");
    const head = $("sheets-head");
    svg.innerHTML = "";
    renderSeamSource();
    renderSheetNotes();
    const data = state.sheets;
    if (!data || !data.available) {
      $("sheets-empty").hidden = false;
      head.textContent = (data && data.reason) || "Run auto-fit first — sheets are cut from the fitted outline.";
      return;
    }
    const preview = data.preview;
    const sheets = (preview && preview.sheets) || [];
    const over = (data.oversize || []).length;
    head.textContent =
      `${sheets.length} sheet${sheets.length === 1 ? "" : "s"} · ${data.piece_count} piece${data.piece_count === 1 ? "" : "s"} · `
      + `${preview.sheet_width_mm / 25.4}×${preview.sheet_length_mm / 25.4}″ · grain along the long axis`
      + (over ? ` · ${over} piece${over === 1 ? "" : "s"} still too big — add a seam` : "");
    $("sheets-empty").hidden = sheets.length > 0;
    if (!sheets.length) return;

    const W = preview.sheet_width_mm, H = preview.sheet_length_mm;
    const gap = W * 0.12;
    const total = sheets.length * W + (sheets.length - 1) * gap;
    const pad = W * 0.08;
    svg.setAttribute("viewBox", `${-pad} ${-pad - 60} ${total + 2 * pad} ${H + 2 * pad + 60}`);
    const stroke = total / 1200;
    const g = document.createElementNS(NS, "g");

    sheets.forEach((sheet, index) => {
      const ox = index * (W + gap);
      const rect = document.createElementNS(NS, "rect");
      rect.setAttribute("x", ox); rect.setAttribute("y", 0);
      rect.setAttribute("width", W); rect.setAttribute("height", H);
      rect.setAttribute("class", "sheet-outline");
      rect.setAttribute("stroke-width", 2 * stroke);
      g.appendChild(rect);

      const mx = (W - preview.usable_width_mm) / 2, my = (H - preview.usable_length_mm) / 2;
      const usable = document.createElementNS(NS, "rect");
      usable.setAttribute("x", ox + mx); usable.setAttribute("y", my);
      usable.setAttribute("width", preview.usable_width_mm);
      usable.setAttribute("height", preview.usable_length_mm);
      usable.setAttribute("class", "sheet-usable");
      usable.setAttribute("stroke-width", stroke);
      g.appendChild(usable);

      // A piece with a cut-out arrives as two rings carrying the same piece id,
      // and drawn identically they read as two parts nested one inside the
      // other -- nine blue shapes for eight pieces, inviting the fabricator to
      // go looking for a part that does not exist. `hole` is what tells them
      // apart, so a hole is drawn as a hole: the sheet's own colour, so the
      // material shows through it. Holes go on after every part, because a
      // later part drawn over one would fill it back in.
      for (const ring of sheet.rings) {
        if (ring.hole) continue;
        g.appendChild(sheetRing(ring, ox, H, stroke, false));
      }
      for (const ring of sheet.rings) {
        if (ring.hole) g.appendChild(sheetRing(ring, ox, H, stroke, true));
      }

      const label = document.createElementNS(NS, "text");
      label.setAttribute("x", ox); label.setAttribute("y", -18);
      label.setAttribute("class", "flat-label");
      label.setAttribute("font-size", 26 * stroke);
      label.textContent = `Sheet ${sheet.sheet} — ${(sheet.utilisation * 100).toFixed(0)}% used`;
      g.appendChild(label);
    });
    svg.appendChild(g);
  }

  // One ring of one nested piece. `hole` says whether this is the part or a
  // cut-out in it; the tooltip says the same thing in words, because the two
  // are not always told apart at a glance on a busy sheet.
  function sheetRing(ring, ox, H, stroke, hole) {
    const poly = document.createElementNS(NS, "polygon");
    // sheet y is up; SVG y is down, so mirror within the sheet height
    poly.setAttribute("points", ring.points.map((p) => `${ox + p[0]},${H - p[1]}`).join(" "));
    poly.setAttribute("class", hole ? "sheet-hole" : "sheet-piece");
    poly.setAttribute("stroke-width", (hole ? 1 : 2) * stroke);
    const title = document.createElementNS(NS, "title");
    title.textContent = hole
      ? `cut-out in ${ring.piece_id}`
      : `${ring.piece_id} (panel ${ring.panel_id}) rotated ${ring.rotation_deg}°`;
    poly.appendChild(title);
    return poly;
  }

  // ------------------------------------------------ work out the best seams
  async function startOptimise() {
    if (!state.runId) return;
    // The search has an undo of its own, and two undos side by side offering
    // two different seam sets is one too many.
    forgetCleared();
    if (state.seams.length && !window.confirm(
      `This replaces all ${state.seams.length} seam${state.seams.length === 1 ? "" : "s"} on this run with the `
      + "layout it works out. The ones you have now are copied to seams_previous.json first, and there is an "
      + "Undo button when it finishes.\n\nGo ahead?")) return;
    // Off now, not when the job id comes back: the confirm box and the POST
    // together are long enough for a second click, and the second one only
    // comes back as a 409 the user has to make sense of.
    $("btn-optimise").disabled = true;
    const el = $("optimise-result");
    el.hidden = false; el.className = "info muted"; el.innerHTML = "";
    el.textContent = "Working out the best seam positions…";
    await startJob("/api/seams/optimise", sheetSettings(), "Best seam layout");
    // startJob swallows a failed start, so nothing else would ever put the
    // button back -- and a button that stays grey after an error reads as the
    // search still running.
    if (!state.job) setJobButtons(false, null);
  }

  function renderOptimiseResult(result) {
    const el = $("optimise-result");
    el.hidden = false; el.className = "info"; el.innerHTML = "";
    const before = result.before || {}, after = result.after || {};
    const line = document.createElement("div");
    if (!result.improved) {
      line.textContent = result.reason || "Nothing better was found, so your seams were left alone.";
    } else {
      const wasOver = (before.oversize || []).length;
      // Built as nodes, not as a string of HTML: every number in here came off
      // the wire, and this is the one line on the page that was interpolating
      // server text into innerHTML.
      const head = document.createElement("b");
      head.textContent = `${plural(after.seam_count, "seam")} · ${plural(after.sheet_count, "sheet")} · `
        + `${Math.round(after.waste_percent)}% waste`;
      line.appendChild(head);
      // The two waste figures are shares of DIFFERENT amounts of material, and
      // side by side without their sheet counts they read as a straight saving:
      // 53% down to 39% looks like a win even when it took two more sheets to
      // get there. The fabricator buys sheets, not percentages, so the "was"
      // clause carries its own sheet count and the sheets are said first.
      const was = document.createElement("span");
      was.textContent = ` (was ${plural(before.sheet_count, "sheet")} at ${Math.round(before.waste_percent)}% waste`
        + `${wasOver ? `, ${wasOver} piece${wasOver === 1 ? "" : "s"} too big` : ""}).`;
      line.appendChild(was);
    }
    el.appendChild(line);
    // Fewer oversize pieces outranks every other objective, so the answer can
    // legitimately cost MORE sheets than the layout it replaced. Say so, in
    // sheets, rather than leaving the fabricator to work it out from two
    // percentages -- and say why it is still the better job.
    if (result.improved && after.sheet_count > before.sheet_count) {
      const extra = after.sheet_count - before.sheet_count;
      const cost = document.createElement("div");
      cost.className = "warn-note";
      cost.textContent = `That is ${plural(extra, "sheet")} more material than before`
        + ((before.oversize || []).length
          ? " — but the old layout had pieces that would not fit a sheet at all, so it could not be cut."
          : ".");
      el.appendChild(cost);
    }

    // Named, measured, and with what would fix each one -- the same lines the
    // seam list and the sheet tab carry, because "P2 (needs a seam across the
    // boat)" run together in a sentence was a list to decode rather than a list
    // to work through.
    const still = after.oversize_detail || [];
    if (still.length) {
      const bad = document.createElement("div");
      bad.className = "bad-note";
      const head = document.createElement("div");
      head.textContent = "Still too big for a sheet:";
      bad.appendChild(head);
      for (const item of still) bad.appendChild(oversizeLine(item));
      el.appendChild(bad);
    }

    const note = document.createElement("div");
    note.className = "muted";
    note.textContent = `${plural(result.candidates_evaluated, "arrangement")} tried in ${Math.round(result.elapsed_s)}s`
      + (result.budget_exhausted ? ", stopped at the time limit" : "")
      + ". This is the best it found, not a proof that nothing better exists — move or remove any seam you like.";
    el.appendChild(note);

    // Only when there is something to put back. The backup file is written even
    // when the run had no seams at all, and an "undo -- put the 0 seams back"
    // button is both nonsense and a lie: the page cannot post an empty seam set.
    if (result.backup && result.seams_replaced > 0) {
      const undo = document.createElement("button");
      undo.className = "linkbtn";
      undo.textContent = `undo — put your ${plural(result.seams_replaced, "seam")} back`;
      undo.addEventListener("click", () => undoOptimise(undo));
      el.appendChild(undo);
    }
  }

  // The optimiser copies the seams it replaces to seams_previous.json before it
  // writes anything, so undo is simply that file posted back as the seam set.
  async function undoOptimise(button) {
    button.disabled = true; button.textContent = "putting them back…";
    try {
      const saved = await api.get(`/api/file/${encodeURIComponent(state.runId)}/seams_previous.json?inline=1`);
      state.seams = (saved.seams || []).map((s) => Object.assign({}, s));
      await seamsChanged("undone");
      const el = $("optimise-result");
      el.className = "info muted";
      el.textContent = `Put your ${plural(state.seams.length, "seam")} back.`;
    } catch (e) {
      button.disabled = false; button.textContent = "undo failed — try again";
      log(e.message, true);
    }
  }

  // ------------------------------------------------------------ data refresh
  async function refreshState() {
    const s = await api.get("/api/state");
    const runChanged = state.runId !== s.run_id;
    state.scan = s.scan; state.runId = s.run_id;
    renderScanInfo(); renderFiles(s.run_files);
    updateSteps(s);
    if (runChanged) {
      setPlacing(false);
      // A seam set belongs to ONE run. Left in place, the previous run's seams
      // would be on screen for the second it takes the new run's to arrive --
      // and a re-plan in that second would write them into the wrong run.
      // The revision bump is part of the same guard: a reply about the PREVIOUS
      // run's seams may still be on the wire, and it must not be adopted into
      // this one.
      // The pending mark, the provenance and the cleared set belong to the run
      // that has just been left: an unsaved edit to the OLD run is not an
      // unsaved edit to this one, and "4 seams you placed" said over another
      // boat's seams is the exact confusion this line exists to stop.
      state.seams = []; state.seamsLoaded = false; state.sheets = null; state.seamsRevision++;
      state.seamsPending = 0; state.seamsOrigin = "run";
      state.boat = null; state.panelLayout = []; state.seamsWorld = [];
      seamTrouble(null); forgetCleared();
      updateFlatFrame(); buildSeam3D(); renderSeamList(); renderSeamTally();
      renderSheets(); renderBoatNote();
      // Straight to a re-plan when anything under Settings has been changed by
      // hand: /api/sheets answers with the ENGINE defaults, and showing a
      // nesting the settings box disagrees with is worse than a moment's wait.
      if (s.run_id) refreshSheets(!sheetSettingsAreDefault());
      // A run is opened by clicking it at the very bottom of this column, which
      // is where the scroll then is -- with the seam list, the "does it all fit
      // yet" line and both seam buttons above the fold, at the exact moment the
      // work moves to them. Put the seam step back on screen.
      if (s.run_id) {
        const seams = $("btn-seam").closest("section");
        if (seams && seams.scrollIntoView) seams.scrollIntoView({ block: "start" });
      }
    }
    setJobButtons(!!s.active_job, s);
    if (s.active_job && (!state.job || state.job !== s.active_job.job_id)) trackJob(s.active_job.job_id);
    return s;
  }

  // Steps 1 to 3 (and the run list) fold themselves away once they have done
  // their job, so that section 4 -- the only part of this column touched more
  // than once per boat -- is on screen when seams are being placed. Without it
  // the column stood four screens deep and opening a run left the user at the
  // bottom of it, with the seam list and the "does it all fit yet" line both
  // above the fold.
  //
  // Only a CHANGE of state moves a disclosure. /api/state is polled every four
  // seconds, so setting `open` on every reply would slam a step the user had
  // just opened by hand shut again, four seconds later, every time.
  const stepSeen = { scan: undefined, run: undefined, fitted: undefined };

  function updateSteps(s) {
    const files = s.run_files || {};
    const fitted = !!(files["final_auto.dxf"] || files["final.dxf"]);
    const scanPath = s.scan ? s.scan.path : null;

    stepDone("scan", !!s.scan, s.scan ? s.scan.name : "");
    stepDone("outline", !!s.run_id, s.run_id ? "outline done" : "");
    stepDone("autofit", fitted, fitted ? "final_auto.dxf ready" : "");
    stepDone("runs", !!s.run_id, "");

    // The order of these three matters, and it is the reason they are written
    // out rather than folded into one rule. Opening a previous run changes the
    // scan AND the run in the same reply: the scan rule would open step 2
    // ("you have a scan, run the outline") and the run rule immediately shuts
    // it again, which is right -- that run already has its outline. Picking a
    // NEW scan changes only the scan, so step 2 stays open, which is also
    // right, because running the outline is exactly what happens next.
    if (scanPath !== stepSeen.scan) {
      stepSeen.scan = scanPath;
      stepOpen("scan", !s.scan);
      stepOpen("outline", !!s.scan);
    }
    if (s.run_id !== stepSeen.run) {
      stepSeen.run = s.run_id;
      stepOpen("outline", !!s.scan && !s.run_id);
      stepOpen("autofit", !!s.run_id && !fitted);
      stepOpen("runs", !s.run_id);
    }
    if (fitted !== stepSeen.fitted) {
      stepSeen.fitted = fitted;
      stepOpen("autofit", !!s.run_id && !fitted);
    }
  }

  function stepDone(name, done, note) {
    const el = $("step-state-" + name);
    if (!el) return;
    el.textContent = done ? (note ? "✓ " + note : "✓") : "";
    el.className = "step-state" + (done ? " done" : "");
  }

  function stepOpen(name, open) {
    const box = $("step-" + name);
    if (box) box.open = !!open;
  }

  // Every button that starts or waits on an engine job, in one place. The
  // engine runs one job at a time, so a second one started from a stale page
  // would only come back as a 409 the user has to make sense of.
  function setJobButtons(busy, s) {
    if (s) {
      $("btn-outline").disabled = !(s.scan && s.scan.preview_ready) || busy;
      $("btn-autofit").disabled = !s.run_id || busy;
      $("btn-ingest").disabled = !s.run_id || busy;
    }
    $("btn-sheets").disabled = busy || !(state.sheets && state.sheets.available);
    // Last, because it owns both Recalculate and Find the best seam layout and
    // its own rule -- no search while a re-plan is in flight -- is the stricter
    // of the two.
    setReplanBusy(replanBusy);
  }

  function renderScanInfo() {
    const el = $("scan-info"); const scan = state.scan;
    if (!scan) { el.textContent = "No scan loaded"; el.className = "info muted"; return; }
    el.className = "info";
    const mb = (scan.size_bytes / 1e6).toFixed(0);
    const units = scan.units_detected ? `units in file: ${scan.units_detected}` : "no units in file (choose below)";
    const pv = scan.preview ? ` · ${scan.preview.face_count.toLocaleString()} faces` : "";
    // Built as nodes rather than as a string of HTML. A file name is not the
    // page's own text -- it is whatever was on the disk -- and this and the run
    // list below were the two places it was being pasted into innerHTML.
    el.textContent = "";
    el.append(bold(scan.name), ` · ${mb} MB${pv}`, document.createElement("br"),
              units, document.createElement("br"), muted(scan.path));
  }

  function bold(text) { const b = document.createElement("b"); b.textContent = text; return b; }

  function muted(text) {
    const s = document.createElement("span"); s.className = "muted"; s.textContent = text; return s;
  }

  function renderFiles(files) {
    const el = $("files");
    if (!state.runId) { el.textContent = "Open or make a run first"; el.className = "info muted"; return; }
    el.className = "info files";
    const order = ["final_auto.dxf", "final.dxf", "outline.3dm", "auto_cam.3dm", "outline.dxf", "autofit_report.md", "outline_report.md", "final_report.md", "calibration_report.md"];
    const labels = { "final_auto.dxf": "final_auto.dxf (auto-fit, for VCarve)", "final.dxf": "final.dxf (from your drawing)", "outline.3dm": "outline.3dm (draw on this in Rhino)",
      "auto_cam.3dm": "auto_cam.3dm (auto-fit, review in Rhino)", "outline.dxf": "outline.dxf", "autofit_report.md": "auto-fit report",
      "outline_report.md": "outline report", "final_report.md": "ingest report", "calibration_report.md": "calibration report" };
    el.textContent = "";
    const head = document.createElement("div"); head.className = "muted";
    head.textContent = `run ${state.runId}`; el.appendChild(head);
    const sheetFiles = (state.sheets && state.sheets.files) || [];
    for (const entry of sheetFiles) {
      const a = document.createElement("a");
      a.href = `/api/file/${encodeURIComponent(state.runId)}/${entry.name}`;
      a.textContent = `⬇ ${entry.name} — sheet ${entry.sheet} (${entry.pieces.length} piece${entry.pieces.length === 1 ? "" : "s"}, ${(entry.utilisation * 100).toFixed(0)}% used)`;
      el.appendChild(a);
    }
    if (sheetFiles.length) {
      const report = document.createElement("a");
      report.href = `/api/file/${encodeURIComponent(state.runId)}/sheet_report.md?inline=1`;
      report.target = "_blank"; report.textContent = "⬇ sheet layout report";
      el.appendChild(report);
    }
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
    const r = await api.get("/api/runs"); const host = $("runs"); host.textContent = "";
    if (!r.runs.length) { host.appendChild(muted("none yet")); return; }
    for (const run of r.runs) {
      const div = document.createElement("div"); div.className = "run" + (run.run_id === state.runId ? " current" : "");
      const bits = [`${run.panels} panels`, run.pattern || (run.teak ? "teak" : null), run.files["final_auto.dxf"] ? "auto-fit DXF" : (run.files["auto_cam.3dm"] ? "auto-fit" : null),
        run.files["final.dxf"] ? "drawn DXF" : null, run.input_exists ? null : "scan file missing"].filter(Boolean).join(" · ");
      const name = document.createElement("div"); name.className = "name"; name.textContent = run.run_id;
      const meta = document.createElement("div"); meta.className = "meta"; meta.textContent = `${run.modified} · ${bits}`;
      div.append(name, meta);
      if (!run.input_exists) div.title = "The scan this run was made from is not at its recorded path; the run cannot be reopened.";
      div.addEventListener("click", () => run.input_exists && startJob("/api/run/open", { run_id: run.run_id }, `Opening ${run.run_id}`));
      host.appendChild(div);
    }
  }

  async function refreshOverlays() {
    // Nothing to fetch before a run is open, and asking anyway answered 404 and
    // left a red line in the browser's console on every fresh page load. The
    // console is one of the few places a fault shows up at all here, so it has
    // to be quiet when nothing is wrong.
    if (!state.runId) {
      state.overlays = null;
      buildHitRings(); renderLayerPanel(); renderFlat(); renderPanelQuality();
      return;
    }
    try {
      const ov = await api.get("/api/overlays");
      state.overlays = ov;
      // "#only=final_auto_file" (comma-separated ids): a bookmarkable verification view
      const only = new URLSearchParams((location.hash || "").replace(/^#/, "")).get("only");
      if (only) { const keep = only.split(","); for (const l of state.overlays.layers) l.on = keep.includes(l.id); }
      buildHitRings();
      buildLayerObjects(); renderLayerPanel(); renderFlat(); rescaleMesh(); renderPanelQuality();
    } catch (e) { state.overlays = null; buildHitRings(); renderLayerPanel(); renderFlat(); renderPanelQuality(); }
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
      // The same three numbers as before, said in shop words: "area change" and
      // "edge strain p95" are what the engine calls them, not what the person
      // reading this calls them. How much the flat part differs in size from
      // the deck it came off is the whole question, so that is what it says.
      const how = f.strategy === "rigid-planar" ? "flat panel — measured in its own plane"
        : (f.strategy ? "curved panel — unrolled flat" : "");
      const bits = [];
      if (typeof f.tilt_deg === "number") bits.push(`tilt ${f.tilt_deg.toFixed(1)}°`);
      if (typeof f.area_change_percent === "number") bits.push(`size change ${f.area_change_percent >= 0 ? "+" : ""}${f.area_change_percent.toFixed(2)}%`);
      if (typeof f.edge_strain_p95_percent === "number") bits.push(`worst stretch ${f.edge_strain_p95_percent.toFixed(2)}%`);
      const warn = f.status && f.status !== "GOOD" ? ` · <b>${f.status} — check this one on the boat</b>` : "";
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
    setPlacing(false);
    setJobButtons(true, null);
    const poll = async () => {
      try {
        const j = await api.get(`/api/jobs/${jobId}?since=${state.jobSince}`);
        for (const line of j.log) log(line, line.includes("ERROR"));
        state.jobSince = j.log_length;
        if (j.status === "done" || j.status === "error") {
          state.job = null;
          setStatus(j.status === "done" ? `${j.kind} finished in ${j.elapsed_s}s` : `${j.kind} failed`, j.status === "done" ? "" : "error");
          // The search's headline must NOT go up before the rest of the page
          // catches up with it. Redrawing the three views takes a few seconds,
          // and announcing "6 seams, 4 sheets, 39% waste" beside a seam list
          // still showing 7 and a tally still reading "2 pieces will not fit"
          // is a screen the fabricator has every reason to disbelieve -- five
          // numbers, one of them right. So the panel says what it is doing,
          // the views are pulled, and only then is the result stated.
          const optimised = j.kind === "optimise_seams";
          if (optimised) {
            const el = $("optimise-result");
            el.hidden = false; el.className = "info muted"; el.innerHTML = "";
            el.textContent = j.result ? "Search finished — redrawing the seams and the sheets…"
                                      : (j.error || "The search did not finish, so your seams were left alone.");
            if (!j.result) el.className = "info bad-note";
          }
          // The seams on the run have just been REPLACED by the search, so the
          // set the page is about to pull is the machine's and must say so --
          // in the seam list and on the sheet tab both. When the search did not
          // improve on what was there it wrote nothing, and the seams are still
          // whoever's they were, so the line is left alone.
          if (optimised && j.result && j.result.improved) {
            state.seamsOrigin = "auto";
            forgetCleared();
          }
          await refreshAll();
          // These two change what is on the sheets, so they pull the layout
          // again and re-plan it with the settings the page is showing.
          if (state.runId && (optimised || j.kind === "sheets")) await refreshSheets(true);
          if (optimised && j.result) renderOptimiseResult(j.result);
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
    $("btn-pick-folder").addEventListener("click", async () => {
      try {
        setStatus("Choose a scan folder…", "busy");
        const r = await api.post("/api/scan/pick", { folder: true });
        if (r.cancelled) { setStatus("Ready"); return; }
        log("— Scan folder: " + r.scan.name); trackJob(r.job_id);
      } catch (e) { setStatus(e.message, "error"); log(e.message, true); }
    });

    for (const set of DIRECTION_CONTROLS) {
      $(set.pick).addEventListener("change", (e) => setSeamDirection(e.target.value));
      $(set.angle).addEventListener("change", (e) => setSeamDirection(null, parseFloat(e.target.value)));
    }
    for (const id of PLACE_BUTTONS) $(id).addEventListener("click", () => setPlacing(!state.placing));
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") setPlacing(false); });
    wireSeamPlacing();

    for (const id of ["opt-spacing", "opt-seamgap", "opt-grain", "opt-axis-snap", "opt-snap-angle",
                      "opt-rot180", "opt-axis-priority", "opt-snap-enabled"]) {
      $(id).addEventListener("change", replanSheets);
    }
    $("btn-replan").addEventListener("click", replanSheets);
    $("btn-clear-seams").addEventListener("click", clearAllSeams);
    $("btn-optimise").addEventListener("click", startOptimise);
    $("btn-sheets").addEventListener("click", () => startJob("/api/sheets/export", sheetSettings(), "Sheet DXFs"));
    $("btn-fit").addEventListener("click", fitView);
    $("opt-xray").addEventListener("change", (e) => {
      state.xray = e.target.checked;
      for (const id in state.layers) { state.layers[id].obj.material.depthTest = !state.xray; state.layers[id].obj.material.needsUpdate = true; }
      // The seams are drawn on the deck like any other line, so they see
      // through the mesh with everything else.
      for (const line of [seamLines, previewLine]) {
        if (!line) continue;
        line.material.depthTest = !state.xray; line.material.needsUpdate = true;
      }
    });
    $("opt-opacity").addEventListener("input", (e) => { meshMaterial.opacity = parseFloat(e.target.value); });
    $("opt-shading").addEventListener("change", (e) => { state.shadingChosen = true; applyShading(e.target.value); });
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
    setSeamDirection("along");
    setPlacing(false);
  }

  function showTab(tab) {
    state.tab = tab;
    document.querySelectorAll("#tabs button").forEach((x) => x.classList.toggle("active", x.dataset.tab === tab));
    $("view3d").hidden = tab !== "3d";
    $("viewflat").hidden = tab !== "flat";
    $("viewsheets").hidden = tab !== "sheets";
    if (tab === "3d") resize();
    // There is nothing to hover on the sheet layout, and leaving the mode armed
    // there would leave orbit switched off behind the user's back.
    // Same reason as on a run change: a plain fetch here re-plans with the
    // engine defaults, so with a grain angle typed in it drew a nesting for a
    // different grain -- while the box still said 45 and the DXFs would have
    // been cut to 45. Re-plan with what the page says instead.
    if (tab === "sheets") { setPlacing(false); refreshSheets(!sheetSettingsAreDefault()); }
    if (controls) controls.enabled = !state.placing;
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

  // A small, deliberate surface for tools/ui_smoke.py. The flat view's
  // screen <-> model mapping has to be checked with numbers rather than by eye:
  // getting it backwards would put every seam somewhere other than where it was
  // clicked, and that is not something a screenshot shows.
  window.AutoDeckUI = {
    state, flat, modelToFlat, flatToModel, panelForPlacedPoint,
    setSeamDirection, setPlacing, hoverAt, placeHovered, replanSheets,
    // Deleting a seam has to work under every condition the sheet plan can be
    // in, and the interesting conditions -- a reply already on the wire, a
    // re-plan that fails -- cannot be reached by clicking at human speed.
    seamsChanged, clearAllSeams, applySheets, seamStamp, seamSourceWords,
    renderSeamTally, renderSheetNotes,
    // So the wording of the auto-layout report can be checked without sitting
    // through a ninety second search: what it must never say is more important
    // than any of it, and an assertion that only runs on the slow path is an
    // assertion that stops running.
    renderOptimiseResult,
    // Same reason: whether a cut-out is drawn as a cut-out depends on the run
    // having a piece with a hole in it that the nester happened to place, which
    // no fixed fixture can promise.
    renderSheets,
    // Which of steps 1 to 3 is open is decided from the run's state, and the
    // interesting case -- a page opened before anything has been loaded at all
    // -- cannot be reached once the server has a run open, because the server
    // remembers it across page loads.
    updateSteps,
    // The 3D objects, so the smoke test can ask whether the seams are really in
    // the scene rather than trusting a screenshot of a very small boat.
    three: () => ({ seamGroup, seamLines, previewLine, meshObject, camera }),
  };

  init3D(); wire();
  refreshAll().catch((e) => log(e.message, true));
  setInterval(() => { if (!state.job) refreshState().catch(() => {}); }, 4000);
})();
