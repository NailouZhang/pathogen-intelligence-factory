from pifactory.cover import STYLE_VERSION, _compose_cover, _deterministic_pathogen_art


def test_cover_supports_cjk_and_does_not_change_with_issue_date():
    profile = {"profile_id":"hantavirus", "display_name_zh":"汉坦病毒", "display_name_en":"Hantavirus"}
    background = _deterministic_pathogen_art(profile, (1200, 675))
    first = _compose_cover(background, profile, "2026-07-20")
    second = _compose_cover(background, profile, "2030-01-01")
    assert first.tobytes() == second.tobytes()
    assert "no-date" in STYLE_VERSION
