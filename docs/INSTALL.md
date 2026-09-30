# Installation

These files were developed against AD5X Z-Mod 1.7.2-5, native Klipper 12. Inspect
your installed implementation before use on a different version or model.

## Four-edge purge only

1. Save a private copy of `mod_data/user.cfg` and the current purge macro.
2. Upload `macros/safe_line_purge.cfg` to `mod_data/safe_line_purge.cfg` in Fluidd.
3. Upload `extras/ad5x_purge.py` to `mod_data/ad5x_purge.py`. On the compatible
   native Klipper 12 installation, create this link over SSH:
   `ln -s /usr/data/config/mod_data/ad5x_purge.py /usr/prog/klipper/klippy/extras/ad5x_purge.py`.
   Inspect an existing destination first; do not overwrite another module.
   The macro include contains `[ad5x_purge]`, which loads this helper.
4. Append `[include safe_line_purge.cfg]` after existing vendor/custom includes.
5. Finish or intentionally cancel any active job, including a heat soak, and
   restart Klipper while idle. Confirm Ready and the new LINE_PURGE description.
   A configuration RESTART loads a newly added Python module. Later Python edits
   need a full Klipper process restart to replace its imported code.
6. With the intended object's polygons/brim bounds loaded, call
   `LINE_PURGE DRY_RUN=1`. To check the fallback against a stored file directly,
   use `PLAN_EDGE_PURGE DRY_RUN=1 FILE="example.gcode"`. Neither command moves or
   heats anything. The explicit FILE override is forbidden for a real purge.
7. Qualify the actual purge on a clear bed under supervision before a large job.

The macro requires Z-Mod's `_CLIENT_VARIABLE`, `_KAMP_Settings`, `_TEST_MIN_MAX`,
`_DISABLE_SENSOR`, `_ENABLE_SENSOR`, and `ZEXCLUDE` helpers, plus exclude-object
support and a configured extruder. Firmware retraction is optional.

The tested baseline uses a 220 x 220 mm usable bed, purge height 0.8 mm, amount
30 mm of filament over a 30 mm XY stroke, 20 mm margin, tip distance 0 mm, flow
setting 12, and Smart Park height 5 mm. Read the actual settings from your printer.
The minimum bed inset is 3 mm or the nominal bead half-width plus 1 mm, whichever
is greater. A nonzero tip-distance extrusion can make a larger stationary deposit
than this simple bead estimate; it needs physical checking.

The toolpath fallback requires zero tip distance and zero XY G-code offsets.
It scans low layers through purge height plus 0.5 mm, protecting extrusion,
ordinary travel and the installed timelapse parking corridor. Complete Orca
by-layer metadata and increasing layer markers are required. Arcs, custom motion
macros, tool changes and unknown transforms in those layers fail closed. Reads
are limited to 16 MiB of prefix and 512 KiB of footer, 300,000 path segments and
40 seconds of planning, with periodic Klipper reactor yields. Square obstacle
buffers make the reported clearance a conservative geometric lower bound.
Planning is only used when the simpler outer-band placement cannot fit.
The last plan is cached in memory for reuse by the real startup after a dry run.
Each use hashes the inspected prefix and footer again and checks bed, purge and
timelapse settings; changed content or settings forces fresh planning. The cache
is cleared by a restart. On the small native CPU, a first complex-file scan can
take tens of seconds; a cached decision needs only the content verification.

Rollback: remove just this include and restart while idle. Preserve later edits.
Do not invoke `_LINE_PURGE` or `_RUN_VALIDATED_PURGE` directly; their arguments
must come from the placement checks.

## Optional thermal soak

Upload `macros/optional_print_soak.cfg` to mod_data and include it from user.cfg.
It overrides START_PRINT, KAMP, _FULL_BED_LEVEL, CANCEL_PRINT and RESUME using the
inspected vendor bodies plus soak hooks. Review these dependencies after updates.

Use the two startup lines from `examples/orca-large-print.gcode` in a separate
Orca printer preset and re-slice. Keep the normal preset for prints without the
extra wait. The soak requires screenless mode, PRINT_LEVELING=1 and MESH_TEST=0.
It cleans once, brings the bed and nozzle to probing conditions, pauses for the
selected interval, and resumes automatically into a fresh mesh. Manual Resume is
blocked during the wait; Cancel disarms it. Twenty minutes is a configurable
starting point from one printer's measurements, not a universal equilibrium time.

