# XDA announcement post — onn. 4K Streaming Box (YOC) unlock + root

**Suggested thread title:**

```
[ROOT][UNLOCK] onn. 4K Streaming Box (YOC) — unlock + Magisk root on current firmware (Android 14, 15051976), no fastboot unlock needed
```

**Body:**

---

## Unlock and root for the onn. 4K Streaming Box (YOC) on current firmware — no fastboot unlock, no button menu, no opening the box

**The short version:** a YOC running current Android 14 firmware (build
15051976, URO4.260304.011.B1) can be unlocked and rooted entirely from a
normal ADB shell on the live device. No `fastboot flashing unlock`, no
reset-button recovery menu, no OTG cable, no disassembly.

### Why this matters

Since build 11261715 the YOC bootloader rejects `fastboot flashing
unlock`, and factory-updated units lost the reset-button fastboot path
too. The standing conclusion for years was "no unlock / no root on
current firmware." This method sidesteps that gate completely.

### How it works

The bootloader defers to the AVB device state, which is held by OP-TEE's
AVB trusted application (UUID
`023f8f1a-292a-432b-8fc4-de8471358067`). On this build `/dev/tee0` is
reachable from the plain ADB shell, and the trusted app speaks its
documented protocol: command 2 reads the lock state, command 3 is a
one-shot write. One write flips locked → unlocked. That's the whole
unlock. It persists across reboots *and* factory resets, verified boot
stays enforcing in the images (vbmeta rollback index 0, flags 0), and
boots come up orange.

### The root chain (overview)

1. Unlock via the trusted service — one verified write.
2. Patch the stock `boot` image with Magisk v30.7 (the `armeabi-v7a`
   kit — this box has a 32-bit userland on an arm64 kernel).
3. Flash `boot_a` through recovery Fastbootd. Note: the *bootloader's*
   fastboot still reports locked and refuses writes — that gate is
   separate from the AVB device state and stays shut. Doesn't matter;
   fastbootd and root `dd` cover all writes.
4. First boot needs the Magisk app's own env fix (a headless
   patch+flash skips Magisk's normal install step).
5. Grant the shell's superuser request — see gotchas.

Everything is on the exact stock build, no other partitions touched,
with readback verification (flashed `boot_a` sha256 == patched image).

### Verified

One YOC, stock 15051976: unlock → root end-to-end, root persists
across reboots, `boot_a` readback byte-exact. The same trusted-service
route was previously proven on the onn. 4K Pro family (2024 unit via a
USB-boot payload — which stayed unlocked through its OTA — and the 2026
unit directly from the shell).

### Gotchas worth knowing before you start (all documented in the repo)

- `fastboot getvar unlocked` in bootloader mode still says `no`
  (`secure: yes`), and it refuses even `oem device-info` as "locked
  device." Expected — see "two locks" above.
- `adb reboot bootloader` leaves a stale `bootonce-bootloader` BCB
  command behind that can drop an unattended cold boot into fastboot.
  Clear it (the repo's survey explains how and why).
- The Magisk superuser prompt is invisible to uiautomator on Android 14
  and its 10-second countdown auto-denies *and persists* the deny
  policy. There's a documented keyevent-based grant procedure.
- A firmware OTA will likely restore stock `boot_a`; expect to re-patch.
  The unlock itself survives (per the 4K Pro evidence).

### Requirements

- onn. 4K Streaming Box (YOC), on or near build 15051976
- ADB access (wireless or USB)
- a Linux host for the tooling (a Beercan/Debian box is what it was
  built on)

Tested on one unit so far — if you try it, post your build number and
where it worked or stopped. Don't flash images from other repos onto a
YOC; exact-build stock comes from Google's full OTA (also in the repo's
docs).

### The repo

**https://github.com/firejackson1/yok-14-root-unlock**

Full walkthrough, the verified unlock orchestration (identity checks,
one-shot write marker, independent readback, receipts for every
partition write), the boot-layer survey (BCB, vbmeta, U-Boot env,
bootloader fastboot probe), and measured results.

### Credits

- The 2023 YOC guides (reset-button menu + `flashing unlock` era) for
  the foundation and the ramdisk/partition homework.
- The trusted service used here is upstream, documented OP-TEE AVB
  infrastructure — this is a route to it, not a TEE exploit.

Happy to answer questions here; deeper technical detail lives in the
repo's docs.

---

**Before posting:** do the repo rename (Settings → remove trailing dash)
so the clean URL above is live — GitHub redirects either way, so the
link works regardless.
