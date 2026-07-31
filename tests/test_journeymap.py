"""Tests for in-process journey position extraction (ported from Go journeymap)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from syndicator.journeymap import (
    CLUSTER_THRESHOLD_DEG,
    JourneyMap,
    Position,
    _EntryWithDays,
    _JournalEntry,
    cluster_positions,
    extract_positions,
    write_journey_json,
)


def _write_journal(dir: Path, day: str, content: str) -> None:
    name = f"{day[:4]}_{day[5:7]}_{day[8:10]}.md"
    (dir / name).write_text(content, encoding="utf-8")


def _entry(day: str, lat: float, lng: float, days: int) -> _EntryWithDays:
    return _EntryWithDays(
        entry=_JournalEntry(date=date.fromisoformat(day), lat=lat, lng=lng),
        days=days,
    )


# ---- extract_positions ------------------------------------------------------


def test_extract_empty_dir(tmp_path: Path):
    assert extract_positions(tmp_path).positions == []


def test_extract_no_position_property(tmp_path: Path):
    _write_journal(tmp_path, "2025-09-13", "- [[Blog]]\n\t- type:: blog\n\t  title:: No position here\n")
    assert extract_positions(tmp_path).positions == []


def test_extract_non_date_files_ignored(tmp_path: Path):
    (tmp_path / "README.md").write_text("- current-position:: 45.0,13.0\n", encoding="utf-8")
    assert extract_positions(tmp_path).positions == []


def test_extract_single_position(tmp_path: Path):
    _write_journal(tmp_path, "2025-09-13", "- current-position:: 45.5127,13.5954\n")
    positions = extract_positions(tmp_path, today=date(2025, 9, 20)).positions
    assert len(positions) == 1
    p = positions[0]
    assert p.date == "2025-09-13"
    assert p.lat == 45.5127
    assert p.lng == 13.5954
    assert p.days == 7


def test_extract_multiple_sorted_and_days(tmp_path: Path):
    _write_journal(tmp_path, "2026-01-17", "- current-position:: 43.5088,16.4402\n")
    _write_journal(tmp_path, "2025-09-13", "- current-position:: 45.5127,13.5954\n")
    positions = extract_positions(tmp_path, today=date(2026, 1, 27)).positions
    assert [p.date for p in positions] == ["2025-09-13", "2026-01-17"]
    assert positions[0].days == 126
    assert positions[1].days == 10


def test_extract_property_inside_blog_block(tmp_path: Path):
    content = "- [[Blog]]\n\t- type:: blog\n\t  current-position:: 45.5127,13.5954\n\t  title:: Test\n"
    _write_journal(tmp_path, "2025-09-13", content)
    positions = extract_positions(tmp_path, today=date(2025, 9, 14)).positions
    assert len(positions) == 1
    assert positions[0].lat == 45.5127


def test_extract_case_insensitive(tmp_path: Path):
    _write_journal(tmp_path, "2025-09-13", "- Current-Position:: 45.5127,13.5954\n")
    assert len(extract_positions(tmp_path, today=date(2025, 9, 14)).positions) == 1


def test_extract_negative_coordinates(tmp_path: Path):
    _write_journal(tmp_path, "2027-03-01", "- current-position:: -33.8688,151.2093\n")
    p = extract_positions(tmp_path, today=date(2027, 3, 2)).positions[0]
    assert p.lat == -33.8688
    assert p.lng == 151.2093


def test_extract_spaces_around_comma(tmp_path: Path):
    _write_journal(tmp_path, "2025-09-13", "- current-position:: 45.5127 , 13.5954\n")
    assert len(extract_positions(tmp_path, today=date(2025, 9, 14)).positions) == 1


def test_extract_minimum_one_day(tmp_path: Path):
    today = date(2025, 9, 13)
    _write_journal(tmp_path, today.isoformat(), "- current-position:: 45.0,13.0\n")
    assert extract_positions(tmp_path, today=today).positions[0].days >= 1


# ---- cluster_positions ------------------------------------------------------


def test_cluster_no_entries():
    assert cluster_positions([]).positions == []


def test_cluster_all_distinct():
    entries = [
        _entry("2025-09-13", 45.5127, 13.5954, 10),
        _entry("2025-10-01", 43.5088, 16.4402, 5),
        _entry("2025-10-15", 42.6507, 18.0944, 7),
    ]
    got = cluster_positions(entries)
    assert len(got.positions) == 3
    assert [p.days for p in got.positions] == [10, 5, 7]


def test_cluster_consecutive_merge():
    entries = [
        _entry("2026-03-14", 45.50543, 13.59597, 4),
        _entry("2026-03-18", 45.50591, 13.59765, 3),
    ]
    got = cluster_positions(entries)
    assert len(got.positions) == 1
    assert got.positions[0].days == 7
    assert got.positions[0].date == "2026-03-14"
    assert got.positions[0].lat == 45.50543


def test_cluster_leave_and_return():
    entries = [
        _entry("2026-03-14", 45.50543, 13.59597, 4),
        _entry("2026-03-18", 45.15039, 13.59877, 1),
        _entry("2026-03-19", 45.50591, 13.59765, 1),
    ]
    got = cluster_positions(entries)
    assert len(got.positions) == 3
    assert [p.days for p in got.positions] == [4, 1, 1]


def test_cluster_within_threshold():
    entries = [
        _entry("2025-09-13", 45.0, 13.0, 5),
        _entry(
            "2025-09-20",
            45.0 + CLUSTER_THRESHOLD_DEG * 0.9,
            13.0 + CLUSTER_THRESHOLD_DEG * 0.9,
            3,
        ),
    ]
    assert len(cluster_positions(entries).positions) == 1


def test_cluster_just_outside_threshold():
    entries = [
        _entry("2025-09-13", 45.0, 13.0, 5),
        _entry("2025-09-20", 45.0 + CLUSTER_THRESHOLD_DEG + 0.001, 13.0, 3),
    ]
    assert len(cluster_positions(entries).positions) == 2


def test_cluster_preserves_chronological_order():
    entries = [
        _entry("2025-09-13", 45.5, 13.6, 10),
        _entry("2025-10-01", 43.5, 16.4, 5),
        _entry("2025-10-15", 40.0, 18.0, 3),
    ]
    dates = [p.date for p in cluster_positions(entries).positions]
    assert dates == sorted(dates)


# ---- write_journey_json -----------------------------------------------------


def test_write_json_valid(tmp_path: Path):
    output = tmp_path / "journey.json"
    journey = JourneyMap(
        positions=[
            Position(date="2025-09-13", lat=45.5127, lng=13.5954, days=126),
            Position(date="2026-01-17", lat=43.5088, lng=16.4402, days=10),
        ]
    )
    write_journey_json(journey, output)
    got = json.loads(output.read_text(encoding="utf-8"))
    assert got == {
        "positions": [
            {"date": "2025-09-13", "lat": 45.5127, "lng": 13.5954, "days": 126},
            {"date": "2026-01-17", "lat": 43.5088, "lng": 16.4402, "days": 10},
        ]
    }


def test_write_json_creates_intermediate_dirs(tmp_path: Path):
    output = tmp_path / "subdir" / "nested" / "journey.json"
    write_journey_json(
        JourneyMap(positions=[Position(date="2025-09-13", lat=1.0, lng=2.0, days=3)]),
        output,
    )
    assert output.exists()


def test_write_json_empty(tmp_path: Path):
    output = tmp_path / "journey.json"
    write_journey_json(JourneyMap(positions=[]), output)
    assert json.loads(output.read_text(encoding="utf-8")) == {"positions": []}
