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
| Actual low-layer paths and macro fallback | 19 |

All 91 cases pass locally with Python 3.14 and Jinja2 3.1.2. The purge suite also
checks low-Z paths against 120 seeded random footprints and explicit side/corner
examples. Public fixtures exercise the same regression assertions as the private
checkpoint without uploading measured meshes or job snapshots.

Purge tests cover each side, widest-band selection, deterministic ties, clipped
front/left and right/back candidates, nearly full-bed brims, margin boundaries,
nominal bead allowance, stroke plus wipe length, missing footprint, cold/unhomed
guards, dry runs, sensor/acceleration restoration, and retraction modes.

The path suite covers large aggregate bounds with free edge segments, genuinely
full beds, brim larger than model polygons, low travels and timelapse parking,
later low layers, coordinate/extrusion modes, invalid/truncated/unsupported input,
filename confinement, dry runs and the complete fallback through native dispatch.
It independently measures accepted clearance against 60 seeded random path sets.

On the development printer, native Python 3.8 and a nonmoving Klipper command
checked the actual file behind the aggregate-box failure. Its complete low-layer
geometry produced a valid back-edge placement with over 25 mm clearance while
retaining the 30 mm purge, 10 mm wipe and 20 mm minimum margin. The first
printer-side scan took 23.209 seconds; a content-verified cached check took
0.132 seconds. Private job paths
and geometry are deliberately omitted from this public repository.

The revised macro and helper are deployed; physical extrusion qualification is
pending. Passing tests and a nonmoving check do not establish actual extrusion,
molten bead shape or mechanics.
