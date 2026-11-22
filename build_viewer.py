#!/usr/bin/python3
"""Build glbviewer/index.html (generic GLB viewer) from the roomscan root index.html.

Deterministic text transform; asserts every replacement lands exactly once.
"""
import pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent / 'index.html'
OUT  = pathlib.Path(__file__).resolve().parent / 'index.html'

src = ROOT.read_text()

def rep(old, new, s, count=1):
    n = s.count(old)
    assert n == count, f'expected {count} occurrence(s), found {n}: {old[:80]!r}'
    return s.replace(old, new)

# ---------- titles / names ----------
src = rep('<title>RoomScan 3D Viewer</title>', '<title>GLB 3D Viewer</title>', src)
src = rep('<h1>RoomScan Viewer</h1>', '<h1>GLB Viewer</h1>', src)
src = rep("a.download = fileTs() + '-roomscan-view.png';",  "a.download = fileTs() + '-glb-view.png';",  src)
src = rep("a.download = fileTs() + '-roomscan-pose.txt';",  "a.download = fileTs() + '-model-pose.txt';", src)
src = rep("a.download = fileTs() + '-roomscan-grade.cube';","a.download = fileTs() + '-glb-grade.cube';", src)

# ---------- HTML: loading + drop panel ----------
DROP_PANEL = '''<div id="loading">Loading&hellip;</div>
<div id="drop">
  <h1>GLB 3D Viewer</h1>
  <p>Drop a <b>.glb</b> file anywhere, or <label class="pick">browse&hellip;<input type="file" id="file" accept=".glb,.gltf" hidden></label></p>
  <p class="sub">1 walk &middot; 2 orbit &middot; Q/E down/up &middot; L grading &middot; middle-drag / pinch = brightness</p>
</div>'''
src = rep('<div id="loading">Loading model&hellip;</div>', DROP_PANEL, src)

# CSS for drop panel (append to existing style block: insert before </style>)
DROP_CSS = '''
    #drop { position: fixed; inset: 0; z-index: 40; display: flex; flex-direction: column;
      align-items: center; justify-content: center; text-align: center;
      background: rgba(10,12,18,.55); backdrop-filter: blur(3px); }
    #drop h1 { font-size: 28px; margin-bottom: 8px; }
    #drop p.sub { opacity: .65; font-size: 12px; }
    #drop .pick { color: #7fb2ff; cursor: pointer; text-decoration: underline; }
'''
src = rep('</style>', DROP_CSS + '</style>', src)

# ---------- remove embedded model ----------
# strip the giant base64 constant + decoder + MODEL_BYTES
src = re.sub(r'/\* The 3D model \(roomscan\.glb\) embedded as base64 \*/\nconst MODEL_B64 = "[A-Za-z0-9+/=]+";',
             '/* generic viewer: model is loaded by the user (drop / browse / #glb= deep link) */', src)
assert src.count('const MODEL_B64') == 0
src = rep('''function b64ToBytes(b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}
const MODEL_BYTES = b64ToBytes(MODEL_B64);
''', 'function bytesFromB64(b64) {\n  const bin = atob(b64);\n  const u = new Uint8Array(bin.length);\n  for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i);\n  return u;\n}\n', src)

