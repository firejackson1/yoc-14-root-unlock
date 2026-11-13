# Measured results — November 13, 2026

One onn. 4K Streaming Box (`YOC`) on the exact Android 14 profile in
`profile.json` was tested. This is the first `YOC` on this method; the
underlying unlock protocol and orchestration were previously verified on the
2026 jarvis2 (direct trusted-service unlock) and the earlier 2024 jarvis/SNA
4K Pro (same trusted service reached through a USB-boot payload, including
its 15051976 → 16202347 upgrade).

| Check | Result |
| --- | --- |
| AVB trusted service reachable from normal ADB shell | Read-only probe: session opened as uid 2000, command 2 state 1 |
| `/dev/tee0` access from shell | World-writable; OP-TEE with GlobalPlatform capabilities |
| Protocol logic tests | 11 scenarios, run on-target; no failure retries a write |
| Source delta from the jarvis2 original | Header comment plus three identity constants only |
| Direct AVB unlock from normal ADB shell | One trusted-service write, state 1 to 0 |
| Write discipline | Exactly 1 write invocation; durable one-shot marker consumed; host attempt record exclusive |
| Independent readback after the write | State 0 on the same boot; identity re-verified before and after |
| Writer cleanup | Remote executable removed after result confirmation |
| Reboot requested by tooling | Never; all reboots manual |
| Android baseline before the write | `flash.locked=1`, `verifiedbootstate=green`, `vbmeta.device_state=locked` |
| Factory reset on the following boot | Expected and observed to start; data loss accepted beforehand |
| Post-reboot unlocked/orange persistence | Verified: factory reset completed, fresh boot reported `flash.locked=0`, `vbmeta.device_state=unlocked`, `verifiedbootstate=orange`, trusted state 0, boot completed |
| Partition layout | No `init_boot`; ramdisk in `boot` (`boot_a`=`mmcblk0p20`, `boot_b`=`mmcblk0p21`), classic Amlogic layout, virtual A/B enabled |
| Root installation | Complete (2026-11-13): Magisk v30.7 `armeabi-v7a` headless on-device patch of `boot` (no file picker on this ATV build), flashed to `boot_a` via recovery Fastbootd over Beercan USB, booted cleanly on the same build; first-boot env gap (`/data/adb/magisk` never installed by the headless path) repaired through the app's own EnvFix flow; `su` magic-mounted at `/system_ext/bin/su`; `su -c id` → `uid=0(root) context=u:r:magisk:s0`; policy `uid=2000 policy=2 until=0` persisted; `boot_a` readback sha256 equals the flashed image exactly |
| Userland ABI | 32-bit: `armeabi-v7a`, `zygote32`, no `/system/lib64` and no `linker64` anywhere, on an arm64 kernel. Anything staged on this box must be `armeabi-v7a` — the arm64 Magisk binaries fail to exec (ENOENT: their `PT_INTERP` `/system/bin/linker64` does not exist); the 32-bit `magisk` is static-PIE and runs unprivileged |
| Magisk-patched `boot` | Built headlessly on-device with the pinned Magisk v30.7 APK's own `boot_patch.sh` + `armeabi-v7a` libs (this ATV build has no documentsui, so the app's file picker cannot run). Flags: `KEEPVERITY=true`, `KEEPFORCEENCRYPT=true`, `PATCHVBMETAFLAG=false`, `RECOVERYMODE=false`, `LEGACYSAR=false`; pre-init partition `metadata`. Verified byte-level: kernel identical to stock, all 26 stock ramdisk entries preserved, 9 Magisk entries whose payloads hash-match the kit, backup config recorded, AVB footer preserved/relocated exactly as the app produces it (receipt `work/20261113T040256Z-magisk-patch-50001366/`) |

## Unlock execution trace (first YOC)

The single run produced this trusted-service exchange; every step returned
transport success and trusted-application success (`0x00000000`):

