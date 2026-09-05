# coding:utf-8
"""角色面板「历练」页（Dashboard 页面，与修仙之路同套骨架）。

布局铁律（血泪教训，与 cultiUI 相同）：
- qfluentwidgets 的 `ExpandLayout.addWidget` **不会重挂 parent**——
  进 expandLayout 的卡片必须以 `self.scrollWidget` 为 parent 创建；
- 悬浮卡（header/状态卡/派出卡）以 ScrollArea 为 parent + move()，
  `setViewportMargins` 顶部须盖住悬浮区。

数据全部来自 `adventure_service.get_service()` 与 `realms/events` 纯数据表；
页面**只读不 tick**——结算驱动归玩法插件（5s tick），单一驱动者不变式。
"""
import os
import datetime

from qfluentwidgets import (ScrollArea, ExpandLayout, PrimaryPushButton, ComboBox,
                            InfoBar, InfoBarPosition, SimpleCardWidget,
                            CaptionLabel, StrongBodyLabel, setFont)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QWidget, QLabel, QHBoxLayout, QVBoxLayout,
                               QSpacerItem, QSizePolicy)

from DyberPet.adventure_service import get_service, compute_success
from DyberPet.cultivation_service import REALMS, fmt_exp, get_core, stage_name
from DyberPet.custom_widgets import RoundBarBase
from DyberPet.plugins.adventure import events, realms
from .dashboard_widgets import HorizontalSeparator, coinWidget

import DyberPet.settings as settings

basedir = settings.BASEDIR

GOLD = '#d8a017'
OUTCOME_COLOR = {'大胜': GOLD, '小胜': '#3aa76d', '险胜': '#e08e2b',
                 '失利': '#8a8a8a', '重伤': '#e04343'}


class RoundBar(RoundBarBase):
    """与 HP/FV 条同款进度条（RoundBarBase 薄封装）。"""


