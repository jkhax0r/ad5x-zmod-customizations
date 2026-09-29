# Validation boundaries

The public tests use synthetic geometry, generic settings and dummy job names.
Selected vendor macro templates and native Klipper Python modules are retained
with their source/license notices. Tests execute the real parser, macro dispatch,
coordinate state and probe sampling code; heaters, motors, load cells, filament
sensors and clocks are test doubles.

| Suite | Cases |
| --- | --- |
| Probe sampling and selectable retract | 21 |
| Stock nine-point mesh averaging | 16 |
| Optional soak and stale pause handling | 17 |
| Four-edge purge placement | 18 |

All 72 cases pass locally with Python 3.14 and Jinja2 3.1.2. The purge suite also
checks low-Z paths against 120 seeded random footprints and explicit side/corner
examples. Public fixtures exercise the same regression assertions as the private
checkpoint without uploading measured meshes or job snapshots.

Purge tests cover each side, widest-band selection, deterministic ties, clipped
front/left and right/back candidates, nearly full-bed brims, margin boundaries,
nominal bead allowance, stroke plus wipe length, missing footprint, cold/unhomed
guards, dry runs, sensor/acceleration restoration, and retraction modes.

The four-edge purge is staged for the development printer; it has not yet been
physically qualified. A source push is not deployment, and a passing offline
test does not establish actual extrusion, bed clearance, or mechanics.
