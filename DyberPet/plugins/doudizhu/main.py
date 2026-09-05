# coding:utf-8
"""斗地主陪玩插件入口 v2——真实斗地主手感版。

定位（用户拍板）：
- 桌宠只演一个 AI（不再精分两角），另一个座位是静默人机。
- 牌型语音用 voice/ 预合成 mp3（零延迟、离线稳定），谁出牌都触发。
- Ollama 只当狗头军师：规则记牌器出简报，LLM 润色成给玩家的建议。
"""
import os

from DyberPet.llm_core import DEFAULT_OLLAMA_BASE
from DyberPet.plugin_system.base import Plugin

from .commentary import Commentator
from .ui import DouDizhuWindow
from .voice import VoiceBank


class DouDizhuPlugin(Plugin):
    def on_load(self):
        pass

    def on_enable(self):
        # 不随桌宠自动弹窗：牌桌由 launch() 手动打开（游戏插件解耦铁律）
        pass

    def launch(self):
        """手动打开斗地主牌桌（插件中心「打开」按钮触发）。"""
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
        commentator = Commentator(
            taunt=bool(cfg.get('pet_taunt', True)),
            use_llm=bool(cfg.get('advisor', False)),
            model=cfg.get('llm_model', 'qwen2.5:7b'),
            ollama_base=DEFAULT_OLLAMA_BASE,
        )
        voices = VoiceBank(os.path.dirname(os.path.abspath(__file__)))

        window = DouDizhuWindow(self.api, commentator, voices)
        # 窗口关闭后清掉 Python 引用，下次 launch 能干净重建
        window.destroyed.connect(lambda: setattr(self, 'window', None))
        window.show()
        self.window = window

    def on_disable(self):
        w = getattr(self, 'window', None)
        if w is not None:
            try:
                w.close()
            except RuntimeError:
                pass
            self.window = None
