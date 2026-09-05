import os
import json
import ctypes
from sys import platform
from collections import defaultdict

from PySide6.QtGui import QImage, QPixmap
from DyberPet.conf import PetData, TaskData, ActData, ItemData
from PySide6 import QtCore

# [插件] 各插件设置子字典，随 settings.json 落盘；结构由 plugin.json 的 settings_schema 定义
plugins_settings = {}

# [对话] 桌宠聊天窗口配置（语音播报 / 语音输入 / 音色 / 模型）
chat_model = "gemma3:4b"
chat_tts = True
chat_stt = False
chat_stt_always_listen = False
chat_voice = "云希(男·活力)"

# [修仙世界] 世界模拟与日志流（角色面板·修仙世界页；原插件设置已迁入主配置）
world_speed = "标准"          # 标准: 1世界年≈1小时 / 疾行≈30分钟 / 悠远≈3小时
world_bubble_major = True     # L3 重大事件桌宠气泡提及
world_notify_medium = False   # L2 中等事件系统通知（默认关，绝不吵）
world_travel_log = True       # 本体游历琐事直播入流
world_qiyu_choices = True     # 奇遇请示（抉择系统）

if platform == 'win32':
    basedir = ''
    BASEDIR = ''
else:
    #from pathlib import Path
    basedir = os.path.dirname(__file__) #Path(os.path.dirname(__file__))
    #basedir = basedir.parent
    basedir = basedir.replace('\\','/')
    basedir = '/'.join(basedir.split('/')[:-1])
    BASEDIR = basedir

if platform == 'linux':
    configdir = os.path.dirname(os.environ['HOME']+'/.config/DyberPet/DyberPet')
    CONFIGDIR = configdir
else:
    configdir = basedir
    CONFIGDIR = configdir

DEFAULT_THEME_COL = "#009faa"

HELP_URL = "https://github.com/ChaozhongLiu/DyberPet/issues"
PROJECT_URL = "https://github.com/ChaozhongLiu/DyberPet"
DEVDOC_URL = "https://github.com/ChaozhongLiu/DyberPet/blob/main/docs/art_dev.md"
VERSION = "v0.6.7"
AUTHOR = "https://github.com/ChaozhongLiu"
CHARCOLLECT_LINK = "https://github.com/ChaozhongLiu/DyberPet/blob/main/docs/collection.md"
ITEMCOLLECT_LINK = "https://github.com/ChaozhongLiu/DyberPet/blob/main/docs/collection.md"
PETCOLLECT_LINK = "https://github.com/ChaozhongLiu/DyberPet/blob/main/docs/collection.md"

RELEASE_API = "https://api.github.com/repos/ChaozhongLiu/DyberPet/releases/latest"
RELEASE_URL = "https://github.com/ChaozhongLiu/DyberPet/releases/latest"
UPDATE_NEEDED = False

HP_TIERS = [0,50,80,100]
TIER_NAMES = ['Starving', 'Hungry', 'Normal', 'Energetic']
HP_INTERVAL = 2
LVL_BAR_V1 = [20, 120, 300, 600, 1200, 1800, 2400, 3200]
LVL_BAR = [20] + [120]*200
PP_HEART = 0.8
PP_COIN = 0.9
COIN_MU = 10
COIN_SIGMA = 5
PP_ITEM = 0.95
PP_AUDIO = 0.8
PP_BUBBLE = 0.15

# Depreciation when sell item to shop
ITEM_DEPRECIATION = 0.75

# Coin reward once a task is checked from Task Panel
SINGLETASK_REWARD = 200
# Coin reward every 5 task
FIVETASK_REWARD = 1500
# Multiply HP and FV effect if item is required by bubble `feed_required`
FACTOR_FEED_REQ = 5

HUNGERSTR = "Satiety"
FAVORSTR = "Favorability"

LINK_PERMIT = {"BiliBili":"https://space.bilibili.com/",
               "微博":"https://m.weibo.cn/profile/",
               "抖音": "https://www.douyin.com/user/",
               "GitHub":"https://github.com/",
               "爱发电":"https://afdian.net/a/",
               "TikTok":"https://www.tiktok.com/",
               "YouTube":"https://www.youtube.com/"}

ITEM_BGC = {'consumable': '#EFEBDF',
            'collection': '#e1eaf4',
            'Empty': '#f0f0ef',
            'dialogue': '#e1eaf4',
            'subpet': '#f6eae9',
            'autofeed': '#e7f1e4'}