# ---------- replace loader block with generic loader ----------
GENERIC_LOADER = '''// --- Generic GLB loading (drop / browse / deep link / test hook) ---
const loader = new THREE.GLTFLoader();
let model = null;
let FLOOR_Y = 0;                 // estimated floor height (world units)
let modelTris = 0, modelVerts = 0, modelName = '';
let modelCenter = new THREE.Vector3();

const dropEl = document.getElementById('drop');
const fileEl = document.getElementById('file');
fileEl.addEventListener('change', () => {
  const f = fileEl.files[0];
  if (f) f.arrayBuffer().then(b => loadGLB(new Uint8Array(b), f.name));
});
window.addEventListener('dragover', (e) => e.preventDefault());
window.addEventListener('drop', (e) => {
  e.preventDefault();
  const f = e.dataTransfer.files[0];
  if (f) f.arrayBuffer().then(b => loadGLB(new Uint8Array(b), f.name));
});
if (location.hash.startsWith('#glb=')) {
  try {
    loadGLB(bytesFromB64(decodeURIComponent(location.hash.slice(5))), 'linked-model.glb');
    history.replaceState(null, '', location.pathname);
  } catch (err) { console.warn('hash model failed', err); }
}
window.__loadModel = (u8, name) => loadGLB(u8, name);

function estimateFloorY(m) {
  const box = new THREE.Box3().setFromObject(m);
  if (box.isEmpty()) return 0;
  const ys = [];
  const v = new THREE.Vector3();
  let sampled = 0;
  m.traverse(o => {
    if (!o.isMesh) return;
    const p = o.geometry.attributes.position;
    if (!p) return;
    const step = Math.max(1, Math.floor(p.count / 2000));
    for (let i = 0; i < p.count && sampled < 60000; i += step) {
      v.fromBufferAttribute(p, i).applyMatrix4(o.matrixWorld);
      ys.push(v.y); sampled++;
    }
  });
  if (!ys.length) return box.min.y;
  ys.sort((a, b) => a - b);
  const lo = ys[0], hi = ys[Math.floor(ys.length * 0.4)];
  const bins = 32, counts = new Array(bins).fill(0);
  const span = (hi - lo) || 1e-9;
  for (const y of ys) if (y <= hi) {
    const b = Math.min(bins - 1, Math.floor((y - lo) / span * bins));
    counts[b]++;
  }
  let best = 0;
  for (let i = 1; i < bins; i++) if (counts[i] > counts[best]) best = i;
  return lo + (best + 0.5) * span / bins;
}

function loadGLB(bytes, name) {
  loading.textContent = 'Loading\\u2026';
  loading.style.display = 'block';
  loader.parse(bytes.buffer, '', (gltf) => {
    if (model) scene.remove(model);
    const m = gltf.scene;
    model = m;
    scene.add(m);

    // Fit: 8-unit span, centered x/z, base at y=0
    const box = new THREE.Box3().setFromObject(m);
    const size = box.getSize(new THREE.Vector3());
    const maxDim = Math.max(size.x, size.y, size.z) || 1;
    m.scale.setScalar(8 / maxDim);
    const box2 = new THREE.Box3().setFromObject(m);
    const c2 = box2.getCenter(new THREE.Vector3());
    m.position.x -= c2.x; m.position.z -= c2.z; m.position.y -= box2.min.y;
    m.updateMatrixWorld(true);

    // Materials: GLTF defaults missing metallicFactor to 1.0 which kills closed-room lighting
    modelTris = 0; modelVerts = 0;
    m.traverse(o => {
      if (o.isMesh) {
        o.castShadow = true; o.receiveShadow = true;
        const mat = o.material;
        if (mat) { mat.metalness = 0; mat.roughness = 1.0; mat.needsUpdate = true; }
        const g = o.geometry;
        modelTris  += (g.index ? g.index.count : g.attributes.position.count) / 3;
        modelVerts += g.attributes.position.count;
      }
    });
    modelName = name || 'model';

    // Floor + spawn
    FLOOR_Y = estimateFloorY(m);
    const box3 = new THREE.Box3().setFromObject(m);
    modelCenter = box3.getCenter(new THREE.Vector3());
    pos.set(0, FLOOR_Y + EYE, 0);
    velY = 0;
    yaw = Math.PI; pitch = 0;
    camera.position.copy(pos);
    camera.quaternion.setFromEuler(new THREE.Euler(pitch, yaw, 0, 'YXZ'));

    // Orbit-framed default view (next frame: OrbitControls script block runs after this one)
    requestAnimationFrame(() => {
      if (mode !== 'orbit') setMode('orbit');
      if (!orbit) orbit = new THREE.OrbitControls(camera, renderer.domElement);
      orbit.target.copy(modelCenter);
      camera.position.copy(modelCenter).add(new THREE.Vector3(0.55, 0.4, 1).normalize().multiplyScalar(13));
      orbit._sph.setFromVector3(camera.position.clone().sub(orbit.target));
      orbit.update();
      orbit.enabled = true;
    });

    started = true;
    dropEl.style.display = 'none';
    loading.style.display = 'none';
    overlay.style.display = window.__renderMode ? 'none' : (started ? 'none' : 'flex');
    if (!window.__renderMode) { hud.style.display = 'block'; coordsEl.style.display = 'block'; }
    window.__modelReady = true;
  }, (err) => {
    loading.textContent = 'Failed to parse GLB.';
    console.error(err);
  });
}
'''

