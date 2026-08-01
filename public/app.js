// Satellite Collision Risk Tracker — CesiumJS frontend.
// Builds one moving entity per satellite from precomputed ECEF samples, and
// overlays close-approach pairs (red connecting line) under the threshold.

const MAX_ALERT_LINES = 400; // cap rendered event lines for performance

let viewer, satEntities = {}, eventsData = [], orbitsData = null, config = {};
let stepSeconds = 60; // derived from output time grid after data loads

function fromIso(s) { return Cesium.JulianDate.fromIso8601(s); }

// Load config first, then data. In production `config.data_url` points at the
// merged positions.json the Cloud Function publishes to a public GCS bucket;
// the frontend fetches it straight from there (not the repo's committed data
// files). In local dev `data_url` is empty, so we fall back to the FastAPI
// endpoints that read data/orbits + data/events from disk.
async function loadAll() {
  config = await fetch('/api/config').then(r => r.json());
  if (config.data_url) {
    // positions.json = orbits payload + a merged `events` array, so it stands
    // in for both orbitsData and eventsData.
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

  orbits.forEach(o => {
    const pos = new Cesium.SampledPositionProperty(Cesium.ReferenceFrame.FIXED);
    const e = o.ecef_m;
    const m = Math.floor(e.length / 3); // timesteps for this sat (== n normally)
    for (let i = 0; i < m; i++) {
      pos.addSample(fromIso(t[i]), new Cesium.Cartesian3(e[i*3], e[i*3+1], e[i*3+2]));
    }
    satEntities[o.sat_id] = viewer.entities.add({
      id: 'sat-' + o.sat_id,
      name: o.name,
      position: pos,
      point: { pixelSize: 6, color: Cesium.Color.SKYBLUE, outlineColor: Cesium.Color.WHITE, outlineWidth: 1 },
    });
  });

  viewer.clock.startTime = start.clone();
  viewer.clock.stopTime = stop.clone();
  viewer.clock.currentTime = start.clone();
  viewer.clock.clockRange = Cesium.ClockRange.LOOP_STOP;
  viewer.clock.multiplier = 600; // 600x playback
  viewer.timeline.zoomTo(start, stop);
  viewer.clock.shouldAnimate = true;
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

  // Build a per-satellite ECEF lookup keyed by timestamp: { sat_id: { ts: [x,y,z] } }.
  const t = orbitsData.t || [];
  const tIndex = {}; t.forEach((ts, i) => { tIndex[ts] = i; });
  const ecefBySat = {};
  (orbitsData.orbits || []).forEach(o => {
    const e = o.ecef_m;
    ecefBySat[o.sat_id] = (ts) => {
      const i = tIndex[ts];
      if (i == null) return null;
      return [e[i*3], e[i*3+1], e[i*3+2]];
    };
  });

  events.forEach((ev, i) => {
    const a = ecefBySat[ev.sat_a] ? ecefBySat[ev.sat_a](ev.timestamp) : null;
    const b = ecefBySat[ev.sat_b] ? ecefBySat[ev.sat_b](ev.timestamp) : null;
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
    div.innerHTML =
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
  // Use a free imagery source so the globe renders without a Cesium Ion token.
  await loadAll();
  stepSeconds = computeStepSeconds(orbitsData.t);
  if (config.cesium_ion_token) Cesium.Ion.defaultAccessToken = config.cesium_ion_token;

  const imageryProvider = new Cesium.OpenStreetMapImageryProvider({
    url: 'https://tile.openstreetmap.org/',
  });
  viewer = new Cesium.Viewer('cesiumContainer', {
    timeline: true, animation: false, baseLayerPicker: false, fullscreenButton: true,
    geocoder: false, homeButton: false, sceneModePicker: false, navigationHelpButton: false,
    infoBox: true, selectionIndicator: true,
    baseLayer: false, // avoid Ion default; we add a free OSM layer below
  });
  viewer.imageryLayers.addImageryProvider(imageryProvider);
  // Keep satellites visible above the globe surface.
  viewer.scene.globe.depthTestAgainstTerrain = false;
  if (!orbitsData.orbits) { document.getElementById('meta').textContent = 'No data — run the pipeline.'; return; }

  buildSatellites();

  document.getElementById('meta').textContent =
    `${orbitsData.group || config.sat_group} · ${orbitsData.n_satellites} sats · ${orbitsData.n_timesteps} steps · fetched ${orbitsData.fetch_time || config.fetch_time || ''}`;

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
}

document.addEventListener('DOMContentLoaded', init);