ITEM_BGC_DEFAULT = '#EFEBDF'
ITEM_BDC = '#B1C790'

# when falling met the screen boundary, 
# it will be bounced back with this speed decay factor
SPEED_DECAY = 0.5
AUTOFEED_THRESHOLD = 60

def init():
    # computer system ==================================================
    global platform
    platform = platform

    # check if data directory exists ===================================
    newpath = os.path.join(configdir, 'data')
    if not os.path.exists(newpath):
        os.makedirs(newpath)
    
    global pet_conf
    pet_conf = None

    # Image and animation related variable =============================
    global current_img, previous_img
    # Make img-to-show a global variable for multi-thread behaviors
    current_img = None #QPixmap()
    previous_img = None #Pixmap()
    global current_anchor, previous_anchor
    current_anchor = [0,0]
    previous_anchor = [0,0]

    global onfloor, draging, set_fall, playid
    global mouseposx1,mouseposx2,mouseposx3,mouseposx4,mouseposx5
    global mouseposy1,mouseposy2,mouseposy3,mouseposy4,mouseposy5
    global dragspeedx,dragspeedy,fixdragspeedx, fixdragspeedy, fall_right, gravity, prefall
    # Drag and fall related global variable
    onfloor = 1
    draging = 0
    set_fall = True # default is allow drag
    playid = 0
    mouseposx1,mouseposx2,mouseposx3,mouseposx4,mouseposx5=0,0,0,0,0
    mouseposy1,mouseposy2,mouseposy3,mouseposy4,mouseposy5=0,0,0,0,0
    dragspeedx,dragspeedy=0,0
    fixdragspeedx, fixdragspeedy = 1.0, 1.0
    fall_right = False
    gravity = 0.1
    prefall = 0

    global act_id, current_act, previous_act
    # Select animation to show
    act_id = 0
    current_act, previous_act = None, None

    global showing_dialogue_now
    showing_dialogue_now = False

    # size settings
    global size_factor, screen_scale, font_factor, status_margin, statbar_h, tunable_scale
    try:
        size_factor = 1.0 #ctypes.windll.shcore.GetScaleFactorForDevice(0) / 100
    except:
        size_factor = 1.0
    tunable_scale = 1.0

    # buff related arguments
    global HP_stop, FV_stop
    HP_stop = False
    FV_stop = False

    # sound volumn =====================================================
    global volume
    volume = 0.4

    # day/night mode ===================================================
    global day_night_on, day_start, night_start
    day_night_on = False
    day_start = '08:00'
    night_start = '23:00'

    # pet name =========================================================
    global petname
    petname = ''

    # which screen =====================================================
    global screens, current_screen
    screens = []
    current_screen = None

    # Always on top ====================================================
    global on_top_hint, pets
    on_top_hint = True

    # Translations ====================================================
    global lang_dict
    lang_dict = json.load(open(os.path.join(basedir, 'res/language/language.json'), 'r', encoding='UTF-8'))

    # Settings =========================================================
    pets = get_petlist(os.path.join(basedir, 'res/role'))
    init_settings()
    global default_pet
    if default_pet not in pets:
        # 默认角色优先韩立（修仙放置系统主形象）
        default_pet = '韩立' if '韩立' in pets else pets[0]
    else:
        pets.remove(default_pet)
        pets.sort()
        pets = [default_pet] + pets
    save_settings()

    # Focus Timer
    global focus_timer_on
    focus_timer_on = False

    # Load in pet data ================================================
    global pet_data 
    pet_data = PetData(pets)

    # Load in task data ================================================
    global task_data 
    task_data = TaskData()

    # Init animation config data ================================================
    global act_data 
    act_data = ActData(pets)

    # Load in Language Choice ==========================================
    global language_code, translator
    change_translator(language_code)

    # Load in items data ==========================================
    global items_data, required_item
    items_data = None
    required_item = None



'''
def init_pet():
    global pet_data 
    pet_data = PetData()
    init_settings()
    save_settings()
'''


