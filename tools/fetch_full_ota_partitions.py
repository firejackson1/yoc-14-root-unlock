#!/usr/bin/env python3
"""Extract stock partition images from a full A/B OTA via HTTP range requests.

Given only the OTA zip URL, reads the zip central directory, the payload
header and manifest, and just the operation blobs needed for the requested
partitions (REPLACE, REPLACE_BZ, REPLACE_XZ, ZERO; delta operations are
rejected), then reassembles each partition image. Verified in order:
payload metadata SHA-256 against payload_properties.txt, every operation
blob SHA-256 present in the manifest, complete destination-block coverage,
and each final partition SHA-256 against both the payload manifest and the
expected-hashes table generated earlier from Google-signed delta manifests.

Nothing here touches an Android device, and no vendor signing key is
authenticated: byte-exact agreement with the expected-hashes table is the
authenticity standard (see docs/FIRMWARE-SOURCES.md).
"""
import argparse
import base64
import bz2
import hashlib
import json
import lzma
import re
import secrets
import struct
import sys
import time
import urllib.request

OPT_NAME = {0: "REPLACE", 1: "REPLACE_BZ", 2: "MOVE", 3: "BSDIFF", 4: "SOURCE_COPY",
            5: "SOURCE_BSDIFF", 6: "ZERO", 7: "DISCARD", 8: "REPLACE_XZ", 9: "PUFFDIFF",
            10: "BROTLI", 11: "ZUCCHINI"}
SUPPORTED = (0, 1, 6, 8)
DECODE = {0: lambda b: b, 1: bz2.decompress, 8: lzma.decompress}


def varint(data, offset):
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            raise ValueError("Truncated protobuf varint")
        byte = data[offset]
        offset += 1
        value |= (byte & 127) << shift
        if not byte & 128:
            return value, offset
    raise ValueError("Oversized protobuf varint")


def fields(data):
    result = {}
    pos = 0
    while pos < len(data):
        tag, pos = varint(data, pos)
        number, wire = tag >> 3, tag & 7
        if not number:
            raise ValueError("Invalid protobuf field")
        if wire == 0:
            value, pos = varint(data, pos)
        elif wire in (1, 2, 5):
            if wire == 2:
                size, pos = varint(data, pos)
            else:
                size = {1: 8, 5: 4}[wire]
            if pos + size > len(data):
                raise ValueError("Truncated protobuf field")
            value = data[pos:pos + size]
            pos += size
        else:
            raise ValueError(f"Unsupported protobuf wire type {wire}")
        result.setdefault(number, []).append(value)
    return result


def one(message, key, default=None):
    values = message.get(key, [default])
    if len(values) != 1:
        raise ValueError(f"Duplicate scalar field {key}")
    return values[0]


def partition_info(raw):
    if raw is None:
        return None
    message = fields(raw)
    return {"size": one(message, 1), "sha256": one(message, 2).hex()}


class Ranged:
    """HTTP range reader that refuses anything but 206 partial content."""

    def __init__(self, url, attempts=4):
        self.url = url
        self.attempts = attempts
        self.requests = 0
        self.fetched = 0
        self._size = None

    def _open(self, headers):
        request = urllib.request.Request(
            self.url, headers={"User-Agent": "onn-yoc-ota-range/1.0", **headers})
        response = urllib.request.urlopen(request, timeout=240)
        if response.status != 206:
            response.close()
            raise ValueError(f"expected HTTP 206, got {response.status}")
        return response

    def get(self, start, end):
        """Fetch the inclusive byte range [start, end]."""
        last = None
        for attempt in range(self.attempts):
            try:
                with self._open({"Range": f"bytes={start}-{end}"}) as response:
                    data = response.read()
                if len(data) != end - start + 1:
                    raise ValueError(f"range {start}-{end}: got {len(data)} bytes")
                self.requests += 1
                self.fetched += len(data)
                return data
            except Exception as exc:
                last = exc
                time.sleep(2 * (attempt + 1))
        raise ValueError(f"range {start}-{end} failed after {self.attempts} attempts: {last}")

    def size(self):
        """Total byte length via a 0-0 probe's Content-Range header."""
        if self._size is None:
            with self._open({"Range": "bytes=0-0"}) as response:
                content_range = response.headers.get("Content-Range", "")
                response.read(1)
            match = re.fullmatch(r"bytes 0-0/(\d+)", content_range.strip())
            if not match:
                raise ValueError(f"unexpected Content-Range {content_range!r}")
            self._size = int(match.group(1))
        return self._size

    def tail(self, n):
        """Fetch the final n bytes using an explicit range."""
        total = self.size()
        if n > total:
            raise ValueError(f"tail of {n} exceeds file size {total}")
        return self.get(total - n, total - 1)


