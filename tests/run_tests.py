#!/usr/bin/python3
"""Milestone gate tests for glbviewer (generic viewer + compressor).

Usage:  /usr/bin/python3 run_tests.py [m1|m2|m3|m4|all]
Exits non-zero on any failure.  Uses Playwright chromium (swiftshader).
"""
import base64, io, json, pathlib, socket, struct, subprocess, sys, threading, time
import http.server, functools

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent                      # glbviewer/
ROOT = REPO.parent                      # roomscan/
VIEWER = REPO / 'index.html'
COMPRESS = REPO / 'compress.html'

import playwright.sync_api as pw_api
import sys as _sys
_sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from PIL import Image

RESULTS = []

def check(name, cond, detail=''):
    RESULTS.append((name, bool(cond), detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f'  ({detail})' if detail and not cond else ''))
    return bool(cond)

def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p

class T:
    """Playwright page that can serve local files via routes."""
    def __init__(self, b, port, url, mobile=False, render_mode=True):
        self.errs = []
        if mobile:
            ctx = b.new_context(viewport={'width': 390, 'height': 844}, has_touch=True, is_mobile=True)
            self.pg = ctx.new_page()
        else:
            self.pg = b.new_page(viewport={'width': 1024, 'height': 576})
        self.pg.on('pageerror', lambda e: self.errs.append(str(e)))
        if render_mode:
            self.pg.add_init_script('window.__renderMode = 1;')
        # serve roomscan.glb + any local file via a virtual route
        self.pg.route('**/localfile/**', self._serve)
        if url.startswith('http'):
            nc = 'nc=' + str(__import__('time').time_ns())
            if '#' in url:
                pre, post = url.split('#', 1)
                url = pre + ('&' if '?' in pre else '?') + nc + '#' + post
            else:
                url = url + ('&' if '?' in url else '?') + nc
        self.pg.goto(url)
        self.port = port

    def _serve(self, route):
        path = route.request.url.split('/localfile/')[-1].split('?')[0]
        f = (pathlib.Path('/tmp') if path.startswith('tmp/') else ROOT) / (path[len('tmp/'):] if path.startswith('tmp/') else path)
        if f.exists():
            route.fulfill(path='image/png' if f.suffix in ('.png',) else 'model/gltf-binary',
                          body=f.read_bytes())
        else:
            route.fulfill(status=404, body=b'not found')

    def load_model(self, fname='roomscan.glb'):
        self.pg.evaluate(f'''async () => {{
            const r = await fetch('http://127.0.0.1:{self.port}/localfile/{fname}');
            const b = new Uint8Array(await r.arrayBuffer());
            window.__loadModel(b, '{fname}');
        }}''')

    def ready(self, timeout=180):
        self.pg.wait_for_function('window.__readyFrames >= 4', timeout=timeout * 1000)
        self.pg.wait_for_timeout(300)

def screenshot_stats(pg):
    """mean luminance + std (0-255) of the rendered canvas"""
    raw = pg.screenshot()
    im = Image.open(io.BytesIO(raw)).convert('L')
    px = list(im.getdata())
    n = len(px)
    mean = sum(px) / n
    var = sum((p - mean) ** 2 for p in px) / n
    return mean, var ** 0.5

# ---------------------------------------------------------------- minimal GLB
def tiny_glb():
    """Tiny valid GLB: one quad (2 tris), white, 1m square on y=0."""
    pos = [0,0,0, 1,0,0, 1,0,1, 0,0,1]
    uv  = [0,0, 1,0, 1,1, 0,1]
    idx = [0,1,2, 0,2,3]
    binbuf = struct.pack('<12f', *pos) + struct.pack('<8f', *uv) + struct.pack('<6I', *idx)
    def acc(b, c, ty, n, minv=None, maxv=None):
        a = {"bufferView": b, "componentType": c, "type": ty, "count": n}
        if minv is not None: a["min"] = minv; a["max"] = maxv
        return a
    j = {
        "asset": {"version": "2.0", "generator": "test"},
        "scenes": [{"nodes": [0]}], "scene": 0,
        "nodes": [{"mesh": 0, "name": "tiny"}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "TEXCOORD_0": 1},
                                    "indices": 2, "material": 0}]}],
        "materials": [{"name": "w", "pbrMetallicRoughness": {"baseColorFactor": [1,1,1,1], "metallicFactor": 0, "roughnessFactor": 1}}],
        "accessors": [
            acc(0, 5126, "VEC3", 4, [0,0,0], [1,0,1]),
            acc(1, 5126, "VEC2", 4),
            acc(2, 5125, "SCALAR", 6)],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0,  "byteLength": 48, "target": 34962},
            {"buffer": 0, "byteOffset": 48, "byteLength": 32, "target": 34962},
            {"buffer": 0, "byteOffset": 80, "byteLength": 24, "target": 34963}],
        "buffers": [{"byteLength": 104}],
    }
    jb = json.dumps(j).encode()
    while len(jb) % 4: jb += b' '
    bb = binbuf
    while len(bb) % 4: bb += b'\0'
    total = 12 + 8 + len(jb) + 8 + len(bb)
    return (struct.pack('<III', 0x46546C67, 2, total)
          + struct.pack('<II', len(jb), 0x4E4F534A) + jb
          + struct.pack('<II', len(bb), 0x004E4942) + bb)

