# coding:utf-8
"""修仙世界页（角色面板）：三线日志流 + 道友名帖 + 已故名录 + 奇遇请示卡。

设计铁律（设计文档 §10.2）：日志是「想看时永远有新内容」——页面被动可查，
绝不红点绝不弹窗。刷新只在页面可见时进行。
抉择卡（文档二 §1.2「道友范式」）：桌宠请示悬在页面顶部，用户拍板，
选择写下因果——绝不倒计时逼选，想什么时候应就什么时候应。

历史沿革：原为 xiuxian_world 插件的独立窗口面板；v0.6.8 起收编为角色
面板一等页——修仙世界是角色体验的一部分，不该藏在插件中心里。
"""
from __future__ import annotations

import os

from qfluentwidgets import (ComboBox, ExpandLayout, ScrollArea,
                            SwitchButton, TransparentToolButton)

from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QColor, QFont, QIcon
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QPushButton,
                               QSpacerItem, QSizePolicy, QTabWidget,
                               QVBoxLayout, QWidget)

import DyberPet.settings as settings
from DyberPet.world_service import get_world, day_str
from DyberPet.choice_service import get_choice

basedir = settings.BASEDIR

LEVEL_COLOR = {1: QColor(120, 120, 120), 2: QColor(58, 110, 165),
               3: QColor(179, 84, 30)}
CAT_NAME = {'friend': '道友', 'world': '天下', 'main': '游历'}
REL_NAME = {'old_friend': '故交', 'old_rival': '宿敌', 'acquaint': '旧识',
            'friend': '友人', 'sworn': '至交', 'enemy': '仇敌',
            'master': '师长', 'disciple': '门徒'}
REALM_NAME = ['炼气', '筑基', '金丹', '元婴', '化神', '炼虚', '合体',
              '大乘', '渡劫', '真仙']


