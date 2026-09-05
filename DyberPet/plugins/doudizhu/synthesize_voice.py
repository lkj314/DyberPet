# coding:utf-8
"""斗地主预合成语音生成脚本（开发期工具，不参与运行时）。

用 edge-tts 把台词清单批量合成为 mp3，输出到本插件 voice/ 目录。
已存在的文件自动跳过（改了文案删掉对应 mp3 重跑即可）。

用法（项目 .venv）：
    .venv/Scripts/python.exe DyberPet/plugins/doudizhu/synthesize_voice.py
"""
from __future__ import annotations

import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'voice')

RANKS = [('3', '三'), ('4', '四'), ('5', '五'), ('6', '六'), ('7', '七'),
         ('8', '八'), ('9', '九'), ('10', '十'), ('J', 'J'), ('Q', 'Q'),
         ('K', 'K'), ('A', 'A'), ('2', '二')]

# 文件名 -> 合成文本
LINES = {
    # ---- 牌型语音（谁出牌都触发，复刻真实斗地主）----
    'pass': '要不起',
    'triple1': '三带一',
    'triple2': '三带二',
    'straight': '顺子',
    'pair_seq': '连对',
    'plane': '飞机',
    'four2': '四带二',
    'four2pair': '四带两对',
    'bomb': '炸弹！',
    'rocket': '王炸！！',
    'spring': '春天！',
    # ---- 桌宠专属情绪语音（仅桌宠座位触发）----
    'pet_landlord': '叫地主！这把牌我看好了！',
    'pet_no_bid': '不叫不叫，这牌拿不动。',
    'pet_bomb': '哈哈！炸弹接招！',
    'pet_rocket': '双王在手！都给我闭嘴！',
    'pet_teammate': '兄弟，牌给你了，走一个！',
    'pet_warning': '我就剩两张了啊，你们悠着点！',
    'pet_pass': '要不起，你们出。',
    'pet_win': '赢了赢了！谢谢老板！',
    'pet_lose': '可恶！就差一张！',
    'pet_taunt_pass': '不敢要了？怕了吧！',
}
# 单张 / 对子 / 三张按 rank 各合成一份
for token, word in RANKS:
    LINES[f'single_{token}'] = word
    LINES[f'pair_{token}'] = f'对{word}'
    LINES[f'triple_{token}'] = f'三个{word}'


async def synth(text: str, path: str, voice: str = 'zh-CN-YunxiNeural'):
    import edge_tts
    await edge_tts.Communicate(text, voice).save(path)


def main():
    os.makedirs(OUT, exist_ok=True)
    todo = {k: t for k, t in LINES.items()
            if not os.path.isfile(os.path.join(OUT, f'{k}.mp3'))}
    print(f'total lines: {len(LINES)}, to synthesize: {len(todo)}')
    for i, (key, text) in enumerate(sorted(todo.items()), 1):
        out = os.path.join(OUT, f'{key}.mp3')
        try:
            asyncio.run(synth(text, out))
            size = os.path.getsize(out)
            print(f'[{i}/{len(todo)}] {key}.mp3  {size} bytes  <- {text}')
        except Exception as e:  # noqa: BLE001
            print(f'[{i}/{len(todo)}] {key} FAILED: {e!r}', file=sys.stderr)
    missing = [k for k in LINES if not os.path.isfile(os.path.join(OUT, f'{k}.mp3'))]
    print('DONE.' + ('' if not missing else f' missing: {missing}'))
    return 1 if missing else 0


if __name__ == '__main__':
    sys.exit(main())
