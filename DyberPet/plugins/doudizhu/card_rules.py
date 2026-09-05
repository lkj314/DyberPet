# coding:utf-8
"""斗地主牌型规则——纯逻辑，不 import 任何 UI / 桌宠代码，可独立单测。

牌的编码：int 0..53
  0..51  = 普通牌，rank_idx = c // 4（0='3' ... 11='A' 12='2'），suit = c % 4
  52     = 小王 (rank_idx 13)
  53     = 大王 (rank_idx 14)
大小顺序：3 < 4 < ... < K < A < 2 < 小王 < 大王（即 rank_idx 单调）
"""
from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Optional

RANK_STR = ['3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A', '2', '小王', '大王']
SUITS = ['♠', '♥', '♣', '♦']
MAX_STRAIGHT_RANK = 11  # 顺子/连对/飞机最大到 A；2 与王不能进序列


def card_rank(c: int) -> int:
    return c // 4 if c < 52 else (13 if c == 52 else 14)


def card_label(c: int) -> str:
    r = card_rank(c)
    if r >= 13:
        return RANK_STR[r]
    return RANK_STR[r] + SUITS[c % 4]


def cards_label(cards) -> str:
    return ' '.join(card_label(c) for c in cards)


def card_is_red(c: int) -> bool:
    return card_rank(c) < 13 and c % 4 in (1, 3)


def sort_cards(cards) -> List[int]:
    return sorted(cards, key=card_rank, reverse=True)


def deal():
    """返回 (3 份 17 张手牌[降序], 3 张底牌[降序])。"""
    deck = list(range(52)) + [52, 53]
    random.shuffle(deck)
    hands = [sort_cards(deck[i * 17:(i + 1) * 17]) for i in range(3)]
    bottom = sort_cards(deck[51:])
    return hands, bottom


# ------------------------------------------------------------------ #
# 牌型
# ------------------------------------------------------------------ #
@dataclass
class Move:
    ptype: str          # single/pair/triple/triple1/triple2/straight/pair_seq/
                        # plane/plane1/plane2/four2/four2pair/bomb/rocket
    rank: int           # 主牌 rank_idx（顺子/连对/飞机为最小 rank）
    length: int = 1     # 序列长度（顺子张数 / 连对组数 / 飞机翼数）
    cards: List[int] = field(default_factory=list)

    def label(self) -> str:
        return cards_label(self.cards)


def detect_move(cards) -> Optional[Move]:
    """把一组牌解析成 Move；非法牌型返回 None。"""
    n = len(cards)
    if n == 0:
        return None
    ranks = [card_rank(c) for c in cards]
    cnt = Counter(ranks)
    distinct = sorted(cnt)

    if n == 2 and cnt.get(13) == 1 and cnt.get(14) == 1:
        return Move('rocket', 14, 1, list(cards))
    if n == 1:
        return Move('single', ranks[0], 1, list(cards))
    if n == 2 and len(cnt) == 1:
        return Move('pair', ranks[0], 1, list(cards))
    if n == 3 and len(cnt) == 1:
        return Move('triple', ranks[0], 1, list(cards))
    if n == 4 and len(cnt) == 1:
        return Move('bomb', ranks[0], 1, list(cards))
    if n == 4 and len(cnt) == 2 and 3 in cnt.values():
        tr = [r for r, k in cnt.items() if k == 3][0]
        return Move('triple1', tr, 1, list(cards))
    if n == 5 and len(cnt) == 2 and 3 in cnt.values() and 2 in cnt.values():
        tr = [r for r, k in cnt.items() if k == 3][0]
        return Move('triple2', tr, 1, list(cards))
    # 顺子（>=5 张单、连续、不超过 A）
    if n >= 5 and len(cnt) == n and max(distinct) <= MAX_STRAIGHT_RANK \
            and distinct[-1] - distinct[0] == n - 1:
        return Move('straight', distinct[0], n, list(cards))
    # 连对（>=3 组、连续、不超过 A）
    if n >= 6 and n % 2 == 0 and set(cnt.values()) == {2} \
            and max(distinct) <= MAX_STRAIGHT_RANK \
            and distinct[-1] - distinct[0] == len(distinct) - 1:
        return Move('pair_seq', distinct[0], n // 2, list(cards))
    # 纯飞机（>=2 组连续三张）
    if n >= 6 and n % 3 == 0:
        k = n // 3
        trips = sorted(r for r, c in cnt.items() if c == 3)
        if len(trips) == k and trips[-1] - trips[0] == k - 1 and trips[-1] <= MAX_STRAIGHT_RANK \
                and all(cnt.get(r) == 3 for r in range(trips[0], trips[-1] + 1)):
            return Move('plane', trips[0], k, list(cards))
    # 四带二（两单）
    if n == 6 and 4 in cnt.values():
        f = [r for r, c in cnt.items() if c == 4][0]
        return Move('four2', f, 1, list(cards))
    # 四带两对
    if n == 8 and list(cnt.values()).count(4) == 1 and list(cnt.values()).count(2) == 2:
        f = [r for r, c in cnt.items() if c == 4][0]
        return Move('four2pair', f, 1, list(cards))
    # 飞机带单翅：n=4k，k 组连续三张 + k 张单
    if n >= 8 and n % 4 == 0:
        k = n // 4
        trips = sorted(r for r, c in cnt.items() if c == 3)
        if len(trips) == k and trips[-1] - trips[0] == k - 1 and trips[-1] <= MAX_STRAIGHT_RANK \
                and all(cnt.get(r) == 3 for r in range(trips[0], trips[-1] + 1)) \
                and n - 3 * k == k:
            return Move('plane1', trips[0], k, list(cards))
    # 飞机带对翅：n=5k，k 组连续三张 + k 个对
    if n >= 10 and n % 5 == 0:
        k = n // 5
        trips = sorted(r for r, c in cnt.items() if c == 3)
        if len(trips) == k and trips[-1] - trips[0] == k - 1 and trips[-1] <= MAX_STRAIGHT_RANK \
                and all(cnt.get(r) == 3 for r in range(trips[0], trips[-1] + 1)) \
                and n == 5 * k:
            return Move('plane2', trips[0], k, list(cards))
    return None


def can_beat(m: Move, last: Move) -> bool:
    """m 能否压过 last。"""
    if m.ptype == 'rocket':
        return True
    if last.ptype == 'rocket':
        return False
    if m.ptype == 'bomb':
        return last.ptype != 'bomb' or m.rank > last.rank
    if last.ptype == 'bomb':
        return False
    if m.ptype != last.ptype or m.length != last.length:
        return False
    return m.rank > last.rank
