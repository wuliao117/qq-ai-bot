#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QQ 群聊 AI 助手（OneBot v11 反向 WebSocket）
------------------------------------------------
架构:  NapCatQQ  ──反向WS──▶  本程序  ──HTTP──▶  大模型 (OpenAI 兼容接口)

特性:
  * 群里 @ 机器人 才回答，也可以配置前缀触发 / 回复自己触发
  * 带"群内最近聊天"作为背景上下文，回答更贴题
  * 每个群独立上下文，可 @ 机器人 /重置 清空
  * 群白名单 / 黑名单、每分钟限流、并发上限、长回复自动分段
  * 断线自动恢复，配置改完重启即可

运行: python main.py             （读取同目录 config.json）
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import sys
import time
import uuid
from collections import deque
from pathlib import Path

import httpx
from aiohttp import WSMsgType, web

BASE = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.environ.get("BOT_CONFIG") or (BASE / "config.json"))

# ---------------------------------------------------------------- 日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(BASE / "bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("qqbot")


# ---------------------------------------------------------------- 配置
def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise SystemExit(f"找不到配置文件: {CONFIG_PATH}")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    # 允许环境变量覆盖 API Key，避免密钥写进文件
    env_key = os.environ.get("LLM_API_KEY")
    if env_key:
        cfg["llm"]["api_key"] = env_key
    return cfg


# ---------------------------------------------------------------- 文本工具
def seg_text(segments: list[dict], self_id: str = "") -> str:
    """把消息段数组拼成纯文本。@自己的那一节直接丢掉，避免把 @QQ号 灌进模型。"""
    out = []
    for s in segments or []:
        t = s.get("type")
        d = s.get("data") or {}
        if t == "text":
            out.append(d.get("text", ""))
        elif t == "at":
            qq = str(d.get("qq", ""))
            if qq == self_id or qq == "all":
                continue
            out.append(f"@{d.get('name') or qq}")
        elif t == "image":
            out.append("[图片]")
        elif t == "face":
            out.append("[表情]")
        elif t == "record":
            out.append("[语音]")
        elif t == "video":
            out.append("[视频]")
        elif t == "file":
            out.append("[文件]")
        elif t == "reply":
            continue
    return "".join(out).strip()


def is_at_me(segments: list[dict], self_id: str) -> bool:
    for s in segments or []:
        if s.get("type") == "at" and str((s.get("data") or {}).get("qq")) == str(self_id):
            return True
    return False


def reply_to_bot(segments: list[dict], bot_msg_ids: deque) -> bool:
    """消息是否为"回复机器人某条消息"。"""
    for s in segments or []:
        if s.get("type") == "reply":
            rid = str((s.get("data") or {}).get("id"))
            if rid and rid in bot_msg_ids:
                return True
    return False


SENT_SPLIT = re.compile(r"(?<=[。！？!?\n])")


def split_reply(text: str, limit: int) -> list[str]:
    """按句子切分超长回复，避免单条消息过长。"""
    text = text.strip()
    if len(text) <= limit:
        return [text]
    chunks, cur = [], ""
    for piece in SENT_SPLIT.split(text):
        if not piece:
            continue
        if len(cur) + len(piece) > limit and cur:
            chunks.append(cur)
            cur = piece
        else:
            cur += piece
    if cur:
        chunks.append(cur)
    # 极端情况（没有标点）硬切
    final = []
    for c in chunks:
        while len(c) > limit:
            final.append(c[:limit])
            c = c[limit:]
        if c:
            final.append(c)
    return final[:6]


