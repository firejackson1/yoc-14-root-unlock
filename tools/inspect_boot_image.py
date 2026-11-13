#!/usr/bin/env python3
"""Inspect Android boot / vendor_boot images; decide the Magisk patch target.

Read-only analysis for host use. Parses Android boot image headers
(boot v0-v4 and vendor_boot v3/v4), extracts the kernel, ramdisk and dtb
sections, detects ramdisk compression by magic (gzip, lz4 frame,
lz4_legacy, lzop, bzip2, xz, zstd, raw cpio), lists cpio (newc) entries,
and reports which partition carries the generic ramdisk (the one with init).

Section layout: AOSP v3/v4 headers put data immediately after the header
with no padding, but Amlogic/BSP images zero-pad the first section to 4096
and later sections to the header page size. Section starts are therefore
detected from content (zero padding vs. non-zero start) at the header size
and at following 2048-byte boundaries, and the detected offsets reported.

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
DTB_MAGIC = b"\xd0\x0d\xfe\xed"


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
        if offset >= ml:
            out += out[start:start + ml]
        else:
            # overlapping match: repeat the last `offset` bytes
            chunk = bytes(out[start:])
            out += (chunk * (ml // offset + 1))[:ml]
    return bytes(out)


LZ4_LEGACY_MAGIC = 0x184C2102


def lz4_legacy_decompress(data, maxout=None):
    """LZ4 legacy framing: magic 0x184C2102, then [u32 size][block]*.

    Vendor_boot ramdisks may concatenate several lz4_legacy streams with no
    terminators: a stream ends exactly where the next magic begins, so a
    block-size field that reads as the magic is treated as a new stream.
    Stops after `maxout` decompressed bytes when given (magic checks)."""
    if data[:4] != b"\x02\x21\x4c\x18":
        raise ValueError("not an LZ4 legacy stream")
    out = bytearray()
    pos = 4
    while pos + 4 <= len(data):
        size = u32(data, pos)
        pos += 4
        if size == LZ4_LEGACY_MAGIC:
            continue  # concatenated stream boundary
        if size == 0 or size >= 0x80000000 or size > 64 * 1024 * 1024:
            break
        if pos + size > len(data):
            raise ValueError("LZ4 legacy block past end")
        out += lz4_block_decompress(data[pos:pos + size])
        pos += size
        if maxout is not None and len(out) >= maxout:
            break
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


def kernel_check(blob):
    """(compression, is_arm64) — decompress only enough to check the magic.

    arm64 Image magic is "ARM\\x64" at offset 0x38; EFI-stub kernels start MZ.
    """
    def looks_arm64(d):
        return d[0x38:0x3c] == b"ARM\x64" or d[:2] == b"MZ"

    if blob[:4] == b"\x02\x21\x4c\x18":
        head = lz4_legacy_decompress(blob, maxout=4096)
        return "lz4_legacy", looks_arm64(head)
    if blob[:2] == b"\x1f\x8b":
        d = gzip.decompress(blob)
        return "gzip", looks_arm64(d)
    if blob[:4] == b"\x04\x22\x4d\x18":
        d = lz4_frame_decompress(blob)
        return "lz4", looks_arm64(d)
    if blob[:2] == b"MZ" or blob[0x38:0x3c] == b"ARM\x64":
        return "raw", True
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
        # do not break at TRAILER!!!: concatenated vendor_ramdisk fragments
        # continue with another 070701 header; the loop breaks on its own
        # when the next bytes are not a cpio header
        pos = (data_start + fsize + 3) & ~3
    return entries


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
    return {
        "entry_count": len(entries),
        "has_init": "init" in interesting,
        "init": interesting.get("init"),
        "fstab": interesting.get("fstab", [])[:4],
        "sample": names[:limit],
    }


def _section_start(data, min_off):
    """Offset where the next section's content begins at or after min_off.

    AOSP v3/v4 images place sections contiguously (content right at
    min_off); Amlogic/BSP images zero-pad to 4096 (first section) and to
    the header page size (later sections). Every 4096 boundary is also a
    2048 boundary, so scanning 2048 multiples covers both. Returns the
    first candidate whose first bytes are non-zero, else min_off.
    """
    if min_off >= len(data):
        return min_off
    if any(data[min_off:min_off + 8]):
        return min_off
    cand = (min_off + 2047) // 2048 * 2048
    end = min(len(data), min_off + 16384)
    while cand < end:
        if any(data[cand:cand + 8]):
            return cand
        cand += 2048
    return min_off


def parse_boot(data):
    if data[:8] != BOOT_MAGIC:
        raise ValueError("not an Android boot image (missing ANDROID! magic)")
    hv = u32(data, 40)
    h = {"header_version": hv, "sections": {}, "offsets": {}}
    if hv in (0, 1, 2):
        h.update({
            "kernel_size": u32(data, 8),
            "kernel_addr": u32(data, 12),
            "ramdisk_size": u32(data, 16),
            "ramdisk_addr": u32(data, 20),
            "second_size": u32(data, 24),
            "tags_addr": u32(data, 32),
            "page_size": u32(data, 36),
            "os_version": u32(data, 44),
        })
        page = h["page_size"] if h["page_size"] in (512, 1024, 2048, 4096) else 4096
        h["page_size"] = page
        pos = page  # header occupies one page for v0-v2
        h["offsets"]["kernel"] = pos
        h["sections"]["kernel"] = data[pos:pos + h["kernel_size"]]
        pos = _section_start(data, pos + h["kernel_size"]) if h["ramdisk_size"] else pos
        h["offsets"]["ramdisk"] = pos
        h["sections"]["ramdisk"] = data[pos:pos + h["ramdisk_size"]]
    else:
        # v3/v4: magic, kernel_size@8, ramdisk_size@12, os_version@16,
        # header_size@20, reserved[4]@24, header_version@40, cmdline@44.
        h.update({
            "kernel_size": u32(data, 8),
            "ramdisk_size": u32(data, 12),
            "os_version": u32(data, 16),
            "header_size": u32(data, 20),
            "cmdline": data[44:44 + 1536].split(b"\0")[0].decode(errors="replace"),
        })
        if hv == 4:
            h["boot_signature_size"] = u32(data, 1580)
        pos = _section_start(data, h["header_size"])
        h["offsets"]["kernel"] = pos
        h["sections"]["kernel"] = data[pos:pos + h["kernel_size"]]
        if h["ramdisk_size"]:
            rpos = _section_start(data, pos + h["kernel_size"])
            h["offsets"]["ramdisk"] = rpos
            h["sections"]["ramdisk"] = data[rpos:rpos + h["ramdisk_size"]]
        else:
            h["sections"]["ramdisk"] = b""
    k = h["sections"]["kernel"]
    h["kernel_magic"] = k[:4].hex() if k else None
    return h


def parse_vendor_boot(data):
    if data[:8] != VENDOR_BOOT_MAGIC:
        raise ValueError("not a vendor_boot image (missing VNDRBOOT magic)")
    h = {
        "header_version": u32(data, 8),
        "page_size": u32(data, 12),
        "kernel_addr": u32(data, 16),
        "ramdisk_addr": u32(data, 20),
        "vendor_ramdisk_size": u32(data, 24),
        "cmdline": data[28:28 + 2048].split(b"\0")[0].decode(errors="replace"),
        "tags_addr": u32(data, 2076),
        "name": data[2080:2096].split(b"\0")[0].decode(errors="replace"),
        "header_size": u32(data, 2096),
        "dtb_size": u32(data, 2100),
        "dtb_addr": u64(data, 2104),
        "sections": {},
        "offsets": {},
    }
    if h["header_version"] >= 4:
        h["bootconfig_size"] = u32(data, 2112)
    pos = _section_start(data, h["header_size"])
    h["offsets"]["vendor_ramdisk"] = pos
    h["sections"]["vendor_ramdisk"] = data[pos:pos + h["vendor_ramdisk_size"]]
    if h["dtb_size"]:
        dpos = _section_start(data, pos + h["vendor_ramdisk_size"])
        h["offsets"]["dtb"] = dpos
        h["sections"]["dtb"] = data[dpos:dpos + h["dtb_size"]]
    else:
        h["sections"]["dtb"] = b""
    if "bootconfig_size" in h and h["bootconfig_size"]:
        bpos = _section_start(data, h["offsets"].get("dtb", pos) + h["dtb_size"])
        h["offsets"]["bootconfig"] = bpos
        h["sections"]["bootconfig"] = data[bpos:bpos + h["bootconfig_size"]]
    return h


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
                entry["page_size"] = h["page_size"]
                entry["header_size"] = h["header_size"]
                entry["cmdline"] = h["cmdline"]
                entry["name"] = h["name"]
                entry["vendor_ramdisk_size"] = h["vendor_ramdisk_size"]
                entry["dtb_size"] = h["dtb_size"]
                if "bootconfig_size" in h:
                    entry["bootconfig_size"] = h["bootconfig_size"]
                entry["offsets"] = h["offsets"]
                dtb = h["sections"]["dtb"]
                if dtb[:3] == b"BZh":
                    # Amlogic packs a bzip2-compressed dtb in vendor_boot
                    try:
                        dtb = bz2.decompress(dtb)
                        entry["dtb_compression"] = "bzip2"
                    except Exception:
                        entry["dtb_compression"] = "bzip2 (undecoded)"
                if dtb[:4] == b"AML_":
                    # Amlogic multi-dtb container: version, count, then a
                    # table of raw dtbs (each starting d00dfeed)
                    version, count = u32(dtb, 4), u32(dtb, 8)
                    occ, pos = [], 0
                    while True:
                        i = dtb.find(DTB_MAGIC, pos)
                        if i < 0:
                            break
                        occ.append(i)
                        pos = i + 1
                    entry["dtb_format"] = (f"amlogic-multi-dtb v{version} "
                                         f"({count} entries, {len(occ)} dtb magics)")
                    entry["dtb_magic_ok"] = len(occ) >= 1
                else:
                    entry["dtb_magic_ok"] = bool(dtb) and dtb[:4] == DTB_MAGIC
                blob = h["sections"]["vendor_ramdisk"]
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
                if "page_size" in h:
                    entry["page_size"] = h["page_size"]
                if "header_size" in h:
                    entry["header_size"] = h["header_size"]
                if "cmdline" in h:
                    entry["cmdline"] = h["cmdline"]
                entry["kernel_size"] = h["kernel_size"]
                entry["ramdisk_size"] = h["ramdisk_size"]
                entry["offsets"] = h["offsets"]
                entry["kernel_magic"] = h["kernel_magic"]
                kblob = h["sections"]["kernel"]
                if kblob:
                    comp, arm64 = kernel_check(kblob)
                    entry["kernel_compression"] = comp
                    entry["kernel_is_arm64"] = arm64
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
