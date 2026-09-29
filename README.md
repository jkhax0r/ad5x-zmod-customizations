# AD5X / Z-Mod customizations

Probe improvements, optional thermal soak, nine-point mesh checking, and a
four-edge startup purge for the FlashForge AD5X. Preserved from **Z-Mod 1.7.2-5
with native Klipper 12**, using screenless/Guppy mode.

This is a collection of version-specific modifications, not a complete firmware
image. Each component can be reviewed and installed separately. Keep a private
backup of your own configuration before making changes.

## Four-edge purge fix

[`macros/safe_line_purge.cfg`](macros/safe_line_purge.cfg) replaces `LINE_PURGE`
and `_LINE_PURGE`. The inspected vendor macro tried the front and then the left.
When neither had room, its coordinate clamp could put the purge on the bed edge
inside a brim. This replacement:

- Evaluates **front, left, right, and back**, choosing the valid side with the
  widest available band; ties use that order.
- Uses all reported polygons, including `BORDER1`, for the quick outer-band check.
- If that aggregate rectangle hides free space, reads the actual Orca brim and
  low-layer travel paths and searches clear segments along all four bed edges.
  Real model polygons remain filled obstacles. Timelapse parking paths are included.
- Checks the remaining clearance **after clamping** the candidate position.
- Keeps the nominal bead envelope, extrusion stroke, and 10 mm wipe inside the
  intersection of configured bed limits and toolhead travel limits.
- Raises to the configured Smart Park height before travel and after the wipe.
- Stops with a clear error before purge motion or sensor changes if no side fits.

The purge stays on the plate to prime the nozzle and anchor loose filament. The
macro preserves the existing purge amount, height, and speed formula. It uses the
configured purge margin as a minimum centerline-to-footprint clearance. The bead
allowance is a geometric estimate, not a measurement of real molten plastic.

The original four-edge version could reject a large L-shaped layout because its
aggregate brim rectangle covered nearly the entire bed. The new
[`extras/ad5x_purge.py`](extras/ad5x_purge.py) fallback checks the actual paths for
clear partial-edge segments. It requires complete, supported Orca by-layer
G-code; unknown motion commands, arcs, incomplete geometry, and truly blocked
edges still stop before purge motion. It does not reduce the configured margin.

**Install both files:** place the macro and helper in `mod_data`, link the helper
into native Klipper's extras directory as described in
[installation](docs/INSTALL.md), then append this to `user.cfg` and restart while idle:

```ini
[include safe_line_purge.cfg]
```

With a footprint already loaded, `LINE_PURGE DRY_RUN=1` reports the selected side,
coordinates, and clearance without motion, heating, or changing sensors. It is a
placement check, not a physical purge test. To inspect a stored file without
loading a job, use `PLAN_EDGE_PURGE DRY_RUN=1 FILE="example.gcode"`.

## Other preserved changes

| Component | Purpose |
| --- | --- |
| [Probe source](probe/probe.py) and [patch](patches/probe-customizations.patch) | Discard the fast first contact, then use slow samples. Add a configurable extra first retract. Retain selectable MCU-based adaptive release. |
| [Optional soak](macros/optional_print_soak.cfg) | Clean once, settle the bed and nozzle for an optional interval, then perform a fresh mesh. Includes the stale-pause startup fix. |
| [Nine-point mesh check](macros/stock_mesh_average.cfg) | Replace the stock mesh-wide MESH_TEST=3 search with nine actual nodes and average each measurement's error against its corresponding mesh height. |
| [Orca examples](examples/) | Separate normal and 20-minute-soak start G-code, plus example probe/IFS/force settings. |

The archived working configuration used **fixed retract**: 0.300 mm extra after
the discarded fast contact and 0.200 mm normal sample retract. The adaptive
implementation remains available but is disabled by default. The nine-point
include was installed with **MESH_TEST=0**, so its startup check was disabled.
The soak path requires fresh meshing and MESH_TEST=0; do not enable the nine-point
saved-mesh path at the same time.

Per-printer bed maps, Z calibration, addresses, SSH keys, account credentials,
job history, camera settings, and the private backup archive are not published.
Test fixtures use synthetic bed geometry, job metadata, and settings. They retain
the selected vendor macro bodies needed for the regression tests.

## Updates and recovery

A GitHub repository preserves the modifications; it does not redirect the
printer's updater. User includes can survive an update while becoming incompatible
with changed vendor macros. Firmware repairs, stock updates, or Klipper version
switches can replace the patched probe module or stop using it.

Keep a private full configuration backup **plus probe.py**. Z-Mod's inspected
`TAR_CONFIG` script does not include that program file. Before reapplying changes,
compare the installed source with this repository's [baselines](baselines/), port
the modifications onto the new compatible version, run tests, and perform a
supervised clear-bed check. See [the update procedure](docs/UPDATING.md).

## Tests and qualification

```sh
python -m pip install -r requirements.txt
python run_tests.py
```

The tests run the retained Klipper parser, macro engine, G-code state, and probe
sampling logic with hardware test doubles. They cover each purge side, greatest
clearance, clipped boundaries, brim bounds, bead allowance, full stroke/wipe fit,
error paths, state restoration, and 120 seeded random footprints. The suites run
in separate processes because the legacy harness changes global logging.

This public release has **91 passing offline tests**. The fallback was also
deployed and checked without motion on the development printer against the file
that exposed the aggregate-box failure. Physical extrusion qualification remains
pending. [Validation details](docs/VALIDATION.md).

## License and attribution

Modifications and this collection are distributed under [GPL-3.0](LICENSE).
Retained upstream source keeps its original notices and applicable licenses,
including Z-Mod's Apache-2.0 material. See [NOTICE.md](NOTICE.md).
