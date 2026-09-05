# coding:utf-8
"""B站放送时间表（v1.3 新番导视核心数据）。

端点 /pgc/web/timeline?types=&before=7&after=7（需 wbi 签名）返回
前后 7 天共 15 天的每日更新条目：
    date / date_ts / day_of_week(1~7) / episodes[]

episodes 条目：season_id, title, cover, square_cover, pub_index("第N话"),
pub_time("10:00"), pub_ts(Unix秒), published(0/1), delay_reason, rating...

types 实测（2026-09-06）：1=日番（换季空窗时极少）、4=国创向（常年排期）。
本模块合并 1+4 双流，同 (season_id, pub_ts) 去重，按播出时间排序。
缓存 30 分钟；断网退旧缓存并标记 stale。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import time

from . import bili_client

_TTL = 30 * 60               # 缓存 30 分钟
_TYPES = (1, 4)              # 日番 + 国创向（合并）
_WEEK_CN = {1: "周一", 2: "周二", 3: "周三", 4: "周四",
            5: "周五", 6: "周六", 7: "周日"}


def weekday_cn(dow: int) -> str:
    return _WEEK_CN.get(int(dow or 0), "周?")


def _norm_episode(e: dict) -> dict | None:
    """单条 episode 归一化（防御式：字段缺失降级）。"""
    try:
        sid = int(e.get("season_id") or 0)
    except (TypeError, ValueError):
        return None
    if not sid:
        return None
    pub_ts = int(e.get("pub_ts") or 0)
    cover = str(e.get("cover") or e.get("square_cover") or "")
    return {
        "season_id": sid,
        "title": str(e.get("title") or "").strip(),
        "cover": cover.replace("http://", "https://"),
        "square_cover": str(e.get("square_cover") or "").replace("http://", "https://"),
        "pub_index": str(e.get("pub_index") or "").strip(),   # "第12话"
        "pub_time": str(e.get("pub_time") or "").strip(),     # "10:00"
        "pub_ts": pub_ts,
        "published": bool(int(e.get("published") or 0)),
        "delay_reason": str(e.get("delay_reason") or "").strip(),
        "rating": float(e.get("rating") or 0) or None,
        "episode_id": int(e.get("episode_id") or 0),
    }


def _fetch_raw(types: int, before: int = 7, after: int = 7):
    url = bili_client._signed_url(
        "/pgc/web/timeline",
        {"types": types, "before": before, "after": after})
    d = bili_client._http_get(url)
    if not isinstance(d, dict) or d.get("code") != 0:
        return None
    result = d.get("result")
    return result if isinstance(result, list) else None


class TimelineStore:
    """放送时间表：磁盘缓存 + 双流合并。"""

    def __init__(self, cache_path: str):
        self.path = cache_path
        self._mem: dict | None = None
        self.last_error = ""     # 最近一次刷新失败原因（成功后清空）

    # ---- 存储 ----
    def _load(self) -> dict:
        if self._mem is None:
            try:
                with open(self.path, encoding="utf-8") as f:
                    self._mem = json.load(f)
                if not isinstance(self._mem, dict):
                    self._mem = {}
            except (OSError, ValueError):
                self._mem = {}
        return self._mem

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = f"{self.path}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._mem, f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError:
            pass

    # ---- 数据访问 ----
    def days(self) -> list[dict]:
        """缓存的天列表（可能为空）。每项 {date,date_ts,day_of_week,episodes[]}，
        episodes 按 pub_ts 升序。"""
        return self._load().get("days") or []

    def fetched_at(self) -> float:
        return float(self._load().get("fetched_at", 0) or 0)

    def refresh(self, force: bool = False,
                before: int = 7, after: int = 7) -> tuple[list[dict], bool]:
        """刷新时间表。返回 (days, stale)。
        stale=True 表示用了过期缓存（本次拉取失败）。"""
        data = self._load()
        now = time.time()
        if not force and data.get("days") and now - self.fetched_at() < _TTL:
            return data["days"], False

        merged: dict[int, dict] = {}      # date_ts -> day dict
        ok_any = False
        for types in _TYPES:
            raw = _fetch_raw(types, before, after)
            if raw is None:
                continue                  # 一个流失败不否定另一个
            ok_any = True
            for day in raw:
                if not isinstance(day, dict):
                    continue
                try:
                    ts = int(day.get("date_ts") or 0)
                except (TypeError, ValueError):
                    continue
                slot = merged.setdefault(ts, {
                    "date": str(day.get("date") or ""),
                    "date_ts": ts,
                    "day_of_week": int(day.get("day_of_week") or 0),
                    "episodes": [],
                })
                seen = {(ep.get("season_id"), ep.get("pub_ts"))
                        for ep in slot["episodes"]}
                for e in day.get("episodes") or []:
                    if not isinstance(e, dict):
                        continue
                    ep = _norm_episode(e)
                    if ep and (ep["season_id"], ep["pub_ts"]) not in seen:
                        slot["episodes"].append(ep)
                        seen.add((ep["season_id"], ep["pub_ts"]))
                slot["episodes"].sort(key=lambda x: (x.get("pub_ts") or 0,
                                                     x.get("title") or ""))

        if ok_any and merged:
            days = [merged[k] for k in sorted(merged)]
            self._mem = {"fetched_at": now, "days": days}
            self._save()
            self.last_error = ""
            return days, False

        # 全部失败：退旧缓存
        self.last_error = (bili_client.LAST_ERROR
                           or "api.bilibili.com 请求失败")
        old = data.get("days") or []
        if old:
            self.last_error += "（当前展示的是缓存数据，点击刷新重试）"
        return old, True

    # ---- 便捷视图 ----
    def day_by_dow(self, dow: int) -> dict | None:
        """取周几（1~7）的一天：优先未来最近（含今天），全过去则取最近一
        次——看周表时，还没播的比已播的更有参考价值。"""
        dow = int(dow or 0)
        days = [d for d in self.days()
                if int(d.get("day_of_week") or 0) == dow]
        if not days:
            return None
        today_ts = int(_dt.datetime.combine(
            _dt.date.today(), _dt.time.min).timestamp())
        future = [d for d in days if int(d.get("date_ts") or 0) >= today_ts]
        return future[0] if future else days[-1]

    def today(self) -> dict | None:
        return self.day_by_dow(_dt.date.today().isoweekday())

    def all_seasons(self) -> list[dict]:
        """目录视图：窗口内所有番剧按 season_id 去重（保留最新一话信息），
        按最近更新时间降序——即「最近在播的番剧」列表。"""
        out: dict[int, dict] = {}
        for day in self.days():
            for ep in day.get("episodes") or []:
                sid = ep.get("season_id")
                if sid not in out or (ep.get("pub_ts") or 0) > (out[sid].get("pub_ts") or 0):
                    out[sid] = ep
        return sorted(out.values(),
                      key=lambda x: x.get("pub_ts") or 0, reverse=True)
