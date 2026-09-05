# coding:utf-8
"""斗地主对局引擎——发牌 / 叫地主 / 回合流转 / 胜负倍数。

纯逻辑，不 import 任何 UI / 桌宠代码。

⚠️ 信息隔离（本项目第一风险点）：
AI 一律通过 :meth:`build_view` 获取自己视角的数据——只含自己手牌、
公开历史、各家剩余张数；底牌仅地主可见；**绝不包含其他玩家手牌**。
"""
from __future__ import annotations

from collections import Counter
from typing import List, Optional, Tuple

from .card_rules import Move, card_rank, deal, detect_move, sort_cards

SEAT_NAMES = ['玩家', 'AI-A', 'AI-B']


class IllegalMove(Exception):
    pass


class DoudizhuEngine:
    """座位：0=玩家, 1=AI-A, 2=AI-B。"""

    def __init__(self):
        self.reset()

    # ------------------------------------------------------------------ #
    def reset(self):
        self.hands, self.bottom = deal()
        self.phase = 'bidding'          # bidding -> playing -> over
        self.landlord: Optional[int] = None
        self.multiplier = 1
        self._current_bid = 0
        self._bid_winner: Optional[int] = None
        self._bid_i = 0                  # 叫分顺序固定 0 -> 1 -> 2
        self.history: List[Tuple[int, Optional[Move]]] = []
        self.last_move: Optional[Move] = None
        self.last_player: Optional[int] = None
        self._passes = 0
        self.turn = 0
        self.winner: Optional[int] = None

    # ------------------------------------------------------------------ #
    # 叫地主
    # ------------------------------------------------------------------ #
    def next_bidder(self) -> Optional[int]:
        if self.phase != 'bidding' or self._bid_i >= 3:
            return None
        return self._bid_i            # 按座位顺序 0,1,2

    def bid(self, seat: int, score: int):
        """score: 0=不叫, 1/2/3=叫分。返回 'pending' | 'ok' | 'redeal'。"""
        if self.phase != 'bidding':
            raise IllegalMove('not in bidding phase')
        if seat != self._bid_i:
            raise IllegalMove('not this seat\'s turn to bid')
        score = int(score)
        if score not in (0, 1, 2, 3):
            raise IllegalMove('bad bid score')
        if score <= self._current_bid and score != 0:
            raise IllegalMove('bid must be higher')

        if score > self._current_bid:
            self._current_bid = score
            self._bid_winner = seat
        self._bid_i += 1

        if self._bid_i >= 3:
            if self._bid_winner is None:
                return 'redeal'
            self._set_landlord(self._bid_winner)
            return 'ok'
        return 'pending'

    def _set_landlord(self, seat: int):
        self.landlord = seat
        self.multiplier = max(1, self._current_bid)
        self.hands[seat] = sort_cards(self.hands[seat] + self.bottom)
        self.bottom = []
        self.phase = 'playing'
        self.turn = seat

    def role(self, seat: int) -> Optional[str]:
        if self.landlord is None:
            return None
        return 'landlord' if seat == self.landlord else 'farmer'

    def teammate(self, seat: int) -> Optional[int]:
        """农民的队友；地主无队友返回 None。"""
        if self.role(seat) != 'farmer':
            return None
        return (set(range(3)) - {seat, self.landlord}).pop()

    # ------------------------------------------------------------------ #
    # 信息隔离：唯一对 AI 暴露的数据入口
    # ------------------------------------------------------------------ #
    def build_view(self, seat: int) -> dict:
        """构造 seat 视角的对局视图。❌ 不含其他玩家的具体手牌。"""
        landlord_remaining = (len(self.hands[self.landlord])
                              if self.landlord is not None else None)
        teammate = self.teammate(seat)
        return {
            'seat': seat,
            'phase': self.phase,
            'role': self.role(seat),
            'landlord': self.landlord,
            'my_cards': list(self.hands[seat]),
            'my_count': len(self.hands[seat]),
            'played': [(s, (m.ptype, m.rank, m.length) if m else None)
                       for s, m in self.history],
            'played_cards': [c for _s, m in self.history if m for c in m.cards],
            'last_move': self.last_move,
            'last_player': self.last_player,
            'remaining': {s: len(self.hands[s]) for s in range(3) if s != seat},
            'landlord_remaining': landlord_remaining,
            'teammate': teammate,
            'teammate_remaining': len(self.hands[teammate]) if teammate is not None else None,
            'bottom': list(self.bottom) if (seat == self.landlord and self.bottom) else None,
            'multiplier': self.multiplier,
            'current_bid': self._current_bid,
            'turn': self.turn,
            'winner': self.winner,
        }

    # ------------------------------------------------------------------ #
    # 出牌 / 过牌
    # ------------------------------------------------------------------ #
    def play(self, seat: int, move: Move) -> Move:
        if self.phase != 'playing' or self.winner is not None:
            raise IllegalMove('game not running')
        if seat != self.turn:
            raise IllegalMove('not your turn')

        hand = self.hands[seat]
        hand_set = set(hand)
        if not set(move.cards) <= hand_set or len(set(move.cards)) != len(move.cards):
            raise IllegalMove('cards not in hand')
        detected = detect_move(move.cards)
        if detected is None or detected.ptype != move.ptype \
                or detected.rank != move.rank or detected.length != move.length:
            raise IllegalMove('invalid pattern')
        if self.last_move is not None and self.last_player != seat:
            if not self._beat(detected, self.last_move):
                raise IllegalMove('cannot beat last move')

        for c in move.cards:
            hand.remove(c)
        self.history.append((seat, detected))
        if detected.ptype in ('bomb', 'rocket'):
            self.multiplier *= 2
        self.last_move = detected
        self.last_player = seat
        self._passes = 0

        if not hand:
            self.winner = seat
            self.phase = 'over'
        else:
            self._next_turn()
        return detected

    def pass_turn(self, seat: int):
        if self.phase != 'playing' or self.winner is not None:
            raise IllegalMove('game not running')
        if seat != self.turn:
            raise IllegalMove('not your turn')
        if self.last_move is None or self.last_player == seat:
            raise IllegalMove('leading seat cannot pass')

        self.history.append((seat, None))
        self._passes += 1
        if self._passes >= 2:
            # 一圈都不要 → 该轮最后出牌者重新领出
            self.last_move = None
            self.last_player = None
            self._passes = 0
        self._next_turn()

    @staticmethod
    def _beat(m: Move, last: Move) -> bool:
        from .card_rules import can_beat
        return can_beat(m, last)

    def _next_turn(self):
        self.turn = (self.turn + 1) % 3

    # ------------------------------------------------------------------ #
    def result(self):
        """返回 ('landlord' | 'farmer', winner_seat)。"""
        if self.winner is None:
            return None, None
        return ('landlord' if self.winner == self.landlord else 'farmer',
                self.winner)
