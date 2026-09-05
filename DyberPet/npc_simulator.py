# coding:utf-8
"""道友人生轨迹模拟器（规则驱动，零 LLM）。

设计来源：《桌宠修仙世界日志与道友人生模拟系统.md》§四/§五
- 每个 NPC 是有独立人生轨迹的模拟个体：会成长、抉择、结缘、结仇、得意、
  落魄、老死。**死亡让世界有重量**；**回响让当年的抉择多年后回来**。
- **需求驱动决策（§4.4）**：生存检查 → 紧急目标（复仇/延寿）→ 常规活动
  （性格 × 处境加权 + 新颖度去重）——不是掷骰子，是"性格→处境→抉择"的因果链。
- **事件效果 100% 代码结算**：事件表只声明效果区间与日志模板，数值由本模块
  掷骰，与修为核心服务同一铁律。
- **分层模拟（§9.1）**：活跃层每世界日完整模拟；休眠层每 10 日只推寿命修为；
  归档层（已陨落）不模拟只留传记。
- 纯逻辑无 Qt 依赖；境界名与主程序 cultivation_service 对齐（10 大境）。
"""
from __future__ import annotations

import random
from typing import Dict, List, Optional

try:
    from DyberPet.cultivation_service import REALMS
except Exception:  # noqa: BLE001
    REALMS = ['炼气', '筑基', '金丹', '元婴', '化神', '炼虚', '合体',
              '大乘', '渡劫', '真仙']

# ------------------------------------------------------------------ #
# 数值表（调参只动这里）
# ------------------------------------------------------------------ #
REALM_NAME = REALMS
REALM_LEVEL_NAME = ['初期', '中期', '后期', '圆满']

#: 各境界基础寿元（岁，index=境界）
LIFESPAN_BASE = [110, 160, 280, 550, 1100, 2200, 3500, 6000, 9000, 14000]
#: 突破到 index 境界时的寿元增补（收紧：高境也有尽头的可能，世界才有生死）
LIFESPAN_GAIN = [0, 40, 100, 250, 500, 900, 1200, 2000, 3000, 4000]
#: progress 攒满后，从当前境界突破的基础成功率（index=当前境界）
BREAK_CHANCE = [0.90, 0.75, 0.60, 0.45, 0.35, 0.30, 0.25, 0.20, 0.15, 0.12]
#: 突破失败时的陨落概率（index=当前境界；元婴起逆天而行有性命之虞）
BREAK_DEATH_CHANCE = [0.0, 0.0, 0.0, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08]

#: 日常活动池基础权重（性格/处境再调）
BASE_POOL_W = {'cultivate': 30, 'travel': 20, 'social': 16,
               'life': 14, 'fortune': 10, 'mishap': 6}

DAYS_PER_YEAR = 365          # 世界日/年
DORMANT_INTERVAL = 10        # 休眠层每 N 世界日简化模拟一次
RECOVERY_THRESHOLD = 0.35    # 血线以下强制疗伤
CRITICAL_HEALTH = 0.0        # 归零即陨落
#: 修为进度全局缩放（平衡旋钮）：事件表声明的 progress 视为相对强度，
#: 统一压缩后落账。另随境界衰减（(10-realm)/10）——境界越高进境越难，
#: 多数 NPC 会卡在某境界老死，而非人手一位真仙。
PROGRESS_SCALE = 0.05
DAILY_HEAL = 0.01            # 活跃层自然回血/日（日子总会好起来）

REL_TYPE_ORDER = [('sworn', 80), ('friend', 30), ('acquaint', -40),
                  ('enemy', -100)]     # (类型, 最低 affinity)


def realm_label(realm: int) -> str:
    """境界中文名 + 阶段（NPC 只有 progress，按四等分给阶段名）。"""
    r = max(0, min(int(realm), len(REALM_NAME) - 1))
    return REALM_NAME[r]


