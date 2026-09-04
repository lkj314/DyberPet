"""桌宠对话窗口 + 语音播报(TTS) + 语音输入(STT)。

- 对话：复用 ``DyberPet.lol_companion.Caster`` 与本地 Ollama（带多轮记忆）。
- 语音播报：edge-tts（微软在线，中文自然，需联网）。
- 语音输入：vosk 离线中文模型（避免 Google STT 被墙），首次使用自动下载。

所有第三方重依赖（edge_tts / vosk / pyaudio / QtMultimedia）均延迟/容错导入，
缺包时对应功能降级而不是让整个程序崩。
"""

import asyncio
import json
import logging
import os
import tempfile
import urllib.request
import zipfile

from PySide6.QtCore import QObject, Signal, QThread, Qt, QUrl
from PySide6.QtWidgets import (QWidget, QListWidget, QLineEdit, QPushButton,
                              QLabel, QVBoxLayout, QHBoxLayout, QMessageBox)
from PySide6.QtGui import QTextCursor

import DyberPet.settings as settings
from DyberPet.lol_companion import Caster, COMPANION_PROMPT, sanitize_commentary

logger = logging.getLogger(__name__)

# 模型没产出正文时的兜底回复（不要把它写进多轮历史，避免污染上下文）
EMPTY_REPLY_FALLBACK = "我脑子空白了，再试一次？"

# 音色：设置项显示名 -> edge-tts voice id
VOICE_OPTIONS = {
    "云希(男·活力)": "zh-CN-YunxiNeural",
    "晓晓(女·温柔)": "zh-CN-XiaoxiaoNeural",
    "云扬(男·专业)": "zh-CN-YunyangNeural",
    "云健(男·沉稳)": "zh-CN-YunjianNeural",
    "晓伊(女·清新)": "zh-CN-XiaoyiNeural",
}
# 未单独设置音色时，按 LoL 解说风格给个默认音色
STYLE_DEFAULT_VOICE = {
    "肥牛": "云希(男·活力)",
    "电竞主播": "云扬(男·专业)",
    "温柔吐槽": "晓晓(女·温柔)",
    "暴躁老哥": "云希(男·活力)",
}

# 可选依赖容错导入 ----------------------------------------------------------- #
try:
    import edge_tts
    HAVE_EDGE_TTS = True
except Exception:  # noqa: BLE001
    HAVE_EDGE_TTS = False

try:
    import vosk
    import pyaudio
    HAVE_STT = True
except Exception:  # noqa: BLE001
    HAVE_STT = False

try:
    from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
    HAVE_QTMEDIA = True
except Exception:  # noqa: BLE001
    HAVE_QTMEDIA = False

VOSK_MODEL_NAME = "vosk-model-small-zh-cn-0.22"
VOSK_MODEL_URL = f"https://alphacephei.com/vosk/models/{VOSK_MODEL_NAME}.zip"


def _vosk_model_dir() -> str:
    return os.path.join(settings.configdir, VOSK_MODEL_NAME)


def _ensure_vosk_model(timeout: int = 120) -> str:
    """确保离线中文语音模型存在，缺失则自动下载并解压。返回模型目录。"""
    model_dir = _vosk_model_dir()
    if os.path.isdir(model_dir) and os.listdir(model_dir):
        return model_dir
    os.makedirs(model_dir, exist_ok=True)
    zip_path = os.path.join(tempfile.gettempdir(), f"{VOSK_MODEL_NAME}.zip")
    logger.info("下载 Vosk 模型: %s", VOSK_MODEL_URL)
    urllib.request.urlretrieve(VOSK_MODEL_URL, zip_path)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(settings.configdir)
    if not (os.path.isdir(model_dir) and os.listdir(model_dir)):
        raise RuntimeError("模型解压后为空")
    try:
        os.remove(zip_path)
    except Exception:  # noqa: BLE001
        pass
    return model_dir


# --------------------------------------------------------------------------- #
# Worker：Ollama 对话（后台线程，避免卡 UI）
# --------------------------------------------------------------------------- #
class ChatWorker(QThread):
    reply_ready = Signal(str)
    error = Signal(str)

    def __init__(self, caster: Caster, text: str, history):
        super().__init__()
        self.caster = caster
        self.text = text
        self.history = history  # list of (user, assistant)

    def run(self):
        try:
            messages = [{"role": "system", "content": COMPANION_PROMPT}]
            for u, a in self.history[-10:]:
                messages.append({"role": "user", "content": u})
                messages.append({"role": "assistant", "content": a})
            messages.append({"role": "user", "content": self.text})
            raw = self.caster._post(messages, num_predict=160, raise_on_error=True)
            reply = sanitize_commentary(raw)
            if not reply:
                reply = EMPTY_REPLY_FALLBACK
            self.reply_ready.emit(reply)
        except RuntimeError as e:
            logger.warning("chat worker runtime error: %s", e)
            self.error.emit(str(e))
        except Exception as e:  # noqa: BLE001
            logger.exception("chat worker failed")
            self.error.emit(f"对话出错：{e}")


