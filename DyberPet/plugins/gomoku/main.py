"""五子棋陪玩插件入口。"""
from DyberPet.plugin_system.base import Plugin
from DyberPet.llm_core import DEFAULT_OLLAMA_BASE

from .game_engine import GomokuEngine
from .ai_engine import GomokuAI
from .commentator import Commentator
from .board_window import GomokuWindow


class GomokuPlugin(Plugin):
    def on_load(self):
        pass

    def on_enable(self):
        # 不在桌宠启动时自动弹窗：棋盘由 launch() 手动打开，
        # 避免多个游戏插件随桌宠一起启动互相干扰 / 出 bug。
        # 这里仅做轻量占位，真正的引擎/AI/窗口在 launch() 里按需创建。
        pass

    def launch(self):
        """手动打开五子棋棋盘（插件中心「打开」按钮触发）。"""
        # 旧窗口可能已被关闭（WA_DeleteOnClose 已销毁），清理失效引用
        old = getattr(self, 'window', None)
        if old is not None:
            try:
                if old.isVisible():
                    old.show()
                    old.raise_()
                    old.activateWindow()
                    return
            except RuntimeError:
                pass
            try:
                old.deleteLater()
            except Exception:  # noqa: BLE001
                pass
            self.window = None

        cfg = self.api.settings.all()
        engine = GomokuEngine(size=cfg.get("board_size", 15))
        ai = GomokuAI()
        ai.configure(cfg.get("difficulty", "普通"), cfg.get("handicap", False))

        commentator = Commentator(
            taunt=cfg.get("taunt", True),
            use_llm=cfg.get("llm", False),
            model=cfg.get("llm_model", "gemma3:4b"),
            ollama_base=DEFAULT_OLLAMA_BASE,
        )

        window = GomokuWindow(engine, ai, commentator, self.api)
        # 窗口关闭后清掉 Python 引用，下次 launch 能干净重建
        window.destroyed.connect(lambda: setattr(self, 'window', None))
        window.show()
        self.window = window

    def on_disable(self):
        w = getattr(self, "window", None)
        if w is not None:
            try:
                w.close()
            except RuntimeError:
                pass
            self.window = None
