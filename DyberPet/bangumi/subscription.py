# coding:utf-8
"""追番清单 CRUD + 持久化（subscriptions.json）。

结构（v1.2 双源）：
    {version, updated_at, items: [{source, subject_id, name, name_cn,
     air_date, air_weekday, eps_total, desc, cover_url, image_local,
     watch{...}, notify{...}, added_at}]}

- source: 'bangumi'（默认，兼容旧存档缺省）| 'bili'；
- bangumi 条目主键 subject_id=Bangumi 条目 ID，更新靠 air_weekday 推算；
- bili 条目主键 subject_id=B站 season_id（extra 字段 season_id 同值，
  便于阅读），更新靠季详情真实 pub_time（停播免疫）；
- 身份统一用 sub_key(sub) = "source:id" 字符串（两个 ID 空间会撞号）。

写盘全部 tmp+replace 原子替换；读侧无需锁（GIL 下整文件替换读到的是
完整旧版或完整新版）。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import time

_VERSION = 2


def sub_key(sub: dict) -> str:
    return f"{sub.get('source') or 'bangumi'}:{int(sub.get('subject_id') or 0)}"


class SubscriptionStore:
    def __init__(self, path: str, covers_dir: str):
        self.path = path
        self.covers_dir = covers_dir
        self._items: list[dict] = []
        self._load()

    # ---- 持久化 ----
    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            items = list(data.get("items") or [])
            # 旧存档无 source → 补 bangumi；旧 covers 文件名兼容
            for it in items:
                it.setdefault("source", "bangumi")
            self._items = items
        except Exception:  # noqa: BLE001
            self._items = []

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            payload = {"version": _VERSION,
                       "updated_at": int(time.time()),
                       "items": self._items}
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except Exception as e:  # noqa: BLE001
            print(f"[bangumi] subscriptions save failed: {e!r}")

    # ---- 读 ----
    def list_all(self) -> list[dict]:
        """按放送星期排序（周一→周日），同日按番名。返回内部引用，
        调用方改动后需调用 save()（面板/守护各自负责）。"""
        return sorted(self._items,
                      key=lambda s: (int(s.get("air_weekday", 0) or 0),
                                     str(s.get("name_cn") or s.get("name", ""))))

    def get(self, item) -> dict | None:
        """身份查找：str（'source:id'）或 int（旧接口，按 subject_id 兼容匹配）。"""
        if isinstance(item, str):
            src, _, sid = item.partition(":")
            try:
                sid_i = int(sid)
            except ValueError:
                return None
            for it in self._items:
                if (it.get("source") or "bangumi") == src and \
                        int(it.get("subject_id") or 0) == sid_i:
                    return it
            return None
        sid = int(item)
        for it in self._items:
            if int(it.get("subject_id") or 0) == sid:
                return it
        return None

    def cover_path(self, item) -> str:
        sub = self.get(item) if not isinstance(item, dict) else item
        src = (sub or {}).get("source") or "bangumi"
        sid = int((sub or {}).get("subject_id", 0) or 0)
        if src == "bili":
            return os.path.join(self.covers_dir, f"bili_{sid}.jpg")
        return os.path.join(self.covers_dir, f"{sid}.jpg")

    # ---- 写：Bangumi 条目 ----
    def add(self, subject: dict) -> dict | None:
        """从 Bangumi 条目（v0 search 结果或 calendar item）添加追番。

        air_weekday 优先取条目自带值（calendar 最准）；v0 搜索结果没有
        放送星期时由开播日期推算。已存在返回 None（幂等）。"""
        sid = int(subject.get("id") or 0)
        if not sid or self.get(f"bangumi:{sid}") is not None:
            return None
        air_date = str(subject.get("air_date") or subject.get("date") or "")
        air_weekday = int(subject.get("air_weekday", 0) or 0)
        if not air_weekday and air_date:
            try:
                air_weekday = _dt.date.fromisoformat(air_date[:10]).isoweekday()
            except ValueError:
                air_weekday = 0
        images = subject.get("images") or {}
        item = {
            "source": "bangumi",
            "subject_id": sid,
            "name": subject.get("name", ""),
            "name_cn": subject.get("name_cn") or subject.get("name", ""),
            "air_date": air_date,
            "air_weekday": air_weekday,
            "eps_total": int(subject.get("eps") or subject.get("eps_total") or 0),
            "desc": str(subject.get("summary") or ""),
            "image_local": "",          # 首次下载封面后填
            "watch": {"current_episode": 0, "last_air_episode": 0,
                      "manual_offset": 0, "completed": False},
            "notify": {"enabled": True, "is_late_night": False, "remind_hour": None},
            "added_at": int(time.time()),
        }
        if images:
            item["images"] = images
        self._items.append(item)
        self._save()
        return item

    # ---- 写：B站条目（v1.2）----
    def add_bili(self, subject: dict) -> dict | None:
        """从 B站搜索结果/季详情添加。已存在返回 None（幂等）。

        subject 至少含 season_id/name；air_weekday/eps_total/desc/cover_url
        可后补（add 后由 fetch_season_update 回填）。"""
        sid = int(subject.get("season_id") or subject.get("id") or 0)
        if not sid or self.get(f"bili:{sid}") is not None:
            return None
        item = {
            "source": "bili",
            "subject_id": sid,
            "season_id": sid,
            "name": subject.get("name", ""),
            "name_cn": subject.get("name_cn") or subject.get("name", ""),
            "air_date": str(subject.get("air_date") or ""),
            "air_weekday": int(subject.get("air_weekday", 0) or 0),
            "eps_total": int(subject.get("eps_total") or subject.get("eps") or 0),
            "desc": str(subject.get("desc") or ""),
            "cover_url": str(subject.get("cover") or subject.get("cover_url") or ""),
            "image_local": "",
            "watch": {"current_episode": 0, "last_air_episode": 0,
                      "today_eps": [], "completed": False},
            "notify": {"enabled": True},
            "added_at": int(time.time()),
        }
        self._items.append(item)
        self._save()
        return item

    def remove(self, item) -> bool:
        sub = self.get(item)
        if sub is None:
            return False
        self._items.remove(sub)
        self._save()
        return True

    def save(self) -> None:
        self._save()

    # ---- 高频操作（面板按钮）----
    def mark_watched(self, item, episode: int | None = None) -> dict | None:
        """「+1 已看」：current_episode 前进一话；episode 给定则直接设值。
        追平总话数时标记完结。"""
        sub = self.get(item)
        if sub is None:
            return None
        w = sub.setdefault("watch", {})
        cur = int(w.get("current_episode", 0) or 0)
        new_ep = cur + 1 if episode is None else max(int(episode), 0)
        w["current_episode"] = new_ep
        total = int(sub.get("eps_total", 0) or 0)
        w["completed"] = bool(total and new_ep >= total)
        self._save()
        return sub

    def calibrate(self, item, actual_episode: int,
                  today: _dt.date) -> dict | None:
        """停播修正（Bangumi 条目）：输入实际已播出话数 → 反推 manual_offset。"""
        from .episode import suggest_offset
        sub = self.get(item)
        if sub is None:
            return None
        offset = suggest_offset(sub, int(actual_episode), today)
        sub.setdefault("watch", {})["manual_offset"] = int(offset)
        self._save()
        return sub

    def set_notify(self, item, key: str, value) -> dict | None:
        sub = self.get(item)
        if sub is None:
            return None
        sub.setdefault("notify", {})[key] = value
        self._save()
        return sub

    def set_cover(self, item, rel_path: str) -> None:
        sub = self.get(item)
        if sub is not None:
            sub["image_local"] = rel_path
            self._save()
