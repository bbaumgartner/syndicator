"""Per-post, per-channel state.

State lives as one JSON file per post in ``<saillog>/.syndicator/state/`` —
inside the Syncthing-synced Logseq folder, so both machines share it. One
file per slug keeps Syncthing conflicts practically impossible; writes are
atomic (temp file + rename).

Channel status lifecycle: ``pending`` → ``exported`` → ``published``.
"""

from __future__ import annotations

import json
import os
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from .config import ALL_CHANNELS

ChannelStatus = Literal["pending", "exported", "published"]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ChannelState(BaseModel):
    status: ChannelStatus = "pending"
    at: str = ""  # timestamp of the last status change
    source_hash: str = ""  # content hash the channel last processed


class PostState(BaseModel):
    slug: str
    source_hash: str = ""  # content hash at last pipeline run
    title: str = ""
    date: str = ""
    channels: dict[str, ChannelState] = {}
    translations: dict[str, str] = {}  # lang -> source hash the translation was made from

    def channel(self, name: str) -> ChannelState:
        if name not in self.channels:
            self.channels[name] = ChannelState()
        return self.channels[name]


def _slug_filename(slug: str) -> str:
    return f"{slug}.json"


class StateStore:
    def __init__(self, state_dir: Path):
        self.state_dir = state_dir

    def load(self, slug: str) -> PostState:
        path = self.state_dir / _slug_filename(slug)
        if path.exists():
            state = PostState.model_validate_json(path.read_text(encoding="utf-8"))
        else:
            state = PostState(slug=slug)
        for channel in ALL_CHANNELS:
            state.channel(channel)
        return state

    def save(self, state: PostState) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path = self.state_dir / _slug_filename(state.slug)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def all(self) -> list[PostState]:
        if not self.state_dir.exists():
            return []
        states = []
        for path in sorted(self.state_dir.glob("*.json")):
            states.append(PostState.model_validate_json(path.read_text(encoding="utf-8")))
        return states

    def mark(self, slug: str, channel: str, status: ChannelStatus, source_hash: str = "") -> PostState:
        state = self.load(slug)
        ch = state.channel(channel)
        ch.status = status
        ch.at = now_iso()
        if source_hash:
            ch.source_hash = source_hash
        self.save(state)
        return state


class PipelineLock:
    """Simple cross-machine lock file with TTL inside the synced data dir.

    Prevents the Mac and the server from processing simultaneously. Not a
    perfect distributed lock (Syncthing sync lag), but combined with
    idempotent state checks it is good enough for a two-machine setup.
    """

    def __init__(self, data_dir: Path, ttl_seconds: int = 3600):
        self.path = data_dir / "lock.json"
        self.ttl = ttl_seconds

    def acquire(self) -> bool:
        if self.path.exists():
            try:
                info = json.loads(self.path.read_text(encoding="utf-8"))
                if time.time() - info.get("ts", 0) < self.ttl and info.get("host") != socket.gethostname():
                    return False
            except (json.JSONDecodeError, OSError):
                pass
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({"host": socket.gethostname(), "pid": os.getpid(), "ts": time.time()}),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)
        return True

    def release(self) -> None:
        try:
            info = json.loads(self.path.read_text(encoding="utf-8"))
            if info.get("host") == socket.gethostname():
                self.path.unlink()
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass

    def __enter__(self) -> "PipelineLock":
        if not self.acquire():
            raise RuntimeError(f"pipeline lock held by another machine: {self.path}")
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
