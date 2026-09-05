# -*- coding: utf-8 -*-
"""
export_cultivation_frames.py
把修仙素材包（SVG+SMIL 概念稿，res/icons/cultivation/*.svg）参数化成
桌宠可播放的 PNG 帧序列（Qt QSvgRenderer 不播 SMIL，桌宠动画 = 帧序列）。

产出：
  1) res/pet/道韵元婴/               —— 完整附属宠物包（24 帧待机 + conf + info + 道具图）
  2) res/icons/cultivation/frames/   —— 周天法阵 / 灵气上涌 演出帧（供后续面板/演出接线）

用法：.venv\\Scripts\\python.exe tools\\export_cultivation_frames.py
"""
import json
import math
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QByteArray
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parent.parent
N_FRAMES = 24
SVG_HEAD = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">'


def render(svg: str, size: int, out: Path) -> None:
    """静态 SVG 字符串 -> 透明底 PNG"""
    out.parent.mkdir(parents=True, exist_ok=True)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if not renderer.isValid():
        raise RuntimeError(f"invalid svg for {out}")
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    painter = QPainter(img)
    renderer.render(painter)
    painter.end()
    if not img.save(str(out)):
        raise RuntimeError(f"save failed: {out}")


# ---------------------------------------------------------------- 道韵元婴
def yuanying_frame(t: float) -> str:
    """t in [0,1)：本体浮动 + 光环呼吸 + 灵气环绕，24 帧无缝循环"""
    s = 2 * math.pi * t
    dy = -6 * math.sin(s)
    halo_r = 72 + 10 * math.sin(s)
    halo_o = 0.60 + 0.40 * math.sin(s)
    body = (
        f'<g transform="translate(0 {dy:.2f})">'
        '<circle cx="128" cy="96" r="26" fill="#3E8E7E"/>'
        '<path d="M104 150 Q128 110 152 150 Q150 178 128 182 Q106 178 104 150 Z" fill="#3E8E7E"/>'
        '<path d="M104 150 Q88 168 104 176 Q120 172 120 156 Z" fill="#347A6C"/>'
        '<path d="M152 150 Q168 168 152 176 Q136 172 136 156 Z" fill="#347A6C"/>'
        '<circle cx="120" cy="92" r="3.4" fill="#EAF2F2"/>'
        '<circle cx="136" cy="92" r="3.4" fill="#EAF2F2"/>'
        '</g>'
    )
    motes = []
    for k in range(4):
        a = math.radians(360 * t + 90 * k)
        x = 128 + 88 * math.cos(a)
        y = 128 + 88 * math.sin(a)
        color = "#E0B449" if k % 2 == 0 else "#6FB7A8"
        r = 5 if k % 2 == 0 else 4
        motes.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{color}"/>')
    return (
        SVG_HEAD
        + '<defs><radialGradient id="g" cx="50%" cy="50%" r="50%">'
          '<stop offset="0%" stop-color="#9FE0D2" stop-opacity="0.55"/>'
          '<stop offset="70%" stop-color="#6FB7A8" stop-opacity="0.18"/>'
          '<stop offset="100%" stop-color="#6FB7A8" stop-opacity="0"/>'
          '</radialGradient></defs>'
        + f'<circle cx="128" cy="128" r="{halo_r:.1f}" fill="url(#g)" opacity="{halo_o:.3f}"/>'
        + body
        + "".join(motes)
        + "</svg>"
    )


# ---------------------------------------------------------------- 周天法阵
def array_frame(t: float) -> str:
    a_out = 360 * t          # 外环青 顺时针
    a_in = -360 * t          # 内环金 逆时针
    core_o = 0.70 + 0.30 * math.sin(2 * math.pi * t)
    lines = "".join(
        f'<line x1="128" y1="22" x2="128" y2="40" transform="rotate({45 * k} 128 128)"/>'
        for k in range(8)
    )
    dots = "".join(
        f'<circle cx="128" cy="64" r="5" transform="rotate({60 * k} 128 128)"/>'
        for k in range(6)
    )
    return (
        SVG_HEAD
        + '<defs><radialGradient id="c" cx="50%" cy="50%" r="50%">'
          '<stop offset="0%" stop-color="#FFF1C2"/>'
          '<stop offset="100%" stop-color="#E0B449" stop-opacity="0.2"/>'
          '</radialGradient></defs>'
        + '<g fill="none" stroke="#6FB7A8" stroke-width="3" opacity="0.85">'
          '<circle cx="128" cy="128" r="98" opacity="0.35"/>'
        + f'<g transform="rotate({a_out:.1f} 128 128)" stroke-width="4">{lines}</g></g>'
        + '<g fill="none" stroke="#E0B449" stroke-width="3" opacity="0.9">'
          '<circle cx="128" cy="128" r="64" opacity="0.4"/>'
        + f'<g transform="rotate({a_in:.1f} 128 128)" fill="#E0B449" stroke="none">{dots}</g></g>'
        + f'<circle cx="128" cy="128" r="12" fill="url(#c)" opacity="{core_o:.3f}"/>'
        + "</svg>"
    )