```json
{"stage":"identity_verified","uid":2000,"automatic_reboot":false}
{"stage":"open","rc":0,"errno":0,"tee_result":"0x00000000","origin":4}
{"stage":"response","command":2,"value":1}   // read: locked
{"stage":"response","command":3,"value":0}   // the one write
{"stage":"response","command":2,"value":0}   // read: unlocked
{"stage":"close","rc":0,"errno":0}
{"stage":"finished","outcome":0,"write_invocations":1}
```

Identity (serial prefix, device, fingerprint, bootloader, slot, ABI) was
re-read from the device immediately before and after the write, and the
kernel boot ID was confirmed unchanged across the whole sequence. The
attempt cannot silently replay: the on-device one-shot marker is created
exclusively before the write request, and the host-side attempt record
(`work/devices/<serial-hash>/unlock-attempt.json`) is also exclusive.

## Root execution trace (first YOC)

The flash and grant chain, all on the exact stock build 15051976, with
every partition write receipted in `work/20261113T041934Z-flash-boot-3a68a3/`:

```text
stock boot.img (Google full OTA, byte-verified)
  → Magisk v30.7 armv7 headless on-device patch  (receipt ...-magisk-patch-50001366)
  → fastbootd flash boot_a                        (Sending/Writing OKAY, single partition)
  → boot: orange, slot _a, magiskd running        (app root OK; shell su denied)
  → app EnvFix (fix_env, /data/adb/magisk only)  (app-sanctioned repair, no partition writes)
  → su at /system_ext/bin/su, policy Query        (first prompt auto-denied after 10 s and persisted)
  → Superuser tab: revoke deny row                 (PolicyDao delete, DB-only)
  → su request + DPAD grant via input channel      (uid=0, Allow forever)
  → boot_a readback sha256 == flashed image         (byte-exact)
```

Two behaviors worth recording for anyone repeating this headless flow:

- **A headless patch+flash has no install step.** The daemon comes up
  from ramdisk with an empty `/data/adb/magisk`, so `su` is not mounted
  and requests are denied with no prompt. The app's own EnvFix dialog
  (`fix_env`, app binaries → `/data/adb/magisk`) is the sanctioned
  repair; it writes no partitions.
- **The superuser prompt auto-denies and persists the deny.** With default
  settings, `SuRequestActivity` counts down 10 s and writes a
  Deny-forever policy row for the requesting uid — so an unanswered
  prompt blocks later attempts before any prompt is shown again. Revoke
  the row in the app's Superuser tab to return to the prompt state.
- **The prompt is invisible to uiautomator on Android 14.** With
  `suTapjack` on (default, and the Settings toggle is removed on
  SDK ≥ 32), the whole window is hidden from accessibility. Grant via the
  input channel instead: `keyevent 22` then `keyevent 23` ~3.5 s after
  firing the request (deny is the initial focus; grant enables after the
  first countdown tick).

## Bootloader layer after the unlock (survey, 2026-11-13)

A survey of the bootloader layer run from the rooted system with **no
reboots** — the constraint was that any future unattended power cycle
must land back in Android. Two independent gates are in play, and only
one was ever opened:

| Layer | State on this unit | Evidence |
| --- | --- | --- |
| AVB device state (TEE) | Unlocked, persisting | `read_state` re-probe as plain shell, 0 write commands: `lock_state a=0`; `flash.locked=0`, `verifiedbootstate=orange`, `vbmeta.device_state=unlocked`, `veritymode=enforcing` |
| Bootloader fastboot gate | Still closed; never needed | bootloader fastboot refused partition writes as "locked device" during flash prep; recovery Fastbootd and root `dd` both bypass it |

Everything else measured stock and healthy:

- `vbmeta_a` header: AVB 1.0, RSA4096/SHA256 (algorithm 2), rollback
  index 0, flags 0 — verification is fully enabled *in the image*; only
  the unlocked device state lets the Magisk-patched `boot_a` boot
  (orange).