# ---------------------------------------------------------------- milestones
def m1(b, port):
    print('== M1: viewer shell + model loading ==')
    url = f'http://127.0.0.1:{port}/index.html'

    # 1. loads without a model
    t = T(b, port, url)
    t.pg.wait_for_timeout(800)
    check('M1.1 page loads, no model',
          t.pg.evaluate('!window.__modelReady') and t.pg.evaluate('document.getElementById("drop").style.display !== "none"'),
          f'errs={t.errs[:2]}')
    check('M1.1b no console errors', not t.errs, str(t.errs[:2]))
    t.pg.close()

    # 2. load real roomscan.glb via test hook
    t = T(b, port, url)
    t.load_model('roomscan.glb')
    t.ready()
    check('M1.2 model loaded via hook', t.pg.evaluate('window.__modelReady === true'), f'errs={t.errs[:2]}')
    check('M1.3 drop panel hidden', t.pg.evaluate('document.getElementById("drop").style.display === "none"'))
    stats = t.pg.evaluate('({fy: FLOOR_Y, tris: modelTris, verts: modelVerts, name: modelName, mode})')
    check('M1.4 floor estimate sane (0 <= FLOOR_Y <= 2)', 0 <= stats['fy'] <= 2, f'FLOOR_Y={stats["fy"]:.3f}')
    check('M1.5 stats collected', stats['tris'] > 100000 and stats['verts'] > 200000,
          f'tris={stats["tris"]:.0f} verts={stats["verts"]}')
    check('M1.6 mode is orbit after load', stats['mode'] == 'orbit', stats['mode'])
    m, s = screenshot_stats(t.pg)
    check('M1.7 renders non-blank', s > 20, f'mean={m:.1f} std={s:.1f}')
    check('M1.7b not overexposed', m < 245, f'mean={m:.1f}')
    dist = t.pg.evaluate('camera.position.distanceTo(orbit.target)')
    check('M1.8 orbit framed on model', 10 < dist < 16, f'dist={dist:.2f}')
    coords = t.pg.evaluate('document.getElementById("coords").textContent')
    check('M1.9 coords show model info', 'roomscan.glb' in coords and 'tris' in coords, coords[:100])
    t.pg.close()

    # 3. tiny GLB via #glb= deep link
    tglb = tiny_glb()
    hash_b64 = base64.b64encode(tglb).decode()
    t = T(b, port, url + '#glb=' + hash_b64)
    t.ready()
    ok = t.pg.evaluate('window.__modelReady === true && modelTris === 2')
    check('M1.10 #glb= deep link loads', ok, f'errs={t.errs[:2]}')
    t.pg.close()
    return T  # keep T class handy

