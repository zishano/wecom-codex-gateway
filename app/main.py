from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Optional

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

from .codex_client import CodexAppServerClient, MockCodexClient
from .config import Settings
from .service import GatewayService
from .storage import GatewayStore
from .wecom import (
    MockWeComClient,
    WeComApiClient,
    WeComBotClient,
    WeComCallbackCodec,
    parse_incoming_message,
)


LOGGER = logging.getLogger(__name__)
MAX_CALLBACK_BYTES = 1024 * 1024


@dataclass
class Runtime:
    settings: Settings
    service: GatewayService
    codec: Optional[WeComCallbackCodec]
    wecom: object


class SimulatedMessage(BaseModel):
    user_id: str
    content: str
    message_id: str


def build_runtime(settings: Settings) -> Runtime:
    store = GatewayStore(settings.state_db)
    codex = (
        MockCodexClient()
        if settings.mock_codex
        else CodexAppServerClient(
            settings.codex_bin,
            settings.codex_cwd,
            settings.codex_model,
            settings.codex_sandbox,
            settings.codex_timeout_seconds,
        )
    )
    if settings.mock_wecom:
        wecom = MockWeComClient()
    elif settings.wecom_transport == "bot":
        wecom = WeComBotClient(settings)
    else:
        wecom = WeComApiClient(settings)
    codec = None
    if (
        not settings.mock_wecom
        and settings.wecom_transport == "callback"
        and settings.callback_token
        and settings.callback_aes_key
        and settings.corp_id
    ):
        codec = WeComCallbackCodec(
            settings.callback_token, settings.callback_aes_key, settings.corp_id
        )
    service = GatewayService(settings, store, codex, wecom)
    set_message_handler = getattr(wecom, "set_message_handler", None)
    if set_message_handler:
        set_message_handler(service.submit_message)
    return Runtime(settings, service, codec, wecom)


def create_app(
    settings: Optional[Settings] = None, runtime: Optional[Runtime] = None
) -> FastAPI:
    selected_settings = settings or Settings.from_env()
    selected_runtime = runtime or build_runtime(selected_settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        selected_settings.validate()
        await selected_runtime.service.start()
        application.state.runtime = selected_runtime
        try:
            yield
        finally:
            await selected_runtime.service.close()

    application = FastAPI(
        title="LMK Enterprise WeChat Codex Gateway",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.runtime = selected_runtime

    @application.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    @application.get("/readyz")
    async def readyz() -> JSONResponse:
        ready = selected_runtime.service.ready
        return JSONResponse(
            status_code=200 if ready else 503,
            content={"status": "ready" if ready else "not_ready"},
        )

    @application.get("/wecom/callback", response_class=PlainTextResponse)
    async def verify_callback(
        msg_signature: str = Query(...),
        timestamp: str = Query(...),
        nonce: str = Query(...),
        echostr: str = Query(...),
    ) -> str:
        if selected_runtime.codec is None:
            raise HTTPException(503, "Enterprise WeChat callback is not configured")
        try:
            return selected_runtime.codec.verify_url(
                msg_signature, timestamp, nonce, echostr
            )
        except Exception as exc:
            LOGGER.warning("Enterprise WeChat URL verification failed: %s", exc)
            raise HTTPException(403, "Invalid Enterprise WeChat signature") from exc

    @application.post("/wecom/callback", response_class=PlainTextResponse)
    async def receive_callback(
        request: Request,
        msg_signature: str = Query(...),
        timestamp: str = Query(...),
        nonce: str = Query(...),
    ) -> str:
        if selected_runtime.codec is None:
            raise HTTPException(503, "Enterprise WeChat callback is not configured")
        body = await request.body()
        if len(body) > MAX_CALLBACK_BYTES:
            raise HTTPException(413, "Callback payload is too large")
        try:
            decrypted = selected_runtime.codec.decrypt_message(
                body.decode("utf-8"), msg_signature, timestamp, nonce
            )
            message = parse_incoming_message(decrypted)
        except Exception as exc:
            LOGGER.warning("Enterprise WeChat callback rejected: %s", exc)
            raise HTTPException(403, "Invalid Enterprise WeChat callback") from exc
        selected_runtime.service.submit_message(
            message.user_id, message.content, message.message_id
        )
        return "success"

    @application.post("/dev/simulate", status_code=202)
    async def simulate_message(
        message: SimulatedMessage,
        x_dev_token: str = Header(default=""),
    ) -> dict:
        if not (selected_settings.mock_wecom or selected_settings.mock_codex):
            raise HTTPException(404, "Not found")
        if x_dev_token != selected_settings.dev_api_token:
            raise HTTPException(403, "Invalid development token")
        accepted = selected_runtime.service.submit_message(
            message.user_id, message.content, message.message_id
        )
        return {"accepted": accepted}

    @application.get("/dev/outbox")
    async def dev_outbox(x_dev_token: str = Header(default="")) -> dict:
        if not isinstance(selected_runtime.wecom, MockWeComClient):
            raise HTTPException(404, "Not found")
        if x_dev_token != selected_settings.dev_api_token:
            raise HTTPException(403, "Invalid development token")
        return {"messages": selected_runtime.wecom.outbox}

    @application.get("/dev/session/{user_id}")
    async def dev_session(
        user_id: str, x_dev_token: str = Header(default="")
    ) -> dict:
        if not (selected_settings.mock_wecom or selected_settings.mock_codex):
            raise HTTPException(404, "Not found")
        if x_dev_token != selected_settings.dev_api_token:
            raise HTTPException(403, "Invalid development token")
        session = selected_runtime.service.store.get_session(user_id)
        return {
            "user_id": session.user_id,
            "thread_id": session.thread_id,
            "active_turn_id": session.active_turn_id,
            "status": session.status,
            "status_detail": session.status_detail,
            "updated_at": session.updated_at,
        }

    return application


app = create_app()


def main() -> None:
    settings = Settings.from_env()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
