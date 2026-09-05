# coding:utf-8
"""offscreen 渲染角色面板各页并截图，用于 UI 排版排查。

用法：.venv/Scripts/python.exe tools/ui_shot.py [窗口宽度]
输出：/tmp/dyber_ui/<page>.png
"""
import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtGui import QPalette, QColor, QFont  # noqa: E402

app = QApplication.instance() or QApplication([])
# offscreen 可视化排查：强制白底黑字 + 中文字体（黑底缺字形会全是方框）
app.setFont(QFont('Microsoft YaHei', 9))
app.setPalette(QPalette(QColor('#f3f3f3')))

import DyberPet.settings as settings  # noqa: E402
settings.init()
settings.petname = settings.default_pet   # 主程序启动时由 PetWidget 设置
from DyberPet.conf import ItemData, ActData  # noqa: E402
settings.items_data = ItemData()          # 主程序在装物品表时设置
settings.act_data = ActData(settings.pets)  # petname 定后再建动作表
try:
    settings.act_data.init_actData(settings.petname, 2,
                                   settings.pet_data.fv_lvl)
except Exception as e:  # noqa: BLE001
    print('[ui_shot] act_data init warn:', e)

# 世界/抉择单例指向 tmp，防写真存档（仅构造+grab，不 tick）
import tempfile  # noqa: E402
import DyberPet.world_service as ws_mod  # noqa: E402
import DyberPet.choice_service as ch_mod  # noqa: E402
ws_mod._WORLD = None
ch_mod._CHOICE = None
_tmp = tempfile.mkdtemp(prefix='dyber_ui_shot_')
_orig_get_world = ws_mod.get_world


def _safe_get_world(save_path=None, seconds_per_year=None):
    import inspect
    try:
        sig = inspect.signature(_orig_get_world)
        kw = {}
        if 'seconds_per_year' in sig.parameters:
            kw['seconds_per_year'] = 3600.0
        return _orig_get_world(os.path.join(_tmp, 'world_state.json'), **kw)
    except TypeError:
        return _orig_get_world(os.path.join(_tmp, 'world_state.json'))


ws_mod.get_world = _safe_get_world

# 追番守护数据目录指向 tmp（防污染项目 data/；CONFIGDIR 是 cwd 相对路径）
import DyberPet.bangumi_daemon as _bgd_mod  # noqa: E402
_bgd_mod._DATA_DIR = os.path.join(_tmp, 'bangumi_data')

# 追番页：patch 掉真实网络（沙箱直连 B站会真拉数据），灌 fixture
from DyberPet.bangumi import bili_client  # noqa: E402
bili_client._http_get = lambda *a, **k: None   # 全部网络请求失败→走 fixture/空
bili_client.download = lambda url, path: False

from DyberPet.Dashboard.DashboardUI import DashboardMainWindow  # noqa: E402

W = int(sys.argv[1]) if len(sys.argv) > 1 else 860
H = 640
board = DashboardMainWindow(minWidth=W, minHeight=H)
board.resize(W, H)

OUT = os.environ.get('UI_SHOT_DIR', '/tmp/dyber_ui')
os.makedirs(OUT, exist_ok=True)

pages = [
    ('status', board.statusInterface),
    ('culti', board.cultiInterface),
    ('adventure', board.adventureInterface),
    ('world', board.worldInterface),
    ('bangumi', board.bangumiInterface),
    ('backpack', board.backpackInterface),
    ('shop', board.shopInterface),
    ('task', board.taskInterface),
    ('anim', board.animInterface),
]

# FluentWindow 整窗在 offscreen 下布局异常——改为每页独立顶层渲染
# 动态内容注入：复现日志流/奇遇卡的真实排版
_FORCE = ('\nQWidget#scrollWidget { background-color: #fafafa; } '
          '\nQWidget { color: #1a1a1a; }')
# 动态内容注入：复现日志流/奇遇卡的真实排版
import datetime  # noqa: E402
si = board.statusInterface
_samples = [
    '噗嗤！移人参入享，修行竟一日千里，突破化神·后期！',
    '突破成功 → 化神·后期（天劫余威尚在，闭关调养数日）',
    '「留守机缘」+1000（离线结算，好友代为照看）',
    '[斗地主战胜] +800，肥牛赢得漂亮，牌友直呼内行',
    '服下「合气丹」：气血翻涌，修为速率 ×1.5（持续 30 分钟）',
    '万万万万——一刀开天门！使用「化神·破境斩」击败了青玄狼王',
    '开炉炼制「万年灵」：丹香四溢，得丹三枚，其中一枚极品',
]
for _txt in _samples:
    from PySide6.QtGui import QPixmap
    si._addNote(QPixmap(24, 24), _txt)

