"""五子棋陪玩 AI——基于评分函数的博弈搜索，difficulty 控制犯错/放水。

设计要点：
- 纯逻辑，不依赖 UI / 桌宠。
- 评分函数对「己方进攻」与「对手威胁」分别打分，取加权和为每个空位的优先级。
- difficulty 映射到「失误率」：萌新几乎全随机，地狱全最优。
- handicap（放水）在失误率基础上再叠加随机，并倾向于选次优着法。
"""
from __future__ import annotations

import random
from typing import Dict, List, Optional, Tuple

from .game_engine import EMPTY, BLACK, WHITE

# 难度 -> 失误率（落子时以该概率不走最优，改走随机着法）
DIFFICULTY_MISTAKE: Dict[str, float] = {
    "萌新": 0.85,
    "简单": 0.55,
    "普通": 0.20,
    "困难": 0.05,
    "地狱": 0.0,
}


def _pattern_base(count: int, open_ends: int) -> int:
    """根据连子数与开放端数给单方向打分。"""
    if count >= 5:
        return 100000
    if count == 4:
        return 10000 if open_ends == 2 else 5000
    if count == 3:
        return 1000 if open_ends == 2 else 500
    if count == 2:
        return 100 if open_ends == 2 else 50
    if count == 1:
        return 10 if open_ends == 2 else 5
    return 0


def _dir_score(board, size, x: int, y: int, player: int, dx: int, dy: int) -> int:
    """假设 (x, y) 已落 player，返回该方向（双向）形成的形态分。"""
    count = 1
    open_ends = 0
    # 正向
    nx, ny = x + dx, y + dy
    while 0 <= nx < size and 0 <= ny < size and board[ny][nx] == player:
        count += 1; nx += dx; ny += dy
    if 0 <= nx < size and 0 <= ny < size and board[ny][nx] == EMPTY:
        open_ends += 1
    # 反向
    nx, ny = x - dx, y - dy
    while 0 <= nx < size and 0 <= ny < size and board[ny][nx] == player:
        count += 1; nx -= dx; ny -= dy
    if 0 <= nx < size and 0 <= ny < size and board[ny][nx] == EMPTY:
        open_ends += 1
    return _pattern_base(count, open_ends)


def _point_score(board, size, x: int, y: int, player: int) -> int:
    """(x, y) 落 player 后，四个方向形态分之和。"""
    total = 0
    for dx, dy in ((1, 0), (0, 1), (1, 1), (1, -1)):
        total += _dir_score(board, size, x, y, player, dx, dy)
    return total


class GomokuAI:
    def __init__(self, mistake_rate: float = 0.20, handicap: bool = False):
        self.mistake_rate = mistake_rate
        self.handicap = handicap

    def configure(self, difficulty: str, handicap: bool = False):
        self.mistake_rate = DIFFICULTY_MISTAKE.get(difficulty, 0.20)
        self.handicap = handicap

    def choose_move(self, engine, ai_player: int) -> Optional[Tuple[int, int]]:
        """为 ai_player 选一个落子点。"""
        opp = BLACK if ai_player == WHITE else WHITE
        moves = engine.candidate_moves()
        if not moves:
            return None

        scored = []
        for x, y in moves:
            # 进攻：己方在此处的形态
            engine.board[y][x] = ai_player
            off = _point_score(engine.board, engine.size, x, y, ai_player)
            # 防守：对手在此处的威胁
            engine.board[y][x] = opp
            deff = _point_score(engine.board, engine.size, x, y, opp)
            engine.board[y][x] = EMPTY
            total = off + deff * 0.9
            scored.append((total, (x, y)))

        scored.sort(key=lambda t: t[0], reverse=True)

        # 失误率：随机走（放水时偏向中低分段，营造「让子」观感）
        rate = min(1.0, self.mistake_rate + (0.2 if self.handicap else 0.0))
        if random.random() < rate:
            if self.handicap and len(scored) > 3:
                # 放水：从 2~末位里挑，避免最优
                pick = random.choice(scored[1:])
            else:
                pick = random.choice(scored)
            return pick[1]

        # 最优；地狱档稳定选第一，其余档在并列高分里随机增加变化
        top = [s for s in scored if s[0] == scored[0][0]]
        return random.choice(top)[1]