# --------------------------------------------------------------------------- #
# Worker：TTS 生成 mp3（后台线程，生成后把路径抛回主线程播放）
# --------------------------------------------------------------------------- #
class TTSWorker(QThread):
    finished_mp3 = Signal(str)
    error = Signal(str)

    def __init__(self, text: str, voice: str):
        super().__init__()
        self.text = text
        self.voice = voice

    def run(self):
        try:
            mp3 = os.path.join(tempfile.gettempdir(), "dyberpet_tts.mp3")
            asyncio.run(self._save(self.text, self.voice, mp3))
            self.finished_mp3.emit(mp3)
        except Exception as e:  # noqa: BLE001
            logger.exception("tts failed")
            self.error.emit(str(e))

    @staticmethod
    async def _save(text, voice, mp3):
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(mp3)


# --------------------------------------------------------------------------- #
# Worker：STT 按住说话（录音 -> vosk 识别 -> 文本）
# --------------------------------------------------------------------------- #
class STTWorker(QThread):
    text_ready = Signal(str)
    error = Signal(str)

    def __init__(self, model_path: str):
        super().__init__()
        self.model_path = model_path
        self._run = True

    def stop(self):
        self._run = False

    def run(self):
        try:
            model = vosk.Model(self.model_path)
            rec = vosk.KaldiRecognizer(model, 16000)
            p = pyaudio.PyAudio()
            stream = p.open(format=pyaudio.paInt16, channels=1, rate=16000,
                            input=True, frames_per_buffer=8000)
            stream.start_stream()
            while self._run:
                data = stream.read(4000, exception_on_overflow=False)
                rec.AcceptWaveform(data)
            res = json.loads(rec.FinalResult())
            text = res.get("text", "").strip()
            stream.stop_stream()
            stream.close()
            p.terminate()
            self.text_ready.emit(text)
        except Exception as e:  # noqa: BLE001
            logger.exception("stt failed")
            self.error.emit(str(e))


# --------------------------------------------------------------------------- #
# 聊天窗口
# --------------------------------------------------------------------------- #
class PetChatWindow(QWidget):
    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        self.setWindowTitle("和 %s 聊天" % settings.default_pet)
        self.resize(380, 500)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

        self.history = QListWidget()
        self.history.setWordWrap(True)

        self.input = QLineEdit()
        self.input.setPlaceholderText("说点什么…（回车发送）")
        self.input.returnPressed.connect(self._on_send)

        self.send_btn = QPushButton("发送")
        self.send_btn.clicked.connect(self._on_send)

        self.voice_btn = QPushButton("🎤 按住说话")
        self.voice_btn.setCheckable(False)
        self.voice_btn.pressed.connect(self._on_voice_press)
        self.voice_btn.released.connect(self._on_voice_release)

        self.status = QLabel("")
        self.status.setStyleSheet("color: #888;")

        input_row = QHBoxLayout()
        input_row.addWidget(self.input, 3)
        input_row.addWidget(self.send_btn, 1)
        input_row.addWidget(self.voice_btn, 1)

        layout = QVBoxLayout(self)
        layout.addWidget(self.history, 1)
        layout.addLayout(input_row)
        layout.addWidget(self.status)

        self.append_message(settings.default_pet,
                            "嗨～我是你的桌宠，想聊点啥都行～")

    def append_message(self, who, text):
        self.history.addItem("%s：%s" % (who, text))
        self.history.scrollToBottom()

    def on_reply(self, text):
        self.append_message(settings.default_pet, text)

    def set_sending(self, sending: bool):
        self.send_btn.setEnabled(not sending)
        self.input.setEnabled(not sending)
        if sending:
            self.status.setText("思考中…")
        else:
            self.status.setText("")

    def _on_send(self):
        text = self.input.text().strip()
        if not text:
            return
        self.append_message("你", text)
        self.input.clear()
        self.set_sending(True)
        self.manager.send(text)

    def _on_voice_press(self):
        if not settings.chat_stt:
            self.status.setText("语音输入未开启（去设置里打开）")
            return
        self.manager.start_stt()
        self.status.setText("聆听中…说完松手")

    def _on_voice_release(self):
        self.manager.stop_stt()


