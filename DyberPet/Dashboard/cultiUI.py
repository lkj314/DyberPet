# coding:utf-8
"""角色面板「修仙之路」页（Dashboard 页面，与角色状态/背包/商店同套骨架）。

布局铁律（本页曾因 parent 用错整页叠加且无法交互）：
- qfluentwidgets 的 `ExpandLayout.addWidget` **不会重挂 parent**——放进
  expandLayout 的卡片必须以 `self.scrollWidget` 为 parent 创建（同 shopUI.ShopView）；
- 悬浮在滚动区上方的卡片（头部/境界卡/操作卡）以 ScrollArea 为 parent + move()，
  与 statusUI 的 StatusCard/BuffCard 同模式；`setViewportMargins` 顶部须盖住悬浮区。

数据全部来自 `cultivation_service.get_core()` 单例；页面**只读不 tick**——
结算驱动仍归玩法插件（5s tick），保证"单一驱动者"不变式。
"""
import os
import json
import datetime

from qfluentwidgets import (ScrollArea, ExpandLayout, PrimaryPushButton, CheckBox,
                            ComboBox, InfoBar, InfoBarPosition, SimpleCardWidget,
                            CaptionLabel, StrongBodyLabel, setFont)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QColor, QFont
from PySide6.QtWidgets import (QWidget, QLabel, QHBoxLayout, QVBoxLayout,
                               QSpacerItem, QSizePolicy)

from DyberPet.cultivation_service import (ALCHEMY_RECIPES, MAX_STAGE, PILL_EFFECTS,
                                          REALMS, fmt_exp, get_core, stage_name)
from .dashboard_widgets import HorizontalSeparator, coinWidget

import DyberPet.settings as settings
from DyberPet.DyberSettings.custom_utils import AvatarImage
from DyberPet.custom_widgets import RoundBarBase

basedir = settings.BASEDIR

GOLD = '#d8a017'
LOG_COLOR = {'breakthrough': GOLD, 'break_fail': '#e04343',
             'epiphany': '#9b59d0', 'exp_gain': '#3aa76d',
             'pill': '#3aa76d', 'stones': '#8a8a8a'}


def _pet_image(petname: str):
    """角色头像：优先 info/pfp，回退默认动作第一帧（与 StatusCard 同逻辑）。"""
    info_file = os.path.join(basedir, 'res/role', petname, 'info', 'info.json')
    pfp_file = None
    if os.path.exists(info_file):
        try:
            info = json.load(open(info_file, 'r', encoding='UTF-8'))
            pfp_file = info.get('pfp', None)
        except Exception:  # noqa: BLE001
            pfp_file = None
    if pfp_file is None:
        try:
            actJson = json.load(open(os.path.join(basedir, 'res/role', petname,
                                                  'act_conf.json'), 'r', encoding='UTF-8'))
            pfp_file = os.path.join(basedir, 'res/role', petname, 'action',
                                    f"{actJson['default']['images']}_0.png")
        except Exception:  # noqa: BLE001
            return None
    else:
        pfp_file = os.path.join(basedir, 'res/role', petname, 'info', pfp_file)
    image = QImage()
    if not image.load(pfp_file):
        return None
    return image


