/*
  The plan, drawn.

  Four layers from geo.py — the boundary the run matched, the register's points
  split by whether they serve, and the recommendation — as inline SVG on an
  equirectangular projection fitted to the run's own bounding box.

  No map library and no basemap tiles. Two reasons, and the second is the real
  one. A district outline with 840 points is a few hundred lines of geometry;
  a tile-backed map is a megabyte and a dependency on somebody's tile server
  being reachable from wherever this is being used. This has to open on a field
  connection, and it has to open when it is being demonstrated on a conference
  network.

  The projection is deliberately crude: at one district's extent the departure
  from the projected CRS the analysis used is far below a pixel, and the map is
  a second reading of the plan rather than a measuring instrument. Anything
  measured is measured in `siting/spatial.py`, in a projected CRS, and reported
  in the account.

  The table comes first in the panel and the map second. The table is what an
  officer carries into a meeting.
*/

function planMap(data, opts){
  const W = opts?.width || 320, H = opts?.height || 250, PAD = 8;
  const [w, s, e, n] = data.bbox;
  // Latitude correction so the district is not stretched east-west.
  const midLat = (s + n) / 2 * Math.PI / 180;
  const dx = (e - w) * Math.cos(midLat), dy = n - s;
  const k = Math.min((W - 2 * PAD) / dx, (H - 2 * PAD) / dy);
  const offX = (W - dx * k) / 2, offY = (H - dy * k) / 2;
  const X = lon => offX + (lon - w) * Math.cos(midLat) * k;
  const Y = lat => offY + (n - lat) * k;

  const ring = coords => coords.map(([lon, lat]) =>
    `${X(lon).toFixed(1)},${Y(lat).toFixed(1)}`).join(" ");

  const polys = [];
  for (const f of data.layers.boundary.features){
    const g = f.geometry;
    const parts = g.type === "MultiPolygon" ? g.coordinates : [g.coordinates];
    for (const poly of parts)
      polys.push(`<polygon points="${ring(poly[0])}" class="m-bound"/>`);
  }

  const dots = (fc, cls) => fc.features.map(f => {
    const [lon, lat] = f.geometry.coordinates;
    return `<circle cx="${X(lon).toFixed(1)}" cy="${Y(lat).toFixed(1)}" r="1.4" class="${cls}"/>`;
  }).join("");

  const sites = data.layers.sites.features.map(f => {
    const [lon, lat] = f.geometry.coordinates;
    const p = f.properties;
    return `<g class="m-site" data-id="${p.id}">
      <circle cx="${X(lon).toFixed(1)}" cy="${Y(lat).toFixed(1)}" r="7" class="m-halo"/>
      <circle cx="${X(lon).toFixed(1)}" cy="${Y(lat).toFixed(1)}" r="3.6" class="m-core"/>
      <text x="${(X(lon) + 9).toFixed(1)}" y="${(Y(lat) + 3.5).toFixed(1)}">${p.rank}</text>
      <title>${p.id} · rank ${p.rank} · ${p.newly_covered.toLocaleString()} newly covered</title>
    </g>`;
  }).join("");

  // Scale bar: one kilometre in degrees of longitude at this latitude.
  const kmDeg = 1 / (111.32 * Math.cos(midLat));
  let barKm = 10;
  while (kmDeg * barKm * Math.cos(midLat) * k > (W - 2 * PAD) * 0.4) barKm /= 2;
  while (kmDeg * barKm * Math.cos(midLat) * k < (W - 2 * PAD) * 0.15) barKm *= 2;
  const barPx = kmDeg * barKm * Math.cos(midLat) * k;

  return `<svg viewBox="0 0 ${W} ${H}" class="m-svg" role="img"
      aria-label="${data.layers.sites.features.length} recommended sites in
      ${data.scope.adm2}, with the district boundary and the existing register">
    ${polys.join("")}
    ${dots(data.layers.broken, "m-broken")}
    ${dots(data.layers.working, "m-working")}
    ${sites}
    <g class="m-scale">
      <line x1="${PAD}" y1="${H - PAD}" x2="${PAD + barPx}" y2="${H - PAD}"/>
      <line x1="${PAD}" y1="${H - PAD - 3}" x2="${PAD}" y2="${H - PAD + 3}"/>
      <line x1="${PAD + barPx}" y1="${H - PAD - 3}" x2="${PAD + barPx}" y2="${H - PAD + 3}"/>
      <text x="${PAD + barPx + 5}" y="${H - PAD + 3}">${barKm} km</text>
    </g>
  </svg>`;
}

function planLegend(data){
  const L = [
    ["m-key-site", `${data.layers.sites.features.length} recommended`],
    ["m-key-working", `${data.layers.working.features.length} serving`],
    ["m-key-broken", `${data.layers.broken.features.length} not serving`],
  ];
  return `<div class="m-legend">` + L.map(([c, t]) =>
    `<span><i class="${c}"></i>${t}</span>`).join("") + `</div>`;
}
