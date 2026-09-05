# coding:utf-8
"""事件模板库（纯数据：骨架 + 变量池，绝不含数值）。

对应文档 §3.2：骨架由代码定死，血肉由 LLM 填充。
- 每个模板只描述"发生了什么事"，收益/胜负由 core.adventure_service 掷骰子；
- 传讯符三段式文案与归来讲述都是**预设模板**（LLM 不可用时的兜底叙事），
  LLM 版本由 narrative.py 在模板给定的事实范围内润色；
- format_map(_SafeDict) 缺变量不炸，模板可以放心共享类别默认句。
"""
from __future__ import annotations

import random
from typing import Dict, List

OUTCOME_TXT = {'大胜': '大获全胜，满载而归', '小胜': '顺利了结，略有斩获',
               '险胜': '险些折戟，总算脱身', '失利': '势不可为，仓皇而归',
               '重伤': '拼死杀出，伤得不轻'}


class _SafeDict(dict):
    """format_map 缺变量时留空，模板共享不炸。"""

    def __missing__(self, key):  # noqa: D105
        return ''


# 类别默认传讯符三段（模板未自定义时套用）
CAT_STAGES: Dict[str, List[str]] = {
    'battle':   ['已至{loc}，此地灵气不错，只是隐有妖气……',
                 '途中撞见{beast}，正与它周旋，一时难分高下，勿念。',
                 '此地事了，不日当归。'],
    'herb':     ['已入{loc}深处，崖壁间隐有药香。',
                 '寻得一株{herb}，守了半日方才到手。',
                 '药已入囊，不日当归。'],
    'ruin':     ['觅得{loc}遗迹入口，禁制斑驳，似久无人至。',
                 '遗迹深处机关重重，正小心摸索前行。',
                 '此行将毕，归期近矣。'],
    'meet':     ['行至{loc}，遇一二同道，相谈甚欢。',
                 '与诸道友论道半日，颇有所得。',
                 '天下无不散之筵席，就此别过，不日归。'],
    'lost':     ['入{loc}后雾气渐重，似有些迷了方向。',
                 '仍在雾中寻路，幸有符箓指北，不致困死。',
                 '路已辨明，正往回走。'],
    'treasure': ['探得{loc}一处秘仓，门上尘封甚厚。',
                 '仓中物件正一一清点，恐惊动守灵，不敢久留。',
                 '事毕，即刻返程。'],
    'weather':  ['行至{loc}，天色骤变，灵雨/罡风难测。',
                 '寻了处山坳避风，静候天象过去。',
                 '天光放晴，兼程赶回。'],
    'escort':   ['在{loc}接了一单护送行商的活计，车马已行。',
                 '途中几番虚惊，货物无损。',
                 '货交割完毕，正自返程。'],
    'cave':     ['天色向晚，在{loc}寻了个山洞落脚。',
                 '洞中一夜无话，唯闻风声如啸。',
                 '收拾行装，启程回府。'],
    'pond':     ['{loc}有灵泉，泉底偶有宝光闪动。',
                 '垂钓半日，指尖似有所感。',
                 '今日收竿，回见。'],
}

# 类别默认归来讲述（win 走 win 句，lose 走 lose 句）
CAT_TALES: Dict[str, Dict[str, str]] = {
    'battle':   {'win': '此去{loc}历练，与{beast}一场恶斗，{outcome_txt}。',
                 'lose': '此去{loc}历练，撞上{beast}力有不逮，{outcome_txt}。'},
    'herb':     {'win': '此去{loc}寻药，幸得{herb}一株，{outcome_txt}。',
                 'lose': '此去{loc}寻药，药没寻全，{outcome_txt}。'},
    'ruin':     {'win': '此去{loc}遗迹，探得前人遗藏，{outcome_txt}。',
                 'lose': '此去{loc}遗迹，禁制凶险，{outcome_txt}。'},
    'meet':     {'win': '此去{loc}访道，与诸道友论道切磋，{outcome_txt}。',
                 'lose': '此去{loc}访道，所遇非人，{outcome_txt}。'},
    'lost':     {'win': '此去{loc}，虽在雾中迷了路，倒也因祸得福，{outcome_txt}。',
                 'lose': '此去{loc}迷路半日，{outcome_txt}。'},
    'treasure': {'win': '此去{loc}开仓探宝，{outcome_txt}。',
                 'lose': '此去{loc}探宝，守灵凶悍，{outcome_txt}。'},
    'weather':  {'win': '此去{loc}遇上罡风灵雨，挺了过来，{outcome_txt}。',
                 'lose': '此去{loc}被天象困了半程，{outcome_txt}。'},
    'escort':   {'win': '此去{loc}护送行商，一路平安，{outcome_txt}。',
                 'lose': '此去{loc}护送，路上不太平，{outcome_txt}。'},
    'cave':     {'win': '此去{loc}夜宿山洞，晨起赶路，{outcome_txt}。',
                 'lose': '此去{loc}夜宿遇险，{outcome_txt}。'},
    'pond':     {'win': '此去{loc}灵泉垂钓，鱼获颇丰，{outcome_txt}。',
                 'lose': '此去{loc}钓了一日空竿，{outcome_txt}。'},
}

