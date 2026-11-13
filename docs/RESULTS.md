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
| Root installation | Not yet attempted; plan is Magisk-patching whichever partition carries the generic ramdisk on this build (`boot_a` or `vendor_boot_a`) from the YOC's own OTA image |

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

- Root on this model: stock image sourcing from the YOC's own 15051976 OTA,
  Magisk patch of whichever partition carries the generic ramdisk on this
  build (`boot_a` or `vendor_boot_a`), flash with full readback, and
  update-pause discipline are planned but not yet executed.
- One unit does not establish support for other YOC firmware versions or
  hardware sold under a similar name.
