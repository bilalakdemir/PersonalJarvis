"""The HUD background looks (N-16): ``work`` and ``attention``.

Raised only by the HUD projection while no conversation holds the bar
(background work / a pending approval in the canonical HudSnapshot). These pin
what makes them trustworthy: each is visibly different from rest, from a
notice (a failure), from each other and from a live session; no audio can
repaint them; attention never fades out; and a click on either behaves exactly
like a click on the resting bar.
"""

from __future__ import annotations

import numpy as np
import pytest

from jarvis.ui.jarvisbar import interaction, modes, renderer


@pytest.fixture(autouse=True)
def _reset_geometry() -> None:
    renderer.apply_display_scale(1.0, renderer.USER_SIZE_DEFAULT)


def _settled(mode: str, t: float = 0.0, **kw) -> np.ndarray:
    r = renderer.JarvisBarRenderer(accent="#e7c46e")
    for _ in range(60):
        r.render(0.0, mode, 0.0, **kw)
    return np.asarray(r.render(t, mode, 0.0, **kw))


def test_hud_modes_are_part_of_the_vocabulary_and_no_voice_mode() -> None:
    assert modes.HUD_MODES == ("work", "attention")
    # The voice modes are untouched — no existing behaviour may shift.
    assert modes.VOICE_MODES == ("idle", "listen", "speak", "think")
    for mode in modes.HUD_MODES:
        assert mode in modes.MODES
        assert mode not in modes.VOICE_MODES
        assert mode not in modes.DICTATION_MODES
        assert mode not in modes.NOTICE_MODES


@pytest.mark.parametrize("mode", modes.HUD_MODES)
def test_hud_looks_are_distinct_from_rest_notice_session_and_each_other(mode: str) -> None:
    frame = _settled(mode, t=0.7)
    others = ["idle", "notice", "think", "listen"] + [m for m in modes.HUD_MODES if m != mode]
    for other in others:
        assert not np.array_equal(frame, _settled(other, t=0.7)), other


@pytest.mark.parametrize("mode", modes.HUD_MODES)
def test_hud_looks_are_not_red(mode: str) -> None:
    """Red is this surface's "no". Waiting on the user is not a failure."""
    frame = _settled(mode).astype(int)
    r, g, b = frame[:, :, 0], frame[:, :, 1], frame[:, :, 2]
    drawn = ~((r >= 250) & (b >= 250))
    red_dominant = drawn & (r > g + 90) & (r > b + 90) & (g < 120)
    assert red_dominant.sum() == 0


@pytest.mark.parametrize("mode", modes.HUD_MODES)
def test_no_audio_signal_can_repaint_a_hud_look(mode: str) -> None:
    for playback in (False, True):
        for since in (0.0, 0.1, 99.0):
            assert renderer.visual_mode(mode, since, hold_s=0.4, playback_active=playback) == mode


@pytest.mark.parametrize("mode", modes.HUD_MODES)
def test_hud_looks_open_the_pill_without_faking_a_session(mode: str) -> None:
    w, h = renderer.target_pill_size(mode, hovered=False)
    assert (w, h) == (renderer.OPEN_W, renderer.OPEN_H)
    assert (w, h) != (renderer.ACTIVE_W, renderer.ACTIVE_H)


def test_attention_pulse_never_fades_out() -> None:
    values = [renderer.attention_pulse(t / 50.0) for t in range(400)]
    assert min(values) >= renderer.ATTENTION_PULSE_MIN
    assert max(values) <= 1.0
    assert max(values) - min(values) > 0.2


@pytest.mark.parametrize("mode", modes.HUD_MODES)
def test_a_click_on_a_hud_look_behaves_like_the_resting_bar(mode: str) -> None:
    for x in (0, 40, 200, 400, 600, 799):
        for hovered in (False, True):
            assert interaction.resolve_click(
                x, 800, mode, hovered=hovered, pill_w=400
            ) == interaction.resolve_click(x, 800, "idle", hovered=hovered, pill_w=400)


@pytest.mark.parametrize("mode", modes.HUD_MODES)
def test_mascot_energy_for_hud_modes_is_level_deaf_and_awake(mode: str) -> None:
    # The mascot module needs Tk; headless runners without it skip, exactly
    # like every other ``ui.orb.overlay`` test.
    pytest.importorskip("tkinter")
    from ui.orb.overlay import mode_energy

    quiet = [mode_energy(mode, t / 10.0, None) for t in range(100)]
    loud = [mode_energy(mode, t / 10.0, 1.0) for t in range(100)]
    assert quiet == loud, "no session is listening — the live level must not drive it"
    assert min(quiet) > 0.0