def _self_group() -> int:
    try:
        return min(max(get_core().stage(), 0) // 4, 10)
    except Exception:  # noqa: BLE001
        return 0


class AdventureStatusCard(SimpleCardWidget):
    """历练状态卡：外出→进度/传讯符；在家→提示与历练 buff。"""

    def __init__(self, card_w: int, parent=None):
        super().__init__(parent)
        self.setBorderRadius(5)
        self.setFixedSize(card_w, 130)

        vBox = QVBoxLayout(self)
        vBox.setContentsMargins(20, 8, 20, 8)
        vBox.setSpacing(6)

        self.titleLabel = CaptionLabel('——')
        setFont(self.titleLabel, 15, QFont.DemiBold)
        self.titleLabel.setFixedHeight(24)

        barRow = QHBoxLayout()
        barRow.setSpacing(8)
        self.progBar = RoundBar(fill_color="#50d8be", parent=self)
        self.progBar.setMinimum(0)
        self.progBar.setMaximum(10000)
        self.progBar.setFormat('')
        self.progBar.setAlignment(Qt.AlignCenter)
        self.progBar.setFixedHeight(15)
        barRow.addWidget(self.progBar, 1)

        self.remainLabel = CaptionLabel('')
        setFont(self.remainLabel, 12, QFont.Normal)
        self.remainLabel.setFixedWidth(110)
        self.remainLabel.setStyleSheet('color: gray;')
        barRow.addWidget(self.remainLabel)

        self.infoLabel = CaptionLabel('——')
        setFont(self.infoLabel, 12, QFont.Normal)
        self.infoLabel.setWordWrap(True)
        self.infoLabel.setStyleSheet('color: gray;')

        vBox.addWidget(self.titleLabel)
        vBox.addWidget(HorizontalSeparator(QColor(20, 20, 20, 125), 1))
        vBox.addLayout(barRow)
        vBox.addWidget(self.infoLabel, 1)

    def refresh(self, svc, core):
        import time as _time
        now = _time.time()
        st = svc.status()
        buffs = st.get('buffs', [])
        buff_txt = ''
        for b in buffs:
            if b['key'] == 'gaming':
                buff_txt = f'｜悟道 buff 成功率 +{b["bonus"]:.0%}' \
                           f'（剩 {int(b["remain"] // 60)} 分）'
        injured = False
        try:
            injured = core.injured()
        except Exception:  # noqa: BLE001
            pass

        if st['state'] != 'away':
            self.titleLabel.setText('洞府中')
            self.progBar.setValue(0)
            self.progBar.setFormat('')
            self.remainLabel.setText('')
            hint = ('桌宠正在洞府修行——从下方面板派出历练吧。\n'
                    '外出期间道韵分身留守桌面，传讯符会陆续寄回。')
            if injured:
                hint = '带伤休养中（修行减速，会自行恢复）。\n' + hint
            if buff_txt:
                hint += buff_txt
            self.infoLabel.setText(hint)
            return

        frac = st['elapsed'] / max(1.0, st['duration'])
        self.progBar.setValue(int(min(1.0, frac) * 10000))
        self.progBar.setFormat(f"{st['name']}")
        remain = realms.dur_label(st['remain'])
        self.titleLabel.setText(f"历练中：{st['name']}")
        self.remainLabel.setText(f'约 {remain} 后归来')
        tal = st.get('latest_talisman', '')
        tal_txt = ('最新传讯符：' + (tal[:26] + '…' if len(tal) > 28 else tal)) \
            if tal else '传讯符尚未寄回……'
        extra = f'｜已收 {st["sent"]}/{st["total"]} 张'
        self.infoLabel.setText(tal_txt + extra + buff_txt)


class DispatchCard(SimpleCardWidget):
    """派出卡：秘境 + 时长 + 策略 → 实时成功率/收益预估 → 派出。"""

    def __init__(self, card_w: int, svc, on_dispatch, parent=None):
        super().__init__(parent)
        self.setBorderRadius(5)
        self.setFixedSize(card_w, 170)
        self.svc = svc
        self.on_dispatch = on_dispatch

        vBox = QVBoxLayout(self)
        vBox.setContentsMargins(20, 10, 20, 12)
        vBox.setSpacing(8)

        title = StrongBodyLabel('派出历练')
        setFont(title, 14, QFont.DemiBold)
        vBox.addWidget(title)

        row1 = QHBoxLayout()
        row1.setSpacing(10)
        self.realmBox = ComboBox(self)
        self.realmBox.setFixedWidth(150)
        self._tiers = realms.REALM_TIERS
        self_group = _self_group()
        for i, t in enumerate(self._tiers):
            locked = self_group < t['req']
            label = t['name'] + ('' if not locked else '（未解锁）')
            self.realmBox.addItem(text=label, userData=i)
        self.durationBox = ComboBox(self)
        for k in realms.DUR_KEYS:
            self.durationBox.addItem(text=k, userData=k)
        self.riskBox = ComboBox(self)
        for k in realms.RISK_KEYS:
            self.riskBox.addItem(text=k, userData=k)
        self.riskBox.setCurrentText('均衡')
        row1.addWidget(self.realmBox, 1)
        row1.addWidget(QLabel('时长：'), 0, Qt.AlignRight)
        row1.addWidget(self.durationBox, 1)
        row1.addWidget(QLabel('策略：'), 0, Qt.AlignRight)
        row1.addWidget(self.riskBox, 1)
        vBox.addLayout(row1)

        self.infoLabel = CaptionLabel('——')
        setFont(self.infoLabel, 12, QFont.Normal)
        self.infoLabel.setWordWrap(True)
        self.infoLabel.setStyleSheet('color: gray;')
        vBox.addWidget(self.infoLabel, 1)

        row2 = QHBoxLayout()
        self.goBtn = PrimaryPushButton('派出历练', self)
        self.goBtn.setFixedWidth(130)
        self.goBtn.clicked.connect(self._go)
        self.hintLabel = CaptionLabel('历练归来带回修为、灵石与丹药；失败只减速不倒扣')
        setFont(self.hintLabel, 11, QFont.Normal)
        self.hintLabel.setStyleSheet('color: gray;')
        row2.addWidget(self.hintLabel, 1)
        row2.addWidget(self.goBtn)
        vBox.addLayout(row2)

        self.realmBox.currentIndexChanged.connect(self.refresh)
        self.durationBox.currentIndexChanged.connect(self.refresh)
        self.riskBox.currentIndexChanged.connect(self.refresh)

    def _selection(self):
        tier_idx = self.realmBox.currentData()
        if tier_idx is None:
            tier_idx = 0
        return int(tier_idx), self.durationBox.currentText(), self.riskBox.currentText()

    def _go(self):
        try:
            self.on_dispatch(*self._selection())
        except Exception as e:  # noqa: BLE001
            print(f'[adventureUI] dispatch failed: {e!r}')

    def refresh(self, *_args):
        svc, core = self.svc, get_core()
        self_group = _self_group()
        st = svc.status()
        away = st['state'] == 'away'
        self.goBtn.setEnabled(not away)
        self.goBtn.setText('历练中…' if away else '派出历练')
        tier_idx, dur_key, risk_key = self._selection()
        tier = self._tiers[tier_idx]
        if self_group < tier['req']:
            need = REALMS[min(tier['req'], 9)]
            self.infoLabel.setText(
                f'「{tier["name"]}」需修为达到 {need} 期，暂时去不了'
                f'（时长 {realms.dur_label(tier["dur"][0])}~{realms.dur_label(tier["dur"][1])}，'
                f'收益 ×{tier["reward"]}，风险 {tier["risk"]}）')
            return
        spec, err = realms.build_spec(tier_idx, dur_key, risk_key, self_group)
        if spec is None:
            self.infoLabel.setText(err or '——')
            return
        injured = False
        try:
            injured = core.injured()
        except Exception:  # noqa: BLE001
            pass
        p = compute_success(spec, self_group, svc.buff_bonus(), injured)
        base_exp = int(spec['exp_base'] * spec['reward_mult'] * spec['risk_reward'])
        injury_txt = '，带伤-10%' if injured else ''
        self.infoLabel.setText(
            f'成功率 {p:.0%}（保底15%封顶95%{injury_txt}）'
            f'｜预计修为 ~{fmt_exp(base_exp)}，灵石 ~{spec["stone_base"] * spec["reward_mult"]}'
            f'｜时长 {realms.dur_label(spec["duration"])}')


class RealmOverviewCard(SimpleCardWidget):
    """秘境一览：六层秘境的解锁条件/时长/收益/风险。"""

    def __init__(self, card_w: int, parent=None):
        super().__init__(parent)
        self.setBorderRadius(5)
        self.setFixedWidth(card_w)

        self.vBox = QVBoxLayout(self)
        self.vBox.setContentsMargins(15, 10, 15, 12)
        self.vBox.setSpacing(6)
        title = StrongBodyLabel('秘境一览')
        setFont(title, 14, QFont.DemiBold)
        self.vBox.addWidget(title)
        self.vBox.addWidget(HorizontalSeparator(QColor(20, 20, 20, 125), 1))
        self._rows: list = []
        for t in realms.REALM_TIERS:
            row = CaptionLabel('——')
            setFont(row, 12, QFont.Normal)
            row.setWordWrap(True)
            self.vBox.addWidget(row)
            self._rows.append(row)
        self.adjustSize()

    def refresh(self, self_group: int):
        for i, (t, row) in enumerate(zip(realms.REALM_TIERS, self._rows)):
            dur = f'{realms.dur_label(t["dur"][0])}~{realms.dur_label(t["dur"][1])}'
            base = (f'「{t["name"]}」 需{REALMS[min(t["req"], 9)]}期 ｜ {dur} ｜ '
                    f'收益 ×{t["reward"]} ｜ 风险 {t["risk"]}')
            if self_group < t['req']:
                row.setText(f'<span style="color:#9a9a9a">{base} ｜ 未解锁</span>')
            else:
                row.setText(f'<span style="color:#3aa76d">{base} ｜ 已解锁</span>')
        self.adjustSize()


class JourneyLogCard(SimpleCardWidget):
    """历练志：最近 20 次归来的记录（桌宠的"人生经历"）。"""

    def __init__(self, card_w: int, parent=None):
        super().__init__(parent)
        self.setBorderRadius(5)
        self.setFixedWidth(card_w)

        self.vBox = QVBoxLayout(self)
        self.vBox.setContentsMargins(15, 10, 15, 12)
        self.vBox.setSpacing(4)
        title = StrongBodyLabel('历练志')
        setFont(title, 14, QFont.DemiBold)
        self.vBox.addWidget(title)
        self.vBox.addWidget(HorizontalSeparator(QColor(20, 20, 20, 125), 1))
        self._rows: list = []
        self._empty = CaptionLabel('暂无历练记录——派出历练后，每次归来都会记在这里')
        self._empty.setStyleSheet('color: gray;')
        self.vBox.addWidget(self._empty)
        self.adjustSize()

    def refresh(self, records: list):
        for w in self._rows:
            w.deleteLater()
        self._rows.clear()
        self._empty.setVisible(not records)
        for i, r in enumerate(reversed(records)):
            if i > 0:
                sep = HorizontalSeparator(QColor(20, 20, 20, 60), 1)
                self.vBox.addWidget(sep)
                self._rows.append(sep)
            t = datetime.datetime.fromtimestamp(r.get('t', 0)).strftime('%m-%d %H:%M')
            color = OUTCOME_COLOR.get(r.get('outcome', ''), '#444444')
            loot = []
            if r.get('exp'):
                loot.append(f'修为+{fmt_exp(r["exp"])}')
            if r.get('stones'):
                loot.append(f'灵石+{r["stones"]}')
            if r.get('pill'):
                loot.append(f'「{r["pill"]}」')
            line = (f'{t} ｜ <b style="color:{color}">{r.get("outcome", "")}</b>'
                    f' · {r.get("name", "")}'
                    f'{"（离线归来）" if r.get("offline") else ""} ｜ '
                    + ('，'.join(loot) if loot else '空手而归'))
            story = r.get('story', '')
            if story:
                line += f'<br><span style="color:#666">{story}</span>'
            row = QLabel(line)
            row.setTextFormat(Qt.RichText)
            row.setWordWrap(True)
            row.setStyleSheet('padding: 3px 5px;')
            self.vBox.addWidget(row)
            self._rows.append(row)
        self.adjustSize()


# 循环依赖规避：RoundBar 从 cultiUI 引用会拖出整页模块，这里内联轻量进度条


class adventureInterface(ScrollArea):
    """角色面板「历练」页。"""

    def __init__(self, sizeHintdb: tuple, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("adventureInterface")
        self.scrollWidget = QWidget()
        self.expandLayout = ExpandLayout(self.scrollWidget)
        self._card_w = sizeHintdb[0] - 165

        # header（悬浮）
        self.headerWidget = QWidget(self)
        self.headerWidget.setFixedWidth(self._card_w)
        self.panelLabel = QLabel('历练', self.headerWidget)
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

        # 服务单例（与玩法插件同一路径；只读不 tick）
        self.svc = get_service(os.path.join(settings.CONFIGDIR, 'data',
                                            'adventure.json'))

        # 悬浮卡（parent=ScrollArea + move()）
        self.StatusCard = AdventureStatusCard(self._card_w, self)
        self.DispatchCard = DispatchCard(self._card_w, self.svc, self._dispatch, self)

        # 滚动区卡（⚠️ parent=scrollWidget——ExpandLayout.addWidget 不重挂 parent！）
        self.OverviewCard = RealmOverviewCard(self._card_w, self.scrollWidget)
        self.LogCard = JourneyLogCard(self._card_w, self.scrollWidget)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(2000)
        self._refresh_timer.timeout.connect(self.refresh)

        self.__initWidget()

    def __initWidget(self):
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setViewportMargins(0, 395, 0, 20)   # header(20~53)+状态卡(75~205)+派出卡(215~385)
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)
        self.__setQss()
        self.__initLayout()

    def __initLayout(self):
        self.headerWidget.move(60, 20)
        self.StatusCard.move(60, 75)
        self.DispatchCard.move(60, 215)

        self.expandLayout.setSpacing(28)
        self.expandLayout.setContentsMargins(70, 30, 70, 0)
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

    # ---- 生命周期 ----
    def showEvent(self, event):
        self.refresh()
        self._refresh_timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self._refresh_timer.stop()
        super().hideEvent(event)

    # ---- 刷新 ----
    def refresh(self):
        core = get_core()
        self.StatusCard.refresh(self.svc, core)
        self.DispatchCard.refresh()
        self.OverviewCard.refresh(_self_group())
        self.LogCard.refresh(self.svc.recent_records(20))
        self.coinWidget._updateCoinUI()

    # ---- 派出（页面回调；驱动仍在插件，本页只写服务状态）----
    def _dispatch(self, tier_idx: int, dur_key: str, risk_key: str):
        self_group = _self_group()
        spec, err = realms.build_spec(tier_idx, dur_key, risk_key, self_group)
        if spec is None:
            InfoBar.warning('去不了', err or '条件不足', duration=3000,
                            parent=self.window(), position=InfoBarPosition.TOP)
            return
        result = self.svc.dispatch(spec, events.pick(tier_idx))
        if result.get('ok'):
            InfoBar.success('出发！', result.get('msg', ''), duration=3000,
                            parent=self.window(), position=InfoBarPosition.TOP)
        else:
            InfoBar.warning('派出失败', result.get('msg', ''), duration=3000,
                            parent=self.window(), position=InfoBarPosition.TOP)
        self.refresh()
