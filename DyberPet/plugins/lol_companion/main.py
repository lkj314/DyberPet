"""LoL 游戏陪玩插件入口：游戏内解说 + LCU 客户端自动化 + 对局战报。"""
import DyberPet.settings as settings
from DyberPet.plugin_system.base import Plugin
from DyberPet.llm_core import DEFAULT_OLLAMA_BASE
from .worker import LoLCompanionWorker
from .lcu_worker import LcuWorker


class LoLCompanionPlugin(Plugin):
    def on_load(self):
        pass

    def on_enable(self):
        cfg = self.api.settings.all()
        # ① 游戏内解说（Live Client Data API 2999 + Ollama）——原有链路不动
        self.worker = LoLCompanionWorker(
            cfg=cfg, ollama_base=DEFAULT_OLLAMA_BASE,
            model=cfg.get('model'), interval=2.0)
        self.worker.caster_line.connect(self._on_caster_line)
        self.worker.companion_react.connect(lambda emo: self.api.pet.react(emo))
        self.worker.start()
        # ② LCU 客户端线程（自动接受/点赞/回房 + 对局战报）
        self.lcu_worker = LcuWorker(cfg)
        self.lcu_worker.report_ready.connect(self._on_report)
        self.lcu_worker.start()

    def _on_caster_line(self, line: str):
        """解说词消费端：气泡与 TTS 语音按设置独立分流。

        语音引擎设置：自动=edge-tts 优先+本地兜底；仅本地=直接 Windows SAPI
        （零联网零延迟，Huihui/Kangkang 老引擎机械腔）。
        """
        try:
            if self.api.settings.get('bubble', True):
                self.api.pet.say(line)
            if self.api.settings.get('tts', True):
                self.api.pet.speak(
                    line,
                    local_only=(self.api.settings.get('voice_engine', '自动')
                                == '仅本地'))
        except Exception as e:  # noqa: BLE001
            print(f'[lol_companion] caster line failed: {e!r}')

    # ---- 对局战报：气泡判词 + 通知栏战报卡 + 修为联动 ----
    def _on_report(self, report: dict):
        try:
            # 气泡：境界判词短句（纯文字，不 TTS）
            self.api.pet.say(report.get('bubble') or '')
            # 胜负情绪联动（程序化动作）
            self.api.pet.react('excited' if report.get('result') == 'win' else 'sad')
            # 通知栏：完整 KDA 战报文字卡片
            notify = report.get('notify')
            if notify:
                self.api.pet.notify('system', notify)
            # 修为联动：胜利加修为 + 历练 buff（额度设置可调，0 = 关闭）
            if report.get('result') == 'win':
                exp = int(self.api.settings.get('exp_reward', 300) or 0)
                if exp > 0:
                    self.api.add_exp(exp, 'LoL 胜利')
                    self.api.add_adventure_buff()
        except Exception as e:  # noqa: BLE001
            print(f'[lol_companion] report handler failed: {e!r}')

    def on_disable(self):
        for name in ('worker', 'lcu_worker'):
            w = getattr(self, name, None)
            if w is not None:
                try:
                    w.stop()
                    w.wait(3000)
                except Exception:  # noqa: BLE001
                    pass
                setattr(self, name, None)
