# coding:utf-8
"""抉择系统核心服务（常驻，纯逻辑，无 Qt 依赖）。

设计来源：《桌宠文字修仙游戏设计.md》§七「抉择系统：把选择变成内容」
- **道友范式（§1.2）**：选择权归用户，行动与讲述归桌宠。桌宠遇奇遇请示，
  用户在日志面板拍板——不是操作员，是引路人。
- **数值铁律（§7.3）**：选项分支与结果 100% 由本模块结算（奇遇表声明区间，
  本模块掷骰），LLM 绝不参与分支决定——与冒险/斗地主同一原则。
- **因果回响（§7.2）**：关键抉择写入 flag 并种下延迟回响（复用
  world_service.pending_echoes 机制，npc='player' 走玩家回响表）——
  当年放生的狼多年后化形来谢，当年杀狼取丹日后狼群寻仇。
- **karma（因果值）**：抉择累积，影响后续奇遇的性质倾向（善缘引善缘）。
- 状态全部挂在世界存档 dict 上（player_karma / player_flags /
  pending_choice），随世界存档持久化——重启不影响未应答的请示。
"""
from __future__ import annotations

import json
import os
import random
import time
from typing import Dict, List, Optional

from .content_engine import ContentEngine

# ------------------------------------------------------------------ #
# 机制参数（调参只动这里）
# ------------------------------------------------------------------ #
COOLDOWN_SECONDS = 8 * 60     # 两次奇遇请示最小间隔（现实秒，文档 §11「间隔数分钟以上」）
RECENT_KEEP = 6               # 近期用过的奇遇 id 保留数（去重）
KARMA_BIAS_AT = 20            # |karma| 达到该值后，奇遇性质开始受因果牵引


