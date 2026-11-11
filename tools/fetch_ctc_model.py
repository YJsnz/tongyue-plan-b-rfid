"""Download the pinned optional PC CTC model; verify hashes before publication."""

import argparse
import concurrent.futures
import hashlib
import os
import tempfile
import urllib.request
from pathlib import Path

from ctc_acoustic import MODEL_HASHES, MODEL_REPO, MODEL_REVISION, verify_model


def fetch(directory, base_url):
    directory.mkdir(parents=True, exist_ok=True)
    prefix = f"{base_url.rstrip('/')}/{MODEL_REPO}/resolve/{MODEL_REVISION}"
    # Bounded ranges avoid a slow single large response on some networks. Each
    # worker owns its part, and nothing is renamed to its final name before SHA256.
    with tempfile.TemporaryDirectory(prefix="ctc-download-", dir=directory) as temporary:
        temporary = Path(temporary)
        for name, total in (("tokens.txt", 20628), ("model.int8.onnx", 162290887)):
            if (directory / name).exists():
                with (directory / name).open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() == MODEL_HASHES[name]:
                        continue
            size = 4 * 1024 * 1024
            ranges = [(start, min(start + size, total) - 1) for start in range(0, total, size)]

            def part(bounds):
                start, end = bounds
                request = urllib.request.Request(f"{prefix}/{name}", headers={"Range": f"bytes={start}-{end}"})
                for attempt in range(3):
                    try:
                        with urllib.request.urlopen(request, timeout=30) as response:
                            content = response.read(end - start + 2)
                            if (response.status == 206 and response.headers.get("Content-Range") !=
                                    f"bytes {start}-{end}/{total}"):
                                raise ValueError("Unexpected download range")
                            if len(content) != end - start + 1 or (response.status != 206 and len(ranges) > 1):
                                raise ValueError("Incomplete or unsupported range response")
                        path = temporary / str(start)
                        path.write_bytes(content)
                        return path
                    except (OSError, ValueError):
                        if attempt == 2:
                            raise

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                paths = list(pool.map(part, ranges))
            output = temporary / name
            with output.open("wb") as stream:
                for path in paths:
                    stream.write(path.read_bytes())
                    path.unlink()
                stream.flush()
                os.fsync(stream.fileno())
            with output.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != MODEL_HASHES[name]:
                    raise ValueError(f"Downloaded CTC checksum mismatch: {name}")
            os.replace(output, directory / name)
            print(f"Verified {name}", flush=True)
    verify_model(directory)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--base-url", default="https://huggingface.co",
                        help="Optional download mirror; pinned hashes remain mandatory")
    args = parser.parse_args()
    fetch(args.directory, args.base_url)
