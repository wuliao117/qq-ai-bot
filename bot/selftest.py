#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
离线自检：不需要 QQ / 不需要真实 API Key。
用一个假的 NapCat 连上机器人，再用一个假的大模型接口回话，
验证「收到群 @ -> 触发 -> 调模型 -> 发群消息」整条链路是否通。

运行: python selftest.py
"""
import asyncio, json, os, subprocess, sys, time
from pathlib import Path
import aiohttp
from aiohttp import web

BASE = Path(__file__).resolve().parent
LLM_PORT, BOT_PORT = 8099, 3012

async def llm_stub(request: web.Request):
    body = await request.json()
    last = body["messages"][-1]["content"]
    n = len(body["messages"])
    return web.json_response({
        "choices": [{"message": {"content": f"测试回复（消息数={n}）| 收到：{last[:30]}"}}]
    })

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
    cfg["behavior"]["max_replies_per_minute"] = 99
    tcfg = BASE / "config.selftest.json"
    tcfg.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    logfile = open(BASE / "selftest.bot.log", "w", encoding="utf-8")
    env = dict(os.environ, BOT_CONFIG=str(tcfg), PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen([sys.executable, str(BASE / "main.py")],
                            stdout=logfile, stderr=subprocess.STDOUT, env=env)
    ok = False
    try:
        await asyncio.sleep(2.5)
        async with aiohttp.ClientSession() as cs:
            ws = await cs.ws_connect(f"http://127.0.0.1:{BOT_PORT}/{cfg['onebot']['path'].lstrip('/')}")
            print("✓ 反向 WebSocket 连接成功")

            await ws.send_str(json.dumps({
                "post_type": "meta_event", "meta_event_type": "lifecycle",
                "sub_type": "connect", "self_id": 10001, "time": int(time.time())}))

            # 1) 不带 @ 的消息 -> 应被忽略
            await ws.send_str(json.dumps({
                "post_type": "message", "message_type": "group", "self_id": 10001,
                "group_id": 5550001, "user_id": 20002, "message_id": 1,
                "sender": {"nickname": "路人"},
                "message": [{"type": "text", "data": {"text": "今天天气不错"}}]}))

            # 2) @ 机器人 -> 应回复
            await ws.send_str(json.dumps({
                "post_type": "message", "message_type": "group", "self_id": 10001,
                "group_id": 5550001, "user_id": 20002, "message_id": 2,
                "sender": {"nickname": "小明", "card": "小明"},
                "message": [
                    {"type": "at", "data": {"qq": "10001", "name": "小助手"}},
                    {"type": "text", "data": {"text": " 你好，做个自我介绍"}}]}));

            got, deadline = [], time.time() + 25
            while time.time() < deadline:
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=max(0.2, deadline - time.time()))
                except asyncio.TimeoutError:
                    break
                if msg.type != aiohttp.WSMsgType.TEXT:
                    break
                data = json.loads(msg.data)
                if data.get("action") == "send_group_msg":
                    text = "".join(s.get("data", {}).get("text", "")
                                   for s in data["params"]["message"] if s["type"] == "text")
                    got.append(text)
                    await ws.send_str(json.dumps({
                        "status": "ok", "retcode": 0, "echo": data["echo"],
                        "data": {"message_id": 900 + len(got)}}))
                    break

            if not got:
                print("✗ 没收到机器人的回复")
            elif "测试回复" not in got[0]:
                print(f"✗ 回复内容异常: {got[0]!r}")
            elif "你好，做个自我介绍" not in got[0]:
                print(f"✗ 提问没正确传进模型: {got[0]!r}")
            else:
                print(f"✓ 触发 -> 调模型 -> 发消息 全链路正常")
                print(f"  机器人回复: {got[0]}")
                ok = True
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

    print("结果:", "PASS ✅" if ok else "FAIL ❌")
    return 0 if ok else 1

if __name__ == "__main__":
    if os.name == "nt":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(asyncio.run(main()))
