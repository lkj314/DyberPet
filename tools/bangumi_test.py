# coding:utf-8
"""追番导航离线测试（不联网：fixture 数据 + tmp 隔离目录）。

跑法：.venv/Scripts/python.exe tools/bangumi_test.py
覆盖：episode 推算/夜间番/校准、subscription 双源 CRUD/持久化、
calendar 解析/缓存/换季、timeline 双流合并/去重/星期选择、
bili_client 签名/解析（离线 monkeypatch）、notifier 检测/去重/合并、
UI offscreen 冒烟（导视/目录/清单三页）。
"""
import datetime as _dt
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


# ============================================================ #
# timeline（时间表合并逻辑，离线 fixture）
# ============================================================ #
def test_timeline(tmp):
    from DyberPet.bangumi.timeline import TimelineStore, weekday_cn

    print("\n== timeline 双流合并 ==")
    raw1 = [  # 日番流
        {"date": "9-6", "date_ts": 1788624000, "day_of_week": 7,
         "episodes": [{"season_id": 100, "title": "日番A", "pub_index": "第5话",
                       "pub_time": "22:00", "pub_ts": 1788650000, "published": 0,
                       "cover": "http://i0.hdslb.com/a.jpg", "rating": 8.5}]},
        {"date": "9-7", "date_ts": 1788710400, "day_of_week": 1, "episodes": []},
    ]
    raw4 = [  # 国创流
        {"date": "9-6", "date_ts": 1788624000, "day_of_week": 7,
         "episodes": [{"season_id": 200, "title": "国创B", "pub_index": "第99话",
                       "pub_time": "11:00", "pub_ts": 1788636000, "published": 1},
                      {"season_id": 100, "title": "日番A", "pub_index": "第5话",
                       "pub_time": "22:00", "pub_ts": 1788650000, "published": 0}]},
    ]
    store = TimelineStore(os.path.join(tmp, "timeline.json"))
    calls = []

    def fake_fetch(types, before=7, after=7):
        calls.append(types)
        return {1: raw1, 4: raw4}.get(types)

    import DyberPet.bangumi.timeline as tl_mod
    orig = tl_mod._fetch_raw
    tl_mod._fetch_raw = staticmethod(fake_fetch)
    try:
        days, stale = store.refresh()
        check("双流合并无 stale", stale is False and not store.last_error)
        check("合并后天数", len(days) == 2, f"got {len(days)}")
        eps = days[0]["episodes"]
        check("同番跨流去重", len(eps) == 2,
              f"got {[(e['season_id'], e['pub_ts']) for e in eps]}")
        check("按播出时间排序", eps[0]["season_id"] == 200 and eps[1]["season_id"] == 100)
        check("cover 升 https", eps[1]["cover"].startswith("https://"))
        check("rating 保留", eps[1]["rating"] == 8.5)
        check("day_by_dow 命中", store.day_by_dow(7) is not None
              and store.day_by_dow(3) is None)
        check("all_seasons 去重", len(store.all_seasons()) == 2)
        check("weekday_cn", weekday_cn(7) == "周日" and weekday_cn(1) == "周一")
        check("缓存落盘", os.path.exists(store.path))
        # 二次 refresh 走缓存（不再发请求）
        calls.clear()
        days2, stale2 = store.refresh()
        check("30min 内走缓存", not calls and not stale2 and len(days2) == 2)
        # 未来优先的星期选择：9-7 是未来周一
        d1 = store.day_by_dow(1)
        check("星期选择未来优先", d1 and d1["date"] == "9-7")
    finally:
        tl_mod._fetch_raw = orig

    # 全流失败 → 退缓存
    tl_mod._fetch_raw = staticmethod(lambda *a, **k: None)
    try:
        import DyberPet.bangumi as bg
        bg.bili_client.LAST_ERROR = "连接超时"
        days3, stale3 = store.refresh(force=True)
        check("失败退缓存 stale", stale3 and len(days3) == 2)
        check("失败有诊断", "超时" in store.last_error)
    finally:
        tl_mod._fetch_raw = orig


# ============================================================ #
# episode 话数推算
# ============================================================ #
def test_episode():
    from DyberPet.bangumi import episode

    print("\n== episode 话数推算 ==")
    sub = {"air_date": "2026-07-05", "eps_total": 12, "air_weekday": 7}
    today = _dt.date(2026, 9, 6)
    check("按开播日推算", episode.calc_air_episode(sub, today) == 10,
          str(episode.calc_air_episode(sub, today)))

    sub_off = dict(sub, watch={"manual_offset": -2})
    check("停播校准 offset", episode.calc_air_episode(sub_off, today) == 8)

    # 夜间番：日本周一深夜 ≈ 大陆周日晚上 → 今天是周日时提醒 air_weekday=1 的番
    sub_late = {"air_weekday": 1, "notify": {"is_late_night": True}}
    check("夜间番提前一天", episode.should_notify_today(sub_late, today)
          and not episode.should_notify_today({"air_weekday": 1}, today))

    check("weekday_cn", episode.weekday_cn(1) == "周一")


