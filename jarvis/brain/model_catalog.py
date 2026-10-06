"""Per-provider model catalog — the live model list behind the API-Keys model
picker.

Where :mod:`jarvis.brain.frontier_resolver` queries a provider's ``/v1/models``
endpoint and distils it down to the single frontier pick per tier, this module
returns the *whole* catalog so the desktop UI can offer a searchable dropdown.
The two share the same upstream endpoints but answer different questions
(``frontier_resolver`` = "what is the newest model?"; ``model_catalog`` = "what
are all of them, so the user can pick one?").

Design goals (maintainer mandate 2026-06-20):
- **Always current.** The list comes from the provider's own catalog, so a model
  the provider published an hour ago appears without any code change here. There
  is no hand-maintained frontier list on the hot path — only a small ``static``
  fallback for the offline/no-key case, honestly labelled as such.
- **OpenRouter included.** Its catalog has hundreds of models, which is exactly
  why the UI needs search; this module just hands over the full list.
- **Honest source.** Every result carries ``source`` ∈ {``live``, ``cache``,
  ``static``} so the UI never pretends a stale fallback is the live catalog.

Cache: ``data/model_catalog_cache.json``, default TTL 6 h (shorter than the
frontier resolver's 24 h — fresher is better for a list the user browses), with
``force_refresh`` to bypass it on an explicit "refresh" click. OpenRouter is a
special case: its public catalog changes daily, so its cache lifetime is capped
at five minutes even when the general TTL is longer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, replace
from pathlib import Path

import httpx

from jarvis.core import config as cfg

log = logging.getLogger(__name__)

DEFAULT_TTL_HOURS = 6

# OpenRouter publishes model drops much more frequently than the direct
# providers. A six-hour cache made a valid new model look absent even though the
# public ``/models`` response already contained it. Cap only this volatile,
# unauthenticated catalog; callers can still use ``force_refresh`` for an
# immediate fetch, and an explicitly shorter global TTL continues to win.
_PROVIDER_TTL_CAP_SECONDS: dict[str, float] = {
    "openrouter": 5 * 60,
    # Local servers: the installed model set changes with every `ollama pull` /
    # server restart, and the fetch is a LAN round-trip — keep it near-live.
    "ollama": 60,
    "local-openai": 60,
}

# The brain providers whose catalogs we can enumerate. Codex is excluded
# on purpose: it authenticates via the ChatGPT login / a generic OpenAI key and
# its model id is largely ignored by the ``codex exec`` CLI path — it has no own
# model picker in the UI (it renders the Codex login widget instead).
CATALOG_PROVIDERS: tuple[str, ...] = (
    "claude-api",
    "openai",
    "gemini",
    "openrouter",
    "grok",
    "nvidia",
    # Keyless local providers (2026-07-25): their "catalog" is the live list of
    # models the user's own server holds — ollama via the native /api/tags,
    # local-openai via the standard /v1/models.
    "ollama",
    "local-openai",
)


@dataclass(frozen=True, slots=True)
class _CatalogEndpoint:
    """Catalog wiring for one provider.

    ``vendor_base`` is the provider's default base in the SAME convention its
    brain plugin uses for ``[brain.providers.<id>].base_url`` overrides (with
    or without ``/v1``, or a bare server root for the local providers), so an
    override composes with ``path`` exactly like the vendor default does.
    ``None`` = no vendor default: without a configured override the fetch
    raises an honest error (local-openai) or resolves dynamically (ollama's
    OLLAMA_HOST handling). ``auth`` selects how the key is attached:

      "x-api-key"  → Anthropic header pair (key required)
      "bearer"     → Authorization: Bearer (key required)
      "query"      → ?key= (Gemini; key required)
      "bearer_opt" → Authorization: Bearer if a key exists, else anonymous
                     (public catalogs: OpenRouter, NVIDIA NIM)
      "none"       → keyless local server; the optional ``secret_slot``
                     (keyring name, ENV name) is attached as Bearer when set.
    """

    vendor_base: str | None
    path: str
    auth: str
    secret_slot: tuple[str, str] | None = None


# Every fetch resolves the provider's EFFECTIVE base URL through
# ``cfg.resolve_provider_endpoint`` (team proxy → base_url override → the
# vendor default here) and appends ``path``. With no override configured the
# resulting URLs are byte-identical to the former static table — pinned by
# tests/unit/brain/test_model_catalog_local.py (S3a regression gate).
_ENDPOINTS: dict[str, _CatalogEndpoint] = {
    # Anthropic SDK convention: override EXCLUDES /v1 (the SDK appends it).
    "claude-api": _CatalogEndpoint("https://api.anthropic.com", "/v1/models", "x-api-key"),
    # OpenAI-compatible convention: override INCLUDES /v1.
    "openai": _CatalogEndpoint("https://api.openai.com/v1", "/models", "bearer"),
    # google-genai convention: override is the raw host (SDK appends /v1beta).
    "gemini": _CatalogEndpoint(
        "https://generativelanguage.googleapis.com",
        "/v1beta/models",
        "query",
    ),
    "openrouter": _CatalogEndpoint("https://openrouter.ai/api/v1", "/models", "bearer_opt"),
    # xAI uses the OpenAI-compatible ``data[].id`` model roster and requires
    # the same bearer key used for Grok inference.
    "grok": _CatalogEndpoint("https://api.x.ai/v1", "/models", "bearer"),
    # NVIDIA NIM speaks the OpenAI-compatible ``data[].id`` shape. Its catalog is
    # PUBLIC (verified 2026-07-08: an unauthenticated GET returns the full model
    # list), so ``bearer_opt`` like OpenRouter — the picker fills in before a key
    # is entered, and the key is attached when present.
    "nvidia": _CatalogEndpoint("https://integrate.api.nvidia.com/v1", "/models", "bearer_opt"),
    # Ollama: server-root convention (the plugin appends /v1 / /api itself);
    # vendor default resolves dynamically (OLLAMA_HOST → localhost:11434).
    "ollama": _CatalogEndpoint(None, "/api/tags", "none"),
    # Generic local OpenAI-compatible server: no default port is guessable
    # (transformers serve 8000, llama-server 8080, LM Studio 1234) — the user
    # sets the base URL on the card; without it the picker stays honestly empty.
    "local-openai": _CatalogEndpoint(
        None,
        "/v1/models",
        "none",
        secret_slot=("local_openai_api_key", "LOCAL_OPENAI_API_KEY"),
    ),
}


@dataclass(frozen=True, slots=True)
class ModelInfo:
    """One selectable model: the wire ``id`` plus a human ``label``.

    ``output_modalities`` carries the provider's declared output kinds (e.g.
    ``("text",)`` or ``("text", "image")``) when available — OpenRouter returns
    it under ``architecture.output_modalities``; direct provider ``/v1/models``
    endpoints do not, so it stays ``None`` there. A tuple (not a list) keeps the
    dataclass frozen/hashable. Used by :func:`filter_brain_models` to exclude
    image/audio/video GENERATION models that a name-substring blocklist misses
    (e.g. ``openrouter/auto``)."""

    id: str
    label: str
    output_modalities: tuple[str, ...] | None = None
    # H4/H5: per-model capability hints from OpenRouter's /v1/models
    # (``architecture.input_modalities`` + ``supported_parameters``). ``None`` when
    # the provider endpoint doesn't expose them → callers default to "capable" (no
    # regression). ``"image" in input_modalities`` ⇒ vision; ``"tools" in
    # supported_parameters`` ⇒ tool-calling.
    input_modalities: tuple[str, ...] | None = None
    supported_parameters: tuple[str, ...] | None = None
    # (input, output) in USD per 1M tokens, straight from the provider's own
    # feed (OpenRouter publishes ``pricing.prompt`` / ``pricing.completion``
    # per token). ``None`` when the endpoint has no price data. This is what
    # lets cost tracking price a model the static table in
    # ``jarvis/brain/cost.py`` has never heard of — the table used to be the
    # only source, and every new model generation shipped as "$0.00" until
    # someone noticed (2026-07-28 and 2026-08-18 audits).
    pricing: tuple[float, float] | None = None
    # Local-server facts from Ollama's ``/api/show`` (``model_info.<arch>.
    # context_length`` and ``details``). ``None`` for every gateway/cloud
    # catalog — they carry no such manifest — so nothing downstream changes for
    # them. Read by the Local models section and the per-model option sheet
    # (native context caps the ``num_ctx`` chips).
    context_length: int | None = None
    quantization_level: str | None = None
    parameter_size: str | None = None
    # Release time (Unix seconds) where the catalog publishes one — OpenRouter
    # and OpenAI send ``created``. Public discovery (``discovered_from_feed``)
    # uses it to admit only models released within the last year.
    created: float | None = None


def _curated(pairs: list[tuple[str, str]]) -> list[ModelInfo]:
    return [ModelInfo(id=i, label=lbl) for i, lbl in pairs]


# Curated current model families per provider — the picker's fallback when the
# live ``/v1/models`` catalog is unreachable (no/invalid key, network down). This
# is what makes the dropdown useful for providers the user drives WITHOUT an API
# key: Claude in particular runs via the Max subscription (OAuth), so its live
# fetch always 401s — the user still expects to pick Fable / Opus / Sonnet /
# Haiku. Keep these to the *current* frontier families (maintainer mandate: never
# offer a years-old model); when a valid key exists the live catalog supersedes
# this entirely, so a new release still shows up automatically there.
CURATED_MODELS: dict[str, list[ModelInfo]] = {
    "claude-api": _curated(
        [
            ("claude-opus-5-5", "Claude Opus 5.5"),
            ("claude-fable-5-1", "Claude Fable 5.1"),
            ("claude-fable-5", "Claude Fable 5"),
            ("claude-opus-5", "Claude Opus 5"),
            ("claude-opus-4-8", "Claude Opus 4.8"),
            ("claude-sonnet-5", "Claude Sonnet 5"),
            ("claude-haiku-4-5-20251001", "Claude Haiku 4.5"),
        ]
    ),
    "openai": _curated(
        [
            ("gpt-6-sol", "GPT-6 Sol"),
            ("gpt-6-astra", "GPT-6 Astra"),
            ("gpt-6-luna", "GPT-6 Luna"),
            ("gpt-6-sol-pro", "GPT-6 Sol Pro"),
            ("gpt-6-astra-pro", "GPT-6 Astra Pro"),
            ("gpt-5.6-sol", "GPT-5.6 Sol (preview)"),
            ("gpt-5.6-terra", "GPT-5.6 Terra (preview)"),
            ("gpt-5.6-luna", "GPT-5.6 Luna (preview)"),
            ("gpt-5.6", "GPT-5.6 (Sol alias, preview)"),
            ("gpt-5.5", "GPT-5.5"),
            ("gpt-5.5-pro", "GPT-5.5 Pro"),
            ("gpt-5.4", "GPT-5.4"),
            ("gpt-5.4-pro", "GPT-5.4 Pro"),
            ("gpt-5.4-mini", "GPT-5.4 Mini"),
            ("gpt-5.4-nano", "GPT-5.4 Nano"),
        ]
    ),
    "gemini": _curated(
        [
            ("gemini-3.8-flash", "Gemini 3.8 Flash"),
            ("gemini-3.7-flash", "Gemini 3.7 Flash"),
            ("gemini-3.6-flash", "Gemini 3.6 Flash"),
            ("gemini-3.5-flash", "Gemini 3.5 Flash"),
            ("gemini-3.1-pro-preview", "Gemini 3.1 Pro"),
            ("gemini-3-flash-preview", "Gemini 3 Flash"),
            ("gemini-3.1-flash-lite", "Gemini 3.1 Flash-Lite"),
            ("gemini-2.5-pro", "Gemini 2.5 Pro"),
            ("gemini-2.5-flash", "Gemini 2.5 Flash"),
            ("gemini-flash-lite-latest", "Gemini Flash Lite"),
        ]
    ),
    "openrouter": _curated(
        [
            ("anthropic/claude-opus-5.5", "Claude Opus 5.5"),
            ("anthropic/claude-fable-5.1", "Claude Fable 5.1"),
            ("anthropic/claude-fable-5", "Claude Fable 5"),
            ("anthropic/claude-opus-4.8", "Claude Opus 4.8"),
            ("anthropic/claude-sonnet-5", "Claude Sonnet 5"),
            ("anthropic/claude-haiku-4.5", "Claude Haiku 4.5"),
            ("openai/gpt-6-sol", "GPT-6 Sol"),
            ("openai/gpt-6-astra", "GPT-6 Astra"),
            ("openai/gpt-6-luna", "GPT-6 Luna"),
            ("openai/gpt-5.6-sol-pro", "GPT-5.6 Sol Pro"),
            ("openai/gpt-5.6-sol", "GPT-5.6 Sol"),
            ("openai/gpt-5.6-terra-pro", "GPT-5.6 Terra Pro"),
            ("openai/gpt-5.6-terra", "GPT-5.6 Terra"),
            ("openai/gpt-5.6-luna-pro", "GPT-5.6 Luna Pro"),
            ("openai/gpt-5.6-luna", "GPT-5.6 Luna"),
            ("google/gemini-3.8-flash", "Gemini 3.8 Flash"),
            ("google/gemini-3.5-flash", "Gemini 3.5 Flash"),
            ("google/gemini-3.1-pro-preview", "Gemini 3.1 Pro"),
            ("x-ai/grok-4.7", "Grok 4.7"),
            ("x-ai/grok-4.20", "Grok 4.20"),
            ("deepseek/deepseek-v4-pro", "DeepSeek V4 Pro"),
        ]
    ),
    # Grok 4.3 leads because it is the universal default — the newer families
    # are not available in every region, and this list is only the OFFLINE
    # fallback, so its head is what a downloader with no live catalog gets.
    # It must stay in step with ``jarvis.plugins.brain.grok.DEFAULT_MODEL``
    # (pinned by test_grok_has_authenticated_live_model_catalog): 4.6 was
    # inserted at the head without moving the default, which offered a model
    # as the pre-selected one that the client would not actually have used.
    # Grok 4.7 (2026-09-21) is in the list, not at its head, for that reason.
    # The authenticated live catalog replaces this fallback entirely.
    # Grok Build does not use this order: ``GROK_BUILD_MODELS`` leads with 4.7.
    "grok": _curated(
        [
            ("grok-4.3", "Grok 4.3"),
            ("grok-4.7", "Grok 4.7"),
            ("grok-4.6", "Grok 4.6"),
            ("grok-4.5", "Grok 4.5"),
        ]
    ),
    # NVIDIA NIM — the offline fallback when the live /v1/models catalog is
    # unreachable. NVIDIA-hosted current families (Nemotron leads: it is NVIDIA's
    # own). A valid key supersedes this with the live list, so a newly hosted
    # model still shows up automatically.
    "nvidia": _curated(
        [
            ("nvidia/llama-3.1-nemotron-ultra-253b-v1", "Nemotron Ultra 253B"),
            ("nvidia/llama-3.3-nemotron-super-49b-v1.5", "Nemotron Super 49B v1.5"),
            ("deepseek-ai/deepseek-v4-pro", "DeepSeek V4 Pro"),
            ("deepseek-ai/deepseek-v4-flash", "DeepSeek V4 Flash"),
            ("moonshotai/kimi-k2.6", "Kimi K2.6"),
            ("z-ai/glm-5.2", "GLM-5.2"),
            ("qwen/qwen3.5-397b-a17b", "Qwen3.5 397B A17B"),
            ("meta/llama-4-maverick-17b-128e-instruct", "Llama 4 Maverick"),
            ("mistralai/mistral-large-3-675b-instruct-2512", "Mistral Large 3"),
        ]
    ),
}


#: Grok Build's picker. The subscription login has no /v1/models, so this
#: list is what the create-agent dialog and the IDE pane offer. grok-4.7
#: leads: it is the current default of the Grok Build CLI (xAI, 2026-09-21).
#: The API fallback in ``CURATED_MODELS["grok"]`` must not copy this order —
#: its head is the client default, ``grok-4.3``.
GROK_BUILD_MODELS: tuple[tuple[str, str], ...] = (
    ("grok-4.7", "Grok 4.7"),
    ("grok-4.7-build-fast", "Grok 4.7 Fast"),
    ("grok-4.6", "Grok 4.6"),
    ("grok-4.5", "Grok 4.5"),
    ("grok-4.3", "Grok 4.3"),
)


@dataclass(frozen=True, slots=True)
class CatalogResult:
    """The model list for one provider, with an honest provenance flag."""

    provider: str
    models: tuple[ModelInfo, ...]
    source: str  # "live" | "cache" | "static" | "curated"
    fetched_at: float
    selects: str = "model"  # what the picker writes: "model" | "voice"


def _ids(ids: list[str]) -> list[ModelInfo]:
    return [ModelInfo(id=i, label=i) for i in ids]


# TTS catalogs — for most TTS providers the user-facing pick is the VOICE
# (Gemini Charon/Kore, Grok leo/rex, OpenAI alloy/nova, Google Neural2 names);
# Cartesia's meaningful pick is its MODEL (sonic-3.5). The ``[tts]`` config is a
# single block (voice_de/voice_en/model), so the picker only renders on the
# ACTIVE TTS card and sets the global value.
TTS_CATALOG: dict[str, tuple[str, list[ModelInfo]]] = {
    # Piper (on-device). A Piper voice speaks ONE language, so this picker is a
    # speaker choice, not a language choice: the provider still resolves the
    # file from the turn's output language. Both sets are listed; the masculine
    # trio is what the install downloads, and the feminine one is fetched on
    # demand. Kept in sync with SHERPA_BUNDLES in jarvis/speech/local_models.py.
    "piper-local": (
        "voice",
        _curated(
            [
                ("vits-piper-de_DE-thorsten-medium", "Thorsten — German, masculine"),
                ("vits-piper-en_US-ryan-medium", "Ryan — English, masculine"),
                ("vits-piper-es_ES-davefx-medium", "Dave — Spanish, masculine"),
                ("vits-piper-de_DE-ramona-low", "Ramona — German, feminine"),
                ("vits-piper-en_US-amy-medium", "Amy — English, feminine"),
                ("vits-piper-es_ES-sharvard-medium", "Sharvard — Spanish, feminine"),
            ]
        ),
    ),
    # ElevenLabs picks a VOICE ID (opaque hashes), so the curated list carries
    # human names as labels while the value stays the id. The picker's
    # "use custom" row lets a user paste their OWN voice id (e.g. a cloned
    # voice) instead of a curated one — kept in sync with DEFAULT_VOICES in
    # jarvis/plugins/tts/elevenlabs_tts.py.
    # Inworld — the new premium default (arena-#1 realtime, mid-2026). Voices are
    # multilingual; these native masculine de/en/es voices are the curated pick,
    # kept in sync with DEFAULT_VOICE_* in jarvis/plugins/tts/inworld_tts.py.
    "inworld": (
        "voice",
        _curated(
            [
                ("Josef", "Josef — German, calm assistant (default)"),
                ("Johanna", "Johanna — German, warm"),
                ("Dennis", "Dennis — English, deep narrator"),
                ("Ashley", "Ashley — English, bright"),
                ("Diego", "Diego — Spanish, formal"),
                ("Lupita", "Lupita — Spanish, warm"),
            ]
        ),
    ),
    "elevenlabs": (
        "voice",
        _curated(
            [
                ("onwK4e9ZLuTAKqWW03F9", "Daniel — British, authoritative (default)"),
                ("JBFqnCBsd6RMkjVDRZzb", "George — British, deep narrator"),
                ("IKne3meq5aSn9XLyUdCD", "Charlie — British, mature butler"),
                ("nPczCjzI2devNBz1zQrb", "Brian — American, deep narrator"),
                ("pNInz6obpgDQGcFmaJgB", "Adam — American, classic AI voice"),
            ]
        ),
    ),
    "gemini-flash-tts": (
        "voice",
        _ids(
            [
                "Charon",
                "Kore",
                "Orus",
                "Iapetus",
                "Rasalgethi",
                "Algenib",
                "Algieba",
                "Fenrir",
                "Aoede",
                "Zephyr",
                "Puck",
                "Leda",
                "Callirrhoe",
                "Autonoe",
                "Enceladus",
                "Umbriel",
                "Despina",
                "Erinome",
                "Laomedeia",
                "Achernar",
                "Alnilam",
                "Schedar",
                "Gacrux",
                "Pulcherrima",
                "Achird",
                "Zubenelgenubi",
                "Vindemiatrix",
                "Sadachbia",
                "Sadaltager",
                "Sulafat",
            ]
        ),
    ),
    "grok-voice": (
        "voice",
        _ids(
            [
                "leo",
                "rex",
                "sal",
                "ara",
                "eve",
                "carina",
                "zagan",
                "helix",
                "orion",
                "luna",
                "iris",
                "altair",
                "zenith",
                "perseus",
                "helios",
                "lux",
                "kepler",
                "rigel",
                "cosmo",
                "celeste",
                "ursa",
                "sirius",
                "lumen",
                "castor",
                "naksh",
                "atlas",
            ]
        ),
    ),
    "openai-tts": (
        "voice",
        _ids(
            [
                "alloy",
                "ash",
                "ballad",
                "coral",
                "echo",
                "fable",
                "onyx",
                "nova",
                "sage",
                "shimmer",
                "verse",
                "marin",
                "cedar",
            ]
        ),
    ),
    "google-neural2": (
        "voice",
        _ids(
            [
                "en-US-Neural2-A",
                "en-US-Neural2-C",
                "en-US-Neural2-D",
                "en-US-Neural2-F",
                "de-DE-Neural2-B",
                "de-DE-Neural2-C",
                "de-DE-Neural2-D",
                "de-DE-Neural2-F",
            ]
        ),
    ),
    "cartesia": (
        "model",
        _curated(
            [
                ("sonic-3.5", "Sonic 3.5 (stable)"),
                ("sonic-3", "Sonic 3"),
                ("sonic-3-latest", "Sonic 3 latest (preview track)"),
            ]
        ),
    ),
    # OpenRouter TTS (the last-resort gateway) — the model picker offers ONLY the
    # four allowlisted, production-grade speech models. The five open-source slop
    # models (Kokoro, Orpheus, CSM-1B, both Zonos) are UNLISTED per the hard
    # allowlist (jarvis/plugins/tts/curated_catalog.py) — they fail the premium
    # multilingual bar on de/es coverage, beta stability, or GPU dependence. Every
    # id here must satisfy curated_catalog.is_allowed("openrouter", id) — guarded
    # by tests/unit/plugins/tts/test_openrouter_curation.py.
    "openrouter-tts": (
        "model",
        _ids(
            [
                "google/gemini-3.1-flash-tts-preview",
                "x-ai/grok-voice-tts-1.0",
                "microsoft/mai-voice-2",
                "mistralai/voxtral-mini-tts-2603",
            ]
        ),
    ),
}

# Realtime catalogs — REALTIME_MODELS + REALTIME_VOICES, keyed by realtime
# provider id (``openai-realtime`` / ``gemini-live`` / ``vertex-live`` /
# ``local-realtime``).
# Realtime needs BOTH a
# model AND a voice selection per provider (unlike every other picker, which
# serves ONE selection), so these two dicts are looked up directly by the
# dedicated ``GET/PUT /providers/{id}/realtime-options`` endpoints rather than
# being registered into ``PROVIDER_CATALOG``/``catalog_spec`` (that machinery
# is built around a single ``selects: "model" | "voice"`` per provider).
# Curated, not live-fetched: no realtime provider exposes a ``/v1/models``
# endpoint reachable the same way as the text-brain catalogs, and a curated
# list is realtime-only by construction (never leaks a non-realtime model into
# the picker). The currently-hardcoded adapter default is always FIRST in each
# model list — the safe fallback an unset pick resolves to.
#
# openai-realtime — verified 2026-07-10 against the official Realtime model
# guide and endpoint-support table. ``gpt-realtime`` remains the adapter default
# (matches ``_MODEL`` in ``jarvis/plugins/realtime/openai_realtime.py``), while
# 2.1/2.1-mini, 2, 1.5, and mini remain selectable general voice-agent models.
# ``gpt-realtime-translate``/``gpt-realtime-whisper`` are deliberately excluded:
# they target dedicated translation/transcription sessions, not the general
# duplex voice-agent protocol implemented by this adapter.
REALTIME_MODELS: dict[str, list[ModelInfo]] = {
    # codex-subscription-realtime was REMOVED 2026-08-10 together with its
    # adapter: Codex's experimental app-server realtime surface never held a
    # dependable call. Subscription voice continues over the classic pipeline
    # (voice.profile = "codex-subscription-voice"), which needs no realtime
    # model catalog entry.
    # local-realtime: the served model is whatever its operator loaded, so the
    # only honest curated entry is "ask the server". The adapter resolves it
    # through /v1/models at connect time (same as the local brain card), and a
    # user who wants a specific one pins it on the card.
    "local-realtime": _curated([("auto", "Chosen by your server")]),
    "openai-live": _curated([("gpt-live-1", "GPT-Live 1")]),
    "openai-realtime": _curated(
        [
            ("gpt-realtime", "GPT Realtime (default)"),
            ("gpt-realtime-2.1", "GPT Realtime 2.1"),
            ("gpt-realtime-2.1-mini", "GPT Realtime 2.1 Mini"),
            ("gpt-realtime-2", "GPT Realtime 2"),
            ("gpt-realtime-1.5", "GPT Realtime 1.5"),
            ("gpt-realtime-mini", "GPT Realtime Mini"),
        ]
    ),
    # gemini-live — verified 2026-07-10 against ai.google.dev/gemini-api/docs/models
    # (the Live API model list). ``gemini-3.1-flash-live-preview`` is the current
    # flagship (matches ``_MODEL`` in ``jarvis/plugins/realtime/gemini_live.py``);
    # ``gemini-2.5-flash-native-audio-preview-12-2025`` is the current 2.5-series
    # native-audio sibling still listed on that page. The older
    # ``gemini-2.0-flash-live-preview`` family is marked for shutdown — omitted.
    "gemini-live": _curated(
        [
            ("gemini-3.1-flash-live-preview", "Gemini 3.1 Flash Live (default)"),
            (
                "gemini-2.5-flash-native-audio-latest",
                "Gemini 2.5 Flash Native Audio (latest alias)",
            ),
            (
                "gemini-2.5-flash-native-audio-preview-12-2025",
                "Gemini 2.5 Flash Native Audio",
            ),
        ]
    ),
    # Vertex publishes its OWN Live model ids — the AI Studio ids above 404
    # on a Cloud project (VertexLiveProvider.default_model, live 2026-08-17).
    # Only the verified adapter default is listed; a per-card pin still
    # overrides it.
    "vertex-live": _curated(
        [
            (
                "gemini-live-2.5-flash-native-audio",
                "Gemini 2.5 Flash Native Audio (default)",
            ),
        ]
    ),
    # grok-realtime (xAI Voice Agent API) was REMOVED 2026-07-16: the xAI
    # server drops the session contract after any response cancel, ignores
    # the configured VAD silence window, swallows response.done, and spams
    # stray auto-responses — four deaf-session variants in one morning
    # (BUG-064 recurrences #1-#4). Maintainer decision: not shippable.
}

# Realtime voice catalogs — stable prebuilt-voice names (curated, not live).
# openai-realtime: verified 2026-07-10 against the official Realtime
# conversations guide — ten current voices, including Marin and Cedar.
# gemini-live / vertex-live: verified 2026-07-10 against the Live API
# capabilities guide, which now permits the complete 30-voice Gemini
# prebuilt roster. Same names on both sockets (AI Studio vs Cloud project).
REALTIME_VOICES: dict[str, list[ModelInfo]] = {
    # A self-hosted server ships whatever voices its operator installed, and a
    # list of OpenAI voice names would be a guess the server then rejects. One
    # honest entry: the adapter sends no voice override and the server uses its
    # own default.
    "local-realtime": _curated([("auto", "Your server's own voice")]),
    "openai-realtime": _ids(
        [
            "alloy",
            "ash",
            "ballad",
            "coral",
            "echo",
            "sage",
            "shimmer",
            "verse",
            "marin",
            "cedar",
        ]
    ),
    "gemini-live": _ids(
        [
            "Puck",
            "Charon",
            "Kore",
            "Fenrir",
            "Aoede",
            "Orus",
            "Leda",
            "Zephyr",
            "Callirrhoe",
            "Autonoe",
            "Enceladus",
            "Iapetus",
            "Umbriel",
            "Algieba",
            "Despina",
            "Erinome",
            "Algenib",
            "Rasalgethi",
            "Laomedeia",
            "Achernar",
            "Alnilam",
            "Schedar",
            "Gacrux",
            "Pulcherrima",
            "Achird",
            "Zubenelgenubi",
            "Vindemiatrix",
            "Sadachbia",
            "Sadaltager",
            "Sulafat",
        ]
    ),
}
REALTIME_VOICES["vertex-live"] = REALTIME_VOICES["gemini-live"]


# STT model catalogs (the ``[stt] model`` is a single global value).
STT_CATALOG: dict[str, list[ModelInfo]] = {
    "groq-api": _ids(["whisper-large-v3", "whisper-large-v3-turbo"]),
    # The on-device recognizers offer exactly ONE model each, deliberately.
    # Their predecessor card was removed in v1.0.1 partly because its picker
    # listed all seven Whisper checkpoints regardless of which had been
    # downloaded — a menu of mostly-absent options. What the installer fetches
    # and what the picker offers are therefore the same single entry.
    # (The wake word is a separate path with its own checkpoint via
    # [stt].wake_*, and is not affected by anything in this catalog.)
    "faster-whisper": _ids(["large-v3"]),
    "nemotron-local": _ids(["nemotron-3.5-asr-streaming-0.6b-560ms-int8"]),
    "openai-api": _ids(
        [
            "gpt-4o-transcribe",
            "gpt-4o-mini-transcribe",
            "gpt-4o-mini-transcribe-2025-12-15",
            "gpt-4o-transcribe-diarize",
            "whisper-1",
        ]
    ),
    # Keyed by the ENTRY-POINT id. It read "deepgram" while the plugin did not
    # exist, so the picker offered models for a provider nothing could build.
    "deepgram-api": _ids(["nova-3", "nova-2", "nova-2-general", "enhanced", "base"]),
    # OpenRouter STT — the model picker offers ONLY transcription models. This
    # curated snapshot mirrors the live `?output_modalities=transcription` list
    # (verified 2026-07-02); audio-in chat models are excluded. The default
    # model (openai/whisper-large-v3) is listed first.
    "openrouter-stt": _ids(
        [
            "openai/whisper-large-v3",
            "openai/gpt-4o-transcribe",
            "openai/gpt-4o-mini-transcribe",
            "openai/whisper-1",
            "openai/whisper-large-v3-turbo",
            "google/chirp-3",
            "mistralai/voxtral-mini-transcribe",
            "qwen/qwen3-asr-flash-2026-02-10",
            "nvidia/parakeet-tdt-0.6b-v3",
            "microsoft/mai-transcribe-1.5",
        ]
    ),
}


def _gemini_audio_models(default_first: str) -> list[ModelInfo]:
    """The Gemini brain catalogue, reordered so ``default_first`` leads.

    Gemini transcription is not a separate speech model: it is an ordinary
    ``generateContent`` call with an audio part, so ANY multimodal Gemini id can
    do it — which is why the recognizer's own module refuses to pin one (AP-21).
    Reusing the brain list is therefore both correct and the only way to avoid a
    second literal that drifts on the next model rotation. Putting the
    recognizer's actual default at the top keeps the picker from opening on a
    model the provider would not have used.
    """
    models = list(CURATED_MODELS.get("gemini", ()))
    leader = [m for m in models if m.id == default_first]
    return leader + [m for m in models if m.id != default_first]


# Both Google recognizers share one derived list — same models, two accounts.
# The default id mirrors ``jarvis.plugins.stt.gemini_api.DEFAULT_MODEL``; it is
# spelled here rather than imported so the catalog module keeps importing on a
# host without google-genai.
STT_CATALOG["gemini-api"] = _gemini_audio_models("gemini-3-flash-preview")
STT_CATALOG["vertex-stt"] = STT_CATALOG["gemini-api"]


@dataclass(frozen=True, slots=True)
class CatalogSpec:
    """Per-provider picker spec: which tier, what it selects, the curated list,
    and whether a live ``/v1/models`` fetch is available (brain providers only)."""

    tier: str  # "brain" | "tts" | "stt"
    selects: str  # "model" | "voice"
    curated: tuple[ModelInfo, ...]
    live: bool
    #: The direct provider whose publicly discovered models join this curated
    #: list (``discovered_from_feed``) — for a login that publishes no catalog
    #: of its own. ``None`` = the curated list is the whole answer.
    discover: str | None = None


def _build_provider_catalog() -> dict[str, CatalogSpec]:
    cat: dict[str, CatalogSpec] = {}
    # The live-fetchable API brains (CATALOG_PROVIDERS). Codex + antigravity are
    # added below as curated-only — no /v1/models over their OAuth logins.
    for p in CATALOG_PROVIDERS:
        cat[p] = CatalogSpec("brain", "model", tuple(CURATED_MODELS.get(p, ())), live=True)
    # Codex — Jarvis-Agent model catalog for the ChatGPT-login worker; no
    # /v1/models over OAuth, so curated only. The concrete GPT-5.6 choices are
    # the current Codex lineup; the still-supported GPT-5.5/5.4 choices remain
    # available for users who intentionally prefer their established behavior.
    cat["codex"] = CatalogSpec(
        "brain",
        "model",
        tuple(
            _curated(
                [
                    ("gpt-5.6-sol", "GPT-5.6 Sol"),
                    ("gpt-5.6-terra", "GPT-5.6 Terra"),
                    ("gpt-5.6-luna", "GPT-5.6 Luna"),
                    ("gpt-5.5", "GPT-5.5"),
                    ("gpt-5.4", "GPT-5.4"),
                    ("gpt-5.4-mini", "GPT-5.4 Mini"),
                ]
            )
        ),
        live=False,
    )
    # Antigravity — subagent model catalog for the official agy/gemini CLI
    # (OAuth login); no /v1/models over OAuth, so curated only. Flash first =
    # the fast default; Pro is the deep option. Both are valid gemini-CLI ids.
    # NOTE (verified live 2026-06-21, agy 1.0.10): the agy CLI IGNORES the chosen
    # model — neither ``--model`` nor ``settings.json model.name`` overrides it
    # (a bogus name still answers), so agy always runs its IDE-configured default.
    # The selection here therefore only takes effect on the gemini-CLI fallback
    # path (and any future agy that honors the flag); for agy it is informational.
    cat["antigravity"] = CatalogSpec(
        "brain",
        "model",
        tuple(
            _curated(
                [
                    ("gemini-3.5-flash", "Gemini 3.5 Flash"),
                    ("gemini-3.1-pro-preview", "Gemini 3.1 Pro"),
                ]
            )
        ),
        live=False,
    )
    # Grok Build — SuperGrok / X Premium+ CLI worker. No public /v1/models over
    # the subscription login, so curated only. grok-4.7 leads: it is the
    # current Grok Build default (xAI, 2026-09-21).
    cat["grok-build"] = CatalogSpec(
        "brain",
        "model",
        tuple(_curated(list(GROK_BUILD_MODELS))),
        live=False,
    )
    # Claude CLI — the Anthropic-subscription writer (``claude -p``); no
    # /v1/models over a plan login, so curated only. The aliases are what the CLI
    # itself accepts, not dated API ids: they keep pointing at the plan's current
    # model as Anthropic rotates it, which is exactly right for a subscription
    # path where the account's plan decides what is reachable anyway. Leaving the
    # model unset is also valid and means "whatever this plan defaults to".
    # Vertex AI — the SAME Gemini catalogue on Google's enterprise endpoint, so
    # the curated list is REUSED rather than re-typed (a second literal would
    # drift on the next model rotation). Curated-only, `live=False`: express
    # mode documents exactly three methods — generateContent,
    # streamGenerateContent, countTokens — and no model-list endpoint, and the
    # project path's list call needs an OAuth token rather than the key the
    # catalog fetcher attaches. Promising a live fetch we cannot make would show
    # the user an empty picker on a working account; the curated list is the
    # honest one, and a newer model can still be typed into the model field.
    cat["vertex"] = CatalogSpec(
        "brain", "model", tuple(CURATED_MODELS.get("gemini", ())), live=False, discover="gemini"
    )
    cat["claude-cli"] = CatalogSpec(
        "brain",
        "model",
        tuple(
            _curated(
                [
                    ("sonnet", "Claude Sonnet (plan default tier)"),
                    ("opus", "Claude Opus (deepest, slowest)"),
                    ("haiku", "Claude Haiku (fastest)"),
                ]
            )
        ),
        live=False,
        # ``claude -p --model`` also takes full ids, so a release the aliases
        # have not reached yet is still pickable by name.
        discover="claude-api",
    )
    for p, (selects, opts) in TTS_CATALOG.items():
        cat[p] = CatalogSpec("tts", selects, tuple(opts), live=False)
    for p, opts in STT_CATALOG.items():
        cat[p] = CatalogSpec("stt", "model", tuple(opts), live=False)
    # The Vertex TTS card offers the identical 30-voice Gemini roster on a
    # different account, so it mirrors its sibling's picker rather than repeating
    # it — a voice added to the curated catalogue reaches both in one edit. (The
    # two STT cards share their list inside ``STT_CATALOG`` itself, above.)
    mirrored_tts = cat.get("gemini-flash-tts")
    if mirrored_tts is not None:
        cat["vertex-tts"] = mirrored_tts
    return cat


PROVIDER_CATALOG: dict[str, CatalogSpec] = _build_provider_catalog()


def catalog_spec(provider: str) -> CatalogSpec | None:
    """The picker spec for ``provider`` (None if it has no catalog)."""
    return PROVIDER_CATALOG.get(provider)


# ----------------------------------------------------------------------
# Pure parsing + sorting (module-level for easy testing)
# ----------------------------------------------------------------------


def parse_models_response(provider: str, payload: dict) -> list[ModelInfo]:
    """Map a provider's ``/v1/models`` JSON to a flat ``list[ModelInfo]``.

    Anthropic / OpenAI / Grok share the OpenAI-compatible ``data[].id`` shape.
    Gemini lists ``models[].name`` (``models/<id>``) with an optional
    ``displayName``. OpenRouter adds a human ``name`` we surface as the label and
    an ``architecture.output_modalities`` we carry through for the brain filter.
    Entries without a usable id are dropped.
    """
    out: list[ModelInfo] = []
    if provider == "ollama":
        # Native /api/tags: {"models": [{"name": "qwen3.5:9b", ...}, ...]} —
        # the installed-model list of the user's own server. DOWNLOADED models
        # only: ``:cloud`` entries are ollama.com-proxied references, not
        # local weights — a "local" card must never offer a path that leaves
        # the machine (maintainer report 2026-07-25).
        for m in payload.get("models", []) or []:
            raw = (m.get("name") or "").strip()
            if not raw or raw.endswith(":cloud") or m.get("remote"):
                continue
            out.append(ModelInfo(id=raw, label=raw))
        return out

    if provider == "gemini":
        for m in payload.get("models", []) or []:
            raw = (m.get("name") or "").removeprefix("models/").strip()
            if not raw:
                continue
            # Capability gate: ListModels also returns ids that CANNOT serve
            # generateContent (embeddings, Imagen/Veo/Lyria, Live/bidi audio —
            # and historically "gemini-3-flash", listed yet 404 on every call).
            # Offering those in the brain picker guarantees a broken pick, so
            # drop them on the DECLARED capability, never the name (AP-21).
            if not gemini_entry_serves_generate_content(m):
                continue
            label = (m.get("displayName") or "").strip() or raw
            out.append(ModelInfo(id=raw, label=label, output_modalities=_output_modalities(m)))
        return out

    # OpenAI-compatible shape (OpenAI / Anthropic / Grok / OpenRouter).
    for m in payload.get("data", []) or []:
        raw = (m.get("id") or "").strip()
        if not raw:
            continue
        label = (m.get("name") or "").strip() or raw  # OpenRouter has a name
        out.append(
            ModelInfo(
                id=raw,
                label=label,
                output_modalities=_output_modalities(m),
                input_modalities=(
                    _input_modalities(m) or _known_input_modalities(provider, raw)
                ),
                supported_parameters=_supported_parameters(m),
                pricing=_pricing(m),
                created=_created(m),
            )
        )
    return out


def _created(entry: dict) -> float | None:
    """The entry's ``created`` Unix timestamp, or None when absent/non-numeric."""
    raw = entry.get("created")
    if isinstance(raw, bool) or not isinstance(raw, int | float) or raw <= 0:
        return None
    return float(raw)


