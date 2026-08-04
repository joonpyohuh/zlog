"""Static renderer contracts backed by TypeScript compilation in CI."""

from pathlib import Path

from pipeline.ai.schemas import EffectId

REPO = Path(__file__).resolve().parents[1]
EFFECTS = REPO / "render" / "src" / "effects"


def test_registry_has_every_effect_and_implementation() -> None:
    registry = (EFFECTS / "registry.ts").read_text(encoding="utf-8")
    expected_files = {
        "CleanCut.tsx",
        "MicroPush.tsx",
        "ReactionPunchIn.tsx",
        "BlurCaptionFocus.tsx",
        "FreezeReactionHold.tsx",
        "SoftReveal.tsx",
        "AmbientOutro.tsx",
    }
    assert expected_files <= {path.name for path in EFFECTS.glob("*.tsx")}
    for effect in EffectId:
        assert f"{effect.value}:" in registry
    assert ": 'clean_cut'" in registry


def test_renderer_uses_execution_effects_not_clip_order() -> None:
    source = (EFFECTS / "CreativeEffect.tsx").read_text(encoding="utf-8")
    assert "entry_effect" in source
    assert "primary_effect" in source
    assert "exit_effect" in source
    assert "clip.order" not in source
