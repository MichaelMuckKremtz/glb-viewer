#!/usr/bin/python3
"""Build glbviewer/compress.html — self-contained GLB compressor.

Inlines meshopt_simplifier.js (base64-embedded WASM, UMD) + a JS port of the
offline build.py pipeline (decimate -> remap -> u16 quantize w/ node-matrix
baking -> texture downscale -> GLB re-emit).
"""
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
MESHOPT = pathlib.Path('/private/tmp/litbuild/node_modules/meshoptimizer/meshopt_simplifier.js')

APP = r'''
// ================= GLB parsing =================
function parseGLB(bytes) {
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (dv.getUint32(0, true) !== 0x46546C67) throw new Error('not a GLB (bad magic)');
  if (dv.getUint32(4, true) !== 2) throw new Error('unsupported GLB version');
  let off = 12, bin = null, json = null;
  while (off + 8 <= dv.byteLength) {
    const len = dv.getUint32(off, true), type = dv.getUint32(off + 4, true);
    const data = new Uint8Array(bytes.buffer, bytes.byteOffset + off + 8, len);
    if (type === 0x4E4F534A) json = JSON.parse(new TextDecoder().decode(data));
    else if (type === 0x004E4942) bin = data;
    else throw new Error('unknown chunk type 0x' + type.toString(16));
    off += 8 + len;
  }
  if (!json) throw new Error('no JSON chunk');
  return { json, bin: bin || new Uint8Array(0) };
}
const CB = { 5126: 4, 5123: 2, 5125: 4, 5121: 1, 5127: 4, 5122: 2 };
const QT = { VEC3: 3, VEC2: 2, SCALAR: 1 };
function readAttr(json, bin, accessor) {
  const bv = json.bufferViews[accessor.bufferView];
  const c = QT[accessor.type] || 1;
  const cb = CB[accessor.componentType];
  const n = accessor.count * c;
  const off = bv.byteOffset + (accessor.byteOffset || 0);
  const src = bin.subarray(off, off + n * cb);
  if (accessor.componentType === 5126) return new Float32Array(src.buffer.slice(src.byteOffset, src.byteOffset + n * cb));
  if (accessor.componentType === 5123) return new Uint16Array(src.buffer.slice(src.byteOffset, src.byteOffset + n * cb));
  if (accessor.componentType === 5125) return new Uint32Array(src.buffer.slice(src.byteOffset, src.byteOffset + n * cb));
  throw new Error('unsupported componentType ' + accessor.componentType);
}

// ================= GLB re-emit =================
class GLBBuilder {
  constructor() { this.parts = []; this.off = 0; }
  push(u8) {
    if (this.off % 4) { const pad = 4 - (this.off % 4); this.parts.push(new Uint8Array(pad)); this.off += pad; }
    const bv = { buffer: 0, byteOffset: this.off, byteLength: u8.byteLength };
    this.parts.push(u8); this.off += u8.byteLength;
    return bv;
  }
  finish() {
    if (this.off % 4) { const pad = 4 - (this.off % 4); this.parts.push(new Uint8Array(pad)); this.off += pad; }
    return { parts: this.parts, total: this.off };
  }
}
function emitGLB(json, parts, total) {
  const jb = new TextEncoder().encode(JSON.stringify(json));
  const jarr = new Uint8Array(jb.length + ((4 - jb.length % 4) % 4));
  jarr.fill(0x20);  // GLTF spec: JSON chunk padded with spaces
  jarr.set(jb);
  const full = 12 + 8 + jarr.length + 8 + total;
  const out = new Uint8Array(full);
  const dv = new DataView(out.buffer);
  dv.setUint32(0, 0x46546C67, true); dv.setUint32(4, 2, true); dv.setUint32(8, full, true);
  dv.setUint32(12, jarr.length, true); dv.setUint32(16, 0x4E4F534A, true);
  out.set(jarr, 20);
  let o = 20 + jarr.length;
  dv.setUint32(o, total, true); dv.setUint32(o + 4, 0x004E4942, true); o += 8;
  for (const p of parts) { new Uint8Array(out.buffer, o, p.byteLength).set(new Uint8Array(p.buffer, p.byteOffset, p.byteLength)); o += p.byteLength; }  // raw byte copy
  return out;
}

// ================= helpers =================
function minMaxCol(vals, comp, c) {
  let mn = Infinity, mx = -Infinity;
  for (let i = c; i < vals.length; i += comp) { const v = vals[i]; if (v < mn) mn = v; if (v > mx) mx = v; }
  return [mn, mx];
}
function quantizeF32(vals, comp, ranges) {
  const out = new Uint16Array(vals.length);
  for (let c = 0; c < comp; c++) {
    const [mn, mx] = ranges[c], s = mx - mn;
    for (let i = c; i < vals.length; i += comp)
      out[i] = Math.min(65535, Math.max(0, Math.round(s < 1e-12 ? 32768 : (vals[i] - mn) / s * 65535)));
  }
  return out;
}
function pick(vals, comp, ids) {
  const out = new Float32Array(ids.length * comp);
  for (let i = 0; i < ids.length; i++) {
    const s = ids[i] * comp;
    for (let k = 0; k < comp; k++) out[i * comp + k] = vals[s + k];
  }
  return out;
}
function mulM(A, B) { // column-major M = A * B
  const O = new Array(16).fill(0);
  for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) {
    let s = 0;
    for (let k = 0; k < 4; k++) s += A[k * 4 + r] * B[c * 4 + k];
    O[c * 4 + r] = s;
  }
  return O;
}
function nodeMatrix(node) {
  if (node.matrix) return node.matrix.slice();
  const q = node.quaternion || [0, 0, 0, 1], p = node.translation || [0, 0, 0], sc = node.scale || [1, 1, 1];
  const [x, y, z, w] = q;
  return [
    sc[0] * (1 - 2 * y * y - 2 * z * z), sc[0] * (2 * x * y - 2 * z * w), sc[0] * (2 * x * z + 2 * y * w), 0,
    sc[1] * (2 * x * y + 2 * z * w), sc[1] * (1 - 2 * x * x - 2 * z * z), sc[1] * (2 * y * z - 2 * x * w), 0,
    sc[2] * (2 * x * z - 2 * y * w), sc[2] * (2 * y * z + 2 * x * w), sc[2] * (1 - 2 * x * x - 2 * y * y), 0,
    p[0], p[1], p[2], 1
  ];
}

// ================= compression pipeline =================
async function compressGLB(bytes, opts) {
  const t0 = performance.now();
  const { json, bin } = parseGLB(bytes);
  const quality = opts.quality != null ? opts.quality : 0.45;
  const texMax = opts.texMax != null ? opts.texMax : 2048;
  const newAcc = [], newBV = [];
  const build = new GLBBuilder();
  const newNodes = new Array(json.nodes.length);
  const newMeshes = new Array(json.meshes.length);
  const doneMesh = new Set();
  let vIn = 0, vOut = 0, tIn = 0, tOut = 0;

  // ---- textures (recompress to target size) ----
  const newImages = [];
  for (let i = 0; i < (json.images || []).length; i++) {
    const img = json.images[i];
    if (img.uri) { newImages.push(img); continue; }
    const bv = json.bufferViews[img.bufferView];
    const data = bin.slice(bv.byteOffset, bv.byteOffset + bv.byteLength);
    let out = data;
    if (texMax > 0) {
      try {
        const bmp = await createImageBitmap(new Blob([data], { type: img.mimeType || 'image/png' }));
        const w = bmp.width, h = bmp.height;
        if (Math.max(w, h) > texMax) {
          const sc = texMax / Math.max(w, h);
          const c = document.createElement('canvas');
          c.width = Math.max(1, Math.round(w * sc)); c.height = Math.max(1, Math.round(h * sc));
          c.getContext('2d').drawImage(bmp, 0, 0, c.width, c.height);
          const isJpeg = (img.mimeType || '').includes('jpeg');
          const blob = await new Promise(r => c.toBlob(r, isJpeg ? 'image/jpeg' : 'image/png', 0.85));
          if (blob) out = new Uint8Array(await blob.arrayBuffer());
        }
        bmp.close();
      } catch (e) { console.warn('texture downscale failed, keeping original', e); }
    }
    newImages.push({ mimeType: (img.mimeType || '').includes('jpeg') ? 'image/jpeg' : 'image/png', bufferView: newBV.length });
    newBV.push(build.push(out));
  }

  // ---- meshes (process in node order; a mesh is processed once) ----
  function processMesh(mi, node) {
    const mesh = json.meshes[mi];
    const nodeKids = !!(node && node.children && node.children.length);
    const canBake = mesh.primitives.length === 1 && !nodeKids;
    const newPrims = [];
    let bake = null;
    for (let pi = 0; pi < mesh.primitives.length; pi++) {
      const prim = mesh.primitives[pi];
      const posA = json.accessors[prim.attributes.POSITION];
      const comp = QT[posA.type] || 3;
      const posF32 = readAttr(json, bin, posA);
      const n = posF32.length / comp;
      vIn += n;

      // ---- fast path: not float32, skinned, or node has kids -> copy raw ----
      const copyThrough = posA.componentType !== 5126 || !!prim.attributes.JOINTS_0 || nodeKids;
      if (copyThrough) {
        vOut += n;
        if (prim.indices != null) tIn += tOut += json.accessors[prim.indices].count / 3;
        const np = { attributes: {}, mode: prim.mode || 4, material: prim.material };
        const copyAcc = (ai) => {
          const a = json.accessors[ai];
          const bv = json.bufferViews[a.bufferView];
          const cb = CB[a.componentType];
          const raw = bin.slice(bv.byteOffset + (a.byteOffset || 0), bv.byteOffset + (a.byteOffset || 0) + a.count * (QT[a.type] || 1) * cb);
          newBV.push(build.push(new Uint8Array(raw)));
          const na = { bufferView: newBV.length - 1, componentType: a.componentType, count: a.count, type: a.type };
          if (a.normalized) na.normalized = true;
          if (a.min) na.min = a.min; if (a.max) na.max = a.max;
          newAcc.push(na);
          return newAcc.length - 1;
        };
        for (const [name, ai] of Object.entries(prim.attributes)) np.attributes[name] = copyAcc(ai);
        if (prim.indices != null) np.indices = copyAcc(prim.indices);
        newPrims.push(np);
        continue;
      }

      // ---- decimate ----
      let idx32 = null, ids = null;
      if (prim.indices != null) {
        const iA = json.accessors[prim.indices];
        const raw = readAttr(json, bin, iA);
        idx32 = new Uint32Array(raw.length); idx32.set(raw);
        tIn += iA.count / 3;
        const target = Math.max(300, Math.floor(idx32.length * quality / 3) * 3);
        if (target < idx32.length - 3 && n > 500) {
          try {
            const [out] = MeshoptSimplifier.simplify(idx32.slice(), posF32.slice(), 3, Math.min(target, n * 3), 0.0008);
            if (out.length >= 3) {
              idx32 = new Uint32Array(out);
              const seen = new Set(); for (const i of idx32) seen.add(i);
              ids = [...seen];
            }
          } catch (e) { console.warn('simplify failed, keeping full mesh', e); }
        }
      }
      if (!ids) ids = Array.from({ length: n }, (_, i) => i);
      const nv = ids.length;
      vOut += nv;
      if (idx32) tOut += idx32.length / 3;

      // ---- remap attributes (list of [name, values, components]) ----
      const attrs = [];
      for (const [name, ai] of Object.entries(prim.attributes)) {
        const a = json.accessors[ai];
        const c = QT[a.type] || 1;
        const raw = readAttr(json, bin, a);
        attrs.push([name, (a.componentType === 5126) ? pick(raw, c, ids) : pick(new Float32Array(raw), c, ids), c]);
      }

      // ---- emit: indices ----
      const np = { attributes: {}, mode: prim.mode || 4, material: prim.material };
      if (idx32) {
        const iu16 = nv <= 65535;
        newBV.push(build.push(iu16 ? new Uint16Array(idx32) : idx32));
        newAcc.push({ bufferView: newBV.length - 1, componentType: iu16 ? 5123 : 5125, count: idx32.length, type: 'SCALAR' });
        np.indices = newAcc.length - 1;
      }
      // ---- emit: attributes (POSITION u16 + ranges; UV u16 if in [0,1]) ----
      const i16 = nv <= 65535 && canBake;
      for (const [name, valsIn, c] of attrs) {
        let vals = valsIn, u16 = false;
        if (name === 'POSITION' && i16) {
          const ranges = [];
          for (let k = 0; k < c; k++) ranges.push(minMaxCol(vals, c, k));
          vals = quantizeF32(vals, c, ranges);
          u16 = true;
          if (!bake) bake = { s: ranges.map(r => (r[1] - r[0]) || 1.0), t: ranges.map(r => r[0]) };
        } else if (name.startsWith('TEXCOORD') && c === 2 && i16) {
          let ok = true;
          for (let i = 0; i < vals.length; i++) if (vals[i] < -1e-6 || vals[i] > 1 + 1e-6) { ok = false; break; }
          if (ok) { vals = quantizeF32(vals, 2, [[0, 1], [0, 1]]); u16 = true; }
        }
        newBV.push(build.push(vals));
        const acc = { bufferView: newBV.length - 1, componentType: u16 ? 5123 : 5126, count: nv, type: c === 3 ? 'VEC3' : c === 2 ? 'VEC2' : 'SCALAR' };
        if (u16) acc.normalized = true;
        newAcc.push(acc);
        np.attributes[name] = newAcc.length - 1;
      }
      newPrims.push(np);
      if (bake && !bake.__done) { bake.__done = true; }
    }
    if (bake) delete bake.__done;
    return { prims: newPrims, bake };
  }

  const bakes = new Array(json.meshes.length).fill('unprocessed');
  for (let ni = 0; ni < json.nodes.length; ni++) {
    const node = json.nodes[ni];
    if (node.mesh == null) {
      const nn = { ...node };
      if (nn.children) nn.children = nn.children.slice();
      newNodes[ni] = nn;
      continue;
    }
    const mi = node.mesh;
    let bake = null;
    if (bakes[mi] === 'unprocessed') {
      const r = processMesh(mi, node);
      newMeshes[mi] = { primitives: r.prims };
      bakes[mi] = 'done';
      bake = r.bake;
    }
    const nn = { ...node };
    delete nn.translation; delete nn.rotation; delete nn.scale; delete nn.matrix;
    if (bake) nn.matrix = mulM(nodeMatrix(node), [bake.s[0], 0, 0, 0, 0, bake.s[1], 0, 0, 0, 0, bake.s[2], 0, bake.t[0], bake.t[1], bake.t[2], 1]);
    else nn.matrix = nodeMatrix(node);
    newNodes[ni] = nn;
  }

  const outJson = {
    asset: { version: '2.0', generator: 'glbviewer-compressor' },
    scene: json.scene, scenes: json.scenes,
    nodes: newNodes, meshes: newMeshes,
    materials: json.materials || [], textures: json.textures || [], samplers: json.samplers || [],
    images: newImages, accessors: newAcc, bufferViews: newBV,
    buffers: [{ byteLength: 0 }]
  };
  const fin = build.finish();
  outJson.buffers[0].byteLength = fin.total;
  const out = emitGLB(outJson, fin.parts, fin.total);
  return { bytes: out, stats: {
    inSize: bytes.length, outSize: out.length,
    vertsIn: vIn, vertsOut: vOut, trisIn: Math.round(tIn), trisOut: Math.round(tOut),
    ms: Math.round(performance.now() - t0)
  } };
}

// ================= UI =================
const $ = id => document.getElementById(id);
let lastResult = null, fileName = 'model.glb';
const fmt = n => n > 1048576 ? (n / 1048576).toFixed(2) + ' MB' : (n / 1024).toFixed(0) + ' KB';
window.__compressGLB = (u8, opts) => compressGLB(u8, opts);
window.__getResult = () => lastResult;

async function run(bytes, name) {
  fileName = name || 'model.glb';
  const q = parseFloat($('quality').value) / 100;   // slider is in percent
  const tex = parseInt($('texsize').value, 10);
  $('status').textContent = 'Compressing\u2026';
  $('btns').style.display = 'none';
  try {
    const r = await compressGLB(new Uint8Array(bytes), { quality: q, texMax: tex });
    lastResult = r;
    const s = r.stats;
    $('stats').innerHTML =
      `<tr><td>Size</td><td>${fmt(s.inSize)} \u2192 <b>${fmt(s.outSize)}</b></td></tr>` +
      `<tr><td>Reduction</td><td><b>${(100 * (1 - s.outSize / s.inSize)).toFixed(0)}%</b></td></tr>` +
      `<tr><td>Vertices</td><td>${s.vertsIn.toLocaleString()} \u2192 ${s.vertsOut.toLocaleString()}</td></tr>` +
      `<tr><td>Triangles</td><td>${s.trisIn.toLocaleString()} \u2192 ${s.trisOut.toLocaleString()}</td></tr>` +
      `<tr><td>Time</td><td>${(s.ms / 1000).toFixed(1)} s</td></tr>`;
    $('btns').style.display = 'flex';
    $('save').onclick = () => downloadGLB(r.bytes, fileName.replace(/\.glb$/i, '') + '-small.glb');
    $('openin').onclick = () => {
      if (r.bytes.length < 1500000) {
        const u = new Uint8Array(r.bytes);
        let bin = '';
        for (let i = 0; i < u.length; i += 0x8000) bin += String.fromCharCode.apply(null, u.subarray(i, i + 0x8000));
        window.open('index.html#glb=' + btoa(bin), '_blank');
      } else {
        window.open('index.html', '_blank');
        toast('Output is large — pick the file in the viewer');
      }
    };
    $('status').textContent = 'Done';
  } catch (e) {
    $('status').textContent = 'Failed: ' + e.message;
    console.error(e);
    $('btns').style.display = 'flex';
  }
}
function downloadGLB(bytes, name) {
  const blob = new Blob([bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength)], { type: 'model/gltf-binary' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}
function toast(msg) { $('status').textContent = msg; }
$('file').addEventListener('change', () => {
  const f = $('file').files[0]; if (f) f.arrayBuffer().then(b => run(b, f.name));
});
document.body.addEventListener('dragover', e => e.preventDefault());
document.body.addEventListener('drop', e => {
  e.preventDefault();
  const f = e.dataTransfer.files[0]; if (f) f.arrayBuffer().then(b => run(b, f.name));
});
$('go').onclick = () => { const f = $('file').files[0]; if (f) f.arrayBuffer().then(b => run(b, f.name)); else toast('Choose a file first'); };
$('quality').addEventListener('input', function () { $('qv').textContent = this.value + '%'; });
MeshoptSimplifier.ready.then(() => { $('status').textContent = 'Ready — drop a .glb file'; });
'''

HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover" />
<link rel="icon" type="image/svg+xml" href="logo.svg"><link rel="alternate icon" href="icon.png">
<title>GLB Compressor</title>
<style>
  html, body { margin: 0; min-height: 100%; background: #12141c; color: #e8ecf4;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
  .wrap { max-width: 560px; margin: 0 auto; padding: 24px 16px calc(24px + env(safe-area-inset-bottom)); }
  h1 { font-size: 22px; margin: 0 0 4px; }
  .sub { opacity: .6; font-size: 13px; margin-bottom: 16px; }
  #drop { border: 2px dashed #3a4356; border-radius: 14px; padding: 34px 16px; text-align: center; cursor: pointer; }
  #drop:hover { border-color: #6b8cff; }
  #drop .big { font-size: 16px; font-weight: 600; }
  #drop .small { opacity: .55; font-size: 12px; margin-top: 6px; }
  .row { display: flex; align-items: center; gap: 10px; margin: 12px 0; font-size: 14px; }
  .row label { width: 110px; opacity: .8; }
  .row input[type=range] { flex: 1; }
  .row select { flex: 1; background: #1c2130; color: #e8ecf4; border: 1px solid #3a4356; border-radius: 8px; padding: 6px; }
  button { background: #3550a8; color: #fff; border: 0; border-radius: 10px; padding: 10px 18px;
    font-size: 15px; font-weight: 600; cursor: pointer; }
  button:active { background: #4a68d8; }
  #status { margin: 10px 0; font-size: 14px; opacity: .85; min-height: 20px; }
  #btns { display: none; gap: 10px; margin-top: 8px; flex-wrap: wrap; }
  table { width: 100%; border-collapse: collapse; margin-top: 14px; font-size: 14px; }
  td { padding: 6px 4px; border-bottom: 1px solid #232939; }
  td:first-child { opacity: .6; width: 110px; }
  .note { opacity: .45; font-size: 12px; margin-top: 18px; line-height: 1.5; }
</style>
</head>
<body>
<div class="wrap">
  <h1>GLB Compressor</h1>
  <div class="sub">100% local &mdash; nothing is uploaded. Decimation + quantization + texture downscale.</div>
  <div id="drop" onclick="document.getElementById('file').click()">
    <div class="big">Drop a .glb file here, or click to browse</div>
    <div class="small">Works best on room scans &middot; typical: 14 MB &rarr; ~4 MB</div>
  </div>
  <input type="file" id="file" accept=".glb" hidden />
  <div class="row"><label>Quality</label><input type="range" id="quality" min="10" max="100" value="45" step="5"><span id="qv">45%</span></div>
  <div class="row"><label>Texture size</label>
    <select id="texsize">
      <option value="2048" selected>2048 px</option>
      <option value="1024">1024 px</option>
      <option value="512">512 px</option>
      <option value="0">Keep original</option>
    </select>
  </div>
  <button id="go">Compress</button>
  <div id="status">Loading&hellip;</div>
  <div id="btns"><button id="save">Save .glb</button><button id="openin">Open in viewer</button></div>
  <table id="stats"></table>
  <div class="note">Positions are stored as 16-bit (baked into node matrices) &mdash; open the result with the GLB Viewer
  (this repo) for best compatibility. Skinned meshes and multi-child nodes are kept at full precision automatically.</div>
</div>
<script>
__MESHOPT__
</script>
<script>
__APP__
</script>
</body>
</html>
'''

mopt = MESHOPT.read_text()
mopt = mopt.replace('export { MeshoptSimplifier };', '').strip()  # ESM -> classic script
assert 'MeshoptSimplifier' in mopt and not any(l.strip().startswith('export') for l in mopt.splitlines())
html = HTML.replace('__MESHOPT__', mopt).replace('__APP__', APP)
(HERE / 'compress.html').write_text(html)
print(f'wrote compress.html ({len(html) / 1024:.0f} KB)')
