# coding:utf-8
"""话数推算 + 放送日判断（纯函数，零依赖，命令行可测）。

核心难点（设计文档 §4.2/§4.3）：
- 日番经常停播一周（体育赛事/节假日/制作延期），纯"开播周数"推算会偏大
  → 手动修正为主：用户校准"实际看到第 N 话"反推 manual_offset；
- 深夜番跨天：日本 24:00~05:00 档（占比很高）对应大陆**前一天**晚上，
  不处理会提醒晚一天 → is_late_night 标记，提醒提前一天。
"""
from __future__ import annotations

import datetime as _dt


def calc_air_episode(sub: dict, today: _dt.date) -> int:
    """按开播日期推算今天应已播出的最新话数（含手动修正，封顶总话数）。

    sub 需要: air_date(ISO str), watch.manual_offset(int)，可选 eps_total(int)。
    """
    try:
        air_date = _dt.date.fromisoformat(str(sub.get("air_date", ""))[:10])
    except ValueError:
        return 0
    if today < air_date:
        return 0
    weeks = (today - air_date).days // 7
    offset = int((sub.get("watch") or {}).get("manual_offset", 0) or 0)
    ep = weeks + 1 + offset
    eps_total = sub.get("eps_total") or 0
    if eps_total:
        ep = min(ep, int(eps_total))
    return max(ep, 0)


def should_notify_today(sub: dict, today: _dt.date,
                        timezone_mode: str = "大陆时间") -> bool:
    """今天（大陆时间）是否该提醒这部番的更新。

    - 默认按 air_weekday（日本播出星期 1~7）与今天星期比对；
    - 夜间番（is_late_night）且时区模式为大陆时间：日本当天深夜 ≈ 大陆
      前一天晚上 → 期望星期 = 明天（air_weekday == today+1）。
    """
    air_weekday = int(sub.get("air_weekday", 0) or 0)
    if not 1 <= air_weekday <= 7:
        return False
    today_weekday = today.isoweekday()          # 1=周一 ... 7=周日
    notify = sub.get("notify") or {}
    if notify.get("is_late_night") and timezone_mode != "日本时间":
        expected = (today_weekday % 7) + 1      # 今天+1天 = 日本播出日
    else:
        expected = today_weekday
    return air_weekday == expected


def suggest_offset(sub: dict, actual_episode: int, today: _dt.date) -> int:
    """校准：用户输入实际已播出第 N 话 → 反推 manual_offset。"""
    try:
        air_date = _dt.date.fromisoformat(str(sub.get("air_date", ""))[:10])
    except ValueError:
        return 0
    if today < air_date:
        return int(actual_episode)
    weeks = (today - air_date).days // 7
    return int(actual_episode) - (weeks + 1)


def weekday_cn(weekday_id: int) -> str:
    """1~7 -> 周一~周日；越界返回空串。"""
    names = {1: "周一", 2: "周二", 3: "周三", 4: "周四",
             5: "周五", 6: "周六", 7: "周日"}
    return names.get(int(weekday_id or 0), "")
