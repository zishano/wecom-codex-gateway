from pathlib import Path

import pytest

from app.config import Settings


@pytest.fixture
def mock_settings(tmp_path: Path) -> Settings:
    return Settings(
        wecom_transport="callback",
        bot_id="",
        bot_secret="",
        corp_id="",
        agent_id=0,
        app_secret="",
        callback_token="",
        callback_aes_key="",
        allowed_users=frozenset(),
        codex_bin="codex",
        codex_cwd=Path("/mnt/e/Project/LMK"),
        codex_model=None,
        codex_sandbox="workspace-write",
        codex_timeout_seconds=30,
        state_db=tmp_path / "gateway.db",
        host="127.0.0.1",
        port=8787,
        progress_delay_seconds=60,
        max_wecom_message_bytes=1800,
        mock_wecom=True,
        mock_codex=True,
        dev_api_token="test-development-token",
    )
