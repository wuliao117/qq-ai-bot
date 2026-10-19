#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
离线自检：不需要 QQ / 不需要真实 API Key。
用一个假的 NapCat 连上机器人，再用一个假的大模型接口回话，
验证三种触发方式：@触发、关键词触发、以及不该触发时不触发。

运行: python selftest.py
"""
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import aiohttp
from aiohttp import web

BASE = Path(__file__).resolve().parent
LLM_PORT, BOT_PORT = 8099, 3012
SELF_ID, GID, UID = 10001, 5550001, 20002


async def llm_stub(request: web.Request):
    body = await request.json()
    last = body["messages"][-1]["content"]
    n = len(body["messages"])
    return web.json_response({
        "choices": [{"message": {"content": f"测试回复（消息数={n}）| 收到：{last[:30]}"}}]
    })


async def wait_reply(ws, timeout: float):
    """等一条 send_group_msg，返回文本；超时返回 None。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            msg = await asyncio.wait_for(ws.receive(), timeout=max(0.2, deadline - time.time()))
        except asyncio.TimeoutError:
            return None
        if msg.type != aiohttp.WSMsgType.TEXT:
            return None
        data = json.loads(msg.data)
        if data.get("action") == "send_group_msg":
            text = "".join(s.get("data", {}).get("text", "")
                           for s in data["params"]["message"] if s["type"] == "text")
            await ws.send_str(json.dumps({
                "status": "ok", "retcode": 0, "echo": data["echo"],
                "data": {"message_id": 900}}))
            return text
    return None


def group_msg(mid: int, segments: list):
    return json.dumps({
        "post_type": "message", "message_type": "group", "self_id": SELF_ID,
        "group_id": GID, "user_id": UID, "message_id": mid,
        "sender": {"nickname": "小明", "card": "小明"}, "message": segments})


async def main() -> int:
    app = web.Application()
    app.router.add_post("/v1/chat/completions", llm_stub)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", LLM_PORT).start()

    src = BASE / "config.json"
    if not src.exists():
        src = BASE / "config.example.json"
    cfg = json.loads(src.read_text(encoding="utf-8"))
    cfg["onebot"]["port"] = BOT_PORT
    cfg["llm"]["base_url"] = f"http://127.0.0.1:{LLM_PORT}/v1"
    cfg["llm"]["api_key"] = "selftest"
    cfg["trigger"]["keywords"] = ["天气"]          # 自检用关键词
    cfg["trigger"]["all_messages"] = False
    cfg["behavior"]["max_replies_per_minute"] = 99
    tcfg = BASE / "config.selftest.json"
    tcfg.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    logfile = open(BASE / "selftest.bot.log", "w", encoding="utf-8")
    env = dict(os.environ, BOT_CONFIG=str(tcfg), PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen([sys.executable, str(BASE / "main.py")],
                            stdout=logfile, stderr=subprocess.STDOUT, env=env)
    ok = True
    try:
        await asyncio.sleep(2.5)
        async with aiohttp.ClientSession() as cs:
            ws = await cs.ws_connect(f"http://127.0.0.1:{BOT_PORT}/{cfg['onebot']['path'].lstrip('/')}")
            print("✓ 反向 WebSocket 连接成功")
            await ws.send_str(json.dumps({
                "post_type": "meta_event", "meta_event_type": "lifecycle",
                "sub_type": "connect", "self_id": SELF_ID, "time": int(time.time())}))
            await asyncio.sleep(0.3)

            # 用例 1：普通聊天（无 @、无关键词）-> 不该回复
            await ws.send_str(group_msg(1, [{"type": "text", "data": {"text": "今天心情不错"}}]))
            got = await wait_reply(ws, 3)
            if got is None:
                print("✓ [1] 普通消息未被触发（不刷屏）")
            else:
                print(f"✗ [1] 普通消息不该回复，却回了: {got!r}")
                ok = False

            # 用例 2：@ 机器人 -> 应该回复
            await ws.send_str(group_msg(2, [
                {"type": "at", "data": {"qq": str(SELF_ID), "name": "小助手"}},
                {"type": "text", "data": {"text": " 你好，做个自我介绍"}}]))
            got = await wait_reply(ws, 20)
            if got and "测试回复" in got and "你好，做个自我介绍" in got:
                print("✓ [2] @ 触发正常（且 @QQ号 已剥离）")
            else:
                print(f"✗ [2] @ 触发异常: {got!r}")
                ok = False

            await asyncio.sleep(0.5)

            # 用例 3：不含 @，但命中关键词 -> 应该回复
            await ws.send_str(group_msg(3, [{"type": "text", "data": {"text": "今天天气怎么样"}}]))
            got = await wait_reply(ws, 20)
            if got and "测试回复" in got and "天气" in got:
                print("✓ [3] 关键词触发正常（不带 @ 也回复）")
            else:
                print(f"✗ [3] 关键词触发失败: {got!r}")
                ok = False

            await ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        logfile.close()
        await runner.cleanup()
        tcfg.unlink(missing_ok=True)
        (BASE / "selftest.bot.log").unlink(missing_ok=True)

    print("结果:", "PASS ✅" if ok else "FAIL ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    if os.name == "nt":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(asyncio.run(main()))