def _pricing(entry: dict) -> tuple[float, float] | None:
    """Pull ``pricing.prompt`` / ``pricing.completion`` as USD per 1M tokens.

    OpenRouter quotes both as decimal strings per TOKEN (``"0.000000375"``);
    direct provider endpoints carry no ``pricing`` block at all → ``None``.
    A negative value is OpenRouter's marker for "variable" (``openrouter/auto``
    routes to whatever answers) — no honest single price exists, so ``None``.
    """
    pricing = entry.get("pricing")
    if not isinstance(pricing, dict):
        return None
    try:
        prompt = float(pricing.get("prompt"))
        completion = float(pricing.get("completion"))
    except (TypeError, ValueError):
        return None  # missing or non-numeric price — same as no pricing block
    if prompt < 0 or completion < 0:
        return None
    return (prompt * 1_000_000, completion * 1_000_000)


def gemini_entry_serves_generate_content(entry: dict) -> bool:
    """True when a Gemini ListModels entry can serve ``generateContent``.

    Google's ListModels declares each model's callable methods
    (``supportedGenerationMethods``, ``supportedActions`` on newer API
    revisions). A model listed WITHOUT ``generateContent`` 404s on every chat
    call — the exact failure the picker/resolver must never offer ("models/…
    is not found … or is not supported for generateContent"). Fail-open: an
    entry that declares nothing is kept, so a payload/mock without the field
    never empties the catalog.
    """
    methods = entry.get("supportedGenerationMethods")
    if not isinstance(methods, list):
        methods = entry.get("supportedActions")
    if not isinstance(methods, list):
        return True
    return "generateContent" in {str(x) for x in methods}


