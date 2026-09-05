# coding:utf-8
"""/calendar 拉取、缓存与今日筛选。

缓存策略（设计文档 §五）：
- 本地缓存 12 小时有效，过期才重新请求（一天最多 1~2 次，社区礼貌）；
- 换季（1/4/7/10 月）缓存视为失效，强制刷新——新番上线，旧数据作废；
- 断网降级：拉取失败时退回旧缓存（stale=True），功能照常可用，只是
  新番不会自动出现。
- 只保留 type==2（动画），三次元/书籍/音乐等一律丢弃。

数据结构以 Bangumi 官方 OpenAPI 规范为准（沙箱内 api.bgm.tv 被网关拦截
无法实跑，字段解析全部做了防御——实际返回与预期不符时按空值降级，
绝不抛异常拖垮插件）。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import time

from . import bgm_client

CACHE_TTL = 12 * 3600          # 12 小时
CALENDAR_URL = f"{bgm_client.BASE}/calendar"


def today_airing(calendar: list, weekday_id: int) -> list:
    """weekday_id: 1=周一 ... 7=周日（与 Python isoweekday() 一致）。"""
    for day in calendar or []:
        wd = ((day or {}).get("weekday") or {}).get("id")
        if wd == weekday_id:
            return [it for it in (day.get("items") or []) if it.get("type") == 2]
    return []


def find_in_calendar(calendar: list, subject_id: int) -> dict | None:
    """全周查找条目（供"搜索添加"补准 air_weekday / air_date / eps）。"""
    for it in today_airing(calendar, 1) + today_airing(calendar, 2) + \
              today_airing(calendar, 3) + today_airing(calendar, 4) + \
              today_airing(calendar, 5) + today_airing(calendar, 6) + \
              today_airing(calendar, 7):
        if it.get("id") == subject_id:
            return it
    return None


class CalendarStore:
    """/calendar 缓存层。线程约定：网络请求放在工作线程调用（main.py 已保证），
    文件读写用 tmp+replace 原子替换。"""

    def __init__(self, cache_path: str):
        self.path = cache_path
        self._mem: dict | None = None
        self.last_error: str = ""     # 最近一次刷新失败的真实原因（UI 诊断展示）

    # ---- 存取 ----
    def _load(self) -> dict:
        if self._mem is not None:
            return self._mem
        try:
            with open(self.path, encoding="utf-8") as f:
                self._mem = json.load(f)
        except Exception:  # noqa: BLE001
            self._mem = {}
        return self._mem

    def _save(self, data: dict) -> None:
        self._mem = data
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception as e:  # noqa: BLE001
            print(f"[bangumi] calendar cache save failed: {e!r}")

    # ---- 刷新 ----
    @staticmethod
    def _season_of(ts: float) -> tuple:
        d = _dt.date.fromtimestamp(ts)
        return (d.year, (d.month - 1) // 3 + 1)      # 1/4/7/10 月换季

    def _needs_refresh(self, data: dict, now: float) -> bool:
        if not data or not data.get("calendar"):
            return True
        fetched = float(data.get("fetched_at", 0) or 0)
        if now - fetched >= CACHE_TTL:
            return True
        return self._season_of(fetched) != self._season_of(now)   # 换季强制刷

    @staticmethod
    def _sanitize(raw: list) -> list:
        """只留 type==2，字段防御（实际返回与规范不符时不炸）。"""
        out = []
        for day in raw or []:
            if not isinstance(day, dict):
                continue
            items = []
            for it in day.get("items") or []:
                if not isinstance(it, dict) or it.get("type") != 2:
                    continue
                items.append({
                    "id": it.get("id"),
                    "url": it.get("url", ""),
                    "name": it.get("name", ""),
                    "name_cn": it.get("name_cn") or it.get("name", ""),
                    "summary": it.get("summary", ""),
                    "air_date": it.get("air_date", ""),
                    "air_weekday": it.get("air_weekday", 0),
                    "eps": it.get("eps", 0),
                    "images": it.get("images") or {},
                })
            out.append({"weekday": day.get("weekday") or {}, "items": items})
        return out

    def refresh(self, force: bool = False) -> tuple[list, bool]:
        """返回 (calendar, stale)。stale=True 表示数据来自过期缓存（断网降级）。
        失败原因写入 self.last_error 供 UI 展示诊断。"""
        now = time.time()
        data = self._load()
        self.last_error = ""
        if not force and not self._needs_refresh(data, now):
            return data.get("calendar") or [], False

        fresh = bgm_client.get_json(CALENDAR_URL)
        if isinstance(fresh, list) and fresh:
            cal = self._sanitize(fresh)
            self._save({"fetched_at": now, "calendar": cal})
            return cal, False
        # 拉取失败：退回旧缓存（可能为空列表——首次离线时无数据可看）
        self.last_error = bgm_client.LAST_ERROR or \
            "api.bgm.tv 请求失败，当前展示的是本地缓存数据"
        return data.get("calendar") or [], True

    def data(self) -> list:
        """当前缓存的整体放送列表（不做网络请求，供 UI 同步读取）。"""
        return self._load().get("calendar") or []

    def fetched_at(self) -> float:
        return float(self._load().get("fetched_at", 0) or 0)
