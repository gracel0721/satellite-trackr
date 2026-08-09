// Satellite Collision Risk Tracker — CesiumJS frontend.
// Builds one moving entity per satellite from precomputed ECEF samples, and
// overlays close-approach pairs (red connecting line) under the threshold.

const MAX_ALERT_LINES = 400; // cap rendered event lines for performance

// Per-constellation marker colors. Validated against the dark Cesium globe
// surface (adjacent-pair ΔE ≥ 8.4, all slots ≥ 3:1 contrast, normal-vision
// worst adjacent ΔE 19.3). The legend below the threshold slider is the
// secondary encoding that makes any pair in the 6–8 CVD floor band
// distinguishable for colorblind readers.
const CONSTELLATION_COLORS = {
  'starlink':            '#3987e5',
  'oneweb':              '#d95926',
  'iridium-NEXT':        '#199e70',
  'gnss':                '#c98500',
  'fengyun-1c-debris':   '#d55181',
  'iridium-33-debris':   '#008300',
  'cosmos-2251-debris':  '#9085e9',
};
// Fallback for any constellation name not in the map (e.g. legacy payloads
// without the `constellation` field, or new groups the user added). Resolved
// lazily inside colorFor() (not at module top level) because app.js loads
// before the deferred Cesium CDN script, so `Cesium` is undefined here at
// parse time — it is only available once init() runs on DOMContentLoaded.
const COLOR_PALETTE_ORDER = ['#3987e5','#d95926','#199e70','#c98500','#d55181','#008300','#9085e9','#e66767','#4cb3c3','#a37bff'];
function colorFor(name) {
  if (!name) return Cesium.Color.SKYBLUE;
  if (CONSTELLATION_COLORS[name]) return Cesium.Color.fromCssColorString(CONSTELLATION_COLORS[name]);
  // Deterministic fallback for user-added groups: hash to a slot in the palette.
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) | 0;
  return Cesium.Color.fromCssColorString(COLOR_PALETTE_ORDER[Math.abs(h) % COLOR_PALETTE_ORDER.length]);
}

// Basemap options for the globe's imagery layer. Each entry is a lazy factory
// (Cesium is not defined at module top level, so providers are constructed on
// demand inside setBasemap after the CDN script has loaded). Swapping a
// basemap only re-loads map tiles — the globe, satellite entities, clock, and
// alerts are untouched. All three are free tile services; no Ion token needed.
//   dark      — CartoDB Dark Matter (near-black; colored dots pop).
//   satellite — Esri World Imagery, dimmed to 55% so dots stay visible.
//   street    — OpenStreetMap (the original basemap).
const BASEMAPS = {
  dark: {
    provider: () => new Cesium.UrlTemplateImageryProvider({
      url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png',
      subdomains: ['a', 'b', 'c', 'd'],
      credit: 'CARTO, OpenStreetMap contributors',
    }),
    brightness: 1.0,
  },
  satellite: {
    provider: () => new Cesium.UrlTemplateImageryProvider({
      url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      credit: 'Esri, Maxar, Earthstar Geographics',
    }),
    brightness: 0.55, // dimmed so colored constellation dots stay visible
  },
  street: {
    provider: () => new Cesium.OpenStreetMapImageryProvider({ url: 'https://tile.openstreetmap.org/' }),
    brightness: 1.0,
  },
};
let baseImageryLayer = null;
// Swap the active basemap. Removing the old imagery layer and adding the new
// one re-loads only map tiles; satellite entities stay put on top.
function setBasemap(key) {
  const b = BASEMAPS[key];
  if (!b || !viewer) return;
  if (baseImageryLayer) viewer.imageryLayers.remove(baseImageryLayer);
  baseImageryLayer = viewer.imageryLayers.addImageryProvider(b.provider());
  baseImageryLayer.brightness = b.brightness;
  const sel = document.getElementById('basemap');
  if (sel) sel.value = key;
}

let viewer, satEntities = {}, eventsData = [], orbitsData = null, config = {};
let stepSeconds = 60; // derived from output time grid after data loads
// In production the ECEF samples arrive as a raw int32 ArrayBuffer (no JSON
// parse); this is the typed-array view over it. Null in local dev, where the
// samples live inside each orbit's `ecef_m` JSON array.
let ecefCoords = null;
// Per-constellation show/hide state. satConstellation maps sat_id -> name so
// the toggle handler can find every entity belonging to a constellation;
// visibleConstellations is the set currently toggled on (all on by default).
let satConstellation = {};
let visibleConstellations = new Set();
// Total unique constellations in the loaded payload (for the "X of Y shown"
// meta line). Set once in renderLegend.
let totalConstellations = 0;

function fromIso(s) { return Cesium.JulianDate.fromIso8601(s); }