def init_settings():
    global file_path, settingGood
    file_path = os.path.join(configdir, 'data/settings.json')

    global gravity, fixdragspeedx, fixdragspeedy, tunable_scale, scale_dict, volume, \
           language_code, on_top_hint, default_pet, defaultAct, themeColor, minipet_scale, \
           toaster_on, usertag_dict, auto_lock, bubble_on, sound_on, \
           plugins_settings, \
           chat_model, chat_tts, chat_stt, chat_stt_always_listen, chat_voice, \
           day_night_on, day_start, night_start, \
           world_speed, world_bubble_major, world_notify_medium, \
           world_travel_log, world_qiyu_choices, \
           bangumi_notify, bangumi_remind_hour, bangumi_merge_notify, \
           bangumi_persona_quip, bangumi_show_cover

    # check json file integrity
    try:
        json.load(open(file_path, 'r', encoding='UTF-8'))
        settingGood = True
    except:
        if os.path.isfile(file_path):
            settingGood = False
        else:
            settingGood = True

    if os.path.isfile(file_path) and settingGood:
        data_params = json.load(open(file_path, 'r', encoding='UTF-8'))

        fixdragspeedx, fixdragspeedy = data_params['fixdragspeedx'], data_params['fixdragspeedy']
        gravity = data_params['gravity']
        #tunable_scale = data_params['tunable_scale']
        volume = data_params['volume']
        language_code = data_params.get('language_code', QtCore.QLocale().name())
        on_top_hint = data_params.get('on_top_hint', True)
        default_pet = data_params.get('default_pet',
                                      '韩立' if '韩立' in pets else pets[0])
        defaultAct = data_params.get('defaultAct', {})
        themeColor = data_params.get('themeColor', None)

        # Fix a bug version distributed to users =============
        if defaultAct is None:
            defaultAct = {}
        elif type(defaultAct) == str:
            defaultAct = {}

        for pet in pets:
            defaultAct[pet] = defaultAct.get(pet, None)
        #=====================================================

        # day/night mode =====================================
        global day_night_on, day_start, night_start
        day_night_on = bool(data_params.get('day_night_on', False))
        day_start = data_params.get('day_start', '08:00')
        night_start = data_params.get('night_start', '23:00')
        #=====================================================

        # update for app <= v0.2.2 ===========================
        if language_code == 'CN':
            language_code = QtCore.QLocale().name()
        #=====================================================

        # v0.4.8 update ======================================
        global set_fall
        set_fall = data_params.get('set_fall', True)
        #=====================================================

        # v0.5.0 update ======================================
        # First time open v0.5.0, get the original 
        # tunable_scale as all default
        tunable_scale = data_params.get('tunable_scale', 1.0)
        # v0.5.0 tunable_scales are specified for each character
        scale_dict_tmp = data_params.get('scale_dict', {})
        scale_dict = {}
        for pet in pets:
            pet_scale = scale_dict_tmp.get(pet, tunable_scale)
            # Ensure type is int
            try:
                pet_scale = float(pet_scale)
            except:
                pet_scale = 1.0
            pet_scale = max( 0, min(5, pet_scale) )
            scale_dict[pet] = pet_scale
        tunable_scale = scale_dict[default_pet]

        # mini-pet scale settings
        minipet_scale = data_params.get('minipet_scale', defaultdict(dict))
        minipet_scale = check_dict_datatype(minipet_scale, dict, {})
        minipet_scale = defaultdict(dict, minipet_scale)
        for minipet, sdict in minipet_scale.items():
            minipet_scale[minipet] = check_dict_datatype(sdict, float, 1.0)
        #=====================================================

        # v0.5.3 Toaster can be turned off
        toaster_on = data_params.get('toaster_on', True)
        #=====================================================

        # 全局音效开关（通知/事件提示音）。默认 False = 静音出厂
        sound_on = bool(data_params.get('sound_on', False))
        #=====================================================

        # v0.6.1 User Tag (how pet will call the user)
        usertag_dict_tmp = data_params.get('usertag_dict', {})
        usertag_dict = {}
        for pet in pets:
            usertag = usertag_dict_tmp.get(pet, '')
            usertag_dict[pet] = usertag

        # v0.6.5 stop HP & FV changes when screen locked
        auto_lock = data_params.get('auto_lock', False)
        #=====================================================

        # v0.6.7 Bubble can be turned off
        bubble_on = data_params.get('bubble_on', True)
        #=====================================================

        # [插件] 读取各插件设置；首次启动把旧顶层 lol_companion_* 迁移进 plugins_settings
        plugins_settings = data_params.get('plugins_settings', {})
        if not plugins_settings.get('lol_companion') and any(
                k.startswith('lol_companion_') for k in data_params):
            plugins_settings['lol_companion'] = {
                'enabled':   data_params.get('lol_companion_enabled', True),
                'model':     data_params.get('lol_companion_model', 'gemma3:4b'),
                'style':     data_params.get('lol_companion_style', '肥牛'),
                'reactions': data_params.get('lol_companion_reactions', True),
                'bubble':    data_params.get('lol_companion_bubble', True),
            }
        #=====================================================

        # [对话] 桌宠聊天
        chat_model = data_params.get('chat_model', 'gemma3:4b')
        chat_tts = data_params.get('chat_tts', True)
        chat_stt = data_params.get('chat_stt', False)
        chat_voice = data_params.get('chat_voice', '云希(男·活力)')
        #=====================================================

        # [修仙世界] 世界模拟（原 xiuxian_world 插件设置一次性迁入主配置）
        _old_world = plugins_settings.get('xiuxian_world', {})
        world_speed = data_params.get(
            'world_speed', _old_world.get('world_speed', '标准'))
        world_bubble_major = bool(data_params.get(
            'world_bubble_major', _old_world.get('bubble_major', True)))
        world_notify_medium = bool(data_params.get(
            'world_notify_medium', _old_world.get('notify_medium', False)))
        world_travel_log = bool(data_params.get(
            'world_travel_log', _old_world.get('travel_log', True)))
        world_qiyu_choices = bool(data_params.get(
            'world_qiyu_choices', _old_world.get('qiyu_choices', True)))
        plugins_settings.pop('xiuxian_world', None)   # 插件已退役，键位清掉
        #=====================================================

        # [追番导航] Bangumi 每日放送（原 bangumi 插件设置一次性迁入主配置）
        _old_bgm = plugins_settings.get('bangumi', {})
        bangumi_notify = bool(data_params.get(
            'bangumi_notify', _old_bgm.get('enable_notify', True)))
        bangumi_remind_hour = int(data_params.get(
            'bangumi_remind_hour', _old_bgm.get('remind_hour', 20) or 20))
        bangumi_remind_hour = min(max(bangumi_remind_hour, 0), 23)
        bangumi_merge_notify = bool(data_params.get(
            'bangumi_merge_notify', _old_bgm.get('merge_notify', True)))
        bangumi_persona_quip = bool(data_params.get(
            'bangumi_persona_quip', _old_bgm.get('persona_quip', True)))
        bangumi_show_cover = bool(data_params.get(
            'bangumi_show_cover', _old_bgm.get('show_cover', True)))
        plugins_settings.pop('bangumi', None)   # 插件已退役，键位清掉
        #=====================================================

        # 迁移：nanbeige4.1:3b 思考型模型（解说/对话会空回复或泄露 prompt），自动切到 gemma3:4b。
        if plugins_settings.get('lol_companion', {}).get('model') == 'nanbeige4.1:3b':
            plugins_settings['lol_companion']['model'] = 'gemma3:4b'
        if chat_model == 'nanbeige4.1:3b':
            chat_model = 'gemma3:4b'

    else:
        fixdragspeedx, fixdragspeedy = 1.0, 1.0
        gravity = 0.1
        volume = 0.5
        language_code = QtCore.QLocale().name()
        on_top_hint = True
        default_pet = '韩立' if '韩立' in pets else pets[0]
        defaultAct = {}
        themeColor = None
        for pet in pets:
            defaultAct[pet] = defaultAct.get(pet, None)
        scale_dict = {}
        for pet in pets:
            scale_dict[pet] = 1.0
        tunable_scale = 1.0
        minipet_scale = defaultdict(dict)
        toaster_on = True
        sound_on = False   # 全局音效默认关闭（出厂静音）
        bubble_on = True
        usertag_dict = {}
        auto_lock = False
        plugins_settings = {'lol_companion': {'enabled': True, 'model': 'gemma3:4b', 'style': '肥牛', 'reactions': True, 'bubble': True}}
        chat_model = 'gemma3:4b'
        chat_tts = True
        chat_stt = False
        chat_voice = '云希(男·活力)'
        world_speed = '标准'
        world_bubble_major = True
        world_notify_medium = False
        world_travel_log = True
        world_qiyu_choices = True
        bangumi_notify = True
        bangumi_remind_hour = 20
        bangumi_merge_notify = True
        bangumi_persona_quip = True
        bangumi_show_cover = True
    check_locale()
    save_settings()

