# GLB Web Toolkit — Build Plan

**STATUS: COMPLETE — 43/43 milestone tests passing** (`tests/run_tests.py all`)

Location: `glbviewer/` (self-contained, publishable as a public GitHub repo —
contains NO scan data).

## Deliverables
1. `index.html` — **generic GLB viewer** (all v2 features: WASD/QE/jump,
   lighting presets, ASC CDL + LUT grading, touch UI, screenshot/pose).
   ~400 KB, no embedded model.
2. `compress.html` — **GLB compressor**: drop a .glb, choose quality +
   texture size, download a smaller .glb (meshopt decimation + u16
   quantization with matrix baking + texture downscale). ~500 KB incl. WASM.
3. `README.md`, this `PLAN.md`, `tests/` (Playwright milestone gates).

## Milestones & acceptance tests

### M1 — Viewer shell + model loading
- [x] Page loads with NO model, shows drop/browse panel ("Drop .glb or click")
- [x] File input + drag-and-drop + `#glb=<base64>` URL hash +
      `window.__loadModel(arrayBuffer)` test hook all feed the same loader
- [x] Auto-fit: model scaled to 8-unit span, centered, base at y=0
- [x] Floor estimate (sample histogram → dominant low-y bin) sets walk FLOOR
- [x] Materials fix (metalness 0, roughness 1) still applied
- [x] Coordinate bar shows model name / tris / verts
- **Tests (m1):** loads without model; loads roomscan.glb via hook;
  __modelReady + non-blank render; camera framed; FLOOR sane (>= minY, <= minY+2);
  stats string populated.

### M2 — Full feature set on a loaded model
- [x] Walk: WASD moves, Q descends to FLOOR, E ascends, Space jumps (gravity)
- [x] Orbit mode default framing after load; key 1/2 toggle works
- [x] Lighting presets switch (menu + number keys)
- [x] Grading standard look active from first frame (syncGrading at boot)
- [x] Screenshot F + pose P produce downloads
- [x] Touch UI appears only on touch devices (emulated iPhone), joystick/Q/E work
- **Tests (m2):** all of the v2 test suite re-run against generic viewer with
  injected model; touch context test; preset switch test.

### M3 — Compressor
- [ ] `compress.html` parses any GLB (port of build.py logic)
- [x] meshopt decimation via embedded WASM (quality slider = vertex fraction)
- [x] u16 quantization of positions/UVs with per-primitive u16/u32 index choice
  and scale+min baked into node matrices (the 6-bug-fixed logic)
- [x] Texture downscale via canvas (2048/1024/512/off), original mime kept
- [x] Output: valid GLB (magic/JSON/chunks/alignment verified) that renders
  identically (screenshot luminance within tolerance of original)
- [x] Stats panel: in/out size, verts, tris, textures
- **Tests (m3):** compress real roomscan.glb (14 MB) → download captured →
  structural GLB validation in Python → load result in viewer → render →
  size < 50% of input and visual parity.

### M4 — Polish & publish
- [x] README.md (usage, privacy note, GitHub Pages steps)
- [x] Total repo size < 1.5 MB
- [x] Drag-and-drop of a real file works in headless (set_input_files)
- [x] file:// smoke test (page works with no server at all)
- [x] Full test suite green (m1+m2+m3+m4)
- **Tests (m4):** size check, file:// load, input-files drop, run_all.

## Test system
`tests/run_tests.py` — Playwright (chromium, swiftshader), `/usr/bin/python3`.
Serves the repo root over localhost http for model fetches. One test function
per acceptance criterion; `python3 run_tests.py m1|m2|m3|m4|all`.
Non-zero exit on any failure. Each milestone must pass before the next starts.

## Architecture notes
- Viewer: root `index.html` with the embedded-model block replaced by a
  generic loader; FLOOR becomes a global set at load; orbit-first start.
- Compressor: pure JS port of `build.py` (GLB chunk walk → accessor arrays →
  simplify → quantize/bake → re-emit). `meshopt_simplifier.js` (67 KB,
  base64-embedded WASM, UMD) inlined — no network needed.
- Cross-tool: `index.html#glb=<base64>` deep link (used by compressor's
  "open in viewer" for outputs < 1.5 MB).
