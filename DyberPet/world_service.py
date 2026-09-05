# coding:utf-8
"""修仙世界核心服务（常驻，纯逻辑，无 Qt 依赖）。

设计来源：《桌宠修仙世界日志与道友人生模拟系统.md》§二/§八/§九/§十
- **三条日志线**：主线·游历直播（adventure 接入，预留）/ 支线·道友名帖
  （npc_simulator）/ 环境·天下大事（本模块低频掷骰）。
- **双时钟（§8.1）**：现实时间戳差值 → 世界日批量补算，关机期间世界照常
  转动——「离开一段时间，世界真的变了」是惊喜感最强来源。
- **内容铁律**：日志 100% 由规则系统+模板产生（content_engine），LLM 只可
  选润色表达层，绝不参与数值与内容生产。
- **分层模拟（§9.1）**：活跃层每世界日完整模拟（≤50）；休眠层每 10 日粗推进；
  归档层只留传记。超长补算对 L1 琐碎日志按比例采样。
- **注意力控制（§10.2）**：L1 静默入库、L2 通知、L3 气泡——绝不弹窗。
  插件经 drain_notable() 取走 L2+ 事件做演出。
- 存档 CONFIGDIR/data/world_state.json；时间回拨放弃推进；与
  cultivation_service / adventure_service 同一工程模式。
"""
from __future__ import annotations

import json
import os
import random
import threading
import time
from typing import Dict, List, Optional

from .content_engine import ContentEngine
from .npc_simulator import NPCSimulator, gen_npc, DAYS_PER_YEAR

try:
    import DyberPet.settings as _settings
except Exception:  # noqa: BLE001
    _settings = None

# ------------------------------------------------------------------ #
# 机制参数（调参只动这里）
# ------------------------------------------------------------------ #
SECONDS_PER_YEAR = 3600.0     # 标准流速：1 世界年 = 1 小时现实（文档 §8.3）
MAX_CATCHUP_DAYS = 20 * DAYS_PER_YEAR   # 单次补算上限（20 世界年）
INIT_NPCS = 30                # 初始 NPC 数
MIN_POP = 24                  # 低于此数开始补充新人
MAX_ACTIVE = 50               # 活跃层上限
LOGS_KEEP = 2000              # 日志流保留条数（文档 §9.3）
BIG_CATCHUP_DAYS = 60         # 超过该补算天数，L1 琐碎按比例采样
L1_SAMPLE_RATE = 0.15
WORLD_EVENT_EVERY = 30        # 每 N 世界日掷一次天下大事
WORLD_EVENT_CHANCE = 0.35
PLAYER_RELATED = 5            # 初始与玩家有旧交的 NPC 数

