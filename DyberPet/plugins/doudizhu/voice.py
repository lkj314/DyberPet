# coding:utf-8
"""斗地主预合成语音库——复刻真实斗地主的"打牌出声"手感。

设计（用户拍板）：
- 台词**提前用 edge-tts 合成好 mp3**，内置 `voice/` 目录随插件打包；
  运行时零延迟直接播放，不依赖实时 TTS/网络。
- **牌型语音谁出牌都触发**（玩家/AI 通用）：打出组合就喊"对三！""王炸！！""要不起"，
  和欢乐斗地主一致。
- **桌宠情绪语音仅桌宠座位触发**（叫地主/炸弹得意/送队友/胜负等）。
- 播放走 `api.pet.play_audio(path)`（宿主常驻播放器，音效互相顶掉是符合直觉的）。
"""
from __future__ import annotations

import os
from typing import Optional

# rank_idx -> 文件名/合成文本用 token
RANK_TOKEN = {0: '3', 1: '4', 2: '5', 3: '6', 4: '7', 5: '8', 6: '9', 7: '10',
              8: 'J', 9: 'Q', 10: 'K', 11: 'A', 12: '2'}


def key_for_move(move) -> str:
    """把一手牌映射到语音键（文件名不含扩展名）。"""
    p = move.ptype
    if p == 'rocket':
        return 'rocket'
    if p == 'bomb':
        return 'bomb'
    if p == 'single':
        return f"single_{RANK_TOKEN.get(move.rank, move.rank)}"
    if p == 'pair':
        return f"pair_{RANK_TOKEN.get(move.rank, move.rank)}"
    if p == 'triple':
        return f"triple_{RANK_TOKEN.get(move.rank, move.rank)}"
    if p in ('plane', 'plane1', 'plane2'):
        return 'plane'
    return p            # triple1/triple2/straight/pair_seq/four2/four2pair


class VoiceBank:
    """从插件 voice/ 目录按键播放预合成 mp3；缺失时静默跳过，绝不阻塞出牌。"""

    def __init__(self, base_dir: str):
        self.dir = os.path.join(base_dir, 'voice')

    def path_of(self, key: str) -> str:
        return os.path.join(self.dir, f"{key}.mp3")

    def has(self, key: str) -> bool:
        return os.path.isfile(self.path_of(key))

    def play(self, api, key: str) -> bool:
        if api is None:
            return False
        path = self.path_of(key)
        if not os.path.isfile(path):
            return False
        try:
            api.pet.play_audio(path)
            return True
        except Exception:  # noqa: BLE001
            return False

    def play_move(self, api, move) -> bool:
        return self.play(api, key_for_move(move))
