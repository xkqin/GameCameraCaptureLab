from __future__ import annotations

import argparse
import base64
import io
import json
import shlex
import struct
import subprocess
import tarfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "assets" / "v2" / "spatialvid_samples"


REMOTE_PROGRAM = r"""
import io
import json
import os
import struct
import sys
import tarfile
import zlib
from pathlib import PurePosixPath

ARCHIVE = "/data/EZCAM2/dataset.zip"
GROUPS = {"group_0001", "group_0012", "group_0024", "group_0036",
          "group_0048", "group_0060", "group_0074"}
ANNOTATION_NAMES = {
    "caption.json", "poses.npy", "intrinsics.npy", "indexes.txt",
    "instructions.json", "aesthetic_scores_0p01s.jsonl",
    "aesthetic_scores_0p01s.meta.json",
}


def resolve_zip64(extra, usize, csize, local_offset, disk):
    cursor = 0
    while cursor + 4 <= len(extra):
        tag, length = struct.unpack_from("<HH", extra, cursor)
        cursor += 4
        payload = extra[cursor:cursor + length]
        cursor += length
        if tag != 0x0001:
            continue
        offset = 0
        if usize == 0xFFFFFFFF:
            usize = struct.unpack_from("<Q", payload, offset)[0]
            offset += 8
        if csize == 0xFFFFFFFF:
            csize = struct.unpack_from("<Q", payload, offset)[0]
            offset += 8
        if local_offset == 0xFFFFFFFF:
            local_offset = struct.unpack_from("<Q", payload, offset)[0]
            offset += 8
        if disk == 0xFFFF and offset + 4 <= len(payload):
            disk = struct.unpack_from("<I", payload, offset)[0]
        break
    return usize, csize, local_offset


def archive_layout(stream):
    size = os.path.getsize(ARCHIVE)
    tail_size = min(size, 1024 * 1024)
    stream.seek(size - tail_size)
    tail = stream.read(tail_size)
    eocd = tail.rfind(b"PK\x05\x06")
    locator = tail.rfind(b"PK\x06\x07", 0, eocd)
    if eocd < 0 or locator < 0:
        raise RuntimeError("ZIP64 end records were not found")
    _, _, zip64_offset, _ = struct.unpack("<4sIQI", tail[locator:locator + 20])
    stream.seek(zip64_offset)
    values = struct.unpack("<4sQ2H2I4Q", stream.read(56))
    return values[7], values[9]


def iter_entries(stream, total, central_offset):
    stream.seek(central_offset)
    for _ in range(total):
        fixed = stream.read(46)
        if fixed[:4] != b"PK\x01\x02":
            raise RuntimeError("Invalid central-directory header")
        values = struct.unpack("<4s6H3I5H2I", fixed)
        (_, _, _, flag, method, _, _, crc, csize, usize, name_len,
         extra_len, comment_len, disk, _, _, local_offset) = values
        raw_name = stream.read(name_len)
        extra = stream.read(extra_len)
        stream.seek(comment_len, 1)
        name = raw_name.decode("utf-8" if flag & 0x800 else "cp437", errors="replace")
        usize, csize, local_offset = resolve_zip64(
            extra, usize, csize, local_offset, disk
        )
        yield {
            "name": name,
            "method": method,
            "csize": csize,
            "usize": usize,
            "local_offset": local_offset,
        }


def read_entry(stream, entry):
    stream.seek(entry["local_offset"])
    header = stream.read(30)
    if header[:4] != b"PK\x03\x04":
        raise RuntimeError("Invalid local header: " + entry["name"])
    values = struct.unpack("<4s5H3I2H", header)
    name_len, extra_len = values[-2], values[-1]
    stream.seek(name_len + extra_len, 1)
    raw = stream.read(entry["csize"])
    if entry["method"] == 0:
        data = raw
    elif entry["method"] == 8:
        data = zlib.decompress(raw, -15)
    else:
        raise RuntimeError("Unsupported ZIP method: " + str(entry["method"]))
    if len(data) != entry["usize"]:
        raise RuntimeError("Size mismatch: " + entry["name"])
    return data


with open(ARCHIVE, "rb", buffering=16 * 1024 * 1024) as archive:
    total, central_offset = archive_layout(archive)
    videos = {}
    previews = []
    annotations = {group: {} for group in GROUPS}
    for entry in iter_entries(archive, total, central_offset):
        parts = PurePosixPath(entry["name"]).parts
        if (
            len(parts) == 5
            and parts[0] == "dataset"
            and parts[1] == "spatialvid_high_aesthetic_full_0001_0074_real"
            and parts[2] == "videos"
            and parts[3] in GROUPS
            and parts[3] not in videos
            and PurePosixPath(parts[4]).suffix.lower() in {".mp4", ".webm"}
        ):
            videos[parts[3]] = entry
        if (
            len(parts) == 6
            and parts[0] == "dataset"
            and parts[1] == "spatialvid_high_aesthetic_full_0001_0074_real"
            and parts[2] == "annotations"
            and parts[3] in videos
            and parts[4] == PurePosixPath(videos[parts[3]]["name"]).stem
            and parts[5] in ANNOTATION_NAMES
        ):
            annotations[parts[3]][parts[5]] = entry
        if (
            len(previews) < 6
            and entry["name"].startswith("dataset/3DGS/videos/")
            and PurePosixPath(entry["name"]).suffix.lower() == ".png"
        ):
            previews.append(entry)

    if set(videos) != GROUPS:
        raise RuntimeError("Missing requested groups: " + repr(sorted(GROUPS - set(videos))))

    manifest = {"archive": ARCHIVE, "videos": {}, "annotations": {}, "3dgs_previews": []}
    output = sys.stdout.buffer
    with tarfile.open(fileobj=output, mode="w|") as tar:
        for group in sorted(videos):
            entry = videos[group]
            data = read_entry(archive, entry)
            suffix = PurePosixPath(entry["name"]).suffix.lower()
            member_name = "videos/" + group + suffix
            info = tarfile.TarInfo(member_name)
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
            manifest["videos"][group] = {
                "archive_path": entry["name"],
                "local_name": member_name,
                "bytes": len(data),
            }
            manifest["annotations"][group] = {}
            for basename, annotation in sorted(annotations[group].items()):
                annotation_data = read_entry(archive, annotation)
                annotation_name = "annotations/" + group + "/" + basename
                annotation_info = tarfile.TarInfo(annotation_name)
                annotation_info.size = len(annotation_data)
                annotation_info.mode = 0o644
                tar.addfile(annotation_info, io.BytesIO(annotation_data))
                manifest["annotations"][group][basename] = {
                    "archive_path": annotation["name"],
                    "local_name": annotation_name,
                    "bytes": len(annotation_data),
                }
        for index, entry in enumerate(previews, start=1):
            data = read_entry(archive, entry)
            member_name = "3dgs/preview_%02d.png" % index
            info = tarfile.TarInfo(member_name)
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
            manifest["3dgs_previews"].append({
                "archive_path": entry["name"],
                "local_name": member_name,
                "bytes": len(data),
            })
        payload = json.dumps(manifest, indent=2).encode("utf-8")
        info = tarfile.TarInfo("selection_manifest.json")
        info.size = len(payload)
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(payload))
"""


def safe_extract(stream, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    root = output.resolve()
    with tarfile.open(fileobj=stream, mode="r|") as archive:
        for member in archive:
            target = (output / member.name).resolve()
            if root not in target.parents and target != root:
                raise RuntimeError(f"Unsafe archive path: {member.name}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise RuntimeError(f"Unable to read archive member: {member.name}")
            with target.open("wb") as destination:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    destination.write(chunk)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stream representative SpatialVID-HQ and 3DGS samples from the cluster ZIP."
    )
    parser.add_argument("--host", default="mydev")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    encoded = base64.b64encode(REMOTE_PROGRAM.encode("utf-8")).decode("ascii")
    loader = f"import base64;exec(base64.b64decode('{encoded}'))"
    remote_command = "python3 -c " + shlex.quote(loader)
    process = subprocess.Popen(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
         args.host, remote_command],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdout is not None
    try:
        safe_extract(process.stdout, args.output)
    except Exception as exc:
        stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
        process.wait()
        raise RuntimeError(f"Unable to read remote sample stream: {stderr}") from exc
    stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
    return_code = process.wait()
    if return_code:
        raise SystemExit(f"Remote extraction failed ({return_code}): {stderr}")
    print(args.output)


if __name__ == "__main__":
    main()
