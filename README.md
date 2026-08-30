# LMK 企业微信 Codex 网关

企业微信智能机器人只负责收发消息；本机 Codex App Server 负责分析；`/mnt/e/Project/LMK` 中的工作档案负责长期记忆。个人创建的未认证企业推荐使用智能机器人长连接，不需要域名、公网 IP 或备案。

## 功能

- 企业微信加密回调验签和 AES 解密；
- 通过官方企业微信 API 主动发送进度与最终答复；
- 每个企业微信用户绑定一个持久 Codex 线程；
- SQLite 保存线程映射、状态和消息审计记录；
- 重复回调去重、用户白名单、长消息安全分片；
- Codex 固定在 LMK 工作目录和 `workspace-write` 沙箱；
- 支持 `/帮助`、`/状态`、`/继续`、`/新建`、`/总结`、`/任务`、`/记录`、`/取消`；
- 无企业微信凭据时可以使用全本地模拟模式。
- 支持企业微信官方智能机器人 SDK 的 WebSocket 长连接、认证、心跳和断线重连。

## 目录与数据

- 应用代码：`wecom-codex-gateway/app/`
- 配置：`.env`，不会提交到 Git；
- 状态数据库：`data/gateway.db`，不会提交到 Git；
- 长期工作档案：`../工作档案/`；
- Codex 项目规则：`../AGENTS.md`。

## 一、本地模拟测试

首次安装：

```bash
cd /mnt/e/Project/LMK/wecom-codex-gateway
python3 -m venv .venv
.venv/bin/python -m ensurepip --upgrade
.venv/bin/python -m pip install -r requirements-dev.txt
```

启动全模拟服务：

```bash
./scripts/start-mock.sh
```

使用真实 Codex、但仍模拟企业微信收发：

```bash
./scripts/start-local-codex.sh
```

该模式会使用当前 Linux 用户的 Codex 登录并创建真实持久线程，但只监听 `127.0.0.1`，不会连接企业微信。启动脚本会优先使用 `PATH` 中的 Codex CLI；如果 Codex 仅作为 VS Code/Cursor 插件安装，则会自动查找插件自带的 Linux CLI。长任务默认每 8 秒发送一次阶段状态。

另开终端发送消息：

```bash
.venv/bin/python scripts/simulate.py '/总结'
.venv/bin/python scripts/simulate.py '/帮助'
```

模拟客户端默认等待最多 180 秒并只显示本次消息产生的回复；长任务可用 `--wait 600` 延长等待时间。

健康检查：

```bash
curl http://127.0.0.1:8787/healthz
curl http://127.0.0.1:8787/readyz
```

## 二、确认本机 Codex 登录

网关复用当前机器已经配置的 Codex 登录，不在 `.env` 中保存 OpenAI 密钥。

运行网关的 Linux 用户必须与完成 Codex 登录的用户一致，并且该用户需要能够写入自己的 `~/.codex` 状态目录。使用 systemd 时不要把 `HOME` 指向只读目录。

```bash
codex --version
codex app-server --help
```

先在 `/mnt/e/Project/LMK` 中正常运行一次 Codex，确保它能读取 `AGENTS.md` 和 `工作档案/`。如果后台服务找不到 `codex`，把 `.env` 中的 `CODEX_BIN` 改成 `command -v codex` 输出的绝对路径。

可以运行只读连通性测试：

```bash
.venv/bin/python scripts/smoke_codex.py
```

成功时输出 `GATEWAY_OK` 和一个测试线程 ID。

## 三、个人企业微信：智能机器人长连接（推荐）

在企业微信手机端创建智能机器人：

1. 选择“API 模式”；
2. 在“API 配置”中选择“使用长连接”；
3. 保存后取得 `BotID` 和长连接专用 `Secret`；
4. 将机器人可见范围暂时限制为你自己。

不要把 `Secret` 发到聊天、截图或提交到 Git。首次配置运行：

```bash
cd /mnt/e/Project/LMK/wecom-codex-gateway
.venv/bin/python -m pip install -r requirements-dev.txt
chmod +x scripts/*.sh scripts/*.py
./scripts/setup-bot.sh
./scripts/start-bot.sh
```

`setup-bot.sh` 会分别询问 `BotID` 和 `Secret`，输入 Secret 时终端不会显示字符。配置保存在 Linux 用户目录 `~/.config/lmk-wecom-gateway/bot.env`，目录权限为 `700`、文件权限为 `600`，不会放到 `/mnt/e` 的 Windows 挂载盘或 Git 项目中。

启动成功时日志包含：

```text
Enterprise WeChat bot long connection authenticated
Application startup complete.
```

然后在企业微信中打开机器人，发送：

```text
/帮助
/总结
```

长任务会先立即回复“已收到”，随后按阶段发送进度；默认约每 8 秒发送一次状态，即使 Codex 正在启动或读取档案也不会无提示等待。进度只包含公开的计划和阶段摘要，不包含模型隐藏思维链、原始命令输出或密钥。

