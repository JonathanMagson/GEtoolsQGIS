"""Red X drawn over Google Earth Web at the spot clicked in QGIS.

Google Earth Web has no way to add a placemark from outside the page, so the
plugin draws the X itself, in the Earth window it controls (the QGIS panel or
the Chrome/Edge window). To keep the X on the ground, the script reads the
camera Earth Web writes into its address (``/@lat,lon,<alt>a,<range>d,<fov>y,
<heading>h,<tilt>t``) and projects the clicked point onto the globe's canvas.

Earth Web only rewrites its address once the view settles, so the X hides
while you drag or scroll and comes back when the camera stops.

Pure Python, no QGIS imports.
"""

import json

# Runs in the page. Idempotent: re-running it just retargets the marker, and
# a null target removes it. It's also installed to run on every page load,
# before the document exists, hence the wait for <body>.
_MARKER_JS = r"""(function (target) {
  if (window.__getoolsMarker) { window.__getoolsMarker.setTarget(target); return; }

  var DEG = Math.PI / 180, A = 6378137.0, E2 = 6.69437999014e-3;

  function ecef(lat, lon, h) {
    var sp = Math.sin(lat * DEG), cp = Math.cos(lat * DEG);
    var n = A / Math.sqrt(1 - E2 * sp * sp);
    return [(n + h) * cp * Math.cos(lon * DEG), (n + h) * cp * Math.sin(lon * DEG),
            (n * (1 - E2) + h) * sp];
  }
  function toEnu(d, lat, lon) {  // ECEF offset -> east, north, up at lat/lon
    var sp = Math.sin(lat * DEG), cp = Math.cos(lat * DEG);
    var sl = Math.sin(lon * DEG), cl = Math.cos(lon * DEG);
    return [-sl * d[0] + cl * d[1],
            -sp * cl * d[0] - sp * sl * d[1] + cp * d[2],
            cp * cl * d[0] + cp * sl * d[1] + sp * d[2]];
  }
  function dot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }

  function parseCamera(href) {
    var m = /\/@(-?[\d.]+),(-?[\d.]+),([^\/?#]*)/.exec(href);
    if (!m) return null;
    var cam = {lat: +m[1], lon: +m[2], a: 0, d: 0, y: 35, h: 0, t: 0};
    m[3].split(',').forEach(function (part) {
      var key = part.slice(-1), value = parseFloat(part);
      if ('adyht'.indexOf(key) >= 0 && !isNaN(value)) cam[key] = value;
    });
    return cam.d > 0 ? cam : null;
  }

  // Screen position of target for the camera in the URL, or null.
  function project(cam, tgt, rect) {
    var l = ecef(cam.lat, cam.lon, cam.a), t = ecef(tgt.lat, tgt.lon, cam.a);
    var p = toEnu([t[0] - l[0], t[1] - l[1], t[2] - l[2]], cam.lat, cam.lon);
    var h = cam.h * DEG, tilt = cam.t * DEG;
    var view = [Math.sin(tilt) * Math.sin(h), Math.sin(tilt) * Math.cos(h), -Math.cos(tilt)];
    var up = [Math.cos(tilt) * Math.sin(h), Math.cos(tilt) * Math.cos(h), Math.sin(tilt)];
    var right = [Math.cos(h), -Math.sin(h), 0];
    // Camera sits cam.d metres back from the look-at point along the view.
    var q = [p[0] + cam.d * view[0], p[1] + cam.d * view[1], p[2] + cam.d * view[2]];
    var depth = dot(q, view);
    if (depth <= 1) return null;
    var f = (rect.height / 2) / Math.tan(cam.y * DEG / 2);
    var x = rect.left + rect.width / 2 + f * dot(q, right) / depth;
    var y = rect.top + rect.height / 2 - f * dot(q, up) / depth;
    if (x < rect.left || x > rect.right || y < rect.top || y > rect.bottom) return null;
    return [x, y];
  }

  // The globe is the largest <canvas>, possibly inside shadow DOM.
  function globeRect() {
    var best = null, bestArea = 0;
    (function walk(root) {
      root.querySelectorAll('*').forEach(function (el) {
        if (el.tagName === 'CANVAS') {
          var r = el.getBoundingClientRect(), area = r.width * r.height;
          if (area > bestArea) { best = r; bestArea = area; }
        }
        if (el.shadowRoot) walk(el.shadowRoot);
      });
    })(document);
    if (best) return best;
    return {left: 0, top: 0, width: innerWidth, height: innerHeight,
            right: innerWidth, bottom: innerHeight};
  }

  var SIZE = 30;
  var el = document.createElement('div');
  el.id = 'getools-target';
  el.style.cssText = 'position:fixed;width:' + SIZE + 'px;height:' + SIZE + 'px;' +
    'pointer-events:none;z-index:2147483647;display:none;';
  el.innerHTML = '<svg width="' + SIZE + '" height="' + SIZE + '" viewBox="0 0 30 30">' +
    '<path d="M5 5L25 25M25 5L5 25" stroke="white" stroke-width="7" stroke-linecap="round"/>' +
    '<path d="M5 5L25 25M25 5L5 25" stroke="#e60000" stroke-width="4" stroke-linecap="round"/>' +
    '</svg>';

  var state = {target: target, href: null, moving: false, rect: ''};

  function place() {
    var pos = null, cam = parseCamera(location.href);
    if (state.target && cam && !state.moving) pos = project(cam, state.target, globeRect());
    if (!pos) { el.style.display = 'none'; return; }
    el.style.left = (pos[0] - SIZE / 2) + 'px';
    el.style.top = (pos[1] - SIZE / 2) + 'px';
    el.style.display = 'block';
  }

  // Hide while the user moves the globe: the URL lags behind the camera.
  var down = null;
  function onDown(e) { down = [e.clientX, e.clientY]; }
  function onMove(e) {
    if (down && Math.abs(e.clientX - down[0]) + Math.abs(e.clientY - down[1]) > 3) {
      state.moving = true; place();
    }
  }
  function onUp() { down = null; }
  function onWheel() { state.moving = true; place(); }
  addEventListener('pointerdown', onDown, true);
  addEventListener('pointermove', onMove, true);
  addEventListener('pointerup', onUp, true);
  addEventListener('wheel', onWheel, {capture: true, passive: true});

  var timer = setInterval(function () {
    if (!el.isConnected && document.body) document.body.appendChild(el);
    var r = globeRect(), key = [r.left, r.top, r.width, r.height].join();
    if (location.href !== state.href || key !== state.rect) {
      state.href = location.href; state.rect = key; state.moving = false;
      place();
    }
  }, 150);

  window.__getoolsMarker = {
    setTarget: function (t) { state.target = t; state.moving = false; place(); },
    remove: function () {
      clearInterval(timer); el.remove(); delete window.__getoolsMarker;
      removeEventListener('pointerdown', onDown, true);
      removeEventListener('pointermove', onMove, true);
      removeEventListener('pointerup', onUp, true);
      removeEventListener('wheel', onWheel, {capture: true});
    }
  };
  place();
})(%s);"""


def marker_js(lat, lon):
    """JavaScript that puts the X at ``lat, lon`` (or moves it there)."""
    return _MARKER_JS % json.dumps({"lat": float(lat), "lon": float(lon)})


CLEAR_MARKER_JS = "window.__getoolsMarker && window.__getoolsMarker.remove();"
