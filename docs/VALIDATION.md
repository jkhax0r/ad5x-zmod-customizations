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
| Actual low-layer paths and macro fallback | 35 |
| Staged timelapse parking and native pause/resume state | 18 |

All 125 cases pass locally with Python 3.14 and Jinja2 3.1.2. The purge suite also
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
The interval-union optimization is compared with independent polygon clipping
across both axes, varied margins and windows, overlapping and protruding buffers,
parallel and zero-length paths. A blocked-window case verifies that redundant
paths are not scanned after no stroke can fit.

Sequential cases cover later-object brims, transitions and low descents after
high layers; missing, mismatched and repeated object boundaries; unknown high
commands and post-end motion; relative coordinates and extrusion-mode changes;
tool-change contract/height checks; chunk boundaries and comments containing
coordinate-like text; and changes to a later object outside the cached prefix
and footer. The fast scanner is compared with the same parser with skipping
disabled, including the entire private 60 MB failing file. Both produced exactly
139,961 protected paths, 19 low layers and two objects.
The optimized native Python 3.8 scan took 107.307 seconds to parse and 20.504
seconds to choose the edge (127.810 seconds total). It selected the front edge
at X3.053 Y3.000 with 54.392 mm clearance. This is a full sequential-job scan,
so its timing is not directly comparable with a by-layer prefix scan. The older
by-layer regression file still produces exactly its original placement.
After backup and a full Klipper process restart, the installed command accepted
the unchanged sequential file in a nonmoving dry run: 127.609 seconds uncached
and 3.162 seconds for a full-file-verified cached repeat. Both reported two
objects, 19 low layers and the same front-edge placement. Toolhead position stayed
fixed and heater targets stayed at zero. Physical extrusion was not exercised.

On the development printer, native Python 3.8 and a nonmoving Klipper command
checked the actual file behind the aggregate-box failure. Its complete low-layer
geometry produced a valid back-edge placement with over 25 mm clearance while
retaining the 30 mm purge, 10 mm wipe and 20 mm minimum margin. The first
printer-side scan took 23.209 seconds; a content-verified cached check took
0.132 seconds. Private job paths
and geometry are deliberately omitted from this public repository.

A later complex file with 37,145 paths across 12 low layers reached the
40-second planning limit. After the interval-union optimization, native printer
Python parsed and planned the same file in 22.937 seconds. The chosen front-edge
position and 78.022 mm clearance exactly matched the original planner's offline
result. Following a full Klipper process restart, the installed command completed
a fresh nonmoving check in 26.285 seconds and a content-verified cached repeat in
0.053 seconds. Heater targets stayed off and the toolhead position did not change.
The original module was backed up; the purge macro, user configuration, minimum
clearance and 40-second planning limit were retained. Timing depends on file
complexity and printer load; this check does not establish physical extrusion.

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