def _input_modalities(entry: dict) -> tuple[str, ...] | None:
    """``architecture.input_modalities`` (e.g. ``("text","image")``) or None."""
    arch = entry.get("architecture")
    if not isinstance(arch, dict):
        return None
    mods = arch.get("input_modalities")
    if not isinstance(mods, list):
        return None
    return tuple(str(x) for x in mods)


# NVIDIA NIMs /v1/models roster is OpenAI-compatible but does not reliably
# publish per-model architecture metadata. Keep the small set of capabilities
# that NVIDIA documents explicitly so screenshot routing never treats a
# text-only Nemotron as visual. Unknown/new models still fail open below.
_KNOWN_INPUT_MODALITIES: dict[tuple[str, str], tuple[str, ...]] = {
    ("nvidia", "nvidia/nemotron-3-super-120b-a12b"): ("text",),
    ("nvidia", "nvidia/nemotron-3-ultra-550b-a55b"): ("text",),
    ("nvidia", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"): (
        "text",
        "image",
        "video",
        "audio",
    ),
}


def _known_input_modalities(provider: str, model_id: str) -> tuple[str, ...] | None:
    """Documented modality fallback when a provider catalog omits the field."""
    return _KNOWN_INPUT_MODALITIES.get(
        ((provider or "").strip().lower(), (model_id or "").strip().lower())
    )