class CultiCard(SimpleCardWidget):
    """境界卡：头像 + 当前境界 + 修为进度条 + 速率/状态行。"""

    def __init__(self, card_w: int, parent=None):
        super().__init__(parent)
        self.setBorderRadius(5)
        self.setFixedSize(card_w, 150)
        self.hBoxLayout = QHBoxLayout(self)
        self.hBoxLayout.setContentsMargins(30, 5, 30, 5)
        self.hBoxLayout.setSpacing(18)
        self.hBoxLayout.setAlignment(Qt.AlignCenter)

        self.pfpLabel = None
        img = _pet_image(settings.petname)
        if img is not None:
            self.pfpLabel = AvatarImage(img, edge_size=80, frameColor="#ffffff")
            self.hBoxLayout.addWidget(self.pfpLabel, 0, Qt.AlignVCenter)

        vBox = QVBoxLayout()
        vBox.setContentsMargins(0, 4, 0, 4)
        vBox.setSpacing(8)

        self.realmLabel = CaptionLabel('——')
        setFont(self.realmLabel, 18, QFont.DemiBold)
        self.realmLabel.setFixedHeight(26)

        # 修为进度行（与 HP/FV 条同款 RoundBarBase）
        expRow = QHBoxLayout()
        expRow.setContentsMargins(2, 0, 2, 0)
        expRow.setSpacing(8)
        expText = CaptionLabel(self.tr("Culti"))
        setFont(expText, 13, QFont.Normal)
        expText.setFixedSize(48, expText.height())
        self.expBar = RoundBarBase(fill_color="#50d8be", parent=self)
        self.expBar.setMinimum(0)
        self.expBar.setMaximum(10000)
        self.expBar.setFormat('')
        self.expBar.setAlignment(Qt.AlignCenter)
        self.expBar.setFixedHeight(18)
        expRow.addWidget(expText, 0, Qt.AlignVCenter)
        expRow.addWidget(self.expBar, 1, Qt.AlignVCenter)

        self.rateLabel = CaptionLabel('——')
        setFont(self.rateLabel, 13, QFont.Normal)

        self.statusLabel = CaptionLabel('')
        setFont(self.statusLabel, 12, QFont.Normal)
        self.statusLabel.setStyleSheet('color: gray;')

        vBox.addStretch(1)
        vBox.addWidget(self.realmLabel)
        vBox.addWidget(HorizontalSeparator(QColor(20, 20, 20, 125), 1))
        vBox.addLayout(expRow)
        vBox.addWidget(self.rateLabel)
        vBox.addWidget(self.statusLabel)
        vBox.addStretch(1)

        self.hBoxLayout.addLayout(vBox, 1)

    def set_avatar(self, petname: str):
        """切换角色时重建头像。"""
        img = _pet_image(petname)
        if img is None:
            return
        if self.pfpLabel is not None:
            self.hBoxLayout.removeWidget(self.pfpLabel)
            self.pfpLabel.deleteLater()
        self.pfpLabel = AvatarImage(img, edge_size=80, frameColor="#ffffff")
        self.hBoxLayout.insertWidget(0, self.pfpLabel, 0, Qt.AlignVCenter)

    def refresh(self, core):
        import time as _time
        now = _time.time()
        stage = core.stage()
        self.realmLabel.setText(stage_name(stage))

        have, need = core.stage_progress()
        frac = 1.0 if need <= 0 else max(0.0, min(1.0, have / need))
        self.expBar.setValue(int(frac * 10000))
        can_break = stage <= MAX_STAGE and have >= need
        self.expBar.setBarColor(GOLD if can_break else "#50d8be")
        if stage > MAX_STAGE:
            self.expBar.setFormat('飞升')
        else:
            self.expBar.setFormat(f'{fmt_exp(have)} / {fmt_exp(need)}')

        base, mults, final = core.get_rate()
        rate_txt = f'速率 {fmt_exp(final)}/秒'
        if mults:
            rate_txt += f'（基础 {fmt_exp(base)}，' + '，'.join(
                f'{k}×{m:g}' for k, m in mults.items()) + '）'
        self.rateLabel.setText(rate_txt)

        if stage > MAX_STAGE:
            st = '已飞升仙界'
        elif can_break:
            st = '修为圆满，可以突破！'
        elif now < core.break_after:
            st = f'突破失败冷却中（{int(core.break_after - now)} 秒）'
        elif now < core.weak_until:
            st = f'走火入魔虚弱中（{int(core.weak_until - now)} 秒）'
        elif core.dual_on:
            st = '双修打坐中……（被抚摸会打断）'
        else:
            st = '日常挂机修行中'
        self.statusLabel.setText(st)


