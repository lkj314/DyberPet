# coding:utf-8
"""叙事层：把 core 掷好的结果讲成故事（文档 §三：双引擎分工）。

铁律：
- 本层**绝不产生数值**——只读 skeleton（事件骨架）与 result（已定结果）；
- prompt 一律是"事实清单 + 人设"，要求只写故事、不报数字；
- LLM（persona_service，Ollama 本地）不可用/失败时，回退 events 里的
  预设模板文案——离线也有故事，只是少了点文采。
- persona 自带角色守卫：Kitty 等非修仙角色 available()=False → 直接走预设。
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

from . import events

try:
    from DyberPet.persona_service import get_persona
except Exception:  # noqa: BLE001
    get_persona = None

# 叙事篇幅设置 → persona 输出模式
LENGTH_MODE = {'一句话': 'talisman', '短篇(80字)': 'narrate', '长篇(200字)': 'tale'}


def _persona():
    if get_persona is None:
        return None
    try:
        p = get_persona()
        return p if p.available() else None
    except Exception:  # noqa: BLE001
        return None


def _async(fn, callback: Callable[[Optional[str]], None]):
    """后台线程跑 fn()，完成后把结果交给 callback（None=失败，走预设）。"""

    def _run():
        try:
            callback(fn())
        except Exception:  # noqa: BLE001
            callback(None)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


def request_talisman(skeleton: dict, idx: int, total: int,
                     callback: Callable[[Optional[str]], None]) -> None:
    """第 idx 张传讯符的 LLM 文案（绝不剧透结果）。"""

    def _job() -> Optional[str]:
        p = _persona()
        if p is None:
            return None
        stage = {0: '刚出发不久，写出发见闻', 1: '行程过半，写途中转折',
                 2: '接近归程，写尾声预告'}.get(
            idx, '写一路见闻')
        user = (f"你正在外出历练。{events.llm_facts(skeleton, stage_hint=stage)}。\n"
                f"请写第 {idx + 1}/{total} 张传讯符给道友："
                f"第一人称，45 字以内，报近况但**绝不透露此行胜负结果**，"
                f"落款不必署名。只写传讯符正文。")
        return p.chat(user, mode='talisman', extra_context=skeleton.get('vars', {}))

    _async(_job, callback)


def preset_talisman(skeleton: dict, idx: int, total: int) -> str:
    return events.preset_talisman(skeleton, idx, total)


def request_return(skeleton: dict, result: dict, length_key: str,
                   callback: Callable[[Optional[str]], None]) -> None:
    """归来讲述：mode 由「见闻篇幅」设置决定；结果事实喂进 prompt。"""

    def _job() -> Optional[str]:
        p = _persona()
        if p is None:
            return None
        mode = LENGTH_MODE.get(length_key, 'narrate')
        user = (f"你刚从外面历练归来。{events.llm_facts(skeleton, outcome=result.get('outcome', ''))}。"
                f"请以第一人称向道友讲述这段经历：有画面感，符合你的人设与当前境界气度，"
                f"不要报具体数字（灵石/修为多少一概不说），只讲故事。")
        return p.chat(user, mode=mode, extra_context=skeleton.get('vars', {}))

    _async(_job, callback)


def preset_return(skeleton: dict, outcome: str) -> str:
    return events.preset_return(skeleton, outcome)


def request_injury_quip(injury_mult: float,
                        callback: Callable[[Optional[str]], None]) -> None:
    """带伤归来的抱怨一句（quip 档，≤15 字）。"""

    def _job() -> Optional[str]:
        p = _persona()
        if p is None:
            return None
        severity = '伤得不轻' if injury_mult <= 0.6 else '受了点轻伤'
        user = (f"你历练归来，{severity}，行动有些迟缓。"
                f"用一句话向道友抱怨/自嘲，不要报数值。")
        return p.chat(user, mode='quip', extra_context={'kw': '受伤 历练'})

    _async(_job, callback)
