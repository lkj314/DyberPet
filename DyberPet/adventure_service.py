# coding:utf-8
"""世界冒险核心服务（常驻，纯逻辑，无 Qt 依赖）。

设计来源：`桌宠世界冒险系统项目介绍.md`
- **数值层铁律（§3.1）**：秘境收益、成功率、胜负结果、受伤判定 100% 由本模块
  掷骰子决定，LLM（叙事层）只负责把结果讲成故事，绝不参与数值。
- **时间戳差值结算（§6.1）**：复用修为系统模式，不跑秒级定时器；天然支持离线
  （关机期间冒险照常推进），时间回拨直接放弃（不推进也不更新时间戳），
  单次差值 > 24 小时按 24 小时截断。
- **与修为系统的接口（§6.3）**：归来由插件调 `add_exp` 注入修为；受伤经
  `cultivation_service.set_rate_modifier('injury', ...)` 挂"减速 debuff"
  （只减速、永不倒扣已获修为）；小游戏胜利经 `grant_gaming_buff()` 给历练 buff。
- **常驻 vs 缺席（§1.3）**：本体离场（隐藏桌宠 + 道韵分身浮层）由玩法插件负责，
  本模块只维护状态机与传讯符时刻表（均匀分布 25%/50%/75%…，绝不剧透结果）。
- 秘境数值表在插件侧 `plugins/adventure/realms.py`（纯数据）；本模块只接收
  打包好的 spec 参数，保持 core 不反向依赖插件。
"""
from __future__ import annotations

import json
import os
import random
import threading
import time
from typing import Dict, List, Optional

try:
    import DyberPet.settings as _settings
except Exception:  # noqa: BLE001
    _settings = None

# ------------------------------------------------------------------ #
# 机制参数（调参只动这里）
# ------------------------------------------------------------------ #
SUCCESS_REALM_BONUS = 0.06   # 自身境界每高秘境要求一境的加成
INJURY_PENALTY = 0.10        # 带伤成功率惩罚
SUCCESS_MIN, SUCCESS_MAX = 0.15, 0.95   # 保底 15%，封顶 95%（文档 §4.4）
MAX_DELTA = 24 * 3600        # 单次结算差值上限（异常跳跃截断）
ONLINE_THRESHOLD = 120.0     # 差值超过该值视为离线段

#: 结果档 → 收益乘数（文档 §4.3：失败惩罚必须轻，只减速不倒扣）
OUTCOME_MULT = {'大胜': 1.2, '小胜': 1.0, '险胜': 0.6, '失利': 0.2, '重伤': 0.0}
#: 结果档 → 受伤 (速率倍率, 持续秒)；表现是修为速率临时降低，自动恢复
INJURY_BY_OUTCOME = {'险胜': (0.8, 2 * 3600),
                     '失利': (0.8, 4 * 3600),
                     '重伤': (0.6, 6 * 3600)}
WIN_OUTCOMES = ('大胜', '小胜', '险胜')

GAMING_BUFF_KEY = 'gaming'
GAMING_BUFF_SECONDS = 2 * 3600
GAMING_BUFF_BONUS = 0.10     # 小游戏胜利：历练成功率 +10%，持续 2 小时

STAY_P_PER_SEC = 1.0 / 2400  # 留守事件概率（冒险期间平均约 40 分钟一次）
PILL_FIND_CHANCE = 0.35      # 大胜/小胜时寻得丹药的概率
RECORDS_MAX = 20             # 历练志保留条数（文档 §6.4）


def compute_success(spec: dict, self_group: int, buff_bonus: float = 0.0,
                    injured: bool = False) -> float:
    """最终成功率（文档 §4.4 公式，唯一实现，UI 预估与结算共用）。"""
    p = (float(spec['base_success'])
         + (int(self_group) - int(spec['req'])) * SUCCESS_REALM_BONUS
         + float(buff_bonus)
         + float(spec.get('risk_success', 0.0)))
    if injured:
        p -= INJURY_PENALTY
    return min(SUCCESS_MAX, max(SUCCESS_MIN, p))