def m2(b, port):
    print('== M2: full feature set on loaded model ==')
    url = f'http://127.0.0.1:{port}/index.html'
    t = T(b, port, url)
    t.load_model('roomscan.glb')
    t.ready()
    check('M2.0 no console errors after load', not t.errs, str(t.errs[:2]))

    # walk mode: WASD (keys force-reset after every press: synthetic keyup can be
    # dropped under CPU load and a stale key poisons the next subtest)
    t.pg.evaluate("setMode('walk'); __resetKeys();")
    t.pg.wait_for_timeout(400)
    K = lambda key, ms: (t.pg.keyboard.down(key), t.pg.wait_for_timeout(ms),
                         t.pg.keyboard.up(key), t.pg.evaluate('__resetKeys()'),
                         t.pg.wait_for_timeout(150))
    P = lambda: t.pg.evaluate('({x: pos.x, y: pos.y, z: pos.z})')
    p0 = P()
    K('KeyW', 1500)
    p1 = P()
    mv = abs(p1['x'] - p0['x']) + abs(p1['z'] - p0['z'])
    check('M2.1 W moves forward', mv > 0.3, f'moved={mv:.3f}')
    K('KeyD', 1000)
    p2 = P()
    check('M2.2 D strafes', abs(p2['x'] - p1['x']) + abs(p2['z'] - p1['z']) > 0.1,
          f'moved={abs(p2["x"] - p1["x"]) + abs(p2["z"] - p1["z"]):.3f}')

    # Q / E vertical
    t.pg.evaluate('velY = 0; pos.y = FLOOR_Y + EYE + 1.0'); t.pg.wait_for_timeout(200)
    a = t.pg.evaluate('pos.y')
    K('KeyQ', 1200)
    yq = t.pg.evaluate('pos.y')
    check('M2.3 Q descends', yq < a - 0.25, f'{a:.3f} -> {yq:.3f}')
    a = t.pg.evaluate('pos.y')
    K('KeyE', 1200)
    ye = t.pg.evaluate('pos.y')
    check('M2.4 E ascends', ye > a + 0.25, f'{a:.3f} -> {ye:.3f}')

    # jump with gravity
    t.pg.evaluate('velY = 0; pos.y = FLOOR_Y + EYE'); t.pg.wait_for_timeout(200)
    K('Space', 250)
    yair = t.pg.evaluate('pos.y')
    t.pg.wait_for_function('pos.y <= FLOOR_Y + EYE + 0.05', timeout=8000)
    yland = t.pg.evaluate('pos.y')
    floor_v = t.pg.evaluate('FLOOR_Y + EYE')
    check('M2.5 jump rises and lands on floor',
          yair > floor_v + 0.3 and abs(yland - floor_v) < 0.05,
          f'air={yair:.3f} land={yland:.3f} floor={floor_v:.3f}')

    # lighting preset
    t.pg.evaluate('applyLighting(2)')
    check('M2.6 preset switch', t.pg.evaluate("LIGHT_PRESETS[lightIdx].name") == 'Night',
          t.pg.evaluate("LIGHT_PRESETS[lightIdx].name"))

    # grading standard active from first frame
    g = t.pg.evaluate('({l: gLift, ga: gGain, g: gGamma, s: gSat})')
    check('M2.7 standard look at boot',
          abs(g['l'] - 0.145) < 1e-6 and abs(g['ga'] - 0.860) < 1e-6
          and abs(g['g'] - 1.150) < 1e-6 and abs(g['s'] - 0.74) < 1e-6, str(g))

    # screenshot + pose downloads
    with t.pg.expect_download() as dl:
        t.pg.keyboard.press('KeyF')
    check('M2.8 screenshot downloads', dl.value.suggested_filename.endswith('.png'),
          dl.value.suggested_filename)
    with t.pg.expect_download() as dl2:
        t.pg.keyboard.press('KeyP')
    txt = pathlib.Path(dl2.value.path()).read_text()
    check('M2.9 pose downloads with coords', 'pos: x:' in txt and 'yaw:' in txt and 'mode:' in txt, txt[:60])
    t.pg.close()

    # touch UI on mobile
    tm = T(b, port, url, mobile=True, render_mode=False)
    tm.load_model('roomscan.glb')
    tm.ready()
    ui = tm.pg.evaluate('({j: !!document.getElementById("t-joy"), q: !!document.getElementById("t-q"), e: !!document.getElementById("t-e"), jn: !!document.getElementById("t-jump")})')
    check('M2.10 touch UI on mobile', all(ui.values()), str(ui))
    x0 = tm.pg.evaluate('pos.x'); z0 = tm.pg.evaluate('pos.z')
    tm.pg.evaluate("setMode('walk')")
    tm.pg.wait_for_timeout(150)
    tm.pg.evaluate('''() => {
        const j = document.getElementById('t-joy');
        const r = j.getBoundingClientRect();
        j.dispatchEvent(new PointerEvent('pointerdown', {pointerId: 9, pointerType: 'touch', clientX: r.left + r.width/2, clientY: r.top + r.height/2, bubbles: true}));
        j.dispatchEvent(new PointerEvent('pointermove', {pointerId: 9, pointerType: 'touch', clientX: r.left + r.width/2, clientY: r.top + 10, bubbles: true}));
    }''')
    tm.pg.wait_for_timeout(400)
    tm.pg.evaluate('''() => {
        const j = document.getElementById('t-joy');
        const r = j.getBoundingClientRect();
        j.dispatchEvent(new PointerEvent('pointerup', {pointerId: 9, pointerType: 'touch', clientX: r.left + r.width/2, clientY: r.top + r.height/2, bubbles: true}));
    }''')
    x1 = tm.pg.evaluate('pos.x'); z1 = tm.pg.evaluate('pos.z')
    check('M2.11 joystick moves player', abs(x1 - x0) + abs(z1 - z0) > 0.1)
    tm.pg.close()

# ---------------------------------------------------------------- main
def main():
    which = sys.argv[1] if len(sys.argv) > 1 else 'm1'
    port = free_port()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(REPO))
    th = threading.Thread(target=lambda: http.server.ThreadingHTTPServer(('127.0.0.1', port), handler).serve_forever(), daemon=True)
    th.start()

    with pw_api.sync_playwright() as pw:
        b = pw.chromium.launch(args=['--enable-unsafe-swiftshader'])
        try:
            if which in ('m1', 'all'): m1(b, port)
            if which in ('m2', 'all'): m2(b, port)
            if which in ('m3', 'all'):
                from test_m3 import m3 as _m3
                _m3(b, port, T, check, screenshot_stats, ROOT)
            if which in ('m4', 'all'):
                from test_m4 import m4 as _m4
                _m4(b, port, T, check, screenshot_stats, ROOT)
        finally:
            b.close()

    fails = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} passed")
    if fails:
        print('FAILURES:', [f[0] for f in fails])
        sys.exit(1)

if __name__ == '__main__':
    main()
