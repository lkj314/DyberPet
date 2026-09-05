# coding:utf-8
"""Bilibili 番剧数据源（v1.2 新增，主数据源）。

背景：bgm.tv 在部分国内网络被拦截/超时，B站 api.bilibili.com 国内直连
稳定——且 episodes[].pub_time 是**真实发布时间戳**，比 Bangumi 的
"开播日期推算"更准（停播/.delay 天然免疫）。

端点（沙箱 2026-09-06 实测均 code:0，无需登录）：
- POST级搜索  /x/web-interface/search/type?search_type=media_bangumi
    需 wbi 签名（w_rid/wts）+ buvid3 cookie，否则被风控返回 HTML；
- 季详情     /pgc/view/web/season?season_id=
    无需签名；episodes[].pub_time 为 Unix 秒，title 为话数数字串。

礼貌约束：搜索只在用户主动触发；季详情每部每 25 分钟最多一次（守护节流）；
封面一次下载永久缓存。
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request

BASE = "https://api.bilibili.com"
REFERER = "https://www.bilibili.com/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
_TIMEOUT = 8
_WBI_TTL = 2 * 3600          # wbi 密钥缓存 2h
_BUVID_TTL = 24 * 3600

# 最近一次请求失败的可读原因（诊断用，成功后清空）
LAST_ERROR: str = ""

# wbi 混淆表（bilibili-API-collect 公开常量，实测可用）
_WBI_TAB = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
            27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
            37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
            22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52]

_mem = {"buvid": "", "buvid_at": 0.0,
        "mixin": "", "wbi_at": 0.0}


def _opener(direct: bool) -> urllib.request.OpenerDirector:
    handler = urllib.request.ProxyHandler({}) if direct else urllib.request.ProxyHandler()
    return urllib.request.build_opener(handler)


def _http_get(url: str, timeout: int = _TIMEOUT, retries: int = 2):
    """GET → 解析 JSON。失败返回 None，原因写入 LAST_ERROR。"""
    global LAST_ERROR
    last_err: Exception | None = None
    for attempt in range(retries):
        for direct in (False, True):
            try:
                req = urllib.request.Request(url, headers=_headers())
                with _opener(direct).open(req, timeout=timeout) as resp:
                    LAST_ERROR = ""
                    return json.loads(resp.read().decode("utf-8"))
            except Exception as e:  # noqa: BLE001
                last_err = e
        if attempt < retries - 1:
            time.sleep(1.0)
    LAST_ERROR = _describe_error(last_err)
    return None


def _headers() -> dict:
    h = {"User-Agent": UA, "Referer": REFERER}
    if _mem["buvid"]:
        h["Cookie"] = f"buvid3={_mem['buvid']}"
    return h


def _describe_error(err: Exception | None) -> str:
    if err is None:
        return "未知错误"
    detail = str(getattr(err, "reason", None) or err)
    if "timed out" in detail or "timeout" in detail.lower():
        return "连接 api.bilibili.com 超时"
    if "ConnectionRefused" in detail or "connection refused" in detail.lower():
        return "连接被拒绝（检查网络/代理设置）"
    if "getaddrinfo" in detail or "No address" in detail:
        return "域名解析失败（api.bilibili.com）"
    if "SSL" in detail or "CERTIFICATE" in detail.upper():
        return "SSL 证书验证失败"
    code = getattr(err, "code", None)
    if code:
        return f"api.bilibili.com 返回 HTTP {code}"
    return f"请求失败：{detail[:60]}"


# ---------------------------------------------------------------- #
# 凭据：buvid3 cookie + wbi 签名密钥
# ---------------------------------------------------------------- #
def _get_buvid() -> str:
    if _mem["buvid"] and time.time() - _mem["buvid_at"] < _BUVID_TTL:
        return _mem["buvid"]
    data = _http_get(f"{BASE}/x/frontend/finger/spi")
    b3 = ((data or {}).get("data") or {}).get("b_3") or ""
    if b3:
        _mem["buvid"] = str(b3)
        _mem["buvid_at"] = time.time()
    return _mem["buvid"]


def mixin_key() -> str:
    """wbi 混淆密钥（缓存 _WBI_TTL）。失败返回空串。"""
    if _mem["mixin"] and time.time() - _mem["wbi_at"] < _WBI_TTL:
        return _mem["mixin"]
    _get_buvid()
    nav = _http_get(f"{BASE}/x/web-interface/nav")
    img = ((nav or {}).get("data") or {}).get("wbi_img") or {}
    img_key = str(img.get("img_url", "")).rsplit("/", 1)[-1].split(".")[0]
    sub_key = str(img.get("sub_url", "")).rsplit("/", 1)[-1].split(".")[0]
    if not img_key or not sub_key:
        return ""
    _mem["mixin"] = "".join((img_key + sub_key)[i] for i in _WBI_TAB)[:32]
    _mem["wbi_at"] = time.time()
    return _mem["mixin"]


def _signed_url(path: str, params: dict) -> str:
    """wbi 签名 URL。密钥不可用时退回无签名 URL（端点自己报错）。"""
    params = dict(params)
    params["wts"] = int(time.time())
    qs = urllib.parse.urlencode(
        {k: str(v).replace("!", "").replace("'", "").replace("(", "")
            .replace(")", "").replace("*", "").replace("~", "")
         for k, v in sorted(params.items())})
    mk = mixin_key()
    if mk:
        rid = hashlib.md5((qs + mk).encode()).hexdigest()
        return f"{BASE}{path}?{qs}&w_rid={rid}"
    return f"{BASE}{path}?{qs}"


# ---------------------------------------------------------------- #
# 业务：搜索 / 季详情 / 更新计算 / 封面
# ---------------------------------------------------------------- #
def _clean_title(text: str) -> str:
    return re.sub(r"<[^>]+>", "", str(text or "")).strip()


def search_bangumi(keyword: str) -> list:
    """番剧搜索 → 归一化条目列表（供添加 tab）。失败返回 []。"""
    url = _signed_url("/x/web-interface/search/type",
                      {"search_type": "media_bangumi", "keyword": keyword})
    res = _http_get(url)
    if not isinstance(res, dict) or res.get("code") != 0:
        return []
    out = []
    for it in (res.get("data") or {}).get("result") or []:
        if not isinstance(it, dict) or not it.get("season_id"):
            continue
        score = (it.get("media_score") or {}).get("score")
        out.append({
            "source": "bili",
            "season_id": int(it["season_id"]),
            "name": _clean_title(it.get("title")),
            "name_cn": _clean_title(it.get("title")),
            "cover": str(it.get("cover") or "").replace("http://", "https://"),
            "index_show": str(it.get("index_show") or ""),
            "score": f"{float(score):.1f}" if score else "",
            "areas": str(it.get("areas") or ""),
            "desc": str(it.get("desc") or "").strip(),
            "pubtime": int(it.get("pubtime") or 0),
        })
    return out[:12]


def get_season(season_id: int) -> dict | None:
    """季详情 → 归一化 dict。失败 None。"""
    res = _http_get(f"{BASE}/pgc/view/web/season?season_id={int(season_id)}")
    if not isinstance(res, dict) or res.get("code") != 0:
        return None
    r = res.get("result") or {}
    eps = []
    for e in r.get("episodes") or []:
        if not isinstance(e, dict):
            continue
        eps.append({
            "no": _ep_no(e.get("title")),
            "title": str(e.get("title") or ""),
            "long_title": str(e.get("long_title") or ""),
            "pub_time": int(e.get("pub_time") or 0),
        })
    return {
        "season_id": int(r.get("season_id") or season_id),
        "title": _clean_title(r.get("title")),
        "cover": str(r.get("cover") or "").replace("http://", "https://"),
        "desc": str(r.get("desc") or r.get("evaluate") or "").strip(),
        "total": int(r.get("total") or 0) or len(eps),
        "publish": r.get("publish") or {},
        "episodes": eps,
    }


def _ep_no(title) -> int:
    """话数：'26'/'第26话'/'26.5' → int（小数/脏值取整或 0）。"""
    m = re.search(r"\d+", str(title or ""))
    return int(m.group()) if m else 0


def compute_air_status(season: dict, today: _dt.date | None = None) -> dict:
    """从季详情算播出状态：
    latest_ep  已播出最大话数（pub_time <= 现在）
    today_eps  今天（本机日期=大陆时间）新播的话数列表
    air_weekday 最近一集的放送星期（1~7）
    eps_total  总话数
    """
    today = today or _dt.date.today()
    start = _dt.datetime.combine(today, _dt.time.min).timestamp()
    end = start + 86400
    now = time.time() + 300          # 5 分钟宽限（刚上架的集）
    latest, today_eps, last_ts = 0, [], 0
    for i, e in enumerate(season.get("episodes") or [], 1):
        pt = int(e.get("pub_time") or 0)
        if not pt or pt > now:
            continue
        no = int(e.get("no") or 0) or i
        latest = max(latest, no)
        last_ts = max(last_ts, pt)
        if start <= pt < end:
            today_eps.append(no)
    wd = (_dt.datetime.fromtimestamp(last_ts).isoweekday()
          if last_ts else 0)
    return {"latest_ep": latest, "today_eps": today_eps,
            "air_weekday": wd, "eps_total": int(season.get("total") or 0)}


def fetch_season_update(sub: dict) -> dict | None:
    """拉订阅番的季详情并把播出状态写进 watch（守护/页面共用）。
    失败 None（LAST_ERROR 有原因）。"""
    sid = int(sub.get("season_id") or sub.get("subject_id") or 0)
    season = get_season(sid)
    if season is None:
        return None
    st = compute_air_status(season)
    w = sub.setdefault("watch", {})
    if st["latest_ep"] > int(w.get("last_air_episode", 0) or 0):
        w["last_air_episode"] = st["latest_ep"]
    w["today_eps"] = st["today_eps"]
    if st["air_weekday"]:
        sub["air_weekday"] = st["air_weekday"]
    if st["eps_total"] and not int(sub.get("eps_total", 0) or 0):
        sub["eps_total"] = st["eps_total"]
    if season.get("desc"):
        sub["desc"] = season["desc"]
    if season.get("cover"):
        sub["cover_url"] = season["cover"]
    return st


def download(url: str, save_path: str, timeout: int = _TIMEOUT) -> bool:
    """下载封面到本地（hdslb CDN，带 Referer；一次下载永久缓存）。"""
    if url and os.path.isfile(save_path) and os.path.getsize(save_path) > 0:
        return True
    url = str(url or "").replace("http://", "https://")
    if not url.startswith("https://"):
        return False
    for direct in (False, True):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": UA, "Referer": REFERER})
            with _opener(direct).open(req, timeout=timeout) as resp:
                blob = resp.read()
            if blob:
                os.makedirs(os.path.dirname(save_path), exist_ok=True)
                with open(save_path, "wb") as f:
                    f.write(blob)
                return True
        except Exception:  # noqa: BLE001
            continue
    return False