class CultiActionCard(SimpleCardWidget):
    """操作卡：突破按钮 + 双修开关 + 自动突破开关。"""

    def __init__(self, card_w: int, on_break, on_dual, on_auto, parent=None):
        super().__init__(parent)
        self.setBorderRadius(5)
        self.setFixedSize(card_w, 48)
        self.on_break = on_break
        self.on_dual = on_dual
        self.on_auto = on_auto

        hBox = QHBoxLayout(self)
        hBox.setContentsMargins(15, 5, 15, 5)
        hBox.setSpacing(14)
        hBox.setAlignment(Qt.AlignCenter)

        self.breakBtn = PrimaryPushButton('突破', self)
        self.breakBtn.setFixedWidth(110)
        self.breakBtn.clicked.connect(self._do_break)
        self.breakBtn.setDisabled(True)

        self.dualCheck = CheckBox('双修打坐', self)
        self.dualCheck.setToolTip('开启后修炼速率 ×2.0；抚摸桌宠会自动打断')
        self.dualCheck.toggled.connect(self._on_dual)

        self.autoCheck = CheckBox('自动突破', self)
        self.autoCheck.setToolTip('修为圆满后自动尝试突破；关闭则需手动点按钮')
        self.autoCheck.toggled.connect(self._on_auto)

        hBox.addWidget(self.breakBtn)
        hBox.addStretch(1)
        hBox.addWidget(self.dualCheck)
        hBox.addWidget(self.autoCheck)

    def _do_break(self):
        try:
            self.on_break()
        except Exception as e:  # noqa: BLE001
            print(f'[cultiUI] break failed: {e!r}')

    def _on_dual(self, checked):
        try:
            self.on_dual(bool(checked))
        except Exception as e:  # noqa: BLE001
            print(f'[cultiUI] dual failed: {e!r}')

    def _on_auto(self, checked):
        try:
            self.on_auto(bool(checked))
        except Exception as e:  # noqa: BLE001
            print(f'[cultiUI] auto failed: {e!r}')

    def set_state(self, core, can_break: bool):
        """按核心状态同步 UI（blockSignals 防回环）。"""
        if can_break:
            p = core.breakthrough_chance()
            self.breakBtn.setText(f'突破（{p:.0%}）')
        else:
            self.breakBtn.setText('突破')
        self.breakBtn.setEnabled(can_break)

        self.dualCheck.blockSignals(True)
        self.dualCheck.setChecked(core.dual_on)
        self.dualCheck.blockSignals(False)

        self.autoCheck.blockSignals(True)
        self.autoCheck.setChecked(core.auto_break)
        self.autoCheck.blockSignals(False)


