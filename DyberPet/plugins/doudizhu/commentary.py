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

from DyberPet.llm_core import DEFAULT_OLLAMA_BASE, list_ollama_models
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
                 ollama_base: str = DEFAULT_OLLAMA_BASE,
                 fallback_models: Optional[list] = None):
        self.taunt = taunt
        self.use_llm = use_llm          # 只用于军师
        self.model = model
        self.ollama_base = ollama_base
        # 模型兜底链：配置的模型未安装时按序降级到本机已装模型
        # （如主程序的 chat_model），避免「模型不存在 → 军师永远失声」
        self.fallback_models = [m for m in (fallback_models or []) if m]

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

    # 座位名（与 ui.py SEAT_NAME 一致）：军师必须用名字称呼牌桌上的人
    SEAT_NAME = {0: '你', 1: '肥牛', 2: '路人甲'}

    @staticmethod
    def build_brief(view: dict, candidates: Optional[str] = None) -> str:
        """确定性记牌（只用玩家的合法信息：自己手牌 + 公开出牌历史）。
        candidates: 本地引擎算出的可出候选（中文描述，已截断）——军师的算牌地基。
        """
        played = Counter(card_rank(c) for c in view.get('played_cards', []))
        mine = Counter(card_rank(c) for c in view['my_cards'])
        full = Counter({r: 4 for r in range(13)})
        full[13] = 1
        full[14] = 1
        unseen = full - played - mine          # 未露面的牌（在两个 AI 手里）

        # gone：外面一张不剩的牌（若全在我手里则不算"出完了"）
        gone = [RANK_STR[r] for r in range(15)
                if full[r] > 0 and unseen[r] == 0 and mine[r] < full[r]]
        hidden_big = [RANK_STR[r] for r in (13, 14, 12, 11) if unseen[r] > 0]
        bombs_alive = [RANK_STR[r] for r in range(13) if unseen[r] == 4]

        names = Commentator.SEAT_NAME
        seat = view.get('seat', 0)
        landlord = view.get('landlord')
        role = view.get('role') or 'farmer'

        parts = []
        if landlord is not None and landlord == seat:
            parts.append('本局你是地主')
        elif landlord is not None and landlord in names:
            parts.append(f'本局地主是{names[landlord]}')
            parts.append('你是农民')
        else:
            parts.append(f"你是{'地主' if role == 'landlord' else '农民'}")
        parts.append(f"你手里还有{view['my_count']}张")

        rem = view.get('remaining', {})
        danger = []
        for s, v in rem.items():
            who = names.get(s, f'座位{s}')
            rl = '地主' if s == landlord else '农民'
            if role != 'landlord' and s != landlord:
                parts.append(f"{who}(你的队友，{rl})剩{v}张")
            else:
                parts.append(f"{who}({rl})剩{v}张")
            if v <= 2 and (role == 'landlord' or s == landlord):
                # 只警告对手快跑完：农民局防地主，地主局防两个农民
                # （队友快跑完是好事，不算威胁）
                danger.append(f"{who}({rl})")

        # 自己手里的重火力（让军师知道玩家还能不能反打）
        firepower = []
        if mine[13] and mine[14]:
            firepower.append('双王在手')
        else:
            if mine[14]:
                firepower.append('大王在手')
            if mine[13]:
                firepower.append('小王在手')
        for r in range(13):
            if mine[r] == 4:
                firepower.append(f"{RANK_STR[r]}炸弹在手")
        twos = mine[12]
        if twos:
            firepower.append(f"{twos}张2")
        if firepower:
            parts.append('你手里' + '、'.join(firepower))

        if gone:
            parts.append(f"这些牌已经出完了：{'、'.join(gone[:10])}")
        if hidden_big:
            parts.append(f"大牌还没露面：{'、'.join(hidden_big)}")
        if bombs_alive:
            if len(bombs_alive) <= 4:
                parts.append(f"警惕！{'、'.join(bombs_alive)}的炸弹还可能没炸")
            else:
                parts.append(f"还有{len(bombs_alive)}个点数可能凑成炸弹，留意")
        if danger:
            parts.append(f"{'、'.join(danger)}快跑完了，优先压制")
        if candidates:
            parts.append(f"你手里可以出的（从优到劣）：{candidates}")
        return '；'.join(parts) + '。'

    def request_advisor(self, brief: str,
                        callback: "Callable[[Optional[str], str], None]"):
        """后台线程让 Ollama 把简报说成狗头军师口吻。
        无论成功/失败/超时/空回复都会回调：
        callback(文本, '') 成功；callback(None, 失败原因) 失败。
        （此前失败静默不回调，UI 会永久卡在「军师在想…」——已修）
        模型自动降级：配置模型未安装时落到本机已装模型（见 _pick_model）。
        """
        system = (
            '你是斗地主牌桌旁的狗头军师，帮玩家分析局势出主意。'
            '牌桌上另有两个 AI：「肥牛」和「路人甲」。称呼铁律（必须遵守）：'
            '① 提玩家一律用「你」，严禁用数字或代号（如玩家1、1号）；'
            '② 提桌上 AI 必须叫名字并带身份，如「肥牛(地主)」「路人甲(农民)」、'
            '农民局里提队友说「你的队友肥牛」。'
            '根据记牌简报，用一句不超过40字的口语化中文给建议：'
            '点出关键威胁（谁快跑完了、什么炸弹没露面），'
            '建议出什么牌（可从给的候选里选）或该压谁。'
            '语气俏皮自信、像老牌友支招，直接说内容不要引号。'
        )
        user = f"记牌简报：{brief}"

        def _run():
            # 先探活 + 拿已装模型列表（复用 llm_core 的禁代理会话）
            installed = list_ollama_models(self.ollama_base)
            if not installed:
                callback(None, '连不上 Ollama——请确认 Ollama 正在运行')
                return
            model = self._pick_model(installed)
            try:
                text = self._generate(system, user, model)
                callback(text.strip() if text else None,
                         '' if text else 'Ollama 返回了空回复，换个模型试试')
            except Exception as e:  # noqa: BLE001
                code = getattr(e, 'code', None)
                if code == 404:
                    callback(None, f'模型 {model} 未安装（Ollama 里没有）')
                else:
                    callback(None, f'Ollama 请求失败：{e}')

        threading.Thread(target=_run, daemon=True).start()

    def _pick_model(self, installed: list) -> str:
        """配置模型不在已装列表时按兜底链自动降级，返回实际使用的模型名。"""
        if self.model in installed:
            return self.model
        for cand in self.fallback_models:
            if cand in installed:
                return cand
        # 最后兜底：挑第一个常规生成模型（排除 embed/vl 等特殊用途）
        for name in installed:
            if not any(x in name for x in ('embed', 'bge', 'vl', 'nomic',
                                           'whisper')):
                return name
        return self.model

    def _generate(self, system: str, user: str,
                  model: Optional[str] = None) -> Optional[str]:
        url = f"{self.ollama_base.rstrip('/')}/api/generate"
        payload = json.dumps({
            'model': model or self.model,
            'system': system,
            'prompt': user,
            'stream': False,
        }).encode('utf-8')
        req = urllib.request.Request(
            url, data=payload, headers={'Content-Type': 'application/json'})
        # 禁代理：http_proxy 环境变量会把 127.0.0.1:11434 的请求发给代理，
        # 代理连不上本机 Ollama 就挂到 30s 超时——军师「想很久」的真凶之一
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=30) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        return data.get('response')