// Fetch /api/config. `config.data_url` points at the positions object the
// Cloud Function publishes to a public GCS bucket; the frontend fetches it
// straight from there. Empty in local dev, where we fall back to the FastAPI
// endpoints that read data/orbits + data/events from disk.
async function fetchConfig() {
  config = await fetch('/api/config').then(r => r.json());
}

// In production the Cloud Function publishes a binary container (magic "STB1")
// at `positions.bin` in the same bucket as the legacy `positions.json` URL. The
// header is a small JSON object; the large ECEF sample array is a raw
// little-endian int32 blob the browser views directly (no JSON.parse), which
// is the point — it removes the dominant parse cost and shrinks the wire size.
function parseBinaryContainer(buf) {
  const dv = new DataView(buf);
  const hdrLen = dv.getUint32(4, true); // little-endian, after the 4-byte magic
  const hdrJson = new TextDecoder().decode(new Uint8Array(buf, 8, hdrLen));
  const data = JSON.parse(hdrJson);
  // The int32 ECEF blob starts after the header, 4-byte aligned.
  const arrOffset = Math.ceil((8 + hdrLen) / 4) * 4;
  ecefCoords = new Int32Array(buf, arrOffset, (buf.byteLength - arrOffset) / 4);
  return data;
}

// Load the data payload. Production prefers the binary positions.bin and falls
// back to the legacy JSON positions.json if it is missing (e.g. mid-deploy, or
// before the Cloud Function has switched over). Local dev reads the JSON
// endpoints directly.
async function loadData() {
  if (config.data_url) {
    const binUrl = config.data_url.replace(/positions\.json$/, 'positions.bin');
    try {
      const r = await fetch(binUrl);
      if (!r.ok) throw new Error('positions.bin not available');
      const data = parseBinaryContainer(await r.arrayBuffer());
      orbitsData = data;
      eventsData = data;
      return;
    } catch (e) {
      console.warn('Falling back to JSON positions.json:', e);
      ecefCoords = null;
    }
    const positions = await fetch(config.data_url).then(r => r.json());
    orbitsData = positions;
    eventsData = positions;
  } else {
    [orbitsData, eventsData] = await Promise.all([
      fetch('/api/orbits').then(r => r.json()),
      fetch('/api/events').then(r => r.json()),
    ]);
  }
}

function buildSatellites() {
  const orbits = orbitsData.orbits || [];
  const t = orbitsData.t || [];
  const start = fromIso(t[0]), stop = fromIso(t[t.length - 1]);
  const n = t.length;
  const stride = n * 3; // int32 values per satellite row in the binary blob

  orbits.forEach((o, i) => {
    const pos = new Cesium.SampledPositionProperty(Cesium.ReferenceFrame.FIXED);
    // Smooth interpolation between the (coarse, 20-min) waypoints so LEO
    // markers glide along the orbit arc instead of cutting straight chords
    // between sparse samples.
    pos.setInterpolationOptions({
      interpolationAlgorithm: Cesium.LagrangePolynomialApproximation,
      interpolationDegree: 5,
    });
    if (ecefCoords) {
      // Production binary path: read the flat int32 blob (sat-major, step-major).
      const base = i * stride;
      const m = Math.min(o.n_steps || n, n); // a few sats drop timesteps
      for (let j = 0; j < m; j++) {
        pos.addSample(fromIso(t[j]),
          new Cesium.Cartesian3(ecefCoords[base + j*3], ecefCoords[base + j*3 + 1], ecefCoords[base + j*3 + 2]));
      }
    } else {
      // Local dev JSON path: samples live in each orbit's ecef_m array.
      const e = o.ecef_m;
      const m = Math.floor(e.length / 3); // timesteps for this sat (== n normally)
      for (let j = 0; j < m; j++) {
        pos.addSample(fromIso(t[j]), new Cesium.Cartesian3(e[j*3], e[j*3 + 1], e[j*3 + 2]));
      }
    }
    satEntities[o.sat_id] = viewer.entities.add({
      id: 'sat-' + o.sat_id,
      name: o.name,
      position: pos,
      point: {
        pixelSize: 4,
        color: colorFor(o.constellation),
        outlineColor: Cesium.Color.WHITE,
        outlineWidth: 1,
      },
    });
    // Record the constellation so the toggle handler can flip this entity's
    // visibility without rebuilding the whole entity set.
    satConstellation[o.sat_id] = o.constellation || 'unknown';
  });

  viewer.clock.startTime = start.clone();
  viewer.clock.stopTime = stop.clone();
  viewer.clock.currentTime = start.clone();
  viewer.clock.clockRange = Cesium.ClockRange.LOOP_STOP;
  viewer.clock.multiplier = 600; // 600x playback
  viewer.timeline.zoomTo(start, stop);
  viewer.clock.shouldAnimate = true;
}

