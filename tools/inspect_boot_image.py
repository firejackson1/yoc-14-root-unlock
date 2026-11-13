#!/usr/bin/env python3
"""Inspect Android boot / vendor_boot images; decide the Magisk patch target.

Read-only analysis for host use. Parses Android boot image headers
(boot v0-v4 and vendor_boot v3/v4), extracts the kernel, ramdisk and dtb
sections, detects ramdisk compression by magic (gzip, lz4 frame,
lz4_legacy, lzop, bzip2, xz, zstd, raw cpio), lists cpio (newc) entries,
and reports which partition carries the generic ramdisk (the one with init).

This tool deliberately writes nothing and does not authenticate a vendor
signing key; image authenticity is proven separately by matching the full
partition SHA-256 against Google's OTA payload manifest.
"""
import argparse
import bz2
import gzip
import hashlib
import json
import lzma
import struct
import sys
import zlib

BOOT_MAGIC = b"ANDROID!"
VENDOR_BOOT_MAGIC = b"VNDRBOOT"


def u32(data, off):
    return struct.unpack_from("<I", data, off)[0]


def u64(data, off):
    return struct.unpack_from("<Q", data, off)[0]


def lz4_block_decompress(src):
    """Decompress one raw LZ4 block (independent block format)."""
    out = bytearray()
    i = 0
    n = len(src)
    while i < n:
        token = src[i]
        i += 1
        lit = token >> 4
        if lit == 15:
            while i < n:
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        if i + lit > n:
            raise ValueError("LZ4 literal run past end")
        out += src[i:i + lit]
        i += lit
        if i >= n:
            break
        if i + 2 > n:
            raise ValueError("LZ4 truncated match header")
        offset = src[i] | (src[i + 1] << 8)
        i += 2
        if offset == 0:
            raise ValueError("LZ4 zero offset")
        ml = token & 15
        if ml == 15:
            while i < n:
                b = src[i]
                i += 1
                ml += b
                if b != 255:
                    break
        ml += 4
        start = len(out) - offset
        if start < 0:
            raise ValueError("LZ4 offset before output start")
        for _ in range(ml):
            out.append(out[start])
            start += 1
    return bytes(out)


def lz4_legacy_decompress(data):
    """LZ4 legacy framing: magic 0x184C2102, then [u32 size][block]*."""
    out = bytearray()
    pos = 4
    while pos + 4 <= len(data):
        size = u32(data, pos)
        pos += 4
        if size == 0 or size >= 0x80000000:
            break
        if pos + size > len(data):
            raise ValueError("LZ4 legacy block past end")
        out += lz4_block_decompress(data[pos:pos + size])
        pos += size
    return bytes(out)


def lz4_frame_decompress(data):
    """Minimal LZ4 frame (v1.x) reader for the block formats 0x40/0x60."""
    if data[:4] != b"\x04\x22\x4d\x18":
        raise ValueError("not an LZ4 frame")
    flg = data[4]
    bd = data[5]
    version = (flg >> 6) & 3
    if version != 1:
        raise ValueError("unsupported LZ4 frame version")
    block_independence = (flg >> 5) & 1
    has_block_checksum = (flg >> 4) & 1
    has_content_size = (flg >> 3) & 1
    has_content_checksum = (flg >> 2) & 1
    has_dict = flg & 1
    block_max = 1 << (2 + ((bd >> 4) & 7) * 2)
    pos = 6
    if has_content_size:
        pos += 8
    if has_dict:
        pos += 4
    out = bytearray()
    prev = bytearray()
    while pos + 4 <= len(data):
        bsize = u32(data, pos)
        pos += 4
        uncompressed = bsize & 0x80000000
        bsize &= 0x7FFFFFFF
        if bsize == 0:
            break
        if pos + bsize > len(data):
            raise ValueError("LZ4 frame block past end")
        block = data[pos:pos + bsize]
        pos += bsize
        if has_block_checksum:
            pos += 4
        if uncompressed:
            out += block
        else:
            dec = lz4_block_decompress(block)
            if block_independence:
                out += dec
            else:
                prev += dec
                out = prev
    if has_content_checksum:
        pos += 4
    return bytes(out)


