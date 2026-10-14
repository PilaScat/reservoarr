from __future__ import annotations

import re
from pathlib import Path

import pytest

from .harness import ffprobe_video_pts, run_pipeline
from .test_audio import AUDIO_PID, RATE_BPS, derive

CORRUPT_FROM = 0.05
DROP_EVERY = 170
RUN_S = 70
STALLS_BEFORE_S = 45
FLOWS_PAST_S = 60
PRODUCTION_AUDIO = {"RESV_AUDIO_CODEC": "aac", "RESV_AUDIO_FILL_GAPS": "1"}


def drop_audio_continuations(src: Path, dst: Path, every: int, from_fraction: float) -> int:
    data = src.read_bytes()
    start = int(len(data) // 188 * from_fraction) * 188
    out = bytearray()
    seen = dropped = 0
    for off in range(0, len(data) - 187, 188):
        packet = data[off:off + 188]
        pid = (packet[1] & 0x1F) << 8 | packet[2]
        continuation = pid == AUDIO_PID and not packet[1] & 0x40 and packet[3] & 0x10
        if off >= start and continuation:
            seen += 1
            if seen % every == 0:
                dropped += 1
                continue
        out += packet
    dst.write_bytes(bytes(out))
    return dropped


def video_span(run) -> float:
    video = ffprobe_video_pts(run.out_ts)
    return video[-1] - video[0] if video else 0.0


@pytest.fixture(scope="module")
def torn_ts(synth_ts, tmp_path_factory):
    folder = tmp_path_factory.mktemp("discard")
    plain = derive(synth_ts, folder / "plain.ts", "anull")
    torn = folder / "torn.ts"
    assert drop_audio_continuations(plain, torn, DROP_EVERY, CORRUPT_FROM) > 0
    return torn


@pytest.mark.e2e
def test_torn_aac_stalls_ffmpeg_without_discard(tmp_path, torn_ts):
    run = run_pipeline(tmp_path, torn_ts, rate_bps=RATE_BPS, duration_s=RUN_S)

    log = run.log_text()
    assert "Error submitting packet to decoder" in log, f"torn packets should reach the AAC decoder:\n{log}"
    assert video_span(run) < STALLS_BEFORE_S, (
        f"the stock pipeline should stall on this fixture, video covers {video_span(run):.1f}s"
    )


@pytest.mark.e2e
@pytest.mark.parametrize("audio_env", [{}, PRODUCTION_AUDIO], ids=["stock", "aac-fill"])
def test_discard_keeps_the_output_flowing(tmp_path, torn_ts, audio_env):
    run = run_pipeline(
        tmp_path, torn_ts, rate_bps=RATE_BPS, duration_s=RUN_S,
        extra_env={**audio_env, "RESV_DISCARD_CORRUPT": "1"},
    )

    log = run.log_text()
    assert "dropping it" in log, f"ffmpeg should drop the corrupt packets:\n{log}"
    assert "Error submitting packet to decoder" not in log
    assert not re.search(r"ffmpeg rc=\d", log), f"ffmpeg should keep running:\n{log}"
    assert video_span(run) > FLOWS_PAST_S, f"video covers {video_span(run):.1f}s\nstderr:\n{run.resv_stderr}"
