# coding:utf-8
"""插件中心界面（Phase 1.5：按 plugin.json 的 settings_schema 动态渲染）。

每个插件的设置组不再硬编码，而是读取该插件 manifest 中的 settings_schema 字段
动态生成 Switch/Combo 卡片。新增插件只要写好 plugin.json 和对应的后端代码，
UI 这边通常不需要再改。

目前支持的 schema 类型：
  - switch: 布尔开关
  - combo:  下拉选择（支持静态 options 或动态 options_ref）
  - slider: 整数滑条（min/max/default，如游戏插件的难度档位）
"""
import os
import json

from qfluentwidgets import (SettingCardGroup, SwitchSettingCard, PushSettingCard,
                            SettingCard, ScrollArea, ExpandLayout, InfoBar,
                            InfoBarPosition)
from qfluentwidgets import FluentIcon as FIF
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QWidget, QLabel, QSlider

import DyberPet.settings as settings
from DyberPet.llm_core import list_ollama_models
from .BasicSettingUI import RECOMMENDED_MODELS
from .custom_utils import Dyber_ComboBoxSettingCard

basedir = settings.BASEDIR


class PluginCenterInterface(ScrollArea):
    """插件中心——按各插件 manifest 的 settings_schema 动态生成设置卡片。"""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("PluginCenterInterface")
        self.scrollWidget = QWidget()
        self.expandLayout = ExpandLayout(self.scrollWidget)

        self._plugin_widgets = {}   # pid -> {key: widget/card}
        self._ollama_combos = []  # [(pid, key, card)]

        self.__initContent()
        self.__initWidget()

    def __initContent(self):
        plugins = self._load_plugins()
        for pid, manifest in plugins.items():
            self._build_plugin_group(pid, manifest)

    def _load_plugins(self):
        """获取插件清单；优先用已启动的 PluginManager，否则直接扫描目录。"""
        pm = getattr(settings, 'plugin_manager', None)
        if pm and getattr(pm, 'plugins', None):
            return {pid: info['manifest'] for pid, info in pm.plugins.items()}

        plugins_dir = os.path.join(basedir, 'DyberPet', 'plugins')
        if not os.path.isdir(plugins_dir):
            return {}

        plugins = {}
        for entry in sorted(os.listdir(plugins_dir)):
            d = os.path.join(plugins_dir, entry)
            if not os.path.isdir(d):
                continue
            mpath = os.path.join(d, 'plugin.json')
            if not os.path.isfile(mpath):
                continue
            try:
                manifest = json.load(open(mpath, encoding='utf-8'))
            except Exception:  # noqa: BLE001
                continue
            plugins[manifest.get('id', entry)] = manifest
        return plugins

    def _build_plugin_group(self, pid, manifest):
        group = SettingCardGroup(manifest.get('name', pid), self.scrollWidget)
        schema = manifest.get('settings_schema', [])
        ps = settings.plugins_settings.setdefault(pid, {})

        for field in schema:
            key = field.get('key')
            ftype = field.get('type')
            if not key or not ftype:
                continue

            label = field.get('label', key)
            desc = field.get('description', '')
            icon = self._resolve_icon(field.get('icon'))
            default = field.get('default')

            if key not in ps and default is not None:
                ps[key] = default

            value = ps.get(key, default)

            if ftype == 'switch':
                card = SwitchSettingCard(icon, label, desc, parent=group)
                card.setChecked(bool(value))
                card.switchButton.checkedChanged.connect(
                    lambda checked, pid=pid, key=key: self._on_switch_changed(pid, key, checked))
                self._plugin_widgets.setdefault(pid, {})[key] = card
                group.addSettingCard(card)

            elif ftype == 'combo':
                options_ref = field.get('options_ref')
                static_options = field.get('options') or []
                texts = field.get('texts') or static_options

                if options_ref == 'ollama_models':
                    opts = list(static_options) if static_options else list(RECOMMENDED_MODELS)
                    display_texts = opts
                else:
                    opts = list(static_options)
                    display_texts = list(texts)

                card = Dyber_ComboBoxSettingCard(opts, display_texts, icon, label, desc, parent=group)
                if value and str(value) in opts:
                    card.comboBox.setCurrentText(str(value))
                elif value:
                    card.comboBox.addItem(str(value), userData=str(value))
                    card.comboBox.setCurrentText(str(value))

                card.comboBox.currentTextChanged.connect(
                    lambda text, pid=pid, key=key: self._on_combo_changed(pid, key, text))
                self._plugin_widgets.setdefault(pid, {})[key] = card

                if options_ref == 'ollama_models':
                    self._ollama_combos.append((pid, key, card))
                    refresh_card = PushSettingCard(
                        self.tr('Refresh'), FIF.SYNC,
                        self.tr('Model List'),
                        self.tr('Click to scan locally pulled Ollama models'),
                        parent=group)
                    refresh_card.button.clicked.connect(self._refresh_models_async)
                    group.addSettingCard(card)
                    group.addSettingCard(refresh_card)
                    continue
                group.addSettingCard(card)

            elif ftype == 'slider':
                card = SettingCard(icon, label, desc, parent=group)
                mn = int(field.get('min', 0))
                mx = int(field.get('max', 100))
                try:
                    cur = int(value) if value is not None else mn
                except (TypeError, ValueError):
                    cur = mn
                cur = max(mn, min(mx, cur))

                slider = QSlider(Qt.Horizontal, card)
                slider.setRange(mn, mx)
                slider.setValue(cur)
                slider.setFixedWidth(150)
                val_label = QLabel(str(cur), card)
                val_label.setFixedWidth(24)
                val_label.setAlignment(Qt.AlignCenter)

                card.hBoxLayout.addWidget(slider, 0, Qt.AlignRight)
                card.hBoxLayout.addWidget(val_label, 0, Qt.AlignRight)
                card.hBoxLayout.addSpacing(16)

                def _on_slider_changed(v, pid=pid, key=key, lbl=val_label):
                    lbl.setText(str(int(v)))
                    settings.plugins_settings.setdefault(pid, {})[key] = int(v)
                    settings.save_settings()

                slider.valueChanged.connect(_on_slider_changed)
                self._plugin_widgets.setdefault(pid, {})[key] = card
                group.addSettingCard(card)

        # 带 UI 的插件（如游戏）提供手动「打开」入口，避免随桌宠自动弹窗
        if manifest.get('launchable'):
            launch_card = PushSettingCard(
                self.tr('Open'), FIF.PLAY,
                self.tr('Launch'),
                self.tr('Open the plugin window manually (won\'t auto-open with pet)'),
                parent=group)
            launch_card.button.clicked.connect(
                lambda _checked=False, pid=pid: self._launch_plugin(pid))
            group.addSettingCard(launch_card)

        self.expandLayout.addWidget(group)

    def _resolve_icon(self, icon_name):
        """把 schema 里的 icon 字段解析成 FIF 枚举或 QIcon；解析失败用 FIF.SETTING 兜底。"""
        if not icon_name:
            return FIF.SETTING
        if isinstance(icon_name, str):
            if '/' in icon_name or '\\' in icon_name:
                path = icon_name if os.path.isabs(icon_name) else os.path.join(basedir, icon_name)
                return QIcon(path)
            try:
                return getattr(FIF, icon_name)
            except AttributeError:
                pass
        return FIF.SETTING

    def _on_switch_changed(self, pid, key, checked):
        settings.plugins_settings.setdefault(pid, {})[key] = bool(checked)
        settings.save_settings()
        if key == 'enabled':
            pm = getattr(settings, 'plugin_manager', None)
            if pm and pid in pm.plugins:
                pm.set_enabled(pid, bool(checked))

    def _launch_plugin(self, pid):
        pm = getattr(settings, 'plugin_manager', None)
        if pm is None or pid not in pm.plugins:
            InfoBar.warning(
                '', self.tr('Plugin manager not ready'),
                duration=3000, position=InfoBarPosition.BOTTOM, parent=self.window())
            return
        if not pm.plugins[pid]['enabled']:
            # 未启用则先启用，再打开（launch 内部会处理）
            pm.set_enabled(pid, True)
            InfoBar.info(
                '', self.tr('Plugin enabled, launching…'),
                duration=1500, position=InfoBarPosition.BOTTOM, parent=self.window())
        pm.launch(pid)

    def _on_combo_changed(self, pid, key, value):
        settings.plugins_settings.setdefault(pid, {})[key] = value
        settings.save_settings()

    def _populate_combo(self, card, models, current_value):
        combo = card.comboBox
        combo.blockSignals(True)
        combo.clear()
        for m in models:
            combo.addItem(m, userData=m)
        idx = combo.findText(current_value) if current_value else -1
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)

    def _refresh_models_async(self):
        """异步扫描本机已拉取的 Ollama 模型，更新所有 options_ref=ollama_models 的下拉框。"""
        class ModelFetchThread(QThread):
            result = Signal(list)

            def run(self):
                try:
                    self.result.emit(list_ollama_models())
                except Exception:  # noqa: BLE001
                    self.result.emit([])

        self._model_fetch = ModelFetchThread()
        self._model_fetch.result.connect(self._on_models_fetched)
        self._model_fetch.start()

    def _on_models_fetched(self, models):
        if not models:
            InfoBar.warning(
                '', self.tr('No Ollama models found / Ollama not running'),
                duration=3000, position=InfoBarPosition.BOTTOM, parent=self.window())
            return
        merged = list(dict.fromkeys(models + RECOMMENDED_MODELS))
        for pid, key, card in self._ollama_combos:
            current_value = settings.plugins_settings.get(pid, {}).get(key, RECOMMENDED_MODELS[0])
            self._populate_combo(card, merged, current_value)
        InfoBar.success(
            '', self.tr(f'Found {len(models)} local model(s)'),
            duration=2000, position=InfoBarPosition.BOTTOM, parent=self.window())

    def __initWidget(self):
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setViewportMargins(0, 75, 0, 20)
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)
        self.__setQss()

    def __setQss(self):
        self.scrollWidget.setObjectName('scrollWidget')
        theme = 'light'
        qss_path = os.path.join(basedir, 'res/icons/system/qss/', theme, 'setting_interface.qss')
        with open(qss_path, encoding='utf-8') as f:
            self.setStyleSheet(f.read())
