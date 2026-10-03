"""Five-layer parity for the HUD wire shape (AP-4).

The HUD vocabulary crosses Python (``jarvis/ui/hud/models.py``) → JSON (the
``/api/hud/snapshot`` body and the ``hud.snapshot`` WS frame) → TypeScript
(``frontend/src/types/hud.ts``) → presentation (``lib/hudSemantics.ts``) → i18n
(three locales). TypeScript cannot see Python, so this test reads both sides
and fails the build when one drifts — a primary state the UI does not know
would otherwise be dropped by ``parseHudSnapshot`` in silence.

Every parsed set is asserted non-empty first, so a reformat that breaks a
regex fails loudly instead of comparing two empty sets.
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

from jarvis.ui.hud import models
from jarvis.ui.hud import reducer as reducer_mod

_REPO = Path(__file__).resolve().parents[4]
_FRONTEND = _REPO / "jarvis" / "ui" / "web" / "frontend" / "src"
_TYPES_TS = _FRONTEND / "types" / "hud.ts"
_SEMANTICS_TS = _FRONTEND / "lib" / "hudSemantics.ts"
_LOCALES = _FRONTEND / "i18n" / "locales"
_LOCALE_NAMES = ("en", "de", "es")


def _ts_array(name: str) -> tuple[str, ...]:
    source = _TYPES_TS.read_text(encoding="utf-8")
    match = re.search(rf"export const {name}\s*=\s*\[(.*?)\]\s*as const", source, re.DOTALL)
    assert match is not None, f"{name} not found in hud.ts"
    values = tuple(re.findall(r'"([A-Za-z_]+)"', match.group(1)))
    assert values, f"parsed no values for {name}"
    return values


def _locale(name: str) -> dict:
    return json.loads((_LOCALES / f"{name}.json").read_text(encoding="utf-8"))


def _get(data: dict, dotted: str) -> object | None:
    node: object = data
    for part in dotted.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def test_vocabularies_match_python_exactly() -> None:
    assert _ts_array("HUD_PRIMARY_STATES") == models.PRIMARY_STATES
    assert _ts_array("HUD_CONNECTION_STATES") == models.CONNECTION_STATES
    assert _ts_array("HUD_ACTIVITY_STATUSES") == models.ACTIVITY_STATUSES
    assert _ts_array("HUD_ERROR_SCOPES") == models.ERROR_SCOPES
    assert _ts_array("HUD_DECISION_CHANNELS") == models.DECISION_CHANNELS
    assert _ts_array("HUD_APPROVAL_KINDS") == models.APPROVAL_KINDS


def test_snapshot_keys_match_the_dataclass() -> None:
    python_keys = tuple(f.name for f in dataclasses.fields(models.HudSnapshot))
    assert set(_ts_array("HUD_SNAPSHOT_KEYS")) == set(python_keys)
    assert set(models.HudSnapshot().to_dict()) == set(python_keys)


def test_schema_version_matches() -> None:
    source = _TYPES_TS.read_text(encoding="utf-8")
    match = re.search(r"export const HUD_SCHEMA_VERSION\s*=\s*(\d+)", source)
    assert match is not None
    assert int(match.group(1)) == models.HUD_SCHEMA_VERSION


def test_every_primary_state_has_a_reactor_style() -> None:
    source = _SEMANTICS_TS.read_text(encoding="utf-8")
    body = re.search(r"REACTOR_STYLES[^=]*=\s*\{(.*?)\n\};", source, re.DOTALL)
    assert body is not None, "REACTOR_STYLES not found"
    styled = set(re.findall(r"^\s*([A-Z_]+):", body.group(1), re.M))
    assert styled == set(models.PRIMARY_STATES)
    patterns = re.findall(r'pattern:\s*"([a-z]+)"', body.group(1))
    tones = re.findall(r'tone:\s*"([a-z]+)"', body.group(1))
    # Distinguishable without motion: no two states share BOTH tone and pattern.
    assert len(set(zip(tones, patterns, strict=True))) == len(models.PRIMARY_STATES)


def test_every_presented_value_has_a_string_in_every_locale() -> None:
    read_only_reasons = {
        reducer_mod._READ_ONLY_NO_ROUTE,  # noqa: SLF001
        reducer_mod._READ_ONLY_CHAT,  # noqa: SLF001
        reducer_mod._READ_ONLY_PROJECT_STATE,  # noqa: SLF001
    }
    keys = (
        ["nav.hud", "hud.title", "hud.loading", "hud.quiet"]
        + [f"hud.state.{s.lower()}" for s in models.PRIMARY_STATES]
        + [f"hud.connection.{s.lower()}" for s in models.CONNECTION_STATES]
        + [f"hud.status.{s}" for s in models.ACTIVITY_STATUSES]
        + [f"hud.error_scope.{s}" for s in models.ERROR_SCOPES]
        + [f"hud.approval.read_only.{r}" for r in sorted(read_only_reasons)]
        + [f"hud.attention.{a}" for a in ("approval", "working", "error")]
    )
    for name in _LOCALE_NAMES:
        data = _locale(name)
        for key in keys:
            value = _get(data, key)
            assert isinstance(value, str) and value.strip(), f"{name}.json: {key}"


def test_hud_locale_namespaces_share_one_key_set() -> None:
    def flat(node: object, prefix: str = "") -> set[str]:
        if not isinstance(node, dict):
            return {prefix}
        out: set[str] = set()
        for k, v in node.items():
            out |= flat(v, f"{prefix}.{k}" if prefix else k)
        return out

    reference = flat(_locale("en")["hud"])
    assert reference
    for name in _LOCALE_NAMES:
        assert flat(_locale(name)["hud"]) == reference, name


def _ts_interface_fields(name: str) -> set[str]:
    source = _TYPES_TS.read_text(encoding="utf-8")
    match = re.search(rf"export interface {name}\s*\{{(.*?)\n\}}", source, re.DOTALL)
    assert match is not None, f"interface {name} not found in hud.ts"
    body = re.sub(r"/\*\*.*?\*/", "", match.group(1), flags=re.DOTALL)
    fields = set(re.findall(r"^\s*([a-z_]+)\s*:", body, re.M))
    assert fields, f"parsed no fields for {name}"
    return fields


def test_every_nested_wire_type_matches_its_dataclass() -> None:
    pairs = {
        "HudProject": models.HudProject,
        "HudActivity": models.HudActivity,
        "HudApproval": models.HudApproval,
        "HudMemoryActivity": models.HudMemoryActivity,
        "HudComputerActivity": models.HudComputerActivity,
        "HudError": models.HudError,
        "HudSnapshot": models.HudSnapshot,
    }
    for ts_name, cls in pairs.items():
        python_fields = {f.name for f in dataclasses.fields(cls)}
        assert _ts_interface_fields(ts_name) == python_fields, ts_name