# ------------------------------------------------------------------ #
# NPC 生成
# ------------------------------------------------------------------ #
def gen_npc(npc_id: str, world_day: int, rng: random.Random,
            pools: dict, used_names: set) -> dict:
    """生成一个新 NPC（年轻散修起家；高境界者年龄相应拉大）。"""
    surname = rng.choice(pools.get('surnames', ['李']))
    given = rng.choice(pools.get('given', ['青玄']))
    name = surname + given
    while name in used_names:                    # 防重名
        given = rng.choice(pools.get('given', ['青玄']))
        name = surname + given
    used_names.add(name)

    realm = min(9, rng.choices(range(4), weights=[55, 28, 13, 4], k=1)[0])
    age = max(rng.randint(18, 50), realm * 22 + rng.randint(0, 24))
    talent = {k: rng.randint(15, 95) for k in
              ('comprehension', 'constitution', 'fortune', 'charisma')}
    return {
        'id': npc_id, 'name': name,
        'gender': rng.choice(['男', '女']),
        'birth_day': world_day - age * DAYS_PER_YEAR,
        'base_lifespan': int(LIFESPAN_BASE[realm] * rng.uniform(0.9, 1.1)),
        'realm': realm,
        'progress': round(rng.uniform(0.05, 0.6), 3),
        'root': rng.choice(pools.get('roots', ['金', '木', '水', '火', '土'])),
        'talent': talent,
        'health': round(rng.uniform(0.7, 1.0), 3),
        'mood': rng.randint(45, 75),
        'loc': rng.choice(pools.get('locations', ['青云山'])),
        'stones': rng.randint(50, 1500),
        'personality': {k: round(rng.random(), 2) for k in
                        ('ambition', 'caution', 'benevolence',
                         'greed', 'loyalty', 'temper')},
        'relations': [],
        'goals': [],
        'flags': [],
        'bio': [],
        'acts': [],
        'tier': 'dormant',
    }


