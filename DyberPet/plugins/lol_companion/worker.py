"""LoL 陪玩后台线程：轮询 LCU + 调 Ollama，通过 Signal 把结果投回主线程。

逻辑（GameDataReader / Caster / caster_worker / emotion_for）来自共享内核
``DyberPet.llm_core``；本模块只负责「把内核逻辑跑在 QThread 并桥接到 PetAPI」。
"""
import threading
from PySide6.QtCore import QThread, Signal

from DyberPet.llm_core import (
    GameDataReader, Caster, caster_worker, emotion_for, DEFAULT_OLLAMA_BASE,
)


class LoLCompanionWorker(QThread):
    caster_line = Signal(str)
    companion_react = Signal(str)

    def __init__(self, cfg: dict, ollama_base=DEFAULT_OLLAMA_BASE,
                 model=None, interval: float = 2.0, parent=None):
        super().__init__(parent)
        # 直接引用 plugins_settings['lol_companion']，UI 改设置即时生效
        self.cfg = cfg
        self.reader = GameDataReader()
        self.caster = Caster(ollama_base=ollama_base,
                             model=model or cfg.get('model'),
                             style=cfg.get('style', '肥牛'))
        self.interval = interval
        self._stop_event = threading.Event()

    def run(self):
        caster_worker(
            self.reader, self.caster, self.interval, self.cfg,
            emit=lambda line: self.caster_line.emit(line)
            if self.cfg.get('bubble', True) else None,
            stop=self._stop_event,
            emit_meta=lambda prio, evs, chs, me:
                self.companion_react.emit(emotion_for(prio, evs, chs, me).value)
                if self.cfg.get('reactions', True) else None,
        )

    def stop(self):
        self._stop_event.set()
