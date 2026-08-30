from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import FrozenSet, Optional

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _as_bool(value: str, default: bool = False) -> bool:
    if value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_path(value: str, base: Path = PROJECT_ROOT) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


@dataclass(frozen=True)
class Settings:
    wecom_transport: str
    bot_id: str
    bot_secret: str
    corp_id: str
    agent_id: int
    app_secret: str
    callback_token: str
    callback_aes_key: str
    allowed_users: FrozenSet[str]
    codex_bin: str
    codex_cwd: Path
    codex_model: Optional[str]
    codex_sandbox: str
    codex_timeout_seconds: int
    state_db: Path
    host: str
    port: int
    progress_delay_seconds: int
    max_wecom_message_bytes: int
    mock_wecom: bool
    mock_codex: bool
    dev_api_token: str

    @classmethod
    def from_env(cls, env_file: Optional[Path] = None) -> "Settings":
        load_dotenv(env_file or PROJECT_ROOT / ".env", override=False)
        model = os.getenv("CODEX_MODEL", "").strip() or None
        users = frozenset(
            user.strip()
            for user in os.getenv("WECOM_ALLOWED_USERS", "").split(",")
            if user.strip()
        )
        return cls(
            wecom_transport=os.getenv("WECOM_TRANSPORT", "callback").strip().lower(),
            bot_id=os.getenv("WECOM_BOT_ID", "").strip(),
            bot_secret=os.getenv("WECOM_BOT_SECRET", "").strip(),
            corp_id=os.getenv("WECOM_CORP_ID", "").strip(),
            agent_id=int(os.getenv("WECOM_AGENT_ID", "0") or 0),
            app_secret=os.getenv("WECOM_APP_SECRET", "").strip(),
            callback_token=os.getenv("WECOM_CALLBACK_TOKEN", "").strip(),
            callback_aes_key=os.getenv("WECOM_CALLBACK_AES_KEY", "").strip(),
            allowed_users=users,
            codex_bin=os.getenv("CODEX_BIN", "codex").strip() or "codex",
            codex_cwd=_resolve_path(os.getenv("CODEX_CWD", "/mnt/e/Project/LMK")),
            codex_model=model,
            codex_sandbox=os.getenv("CODEX_SANDBOX", "workspace-write").strip(),
            codex_timeout_seconds=int(os.getenv("CODEX_TIMEOUT_SECONDS", "900")),
            state_db=_resolve_path(os.getenv("STATE_DB", "./data/gateway.db")),
            host=os.getenv("HOST", "127.0.0.1").strip(),
            port=int(os.getenv("PORT", "8787")),
            progress_delay_seconds=int(os.getenv("PROGRESS_DELAY_SECONDS", "8")),
            max_wecom_message_bytes=int(os.getenv("MAX_WECOM_MESSAGE_BYTES", "1800")),
            mock_wecom=_as_bool(os.getenv("MOCK_WECOM", "false")),
            mock_codex=_as_bool(os.getenv("MOCK_CODEX", "false")),
            dev_api_token=os.getenv("DEV_API_TOKEN", "").strip(),
        )

    def validate(self) -> None:
        errors = []
        if not self.codex_cwd.is_dir():
            errors.append(f"CODEX_CWD does not exist: {self.codex_cwd}")
        if self.codex_sandbox not in {"read-only", "workspace-write"}:
            errors.append("CODEX_SANDBOX must be read-only or workspace-write")
        if not self.mock_codex:
            binary_exists = Path(self.codex_bin).is_file() or shutil.which(self.codex_bin)
            if not binary_exists:
                errors.append(f"CODEX_BIN was not found: {self.codex_bin}")
        if self.wecom_transport not in {"callback", "bot"}:
            errors.append("WECOM_TRANSPORT must be callback or bot")
        if not self.mock_wecom and self.wecom_transport == "bot":
            required = {
                "WECOM_BOT_ID": self.bot_id,
                "WECOM_BOT_SECRET": self.bot_secret,
                "WECOM_ALLOWED_USERS": self.allowed_users,
            }
            for name, value in required.items():
                if not value:
                    errors.append(f"{name} is required")
        if not self.mock_wecom and self.wecom_transport == "callback":
            required = {
                "WECOM_CORP_ID": self.corp_id,
                "WECOM_AGENT_ID": self.agent_id,
                "WECOM_APP_SECRET": self.app_secret,
                "WECOM_CALLBACK_TOKEN": self.callback_token,
                "WECOM_CALLBACK_AES_KEY": self.callback_aes_key,
                "WECOM_ALLOWED_USERS": self.allowed_users,
            }
            for name, value in required.items():
                if not value:
                    errors.append(f"{name} is required")
            if self.callback_aes_key and len(self.callback_aes_key) != 43:
                errors.append("WECOM_CALLBACK_AES_KEY must contain 43 characters")
        if (self.mock_wecom or self.mock_codex) and len(self.dev_api_token) < 16:
            errors.append("DEV_API_TOKEN must contain at least 16 characters in mock mode")
        if self.max_wecom_message_bytes < 256:
            errors.append("MAX_WECOM_MESSAGE_BYTES must be at least 256")
        if errors:
            raise ValueError("Invalid configuration:\n- " + "\n- ".join(errors))

    def is_user_allowed(self, user_id: str) -> bool:
        return (
            self.mock_wecom
            or "*" in self.allowed_users
            or user_id in self.allowed_users
        )
