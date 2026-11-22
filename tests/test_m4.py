"""M4 tests: polish & publish."""
import os
import pathlib


def m4(b, port, T, check, screenshot_stats, ROOT):
    print('== M4: polish & publish ==')

    # repo size
    repo = ROOT / 'glbviewer'
    total = sum(f.stat().st_size for f in repo.rglob('*') if f.is_file())
    check('M4.1 repo < 1.5 MB', total < 1.5 * 1048576, f'{total / 1048576:.2f} MB')
    check('M4.2 no model data in repo', not list(repo.rglob('*.glb')),
          str([str(f) for f in repo.rglob('*.glb')]))
    check('M4.3 README present', (repo / 'README.md').exists())

    # drag-and-drop (file input path) of a real file into the viewer
    url = f'http://127.0.0.1:{port}/index.html'
    t = T(b, port, url)
    t.pg.set_input_files('#file', str(ROOT / 'roomscan.glb'))
    t.ready()
    check('M4.4 file-input load works', t.pg.evaluate('window.__modelReady === true'), str(t.errs[:2]))
    check('M4.4b no console errors', not t.errs, str(t.errs[:2]))
    m, s = screenshot_stats(t.pg)
    check('M4.4c renders', s > 20, f'mean={m:.1f} std={s:.1f}')
    t.pg.close()

    # file:// smoke test (no server at all)
    t2 = T(b, port, (repo / 'index.html').as_uri())
    t2.pg.wait_for_timeout(1500)
    ok = t2.pg.evaluate('!!document.getElementById("drop") && !window.__modelReady')
    check('M4.5 file:// loads with drop panel', ok, str(t2.errs[:2]))
    check('M4.5b file:// no console errors', not t2.errs, str(t2.errs[:2]))
    t2.pg.close()

    t3 = T(b, port, (repo / 'compress.html').as_uri())
    t3.pg.wait_for_timeout(1500)
    ok = t3.pg.evaluate('typeof MeshoptSimplifier.simplify === "function"')
    check('M4.6 file:// compressor loads', ok, str(t3.errs[:2]))
    t3.pg.close()