class worldInterface(ScrollArea):
    """「修仙世界」页：世界日志流（世界日志/道友名帖/已故名录）+ 奇遇请示。"""

    def __init__(self, sizeHintdb: tuple, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("worldInterface")
        self.scrollWidget = QWidget()
        self.expandLayout = ExpandLayout(self.scrollWidget)

        # setting label
        self.headerWidget = QWidget(self)
        self.headerWidget.setFixedWidth(sizeHintdb[0] - 165)
        self.panelLabel = QLabel(self.tr("修仙世界"), self.headerWidget)
        self.panelLabel.setSizePolicy(QSizePolicy.Maximum,
                                      self.panelLabel.sizePolicy().verticalPolicy())
        self.panelLabel.adjustSize()
        self.panelHelp = TransparentToolButton(
            QIcon(os.path.join(basedir, 'res/icons/question.svg')),
            self.headerWidget)
        self.panelHelp.setFixedSize(25, 25)
        self.panelHelp.setIconSize(QSize(25, 25))
        self.headerLayout = QHBoxLayout(self.headerWidget)
        self.headerLayout.setContentsMargins(0, 0, 0, 0)
        self.headerLayout.setSpacing(0)
        self.headerLayout.addWidget(self.panelLabel, Qt.AlignLeft | Qt.AlignVCenter)
        spacerItem1 = QSpacerItem(10, 20, QSizePolicy.Fixed, QSizePolicy.Minimum)
        self.headerLayout.addItem(spacerItem1)
        self.headerLayout.addWidget(self.panelHelp, Qt.AlignLeft | Qt.AlignVCenter)
        spacerItem2 = QSpacerItem(10, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.headerLayout.addItem(spacerItem2)

        # ---- 世界核心（与守护共用同一单例/存档）----
        save_path = os.path.join(settings.CONFIGDIR, 'data', 'world_state.json')
        self.world = get_world(save_path)
        data_dir = os.path.join(basedir, 'res', 'world')
        self.world.load_content(data_dir)
        self.choice = get_choice(self.world)

        # ---- 纪年 + 因果 ----
        # ⚠️ ExpandLayout 只取子控件当前高度（不管理高度），卡片必须定高
        self.headCard = QFrame(self.scrollWidget)
        self.headCard.setFixedHeight(46)
        head = QHBoxLayout(self.headCard)
        head.setContentsMargins(16, 10, 16, 10)
        self.day_label = QLabel(self.headCard)
        self.day_label.setFont(QFont('Microsoft YaHei', 13, QFont.Bold))
        head.addWidget(self.day_label)
        self.karma_label = QLabel(self.headCard)
        self.karma_label.setFont(QFont('Microsoft YaHei', 11))
        head.addWidget(self.karma_label)
        head.addStretch(1)
        self.filter = QComboBox(self.headCard)
        self.filter.addItems(['全部日志', '道友动态', '天下大事', '游历直播'])
        self.filter.currentIndexChanged.connect(lambda _i: self.refresh())
        head.addWidget(self.filter)
        btn = QPushButton('刷新', self.headCard)
        btn.clicked.connect(self.refresh)
        head.addWidget(btn)

        # ---- 奇遇请示卡（有待应答的请示时显示）----
        self.choice_frame = QFrame(self.scrollWidget)
        self.choice_frame.setObjectName('choiceCard')
        self.choice_frame.setFixedHeight(190)
        self.choice_frame.setStyleSheet(
            '#choiceCard {background: #fdf6e3; border: 1px solid #e0c98f; '
            'border-radius: 8px;}')
        cf = QVBoxLayout(self.choice_frame)
        cf.setContentsMargins(12, 8, 12, 8)
        self.choice_title = QLabel(self.choice_frame)
        self.choice_title.setFont(QFont('Microsoft YaHei', 11, QFont.Bold))
        self.choice_title.setWordWrap(True)
        cf.addWidget(self.choice_title)
        self.choice_text = QLabel(self.choice_frame)
        self.choice_text.setWordWrap(True)
        self.choice_text.setFont(QFont('Microsoft YaHei', 10))
        self.choice_text.setFixedWidth(max(300, sizeHintdb[0] - 330))
        cf.addWidget(self.choice_text)
        self.choice_btns = QHBoxLayout()
        cf.addLayout(self.choice_btns)
        self.choice_result = QLabel(self.choice_frame)
        self.choice_result.setWordWrap(True)
        self.choice_result.setFont(QFont('Microsoft YaHei', 10))
        self.choice_result.setStyleSheet('color: #7a5c1e;')
        self.choice_result.hide()
        cf.addWidget(self.choice_result)
        self.choice_frame.hide()

        # ---- 设置卡 ----
        self.settingCard = QFrame(self.scrollWidget)
        self.settingCard.setFixedHeight(52)
        st = QHBoxLayout(self.settingCard)
        st.setContentsMargins(16, 8, 16, 8)
        st.setSpacing(14)
        speed_label = QLabel('时间流速', self.settingCard)
        st.addWidget(speed_label)
        self.speedBox = ComboBox(self.settingCard)
        self.speedBox.addItems(['标准', '疾行', '悠远'])
        self.speedBox.setCurrentText(str(settings.world_speed)
                                     if settings.world_speed in ('标准', '疾行', '悠远')
                                     else '标准')
        self.speedBox.currentTextChanged.connect(self._on_speed)
        st.addWidget(self.speedBox)
        st.addSpacing(8)
        self.sw_bubble = SwitchButton('大事气泡', self.settingCard)
        self.sw_bubble.setOnText('开')
        self.sw_bubble.setOffText('关')
        self.sw_bubble.setChecked(bool(settings.world_bubble_major))
        self.sw_bubble.checkedChanged.connect(self._on_bubble)
        st.addWidget(self.sw_bubble)
        self.sw_notify = SwitchButton('中等事件通知', self.settingCard)
        self.sw_notify.setOnText('开')
        self.sw_notify.setOffText('关')
        self.sw_notify.setChecked(bool(settings.world_notify_medium))
        self.sw_notify.checkedChanged.connect(self._on_notify)
        st.addWidget(self.sw_notify)
        self.sw_travel = SwitchButton('游历直播', self.settingCard)
        self.sw_travel.setOnText('开')
        self.sw_travel.setOffText('关')
        self.sw_travel.setChecked(bool(settings.world_travel_log))
        self.sw_travel.checkedChanged.connect(self._on_travel)
        st.addWidget(self.sw_travel)
        self.sw_qiyu = SwitchButton('奇遇请示', self.settingCard)
        self.sw_qiyu.setOnText('开')
        self.sw_qiyu.setOffText('关')
        self.sw_qiyu.setChecked(bool(settings.world_qiyu_choices))
        self.sw_qiyu.checkedChanged.connect(self._on_qiyu)
        st.addWidget(self.sw_qiyu)

        # ---- 三线日志 ----
        self.tabs = QTabWidget(self.scrollWidget)
        self.tabs.setFixedHeight(420)
        self.log_list = QListWidget(self)
        self.log_list.setWordWrap(True)
        self.log_list.setSpacing(2)
        self.tabs.addTab(self.log_list, '世界日志')
        self.roster_list = QListWidget(self)
        self.tabs.addTab(self.roster_list, '道友名帖')
        self.fallen_list = QListWidget(self)
        self.tabs.addTab(self.fallen_list, '已故名录')

        self.__initWidget()

    # ---- 布局与样式（照 taskInterface 范式）----
    def __initWidget(self):
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setViewportMargins(0, 75, 0, 20)
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)
        self.__setQss()

        self.expandLayout.setSpacing(16)
        self.expandLayout.setContentsMargins(50, 10, 50, 20)
        self.expandLayout.addWidget(self.headCard)
        self.expandLayout.addWidget(self.choice_frame)
        self.expandLayout.addWidget(self.settingCard)
        self.expandLayout.addWidget(self.tabs)

        self.headerWidget.move(60, 20)
        self.panelHelp.clicked.connect(self._showInstruction)

        self._timer = QTimer(self)
        self._timer.setInterval(15000)
        self._timer.timeout.connect(self._auto_refresh)

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
    def showEvent(self, ev):
        super().showEvent(ev)
        self._timer.start()
        self.refresh()

    def hideEvent(self, ev):
        super().hideEvent(ev)
        self._timer.stop()

    def _auto_refresh(self):
        if self.isVisible():
            try:
                self.world.catch_up()
            except Exception:  # noqa: BLE001
                pass
            self.refresh()

    # ---- 设置写入 ----
    def _on_speed(self, text: str):
        settings.world_speed = text
        settings.save_settings()

    def _on_bubble(self, flag: bool):
        settings.world_bubble_major = bool(flag)
        settings.save_settings()

    def _on_notify(self, flag: bool):
        settings.world_notify_medium = bool(flag)
        settings.save_settings()

    def _on_travel(self, flag: bool):
        settings.world_travel_log = bool(flag)
        settings.save_settings()

    def _on_qiyu(self, flag: bool):
        settings.world_qiyu_choices = bool(flag)
        settings.save_settings()

    def _showInstruction(self):
        from qfluentwidgets import MessageBox
        box = MessageBox(
            '修仙世界',
            '这是一个在你离开时也在继续运转的世界。\n\n'
            '⏺ 世界日志：道友们各自修行历练、结缘结仇、生老病死，'
            '天下大事低频流转。\n'
            '⏺ 游历直播：桌宠外出历练时的见闻琐事实时入流。\n'
            '⏺ 奇遇请示：桌宠遇到拿不准的事会来问你，你的选择会写下'
            '因果——多年之后，当年的因会自己找上门来。\n\n'
            '离开一段时间再回来看，世界真的会变。',
            self)
        box.yesButton.setText('知道了')
        box.cancelButton.hide()
        box.exec()

    # ---- 渲染 ----
    def refresh(self):
        if self.world is None:
            return
        self.day_label.setText(
            f'世界纪年 · {day_str(self.world.world.get("world_day", 0))}')
        karma = int(self.world.world.get('player_karma', 0))
        self.karma_label.setText(f'因果 {karma:+d}')
        self.karma_label.setStyleSheet(
            'color: #2e7d32;' if karma >= 20 else
            'color: #b3402e;' if karma <= -20 else 'color: #666;')
        self._fill_choice()
        self._fill_logs()
        self._fill_roster()
        self._fill_fallen()

    # ---- 奇遇请示卡 ----
    def _fill_choice(self):
        pending = self.world.world.get('pending_choice')
        if not pending:
            self.choice_frame.hide()
            return
        if getattr(self, '_shown_choice_id', None) == pending.get('id') \
                and self.choice_frame.isVisible() \
                and not self.choice_result.isHidden():
            return                      # 已应答态不重绘，保留结果展示
        self._shown_choice_id = pending.get('id')
        self.choice_title.setText(f"请示 · {pending.get('title', '奇遇')}")
        self.choice_text.setText(pending.get('narrative', ''))
        self.choice_result.hide()
        while self.choice_btns.count():
            item = self.choice_btns.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for c in pending.get('choices', []):
            b = QPushButton(c.get('text', ''), self.choice_frame)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(
                lambda _=False, key=c.get('key'): self._on_choice(key))
            self.choice_btns.addWidget(b)
        self.choice_frame.show()

    def _on_choice(self, key: str):
        if self.choice is None:
            return
        result = self.choice.resolve(key)
        if result is None:
            self.refresh()
            return
        # 抉择收益当场兑现（走世界守护，与 tick 的世界回响收益互不重叠）
        from DyberPet.world_daemon import get_daemon
        daemon = get_daemon()
        g = result.get('grants') or {}
        if daemon is not None:
            daemon.apply_player_grants([g] if any(g.values()) else [])
        echo_hint = "（因果已种下，回响不知何日归来）" \
            if result.get('echoes') else ''
        self.choice_result.setText(
            f"✦ {result.get('text', '')}{echo_hint}")
        self.choice_result.show()
        # 结果也入世界日志流（游历直播线）
        try:
            self.world.player_log(
                f"奇遇「{result.get('title', '')}」了结："
                f"{result.get('text', '')}", 3)
        except Exception:  # noqa: BLE001
            pass
        self.refresh()

    def _fill_logs(self):
        self.log_list.clear()
        cat = {0: None, 1: 'friend', 2: 'world', 3: 'main'}.get(
            self.filter.currentIndex())
        logs = self.world.recent_logs(200, cat=cat)
        font = QFont('Microsoft YaHei', 10)
        for lg in reversed(logs):                       # 最新在顶
            lvl = int(lg.get('level', 1))
            cat_tag = CAT_NAME.get(lg.get('cat', ''), '')
            item = QListWidgetItem(
                f"{day_str(int(lg.get('day', 0)))} ｜ {lg.get('text', '')}")
            item.setForeground(LEVEL_COLOR.get(lvl, LEVEL_COLOR[1]))
            f = QFont(font)
            if lvl >= 3:
                f.setBold(True)
            item.setFont(f)
            item.setToolTip(f"[{cat_tag} · L{lvl}]")
            self.log_list.addItem(item)

    def _fill_roster(self):
        self.roster_list.clear()
        font = QFont('Microsoft YaHei', 10)
        for n in self.world.roster(80):
            rel = ''
            if n.get('rel_type'):
                aff = n.get('rel_affinity')
                rel = f" ｜ 与你·{REL_NAME.get(n['rel_type'], n['rel_type'])}"
                if aff is not None:
                    rel += f"({aff:+d})"
            realm = REALM_NAME[min(int(n.get('realm', 0)), len(REALM_NAME) - 1)]
            item = QListWidgetItem(
                f"{n['name']} ｜ {realm}期 ｜ {n['age']}岁/寿{n['lifespan']}"
                f" ｜ {n.get('loc', '')}{rel}")
            f = QFont(font)
            if n.get('rel_type'):
                f.setBold(True)
            item.setFont(f)
            item.setForeground(QColor(60, 90, 60) if n.get('rel_type')
                               else QColor(60, 60, 60))
            self.roster_list.addItem(item)

    def _fill_fallen(self):
        self.fallen_list.clear()
        font = QFont('Microsoft YaHei', 10)
        for d in self.world.fallen(80):
            item = QListWidgetItem(
                f"{d.get('name', '')} ｜ {d.get('realm', '')}期 ｜ "
                f"享年{d.get('age', '?')}岁 ｜ {d.get('cause', '')}")
            item.setForeground(QColor(130, 130, 130))
            item.setFont(font)
            self.fallen_list.addItem(item)
