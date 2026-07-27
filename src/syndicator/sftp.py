"""Resumable SFTP uploader for the chrooted staging area."""

from __future__ import annotations

import logging
import stat
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Iterator

import paramiko

from .config import Config

log = logging.getLogger(__name__)

_CHUNK = 32 * 1024


class SftpUploader:
    def __init__(self, client: paramiko.SSHClient, sftp: paramiko.SFTPClient):
        self._client = client
        self._sftp = sftp

    def _remote_size(self, remote: str) -> int | None:
        try:
            return self._sftp.stat(remote).st_size
        except FileNotFoundError:
            return None

    def ensure_dir(self, remote_dir: str) -> None:
        parts = PurePosixPath(remote_dir).parts
        current = PurePosixPath("/")
        for part in parts:
            if part == "/":
                continue
            current = current / part
            path = str(current)
            try:
                if stat.S_ISDIR(self._sftp.stat(path).st_mode):
                    continue
                raise OSError(f"remote path exists but is not a directory: {path}")
            except FileNotFoundError:
                self._sftp.mkdir(path)

    def upload(self, local_path: Path, remote_path: str) -> None:
        local_path = Path(local_path)
        local_size = local_path.stat().st_size
        self.ensure_dir(str(PurePosixPath(remote_path).parent))

        remote_size = self._remote_size(remote_path)
        if remote_size is not None and 0 < remote_size < local_size:
            self._resume(local_path, remote_path, remote_size)
        else:
            self._overwrite(local_path, remote_path)

        final = self._remote_size(remote_path)
        if final != local_size:
            raise OSError(
                f"upload size mismatch for {remote_path}: {final} != {local_size}"
            )
        log.info("uploaded %s -> %s (%d bytes)", local_path.name, remote_path, local_size)

    def _overwrite(self, local_path: Path, remote_path: str) -> None:
        with open(local_path, "rb") as src, self._sftp.open(remote_path, "wb") as dst:
            dst.set_pipelined(True)
            while chunk := src.read(_CHUNK):
                dst.write(chunk)

    def _resume(self, local_path: Path, remote_path: str, offset: int) -> None:
        log.info("resuming %s from byte %d", remote_path, offset)
        with open(local_path, "rb") as src, self._sftp.open(remote_path, "a") as dst:
            dst.set_pipelined(True)
            src.seek(offset)
            while chunk := src.read(_CHUNK):
                dst.write(chunk)


@contextmanager
def sftp_session(cfg: Config) -> Iterator[SftpUploader]:
    sftp_cfg = cfg.shared.sftp
    key_path = Path(cfg.local.sftp_key).expanduser()
    if not key_path.exists():
        raise FileNotFoundError(f"SFTP key not found: {key_path}")

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=sftp_cfg.host,
        port=sftp_cfg.port,
        username=sftp_cfg.user,
        key_filename=str(key_path),
        look_for_keys=False,
        allow_agent=False,
        timeout=30,
    )
    sftp = client.open_sftp()
    try:
        yield SftpUploader(client, sftp)
    finally:
        sftp.close()
        client.close()
