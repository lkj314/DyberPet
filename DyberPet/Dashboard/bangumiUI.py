# coding:utf-8
"""「追番导航」页（v1.3 新番导视重构）。

三个分区（SegmentedWidget 切换，QStackedWidget 承载）：
- 新番导视：七日放送时间表——星期条选天，当日更新番剧按播出时间排列，
  每条含封面/第N话/播出时间/评分/延期原因，一键加入追番清单；
- 新番目录：最近两周在播番剧去重（3 列封面网格），发现向；
- 我的追番：清单进度管理 + 搜索添加（B站主源 / Bangumi 备用）。

数据源：B站 /pgc/web/timeline（wbi 签名，TimelineStore 30 分钟缓存）；
详情简介按需拉季详情（用户点击触发，不轮询）。
"""
from __future__ import annotations

import datetime as _dt
import os
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QPainter, QColor, QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QGridLayout, QVBoxLayout, QWidget,
                               QSizePolicy, QStackedWidget)
from qfluentwidgets import ScrollArea, ExpandLayout, SegmentedWidget

from DyberPet.bangumi.subscription import SubscriptionStore, sub_key
from DyberPet.bangumi import bili_client
from DyberPet.bangumi.timeline import TimelineStore, weekday_cn
from DyberPet.bangumi.bgm_calendar import CalendarStore

TEAL = "#009faa"
BILI = "#fb7299"
ORANGE = "#e67e22"
RED = "#d4553f"
GREY = "#999"

COVER_W, COVER_H = 60, 80          # 我的追番行封面
TL_COVER_W, TL_COVER_H = 78, 104   # 导视行封面
CAT_W, CAT_H = 172, 236            # 目录卡
PLACEHOLDER_COLORS = ["#7ea7d8", "#a3c9e2", "#d8b17e", "#c9a3d8",
                      "#8fd0b8", "#d8a3a3", "#b8c98f", "#a3b8d8"]


def _elide(text: str, width: int, font: QFont) -> str:
    from PySide6.QtGui import QFontMetrics
    return QFontMetrics(font).elidedText(
        str(text or ""), Qt.ElideRight, max(width, 20))


def _clean(text: str) -> str:
    return bili_client._clean_title(text)


# ---------------------------------------------------------------- #
# 导视行：播出时间 | 封面 | 标题+话数+评分 | 追按钮
# ---------------------------------------------------------------- #
class GuideRow(QFrame):
    clicked = Signal(dict)

    def __init__(self, panel: "bangumiInterface", ep: dict):
        super().__init__()
        self.panel = panel
        self.ep = ep
        self.key = f"tl:{ep.get('season_id')}"
        self.setFixedSize(panel.page_w, 116)
        self.setStyleSheet(
            "QFrame{background:rgba(255,255,255,235);border-radius:8px;}"
            "QFrame:hover{background:rgba(255,255,255,255);}")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(10)

        # 播出时间列
        time_col = QWidget()
        tc = QVBoxLayout(time_col)
        tc.setContentsMargins(0, 0, 0, 0)
        tc.setSpacing(2)
        f_big = QFont("Microsoft YaHei", 11, QFont.Bold)
        t_label = QLabel(ep.get("pub_time") or "--:--", time_col)
        t_label.setFont(f_big)
        t_label.setAlignment(Qt.AlignCenter)
        published = bool(ep.get("published"))
        t_label.setStyleSheet(f"color:{TEAL if published else GREY};")
        tc.addWidget(t_label)
        st = QLabel("已播" if published else "待播", time_col)
        st.setFont(QFont("Microsoft YaHei", 8))
        st.setAlignment(Qt.AlignCenter)
        st.setStyleSheet(f"color:{GREY};")
        tc.addWidget(st)
        tc.addStretch(1)
        lay.addWidget(time_col)

        # 封面
        self.cover = QLabel(self)
        self.cover.setFixedSize(TL_COVER_W, TL_COVER_H)
        self.cover.setAlignment(Qt.AlignCenter)
        self.cover.setStyleSheet("background:rgba(0,0,0,8);border-radius:6px;")
        self._paint_placeholder()
        lay.addWidget(self.cover)

        # 信息列
        col = QWidget()
        cv = QVBoxLayout(col)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(4)
        f_name = QFont("Microsoft YaHei", 12, QFont.Bold)
        name = QLabel(_elide(_clean(ep.get("title")), panel.page_w - 240, f_name), col)
        name.setFont(f_name)
        name.setMaximumWidth(panel.page_w - 240)
        cv.addWidget(name)

        # 话数 + 时间段信息（第N话 · 已播/待播）
        pub_index = ep.get("pub_index") or ""
        meta_parts = [p for p in (pub_index, ep.get("pub_time")) if p]
        rating = ep.get("rating")
        if rating:
            meta_parts.append(f"{rating:.1f} 分")
        meta = QLabel("  ·  ".join(meta_parts), col)
        meta.setFont(QFont("Microsoft YaHei", 9))
        meta.setStyleSheet(f"color:{ORANGE};")
        cv.addWidget(meta)

        if ep.get("delay_reason"):
            d = QLabel(f"延期：{ep['delay_reason']}", col)
            d.setFont(QFont("Microsoft YaHei", 9))
            d.setStyleSheet(f"color:{RED};")
            cv.addWidget(d)
        cv.addStretch(1)
        lay.addWidget(col, 1)

        # 追按钮
        followed = panel.store.get(f"bili:{ep.get('season_id')}") is not None
        self.follow_btn = QPushButton("已追" if followed else "+ 追", self)
        self.follow_btn.setFixedSize(58, 28)
        self.follow_btn.setEnabled(not followed)
        color = "#bbb" if followed else BILI
        self.follow_btn.setStyleSheet(
            f"QPushButton{{background:{color};color:white;border:none;"
            f"border-radius:6px;font:12px;}}"
            f"QPushButton:disabled{{background:#bbb;}}")
        if not followed:
            self.follow_btn.clicked.connect(self._follow)
        lay.addWidget(self.follow_btn)

        # 封面异步加载
        panel.request_cover(int(ep.get("season_id") or 0),
                            ep.get("cover") or ep.get("square_cover") or "")
        self.panel._cover_cards.setdefault(self.key, []).append(self)

    def _paint_placeholder(self):
        pm = QPixmap(TL_COVER_W, TL_COVER_H)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        sid = int(self.ep.get("season_id", 0) or 0)
        p.setBrush(QColor(PLACEHOLDER_COLORS[sid % len(PLACEHOLDER_COLORS)]))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(0, 0, TL_COVER_W, TL_COVER_H, 6, 6)
        p.setPen(Qt.white)
        p.setFont(QFont("Microsoft YaHei", 20, QFont.Bold))
        name = str(self.ep.get("title") or "?")
        p.drawText(pm.rect(), Qt.AlignCenter, name[0] if name else "?")
        p.end()
        self.cover.setPixmap(pm)

    def apply_cover(self, path: str):
        pm = QPixmap(path)
        if pm.isNull():
            return
        self.cover.setPixmap(pm.scaled(
            TL_COVER_W, TL_COVER_H, Qt.KeepAspectRatioByExpanding,
            Qt.SmoothTransformation))

    def _follow(self):
        if self.panel.follow_ep(self.ep):
            self.follow_btn.setText("已追")
            self.follow_btn.setEnabled(False)
            self.follow_btn.setStyleSheet(
                "QPushButton{background:#bbb;color:white;border:none;"
                "border-radius:6px;font:12px;}")

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.ep)
        super().mousePressEvent(e)


