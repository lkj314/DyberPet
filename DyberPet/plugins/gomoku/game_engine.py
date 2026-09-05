"""五子棋纯逻辑引擎——不依赖任何 UI / 桌宠代码，可独立运行与单元测试。

设计要点（见插件开发规格）：
- ``GomokuEngine`` 只管棋盘状态与规则（落子、胜负、连子数、关键形态检测）。
- 不 import PySide6 / qfluentwidgets / DyberPet 内部模块，方便以后复用到网页版/手机端。
- 棋盘坐标用 (x, y)，x 为列、y 为行，均从 0 开始。
"""
from __future__ import annotations

from typing import List, Optional, Tuple

EMPTY = 0
BLACK = 1  # 玩家
WHITE = 2  # AI
WIN_LEN = 5

_DIRECTIONS = ((1, 0), (0, 1), (1, 1), (1, -1))


class GomokuEngine:
    def __init__(self, size: int = 15):
        self.size = size
        self.reset()

    # ------------------------------------------------------------------ #
    # 基础状态
    # ------------------------------------------------------------------ #
    def reset(self):
        self.board = [[EMPTY] * self.size for _ in range(self.size)]
        self.current: int = BLACK
        self.history: List[Tuple[int, int, int]] = []  # (x, y, player)
        self.winner: Optional[int] = None
        self.win_line: List[Tuple[int, int]] = []

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.size and 0 <= y < self.size

    def is_empty(self, x: int, y: int) -> bool:
        return self.in_bounds(x, y) and self.board[y][x] == EMPTY

    def last_move(self) -> Optional[Tuple[int, int, int]]:
        return self.history[-1] if self.history else None

    # ------------------------------------------------------------------ #
    # 落子
    # ------------------------------------------------------------------ #
    def place(self, x: int, y: int, player: Optional[int] = None) -> bool:
        """在 (x, y) 落子。成功返回 True。player 省略时按 current 走。"""
        if self.winner is not None or not self.is_empty(x, y):
            return False
        p = player or self.current
        self.board[y][x] = p
        self.history.append((x, y, p))
        if self.check_win(x, y, p):
            self.winner = p
        else:
            self.current = WHITE if p == BLACK else BLACK
        return True

    def undo(self) -> bool:
        """撤销上一步（用于 AI 搜索回溯）。"""
        if not self.history:
            return False
        x, y, p = self.history.pop()
        self.board[y][x] = EMPTY
        self.current = p
        self.winner = None
        self.win_line = []
        return True

    # ------------------------------------------------------------------ #
    # 胜负 / 连子
    # ------------------------------------------------------------------ #
    def check_win(self, x: int, y: int, player: Optional[int] = None) -> bool:
        p = player if player is not None else self.board[y][x]
        if p == EMPTY:
            return False
        for dx, dy in _DIRECTIONS:
            line = [(x, y)]
            nx, ny = x + dx, y + dy
            while self.in_bounds(nx, ny) and self.board[ny][nx] == p:
                line.append((nx, ny)); nx += dx; ny += dy
            nx, ny = x - dx, y - dy
            while self.in_bounds(nx, ny) and self.board[ny][nx] == p:
                line.insert(0, (nx, ny)); nx -= dx; ny -= dy
            if len(line) >= WIN_LEN:
                self.win_line = line
                return True
        return False

    def line_length(self, x: int, y: int, player: Optional[int] = None) -> int:
        """返回以 (x, y) 为端点的某个方向（双向）最长连子数（含该点本身）。"""
        p = player if player is not None else self.board[y][x]
        if p == EMPTY:
            return 0
        best = 1
        for dx, dy in _DIRECTIONS:
            cnt = 1
            nx, ny = x + dx, y + dy
            while self.in_bounds(nx, ny) and self.board[ny][nx] == p:
                cnt += 1; nx += dx; ny += dy
            nx, ny = x - dx, y - dy
            while self.in_bounds(nx, ny) and self.board[ny][nx] == p:
                cnt += 1; nx -= dx; ny -= dy
            best = max(best, cnt)
        return best

    def is_draw(self) -> bool:
        return self.winner is None and len(self.history) >= self.size * self.size

    # ------------------------------------------------------------------ #
    # 候选生成（供 AI 使用）
    # ------------------------------------------------------------------ #
    def candidate_moves(self, radius: int = 2) -> List[Tuple[int, int]]:
        """返回「已有棋子周围 radius 格内」的空位；空盘则只给中心。"""
        if not self.history:
            c = self.size // 2
            return [(c, c)]
        seen = set()
        out = []
        for hx, hy, _ in self.history:
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    x, y = hx + dx, hy + dy
                    if self.is_empty(x, y) and (x, y) not in seen:
                        seen.add((x, y))
                        out.append((x, y))
        return out

    def __repr__(self) -> str:
        rows = []
        for row in self.board:
            rows.append(" ".join(".XO"[c] for c in row))
        return "\n".join(rows)
