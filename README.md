# QQ 群聊 AI 助手

> 在群里 @ 一下，就用大模型回答 —— 还会自动带上群里最近的聊天记录当背景，不会答非所问。

基于 [NapCatQQ](https://github.com/NapNeko/NapCatQQ)（OneBot v11 协议端）的 QQ 群机器人。
对接任意 **OpenAI 兼容** 的大模型接口：DeepSeek、通义千问、Kimi、硅基流动、OpenAI，
或者本地 **Ollama**（完全离线、不花钱）。

```
QQ 客户端(无头) ──注入──▶ NapCatQQ ──反向 WebSocket──▶ 本机器人 ──HTTP──▶ 大模型
                          :6099 WebUI                  :3002              Ollama / 云端 API
```

## 特性

- **@ 即答**：只在被 @ 时说话，不刷屏；也可以配前缀触发、回复触发
- **群上下文感知**：把群里最近 N 条聊天当背景喂给模型，回答贴合当前话题
- **每个群独立上下文**：`@机器人 /reset` 单独清空，互不干扰
- **防刷屏**：每群每分钟回复上限、并发上限、超长回复自动分段
- **白/黑名单**：只想在某几个群工作就填 whitelist
- **引用回复**：默认带着原消息引用，一眼看出在回谁
- **不绑定厂商**：换模型只改 3 行配置
- **思考链剥离**：用 deepseek-r1 这类型号时，自动把 ` thinking` 推理过程干掉，只发结论
- **零依赖膨胀**：只用 `aiohttp` + `httpx`，没上 nb2 那套框架，代码 400 行看得完

## 快速开始

### 0. 准备

- Windows 10/11
- Python 3.10+
- 已安装 [QQ NT 客户端](https://im.qq.com/pcqq)（NapCat 靠它跑，不会弹出窗口）
- 一个 **小号**（见下方风险提示）

### 1. 装 NapCat

下载 [NapCat.Shell.zip](https://github.com/NapNeko/NapCatQQ/releases/latest)，
解压到项目根目录并命名为 `NapCat.Shell`。目录结构应该是：

```
你的项目目录\
├─ bot\                 # 本仓库的代码
├─ scripts\             # 本仓库的脚本
└─ NapCat.Shell\        # NapCat 本体（不进版本库）
   ├─ NapCatWinBootMain.exe
   ├─ loadNapCat.js
   └─ config\
```

### 2. 配机器人

```bash
pip install -r requirements.txt
cd bot
cp config.example.json config.json     # Windows: copy config.example.json config.json
```

编辑 `bot/config.json`，填模型：

```json
"llm": {
  "base_url": "https://api.deepseek.com/v1",
  "api_key":  "sk-你的key",
  "model":    "deepseek-chat"
}
```

用本地 Ollama（不需要 key）：

```json
"base_url": "http://127.0.0.1:11434/v1",
"api_key":  "ollama",
"model":    "deepseek-r1:7b"
```

### 3. 配 NapCat 反向 WebSocket

在 `NapCat.Shell/config/` 下新建 `onebot11.json`（登录后也可以建 `onebot11_<QQ号>.json`）：

```json
{
  "network": {
    "httpServers": [],
    "httpSseServers": [],
    "httpClients": [],
    "websocketServers": [],
    "websocketClients": [
      {
        "name": "ai-bot",
        "enable": true,
        "url": "ws://127.0.0.1:3002/onebot/v11/ws",
        "messagePostFormat": "array",
        "reportSelfMessage": false,
        "reconnectInterval": 5000,
        "token": "",
        "debug": false,
        "heartInterval": 30000
      }
    ],
    "plugins": []
  },
  "musicSignUrl": "",
  "enableLocalFile2Url": false,
  "parseMultMsg": false
}
```

### 4. 启动

```bat
scripts\start_all.cmd
```

会开两个窗口（机器人 + NapCat）。首次使用打开
`http://127.0.0.1:6099/webui` 用手机 QQ 扫码登录，token 在
`NapCat.Shell\config\webui.json` 里。之后无需重复扫码。

然后去群里 @ 机器人 说话即可。

> 手动启动也可以：`cd bot && python main.py`，另开一个窗口 `scripts\start_napcat.cmd`。
> 想用快速登录跳过扫码：`scripts\start_napcat.cmd 你的QQ号`（需要此前登录过）。

## 配置说明

`bot/config.json`：

| 字段 | 说明 |
| --- | --- |
| `onebot.port` | 机器人监听的端口，要和 NapCat 里填的 url 对上 |
| `onebot.access_token` | 非空时 NapCat 必须带同样 token 才能连入 |
| `trigger.require_at` | `true` 只有 @ 才回答；`false` 则群消息都回（慎用） |
| `trigger.prefixes` | 额外触发前缀，如 `["/ai "]` |
| `trigger.allow_private` | 是否响应私聊 |
| `llm.base_url` | OpenAI 兼容地址，末尾的 `/v1` 要带上 |
| `llm.system_prompt` | 人格设定，随便改 |
| `context.ambient_size` | 带多少条群聊记录当背景（默认 20） |
| `groups.whitelist` | 非空时只在这些群工作 |
| `behavior.max_replies_per_minute` | 每群每分钟最多回几条 |
| `behavior.max_reply_chars` | 单条消息超过就自动分段 |

内置指令（需要 @ 机器人）：`/reset` 清空上下文、`/ping` 看状态、`/help` 帮助。

## 自检与联调

不用 QQ 也能验证代码是通的：

```bash
cd bot
python selftest.py     # 假 NapCat + 假模型接口，验证「收 @ → 调模型 → 发消息」全链路
python live_test.py    # 机器人已在跑时，假装成 NapCat 灌一条群 @ 消息，看真实回复
```

## 踩坑记录

**1. `Cannot find module ... resources\app\loadNapCat.js`**

NapCat 注入后会把 QQ 的 `package.json` 在内存里替换成以 `./loadNapCat.js` 为入口，
而 Node 是相对 **QQ 自己的 `resources\app`** 解析这个相对路径的。所以必须把
`NapCat.Shell\loadNapCat.js` 复制一份到：

```
<QQ安装目录>\versions\<版本号>\resources\app\loadNapCat.js
```

`scripts\start_napcat.cmd` 会自动做这件事。想还原成干净 QQ，删掉那个文件即可。

**2. `.cmd` 文件必须是 CRLF 换行**

用 LF 换行保存的批处理会被 cmd 报 `The syntax of the command is incorrect.`。

**3. `github.com` 的 release 下载在国内常被墙**

`NapCat.Shell.zip` 下不动的话，加个镜像前缀，例如：
`https://ghfast.top/https://github.com/NapNeko/NapCatQQ/releases/download/<tag>/NapCat.Shell.zip`

## 风险提示

- NapCat 是**第三方非官方**客户端，用真实账号登录，**存在被风控/封号的概率**。
  请用小号，或你自己承担风险。
- 默认只监听 `127.0.0.1`，**不要**把 6099 / 3002 暴露到公网。
- 你 @ 机器人 说的内容会发送到你配置的模型服务商，注意隐私。
- 本项目与腾讯、NapCat 官方均无关。

## License

[MIT](LICENSE)。NapCatQQ 遵循其自身许可，本仓库不包含其任何二进制文件。

---

<sub>English: A QQ group chat AI assistant built on NapCatQQ (OneBot v11),
works with any OpenAI-compatible LLM endpoint or local Ollama. Mention the bot in a
group and it answers, using recent group messages as context. MIT licensed.</sub>
