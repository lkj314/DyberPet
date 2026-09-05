# -*- coding: utf-8 -*-
"""LCU 客户端轮询线程：gameflow 状态机驱动自动化与战报。

职责（每 2s 一 tick，纯状态边沿触发，避免对 gameId 的依赖）：
  - 首次进入 ReadyCheck        → 自动接受对局（开关）
  - 首次进入 PreEndOfGame/EndOfGame → 拉结算 → 战报 emit（开关）
       ├─ 自动点赞同队 KDA 最高的队友（开关）
       └─ 延时数秒后自动返回房间（开关，让用户先看到战报）

所有 LCU 请求异常在 lcu_client 内部吞掉返回 None；本线程再有兜底
try/except——工作线程绝不允许拖崩桌宠。
"""
import threading

from PySide6.QtCore import QThread, Signal

from . import lcu_client
from .battle_report import build_report, save_report

_POLL_SECONDS = 2.0
_PLAY_AGAIN_DELAY = 5.0  # 战报弹出后等几秒再回房


class LcuWorker(QThread):
    report_ready = Signal(dict)   # 战报 dict（含 bubble/notify 文本与 result）
    flow_event = Signal(str)      # gameflow 阶段变化（预留演出/调试）

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        # 直接引用 plugins_settings['lol_companion']，设置即时生效
        self.cfg = cfg
        self._stop_event = threading.Event()
        self._last_phase = None
        self._report_done = False   # 本局战报是否已处理（防重）

    # ---------------- QThread ----------------
    def run(self):
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception:
                pass  # 任何异常都不许杀死循环
            self._stop_event.wait(_POLL_SECONDS)

    def stop(self):
        self._stop_event.set()

    # ---------------- 状态机 ----------------
    def _tick(self):
        phase = lcu_client.lcu_get("/lol-gameflow/v1/gameflow-phase")
        if phase is None:
            # 客户端未开 / 探测冷却中：重置边沿，等下一轮
            self._last_phase = None
            return
        phase = str(phase)
        prev = self._last_phase
        if phase != prev:
            self._last_phase = phase
            try:
                self.flow_event.emit(phase)
            except Exception:
                pass

        # 1) 首次进入 ReadyCheck → 自动接受（POST 幂等，边沿触发省请求）
        if (phase == "ReadyCheck" and prev != "ReadyCheck"
                and self.cfg.get("auto_accept", True)):
            lcu_client.lcu_post("/lol-matchmaking/v1/ready-check/accept")

        # 2) 结算阶段首次出现 → 战报 + 点赞 + 回房
        if phase in ("PreEndOfGame", "EndOfGame"):
            if not self._report_done:
                # 拉不到有效数据（端点未就绪）不置位，下一 tick 重试
                if self._do_end_of_game():
                    self._report_done = True
        else:
            # 离开结算阶段（回大厅/重新排队）→ 重置，为下一局做准备
            if self._report_done and phase not in ("InProgress", "ChampSelect"):
                self._report_done = False

    # ---------------- 结算处理 ----------------
    def _do_end_of_game(self) -> bool:
        eog = lcu_client.lcu_get("/lol-end-of-game/v1/eog-stats-block")
        if not eog or not eog.get("players"):
            return False
        my_name = self._my_summoner_name()
        report = build_report(eog, my_name)
        if not report:
            return False
        save_report(report)                      # 落档 data/lol_reports.json
        try:
            self.report_ready.emit(report)       # → 主线程气泡/通知栏
        except Exception:
            pass
        if self.cfg.get("auto_honor", True):
            self._honor_teammate(report)
        if self.cfg.get("auto_play_again", True):
            # 等几秒让用户看到战报再回房；期间可被打断
            for _ in range(int(_PLAY_AGAIN_DELAY)):
                if self._stop_event.wait(1.0):
                    return True
            lcu_client.lcu_post("/lol-lobby/v2/play-again")
        return True

    def _my_summoner_name(self):
        d = lcu_client.lcu_get("/lol-summoner/v1/current-summoner") or {}
        return d.get("gameName") or d.get("displayName") or d.get("name") or ""

    def _honor_teammate(self, report):
        """给同队（非自己）KDA 最高的队友点赞；失败静默。"""
        try:
            me = report.get("my") or {}
            my_team = me.get("team")
            mates = [r for r in report.get("players", [])
                     if r.get("summoner_id")
                     and not (r.get("name") == me.get("name"))
                     and r.get("team") == my_team]
            if not mates:
                return
            mate = max(mates, key=lambda r: r.get("kda", 0))
            lcu_client.lcu_post(
                "/lol-honor-v2/v1/honor-player",
                {"summonerId": mate["summoner_id"], "honorCategory": "COOL"})
        except Exception:
            pass
