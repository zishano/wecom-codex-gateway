from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from typing import Any, Dict, Optional, Set

from .codex_client import CodexProtocolError
from .config import Settings
from .storage import GatewayStore


LOGGER = logging.getLogger(__name__)

HELP_TEXT = """LMK 工作助手命令

/帮助  查看命令
/状态  查看当前线程和处理状态
/继续  查看并继续当前线程
/新建 [主题]  新建独立分析线程
/总结  总结工作主线、风险和未来两周行动
/任务  查看近期行动清单
/记录 内容  将新沟通或工作内容整理进档案
/取消  中断当前分析

直接发送文字会在当前 LMK 线程中继续分析。"""

IMMEDIATE_COMMANDS = {
    "/帮助",
    "/help",
    "/状态",
    "/status",
    "/继续",
    "/continue",
    "/取消",
    "/cancel",
}


class GatewayService:
    def __init__(
        self,
        settings: Settings,
        store: GatewayStore,
        codex: Any,
        wecom: Any,
    ):
        self.settings = settings
        self.store = store
        self.codex = codex
        self.wecom = wecom
        self._tasks: Set[asyncio.Task] = set()
        self._locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._loaded_threads: Set[str] = set()
        self.ready = False

    async def start(self) -> None:
        await self.codex.start()
        start_wecom = getattr(self.wecom, "start", None)
        try:
            if start_wecom:
                await start_wecom()
        except Exception:
            await self.codex.close()
            raise
        self.ready = True

    async def close(self) -> None:
        self.ready = False
        active = list(self._tasks)
        if active:
            done, pending = await asyncio.wait(active, timeout=10)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        close_wecom = getattr(self.wecom, "close", None)
        if close_wecom:
            await close_wecom()
        await self.codex.close()
        self.store.close()

    def submit_message(
        self, user_id: str, content: str, message_id: str
    ) -> bool:
        if not self.settings.is_user_allowed(user_id):
            LOGGER.warning("Rejected Enterprise WeChat user %s", user_id)
            return False
        if not self.store.record_message(
            user_id, "incoming", content, external_id=message_id
        ):
            LOGGER.info("Ignored duplicate Enterprise WeChat message %s", message_id)
            return False
        task = asyncio.create_task(self._handle_message(user_id, content.strip()))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return True

    async def _handle_message(self, user_id: str, content: str) -> None:
        try:
            command = content.partition(" ")[0].strip()
            if command in IMMEDIATE_COMMANDS:
                await self._handle_local_command(user_id, content)
                return
            async with self._locks[user_id]:
                if not content:
                    await self._reply(user_id, "消息内容为空，请发送文字或使用 /帮助。")
                    return
                if await self._handle_local_command(user_id, content):
                    return
                prompt = self._prompt_for_message(content)
                await self._run_codex(user_id, prompt)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Failed to process message for %s", user_id)
            self.store.set_status(user_id, "failed", "处理失败")
            await self._reply(
                user_id,
                "处理失败。请稍后重试；若持续失败，请在服务器查看日志和 /readyz。",
            )

    async def _handle_local_command(self, user_id: str, content: str) -> bool:
        command, _, argument = content.partition(" ")
        command = command.strip()
        argument = argument.strip()
        if command in {"/帮助", "/help"}:
            await self._reply(user_id, HELP_TEXT)
            return True
        if command in {"/状态", "/status"}:
            session = self.store.get_session(user_id)
            thread = session.thread_id or "尚未创建"
            await self._reply(
                user_id,
                f"状态：{session.status}\n详情：{session.status_detail or '无'}\n"
                f"线程：{thread}\n更新时间：{session.updated_at}",
            )
            return True
        if command in {"/继续", "/continue"}:
            session = self.store.get_session(user_id)
            if session.thread_id:
                await self._reply(user_id, f"已继续当前线程：{session.thread_id}")
            else:
                await self._reply(user_id, "尚无线程。直接发送问题即可自动创建。")
            return True
        if command in {"/新建", "/new"}:
            session = self.store.get_session(user_id)
            if session.active_turn_id:
                await self._reply(user_id, "当前仍在分析，请先等待完成或使用 /取消。")
                return True
            name = argument or "LMK 企业微信工作分析"
            thread_id = await self.codex.start_thread(name)
            self.store.set_thread(user_id, thread_id)
            self._loaded_threads.add(thread_id)
            self.store.set_status(user_id, "idle", f"新线程：{name}")
            await self._reply(user_id, f"已创建新线程：{name}\n{thread_id}")
            return True
        if command in {"/取消", "/cancel"}:
            session = self.store.get_session(user_id)
            if session.thread_id and session.active_turn_id:
                await self.codex.interrupt(session.thread_id, session.active_turn_id)
                self.store.set_active_turn(user_id, None)
                self.store.set_status(user_id, "interrupted", "用户主动取消")
                await self._reply(user_id, "已请求中断当前分析。")
            else:
                await self._reply(user_id, "当前没有正在运行的分析。")
            return True
        if command in {"/记录", "/record"} and not argument:
            await self._reply(user_id, "请使用：/记录 需要归档的聊天或工作内容")
            return True
        return False

    def _prompt_for_message(self, content: str) -> str:
        if content in {"/总结", "/summary"}:
            return (
                "读取 LMK 工作档案，简明总结当前工作主线、考核目标变化、主要成果、"
                "风险、待主管确认事项和未来两周最重要的行动。"
            )
        if content in {"/任务", "/tasks"}:
            return (
                "读取工作档案/05_行动清单.md 和最新周报，列出当前任务，按优先级说明"
                "截止时间、验收方式、阻塞项和今天最应该做的第一步。"
            )
        if content.startswith("/记录 ") or content.startswith("/record "):
            _, _, record = content.partition(" ")
            return (
                "把下面由用户提供的新记录作为事实来源处理。先区分明确事实、用户转述、"
                "待确认内容和分析判断，再更新 LMK 工作档案中相关的原始记录、主管决策、"
                "成果证据和行动清单，最后说明更新了什么以及下一步建议。\n\n"
                f"新记录：\n{record}"
            )
        return content

    async def _run_codex(self, user_id: str, prompt: str) -> None:
        self.store.set_status(user_id, "analyzing", "已收到，准备读取工作档案")
        await self._reply(user_id, "已收到，正在读取 LMK 工作档案并分析。")
        started_at = time.monotonic()
        activities = []

        async def on_started(turn_id: str) -> None:
            self.store.set_active_turn(user_id, turn_id)

        async def on_progress(activity: str) -> None:
            is_new = activity not in activities
            if is_new:
                activities.append(activity)
            self.store.set_status(user_id, "analyzing", activity)
            if is_new:
                await self._progress(user_id, activity)

        heartbeat = asyncio.create_task(
            self._heartbeat(user_id, started_at, activities)
        )
        thread_id = None
        try:
            thread_id = await self._ensure_thread(user_id)
            result = await self.codex.run_turn(
                thread_id,
                prompt,
                on_progress=on_progress,
                on_turn_started=on_started,
            )
            self.store.set_status(user_id, "idle", "分析完成")
            await self._reply(user_id, result.text)
        except CodexProtocolError:
            if thread_id:
                self._loaded_threads.discard(thread_id)
            raise
        finally:
            heartbeat.cancel()
            self.store.set_active_turn(user_id, None)

    async def _heartbeat(
        self, user_id: str, started_at: float, activities: list
    ) -> None:
        try:
            await asyncio.sleep(max(self.settings.progress_delay_seconds, 8))
            while True:
                elapsed = int(time.monotonic() - started_at)
                detail = "；".join(activities[-3:]) or "正在分析材料"
                await self._progress(user_id, f"仍在处理中（{elapsed} 秒）：{detail}")
                await asyncio.sleep(max(self.settings.progress_delay_seconds, 8))
        except asyncio.CancelledError:
            return

    async def _ensure_thread(self, user_id: str) -> str:
        session = self.store.get_session(user_id)
        if session.thread_id:
            if session.thread_id not in self._loaded_threads:
                try:
                    await self.codex.resume_thread(session.thread_id)
                    self._loaded_threads.add(session.thread_id)
                except Exception:
                    LOGGER.exception("Could not resume %s; creating a new thread", session.thread_id)
                    self.store.set_thread(user_id, None)
                else:
                    return session.thread_id
            else:
                return session.thread_id
        thread_id = await self.codex.start_thread(f"LMK 工作分析 - {user_id}")
        self.store.set_thread(user_id, thread_id)
        self._loaded_threads.add(thread_id)
        return thread_id

    async def _reply(self, user_id: str, text: str) -> None:
        self.store.record_message(user_id, "outgoing", text)
        await self.wecom.send_text(user_id, text)

    async def _progress(self, user_id: str, text: str) -> None:
        self.store.record_message(user_id, "outgoing", f"进度：{text}")
        send_progress = getattr(self.wecom, "send_progress", None)
        if send_progress:
            await send_progress(user_id, text)
        else:
            await self.wecom.send_text(user_id, f"进度：{text}")