def day_str(day: int) -> str:
    """世界日 → 「第X年 M月D日」纪年文本。
    修仙历：一年 365 日，前 11 个月各 30 日，腊月 35 日（岁余五日归腊月）。"""
    year = day // DAYS_PER_YEAR + 1
    rem = day % DAYS_PER_YEAR
    month = min(rem // 30 + 1, 12)
    day_of_month = rem % 30 + 1 if rem < 330 else rem - 329
    return f'第{year}年 {month}月{day_of_month}日'


class WorldService:
    """世界时钟 + 三线日志流。线程安全（tick 可来自定时器线程）。"""

    def __init__(self, save_path: Optional[str] = None,
                 seconds_per_year: float = SECONDS_PER_YEAR):
        self.save_path = save_path
        self.seconds_per_year = float(seconds_per_year)
        self.lock = threading.Lock()
        self.rng = random.Random()
        self.engine = ContentEngine()
        self.sim = NPCSimulator(self.engine, self.rng)
        self.events: Dict[str, dict] = {'by_id': {}, 'pools': {}}
        self.ctx_pools: Dict[str, list] = {}
        self.world_events_table: List[dict] = []
        self.player_echoes: Dict[str, dict] = {}   # 玩家回响事件表
        self.player_travel: dict = {}              # 游历琐事模板（主线·游历直播）
        self._grants: List[dict] = []              # 待兑现的玩家收益（回响/世界结算）
        self._notable: List[dict] = []       # 待演出的 L2+ 事件
        self.world: Optional[dict] = None
        self._load()

    # ------------------------------------------------------------------ #
    # 内容装载（由插件注入，core 不反向依赖插件）
    # ------------------------------------------------------------------ #
    def load_content(self, data_dir: str):
        """加载内容目录（res/world）下的事件表与素材池。

        幂等：守护与角色面板页可能先后调用，二次调用直接跳过
        （素材池是 extend 合并，重复加载会翻倍池子）。
        """
        if getattr(self, '_content_loaded', False):
            return
        import glob
        for path in sorted(glob.glob(os.path.join(data_dir, 'events_*.json'))):
            try:
                with open(path, encoding='utf-8') as f:
                    batch = json.load(f)
            except Exception as e:  # noqa: BLE001
                print(f'[world] load {path} failed: {e!r}')
                continue
            for ev in batch:
                self.events['by_id'][ev['id']] = ev
                pool = ev.get('pool')
                if pool:
                    ids = self.events['pools'].setdefault(pool, [])
                    if ev['id'] not in ids:
                        ids.append(ev['id'])
        for name in ('name_pools', 'ctx_pools', 'world_events',
                     'player_echoes', 'player_travel'):
            path = os.path.join(data_dir, f'{name}.json')
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
            except Exception:  # noqa: BLE001
                continue
            if name == 'name_pools':
                self.ctx_pools.update(data)          # surnames/given/roots...
            elif name == 'ctx_pools':
                for k, v in data.items():            # beasts/items/...
                    self.ctx_pools.setdefault(k, []).extend(v)
            elif name == 'player_echoes':
                for ev in data:
                    self.player_echoes[ev['id']] = ev
            elif name == 'player_travel':
                self.player_travel = data
            else:
                self.world_events_table = data
        # 内容就绪后再开天辟地（初始 NPC 需要名字池/地点池）
        if getattr(self, '_fresh', False) and self.events['by_id']:
            self._populate()
            self._fresh = False
            self.dirty = True
        self._content_loaded = True

    # ------------------------------------------------------------------ #
    # 存档
    # ------------------------------------------------------------------ #
    def _load(self):
        data = None
        if self.save_path and os.path.isfile(self.save_path):
            try:
                with open(self.save_path, encoding='utf-8') as f:
                    data = json.load(f)
            except Exception:  # noqa: BLE001
                data = None
        if data and data.get('version') == 1:
            self.world = data
            self.world.setdefault('dead_index', {})
            self.world.setdefault('pending_echoes', [])
            self.world.setdefault('player_karma', 0)
            self.world.setdefault('player_flags', {})
            self.world.setdefault('pending_choice', None)
            self.world.setdefault('last_qiyu_ts', 0.0)
            self.world.setdefault('qiyu_history', [])
            self._fresh = False
        else:
            self.world = self._fresh_world()
            self._fresh = True          # 等内容装载后再开天辟地
            self.dirty = True

    @staticmethod
    def _fresh_world() -> dict:
        return {'version': 1, 'world_day': 0, 'last_tick': time.time(),
                'npcs': {}, 'dead_index': {}, 'logs': [],
                'pending_echoes': [], 'used_names': [],
                'active_ids': [], 'last_world_event': None,
                'next_npc_id': 1,
                # ---- 玩家侧（抉择系统）：因果值/flag/待应答请示 ----
                'player_karma': 0, 'player_flags': {},
                'pending_choice': None, 'last_qiyu_ts': 0.0,
                'qiyu_history': []}

    def _populate(self):
        """开天辟地：初始 NPC 群 + 数名与玩家有旧交者。"""
        w = self.world
        rng = self.rng
        used = set(w['used_names'])
        for i in range(INIT_NPCS):
            npc = gen_npc(f"npc_{w['next_npc_id']}", 0, rng,
                          self.ctx_pools, used)
            used.add(npc['name'])
            w['next_npc_id'] += 1
            if i < PLAYER_RELATED:
                kind = rng.choice(['old_friend', 'old_friend', 'acquaint',
                                   'old_rival'])
                npc['relations'].append({'target': 'player', 'type': kind,
                                         'affinity': rng.randint(
                                             -60 if kind == 'old_rival' else 25,
                                             85)})
                npc['tier'] = 'active'
                w['active_ids'].append(npc['id'])
            w['npcs'][npc['id']] = npc
        w['used_names'] = list(used)[-2000:]
        self._fill_active()

    def _fill_active(self):
        """活跃层 = 与玩家有旧交者 + 随机补足到上限。"""
        w = self.world
        active = set(w.get('active_ids', []))
        for npc in w['npcs'].values():
            rel = next((r for r in npc.get('relations', [])
                        if r['target'] == 'player'), None)
            if rel is not None:
                active.add(npc['id'])
        rest = [n['id'] for n in w['npcs'].values() if n['id'] not in active]
        for nid in rest:
            if len(active) >= MAX_ACTIVE:
                break
            active.add(nid)
        w['active_ids'] = list(active)
        for n in w['npcs'].values():
            n['tier'] = 'active' if n['id'] in active else 'dormant'

    def save(self):
        if not (self.save_path and self.world):
            return
        try:
            os.makedirs(os.path.dirname(self.save_path), exist_ok=True)
            tmp = self.save_path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self.world, f, ensure_ascii=False)
            os.replace(tmp, self.save_path)
            self.dirty = False
        except Exception as e:  # noqa: BLE001
            print(f'[world] save failed: {e!r}')

    def save_if_dirty(self):
        if getattr(self, 'dirty', False):
            self.save()

    # ------------------------------------------------------------------ #
    # 时间推进
    # ------------------------------------------------------------------ #
    def catch_up(self, now: Optional[float] = None) -> dict:
        """现实时间差 → 世界日补算。返回统计 {days, logs}。"""
        now = time.time() if now is None else now
        with self.lock:
            w = self.world
            elapsed = now - float(w.get('last_tick', now))
            if elapsed < 0:                    # 时间回拨：放弃推进且不更新时间戳
                return {'days': 0, 'logs': 0}
            days = int(elapsed * DAYS_PER_YEAR / self.seconds_per_year)
            days = min(days, MAX_CATCHUP_DAYS)
            if days <= 0:
                w['last_tick'] = now
                return {'days': 0, 'logs': 0}
            n_logs = self._simulate_days(days)
            w['last_tick'] = now
            self.dirty = True
            return {'days': days, 'logs': n_logs}

    def _simulate_days(self, n_days: int) -> int:
        w = self.world
        sample_l1 = n_days > BIG_CATCHUP_DAYS
        rng = self.rng
        count = 0
        for _ in range(n_days):
            w['world_day'] += 1
            day = w['world_day']
            active_ids = list(w.get('active_ids', []))
            for nid in active_ids:
                npc = w['npcs'].get(nid)
                if npc is None:
                    continue
                for lg in self.sim.simulate_day(npc, w, self.events,
                                                self.ctx_pools, full=True):
                    count += self._append_log(lg, sample_l1)
            if day % 10 == 0:                        # 休眠层粗推进
                for npc in w['npcs'].values():
                    if npc.get('tier') != 'dormant':
                        continue
                    for lg in self.sim.simulate_day(npc, w, self.events,
                                                    self.ctx_pools, full=False):
                        count += self._append_log(lg, sample_l1)
            # 玩家回响（抉择系统的因果种子）由世界服务直接结算；
            # 其余 NPC 回响交给模拟器
            self._resolve_player_echoes()
            for lg in self.sim.resolve_echoes(w, self.events, self.ctx_pools):
                count += self._append_log(lg, sample_l1)
            if day % DAYS_PER_YEAR == 0:
                self.sim.yearly_relations(w)
            if day % WORLD_EVENT_EVERY == 0:
                self._roll_world_event()
            self._replenish()
        return count

    def _append_log(self, lg: dict, sample_l1: bool) -> int:
        if lg.get('level', 1) <= 1 and sample_l1 \
                and self.rng.random() > L1_SAMPLE_RATE:
            return 0
        logs = self.world.setdefault('logs', [])
        logs.append(lg)
        if len(logs) > LOGS_KEEP:
            del logs[:len(logs) - LOGS_KEEP]
        if lg.get('level', 1) >= 2:
            self._notable.append(lg)
        return 1

    # ------------------------------------------------------------------ #
    # 天下大事（环境线）
    # ------------------------------------------------------------------ #
    def _roll_world_event(self):
        if not self.world_events_table:
            return
        if self.rng.random() > WORLD_EVENT_CHANCE:
            return
        ws = [max(0.1, float(e.get('weight', 10)))
              for e in self.world_events_table]
        ev = self.rng.choices(self.world_events_table, weights=ws, k=1)[0]
        tpl = self.rng.choice(ev.get('log') or ['天下有变。'])
        line = self.engine.render(tpl, {})
        self.world['last_world_event'] = {
            'id': ev['id'], 'until_day': self.world['world_day']
            + int(ev.get('days', 60))}
        self._append_log({'day': self.world['world_day'], 'cat': 'world',
                          'level': int(ev.get('level', 2)), 'text': line,
                          'who': None}, sample_l1=False)

    # ------------------------------------------------------------------ #
    # 玩家侧：游历直播日志 / 玩家回响 / 收益取件
    # ------------------------------------------------------------------ #
    def player_log(self, text: str, level: int = 1) -> None:
        """主线·游历直播（cat='main'）：本体离家/传讯符/归来由冒险侧镜像写入。

        注意力铁律：调用方自行控制级别——L1 静默入库，L2/L3 会进演出队列。
        """
        text = str(text or '').strip()
        if not text:
            return
        with self.lock:
            self._append_log({'day': self.world.get('world_day', 0),
                              'cat': 'main', 'level': int(level),
                              'text': text, 'who': 'player'},
                             sample_l1=False)

    def gen_travel_log(self, loc: str, realm: int) -> Optional[str]:
        """生成一条游历琐事（境界分档措辞，引擎近期去重生效）。返回文本。"""
        table = self.player_travel.get('ambient') if self.player_travel \
            else None
        if not table:
            return None
        tier = 'low' if realm <= 2 else ('mid' if realm <= 6 else 'high')
        lines = table.get(tier) or table.get('low') or []
        if not lines:
            return None
        pools = self.ctx_pools
        ctx = {'loc': loc or self.rng.choice(
                   pools.get('locations', ['青云山'])),
               'beast': self.rng.choice(pools.get('beasts', ['妖兽'])),
               'item': self.rng.choice(pools.get('items', ['灵物']))}
        ev = {'id': 'travel_ambient', 'log': {tier: lines}}
        return self.engine.render(
            self.engine.pick_template(ev, realm, self.rng) or '', ctx)

    def _resolve_player_echoes(self) -> None:
        """结算 npc='player' 的到期回响：日志（cat='main' L2+）+ 收益入队。"""
        w = self.world
        pending = w.get('pending_echoes', [])
        if not pending:
            return
        due = [e for e in pending
               if e.get('npc') == 'player' and e['day'] <= w['world_day']]
        if not due:
            return
        w['pending_echoes'] = [e for e in pending if e not in due]
        for item in due:
            ev = self.player_echoes.get(item.get('event'))
            if ev is None:
                continue
            pools = self.ctx_pools
            ctx = {'loc': self.rng.choice(pools.get('locations', ['青云山'])),
                   'beast': self.rng.choice(pools.get('beasts', ['妖兽'])),
                   'item': self.rng.choice(pools.get('items', ['灵物'])),
                   'person': self.rng.choice(pools.get('persons', ['路人修士'])),
                   'sect': self.rng.choice(pools.get('sects', ['青云宗']))}
            tpl = self.engine.pick_template(ev, 4, self.rng)
            if tpl:
                self._append_log({'day': w['world_day'], 'cat': 'main',
                                  'level': int(ev.get('level', 3)),
                                  'text': self.engine.render(tpl, ctx),
                                  'who': 'player'}, sample_l1=False)
            grants = {}
            for key, lo_hi in (ev.get('grants') or {}).items():
                if key == 'karma':
                    w['player_karma'] = int(w.get('player_karma', 0)) + \
                        self.rng.randint(int(lo_hi[0]), int(lo_hi[1]))
                elif key == 'injury':       # (速率倍率, 持续秒) 原样下发
                    grants['injury'] = [float(lo_hi[0]), float(lo_hi[1])]
                else:
                    grants[key] = self.rng.randint(int(lo_hi[0]), int(lo_hi[1]))
            if grants:
                grants['reason'] = ev.get('id', 'player_echo')
                self._grants.append(grants)
            self.dirty = True

    def drain_grants(self) -> List[dict]:
        """取走世界结算出的玩家收益（exp/stones/injury/item），由插件兑现。"""
        with self.lock:
            out, self._grants = self._grants, []
            return out

    # ------------------------------------------------------------------ #
    # 人口补充
    # ------------------------------------------------------------------ #
    def _replenish(self):
        w = self.world
        if len(w['npcs']) >= MIN_POP or self.rng.random() > 0.4:
            return
        used = set(w.get('used_names', []))
        npc = gen_npc(f"npc_{w['next_npc_id']}", w['world_day'], self.rng,
                      self.ctx_pools, used)
        npc['realm'] = self.rng.choices([0, 1], weights=[80, 20], k=1)[0]
        # 新登场的都是年轻人（18~30 岁凡人/炼气），重新按世界日折算生辰
        npc['birth_day'] = w['world_day'] - self.rng.randint(18, 30) * DAYS_PER_YEAR
        used.add(npc['name'])
        w['used_names'] = list(used)[-2000:]
        w['next_npc_id'] += 1
        w['npcs'][npc['id']] = npc
        if len(w['active_ids']) < MAX_ACTIVE:
            w['active_ids'].append(npc['id'])
            npc['tier'] = 'active'

    # ------------------------------------------------------------------ #
    # 对外查询 / 演出取件
    # ------------------------------------------------------------------ #
    def drain_notable(self) -> List[dict]:
        """取走自上次以来积累的 L2+ 日志（插件做通知/气泡演出）。"""
        with self.lock:
            out, self._notable = self._notable, []
            return out

    def recent_logs(self, n: int = 120,
                    cat: Optional[str] = None) -> List[dict]:
        logs = self.world.get('logs', []) if self.world else []
        if cat:
            logs = [x for x in logs if x.get('cat') == cat]
        return logs[-n:]

    def roster(self, n: int = 60) -> List[dict]:
        """道友名帖：与玩家有旧交者在前，按境界降序。"""
        npcs = list(self.world.get('npcs', {}).values()) if self.world else []
        npcs.sort(key=lambda x: (-any(r.get('target') == 'player'
                                      for r in x.get('relations', [])),
                                  -int(x.get('realm', 0))))
        out = []
        for npc in npcs[:n]:
            rel = next((r for r in npc.get('relations', [])
                        if r['target'] == 'player'), None)
            out.append({
                'id': npc['id'], 'name': npc['name'],
                'realm': npc.get('realm', 0),
                'age': self.sim._age(npc, self.world),
                'lifespan': npc.get('base_lifespan', 0),
                'loc': npc.get('loc', ''),
                'health': round(float(npc.get('health', 1)), 2),
                'mood': npc.get('mood', 60),
                'rel_type': rel['type'] if rel else '',
                'rel_affinity': rel['affinity'] if rel else None,
            })
        return out

    def fallen(self, n: int = 40) -> List[dict]:
        return list(self.world.get('dead_index', {}).values())[-n:]


# ---------------------------------------------------------------------- #
# 模块级单例
# ---------------------------------------------------------------------- #
_WORLD: Optional[WorldService] = None
_WORLD_LOCK = threading.Lock()


def get_world(save_path: Optional[str] = None,
              seconds_per_year: float = SECONDS_PER_YEAR) -> WorldService:
    global _WORLD
    if _WORLD is None:
        with _WORLD_LOCK:
            if _WORLD is None:
                if save_path is None and _settings is not None:
                    save_path = os.path.join(_settings.CONFIGDIR, 'data',
                                             'world_state.json')
                _WORLD = WorldService(save_path, seconds_per_year)
    return _WORLD
