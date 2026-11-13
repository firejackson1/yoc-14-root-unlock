# Firmware sources and offline validation for build 15051976

Root needs stock images of the exact running build
(`onn/onn_4k_gtv/YOC:14/URO4.260304.011.B1/15051976:user/release-keys`).
Neither Google OTA delta that touches this build carries full `boot` or
`vendor_boot` images (both are source-copy deltas), so a stock image had to
be sourced elsewhere and *proven* to be this build before anything is
patched or flashed. This page records the validation standard and the
evidence behind it. The comparison itself is read-only; no device was
touched.

**Status (2026-11-13): sourced and validated.** Google's own full OTA
for 15051976 (lineage entry #4 below) was located through a third-party
tracker's stored checkin result; `boot`, `vendor_boot`, `vbmeta`,
`vbmeta_system` and `dtbo` were then extracted from it over HTTP range
requests with `tools/fetch_full_ota_partitions.py` (receipt:
`work/20261113T034457Z-stock-image-fetch-29615442/`). All five images
match this table byte-exactly. The images live outside git in
`cache/stock-15051976/`.

The table below is generated from
`work/20261113T023909Z-firmware-crosscheck-9ee2c82c/build-15051976-validated-partitions.json`;
do not hand-edit the hashes.

## YOC OTA lineage

| # | From → to | Type | Package | Local status |
|---|-----------|------|---------|--------------|
| 1 | `11261715` (A12) → `14238279` (A14 `URO1.250103.055.A2`) | FULL, 855.6 MB | `223800036cad7ec81235678349fd108473acfc7f.zip` | Not downloaded. URL and post-build per OTAChecker entry #1811 (third-party tracker, captured 2025-11-27). Must be verified on download. |
| 2 | `14238279` → **`15051976`** | Incremental, 326,474,649 B | `e07744b09b02f8e8fcc66ecc6ec0654212d66cfc.zip` | Payload manifest read completely via an HTTP range request (CrAU v2, 299,256-byte manifest). Pre/post builds confirmed locally. |
| 3 | **`15051976`** → `16202347` (`URO4.260304.092`) | Incremental, 68,950,795 B | `04719fd8a6758871adad5c6821389f4cdb7df533.zip` | Full zip held in `cache/`, sha256 `c569532273627f08d40105d5a7c150aa98b05c41d89754f270f5a8f94d4b6f63`. URL captured from this unit's own SystemUpdate log (2026-11-13). |
| 4 | `9885170` (A12) → **`15051976`** | FULL, 885,444,096 B | `f504b3fd7088e606bc8eaba9542a03709b474d62.zip` | **The exact-build stock source.** URL per OTAChecker entry #2201 (post-timestamp 2026-03-19; the tracker recorded Google serving this full to an Android 12 fingerprint in March 2026). Read via range requests only; payload metadata SHA-256 verified against its own `payload_properties.txt`; the five needed partitions extracted and hash-verified 2026-11-13. Never stored whole. |

Google's OTA URLs redirect to `redirector.gvt1.com` and do honor range
requests, which is how manifest #2 was read without storing 311 MB and how
the needed partitions of full OTA #4 were extracted without storing its
885 MB.

## Cross-manifest validation of 15051976

Two independent, Google-signed payloads describe build 15051976: delta #2
as its *post* condition and delta #3 as its *pre* condition. Every
partition listed by both manifests was compared for **exact byte length and
SHA-256**, with all values copied programmatically from the manifests
(receipts in `work/20261113T023909Z-firmware-crosscheck-9ee2c82c/`).

- 16 of 16 partitions appear in both manifests.
- **15 of 16 agree exactly** — same length, same SHA-256.
- The only divergence is `bootloader` (see provenance note below).

These are therefore the authoritative validation values for build
15051976. Google's payload hashes cover the **full partition content** —
the byte counts below are the partition lengths, not trimmed image sizes.

| Partition | Exact bytes | SHA-256 |
|---|---:|---|
| boot | 67108864 | `9573ad1de84af6bef755b6ff355983831e5864e3c40fdc4901d5ccfabcc4d092` |
| vendor_boot | 25165824 | `a06b0ac8a51a8027edbea0b358e1a453c71a38a66ddc3cdf3472534f319efc1e` |
| vbmeta | 8192 | `fa436d39375dbd3ac2d727620a17b5b8e5055be3d50d2b1421c48403c59b2251` |
| vbmeta_system | 4096 | `c33aab4f20d10bf6dcfcad7eaf56a299219b0968c299d348724f4bd124c7a91b` |
| dtbo | 2097152 | `5630b4a78af70e3b4ddcf9d4844ba3270a348d4dc231388472751dea6af08f8d` |
| bootloader | 4096000 | `b9bc2300d9328a263d1789ff08828dc6d478b71d6b27a6ffb9f6e1076fb94473` *(two OTA tracks; see below)* |
| odm | 507904 | `ff99f212eb016493ac1bfa1ca667a1171e5723d3e862e747b080f12129ad5ebe` |
| odm_dlkm | 348160 | `20636174b4456aaa9fac56673d02ec773a8526b67ff7ad6129ed38db7e41c847` |
| odm_ext | 16777216 | `ec953a700fa03d60dd41300da70b6f89f167797848b631d945f0a6599deee096` |
| oem | 33554432 | `3660417204134cd42ad76c9e74ec49f2208701c92d51f7dea3fb61c516e73e5e` |
| product | 543801344 | `2063a277d4f2bfb664b8ccfa70ebbc2bdbf4365d6d069f41bf3c8092ea3e736a` |
| system | 472686592 | `4c39ac8ec6f1d10d639e5b7ccc9cdea7dd41787e948ed4cca79421267d5c6bdf` |
| system_dlkm | 581632 | `4e8e3a892db73e8a6aac7203b9e1ec6fed8f38a062ea5d68c0d73f12be90e040` |
| system_ext | 96182272 | `5a6ad53d82ad5870e2dbfdcaebc27c5a3d05266a3d25c1868ac0520bb4bb09a4` |
| vendor | 83668992 | `ce6204cc55fa363081fecc3f0ccbe99e6dec46643751b8ff9067904ce044ba10` |
| vendor_dlkm | 11911168 | `f0730b2e44ec3b9dc1f1641faf2f448fefd1cdbf19e7392c93478af0868eda99` |

### Provenance notes

- **`bootloader` is a two-track partition.** Delta #2 installs a
  4100096-byte bootloader (sha256
  `0e8252b7f417d6b2a842ca49aac56adc4bf84dfafe34299d8b2d1d594b524583`),
  while delta #3 — the one Google actually served to our unit — expects a
  4096000-byte source bootloader
  (`b9bc2300d9328a263d1789ff08828dc6d478b71d6b27a6ffb9f6e1076fb94473`). Same 15051976
  fingerprint, different bootloader content and length: at least two OTA
  tracks exist (most plausibly hardware batches). Our unit is on the
  4096000-byte track. This is an **unresolved provenance
  split**: there is no single authoritative "15051976 bootloader". It does
  not affect rooting (we flash neither bootloader nor anything whose hash
  disagrees), and it is one more reason not to take the `16202347` update
  blindly.
- OTAChecker is a third-party tracker. Its URLs are Google `ota-api`
  package links, and the locally read manifests independently confirm the
  pre/post builds. Full OTA #1 remains undownloaded; full OTA #4 was
  located through the tracker (entry #2201) and then verified here entirely
  against Google's own payload data: the metadata SHA-256 from
  `payload_properties.txt`, per-operation SHA-256 values, and
  per-partition SHA-256 values matching this table.
- A community dump (dumps.tadiphone.dev / t.me/android_dumps message
  10231, exact fingerprint match) is a device-side `dd`, not a
  Google-signed artifact. It becomes trustworthy **only** after matching
  the table above.

## Validating a stock dump offline

A candidate image for build 15051976 is accepted only if:

1. its byte length is exactly the table value for that partition, and
2. its SHA-256 is exactly the table value.

If a dump stores a trimmed image (partition padding removed), pad it back
to the exact table length with zeros and re-check; if it still differs, it
is from a different build or OTA track and must not be patched or flashed.
Never extend this rule to "close enough": a wrong-build `boot`/`vendor_boot`
is a brick risk, and the reset-button fastboot menu is the only recovery.

## Recorded fallback (superseded; not executed)

Before OTA #4 was found, the recorded fallback was to rebuild 15051976
images from Google-signed packages: extract `14238279` images from full
OTA #1 (`2238000…`), then apply delta #2 (`e07744…`, 14238279 →
15051976). A complete parse of delta #2's manifest (cached head) shows its
`boot` uses PUFFDIFF, BROTLI, SOURCE_COPY and ZERO operations (30 ops,
4,623,237 data bytes) and its `vendor_boot` uses BROTLI, SOURCE_COPY and
ZERO (7 ops, 1,262,218 data bytes); PUFFDIFF and BROTLI blobs would have
required host-side brotli/puffdiff implementations (no pip/apt on this
host). With OTA #4 located and verified, this chain stays recorded but
unneeded; only a byte-exact match against the table above would have
counted.

## Partition-length facts for the flash phase

- `boot_a`/`boot_b`: 64 MiB partitions, image must be 67108864 B.
- `vendor_boot_a`/`vendor_boot_b`: **24 MiB** partitions (25165824 B) —
  smaller than `boot`; a patched image that outgrows it is invalid.
- `vbmeta`: 8192 B. Rooting keeps verity (`KEEPVERITY=true`) and does
  not patch vbmeta, as on the jarvis2 flow; the already-unlocked (orange)
  state is what permits the modified image.

## Ramdisk location (from the validated stock images, 2026-11-13)

`tools/inspect_boot_image.py` on the extracted stock images confirms the
generic ramdisk lives in **`boot`**: `boot` carries a 1,845,968-byte
lz4_legacy ramdisk with `init` (26 cpio entries, GKI-style first-stage
skeleton), while `vendor_boot`'s ramdisk is vendor-only (48 entries,
`first_stage_ramdisk/fstab.amlogic`, f2fs tooling, no `init`). The
Magisk patch target is therefore **`boot.img`**, not the Android 12-era
`vendor_boot`. The `boot` kernel is gzip-compressed (arm64 magic verified
after decompression); `vendor_boot` packs a bzip2-compressed Amlogic
multi-dtb (`AML_` v2, 4 dtbs) behind its ramdisk.