# ============================================================ #
# subscription 双源 CRUD
# ============================================================ #
def test_subscription(tmp):
    from DyberPet.bangumi import subscription

    print("\n== subscription 双源 CRUD ==")
    path = os.path.join(tmp, "subscriptions.json")
    covers = os.path.join(tmp, "covers")
    store = subscription.SubscriptionStore(path, covers)

    b1 = store.add({"id": 464885, "name": "Frieren", "name_cn": "葬送的芙莉莲",
                    "date": "2026-08-30", "eps": 28})
    check("add bangumi 默认 source", b1["source"] == "bangumi")
    check("sub_key 格式", subscription.sub_key(b1) == "bangumi:464885")

    b2 = store.add_bili({"season_id": 102261, "name": "芙莉莲 中配",
                         "cover": "https://i0.hdslb.com/x.png", "desc": "d"})
    check("add_bili 成功", b2 is not None and b2["source"] == "bili")
    check("bili 重复幂等", store.add_bili({"season_id": 102261}) is None)
    check("双源同号不冲突",
          store.get("bangumi:102261") is None and store.get("bili:102261") is b2)

    store.mark_watched("bangumi:464885", 2)
    store.calibrate("bangumi:464885", 1, _dt.date(2026, 9, 6))
    check("mark+calibrate", store.get("bangumi:464885")["watch"]["current_episode"] == 2
          and store.get("bangumi:464885")["watch"]["manual_offset"] == -1)

    store2 = subscription.SubscriptionStore(path, covers)
    check("持久化恢复", store2.get("bangumi:464885") is not None
          and store2.get("bangumi:464885")["watch"]["current_episode"] == 2)

    # 旧档兼容：无 source 的条目自动补 bangumi；int 查找兼容
    raw = json.load(open(path, encoding="utf-8"))
    for s in raw["items"]:
        s.pop("source", None)
    json.dump(raw, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    store3 = subscription.SubscriptionStore(path, covers)
    legacy = store3.get(464885)
    check("旧条目补 source", legacy is not None and legacy.get("source") == "bangumi")
    check("删除（key 形式）", store3.remove("bangumi:464885")
          and store3.get(464885) is None)


# ============================================================ #
# bili_client（离线 monkeypatch）
# ============================================================ #
def test_bili_client(tmp):
    from DyberPet.bangumi import bili_client

    print("\n== bili_client 签名/解析（离线）==")
    url = bili_client._signed_url("/x/web-interface/search/type",
                                  {"keyword": "芙莉莲", "search_type": "media_bangumi"})
    check("签名 URL 含 w_rid", "w_rid=" in url and "wts=" in url)
    import urllib.parse
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
    check("wts 不重复", url.count("wts=") == 1, url)
    check("签名 32 位", len(q.get("w_rid", "")) == 32)


# ============================================================ #
# notifier
# ============================================================ #
def test_notifier(tmp):
    from DyberPet.bangumi import notifier

    print("\n== notifier 检测/去重 ==")
    checker = notifier.UpdateChecker(os.path.join(tmp, "notified.json"))
    today = _dt.date.today()
    sub = {"subject_id": 464885, "air_date": str(today - _dt.timedelta(days=21)),
           "air_weekday": today.isoweekday(),
           "notify": {"enabled": True},
           "watch": {"current_episode": 1, "last_air_episode": 3}}
    updates = checker.check([sub], today)
    check("有新话触发", len(updates) == 1 and updates[0]["episode"] == 4,
          str(updates))
    checker.mark_notified(updates, today)
    updates2 = checker.check([sub], today)
    check("同话去重", len(updates2) == 0)

    sub_off = dict(sub, notify={"enabled": False})
    check("关闭通知跳过", len(checker.check([sub_off], today)) == 0)

    one = [{"sub": {"name_cn": "葬送的芙莉莲"}, "episode": 5}]
    check("单番文案", "芙莉莲" in checker.build_text(one))
    two = one + [{"sub": {"name_cn": "另一部"}, "episode": 2}]
    check("合并文案", checker.build_text(two).startswith("今日更新："))


# ============================================================ #
# calendar（Bangumi 解析，离线 fixture）
# ============================================================ #
def test_calendar(tmp):
    from DyberPet.bangumi import bgm_calendar

    print("\n== calendar 解析/缓存 ==")
    fixture = [
        {"weekday": {"en": "Sat", "id": 6},
         "items": [{"id": 12, "type": 2, "name": "Anime A", "name_cn": "番剧A",
                    "air_date": "2026-07-04", "eps": 12,
                    "images": {"grid": "https://lain.bgm.tv/x.jpg"}},
                   {"id": 13, "type": 1, "name": "Book", "name_cn": "书"}]},
    ]
    p = os.path.join(tmp, "calendar_cache.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"fetched_at": 1.0, "calendar": fixture}, f)
    store = bgm_calendar.CalendarStore(p)
    data = store.data()
    check("只留 type==2", len(data) == 1 and data[0]["items"][0]["id"] == 12)
    check("脏数据不炸", bgm_calendar.CalendarStore(
        os.path.join(tmp, "nonexist.json")).data() == [])


# ============================================================ #
# UI offscreen 冒烟
# ============================================================ #
def test_ui(tmp):
    print("\n== UI offscreen 冒烟 ==")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)

    import DyberPet.bangumi_daemon as daemon_mod
    # 单例重置 + fallback 目录 patch（UI 自建 BangumiDaemon(None) 时用 _DATA_DIR）
    daemon_mod._DAEMON = None
    daemon_mod._DATA_DIR = os.path.join(tmp, "bangumi_fallback")

    from DyberPet.Dashboard.bangumiUI import (bangumiInterface, GuideRow,
                                              CatalogCard, SubRow, SearchRow)
    board = bangumiInterface((750, 600))
    board.store.add({"id": 464885, "name": "Frieren", "name_cn": "葬送的芙莉莲",
                     "date": "2026-08-30", "eps": 28})

    # 灌时间线缓存
    board.timeline._mem = {
        "fetched_at": __import__("time").time(),
        "days": [
            {"date": "9-6", "date_ts": 1788624000, "day_of_week": 7,
             "episodes": [
                 {"season_id": 100, "title": "今日番A", "pub_index": "第5话",
                  "pub_time": "22:00", "pub_ts": 1788650000, "published": 0,
                  "cover": "", "rating": 8.5, "delay_reason": ""},
                 {"season_id": 200, "title": "今日番B", "pub_index": "第9话",
                  "pub_time": "11:00", "pub_ts": 1788636000, "published": 1,
                  "cover": "", "delay_reason": " Upstream delay"},
             ]},
            {"date": "9-7", "date_ts": 1788710400, "day_of_week": 1,
             "episodes": [{"season_id": 300, "title": "周一番C",
                           "pub_index": "第2话", "pub_time": "21:00",
                           "pub_ts": 1788730000, "published": 0, "cover": ""}]},
        ]}
    board._on_refresh(False)

    check("导视默认今天", "9-6" in board.dayTitle.text()
          and "周日" in board.dayTitle.text())
    check("导视行数", board.guideLay.count() == 2)
    check("目录去重数", board.catGrid.count() == 3,
          f"got {board.catGrid.count()}")
    check("清单行数", board.subsLay.count() == 1,
          f"got {board.subsLay.count()}")

    # 切星期 → 周一
    board._select_dow(1)
    check("切星期重建", board.guideLay.count() == 1
          and "周一" in board.dayTitle.text())
    board._select_dow(7)

    # GuideRow / CatalogCard / SubRow 直接构造
    ep = {"season_id": 999, "title": "行测试", "pub_index": "第1话",
          "pub_time": "10:00", "pub_ts": 1788650000, "published": 1,
          "cover": "", "rating": 9.0}
    r1 = GuideRow(board, ep)
    r2 = CatalogCard(board, ep)
    r3 = SubRow(board, board.store.get(464885))
    check("行组件尺寸", r1.height() == 116 and r2.height() == 236
          and r3.height() == 72)

    # 详情卡（先 show 面板，offscreen 下 isVisible 依赖父可见）
    board.resize(750, 900)
    board.show()
    board._show_detail(ep)
    check("详情展开", board.detailCard.isVisible())
    board._on_detail("tl:999", "测试简介内容", "")
    check("详情简介填充", board.d_desc.text() == "测试简介内容")
    board.detailCard.hide()

    # 追番操作
    ok = board.follow_ep(ep)
    check("follow_ep 成功", ok and board.store.get("bili:999") is not None)
    check("follow 后行按钮态", not r1.follow_btn.isEnabled())

    board.mark_watched("bangumi:464885")
    check("mark_watched +1", board.store.get(464885)["watch"]["current_episode"] == 1)

    board.remove_sub("bangumi:464885")
    check("remove_sub", board.store.get(464885) is None)

    # 三页切换 + 渲染
    board._switch("catalog")
    check("切目录页", board.stack.currentIndex() == 1)
    board._switch("subs")
    check("切清单页", board.stack.currentIndex() == 2)
    board._switch("guide")

    board.resize(750, 900)
    board.show()
    board.grab()
    check("整页渲染 grab", not board.grab().isNull())
    board.close()


def main():
    tmp = tempfile.mkdtemp(prefix="bangumi_test_")
    test_timeline(tmp)
    test_episode()
    test_subscription(tmp)
    test_bili_client(tmp)
    test_notifier(tmp)
    test_calendar(tmp)
    test_ui(tmp)
    print(f"\n==== 结果: {PASS} passed, {FAIL} failed ====")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