每个智能机器人只能保持一个有效长连接。不要在多个终端同时运行 `start-bot.sh`，新连接会让旧连接断开。官方协议说明：[智能机器人长连接](https://developer.work.weixin.qq.com/document/path/101463)。

初始配置使用 `WECOM_ALLOWED_USERS=*`，仅适用于机器人可见范围只有你自己的情况。以后扩大可见范围前，应将 `~/.config/lmk-wecom-gateway/bot.env` 中该值改为允许使用者的企业微信 UserID，多个值用英文逗号分隔。

## 四、已认证企业：创建自建应用回调（可选）

1. 登录企业微信管理后台。
2. 打开“应用管理”，选择“自建”，创建应用，例如“LMK 工作助手”。
3. 将你自己的企业微信账号加入应用可见范围。
4. 记录应用页面中的 `AgentId` 和 `Secret`。
5. 在“我的企业”页面记录企业 `CorpID`。
6. 在应用的“接收消息”或“API 接收消息”配置中准备设置回调 URL、Token 和 EncodingAESKey。
7. 在应用的“企业可信 IP”中加入运行网关服务器的公网出口 IP，否则主动发送消息可能被企业微信拒绝。

不同企业微信管理后台版本的菜单文字可能略有差异，但需要取得的五项值固定为：`CorpID`、`AgentID`、应用 `Secret`、回调 `Token`、43 位 `EncodingAESKey`。

## 五、填写回调模式生产配置

```bash
cd /mnt/e/Project/LMK/wecom-codex-gateway
cp .env.example .env
chmod 600 .env
```

编辑 `.env`：

```dotenv
WECOM_CORP_ID=ww...
WECOM_AGENT_ID=1000002
WECOM_APP_SECRET=...
WECOM_CALLBACK_TOKEN=...
WECOM_CALLBACK_AES_KEY=...
WECOM_ALLOWED_USERS=你的企业微信UserID
WECOM_TRANSPORT=callback

CODEX_BIN=/absolute/path/to/codex
CODEX_CWD=/mnt/e/Project/LMK
MOCK_WECOM=false
MOCK_CODEX=false
DEV_API_TOKEN=至少16位且随机的本地开发令牌
```

`WECOM_ALLOWED_USERS` 填企业微信通讯录中的账号/UserID，不是显示姓名。多个账号使用英文逗号分隔。

## 六、为回调模式提供稳定的 HTTPS 地址

企业微信服务器必须能够访问回调地址：

```text
https://你的固定域名/wecom/callback
```

不要将 Codex App Server 暴露到公网。只公开本 FastAPI 网关的 HTTPS 回调。推荐使用以下任一方式：

- 有公网 Linux 服务器：使用 Caddy/Nginx 终止 HTTPS，再反向代理到 `127.0.0.1:8787`；
- 本机位于 NAT 后：使用固定域名的 Cloudflare Named Tunnel；不要使用每次重启都会变化的临时 URL；
- 公司已有网关：由公司 HTTPS 网关转发该路径。

仓库提供 `deploy/Caddyfile.example`。Caddy 仅需：

```text
your-domain.example.com {
    reverse_proxy 127.0.0.1:8787
}
```

## 七、启动回调模式生产服务

```bash
./scripts/start.sh
```

确认：

```bash
curl http://127.0.0.1:8787/readyz
```

返回 `{"status":"ready"}` 后，在企业微信管理后台填写：

- URL：`https://你的固定域名/wecom/callback`
- Token：与 `.env` 的 `WECOM_CALLBACK_TOKEN` 完全一致；
- EncodingAESKey：与 `.env` 的 `WECOM_CALLBACK_AES_KEY` 完全一致。

点击保存时，企业微信会访问 GET 回调进行验签；保存成功说明公网、域名、Token 和 AESKey 均正确。

## 八、在企业微信中使用

打开自建应用，首先发送：

```text
/帮助
```

常见流程：

```text
/总结
/状态
/记录 主管今天说后续重点调整到Kimi K3仿真，周五前给出第一版误差分析
结合刚才的记录，帮我安排下周工作并起草给主管的确认消息
```

网关会先回复“已收到”，长任务每隔一段时间发送公开进度，最后发送结果。它不会发送模型内部思维过程。

## 安全边界

- App Server 使用 stdio，只在本机运行；
- Codex 固定在 `CODEX_CWD`，默认只允许工作区写入；
- 网关禁止交互式提权，无法通过微信批准沙箱逃逸；
- 未列入 `WECOM_ALLOWED_USERS` 的账号会被拒绝；
- `.env`、SQLite 数据库和企业微信聊天内容不得提交到共享仓库；
- 不要在微信中发送账号密码、API 密钥、客户数据或其他公司敏感信息；
- 部署前确认公司是否允许工作材料发送到所使用的模型服务。

## 故障定位

- 企业微信保存回调失败：检查公网 HTTPS、URL 路径、Token、AESKey 和服务器时间。
- 能接收但不能回复：检查应用 Secret、AgentID、应用可见范围和企业可信 IP。
- `/readyz` 返回 503：查看启动日志，通常是 `.env` 缺项、Codex 未登录或路径错误。
- 每次都创建新线程：检查 `data/gateway.db` 是否可写，旧线程是否仍存在于 Codex 会话存储。
- Codex 处理超时：提高 `CODEX_TIMEOUT_SECONDS`，同时检查网络和账户限额。

## 自动化测试

```bash
.venv/bin/python -m pytest -q
```
