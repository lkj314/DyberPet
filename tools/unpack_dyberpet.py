# coding:utf-8
"""解包官方 DyberPet v0.8.10 的 action.dyberpet（pickle 帧包）为散图 PNG。

安全：pickle 是不可信数据格式，用 RestrictedUnpickler 只允许
dict/list/tuple/str/bytes/bytearray/int/float/bool/None，
禁止任何 GLOBAL/STACK_GLOBAL（即禁止反序列化时执行代码）。

用法：
    .venv/Scripts/python.exe tools/unpack_dyberpet.py
"""
import os
import pickle
import io
import shutil

SRC_ROOT = r'U:\game\zc\win\res'
DST_ROOT = r'U:\DyberPet\res'

TARGETS = [
    (r'role\韩立', '韩立'),
    (r'role\银月', '银月'),
]


class RestrictedUnpickler(pickle.Unpickler):
    """只允许基础内建类型；遇到任何 GLOBAL 直接拒绝。"""

    def find_class(self, module, name):
        raise pickle.UnpicklingError(
            f"forbidden global: {module}.{name} (safety)")


def safe_load(path):
    with open(path, 'rb') as f:
        return RestrictedUnpickler(f).load()


def extract(dyberpet_path, out_action_dir):
    data = safe_load(dyberpet_path)
    if not isinstance(data, dict):
        raise ValueError(f'unexpected payload type: {type(data)}')
    os.makedirs(out_action_dir, exist_ok=True)
    n = 0
    for key, val in data.items():
        if not isinstance(key, str):
            print(f'  skip non-str key: {key!r}')
            continue
        # 兼容 key 带/不带 .png 后缀
        name = key[:-4] if key.endswith('.png') else key
        if isinstance(val, (bytes, bytearray)):
            raw = bytes(val)
        elif isinstance(val, str) and val.startswith('iVBOR'):  # base64 png
            import base64
            raw = base64.b64decode(val)
        else:
            print(f'  skip unsupported value for {name}: {type(val)}')
            continue
        if not raw.startswith(b'\x89PNG'):
            print(f'  skip non-png payload: {name}')
            continue
        out = os.path.join(out_action_dir, f'{name}.png')
        with open(out, 'wb') as f:
            f.write(raw)
        n += 1
    return n, len(data)


def main():
    import json
    for rel, name in TARGETS:
        src = os.path.join(SRC_ROOT, rel)
        dst = os.path.join(DST_ROOT, 'role', name)
        pkg = os.path.join(src, 'action', 'action.dyberpet')
        print(f'== {name} ==')
        if os.path.isfile(pkg):
            out_dir = os.path.join(dst, 'action')
            # action 目录若已有散图则先清掉（只有我们生成的才在里面）
            if os.path.isdir(out_dir):
                shutil.rmtree(out_dir)
            n, total = extract(pkg, out_dir)
            print(f'  unpacked {n}/{total} frames -> {out_dir}')
        else:
            print('  no action.dyberpet found, skip unpack')
        # 拷贝配置与 info / note
        for item in ('act_conf.json', 'pet_conf.json'):
            s = os.path.join(src, item)
            if os.path.isfile(s):
                shutil.copy2(s, os.path.join(dst, item))
                print(f'  copied {item}')
        for d in ('info', 'note'):
            s = os.path.join(src, d)
            if os.path.isdir(s):
                shutil.copytree(s, os.path.join(dst, d), dirs_exist_ok=True)
                print(f'  copied {d}/')
        # 校验帧数 vs act_conf
        conf_path = os.path.join(dst, 'act_conf.json')
        with open(conf_path, encoding='utf-8') as f:
            conf = json.load(f)
        missing_total = 0
        for act, c in conf.items():
            imgs = c.get('images')
            num = int(c.get('act_num', 0))
            for i in range(num):
                p = os.path.join(dst, 'action', f'{imgs}_{i}.png')
                if not os.path.isfile(p):
                    missing_total += 1
                    if missing_total <= 5:
                        print(f'  MISSING frame: {imgs}_{i}.png')
        print(f'  act_conf check: {len(conf)} acts, '
              f'{"ALL FRAMES OK" if missing_total == 0 else f"{missing_total} frames missing"}')
    print('DONE')


if __name__ == '__main__':
    main()
