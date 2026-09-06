# -*- coding: utf-8 -*-
"""PrintWindow(PW_RENDERFULLCONTENT) 抓游戏窗口 + 手写 PNG 编码（零第三方依赖）。"""
import ctypes
import struct
import zlib
from ctypes import wintypes

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

HWND = 0x5D0A5C
OUT = r'U:\DyberPet\build\game_frame_pw.png'

RECT = wintypes.RECT
rect = RECT()
user32.GetWindowRect(HWND, ctypes.byref(rect))
w, h = rect.right - rect.left, rect.bottom - rect.top
print('window size:', w, 'x', h)

hdc_window = user32.GetWindowDC(HWND)
hdc_mem = gdi32.CreateCompatibleDC(hdc_window)
hbmp = gdi32.CreateCompatibleBitmap(hdc_window, w, h)
gdi32.SelectObject(hdc_mem, hbmp)

PW_RENDERFULLCONTENT = 2
ok = user32.PrintWindow(HWND, hdc_mem, PW_RENDERFULLCONTENT)
print('PrintWindow:', ok)

class BMIH(ctypes.Structure):
    _fields_ = [('biSize', wintypes.DWORD),
                ('biWidth', ctypes.c_long),
                ('biHeight', ctypes.c_long),
                ('biPlanes', wintypes.WORD),
                ('biBitCount', wintypes.WORD),
                ('biCompression', wintypes.DWORD),
                ('biSizeImage', wintypes.DWORD),
                ('biXPelsPerMeter', ctypes.c_long),
                ('biYPelsPerMeter', ctypes.c_long),
                ('biClrUsed', wintypes.DWORD),
                ('biClrImportant', wintypes.DWORD)]

bmi = BMIH()
bmi.biSize = ctypes.sizeof(BMIH)
bmi.biWidth = w
bmi.biHeight = -h          # top-down
bmi.biPlanes = 1
bmi.biBitCount = 32
bmi.biCompression = 0      # BI_RGB
buf = ctypes.create_string_buffer(w * h * 4)
gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bmi), 0)
raw = buf.raw

gdi32.DeleteObject(hbmp)
gdi32.DeleteDC(hdc_mem)
user32.ReleaseDC(HWND, hdc_window)

# BGRA -> RGB 行扫描（每行前置 filter byte 0）
row_len_src = w * 4
rgb_rows = []
for y in range(h):
    row = raw[y * row_len_src:(y + 1) * row_len_src]
    r = row[2::4]
    g = row[1::4]
    b = row[0::4]
    rgb = bytes(bytearray(
        v for tup in zip(r, g, b) for v in tup))
    rgb_rows.append(b'\x00' + rgb)

def chunk(tag, data):
    c = struct.pack('>I', len(data)) + tag + data
    return c + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)

png = (b'\x89PNG\r\n\x1a\n'
       + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
       + chunk(b'IDAT', zlib.compress(b''.join(rgb_rows), 6))
       + chunk(b'IEND', b''))
with open(OUT, 'wb') as f:
    f.write(png)
print('saved:', OUT, len(png), 'bytes')