# ------------------------------------------------------------------ #
# 模拟器
# ------------------------------------------------------------------ #
class NPCSimulator:
    """驱动 NPC 世界。所有随机走传入的 rng（模拟器可用种子复现）。"""

    def __init__(self, engine, rng: random.Random):
        self.engine = engine            # ContentEngine
        self.rng = rng

    # ------------------------------------------------------------------ #
    # 日常模拟（活跃层每世界日 / 休眠层每 N 日）
    # ------------------------------------------------------------------ #
    def simulate_day(self, npc: dict, world: dict, events: dict,
                     pools: dict, full: bool = True) -> List[dict]:
        """推进 NPC 一个世界日，返回产生的日志条目列表。"""
        rng = self.rng
        logs: List[dict] = []
        if not full:                                   # 休眠层：粗推进
            npc['progress'] = min(1.0, npc['progress'] + rng.uniform(0.004, 0.012))
            npc['health'] = min(1.0, npc['health'] + 0.01)
            self._check_lifespan(npc, world, logs)
            return logs

        # 1) 生存检查（最高优先级）
        if float(npc['health']) < RECOVERY_THRESHOLD:
            logs += self._run_event(events['by_id'].get('life_recuperate'),
                                    npc, world, events, pools)
            return logs
        npc['health'] = min(1.0, float(npc.get('health', 1)) + DAILY_HEAL)
        # 心情均值回归（大喜大悲终会归于平淡，防「心情甚好」刷屏）
        npc['mood'] = int(npc['mood'] + (60 - npc['mood']) * 0.02)
        remaining = int(npc['base_lifespan']) - self._age(npc, world)
        if 0 < remaining <= 5:
            logs += self._run_event(events['by_id'].get('life_seek_longevity'),
                                    npc, world, events, pools)
            return logs

        # 2) 紧急目标：复仇
        rev = self._revenge_goal(npc, world)
        if rev is not None and rng.random() < float(rev.get('priority', 0.5)) * 0.3:
            logs += self._attempt_revenge(npc, world, rev)
            return logs

        # 3) 常规活动：性格 × 处境 × 新颖度 加权抽池
        pool = self._pick_pool(npc, world)
        ev = self._pick_event(events['pools'].get(pool, []), events, rng)
        if ev is None:
            return logs
        npc['acts'] = (npc.get('acts', []) + [pool])[-5:]
        logs += self._run_event(ev, npc, world, events, pools)

        # 4) 突破结算（progress 由事件效果累积）
        logs += self._check_breakthrough(npc, world)
        self._check_lifespan(npc, world, logs)
        return logs

    # ------------------------------------------------------------------ #
    # 决策
    # ------------------------------------------------------------------ #
    def _pick_pool(self, npc: dict, world: dict) -> str:
        p = npc.get('personality', {})
        w = {}
        disaster = (world.get('last_world_event') or {}).get('until_day', 0) \
            > world.get('world_day', 0)
        for pool, base in BASE_POOL_W.items():
            bias = 0.0
            if pool == 'cultivate':
                bias += p.get('ambition', 0.5) * 10 + npc.get('realm', 0) * 1.5
            elif pool == 'travel':
                bias += (1 - p.get('caution', 0.5)) * 8
            elif pool == 'social':
                bias += p.get('benevolence', 0.5) * 8
            elif pool == 'fortune':
                bias += p.get('greed', 0.5) * 8 + p.get('ambition', 0.5) * 3
            elif pool == 'mishap':
                bias -= p.get('caution', 0.5) * 6
                if disaster:
                    bias += 18          # 灾劫期间凶险事件概率翻倍
            w[pool] = max(1.0, base + bias)
        acts = npc.get('acts', [])
        if acts and acts[-1] in w:      # 新颖度：连续同活动降权
            w[acts[-1]] *= 0.6
        return self.rng.choices(list(w.keys()), weights=list(w.values()), k=1)[0]

    def _pick_event(self, ids: List[str], events: dict,
                    rng: random.Random) -> Optional[dict]:
        by_id = events['by_id']
        cands = [by_id[i] for i in ids if i in by_id]
        if not cands:
            return None
        ws = [max(0.1, float(e.get('weight', 10))) for e in cands]
        return rng.choices(cands, weights=ws, k=1)[0]

    # ------------------------------------------------------------------ #
    # 事件结算（效果 100% 代码）
    # ------------------------------------------------------------------ #
    def _run_event(self, ev: Optional[dict], npc: dict, world: dict,
                   events: dict, pools: dict) -> List[dict]:
        if ev is None:
            return []
        rng = self.rng
        eff = ev.get('effects') or {}
        ctx = {'name': npc['name'], 'loc': npc.get('loc', ''),
               'realm': realm_label(npc.get('realm', 0)),
               'root': npc.get('root', ''),
               'beast': rng.choice(pools.get('beasts', ['妖兽'])),
               'item': rng.choice(pools.get('items', ['灵物'])),
               'person': rng.choice(pools.get('persons', ['路人修士'])),
               'sect': rng.choice(pools.get('sects', ['青云宗']))}

        # 特例：寻求延寿（寿元将尽期的拼命一搏，§4.3）
        if ev.get('id') == 'life_seek_longevity':
            p = 0.5 + npc['talent'].get('fortune', 50) / 250.0
            logs: List[dict] = []
            if rng.random() < p:
                gain = rng.randint(30, 90)
                npc['base_lifespan'] = int(npc['base_lifespan']) + gain
                logs.append(self._log(
                    world, 2, f"{npc['name']}自知寿元无多，遍寻延寿之法，"
                              f"竟真教它续了{gain}年寿数。", who=npc['id']))
            else:
                logs.append(self._log(
                    world, 1, f"{npc['name']}四处求延寿之方，皆无功效，"
                              f"只得认命。", who=npc['id']))
            return logs

        # 数值效果
        if 'progress' in eff:
            lo, hi = eff['progress']
            gain = rng.uniform(lo, hi) * PROGRESS_SCALE \
                * max(0.15, (10 - npc.get('realm', 0)) / 10.0) \
                * (0.6 + npc['talent']['comprehension'] / 150)
            npc['progress'] = min(1.2, float(npc.get('progress', 0)) + gain)
        if 'health' in eff:
            lo, hi = eff['health']
            npc['health'] = max(CRITICAL_HEALTH,
                                min(1.0, float(npc.get('health', 1)) + rng.uniform(lo, hi)))
        if 'mood' in eff:
            lo, hi = eff['mood']
            npc['mood'] = max(0, min(100, int(npc.get('mood', 60)) + rng.randint(int(lo), int(hi))))
        if 'stones' in eff:
            lo, hi = eff['stones']
            npc['stones'] = max(0, int(npc.get('stones', 0)) + rng.randint(int(lo), int(hi)))
        for flag in eff.get('flags', []) or []:
            if flag not in npc['flags']:
                npc['flags'].append(flag)
        if 'loc' in eff and rng.random() < 0.5:
            npc['loc'] = rng.choice(pools.get('locations', ['青云山']))

        # 关系效果（与既有关系者或随机 NPC）
        rel_logs: List[dict] = []
        rel = eff.get('rel')
        if rel:
            rel_logs += self._apply_relation(npc, world, rel, ctx)

        # 回响种子
        echo = ev.get('echo')
        if echo:
            delay = echo.get('delay', [30, 90])
            world.setdefault('pending_echoes', []).append({
                'day': world['world_day'] + rng.randint(int(delay[0]), int(delay[1])),
                'event': echo['event'], 'npc': npc['id']})

        # 日志
        logs: List[dict] = []
        line = self.engine.emit(ev, npc, ctx, rng)
        if line:
            logs.append(self._log(world, ev.get('level', 1), line,
                                  who=npc['id']))
        if float(npc.get('health', 1)) <= CRITICAL_HEALTH:
            cause = ev.get('death_cause') or '意外陨落'
            logs += self._die(npc, world, self.engine.render(cause, ctx),
                              killer=None)
        return logs + rel_logs

    def _apply_relation(self, npc: dict, world: dict, kind: str,
                        ctx: dict) -> List[dict]:
        """社交事件的关系落点。befriend/enemy/aid。"""
        rng = self.rng
        others = [n for n in world['npcs'].values() if n['id'] != npc['id']]
        if not others:
            return []
        other = rng.choice(others)
        if kind == 'befriend':
            delta = rng.randint(12, 30)
        elif kind == 'enemy':
            delta = -rng.randint(25, 50)
        else:                                   # aid 施恩
            delta = rng.randint(8, 18)
        self._bump_relation(npc, other['id'], delta)
        self._bump_relation(other, npc['id'], delta)
        verb = {'befriend': '与{person}一见如故，互留了传讯玉简',
                'enemy': '与{person}起了冲突，结下梁子',
                'aid': '出手帮了{person}一把，对方感激不尽'}.get(kind, '')
        if not verb:
            return []
        sub = ctx.copy()
        sub['person'] = other['name']
        line = f"{npc['name']}" + verb.format(**sub)
        lvl = 3 if (self._rel_with_player(other) or self._rel_with_player(npc)) else 1
        return [self._log(world, lvl, line, who=npc['id'])]

    # ------------------------------------------------------------------ #
    # 突破 / 寿元 / 死亡
    # ------------------------------------------------------------------ #
    def _check_breakthrough(self, npc: dict, world: dict) -> List[dict]:
        if float(npc.get('progress', 0)) < 1.0:
            return []
        npc['progress'] = 0.0
        rng = self.rng
        realm = int(npc.get('realm', 0))
        if realm >= 9:
            npc['bio'].append(f"{world['world_day']}日：修为登峰造极")
            return []
        p = BREAK_CHANCE[realm] + npc['talent']['comprehension'] / 500 \
            + npc['personality'].get('caution', 0.4) * 0.08
        if rng.random() < min(0.95, p):
            npc['realm'] = realm + 1
            npc['base_lifespan'] += int(LIFESPAN_GAIN[realm + 1] * rng.uniform(0.9, 1.1))
            npc['mood'] = min(100, npc['mood'] + 20)
            npc['bio'].append(f"突破{realm_label(npc['realm'])}")
            with_player = self._rel_with_player(npc)
            line = (f"{npc['name']}闭关功成，突破至{realm_label(npc['realm'])}期！"
                    + ('你与它有一段旧交，听闻消息颇为感慨。' if with_player else ''))
            return [self._log(world, 3 if with_player else 2, line, who=npc['id'])]
        npc['progress'] = 0.6
        npc['health'] = max(0.1, float(npc.get('health', 1)) - 0.1)
        npc['mood'] = max(0, npc['mood'] - 15)
        logs = [self._log(world, 1,
                          f"{npc['name']}冲击{realm_label(realm + 1)}未成，"
                          f"气息紊乱，闭关调养。", who=npc['id'])]
        # 元婴起逆天而行有性命之虞（突破陨落，世界才有重量）
        if rng.random() < BREAK_DEATH_CHANCE[realm]:
            logs += self._die(npc, world,
                              f"冲击{realm_label(realm + 1)}失败，走火入魔而陨")
        return logs

    def _check_lifespan(self, npc: dict, world: dict, logs: List[dict]):
        if self._age(npc, world) >= int(npc['base_lifespan']):
            logs.extend(self._die(npc, world, '坐化'))

    def _die(self, npc: dict, world: dict, cause: str,
             killer: Optional[str] = None) -> List[dict]:
        """死亡 + 连锁（亲友哀恸/与玩家旧情 → 高级别日志）。"""
        if npc['id'] in world.get('dead_index', {}):
            return []
        world.setdefault('dead_index', {})[npc['id']] = {
            'id': npc['id'], 'name': npc['name'],
            'realm': realm_label(npc.get('realm', 0)),
            'age': self._age(npc, world), 'day': world['world_day'],
            'cause': cause, 'bio': list(npc.get('bio', []))[-8:]}
        world['npcs'].pop(npc['id'], None)
        ids = world.get('active_ids', [])
        if npc['id'] in ids:
            ids.remove(npc['id'])
        logs: List[dict] = []
        # 与玩家的旧情 → 重量级日志（文档 §4.7 示例句式）
        rel = self._rel_with_player(npc)
        if rel is not None:
            age = self._age(npc, world)
            line = {'old_friend':
                    f"与你相识多年的故交{npc['name']}，已于近日{cause}，享年{age}岁。",
                    'old_rival':
                    f"与你纠缠半生的宿敌{npc['name']}，已于近日{cause}，享年{age}岁。",
                    }.get(rel['type'],
                          f"你曾有一面之缘的散修{npc['name']}，"
                          f"已于近日{cause}，享年{age}岁。")
            logs.append(self._log(world, 3, line, who=npc['id']))
        # NPC 间连锁：亲友哀恸、仇敌称快
        for other in world['npcs'].values():
            for r in other.get('relations', []):
                if r['target'] != npc['id']:
                    continue
                if r['affinity'] > 30:
                    r['affinity'] = max(-100, r['affinity'] - 20)
                    if 'bereaved' not in other['flags']:
                        other['flags'].append('bereaved')
                    other['mood'] = max(0, other['mood'] - 25)
                    logs.append(self._log(
                        world, 1,
                        f"{other['name']}闻得{npc['name']}{cause}的噩耗，郁郁数日。",
                        who=other['id']))
        return logs

    # ------------------------------------------------------------------ #
    # 复仇
    # ------------------------------------------------------------------ #
    def _revenge_goal(self, npc: dict, world: dict) -> Optional[dict]:
        for g in npc.get('goals', []):
            if g.get('type') == 'revenge':
                t = world['npcs'].get(g.get('target'))
                if t is not None:
                    return g
        return None

    def _attempt_revenge(self, npc: dict, world: dict, goal: dict) -> List[dict]:
        rng = self.rng
        foe = world['npcs'].get(goal['target'])
        if foe is None:
            return []
        p = (0.5 + (int(npc['realm']) - int(foe['realm'])) * 0.15
             + float(npc.get('health', 1)) * 0.1
             - npc['personality'].get('caution', 0.4) * 0.1)
        npc['acts'] = (npc.get('acts', []) + ['revenge'])[-5:]
        if rng.random() < max(0.1, min(0.9, p)):
            foe['health'] = float(foe.get('health', 1)) - rng.uniform(0.3, 0.5)
            npc['goals'].remove(goal)
            self._bump_relation(npc, foe['id'], -100)
            self._bump_relation(foe, npc['id'], -100)
            line = f"{npc['name']}寻到{foe['name']}，为新仇旧恨大打出手，占得上风。"
            logs = [self._log(world, 2, line, who=npc['id'])]
            if foe['health'] <= 0:
                logs += self._die(foe, world, '死于仇家之手', killer=npc['id'])
            return logs
        npc['health'] = max(0.1, float(npc.get('health', 1)) - rng.uniform(0.2, 0.35))
        return [self._log(world, 2,
                          f"{npc['name']}寻{foe['name']}寻仇，反被压着打，"
                          f"负伤遁走。", who=npc['id'])]

    # ------------------------------------------------------------------ #
    # 关系维护（年度衰减 + 跃迁）
    # ------------------------------------------------------------------ #
    def yearly_relations(self, world: dict):
        for npc in world['npcs'].values():
            for r in npc.get('relations', []):
                r['affinity'] = max(-100, min(100, int(r['affinity'] * 0.97)))
                aff = r['affinity']
                if aff >= 80:
                    r['type'] = 'sworn'
                elif aff >= 30:
                    r['type'] = 'friend'
                elif aff > -40:
                    r['type'] = 'acquaint' if r['type'] not in (
                        'old_friend', 'old_rival', 'master', 'disciple') else r['type']
                else:
                    r['type'] = 'enemy' if r['type'] != 'old_rival' else r['type']

    # ------------------------------------------------------------------ #
    # 回响（跨越时间的报应/报恩）
    # ------------------------------------------------------------------ #
    def resolve_echoes(self, world: dict, events: dict, pools: dict) -> List[dict]:
        logs: List[dict] = []
        pending = world.get('pending_echoes', [])
        due = [e for e in pending if e['day'] <= world['world_day']]
        if not due:
            return []
        world['pending_echoes'] = [e for e in pending if e not in due]
        for item in due:
            npc = world['npcs'].get(item.get('npc'))
            ev = events['by_id'].get(item.get('event'))
            if ev is None:
                continue
            if npc is None:                 # 回响落空（人已陨落）——历史不改写
                continue
            logs += self._run_event(ev, npc, world, events, pools)
        return logs

    # ------------------------------------------------------------------ #
    # 工具
    # ------------------------------------------------------------------ #
    @staticmethod
    def _age(npc: dict, world: dict) -> int:
        return max(0, (world['world_day'] - int(npc.get('birth_day', 0)))
                   // DAYS_PER_YEAR)

    @staticmethod
    def _bump_relation(npc: dict, target_id: str, delta: int):
        for r in npc.get('relations', []):
            if r['target'] == target_id:
                r['affinity'] = max(-100, min(100, r['affinity'] + delta))
                return
        npc.setdefault('relations', []).append(
            {'target': target_id, 'type': 'acquaint', 'affinity': delta})

    @staticmethod
    def _rel_with_player(npc: dict) -> Optional[dict]:
        for r in npc.get('relations', []):
            if r['target'] == 'player':
                return r
        return None

    @staticmethod
    def _log(world: dict, level: int, text: str, who: Optional[str] = None) -> dict:
        return {'day': world['world_day'], 'cat': 'friend', 'level': level,
                'text': text, 'who': who}
