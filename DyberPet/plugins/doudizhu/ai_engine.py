# coding:utf-8
"""斗地主决策 AI——规则引擎 + 启发式打分（文档 §3.1/§4.3）。

铁律（文档 §3.2）：出牌决策 100% 由本模块负责，绝不让 LLM 出牌。
信息隔离：decide_bid / decide_play 只接收 engine.build_view(seat) 的数据，
          **拿不到其他玩家手牌**；仅凭公开信息 + 记牌即可显得会算牌。
"""
from __future__ import annotations

import random
from collections import Counter
from typing import List, Optional

from .card_rules import (Move, can_beat, card_rank)

# 难度 1~5 -> 每步犯错概率
MISTAKE = {1: 0.45, 2: 0.30, 3: 0.18, 4: 0.08, 5: 0.0}


def _counts(hand) -> Counter:
    return Counter(card_rank(c) for c in hand)


def _pick_cards(hand, rank: int, need: int, exclude: Optional[set] = None) -> List[int]:
    """从手牌里取指定 rank 的 need 张。"""
    exclude = exclude or set()
    out = [c for c in hand if card_rank(c) == rank and c not in exclude]
    return out[:need]


class AIPlayer:
    """一个座位上的决策 AI。两个实例共用同一套代码（文档 §4.1）。"""

    def __init__(self, seat: int, style: str = '暴躁哥'):
        self.seat = seat
        self.style = style
        self.difficulty = 3

    def configure(self, difficulty: int):
        self.difficulty = max(1, min(5, int(difficulty)))

    # ------------------------------------------------------------------ #
    # 叫地主
    # ------------------------------------------------------------------ #
    def decide_bid(self, view: dict) -> int:
        cnt = _counts(view['my_cards'])
        v = (cnt.get(14, 0) * 8 + cnt.get(13, 0) * 6 + cnt.get(12, 0) * 3
             + cnt.get(11, 0) * 1)
        v += sum(6 for k in cnt.values() if k == 4)
        cur = view.get('current_bid', 0)
        want = 3 if v >= 16 else 2 if v >= 11 else 1 if v >= 6 else 0
        # 低难度偶尔少叫一档，显得更"人"
        if random.random() < (0.35 - 0.06 * self.difficulty):
            want = max(0, want - 1)
        return want if want > cur else 0

    # ------------------------------------------------------------------ #
    # 出牌
    # ------------------------------------------------------------------ #
    def decide_play(self, view: dict):
        hand = view['my_cards']
        counts = _counts(hand)
        last = view['last_move']
        last_player = view['last_player']
        my_count = view['my_count']

        if last is None or last_player == self.seat:
            mv = self._lead(view, counts)
        else:
            mv = self._follow(view, counts)

        if mv is None:
            return 'pass'

        # 难度 -> 偶尔犯错（换成另一个合法选择）
        p = MISTAKE.get(self.difficulty, 0.18)
        if p > 0 and random.random() < p:
            alts = (self._gen_beats(hand, counts, last)
                    if last is not None and last_player != self.seat
                    else self._gen_leads(hand, counts))
            alts = [m for m in alts if m.ptype != 'rocket']
            if alts:
                mv = random.choice(alts)
        return mv

    # ------------------------------------------------------------------ #
    # 领出
    # ------------------------------------------------------------------ #
    def _lead(self, view: dict, counts: Counter):
        hand = view['my_cards']
        my_count = view['my_count']
        leads = self._gen_leads(hand, counts)

        # 1. 一手走完 -> 直接赢
        for mv in leads:
            if len(mv.cards) == my_count:
                return mv

        # 2. 队友只剩 ≤2 张 -> 出最小单张送队友（文档 §4.3 规则3）
        if view.get('teammate_remaining') is not None and view['teammate_remaining'] <= 2:
            singles = [m for m in leads if m.ptype == 'single']
            if singles:
                return min(singles, key=lambda m: m.rank)

        # 3. 打分选优：多用牌、用小牌、别拆炸弹/王
        if not leads:
            return None
        return max(leads, key=lambda m: self._score_lead(m, counts))

    def _score_lead(self, mv: Move, counts: Counter) -> float:
        s = 3.0 * len(mv.cards) - mv.rank
        if mv.ptype in ('straight', 'pair_seq', 'plane', 'plane1', 'plane2'):
            s += 2
        if mv.ptype == 'bomb':
            s -= 20
        elif mv.ptype == 'rocket':
            s -= 25
        used = Counter(card_rank(c) for c in mv.cards)
        for r, k in used.items():
            if counts[r] == 4 and k < 4:
                s -= 10          # 拆炸弹
            elif counts[r] == 3 and k < 3:
                s -= 4           # 拆三张
            if r >= 13:
                s -= 6           # 别乱甩王
        return s

    def all_moves(self, view: dict) -> List[Move]:
        """玩家「提示」用：当前合法动作全集，最优在前（领出按打分，跟牌按 rank）。"""
        hand = view['my_cards']
        counts = _counts(hand)
        last = view['last_move']
        last_player = view['last_player']
        if last is None or last_player == self.seat:
            moves = self._gen_leads(hand, counts)
            return sorted(moves, key=lambda m: -self._score_lead(m, counts))
        moves = self._gen_beats(hand, counts, last)
        return sorted(moves, key=lambda m: (m.rank, m.ptype != 'single'))

    # ------------------------------------------------------------------ #
    # 跟牌（含农民配合，文档 §4.3）
    # ------------------------------------------------------------------ #
    def _follow(self, view: dict, counts: Counter):
        hand = view['my_cards']
        last = view['last_move']
        last_player = view['last_player']
        role = view['role']
        landlord = view['landlord']
        my_count = view['my_count']

        beats = self._gen_beats(hand, counts, last)
        if not beats:
            return None

        # 能一手走完 -> 无论谁出的都压
        for mv in beats:
            if len(mv.cards) == my_count:
                return mv

        last_by_teammate = (role == 'farmer' and last_player != landlord)
        landlord_remaining = view.get('landlord_remaining')

        # 规则1：队友出的且牌不小 -> 不压，让队友走
        if last_by_teammate:
            if last.rank >= 8 or last.ptype in ('straight', 'pair_seq',
                                                'plane', 'plane1', 'plane2'):
                return None
            # 队友出小牌：地主告急则顶最大，否则省着不压
            if landlord_remaining is not None and landlord_remaining <= 2:
                return max(beats, key=lambda m: (m.ptype == 'bomb', m.rank))
            return None

        # 规则2：地主只剩 ≤2 张 -> 必须顶，哪怕拆牌
        if role == 'farmer' and landlord_remaining is not None and landlord_remaining <= 2:
            return max(beats, key=lambda m: (m.ptype in ('bomb', 'rocket'),
                                             m.rank))

        # 常规：最省的压牌（rank 最小、拆牌代价最低）
        def waste(mv: Move) -> int:
            used = Counter(card_rank(c) for c in mv.cards)
            w = 0
            for r, k in used.items():
                have = counts[r]
                if have == 4 and k < 4:
                    w += 3
                elif have == 3 and k < 3:
                    w += 2
                elif have == 2 and k == 1:
                    w += 1
                if r >= 13:
                    w += 2
            return w

        return min(beats, key=lambda m: (m.rank, waste(m), m.ptype != 'single'))

    # ------------------------------------------------------------------ #
    # 跟牌候选：同类型同长度能压的 + 炸弹/王炸
    # ------------------------------------------------------------------ #
    def _gen_beats(self, hand, counts: Counter, last: Move) -> List[Move]:
        out: List[Move] = []
        if last.ptype == 'rocket':
            return out
        ranks_sorted = sorted(counts)

        def cards_of(r: int, need: int) -> List[int]:
            return _pick_cards(hand, r, need)

        def attach_singles(exclude_ranks, k) -> List[int]:
            pool = [r for r in ranks_sorted if r not in exclude_ranks]
            pool.sort(key=lambda r: (counts[r], r))
            picked: List[int] = []
            for r in pool:
                while len(picked) < k and counts[r] - sum(1 for c in picked
                                                          if card_rank(c) == r) > 0:
                    cands = _pick_cards(hand, r, 1, exclude=set(picked))
                    if not cands:
                        break
                    picked += cands
                if len(picked) >= k:
                    break
            return picked if len(picked) == k else []

        def attach_pairs(exclude_ranks, k) -> List[int]:
            pool = [r for r in ranks_sorted
                    if r not in exclude_ranks and counts[r] >= 2]
            pool.sort(key=lambda r: (counts[r], r))
            picked: List[int] = []
            for r in pool:
                if len(picked) + 2 <= 2 * k:
                    picked += _pick_cards(hand, r, 2)
                if len(picked) >= 2 * k:
                    break
            return picked if len(picked) == 2 * k else []

        if last.ptype == 'bomb':
            for r in ranks_sorted:
                if counts[r] == 4 and r > last.rank:
                    out.append(Move('bomb', r, 1, cards_of(r, 4)))
        elif last.ptype == 'single':
            for r in ranks_sorted:
                if r > last.rank:
                    out.append(Move('single', r, 1, cards_of(r, 1)))
        elif last.ptype == 'pair':
            for r in ranks_sorted:
                if r > last.rank and counts[r] >= 2:
                    out.append(Move('pair', r, 1, cards_of(r, 2)))
        elif last.ptype == 'triple':
            for r in ranks_sorted:
                if r > last.rank and counts[r] >= 3:
                    out.append(Move('triple', r, 1, cards_of(r, 3)))
        elif last.ptype == 'triple1':
            for r in ranks_sorted:
                if r > last.rank and counts[r] >= 3:
                    wing = attach_singles({r}, 1)
                    if wing:
                        out.append(Move('triple1', r, 1, cards_of(r, 3) + wing))
        elif last.ptype == 'triple2':
            for r in ranks_sorted:
                if r > last.rank and counts[r] >= 3:
                    wing = attach_pairs({r}, 1)
                    if wing:
                        out.append(Move('triple2', r, 1, cards_of(r, 3) + wing))
        elif last.ptype == 'straight':
            L = last.length
            for start in range(last.rank + 1, 12 - L + 1):
                if all(counts.get(r, 0) >= 1 for r in range(start, start + L)):
                    cs = []
                    for r in range(start, start + L):
                        cs += _pick_cards(hand, r, 1)
                    out.append(Move('straight', start, L, cs))
        elif last.ptype == 'pair_seq':
            k = last.length
            for start in range(last.rank + 1, 12 - k + 1):
                if all(counts.get(r, 0) >= 2 for r in range(start, start + k)):
                    cs = []
                    for r in range(start, start + k):
                        cs += _pick_cards(hand, r, 2)
                    out.append(Move('pair_seq', start, k, cs))
        elif last.ptype in ('plane', 'plane1', 'plane2'):
            k = last.length
            for start in range(last.rank + 1, 12 - k + 1):
                if not all(counts.get(r, 0) >= 3 for r in range(start, start + k)):
                    continue
                body: List[int] = []
                for r in range(start, start + k):
                    body += _pick_cards(hand, r, 3)
                if last.ptype == 'plane':
                    out.append(Move('plane', start, k, body))
                elif last.ptype == 'plane1':
                    wing = attach_singles(set(range(start, start + k)), k)
                    if wing:
                        out.append(Move('plane1', start, k, body + wing))
                else:
                    wing = attach_pairs(set(range(start, start + k)), k)
                    if wing:
                        out.append(Move('plane2', start, k, body + wing))
        elif last.ptype == 'four2':
            for r in ranks_sorted:
                if counts[r] == 4 and r > last.rank:
                    wing = attach_singles({r}, 2)
                    if wing:
                        out.append(Move('four2', r, 1, cards_of(r, 4) + wing))
        elif last.ptype == 'four2pair':
            for r in ranks_sorted:
                if counts[r] == 4 and r > last.rank:
                    wing = attach_pairs({r}, 2)
                    if wing:
                        out.append(Move('four2pair', r, 1, cards_of(r, 4) + wing))

        # 炸弹 / 王炸可压一切非炸
        if last.ptype != 'bomb':
            for r in ranks_sorted:
                if counts[r] == 4:
                    out.append(Move('bomb', r, 1, cards_of(r, 4)))
        if counts.get(13) and counts.get(14):
            out.append(Move('rocket', 14, 1,
                            _pick_cards(hand, 13, 1) + _pick_cards(hand, 14, 1)))
        return out

    # ------------------------------------------------------------------ #
    # 领出候选
    # ------------------------------------------------------------------ #
    def _gen_leads(self, hand, counts: Counter) -> List[Move]:
        out: List[Move] = []
        ranks_sorted = sorted(counts)

        def cards_of(r, need):
            return _pick_cards(hand, r, need)

        for r in ranks_sorted:
            out.append(Move('single', r, 1, cards_of(r, 1)))
            if counts[r] >= 2:
                out.append(Move('pair', r, 1, cards_of(r, 2)))
            if counts[r] >= 3:
                out.append(Move('triple', r, 1, cards_of(r, 3)))

        # 顺子（各种长度）
        for L in range(5, 13):
            for start in range(0, 12 - L + 1):
                if all(counts.get(r, 0) >= 1 for r in range(start, start + L)):
                    cs = []
                    for r in range(start, start + L):
                        cs += _pick_cards(hand, r, 1)
                    out.append(Move('straight', start, L, cs))

        # 连对
        for k in range(3, 11):
            for start in range(0, 12 - k + 1):
                if all(counts.get(r, 0) >= 2 for r in range(start, start + k)):
                    cs = []
                    for r in range(start, start + k):
                        cs += _pick_cards(hand, r, 2)
                    out.append(Move('pair_seq', start, k, cs))

        # 飞机（纯 / 带单 / 带对）
        def attach_singles(exclude_ranks, k):
            pool = sorted((r for r in ranks_sorted if r not in exclude_ranks),
                          key=lambda r: (counts[r], r))
            picked: List[int] = []
            for r in pool:
                while len(picked) < k:
                    c = _pick_cards(hand, r, 1, exclude=set(picked))
                    if not c:
                        break
                    picked += c
                if len(picked) >= k:
                    break
            return picked if len(picked) == k else []

        def attach_pairs(exclude_ranks, k):
            pool = sorted((r for r in ranks_sorted
                           if r not in exclude_ranks and counts[r] >= 2),
                          key=lambda r: (counts[r], r))
            picked: List[int] = []
            for r in pool:
                if len(picked) + 2 <= 2 * k:
                    picked += _pick_cards(hand, r, 2)
                if len(picked) >= 2 * k:
                    break
            return picked if len(picked) == 2 * k else []

        for k in range(2, 7):
            for start in range(0, 12 - k + 1):
                if not all(counts.get(r, 0) >= 3 for r in range(start, start + k)):
                    continue
                body = []
                for r in range(start, start + k):
                    body += _pick_cards(hand, r, 3)
                out.append(Move('plane', start, k, body))
                wing = attach_singles(set(range(start, start + k)), k)
                if wing:
                    out.append(Move('plane1', start, k, body + wing))
                wing = attach_pairs(set(range(start, start + k)), k)
                if wing:
                    out.append(Move('plane2', start, k, body + wing))

        # 四带二 / 四带两对
        for r in ranks_sorted:
            if counts[r] == 4:
                body = cards_of(r, 4)
                wing = attach_singles({r}, 2)
                if wing:
                    out.append(Move('four2', r, 1, body + wing))
                wing = attach_pairs({r}, 2)
                if wing:
                    out.append(Move('four2pair', r, 1, body + wing))
                out.append(Move('bomb', r, 1, body))

        if counts.get(13) and counts.get(14):
            out.append(Move('rocket', 14, 1,
                            _pick_cards(hand, 13, 1) + _pick_cards(hand, 14, 1)))
        return out