# ---------------------------------------------------------------- 机器人
class Bot:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.ws: web.WebSocketResponse | None = None
        self.self_id: str = ""
        self.pending: dict[str, asyncio.Future] = {}
        self.history: dict[str, deque] = {}       # 群/私聊 最近聊天缓冲
        self.bot_msg_ids: dict[str, deque] = {}   # 机器人自己发过的消息 id
        self.locks: dict[str, asyncio.Lock] = {}
        self.stamps: dict[str, deque] = {}        # 限流时间戳
        self.sem = asyncio.Semaphore(int(cfg["behavior"].get("max_concurrency", 3)))
        self.http = httpx.AsyncClient(
            timeout=httpx.Timeout(cfg["llm"].get("timeout", 60), connect=15),
            limits=httpx.Limits(max_connections=20),
        )
        self.started = time.time()

    # ------------------------------------------------ OneBot API
    async def call(self, action: str, params: dict, wait: float = 20.0):
        if self.ws is None or self.ws.closed:
            log.warning("WS 未连接，无法调用 %s", action)
            return None
        echo = uuid.uuid4().hex[:12]
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending[echo] = fut
        try:
            await self.ws.send_str(json.dumps(
                {"action": action, "params": params, "echo": echo}, ensure_ascii=False))
            return await asyncio.wait_for(fut, timeout=wait)
        except asyncio.TimeoutError:
            log.warning("调用 %s 超时", action)
            return None
        except Exception as e:                      # noqa: BLE001
            log.warning("调用 %s 异常: %s", action, e)
            return None
        finally:
            self.pending.pop(echo, None)

    async def send_text(self, kind: str, target: int, text: str,
                        reply_id: str | None = None) -> str | None:
        """kind: group | private，返回发出的消息 id。"""
        segs: list[dict] = []
        if reply_id and self.cfg["behavior"].get("reply_with_quote", True):
            segs.append({"type": "reply", "data": {"id": reply_id}})
        segs.append({"type": "text", "data": {"text": text}})
        action = "send_group_msg" if kind == "group" else "send_private_msg"
        key = "group_id" if kind == "group" else "user_id"
        res = await self.call(action, {key: target, "message": segs})
        if res and res.get("status") == "ok" and res.get("data"):
            return str(res["data"].get("message_id", ""))
        log.warning("发送失败: %s", res)
        return None

    # ------------------------------------------------ 事件入口
    async def on_event(self, data: dict):
        # 1) API 响应
        if "echo" in data and "post_type" not in data:
            fut = self.pending.get(str(data["echo"]))
            if fut and not fut.done():
                fut.set_result(data)
            return

        ptype = data.get("post_type")
        if ptype == "meta_event":
            if data.get("meta_event_type") == "lifecycle":
                self_id = str(data.get("self_id", ""))
                if self_id and self_id != self.self_id:
                    self.self_id = self_id
                    log.info("已连接 QQ 账号: %s", self_id)
            return
        if ptype != "message":
            return

        self_id = str(data.get("self_id", ""))
        if self_id:
            self.self_id = self_id
        if data.get("message_type") == "group":
            await self.on_group_msg(data)
        elif data.get("message_type") == "private":
            await self.on_private_msg(data)

    # ------------------------------------------------ 群消息
    async def on_group_msg(self, data: dict):
        segs = data.get("message") or []
        gid = str(data.get("group_id"))
        uid = str(data.get("user_id"))
        msg_id = str(data.get("message_id", ""))
        sender = (data.get("sender") or {}).get("card") or \
                 (data.get("sender") or {}).get("nickname") or uid

        # 忽略自己发的消息
        if uid == self.self_id:
            return

        text = seg_text(segs, self.self_id)
        if text:
            self._remember(gid, f"{sender}: {text}", msg_id)

        # 白/黑名单
        wl = [str(x) for x in self.cfg["groups"].get("whitelist", [])]
        bl = [str(x) for x in self.cfg["groups"].get("blacklist", [])]
        if gid in bl or (wl and gid not in wl):
            return

        # 触发判定
        trig = self.cfg["trigger"]
        at_me = is_at_me(segs, self.self_id)
        reply_me = reply_to_bot(segs, self.bot_msg_ids.get(gid, deque()))
        hit_prefix = None
        for p in trig.get("prefixes", []):
            if text.startswith(p):
                hit_prefix = p
                break

        if trig.get("require_at", True):
            if not (at_me or (reply_me and trig.get("reply_to_bot", True)) or hit_prefix):
                return
        elif not (at_me or hit_prefix):
            return

        question = text
        if hit_prefix:
            question = text[len(hit_prefix):].strip()
        question = re.sub(r"^@\S+\s*", "", question)
        question = question.replace(f"@{self.self_id}", "").strip()
        if not question:
            await self.send_text("group", int(gid), "在的，有什么可以帮你？", msg_id)
            return

        # 内置指令
        if question in ("/reset", "/重置", "重置", "/clear"):
            self.history.pop(gid, None)
            await self.send_text("group", int(gid), "这个群的上下文已经清空了。", msg_id)
            return
        if question in ("/ping",):
            up = int(time.time() - self.started)
            await self.send_text("group", int(gid), f"pong · 在线 {up//3600}h{up%3600//60}m", msg_id)
            return
        if question in ("/help", "/帮助"):
            await self.send_text(
                "group", int(gid),
                "直接 @ 我说话就行。指令：/reset 清空上下文，/ping 看状态。", msg_id)
            return

        await self.answer("group", int(gid), gid, question,
                          msg_id=msg_id, nick=sender, gid=gid)

    # ------------------------------------------------ 私聊
    async def on_private_msg(self, data: dict):
        if not self.cfg["trigger"].get("allow_private", True):
            return
        uid = str(data.get("user_id"))
        msg_id = str(data.get("message_id", ""))
        if uid == self.self_id:
            return
        text = seg_text(data.get("message") or [], self.self_id)
        if not text:
            return
        self._remember(f"p{uid}", f"对方: {text}", msg_id)
        await self.answer("private", int(uid), f"p{uid}", text, msg_id=msg_id, gid=None)

    # ------------------------------------------------ 上下文
    def _remember(self, key: str, line: str, msg_id: str):
        buf = self.history.setdefault(
            key, deque(maxlen=int(self.cfg["context"].get("ambient_size", 20))))
        buf.append(line[:400])
        if msg_id:
            ids = self.bot_msg_ids.setdefault(key, deque(maxlen=200))
            ids.append(msg_id)

    def _build_messages(self, key: str, question: str) -> list[dict]:
        cfg = self.cfg["llm"]
        msgs = [{"role": "system", "content": cfg.get("system_prompt", "你是一个 helpful assistant。")}]
        ambient = list(self.history.get(key, []))
        if ambient and self.cfg["context"].get("ambient", True):
            log_text = "\n".join(ambient[-int(self.cfg["context"].get("ambient_size", 20)):])
            if len(log_text) > int(self.cfg["context"].get("max_chars", 4000)):
                log_text = log_text[-int(self.cfg["context"].get("max_chars", 4000)):]
            msgs.append({"role": "user", "content":
                         "（以下是群里最近的聊天记录，仅供你了解上下文，不要逐条回应：）\n" + log_text})
            msgs.append({"role": "assistant", "content": "收到，我已了解上下文。"})
        msgs.append({"role": "user", "content": question})
        return msgs

    # ------------------------------------------------ 调模型
    async def ask_llm(self, messages: list[dict]) -> str:
        cfg = self.cfg["llm"]
        key = (cfg.get("api_key") or "").strip()
        if not key:
            return (f"【配置缺失】还没有填 API Key，请在 {CONFIG_PATH.name} 的 "
                    f"llm.api_key 里填上，或设置环境变量 LLM_API_KEY 后重启。")
        base = (cfg.get("base_url") or "https://api.deepseek.com/v1").rstrip("/")
        url = base + "/chat/completions"
        payload = {
            "model": cfg.get("model", "deepseek-chat"),
            "messages": messages,
            "temperature": float(cfg.get("temperature", 0.7)),
            "max_tokens": int(cfg.get("max_tokens", 800)),
            "stream": False,
        }
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        last_err = ""
        for attempt in range(3):
            try:
                r = await self.http.post(url, json=payload, headers=headers)
                if r.status_code == 200:
                    j = r.json()
                    ans = (j["choices"][0]["message"]["content"] or "").strip()
                    # 思考型模型（如 deepseek-r1）会把推理过程放在  thinking 里，群里不需要
                    ans = re.sub(r"<think(?:ing)?>.*?</think(?:ing)?>", "", ans, flags=re.S).strip()
                    ans = re.sub(r"</?think(?:ing)?>", "", ans).strip()
                    return ans or "（模型返回了空内容）"
                last_err = f"HTTP {r.status_code}: {r.text[:200]}"
                if r.status_code in (429, 500, 502, 503, 504):
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                break
            except Exception as e:                  # noqa: BLE001
                last_err = f"{type(e).__name__}: {e}"
                await asyncio.sleep(1.5 * (attempt + 1))
        log.error("模型调用失败: %s", last_err)
        return "模型暂时不可用，稍后再试试。"

    # ------------------------------------------------ 生成并回复
    async def answer(self, kind: str, target: int, key: str, question: str,
                     msg_id: str | None = None, nick: str = "", gid: str | None = None):
        # 限流
        rpm = int(self.cfg["behavior"].get("max_replies_per_minute", 6))
        stamps = self.stamps.setdefault(key, deque())
        now = time.time()
        while stamps and now - stamps[0] > 60:
            stamps.popleft()
        if len(stamps) >= rpm:
            log.info("[%s] 触发限流，忽略本条", key)
            return

        lock = self.locks.setdefault(key, asyncio.Lock())
        if lock.locked():
            log.info("[%s] 上一条还在处理，忽略并发消息", key)
            return

        async with lock:
            stamps.append(time.time())
            q = question
            if nick:
                q = f"（{nick} 在群里 @你 说：）{question}"
            messages = self._build_messages(key, q)
            t0 = time.time()
            async with self.sem:
                ans = await self.ask_llm(messages)
            log.info("[%s] %s -> %d 字, %.1fs", key, question[:30], len(ans), time.time() - t0)

            limit = int(self.cfg["behavior"].get("max_reply_chars", 900))
            for piece in split_reply(ans, limit):
                mid = await self.send_text(kind, target, piece, msg_id)
                if mid:
                    self.bot_msg_ids.setdefault(key, deque(maxlen=200)).append(mid)
                await asyncio.sleep(0.4)