def _supported_parameters(entry: dict) -> tuple[str, ...] | None:
    """OpenRouter's top-level ``supported_parameters`` (includes ``"tools"`` when
    the model can tool-call) or None when the endpoint doesn't expose it."""
    params = entry.get("supported_parameters")
    if not isinstance(params, list):
        return None
    return tuple(str(x) for x in params)


def model_capabilities(provider: str, model_id: str) -> dict[str, bool | None]:
    """Per-model capability hints from cache plus documented provider fallbacks.

    None means unknown. Unknown models therefore keep the existing fail-open
    behavior, while models with an explicit text-only contract cannot be sent
    screenshots merely because their provider family supports vision elsewhere.
    """
    from jarvis.core import config as _cfg

    cache_path = _cfg.DATA_DIR / "model_catalog_cache.json"
    mid = (model_id or "").strip()
    known_input = _known_input_modalities(provider, mid)
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        for m in data.get(provider, {}).get("models", []):
            if m.get("id") == mid:
                inp = m.get("input_modalities")
                params = m.get("supported_parameters")
                vision = (
                    ("image" in inp)
                    if isinstance(inp, list)
                    else ("image" in known_input if known_input is not None else None)
                )
                return {
                    "vision": vision,
                    "tools": ("tools" in params) if isinstance(params, list) else None,
                }
    except Exception:  # noqa: BLE001, S110 - missing/corrupt cache means fallback/unknown
        pass
    return {
        "vision": ("image" in known_input) if known_input is not None else None,
        "tools": None,
    }