# 事件模板（20+ 骨架）：vars 为变量池，pick 时逐项采样
TEMPLATES: List[dict] = [
    # ---- battle ----
    {'id': 'wolf',   'cat': 'battle', 'title': '遭遇妖狼', 'tiers': [0, 1, 2],
     'vars': {'beast': ['妖狼', '苍鬃妖狼', '月嚎狼王']}},
    {'id': 'snake',  'cat': 'battle', 'title': '赤鳞蛇拦路', 'tiers': [1, 2, 3],
     'vars': {'beast': ['赤鳞蛇', '赤鳞蟒', '火纹蛇妖']}},
    {'id': 'bear',   'cat': 'battle', 'title': '铁背熊挡道', 'tiers': [1, 2],
     'vars': {'beast': ['铁背熊', '玄岩熊罴']}},
    {'id': 'bandit', 'cat': 'battle', 'title': '散修劫道', 'tiers': [0, 1, 2, 3],
     'vars': {'beast': ['劫道的散修', '黑衣修士']}},
    {'id': 'demon',  'cat': 'battle', 'title': '域外魔物', 'tiers': [4, 5],
     'vars': {'beast': ['域外魔物', '噬灵魔物', '魔渊邪影']}},
    # ---- herb ----
    {'id': 'cliff',  'cat': 'herb', 'title': '崖壁采药', 'tiers': [0, 1, 2],
     'vars': {'herb': ['紫猴花', '金线莲', '百年茯苓']}},
    {'id': 'valley', 'cat': 'herb', 'title': '幽谷灵草', 'tiers': [1, 2, 3],
     'vars': {'herb': ['七叶清心草', '龙涎草', '九叶灵芝']}},
    {'id': 'ginseng','cat': 'herb', 'title': '老参传闻', 'tiers': [2, 3, 4],
     'vars': {'herb': ['千年血参', '老山参', '地髓灵根']}},
    # ---- ruin ----
    {'id': 'hall',   'cat': 'ruin', 'title': '古修洞府', 'tiers': [2, 3],
     'vars': {}},
    {'id': 'tower',  'cat': 'ruin', 'title': '锁妖塔遗址', 'tiers': [3, 4],
     'vars': {}},
    {'id': 'altar',  'cat': 'ruin', 'title': '上古祭坛', 'tiers': [4, 5],
     'vars': {}},
    # ---- meet ----
    {'id': 'fair',   'cat': 'meet', 'title': '坊市逢友', 'tiers': [0, 1, 2],
     'vars': {'npc': ['游方道人', '卖符的老修士', '同行女修']}},
    {'id': 'duel',   'cat': 'meet', 'title': '道友论法', 'tiers': [1, 2, 3],
     'vars': {'npc': ['剑修道友', '体修壮汉', '丹修前辈']}},
    # ---- lost ----
    {'id': 'mist',   'cat': 'lost', 'title': '迷雾困山', 'tiers': [0, 1, 2],
     'vars': {}},
    {'id': 'maze',   'cat': 'lost', 'title': '幻阵迷途', 'tiers': [2, 3, 4],
     'vars': {}},
    # ---- treasure ----
    {'id': 'vault',  'cat': 'treasure', 'title': '秘仓寻宝', 'tiers': [1, 2, 3],
     'vars': {'goods': ['几件古器', '一匣灵石', '半卷残经']}},
    {'id': 'wreck',  'cat': 'treasure', 'title': '沉舟遗宝', 'tiers': [2, 3, 4],
     'vars': {'goods': ['舱中遗货', '船主遗藏']}},
    # ---- weather ----
    {'id': 'storm',  'cat': 'weather', 'title': '罡风带', 'tiers': [3, 4, 5],
     'vars': {}},
    {'id': 'rain',   'cat': 'weather', 'title': '灵雨绵绵', 'tiers': [0, 1],
     'vars': {}},
    # ---- escort / cave / pond ----
    {'id': 'caravan','cat': 'escort', 'title': '护送商队', 'tiers': [0, 1, 2],
     'vars': {}},
    {'id': 'cave',   'cat': 'cave', 'title': '山洞借宿', 'tiers': [0, 1],
     'vars': {}},
    {'id': 'pond',   'cat': 'pond', 'title': '灵泉垂钓', 'tiers': [0, 1, 2],
     'vars': {'catch': ['一尾银鳞灵鱼', '半兜泉底沙金', '一枚螺贝']}},
]

