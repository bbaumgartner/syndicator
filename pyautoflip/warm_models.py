from __future__ import annotations

import hashlib
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from insightface.app import FaceAnalysis


NAME = "buffalo_s"
URL = "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_s.zip"
SHA256 = "d85a87f503f691807cd8bb97128bdf7a0660326cd9cd02657127fa978bab8b5e"
MODEL_DIR = Path.home() / ".insightface" / "models" / NAME


def install() -> None:
    if MODEL_DIR.is_dir():
        return
    MODEL_DIR.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".zip") as archive:
        digest = hashlib.sha256()
        with urllib.request.urlopen(URL) as response:
            while chunk := response.read(1024 * 1024):
                archive.write(chunk)
                digest.update(chunk)
        archive.flush()
        actual = digest.hexdigest()
        if actual != SHA256:
            raise RuntimeError(f"{NAME} checksum mismatch: {actual}")
        temporary = Path(tempfile.mkdtemp(dir=MODEL_DIR.parent))
        try:
            with zipfile.ZipFile(archive.name) as bundle:
                bundle.extractall(temporary)
            temporary.rename(MODEL_DIR)
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise


install()
app = FaceAnalysis(name=NAME, providers=["CPUExecutionProvider"])
app.prepare(ctx_id=-1, det_size=(640, 640))
print(f"insightface {NAME} ready ({SHA256})")
