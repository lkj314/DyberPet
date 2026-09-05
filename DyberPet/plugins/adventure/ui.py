# coding:utf-8
"""桌面浮层 UI：道韵分身 + 传讯符（文档 §1.3 / §5.2）。

设计要点：
- 道韵分身：桌宠历练时留守桌面的一缕分身——**本体去历练，元婴来驻形**
  （默认使用韩立元婴 22 帧动画素材，周身绕青色道韵微光；素材缺失时回落
  简笔小剑）。可点击查看"去哪了、去了多久、预计何时回"——桌面永不空置；
- 传讯符：飘落到桌面角落的小纸条，点击展开文字，不点也不骚扰（静置角落）；
- 跨线程文案更新走 Signal（队列安全）。
"""
from __future__ import annotations

import math
import os
import re

from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, Signal, QEasingCurve, QRectF, QPointF
from PySide6.QtGui import (QColor, QFont, QPainter, QPen, QRadialGradient,
                           QLinearGradient, QPainterPath, QPixmap)
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

import DyberPet.settings as settings


def _tool_flags():
    return (Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint |
            Qt.NoDropShadowWindowHint)


class DaoYunWidget(QWidget):
    """道韵分身：元婴帧动画 + 道韵微光。点击 → 打开角色面板「历练」页。

    素材：res/pet/<petname>/action/{prefix}_{i}.png（当前默认韩立元婴）；
    素材不存在时自动回落为简笔小剑绘制。
    """

    FALLBACK_DRAW = 'sword'   # 回落绘制模式
    FRAME_DRAW = 'frames'     # 帧动画模式

    def __init__(self, on_click, parent=None, pet_name: str = '韩立元婴'):
        super().__init__(parent)
        self.setWindowFlags(_tool_flags())
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(88, 88)
        self._phase = 0.0
        self._tip = '道韵分身 · 元婴驻形\n本体外出历练未归'
        self._on_click = on_click
        self.setToolTip(self._tip)

        self._mode = self.FALLBACK_DRAW
        self._frames: list = []
        self._frame_idx = 0
        self._load_frames(pet_name)

        # 拖动状态：区分「点击打开面板」与「拖动换位置」
        self._drag_offset = None
        self._dragging = False
        self._press_gpos = None
        self.setCursor(Qt.OpenHandCursor)

        self._pulse = QTimer(self)
        self._pulse.setInterval(60 if self._mode == self.FRAME_DRAW else 120)
        self._pulse.timeout.connect(self._tick_phase)
        self._pulse.start()

    # ---- 素材加载 ----
    def _load_frames(self, pet_name: str, size: int = 64):
        """尝试加载元婴帧动画（按 act_conf 的 images 前缀 + 数字序排序）。"""
        try:
            base = os.path.join(settings.BASEDIR, 'res', 'pet', pet_name)
            conf_path = os.path.join(base, 'act_conf.json')
            if not os.path.isfile(conf_path):
                return
            import json
            conf = json.load(open(conf_path, encoding='utf-8'))
            images = str(conf.get('default', {}).get('images', ''))
            refresh = float(conf.get('default', {}).get('frame_refresh', 0.06))
            if not images:
                return
            action_dir = os.path.join(base, 'action')
            pat = re.compile(rf'^{re.escape(images)}_(\d+)\.png$')
            pairs = []
            for fn in os.listdir(action_dir):
                m = pat.match(fn)
                if m:
                    pairs.append((int(m.group(1)), os.path.join(action_dir, fn)))
            pairs.sort()
            if len(pairs) < 2:
                return
            frames = []
            for _, path in pairs:
                pm = QPixmap(path)
                if pm.isNull():
                    return                     # 有一帧坏就整体回落，别播放残缺动画
                frames.append(pm.scaled(
                    size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self._frames = frames
            self._interval_ms = max(30, int(refresh * 1000))
            self._mode = self.FRAME_DRAW
        except Exception as e:  # noqa: BLE001
            print(f'[adventure] daoyun frames fallback to sword: {e!r}')
            self._frames = []
            self._mode = self.FALLBACK_DRAW

    # ---- 动画 ----
    def _tick_phase(self):
        self._phase = (self._phase + 0.13) % (2 * math.pi)
        if self._mode == self.FRAME_DRAW:
            self._frame_idx = (self._frame_idx + 1) % len(self._frames)
        self.update()

    def set_tip(self, text: str):
        self._tip = text
        self.setToolTip(text)
        self.update()

    # ---- 位置与拖动 ----
    def snap_bottom_right(self):
        """固定到桌面右下角（贴角）。只在首次显示时调用，之后位置归用户拖动。"""
        geo = QApplication.primaryScreen().availableGeometry()
        self.move(geo.right() - self.width() - 8,
                  geo.bottom() - self.height() - 8)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.pos()
            self._press_gpos = event.globalPosition().toPoint()
            self._dragging = False
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_offset is None:
            return
        gpos = event.globalPosition().toPoint()
        if not self._dragging and (gpos - self._press_gpos).manhattanLength() > 6:
            self._dragging = True
        if self._dragging:
            self.move(gpos - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            was_click = not self._dragging
            self._drag_offset = None
            self._dragging = False
            self._press_gpos = None
            self.setCursor(Qt.OpenHandCursor)
            if was_click:
                # 位移极小 → 视为点击，打开历练面板
                try:
                    self._on_click()
                except Exception:  # noqa: BLE001
                    pass
            event.accept()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        cx, cy = self.width() / 2, self.height() / 2
        glow = 0.55 + 0.30 * math.sin(self._phase)

        # 外圈微光（径向渐变，青色道韵——元婴周身萦绕）
        g = QRadialGradient(cx, cy, 40)
        c = QColor(96, 220, 200)
        c.setAlpha(int(80 * glow))
        g.setColorAt(0.0, c)
        c2 = QColor(96, 220, 200)
        c2.setAlpha(0)
        g.setColorAt(1.0, c2)
        p.setBrush(g)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(cx, cy), 40, 40)

        if self._mode == self.FRAME_DRAW and self._frames:
            # 元婴帧动画（帧本身自带飘浮姿态；外加极轻呼吸浮动）
            bob = 1.4 * math.sin(self._phase * 0.9)
            pm = self._frames[self._frame_idx]
            p.drawPixmap(QPointF(cx - pm.width() / 2,
                                 cy - pm.height() / 2 + bob), pm)
        else:
            self._paint_sword(p, cx, cy)

        # 底部小字提示
        p.setPen(QColor(120, 200, 188, int(170 * glow)))
        f = QFont()
        f.setPixelSize(9)
        p.setFont(f)
        p.drawText(QRectF(0, self.height() - 13, self.width(), 12),
                   Qt.AlignCenter, '道韵 · 元婴' if self._mode == self.FRAME_DRAW else '道韵')

    def _paint_sword(self, p: QPainter, cx: float, cy: float):
        """素材缺失时的回落绘制：简笔悬浮小剑。"""
        bob = 2.2 * math.sin(self._phase * 0.9)
        p.save()
        p.translate(cx, cy + bob)
        p.rotate(35)
        blade = QLinearGradient(0, -24, 0, 8)
        blade.setColorAt(0.0, QColor(210, 245, 240, 235))
        blade.setColorAt(1.0, QColor(110, 200, 188, 220))
        p.setBrush(blade)
        pen = QPen(QColor(60, 150, 138, 220), 1.2)
        p.setPen(pen)
        path = QPainterPath()
        path.moveTo(0, -26)
        path.lineTo(4.2, -14)
        path.lineTo(4.2, 10)
        path.lineTo(0, 15)
        path.lineTo(-4.2, 10)
        path.lineTo(-4.2, -14)
        path.closeSubpath()
        p.drawPath(path)
        # 剑格与剑柄
        p.setBrush(QColor(150, 120, 70, 230))
        p.drawRect(QRectF(-8, 10, 16, 3.4))
        p.drawRect(QRectF(-1.6, 13.4, 3.2, 9))
        p.restore()


class TalismanWidget(QWidget):
    """传讯符：角落的小纸条，点击展开/收起，× 关闭。"""

    closed = Signal(object)          # 关闭后通知管理层重排
    textArrived = Signal(str)        # LLM 文案异步回填（跨线程安全）

    def __init__(self, idx: int, total: int, text: str, parent=None):
        super().__init__(parent)
        self.setWindowFlags(_tool_flags())
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedWidth(248)
        self._expanded = False
        self._idx, self._total = idx, total

        self._bg = QLabel(self)
        self._bg.setStyleSheet(
            'background:#f7ecd2; border:1px solid #c9a86a; border-radius:6px;')
        self._bg.setGeometry(6, 0, 236, 62)

        self.title = QLabel(self)
        self.title.setText(f'✉ 传讯符 {idx + 1}/{total}')
        self.title.setStyleSheet('color:#7a5b23; font-weight:bold;')
        self.title.setGeometry(16, 5, 150, 16)

        self.closeBtn = QPushButton('×', self)
        self.closeBtn.setFixedSize(18, 18)
        self.closeBtn.setStyleSheet(
            'QPushButton{color:#8a6d3b; background:transparent; border:none;'
            'font-size:14px;} QPushButton:hover{color:#c0392b;}')
        self.closeBtn.clicked.connect(self._dismiss)

        self.body = QLabel(self)
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(Qt.NoTextInteraction)
        self.body.setStyleSheet('color:#4a3a18;')
        self.body.setGeometry(16, 24, 216, 32)
        self.body.setCursor(Qt.PointingHandCursor)

        self.set_text(text)
        self.textArrived.connect(self._on_text)
        self.adjustSize()
        self.resize(248, 62)

        # 淡入动画
        self.setWindowOpacity(0.0)
        self._anim = QPropertyAnimation(self, b'windowOpacity', self)
        self._anim.setDuration(420)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    # ---- 展示 ----
    def set_text(self, text: str):
        self._text = str(text or '')
        elided = self._text if len(self._text) <= 40 else self._text[:38] + '…'
        self.body.setText(elided)
        if self._expanded:
            self._apply_expand()

    def _on_text(self, text: str):
        if text:
            self.set_text(text)

    def popup_at(self, x: int, y: int):
        self.move(x, y)
        self.show()
        self._anim.start()

    def move_to(self, x: int, y: int):
        self.move(x, y)

    # ---- 交互 ----
    def mousePressEvent(self, event):
        if self.body.underMouse() or True:
            self._toggle()

    def _toggle(self):
        self._expanded = not self._expanded
        self._apply_expand()

    def _apply_expand(self):
        if self._expanded:
            self.body.setText(self._text)
            self.body.adjustSize()
            h = max(70, self.body.height() + 34)
            self._bg.setGeometry(6, 0, 236, h)
            self.resize(248, h)
        else:
            elided = self._text if len(self._text) <= 40 else self._text[:38] + '…'
            self.body.setText(elided)
            self._bg.setGeometry(6, 0, 236, 62)
            self.resize(248, 62)

    def _dismiss(self):
        self.hide()
        self.close()
        self.closed.emit(self)

    @staticmethod
    def base_position() -> tuple:
        geo = QApplication.primaryScreen().availableGeometry()
        return geo.right() - 262, geo.bottom() - 150