# ---------------------------------------------------------------- #
# 目录卡：封面网格（3 列）
# ---------------------------------------------------------------- #
class CatalogCard(QFrame):
    clicked = Signal(dict)

    def __init__(self, panel: "bangumiInterface", ep: dict):
        super().__init__()
        self.panel = panel
        self.ep = ep
        self.key = f"tl:{ep.get('season_id')}"
        self.setFixedSize(CAT_W, CAT_H)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            "QFrame{background:rgba(255,255,255,235);border-radius:8px;}"
            "QFrame:hover{background:rgba(251,114,153,12);}")

        cv = QVBoxLayout(self)
        cv.setContentsMargins(12, 12, 12, 10)
        cv.setSpacing(6)

        self.cover = QLabel(self)
        self.cover.setFixedSize(CAT_W - 24, CAT_H - 68)
        self.cover.setAlignment(Qt.AlignCenter)
        self.cover.setStyleSheet("background:rgba(0,0,0,8);border-radius:6px;")
        self._paint_placeholder()
        cv.addWidget(self.cover, 0, Qt.AlignHCenter)

        f_name = QFont("Microsoft YaHei", 10, QFont.Bold)
        name = QLabel(_elide(_clean(ep.get("title")), CAT_W - 24, f_name), self)
        name.setFont(f_name)
        name.setMaximumWidth(CAT_W - 24)
        cv.addWidget(name)

        f_meta = QFont("Microsoft YaHei", 8)
        meta = QLabel(_elide(ep.get("pub_index") or "", CAT_W - 24, f_meta), self)
        meta.setFont(f_meta)
        meta.setStyleSheet(f"color:{ORANGE};")
        cv.addWidget(meta)

        panel.request_cover(int(ep.get("season_id") or 0),
                            ep.get("cover") or ep.get("square_cover") or "")
        panel._cover_cards.setdefault(self.key, []).append(self)

    def _paint_placeholder(self):
        w, h = CAT_W - 24, CAT_H - 68
        pm = QPixmap(w, h)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        sid = int(self.ep.get("season_id", 0) or 0)
        p.setBrush(QColor(PLACEHOLDER_COLORS[sid % len(PLACEHOLDER_COLORS)]))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(0, 0, w, h, 6, 6)
        p.setPen(Qt.white)
        p.setFont(QFont("Microsoft YaHei", 24, QFont.Bold))
        name = str(self.ep.get("title") or "?")
        p.drawText(pm.rect(), Qt.AlignCenter, name[0] if name else "?")
        p.end()
        self.cover.setPixmap(pm)

    def apply_cover(self, path: str):
        pm = QPixmap(path)
        if pm.isNull():
            return
        self.cover.setPixmap(pm.scaled(
            CAT_W - 24, CAT_H - 68, Qt.KeepAspectRatioByExpanding,
            Qt.SmoothTransformation))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.ep)
        super().mousePressEvent(e)