start = src.index('// --- Load model (from embedded bytes, no network) ---')
end   = src.index('}, 50);') + len('}, 50);')
src = src[:start] + GENERIC_LOADER + src[end:]

# ---------- mode: orbit-first ----------
src = rep("let mode = 'walk';            // 'walk' | 'orbit'",
          "let mode = 'orbit';           // 'walk' | 'orbit' (orbit-first for generic models)", src)

# ---------- walk floor from estimate ----------
src = rep('const FLOOR = 1.072 + EYE, GRAV = 22, JUMP_V = 7.5;  // 1.072 = real floor height in viewer units',
          'const FLOOR = FLOOR_Y + EYE, GRAV = 22, JUMP_V = 7.5;', src)

# ---------- vertical movement: E/Q velocity override (gravity-free while held) ----------
VMOV_OLD = """

    // Jump with gravity (Space) — Q/E vertical climb removed (v3: felt like a stuck jump)
    const FLOOR = FLOOR_Y + EYE, GRAV = 22, JUMP_V = 7.5;
    if (keys['Space'] && pos.y <= FLOOR + 0.05 && velY >= 0) velY = JUMP_V;
    velY -= GRAV * dt;
    pos.y += velY * dt;
    if (pos.y < FLOOR) { pos.y = FLOOR; velY = 0; }
"""
VMOV_NEW = """

    // Jump with gravity (Space) — Q/E vertical climb removed (v3: felt like a stuck jump)
    const FLOOR = FLOOR_Y + EYE, GRAV = 22, JUMP_V = 7.5;
    if (keys['Space'] && pos.y <= FLOOR + 0.05 && velY >= 0) velY = JUMP_V;
    velY -= GRAV * dt;
    pos.y += velY * dt;
    if (pos.y < FLOOR) { pos.y = FLOOR; velY = 0; }
"""
src = rep(VMOV_OLD, VMOV_NEW, src)


# ---------- overlay click: generic (orbit needs no pointer lock) ----------
src = rep('''overlay.addEventListener('click', () => {
  if (mode === 'walk' && !pointerLocked) {
    if (IS_TOUCH) { started = true; overlay.style.display = 'none'; }
    else canvas.requestPointerLock();
  }
});''',
'''overlay.addEventListener('click', () => {
  if (mode === 'walk' && !pointerLocked) {
    if (IS_TOUCH) { started = true; overlay.style.display = 'none'; }
    else canvas.requestPointerLock();
  } else {
    started = true; overlay.style.display = 'none';
  }
});''', src)

# ---------- coords bar: model info ----------
src = rep('''    `lighting: ${LIGHT_PRESETS[lightIdx].name} ${Math.round(brightK * 100)}%   |   mode: ${mode}   |   ` +
    (IS_TOUCH ? 'buttons: MENU / SHOT' : 'F: screenshot   P: save pose');''',
'''    `lighting: ${LIGHT_PRESETS[lightIdx].name} ${Math.round(brightK * 100)}%   |   mode: ${mode}${model ? ' \\u00b7 ' + modelName + ' ' + (modelTris / 1000).toFixed(0) + 'k tris' : ''}   |   ` +
    (IS_TOUCH ? 'buttons: MENU / SHOT' : 'F: screenshot   P: save pose');''', src)

# ---------- script order: OrbitControls fallback must precede the app ----------
m = re.search(r'<script>\n/\* Minimal OrbitControls fallback.*?</script>', src, re.S)
assert m, 'fallback block not found'
fallback = m.group(0)
src = src.replace(fallback, '')
anchor = '<script>\n/* generic viewer: model is loaded by the user (drop / browse / #glb= deep link) */\n</script>'
assert src.count(anchor) == 1
src = src.replace(anchor, anchor + '\n' + fallback)

OUT.write_text(src)
print(f'wrote {OUT} ({len(src) / 1024:.0f} KB)')
