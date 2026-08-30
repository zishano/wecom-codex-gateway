from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from aibot import WSClient, WSClientOptions, generate_req_id

import requests
from wechatpy.enterprise.crypto import WeChatCrypto

from .config import Settings


LOGGER = logging.getLogger(__name__)
TOKEN_ERROR_CODES = {40014, 42001, 42007, 42009}


@dataclass(frozen=True)
class IncomingMessage:
    user_id: str
    content: str
    message_id: str
    message_type: str


class WeComCallbackCodec:
    def __init__(self, token: str, aes_key: str, corp_id: str):
        self._crypto = WeChatCrypto(token, aes_key, corp_id)

    def verify_url(
        self, signature: str, timestamp: str, nonce: str, echo_string: str
    ) -> str:
        result = self._crypto.check_signature(
            signature, timestamp, nonce, echo_string
        )
        if isinstance(result, bytes):
            return result.decode("utf-8")
        return str(result)

    def decrypt_message(
        self, payload: str, signature: str, timestamp: str, nonce: str
    ) -> str:
        result = self._crypto.decrypt_message(
            payload, signature, timestamp, nonce
        )
        if isinstance(result, bytes):
            return result.decode("utf-8")
        return str(result)


def parse_incoming_message(xml_text: str) -> IncomingMessage:
    root = ET.fromstring(xml_text)

    def text(name: str) -> str:
        node = root.find(name)
        return (node.text or "").strip() if node is not None else ""

    user_id = text("FromUserName")
    message_type = text("MsgType") or "unknown"
    if message_type == "text":
        content = text("Content")
    elif message_type == "voice":
        content = text("Recognition")
    elif message_type == "event":
        content = f"[企业微信事件] {text('Event')} {text('EventKey')}".strip()
    else:
        content = f"[暂不支持的消息类型：{message_type}]"

    message_id = text("MsgId")
    if not message_id:
        stable_source = "|".join(
            [user_id, text("CreateTime"), message_type, content]
        ).encode("utf-8")
        message_id = hashlib.sha256(stable_source).hexdigest()
    if not user_id:
        raise ValueError("Callback did not contain FromUserName")
    return IncomingMessage(user_id, content, message_id, message_type)


def split_utf8(text: str, max_bytes: int) -> List[str]:
    if len(text.encode("utf-8")) <= max_bytes:
        return [text]
    chunks: List[str] = []
    current: List[str] = []
    current_size = 0
    for character in text:
        encoded_size = len(character.encode("utf-8"))
        if current and current_size + encoded_size > max_bytes:
            chunks.append("".join(current))
            current = []
            current_size = 0
        current.append(character)
        current_size += encoded_size
    if current:
        chunks.append("".join(current))
    return chunks


class WeComApiClient:
    def __init__(self, settings: Settings):
        self._corp_id = settings.corp_id
        self._secret = settings.app_secret
        self._agent_id = settings.agent_id
        self._max_bytes = settings.max_wecom_message_bytes
        self._session = requests.Session()
        self._access_token = ""
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    async def send_text(self, user_id: str, text: str) -> None:
        chunks = split_utf8(text, self._max_bytes)
        total = len(chunks)
        for index, chunk in enumerate(chunks, start=1):
            content = chunk
            if total > 1:
                content = f"[{index}/{total}]\n{chunk}"
            await self._send_chunk(user_id, content)

    async def _send_chunk(self, user_id: str, content: str) -> None:
        token = await self._get_token()
        response = await asyncio.to_thread(
            self._post_message, token, user_id, content
        )
        if response.get("errcode") in TOKEN_ERROR_CODES:
            async with self._token_lock:
                self._access_token = ""
                self._token_expires_at = 0.0
            token = await self._get_token()
            response = await asyncio.to_thread(
                self._post_message, token, user_id, content
            )
        if response.get("errcode", 0) != 0:
            raise RuntimeError(f"Enterprise WeChat send failed: {response}")

    async def _get_token(self) -> str:
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token
        async with self._token_lock:
            if self._access_token and time.time() < self._token_expires_at:
                return self._access_token
            response = await asyncio.to_thread(self._fetch_token)
            if response.get("errcode", 0) != 0 or not response.get("access_token"):
                raise RuntimeError(f"Enterprise WeChat token request failed: {response}")
            self._access_token = response["access_token"]
            self._token_expires_at = time.time() + int(response.get("expires_in", 7200)) - 300
            return self._access_token

    def _fetch_token(self) -> dict:
        response = self._session.get(
            "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
            params={"corpid": self._corp_id, "corpsecret": self._secret},
            timeout=15,
        )
        response.raise_for_status()
        return response.json()

    def _post_message(self, token: str, user_id: str, content: str) -> dict:
        response = self._session.post(
            "https://qyapi.weixin.qq.com/cgi-bin/message/send",
            params={"access_token": token},
            json={
                "touser": user_id,
                "msgtype": "text",
                "agentid": self._agent_id,
                "text": {"content": content},
                "safe": 0,
            },
            timeout=15,
        )
        response.raise_for_status()
        return response.json()


