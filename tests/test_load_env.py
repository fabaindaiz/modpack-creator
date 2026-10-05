"""load_env nunca escribe un valor en el log: `.env.yaml` guarda secretos, y el log sale del
proceso (docker logs, un agregador). Solo las claves."""
import logging
from pathlib import Path

import pytest

SECRET = "s3cr3t-value-never-logged"


@pytest.mark.parametrize("verbose", [False, True])
def test_load_env_logs_keys_never_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                         caplog: pytest.LogCaptureFixture, verbose: bool) -> None:
    from src.library import utils

    monkeypatch.setattr(utils, "SKIPPED_NON_STRING", set())
    monkeypatch.setenv("LOAD_ENV_PRESENT", "already")
    monkeypatch.delenv("LOAD_ENV_NEW", raising=False)
    path = tmp_path / ".env.yaml"
    path.write_text(f'LOAD_ENV_PRESENT: "{SECRET}-a"\nLOAD_ENV_NEW: "{SECRET}-b"\nLOAD_ENV_NUMBER: 8080\n')
    with caplog.at_level(logging.DEBUG):
        utils.load_env(str(path), verbose=verbose)
    monkeypatch.delenv("LOAD_ENV_NEW")

    logged = caplog.text
    assert SECRET not in logged and "8080" not in logged
    assert "LOAD_ENV_PRESENT" in logged
    if verbose:
        assert "LOAD_ENV_NEW" in logged
