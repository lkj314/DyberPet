# coding:utf-8
"""模板化内容生成引擎（规则驱动，零 LLM）。

设计来源：《桌宠修仙世界日志与道友人生模拟系统.md》§七
- **三层结构**：事件模板（发生了什么）→ 表述变体（同一事件多种说法）
  → 上下文修饰（按状态插入细节）。
- **组合爆炸**：20 模板 × 15 地点 × 8 结果 × 3 变体 ≈ 8.6 万组合，
  少量模板即可海量不重复，且完全确定、可回归测试。
- **近期去重**：记录最近 N 个已用 (模板, 变体)，抽取时对近期用过的降权。
- **境界分档措辞**：同一"赶路"，炼气"脚上磨出两个泡"、金丹"御剑三千里"、
  元婴"缩地成寸"——成长感渗透进每一条日志。
- 纯逻辑无 Qt 依赖，可独立单测（与 cultivation_service 同模式）。
"""
from __future__ import annotations

import random
import re
from typing import Dict, List, Optional

_SLOT_RE = re.compile(r'\{(\w+)\}')

# 境界(0..9) → 措辞档位
_REALM_TIER = lambda realm: ('low' if realm <= 2 else
                             'mid' if realm <= 6 else 'high')


class ContentEngine:
    """渲染引擎。线程不安全——由世界模拟单线程调用。"""

    def __init__(self, recent_limit: int = 60):
        self.recent_limit = recent_limit
        self._recent: List[str] = []          # 最近用过的 "event_id#variant" 键

    # ------------------------------------------------------------------ #
    # 模板抽取
    # ------------------------------------------------------------------ #
    @staticmethod
    def templates_for(event: dict, realm: int) -> List[str]:
        """按 NPC 境界取措辞档模板列表（缺档回退 low）。"""
        tier = _REALM_TIER(realm)
        log = event.get('log') or {}
        lines = log.get(tier) or log.get('low') or []
        return list(lines)

    def pick_template(self, event: dict, realm: int,
                      rng: random.Random) -> Optional[str]:
        """抽一条模板：近期用过的 (事件, 变体) 强降权，防连刷同款。"""
        lines = self.templates_for(event, realm)
        if not lines:
            return None
        weights = []
        for i, _ in enumerate(lines):
            key = f"{event.get('id', '?')}#{i}"
            weights.append(0.05 if key in set(self._recent) else 1.0)
        idx = rng.choices(range(len(lines)), weights=weights, k=1)[0]
        key = f"{event.get('id', '?')}#{idx}"
        self._recent.append(key)
        if len(self._recent) > self.recent_limit:
            del self._recent[:len(self._recent) - self.recent_limit]
        return lines[idx]

    # ------------------------------------------------------------------ #
    # 渲染
    # ------------------------------------------------------------------ #
    def render(self, template: str, ctx: Dict[str, str]) -> str:
        """槽位替换；缺失槽位以 '' 填充（模板作者负责完整性）。"""
        def _sub(m: 're.Match[str]') -> str:
            return str(ctx.get(m.group(1), ''))
        return _SLOT_RE.sub(_sub, template)

    @staticmethod
    def decorate(line: str, npc: dict) -> str:
        """上下文修饰：按 NPC 状态在句首插细节（最多一条，克制）。"""
        health = float(npc.get('health', 1.0))
        mood = float(npc.get('mood', 60))          # 0~100
        if health <= 0.30:
            return '带伤之下，' + line
        if health <= 0.45:
            return '伤势未愈，' + line
        if mood >= 85:
            return '心情甚好，' + line
        if mood <= 15:
            return '无精打采，' + line
        return line

    # ------------------------------------------------------------------ #
    # 事件 → 日志一行
    # ------------------------------------------------------------------ #
    def emit(self, event: dict, npc: dict, ctx: Dict[str, str],
             rng: random.Random) -> Optional[str]:
        """事件 + 上下文 → 一条日志文本。无可用模板返回 None。"""
        tpl = self.pick_template(event, int(npc.get('realm', 0)), rng)
        if tpl is None:
            return None
        return self.decorate(self.render(tpl, ctx), npc)