- `bootloader_control` in `misc`: BCAB magic, `slot_suffix _a`, slot a
  active (priority 9, successful boot), slot b dormant — healthy steady
  state.
- `bootloader_a`/`bootloader_b` (4 MiB each): signed Amlogic images
  (`@ML ` magic, `DBLK_PAYLOAD_END` markers); the U-Boot env carries
  `bootloader_wp=1` and `forUpgrade_secureBoot=true`.
- U-Boot env: `avb2=1`, `EnableSelinux=enforcing`, `active_slot=_a`,
  `boot_part=boot_a`, `check_result=succ`, `production_mode=1`. The
  env's `bootloader_version` (01.01.240321.093522) predates the running
  bootloader (`ro.boot.bootloader` = 01.01.260306.163417) — the env
  block is not rewritten wholesale by bootloader updates.

One noncanonical state was found and repaired. The `misc` BCB still
carried `bootonce-bootloader`, written during flash-session prep. The
box had booted Android twice with it present (warm-reboot path), but
U-Boot's `preboot` runs `bcb uboot-command` (with `enter_fastboot` as
the target), so the field is live code on the boot path — an unattended
cold boot could have parked the box in fastboot. It was cleared with
one surgical verified write: bytes 0–32 of `misc` block 0 zeroed
(19 bytes changed), every other byte — including `bootloader_control`
at offset 2048 — preserved and byte-verified by an independent readback.
With the command field empty, the canonical state, any future reboot
follows the normal Android path; `adb reboot bootloader`/`recovery`
rewrite the field on demand, so nothing is lost.

Re-locking the TEE state is possible but only sensible after restoring
the stock `boot_a`: a locked device enforces AVB and the
Magisk-patched image would fail it. Receipt and before/after block
images: `work/20261113T172743Z-bootlayer-probe-aafd/` (out of git).

## Evidence lineage

- The trusted service is the documented upstream OP-TEE AVB TA
  `023f8f1a-292a-432b-8fc4-de8471358067` (commands 2/3), not a new exploit.
- The 2024 jarvis/SNA 4K Pro unlocked the same TA on the same build number
  (15051976) and stayed unlocked through its 16202347 OTA — evidence that
  the unlock state survives OTAs on this firmware family.
- The 2026 jarvis2 proved the direct `/dev/tee0` shell route and the exact
  orchestration reused here unchanged.
- This port adds only the `YOC` identity constants and the on-target logic
  test run (the build host has no C compiler; the pure-logic test binary was
  executed on the device as uid 2000, writing nothing).

## Prior art

- This exact model was unlocked and rooted in 2023 on Android 12 firmware
  (through `SGZ2.230609.049.A1/11261715`) via the reset-button fastboot menu,
  `fastboot flashing unlock`, and a Magisk-patched `vendor_boot` image — see
  the XDA thread "[Guides] 2023 Walmart ONN Google TV (4K YOC)".
- From build 11261715 onward the bootloader reject(ed) `fastboot flashing
  unlock`; factory-updated ("24XXA") units and Google's forced
  setup-time update closed that path. The community's standing conclusion
  for newer firmware was "cannot root at this time".
- This package's direct trusted-service unlock ran on Android 14
  (`URO4.260304.011.B1/15051976`), from a normal ADB shell on the running
  box, with no fastboot entry, OTG cable or button menu. To our knowledge
  this is the first published unlock method for a YOC on current firmware.
- The 2023 method's two lasting facts: the Android 12 ramdisk was patched in
  `vendor_boot` (so the Android 14 root target must be determined from the
  payload, not assumed to be `boot`), and the reset-button fastboot menu
  remains a hardware fallback for recovery.

## Personal identifiers and receipts

The exact hardware serial, LAN addresses, boot IDs and raw command receipts
of the tested unit are recorded only in `work/` (kept out of commits),
matching the packaged workflow's rules. The committed `profile.json` pins
the `GUSA` serial family, not one serial.

## Not yet established

- One unit does not establish support for other YOC firmware versions or
  hardware sold under a similar name.
