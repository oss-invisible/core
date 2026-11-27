// The last trusted mousemove's screen minus client position, and how many moves were seen.
window.__m = {count: 0, dx: null, dy: null};
// Every trusted mousemove since the last reset, in order: the client and screen point it carried and their difference (only the
// iframes under a transform ask for this list).
window.__moves = [];
window.__reset = () => { window.__m = {count: 0, dx: null, dy: null}; window.__moves = []; };
addEventListener("mousemove", (e) => {
  if (!e.isTrusted) return;
  window.__m.count += 1;
  window.__m.dx = e.screenX - e.clientX;
  window.__m.dy = e.screenY - e.clientY;
  window.__moves.push({clientX: e.clientX, clientY: e.clientY, screenX: e.screenX, screenY: e.screenY,
                       dx: e.screenX - e.clientX, dy: e.screenY - e.clientY});
});

// Everything a page can read that may differ under responsive-design mode (inRDMPane), read here in the page itself and
// returned as a JSON string (the content script parses it): the screen, the pixel ratio, the media queries (the integers
// found by binary search over min- queries, the booleans as .matches), touch, the visual viewport, the root's client size.
window.__probe = () => {
  const matches = (query) => matchMedia(query).matches;
  // The largest integer n in [0, hi] for which make(n) matches; every min- query is true up to the value and false after.
  const largest = (make, hi) => {
    let lo = 0;
    while (lo < hi) {
      const mid = Math.ceil((lo + hi) / 2);
      if (matches(make(mid))) lo = mid; else hi = mid - 1;
    }
    return lo;
  };
  const found = {
    width: largest((n) => `(min-width: ${n}px)`, 65536),
    height: largest((n) => `(min-height: ${n}px)`, 65536),
    "device-width": largest((n) => `(min-device-width: ${n}px)`, 65536),
    "device-height": largest((n) => `(min-device-height: ${n}px)`, 65536),
    // the ratio times 10000, rounded down
    "aspect-ratio_x10000": largest((n) => `(min-aspect-ratio: ${n}/10000)`, 1000000),
    "device-aspect-ratio_x10000": largest((n) => `(min-device-aspect-ratio: ${n}/10000)`, 1000000),
    // the resolution in dppx times 100, rounded down (0.01 dppx)
    "resolution_x100": largest((n) => `(min-resolution: ${n / 100}dppx)`, 2000),
  };
  const queries = {};
  for (const q of [
    `(width: ${found.width}px)`, `(height: ${found.height}px)`,
    `(device-width: ${found["device-width"]}px)`, `(device-height: ${found["device-height"]}px)`,
    "(pointer: fine)", "(pointer: coarse)", "(pointer: none)",
    "(any-pointer: fine)", "(any-pointer: coarse)", "(any-pointer: none)",
    "(hover: hover)", "(hover: none)", "(any-hover: hover)", "(any-hover: none)",
    "(orientation: portrait)", "(orientation: landscape)",
  ]) queries[q] = matches(q);
  const s = screen;
  const vv = window.visualViewport;
  return JSON.stringify({
    screen: {width: s.width, height: s.height, availWidth: s.availWidth, availHeight: s.availHeight,
             availLeft: s.availLeft, availTop: s.availTop, colorDepth: s.colorDepth, pixelDepth: s.pixelDepth,
             orientation: s.orientation ? {type: s.orientation.type, angle: s.orientation.angle} : null},
    devicePixelRatio: window.devicePixelRatio,
    found: found,
    matches: queries,
    touch: {maxTouchPoints: navigator.maxTouchPoints, ontouchstart: "ontouchstart" in window, touchEvent: typeof TouchEvent},
    visualViewport: vv ? {width: vv.width, height: vv.height, scale: vv.scale, offsetLeft: vv.offsetLeft, offsetTop: vv.offsetTop,
                          pageLeft: vv.pageLeft, pageTop: vv.pageTop} : null,
    client: {width: document.documentElement.clientWidth, height: document.documentElement.clientHeight},
  });
};

// The whole content record of a window, as a JSON string (the same fields as the content script of measure.py reads in the
// main page): what a popup and an iframe report about themselves, with the probe.
window.__state = () => JSON.stringify({
  innerWidth: window.innerWidth, innerHeight: window.innerHeight, outerWidth: window.outerWidth, outerHeight: window.outerHeight,
  screenX: window.screenX, screenY: window.screenY, screenLeft: window.screenLeft, screenTop: window.screenTop,
  mozInnerScreenX: window.mozInnerScreenX, mozInnerScreenY: window.mozInnerScreenY, devicePixelRatio: window.devicePixelRatio,
  screen: {width: screen.width, height: screen.height, availWidth: screen.availWidth, availHeight: screen.availHeight,
           availLeft: screen.availLeft, availTop: screen.availTop, pixelDepth: screen.pixelDepth},
  events: {count: window.__m.count, dx: window.__m.dx, dy: window.__m.dy},
  probe: JSON.parse(window.__probe()),
});

