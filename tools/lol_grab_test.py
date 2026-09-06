# -*- coding: utf-8 -*-
"""抓取 LoL 游戏窗口画面（验证聊天框 OCR 可行性）。纯只读。"""
import sys

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

HWND = 0x5D0A5C
OUT = r'U:\DyberPet\build\game_frame_test.png'

app = QApplication.instance() or QApplication(sys.argv[:1])
screen = QGuiApplication.primaryScreen()
pm = screen.grabWindow(HWND)
print('grabbed:', pm.width(), 'x', pm.height(), 'isNull:', pm.isNull())
ok = pm.save(OUT, 'PNG')
print('saved:', ok, OUT)

# 顺带裁出左下角聊天框区域（约 45% 宽 x 30% 高）再存一份
crop = pm.copy(0, int(pm.height() * 0.66), int(pm.width() * 0.50),
               int(pm.height() * 0.34))
crop.save(OUT.replace('.png', '_chatbox.png'), 'PNG')
print('chatbox crop saved')
QCoreApplication.quit()