def read_zip_entries(ranged):
    """Parse the zip central directory into name -> entry metadata."""
    tail = ranged.tail(65536)
    eocd = tail.rfind(b"PK\x05\x06")
    if eocd < 0:
        raise ValueError("no end-of-central-directory record in zip tail")
    (sig, disk, cd_disk, disk_entries, total_entries, cd_size, cd_offset,
     comment_len) = struct.unpack_from("<4sHHHHIIH", tail, eocd)
    if cd_size == 0xFFFFFFFF or cd_offset == 0xFFFFFFFF:
        raise ValueError("ZIP64 central directory not supported")
    directory = ranged.get(cd_offset, cd_offset + cd_size - 1)
    entries = {}
    pos = 0
    while pos < len(directory):
        if directory[pos:pos + 4] != b"PK\x01\x02":
            raise ValueError("bad central directory entry magic")
        (sig, vmade, vneed, flags, method, mtime, mdate, crc, csize, usize,
         nlen, elen, clen, dsk, iattr, eattr, lh) = struct.unpack_from(
            "<4sHHHHHHIIIHHHHHII", directory, pos)
        name = directory[pos + 46:pos + 46 + nlen].decode()
        entries[name] = {"method": method, "csize": csize, "usize": usize,
                        "local_header": lh}
        pos += 46 + nlen + elen + clen
    return entries


