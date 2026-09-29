# Preserving changes through updates

Keep the repository revision used by your printer and a private backup of its
actual configuration, saved variables, calibration, Orca presets and probe.py.
The source repository intentionally omits private machine snapshots.

Z-Mod [documents mod_data as the user-override location](https://wiki.zmod.link/FAQ/#configuration-storage)
and [says normal USB updates preserve user data](https://wiki.zmod.link/Setup/#updating-the-mod).
Survival of a file does not establish compatibility of its macro body with a new
version. The inspected TAR_CONFIG script backs up config and logs but omits the
modified Klipper program file.

After an update, while the printer is idle:

1. Check the Z-Mod, host Klipper and MCU versions. Changing from native Klipper
   12 to another version changes the target of these modifications.
2. Compare the new probe source with the recorded original and modified files.
   Port the diff onto a compatible new version; do not replace upstream fixes by
   blindly restoring the old complete module. If the extra probe options remain
   but the module was replaced, Klipper can reject the unsupported settings.
3. Compare the new vendor macro files with `baselines/zmod`. Port the small soak
   hooks to changed macro bodies and review the purge helpers/bed limits.
4. Run `python run_tests.py`. The retained tests use the recorded interpreter
   fixtures; extend/update fixtures and tests for changed upstream behavior.
5. Restart appropriately, check loaded settings/macros, and verify a supervised
   clear-bed probe and first-layer/purge before relying on the change.

`SOURCE-MANIFEST.json` identifies the published installable source bytes. It is a
source-integrity record, not a firmware compatibility guarantee. No automatic
update hook overwrites files on the printer, and publishing this repository does
not change the firmware updater's source.
