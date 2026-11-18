from pathlib import Path

import pytest

from cutter import CutError, select_segments
from recorder import WallClockMapper
from segments import Segment, SegmentIndex, parse_segment_list_line


def segs(start=1000.0, n=10, dur=10.0):
    return [Segment(Path(f"seg_{i:06d}.ts"), start + i * dur, dur) for i in range(n)]


def test_select_middle_range():
    chosen, offset, duration = select_segments(segs(), 1025.0, 1065.0)
    assert [s.path.name for s in chosen] == [f"seg_{i:06d}.ts" for i in (2, 3, 4, 5, 6)]
    assert offset == pytest.approx(5.0)
    assert duration == pytest.approx(40.0)


def test_select_clamps_to_buffer_edges():
    chosen, offset, duration = select_segments(segs(), 980.0, 1015.0)
    assert chosen[0].path.name == "seg_000000.ts"
    assert offset == 0.0
    assert duration == pytest.approx(15.0)


def test_select_exact_boundaries():
    chosen, offset, duration = select_segments(segs(), 1010.0, 1030.0)
    assert [s.path.name for s in chosen] == ["seg_000001.ts", "seg_000002.ts"]
    assert offset == 0.0 and duration == pytest.approx(20.0)


def test_select_nothing_buffered():
    with pytest.raises(CutError):
        select_segments(segs(), 5000.0, 5040.0)


def test_unsorted_input():
    chosen, _, _ = select_segments(list(reversed(segs())), 1025.0, 1045.0)
    assert [s.path.name for s in chosen] == ["seg_000002.ts", "seg_000003.ts", "seg_000004.ts"]


def test_index_rotation():
    idx = SegmentIndex()
    for s in segs():
        idx.add(s)
    old = idx.pop_older_than(1035.0)
    assert [s.path.name for s in old] == ["seg_000000.ts", "seg_000001.ts", "seg_000002.ts"]
    assert len(idx) == 7
    assert idx.latest_end() == 1100.0


def test_parse_segment_list_line():
    assert parse_segment_list_line("seg_000003.ts,30.033333,40.040000") == ("seg_000003.ts", 30.033333, 40.04)
    assert parse_segment_list_line("garbage") is None


def test_wall_clock_mapper_anchors_and_follows_stream_time():
    m = WallClockMapper(tolerance=8)
    # stream timestamps start at an arbitrary base (e.g. 5000s)
    assert m.to_wall(5000.0, 5010.0, now=100.0) == pytest.approx(90.0)
    # next segment arrives a bit late (burst) — still placed on the stream timeline
    assert m.to_wall(5010.0, 5020.0, now=112.0) == pytest.approx(100.0)


def test_wall_clock_mapper_reanchors_on_drift():
    m = WallClockMapper(tolerance=8)
    m.to_wall(0.0, 10.0, now=100.0)
    # after a 30s stall the timeline is re-anchored to arrival time
    assert m.to_wall(10.0, 20.0, now=140.0) == pytest.approx(130.0)
