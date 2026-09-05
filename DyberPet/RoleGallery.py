# coding:utf-8
"""角色图鉴——读取 res/role/* 与 res/pet/* 下的 info/info.json 渲染角色信息卡。

借鉴官方 v0.8.10 的 info 角色信息卡设计（coverImages/pfp/intro/tages/author）。
兼容处理：没有 info/ 的老角色（Kitty/ChrisKitty/派蒙）显示基础信息页。
"""
from __future__ import annotations

import json
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon, QPixmap
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QStackedWidget, QVBoxLayout,
                               QWidget)

import DyberPet.settings as settings

basedir = settings.BASEDIR


def _scan_roles():
    """返回 [(显示名, 目录路径, 类型)]，role 在前 pet 在后。"""
    out = []
    for kind in ('role', 'pet'):
        root = os.path.join(basedir, 'res', kind)
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            if name == 'sys':
                continue
            d = os.path.join(root, name)
            if os.path.isdir(d):
                out.append((name, d, kind))
    return out


class RoleGalleryDialog(QDialog):
    """角色图鉴：左侧角色列表，右侧信息卡（封面/标签/介绍/作者）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr('Role Gallery'))
        self.resize(680, 460)
        self.setStyleSheet(
            "QDialog{background:#faf7f2;} "
            "QLabel#galleryIntro{color:#444;} "
            "QLabel#galleryName{color:#333;}")

        self._roles = _scan_roles()
        self._pages = {}

        layout = QHBoxLayout(self)
        self.listw = QListWidget(self)
        self.listw.setFixedWidth(190)
        self.listw.setIconSize(QPixmap(32, 32).size() * 0 + self.listw.iconSize())
        self.stack = QStackedWidget(self)
        layout.addWidget(self.listw)
        layout.addWidget(self.stack, 1)

        for name, d, kind in self._roles:
            info = self._load_info(d)
            page = self._build_page(name, d, info, kind)
            self._pages[name] = page
            self.stack.addWidget(page)

            item = QListWidgetItem(name)
            pfp = os.path.join(d, 'info', 'pfp.png')
            if os.path.isfile(pfp):
                item.setIcon(QIcon(pfp))
            self.listw.addItem(item)

        if self.listw.count():
            self.listw.currentRowChanged.connect(self.stack.setCurrentIndex)
            self.listw.setCurrentRow(0)

    @staticmethod
    def _load_info(d):
        p = os.path.join(d, 'info', 'info.json')
        if os.path.isfile(p):
            try:
                return dict(json.load(open(p, encoding='utf-8')))
            except Exception:  # noqa: BLE001
                return {}
        return {}

    # ------------------------------------------------------------------ #
    def _build_page(self, name, d, info, kind):
        page = QWidget(self)
        v = QVBoxLayout(page)
        v.setContentsMargins(18, 14, 18, 14)
        v.setSpacing(8)

        # 封面
        cover = QLabel(page)
        cover.setAlignment(Qt.AlignCenter)
        cover.setMinimumHeight(150)
        cover.setStyleSheet("background:#eeece6;border-radius:8px;")
        for c in (info.get('coverImages') or []):
            p = os.path.join(d, 'info', c)
            if os.path.isfile(p):
                pm = QPixmap(p)
                if not pm.isNull():
                    cover.setPixmap(pm.scaled(
                        400, 190, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                    break
        else:
            cover.setText(self.tr('(no cover)'))
        v.addWidget(cover)

        # 名字
        name_lbl = QLabel(name, page)
        name_lbl.setObjectName('galleryName')
        name_lbl.setFont(QFont('Microsoft YaHei', 15, QFont.Bold))
        v.addWidget(name_lbl)

        # 标签
        tags = info.get('tages') or {}
        if tags:
            tag_row = QWidget(page)
            h = QHBoxLayout(tag_row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(6)
            h.addStretch(0)
            for text, color in tags.items():
                lbl = QLabel(text, tag_row)
                lbl.setStyleSheet(
                    f"background:{color};color:#333;border-radius:8px;"
                    f"padding:2px 10px;font-size:12px;")
                h.addWidget(lbl)
            h.addStretch(1)
            v.addWidget(tag_row)

        # 介绍
        intro = QLabel(info.get('intro') or self.tr('No gallery info yet.'), page)
        intro.setObjectName('galleryIntro')
        intro.setWordWrap(True)
        v.addWidget(intro, 1)

        # 作者
        author = info.get('author') or {}
        if author.get('name'):
            links = ' / '.join(f"{k}: {v}" for k, v in (author.get('links') or {}).items())
            txt = self.tr('Author') + f": {author['name']}"
            if links:
                txt += f'   ({links})'
            if author.get('infos'):
                txt += f"  —— {author['infos']}"
            al = QLabel(txt, page)
            al.setStyleSheet("color:#888;font-size:12px;")
            al.setWordWrap(True)
            v.addWidget(al)

        kind_lbl = QLabel(self.tr('Companion Pet') if kind == 'pet'
                          else self.tr('Character'), page)
        kind_lbl.setStyleSheet("color:#aaa;font-size:11px;")
        kind_lbl.setAlignment(Qt.AlignRight)
        v.addWidget(kind_lbl)
        return page
