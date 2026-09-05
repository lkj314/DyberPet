# coding:utf-8
"""斗地主解说 + 狗头军师（用户重新定位后的设计）。

定位变更（用户拍板）：
- **桌宠只演一个 AI**，不再精分两角；台词是桌宠本体口吻（"肥牛"），配合
  `voice/` 预合成情绪语音（叫地主/炸弹/胜负），像真实斗地主的角色配音。
- **牌型语音**（"对三！""王炸！！""要不起"）由 voice.py 的预合成 mp3 承担，
  谁出牌都触发，本模块不再管。
- **Ollama = 狗头军师**：规则记牌器先算出简报（哪些牌已出完/大牌是否露面/
  各家剩张数），Ollama 只负责把简报润色成口语化的提示——算牌是确定性的
  规则活，LLM 干不来；LLM 说错也无所谓，自娱自乐要的是情绪价值。
"""
from __future__ import annotations

import json
import random
import threading
import urllib.request
from collections import Counter
from typing import Callable, Optional, Tuple

from DyberPet.llm_core import DEFAULT_OLLAMA_BASE
from .card_rules import RANK_STR, card_rank

# ------------------------------------------------------------------ #
# 桌宠预设吐槽（气泡用；关键事件另有预合成语音）
# ------------------------------------------------------------------ #
_LINES = {
    'game_start': [
        "三缺一？不存在的，坐好了！",
        "斗地主开局，敢不敢跟我叫地主？",
        "洗牌洗牌，今天手气应该不错~",
    ],
    'become_landlord': [
        "这把我是地主，你俩完蛋了！",
        "地主到手，看我表演！",
    ],
    'become_farmer': [
        "行，我是农民，联手把它摁死！",
        "农民就农民，配合好照样赢！",
    ],
    'play_bomb': [
        "哈哈！炸弹接招！",
        "炸了炸了，这就叫排面！",
    ],
    'play_rocket': [
        "双王在手！都给我闭嘴！",
        "王炸！！结束的信号！",
    ],
    'teammate_coop': [
        "兄弟，牌给你了，走一个！",
        "队友顶住，我送牌！",
    ],
    'landlord_critical': [
        "我就剩两张了啊，你们悠着点！",
        "警告：我要跑路了！",
    ],
    'pet_pass': [
        "要不起，你们出。",
        "过！这轮让给你们。",
    ],
    'taunt_pass': [
        "不敢要了？怕了吧！",
        "不要？那我可就继续出了！",
    ],
    'result_win': [
        "赢了赢了！谢谢老板！",
        "赢麻了，再来再来！",
    ],
    'result_lose': [
        "可恶！就差一张！",
        "行吧，是你走运……",
    ],
    'spring': [
        "春天！一张没让你走，嘚瑟一下！",
    ],
}

_EMOTION = {
    'game_start': 'taunt', 'become_landlord': 'excited', 'become_farmer': 'calm',
    'play_bomb': 'excited', 'play_rocket': 'excited', 'teammate_coop': 'happy',
    'landlord_critical': 'excited', 'pet_pass': 'calm', 'taunt_pass': 'taunt',
    'result_win': 'excited', 'result_lose': 'sad', 'spring': 'taunt',
}

# 事件 -> 预合成语音键（voice.py 的文件名）
_EVENT_VOICE = {
    'become_landlord': 'pet_landlord', 'play_bomb': 'pet_bomb',
    'play_rocket': 'pet_rocket', 'teammate_coop': 'pet_teammate',
    'landlord_critical': 'pet_warning', 'pet_pass': 'pet_pass',
    'taunt_pass': 'pet_taunt_pass', 'result_win': 'pet_win',
    'result_lose': 'pet_lose',
}


