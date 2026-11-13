# Root procedure — onn. 4K Streaming Box (YOC), stock build 15051976

The complete root chain for a YOC on the exact stock firmware
(`URO4.260304.011.B1` / 15051976, fingerprint `onn/onn_4k_gtv/YOC`),
using only official Magisk v30.7 components. Written so it can be replayed
from this repo alone; every step below was executed and verified on one
unit (receipts with full transcripts are kept locally, out of git).

## Prerequisites

- The **unlock completed first** (this repo's `yoc_unlock.py`): the box
  boots `verifiedbootstate=orange`, `flash.locked=0`,
  `vbmeta.device_state=unlocked`. Root is not possible while locked.
- ADB access to the box (wireless `:5555` or USB).
- A Linux host with `platform-tools` (adb), and **USB access to that host**
  for the flash step (fastbootd over USB).
- No hardware access is needed at any point: no opening the box, no
  reset-button menu, no OTG cable.

Partition facts for this model (no `init_boot`; the generic ramdisk
lives in `boot`): root target is `boot_a` (`/dev/block/mmcblk0p20`),
64 MiB. Do **not** flash boot images taken from other models or firmware
versions — exact-build stock is sourced below.

## 1. Source and verify the exact-build stock boot image

Follow `docs/FIRMWARE-SOURCES.md` with
`tools/fetch_full_ota_partitions.py`: pull `boot.img` from Google's
full OTA for build 15051976 and verify with
`tools/inspect_boot_image.py`. Expected stock image:

```
size   67108864
sha256 9573ad1de84af6bef755b6ff355983831e5864e3c40fdc4901d5ccfabcc4d092
```

## 2. Stage Magisk

- Install the official Magisk v30.7 APK (`adb install`) — the app is
  needed after the flash for the env fix and the superuser grant.
- Unpack the same APK and assemble a patch workdir with the
  **`armeabi-v7a`** kit — the YOC userland is 32-bit `zygote32` on an
  arm64 kernel; the arm64 kit cannot exec there. The workdir needs the
  APK's `busybox`, `magiskboot`, `magiskinit`, `init-ld`, `stub.apk`,
  plus `util_functions.sh`, `boot_patch.sh`, `addon.d.sh`, and the stock
  `boot.img`.
- Push the workdir to `/data/local/tmp/` on the box.

## 3. Headless on-device patch

This ATV build ships no file picker, so the app's "Select and patch a
file" is replaced by running the same script with the same flags the app
uses, from an ADB shell in the workdir:

```
KEEPVERITY=true KEEPFORCEENCRYPT=true PATCHVBMETAFLAG=false \
RECOVERYMODE=false LEGACYSAR=false sh boot_patch.sh boot.img
```

Expected: `Stock boot image detected`, ramdisk patched and repacked into
`new-boot.img`, `patch_exit=0`. **Three `Failed to patch` lines are
normal** — those are the Samsung kernel hexpatches (RKP / defex / PROCA)
finding no pattern on an Amlogic kernel; the kernel section is kept
byte-identical to stock. Verify the result:

```
size   67108864
sha256 639060ccec7f7419f665a66faf630059606ffb428199a765a64afba8f0fd0cd8
```

(a fully deterministic output for this stock image + v30.7 armv7 kit)

## 4. Flash `boot_a` through recovery Fastbootd

The bootloader's own fastboot stays locked on this firmware
(`fastboot getvar unlocked` → `no`, writes refused as "locked device") —
that gate is independent of the AVB unlock and never needs to open. Use
the userspace fastbootd instead:

1. Host USB prep: a udev rule (or manual chmod) making the node
   accessible, e.g. `SUBSYSTEM=="usb", ATTRS{idVendor}=="18d1",
   ATTRS{idProduct}=="4ee0", MODE="0666"`. Cover `4ee7` too if you ever
   enter bootloader fastboot (observed: fastbootd enumerates as
   `18d1:4ee0`, bootloader fastboot as `18d1:4ee7`).
2. `adb reboot fastboot` — the box reboots into recovery fastbootd; ADB
   drops during the restart, which is expected.
3. On the USB-attached host:

```
fastboot getvar is-userspace        # -> yes (confirms fastbootd, not bootloader)
fastboot getvar current-slot       # -> a
fastboot flash boot_a new-boot.img
fastboot reboot
```

Exactly one partition is written: `boot_a`, 64 MiB. Keep
`new-boot.img` and the stock image as rollback copies.

## 5. First boot: the env fix (app-sanctioned)

A headless patch+flash skips Magisk's normal install step, so the daemon
comes up from ramdisk with `/data/adb/magisk` empty — app root works,
but `su` is not mounted and shell requests are denied with no prompt.
The Magisk app itself flags and repairs this: open (or force-stop and
relaunch) the app → the "Requires additional setup — proceed and
reboot?" dialog appears → confirm. `fix_env` copies the app's v30.7
armv7 binaries into `/data/adb/magisk` (no partition writes) and the
app reboots the box. Afterwards `su` is magic-mounted at
`/system_ext/bin/su`.

## 6. Grant shell superuser (the prompt is invisible on Android 14)

Two quirks of `SuRequestActivity` on this build shape the procedure:

- With `suTapjack` on (default; the Settings toggle no longer exists on
  SDK ≥ 32) the prompt is hidden from uiautomator entirely.
- Its 10-second countdown **auto-denies and persists the deny policy
  forever** for the requesting uid — an unanswered prompt blocks all later
  attempts with no prompt shown.

Procedure:

1. Magisk app → Superuser tab: if a `[SharedUID] Shell` row exists,
   open it, **Revoke**, confirm — this returns the policy to "ask".
2. With the Magisk app in the foreground, fire the request from ADB:
   `adb shell su -c id`.
3. Wait ~3.5 s (the prompt needs ~1 s to display and the Grant button
   enables on the first countdown tick), then drive the prompt through
   the input channel:

```
adb shell input keyevent 22    # DPAD_RIGHT — focus moves deny -> grant
adb shell input keyevent 23    # DPAD_CENTER — click grant
```

## 7. Verification battery

```
adb shell su -c id
# -> uid=0(root) gid=0(root) groups=0(root) context=u:r:magisk:s0

adb shell su -c "magisk --sqlite \"select * from policies\""
# -> uid=2000|policy=2|until=0|logging=1|notification=1  (Allow, forever)

adb shell su -c "ls /data/adb/magisk"
# -> busybox, magiskboot, magiskinit, magiskpolicy, stub.apk, scripts...

adb shell su -c "dd if=/dev/block/by-name/boot_a bs=4M 2>/dev/null | sha256sum"
# -> 639060ccec7f7419f665a66faf630059606ffb428199a765a64afba8f0fd0cd8 (== patched image)

adb shell getprop ro.boot.verifiedbootstate   # orange
adb shell getprop ro.boot.slot_suffix          # _a
```

Root survives reboots; `su -c id` works without the app in the
foreground.

## 8. BCB hygiene after `adb reboot bootloader`

`adb reboot bootloader` leaves `bootonce-bootloader` in the `misc` BCB
command field, and U-Boot's `preboot` runs `bcb uboot-command` — an
unattended cold boot can park the box in fastboot. After any use,
clear the command field (32 bytes at offset 0) while preserving
everything else — `status` at 32, `recovery` at 64, and
`bootloader_control` at offset 2048 (slot state + CRC):

```
adb exec-out 'su -c "dd if=/dev/block/by-name/misc bs=4096 count=1 2>/dev/null"' > misc_blk0.bin
# locally: new = copy of misc_blk0.bin with bytes [0:32] zeroed; verify 19 bytes change
adb push misc_blk0_new.bin /data/local/tmp/
adb shell 'su -c "dd if=/data/local/tmp/misc_blk0_new.bin of=/dev/block/by-name/misc bs=4096 count=1 conv=notrunc 2>/dev/null"'
# read back and byte-verify: command zeroed, bytes 32:4096 identical
```

(`adb reboot fastboot` does not leave this behind.)

## 9. Notes and gotchas

- **Display asleep breaks uiautomator**: `null root node` means the
  screen is off — `input keyevent 224` to wake, then re-dump.
- **Tap coordinates**: with `wm size` Override 1920x1080, uiautomator
  dump coordinates are already in input-tap space — no scaling.
- **Firmware OTA** will likely restore stock `boot_a`: expect to re-run
  steps 1–4 (the unlock itself survives; see the 4K Pro OTA evidence in
  `docs/RESULTS.md`).
- **Relock** (returning the unit) only after restoring stock `boot_a` —
  a locked device enforces AVB and the patched image fails it.
- Zygisk is off by default; enable it in the app if you need modules.
