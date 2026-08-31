/* <windfall-map> — real Austin, TX operations map for Windfall Rescue Router.
   Real OSM geometry via Leaflet, tiles graded to the Classical palette.
   Data-driven: call .setEntities(donors, foodBanks, drivers) with real rows
   from GET /state, and .addRoute(donor, foodBank) with real lat/lon on a
   confirmed match. No placeholder data lives in this file.
*/
(() => {
  if (window.__windfallMapLoaded) return;
  window.__windfallMapLoaded = true;

  const INK = '#201f1d', GOLD = '#b68235', OLIVE = '#6c7a3c';

  const CSS = `
windfall-map{display:block;position:relative;width:100%;height:100%;min-height:240px;background:#e8e4dd;overflow:hidden}
windfall-map .wf-canvas{position:absolute;inset:0}
windfall-map .leaflet-tile-pane{filter:sepia(.46) saturate(.5) contrast(.9) brightness(1.07)}
windfall-map .leaflet-container{background:#e8e4dd;font-family:Lora,Georgia,serif;outline:none}
windfall-map .leaflet-control-attribution{background:rgba(243,242,242,.72);color:#605d5d;font-size:9px;font-family:Lora,Georgia,serif}
windfall-map .leaflet-control-attribution a{color:#7d5411}
windfall-map .leaflet-bar{border:1px solid rgba(32,31,29,.16);box-shadow:none;border-radius:4px;overflow:hidden}
windfall-map .leaflet-bar a{background:rgba(243,242,242,.9);color:${INK};border-bottom-color:rgba(32,31,29,.16);width:26px;height:26px;line-height:26px;font-size:15px}
windfall-map .leaflet-bar a:hover{background:rgba(182,130,53,.14);color:${GOLD}}
windfall-map .wf-pin{display:grid;place-items:center}
windfall-map .wf-route{animation:wf-dash 1.6s linear infinite}
@keyframes wf-dash{to{stroke-dashoffset:-14}}
windfall-map .wf-pulse{border-radius:50%;border:1.5px solid ${GOLD};animation:wf-pulse 2.4s ease-out infinite}
@keyframes wf-pulse{0%{transform:scale(.35);opacity:.9}100%{transform:scale(1);opacity:0}}
windfall-map .leaflet-tooltip{background:#f3f2f2;border:1px solid rgba(32,31,29,.16);border-radius:3px;box-shadow:0 1px 2px rgba(45,43,43,.14);color:${INK};font-size:11px;padding:4px 8px;font-family:Lora,Georgia,serif}
windfall-map .leaflet-tooltip::before{display:none}
windfall-map .wf-veil{position:absolute;inset:0;pointer-events:none;z-index:450;box-shadow:inset 0 0 60px 12px rgba(45,43,43,.13)}

/* Dark theme: same rules the page uses to pick its palette (see index.html) --
   default-dark-if-system-prefers-it, or forced by the header toggle. */
@media (prefers-color-scheme: dark) {
  html:not([data-theme="light"]) windfall-map{background:#232019}
  html:not([data-theme="light"]) windfall-map .leaflet-tile-pane{filter:sepia(.35) saturate(.4) contrast(.85) brightness(.55) invert(.92) hue-rotate(180deg)}
  html:not([data-theme="light"]) windfall-map .leaflet-container{background:#232019}
  html:not([data-theme="light"]) windfall-map .leaflet-control-attribution{background:rgba(24,23,21,.72);color:#a39e98}
  html:not([data-theme="light"]) windfall-map .leaflet-control-attribution a{color:#e3b45c}
  html:not([data-theme="light"]) windfall-map .leaflet-bar a{background:rgba(35,32,28,.9);color:#ece9e3;border-bottom-color:rgba(255,255,255,.14)}
  html:not([data-theme="light"]) windfall-map .leaflet-bar a:hover{background:rgba(227,180,92,.16);color:#e3b45c}
  html:not([data-theme="light"]) windfall-map .leaflet-tooltip{background:#2b2823;border-color:rgba(255,255,255,.16);color:#ece9e3}
}
html[data-theme="dark"] windfall-map{background:#232019}
html[data-theme="dark"] windfall-map .leaflet-tile-pane{filter:sepia(.35) saturate(.4) contrast(.85) brightness(.55) invert(.92) hue-rotate(180deg)}
html[data-theme="dark"] windfall-map .leaflet-container{background:#232019}
html[data-theme="dark"] windfall-map .leaflet-control-attribution{background:rgba(24,23,21,.72);color:#a39e98}
html[data-theme="dark"] windfall-map .leaflet-control-attribution a{color:#e3b45c}
html[data-theme="dark"] windfall-map .leaflet-bar a{background:rgba(35,32,28,.9);color:#ece9e3;border-bottom-color:rgba(255,255,255,.14)}
html[data-theme="dark"] windfall-map .leaflet-bar a:hover{background:rgba(227,180,92,.16);color:#e3b45c}
html[data-theme="dark"] windfall-map .leaflet-tooltip{background:#2b2823;border-color:rgba(255,255,255,.16);color:#ece9e3}
`;

  const icons = {
    donor: `<svg width="16" height="16" viewBox="0 0 16 16"><circle cx="8" cy="8" r="5.4" fill="#f3f2f2" stroke="${GOLD}" stroke-width="1.5"/><circle cx="8" cy="8" r="1.7" fill="${GOLD}"/></svg>`,
    bank: `<svg width="16" height="16" viewBox="0 0 16 16"><rect x="2.6" y="2.6" width="10.8" height="10.8" fill="#f3f2f2" stroke="${OLIVE}" stroke-width="1.5"/><path d="M5 8h6M8 5v6" stroke="${OLIVE}" stroke-width="1.4"/></svg>`,
    driver: (assigned) => {
      const c = assigned ? GOLD : OLIVE;
      return `<svg width="14" height="14" viewBox="0 0 14 14"><circle cx="7" cy="7" r="4.2" fill="${c}" stroke="${c}" stroke-width="1.5"/></svg>`;
    }
  };

  const waitForL = () => new Promise((res, rej) => {
    if (window.L) return res(window.L);
    let t = 0;
    const iv = setInterval(() => {
      if (window.L) { clearInterval(iv); res(window.L); }
      else if ((t += 80) > 12000) { clearInterval(iv); rej(new Error('Leaflet did not load')); }
    }, 80);
  });

  /* a gently bowed path between two points, so routes read as streets not rubber bands */
  function arcPath(a, b, bow = 0.14) {
    const pts = [], n = 26;
    const mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2;
    const dx = b[0] - a[0], dy = b[1] - a[1];
    const cx = mx - dy * bow, cy = my + dx * bow;
    for (let i = 0; i <= n; i++) {
      const t = i / n, u = 1 - t;
      pts.push([u * u * a[0] + 2 * u * t * cx + t * t * b[0], u * u * a[1] + 2 * u * t * cy + t * t * b[1]]);
    }
    return pts;
  }

  class WindfallMap extends HTMLElement {
    connectedCallback() {
      if (this._booted) return;
      this._booted = true;
      if (!document.getElementById('wf-map-css')) {
        const s = document.createElement('style');
        s.id = 'wf-map-css'; s.textContent = CSS;
        document.head.appendChild(s);
      }
      this.style.width = '100%';
      this.style.height = '100%';
      const canvas = document.createElement('div');
      canvas.className = 'wf-canvas';
      this.appendChild(canvas);
      this._routes = [];
      this._markers = { donors: {}, foodBanks: {}, drivers: {} };
      this._ready = waitForL().then((L) => this.boot(L, canvas)).catch(() => {
        canvas.innerHTML = '<div style="position:absolute;inset:0;display:grid;place-items:center;font:13px Lora,serif;color:#605d5d">Map tiles unavailable offline</div>';
      });
    }

    boot(L, canvas) {
      const map = L.map(canvas, {
        zoomControl: true, scrollWheelZoom: false, zoomSnap: 0.25,
        attributionControl: true, keyboard: true
      });
      this.map = map; this.L = L;
      L.control.zoom({ position: 'bottomright' }).addTo(map);
      L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; OpenStreetMap contributors', maxZoom: 18
      }).addTo(map);
      map.setView([30.283, -97.735], 12);
      const ro = new ResizeObserver(() => map.invalidateSize());
      ro.observe(this);
      this._tick();
      if (this._pendingEntities) this.setEntities(...this._pendingEntities);
    }

    setEntities(donors, foodBanks, drivers) {
      if (!this.map) { this._pendingEntities = [donors, foodBanks, drivers]; return; }
      const L = this.L, map = this.map;
      const upsert = (store, p, iconHtml, size) => {
        if (store[p.id]) {
          store[p.id].setLatLng([p.lat, p.lon]);
          return store[p.id];
        }
        const m = L.marker([p.lat, p.lon], {
          icon: L.divIcon({ className: 'wf-pin', html: iconHtml, iconSize: [size, size], iconAnchor: [size / 2, size / 2] }),
          keyboard: false
        }).addTo(map).bindTooltip(p.name, { direction: 'top', offset: [0, -8] });
        store[p.id] = m;
        return m;
      };

      donors.forEach(d => upsert(this._markers.donors, d, icons.donor, 16));
      foodBanks.forEach(fb => upsert(this._markers.foodBanks, fb, icons.bank, 16));
      drivers.forEach(dr => {
        const assigned = dr.status === 'assigned';
        const m = upsert(this._markers.drivers, dr, icons.driver(assigned), 14);
        m.setIcon(L.divIcon({ className: 'wf-pin', html: icons.driver(assigned), iconSize: [14, 14], iconAnchor: [7, 7] }));
      });

      if (!this._fitted && (donors.length || foodBanks.length)) {
        this._fitted = true;
        const pts = [...donors, ...foodBanks].map(p => [p.lat, p.lon]);
        map.fitBounds(L.latLngBounds(pts), { padding: [24, 24] });
      }
    }

    addRoute(donor, foodBank) {
      if (!this.map || !donor || !foodBank) return;
      const L = this.L;
      const from = [donor.lat, donor.lon], to = [foodBank.lat, foodBank.lon];
      const pts = arcPath(from, to, 0.16);
      const line = L.polyline(pts, { color: GOLD, weight: 1.8, opacity: 0.9, dashArray: '7 7', className: 'wf-route' }).addTo(this.map);
      const mover = L.marker(pts[0], {
        icon: L.divIcon({ className: 'wf-pin', html: icons.driver(true), iconSize: [14, 14], iconAnchor: [7, 7] })
      }).addTo(this.map);
      this._routes.push({ line, mover, pts, t: 0, speed: 0.0009 });

      const pulse = L.marker(from, {
        icon: L.divIcon({ className: 'wf-pin', html: '<div class="wf-pulse" style="width:46px;height:46px"></div>', iconSize: [46, 46], iconAnchor: [23, 23] })
      }).addTo(this.map);
      setTimeout(() => this.map.removeLayer(pulse), 6000);
      setTimeout(() => { this.map.removeLayer(line); this.map.removeLayer(mover); this._routes = this._routes.filter(r => r.mover !== mover); }, 20000);
    }

    _tick() {
      const step = () => {
        this._routes.forEach(r => {
          r.t += r.speed;
          if (r.t > 1) r.t = 1;
          const i = Math.min(r.pts.length - 2, Math.floor(r.t * (r.pts.length - 1)));
          const f = r.t * (r.pts.length - 1) - i;
          const a = r.pts[i], b = r.pts[i + 1];
          r.mover.setLatLng([a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f]);
        });
        this._raf = requestAnimationFrame(step);
      };
      step();
    }
  }

  if (!customElements.get('windfall-map')) customElements.define('windfall-map', WindfallMap);
})();