class AlchemyCard(SimpleCardWidget):
    """炼丹炉：选丹方 → 耗灵石 →（韩立炼丹演出）→ 丹药进背包（与商店同源）。"""

    def __init__(self, card_w: int, core, parent=None):
        super().__init__(parent)
        self.core = core
        self.setBorderRadius(5)
        self.setFixedSize(card_w, 128)

        vBox = QVBoxLayout(self)
        vBox.setContentsMargins(15, 10, 15, 12)
        vBox.setSpacing(8)

        titleRow = QHBoxLayout()
        title = StrongBodyLabel('炼丹炉')
        setFont(title, 14, QFont.DemiBold)
        hint = CaptionLabel('修炼产出灵石 → 炼丹产出丹药 → 服丹获得修行增益')
        setFont(hint, 12, QFont.Normal)
        hint.setStyleSheet('color: gray;')
        titleRow.addWidget(title, 0, Qt.AlignLeft | Qt.AlignVCenter)
        titleRow.addStretch(1)
        titleRow.addWidget(hint, 0, Qt.AlignLeft | Qt.AlignVCenter)
        vBox.addLayout(titleRow)

        row = QHBoxLayout()
        row.setSpacing(12)
        self.recipeBox = ComboBox(self)
        self.recipeBox.setFixedWidth(150)
        for name, cost, seconds in ALCHEMY_RECIPES:
            self.recipeBox.addItem(text=name, userData=name)
        self.recipeBox.currentIndexChanged.connect(self._on_recipe_changed)

        self.detailLabel = CaptionLabel('——')
        setFont(self.detailLabel, 12, QFont.Normal)
        self.detailLabel.setStyleSheet('color: gray;')

        self.startBtn = PrimaryPushButton('开始炼丹', self)
        self.startBtn.setFixedWidth(110)
        self.startBtn.clicked.connect(self._on_start)

        row.addWidget(self.recipeBox, 0, Qt.AlignLeft | Qt.AlignVCenter)
        row.addWidget(self.detailLabel, 1, Qt.AlignLeft | Qt.AlignVCenter)
        row.addWidget(self.startBtn, 0, Qt.AlignRight | Qt.AlignVCenter)
        vBox.addLayout(row)

        self.statusLabel = CaptionLabel('丹炉空闲')
        setFont(self.statusLabel, 12, QFont.Normal)
        self.statusLabel.setStyleSheet('color: gray;')
        vBox.addWidget(self.statusLabel, 0, Qt.AlignLeft | Qt.AlignVCenter)
        self._on_recipe_changed(0)

    # ---- 交互 ----
    def _current_recipe(self):
        name = self.recipeBox.currentData()
        return next((r for r in ALCHEMY_RECIPES if r[0] == name), None)

    def _on_recipe_changed(self, _idx):
        r = self._current_recipe()
        if r is None:
            return
        name, cost, seconds = r
        eff = PILL_EFFECTS.get(name, {}).get('desc', '')
        mins, secs = divmod(int(seconds), 60)
        self.detailLabel.setText(f'{eff} ｜ 灵石 {cost} ｜ 耗时 {mins}分{secs:02d}秒')

    def _on_start(self):
        r = self._current_recipe()
        if r is None:
            return
        name, cost, _seconds = r
        coins = int(getattr(settings.pet_data, 'coins', 0))
        if coins < cost:
            InfoBar.warning('灵石不足', f'炼制「{name}」需要 {cost} 灵石，'
                                       f'当前持有 {coins}（修炼可获得灵石）',
                            duration=3000, parent=self.window(),
                            position=InfoBarPosition.TOP)
            return
        result = self.core.start_alchemy(name)
        if result.get('ok'):
            InfoBar.success('开炉！', result.get('msg', ''),
                            duration=2500, parent=self.window(),
                            position=InfoBarPosition.TOP)
        else:
            InfoBar.warning('炼丹失败', result.get('msg', ''),
                            duration=2500, parent=self.window(),
                            position=InfoBarPosition.TOP)
        self.refresh(self.core)

    # ---- 刷新 ----
    def refresh(self, core):
        state, pill, remain = core.alchemy_status()
        if state == 'idle':
            self.statusLabel.setText('丹炉空闲')
            self.startBtn.setEnabled(True)
        elif state == 'refining':
            mins, secs = divmod(int(remain) + 1, 60)
            self.statusLabel.setText(
                f'炼制中：「{pill}」剩 {mins}分{secs:02d}秒'
                f'（离线也照常炼制，丹成自动放进背包）')
            self.startBtn.setEnabled(False)
        else:
            self.statusLabel.setText(f'「{pill}」已炼成，放进了背包')
            self.startBtn.setEnabled(True)


class RealmOverviewCard(SimpleCardWidget):
    """十境总览：当前境界金色高亮。"""

    def __init__(self, card_w: int, parent=None):
        super().__init__(parent)
        self.setBorderRadius(5)
        self.setFixedWidth(card_w)

        vBox = QVBoxLayout(self)
        vBox.setContentsMargins(15, 10, 15, 12)
        vBox.setSpacing(6)

        title = StrongBodyLabel('境界总览')
        setFont(title, 14, QFont.DemiBold)
        vBox.addWidget(title)

        self.realmLabel = QLabel(self)
        self.realmLabel.setTextFormat(Qt.RichText)
        self.realmLabel.setWordWrap(True)
        vBox.addWidget(self.realmLabel)
        self.adjustSize()

    def refresh(self, stage: int):
        if stage > MAX_STAGE:
            self.realmLabel.setText(
                f'<span style="color:{GOLD}"><b>炼气 → 筑基 → 金丹 → 元婴 → 化神 '
                f'→ 炼虚 → 合体 → 大乘 → 渡劫 → 真仙</b></span>&nbsp;&nbsp;已飞升 🎉')
            self.adjustSize()
            return
        cur_g = stage // 4
        parts = []
        for g, name in enumerate(REALMS):
            if g == cur_g:
                parts.append(f'<span style="color:{GOLD}"><b>{name}</b></span>')
            else:
                parts.append(f'<span style="color:#9a9a9a">{name}</span>')
        self.realmLabel.setText(' → '.join(parts))
        self.adjustSize()


