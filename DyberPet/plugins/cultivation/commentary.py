# coding:utf-8
"""修仙表达层：境界人格台词 + Ollama 感言（数值绝不经过这里）。

分工铁律（沿用斗地主验证过的架构哲学）：
- 数值层 `cultivation_service.py`：修为/境界/成功率 100% 代码公式。
- 表达层（本模块）：突破感言、顿悟语录、境界人格——LLM 说错无妨，好玩即可。

境界人格（以韩立为主形象定制，凡人修仙传风味）：
  炼气/筑基 → 谨小慎微；金丹/元婴 → 自信从容；
  化神/炼虚/合体 → 狂放不羁；大乘/渡劫/真仙 → 沧桑通透。
"""
from __future__ import annotations

import json
import random
import threading
import urllib.request
from typing import Callable, Optional

try:
    from DyberPet.llm_core import DEFAULT_OLLAMA_BASE
except Exception:  # noqa: BLE001
    DEFAULT_OLLAMA_BASE = 'http://localhost:11434'

# 境界名 → 演出动作（韩立动作库；其他角色 play_act 内部静默跳过）
REALM_ACT = {
    '炼气': 'meditate',            # 打坐
    '筑基': 'luoyanbu',            # 罗烟步
    '金丹': 'alchemy',             # 炼丹
    '元婴': 'dayanjue',            # 大衍决（元婴出场）
    '化神': 'qianlanbingyan',      # 乾蓝冰焰
    '炼虚': 'sword_fly',           # 御剑飞行
    '合体': 'sword_shadow_split',  # 剑影分光
    '大乘': 'giant_sword',         # 巨剑
    '渡劫': 'dageng_sword_array',  # 剑阵
    '真仙': 'qingzhu_fengyun_sword',  # 庆祝御剑
}

# 人格分档（境界序 // 2）：0 谨慎 1 从容 2 狂放 3 沧桑
_PERSONA = [
    {
        'tone': '谨小慎微、结结巴巴的修仙新人',
        'lines': [
            "前辈……我、我才炼气几层，莫要笑话。",
            "今日灵气似乎很充沛，我偷偷修炼了一小会儿。",
            "师父说，修行第一要紧的是莫要心浮气躁……",
            "我还不敢御剑，走路都是轻手轻脚的。",
        ],
    },
    {
        'tone': '自信从容、颇有大派弟子风范',
        'lines': [
            "金丹既成，这方天地也该有我一名号了。",
            "区区琐事，交给我便是。",
            "今日剑意微有精进，甚好。",
            "灵石管够的话，丹药也不是不能炼。",
        ],
    },
    {
        'tone': '狂放不羁、剑气纵横',
        'lines': [
            "哈哈！区区心魔，也敢来扰我道心！",
            "天地为炉，造化为工——今日我便逆天一回！",
            "御剑三万里，谁人识得旧韩郎！",
            "一念花开，一念剑来。",
        ],
    },
    {
        'tone': '沧桑通透、看尽仙凡',
        'lines': [
            "千年修行，只为这一线生机。",
            "仙凡之隔，原来不过一念之间。",
            "回首向来萧瑟处，也无风雨也无仙。",
            "大道三千，我只取一瓢饮。",
        ],
    },
]