# ---------------------------------------------------------------- #
# 追番行（我的追番页）：封面 | 名称+进度 | 操作按钮
# ---------------------------------------------------------------- #
class SubRow(QFrame):
    """订阅行：进度 +1 / 删除（B站订阅用真实数据，无停播校准需求）。"""

    def __init__(self, panel: "bangumiInterface", sub: dict):
        super().__init__()
        self.panel = panel
        self.sub = sub
        self.key = sub_key(sub)
        self.setFixedSize(panel.page_w, 72)
        self.setStyleSheet(
            "QFrame{background:rgba(255,255,255,235);border-radius:8px;}")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(10)

        self.cover = QLabel(self)
        self.cover.setFixedSize(COVER_W, COVER_H)
        self.cover.setAlignment(Qt.AlignCenter)
        self.cover.setStyleSheet("background:rgba(0,0,0,8);border-radius:6px;")
        self._paint_placeholder()
        lay.addWidget(self.cover)
        panel.request_cover(int(sub.get("season_id") or 0),
                            sub.get("cover_url") or "")
        panel._cover_cards.setdefault(self.key, []).append(self)

        col = QWidget()
        cv = QVBoxLayout(col)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(4)
        f = QFont("Microsoft YaHei", 12, QFont.Bold)
        name = QLabel(_elide(_clean(sub.get("name_cn") or sub.get("name", "")),
                             panel.page_w - 230, f), col)
        name.setFont(f)
        name.setMaximumWidth(panel.page_w - 230)
        cv.addWidget(name)
        w = sub.get("watch") or {}
        cur = int(w.get("current_episode", 0) or 0)
        total = int(sub.get("eps_total", 0) or 0)
        txt = f"已看 {cur}" + (f" / {total} 话" if total else " 话")
        if sub.get("air_weekday"):
            txt += f" · {weekday_cn(int(sub['air_weekday']))}"
        info = QLabel(txt, col)
        info.setFont(QFont("Microsoft YaHei", 9))
        info.setStyleSheet("color:#999;")
        cv.addWidget(info)
        cv.addStretch(1)
        lay.addWidget(col, 1)

        is_bili = sub.get("source") == "bili"

        def _btn(text, tip, cb, color=TEAL):
            b = QPushButton(text, self)
            b.setFixedSize(56, 30)
            b.setToolTip(tip)
            b.setStyleSheet(
                f"QPushButton{{background:{color};color:white;border:none;"
                f"border-radius:6px;font:12px;}} "
                f"QPushButton:hover{{background:{color}cc;}}")
            b.clicked.connect(cb)
            lay.addWidget(b)
            return b

        _btn("+1", "已看完最新一话，进度 +1", self._watch)
        if not is_bili:
            _btn("校准", "修正停播导致的话数偏差", self._calibrate)
        _btn("删", "移出追番清单", self._remove, RED)

    def _paint_placeholder(self):
        pm = QPixmap(COVER_W, COVER_H)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        sid = int(self.sub.get("subject_id", 0) or 0)
        p.setBrush(QColor(PLACEHOLDER_COLORS[sid % len(PLACEHOLDER_COLORS)]))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(0, 0, COVER_W, COVER_H, 6, 6)
        p.setPen(Qt.white)
        p.setFont(QFont("Microsoft YaHei", 16, QFont.Bold))
        name = str(self.sub.get("name_cn") or self.sub.get("name") or "?")
        p.drawText(pm.rect(), Qt.AlignCenter, name[0] if name else "?")
        p.end()
        self.cover.setPixmap(pm)

    def apply_cover(self, path: str):
        pm = QPixmap(path)
        if pm.isNull():
            return
        self.cover.setPixmap(pm.scaled(
            COVER_W, COVER_H, Qt.KeepAspectRatioByExpanding,
            Qt.SmoothTransformation))

    def _watch(self):
        self.panel.mark_watched(self.key)

    def _calibrate(self):
        self.panel.calibrate(self.key)

    def _remove(self):
        self.panel.remove_sub(self.key)


# ---------------------------------------------------------------- #
# 搜索行（添加追番）
# ---------------------------------------------------------------- #
class SearchRow(QWidget):
    def __init__(self, panel: "bangumiInterface", subject: dict, added: bool):
        super().__init__()
        self.panel = panel
        self.subject = subject
        self.setFixedHeight(44)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 10, 4)
        lay.setSpacing(8)
        f = QFont("Microsoft YaHei", 10)
        is_bili = subject.get("source") == "bili"
        name = _clean(subject.get("name_cn") or subject.get("name", ""))
        jp = subject.get("name", "")
        text = f"{name}（{jp}）" if (jp and jp != name and not is_bili) else name
        main = QLabel(_elide(text, panel.page_w - 220, f), self)
        main.setFont(f)
        lay.addWidget(main, 1)
        if is_bili:
            meta = "  ".join(x for x in (str(subject.get("index_show") or ""),
                                         str(subject.get("score") or "")) if x)
            meta_l = QLabel(meta, self)
            meta_l.setStyleSheet(f"color:{BILI}; font:9pt;")
            lay.addWidget(meta_l)
        else:
            year = QLabel(str(subject.get("date") or "")[:4], self)
            year.setStyleSheet("color:#999; font:9pt;")
            lay.addWidget(year)
        btn = QPushButton("已追" if added else "追")
        btn.setFixedSize(56, 26)
        btn.setEnabled(not added)
        btn.setStyleSheet(
            "QPushButton{background:%s;color:white;border:none;border-radius:6px;"
            "font:11px;} QPushButton:disabled{background:#bbb;}"
            % ("#bbb" if added else (BILI if is_bili else TEAL)))
        if not added:
            btn.clicked.connect(self._add)
        lay.addWidget(btn)

    def _add(self):
        self.panel.add_from_search(self.subject)


