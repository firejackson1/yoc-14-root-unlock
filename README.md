# onn. 4K Streaming Box (YOC) — direct AVB unlock

Port of the tested 2026 jarvis2 unlock workflow
(`../onn-4k-pro-2026-unlock-root/`) to the onn. 4K Streaming Box (`YOC`,
fingerprint `onn/onn_4k_gtv/YOC`, serial prefix `GUSA`). Same method, same
rules: stop on failure, no automatic retry, one unlock write ever, no
automatic reboot.

## What is reused unchanged

- `native/unlock_logic.h` — byte-identical copy of the jarvis2 original
  (AVB TA `023f8f1a-292a-432b-8fc4-de8471358067`, command 2 read / command 3
  one-shot write state machine).
- `bin/armeabi-v7a/read_state` — the reviewed jarvis2 binary, hash identical
  to the upstream `NATIVE_SHA256.json`.
- `native/read_state.c`, `native/test_unlock_logic.c` — byte-identical copies.
- The whole `onn2026.unlock()` orchestration (identity checks, boot-id
  checks, exclusive attempt marker, push/hash-verify, one-shot marker
  consumption, outcome proof, independent readback, receipts) runs unmodified
  via `yoc_unlock.py`, which substitutes only the identity validation and
  `ROOT`/`PROFILE` pointers.

## What differs from the jarvis2 package

`native/unlock_once.c` differs from the jarvis2 original in exactly four
places: the header comment and the three hardware identity constants —
serial prefix `GUSA` (was `JUSA`), fingerprint prefix `onn/onn_4k_gtv/YOC:`
(was `onn/jarvis2/jarvis2:`), and `ro.product.device` check `YOC`
(was `jarvis2`). `yoc_unlock.py` additionally pins the exact fingerprint,
bootloader `01.01.260306.163417`, and slot `_a` in `profile.json`, and
validates `GUSA`-prefix serials the way the jarvis2 package validates
`JUSA`-prefix serials. The exact serial of the first unit is recorded only
in local `work/` receipts, which stay out of commits.

## Build and validation

```sh
# same documented command as tools/build_native.py in the jarvis2 repo
$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/armv7a-linux-androideabi28-clang \
  -std=c11 -D_GNU_SOURCE -O2 -Wall -Wextra -Werror -fPIE -pie \
  native/unlock_once.c -o bin/armeabi-v7a/unlock_once

# logic test (no host cc on this machine; ran as ARM binary on the YOC itself):
# "11 protocol scenarios passed; no failure retries a write." exit 0
```

Built and validated 2026-11-13 with NDK r28c (28.2.13676358). The host-side
logic test was executed on the target device because no host C compiler is
available; it is pure in-process logic (no TEE access, no writes).

## Evidence before the first write (read-only, 2026-11-13)

- `read_state` probe on this unit: TA session opened from shell (uid 2000,
  `LOGIN_PUBLIC`), command 2 returned `lock_state a=1` (locked), 0 writes.
  Binary removed after the run. Receipt: `work/20261113T000000Z-read-state-probe/`.
- `/dev/tee0` world-writable; OP-TEE reports AVB 1.2, virtual A/B enabled.
- Android baseline: `ro.boot.flash.locked=1`, `ro.boot.verifiedbootstate=green`,
  `ro.boot.vbmeta.device_state=locked`.

## Usage

```sh
python3 yoc_unlock.py --adb /tmp/opencode/platform-tools/adb \
  --target 10.0.0.44:5555 verify-files        # packaged inputs only
python3 yoc_unlock.py --adb /tmp/opencode/platform-tools/adb \
  --target 10.0.0.44:5555 unlock --accept-data-loss
python3 yoc_unlock.py --adb /tmp/opencode/platform-tools/adb \
  --target 10.0.0.44:5555 verify             # after the user's reboot
```

Unlock itself never reboots. The next reboot performs the Android factory
reset (data loss is accepted with `--accept-data-loss`); after setup, the
user re-enables ADB debugging and `verify` proves orange/unlocked.

## Rooting notes (next phase, not in this package yet)

- No `init_boot` partitions on the YOC: `boot_a`=`mmcblk0p20`,
  `boot_b`=`mmcblk0p21`, `vendor_boot_a`=`mmcblk0p5`,
  `vendor_boot_b`=`mmcblk0p6`; virtual A/B enabled, classic Amlogic layout.
- **Ramdisk partition confirmed (2026-11-13) from the validated stock
  images**: the generic ramdisk (`init`, GKI-style first-stage skeleton) is
  in `boot`; `vendor_boot` is vendor-only. The Magisk target is `boot_a`,
  not the Android 12-era `vendor_boot`.
- Stock images for the exact 15051976 build were sourced from Google's own
  full OTA for that build and validated byte-exactly against the two-manifest
  cross-check (`docs/FIRMWARE-SOURCES.md`; images in `cache/stock-15051976/`,
  out of git). Do not assume the 2024 repo's
  `firmware/stock-boot-15051976.img.gz` (jarvis/SNA) or the Android 12-era
  YOC OTA (XDA thread) matches this build.
- **Patched image built and verified (2026-11-13)**: Magisk v30.7 with its
  `armeabi-v7a` kit (the userland is 32-bit `zygote32` on this arm64-kernel
  box; the arm64 kit cannot exec there), run headlessly on-device via the
  APK's own `boot_patch.sh` because this ATV build has no file picker.
  Output: `cache/magisk-patched-15051976/new-boot.img` (out of git),
  receipt `work/20261113T040256Z-magisk-patch-50001366/`.
- **Flashed and verified (2026-11-13)**: the patched image was written to
  `boot_a` through recovery Fastbootd (bootloader fastboot refuses writes
  on this unit as a "locked device"; the recovery-userspace Fastbootd
  accepts them while unlocked-orange) and booted cleanly on the same
  build. A headless patch+flash skips Magisk's normal install step, so the
  first boot lacked `/data/adb/magisk`; that gap was repaired through the
  app's own sanctioned EnvFix flow (app binaries copied into
  `/data/adb/magisk`, no partition writes), after which `su` is
  magic-mounted at `/system_ext/bin/su`.
- **Granting shell superuser on this build**: the Magisk prompt
  (`SuRequestActivity`) is invisible to uiautomator on Android 14
  (`suTapjack` attaches an empty accessibility delegate, and the toggle is
  removed from Settings on SDK ≥ 32), and its 10 s countdown auto-denies
  and *persists* the deny policy forever by default. Working procedure:
  revoke any `[SharedUID] Shell` deny row in the app's Superuser tab, fire
  the `su` request with the app foreground, then drive the prompt through
  the input channel — `keyevent 22` (DPAD right, deny→grant focus) and
  `keyevent 23` (DPAD center, click grant) about 3.5 s after firing.
  Verified: `su -c id` → `uid=0(root)`, policy
  `uid=2000|policy=2|until=0`, and a `boot_a` readback whose sha256 equals
  the flashed image. Receipt:
  `work/20261113T041934Z-flash-boot-3a68a3/`.
- The reset-button fastboot menu documented on XDA for this model is a
  hardware fallback if a future flash is unbootable.
- This is the first YOC on this method; `unlock_hardware_verified` in
  `profile.json` flips to true only after the first verified unlock.
