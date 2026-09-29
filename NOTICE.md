# Source and licensing notices

This repository contains modifications made for the jkhax0r AD5X setup in
September 2026. It is not an official FlashForge, Z-Mod, or Klipper release.

- Klipper Python source and derived probe modifications retain their original
  copyright notices, including Kevin O'Connor and the other named contributors.
  Their license is GNU GPL version 3; see [LICENSE](LICENSE).
  Upstream: <https://github.com/Klipper3d/klipper>.
- Z-Mod macro baselines and derived startup/mesh macros originate from
  ghzserg/Z-Mod, copyright 2024-2026 ghzserg and its contributors. The upstream
  repository provides the [Apache-2.0 license](LICENSES/Apache-2.0.txt).
  Individual files carrying another license retain that license.
  Upstream: <https://github.com/ghzserg/zmod>.
- The retained client pause/resume definitions identify copyright 2022 Alex
  Zellner and GNU GPL version 3. Derived pause/resume sections are included in
  the optional soak macro. Their notices are retained in the client baseline.
- Adaptive meshing, parking, and line-purge behavior came through Z-Mod's KAMP
  integration. Original KAMP project:
  <https://github.com/kyleisah/Klipper-Adaptive-Meshing-Purging>.
  The replacement four-edge planner is documented in this repository; it retains
  the existing Z-Mod purge workflow and helper calls.

`baselines/` and `tests/vendor/` contain retained upstream/deployed reference
material for comparison and offline tests. They are not install targets. Modified
macro/probe files are marked at their start. Synthetic test fixtures contain
selected macro templates from those sources and no captured personal job data.
