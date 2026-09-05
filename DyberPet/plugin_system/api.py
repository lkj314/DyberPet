"""给插件的干净门面 PetAPI：pet / events / settings / app。

插件不直接 import 内部模块，只通过这个对象与宿主交互。
"""
import os
import sys
import subprocess
import uuid
import tempfile
import asyncio
import datetime

from PySide6.QtCore import QThread, Signal, QUrl, QTimer, QObject
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput

import DyberPet.settings as settings

# 中文显示名 -> edge-tts voice id（与 pet_chat.VOICE_OPTIONS 保持一致）
_VOICE_MAP = {
    "云希(男·活力)": "zh-CN-YunxiNeural",
    "晓晓(女·温柔)": "zh-CN-XiaoxiaoNeural",
    "云扬(男·专业)": "zh-CN-YunyangNeural",
    "云健(男·沉稳)": "zh-CN-YunjianNeural",
    "晓伊(女·清新)": "zh-CN-XiaoyiNeural",
}


class _TTSWorker(QThread):
    """后台生成 TTS 音频（edge-tts），完成后回传 mp3 路径。"""
    finished = Signal(str)
    error = Signal(str)  # 携带原始文本，供离线兜底使用

    def __init__(self, text, voice, path):
        super().__init__()
        self.text = text
        self.voice = voice
        self.path = path

    def run(self):
        try:
            import edge_tts
            # 加超时，避免联网慢/被墙时长时间卡住；超时或失败都走离线兜底
            asyncio.run(asyncio.wait_for(
                edge_tts.Communicate(self.text, self.voice).save(self.path),
                timeout=8))
            if not os.path.exists(self.path) or os.path.getsize(self.path) == 0:
                raise RuntimeError("edge-tts 未生成有效音频")
            self.finished.emit(self.path)
        except Exception as e:  # noqa: BLE001
            print(f"[TTS] edge-tts 合成失败，转离线语音: {e!r}", file=sys.stderr)
            self.error.emit(self.text)


def _resolve_voice(name):
    return _VOICE_MAP.get(name, _VOICE_MAP.get("云希(男·活力)"))


