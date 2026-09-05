# coding:utf-8
"""Bangumi HTTP 客户端：统一 UA、指数退避重试、代理兜底。

合规铁律（设计文档 §二，不可省略）：
- UA 必须形如 ``{开发者}/{应用}/{版本} (平台) (项目URL)``——Bangumi 明确
  封禁通用 UA（Bangumi/1.0、database 等），违规直接 403；
- 频率克制：/calendar 的拉取节奏由 bgm_calendar 缓存层管（12h 一次），
  封面图下载一次永久缓存（subscription 层管），本模块只管"怎么请求"；
- 失败指数退避重试，上限 3 轮；每轮先尊重系统代理、再绕过代理直连
  （环境变量里的挂掉代理是常见坑，直连兜底保命）。
"""
from __future__ import annotations

import json
import time
import urllib.request

# 规范格式：{开发者ID}/{应用名}/{版本} (平台) (项目地址)
USER_AGENT = ("lkj314/DyberPet/1.0.0 (Windows) "
              "(https://github.com/ChaozhongLiu/DyberPet)")

BASE = "https://api.bgm.tv"
_TIMEOUT = 8

# 最近一次请求失败的可读原因（诊断用，成功后清空）
LAST_ERROR: str = ""


def _opener(direct: bool) -> urllib.request.OpenerDirector:
    """direct=True 绕过环境代理直连；False 走系统默认（含 http_proxy）。"""
    handler = urllib.request.ProxyHandler({}) if direct else urllib.request.ProxyHandler()
    return urllib.request.build_opener(handler)


def get_json(url: str, method: str = "GET", payload: dict | None = None,
             timeout: int = _TIMEOUT, retries: int = 3):
    """请求并解析 JSON。成功返回解析结果；失败返回 None（不抛异常，
    由调用方决定降级策略——离线时用缓存继续跑是本插件的底线）。"""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"

    last_err: Exception | None = None
    delay = 1.0
    global LAST_ERROR
    for attempt in range(retries):
        for direct in (False, True):
            try:
                req = urllib.request.Request(
                    url, data=data, headers=headers, method=method)
                with _opener(direct).open(req, timeout=timeout) as resp:
                    LAST_ERROR = ""
                    return json.loads(resp.read().decode("utf-8"))
            except Exception as e:  # noqa: BLE001
                last_err = e
        if attempt < retries - 1:
            time.sleep(delay)   # 指数退避 1s -> 2s -> 4s
            delay *= 2
    LAST_ERROR = _describe_error(last_err)
    print(f"[bangumi] {method} {url} 连续 {retries} 轮失败: {last_err!r}")
    return None


def _describe_error(err: Exception | None) -> str:
    """把底层异常翻译成用户能看懂的一句话。"""
    if err is None:
        return "未知错误"
    text = str(err)
    reason = getattr(err, "reason", None) or getattr(err, "__cause__", None)
    detail = str(reason) if reason else text
    if isinstance(reason, TimeoutError) or "timed out" in detail or "timeout" in detail.lower():
        return "连接 api.bgm.tv 超时（网络不稳定或站点被拦截）"
    if "ConnectionRefused" in detail or "connection refused" in detail.lower():
        return "连接被拒绝（检查网络/代理设置）"
    if "Name or service" in detail or "getaddrinfo" in detail or "No address" in detail:
        return "域名解析失败（当前无法访问 api.bgm.tv）"
    if "SSL" in detail or "CERTIFICATE" in detail.upper():
        return "SSL 证书验证失败"
    code = getattr(err, "code", None)
    if code:
        return f"api.bgm.tv 返回 HTTP {code}"
    return f"请求失败：{detail[:60]}"


def download(url: str, save_path: str, timeout: int = _TIMEOUT) -> bool:
    """下载二进制（封面图）到 save_path。成功 True；已存在直接 True（永久缓存）。"""
    if url and __import__('os').path.isfile(save_path) and \
            __import__('os').path.getsize(save_path) > 0:
        return True
    for direct in (False, True):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with _opener(direct).open(req, timeout=timeout) as resp:
                blob = resp.read()
            if blob:
                with open(save_path, "wb") as f:
                    f.write(blob)
                return True
        except Exception:  # noqa: BLE001
            continue
    return False
