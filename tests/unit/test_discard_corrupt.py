from __future__ import annotations

import importlib.util
import io
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RESERVOIR_PATH = REPO_ROOT / "reservoarr.py"
DISCARD = ["-fflags", "+discardcorrupt"]


def load(tmp_log_dir, **env):
    os.environ["RESV_LOG_DIR"] = str(tmp_log_dir)
    os.environ.update(env)
    sys.argv = ["reservoarr", "http://offline/unit-test"]
    spec = importlib.util.spec_from_file_location("resv_discard", str(RESERVOIR_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for key in env:
        os.environ.pop(key, None)
    return mod


class FakeFfmpeg:
    def __init__(self, chunks):
        read_fd, write_fd = os.pipe()
        os.write(write_fd, b"".join(chunks))
        os.close(write_fd)
        self.stderr = io.FileIO(read_fd, "r")


def input_args(cmd):
    return cmd[:cmd.index("-i")]


def test_default_command_has_no_fflags(tmp_path):
    assert "-fflags" not in load(tmp_path).FFMPEG_CMD


def test_discard_goes_on_the_input(tmp_path):
    cmd = load(tmp_path, RESV_DISCARD_CORRUPT="1").FFMPEG_CMD

    assert input_args(cmd)[-2:] == DISCARD
    assert cmd.count("-fflags") == 1


@pytest.mark.parametrize("value", ["0", "", "yes", "true"])
def test_only_one_turns_it_on(tmp_path, value):
    assert "-fflags" not in load(tmp_path, RESV_DISCARD_CORRUPT=value).FFMPEG_CMD


def test_discard_survives_the_timeline_offset(tmp_path):
    cmd = load(tmp_path, RESV_DISCARD_CORRUPT="1").ffmpeg_cmd(12.5)

    assert input_args(cmd)[-2:] == DISCARD
    assert cmd[-3:] == ["-output_ts_offset", "12.500000", "pipe:1"]


def test_corruption_detector_reads_the_dropping_line(tmp_path, capsys):
    resv = load(tmp_path, RESV_DISCARD_CORRUPT="1")
    seen = []
    resv.register_corrupt = lambda dts: seen.append(dts)

    resv.stderr_watcher(FakeFfmpeg([
        b"[in#0/mpegts @ 0x1] Packet corrupt (stream = 1, dts = 2000030856), dropping it.\n",
    ]))
    capsys.readouterr()

    assert seen == [2000030856]