_LOCS = {
    0: ['青云山', '落霞镇', '黄枫谷', '乱石岗'],
    1: ['灵兽山', '太岳山脉', '彩霞山', '落云涧'],
    2: ['昆吾山', '虚天殿外围', '黑风岭', '碧水潭'],
    3: ['坠魔谷', '乱星海孤岛', '古剑门遗址'],
    4: ['域外战场', '天渊废墟', '魔渊边缘'],
    5: ['天外秘境', '星宫古域', '界缝深处'],
}


def pick(tier_idx: int, rng: Optional[random.Random] = None) -> dict:
    """按秘境层序随机抽一个事件模板并采样变量 → 事件骨架 skeleton。

    skeleton 是纯数据 dict：{tid, cat, title, loc, vars}，交给 core 存档、
    交给 narrative 讲故事。 tiers 空缺的层回退到任意模板。
    """
    rng = rng or random
    cands = [t for t in TEMPLATES if not t.get('tiers') or tier_idx in t['tiers']]
    tpl = rng.choice(cands or TEMPLATES)
    locs = _LOCS.get(min(tier_idx, len(_LOCS) - 1), _LOCS[0])
    vars_ = {k: rng.choice(v) for k, v in tpl.get('vars', {}).items()}
    vars_['loc'] = rng.choice(locs)
    return {'tid': tpl['id'], 'cat': tpl['cat'], 'title': tpl['title'],
            'vars': vars_}


def _tpl_of(skeleton: dict) -> dict:
    tid = skeleton.get('tid')
    return next((t for t in TEMPLATES if t['id'] == tid), {})


def preset_talisman(skeleton: dict, idx: int, total: int) -> str:
    """第 idx 张传讯符的预设文案（LLM 不可用时的兜底）。"""
    tpl = _tpl_of(skeleton)
    stages = tpl.get('stages') or CAT_STAGES.get(skeleton.get('cat', ''), [])
    if not stages:
        return f'行至{skeleton.get("vars", {}).get("loc", "外")}，一切安好，勿念。'
    # 三段取 0/中/末，段数多于三张时均匀取
    pos = 0 if total <= 1 else int(round(idx * (len(stages) - 1) / max(1, total - 1)))
    s = stages[min(pos, len(stages) - 1)]
    return s.format_map(_SafeDict(skeleton.get('vars', {})))


def preset_return(skeleton: dict, outcome: str) -> str:
    """归来讲述的预设文案。"""
    tpl = _tpl_of(skeleton)
    tales = tpl.get('tales') or CAT_TALES.get(skeleton.get('cat', ''),
                                              CAT_TALES['battle'])
    branch = 'win' if outcome in ('大胜', '小胜', '险胜') else 'lose'
    s = tales.get(branch) or list(tales.values())[0]
    vars_ = dict(skeleton.get('vars', {}))
    vars_['outcome_txt'] = OUTCOME_TXT.get(outcome, '')
    return s.format_map(_SafeDict(vars_))


def llm_facts(skeleton: dict, outcome: str = '', stage_hint: str = '') -> str:
    """给 LLM 的事实清单（模板 + 变量，文档 §3.2 的"事件骨架"）。

    只含事实，不含数值——收益数字绝不出现在 prompt 里。
    """
    v = skeleton.get('vars', {})
    facts = f"地点：{v.get('loc', '无名之地')}；事件：{skeleton.get('title', '历练')}"
    for k in ('beast', 'herb', 'npc', 'goods', 'catch'):
        if v.get(k):
            facts += f"；相关：{v[k]}"
    if stage_hint:
        facts += f"；阶段：{stage_hint}"
    if outcome:
        facts += f"；结果（只能按此描述，不得改动）：{OUTCOME_TXT.get(outcome, outcome)}"
    return facts
