# -*- coding: utf-8 -*-
"""LCU 端点探测脚本（跑在用户机器上，需要 LoL 客户端开着）。

用途：实测各 LCU 端点的真实返回结构，为 lol_companion v2 校准字段。
用法：
  .venv\\Scripts\\python.exe tools\\lcu_probe.py

输出每个端点的 HTTP 结果与 JSON 键摘要；结算界面（PreEndOfGame/EndOfGame）
阶段跑一遍最有价值（可校准 eog-stats-block 的 players/stats 字段）。
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from DyberPet.plugins.lol_companion import lcu_client  # noqa: E402

PROBE_ENDPOINTS = [
    ("GET", "/lol-gameflow/v1/gameflow-phase", None),
    ("GET", "/lol-gameflow/v1/session", None),
    ("GET", "/lol-summoner/v1/current-summoner", None),
    ("GET", "/lol-matchmaking/v1/search", None),
    ("GET", "/lol-end-of-game/v1/eog-stats-block", None),
]


def summarize(obj, depth=0):
    """递归打印 JSON 结构摘要（list 只展开首元素，dict 只打键）。"""
    pad = "  " * depth
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                print(f"{pad}{k}:")
                if isinstance(v, list):
                    print(f"{pad}  [0/len={len(v)}]")
                    if v:
                        summarize(v[0], depth + 2)
                else:
                    summarize(v, depth + 1)
            else:
                print(f"{pad}{k} = {v!r}")
    elif isinstance(obj, list):
        print(f"{pad}list(len={len(obj)})")
        if obj:
            summarize(obj[0], depth + 1)
    else:
        print(f"{pad}{obj!r}")


def main():
    auth = lcu_client.get_auth(force=True)
    if not auth:
        print("❌ 未找到 LeagueClientUx.exe 进程——请先打开 LoL 客户端再跑本脚本。")
        return 1
    print(f"✅ LCU 认证成功: port={auth[0]}, token={auth[1][:6]}...")

    for method, path, body in PROBE_ENDPOINTS:
        print("\n" + "=" * 60)
        print(f"{method} {path}")
        print("=" * 60)
        data = lcu_client.lcu_get(path)
        if data is None:
            print("(无数据——当前客户端阶段可能不适用，或端点不存在)")
            continue
        if path.endswith("eog-stats-block") and isinstance(data, dict):
            # 结算块重点展开 players[0]
            players = data.get("players")
            print(f"gameId={data.get('gameId')}  gameMode={data.get('gameMode')}  "
                  f"gameDuration={data.get('gameDuration')}")
            print(f"players: len={len(players) if players else 0}")
            if players:
                summarize(players[0])
        else:
            summarize(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