def pick_vision_model(provider: str) -> str | None:
    """The best vision-capable brain model of provider, including known hints."""
    from jarvis.core import config as _cfg  # noqa: PLC0415

    cache_path = _cfg.DATA_DIR / "model_catalog_cache.json"
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        entries = data.get(provider, {}).get("models", [])
    except Exception:  # noqa: BLE001 - static hints may still provide a candidate
        entries = []

    candidates: list[ModelInfo] = []
    seen: set[str] = set()
    for m in entries:
        mid = str(m.get("id") or "").strip()
        inp = m.get("input_modalities")
        if not isinstance(inp, list):
            known = _known_input_modalities(provider, mid)
            inp = list(known) if known is not None else None
        if not mid or not (isinstance(inp, list) and "image" in inp):
            continue
        seen.add(mid.lower())
        out_mods = m.get("output_modalities")
        params = m.get("supported_parameters")
        candidates.append(
            ModelInfo(
                id=mid,
                label=str(m.get("label") or mid),
                output_modalities=tuple(out_mods) if isinstance(out_mods, list) else None,
                input_modalities=tuple(str(x) for x in inp),
                supported_parameters=tuple(params) if isinstance(params, list) else None,
            )
        )

    provider_key = (provider or "").strip().lower()
    for (known_provider, mid), inp in _KNOWN_INPUT_MODALITIES.items():
        if known_provider != provider_key or mid in seen or "image" not in inp:
            continue
        candidates.append(ModelInfo(id=mid, label=mid, input_modalities=inp))

    usable = sort_models(provider, filter_brain_models(candidates))
    return usable[0].id if usable else None

