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
- **Determine the ramdisk partition from the OTA payload before patching**:
  the Android 12 community method patched `vendor_boot`, not `boot`, so the
  Android 14 target (`boot_a` or `vendor_boot_a`) must be confirmed from the
  payload images, not assumed.
- The stock image must come from the YOC's own 15051976 OTA — do not assume
  the 2024 repo's `firmware/stock-boot-15051976.img.gz` (jarvis/SNA) or the
  Android 12-era YOC OTA (XDA thread) matches this build.
- The reset-button fastboot menu documented on XDA for this model is a
  hardware fallback if a future flash is unbootable.
- This is the first YOC on this method; `unlock_hardware_verified` in
  `profile.json` flips to true only after the first verified unlock.
