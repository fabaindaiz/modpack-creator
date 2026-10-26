"""The builds' list (O8 tanda 1, d-7985ee-a99db7 B2, B3; user-walk W15, W16, W20; H3 of d-7985ee-3bb53b)."""
from src.library.builds import BUILD_NAME_MAX, build_name


def test_a_name_is_trimmed_capped_and_empty_is_none() -> None:
    assert BUILD_NAME_MAX == 60
    assert build_name("  con Iris 1.7  ") == "con Iris 1.7"
    assert build_name("x" * 80) == "x" * 60
    assert build_name("   ") is None and build_name("") is None and build_name(None) is None