// Render the per-constellation toggle list: one row per constellation with a
// checkbox (color-tinted), color swatch, name, and count. All start checked
// (visible). Called once after buildSatellites.
function renderLegend() {
  const el = document.getElementById('legend');
  if (!el || !orbitsData) return;
  // Count satellites per constellation, preserving the order they first
  // appear in the orbits list (stable, deterministic).
  const counts = new Map();
  for (const o of (orbitsData.orbits || [])) {
    const k = o.constellation || 'unknown';
    counts.set(k, (counts.get(k) || 0) + 1);
  }
  totalConstellations = counts.size;
  visibleConstellations = new Set(counts.keys()); // all on by default
  el.innerHTML = '';
  for (const [name, n] of counts) {
    const c = colorFor(name);
    const rgba = `rgba(${Math.round(c.red*255)},${Math.round(c.green*255)},${Math.round(c.blue*255)},${c.alpha})`;
    const hex = `#${[c.red,c.green,c.blue].map(v => Math.round(v*255).toString(16).padStart(2,'0')).join('')}`;

    // <label> wraps the checkbox so clicking anywhere on the row toggles it.
    const row = document.createElement('label');
    row.className = 'legend-item';

    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = true;
    cb.style.accentColor = hex; // tint the checkbox with the constellation color

    const sw = document.createElement('span');
    sw.className = 'swatch';
    sw.style.background = rgba;

    const nm = document.createElement('span');
    nm.className = 'name';
    nm.textContent = name;

    const cnt = document.createElement('span');
    cnt.className = 'count';
    cnt.textContent = n.toLocaleString();

    row.appendChild(cb);
    row.appendChild(sw);
    row.appendChild(nm);
    row.appendChild(cnt);
    // Toggles affect only the globe markers; the alerts panel is independent
    // of visibility by design (see updateMeta / renderAlerts).
    cb.addEventListener('change', () => toggleConstellation(name, cb.checked));
    el.appendChild(row);
  }
}

// Toggle a constellation's satellite markers on/off and refresh the meta line.
function toggleConstellation(name, visible) {
  if (visible) visibleConstellations.add(name);
  else visibleConstellations.delete(name);
  for (const id in satEntities) {
    if (satConstellation[id] === name) satEntities[id].show = visible;
  }
  updateMeta();
}

// Meta line under the title: "<total> sats (<visible> of <total> shown) ·
// <steps> steps · fetched <time>". Falls back to the old comma-joined group
// label for payloads without the per-satellite constellation field.
function updateMeta() {
  const orbits = orbitsData.orbits || [];
  const meta = document.getElementById('meta');
  if (!meta) return;
  if (!orbits.length) { meta.textContent = 'No data — run the pipeline.'; return; }
  const totalSats = orbitsData.n_satellites || orbits.length;
  const fetchTime = orbitsData.fetch_time || config.fetch_time || '';
  let lead;
  if (orbits[0].constellation) {
    lead = `${totalSats.toLocaleString()} sats (${visibleConstellations.size} of ${totalConstellations} shown)`;
  } else {
    lead = (orbitsData.group || config.sat_group || '').toString();
  }
  meta.textContent = `${lead} · ${orbitsData.n_timesteps} steps · fetched ${fetchTime}`;
}

