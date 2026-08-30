import json

from picscli import cache


def test_hit_only_when_the_inputs_are_identical(tmp_path):
    path = cache.path_for(tmp_path, "faces")
    key = cache.fingerprint({"photos": [("b0000", "abc")], "distance": 0.8})
    cache.save(path, key, {"faces": ["f0"]})

    assert cache.load(path, key) == {"faces": ["f0"]}

    # A different frame, or a different setting, must not reuse the answer.
    assert cache.load(path, cache.fingerprint({"photos": [("b0000", "xyz")], "distance": 0.8})) is None
    assert cache.load(path, cache.fingerprint({"photos": [("b0000", "abc")], "distance": 0.7})) is None


def test_fingerprint_ignores_key_order_but_not_values():
    a = cache.fingerprint({"one": 1, "two": 2})
    assert a == cache.fingerprint({"two": 2, "one": 1})
    assert a != cache.fingerprint({"one": 1, "two": 3})


def test_missing_or_corrupt_file_is_simply_a_miss(tmp_path):
    path = cache.path_for(tmp_path, "covers")
    assert cache.load(path, "anything") is None
    path.write_text("{not json")
    assert cache.load(path, "anything") is None


def test_cache_files_are_named_so_upload_can_skip_them(tmp_path):
    assert cache.path_for(tmp_path, "faces").name.endswith(cache.SUFFIX)