def save_settings():
    global file_path, set_fall, gravity, fixdragspeedx, fixdragspeedy, scale_dict, volume, \
           language_code, on_top_hint, default_pet, defaultAct, themeColor, minipet_scale, \
           toaster_on, usertag_dict, auto_lock, bubble_on, sound_on, \
           plugins_settings, \
           chat_model, chat_tts, chat_stt, chat_stt_always_listen, chat_voice, \
           day_night_on, day_start, night_start, \
           world_speed, world_bubble_major, world_notify_medium, \
           world_travel_log, world_qiyu_choices, \
           bangumi_notify, bangumi_remind_hour, bangumi_merge_notify, \
           bangumi_persona_quip, bangumi_show_cover

    data_js = {'gravity':gravity,
               'set_fall': set_fall,
               'fixdragspeedx':fixdragspeedx,
               'fixdragspeedy':fixdragspeedy,
               'usertag_dict':usertag_dict,
               'scale_dict':scale_dict,
               'minipet_scale':minipet_scale,
               'volume':volume,
               'on_top_hint':on_top_hint,
               'toaster_on':toaster_on,
               'sound_on':sound_on,
               'bubble_on':bubble_on,
               'plugins_settings':plugins_settings,
               'chat_model':chat_model,
               'chat_tts':chat_tts,
               'chat_stt':chat_stt,
               'chat_stt_always_listen':chat_stt_always_listen,
               'chat_voice':chat_voice,
               'world_speed':world_speed,
               'world_bubble_major':world_bubble_major,
               'world_notify_medium':world_notify_medium,
               'world_travel_log':world_travel_log,
               'world_qiyu_choices':world_qiyu_choices,
               'bangumi_notify':bangumi_notify,
               'bangumi_remind_hour':bangumi_remind_hour,
               'bangumi_merge_notify':bangumi_merge_notify,
               'bangumi_persona_quip':bangumi_persona_quip,
               'bangumi_show_cover':bangumi_show_cover,
               'default_pet':default_pet,
               'defaultAct':defaultAct,
               'language_code':language_code,
               'themeColor':themeColor,
               'auto_lock':auto_lock,
               'day_night_on':day_night_on,
               'day_start':day_start,
               'night_start':night_start
               }

    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data_js, f, ensure_ascii=False, indent=4)

