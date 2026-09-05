# coding:utf-8
"""修仙放置核心服务（常驻，纯逻辑，无 Qt 依赖）。

设计来源：`桌宠修仙放置系统项目介绍.md`
- **分层铁律**：本模块是"数值层"——修为计算、境界判定、突破成功率 100% 由
  代码公式决定，绝不让 LLM 参与数值（LLM 只在插件表达层读结果做台词）。
- **常驻**：作为主程序模块（非插件）存在，任何插件可通过 `add_exp()` 加修为，
  无插件间依赖。玩法插件（plugins/cultivation）只负责 UI + 表达 + 驱动。
- **时间戳差值结算**：不跑秒级定时器，只存 last_tick，按差值一次性结算；
  天然支持离线收益（关机期间照常结算）。
- **防作弊**：时间回拨放弃结算、单次结算上限、离线效率打折。

境界体系：10 境 × 4 阶 = 40 阶
  炼气 → 筑基 → 金丹 → 元婴 → 化神 → 炼虚 → 合体 → 大乘 → 渡劫 → 真仙
  每阶突破需求与速率均为指数增长（前期快、中期稳、后期长）。
"""
from __future__ import annotations

import bisect
import json
import math
import os
import random
import threading
import time
from typing import Dict, List, Optional, Tuple

# ------------------------------------------------------------------ #
# 数值表（调参只动这里）
# ------------------------------------------------------------------ #
REALMS = ['炼气', '筑基', '金丹', '元婴', '化神', '炼虚', '合体', '大乘', '渡劫', '真仙']
STAGES = ['初期', '中期', '后期', '圆满']

# 每境突破总需求（修为，与文档 §4.2 对齐；化神后逐级 ×15）
REALM_NEED = [1.0e3, 1.5e4, 2.5e5, 4.0e6, 6.0e7,
              9.0e8, 1.35e10, 2.0e11, 3.0e12, 4.5e13]
# 境内四阶需求权重（和为 1：初期快突破建立正反馈，圆满最难）
STAGE_WEIGHT = [0.15, 0.20, 0.30, 0.35]
# 每境基础挂机速率（/秒）
REALM_RATE = [1.0, 5.0, 25.0, 120.0, 600.0,
              3000.0, 1.5e4, 7.5e4, 3.75e5, 1.875e6]
# 境内四阶速率乘数
STAGE_RATE_MULT = [1.0, 1.3, 1.7, 2.2]

# ---- 展开成 40 阶表 ----
#: NEEDS[i]：完成第 i 阶所需修为；RATES[i]：第 i 阶的基础速率
NEEDS: List[float] = []
RATES: List[float] = []
for _g, _need in enumerate(REALM_NEED):
    for _s in range(4):
        NEEDS.append(_need * STAGE_WEIGHT[_s])
        RATES.append(REALM_RATE[_g] * STAGE_RATE_MULT[_s])
#: CUM[i]：进入第 i 阶所需累计修为；CUM[40] = 飞升
CUM: List[float] = [0.0]
for _n in NEEDS:
    CUM.append(CUM[-1] + _n)
MAX_STAGE = len(NEEDS) - 1          # 39 = 真仙圆满
ASCEND_CUM = CUM[-1]

# ------------------------------------------------------------------ #
# 机制参数
# ------------------------------------------------------------------ #
OFFLINE_EFFICIENCY = 0.70       # 离线效率（文档 §7.2：60~80%）
OFFLINE_THRESHOLD = 120.0       # 单次差值超过该值视为离线段（挂起/关机）
MAX_OFFLINE_SECONDS = 8 * 3600  # 收益上限：最多累积 8 小时
SECLUSION_AFTER = 15 * 60       # 系统空闲 15 分钟 → 闭关 ×1.5
COMFORT_WINDOW = 120.0          # 抚摸后 2 分钟内 → 抚慰 ×1.2
GLOOM_AFTER = 2 * 3600          # 超过 2 小时未互动 → 心境低落 ×0.8
WEAK_SECONDS = 600.0            # 突破失败虚弱时长（速率 ×0.5）
BREAK_COOLDOWN = 300.0          # 突破失败后的重试冷却
EPIPHANY_P_PER_SEC = 1.0 / 2700  # 顿悟概率（平均约 45 分钟一次）
EPIPHANY_REWARD_SEC = 600.0     # 顿悟奖励 = 当前速率 × 600 秒
BREAK_BASE = 0.88               # 突破基础成功率
BREAK_REALM_DECAY = 0.04        # 每高一个大境界衰减
BREAK_COMFORT_BONUS = 0.06      # 抚慰加成
BREAK_DUAL_BONUS = 0.04         # 双修加成
BREAK_FAIL_LOSS = 0.08          # 失败损失当前阶需求的 8%