class _SdkLogger:
    def debug(self, message: str, *args: object) -> None:
        LOGGER.debug(message, *args)

    def info(self, message: str, *args: object) -> None:
        LOGGER.info(message, *args)

    def warn(self, message: str, *args: object) -> None:
        LOGGER.warning(message, *args)

    def error(self, message: str, *args: object) -> None:
        LOGGER.error(message, *args)


def parse_bot_message(frame: Dict[str, Any]) -> Tuple[IncomingMessage, str]:
    body = frame.get("body") or {}
    sender = body.get("from") or {}
    user_id = str(sender.get("userid") or "").strip()
    message_type = str(body.get("msgtype") or "unknown").strip()
    if message_type == "text":
        content = str((body.get("text") or {}).get("content") or "").strip()
    elif message_type == "voice":
        voice = body.get("voice") or {}
        content = str(voice.get("content") or voice.get("recognition") or "").strip()
    else:
        content = f"[暂不支持的消息类型：{message_type}]"
    message_id = str(body.get("msgid") or "").strip()
    if not message_id:
        stable_source = "|".join(
            [user_id, str(body.get("create_time") or ""), message_type, content]
        ).encode("utf-8")
        message_id = hashlib.sha256(stable_source).hexdigest()
    if not user_id:
        raise ValueError("Bot callback did not contain body.from.userid")
    reply_target = str(body.get("chatid") or user_id).strip()
    return IncomingMessage(user_id, content, message_id, message_type), reply_target