// A real click on #fs enters element fullscreen and a real click on #open opens the popup: Marionette cannot give a page the
// user activation either needs, so the capture clicks them with SendInput. The popup is opened with the url and the features the
// capture stored in window.__popupUrl and window.__popupFeatures.
window.__fsError = null;
window.__popup = null;
addEventListener("DOMContentLoaded", () => {
  const fs = document.getElementById("fs"), open = document.getElementById("open");
  if (fs) fs.addEventListener("click", () => {
    document.documentElement.requestFullscreen().catch((e) => { window.__fsError = String(e); });
  });
  if (open) open.addEventListener("click", () => {
    window.__popup = window.open(window.__popupUrl, "_blank", window.__popupFeatures);
  });
});

// Iframes. The main page adds a same-origin and a cross-site iframe (window.__addFrames) and each frame page, which loads this
// same script, says hello to its parent and answers "reset" (forget the mouse events) and "record" (its __state) by postMessage.
// The parent keeps the hellos in window.__frameHello and the records in window.__frameRecords, by frame name.
window.__frameHello = {};
window.__frameRecords = {};
const FRAME_BOXES = {same: {left: 10, top: 60}, cross: {left: 10, top: 200}};
window.__addFrames = (sameUrl, crossUrl) => {
  window.__removeFrames();
  window.__frameHello = {};
  window.__frameRecords = {};
  for (const [name, url] of [["same", sameUrl], ["cross", crossUrl]]) {
    const frame = document.createElement("iframe");
    frame.id = "frame-" + name;
    frame.src = url;
    const box = FRAME_BOXES[name];
    frame.style.cssText = `position:fixed;left:${box.left}px;top:${box.top}px;width:300px;height:120px;border:0;margin:0;padding:0`;
    document.body.appendChild(frame);
  }
};
window.__removeFrames = () => {
  for (const name of ["same", "cross"]) {
    const frame = document.getElementById("frame-" + name);
    if (frame) frame.remove();
  }
};
// Iframes under a CSS transform: one container (fixed at left 10, top 200, 300 x 120, transform-origin 0 0) holding one iframe of
// the same size, the transform on the container. One at a time (two at the same place would cover each other), named
// <origin>_<transform>; the frame page is the same surface.html and answers by the name in its url.
window.__addTransformed = (name, url, transform) => {
  window.__removeTransformed();
  delete window.__frameHello[name];
  delete window.__frameRecords[name];
  const box = document.createElement("div");
  box.id = "tf-container";
  box.style.cssText = `position:fixed;left:10px;top:200px;width:300px;height:120px;margin:0;padding:0;transform:${transform};transform-origin:0 0`;
  const frame = document.createElement("iframe");
  frame.id = "frame-" + name;
  frame.src = url;
  frame.style.cssText = "position:absolute;left:0;top:0;width:300px;height:120px;border:0;margin:0;padding:0";
  box.appendChild(frame);
  document.body.appendChild(box);
};
window.__removeTransformed = () => {
  const box = document.getElementById("tf-container");
  if (box) box.remove();
};
// The container's rectangle (its bounding box after the transform), its place before it, and the matrix of the transform.
window.__transformedGeometry = () => {
  const box = document.getElementById("tf-container");
  const r = box.getBoundingClientRect();
  const m = new DOMMatrix(getComputedStyle(box).transform);
  return {rect: {x: r.x, y: r.y, width: r.width, height: r.height}, left: box.offsetLeft, top: box.offsetTop,
          width: box.offsetWidth, height: box.offsetHeight, matrix: [m.a, m.b, m.c, m.d, m.e, m.f],
          viewport: [window.innerWidth, window.innerHeight]};
};
window.__askTransformed = (name, cmd) => {
  const frame = document.getElementById("frame-" + name);
  if (frame && frame.contentWindow) {
    frame.contentWindow.postMessage(JSON.stringify({source: "geometry-parent", cmd: cmd, moves: true}), "*");
  }
};
window.__askFrames = (cmd) => {
  for (const name of ["same", "cross"]) {
    const frame = document.getElementById("frame-" + name);
    if (frame && frame.contentWindow) frame.contentWindow.postMessage(JSON.stringify({source: "geometry-parent", cmd: cmd}), "*");
  }
};
if (window.parent !== window) {
  const name = new URLSearchParams(location.search).get("name");
  const say = (message) => window.parent.postMessage(JSON.stringify(Object.assign({source: "geometry-frame", name: name}, message)), "*");
  addEventListener("message", (e) => {
    let data = null;
    try { data = JSON.parse(e.data); } catch (_) { return; }
    if (!data || data.source !== "geometry-parent") return;
    if (data.cmd === "reset") { window.__reset(); say({kind: "reset"}); }
    if (data.cmd === "record") {
      const state = JSON.parse(window.__state());
      if (data.moves) state.moves = window.__moves;
      say({kind: "record", state: state, early: window.__early});
    }
  });
  say({kind: "hello"});
} else {
  addEventListener("message", (e) => {
    let data = null;
    try { data = JSON.parse(e.data); } catch (_) { return; }
    if (!data || data.source !== "geometry-frame") return;
    if (data.kind === "hello") window.__frameHello[data.name] = true;
    // The frame's record carries what the frame read in its first moments (surface.html's inline script): `early`.
    if (data.kind === "record") window.__frameRecords[data.name] = Object.assign({}, data.state, {early: data.early});
  });
}
