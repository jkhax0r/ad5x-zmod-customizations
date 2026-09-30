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
| Staged timelapse parking and native pause/resume state | 18 |

All 109 cases pass locally with Python 3.14 and Jinja2 3.1.2. The purge suite also
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

Timelapse tests execute native G-code state, PAUSE/RESUME and the installed
plugin's setup macro. Hardware and camera completion remain test doubles.
They check lift-before-XY, return-XY-before-lowering, first-layer/purge heights,
tall prints, minimum and larger configured lifts, source/target outside the bed,
insufficient Z room including mesh reserve, cold/unhomed dry runs, existing
pauses, cancellation, lost homing, absolute/relative XYZ and E modes, offsets,
G92 E, feed/flow factors, firmware retraction dispatch, no cold priming,
Orca parking enable, disabled/in-place frames and hyperlapse mode filtering.

The timelapse include and persistent center settings were deployed to the idle
development printer, restarted and read back. Other loaded configuration was
compared with the pre-install snapshot. A native nonmoving route check passed.
No homing, extrusion or physical frame-parking test was performed for this change;
physical clearance, noise and camera composition remain to be qualified.

The subsequent corner setting uses Custom X10 Y210 DZ2, opposite the confirmed
front-right camera. The two lift/travel floor variables and saved include were
also changed to 2 mm, avoiding a hidden 5 mm floor. An additional native replay
case checks that corner at first-purge, first-layer and tall-print heights. These
settings were applied and read back without restarting the ongoing print;
physical clearance and camera composition at the new corner remain unqualified.

The no-parking branch no longer issues `M400`. The existing native replay tests
now record the synchronization/frame commands: in-place capture must dispatch
only the frame request, with no motion, pause or delayed return; parked capture
must still dispatch `M400` before the frame request. This is a command-path
check, not a measurement of host latency or nozzle ooze.