def persona_tier(realm_idx: int) -> int:
    """境界序（0..9）→ 人格档位（0..3）。
    炼气/筑基→0 谨慎；金丹/元婴→1 从容；化神~合体→2 狂放；大乘以上→3 沧桑。
    """
    g = max(0, min(realm_idx // 4, 9))
    if g <= 1:
        return 0
    if g <= 3:
        return 1
    if g <= 6:
        return 2
    return 3


# 预设台词（事件 → [(文本, 权重)]；LLM 不可用时的兜底，也是秒弹的第一句）
_LINES = {
    'alchemy': [
        ("开炉炼丹！看我的手艺。", 0),
        ("丹火已起，静待丹成。", 0),
    ],
    'alchemy_done': [
        ("丹成！这炉成色不错。", 0),
        ("哈，好丹！快收进背包。", 0),
    ],
    'pill_used': [
        ("药力入腹，浑身舒坦~", 0),
    ],
    'breakthrough': [
        ("灵气入体，瓶颈松动——突破了！", 1.0),
        ("水到渠成，境界又精进了一层。", 1.0),
        ("多谢道友平日照拂，今日突破，全赖道心稳固！", 1.0),
        ("闭关数日，一朝破境，快哉！", 0.8),
    ],
    'break_fail': [
        ("唉……走火入魔，修为倒退了几分。", 1.0),
        ("差之毫厘，谬以千里。且让我缓缓……", 1.0),
        ("心浮气躁了，改日再战！", 0.8),
    ],
    'epiphany': [
        ("妙啊！灵光一闪，竟有一番大彻大悟！", 1.0),
        ("天地有理，万物有灵——我悟了！", 1.0),
        ("这一刻，仿佛看见了大道的一角。", 1.0),
    ],
    'exp_gain': [
        ("与道友斗法一场，胜有所悟！", 1.0),
        ("此战痛快，修为竟有所进！", 1.0),
        ("以战养战，方是修行正途。", 0.8),
    ],
    'ascend': [
        ("雷劫已过，仙门大开——道友，天上见了！", 1.0),
        ("千年修行，今日圆满。飞升！", 1.0),
    ],
    'seclusion_on': [
        ("你且忙去，我自行闭关修炼。", 1.0),
        ("入定了……不必挂念。", 0.8),
    ],
    'dual_on': [
        ("与我一同打坐双修吧，功效翻倍！", 1.0),
        ("双修开始，你我皆莫要动念。", 0.8),
    ],
}

_EMOTION = {'breakthrough': 'excited', 'break_fail': 'sad', 'epiphany': 'excited',
            'exp_gain': 'happy', 'ascend': 'excited', 'seclusion_on': 'calm',
            'dual_on': 'calm'}


class Commentator:
    def __init__(self, use_llm: bool = False, model: str = 'qwen2.5:7b',
                 ollama_base: str = DEFAULT_OLLAMA_BASE):
        self.use_llm = use_llm
        self.model = model
        self.ollama_base = ollama_base

    # ------------------------------------------------------------------ #
    def on_event(self, event: str, realm_idx: int = 0) -> Optional[str]:
        """预设台词（立即播）。按境界人格微调：低境界优先'谨慎'语气。"""
        lines = _LINES.get(event)
        if not lines:
            return None
        # 人格化加权：突破/顿悟类台词叠加当前人格语录的概率
        tier = persona_tier(realm_idx)
        if random.random() < 0.35:
            return random.choice(_PERSONA[tier]['lines'])
        return random.choice(lines)[0]

    def act_for_realm(self, realm_idx: int) -> Optional[str]:
        """境界 → 韩立演出动作。"""
        if realm_idx > 9:
            return REALM_ACT['真仙']
        try:
            from DyberPet.cultivation_service import REALMS
            return REALM_ACT.get(REALMS[max(0, realm_idx // 4)])
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------ #
    # Ollama 表达（异步线程 + 回调；失败静默，预设台词已兜底）
    # 人设统一走 persona_service（韩立口吻/境界语气/记忆）；角色不在
    # applicable_pets 时回退内置 prompt（其他角色仍可吐槽）。
    # ------------------------------------------------------------------ #
    def request_talk(self, kind: str, ctx: dict,
                     callback: Callable[[str], None]) -> None:
        """后台生成感言/顿悟/失败抱怨。kind: breakthrough|break_fail|epiphany|ascend"""
        if not self.use_llm:
            return
        realm = ctx.get('stage', '炼气 · 初期')
        user = {
            'breakthrough': f'刚刚突破成功（当前{realm}），请以第一人称说一句突破感言。',
            'break_fail': '刚刚突破失败、修为受损，说一句沮丧又不失幽默的抱怨。',
            'epiphany': '修炼时突然顿悟，收获了大量修为。说一句顿悟感悟，要有仙侠味。',
            'ascend': f'此刻已渡劫飞升成仙（原{realm}）。说一句飞升感言，感慨千年陪伴与仙途。',
        }.get(kind)
        if not user:
            return
        # 模式映射：突破/飞升走仪式感档，其余走短吐槽档（长度卡死）
        mode = {'breakthrough': 'breakthrough', 'ascend': 'breakthrough',
                'break_fail': 'quip', 'epiphany': 'quip'}.get(kind, 'quip')
        realm_idx = ctx.get('to') if isinstance(ctx.get('to'), int) else None
        fallback_system = self._fallback_system(kind, realm)

        def _run():
            text = None
            try:
                from DyberPet.persona_service import get_persona
                p = get_persona()
                if p.available():
                    text = p.chat(user, mode=mode, realm_idx=realm_idx)
            except Exception:  # noqa: BLE001
                text = None
            if not text:
                try:
                    text = self._generate(fallback_system, user)
                except Exception:  # noqa: BLE001
                    return
            text = text.strip().strip('"“”').strip()
            if text:
                callback(text)

        threading.Thread(target=_run, daemon=True).start()

    def _fallback_system(self, kind: str, realm: str) -> str:
        """persona 不可用时的内置 prompt（其他角色）。"""
        tier = persona_tier(0)
        persona = _PERSONA[tier]['tone']
        prompts = {
            'breakthrough': (
                f'你是桌宠修士（当前境界：{realm}，说话风格：{persona}）。'
                f'刚刚突破成功，请以第一人称说一句突破感言。'
                f'可结合当前时间、天气或修行小事，不超过40字，口语化中文，不要引号。'),
            'break_fail': (
                f'你是桌宠修士（当前境界：{realm}，说话风格：{persona}）。'
                f'刚刚突破失败、修为受损，说一句沮丧又不失幽默的抱怨，'
                f'不超过40字，口语化中文，不要引号。'),
            'epiphany': (
                f'你是桌宠修士（当前境界：{realm}，说话风格：{persona}）。'
                f'修炼时突然顿悟，收获了大量修为。说一句顿悟感悟，'
                f'要有仙侠味，不超过40字，口语化中文，不要引号。'),
            'ascend': (
                f'你是桌宠修士，此刻已渡劫飞升成仙（原境界：{realm}）。'
                f'说一句飞升感言，感慨千年陪伴与仙途，不超过40字，'
                f'口语化中文，不要引号。'),
        }
        return prompts.get(kind, prompts['breakthrough'])

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