# ---- 灵石（商店货币联动：修炼即报酬）----
STONE_ONLINE_PER_SEC = 1.0 / 45   # 在线挂机约 1.3 灵石/分钟
STONE_OFFLINE_PER_SEC = 1.0 / 90  # 离线减半
EPIPHANY_STONES = 25              # 顿悟额外灵石
BREAK_STONES_PER_REALM = 15       # 突破成功奖励 = (境序+1) × 该值

# ------------------------------------------------------------------ #
# 丹药修仙效果（键 = res/items/FanRenXiuXianZhuan 真实物品名，与商店同源）
#   exp: 一次性修为；buff: (持续秒, 速率倍率)；lucky: 下次突破成功率加成；
#   cleanse: 清除虚弱与突破冷却。服丹 hook：DyberPet.use_item → apply_pill_if_known
# ------------------------------------------------------------------ #
PILL_EFFECTS: Dict[str, dict] = {
    '清灵散': {'buff': (1800, 1.5), 'desc': '安神清心，30 分钟修炼速率 ×1.5'},
    '灵茶':   {'buff': (2700, 1.6), 'desc': '品茶悟道，45 分钟修炼速率 ×1.6'},
    '培元丹': {'buff': (2700, 1.8), 'desc': '培固元本，45 分钟修炼速率 ×1.8'},
    '辟谷丹': {'buff': (3600, 2.0), 'desc': '气足不思食，60 分钟修炼速率 ×2.0'},
    '洗髓丹': {'buff': (5400, 2.2), 'desc': '洗髓伐毛，90 分钟修炼速率 ×2.2'},
    '养精丹': {'exp': 600,   'desc': '温养精神 +600 修为'},
    '合气丹': {'exp': 1000,  'desc': '气机圆融 +1000 修为'},
    '灵果':   {'exp': 1300,  'desc': '灵气充盈 +1300 修为'},
    '回气丹': {'exp': 1800,  'desc': '回气调息 +1800 修为'},
    '定颜丹': {'exp': 2000,  'desc': '驻颜有术 +2000 修为'},
    '小还丹': {'exp': 3500,  'desc': '还精补脑 +3500 修为'},
    '金髓丸': {'exp': 2600,  'desc': '固本培元 +2600 修为'},
    '灵露':   {'exp': 4200,  'desc': '天露凝华 +4200 修为'},
    '黄龙丹': {'exp': 5500,  'desc': '增进功力 +5500 修为'},
    '筑基丹': {'exp': 9000,  'desc': '药力强劲 +9000 修为'},
    '降尘丹': {'lucky': 0.15, 'desc': '下次突破成功率 +15%'},
    '九曲灵参': {'exp': 16000, 'desc': '千年灵参 +16000 修为'},
    '补天丹': {'exp': 22000, 'cleanse': True, 'desc': '补天造化 +22000 修为，清除虚弱/冷却'},
    '万年灵乳': {'exp': 30000, 'cleanse': True, 'desc': '万年药力 +30000 修为，清除虚弱/冷却'},
}

# 炼丹配方（产出与商店同源的丹药；灵石成本约为店价 55 折，耗时秒）
ALCHEMY_RECIPES: List[tuple] = [
    ('清灵散', 30, 90),
    ('养精丹', 35, 120),
    ('合气丹', 45, 150),
    ('回气丹', 60, 210),
    ('培元丹', 55, 240),
    ('金髓丸', 60, 270),
    ('小还丹', 70, 300),
    ('黄龙丹', 80, 360),
    ('筑基丹', 120, 480),
    ('洗髓丹', 150, 600),
    ('降尘丹', 150, 720),
    ('万年灵乳', 360, 1200),
]