def get_petlist(dirname):
    folders = os.listdir(dirname)
    pets = []
    # subpets = []
    # v0.3.3 subpet now moved to folder: res/pet/
    for folder in folders:
        folder_path = os.path.join(dirname, folder)
        if folder != 'sys' and os.path.isdir(folder_path):
            pets.append(folder)
            #conf_path = os.path.join(folder_path, 'pet_conf.json')
            #conf = dict(json.load(open(conf_path, 'r', encoding='UTF-8')))
            #subpets += [i for i in conf.get('subpet',{}).keys()]
    pets = list(set(pets))
    #subpets = list(set(subpets))
    #for subpet in subpets:
    #    pets.remove(subpet)
    return pets

def change_translator(language_code):
    global translator
    if language_code == 'en_US':
        translator = None
    else:
        translator = QtCore.QTranslator()
        translator.load(QtCore.QLocale(language_code), "langs", ".", os.path.join(basedir, "res/language/"))

        global TIER_NAMES, HUNGERSTR, FAVORSTR
        TIER_NAMES = [translator.translate("others", i) for i in TIER_NAMES] #.encode('utf-8')
        HUNGER_trans = translator.translate("others", HUNGERSTR) #.encode('utf-8'))
        if HUNGER_trans:
            HUNGERSTR = HUNGER_trans
        FAVOR_trans = translator.translate("others", FAVORSTR) #.encode('utf-8'))
        if FAVOR_trans:
            FAVORSTR = FAVOR_trans

def check_locale():
    global language_code, lang_dict
    if language_code not in lang_dict.values():
        if language_code.split("_")[0] == 'zh':
            language_code = "zh_CN"
        else:
            language_code = "en_US"
            

def check_dict_datatype(raw_dict:dict, dtype, default_value):
    """
    Checks the datatype of values in a dictionary. If a value does not match the specified datatype, it is replaced with a default value.

    Parameters:
    raw_dict (dict): The dictionary to check.
    dtype (type): The expected datatype for the values.
    default_value: The value to replace if the datatype does not match.

    Returns:
    dict: A new dictionary with corrected datatypes.
    """
    return {k: (v if isinstance(v, dtype) else default_value) for k, v in raw_dict.items()}

