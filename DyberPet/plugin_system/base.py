"""插件基类：所有插件继承 Plugin，只通过 PetAPI 与宿主交互，禁止 import 内部模块。"""
from typing import Optional
from .api import PetAPI


class Plugin:
    #: 由 PluginManager 在加载时填充
    plugin_id: str = ""
    manifest: dict = {}

    def __init__(self, api: PetAPI):
        self.api = api
        self.worker = None

    # ---- 生命周期钩子（均由 PluginManager 在异常边界内调用）----
    def on_load(self):
        """插件被加载、设置就绪后调用一次。用于注册事件 / 菜单。"""
        pass

    def on_enable(self):
        """插件被启用（首次或重启后）时调用。启动后台任务。"""
        pass

    def on_disable(self):
        """插件被禁用 / 应用退出前调用。停止并清理。"""
        pass

    def launch(self):
        """手动打开插件的 UI（如游戏窗口）。由插件中心「打开」按钮触发。

        默认空实现：无 UI 的插件（如后台解说）无需关心。
        """
        pass

    def on_unload(self):
        """插件被卸载前调用。"""
        pass