# ---------------------------------------------------------------- #
# 主页面
# ---------------------------------------------------------------- #
class bangumiInterface(ScrollArea):
    """「追番导航」：新番导视 / 新番目录 / 我的追番。"""

    coversReady = Signal(str, str)      # card_key, 本地路径
    refreshDone = Signal(bool)          # stale
    detailReady = Signal(str, str, str) # card_key, desc, rating_str
    searchDone = Signal(list, bool)     # 结果, 失败?

    def __init__(self, sizeHintdb: tuple, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("bangumiInterface")
        self.scrollWidget = QWidget()
        self.expandLayout = ExpandLayout(self.scrollWidget)
        self.setViewportMargins(0, 0, 0, 0)
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)

        # ---- 数据层 ----
        from DyberPet.bangumi_daemon import get_daemon, BangumiDaemon
        daemon = get_daemon()
        if daemon is None:
            daemon = BangumiDaemon(None)
        self.daemon = daemon
        self.store: SubscriptionStore = daemon.store
        self.calendar: CalendarStore = daemon.calendar
        cache_dir = os.path.dirname(daemon.store.path)
        self.timeline = TimelineStore(os.path.join(cache_dir, "timeline_cache.json"))

        self.page_w = sizeHintdb[0] - 165
        self._selected_dow = _dt.date.today().isoweekday()
        self._detail_ep: dict | None = None
        self._detail_lock = 0
        self._cover_locks: set = set()
        self._cover_cards: dict[str, list] = {}
        self._search_source = "bili"
        self._searching = False

        self.coversReady.connect(self._on_cover)
        self.refreshDone.connect(self._on_refresh)
        self.detailReady.connect(self._on_detail)
        self.searchDone.connect(self._on_search)

        self._build_ui()
        self.refresh_data(False)

    # ================= UI 构建 =================
    def _build_ui(self):
        w = self.page_w

        # ---- 标题行 ----
        self.headerWidget = QWidget(self.scrollWidget)
        self.headerWidget.setFixedHeight(34)
        self.panelLabel = QLabel("追番导航", self.headerWidget)
        self.panelLabel.setFont(QFont("Microsoft YaHei", 14, QFont.Bold))
        self.panelLabel.setSizePolicy(QSizePolicy.Maximum,
                                      self.panelLabel.sizePolicy().verticalPolicy())
        self.panelLabel.adjustSize()
        self.panelHelp = QLabel("每周放送时间表 · 数据来自 bilibili",
                                self.headerWidget)
        self.panelHelp.setFont(QFont("Microsoft YaHei", 9))
        self.panelHelp.setStyleSheet("color:#999;")
        head = QHBoxLayout(self.headerWidget)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(10)
        head.addWidget(self.panelLabel)
        head.addWidget(self.panelHelp)
        head.addStretch(1)

        # ---- 状态行 ----
        self.statusCard = QFrame(self.scrollWidget)
        self.statusCard.setFixedHeight(46)
        sl = QHBoxLayout(self.statusCard)
        sl.setContentsMargins(16, 8, 16, 8)
        self.statusLabel = QLabel("放送数据加载中…", self.statusCard)
        self.statusLabel.setFont(QFont("Microsoft YaHei", 10))
        self.statusLabel.setStyleSheet("color:#777;")
        sl.addWidget(self.statusLabel, 1)
        self.refreshBtn = QPushButton("刷新", self.statusCard)
        self.refreshBtn.setFixedSize(64, 30)
        self.refreshBtn.setStyleSheet(
            f"QPushButton{{background:rgba(0,159,170,30);color:{TEAL};border:none;"
            f"border-radius:6px;font:12px;}} "
            f"QPushButton:hover{{background:rgba(0,159,170,60);}}")
        self.refreshBtn.clicked.connect(lambda: self.refresh_data(True))
        sl.addWidget(self.refreshBtn)

        # ---- tab 切换 ----
        self.seg = SegmentedWidget(self.scrollWidget)
        self.seg.addItem(routeKey="guide", text="新番导视",
                         onClick=lambda *_: self._switch("guide"))
        self.seg.addItem(routeKey="catalog", text="新番目录",
                         onClick=lambda *_: self._switch("catalog"))
        self.seg.addItem(routeKey="subs", text="我的追番",
                         onClick=lambda *_: self._switch("subs"))
        self.seg.setCurrentItem("guide")
        self.seg.setFixedHeight(36)

        # ---- 详情卡（导视/目录共用，点击行展开）----
        self._build_detail()

        # ---- 三个子页 ----
        self.stack = QStackedWidget(self.scrollWidget)
        self._build_guide_page()
        self._build_catalog_page()
        self._build_subs_page()
        self.stack.setCurrentIndex(0)

        self.expandLayout.addWidget(self.headerWidget)
        self.expandLayout.addWidget(self.statusCard)
        self.expandLayout.addWidget(self.seg)
        self.expandLayout.addWidget(self.detailCard)
        self.expandLayout.addWidget(self.stack)
        self.detailCard.hide()
        self._sync_stack_height()

    # ---- 详情卡 ----
    def _build_detail(self):
        self.detailCard = QFrame(self.scrollWidget)
        self.detailCard.setFixedHeight(196)
        self.detailCard.setStyleSheet(
            "QFrame{background:rgba(251,114,153,12);border-radius:10px;}")
        dl = QHBoxLayout(self.detailCard)
        dl.setContentsMargins(14, 10, 14, 10)
        dl.setSpacing(14)
        self.d_cover = QLabel(self.detailCard)
        self.d_cover.setFixedSize(92, 128)
        self.d_cover.setAlignment(Qt.AlignCenter)
        self.d_cover.setStyleSheet("background:rgba(255,255,255,200);border-radius:6px;")
        dl.addWidget(self.d_cover)

        col = QWidget()
        cv = QVBoxLayout(col)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(4)
        f_title = QFont("Microsoft YaHei", 12, QFont.Bold)
        self.d_title = QLabel("", col)
        self.d_title.setFont(f_title)
        self.d_title.setMaximumWidth(self.page_w - 300)
        self.d_title.setWordWrap(False)
        cv.addWidget(self.d_title)
        self.d_meta = QLabel("", col)
        self.d_meta.setFont(QFont("Microsoft YaHei", 9))
        self.d_meta.setStyleSheet(f"color:{ORANGE};")
        cv.addWidget(self.d_meta)
        f_desc = QFont("Microsoft YaHei", 9)
        self.d_desc = QLabel("简介加载中…", col)
        self.d_desc.setFont(f_desc)
        self.d_desc.setStyleSheet("color:#555;")
        self.d_desc.setWordWrap(True)
        self.d_desc.setFixedWidth(self.page_w - 300)
        self.d_desc.setFixedHeight(78)
        self.d_desc.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        cv.addWidget(self.d_desc)
        cv.addStretch(1)
        dl.addWidget(col, 1)

        btn_col = QWidget()
        bv = QVBoxLayout(btn_col)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.setSpacing(8)
        self.d_follow = QPushButton("+ 追", btn_col)
        self.d_follow.setFixedSize(64, 30)
        self.d_follow.setStyleSheet(
            f"QPushButton{{background:{BILI};color:white;border:none;"
            f"border-radius:6px;font:12px;}}")
        self.d_follow.clicked.connect(self._follow_from_detail)
        bv.addWidget(self.d_follow)
        close = QPushButton("收起", btn_col)
        close.setFixedSize(64, 26)
        close.setStyleSheet(
            "QPushButton{background:rgba(0,0,0,30);color:#666;border:none;"
            "border-radius:6px;font:11px;}")
        close.clicked.connect(lambda: self.detailCard.hide())
        bv.addWidget(close)
        bv.addStretch(1)
        dl.addWidget(btn_col)

    # ---- 导视页 ----
    def _build_guide_page(self):
        page = QWidget()
        pv = QVBoxLayout(page)
        pv.setContentsMargins(0, 4, 0, 8)
        pv.setSpacing(8)

        # 星期条
        week_bar = QWidget()
        wb = QHBoxLayout(week_bar)
        wb.setContentsMargins(0, 0, 0, 0)
        wb.setSpacing(8)
        self._week_btns: dict[int, QPushButton] = {}
        today = _dt.date.today().isoweekday()
        for dow in range(1, 8):
            b = QPushButton(weekday_cn(dow).replace("周", ""), week_bar)
            b.setFixedSize(74, 32)
            b.clicked.connect(lambda _=False, d=dow: self._select_dow(d))
            wb.addWidget(b)
            self._week_btns[dow] = b
        wb.addStretch(1)
        pv.addWidget(week_bar)

        # 当日标题
        self.dayTitle = QLabel("", page)
        self.dayTitle.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
        self.dayTitle.setStyleSheet(f"color:{TEAL};")
        pv.addWidget(self.dayTitle)

        # 导视行列表容器
        self.guideList = QWidget(page)
        self.guideLay = QVBoxLayout(self.guideList)
        self.guideLay.setContentsMargins(0, 0, 0, 0)
        self.guideLay.setSpacing(8)
        pv.addWidget(self.guideList)
        pv.addStretch(1)

        self.guidePage = page
        self.stack.addWidget(page)

    # ---- 目录页 ----
    def _build_catalog_page(self):
        page = QWidget()
        pv = QVBoxLayout(page)
        pv.setContentsMargins(0, 4, 0, 8)
        pv.setSpacing(8)
        self.catTitle = QLabel("最近两周在播", page)
        self.catTitle.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
        self.catTitle.setStyleSheet(f"color:{TEAL};")
        pv.addWidget(self.catTitle)

        self.catGridHost = QWidget(page)
        self.catGrid = QGridLayout(self.catGridHost)
        self.catGrid.setContentsMargins(0, 0, 0, 0)
        self.catGrid.setSpacing(10)
        pv.addWidget(self.catGridHost)
        pv.addStretch(1)
        self.catalogPage = page
        self.stack.addWidget(page)

    # ---- 我的追番页 ----
    def _build_subs_page(self):
        page = QWidget()
        pv = QVBoxLayout(page)
        pv.setContentsMargins(0, 4, 0, 8)
        pv.setSpacing(8)

        self.subsTitle = QLabel("", page)
        self.subsTitle.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
        self.subsTitle.setStyleSheet(f"color:{TEAL};")
        pv.addWidget(self.subsTitle)

        self.subsList = QWidget(page)
        self.subsLay = QVBoxLayout(self.subsList)
        self.subsLay.setContentsMargins(0, 0, 0, 0)
        self.subsLay.setSpacing(8)
        pv.addWidget(self.subsList)

        # 添加追番卡
        add_card = QFrame(page)
        add_card.setFixedHeight(120)
        add_card.setStyleSheet(
            "QFrame{background:rgba(255,255,255,235);border-radius:8px;}")
        av = QVBoxLayout(add_card)
        av.setContentsMargins(14, 10, 14, 10)
        av.setSpacing(6)
        row1 = QWidget()
        r1 = QHBoxLayout(row1)
        r1.setContentsMargins(0, 0, 0, 0)
        r1.setSpacing(8)
        self.searchInput = QPushButton("输入番剧名搜索…（点击弹出输入框）", row1)
        self.searchInput.setFixedHeight(32)
        self.searchInput.setStyleSheet(
            "QPushButton{background:rgba(0,0,0,25);color:#999;border:none;"
            "border-radius:6px;font:12px;text-align:left;padding-left:10px;}")
        self.searchInput.clicked.connect(self._ask_search)
        r1.addWidget(self.searchInput, 1)
        self.srcBtn = QPushButton("B站源", row1)
        self.srcBtn.setFixedSize(72, 32)
        self.srcBtn.setStyleSheet(
            f"QPushButton{{background:rgba(251,114,153,30);color:{BILI};"
            f"border:none;border-radius:6px;font:12px;}}")
        self.srcBtn.clicked.connect(self._toggle_source)
        r1.addWidget(self.srcBtn)
        av.addWidget(row1)
        tip = QLabel("从导视页点「+追」更直接；搜索用于补追旧番", add_card)
        tip.setFont(QFont("Microsoft YaHei", 8))
        tip.setStyleSheet("color:#aaa;")
        av.addWidget(tip)
        pv.addWidget(add_card)

        self.searchList = QWidget(page)
        self.searchLay = QVBoxLayout(self.searchList)
        self.searchLay.setContentsMargins(0, 0, 0, 0)
        self.searchLay.setSpacing(4)
        pv.addWidget(self.searchList)
        pv.addStretch(1)

        self.subsPage = page
        self.stack.addWidget(page)

    # ================= 数据刷新 =================
    def refresh_data(self, force: bool):
        self.statusLabel.setText("正在拉取放送数据…")
        self.statusLabel.setStyleSheet("color:#777;")
        threading.Thread(target=self._refresh_worker, args=(force,),
                         daemon=True).start()

    def _refresh_worker(self, force: bool):
        days, stale = self.timeline.refresh(force=force)
        self.refreshDone.emit(stale)

    def _on_refresh(self, stale: bool):
        days = self.timeline.days()
        n_eps = sum(len(d.get("episodes") or []) for d in days)
        if stale and not days:
            self.statusLabel.setText(f"放送数据拉取失败：{self.timeline.last_error}")
            self.statusLabel.setStyleSheet(f"color:{RED};")
        elif stale:
            self.statusLabel.setText(
                f"刷新失败（{self.timeline.last_error}），展示缓存数据")
            self.statusLabel.setStyleSheet(f"color:{ORANGE};")
        else:
            ts = self.timeline.fetched_at()
            when = (_dt.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")
                    if ts else "")
            self.statusLabel.setText(f"已更新 {when} · 未来两周共 {n_eps} 条更新")
            self.statusLabel.setStyleSheet(f"color:{TEAL};")
        self._rebuild_guide()
        self._rebuild_catalog()
        self._rebuild_subs()

    # ================= 导视 =================
    def _select_dow(self, dow: int):
        self._selected_dow = dow
        self._rebuild_guide()

    def _rebuild_guide(self):
        # 星期条样式
        today = _dt.date.today().isoweekday()
        for dow, b in self._week_btns.items():
            if dow == self._selected_dow:
                b.setStyleSheet(
                    f"QPushButton{{background:{TEAL};color:white;border:none;"
                    f"border-radius:8px;font:12px bold;}}")
            elif dow == today:
                b.setStyleSheet(
                    f"QPushButton{{background:rgba(0,159,170,22);color:{TEAL};"
                    f"border:none;border-radius:8px;font:12px bold;}}")
            else:
                b.setStyleSheet(
                    "QPushButton{background:rgba(0,0,0,18);color:#555;border:none;"
                    "border-radius:8px;font:12px;} "
                    "QPushButton:hover{background:rgba(0,0,0,32);}")

        # 清空旧行
        while self.guideLay.count():
            item = self.guideLay.takeAt(0)
            wdg = item.widget()
            if wdg:
                wdg.setParent(None)
                wdg.deleteLater()

        day = self.timeline.day_by_dow(self._selected_dow)
        eps = (day or {}).get("episodes") or []
        date_txt = (day or {}).get("date") or ""
        head = f"{date_txt} {weekday_cn(self._selected_dow)} · 更新 {len(eps)} 部"
        self.dayTitle.setText(head)

        if not eps:
            empty = QLabel("这一天暂无排期（可能番剧已完结或未开播）", self.guideList)
            empty.setFont(QFont("Microsoft YaHei", 10))
            empty.setStyleSheet("color:#bbb;")
            empty.setFixedHeight(48)
            empty.setAlignment(Qt.AlignCenter)
            self.guideLay.addWidget(empty)
        for ep in eps:
            row = GuideRow(self, ep)
            row.clicked.connect(self._show_detail)
            self.guideLay.addWidget(row)
        self._sync_stack_height()

    # ================= 目录 =================
    def _rebuild_catalog(self):
        while self.catGrid.count():
            item = self.catGrid.takeAt(0)
            wdg = item.widget()
            if wdg:
                wdg.setParent(None)
                wdg.deleteLater()
        seasons = self.timeline.all_seasons()
        self.catTitle.setText(f"最近两周在播 · 共 {len(seasons)} 部")
        cols = 3
        for i, ep in enumerate(seasons):
            card = CatalogCard(self, ep)
            card.clicked.connect(self._show_detail)
            self.catGrid.addWidget(card, i // cols, i % cols)
        self._sync_stack_height()

    # ================= 我的追番 =================
    def _rebuild_subs(self):
        while self.subsLay.count():
            item = self.subsLay.takeAt(0)
            wdg = item.widget()
            if wdg:
                wdg.setParent(None)
                wdg.deleteLater()
        subs = self.store.list_all()
        self.subsTitle.setText(f"追番清单 · {len(subs)} 部")
        if not subs:
            empty = QLabel("还没有追的番——去「新番导视」挑一部吧", self.subsList)
            empty.setFont(QFont("Microsoft YaHei", 10))
            empty.setStyleSheet("color:#bbb;")
            empty.setFixedHeight(48)
            empty.setAlignment(Qt.AlignCenter)
            self.subsLay.addWidget(empty)
        for sub in subs:
            row = SubRow(self, sub)
            self.subsLay.addWidget(row)
        self._sync_stack_height()

    # ================= 详情 =================
    def _show_detail(self, ep: dict):
        self._detail_ep = ep
        self.d_title.setText(_elide(_clean(ep.get("title")),
                                    self.page_w - 300,
                                    QFont("Microsoft YaHei", 12, QFont.Bold)))
        parts = [p for p in (ep.get("pub_index"), ep.get("pub_time")) if p]
        if ep.get("rating"):
            parts.append(f"{ep['rating']:.1f} 分")
        self.d_meta.setText("  ·  ".join(parts) or " ")
        self.d_desc.setText("简介加载中…")
        self.d_desc.setStyleSheet("color:#555;")
        self.d_cover.setPixmap(QPixmap())   # 先清空占位
        self.d_cover.setText("…")
        sid = int(ep.get("season_id") or 0)
        followed = self.store.get(f"bili:{sid}") is not None
        self.d_follow.setText("已追" if followed else "+ 追")
        self.d_follow.setEnabled(not followed)
        self.detailCard.show()

        # 封面：请求大图
        self.request_cover(sid, ep.get("cover") or ep.get("square_cover") or "")
        # 简介异步
        self._detail_lock += 1
        lock = self._detail_lock
        threading.Thread(target=self._detail_worker, args=(sid, lock),
                         daemon=True).start()

    def _detail_worker(self, sid: int, lock: int):
        season = bili_client.get_season(sid) if sid else None
        desc = (season or {}).get("desc") or ""
        rating = ""
        if season is None:
            desc = f"简介拉取失败：{bili_client.LAST_ERROR}"
        self.detailReady.emit(f"tl:{sid}", desc, rating)

    def _on_detail(self, key: str, desc: str, rating: str):
        if not self._detail_ep or key != f"tl:{self._detail_ep.get('season_id')}":
            return
        self.d_desc.setText(desc or "暂无简介")
        self.d_desc.setStyleSheet("color:#555;" if desc else "color:#999;")

    def _follow_from_detail(self):
        if not self._detail_ep:
            return
        if self.follow_ep(self._detail_ep):
            self.d_follow.setText("已追")
            self.d_follow.setEnabled(False)
            self.d_follow.setStyleSheet(
                "QPushButton{background:#bbb;color:white;border:none;"
                "border-radius:6px;font:12px;}")

    # ================= 追番操作 =================
    def follow_ep(self, ep: dict) -> bool:
        sid = int(ep.get("season_id") or 0)
        if not sid:
            return False
        rec = {
            "source": "bili",
            "season_id": sid,
            "name": _clean(ep.get("title")),
            "name_cn": _clean(ep.get("title")),
            "cover_url": (ep.get("cover") or ep.get("square_cover") or "")
            .replace("http://", "https://"),
            "pub_index": ep.get("pub_index") or "",
        }
        if ep.get("pub_ts"):
            import datetime as _d2
            wd = _d2.datetime.fromtimestamp(int(ep["pub_ts"])).isoweekday()
            rec["air_weekday"] = wd
        got = self.store.add_bili(rec)
        if got is not None:
            self.store.set_notify(got["subject_id"], "enabled", True)
            # 导视/目录行的按钮状态同步
            key = f"tl:{sid}"
            for card in self._cover_cards.get(key, []):
                if hasattr(card, "follow_btn") and card.follow_btn.isEnabled():
                    card.follow_btn.setText("已追")
                    card.follow_btn.setEnabled(False)
                    card.follow_btn.setStyleSheet(
                        "QPushButton{background:#bbb;color:white;border:none;"
                        "border-radius:6px;font:12px;}")
            return True
        return False

    def request_cover(self, season_id: int, url: str):
        if not season_id or not url or not url.startswith("http"):
            return
        key = f"tl:{season_id}"
        cache = os.path.join(os.path.dirname(self.store.path),
                             "covers", f"tl_{season_id}.jpg")
        if os.path.exists(cache) or key in self._cover_locks:
            return
        self._cover_locks.add(key)
        threading.Thread(target=self._cover_worker,
                         args=(season_id, url, cache), daemon=True).start()

    def _cover_worker(self, season_id: int, url: str, cache: str):
        try:
            ok = bili_client.download(url, cache)
        except Exception:  # noqa: BLE001
            ok = False
        if not ok:
            self._cover_locks.discard(f"tl:{season_id}")
            return
        self.coversReady.emit(f"tl:{season_id}", cache)

    def _on_cover(self, key: str, path: str):
        self._cover_locks.discard(key)
        for card in self._cover_cards.get(key, []):
            try:
                card.apply_cover(path)
            except RuntimeError:
                pass    # 卡片已被销毁

    # ---- 我的追番操作 ----
    def mark_watched(self, key: str):
        sub = self.store.get(key)
        if not sub:
            return
        cur = int((sub.get("watch") or {}).get("current_episode", 0) or 0)
        self.store.mark_watched(key, cur + 1)
        self._rebuild_subs()

    def calibrate(self, key: str):
        sub = self.store.get(key)
        if not sub:
            return
        cur = int((sub.get("watch") or {}).get("current_episode", 0) or 0)
        self.store.calibrate(key, cur, _dt.date.today())
        self._rebuild_subs()

    def remove_sub(self, key: str):
        self.store.remove(key)
        self._rebuild_subs()

    # ---- 搜索 ----
    def _toggle_source(self):
        self._search_source = ("bangumi" if self._search_source == "bili"
                               else "bili")
        self.srcBtn.setText("B站源" if self._search_source == "bili"
                            else "Bangumi")
        self.srcBtn.setStyleSheet(
            f"QPushButton{{background:rgba({'251,114,153' if self._search_source == 'bili' else '0,159,170'},30);"
            f"color:{BILI if self._search_source == 'bili' else TEAL};"
            f"border:none;border-radius:6px;font:12px;}}")

    def _ask_search(self):
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(self, "搜索番剧",
                                        f"番剧名（{self._search_source}）：")
        if not ok or not text.strip() or self._searching:
            return
        self._searching = True
        self.searchInput.setText(f"正在搜索「{text.strip()}」…")
        threading.Thread(target=self._search_worker, args=(text.strip(),),
                         daemon=True).start()

    def _search_worker(self, keyword: str):
        if self._search_source == "bili":
            res = bili_client.search_bangumi(keyword)
            if res is None:
                self.searchDone.emit([], True)
                return
            subs = [{"source": "bili", "season_id": int(r["season_id"]),
                     "name": r["name"], "name_cn": r["name"],
                     "cover_url": r["cover"], "index_show": r.get("index_show"),
                     "score": r.get("score"), "desc": r.get("desc")}
                    for r in res]
        else:
            subs = self._bgm_search(keyword)
        self.searchDone.emit(subs, False)

    def _bgm_search(self, keyword: str) -> list:
        """Bangumi 搜索（备用源）。"""
        import json as _json
        from DyberPet.bangumi import bgm_client
        body = _json.dumps({"keyword": keyword,
                            "filter": {"type": [2]}}).encode()
        d = bgm_client.get_json("https://api.bgm.tv/v0/search/subjects?limit=8",
                                data=body, method="POST")
        out = []
        for it in (d or {}).get("data") or []:
            out.append({"source": "bangumi", "subject_id": int(it.get("id") or 0),
                        "name": it.get("name") or "", "name_cn": it.get("name_cn") or "",
                        "date": it.get("date") or "", "cover_url":
                            (it.get("images") or {}).get("large") or ""})
        return out

    def _on_search(self, subs: list, failed: bool):
        self._searching = False
        while self.searchLay.count():
            item = self.searchLay.takeAt(0)
            wdg = item.widget()
            if wdg:
                wdg.setParent(None)
                wdg.deleteLater()
        if failed:
            self.searchInput.setText("搜索失败（网络问题）——点击重试")
            return
        self.searchInput.setText("输入番剧名搜索…（点击弹出输入框）")
        if not subs:
            return
        for s in subs:
            added = self.store.get(sub_key(s)) is not None
            self.searchLay.addWidget(SearchRow(self, s, added))
        self._sync_stack_height()

    def add_from_search(self, subject: dict):
        if subject.get("source") == "bili":
            got = self.store.add_bili(subject)
        else:
            got = self.store.add(subject)
        if got is not None:
            self.store.set_notify(got["subject_id"], "enabled", True)
            self._rebuild_subs()

    # ================= 布局同步 =================
    def _sync_stack_height(self):
        page = self.stack.currentWidget()
        if page is None:
            return
        h = page.sizeHint().height()
        self.stack.setFixedHeight(max(h, 200))
        self.stack.updateGeometry()

    def _switch(self, key: str):
        idx = {"guide": 0, "catalog": 1, "subs": 2}.get(key, 0)
        self.stack.setCurrentIndex(idx)
        if key == "subs":
            self._rebuild_subs()
        self._sync_stack_height()

    def showEvent(self, e):
        super().showEvent(e)
        self._sync_stack_height()
