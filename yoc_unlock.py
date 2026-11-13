#!/usr/bin/env python3
"""onn. 4K Streaming Box (YOC) unlock port of the tested jarvis2 workflow.

Runs the tested onn2026 unlock orchestration unchanged, substituting only:
  - identity validation: pins the YOC (exact serial, fingerprint,
    bootloader, slot) instead of the jarvis2 profile
  - bin/armeabi-v7a/unlock_once: built from native/unlock_once.c, which
    differs from the jarvis2 original only in its identity constants
  - native/unlock_logic.h and the reviewed read_state binary are
    byte-identical jarvis2 copies, hash-checked by NATIVE_SHA256.json

Receipts are stored under this directory's work/. Same rules as the
original: stop on failure, no automatic retry, never repeat an uncertain
write or reboot.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

YOC_ROOT = Path(__file__).resolve().parent
JARVIS2_ROOT = YOC_ROOT.parent / "onn-4k-pro-2026-unlock-root"
sys.path.insert(0, str(JARVIS2_ROOT))

import onn2026  # noqa: E402


def apply_yoc_profile():
    profile = json.loads((YOC_ROOT / "profile.json").read_text())
    onn2026.ROOT = YOC_ROOT
    onn2026.PROFILE = profile

    def validate_identity(props, unlocked=False):
        expected = {"ro.product.device": profile["device"],
                    "ro.build.fingerprint": profile["fingerprint"],
                    "ro.bootloader": profile["bootloader"],
                    "ro.boot.slot_suffix": profile["slot"]}
        for key, value in expected.items():
            onn2026.require(props.get(key) == value, "Unsupported or changed device: " + key)
        onn2026.require(
            re.fullmatch(profile["serial_prefix"] + r"[A-Za-z0-9]+",
                         props.get("ro.serialno", "")),
            "Unexpected YOC hardware serial")
        onn2026.require(profile["abi"] in props.get("ro.product.cpu.abilist", "").split(","),
                        "Unsupported userspace ABI")
        if unlocked:
            onn2026.require(all(props.get(k) == v for k, v in onn2026.UNLOCKED.items()),
                            "Post-reboot Android unlock properties are not proved")
            onn2026.require(props.get("sys.boot_completed") == "1", "Android boot is incomplete")
        return {key: props[key] for key in onn2026.IDENTITY}

    onn2026.validate_identity = validate_identity
    return profile


def verify(device):
    """Post-reboot proof: Android unlock properties, boot completed, TA state 0."""
    props = device.identify(unlocked=True)
    state, boot = onn2026.lock_state(device)
    result = {"at": onn2026.now(), "binding": device.binding, "boot_id": boot,
              "secure_lock_state": state,
              "unlocked_on_current_boot": state == 0,
              "boot_properties": {k: props.get(k) for k in onn2026.UNLOCKED},
              "boot_completed": props.get("sys.boot_completed"),
              "write_invocations": 0, "reboot_invocations": 0}
    onn2026.require(state == 0, "Secure AVB unlock is not proved on this boot")
    onn2026.save(device.s.path / "verify-result.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", required=True, help="Path to ADB")
    parser.add_argument("--target", required=True,
                        help="Explicit ADB address or serial of the YOC")
    sub = parser.add_subparsers(dest="command", required=True)
    u = sub.add_parser("unlock", help="One direct trusted-service unlock; never reboots")
    u.add_argument("--accept-data-loss", action="store_true")
    sub.add_parser("verify", help="Post-reboot unlock verification; read only")
    sub.add_parser("verify-files", help="Verify packaged YOC inputs; no device access")
    args = parser.parse_args(argv)
    os.umask(0o077)
    profile = apply_yoc_profile()
    if args.command == "verify-files":
        onn2026.verify_files()
        result = {"packaged_inputs_verified": True, "profile": profile["id"]}
        print(json.dumps(result, indent=2))
        return 0
    session = onn2026.Session(YOC_ROOT / "work", args.command)
    try:
        device = onn2026.Device(session, args.target, args.adb)
        if args.command == "unlock":
            result = onn2026.unlock(device, args.accept_data_loss)
        else:
            result = verify(device)
        print(json.dumps(result, indent=2))
        print("Receipts:", session.path)
        return 0
    except (onn2026.Stop, OSError, ValueError, KeyError, IndexError) as error:
        onn2026.save(session.path / "stopped.json",
                     {"at": onn2026.now(), "reason": str(error), "automatic_retry": False})
        print("STOP:", error, "\nReceipts:", session.path, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