def detect_and_decompress(blob):
    """Return (compression_name, decompressed_bytes_or_None)."""
    if blob[:2] == b"\x1f\x8b":
        return "gzip", gzip.decompress(blob)
    if blob[:4] == b"\x04\x22\x4d\x18":
        return "lz4", lz4_frame_decompress(blob)
    if blob[:4] == b"\x02\x21\x4c\x18":
        return "lz4_legacy", lz4_legacy_decompress(blob)
    if blob[:4] == b"\x89\x4c\x5a\x4f":
        return "lzop", None
    if blob[:3] == b"BZh":
        return "bzip2", bz2.decompress(blob)
    if blob[:6] == b"\xfd7zXZ\x00":
        return "xz", lzma.decompress(blob)
    if blob[:4] == b"\x28\xb5\x2f\xfd":
        return "zstd", None
    if blob[:6] == b"070701":
        return "raw", blob
    return "unknown", None


def cpio_newc_list(data):
    """List (name, mode, size) entries of a newc cpio archive."""
    entries = []
    pos = 0
    while pos + 110 <= len(data):
        if data[pos:pos + 6] != b"070701":
            break
        vals = []
        for i in range(13):
            chunk = data[pos + 6 + i * 8: pos + 14 + i * 8]
            vals.append(int(chunk, 16))
        (ino, mode, uid, gid, nlink, mtime, fsize, dmaj, dmin, rmaj, rmin,
         namesize, chksum) = vals
        name = data[pos + 110: pos + 110 + namesize - 1].decode(errors="replace")
        data_start = (pos + 110 + namesize + 3) & ~3
        entries.append((name, mode, fsize))
        if name == "TRAILER!!!":
            break
        pos = (data_start + fsize + 3) & ~3
    return entries


def parse_boot(data):
    if data[:8] != BOOT_MAGIC:
        raise ValueError("not an Android boot image (missing ANDROID! magic)")
    h = {
        "kernel_size": u32(data, 8),
        "kernel_addr": u32(data, 12),
        "ramdisk_size": u32(data, 16),
        "ramdisk_addr": u32(data, 20),
        "second_size": u32(data, 24),
        "second_addr": u32(data, 28),
        "tags_addr": u32(data, 32),
        "page_size": u32(data, 36),
        "header_version": u32(data, 40),
        "os_version": u32(data, 44),
    }
    page = h["page_size"] if h["page_size"] in (512, 1024, 2048, 4096) else 4096
    h["page_size"] = page
    pos = page  # header occupies one page for v0-v2
    if h["header_version"] in (0, 1, 2):
        extra = u32(data, 48)
        pos += (extra + page - 1) // page * page if extra else 0
    else:
        pos = 4096  # v3/v4 header is 1580/1584 bytes but kernel starts page-aligned
    sections = {}
    sections["kernel"] = data[pos: pos + h["kernel_size"]]
    pos += (h["kernel_size"] + page - 1) // page * page if h["kernel_size"] else 0
    sections["ramdisk"] = data[pos: pos + h["ramdisk_size"]]
    pos += (h["ramdisk_size"] + page - 1) // page * page if h["ramdisk_size"] else 0
    if h["header_version"] in (0, 1, 2):
        sections["second"] = data[pos: pos + h["second_size"]]
        pos += (h["second_size"] + page - 1) // page * page if h["second_size"] else 0
        if h["header_version"] in (1, 2):
            reco = u32(data, 48) if h["header_version"] == 1 else u32(data, 48)
            # dtb for v1/v2 lives after second; sizes: v1 dtbo? keep simple: report via header only
    h["sections"] = sections
    h["kernel_magic"] = sections["kernel"][:4].hex()
    return h


