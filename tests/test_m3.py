"""M3 tests: GLB compressor."""
import json as J
import os
import pathlib
import struct


def validate_glb(path):
    """Structural GLB validation; returns (ok, detail, json)."""
    d = pathlib.Path(path).read_bytes()
    magic, ver, total = struct.unpack_from('<III', d, 0)
    if magic != 0x46546C67:
        return False, 'bad magic', None
    if ver != 2:
        return False, 'bad version', None
    if total != len(d):
        return False, f'header total {total} != {len(d)}', None
    off = 12
    j = None
    binlen = 0
    while off < len(d):
        ln, ct = struct.unpack_from('<II', d, off)
        if ct == 0x4E4F534A:
            j = J.loads(d[off + 8:off + 8 + ln].rstrip(b' \x00'))
        elif ct == 0x004E4942:
            binlen = ln
        else:
            return False, f'bad chunk type {ct}', None
        off += 8 + ln
    if j is None:
        return False, 'no json', None
    buf = j['buffers'][0]['byteLength']
    if buf != binlen:
        return False, f'buffer {buf} != bin {binlen}', None
    CB = {5126: 4, 5123: 2, 5125: 4, 5121: 1}
    QT = {'VEC3': 3, 'VEC2': 2, 'SCALAR': 1}
    for a in j.get('accessors', []):
        bv = j['bufferViews'][a['bufferView']]
        cb = CB[a['componentType']]
        comp = QT[a['type']]
        end = bv['byteOffset'] + a.get('byteOffset', 0) + a['count'] * comp * cb
        if end > binlen:
            return False, f'accessor overruns bin: {end} > {binlen}', j
    return True, f'{len(d)} bytes, {len(j["accessors"])} accessors', j


def m3(b, port, T, check, screenshot_stats, ROOT):
    print('== M3: compressor ==')
    url = f'http://127.0.0.1:{port}/compress.html'
    t = T(b, port, url)
    t.pg.wait_for_timeout(1500)
    status = t.pg.evaluate('document.getElementById("status").textContent')
    check('M3.1 compressor loads, meshopt ready',
          t.pg.evaluate('typeof MeshoptSimplifier.simplify === "function"') and 'Ready' in status,
          f'status={status!r} errs={t.errs[:2]}')
    check('M3.1b no console errors', not t.errs, str(t.errs[:2]))

    # E2E: file input -> compress -> download
    out_path = '/tmp/compress_out.glb'
    with t.pg.expect_download(timeout=300000) as dl:
        t.pg.set_input_files('#file', str(ROOT / 'roomscan.glb'))
        t.pg.wait_for_function(
            '["Compressing","Done","Failed"].some(s => document.getElementById("status").textContent.includes(s))',
            timeout=60000)
        t.pg.wait_for_timeout(200)
        t.pg.click('#save')
    path = dl.value.path()
    with open(path, 'rb') as f, open(out_path, 'wb') as g:
        g.write(f.read())
    stats = t.pg.evaluate('window.__getResult() && window.__getResult().stats')
    check('M3.2 compression ran', stats is not None and stats['outSize'] > 100000, str(stats))
    ratio = stats['outSize'] / stats['inSize']
    check('M3.3 output < 50% of input', ratio < 0.5,
          f'{stats["inSize"] // 1048576}MB -> {stats["outSize"] // 1048576}MB ({ratio:.0%})')
    check('M3.4 tris reduced (45% target)', 0.2 < stats['trisOut'] / stats['trisIn'] < 0.75,
          f'{stats["trisIn"]} -> {stats["trisOut"]}')
    ok, detail, j = validate_glb(out_path)
    check('M3.5 output is structurally valid GLB', ok, detail)
    if j:
        n16 = sum(1 for a in j['accessors']
                  if a['componentType'] == 5123 and a['type'] == 'VEC3' and a.get('normalized'))
        check('M3.6 small prim quantized to u16', n16 >= 1, f'{n16} u16 VEC3 accessors')
    t.pg.close()

    # render parity: compressed result in viewer vs original
    t2 = T(b, port, f'http://127.0.0.1:{port}/index.html')
    t2.pg.evaluate("async () => {"
                  f'const r = await fetch("http://127.0.0.1:{port}/localfile/tmp/compress_out.glb?v=" + Date.now(), {{cache: "no-store"}});'
                  'window.__loadModel(new Uint8Array(await r.arrayBuffer()), "compressed.glb");}')
    t2.ready()
    check('M3.7 compressed GLB loads in viewer', t2.pg.evaluate('window.__modelReady === true'), str(t2.errs[:2]))
    m2, s2 = screenshot_stats(t2.pg)
    t2.pg.close()
    t3 = T(b, port, f'http://127.0.0.1:{port}/index.html')
    t3.load_model('roomscan.glb')
    t3.ready()
    m1, s1 = screenshot_stats(t3.pg)
    t3.pg.close()
    check('M3.8 visual parity (luminance within 15)', abs(m1 - m2) < 15, f'orig={m1:.1f} comp={m2:.1f}')
    check('M3.9 both non-blank', s1 > 20 and s2 > 20, f'std {s1:.1f}/{s2:.1f}')