#: Name markers of the FAST model class (low-latency siblings). Computer-Use
#: issues one vision call per step, so step latency — not peak intelligence —
#: dominates mission wall-clock (live forensic 2026-07-02: think=60.8s of a
#: 75.8s mission on a flagship model). Data, not logic; extend by adding a row.
_FAST_CLASS_MARKERS: tuple[str, ...] = (
    "flash",
    "haiku",
    "mini",
    "lite",
    "turbo",
    "air",
    "nano",
)


def provider_has_modality_data(provider: str) -> bool:
    """Whether a provider has an informed modality verdict (cache or hints)."""
    from jarvis.core import config as _cfg  # noqa: PLC0415

    provider_key = (provider or "").strip().lower()
    if any(p == provider_key for p, _ in _KNOWN_INPUT_MODALITIES):
        return True
    cache_path = _cfg.DATA_DIR / "model_catalog_cache.json"
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        return any(
            isinstance(m.get("input_modalities"), list)
            for m in data.get(provider, {}).get("models", [])
        )
    except Exception:  # noqa: BLE001
        return False


def is_fast_class_model(model_id: str | None) -> bool:
    """True when the model id names a low-latency sibling (fast class).

    Markers match WHOLE id tokens (split on non-alphanumerics), never raw
    substrings — "mini" as a substring matches every "ge**mini**" model and
    classified Gemini PRO as fast (live bug 2026-07-02).
    """
    if not model_id:
        return False
    tokens = set(re.split(r"[^a-z0-9]+", model_id.lower()))
    return any(mark in tokens for mark in _FAST_CLASS_MARKERS)


