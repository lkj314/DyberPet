# coding:utf-8
"""斗地主视图层 v2——按真实斗地主手感重做（用户实测反馈）。

变更要点：
- **桌宠只演一个 AI**（左上「肥牛」），右上「路人甲」是静默人机，不再精分。
- **牌型语音**：谁出牌都触发 voice/ 里的预合成 mp3（"对三！""王炸！！""要不起"），
  桌宠关键事件用专属情绪语音（叫地主/炸弹得意/胜负）。
- **提示按钮**：循环给出当前所有合法出法（最优在前），再次点击换下一手；
  选中金框高亮 + 文案显示牌型与压制目标；无解时明示"要不起"并自动禁用出牌。
- **选中即校验**：选牌实时显示牌型与"可出/压不过"，出牌按钮联动。
- **军师**：本地算牌即时给确定性提示（高亮+文案+预合成语音），Ollama 只负责
  把记牌简报润色成狗头军师建议上气泡——**语音永远播预合成牌型音**，
  绝不实时 TTS 念 LLM 输出（LLM 长文/算牌过程念出来没人听得懂）。
"""
from __future__ import annotations

import random
from typing import List, Optional

from PySide6.QtCore import Qt, QTimer, QRect, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QPushButton, QWidget

from .ai_engine import AIPlayer
from .card_rules import (Move, can_beat, card_is_red, card_label,
                         card_rank, detect_move, move_desc)
from .commentary import Commentator
from .game_engine import DoudizhuEngine, IllegalMove
from .voice import VoiceBank

W, H = 880, 640
CW, CH = 62, 88          # 手牌尺寸
OVER = 34                # 手牌重叠
SMW, SMH = 40, 56        # 出牌区迷你牌
SM_OVER = 18

PATTERN_NAME = {
    'single': '单张', 'pair': '对子', 'triple': '三张', 'triple1': '三带一',
    'triple2': '三带二', 'straight': '顺子', 'pair_seq': '连对',
    'plane': '飞机', 'plane1': '飞机带翅', 'plane2': '飞机带对',
    'four2': '四带二', 'four2pair': '四带两对', 'bomb': '炸弹', 'rocket': '王炸',
}

THEMES = {
    '经典': {'bg': QColor(245, 240, 228, 248), 'felt': QColor(70, 110, 75),
             'line': QColor(120, 90, 50), 'text': QColor(60, 40, 20),
             'accent': QColor(212, 160, 23)},
    '霓虹': {'bg': QColor(18, 22, 36, 248), 'felt': QColor(10, 16, 30),
             'line': QColor(0, 229, 255), 'text': QColor(220, 235, 255),
             'accent': QColor(255, 214, 64)},
    '像素': {'bg': QColor(56, 56, 66, 248), 'felt': QColor(38, 38, 48),
             'line': QColor(255, 200, 60), 'text': QColor(240, 240, 220),
             'accent': QColor(255, 200, 60)},
}

SEAT_NAME = {0: '你', 1: '肥牛', 2: '路人甲'}