# ---------------------------------------------------------------- 灵气上涌
QI_XR = [(70, 4), (128, 5), (186, 4), (100, 3), (158, 3)]
QI_PHASE = [0.0, 0.2, 0.4, 0.6, 0.8]


def qi_frame(t: float) -> str:
    parts = [SVG_HEAD, '<g fill="#6FB7A8">']
    for (x, r0), ph in zip(QI_XR, QI_PHASE):
        tt = (t + ph) % 1.0
        cy = 236 - 212 * tt
        o = 4 * tt * (1 - tt)  # 三角包络：两端隐没、中段最亮
        parts.append(f'<circle cx="{x}" cy="{cy:.1f}" r="{r0}" opacity="{o:.3f}"/>')
    parts.append("</g></svg>")
    return "".join(parts)


# ---------------------------------------------------------------- 配置文件
PET_DIR = ROOT / "res" / "pet" / "道韵元婴"
FRAMES_DIR = ROOT / "res" / "icons" / "cultivation" / "frames"

ACT_CONF = {"default": {"images": "daoying", "act_num": 1, "frame_refresh": 0.06}}

PET_CONF = {
    "width": 128,
    "height": 128,
    "scale": 1.0,
    "interact_speed": 0.02,
    "default": "default",
    "up": "default",
    "down": "default",
    "left": "default",
    "right": "default",
    "drag": "default",
    "fall": "default",
    "on_floor": "default",
    "patpat": "default",
    "follow_main_x": True,
    "follow_main_y": True,
    "anchor_to_main": [-110, -10],
    "random_act": [
        {"name": "default", "act_list": ["default"], "act_prob": 1.0, "act_type": [0, 10000]}
    ],
    "main_interact": {},
}

ITEMS_CONF = {
    "道韵元婴": {
        "image": "daoyun.png",
        "effect_HP": 0,
        "effect_FV": 0,
        "drop_rate": 0,
        "fv_lock": 3,
        "cost": -1,
        "fv_reward": [3],
        "type": "subpet",
        "description": "一缕青色道韵凝成的元婴小灵体，随周天灵气缓缓浮动。",
    }
}

INFO_CONF = {
    "coverImages": ["c1.png"],
    "pfp": "pfp.png",
    "petName": "道韵元婴",
    "intro": "一缕青色道韵凝成的元婴小灵体，悬于周天灵气之间，随呼吸缓缓浮动，陪伴本体修炼。",
    "tages": {"凡人修仙传": "#C5E0B4", "修仙": "#BDD7EE", "附属宠物": "#FFE699"},
    "author": {
        "name": "肥牛工坊",
        "pfp": "pfp.png",
        "frameColor": "#FFFFFF",
        "links": {},
        "infos": "WorkBuddy 设计助手矢量绘制",
    },
}


def write_json(data, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    app = QGuiApplication([])  # noqa: F841  QSvgRenderer 前置依赖

    # 1) 道韵元婴 24 帧待机（128x128，与现有附属宠物帧规格一致）
    for i in range(N_FRAMES):
        render(yuanying_frame(i / N_FRAMES), 128, PET_DIR / "action" / f"daoying_{i}.png")
    # 道具图 / 图鉴封面 / 头像（用第 0 帧 256 渲染）
    render(yuanying_frame(0.0), 256, PET_DIR / "daoyun.png")
    render(yuanying_frame(0.0), 256, PET_DIR / "info" / "c1.png")
    render(yuanying_frame(0.0), 128, PET_DIR / "info" / "pfp.png")

    # 2) 演出帧：周天法阵 / 灵气上涌（256x256）
    for i in range(N_FRAMES):
        render(array_frame(i / N_FRAMES), 256, FRAMES_DIR / f"array_{i}.png")
        render(qi_frame(i / N_FRAMES), 256, FRAMES_DIR / f"qi_{i}.png")

    # 3) 配置文件
    write_json(ACT_CONF, PET_DIR / "act_conf.json")
    write_json(PET_CONF, PET_DIR / "pet_conf.json")
    write_json(ITEMS_CONF, PET_DIR / "items_config.json")
    write_json(INFO_CONF, PET_DIR / "info" / "info.json")

    # 4) 自检：JSON 可回读 + 帧数量 + PNG 尺寸
    for j in ["act_conf.json", "pet_conf.json", "items_config.json", "info/info.json"]:
        json.loads((PET_DIR / j).read_text(encoding="utf-8"))
    pet_frames = sorted((PET_DIR / "action").glob("daoying_*.png"))
    arr_frames = sorted(FRAMES_DIR.glob("array_*.png"))
    qi_frames = sorted(FRAMES_DIR.glob("qi_*.png"))
    assert len(pet_frames) == N_FRAMES and len(arr_frames) == N_FRAMES and len(qi_frames) == N_FRAMES
    probe = QImage(str(pet_frames[0]))
    assert probe.width() == 128 and probe.height() == 128, probe.size()
    print(f"OK: daoying {len(pet_frames)}x128px, array {len(arr_frames)}x256px, "
          f"qi {len(qi_frames)}x256px, 4 conf json valid")


if __name__ == "__main__":
    main()
