from pathlib import Path

from picscli.scan import drop_jpeg_when_raw_sibling


def test_jpeg_dropped_when_raw_sibling_present():
    paths = [Path("/card/DSC01234.ARW"), Path("/card/DSC01234.JPG")]
    assert drop_jpeg_when_raw_sibling(paths) == [Path("/card/DSC01234.ARW")]


def test_jpeg_only_card_untouched():
    paths = [Path("/card/DSC01234.JPG"), Path("/card/DSC01235.JPG")]
    assert drop_jpeg_when_raw_sibling(paths) == paths


def test_matching_is_case_insensitive():
    paths = [Path("/card/DSC01234.arw"), Path("/card/DSC01234.jpg")]
    assert drop_jpeg_when_raw_sibling(paths) == [Path("/card/DSC01234.arw")]


def test_same_stem_in_different_directories_not_paired():
    paths = [Path("/card/a/DSC01234.ARW"), Path("/card/b/DSC01234.JPG")]
    assert drop_jpeg_when_raw_sibling(paths) == paths


def test_raw_only_and_unrelated_jpegs_pass_through():
    paths = [Path("/card/DSC01234.ARW"), Path("/card/DSC01235.JPG")]
    assert drop_jpeg_when_raw_sibling(paths) == paths


def test_videos_pass_through_untouched():
    paths = [Path("/card/DSC01234.ARW"), Path("/card/DSC01234.JPG"), Path("/card/C0001.MP4")]
    assert drop_jpeg_when_raw_sibling(paths) == [Path("/card/DSC01234.ARW"), Path("/card/C0001.MP4")]