class DouDizhuWindow(QWidget):
    """斗地主牌桌。座位：0=玩家(下方)，1=桌宠「肥牛」(左上)，2=路人甲(右上)。"""

    # Ollama 军师回传主线程：(文本, 失败原因)——成功时原因为空串
    advisorReady = Signal(str, str)

    def __init__(self, api, commentator: Commentator, voices: VoiceBank,
                 parent=None):
        super().__init__(parent)
        self.api = api
        self.commentator = commentator
        self.voices = voices
        self.engine = DoudizhuEngine()
        self.ais = {1: AIPlayer(1), 2: AIPlayer(2)}

        self.selected: set = set()
        self._sel_move: Optional[Move] = None
        self._sel_can = False
        self._hint_moves: Optional[List[Move]] = None
        self._hint_idx = -1
        self._bottom_show: List[int] = []
        self._bid_buttons: List[QPushButton] = []
        self._result_btn: Optional[QPushButton] = None
        self._critical_announced: set = set()
        self._status = ''
        self._advisor_asked = False
        self._advisor_pending = False
        self._advisor_hint: Optional[Move] = None    # 本地算牌的最优候选
        self._advisor_spoken = False                 # 该次询问语音是否已播
        self._advisor_timer = QTimer(self)
        self._advisor_timer.setSingleShot(True)
        self._advisor_timer.timeout.connect(self._advisor_timeout)

        self.advisorReady.connect(self._on_advisor_ready)

        self.setFixedSize(W, H)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.SubWindow)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)

        self._build_buttons()
        self._new_game()

    # ------------------------------------------------------------------ #
    # 设置实时读取
    # ------------------------------------------------------------------ #
    def _s(self, key, default):
        return self.api.settings.get(key, default) if self.api else default

    def _theme(self):
        return THEMES.get(self._s('card_theme', '经典'), THEMES['经典'])

    def _voice_on(self):
        return bool(self._s('voice', True))

    def _pet_taunt(self):
        return bool(self._s('pet_taunt', True))

    def _use_llm(self):
        return bool(self._s('advisor', False))

    # ------------------------------------------------------------------ #
    # 控件
    # ------------------------------------------------------------------ #
    def _mk_btn(self, text, x, y, w, h, bg, on_click):
        b = QPushButton(text, self)
        b.setGeometry(x, y, w, h)
        b.setStyleSheet(
            f"QPushButton{{color:#fff;background:{bg};border-radius:8px;"
            f"font-weight:bold;}} QPushButton:hover{{opacity:0.85;}} "
            f"QPushButton:disabled{{color:#bbb;background:#666;}}")
        b.clicked.connect(on_click)
        return b

    def _build_buttons(self):
        self.closeBtn = self._mk_btn('×', W - 40, 10, 28, 28, '#e0524c',
                                     self.close)
        self.restartBtn = self._mk_btn('重开', W - 82, 10, 36, 28, '#4a7dbe',
                                       self._new_game)
        self.advisorBtn = self._mk_btn('军师', W // 2 - 256, H - 62, 88, 34,
                                       '#7a5aa8', self._ask_advisor)
        self.hintBtn = self._mk_btn('提示', W // 2 - 148, H - 62, 88, 34,
                                    '#5a6b8c', self._on_hint)
        self.passBtn = self._mk_btn('不要', W // 2 - 40, H - 62, 88, 34,
                                    '#8a6d3b', self._on_pass)
        self.playBtn = self._mk_btn('出牌', W // 2 + 68, H - 62, 88, 34,
                                    '#3a8f4a', self._on_play)
        for i, (txt, sc) in enumerate((('不叫', 0), ('1 分', 1),
                                       ('2 分', 2), ('3 分', 3))):
            b = self._mk_btn(txt, W // 2 - 170 + i * 90, 330, 76, 40,
                             '#8a5a2b', lambda _=False, s=sc: self._on_bid(s))
            b.hide()
            self._bid_buttons.append(b)

    # ------------------------------------------------------------------ #
    # 对局流程
    # ------------------------------------------------------------------ #
    def _new_game(self):
        self.engine.reset()
        self.selected.clear()
        self._sel_move = None
        self._sel_can = False
        self._hint_moves = None
        self._hint_idx = -1
        self._advisor_asked = False
        self._advisor_pending = False
        self._advisor_hint = None
        self._advisor_spoken = False
        self._advisor_timer.stop()
        self._bid_buttons_visibility(False)
        if self._result_btn is not None:
            self._result_btn.deleteLater()
            self._result_btn = None
        self._critical_announced.clear()
        self._bottom_show = list(self.engine.bottom)
        for ai in self.ais.values():
            ai.configure(int(self._s('difficulty', 3)))
        self._status = ''
        self._pet_say('game_start')
        self._advance_bid()

    # ---- 叫地主 ----
    def _advance_bid(self):
        self.update()
        seat = self.engine.next_bidder()
        if seat is None:
            return
        if seat == 0:
            self._bid_buttons_visibility(True)
        else:
            QTimer.singleShot(random.randint(700, 1200),
                              lambda s=seat: self._ai_bid(s))

    def _bid_buttons_visibility(self, show):
        cur = self.engine._current_bid
        for i, b in enumerate(self._bid_buttons):
            if show and (i == 0 or i > cur):
                b.show()
            else:
                b.hide()

    def _on_bid(self, score: int):
        if self.engine.phase != 'bidding':
            return
        self._bid_buttons_visibility(False)
        res = self.engine.bid(0, score)
        self._after_bid(res)

    def _ai_bid(self, seat: int):
        if self.engine.phase != 'bidding':
            return
        view = self.engine.build_view(seat)
        score = self.ais[seat].decide_bid(view)
        if seat == 1:
            # 桌宠叫分：预合成语音 + 气泡
            if score:
                self.voices.play(self.api, 'pet_landlord')
                self._pet_say('become_landlord', force_voice=False)
            else:
                self.voices.play(self.api, 'pet_no_bid')
        res = self.engine.bid(seat, score)
        self._after_bid(res)

    def _after_bid(self, res: str):
        if res == 'redeal':
            self._status = '三家都不叫，重新发牌…'
            self.update()
            QTimer.singleShot(1600, self._new_game)
            return
        if res == 'ok':
            self._status = ''
            if self.engine.landlord == 1:
                self._pet_say('become_landlord')
            elif self.engine.landlord == 2:
                self._pet_say('become_farmer')
            self._advance_play()
            return
        self._advance_bid()

    # ---- 出牌回合 ----
    def _advance_play(self):
        self.update()
        if self.engine.phase != 'playing':
            return
        if self.engine.turn == 0:
            self._hint_moves = None          # 新回合重置提示循环
            self._advisor_asked = False
            self._refresh_selection_state()
            self.passBtn.setEnabled(self.engine.last_move is not None
                                    and self.engine.last_player != 0)
            if (self._use_llm() and not self._advisor_asked
                    and not self._advisor_pending
                    and self.engine.last_player != 0):
                self._ask_advisor(auto=True)
        else:
            self.playBtn.setEnabled(False)
            self.passBtn.setEnabled(False)
            self.hintBtn.setEnabled(False)
            self.advisorBtn.setEnabled(False)
            QTimer.singleShot(random.randint(750, 1300),
                              lambda s=self.engine.turn: self._ai_move(s))

    def _ai_move(self, seat: int):
        if self.engine.phase != 'playing' or self.engine.turn != seat:
            return
        view = self.engine.build_view(seat)
        action = self.ais[seat].decide_play(view)

        if action == 'pass':
            self._do_pass(seat)
        else:
            # 队友告急送牌 -> 桌宠配合台词
            if (seat == 1 and view['role'] == 'farmer'
                    and view.get('teammate_remaining') is not None
                    and view['teammate_remaining'] <= 2
                    and view['last_move'] is None
                    and action.ptype == 'single'):
                self._do_play(seat, action, pet_event='teammate_coop')
            else:
                self._do_play(seat, action)

        self._check_critical(seat)
        self._finish_if_over()
        self._advance_play()

    def _do_play(self, seat: int, move: Move, pet_event: Optional[str] = None):
        """出牌执行：引擎 -> 牌型语音（三家通用）-> 桌宠专属事件。"""
        try:
            played = self.engine.play(seat, move)
        except IllegalMove as e:
            # AI 决策理论上永远合法；真出问题就过牌兜底，绝不崩
            print(f'[doudizhu] AI illegal move: {e}', flush=True)
            try:
                self.engine.pass_turn(seat)
            except IllegalMove:
                pass
            return
        if self._voice_on():
            if seat == 1 and played.ptype in ('bomb', 'rocket'):
                self._pet_say('play_bomb' if played.ptype == 'bomb'
                              else 'play_rocket')
            else:
                self.voices.play_move(self.api, played)
        if pet_event and seat == 1:
            self._pet_say(pet_event)

    def _do_pass(self, seat: int):
        try:
            self.engine.pass_turn(seat)
        except IllegalMove as e:
            print(f'[doudizhu] pass refused: {e}', flush=True)
            return
        if self._voice_on():
            if seat == 1:
                self._pet_say('pet_pass')
            else:
                self.voices.play(self.api, 'pass')

    def _check_critical(self, _seat):
        if self.engine.landlord is None:
            return
        n = len(self.engine.hands[self.engine.landlord])
        if n in (1, 2) and n not in self._critical_announced:
            self._critical_announced.add(n)
            if self.engine.landlord == 1:
                self._pet_say('landlord_critical')

    # ---- 玩家操作 ----
    def _on_play(self):
        if self.engine.phase != 'playing' or self.engine.turn != 0:
            return
        hand = self.engine.hands[0]
        cards = sorted((hand[i] for i in self.selected), key=card_rank)
        if not cards:
            return
        move = detect_move(cards)
        if move is None:
            self._flash('不是合法牌型')
            return
        if self.engine.last_move is not None and self.engine.last_player != 0 \
                and not can_beat(move, self.engine.last_move):
            self._flash('压不过上家')
            return
        try:
            played = self.engine.play(0, move)
        except IllegalMove as e:
            self._flash(str(e))
            return
        if self._voice_on():
            self.voices.play_move(self.api, played)
        self.selected.clear()
        self._sel_move = None
        self._sel_can = False
        self._hint_moves = None
        self._check_critical(0)
        self._finish_if_over()
        if self.engine.phase == 'playing':
            self._advance_play()

    def _on_pass(self):
        if self.engine.phase != 'playing' or self.engine.turn != 0:
            return
        if self.engine.last_move is None or self.engine.last_player == 0:
            self._flash('你领出，必须出牌')
            return
        self._do_pass(0)
        if self._voice_on() and random.random() < 0.45:
            self._pet_say('taunt_pass')
        self.selected.clear()
        self._sel_move = None
        self._sel_can = False
        self._hint_moves = None
        self._finish_if_over()
        if self.engine.phase == 'playing':
            self._advance_play()

    def _on_hint(self):
        """提示：循环给出所有合法出法（最优在前）；高亮 + 文案；无解明示。"""
        if self.engine.phase != 'playing' or self.engine.turn != 0:
            return
        if self._hint_moves is None:
            view = self.engine.build_view(0)
            self._hint_moves = self.helper_moves(view)
            self._hint_idx = -1
            if not self._hint_moves:
                self._hint_moves = []
                self._flash('要不起，点「不要」吧')
                self.playBtn.setEnabled(False)
                return
        self._hint_idx = (self._hint_idx + 1) % len(self._hint_moves)
        mv = self._hint_moves[self._hint_idx]
        hand = self.engine.hands[0]
        self.selected = {hand.index(c) for c in mv.cards}   # 提示高亮也走索引
        self._refresh_selection_state()
        self._flash(f'提示 {self._hint_idx + 1}/{len(self._hint_moves)}：'
                    f'{self._move_hint_text(mv)}')
        self.update()

    def helper_moves(self, view: dict) -> List[Move]:
        helper = AIPlayer(0)
        helper.configure(5)
        return helper.all_moves(view)

    # ---- 军师 ----
    def _move_hint_text(self, mv: Move) -> str:
        """提示文案：牌型 + 压制目标（压谁的什么牌）。"""
        txt = move_desc(mv)
        if self.engine.last_move is not None and self.engine.last_player != 0:
            txt += (f'（压{SEAT_NAME.get(self.engine.last_player, "上家")}'
                    f'的{move_desc(self.engine.last_move)}）')
        return txt

    def _apply_hint(self, mv: Optional[Move]):
        """确定性出牌提示：高亮推荐牌 + 文案 + 预合成牌型语音。
        mv=None 表示要不起。不依赖 Ollama，永远即时可用。"""
        if self.engine.phase != 'playing' or self.engine.turn != 0:
            return
        self._hint_moves = None          # 高亮后重置提示循环
        if mv is None:
            self.selected.clear()
            self._refresh_selection_state()
            self._flash('要不起，点「不要」吧')
            if self._s('advisor_tts', True):
                self.voices.play(self.api, 'pass')       # 预合成「要不起」
            self.update()
            return
        hand = self.engine.hands[0]
        self.selected = {hand.index(c) for c in mv.cards}
        self._refresh_selection_state()
        self._flash(f'建议：{self._move_hint_text(mv)}')
        if self._s('advisor_tts', True):
            self.voices.play_move(self.api, mv)          # 预合成「单K」「对5」…
        self.update()

    def _ask_advisor(self, auto: bool = False):
        if self.engine.phase not in ('bidding', 'playing'):
            return
        view = self.engine.build_view(0)
        # ---- 本地确定性算牌：候选既注入 LLM 简报，也直接当提示用 ----
        cands: List[Move] = []
        hint: Optional[Move] = None
        if self.engine.phase == 'playing' and self.engine.turn == 0:
            try:
                cands = self.helper_moves(view)
            except Exception:  # noqa: BLE001
                cands = []
            hint = cands[0] if cands else None
        self._advisor_hint = hint
        self._advisor_spoken = False

        if not auto:
            # 军师按钮先给确定性反馈（高亮+文案+预合成语音）——
            # Ollama 挂了/没开也照样有提示，不再只甩一句"未开启"
            if self.engine.phase == 'playing' and self.engine.turn == 0:
                self._apply_hint(hint)
            else:
                self._flash('叫分阶段没有出牌建议，先叫地主吧')
        if not self._use_llm():
            return
        if self._advisor_pending:
            return                      # 上一问还没回来，不叠问
        self._advisor_asked = True
        self._advisor_pending = True
        if not auto:
            self._status = '军师在想…'
            self.update()
        # 35 秒兜底：即使回调链全部失联也能解除等待态
        self._advisor_timer.start(35000)
        brief = Commentator.build_brief(view)
        # 算牌注入：本地引擎的合法候选（最优在前）喂给军师——
        # LLM 拿确定性候选给建议，不再凭空瞎说
        if cands:
            cand_txt = '、'.join(move_desc(m) for m in cands[:8])
            brief += f'可出候选（从优到劣）：{cand_txt}。'
        self.commentator.request_advisor(
            brief, lambda text, error: self.advisorReady.emit(text or '', error or ''))

    def _on_advisor_ready(self, text, error=''):
        """军师回话：LLM 文本只上气泡；语音走预合成牌型音，绝不念 LLM 长文。
        失败/超时/空回复也必须解除等待态并明示原因。"""
        self._advisor_pending = False
        self._advisor_timer.stop()
        was_waiting = (self._status == '军师在想…')
        if was_waiting:
            self._status = ''
        if not text:
            if was_waiting:
                # 手动问时确定性提示（高亮+语音）已在 _apply_hint 给过，
                # 这里只补失败原因，不打断玩家
                self._flash(error or '军师走神了（Ollama 未响应，检查服务与模型）')
            return
        if self.api is None:
            return
        self.api.pet.say(f'军师：{text}')
        self.api.pet.react('happy')
        # 语音：播预合成牌型音（"单K！""对5"），离线零延迟、永远听得懂。
        # 手动问时 _apply_hint 已播过（_advisor_spoken=True），不重播；
        # 自动问时在此补播。
        if (self._s('advisor_tts', True) and not self._advisor_spoken
                and self.engine.phase == 'playing'
                and self.engine.turn == 0):
            mv = self._advisor_hint
            if mv is not None and set(mv.cards) <= set(self.engine.hands[0]):
                self.voices.play_move(self.api, mv)
            elif mv is None:
                self.voices.play(self.api, 'pass')
            self._advisor_spoken = True

    def _advisor_timeout(self):
        if not self._advisor_pending:
            return
        self._advisor_pending = False
        if self._status == '军师在想…':
            self._flash('军师想太久了，先自己拿主意吧')

    # ---- 结算 ----
    def _finish_if_over(self):
        if self.engine.winner is None:
            return
        side, _seat = self.engine.result()
        self.playBtn.setEnabled(False)
        self.passBtn.setEnabled(False)
        self.hintBtn.setEnabled(False)
        self.advisorBtn.setEnabled(False)
        player_is_landlord = self.engine.landlord == 0
        pet_is_winner = ((side == 'landlord') == (self.engine.landlord == 1))
        if side == 'farmer' and self.engine.landlord is not None:
            landlord_plays = sum(1 for s, m in self.engine.history
                                 if s == self.engine.landlord and m)
            if landlord_plays <= 1:
                self._pet_say('spring')
                ev = 'spring'
            else:
                ev = 'result_win' if pet_is_winner else 'result_lose'
        else:
            ev = 'result_win' if pet_is_winner else 'result_lose'
        # 斗法历练：胜利（含春天）转化修为（修仙放置联动）
        if self.api is not None:
            try:
                if ev == 'spring':
                    self.api.add_exp(1000, "斗地主春天")
                    self.api.add_adventure_buff()   # 历练 buff：大杀四方 +10%
                elif pet_is_winner:
                    self.api.add_exp(600, "斗地主获胜")
                    self.api.add_adventure_buff()   # 历练 buff：对局悟道 +10%
            except Exception:  # noqa: BLE001
                pass
        self._pet_say(ev)
        self._status = ''
        self._result_btn = self._mk_btn(
            '再来一局', W // 2 - 60, H // 2 + 60, 120, 40, '#3a8f4a',
            self._new_game)
        self.update()

    # ------------------------------------------------------------------ #
    # 桌宠台词 + 语音
    # ------------------------------------------------------------------ #
    def _pet_say(self, event: Optional[str], force_voice: Optional[bool] = None):
        """桌宠本体说话：气泡（受吐槽开关/必播事件控制）+ 预合成语音。"""
        if event is None:
            return
        text, emotion = self.commentator.on_event(event)
        if not text:
            return
        always = event in ('game_start', 'become_landlord', 'result_win',
                           'result_lose', 'spring')
        if self.api is not None and (always or self._pet_taunt()):
            self.api.pet.say(text)
            self.api.pet.react(emotion)
            # 人设吐槽异步追补 + 结局写入历练记忆（修仙人设角色）
            if event in ('result_win', 'result_lose', 'spring'):
                try:
                    self.commentator.request_pet_quip(
                        event, callback=lambda t: self.api.pet.say(t))
                except Exception:  # noqa: BLE001
                    pass
                try:
                    from DyberPet.persona_service import add_memory
                    add_memory('与道友斗地主，' + {
                        'result_win': '胜了一局',
                        'result_lose': '输了一局',
                        'spring': '打出了一手春天'}.get(event, ''),
                        tags=['doudizhu'])
                except Exception:  # noqa: BLE001
                    pass
        play_voice = self._voice_on() if force_voice is None else force_voice
        if play_voice:
            key = self.commentator.voice_for(event)
            if key:
                self.voices.play(self.api, key)

    def _flash(self, msg: str):
        self._status = msg
        self.update()
        QTimer.singleShot(1500, lambda: self._clear_flash(msg))

    def _clear_flash(self, msg):
        if self._status == msg:
            self._status = ''
            self.update()

    # ------------------------------------------------------------------ #
    # 选牌 + 实时校验
    # ------------------------------------------------------------------ #
    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        if self.engine.phase != 'playing' or self.engine.turn != 0:
            return
        hand = self.engine.hands[0]
        x0, y = self._hand_origin(len(hand))
        px, py = e.pos().x(), e.pos().y()
        for i in range(len(hand) - 1, -1, -1):
            cx = x0 + i * OVER
            top = y - (16 if i in self.selected else 0)
            if cx <= px <= cx + CW and top <= py <= top + CH:
                # selected 存索引（非牌值）：牌值 0..53 会与手牌索引 0..19 错配，
                # 导致高亮判断 `i in selected` 错位甚至永远不命中
                if i in self.selected:
                    self.selected.discard(i)
                else:
                    self.selected.add(i)
                self._hint_moves = None      # 手动改动后重新开始提示循环
                self._refresh_selection_state()
                self.update()
                return

    def _refresh_selection_state(self):
        self._sel_move = None
        self._sel_can = False
        hand = self.engine.hands[0]
        if self.selected and all(0 <= i < len(hand) for i in self.selected):
            # selected 存的是手牌索引，必须先换算成真实牌值再评估——
            # 直接拿索引喂 detect_move 会产生幻影牌型：大王在手牌第 0 位
            # 会被当成 3♠（牌值 0，rank 0），出现「大王压不过 10」的错判
            cards = sorted((hand[i] for i in self.selected), key=card_rank)
            mv = detect_move(cards)
            self._sel_move = mv
            if mv is not None:
                leading = (self.engine.last_move is None
                           or self.engine.last_player == 0)
                self._sel_can = leading or can_beat(mv, self.engine.last_move)
        if self.engine.phase == 'playing' and self.engine.turn == 0:
            self.playBtn.setEnabled(bool(self.selected) and self._sel_can)
            self.hintBtn.setEnabled(True)
            self.advisorBtn.setEnabled(True)

    def _hand_origin(self, n: int):
        total = OVER * (n - 1) + CW
        x0 = max(20, (W - total) // 2)
        return x0, H - CH - 70

    # ------------------------------------------------------------------ #
    # 绘制
    # ------------------------------------------------------------------ #
    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        th = self._theme()

        p.setBrush(th['bg'])
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(0, 0, W, H, 16, 16)
        p.setBrush(th['felt'])
        p.drawRoundedRect(14, 46, W - 28, H - 60, 12, 12)

        p.setPen(th['text'])
        p.setFont(QFont('Microsoft YaHei', 13, QFont.Bold))
        p.drawText(QRect(0, 8, W - 96, 26), Qt.AlignCenter,
                   '斗地主 · 肥牛陪玩')
        p.setFont(QFont('Microsoft YaHei', 9))
        p.drawText(QRect(16, 12, 160, 22), Qt.AlignLeft,
                   f'倍数 x{self.engine.multiplier}')

        self._draw_bottom(p, th)
        self._draw_seat(p, th, 1, 36, 66)
        self._draw_seat(p, th, 2, W - 200, 66)
        self._draw_tricks(p, th)
        self._draw_hand(p, th)
        self._draw_selection_hint(p, th)

        if self._status:
            p.setPen(QColor(255, 90, 60))
            p.setFont(QFont('Microsoft YaHei', 11, QFont.Bold))
            p.drawText(QRect(0, H - 120, W, 26), Qt.AlignCenter, self._status)

        if self.engine.phase == 'bidding' and self.engine.next_bidder() == 0:
            self._draw_banner(p, '轮到你叫地主')

        if self.engine.winner is not None:
            self._draw_result(p)

    def _draw_banner(self, p, text):
        p.setBrush(QColor(0, 0, 0, 130))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(W // 2 - 160, 250, 320, 44, 10, 10)
        p.setPen(QColor(255, 255, 255))
        p.setFont(QFont('Microsoft YaHei', 13, QFont.Bold))
        p.drawText(QRect(W // 2 - 160, 250, 320, 44), Qt.AlignCenter, text)

    def _draw_bottom(self, p, th):
        p.setPen(th['text'])
        p.setFont(QFont('Microsoft YaHei', 9))
        p.drawText(QRect(W // 2 - 80, 48, 160, 18), Qt.AlignCenter, '底牌')
        revealed = self.engine.landlord is not None
        x0 = W // 2 - (3 * (SMW + 6) - 6) // 2
        for i in range(3):
            if revealed and i < len(self._bottom_show):
                self._draw_card(p, x0 + i * (SMW + 6), 68, SMW, SMH,
                                self._bottom_show[i])
            else:
                self._draw_back(p, x0 + i * (SMW + 6), 68, SMW, SMH, th)

    def _draw_seat(self, p, th, seat: int, x: int, y: int):
        name = SEAT_NAME[seat]
        role = self.engine.role(seat)
        cnt = len(self.engine.hands[seat])
        color = QColor(80, 160, 220) if seat == 1 else th['text']
        p.setPen(color)
        p.setFont(QFont('Microsoft YaHei', 11, QFont.Bold))
        tag = '👑地主' if role == 'landlord' else ('农民' if role else '')
        p.drawText(x, y, f'{name} {tag}')
        p.setPen(th['text'])
        p.setFont(QFont('Microsoft YaHei', 9))
        p.drawText(x + 90, y, f'剩 {cnt} 张')
        shown = min(cnt, 8)
        for i in range(shown):
            self._draw_back(p, x + i * 14, y + 22, 30, 42, th)

    def _draw_tricks(self, p, th):
        zones = {1: (48, 190), 2: (W - 48 - 7 * (SMW + SM_OVER), 190),
                 0: (W // 2 - 160, 250)}
        last_action = {}
        for s, m in self.engine.history:
            last_action[s] = m
        for seat, m in last_action.items():
            zx, zy = zones[seat]
            if m is None:
                p.setBrush(QColor(0, 0, 0, 110))
                p.setPen(Qt.NoPen)
                p.drawRoundedRect(zx, zy + SMH // 2 - 14, 64, 28, 8, 8)
                p.setPen(QColor(255, 255, 255))
                p.setFont(QFont('Microsoft YaHei', 10))
                p.drawText(QRect(zx, zy + SMH // 2 - 14, 64, 28),
                           Qt.AlignCenter, '不要')
            else:
                for i, c in enumerate(m.cards[:10]):
                    self._draw_card(p, zx + i * SM_OVER, zy, SMW, SMH, c)
            p.setPen(th['text'])

    def _draw_hand(self, p, th):
        hand = self.engine.hands[0]
        if not hand:
            return
        x0, y = self._hand_origin(len(hand))
        for i, c in enumerate(hand):
            sel = i in self.selected
            cy = y - (16 if sel else 0)
            self._draw_card(p, x0 + i * OVER, cy, CW, CH, c)
            if sel:
                p.setPen(QPen(th['accent'], 2))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(x0 + i * OVER, cy, CW, CH, 5, 5)

    def _draw_selection_hint(self, p, th):
        """选中牌的实时校验提示（仅玩家回合）。"""
        if self.engine.phase != 'playing' or self.engine.turn != 0:
            return
        if not self.selected:
            return
        if self._sel_move is None:
            txt = '不是合法牌型'
        else:
            name = PATTERN_NAME.get(self._sel_move.ptype,
                                    self._sel_move.ptype)
            leading = (self.engine.last_move is None
                       or self.engine.last_player == 0)
            ok = leading or self._sel_can
            txt = f'{name} · {"可出" if ok else "压不过上家"}'
        p.setPen(th['accent'])
        p.setFont(QFont('Microsoft YaHei', 10, QFont.Bold))
        p.drawText(QRect(0, H - CH - 96, W, 22), Qt.AlignCenter, txt)

    def _draw_card(self, p, x, y, w, h, c):
        p.setBrush(QColor(255, 255, 255))
        p.setPen(QPen(QColor(150, 150, 150), 1))
        p.drawRoundedRect(int(x), int(y), int(w), int(h), 5, 5)
        label = card_label(c)
        red = card_is_red(c)
        color = QColor(210, 50, 40) if red else QColor(30, 30, 30)
        p.setPen(color)
        rank_str = label[:-1] if len(label) > 2 else label
        suit_str = label[-1] if len(label) > 2 else ''
        if card_rank(c) >= 13:
            p.setFont(QFont('Microsoft YaHei', int(w * 0.22), QFont.Bold))
            p.drawText(QRect(int(x), int(y), int(w), int(h)), Qt.AlignCenter,
                       label)
        else:
            p.setFont(QFont('Arial', int(w * 0.30), QFont.Bold))
            p.drawText(QRect(int(x) + 3, int(y) + 2, int(w) - 6, int(h * 0.5)),
                       Qt.AlignLeft | Qt.AlignTop, rank_str)
            p.setFont(QFont('Arial', int(w * 0.26)))
            p.drawText(QRect(int(x) + 3, int(y) + int(h * 0.42),
                             int(w) - 6, int(h * 0.5)),
                       Qt.AlignLeft | Qt.AlignTop, suit_str)

    def _draw_back(self, p, x, y, w, h, th):
        p.setBrush(QColor(40, 70, 130))
        p.setPen(QPen(QColor(220, 230, 250), 1))
        p.drawRoundedRect(int(x), int(y), int(w), int(h), 4, 4)
        p.setPen(QPen(QColor(255, 255, 255, 90), 1))
        p.drawRoundedRect(int(x) + 3, int(y) + 3, int(w) - 6, int(h) - 6,
                          3, 3)

    def _draw_result(self, p):
        p.setBrush(QColor(0, 0, 0, 150))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(0, 0, W, H, 16, 16)
        side, _seat = self.engine.result()
        player_is_landlord = self.engine.landlord == 0
        player_won = (side == 'landlord') == player_is_landlord
        msg = '你赢了！🎉' if player_won else '你输了…'
        sub = (f"{'地主' if side == 'landlord' else '农民'}方获胜 · "
               f"倍数 x{self.engine.multiplier}")
        p.setPen(QColor(255, 255, 255))
        p.setFont(QFont('Microsoft YaHei', 28, QFont.Bold))
        p.drawText(QRect(0, H // 2 - 90, W, 60), Qt.AlignCenter, msg)
        p.setFont(QFont('Microsoft YaHei', 12))
        p.drawText(QRect(0, H // 2 - 24, W, 30), Qt.AlignCenter, sub)
