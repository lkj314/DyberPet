"""五子棋陪玩解说——棋局事件 -> 预设台词 + 情绪；可选 Ollama 异步增强。

设计要点：
- ``on_event`` 同步返回 (text, emotion)，由视图层立刻驱动桌宠气泡/动画（即时响应）。
- 若开启 LLM 增强，``request_llm`` 在后台线程调本机 Ollama 生成更自然的吐槽，
  完成后通过 callback 回传（预设已先顶替思考空窗，Ollama 结果为主）。
- 本模块只产出「文本 + 情绪标签」，不直接调用桌宠 API，保持解耦。
"""
from __future__ import annotations

import json
import random
import threading
import urllib.request
from typing import Callable, Dict, List, Optional, Tuple

from DyberPet.llm_core import DEFAULT_OLLAMA_BASE


# 情绪价值路线：会吐槽、会放水、会演戏、会甩锅、会嘚瑟
_LINES: Dict[str, List[str]] = {
    "game_start": [
        "来啊，你先手，我让你三子都行~",
        "开局了，今天想被我虐几下？",
        "就你这水平，也敢跟我下五子棋？",
    ],
    "player_open_three": [
        "啧，连成三个了？纯属运气。",
        "别得意，这才哪到哪。",
        "三个而已，我睁一只眼闭一只眼罢了。",
    ],
    "player_four": [
        "卧槽你要四了？！我堵！",
        "有点东西啊你，但这手我早看穿了。",
    ],
    "ai_open_three": [
        "看见没，三个连成线了，你慌不慌？",
        "嗤，就这防守也能让我连三？",
        "我连三个了，你堵得住算我输。",
    ],
    "ai_four": [
        "四连了，你这下有得堵咯~",
        "四子一线，认输吧你。",
    ],
    "player_win": [
        "啊？这都能输？都怪网络卡！",
        "你肯定是开挂了，这局不算不算！",
        "哼，让你一局而已，别太得意。",
    ],
    "ai_win": [
        "哈哈哈，就这？下次多练练再来~",
        "稳得一批，认输吧你。",
        "嘚瑟一下不过分吧？毕竟赢麻了。",
    ],
    "draw": [
        "平了？不行，再来一局分胜负！",
        "势均力敌？那是我在让你。",
    ],
    "handicap": [
        "这步我故意的，看你笨手笨脚怪可怜的。",
        "哼，让你一个子，别哭就行。",
    ],
}

_EMOTION: Dict[str, str] = {
    "game_start": "taunt",
    "player_open_three": "worried",
    "player_four": "worried",
    "ai_open_three": "taunt",
    "ai_four": "excited",
    "player_win": "sad",
    "ai_win": "excited",
    "draw": "calm",
    "handicap": "taunt",
}

# 这些事件即使关闭吐槽模式也要播报（开局/胜负/和棋）
_ALWAYS_ON = {"game_start", "player_win", "ai_win", "draw"}


class Commentator:
    def __init__(self, taunt: bool = True, use_llm: bool = False,
                 model: str = "gemma3:4b", ollama_base: str = DEFAULT_OLLAMA_BASE):
        self.taunt = taunt
        self.use_llm = use_llm
        self.model = model
        self.ollama_base = ollama_base

    def on_event(self, event: str, **ctx) -> Tuple[Optional[str], str]:
        """返回 (台词, 情绪)。不需要台词时文本为 None。"""
        if event not in _ALWAYS_ON and not self.taunt:
            return None, "calm"
        lines = _LINES.get(event)
        if not lines:
            return None, "calm"
        text = random.choice(lines)
        return text, _EMOTION.get(event, "calm")

    def request_llm(self, event: str, board_desc: str,
                    callback: Callable[[str], None]):
        """后台线程生成吐槽；人设统一走 persona_service（修仙角色），
        其他角色回退原 prompt。失败静默（预设台词已兜底）。"""
        if not self.use_llm:
            return
        prompt = (
            f"你是桌宠肥牛，正在和玩家下五子棋。当前局面：{board_desc}。"
            f"刚刚发生了：{event}。用一句口语化中文吐槽/嘚瑟/甩锅，带点性格，不超过30字，不要标点以外的符号。"
        )

        def _run():
            text = None
            try:
                from DyberPet.persona_service import get_persona
                p = get_persona()
                if p.available():
                    text = p.chat(
                        f"五子棋对局中，当前局面：{board_desc}。"
                        f"刚刚发生了：{event}。请按你的性子吐槽一句。",
                        mode="quip")
            except Exception:  # noqa: BLE001
                text = None
            if not text:
                try:
                    text = self._ollama_generate(prompt)
                except Exception:  # noqa: BLE001
                    return
            if text:
                callback(text.strip())

        threading.Thread(target=_run, daemon=True).start()

    def _ollama_generate(self, prompt: str) -> Optional[str]:
        url = f"{self.ollama_base.rstrip('/')}/api/generate"
        payload = json.dumps({
            "model": self.model,
            "prompt": prompt,
            "stream": False,
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("response")
