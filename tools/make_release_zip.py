# -*- coding: utf-8 -*-
"""把 dist/DyberPet 打成对外发布的 zip。

隐私铁律：排除 data/ 目录（用户真实存档：修为进度/追番订阅/人设记忆/
settings.json）——发布包必须是干净的出厂态，程序首次启动会自动重建 data/。
用法：.venv\\Scripts\\python.exe tools\\make_release_zip.py [版本号]
"""
import os
import sys
import time
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(ROOT)
SRC = os.path.join(PROJ, 'dist', 'DyberPet')
VER = sys.argv[1] if len(sys.argv) > 1 else 'v0.7.0'
OUT = os.path.join(PROJ, 'dist', f'DyberPet-{VER}-win64.zip')

# 顶层需排除的目录（相对 SRC）
EXCLUDE_TOP = {'data'}
# 需排除的文件后缀（PyInstaller 调试残留）
EXCLUDE_EXT = {'.pyc'}

if not os.path.isdir(SRC):
    print(f'NOT_FOUND: {SRC}')
    sys.exit(1)

t0 = time.time()
n_file = 0
n_skip = 0
with zipfile.ZipFile(OUT, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for dirpath, dirnames, filenames in os.walk(SRC):
        rel_dir = os.path.relpath(dirpath, SRC)
        top = rel_dir.split(os.sep)[0] if rel_dir != '.' else ''
        if top in EXCLUDE_TOP:
            n_skip += len(filenames)
            continue
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() in EXCLUDE_EXT:
                n_skip += 1
                continue
            fp = os.path.join(dirpath, fn)
            z.write(fp, os.path.join('DyberPet', os.path.relpath(fp, SRC)))
            n_file += 1
            if n_file % 1000 == 0:
                print(f'  {n_file} files ... {time.time() - t0:.0f}s', flush=True)
    # 空 data/ 目录占位（程序首次运行会往里写存档）
    z.writestr(zipfile.ZipInfo('DyberPet/data/'), b'')

size_mb = os.path.getsize(OUT) / 1024 / 1024
print(f'DONE {OUT}')
print(f'  files={n_file} skipped={n_skip} size={size_mb:.1f}MB '
      f'time={time.time() - t0:.0f}s')