// Rebuild the red close-approach overlays + alerts list under `threshold`.
function renderAlerts(threshold) {
  // Clear previous alert entities (those starting with 'evt-').
  const toRemove = [];
  viewer.entities.values.forEach(e => { if (String(e.id).startsWith('evt-')) toRemove.push(e.id); });
  toRemove.forEach(id => viewer.entities.removeById(id));

  const alertsEl = document.getElementById('alerts');
  alertsEl.innerHTML = '';
  const events = (eventsData.events || [])
    .filter(e => e.distance_km <= threshold)
    .sort((a, b) => a.distance_km - b.distance_km)
    .slice(0, MAX_ALERT_LINES);

  document.getElementById('alertCount').textContent = events.length;

  // Each event carries its two satellites' ECEF positions at the event time
  // (embedded by the pipeline), so we render straight from the event record —
  // no lookup against the (now coarse) orbit grid. This is what lets the orbit
  // grid be decimated for size without dropping or mislocating any alert.
  events.forEach((ev, i) => {
    const a = ev.ecef_a, b = ev.ecef_b;
    if (!a || !b) return;
    const t0 = fromIso(ev.timestamp);
    const t1 = Cesium.JulianDate.addSeconds(t0, stepSeconds, new Cesium.JulianDate());
    const avail = new Cesium.TimeIntervalCollection([
      new Cesium.TimeInterval({ start: t0, stop: t1 }),
    ]);
    viewer.entities.add({
      id: 'evt-line-' + i,
      availability: avail,
      polyline: {
        positions: [
          new Cesium.Cartesian3(a[0], a[1], a[2]),
          new Cesium.Cartesian3(b[0], b[1], b[2]),
        ],
        arcType: Cesium.ArcType.NONE,
        width: 2,
        material: Cesium.Color.RED,
      },
    });
    viewer.entities.add({
      id: 'evt-a-' + i,
      availability: avail,
      position: new Cesium.ConstantPositionProperty(new Cesium.Cartesian3(a[0], a[1], a[2])),
      point: { pixelSize: 10, color: Cesium.Color.RED },
    });
    viewer.entities.add({
      id: 'evt-b-' + i,
      availability: avail,
      position: new Cesium.ConstantPositionProperty(new Cesium.Cartesian3(b[0], b[1], b[2])),
      point: { pixelSize: 10, color: Cesium.Color.RED },
    });

    const div = document.createElement('div');
    div.className = 'alert';
    // Cross-group events get a small constellation tag above the satellite
    // names so users can see why this alert stands out from same-group traffic.
    const cross = ev.constellation_a && ev.constellation_b
      && ev.constellation_a !== ev.constellation_b;
    const tag = cross
      ? `<div class="c">${ev.constellation_a} ↔ ${ev.constellation_b}</div>`
      : '';
    div.innerHTML =
      tag +
      `<div class="a">${ev.name_a} ↔ ${ev.name_b}</div>` +
      `<div class="d">${ev.distance_km.toFixed(1)} km` +
      (ev.rel_vel_km_s != null ? ` · ${ev.rel_vel_km_s.toFixed(2)} km/s` : '') + `</div>` +
      `<div class="t">${ev.timestamp}</div>`;
    div.onclick = () => {
      viewer.clock.currentTime = t0.clone();
      const mid = new Cesium.Cartesian3((a[0]+b[0])/2, (a[1]+b[1])/2, (a[2]+b[2])/2);
      viewer.scene.camera.lookAt(mid, new Cesium.HeadingPitchRange(0, -0.6, 1500000));
    };
    alertsEl.appendChild(div);
  });
}

function computeStepSeconds(t) {
  if (!Array.isArray(t) || t.length < 2) return 60;
  const dt = (new Date(t[1]).getTime() - new Date(t[0]).getTime()) / 1000;
  return Number.isFinite(dt) && dt > 0 ? dt : 60;
}

async function init() {
  // Fetch config first (small), then kick off the (slow) data fetch so it
  // overlaps with globe construction below — the globe needs only Cesium, not
  // the orbit data, so it can render before the data lands.
  await fetchConfig();
  const dataP = loadData();

  if (config.cesium_ion_token) Cesium.Ion.defaultAccessToken = config.cesium_ion_token;
  // baseLayer: false opts out of the Cesium Ion default imagery; we add our own
  // free basemap below (default 'dark'). Swappable at runtime via setBasemap.
  viewer = new Cesium.Viewer('cesiumContainer', {
    timeline: true, animation: false, baseLayerPicker: false, fullscreenButton: true,
    geocoder: false, homeButton: false, sceneModePicker: false, navigationHelpButton: false,
    infoBox: true, selectionIndicator: true,
    baseLayer: false,
  });
  // Keep satellites visible above the globe surface.
  viewer.scene.globe.depthTestAgainstTerrain = false;
  setBasemap('dark');

  // Now block on the data and build satellites on top of the live globe.
  await dataP;
  stepSeconds = computeStepSeconds(orbitsData.t);
  if (!orbitsData.orbits) { document.getElementById('meta').textContent = 'No data — run the pipeline.'; return; }

  buildSatellites();
  renderLegend();
  updateMeta();

  const slider = document.getElementById('threshold');
  const thrVal = document.getElementById('thrVal');
  // Default slider / filter position is 1 km, regardless of the pipeline's
  // detection threshold. The slider ranges 0.01–5 km in 0.01 km steps.
  const initThr = Math.min(1.0, Number(slider.max) || 5);
  slider.value = initThr; thrVal.textContent = initThr.toFixed(2);
  renderAlerts(initThr);
  slider.addEventListener('input', () => {
    const v = Number(slider.value); thrVal.textContent = v.toFixed(2); renderAlerts(v);
  });

  // Basemap selector — swapping only re-loads map tiles; satellites stay.
  const basemapSel = document.getElementById('basemap');
  if (basemapSel) {
    basemapSel.value = 'dark';
    basemapSel.addEventListener('change', () => setBasemap(basemapSel.value));
  }
}

document.addEventListener('DOMContentLoaded', init);