# ------------------------------------------------------------------ #
# 显示格式化（文档 §4.3：数值膨胀处理）
# ------------------------------------------------------------------ #
_UNITS = ['', '万', '亿', '兆', '京', '垓', '秭', '穰']


def fmt_exp(n) -> str:
    """修为/速率中文单位格式化：灵气→万→亿→兆→京→垓→秭→穰，超出用科学计数法。"""
    n = float(max(n, 0.0))
    if n < 1.0e4:
        return f'{n:.0f}'
    idx = int(math.log10(n)) // 4
    if idx >= len(_UNITS):
        return f'{n:.2e}'
    x = n / (1.0e4 ** idx)
    s = f'{x:.2f}'.rstrip('0').rstrip('.')
    return f'{s}{_UNITS[idx]}'


def stage_name(i: int) -> str:
    """阶索引 → '金丹 · 中期'。"""
    if i < 0:
        return REALMS[0] + ' · ' + STAGES[0]
    if i > MAX_STAGE:
        return '真仙 · 飞升'
    return f'{REALMS[i // 4]} · {STAGES[i % 4]}'


def idle_seconds() -> float:
    """系统空闲秒数（Win32 GetLastInputInfo）；非 Windows/失败返回 0。"""
    try:
        import ctypes

        class _LASTINPUTINFO(ctypes.Structure):
            _fields_ = [('cbSize', ctypes.c_uint), ('dwTime', ctypes.c_uint)]

        lii = _LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            tick = ctypes.windll.kernel32.GetTickCount()
            return max(0.0, (tick - lii.dwTime) / 1000.0)
    except Exception:  # noqa: BLE001
        pass
    return 0.0