class CultiLogCard(SimpleCardWidget):
    """修行日志：与角色状态页的状态日志同风格（时间 + 彩色事件）。"""

    def __init__(self, card_w: int, parent=None):
        super().__init__(parent)
        self.setBorderRadius(5)
        self.setFixedWidth(card_w)

        self.vBox = QVBoxLayout(self)
        self.vBox.setContentsMargins(15, 10, 15, 12)
        self.vBox.setSpacing(4)
        self._text_w = max(200, card_w - 130)   # 日志正文固定宽（换行高度可算）

        title = StrongBodyLabel('修行日志')
        setFont(title, 14, QFont.DemiBold)
        self.vBox.addWidget(title)
        self.vBox.addWidget(HorizontalSeparator(QColor(20, 20, 20, 125), 1))
        self._rows: list = []
        self._empty = CaptionLabel('暂无日志——突破、顿悟、炼丹与斗法收益都会记在这里')
        self._empty.setStyleSheet('color: gray;')
        self.vBox.addWidget(self._empty)
        self.adjustSize()

    def refresh(self, logs: list):
        for w in self._rows:
            w.deleteLater()
        self._rows.clear()
        self._empty.setVisible(not logs)

        for i, entry in enumerate(reversed(logs)):  # 最新在上
            if i > 0:
                sep = HorizontalSeparator(QColor(20, 20, 20, 60), 1)
                self.vBox.addWidget(sep)
                self._rows.append(sep)
            row = QWidget(self)
            h = QHBoxLayout(row)
            h.setContentsMargins(5, 3, 5, 3)
            h.setSpacing(10)
            t = datetime.datetime.fromtimestamp(entry.get('t', 0)).strftime('%H:%M:%S')
            timeLabel = CaptionLabel(t)
            setFont(timeLabel, 12, QFont.Normal)
            timeLabel.setFixedWidth(70)
            timeLabel.setStyleSheet('color: gray;')

            kind = entry.get('kind', '')
            color = LOG_COLOR.get(kind, '#444444')
            textLabel = CaptionLabel(entry.get('text', ''))
            setFont(textLabel, 13, QFont.Normal)
            textLabel.setWordWrap(True)
            textLabel.setStyleSheet(f'color: {color};')
            # 固定正文宽度让换行在 adjustSize 前生效（否则行高按单行算，
            # 卡片高度偏低 → 每条日志下半截被裁）
            textLabel.setFixedWidth(max(200, self.width() - 130
                                        if self.width() > 200
                                        else self._text_w))

            h.addWidget(timeLabel, 0, Qt.AlignVCenter)
            h.addWidget(textLabel, 1, Qt.AlignVCenter)
            row.adjustSize()
            row.setFixedHeight(max(row.sizeHint().height(), 24))
            self.vBox.addWidget(row)
            self._rows.append(row)
        self.adjustSize()


