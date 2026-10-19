#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实机联调：以「假 NapCat」身份连到正在运行的机器人，发一条群 @ 消息，
看机器人是否真的调用了模型并把回复发出来。不需要 QQ 登录。

运行: python live_test.py  [提问内容]
"""
import asyncio, json, sys, time

import aiohttp

WS = "http://127.0.0.1:3002/onebot/v11/ws"


async def main(question: str) -> int:
    self_id, gid, uid = 10001, 88880001, 20002
    async with aiohttp.ClientSession() as cs:
        try:
            ws = await cs.ws_connect(WS)
        except Exception as e:
            print(f"✗ 连不上机器人 ({WS}): {e}")
            return 1
        print("✓ 已连上机器人")

        await ws.send_str(json.dumps({
            "post_type": "meta_event", "meta_event_type": "lifecycle",
            "sub_type": "connect", "self_id": self_id, "time": int(time.time())}))
        await asyncio.sleep(0.3)

        # 先造几条"群聊上下文"，验证背景上下文是否生效
        for i, line in enumerate(["今天下午开会要讲什么？", "就讲新版本的两个改进点吧"]):
            await ws.send_str(json.dumps({
                "post_type": "message", "message_type": "group", "self_id": self_id,
                "group_id": gid, "user_id": 20003 + i, "message_id": 100 + i,
                "sender": {"nickname": f"群友{i+1}"},
                "message": [{"type": "text", "data": {"text": line}}]}))
        await asyncio.sleep(0.3)

        await ws.send_str(json.dumps({
            "post_type": "message", "message_type": "group", "self_id": self_id,
            "group_id": gid, "user_id": uid, "message_id": 500,
            "sender": {"nickname": "测试员", "card": "测试员"},
            "message": [{"type": "at", "data": {"qq": str(self_id), "name": "小助手"}},
                        {"type": "text", "data": {"text": " " + question}}]}))
        print(f"→ 已发送群 @ 消息: {question}")
        print("  等待模型回复（本地模型可能需要几十秒）…")

        deadline = time.time() + 240
        while time.time() < deadline:
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=max(0.5, deadline - time.time()))
            except asyncio.TimeoutError:
                break
            if msg.type != aiohttp.WSMsgType.TEXT:
                break
            data = json.loads(msg.data)
            if data.get("action") in ("send_group_msg", "send_private_msg"):
                segs = data["params"].get("message", [])
                text = "".join(s.get("data", {}).get("text", "")
                               for s in segs if s.get("type") == "text")
                print("\n===== 机器人回复 =====")
                print(text)
                print("======================")
                await ws.send_str(json.dumps({
                    "status": "ok", "retcode": 0, "echo": data["echo"],
                    "data": {"message_id": 777}}))
                await ws.close()
                return 0 if "配置缺失" not in text and "暂时不可用" not in text else 1

        print("✗ 超时，没有收到回复")
        await ws.close()
        return 1


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "刚才他们说的会要讲什么？一句话说。"
    sys.exit(asyncio.run(main(q)))