class Commentator:
    def __init__(self, taunt: bool = True, use_llm: bool = False,
                 model: str = 'qwen2.5:7b',
                 ollama_base: str = DEFAULT_OLLAMA_BASE):
        self.taunt = taunt
        self.use_llm = use_llm          # 只用于军师
        self.model = model
        self.ollama_base = ollama_base

    # ------------------------------------------------------------------ #
    def on_event(self, event: str) -> Tuple[Optional[str], str]:
        """桌宠本体台词：返回 (文本, 情绪)。配 _EVENT_VOICE 的预合成语音。"""
        if event not in ('game_start', 'result_win', 'result_lose',
                         'become_landlord', 'spring') and not self.taunt:
            return None, 'calm'
        lines = _LINES.get(event)
        if not lines:
            return None, 'calm'
        return random.choice(lines), _EMOTION.get(event, 'calm')

    @staticmethod
    def voice_for(event: str) -> Optional[str]:
        return _EVENT_VOICE.get(event)

    # ------------------------------------------------------------------ #
    # 狗头军师：规则记牌器出简报 -> Ollama 润色成人话
    # ------------------------------------------------------------------ #
    def request_pet_quip(self, event: str,
                         callback: "Callable[[str], None]"):
        """桌宠人设吐槽（persona_service 统一出口，异步追补）。
        仅修仙人设角色生效；失败静默（预设台词已兜底）。
        event: result_win | result_lose | spring | become_landlord | become_farmer
        注意：军师（request_advisor）不走人设——算牌报数与人设的数值禁令天然互斥。
        """
        def _run():
            try:
                from DyberPet.persona_service import get_persona
                p = get_persona()
                if not p.available():
                    return
                ask = {
                    'result_win': '斗地主对局刚刚获胜，按你的性子得意一句。',
                    'result_lose': '斗地主对局刚刚落败，不甘又不失风度地说一句。',
                    'spring': '对局打出春天（对方一手未出），按你的性子嘚瑟一句。',
                    'become_landlord': '刚刚叫上地主，按你的性子说一句。',
                    'become_farmer': '这一局做农民，按你的性子说一句。',
                }.get(event)
                if not ask:
                    return
                text = p.chat(ask, mode='quip')
                if text and callback:
                    callback(text)
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=_run, daemon=True).start()

    @staticmethod
    def build_brief(view: dict) -> str:
        """确定性记牌（只用玩家的合法信息：自己手牌 + 公开出牌历史）。"""
        played = Counter(card_rank(c) for c in view.get('played_cards', []))
        mine = Counter(card_rank(c) for c in view['my_cards'])
        full = Counter({r: 4 for r in range(13)})
        full[13] = 1
        full[14] = 1
        unseen = full - played - mine          # 未露面的牌（在两个 AI 手里）

        gone = [RANK_STR[r] for r in range(15)
                if full[r] > 0 and unseen[r] == 0]
        hidden_big = [RANK_STR[r] for r in (13, 14, 12, 11) if unseen[r] > 0]
        bombs_alive = [RANK_STR[r] for r in range(13) if unseen[r] == 4]

        parts = []
        role = view.get('role') or 'farmer'
        parts.append(f"你是{'地主' if role == 'landlord' else '农民'}")
        parts.append(f"你手里还有{view['my_count']}张")
        rem = view.get('remaining', {})
        names = {s: ('桌宠' if s == 1 else '路人甲') for s in rem}
        parts.append('、'.join(f"{names[s]}剩{v}张" for s, v in rem.items()))
        if gone:
            parts.append(f"这些牌已经出完了：{'、'.join(gone[:10])}")
        if hidden_big:
            parts.append(f"大牌还没露面：{'、'.join(hidden_big)}")
        if bombs_alive:
            parts.append(f"警惕！{'、'.join(bombs_alive)}的炸弹还可能没炸")
        return '；'.join(parts) + '。'

    def request_advisor(self, brief: str, callback: Callable[[str], None]):
        """后台线程让 Ollama 把简报说成狗头军师口吻；成功 callback(text)。"""
        if not self.use_llm:
            return
        system = (
            '你是斗地主里的狗头军师「肥牛」，帮玩家分析局势出主意。'
            '根据给你的记牌简报，用一句不超过40字的口语化中文给出建议，'
            '语气俏皮自信、像老牌友支招，直接说内容不要引号。'
        )
        user = f"记牌简报：{brief}"

        def _run():
            try:
                text = self._generate(system, user)
                if text:
                    callback(text.strip())
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=_run, daemon=True).start()

    def _generate(self, system: str, user: str) -> Optional[str]:
        url = f"{self.ollama_base.rstrip('/')}/api/generate"
        payload = json.dumps({
            'model': self.model,
            'system': system,
            'prompt': user,
            'stream': False,
        }).encode('utf-8')
        req = urllib.request.Request(
            url, data=payload, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        return data.get('response')