# ---------------------------------------------------------------- Web 层
def make_app(bot: Bot) -> web.Application:
    app = web.Application()
    path = bot.cfg["onebot"].get("path", "/onebot/v11/ws")
    token = (bot.cfg["onebot"].get("access_token") or "").strip()

    async def ws_handler(request: web.Request) -> web.WebSocketResponse:
        if token:
            auth = request.headers.get("Authorization", "")
            got = auth[7:] if auth.startswith("Bearer ") else request.query.get("access_token", "")
            if got != token:
                log.warning("鉴权失败，拒绝连接")
                return web.Response(status=401, text="unauthorized")
        ws = web.WebSocketResponse(heartbeat=30, max_msg_size=0)
        await ws.prepare(request)
        bot.ws = ws
        log.info("NapCat 已连接 (%s)", request.remote)
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except json.JSONDecodeError:
                        continue
                    asyncio.create_task(bot.on_event(data))
                elif msg.type == WSMsgType.ERROR:
                    log.warning("WS 错误: %s", ws.exception())
        finally:
            if bot.ws is ws:
                bot.ws = None
            log.warning("NapCat 连接断开")
        return ws

    app.router.add_get(path, ws_handler)

    async def health(_req):
        return web.json_response({
            "ok": True, "self_id": bot.self_id,
            "connected": bot.ws is not None and not bot.ws.closed,
            "uptime_s": int(time.time() - bot.started),
        })

    app.router.add_get("/health", health)
    return app


async def main():
    cfg = load_config()
    bot = Bot(cfg)
    host = cfg["onebot"].get("host", "127.0.0.1")
    port = int(cfg["onebot"].get("port", 3002))
    app = make_app(bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info("机器人已启动: ws://%s:%d%s  (模型: %s)", host, port,
             cfg["onebot"].get("path"), cfg["llm"].get("model"))
    log.info("等待 NapCat 反向 WebSocket 连接…")
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        await bot.http.aclose()


if __name__ == "__main__":
    try:
        if os.name == "nt":
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n已停止。")
