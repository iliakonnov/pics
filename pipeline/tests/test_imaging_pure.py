from picscli.imaging import select_preview_frames


def test_select_preview_frames_passthrough_when_short():
    frames = list(range(5))
    assert select_preview_frames(frames, 24) == frames


def test_select_preview_frames_decimates_and_keeps_endpoints():
    frames = list(range(100))
    selected = select_preview_frames(frames, 24)
    assert selected[0] == 0
    assert selected[-1] == 99
    assert len(selected) <= 24
    assert selected == sorted(set(selected))


def test_select_preview_frames_single_max():
    frames = list(range(10))
    assert select_preview_frames(frames, 1) == [0]
