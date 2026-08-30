import base64
import xml.etree.ElementTree as ET

from wechatpy.enterprise.crypto import WeChatCrypto

from app.wecom import (
    WeComBotClient,
    WeComCallbackCodec,
    parse_bot_message,
    parse_incoming_message,
    split_utf8,
)

from app.codex_client import _item_progress, _plan_progress, _summary_progress


def test_split_utf8_respects_byte_limit():
    chunks = split_utf8("中文内容" * 100, 64)
    assert len(chunks) > 1
    assert "".join(chunks) == "中文内容" * 100
    assert all(len(chunk.encode("utf-8")) <= 64 for chunk in chunks)


def test_progress_events_are_safe_and_readable():
    assert _item_progress({"type": "commandExecution"}, started=True) == "开始执行本地检查"
    assert _item_progress({"type": "commandExecution"}, started=False) == "已完成一项本地检查"
    assert _plan_progress([
        {"step": "读取工作档案", "status": "inProgress"},
        {"step": "输出行动清单", "status": "pending"},
    ]) == "计划：进行中：读取工作档案；待处理：输出行动清单"
    summary = _summary_progress("先读取工作档案，再核对最近的主管沟通记录。")
    assert summary.startswith("分析摘要：")
    assert len(summary) <= 166


def test_parse_text_callback():
    message = parse_incoming_message(
        """<xml>
        <FromUserName><![CDATA[lmk]]></FromUserName>
        <CreateTime>1788012345</CreateTime>
        <MsgType><![CDATA[text]]></MsgType>
        <Content><![CDATA[/总结]]></Content>
        <MsgId>123456</MsgId>
        </xml>"""
    )
    assert message.user_id == "lmk"
    assert message.content == "/总结"
    assert message.message_id == "123456"


def test_encrypted_callback_round_trip():
    token = "callback-token"
    corp_id = "ww-test-corp"
    aes_key = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode().rstrip("=")
    nonce = "987654"
    timestamp = "1788012345"
    plaintext = (
        "<xml><FromUserName>lmk</FromUserName><CreateTime>1788012345</CreateTime>"
        "<MsgType>text</MsgType><Content>hello</Content><MsgId>7</MsgId></xml>"
    )
    crypto = WeChatCrypto(token, aes_key, corp_id)
    encrypted = crypto.encrypt_message(plaintext, nonce, timestamp)
    if isinstance(encrypted, bytes):
        encrypted = encrypted.decode("utf-8")
    signature = ET.fromstring(encrypted).findtext("MsgSignature")

    codec = WeComCallbackCodec(token, aes_key, corp_id)
    decrypted = codec.decrypt_message(encrypted, signature, timestamp, nonce)
    assert decrypted == plaintext


def test_parse_bot_text_message_and_group_reply_target():
    message, reply_target = parse_bot_message(
        {
            "headers": {"req_id": "request-1"},
            "body": {
                "msgid": "bot-message-1",
                "chatid": "group-chat-1",
                "chattype": "group",
                "from": {"userid": "lmk"},
                "msgtype": "text",
                "text": {"content": "/总结"},
            },
        }
    )

    assert message.user_id == "lmk"
    assert message.content == "/总结"
    assert message.message_id == "bot-message-1"
    assert reply_target == "group-chat-1"


class FakeBotSdk:
    def __init__(self):
        self.handlers = {}
        self.sent = []
        self.connected = False

    def on(self, event, handler):
        self.handlers[event] = handler

    async def connect(self):
        self.connected = True
        self.handlers["authenticated"]()

    def disconnect(self):
        self.connected = False

    async def send_message(self, target, body):
        self.sent.append((target, body))

    async def reply_stream(self, frame, stream_id, content, finish=False, **kwargs):
        self.sent.append(("stream", stream_id, content, finish))

    async def reply_welcome(self, frame, body):
        self.sent.append(("welcome", body))


def test_bot_client_receives_and_replies_over_sdk(mock_settings):
    import asyncio
    from dataclasses import replace

    async def scenario():
        sdk = FakeBotSdk()
        settings = replace(
            mock_settings,
            mock_wecom=False,
            wecom_transport="bot",
            bot_id="bot-id",
            bot_secret="bot-secret",
            allowed_users=frozenset({"*"}),
        )
        client = WeComBotClient(settings, sdk_client=sdk)
        received = []
        client.set_message_handler(
            lambda user_id, content, message_id: received.append(
                (user_id, content, message_id)
            )
            is None
        )

        await client.start()
        await sdk.handlers["message.text"](
            {
                "body": {
                    "msgid": "message-2",
                    "chatid": "chat-2",
                    "from": {"userid": "lmk"},
                    "msgtype": "text",
                    "text": {"content": "/帮助"},
                }
            }
        )
        await client.send_progress("lmk", "开始执行分析")
        await client.send_progress("lmk", "正在读取工作档案")
        await client.send_text("lmk", "帮助内容")
        await client.close()

        assert received == [("lmk", "/帮助", "message-2")]
        assert sdk.sent[-1][0] == "stream"
        assert sdk.sent[-1][2:] == ("帮助内容", True)
        assert sdk.sent[-2][0] == "stream"
        assert "任务进度" in sdk.sent[-2][2]
        assert sdk.connected is False

    asyncio.run(scenario())