def pick_fast_vision_model(provider: str) -> str | None:
    """The best FAST vision-capable model of provider, including known hints."""
    from jarvis.core import config as _cfg  # noqa: PLC0415

    cache_path = _cfg.DATA_DIR / "model_catalog_cache.json"
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        entries = data.get(provider, {}).get("models", [])
    except Exception:  # noqa: BLE001 - static hints may still provide a candidate
        entries = []

    candidates: list[ModelInfo] = []
    seen: set[str] = set()
    for m in entries:
        mid = str(m.get("id") or "").strip()
        inp = m.get("input_modalities")
        if not isinstance(inp, list):
            known = _known_input_modalities(provider, mid)
            inp = list(known) if known is not None else None
        if not mid or not (isinstance(inp, list) and "image" in inp):
            continue
        seen.add(mid.lower())
        out_mods = m.get("output_modalities")
        params = m.get("supported_parameters")
        candidates.append(
            ModelInfo(
                id=mid,
                label=str(m.get("label") or mid),
                output_modalities=tuple(out_mods) if isinstance(out_mods, list) else None,
                input_modalities=tuple(str(x) for x in inp),
                supported_parameters=tuple(params) if isinstance(params, list) else None,
            )
        )

    provider_key = (provider or "").strip().lower()
    for (known_provider, mid), inp in _KNOWN_INPUT_MODALITIES.items():
        if known_provider != provider_key or mid in seen or "image" not in inp:
            continue
        candidates.append(ModelInfo(id=mid, label=mid, input_modalities=inp))

    usable = sort_models(provider, filter_brain_models(candidates))
    if not usable:
        return None
    # Prefer the low-latency class when it belongs to a ranked family; with
    # one documented visual sibling (NVIDIA Omni) the normal sorted fallback
    # still selects it even if the family table has not learned its name yet.
    for m in usable:
        if _family_rank(m.id) > 0 and is_fast_class_model(m.id):
            return m.id
    return usable[0].id