wi = board.worldInterface
wi.world.world['pending_choice'] = {
    'id': 'shot_qiyu', 'title': '古树对弈',
    'narrative': '道友，我在青云山遇一棵千年古树，树下有位白衣老者邀我对弈一局，'
                 '棋盘上灵光流转，似有大道韵味。你说我下不下？',
    'loc': '青云山', 'day': 400, 'ts': 0, 'realm': 6,
    'choices': [
        {'key': 'watch', 'text': '观棋不语，静静参悟'},
        {'key': 'play', 'text': '暗中指点白衣老者一子'},
        {'key': 'drink', 'text': '偷饮树下仙酒'},
    ]}
wi.refresh()

# 追番页：灌真实感测试数据（七日时间线 fixture + 长名 + 完结态）
bi = board.bangumiInterface
_today_wd = datetime.date.today().isoweekday()
_today_ts = int(datetime.datetime.combine(
    datetime.date.today(), datetime.time.min).timestamp())
bi.store.add({"id": 464885, "name": "葬送のフリーレン", "name_cn": "葬送的芙莉莲",
              "date": "2026-08-29", "air_weekday": _today_wd, "eps": 28})
bi.store.add({"id": 464886, "name": "薬屋のひとりごと", "name_cn": "药屋少女的呢喃",
              "date": "2026-08-29", "air_weekday": 6, "eps": 24})
bi.store.add({"id": 464887, "name": "One Piece",
              "name_cn": "海贼王超长标题测试超长超长超长超长超长超长超长",
              "date": "1999-10-20", "air_weekday": 7, "eps": 0})
bi.store.mark_watched(464887)


def _ep(sid, title, pub_index, pub_time, ts, published=1, rating=None):
    e = {"season_id": sid, "title": title, "pub_index": pub_index,
         "pub_time": pub_time, "pub_ts": ts, "published": published,
         "cover": "", "delay_reason": ""}
    if rating:
        e["rating"] = rating
    return e


_tl_days = []
for _off in range(-7, 8):          # 前后 7 天
    _d = datetime.date.today() + datetime.timedelta(days=_off)
    _ts = int(datetime.datetime.combine(_d, datetime.time.min).timestamp())
    _eps = []
    if _off == 0:                  # 今天：三部（含长名+待播+评分）
        _eps = [_ep(464885, "葬送的芙莉莲", "第24话", "17:00",
                    _ts + 17 * 3600, 0, 9.4),
                _ep(464886, "药屋少女的呢喃", "第12话", "21:30",
                    _ts + 21 * 3600, 1, 8.9),
                _ep(464887, "海贼王超长标题测试超长超长超长超长超长超长超长",
                    "第1122话", "19:30", _ts + 19 * 3600, 1)]
    elif _off in (-7, 7):          # 上下周日：两部（目录去重样本）
        _eps = [_ep(464887, "海贼王", "第1121话", "09:30", _ts + 9 * 3600),
                _ep(561062, "牧神记", "第99话", "11:00", _ts + 11 * 3600)]
    _tl_days.append({"date": f"{_d.month}-{_d.day}", "date_ts": _ts,
                     "day_of_week": _d.isoweekday(), "episodes": _eps})
bi.timeline._mem = {"fetched_at": datetime.datetime.now().timestamp(),
                    "days": _tl_days}
bi._on_refresh(False)
bi._show_detail(_ep(464885, "葬送的芙莉莲", "第24话", "17:00",
                    _today_ts + 17 * 3600, 0, 9.4))

for name, page in pages:
    try:
        page.setParent(None)
        page.setWindowFlags(page.windowFlags() | 0x00000001)  # Qt.Window
        page.resize(W, H)
        page.setStyleSheet(page.styleSheet() + _FORCE)
        page.show()
        app.processEvents()
        app.processEvents()
        pix = page.grab()
        path = os.path.join(OUT, f'{name}.png')
        pix.save(path)
        page.hide()
        print('saved', path)
    except Exception as e:  # noqa: BLE001
        print(f'[FAIL] {name}: {e!r}')
print('DONE')