def parse_vendor_boot(data):
    if data[:8] != VENDOR_BOOT_MAGIC:
        raise ValueError("not a vendor_boot image (missing VNDRBOOT magic)")
    h = {
        "header_version": u32(data, 8),
        "page_size": u32(data, 12),
        "kernel_size": u32(data, 16),
        "ramdisk_size": u32(data, 20),
        "vendor_ramdisk_size": u32(data, 24),
        "tags_addr": u32(data, 28),
    }
    name = data[32:48].split(b"\x00")[0].decode(errors="replace")
    h["name"] = name
    h["header_size"] = u32(data, 48)
    h["dtb_size"] = u32(data, 52)
    h["dtb_addr"] = u64(data, 56)
    page = h["page_size"] if h["page_size"] in (512, 1024, 2048, 4096) else 4096
    pos = page
    h["kernel"] = data[pos: pos + h["kernel_size"]]
    pos += (h["kernel_size"] + page - 1) // page * page if h["kernel_size"] else 0
    # combined ramdisk blob (vendor fragments and, for some devices, the generic one)
    h["ramdisk_blob"] = data[pos: pos + h["ramdisk_size"]]
    pos += (h["ramdisk_size"] + page - 1) // page * page if h["ramdisk_size"] else 0
    h["dtb"] = data[pos: pos + h["dtb_size"]]
    pos += (h["dtb_size"] + page - 1) // page * page if h["dtb_size"] else 0
    if h["header_version"] >= 4:
        h["bootconfig_size"] = u32(data, 40)
    return h


def summarize_cpio(entries, limit=12):
    interesting = {}
    for name, mode, size in entries:
        base = name.rsplit("/", 1)[-1]
        if base == "init" and (mode & 0o170000) == 0o100000:
            interesting["init"] = {"size": size, "mode": oct(mode)}
        if name in ("init.rc", "system/etc/init/hw/init.rc", "etc/init/hw/init.rc"):
            interesting.setdefault("init_rc", []).append(name)
        if base == "fstab" or "fstab" in name:
            interesting.setdefault("fstab", []).append(name)
    names = [e[0] for e in entries]
    has_dirs = any(e[0] == "." for e in entries)
    return {
        "entry_count": len(entries),
        "has_init": "init" in interesting,
        "init": interesting.get("init"),
        "fstab": interesting.get("fstab", [])[:4],
        "sample": names[:limit],
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("image", nargs="+")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    report = {}
    for path in args.image:
        data = open(path, "rb").read()
        entry = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        try:
            if data[:8] == VENDOR_BOOT_MAGIC:
                h = parse_vendor_boot(data)
                entry["type"] = "vendor_boot"
                entry["header_version"] = h["header_version"]
                entry["name"] = h["name"]
                entry["kernel_size"] = h["kernel_size"]
                entry["ramdisk_size"] = h["ramdisk_size"]
                entry["dtb_size"] = h["dtb_size"]
                blob = h["ramdisk_blob"]
                if blob:
                    comp, dec = detect_and_decompress(blob)
                    entry["ramdisk_compression"] = comp
                    if dec is None:
                        entry["ramdisk_entries"] = f"undecoded ({comp})"
                    else:
                        entry["ramdisk_entries"] = summarize_cpio(cpio_newc_list(dec))
                else:
                    entry["ramdisk_compression"] = None
            else:
                h = parse_boot(data)
                entry["type"] = "boot"
                entry["header_version"] = h["header_version"]
                entry["page_size"] = h["page_size"]
                entry["kernel_size"] = h["kernel_size"]
                entry["kernel_magic"] = h["kernel_magic"]
                entry["ramdisk_size"] = h["ramdisk_size"]
                blob = h["sections"]["ramdisk"]
                if blob:
                    comp, dec = detect_and_decompress(blob)
                    entry["ramdisk_compression"] = comp
                    if dec is None:
                        entry["ramdisk_entries"] = f"undecoded ({comp})"
                    else:
                        entry["ramdisk_entries"] = summarize_cpio(cpio_newc_list(dec))
                else:
                    entry["ramdisk_compression"] = None
        except Exception as exc:  # report, do not crash the whole run
            entry["error"] = f"{type(exc).__name__}: {exc}"
        report[path] = entry
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