class cultiInterface(ScrollArea):
    """角色面板「修仙之路」页。"""

    def __init__(self, sizeHintdb: tuple, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("cultiInterface")
        self.scrollWidget = QWidget()
        self.expandLayout = ExpandLayout(self.scrollWidget)
        self._card_w = sizeHintdb[0] - 165

        # header（悬浮，同 statusInterface；右侧灵石与商店/背包同源显示）
        self.headerWidget = QWidget(self)
        self.headerWidget.setFixedWidth(self._card_w)
        self.panelLabel = QLabel('修仙之路', self.headerWidget)
        self.panelLabel.setSizePolicy(QSizePolicy.Maximum,
                                      self.panelLabel.sizePolicy().verticalPolicy())
        self.panelLabel.adjustSize()
        self.coinWidget = coinWidget(self.headerWidget)
        self.headerLayout = QHBoxLayout(self.headerWidget)
        self.headerLayout.setContentsMargins(0, 0, 0, 0)
        self.headerLayout.addWidget(self.panelLabel, Qt.AlignLeft | Qt.AlignVCenter)
        spacer = QSpacerItem(10, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.headerLayout.addItem(spacer)
        self.headerLayout.addWidget(self.coinWidget, Qt.AlignRight | Qt.AlignVCenter)

        # 核心单例（与玩法插件同一路径；只读，不 tick）
        self.core = get_core(os.path.join(settings.CONFIGDIR, 'data',
                                          'cultivation.json'))

        # 悬浮卡（parent=ScrollArea + move()，同 statusUI 的 StatusCard/BuffCard）
        self.CultiCard = CultiCard(self._card_w, self)
        self.ActionCard = CultiActionCard(self._card_w, self._do_break,
                                          self._on_dual, self._on_auto, self)

        # 滚动区卡（⚠️ parent=scrollWidget——ExpandLayout.addWidget 不重挂 parent！）
        self.AlchemyCard = AlchemyCard(self._card_w, self.core, self.scrollWidget)
        self.OverviewCard = RealmOverviewCard(self._card_w, self.scrollWidget)
        self.LogCard = CultiLogCard(self._card_w, self.scrollWidget)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(2000)
        self._refresh_timer.timeout.connect(self.refresh)

        self.__initWidget()

    def __initWidget(self):
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setViewportMargins(0, 300, 0, 20)   # 盖住 header(20~53)+境界卡(75~225)+操作卡(235~283)
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)

        self.__setQss()
        self.__initLayout()

    def __initLayout(self):
        self.headerWidget.move(60, 20)
        self.CultiCard.move(60, 75)
        self.ActionCard.move(60, 235)

        self.expandLayout.setSpacing(28)
        self.expandLayout.setContentsMargins(70, 30, 70, 0)
        self.expandLayout.addWidget(self.AlchemyCard)
        self.expandLayout.addWidget(self.OverviewCard)
        self.expandLayout.addWidget(self.LogCard)

    def __setQss(self):
        self.scrollWidget.setObjectName('scrollWidget')
        self.panelLabel.setObjectName('panelLabel')
        theme = 'light'
        qss = os.path.join(basedir, 'res/icons/Dashboard/qss/', theme,
                           'status_interface.qss')
        if os.path.isfile(qss):
            with open(qss, encoding='utf-8') as f:
                self.setStyleSheet(f.read())

    # ---- 生命周期：页面可见才刷新 ----
    def showEvent(self, event):
        self.refresh()
        self._refresh_timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self._refresh_timer.stop()
        super().hideEvent(event)

    # ---- 刷新 ----
    def refresh(self):
        core = self.core
        self.CultiCard.refresh(core)
        import time as _time
        have, need = core.stage_progress()
        can_break = core.stage() <= MAX_STAGE and have >= need \
            and _time.time() >= core.break_after
        self.ActionCard.set_state(core, can_break)
        self.AlchemyCard.refresh(core)
        self.OverviewCard.refresh(core.stage())
        self.LogCard.refresh(core.recent_logs(20))
        self.coinWidget._updateCoinUI()

    def _changePet(self):
        """切换角色时刷新头像（与 statusInterface._changePet 同约定）。"""
        try:
            self.CultiCard.set_avatar(settings.petname)
        except Exception:  # noqa: BLE001
            pass

    # ---- 操作回调（事件进 core.pending，由插件统一演出）----
    def _do_break(self):
        ev = self.core.try_breakthrough()
        if ev is None:
            InfoBar.warning('无法突破', '修为尚未圆满（或已飞升），继续修行吧',
                            duration=2500, parent=self.window(),
                            position=InfoBarPosition.TOP)
        elif ev['type'] == 'breakthrough':
            InfoBar.success('突破成功', f"迈入 {ev.get('stage', '')}",
                            duration=3000, parent=self.window(),
                            position=InfoBarPosition.TOP)
        else:
            InfoBar.warning('突破失败', f"损失 {fmt_exp(ev.get('lost', 0))} 修为，"
                                       f"10 分钟内速率减半",
                            duration=3000, parent=self.window(),
                            position=InfoBarPosition.TOP)
        self.refresh()

    def _on_dual(self, checked: bool):
        self.core.set_dual(checked)
        self.refresh()

    def _on_auto(self, checked: bool):
        self.core.auto_break = bool(checked)
        self.core.dirty = True
        self.core.save_if_dirty()
        self.refresh()