class _PetFacade(QObject):
    """桌宠能力门面。

    ⚠️ 必须是 QObject：TTS 工作线程的 finished/error 信号连接到本对象的方法，
    只有 QObject 才有线程亲和性，Qt 才会把回调**队列回主线程**执行。
    若是普通 Python 对象，连接退化为直连——回调在工作线程跑，QMediaPlayer
    在没有事件循环的线程里创建，媒体永远不加载，播放静默失败（无任何报错）。
    """
    def __init__(self, widget):
        super().__init__()
        self._w = widget

    def say(self, text: str):
        if text:
            self._w.show_speech(str(text))

    def react(self, emotion: str):
        self._w.react(str(emotion))

    def notify(self, note_type, message):
        self._w.register_notification(note_type, message)

    def bubble(self, bubble_dict: dict):
        self._w.register_bubbleText(bubble_dict)

    def use_item(self, item_name: str):
        self._w.use_item(item_name)

    def add_menu(self, action):
        if hasattr(self._w, 'addContextMenuAction'):
            self._w.addContextMenuAction(action)

    def play_act(self, act_name: str):
        """触发桌宠播放指定动作（动作不存在于当前角色时静默跳过）。"""
        try:
            self._w._show_act(str(act_name))
        except Exception as e:  # noqa: BLE001
            print(f'[api] play_act({act_name!r}) skipped: {e!r}')

    def add_coins(self, n: int):
        """加/扣灵石（商店通用货币）。

        经主程序 addCoins 信号链：pet_data 记账 + 背包/商店 UI 刷新 + 掉落动画。
        注意 0 会被背包当成"随机掉落"信号，这里直接拦截。
        """
        n = int(n)
        if n == 0:
            return
        try:
            self._w.addCoins.emit(n)
        except Exception as e:  # noqa: BLE001
            print(f'[api] add_coins({n}) failed: {e!r}')

    def add_item(self, n: int, names=None):
        """物品入背包（炼丹产出等；names 为空则按掉落表随机）。"""
        try:
            self._w.add_item(int(n), list(names or []))
        except Exception as e:  # noqa: BLE001
            print(f'[api] add_item failed: {e!r}')

    def get_position(self):
        """桌宠窗口位置与尺寸 (x, y, w, h)——供插件做跟随小窗。"""
        try:
            p = self._w.pos()
            return (p.x(), p.y(), self._w.width(), self._w.height())
        except Exception:  # noqa: BLE001
            return (0, 0, 100, 100)

    def open_cultivation(self):
        """打开角色面板的「修仙之路」页（修为条点击/插件 launch 入口）。"""
        try:
            self._w.show_culti_page.emit()
        except Exception as e:  # noqa: BLE001
            print(f'[api] open_cultivation failed: {e!r}')

    def open_adventure(self):
        """打开角色面板的「历练」页（道韵/传讯符点击入口）。"""
        try:
            self._w.show_adventure_page.emit()
        except Exception as e:  # noqa: BLE001
            print(f'[api] open_adventure failed: {e!r}')

    def hide_pet(self):
        """隐藏桌宠本体（外出历练离场）。异常绝不吞进程，仅打印。"""
        try:
            self._w.hide()
        except Exception as e:  # noqa: BLE001
            print(f'[api] hide_pet failed: {e!r}')

    def show_pet(self):
        """重新显示桌宠本体（历练归来/插件禁用兜底）。"""
        try:
            self._w.show()
        except Exception as e:  # noqa: BLE001
            print(f'[api] show_pet failed: {e!r}')

    @staticmethod
    def _tts_log(msg):
        """TTS 诊断日志：写 %TEMP%\\dyberpet_tts.log（windowed EXE 里 stderr 不可见）。"""
        try:
            with open(os.path.join(tempfile.gettempdir(), "dyberpet_tts.log"),
                      "a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now():%m-%d %H:%M:%S}] {msg}\n")
        except Exception:  # noqa: BLE001
            pass

    def speak(self, text, voice=None):
        """语音播报：优先 edge-tts（联网、自然），失败/不可用时转 Windows 离线 SAPI。
        离线兜底不依赖网络与 QMediaPlayer，保证一定有声音。"""
        if not text:
            return
        self._tts_log(f"speak: {text!r}")
        try:
            import edge_tts  # noqa: F401
        except Exception as e:  # noqa: BLE001
            self._tts_log(f"edge_tts import failed -> offline: {e!r}")
            self._speak_offline(text)
            return
        if voice is None:
            voice = getattr(settings, 'chat_voice', None) or "云希(男·活力)"
        voice = _resolve_voice(voice)
        path = os.path.join(
            tempfile.gettempdir(), f"dyberpet_tts_{uuid.uuid4().hex}.mp3")
        self._tts_last_text = text
        w = _TTSWorker(text, voice, path)
        w.finished.connect(self._on_tts_ready)
        # 必须连绑定方法（self 是 QObject）：lambda 无线程亲和会直连在工作线程执行
        w.error.connect(self._on_tts_error)
        w.start()
        self._tts_workers = getattr(self, '_tts_workers', [])
        self._tts_workers.append(w)

    def _on_tts_error(self, text):
        self._tts_log("edge-tts synthesize failed -> offline fallback")
        self._speak_offline(text)

    def _ensure_player(self):
        """懒创建并**常驻复用**播放器（照抄 pet_chat.ChatManager 的可用模式）。

        ⚠️ 血泪教训：QMediaPlayer 与 QAudioOutput 都必须作为成员长期持有！
        QAudioOutput 若是临时对象（无 Python 引用）会被 GC 回收，音频输出随之
        销毁 → 播放**静默无声、零报错**（这正是插件 TTS 一直没声音的根因，
        也是 chat 能出声而插件不能的唯一差异）。必须在主线程创建/使用。
        """
        if getattr(self, '_player', None) is None:
            self._player = QMediaPlayer()
            self._audio_out = QAudioOutput()
            self._player.setAudioOutput(self._audio_out)
        return self._player

    def _on_tts_ready(self, path):
        try:
            # 顶掉上一条的临时 mp3
            old = getattr(self, '_tts_path', None)
            if old and old != path:
                try:
                    os.remove(old)
                except Exception:  # noqa: BLE001
                    pass
            self._tts_path = path
            player = self._ensure_player()
            player.stop()
            player.setSource(QUrl.fromLocalFile(path))
            player.play()
            self._tts_log(f"play: {os.path.basename(path)} "
                          f"({os.path.getsize(path)} bytes)")
        except Exception as e:  # noqa: BLE001
            self._tts_log(f"play failed: {e!r} -> offline fallback")
            self._speak_offline(getattr(self, '_tts_last_text', '') or '')

    def play_audio(self, path):
        """播放本地音频文件（插件预合成语音等）。文件缺失/异常时静默跳过。

        复用常驻播放器（_ensure_player），与 TTS 互相顶掉——
        短促牌型音效打断长语音是符合直觉的行为。
        """
        if not path or not os.path.isfile(path):
            self._tts_log(f"play_audio skip (missing): {path}")
            return
        try:
            player = self._ensure_player()
            player.stop()
            player.setSource(QUrl.fromLocalFile(path))
            player.play()
            self._tts_log(f"play_audio: {os.path.basename(path)}")
        except Exception as e:  # noqa: BLE001
            self._tts_log(f"play_audio failed: {e!r}")

    def _speak_offline(self, text):
        """离线兜底：调用 Windows 自带 SAPI 语音（cscript + SAPI.SpVoice），无需联网。"""
        if not text:
            return
        self._tts_log("offline SAPI fallback")
        try:
            safe = text.replace('"', '""').replace('\r', ' ').replace('\n', ' ')
            vbs = os.path.join(
                tempfile.gettempdir(), f"dyberpet_tts_{uuid.uuid4().hex}.vbs")
            with open(vbs, "w", encoding="utf-8") as f:
                f.write(
                    'Set s = CreateObject("SAPI.SpVoice")\r\n'
                    'On Error Resume Next\r\n'
                    'Set v = s.GetVoices("Language=804")\r\n'  # 804 = zh-CN，优先中文嗓
                    'If v.Count > 0 Then s.Voice = v.Item(0)\r\n'
                    f's.Speak "{safe}"\r\n')
            subprocess.Popen(
                ["cscript", "//nologo", vbs],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=0x08000000,  # CREATE_NO_WINDOW
            )
            # cscript 退出后删除临时脚本（30s 足够播完一句）
            QTimer.singleShot(30000, lambda p=vbs: os.remove(p) if os.path.exists(p) else None)
        except Exception as e:  # noqa: BLE001
            print(f"[TTS] 离线语音也失败: {e!r}", file=sys.stderr)