# --------------------------------------------------------------------------- #
# 管理器：串起窗口 / Ollama / TTS / STT，并把回复投给宠物气泡
# --------------------------------------------------------------------------- #
class ChatManager(QObject):
    sig_reply = Signal(str)
    sig_react = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.caster = Caster(model=settings.chat_model or settings.lol_companion_model)
        self.window = PetChatWindow(self)
        self.history = []  # (user, assistant)
        self._chat_thread = None
        self._stt = None
        self._tts = None

        self.player = None
        self.audio_out = None
        if HAVE_QTMEDIA:
            try:
                self.player = QMediaPlayer()
                self.audio_out = QAudioOutput()
                self.player.setAudioOutput(self.audio_out)
            except Exception:  # noqa: BLE001
                self.player = None

    # ---- 窗口 ----
    def open_window(self):
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        # 打开窗口时异步检查 Ollama 状态，给用户即时反馈
        self._check_ollama_async()

    def _check_ollama_async(self):
        class CheckThread(QThread):
            result = Signal(str)
            def __init__(self, caster):
                super().__init__()
                self.caster = caster
            def run(self):
                self.result.emit(self.caster._check_ollama())
        self._check_thread = CheckThread(self.caster)
        self._check_thread.result.connect(self._on_ollama_status)
        self._check_thread.start()

    def _on_ollama_status(self, err: str):
        if err:
            self.window.status.setText("❌ %s" % err)
            self.window.append_message(settings.default_pet,
                                       "我脑子暂时不在线～%s" % err)
        else:
            self.window.status.setText("✅ Ollama 就绪")

    # ---- 对话 ----
    def send(self, text):
        self._last_user = text
        if self._chat_thread is not None and self._chat_thread.isRunning():
            return
        # 先发制人检查 Ollama，避免空跑一轮再报错
        err = self.caster._check_ollama()
        if err:
            self._on_error(err)
            return
        self.window.set_sending(True)
        self._chat_thread = ChatWorker(self.caster, text, self.history)
        self._chat_thread.reply_ready.connect(self._on_reply)
        self._chat_thread.error.connect(self._on_error)
        self._chat_thread.start()

    def _on_reply(self, reply):
        # 兜底回复只是占位提示，不写进多轮历史，否则模型会把它当成自己说过的话继续接龙，
        # 容易出现“把系统提示词吐出来”之类的奇怪现象。
        if reply != EMPTY_REPLY_FALLBACK:
            self.history.append((self._last_user or "", reply))
        self.window.on_reply(reply)
        self.window.set_sending(False)
        self.sig_reply.emit(reply)
        if settings.chat_tts and reply != EMPTY_REPLY_FALLBACK:
            self._speak(reply)

    def _on_error(self, err):
        err = str(err)
        self.window.status.setText("❌ %s" % err)
        # 根据错误类型给出可操作的回复提示
        if "Ollama" in err or "ollama" in err:
            self.window.append_message(settings.default_pet,
                                       "我脑子连不上啦～%s" % err)
        else:
            self.window.append_message(settings.default_pet,
                                       "出错了：%s" % err)
        self.window.set_sending(False)

    # ---- TTS ----
    def _current_voice(self):
        v = settings.chat_voice
        if v in VOICE_OPTIONS:
            return VOICE_OPTIONS[v]
        style = settings.lol_companion_style
        default = STYLE_DEFAULT_VOICE.get(style, "云希(男·活力)")
        return VOICE_OPTIONS.get(default, "zh-CN-YunxiNeural")

    def _speak(self, text):
        if not HAVE_EDGE_TTS:
            self.window.status.setText("edge-tts 未安装，无法语音播报")
            return
        voice = self._current_voice()
        self._tts = TTSWorker(text, voice)
        self._tts.finished_mp3.connect(self._play)
        self._tts.error.connect(lambda e: self.window.status.setText("语音生成失败：%s" % e))
        self._tts.start()

    def _play(self, path):
        if self.player is None:
            self.window.status.setText("当前环境不支持播放（缺 QtMultimedia）")
            return
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()

    # ---- STT ----
    def start_stt(self):
        if not HAVE_STT:
            self.window.status.setText("未安装 vosk/pyaudio，无法语音输入")
            return
        if self._stt is not None and self._stt.isRunning():
            return
        try:
            model_path = _ensure_vosk_model()
        except Exception as e:  # noqa: BLE001
            self.window.status.setText(
                "语音模型下载失败（可能网络受限）。请手动下载 %s 解压到 %s"
                % (VOSK_MODEL_URL, settings.configdir))
            logger.warning("vosk model fetch failed: %s", e)
            return
        self._stt = STTWorker(model_path)
        self._stt.text_ready.connect(self._on_stt_text)
        self._stt.error.connect(lambda e: self.window.status.setText("语音识别错误：%s" % e))
        self._stt.start()

    def stop_stt(self):
        if self._stt is not None:
            self._stt.stop()

    def _on_stt_text(self, text):
        if text:
            self.window.input.setText(text)
            self.window._on_send()
        else:
            self.window.status.setText("没听清，再试一次")