class WeComBotClient:
    def __init__(self, settings: Settings, sdk_client: Optional[Any] = None):
        self._max_bytes = settings.max_wecom_message_bytes
        self._client = sdk_client or WSClient(
            WSClientOptions(
                bot_id=settings.bot_id,
                secret=settings.bot_secret,
                max_reconnect_attempts=-1,
                logger=_SdkLogger(),
            )
        )
        self._message_handler: Optional[Callable[[str, str, str], bool]] = None
        self._reply_targets: Dict[str, str] = {}
        self._reply_frames: Dict[str, Dict[str, Any]] = {}
        self._progress_streams: Dict[str, str] = {}
        self._progress_lines: Dict[str, List[str]] = {}
        self._authenticated = asyncio.Event()
        self._bind_events()

    def _bind_events(self) -> None:
        self._client.on("authenticated", self._on_authenticated)
        self._client.on("disconnected", self._on_disconnected)
        self._client.on("error", self._on_error)
        self._client.on("message.text", self._on_message)
        self._client.on("message.voice", self._on_message)
        self._client.on("event.enter_chat", self._on_enter_chat)

    def set_message_handler(
        self, handler: Callable[[str, str, str], bool]
    ) -> None:
        self._message_handler = handler

    def _on_authenticated(self) -> None:
        self._authenticated.set()
        LOGGER.info("Enterprise WeChat bot long connection authenticated")

    def _on_disconnected(self, reason: str) -> None:
        self._authenticated.clear()
        LOGGER.warning("Enterprise WeChat bot disconnected: %s", reason)

    def _on_error(self, error: Exception) -> None:
        LOGGER.error("Enterprise WeChat bot error: %s", error)

    async def _on_message(self, frame: Dict[str, Any]) -> None:
        try:
            message, reply_target = parse_bot_message(frame)
            self._reply_targets[message.user_id] = reply_target
            self._reply_frames[message.user_id] = frame
            if self._message_handler is None:
                raise RuntimeError("Bot message handler is not configured")
            self._message_handler(
                message.user_id, message.content, message.message_id
            )
        except Exception:
            LOGGER.exception("Could not process Enterprise WeChat bot message")

    async def _on_enter_chat(self, frame: Dict[str, Any]) -> None:
        try:
            await self._client.reply_welcome(
                frame,
                {
                    "msgtype": "text",
                    "text": {"content": "LMK 工作助手已连接。发送 /帮助 查看命令。"},
                },
            )
        except Exception:
            LOGGER.exception("Could not send Enterprise WeChat bot welcome message")

    async def start(self) -> None:
        await self._client.connect()
        try:
            await asyncio.wait_for(self._authenticated.wait(), timeout=20)
        except asyncio.TimeoutError as exc:
            self._client.disconnect()
            raise RuntimeError(
                "Enterprise WeChat bot authentication timed out; check BotID, Secret, "
                "network access, and whether another connection is using this bot"
            ) from exc

    async def close(self) -> None:
        self._authenticated.clear()
        self._client.disconnect()

    async def send_text(self, user_id: str, text: str) -> None:
        if not self._authenticated.is_set():
            raise RuntimeError("Enterprise WeChat bot is not authenticated")
        target = self._reply_targets.get(user_id, user_id)
        frame = self._reply_frames.get(user_id)
        stream_id = self._progress_streams.pop(user_id, None)
        self._progress_lines.pop(user_id, None)
        if frame and stream_id:
            await self._client.reply_stream(frame, stream_id, text, True)
            return
        chunks = split_utf8(text, self._max_bytes)
        total = len(chunks)
        for index, chunk in enumerate(chunks, start=1):
            content = f"[{index}/{total}]\n{chunk}" if total > 1 else chunk
            await self._client.send_message(
                target,
                {"msgtype": "markdown", "markdown": {"content": content}},
            )

    async def send_progress(self, user_id: str, text: str) -> None:
        """Update one streaming progress bubble for the current bot message."""
        if not self._authenticated.is_set():
            raise RuntimeError("Enterprise WeChat bot is not authenticated")
        frame = self._reply_frames.get(user_id)
        if frame is None:
            await self.send_text(user_id, f"进度：{text}")
            return
        stream_id = self._progress_streams.setdefault(
            user_id, generate_req_id("lmk-progress")
        )
        lines = self._progress_lines.setdefault(user_id, [])
        if text.startswith("仍在处理中"):
            if lines and lines[-1].startswith("仍在处理中"):
                lines[-1] = text
            else:
                lines.append(text)
        elif text not in lines:
            lines.append(text)
        lines[:] = lines[-8:]
        content = "**任务进度**\n" + "\n".join(f"- {line}" for line in lines)
        while len(content.encode("utf-8")) > self._max_bytes and len(lines) > 1:
            lines.pop(0)
            content = "**任务进度**\n" + "\n".join(f"- {line}" for line in lines)
        if len(content.encode("utf-8")) > self._max_bytes:
            content = split_utf8(content, self._max_bytes)[0]
        await self._client.reply_stream(frame, stream_id, content, False)

class MockWeComClient:
    def __init__(self):
        self.outbox: List[dict] = []

    async def send_text(self, user_id: str, text: str) -> None:
        LOGGER.info("Mock WeCom message to %s: %s", user_id, text)
        self.outbox.append({"user_id": user_id, "text": text})