class AdventureService:
    """冒险状态机。线程安全（dispatch 可能来自面板线程）。"""

    def __init__(self, save_path: Optional[str] = None):
        self.save_path = save_path
        self.lock = threading.Lock()
        self.state: Optional[dict] = None   # away 状态数据；None=在家
        self.buffs: Dict[str, list] = {}    # key → [截止时间戳, 加成]
        self.records: List[dict] = []       # 历练志（最近 N 条）
        self.last_tick: float = time.time()
        self.dirty: bool = False

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #
    @staticmethod
    def _self_group() -> int:
        """自身境界序（0..9）；读不到修为核心时按炼气处理。"""
        try:
            from DyberPet.cultivation_service import get_core
            core = get_core()
            return min(max(core.stage(), 0) // 4, 10)   # 飞升按 10（压制加成封顶）
        except Exception:  # noqa: BLE001
            return 0

    @staticmethod
    def _injured(now: float) -> bool:
        try:
            from DyberPet.cultivation_service import get_core
            return get_core().injured(now)
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _message_count() -> int:
        """传讯符数量（插件设置 message_count，1~5，默认 3）。"""
        try:
            v = int(_settings.plugins_settings.get('adventure', {})
                    .get('message_count', 3))
            return min(5, max(1, v))
        except Exception:  # noqa: BLE001
            return 3

    # ------------------------------------------------------------------ #
    # 派出 / 结算
    # ------------------------------------------------------------------ #
    def dispatch(self, spec: dict, skeleton: dict,
                 now: Optional[float] = None) -> dict:
        """派出历练。spec 数值参数由插件侧 realms.build_spec 打包，skeleton 为
        事件模板骨架（events.pick）。结果在派出时一次掷定（防反复刷）。"""
        now = time.time() if now is None else now
        with self.lock:
            if self.state is not None:
                return {'ok': False, 'msg': '道友正在历练中，尚未归来'}
            self_group = self._self_group()
            if self_group < int(spec['req']):
                return {'ok': False,
                        'msg': f"境界不足：{spec['name']}需达到 "
                               f"{spec.get('req_name', '更高境界')}才能涉足"}
            p = compute_success(spec, self_group, self._buff_bonus_locked(now),
                                self._injured(now))
            # ---- 一次掷定结果与收益（文档 §3.1 铁律）----
            r = random.random()
            if r < p:
                r2 = random.random()
                outcome = '大胜' if r2 < 0.25 else ('小胜' if r2 < 0.75 else '险胜')
            else:
                outcome = '重伤' if random.random() < 0.10 else '失利'
            mult = OUTCOME_MULT[outcome]
            scale = float(spec['reward_mult']) * float(spec.get('risk_reward', 1.0))
            exp = int(float(spec['exp_base']) * scale * mult)
            stones = int(float(spec['stone_base']) * scale * mult)
            pill = None
            if outcome in WIN_OUTCOMES and spec.get('pill_pool') and \
                    random.random() < PILL_FIND_CHANCE:
                pill = random.choice(list(spec['pill_pool']))
            injury = INJURY_BY_OUTCOME.get(outcome)   # (速率倍率, 秒) or None

            n_msg = self._message_count()
            duration = float(spec['duration'])
            times = [duration * k / (n_msg + 1) for k in range(1, n_msg + 1)]
            self.state = {
                'spec': dict(spec), 'skeleton': dict(skeleton),
                'start': now, 'last_tick': now, 'elapsed': 0.0,
                'duration': duration, 'p': p,
                'outcome': outcome, 'exp': exp, 'stones': stones,
                'pill': pill, 'injury': list(injury) if injury else None,
                'talisman_times': times, 'sent': 0, 'talisman_texts': [],
            }
            self.last_tick = now   # 派出即对齐服务时钟（防历史差值误判回拨）
            self.dirty = True
            mins = int(duration // 60)
            eta = f'{mins // 60}小时{mins % 60}分' if mins >= 60 else f'{mins}分钟'
            return {'ok': True, 'msg': f"已赴「{spec['name']}」历练，"
                                       f"预计 {eta} 后归来", 'success': p}

    def tick(self, now: Optional[float] = None) -> List[dict]:
        """时间戳差值结算：传讯符到点推送、留守事件、归来判定。"""
        now = time.time() if now is None else now
        events: List[dict] = []
        with self.lock:
            delta = now - self.last_tick
            if delta < 0:
                return events          # 时间回拨：放弃本次（文档 §6.2）
            offline = delta > ONLINE_THRESHOLD
            self.last_tick = now
            if self.state is None:
                return events
            capped = min(delta, MAX_DELTA)
            st = self.state
            st['elapsed'] += capped
            self.dirty = True

            # 传讯符到点（均匀分布；文本由插件层拼装后经 note_talisman 回填）
            while st['sent'] < len(st['talisman_times']) and \
                    st['elapsed'] >= st['talisman_times'][st['sent']]:
                idx = st['sent']
                st['sent'] += 1
                events.append({'type': 'talisman', 'idx': idx,
                               'total': len(st['talisman_times']),
                               'name': st['spec']['name'],
                               'skeleton': dict(st['skeleton']),
                               'offline': offline})

            # 留守事件（仅在线；频率低、错过无惩罚——文档 §5.3）
            if not offline and random.random() < STAY_P_PER_SEC * capped:
                kind = random.choices(
                    ['visitor', 'beast', 'fortune'], weights=[4, 3, 2])[0]
                ev = {'type': 'stay', 'kind': kind}
                if kind == 'visitor':
                    ev['gift_stones'] = random.randint(4, 10)
                elif kind == 'fortune':
                    ev['gift_exp'] = max(50, int(st['spec']['exp_base'] * 0.05))
                events.append(ev)

            # 归来判定
            if st['elapsed'] >= st['duration']:
                result = {
                    'name': st['spec']['name'],
                    'outcome': st['outcome'],
                    'exp': st['exp'], 'stones': st['stones'],
                    'pill': st['pill'],
                    'injury': st['injury'],
                    'success': st['p'],
                    'skeleton': st['skeleton'],
                    'duration': st['duration'],
                }
                record = {'t': now, 'name': st['spec']['name'],
                          'outcome': st['outcome'], 'exp': st['exp'],
                          'stones': st['stones'], 'pill': st['pill'],
                          'offline': offline, 'story': ''}
                self.records.append(record)
                if len(self.records) > RECORDS_MAX:
                    del self.records[:-RECORDS_MAX]
                self.state = None
                events.append({'type': 'return', 'result': result,
                               'offline': offline})
        return events

    # ------------------------------------------------------------------ #
    # 查询 / 回填
    # ------------------------------------------------------------------ #
    def status(self, now: Optional[float] = None) -> dict:
        """当前状态快照（角色面板历练页 / 道韵分身展示用）。"""
        now = time.time() if now is None else now
        with self.lock:
            if self.state is None:
                return {'state': 'idle', 'buffs': self._buffs_snapshot(now)}
            st = self.state
            texts = [t for t in st['talisman_texts'] if t]
            return {
                'state': 'away',
                'name': st['spec']['name'],
                'duration': st['duration'],
                'elapsed': st['elapsed'],
                'remain': max(0.0, st['duration'] - st['elapsed']),
                'sent': st['sent'],
                'total': len(st['talisman_times']),
                'talisman_texts': texts,
                'latest_talisman': texts[-1] if texts else '',
                'buffs': self._buffs_snapshot(now),
            }

    def _buffs_snapshot(self, now: float) -> List[dict]:
        return [{'key': k, 'remain': max(0.0, v[0] - now), 'bonus': v[1]}
                for k, v in self.buffs.items() if now < v[0]]

    def note_talisman(self, idx: int, text: str) -> None:
        """插件把传讯符文案（预设/LLM）回填进状态，供面板展示。"""
        with self.lock:
            if self.state is not None and 0 <= idx < len(self.state['talisman_texts']) + 1:
                while len(self.state['talisman_texts']) <= idx:
                    self.state['talisman_texts'].append('')
                self.state['talisman_texts'][idx] = str(text)
                self.dirty = True

    def update_last_record_story(self, text: str) -> None:
        """归来叙事生成后回填进历练志最新一条（LLM 异步完成时）。"""
        text = str(text or '').strip()
        if not text:
            return
        with self.lock:
            if self.records:
                self.records[-1]['story'] = text
                self.dirty = True

    # ------------------------------------------------------------------ #
    # 历练 buff（小游戏胜利等）
    # ------------------------------------------------------------------ #
    def add_buff(self, key: str, seconds: float, bonus: float,
                 now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        with self.lock:
            cur = self.buffs.get(key)
            until = now + float(seconds)
            # 同名 buff 取更高加成、更晚截止
            if cur and now < cur[0]:
                until = max(until, cur[0])
                bonus = max(float(bonus), cur[1])
            self.buffs[key] = [until, float(bonus)]
            self.dirty = True

    def _buff_bonus_locked(self, now: float) -> float:
        self.buffs = {k: v for k, v in self.buffs.items() if now < v[0]}
        return sum(v[1] for v in self.buffs.values())

    def buff_bonus(self, now: Optional[float] = None) -> float:
        now = time.time() if now is None else now
        with self.lock:
            return self._buff_bonus_locked(now)

    def recent_records(self, n: int = 20) -> List[dict]:
        with self.lock:
            return list(self.records[-n:])

    # ------------------------------------------------------------------ #
    # 存档（延迟写入 + 退出强制保存）
    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict:
        now = time.time()
        return {'state': self.state, 'records': self.records,
                'buffs': {k: v for k, v in self.buffs.items() if now < v[0]},
                'last_tick': self.last_tick}

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
            os.replace(tmp, path)
        except Exception as e:  # noqa: BLE001
            print(f'[adventure] save failed: {e!r}')

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
                st = data.get('state')
                self.state = dict(st) if isinstance(st, dict) else None
                if self.state is not None:
                    # last_tick 恢复为读档时刻，离线时长由本次 tick 差值体现
                    self.state['last_tick'] = time.time()
                rec = data.get('records')
                self.records = [r for r in rec if isinstance(r, dict)][-RECORDS_MAX:]
                buffs = data.get('buffs')
                self.buffs = {str(k): [float(v[0]), float(v[1])]
                              for k, v in (buffs or {}).items()
                              if isinstance(v, (list, tuple)) and len(v) == 2}
                self.last_tick = time.time()
                self.dirty = False
        except Exception as e:  # noqa: BLE001
            print(f'[adventure] load failed: {e!r}')


# ------------------------------------------------------------------ #
# 模块级单例 + 便捷接口
# ------------------------------------------------------------------ #
_svc: Optional[AdventureService] = None
_svc_lock = threading.Lock()


def get_service(save_path: Optional[str] = None) -> AdventureService:
    """惰性单例。首次创建时若给 save_path 则自动读档。"""
    global _svc
    with _svc_lock:
        if _svc is None:
            _svc = AdventureService(save_path)
            if save_path:
                _svc.load()
        elif save_path and _svc.save_path is None:
            _svc.save_path = save_path
            _svc.load()
        return _svc


def grant_gaming_buff() -> None:
    """小游戏胜利 → 历练 buff（成功率 +10%，2 小时）。

    叙事："与你对弈一场，心神通明，此次历练当有所得。"
    """
    try:
        get_service().add_buff(GAMING_BUFF_KEY, GAMING_BUFF_SECONDS,
                               GAMING_BUFF_BONUS)
    except Exception as e:  # noqa: BLE001
        print(f'[adventure] grant_gaming_buff failed: {e!r}')


def is_away() -> bool:
    """桌宠是否正在外出历练（其他插件据此抑制桌宠气泡/动作演出）。"""
    try:
        return get_service().status()['state'] == 'away'
    except Exception:  # noqa: BLE001
        return False
