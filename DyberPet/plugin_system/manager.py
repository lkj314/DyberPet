"""插件管理器：发现 / 校验 / 生命周期 / 崩溃守护。

设计原则（见 docs/plugin_center_blueprint.md）：
- in-process Python，无真沙箱；靠 QThread 隔离 + hook 边界 try/except + 崩溃计数自动禁用。
- 复用现有 MOD 文件夹扫描范式，不引入 setuptools entry_points。
"""
import os
import json
import logging
import importlib

import DyberPet.settings as settings
from .api import PetAPI

logger = logging.getLogger(__name__)

MAX_CONSECUTIVE_ERRORS = 5


class PluginManager:
    def __init__(self, pet_widget, app):
        self.pet = pet_widget
        self.app = app
        self.plugins = {}  # pid -> {manifest, instance, enabled, error_count}
        # plugin_system/ 的上一级即 DyberPet/，plugins 同级的 DyberPet/plugins
        self.plugins_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), 'plugins')

    # ---- 发现 ----
    def discover(self):
        if not os.path.isdir(self.plugins_dir):
            return
        for entry in sorted(os.listdir(self.plugins_dir)):
            d = os.path.join(self.plugins_dir, entry)
            if not os.path.isdir(d):
                continue
            mpath = os.path.join(d, 'plugin.json')
            if not os.path.isfile(mpath):
                continue
            try:
                manifest = json.load(open(mpath, encoding='utf-8'))
            except Exception:  # noqa: BLE001
                logger.warning('插件 %s 的 plugin.json 解析失败', entry)
                continue
            pid = manifest.get('id') or entry
            if pid in self.plugins:
                logger.warning('插件 id 重复，跳过：%s', pid)
                continue
            self._ensure_settings(pid, manifest)
            enabled = settings.plugins_settings.get(pid, {}).get('enabled', True)
            self.plugins[pid] = {
                'manifest': manifest,
                'instance': None,
                'enabled': enabled,
                'error_count': 0,
            }
        logger.info('发现插件 %d 个：%s', len(self.plugins), list(self.plugins))

    def _ensure_settings(self, pid, manifest):
        ps = settings.plugins_settings.setdefault(pid, {})
        for f in manifest.get('settings_schema', []):
            key = f.get('key')
            if key is not None and key not in ps:
                ps[key] = f.get('default')

    # ---- 生命周期 ----
    def start_enabled(self):
        for pid in list(self.plugins):
            if self.plugins[pid]['enabled']:
                self._safe_enable(pid)

    def _safe_enable(self, pid):
        p = self.plugins.get(pid)
        if p is None:
            return
        try:
            if p['instance'] is None:
                entry = p['manifest']['entry']            # "main:MyPlugin"
                mod_name, cls_name = entry.split(':', 1)
                module = importlib.import_module(
                    f'DyberPet.plugins.{pid}.{mod_name}')
                cls = getattr(module, cls_name)
                api = PetAPI(self.pet, self.app, pid)
                inst = cls(api)
                inst.plugin_id = pid
                inst.manifest = p['manifest']
                p['instance'] = inst
                inst.on_load()
            inst = p['instance']
            inst.on_enable()
            p['enabled'] = True
            p['error_count'] = 0
        except Exception:  # noqa: BLE001
            p['error_count'] += 1
            logger.exception('插件 %s 启用失败', pid)
            self._maybe_auto_disable(pid)

    def _disable(self, pid):
        p = self.plugins.get(pid)
        if p is None:
            return
        inst = p.get('instance')
        if inst is not None:
            try:
                inst.on_disable()
            except Exception:  # noqa: BLE001
                logger.exception('插件 %s 停用异常', pid)
        p['enabled'] = False

    def set_enabled(self, pid, flag: bool):
        if pid not in self.plugins:
            return
        settings.plugins_settings.setdefault(pid, {})['enabled'] = bool(flag)
        settings.save_settings()
        if flag:
            self._safe_enable(pid)
        else:
            self._disable(pid)

    def launch(self, pid):
        """手动打开插件的 UI（如游戏窗口）。插件需实现 ``launch()`` 钩子并声明 launchable。

        - 插件未启用时自动启用（确保实例就绪）。
        - launch() 失败会记录日志并通知，不会拖垮宿主。
        """
        p = self.plugins.get(pid)
        if p is None:
            return
        if not p['enabled']:
            self.set_enabled(pid, True)
        inst = p.get('instance')
        if inst is None:
            return
        meth = getattr(inst, 'launch', None)
        if not callable(meth):
            return
        try:
            meth()
        except Exception:  # noqa: BLE001
            logger.exception('插件 %s 启动失败', pid)
            try:
                self.pet.register_notification(
                    'plugin', f'插件「{p["manifest"].get("name", pid)}」启动失败')
            except Exception:  # noqa: BLE001
                pass

    def stop_all(self):
        for pid in list(self.plugins):
            self._disable(pid)

    def _maybe_auto_disable(self, pid):
        p = self.plugins.get(pid)
        if p is None:
            return
        if p['error_count'] >= MAX_CONSECUTIVE_ERRORS:
            logger.error('插件 %s 连续出错 %d 次，自动禁用', pid, p['error_count'])
            p['enabled'] = False
            settings.plugins_settings.setdefault(pid, {})['enabled'] = False
            settings.save_settings()
            self._disable(pid)
            try:
                self.pet.register_notification(
                    'plugin',
                    f'插件「{p["manifest"].get("name", pid)}」连续出错已自动禁用')
            except Exception:  # noqa: BLE001
                pass