# ------------------------------------------------------------------ #
# 核心服务
# ------------------------------------------------------------------ #
class CultivationCore:
    """修为核心状态机。线程安全（add_exp 可能来自其他插件线程）。"""

    def __init__(self, save_path: Optional[str] = None):
        self.save_path = save_path
        self.lock = threading.Lock()
        self.exp: float = 0.0            # 累计修为
        self.realm_idx: int = 0          # 当前阶索引（0..39；40=已飞升）
        self.last_tick: float = time.time()
        self.dual_on: bool = False       # 双修打坐开关
        self.weak_until: float = 0.0     # 虚弱截止时间戳
        self.last_touch: float = 0.0     # 最近一次被抚摸/点击
        self.break_after: float = 0.0    # 突破失败重试冷却截止
        self.auto_break: bool = True     # 达到阈值自动尝试突破
        self.stone_balance: int = 0      # 待入账灵石（插件兑现到商店货币）
        self._stone_frac: float = 0.0
        self.buff_until: float = 0.0     # 丹药 buff 截止时间戳
        self.buff_mult: float = 1.0      # 丹药速率倍率
        self.lucky_next: float = 0.0     # 下次突破成功率加成（一次性）
        self.rate_mods: Dict[str, List[float]] = {}  # 通用速率修改器 key→[截止,倍率]
        self.alchemy: Optional[dict] = None  # {'pill','start','end','cost'}
        self.log: List[dict] = []        # 状态日志（角色面板展示，上限 50 条）
        self.pending: List[dict] = []    # 待演出事件（面板/游戏路径产生，插件 drain）
        self.dirty: bool = False

    # ---- 状态查询 ----
    def stage(self) -> int:
        """当前阶索引（0..39；40 表示已飞升）。由突破决定，不随修为自动涨。"""
        return self.realm_idx

    def stage_progress(self) -> Tuple[float, float]:
        """当前阶内进度 (have, need)。已飞升返回 (1, 1)。"""
        i = self.stage()
        if i > MAX_STAGE:
            return 1.0, 1.0
        return max(0.0, self.exp - CUM[i]), NEEDS[i]

    def _multipliers(self, now: float, idle: float) -> Dict[str, float]:
        """桌宠状态 → 速率乘数（文档 §五：状态联动）。"""
        mults: Dict[str, float] = {}
        if self.dual_on:
            mults['双修打坐'] = 2.0
        elif idle >= SECLUSION_AFTER:
            mults['闭关'] = 1.5
        if now - self.last_touch <= COMFORT_WINDOW and self.last_touch > 0:
            mults['抚慰'] = 1.2
        if self.last_touch > 0 and now - self.last_touch > GLOOM_AFTER:
            mults['心境低落'] = 0.8
        if now < self.weak_until:
            mults['虚弱'] = 0.5
        if now < self.buff_until and self.buff_mult > 1.0:
            mults['丹药'] = self.buff_mult
        for key, (until, m) in self.rate_mods.items():
            if now < until:
                mults['伤势' if key == 'injury' else key] = m
        return mults

    def get_rate(self, now: Optional[float] = None,
                 idle: Optional[float] = None) -> Tuple[float, Dict[str, float], float]:
        """返回 (基础速率, 状态乘数表, 最终速率)。"""
        now = time.time() if now is None else now
        idle = idle_seconds() if idle is None else idle
        i = min(self.stage(), MAX_STAGE)
        base = RATES[i]
        mults = self._multipliers(now, idle)
        final = base
        for m in mults.values():
            final *= m
        return base, mults, final

    def breakthrough_chance(self, now: Optional[float] = None) -> float:
        """当前突破成功率（文档 §4.4）。"""
        now = time.time() if now is None else now
        i = self.stage()
        g = min(i // 4, len(REALMS) - 1)
        p = BREAK_BASE - g * BREAK_REALM_DECAY
        if self.last_touch > 0 and now - self.last_touch <= COMFORT_WINDOW:
            p += BREAK_COMFORT_BONUS
        if self.dual_on:
            p += BREAK_DUAL_BONUS
        p += self.lucky_next
        return min(0.95, max(0.45, p))

    # ---- 结算 ----
    def tick(self, now: Optional[float] = None,
             idle: Optional[float] = None) -> List[dict]:
        """时间戳差值结算。返回需要演出的事件列表。"""
        now = time.time() if now is None else now
        idle = idle_seconds() if idle is None else idle
        events: List[dict] = []
        with self.lock:
            delta = now - self.last_tick
            if delta < 0:
                # 时间回拨（文档 §7.2）：放弃本次结算，也不更新时间戳
                return events
            online = delta <= OFFLINE_THRESHOLD
            capped = min(delta, MAX_OFFLINE_SECONDS)
            eff = 1.0 if online else OFFLINE_EFFICIENCY
            _, mults, rate = self.get_rate(now, idle)
            gain = capped * rate * eff
            self.exp = min(self.exp + gain, ASCEND_CUM)
            self.last_tick = now
            self.dirty = True

            # 灵石产出（修炼即报酬；离线减半）——由插件兑现进商店货币
            self._stone_frac += capped * (STONE_ONLINE_PER_SEC if online
                                          else STONE_OFFLINE_PER_SEC)
            if self._stone_frac >= 1.0:
                self.stone_balance += int(self._stone_frac)
                self._stone_frac -= int(self._stone_frac)

            # 在线才可能顿悟（离线批量结算不出惊喜）
            if online and self.stage() <= MAX_STAGE and \
                    random.random() < EPIPHANY_P_PER_SEC * capped:
                bonus = rate * EPIPHANY_REWARD_SEC
                self.exp = min(self.exp + bonus, ASCEND_CUM)
                self.stone_balance += EPIPHANY_STONES
                self._log(now, 'epiphany',
                          f'顿悟！修为大涨 +{fmt_exp(bonus)}，灵石 +{EPIPHANY_STONES}')
                events.append({'type': 'epiphany', 'amount': bonus,
                               'stage': stage_name(self.stage())})

            # 炼丹完成检测（时间戳制，离线也照常完成）
            if self.alchemy and now >= self.alchemy['end']:
                pill = self.alchemy['pill']
                self.alchemy = None
                self._log(now, 'pill', f'丹炉开炉——「{pill}」炼成了！')
                events.append({'type': 'alchemy_done', 'pill': pill})

            # 突破检测（可多次跨越——大额 add_exp 后可能连破）
            events.extend(self._check_breakthrough_locked(now, idle))
        return events

    def _check_breakthrough_locked(self, now: float, idle: float) -> List[dict]:
        events: List[dict] = []
        while self.stage() <= MAX_STAGE and self.exp >= CUM[self.stage() + 1]:
            if not self.auto_break:
                break
            if now < self.break_after:
                break
            ev = self._try_breakthrough_locked(now)
            events.append(ev)
            if ev['type'] == 'break_fail':
                break  # 失败后修为已回退，退出循环等冷却
        return events

    def _try_breakthrough_locked(self, now: float) -> dict:
        i = self.realm_idx
        p = self.breakthrough_chance(now)
        self.lucky_next = 0.0   # 丹药幸运加成一次性消耗
        if random.random() < p:
            self.realm_idx = i + 1
            self.stone_balance += (min(i // 4, len(REALMS) - 1) + 1) * BREAK_STONES_PER_REALM
            self._log(now, 'breakthrough',
                      f'突破成功 → {stage_name(i + 1)}' if i + 1 <= MAX_STAGE
                      else '渡劫成功，飞升仙界！')
            return {'type': 'breakthrough', 'from': i, 'to': i + 1,
                    'from_stage': stage_name(i), 'stage': stage_name(i + 1),
                    'ascended': i + 1 > MAX_STAGE}
        # 失败：损失当前阶需求的一部分，进入虚弱 + 冷却
        lost = NEEDS[i] * BREAK_FAIL_LOSS
        self.exp = max(CUM[i], self.exp - lost)
        self.weak_until = now + WEAK_SECONDS
        self.break_after = now + BREAK_COOLDOWN
        self._log(now, 'break_fail',
                  f'突破失败（{stage_name(i)}），损失 {fmt_exp(lost)} 修为')
        return {'type': 'break_fail', 'stage': stage_name(i), 'lost': lost,
                'chance': p}

    def take_stones(self) -> int:
        """取走累积灵石（插件每 tick 兑现到商店货币）。禁用期间余额保留。"""
        with self.lock:
            n, self.stone_balance = self.stone_balance, 0
            if n:
                self.dirty = True
            return n

    def start_alchemy(self, pill: str, now: Optional[float] = None) -> dict:
        """开炉炼丹。灵石费用由插件经 pending 事件扣除（面板先做余额校验）。"""
        now = time.time() if now is None else now
        recipe = next((r for r in ALCHEMY_RECIPES if r[0] == pill), None)
        if recipe is None:
            return {'ok': False, 'msg': '没有这个丹方'}
        with self.lock:
            if self.alchemy and now < self.alchemy['end']:
                return {'ok': False, 'msg': '丹炉正忙着呢'}
            _, cost, seconds = recipe
            self.alchemy = {'pill': pill, 'start': now, 'end': now + seconds,
                            'cost': cost}
            self.dirty = True
            self._log(now, 'pill', f'开炉炼制「{pill}」（耗灵石 {cost}）')
            self.pending.append({'type': 'alchemy_start', 'pill': pill,
                                 'cost': cost, 'seconds': seconds})
            return {'ok': True, 'msg': f'开炉炼制「{pill}」'}

    def alchemy_status(self, now: Optional[float] = None) -> tuple:
        """返回 (状态, 丹名, 剩余秒)。状态 ∈ idle / refining / ready。"""
        now = time.time() if now is None else now
        with self.lock:
            if not self.alchemy:
                return 'idle', None, 0.0
            if now >= self.alchemy['end']:
                return 'ready', self.alchemy['pill'], 0.0
            return 'refining', self.alchemy['pill'], self.alchemy['end'] - now

    def apply_pill(self, name: str, now: Optional[float] = None) -> Optional[str]:
        """服用丹药的修仙效果（主程序 use_item hook 调用）。未知丹药返回 None。"""
        eff = PILL_EFFECTS.get(name)
        if not eff:
            return None
        now = time.time() if now is None else now
        with self.lock:
            if eff.get('cleanse'):
                self.weak_until = 0.0
                self.break_after = 0.0
                self.rate_mods.pop('injury', None)   # 补天类丹药顺带疗伤
            if 'exp' in eff:
                before = self.stage()
                self.exp = min(self.exp + eff['exp'], ASCEND_CUM)
                events = self._check_breakthrough_locked(now, 0.0)
                self.pending.extend(events)
                if before != self.stage() and not any(
                        e['type'] == 'breakthrough' for e in events):
                    self.pending.append({'type': 'breakthrough', 'from': before,
                                         'to': self.stage(),
                                         'from_stage': stage_name(before),
                                         'stage': stage_name(self.stage()),
                                         'ascended': self.stage() > MAX_STAGE})
            if 'buff' in eff:
                seconds, mult = eff['buff']
                if now >= self.buff_until or mult >= self.buff_mult:
                    self.buff_until = now + seconds
                    self.buff_mult = mult
                else:
                    self.buff_until = max(self.buff_until, now + seconds)
            if 'lucky' in eff:
                self.lucky_next = max(self.lucky_next, eff['lucky'])
            self.dirty = True
            self._log(now, 'pill', f'服下「{name}」：{eff["desc"]}')
            self.pending.append({'type': 'pill_used', 'pill': name,
                                 'desc': eff['desc']})
            return eff['desc']

    def _log(self, now: float, kind: str, text: str) -> None:
        """追加状态日志（锁内调用），上限 50 条。"""
        self.log.append({'t': now, 'kind': kind, 'text': text})
        if len(self.log) > 50:
            del self.log[:-50]

    def recent_logs(self, n: int = 20) -> List[dict]:
        with self.lock:
            return list(self.log[-n:])

    def try_breakthrough(self, now: Optional[float] = None) -> Optional[dict]:
        """手动突破（角色面板按钮）。只有修为达阈值才有效。"""
        now = time.time() if now is None else now
        with self.lock:
            if self.stage() > MAX_STAGE or self.exp < CUM[self.stage() + 1]:
                return None
            ev = self._try_breakthrough_locked(now)
            self.dirty = True
            self.pending.append(ev)   # 交由插件 drain 后统一演出
            return ev

    def add_exp(self, amount: float, reason: str = '',
                now: Optional[float] = None) -> List[dict]:
        """外部加修为（小游戏胜利等）。返回触发的事件（供调用方演出）。"""
        if amount <= 0:
            return []
        now = time.time() if now is None else now
        with self.lock:
            before = self.stage()
            self.exp = min(self.exp + float(amount), ASCEND_CUM)
            self.dirty = True
            events: List[dict] = []
            if reason:
                self._log(now, 'exp_gain', f'「{reason}」+{fmt_exp(amount)}')
                events.append({'type': 'exp_gain', 'amount': float(amount),
                               'reason': reason, 'stage': stage_name(self.stage())})
            # 大额修为可能直接跨阶——逐阶检测突破
            events.extend(self._check_breakthrough_locked(now, 0.0))
            if events and before != self.stage() and \
                    not any(e['type'] == 'breakthrough' for e in events):
                events.append({'type': 'breakthrough', 'from': before,
                               'to': self.stage(),
                               'from_stage': stage_name(before),
                               'stage': stage_name(self.stage()),
                               'ascended': self.stage() > MAX_STAGE})
            # 非插件路径（游戏联动/面板）产生的事件入待演队列，插件统一 drain 演出
            self.pending.extend(events)
        return events

    def drain_pending(self) -> List[dict]:
        """取走待演出事件（面板手动突破、游戏联动加修为等路径产生的）。"""
        with self.lock:
            out, self.pending = self.pending, []
            return out

    def mark_touch(self, now: Optional[float] = None) -> None:
        """被抚摸/点击：记录时间；双修被打断（文档：双方不动才算双修）。"""
        now = time.time() if now is None else now
        with self.lock:
            self.last_touch = now
            self.dual_on = False
            self.dirty = True

    def set_dual(self, on: bool) -> None:
        with self.lock:
            self.dual_on = bool(on)
            self.dirty = True

    def set_rate_modifier(self, key: str, multiplier: float, seconds: float,
                          now: Optional[float] = None) -> None:
        """通用速率修改器（冒险受伤等）：自动过期，只减速、永不倒扣已获修为。"""
        now = time.time() if now is None else now
        with self.lock:
            self.rate_mods[str(key)] = [now + float(seconds), float(multiplier)]
            self.dirty = True

    def injured(self, now: Optional[float] = None) -> bool:
        """是否处于受伤减速中（冒险归来的 debuff）。"""
        now = time.time() if now is None else now
        with self.lock:
            mod = self.rate_mods.get('injury')
            return bool(mod) and now < mod[0]

    # ---- 存档（文档 §7.3：延迟写入 + 退出强制保存）----
    def to_dict(self) -> dict:
        return {'exp': self.exp, 'realm_idx': self.realm_idx,
                'last_tick': self.last_tick,
                'dual_on': self.dual_on, 'weak_until': self.weak_until,
                'last_touch': self.last_touch, 'auto_break': self.auto_break,
                'stone_balance': self.stone_balance,
                'buff_until': self.buff_until, 'buff_mult': self.buff_mult,
                'lucky_next': self.lucky_next, 'alchemy': self.alchemy,
                'rate_mods': {k: list(v) for k, v in self.rate_mods.items()},
                'log': self.log[-50:]}

    def save(self, path: Optional[str] = None) -> None:
        path = path or self.save_path
        if not path:
            return
        with self.lock:
            data = self.to_dict()
            self.dirty = False
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, path)  # 原子替换，防存档损坏
        except Exception as e:  # noqa: BLE001
            print(f'[cultivation] save failed: {e!r}')

    def save_if_dirty(self, path: Optional[str] = None) -> None:
        if self.dirty:
            self.save(path)

    def load(self, path: Optional[str] = None) -> None:
        path = path or self.save_path
        if not path or not os.path.isfile(path):
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            with self.lock:
                self.exp = float(data.get('exp', 0.0))
                self.realm_idx = int(data.get('realm_idx', 0))
                self.last_tick = float(data.get('last_tick', time.time()))
                self.dual_on = bool(data.get('dual_on', False))
                self.weak_until = float(data.get('weak_until', 0.0))
                self.last_touch = float(data.get('last_touch', 0.0))
                self.auto_break = bool(data.get('auto_break', True))
                self.stone_balance = int(data.get('stone_balance', 0))
                self.buff_until = float(data.get('buff_until', 0.0))
                self.buff_mult = float(data.get('buff_mult', 1.0))
                self.lucky_next = float(data.get('lucky_next', 0.0))
                alch = data.get('alchemy')
                self.alchemy = dict(alch) if isinstance(alch, dict) else None
                rm = data.get('rate_mods')
                self.rate_mods = {
                    str(k): [float(v[0]), float(v[1])]
                    for k, v in (rm or {}).items()
                    if isinstance(v, (list, tuple)) and len(v) == 2}
                raw_log = data.get('log', [])
                self.log = [e for e in raw_log if isinstance(e, dict)][-50:]
                self.dirty = False
        except Exception as e:  # noqa: BLE001
            print(f'[cultivation] load failed: {e!r}')


# ------------------------------------------------------------------ #
# 模块级单例 + 便捷接口（供其他插件/主程序调用）
# ------------------------------------------------------------------ #
_core: Optional[CultivationCore] = None
_core_lock = threading.Lock()


def get_core(save_path: Optional[str] = None) -> CultivationCore:
    """惰性单例。首次创建时若给 save_path 则自动读档。"""
    global _core
    with _core_lock:
        if _core is None:
            _core = CultivationCore(save_path)
            if save_path:
                _core.load()
        elif save_path and _core.save_path is None:
            _core.save_path = save_path
            _core.load()
        return _core


def add_exp(amount, reason: str = '') -> Optional[List[dict]]:
    """全项目通用加修为入口：五子棋/斗地主胜利等只需一行。

    返回事件列表（可能含 breakthrough），调用方可用于演出；服务未初始化时
    会自动创建单例（存档路径由玩法插件首次启用时注入）。
    """
    try:
        return get_core().add_exp(float(amount), reason)
    except Exception as e:  # noqa: BLE001
        print(f'[cultivation] add_exp failed: {e!r}')
        return None


def apply_pill_if_known(name: str) -> Optional[str]:
    """主程序 use_item hook：已知修仙丹药则生效，返回效果描述（未知返回 None）。"""
    try:
        return get_core().apply_pill(name)
    except Exception as e:  # noqa: BLE001
        print(f'[cultivation] apply_pill failed: {e!r}')
        return None
