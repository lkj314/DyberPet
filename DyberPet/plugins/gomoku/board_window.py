"""五子棋视图层——PySide6 置顶透明无边框棋盘窗口。

仅这一层接触 UI。游戏状态全部在 ``GomokuEngine``，AI 在 ``GomokuAI``，
解说在 ``Commentator``；本窗口只负责：画棋盘、接收点击、调度 AI、把事件交给解说。
所有设置（难度/吐槽/放水/语音/LLM）运行时从 PetAPI.settings 实时读取。
"""
from __future__ import annotations

import random
from typing import Optional, Tuple

from PySide6.QtCore import Qt, QTimer, QRect, Signal
from PySide6.QtGui import QPainter, QColor, QPen, QFont
from PySide6.QtWidgets import QWidget, QPushButton

from .game_engine import EMPTY, BLACK, WHITE

MARGIN = 28
CELL = 34
STONE_R = int(CELL * 0.42)


class GomokuWindow(QWidget):
    # Ollama 异步生成的台词回传主线程
    llmReady = Signal(str)

    def __init__(self, engine, ai, commentator, api, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.ai = ai
        self.commentator = commentator
        self.api = api

        self._player_turn = True
        self._game_over = False

        self.llmReady.connect(self._on_llm_ready)

        n = self.engine.size
        board_px = CELL * (n - 1)
        self._panel_w = board_px + MARGIN * 2
        self._panel_h = board_px + MARGIN * 2 + 46
        self.setFixedSize(self._panel_w, self._panel_h)

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.SubWindow)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)  # 关闭后自动销毁，避免引用悬空

        self._build_controls()
        self._announce("game_start")

    # ------------------------------------------------------------------ #
    # 控件
    # ------------------------------------------------------------------ #
    def _build_controls(self):
        self.closeBtn = QPushButton("×", self)
        self.closeBtn.setGeometry(self._panel_w - 34, 8, 26, 26)
        self.closeBtn.setStyleSheet(
            "QPushButton{color:#fff;background:#e0524c;border-radius:13px;"
            "font-weight:bold;} QPushButton:hover{background:#c33;}")
        self.closeBtn.clicked.connect(self.close)

        self.restartBtn = QPushButton("重开", self)
        self.restartBtn.setGeometry(self._panel_w // 2 - 32, self._panel_h - 38, 64, 28)
        self.restartBtn.setStyleSheet(
            "QPushButton{color:#5a3a1a;background:#f3d9a8;border-radius:6px;}")
        self.restartBtn.clicked.connect(self._restart)

    def _restart(self):
        self.engine.reset()
        self.ai.configure(self._difficulty(), self._handicap())
        self._player_turn = True
        self._game_over = False
        self.update()
        self._announce("game_start")

    # ------------------------------------------------------------------ #
    # 设置实时读取
    # ------------------------------------------------------------------ #
    def _voice(self) -> bool:
        return self.api.settings.get("voice", True) if self.api else True

    def _difficulty(self) -> str:
        return self.api.settings.get("difficulty", "普通") if self.api else "普通"

    def _handicap(self) -> bool:
        return self.api.settings.get("handicap", False) if self.api else False

    def _taunt(self) -> bool:
        return self.api.settings.get("taunt", True) if self.api else True

    def _use_llm(self) -> bool:
        return self.api.settings.get("llm", False) if self.api else False

    # ------------------------------------------------------------------ #
    # 落子流程
    # ------------------------------------------------------------------ #
    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        gx, gy = self._pixel_to_grid(e.pos())
        if gx is None or not self._player_turn or self._game_over:
            return
        if not self.engine.is_empty(gx, gy):
            return
        self._apply_move(gx, gy, BLACK)
        if not self._game_over:
            self._player_turn = False
            QTimer.singleShot(random.randint(350, 850), self._ai_turn)

    def _ai_turn(self):
        self.ai.configure(self._difficulty(), self._handicap())
        move = self.ai.choose_move(self.engine, WHITE)
        if move is None:
            return
        if self._handicap() and random.random() < 0.25:
            self._apply_move(move[0], move[1], WHITE, suppress_threat=True)
            if not self._game_over:
                self._announce("handicap")
        else:
            self._apply_move(move[0], move[1], WHITE)
        if not self._game_over:
            self._player_turn = True

    def _apply_move(self, x, y, player, suppress_threat=False):
        if not self.engine.place(x, y, player):
            return
        self.update()
        if self.engine.winner is not None:
            event = "player_win" if self.engine.winner == BLACK else "ai_win"
            if event == "player_win" and self.api is not None:
                # 斗法历练：胜利转化修为（修仙放置联动）
                try:
                    self.api.add_exp(600, "五子棋获胜")
                    self.api.add_adventure_buff()   # 历练 buff：对弈悟道 +10%
                except Exception:  # noqa: BLE001
                    pass
            # 历练记忆（persona L3）：对局结局写进人设记忆
            try:
                from DyberPet.persona_service import add_memory
                add_memory('与道友对弈五子棋，'
                           + ('胜了一局' if event == "player_win" else '惜败一局'),
                           tags=['gomoku'])
            except Exception:  # noqa: BLE001
                pass
            self._announce(event)
            self._game_over = True
            self._player_turn = False
            return
        if self.engine.is_draw():
            self._announce("draw")
            self._game_over = True
            self._player_turn = False
            return
        if suppress_threat:
            return
        length = self.engine.line_length(x, y, player)
        if length >= 4:
            ev = "player_four" if player == BLACK else "ai_four"
        elif length == 3:
            ev = "player_open_three" if player == BLACK else "ai_open_three"
        else:
            return
        self._announce(ev)

    # ------------------------------------------------------------------ #
    # 解说
    # ------------------------------------------------------------------ #
    def _announce(self, event: str):
        self.commentator.taunt = self._taunt()
        self.commentator.use_llm = self._use_llm()
        text, emotion = self.commentator.on_event(event)
        if text and self.api is not None:
            # 气泡 + 动画始终播报
            self.api.pet.say(text)
            self.api.pet.react(emotion)
            # 语音(TTS)受「语音解说」开关控制（需联网）
            if self._voice():
                self.api.pet.speak(text)
        if self._use_llm() and self.api is not None:
            self.commentator.request_llm(
                event, self._board_desc(), lambda t: self.llmReady.emit(t))

    def _on_llm_ready(self, text: str):
        if text and self.api is not None:
            self.api.pet.say(text)
            if self._voice():
                self.api.pet.speak(text)

    def _board_desc(self) -> str:
        last = self.engine.last_move()
        who = "你" if self.engine.current == BLACK else "我"
        if last:
            return f"轮到{who}，上一步在({last[0]},{last[1]})"
        return f"轮到{who}"

    # ------------------------------------------------------------------ #
    # 绘制
    # ------------------------------------------------------------------ #
    def _pixel_to_grid(self, pos) -> Tuple[Optional[int], Optional[int]]:
        gx = round((pos.x() - MARGIN) / CELL)
        gy = round((pos.y() - MARGIN) / CELL)
        n = self.engine.size
        if 0 <= gx < n and 0 <= gy < n:
            cx = MARGIN + gx * CELL
            cy = MARGIN + gy * CELL
            if (pos.x() - cx) ** 2 + (pos.y() - cy) ** 2 <= (CELL * 0.5) ** 2:
                return gx, gy
        return None, None

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        # 外层面板
        p.setBrush(QColor(255, 250, 240, 240))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(0, 0, self._panel_w, self._panel_h, 16, 16)

        # 标题
        p.setPen(QColor(90, 60, 30))
        p.setFont(QFont("Microsoft YaHei", 12, QFont.Bold))
        p.drawText(QRect(0, 6, self._panel_w - 40, 24),
                   Qt.AlignCenter, "五子棋 · 肥牛陪玩")

        # 木质棋盘
        n = self.engine.size
        board_px = CELL * (n - 1)
        bx, by = MARGIN - 14, MARGIN - 14
        p.setBrush(QColor(222, 184, 135))
        p.setPen(QPen(QColor(120, 80, 40), 2))
        p.drawRoundedRect(bx, by, board_px + 28, board_px + 28, 8, 8)

        # 网格
        p.setPen(QPen(QColor(60, 40, 20), 1))
        for i in range(n):
            off = MARGIN + i * CELL
            p.drawLine(off, MARGIN, off, MARGIN + board_px)
            p.drawLine(MARGIN, off, MARGIN + board_px, off)

        # 星位
        if n >= 15:
            for sx, sy in ((3, 3), (11, 3), (3, 11), (11, 11), (7, 7)):
                cx, cy = MARGIN + sx * CELL, MARGIN + sy * CELL
                p.setBrush(QColor(40, 25, 10))
                p.setPen(Qt.NoPen)
                p.drawEllipse(cx - 3, cy - 3, 6, 6)

        # 棋子
        for hy in range(n):
            for hx in range(n):
                c = self.engine.board[hy][hx]
                if c == EMPTY:
                    continue
                cx, cy = MARGIN + hx * CELL, MARGIN + hy * CELL
                if c == BLACK:
                    p.setBrush(QColor(35, 35, 35))
                    p.setPen(QPen(QColor(0, 0, 0), 1))
                else:
                    p.setBrush(QColor(245, 245, 245))
                    p.setPen(QPen(QColor(120, 120, 120), 1))
                p.drawEllipse(cx - STONE_R, cy - STONE_R, STONE_R * 2, STONE_R * 2)

        # 最后一手标记
        last = self.engine.last_move()
        if last:
            cx, cy = MARGIN + last[0] * CELL, MARGIN + last[1] * CELL
            p.setPen(QPen(QColor(220, 40, 40), 2))
            p.drawEllipse(cx - STONE_R + 4, cy - STONE_R + 4,
                          STONE_R * 2 - 8, STONE_R * 2 - 8)

        # 结算遮罩
        if self._game_over:
            p.setBrush(QColor(0, 0, 0, 150))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(0, 0, self._panel_w, self._panel_h, 16, 16)
            msg = "你赢了！" if self.engine.winner == BLACK else (
                "我赢了！" if self.engine.winner == WHITE else "平局")
            p.setPen(QColor(255, 255, 255))
            p.setFont(QFont("Microsoft YaHei", 26, QFont.Bold))
            p.drawText(QRect(0, 0, self._panel_w, self._panel_h - 40),
                       Qt.AlignCenter, msg)