## Timelapse parking away from the camera

This override requires the installed Moonraker timelapse plugin, its setup/camera
macros, native PAUSE/RESUME base commands and Z-Mod's bed limits. Save the current
`user.cfg` and Fluidd timelapse settings before changing them.

1. Upload `macros/safe_timelapse.cfg` to `mod_data/safe_timelapse.cfg` and append
   `[include safe_timelapse.cfg]` after the vendor timelapse configuration.
2. For a camera at the front-right, select **Custom**, X **10**, Y **210**, and
   delta Z **2** in Fluidd's timelapse settings. This parks at the opposite
   back-left corner, 10 mm inside the 220 x 220 mm bed boundaries. These
   settings persist in Moonraker's database. Avoid the stock corner presets:
   they use extended machine limits that enter maintenance areas. If preferred,
   the actual bed center can still be selected as Custom X110 Y110.
3. Restart Klipper while idle. Confirm `GET_TIMELAPSE_SETUP` reports the custom
   coordinates and `_SAFE_TIMELAPSE_PARK DRY_RUN=1` reports the route without
   movement. The dry run is valid while cold and unhomed.
4. Qualify an actual frame on a cleared bed under supervision before a job.

The macro lifts **before** XY movement, pauses for the existing camera workflow,
returns XY at the raised height, and only then lowers. It saves/restores native
G-code state. Native RESUME reaches a position already restored, avoiding its
usual simultaneous XYZ return. Configured XY offsets are preserved, with the
park destination interpreted in machine coordinates.

At the default settings, the first-purge exit at Z5 parks at Z7, and a frame at
layer Z0.25 travels at Z2.25, above the normal 0.8 mm purge bead. The configured
delta Z can be increased in Fluidd; `variable_min_lift` in this include supplies
a 2 mm safety floor, and `variable_min_travel_z` supplies an absolute Z2 floor.
Separate Z moves use
`variable_z_speed: 10.0` mm/s, while XY retains the Fluidd travel speed. The
macro skips frames rather than reducing clearance near the Z limit. It reserves
the absolute loaded-mesh envelope plus 1 mm below that limit. Unhomed axes,
source/destination outside the usable bed and existing pauses also skip a frame.
A cancelled or abandoned frame cannot trigger an automatic return/resume.

Orca's existing layer G-code can stay as:

```gcode
_SET_TIMELAPSE_SETUP PARK_ENABLE=True
TIMELAPSE_TAKE_FRAME
```

The first line enables parking, the second takes the frame. Neither line selects
the location. `PARK_ENABLE=False` takes frames in place. An Orca line enabling
parking overrides a Fluidd parking-off choice on each layer, but does not change
the custom coordinates or lift. The existing purge-path planner conservatively
protects the old low XY corridor even though this override now raises first.

Rollback: remove only `[include safe_timelapse.cfg]`, restore the saved Fluidd
settings and restart while idle. Recheck compatibility after plugin updates.

## Probe module

`probe/probe.py` replaces `/usr/prog/klipper/klippy/extras/probe.py` only on the
verified compatible native Klipper installation. Keep the original file and
compare it with `baselines/probe.before-fast-probe.py` and the patch first. Add
the probe settings from the example only after the compatible module is present.
A full Klipper process restart is needed to reload Python; a configuration
restart may retain the imported module. Do this while idle, then check Ready and
the loaded options before a supervised probe test.

`first_probe_extra_retract_dist` adds to sample_retract_dist after the discarded
fast contact only. Fixed mode does not poll the load-cell pin. Adaptive mode uses
the MCU endstop-release move with a maximum distance and optional extra clearance.
The archived configuration selected fixed mode after limited adaptive comparisons.

## Nine-point stock mesh check

This is a separate optional replacement for MESH_TEST=3, not an additional step
in the fresh-mesh soak path. It overrides _FIND_POINT and _TEST_POINT and retains
the native _PROBE_POINT and _MESH_TEST flow. It chooses a 3 x 3 set of actual mesh
nodes, subtracts the saved mesh height at each corresponding location, and
applies the mean correction once. Each point still obeys the native 0.310 mm
limit. Screenless native Klipper 12 and a loaded mesh of at least 5 x 5 are required.
Installing the include does not enable MESH_TEST=3; the archived setup left it at 0.

IFS distance and force limit values in the example document separate local tuning
choices. They are not required by the purge fix and are not universal defaults.
