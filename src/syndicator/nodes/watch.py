"""watch node: daemon mode — trigger the pipeline on Logseq changes.

Watches journals/, pages/ and assets/ via watchdog. Syncthing-safe: ignores
its own write targets (review pages ``syndicator___*.md``, adapted media in
``assets/syndicator/``), Syncthing temp files and version stores, and Logseq
backups — otherwise our own writes would re-trigger the watcher in an
endless loop. Status edits on review pages need no pipeline run either;
they are read on the next run.

Debounce: the pipeline runs once no event has arrived for
``watch.debounce_seconds`` (edits often come in bursts, both locally and
via Syncthing sync).
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from ..config import Config
from ..state import PAGE_PREFIX

log = logging.getLogger(__name__)

# ".syndicator" stays ignored so deleting the legacy data dir at cutover
# does not trigger a pipeline run.
IGNORE_PARTS = (PAGE_PREFIX, ".syndicator", ".stversions", "bak", ".trash", ".recycle")
IGNORE_PREFIXES = (f"{PAGE_PREFIX}___",)  # review pages written by the pipeline
IGNORE_SUBSTRINGS = (".syncthing.", "~syncthing~")
IGNORE_SUFFIXES = (".tmp", ".swp", ".part")


def is_relevant_path(path_str: str) -> bool:
    path = Path(path_str)
    parts = set(path.parts)
    if parts & set(IGNORE_PARTS):
        return False
    name = path.name
    if name.startswith("."):
        return False
    if name.startswith(IGNORE_PREFIXES):
        return False
    if any(s in name for s in IGNORE_SUBSTRINGS):
        return False
    if name.endswith(IGNORE_SUFFIXES):
        return False
    return True


class _Handler(FileSystemEventHandler):
    def __init__(self, on_event: Callable[[str], None]):
        self.on_event = on_event

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        for p in filter(None, [getattr(event, "src_path", None), getattr(event, "dest_path", None)]):
            if is_relevant_path(str(p)):
                self.on_event(str(p))
                return


def watch(cfg: Config, run_pipeline: Callable[[], None], run_on_start: bool = True) -> None:
    """Blocking watch loop; calls run_pipeline() after debounced changes."""
    debounce = cfg.shared.watch.debounce_seconds
    last_event = threading.Event()
    last_time: list[float] = [0.0]

    def on_event(path: str) -> None:
        log.info("change detected: %s", path)
        last_time[0] = time.monotonic()
        last_event.set()

    observer = Observer()
    handler = _Handler(on_event)
    watch_dirs = [cfg.journals_dir, cfg.pages_dir, cfg.local.saillog_dir / "assets"]
    for directory in watch_dirs:
        if directory.exists():
            observer.schedule(handler, str(directory), recursive=True)
            log.info("watching %s", directory)
    observer.start()

    try:
        if run_on_start:
            _safe_run(run_pipeline)
        while True:
            last_event.wait()
            # Debounce: wait until the burst has settled.
            while time.monotonic() - last_time[0] < debounce:
                time.sleep(min(5.0, debounce))
            last_event.clear()
            _safe_run(run_pipeline)
            log.info("watching for changes ...")
    finally:
        observer.stop()
        observer.join()


def _safe_run(run_pipeline: Callable[[], None]) -> None:
    try:
        run_pipeline()
    except Exception:  # noqa: BLE001 - daemon must survive pipeline errors
        log.exception("pipeline run failed; will retry on next change")