class _Events:
    """桥接 PetWidget / App 已有信号（更多信号 Phase 2 补全）。"""
    def __init__(self, widget, app):
        self.hp_changed = widget.hp_updated
        self.fv_changed = widget.fv_updated
        self.pet_changed = widget.change_note
        self.midnight = app.date_changed
        # 用户点击/抚摸桌宠（PetWidget.touched，patpat 时 emit）
        self.touched = getattr(widget, 'touched', None)


class _Settings:
    """插件私有的设置子字典（plugins_settings[plugin_id]）。"""
    def __init__(self, plugin_id: str):
        self._pid = plugin_id

    def get(self, key, default=None):
        return settings.plugins_settings.get(self._pid, {}).get(key, default)

    def set(self, key, value, save: bool = True):
        settings.plugins_settings.setdefault(self._pid, {})[key] = value
        if save:
            settings.save_settings()

    def all(self) -> dict:
        return settings.plugins_settings.get(self._pid, {})


class PetAPI:
    def __init__(self, widget, app, plugin_id: str):
        self.pet = _PetFacade(widget)
        self.events = _Events(widget, app)
        self.settings = _Settings(plugin_id)
        self.app = app

    def add_exp(self, amount, reason: str = ''):
        """给桌宠加修为（修仙放置核心服务）。服务异常时静默返回 None。

        这是跨插件联动的唯一入口：五子棋/斗地主等小游戏胜利时调用，
        无插件间依赖（core 是主程序模块，见 cultivation_service.py）。
        """
        try:
            from DyberPet.cultivation_service import add_exp as _add
            return _add(float(amount), reason)
        except Exception as e:  # noqa: BLE001
            print(f'[api] add_exp failed: {e!r}')
            return None

    def add_adventure_buff(self):
        """小游戏胜利 → 历练 buff（成功率 +10%，2 小时）。叙事：与道友对弈，
        心神通明。服务未初始化时自动建单例（不落盘，无副作用）。"""
        try:
            from DyberPet.adventure_service import grant_gaming_buff
            grant_gaming_buff()
        except Exception as e:  # noqa: BLE001
            print(f'[api] add_adventure_buff failed: {e!r}')
