# coding:utf-8
"""追番提醒守护（主程序级）：每日更新提醒调度。

历史沿革：原为 bangumi 插件入口；v1.1 收编为主程序守护——追番导航升级为
角色面板一等页后，提醒调度与面板 UI 都不再属于插件系统（与修仙世界
world_daemon 收编同一逻辑）。

调度铁律（绝不骚扰）：
- 每 30 分钟巡检一次（QTimer 主线程触发，网络部分丢工作线程）；
- 只在「过了 remind_hour 且今天还没提醒过」时真正干活；
- 提醒 = 模板气泡（立即、确定性）+ 修仙人设 quip（异步追补、失败静默）
  + 通知中心一条（被动可查，不弹窗不红点）；
- 桌宠启动后 20 秒补一轮（开机晚了也能补上当天提醒；已提醒会被去重拦住）。
"""
from __future__ import annotations

import datetime as _dt
import os
import threading

from PySide6.QtCore import QObject, QTimer, Signal

import DyberPet.settings as app_settings
from DyberPet.bangumi import bili_client
from DyberPet.bangumi.bgm_calendar import CalendarStore
from DyberPet.bangumi.notifier import UpdateChecker
from DyberPet.bangumi.subscription import SubscriptionStore, sub_key

_DATA_DIR = os.path.join(app_settings.CONFIGDIR, 'data', 'bangumi')
_BILI_INTERVAL = 25 * 60        # B站季详情每部 25 分钟最多拉一次


class BangumiDaemon(QObject):
    """追番提醒守护：数据目录注入式构造（测试可隔离），单例 get_daemon。"""

    reminderReady = Signal(str, str)   # 气泡文本, 通知文本（工作线程→主线程）
    quipReady = Signal(str)            # 人设润色文本（后到，补一句）

    def __init__(self, pet_widget, data_dir: str | None = None):
        super().__init__()
        self.pet = pet_widget
        self._dir = data_dir or _DATA_DIR
        os.makedirs(self._dir, exist_ok=True)
        self.store = SubscriptionStore(
            os.path.join(self._dir, "subscriptions.json"),
            os.path.join(self._dir, "assets", "covers"))
        self.calendar = CalendarStore(
            os.path.join(self._dir, "calendar_cache.json"))
        self.checker = UpdateChecker(
            os.path.join(self._dir, "notified.json"))
        self._timer: QTimer | None = None
        self._quip_pending = False
        self.last_bili_error: str = ""      # B站刷新最近失败原因（UI 诊断）
        self.reminderReady.connect(self._on_reminder)
        self.quipReady.connect(self._on_quip)

    # ---- 生命周期 ----
    def start(self):
        if self._timer is not None:
            return
        self._timer = QTimer(self)
        self._timer.setInterval(30 * 60 * 1000)
        self._timer.timeout.connect(self.tick)
        self._timer.start()
        QTimer.singleShot(20_000, self.tick)

    def stop(self):
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    # ---- B站订阅刷新（真实 pub_time，工作线程内调用）----
    def refresh_bili(self, force: bool = False) -> None:
        """刷新全部 B站订阅的播出状态（写 watch.last_air_episode/today_eps）。
        每部 25 分钟节流；force=True（页面手动刷新）绕过节流。"""
        now = _dt.datetime.now().timestamp()
        changed = False
        for sub in self.store.list_all():
            if sub.get("source") != "bili":
                continue
            w = sub.setdefault("watch", {})
            last = float(w.get("last_bili_check", 0) or 0)
            if not force and now - last < _BILI_INTERVAL:
                continue
            st = bili_client.fetch_season_update(sub)
            w["last_bili_check"] = now
            changed = True
            if st is None:
                self.last_bili_error = bili_client.LAST_ERROR or "B站数据拉取失败"
            else:
                self.last_bili_error = ""
        if changed:
            self.store.save()

    # ---- 巡检 ----
    def tick(self):
        if not getattr(app_settings, 'bangumi_notify', True):
            return
        threading.Thread(target=self._tick_worker, daemon=True).start()

    def _tick_worker(self):
        try:
            now = _dt.datetime.now()
            today = now.date()
            remind_hour = int(getattr(app_settings, 'bangumi_remind_hour', 20))
            if now.hour < remind_hour:
                return
            if self.checker.already_notified_today(today):
                return

            calendar, stale = self.calendar.refresh()
            self.refresh_bili()
            subs = self.store.list_all()
            updates = self.checker.check(
                subs, today,
                str(getattr(app_settings, 'bangumi_timezone', '大陆时间')),
                bangumi_stale=stale)
            self.store.save()          # last_air_episode 回写落盘
            if not updates:
                self.checker.mark_notified([], today)
                return

            merge = bool(getattr(app_settings, 'bangumi_merge_notify', True))
            text = self.checker.build_text(updates, merge)
            note = text if text.startswith("今日更新") else f"追番导航：{text}"
            self.checker.mark_notified(updates, today)
            self.reminderReady.emit(text, note)

            if getattr(app_settings, 'bangumi_persona_quip', True):
                self._quip_pending = True
                UpdateChecker.request_quip(
                    updates, lambda t: self.quipReady.emit(t))
        except Exception as e:  # noqa: BLE001
            print(f"[bangumi] tick failed: {e!r}")

    def _on_reminder(self, bubble: str, note: str):
        if self.pet is None:
            return
        try:
            self.pet.show_speech(bubble)
            self.pet.register_notification('plugin', note)
        except Exception as e:  # noqa: BLE001
            print(f"[bangumi] reminder failed: {e!r}")

    def _on_quip(self, text: str):
        if not (text and self._quip_pending):
            return
        self._quip_pending = False
        if self.pet is None:
            return
        try:
            self.pet.show_speech(text)
        except Exception as e:  # noqa: BLE001
            print(f"[bangumi] quip failed: {e!r}")


_DAEMON: BangumiDaemon | None = None


def start_daemon(pet_widget) -> BangumiDaemon:
    """启动全局追番守护（已启动则复用）。"""
    global _DAEMON
    if _DAEMON is None:
        _DAEMON = BangumiDaemon(pet_widget)
    _DAEMON.start()
    return _DAEMON


def get_daemon() -> BangumiDaemon | None:
    return _DAEMON


def stop_daemon():
    global _DAEMON
    if _DAEMON is not None:
        _DAEMON.stop()
        _DAEMON = None
