# -*- coding: utf-8 -*-
"""LCU 客户端 API 封装（League Client Update API，本地客户端接口）。

与 llm_core 的 Live Client Data API（游戏内 2999 端口、免认证）不同：
LCU 走客户端进程的动态端口 + riot token 认证，覆盖客户端流程
（匹配/对局接受/结算/点赞/房间）。

认证方式：解析 LeagueClientUx.exe 进程命令行中的
  --app-port=<port> --remoting-auth-token=<token>
不依赖游戏安装路径。请求用 Basic auth ("riot", token)，
自签证书 verify=False、强制禁代理（仅 localhost，与 llm_core 同策略）。

异常策略：所有请求失败一律返回 None 并吞掉异常——LCU 工作线程
绝不允许把桌宠拖崩；客户端未开是常态而非错误。
"""
import re
import time
import threading
import subprocess

import requests
import urllib3

# windowed EXE 里禁止弹出 PowerShell 黑窗
_CREATE_NO_WINDOW = 0x08000000

# 认证解析冷却：客户端没开时避免每个轮询 tick 都跑一次 PowerShell
_AUTH_RETRY_COOLDOWN = 60.0

_lock = threading.Lock()
_auth = None      # (port:int, token:str) or None
_auth_ts = 0.0    # 上次尝试解析的时间（monotonic）

_SESSION = requests.Session()
_SESSION.verify = False

# LCU 用自签证书，屏蔽 urllib3 告警（社区通行做法）
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _parse_ux_commandline():
    """从 LeagueClientUx.exe 命令行解析 (port, token)。失败返回 None。"""
    try:
        ps = ("Get-CimInstance Win32_Process -Filter \"Name='LeagueClientUx.exe'\" "
              "| Select-Object -First 1 -ExpandProperty CommandLine")
        out = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-Command", ps],
            capture_output=True, text=True, timeout=6,
            creationflags=_CREATE_NO_WINDOW)
        m_port = re.search(r"--app-port=(\d+)", out.stdout or "")
        m_tok = re.search(r"--remoting-auth-token=([\w-]+)", out.stdout or "")
        if m_port and m_tok:
            return int(m_port.group(1)), m_tok.group(1)
    except Exception:
        pass
    return None


def get_auth(force=False):
    """获取 LCU 认证（带缓存 + 失败冷却）。客户端未开返回 None。"""
    global _auth, _auth_ts
    with _lock:
        if _auth is not None and not force:
            return _auth
        now = time.monotonic()
        if not force and now - _auth_ts < _AUTH_RETRY_COOLDOWN:
            return None
        _auth_ts = now
        _auth = _parse_ux_commandline()
        return _auth


def invalidate_auth():
    """客户端重启后 token 失效，丢弃缓存（下次请求重解析）。"""
    global _auth
    with _lock:
        _auth = None


def _request(method, path, json_body=None, timeout=4):
    """LCU 请求统一入口。成功返回解析后的 JSON；失败/404 返回 None。

    401 时认定客户端重启导致 token 失效，强制重解析后再试一次。
    """
    auth = get_auth()
    if not auth:
        return None
    port, token = auth
    url = f"https://127.0.0.1:{port}{path}"
    try:
        resp = _SESSION.request(
            method, url, json=json_body, timeout=timeout,
            auth=("riot", token),
            proxies={"http": None, "https": None})
        if resp.status_code == 401:
            invalidate_auth()
            auth = get_auth(force=True)
            if not auth:
                return None
            port, token = auth
            resp = _SESSION.request(
                method, f"https://127.0.0.1:{port}{path}", json=json_body,
                timeout=timeout, auth=("riot", token),
                proxies={"http": None, "https": None})
        if resp.status_code in (200, 201, 204):
            if resp.status_code == 204 or not resp.content:
                return {}
            return resp.json()
        return None
    except Exception:
        return None


def lcu_get(path, timeout=4):
    return _request("GET", path, timeout=timeout)


def lcu_post(path, json_body=None, timeout=4):
    return _request("POST", path, json_body=json_body, timeout=timeout)