def _self_realm() -> int:
    """玩家境界序（0..9）。读不到修为核心按炼气处理。"""
    try:
        from DyberPet.cultivation_service import get_core
        return min(max(get_core().stage(), 0) // 4, 9)
    except Exception:  # noqa: BLE001
        return 0


class ChoiceService:
    """奇遇请示 + 因果结算。状态持久化在 world dict 上。"""

    def __init__(self, world_service, engine: Optional[ContentEngine] = None):
        self.ws = world_service
        self.engine = engine or world_service.engine
        self.rng = world_service.rng
        self.table: List[dict] = []
        self._by_id: Dict[str, dict] = {}

    # ------------------------------------------------------------------ #
    # 内容装载
    # ------------------------------------------------------------------ #
    def load(self, data_dir: str) -> None:
        path = os.path.join(data_dir, 'qiyu_events.json')
        try:
            with open(path, encoding='utf-8') as f:
                self.table = json.load(f)
            self._by_id = {e['id']: e for e in self.table}
        except Exception as e:  # noqa: BLE001
            print(f'[choice] load {path} failed: {e!r}')
            self.table, self._by_id = [], {}

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #
    def _w(self) -> dict:
        return self.ws.world

    @property
    def karma(self) -> int:
        return int(self._w().get('player_karma', 0))

    def _history(self) -> List[str]:
        return list(self._w().get('qiyu_history', []))

    # ------------------------------------------------------------------ #
    # 请示（offer）
    # ------------------------------------------------------------------ #
    def cooldown_ok(self) -> bool:
        return time.time() - float(self._w().get('last_qiyu_ts', 0.0)) \
            >= COOLDOWN_SECONDS

    def eligible(self, context: dict) -> List[dict]:
        """按境界门槛 + 去重 + 因果牵引筛出候选奇遇。"""
        realm = max(int(context.get('realm', _self_realm())), 0)
        used = set(self._history())
        karma = self.karma
        cands: List[dict] = []
        for ev in self.table:
            if ev['id'] in used or int(ev.get('min_realm', 0)) > realm:
                continue
            w = float(ev.get('weight', 10))
            tone = str(ev.get('tone', 'neutral'))
            # 因果牵引：善缘引善缘、恶因招恶果（文档 §7.2 karma 影响后续性质）
            if karma >= KARMA_BIAS_AT and tone == 'good':
                w *= 1.6
            elif karma <= -KARMA_BIAS_AT and tone == 'evil':
                w *= 1.6
            cands.append(ev)
            ev['_w'] = w
        return cands

    def offer(self, context: Optional[dict] = None) -> Optional[dict]:
        """掷一次奇遇请示。命中则写入 pending 并返回；不中返回 None。

        context: {'phase': 'return'|'idle'|'stay', 'loc': 秘境/地点名}
        """
        if self._w().get('pending_choice'):
            return None                    # 上一道请示还没应答，不叠新题
        if not self.cooldown_ok():
            return None
        context = dict(context or {})
        context.setdefault('realm', _self_realm())
        # 总开关（主配置 world_qiyu_choices；旧插件键由 settings 迁移，回退兼容）
        try:
            import DyberPet.settings as _settings
            enabled = getattr(_settings, 'world_qiyu_choices', True)
            if enabled is None:
                enabled = _settings.plugins_settings.get('xiuxian_world', {}) \
                    .get('qiyu_choices', True)
            if not enabled:
                return None
        except Exception:  # noqa: BLE001
            pass
        cands = self.eligible(context)
        if not cands:
            return None
        rng = self.rng
        ev = rng.choices(cands, weights=[e['_w'] for e in cands], k=1)[0]

        # 叙事渲染（境界分档措辞 + 槽位）
        ctx = self._ctx(context)
        tpl = self.engine.pick_template(
            {'id': ev['id'], 'log': ev.get('narrative')},
            int(context['realm']), rng)
        if tpl is None:
            return None
        narrative = self.engine.render(tpl, ctx)
        loc = context.get('loc') or ctx.get('loc') or ''

        choices = [{'key': c['key'], 'text': c['text']}
                   for c in ev.get('choices', [])]
        pending = {
            'id': ev['id'], 'title': ev.get('title', '奇遇'),
            'narrative': narrative, 'loc': loc,
            'choices': choices, 'day': int(self._w().get('world_day', 0)),
            'ts': time.time(), 'realm': int(context['realm']),
        }
        w = self._w()
        w['pending_choice'] = pending
        w['last_qiyu_ts'] = pending['ts']
        hist = self._history()
        hist.append(ev['id'])
        w['qiyu_history'] = hist[-RECENT_KEEP:]
        self.ws.dirty = True
        return pending

    def _ctx(self, context: dict) -> Dict[str, str]:
        pools = self.ws.ctx_pools
        rng = self.rng
        return {
            'loc': context.get('loc') or rng.choice(
                pools.get('locations', ['青云山'])),
            'beast': rng.choice(pools.get('beasts', ['妖兽'])),
            'item': rng.choice(pools.get('items', ['灵物'])),
            'person': rng.choice(pools.get('persons', ['路人修士'])),
            'sect': rng.choice(pools.get('sects', ['青云宗'])),
        }

    # ------------------------------------------------------------------ #
    # 应答（resolve）——效果 100% 本模块结算
    # ------------------------------------------------------------------ #
    def resolve(self, choice_key: str) -> Optional[dict]:
        """对当前 pending 抉择。返回 {'title','text','grants','echoes'} 或 None。"""
        pending = self._w().get('pending_choice')
        if not pending:
            return None
        ev = self._by_id.get(pending.get('id'))
        if ev is None:
            self._w()['pending_choice'] = None
            self.ws.dirty = True
            return None
        ch = next((c for c in ev.get('choices', [])
                   if c.get('key') == choice_key), None)
        if ch is None:
            return None
        rng = self.rng
        ctx = self._ctx({'loc': pending.get('loc')})
        eff = ch.get('effects') or {}

        # ---- 数值结算（区间掷骰）----
        grants: Dict[str, object] = {}
        if 'exp' in eff:
            grants['exp'] = rng.randint(int(eff['exp'][0]), int(eff['exp'][1]))
        if 'stones' in eff:
            grants['stones'] = rng.randint(int(eff['stones'][0]),
                                           int(eff['stones'][1]))
        if 'item' in eff:
            grants['item'] = str(eff['item'])
        if 'injury' in eff:
            mult, seconds = eff['injury']
            grants['injury'] = [float(mult), float(seconds)]

        # ---- 因果落账 ----
        w = self._w()
        if 'karma' in eff:
            w['player_karma'] = self.karma + rng.randint(int(eff['karma'][0]),
                                                         int(eff['karma'][1]))
        flag = eff.get('flag')
        if flag:
            w.setdefault('player_flags', {})[flag] = int(w.get('world_day', 0))

        # ---- 回响种子（复用世界回响机制，npc='player'）----
        echoes = []
        echo = ch.get('echo')
        if echo:
            delay = echo.get('delay', [45, 120])
            due_day = int(w.get('world_day', 0)) + \
                rng.randint(int(delay[0]), int(delay[1]))
            w.setdefault('pending_echoes', []).append(
                {'day': due_day, 'event': echo['event'], 'npc': 'player'})
            echoes.append({'event': echo['event'], 'day': due_day})

        # ---- 结果措辞（境界分档）----
        result_tpl = self.engine.pick_template(
            {'id': f"{ev['id']}#{ch['key']}", 'log': ch.get('result')},
            pending.get('realm', 0), rng)
        text = self.engine.render(result_tpl, ctx) if result_tpl else \
            f"（{ch.get('text', '')}）此事便这么定了。"

        w['pending_choice'] = None
        self.ws.dirty = True
        return {'title': pending.get('title', ''), 'text': text,
                'grants': grants, 'echoes': echoes}


# ---------------------------------------------------------------------- #
# 模块级单例（跟随世界单例）
# ---------------------------------------------------------------------- #
_CHOICE: Optional[ChoiceService] = None


def get_choice(world_service) -> ChoiceService:
    """惰性单例。内容装载由插件调 svc.load(data_dir)。"""
    global _CHOICE
    if _CHOICE is None:
        _CHOICE = ChoiceService(world_service)
    return _CHOICE