def entry_data_offset(ranged, name, entry):
    """Data offset of a stored entry, confirmed via its local file header."""
    lh = entry["local_header"]
    head = ranged.get(lh, lh + 127)
    if head[:4] != b"PK\x03\x04":
        raise ValueError(f"bad local header magic for {name}")
    nlen, elen = struct.unpack_from("<HH", head, 26)
    need = 30 + nlen + elen
    if len(head) < need:
        head = ranged.get(lh, lh + need - 1)
    if head[30:30 + nlen].decode() != name:
        raise ValueError(f"central/local name mismatch for {name}")
    return lh + need


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True,
                        help="full A/B OTA zip URL (must honor range requests)")
    parser.add_argument("partitions", nargs="+",
                        help="partition names to extract, e.g. boot vendor_boot vbmeta")
    parser.add_argument("--expected", required=True,
                        help="JSON mapping partition name to {size, sha256} validation values")
    parser.add_argument("--images-dir", required=True, help="output directory for <name>.img")
    parser.add_argument("--receipt-dir", required=True, help="directory for the receipt JSON")
    args = parser.parse_args()

    for name in args.partitions:
        if not re.fullmatch(r"[a-zA-Z0-9_]+", name):
            raise ValueError(f"Invalid partition name {name!r}")
    wanted = set(args.partitions)
    expected = json.loads(open(args.expected, encoding="utf-8").read())
    missing = wanted - set(expected)
    if missing:
        raise ValueError(f"Partitions absent from expected table: {sorted(missing)}")

    images_dir = __import__("pathlib").Path(args.images_dir)
    receipt_dir = __import__("pathlib").Path(args.receipt_dir)
    images_dir.mkdir(parents=True, exist_ok=True)
    receipt_dir.mkdir(parents=True, exist_ok=True)
    for name in wanted:
        if (images_dir / f"{name}.img").exists():
            raise ValueError(f"Refusing to overwrite {name}.img")

    ranged = Ranged(args.url)
    entries = read_zip_entries(ranged)
    for required in ("payload.bin", "payload_properties.txt"):
        if required not in entries:
            raise ValueError(f"zip is missing {required}")
        if entries[required]["method"] != 0:
            raise ValueError(f"{required} is not stored uncompressed")

    props_offset = entry_data_offset(ranged, "payload_properties.txt",
                                     entries["payload_properties.txt"])
    props_raw = ranged.get(props_offset,
                           props_offset + entries["payload_properties.txt"]["usize"] - 1)
    props = dict(line.split("=", 1) for line in props_raw.decode().splitlines())
    for key in ("FILE_HASH", "FILE_SIZE", "METADATA_HASH", "METADATA_SIZE"):
        if key not in props:
            raise ValueError(f"payload_properties.txt missing {key}")

    payload_offset = entry_data_offset(ranged, "payload.bin", entries["payload.bin"])
    header = ranged.get(payload_offset, payload_offset + 23)
    magic, major, manifest_size, signature_size = struct.unpack(">4sQQI", header)
    if magic != b"CrAU" or major != 2 or not 0 < manifest_size <= 16 * 1024 * 1024:
        raise ValueError("Unsupported payload header")
    metadata = ranged.get(payload_offset, payload_offset + 24 + manifest_size - 1)
    metadata_verified = False
    if int(props["METADATA_SIZE"]) != 24 + manifest_size:
        raise ValueError("METADATA_SIZE disagrees with payload header")
    if hashlib.sha256(metadata).digest() != base64.b64decode(props["METADATA_HASH"]):
        raise ValueError("Metadata SHA-256 mismatch")
    metadata_verified = True

    manifest = fields(metadata[24:])
    if one(manifest, 12, 0) != 0:
        raise ValueError("Delta payload: this tool only extracts full payloads")
    block = one(manifest, 3, 4096)
    data_start = payload_offset + 24 + manifest_size + signature_size

    receipt = {
        "tool": "tools/fetch_full_ota_partitions.py",
        "url": args.url,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "zip_entries": {k: v for k, v in entries.items()
                        if k in ("payload.bin", "payload_properties.txt")},
        "payload": {
            "major_version": major,
            "manifest_size": manifest_size,
            "metadata_signature_size": signature_size,
            "block_size": block,
            "metadata_sha256_verified": metadata_verified,
            "file_size": int(props["FILE_SIZE"]),
            "file_sha256_verified": False,
        },
        "expected_table": args.expected,
        "partitions": [],
        "http": {"requests": 0, "bytes_fetched": 0},
    }

    for raw in manifest.get(13, []):
        part = fields(raw)
        name = one(part, 1).decode()
        if name not in wanted:
            continue
        operations = [fields(op) for op in part.get(8, [])]
        new_info = partition_info(one(part, 7))
        size = new_info["size"]
        if not 0 < size <= 512 * 1024 * 1024 or size % block:
            raise ValueError(f"Unsupported size for {name}: {size}")
        image = bytearray(size)
        coverage = bytearray(size // block)
        data_ops = [(one(op, 2, 0), one(op, 3, 0)) for op in operations
                    if one(op, 3, 0) > 0]
        if data_ops:
            lo = min(off for off, _ in data_ops)
            hi = max(off + ln for off, ln in data_ops)
            blob_region = ranged.get(data_start + lo, data_start + hi - 1)
        else:
            lo, hi, blob_region = 0, 0, b""
        op_hashes_verified = 0
        for operation in operations:
            kind = one(operation, 1)
            if kind not in SUPPORTED:
                raise ValueError(f"Unsupported operation {OPT_NAME.get(kind, kind)} in {name}")
            extents = [fields(e) for e in operation.get(6, [])]
            extent_bytes = sum(one(e, 2) for e in extents) * block
            if kind == 6:
                blob = bytes(extent_bytes)
            else:
                off, ln = one(operation, 2, 0), one(operation, 3, 0)
                if off < lo or off - lo + ln > len(blob_region):
                    raise ValueError(f"Operation data out of fetched region in {name}")
                compressed = blob_region[off - lo:off - lo + ln]
                op_hash = one(operation, 8, None)
                if isinstance(op_hash, bytes) and len(op_hash) == 32:
                    if hashlib.sha256(compressed).digest() != op_hash:
                        raise ValueError(f"Operation blob SHA-256 mismatch in {name}")
                    op_hashes_verified += 1
                blob = DECODE[kind](compressed)
            if len(blob) > extent_bytes:
                raise ValueError(f"Decoded data exceeds extents in {name}")
            blob += bytes(extent_bytes - len(blob))
            pos = 0
            for extent in extents:
                start, count = one(extent, 1), one(extent, 2)
                if start + count > len(coverage) or any(coverage[start:start + count]):
                    raise ValueError(f"Out-of-bounds or overlapping extent in {name}")
                coverage[start:start + count] = bytes([1]) * count
                length = count * block
                image[start * block:(start + count) * block] = blob[pos:pos + length]
                pos += length
        if not all(coverage):
            raise ValueError(f"Incomplete block coverage for {name}")
        actual = hashlib.sha256(image).hexdigest()
        if actual != new_info["sha256"]:
            raise ValueError(f"Partition SHA-256 mismatch vs manifest for {name}")
        if size != expected[name]["size"] or actual != expected[name]["sha256"].lower():
            raise ValueError(f"Partition SHA-256 mismatch vs expected table for {name}")
        out = images_dir / f"{name}.img"
        with out.open("xb") as target:
            target.write(image)
        receipt["partitions"].append({
            "name": name, "file": str(out), "bytes": size, "sha256": actual,
            "operations": len(operations),
            "operation_types": sorted({OPT_NAME.get(one(op, 1), one(op, 1))
                                       for op in operations}),
            "operation_hashes_verified": op_hashes_verified,
            "all_blocks_covered": True,
            "partition_hash_verified": True,
            "expected_table_match": True,
        })
        print(json.dumps(receipt["partitions"][-1]), flush=True)

    missing = wanted - {p["name"] for p in receipt["partitions"]}
    if missing:
        raise ValueError(f"Partitions missing from payload: {sorted(missing)}")
    receipt["http"] = {"requests": ranged.requests, "bytes_fetched": ranged.fetched}
    receipt["images_dir"] = str(images_dir)
    (receipt_dir / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n",
                                              encoding="utf-8")
    print(f"receipt: {receipt_dir / 'receipt.json'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