def _output_modalities(entry: dict) -> tuple[str, ...] | None:
    """Pull ``architecture.output_modalities`` from a model entry as a tuple.

    Returns ``None`` when the field is absent (direct provider ``/v1/models``
    endpoints don't return ``architecture``) so the filter knows to fall back to
    the substring blocklist rather than treat "no data" as "no output".
    """
    arch = entry.get("architecture")
    if not isinstance(arch, dict):
        return None
    mods = arch.get("output_modalities")
    if not isinstance(mods, list):
        return None
    return tuple(str(x) for x in mods)


# Substrings (case-insensitive on the id) that mark a model as NOT a usable
# chat/reasoning brain: generative-media (video/image/music), audio I/O, speech,
# embeddings, moderation/safety classifiers. These can never back the brain (the
# probe would 404/400), and showing them in a brain picker is pure noise — the
# Gemini catalog in particular front-loads Veo/Imagen/Lyria/Nano-Banana. A truly
# exotic model is still reachable via the free-text custom entry.
_NON_BRAIN_MARKERS: tuple[str, ...] = (
    "veo",
    "imagen",
    "lyria",
    "nano-banana",
    "dall-e",
    "dalle",
    "sora",
    "whisper",
    "transcrib",
    "speech",
    "tts",
    "-audio",
    "audio-",
    "embed",
    "image",
    "moderation",
    "rerank",
    "realtime",
    "-live",
    "guard",
)


# Output modalities that disqualify a model as a chat/reasoning brain — a model
# that GENERATES image/audio/video can't back the text brain (the probe 400s and
# it is pure noise in the picker). NOTE: this is about OUTPUT only; a model that
# ACCEPTS image INPUT (vision) but outputs text is a valid — even required —
# brain (Computer-Use needs vision-input), so input modalities are NOT filtered.
_GENERATION_OUTPUT_MODALITIES: frozenset[str] = frozenset(
    {
        "image",
        "audio",
        "video",
        "music",
        "speech",
    }
)


def filter_brain_models(models: list[ModelInfo]) -> list[ModelInfo]:
    """Keep only models that can plausibly serve as a chat/reasoning brain.

    BOTH checks always apply (a model is dropped if EITHER fires):

    1. **Name blocklist** (:data:`_NON_BRAIN_MARKERS`) — drops embedding / rerank
       / moderation / safety-classifier (e.g. ``llama-guard``) and known media
       families. These OUTPUT text yet are not chat brains, so modality alone
       can't catch them — the name signal must always run.
    2. **Output modality** (:data:`_GENERATION_OUTPUT_MODALITIES`, when declared)
       — drops anything that GENERATES image/audio/video/music. Robust where the
       name blocklist isn't: e.g. ``openrouter/auto`` outputs text OR image yet
       has no marker in its id. Absent on direct provider ``/v1/models`` (no
       ``architecture`` field) → check 1 carries those.

    Vision-INPUT models (image in, text out) are KEPT — Computer-Use needs them;
    only OUTPUT modalities gate. Capability-based, never provider-name-based
    (AP-21). The UI's free-text entry still reaches anything dropped.
    """
    out: list[ModelInfo] = []
    for m in models:
        # 1. Name blocklist — always, even when text is the output (classifiers).
        low = m.id.lower()