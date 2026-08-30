import asyncio
import time
from dataclasses import replace

import httpx

from app.codex_client import CodexTurnResult, MockCodexClient
from app.main import Runtime, build_runtime, create_app
from app.service import GatewayService
from app.storage import GatewayStore
from app.wecom import MockWeComClient


class SlowCodexClient(MockCodexClient):
    def __init__(self):
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run_turn(
        self, thread_id, text, on_progress=None, on_turn_started=None
    ):
        if on_turn_started:
            await on_turn_started("slow-turn")
        self.started.set()
        await self.release.wait()
        return CodexTurnResult("当前分析已取消。", "interrupted", [])

    async def interrupt(self, thread_id: str, turn_id: str) -> None:
        self.release.set()


def test_mock_wecom_ignores_placeholder_callback_credentials(mock_settings):
    settings = replace(
        mock_settings,
        corp_id="placeholder-corp-id",
        callback_token="placeholder-token",
        callback_aes_key="replace-with-enterprise-wechat-encoding-aes-key",
    )

    runtime = build_runtime(settings)

    assert runtime.codec is None


async def _wait_for_messages(client: httpx.AsyncClient, token: str, count: int):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        response = await client.get(
            "/dev/outbox", headers={"X-Dev-Token": token}
        )
        messages = response.json()["messages"]
        if len(messages) >= count:
            return messages
        await asyncio.sleep(0.02)
    raise AssertionError(f"Expected at least {count} messages")


async def _with_client(settings, scenario):
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            await scenario(client)


def test_mock_end_to_end_and_duplicate_message(mock_settings):
    async def scenario(client: httpx.AsyncClient):
        assert (await client.get("/readyz")).status_code == 200
        payload = {
            "user_id": "lmk",
            "content": "/总结",
            "message_id": "message-1",
        }
        headers = {"X-Dev-Token": mock_settings.dev_api_token}
        first = await client.post("/dev/simulate", json=payload, headers=headers)
        assert first.status_code == 202
        assert first.json()["accepted"] is True
        messages = await _wait_for_messages(
            client, mock_settings.dev_api_token, 3
        )
        assert "正在读取" in messages[0]["text"]
        assert "模拟 Codex 回复" in messages[-1]["text"]

        duplicate = await client.post(
            "/dev/simulate", json=payload, headers=headers
        )
        assert duplicate.json()["accepted"] is False

        session = await client.get(
            "/dev/session/lmk", headers=headers
        )
        assert session.status_code == 200
        assert session.json()["status"] == "idle"

    asyncio.run(_with_client(mock_settings, scenario))


def test_help_and_status_commands(mock_settings):
    async def scenario(client: httpx.AsyncClient):
        headers = {"X-Dev-Token": mock_settings.dev_api_token}
        await client.post(
            "/dev/simulate",
            json={"user_id": "lmk", "content": "/帮助", "message_id": "help-1"},
            headers=headers,
        )
        messages = await _wait_for_messages(
            client, mock_settings.dev_api_token, 1
        )
        assert "/状态" in messages[-1]["text"]

        await client.post(
            "/dev/simulate",
            json={"user_id": "lmk", "content": "/状态", "message_id": "status-1"},
            headers=headers,
        )
        messages = await _wait_for_messages(
            client, mock_settings.dev_api_token, 2
        )
        assert "状态：" in messages[-1]["text"]

    asyncio.run(_with_client(mock_settings, scenario))


def test_dev_endpoint_requires_token(mock_settings):
    async def scenario(client: httpx.AsyncClient):
        response = await client.post(
            "/dev/simulate",
            json={"user_id": "lmk", "content": "hello", "message_id": "bad-token"},
        )
        assert response.status_code == 403

    asyncio.run(_with_client(mock_settings, scenario))


def test_status_and_cancel_do_not_wait_for_long_turn(mock_settings):
    async def scenario():
        store = GatewayStore(mock_settings.state_db)
        codex = SlowCodexClient()
        wecom = MockWeComClient()
        service = GatewayService(mock_settings, store, codex, wecom)
        runtime = Runtime(mock_settings, service, None, wecom)
        application = create_app(mock_settings, runtime)
        async with application.router.lifespan_context(application):
            transport = httpx.ASGITransport(app=application)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                headers = {"X-Dev-Token": mock_settings.dev_api_token}
                await client.post(
                    "/dev/simulate",
                    json={"user_id": "lmk", "content": "long task", "message_id": "long-1"},
                    headers=headers,
                )
                await asyncio.wait_for(codex.started.wait(), timeout=1)

                await client.post(
                    "/dev/simulate",
                    json={"user_id": "lmk", "content": "/状态", "message_id": "status-live"},
                    headers=headers,
                )
                await _wait_for_messages(client, mock_settings.dev_api_token, 2)
                assert any("状态：analyzing" in item["text"] for item in wecom.outbox)

                await client.post(
                    "/dev/simulate",
                    json={"user_id": "lmk", "content": "/取消", "message_id": "cancel-live"},
                    headers=headers,
                )
                await asyncio.wait_for(codex.release.wait(), timeout=1)
                await _wait_for_messages(client, mock_settings.dev_api_token, 4)
                assert any("已请求中断" in item["text"] for item in wecom.outbox)

    asyncio.run(scenario())
