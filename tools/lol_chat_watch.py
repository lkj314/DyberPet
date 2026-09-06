# -*- coding: utf-8 -*-
"""LoL 聊天框监控原型：定时抓窗口 -> 裁聊天框 -> 帧差分 -> 变化存帧。纯只读。"""
import ctypes
import os
import struct
import time
import zlib
from ctypes import wintypes

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

OUT_DIR = r'U:\DyberPet\build\chat_frames'
DURATION = 180          # 秒
INTERVAL = 1.5          # 秒
WINDOW_TITLE = 'League of Legends (TM) Client'
os.makedirs(OUT_DIR, exist_ok=True)

RECT = wintypes.RECT
BMIH = None


def find_game_hwnd():
    result = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        if buf.value == WINDOW_TITLE and user32.IsWindowVisible(hwnd):
            result.append(hwnd)
        return True
    user32.EnumWindows(cb, 0)
    return result[0] if result else None


def grab(hwnd):
    rect = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w <= 0 or h <= 0:
        return None, (0, 0)
    hdc_window = user32.GetWindowDC(hwnd)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_window)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_window, w, h)
    gdi32.SelectObject(hdc_mem, hbmp)
    user32.PrintWindow(hwnd, hdc_mem, 2)
    global BMIH
    if BMIH is None:
        class BMIH(ctypes.Structure):
            _fields_ = [('biSize', wintypes.DWORD), ('biWidth', ctypes.c_long),
                        ('biHeight', ctypes.c_long), ('biPlanes', wintypes.WORD),
                        ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
                        ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', ctypes.c_long),
                        ('biYPelsPerMeter', ctypes.c_long), ('biClrUsed', wintypes.DWORD),
                        ('biClrImportant', wintypes.DWORD)]
        BMIH = BMIH
    bmi = BMIH()
    bmi.biSize, bmi.biWidth, bmi.biHeight = ctypes.sizeof(BMIH), w, -h
    bmi.biPlanes, bmi.biBitCount, bmi.biCompression = 1, 32, 0
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bmi), 0)
    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(hwnd, hdc_window)
    return buf.raw, (w, h)


def ahash(raw, w, h, box=None, size=16):
    """裁 box=(x0,y0,x1,x1) 比例区域 -> 缩到 size x size 灰度 -> 均值哈希。"""
    x0, y0, x1, y1 = box or (0, 0, 1, 1)
    cw, ch = int(w * (x1 - x0)), int(h * (y1 - y0))
    ox, oy = int(w * x0), int(h * y0)
    row_src = w * 4
    acc = [0] * (size * size)
    n = 0
    for yy in range(0, ch, max(1, ch // (size * 8))):
        row = raw[(oy + yy) * row_src + ox * 4:(oy + yy) * row_src + (ox + cw) * 4]
        for xx in range(0, cw, max(1, cw // (size * 8))):
            px = row[xx * 4:xx * 4 + 4]
            if len(px) < 4:
                break
            gray = (px[0] + px[1] * 2 + px[2]) // 4
            idx = min(size - 1, yy * size // ch) * size + min(size - 1, xx * size // cw)
            acc[idx] += gray
            n += 1
    if n == 0:
        return 0
    avg = sum(acc) // max(1, size * size)
    return sum(1 << i for i, v in enumerate(acc) if (v // max(1, n // (size * size))) > avg)


def save_png(raw, w, h, box, path):
    x0, y0, x1, y1 = box
    cw, ch = int(w * (x1 - x0)), int(h * (y1 - y0))
    ox, oy = int(w * x0), int(h * y0)
    row_src = w * 4
    rows = []
    for yy in range(ch):
        row = raw[(oy + yy) * row_src + ox * 4:(oy + yy) * row_src + (ox + cw) * 4]
        r = row[2::4]; g = row[1::4]; b = row[0::4]
        rows.append(b'\x00' + bytes(bytearray(v for t in zip(r, g, b) for v in t)))

    def chunk(tag, data):
        c = struct.pack('>I', len(data)) + tag + data
        return c + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (b'\x89PNG\r\n\x1a\n'
           + chunk(b'IHDR', struct.pack('>IIBBBBB', cw, ch, 8, 2, 0, 0, 0))
           + chunk(b'IDAT', zlib.compress(b''.join(rows), 6))
           + chunk(b'IEND', b''))
    with open(path, 'wb') as f:
        f.write(png)


def main():
    hwnd = find_game_hwnd()
    if not hwnd:
        print('GAME_NOT_FOUND')
        return
    print(f'watching hwnd={hwnd:#x} for {DURATION}s ...')
    CHAT_BOX = (0.0, 0.64, 0.52, 1.0)      # 左下角聊天框区域
    last_hash = None
    shots = 0
    t0 = time.time()
    n_iter = 0
    while time.time() - t0 < DURATION:
        raw, (w, h) = grab(hwnd)
        if raw is None:
            print('window gone'); break
        n_iter += 1
        cur = ahash(raw, w, h, CHAT_BOX)
        if n_iter <= 3:                    # 前 3 帧无条件保存（诊断基准）
            shots += 1
            path = os.path.join(OUT_DIR, f'base_{shots:02d}.png')
            save_png(raw, w, h, CHAT_BOX, path)
            print(f'BASE {shots} -> {os.path.basename(path)}')
        elif last_hash is not None:
            diff = bin(cur ^ last_hash).count('1')
            if diff > 10:                  # 聊天框内容变化（阈值放宽）
                shots += 1
                path = os.path.join(OUT_DIR, f'frame_{shots:02d}_{int(time.time())}.png')
                save_png(raw, w, h, CHAT_BOX, path)
                print(f'CHANGE bits={diff} -> {os.path.basename(path)}')
        last_hash = cur
        time.sleep(INTERVAL)
    print(f'DONE shots={shots}')


if __name__ == '__main__':
    main()
