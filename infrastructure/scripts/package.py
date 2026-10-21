"""Package the committed source tree, excluding ignored local configuration and runtime state."""

import hashlib
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT.parent / "DataMind-AI-source.zip"


def main():
    subprocess.run(
        ["git", "archive", "--format=zip", "--prefix=datamind-ai/", f"--output={DEST}", "HEAD"],
        cwd=ROOT,
        check=True,
    )
    with zipfile.ZipFile(DEST) as archive:
        if archive.testzip() is not None or any(
            name.endswith("/.env") or "/node_modules/" in name or "/data/" in name
            for name in archive.namelist()
        ):
            raise RuntimeError("Distribution contains an invalid or local-only entry")
    digest = hashlib.sha256(DEST.read_bytes()).hexdigest()
    DEST.with_suffix(".zip.sha256").write_text(f"{digest}  {DEST.name}\n")
    print(f"Created {DEST.name} from the committed source tree")


if __name__ == "__main__":
    main()
