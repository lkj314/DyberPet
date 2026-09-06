# -*- coding: utf-8 -*-
"""LoL 聊天日志探测脚本（纯只读，不改任何文件）。

目的：为「玩家发言识别 + AI 翻译」功能确认本机日志格式。
用法：开一局对局（训练模式/人机即可，进游戏后随便打一句字），然后运行：
    .venv\\Scripts\\python.exe tools\\lol_chat_probe.py
把输出整段发给 AI 即可。
"""
import os
import re
import subprocess

CHAT_PREVIEW_RE = re.compile(
    r'^\s*(\d{1,2}:\d{2})\s+(?:\[(All|Team|Party|All?|敌人|全体|队伍)\]\s*)?'
    r'(.+?)(?:\s+\((.+?)\))?:\s*(.+)$')

CANDIDATE_ROOTS = [
    r"C:\Riot Games\League of Legends",
    r"D:\Riot Games\League of Legends",
    r"C:\Riot Games\League of Legends PBE",
    r"D:\Riot Games\League of Legends PBE",
    r"E:\Riot Games\League of Legends PBE",
    r"D:\League of Legends (PBE)",
]


def find_gamelogs_from_process():
    """游戏运行中：从进程可执行文件路径反推 Logs\\GameLogs。"""
    dirs = []
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process | Where-Object { $_.Path } | "
             "Where-Object { $_.Name -like '*League*' -or $_.Path -like '*英雄*' } "
             "| Select-Object -ExpandProperty Path"],
            capture_output=True, text=True, timeout=20).stdout
        for line in out.splitlines():
            exe = line.strip()
            if not exe.lower().endswith('.exe'):
                continue
            d = os.path.dirname(exe)
            for cand in (os.path.join(d, 'Logs', 'GameLogs'),
                         os.path.normpath(os.path.join(d, '..', 'Logs', 'GameLogs')),
                         os.path.normpath(os.path.join(d, '..', '..', 'Logs', 'GameLogs')),
                         os.path.normpath(os.path.join(d, 'Game', 'Logs', 'GameLogs'))):
                if os.path.isdir(cand):
                    dirs.append(cand)
            print(f'[进程] {exe}')
    except Exception as e:  # noqa: BLE001
        print(f'[进程探测] 失败: {e!r}')
    return dirs


def find_gamelogs_from_candidates():
    dirs = []
    for base in CANDIDATE_ROOTS:
        for rel in (('Game', 'Logs', 'GameLogs'), ('Logs', 'GameLogs')):
            cand = os.path.join(base, *rel)
            if os.path.isdir(cand):
                dirs.append(cand)
    return dirs


def newest_sessions(gamelogs_dir, k=2):
    """GameLogs 下按修改时间取最新 k 个对局文件夹。"""
    try:
        subs = [os.path.join(gamelogs_dir, n) for n in os.listdir(gamelogs_dir)]
        subs = [s for s in subs if os.path.isdir(s)]
        subs.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        return subs[:k]
    except OSError:
        return []


def read_tail(path, max_bytes=20000):
    """读文件尾部并猜测编码解码。"""
    size = os.path.getsize(path)
    with open(path, 'rb') as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
        raw = f.read()
    for enc in ('utf-8', 'utf-16', 'gbk'):
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode('utf-8', errors='replace'), 'utf-8(replace)'


def main():
    print('=' * 64)
    print('LoL 聊天日志探测（只读）')
    print('=' * 64)

    dirs = find_gamelogs_from_process() or []
    dirs += find_gamelogs_from_candidates()
    dirs = list(dict.fromkeys(dirs))
    if not dirs:
        print('\n未找到 Logs\\GameLogs 目录。请在下方补充你的游戏安装目录：')
        print('  （WeGame 右键英雄联盟 → 打开文件夹，看里面是否有 Game\\Logs 或 Logs）')
        return

    print(f'\n发现 GameLogs 目录 x{len(dirs)}:')
    for d in dirs:
        print(f'  {d}')

    for gd in dirs:
        for sess in newest_sessions(gd):
            print('\n' + '-' * 64)
            print(f'对局目录: {sess}')
            try:
                names = sorted(
                    (os.path.join(sess, n) for n in os.listdir(sess)),
                    key=os.path.getmtime)
            except OSError as e:
                print(f'  读取失败: {e!r}')
                continue
            for path in names:
                mt = os.path.getmtime(path)
                size = os.path.getsize(path)
                print(f'\n  文件: {os.path.basename(path)}  '
                      f'({size} bytes, mtime={mt:.0f})')
                if size == 0:
                    continue
                text, enc = read_tail(path)
                lines = text.splitlines()
                hits = [ln for ln in lines if CHAT_PREVIEW_RE.match(ln)]
                print(f'  编码猜测: {enc}；尾部 {len(lines)} 行；'
                      f'疑似聊天行 x{len(hits)}')
                for ln in lines[-25:]:
                    print(f'    | {ln[:150]}')
    print('\n' + '=' * 64)
    print('完成。请把以上输出整段复制给 AI 分析。')


if __name__ == '__main__':
    main()
