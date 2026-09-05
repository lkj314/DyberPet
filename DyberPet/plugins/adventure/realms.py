# coding:utf-8
"""秘境数值表（纯数据，不依赖 UI 或桌宠代码，可独立测试）。

对应文档 §4.1/§4.2/§4.4：
- 秘境分级与境界绑定：境界解锁更高层数 → 收益更高、风险更大；
- 时长三档：短程(~30分)立刻有回报 / 中程(~2时)主要档 / 长程(过夜)第二天有惊喜；
- 成功率公式实现在 core.adventure_service.compute_success（唯一实现），
  本表只提供 base_success 与要求境界。
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

# 秘境等级（文档 §4.1）：req = 要求境界序（0=炼气 … 5=炼虚+）
REALM_TIERS = [
    {'id': 'fansu',    'name': '凡俗之地', 'req': 0, 'dur': (15 * 60, 30 * 60),
     'reward': 1,   'base': 0.80, 'exp': 1200,    'stone': 15,   'risk': '极低'},
    {'id': 'lingshan', 'name': '灵山福地', 'req': 1, 'dur': (30 * 60, 60 * 60),
     'reward': 3,   'base': 0.72, 'exp': 6000,    'stone': 40,   'risk': '低'},
    {'id': 'dongfu',   'name': '古修洞府', 'req': 2, 'dur': (3600, 7200),
     'reward': 8,   'base': 0.65, 'exp': 20000,   'stone': 100,  'risk': '中'},
    {'id': 'yiji',     'name': '上古遗迹', 'req': 3, 'dur': (7200, 14400),
     'reward': 20,  'base': 0.58, 'exp': 80000,   'stone': 250,  'risk': '中高'},
    {'id': 'yuwai',    'name': '域外战场', 'req': 4, 'dur': (14400, 28800),
     'reward': 50,  'base': 0.50, 'exp': 300000,  'stone': 600,  'risk': '高'},
    {'id': 'tianwai',  'name': '天外秘境', 'req': 5, 'dur': (28800, 43200),
     'reward': 120, 'base': 0.42, 'exp': 1200000, 'stone': 1500, 'risk': '极高'},
]

#: 时长档 → 秘境时长区间内的取值位（0=下限 1=中位 2=上限）
DURATIONS: Dict[str, int] = {'短程': 0, '中程': 1, '长程': 2}
DUR_KEYS = ['短程', '中程', '长程']

#: 历练策略 → (成功率修正, 收益乘数)
RISKS: Dict[str, Tuple[float, float]] = {
    '稳健': (0.05, 0.9),
    '均衡': (0.0, 1.0),
    '激进': (-0.10, 1.3),
}
RISK_KEYS = ['稳健', '均衡', '激进']

#: 历练寻获丹药池（按秘境层序递增品阶；产出与商店/背包同源）
PILL_POOLS = [
    ['清灵散', '养精丹', '灵果'],
    ['合气丹', '回气丹', '培元丹', '灵果'],
    ['金髓丸', '小还丹', '定颜丹', '回气丹'],
    ['黄龙丹', '筑基丹', '灵露', '金髓丸'],
    ['洗髓丹', '降尘丹', '小还丹', '黄龙丹'],
    ['九曲灵参', '万年灵乳', '筑基丹', '洗髓丹'],
]


def dur_label(seconds: float) -> str:
    mins = int(round(seconds / 60))
    if mins < 60:
        return f'{mins}分钟'
    h, m = divmod(mins, 60)
    return f'{h}小时{m:02d}分' if m else f'{h}小时'


def duration_for(tier: dict, dur_key: str) -> int:
    """时长档 → 秘境区间内秒数（短=下限 / 中=中位 / 长=上限）。"""
    lo, hi = tier['dur']
    idx = DURATIONS.get(dur_key, 1)
    if idx == 0:
        return int(lo)
    if idx == 2:
        return int(hi)
    return int((lo + hi) / 2)


def build_spec(tier_idx: int, dur_key: str, risk_key: str,
               self_group: int) -> Tuple[Optional[dict], Optional[str]]:
    """把用户选择打包成 core.dispatch 需要的 spec。

    返回 (spec, None) 或 (None, 错误消息)。境界校验在这里做一次（UI 提示友好），
    core.dispatch 里还有一道兜底校验。
    """
    if not 0 <= tier_idx < len(REALM_TIERS):
        return None, '没有这个秘境'
    tier = REALM_TIERS[tier_idx]
    if self_group < tier['req']:
        from DyberPet.cultivation_service import REALMS
        need = REALMS[min(tier['req'], len(REALMS) - 1)]
        return None, f'境界不足：「{tier["name"]}」需修为达到 {need} 期方可涉足'
    risk_success, risk_reward = RISKS.get(risk_key, RISKS['均衡'])
    from DyberPet.cultivation_service import REALMS
    spec = {
        'realm_id': tier['id'], 'name': tier['name'],
        'req': tier['req'], 'req_name': REALMS[min(tier['req'], 9)] + '期',
        'duration': duration_for(tier, dur_key),
        'base_success': tier['base'], 'reward_mult': tier['reward'],
        'risk_success': risk_success, 'risk_reward': risk_reward,
        'exp_base': tier['exp'], 'stone_base': tier['stone'],
        'pill_pool': PILL_POOLS[min(tier_idx, len(PILL_POOLS) - 1)],
    }
    return spec, None
