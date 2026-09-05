# coding:utf-8
import os
import json
import urllib.request
from sys import platform

from qfluentwidgets import (SettingCardGroup, SwitchSettingCard, HyperlinkCard,InfoBar,
                            ComboBoxSettingCard, ScrollArea, ExpandLayout, InfoBarPosition,
                            setThemeColor, PushSettingCard)

from qfluentwidgets import FluentIcon as FIF
from PySide6.QtCore import Qt, Signal, QUrl, QStandardPaths, QLocale, QThread
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import QWidget, QLabel, QApplication
#from qframelesswindow import FramelessWindow

from .custom_utils import Dyber_RangeSettingCard, Dyber_ComboBoxSettingCard, CustomColorSettingCard
import DyberPet.settings as settings
from DyberPet.llm_core import list_ollama_models

# 推荐模型列表（Ollama 不可达时的兜底选项 + 常见小模型）
# 仅保留确定存在 / 已在本机验证过的模型标签，避免给不存在的 tag 当推荐
# 仅放 Ollama 官方库/本机可确认存在的模型。gemma3:4b 为非 qwen、非思考型、
# 多语言含中文的可用模型，作为默认推荐；nanbeige4.1:3b 是思考型模型，
# 用作实时解说/对话会空回复，仅作可选项保留，默认不推荐。
RECOMMENDED_MODELS = ["gemma3:4b", "qwen2.5:3b", "qwen3:1.7b", "nanbeige4.1:3b", "qwen2.5vl:3b"]

basedir = settings.BASEDIR
module_path = os.path.join(basedir, 'DyberPet/DyberSettings/')
'''
if platform == 'win32':
    basedir = ''
    module_path = 'DyberPet/DyberSettings/'
else:
    #from pathlib import Path
    basedir = os.path.dirname(__file__) #Path(os.path.dirname(__file__))
    #basedir = basedir.parent
    basedir = basedir.replace('\\','/')
    basedir = '/'.join(basedir.split('/')[:-2])

    module_path = os.path.join(basedir, 'DyberPet/DyberSettings/')
'''


class SettingInterface(ScrollArea):
    """ Setting interface """

    ontop_changed = Signal(name='ontop_changed')
    scale_changed = Signal(name='scale_changed')
    lang_changed = Signal(name='lang_changed')

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("SettingInterface")
        self.scrollWidget = QWidget()
        self.expandLayout = ExpandLayout(self.scrollWidget)

        # setting label
        self.settingLabel = QLabel(self.tr("Settings"), self)
        
        # Mode =========================================================================================
        self.ModeGroup = SettingCardGroup(self.tr('Mode'), self.scrollWidget)
        # Always on top
        self.AlwaysOnTopCard = SwitchSettingCard(
            FIF.PIN,
            self.tr("Always-On-Top"),
            self.tr("Pet will be displayed on top of the other Apps"),
            parent=self.ModeGroup #DisplayModeGroup
        )
        if settings.on_top_hint:
            self.AlwaysOnTopCard.setChecked(True)
        else:
            self.AlwaysOnTopCard.setChecked(False)
        self.AlwaysOnTopCard.switchButton.checkedChanged.connect(self._AlwaysOnTopChanged)

        # Allow drop
        self.AllowDropCard = SwitchSettingCard(
            QIcon(os.path.join(basedir, 'res/icons/system/falldown.svg')),
            self.tr("Allow Drop"),
            self.tr("When mouse released, pet falls to the ground (on) / stays at the site (off)"),
            parent=self.ModeGroup #DisplayModeGroup
        )
        if settings.set_fall:
            self.AllowDropCard.setChecked(True)
        else:
            self.AllowDropCard.setChecked(False)
        self.AllowDropCard.switchButton.checkedChanged.connect(self._AllowDropChanged)

        # Auto-Lock
        self.AutoLockCard = SwitchSettingCard(
            QIcon(os.path.join(basedir, 'res/icons/system/lock.svg')),
            self.tr("Auto-Lock"),
            self.tr("When screen is locked, HP and FV will be locked too (currently only works in Windows)"),
            parent=self.ModeGroup #DisplayModeGroup
        )
        if settings.auto_lock:
            self.AutoLockCard.setChecked(True)
        else:
            self.AutoLockCard.setChecked(False)
        self.AutoLockCard.switchButton.checkedChanged.connect(self._AutoLockChanged)
        if platform != 'win32':
            self.AutoLockCard.switchButton.indicator.setEnabled(False)


        # Interaction parameters =======================================================================
        self.InteractionGroup = SettingCardGroup(self.tr('Interaction'), self.scrollWidget)
        self.GravityCard = Dyber_RangeSettingCard(
            1, 200, 0.01,
            QIcon(os.path.join(basedir, 'res/icons/system/gravity.svg')),
            self.tr("Gravity"),
            self.tr("Pet falling down acceleration"),
            parent=self.InteractionGroup
        )

        self.GravityCard.setValue(int(settings.gravity*100))
        self.GravityCard.slider.valueChanged.connect(self._GravityChanged)

        self.DragCard = Dyber_RangeSettingCard(
            0, 200, 0.01,
            QIcon(os.path.join(basedir, 'res/icons/system/mousedrag.svg')),
            self.tr("Drag Speed"),
            self.tr("Mouse speed factor"),
            parent=self.InteractionGroup
        )
        self.DragCard.setValue(int(settings.fixdragspeedx*100))
        self.DragCard.slider.valueChanged.connect(self._DragChanged)


        # Notification parameters ======================================================================
        self.VolumnGroup = SettingCardGroup(self.tr('Notification'), self.scrollWidget)
        self.VolumnCard = Dyber_RangeSettingCard(
            0, 10, 0.1,
            QIcon(os.path.join(basedir, 'res/icons/system/speaker.svg')),
            self.tr("Volumn"),
            self.tr("Volumn of notification and pet"),
            parent=self.VolumnGroup
        )
        self.VolumnCard.setValue(int(settings.volume*10))
        self.VolumnCard.slider.valueChanged.connect(self._VolumnChanged)

        # 全局音效开关（默认关闭 = 静音出厂）
        self.SoundOnCard = SwitchSettingCard(
            QIcon(os.path.join(basedir, 'res/icons/system/speaker.svg')),
            self.tr("Sound Effects"),
            self.tr("When turned on, notification and event sound effects will play"),
            parent=self.VolumnGroup
        )
        self.SoundOnCard.setChecked(bool(getattr(settings, 'sound_on', False)))
        self.SoundOnCard.switchButton.checkedChanged.connect(self._SoundOnChanged)

        self.AllowToasterCard = SwitchSettingCard(
            QIcon(os.path.join(basedir, 'res/icons/system/popup.svg')),
            self.tr("Pop-up Toaster"),
            self.tr("When turned on, notification will pop-up at the bottom right corner"),
            parent=self.VolumnGroup
        )
        if settings.toaster_on:
            self.AllowToasterCard.setChecked(True)
        else:
            self.AllowToasterCard.setChecked(False)
        self.AllowToasterCard.switchButton.checkedChanged.connect(self._AllowToasterChanged)

        self.AllowBubbleCard = SwitchSettingCard(
            QIcon(os.path.join(basedir, 'res/icons/system/bubble.svg')),
            self.tr("Dialogue Bubble"),
            self.tr("When turned on, various kinds of bubbles will pop-up above the pet"),
            parent=self.VolumnGroup
        )
        if settings.bubble_on:
            self.AllowBubbleCard.setChecked(True)
        else:
            self.AllowBubbleCard.setChecked(False)
        self.AllowBubbleCard.switchButton.checkedChanged.connect(self._AllowBubbleChanged)

        # Personalization ==============================================================================
        self.PersonalGroup = SettingCardGroup(self.tr('Personalization'), self.scrollWidget)
        self.ScaleCard = Dyber_RangeSettingCard(
            1, 50, 0.1,
            QIcon(os.path.join(basedir, 'res/icons/system/resize.svg')),
            self.tr("Pet Scale"),
            self.tr("Adjust size of the pet"),
            parent=self.PersonalGroup
        )
        self.ScaleCard.setValue(int(settings.tunable_scale*10))
        self.ScaleCard.slider.valueChanged.connect(self._ScaleChanged)

        pet_list = settings.pets
        self.DefaultPetCard = Dyber_ComboBoxSettingCard(
            pet_list,
            pet_list,
            QIcon(os.path.join(basedir, 'res/icons/system/homestar.svg')),
            self.tr('Default Pet'),
            self.tr('Pet to show everytime App starts'),
            parent=self.PersonalGroup
        )
        self.DefaultPetCard.comboBox.currentTextChanged.connect(self._DefaultPetChanged)

        lang_choices = list(settings.lang_dict.keys())
        lang_now = lang_choices[list(settings.lang_dict.values()).index(settings.language_code)]
        lang_choices.remove(lang_now)
        lang_choices = [lang_now] + lang_choices
        self.languageCard = Dyber_ComboBoxSettingCard(
            lang_choices,
            lang_choices,
            FIF.LANGUAGE,
            self.tr('Language/语言'),
            self.tr('Set your preferred language for UI'),
            parent=self.PersonalGroup
        )
        self.languageCard.comboBox.currentTextChanged.connect(self._LanguageChanged)

        self.themeColorCard = CustomColorSettingCard(
            FIF.PALETTE,
            self.tr('Theme color'),
            self.tr('Change the theme color of you application'),
            self.PersonalGroup
        )
        self.themeColorCard.colorChanged.connect(self.colorChanged)

        # 注意：LoL Companion 插件设置已迁移到「插件中心」（DyberPet/DyberSettings/PluginCenterUI.py），
        # 不再出现在「基本设置」中。

        # Chat (对话) ==============================================================================
        self.ChatGroup = SettingCardGroup(self.tr('Chat'), self.scrollWidget)
        self.ChatTtsCard = SwitchSettingCard(
            FIF.SEND,
            self.tr("Voice Reply"),
            self.tr("Pet speaks its replies out loud via edge-tts"),
            parent=self.ChatGroup
        )
        if settings.chat_tts:
            self.ChatTtsCard.setChecked(True)
        else:
            self.ChatTtsCard.setChecked(False)
        self.ChatTtsCard.switchButton.checkedChanged.connect(self._ChatTtsChanged)

        self.ChatSttCard = SwitchSettingCard(
            FIF.MICROPHONE,
            self.tr("Voice Input"),
            self.tr("Hold the mic button to talk; speech is transcribed to text (offline Vosk)"),
            parent=self.ChatGroup
        )
        if settings.chat_stt:
            self.ChatSttCard.setChecked(True)
        else:
            self.ChatSttCard.setChecked(False)
        self.ChatSttCard.switchButton.checkedChanged.connect(self._ChatSttChanged)

        self.ChatSttAlwaysCard = SwitchSettingCard(
            FIF.MICROPHONE,
            self.tr("Always Listening"),
            self.tr("Keep mic on and auto-reply when you speak (requires Voice Input)"),
            parent=self.ChatGroup
        )
        if settings.chat_stt_always_listen:
            self.ChatSttAlwaysCard.setChecked(True)
        else:
            self.ChatSttAlwaysCard.setChecked(False)
        self.ChatSttAlwaysCard.switchButton.checkedChanged.connect(self._ChatSttAlwaysChanged)

        self.ChatVoiceCard = Dyber_ComboBoxSettingCard(
            ["云希(男·活力)", "晓晓(女·温柔)", "云扬(男·专业)", "云健(男·沉稳)", "晓伊(女·清新)"],
            ["云希(男·活力)", "晓晓(女·温柔)", "云扬(男·专业)", "云健(男·沉稳)", "晓伊(女·清新)"],
            FIF.ROBOT,
            self.tr('TTS Voice'),
            self.tr('Voice used for spoken replies'),
            parent=self.ChatGroup
        )
        self.ChatVoiceCard.comboBox.setCurrentText(settings.chat_voice)
        self.ChatVoiceCard.comboBox.currentTextChanged.connect(self._ChatVoiceChanged)

        self.ChatModelCard = Dyber_ComboBoxSettingCard(
            RECOMMENDED_MODELS, RECOMMENDED_MODELS,
            FIF.ROBOT,
            self.tr('Chat Model'),
            self.tr('Local Ollama model used for chat replies (can differ from commentary)'),
            parent=self.ChatGroup
        )
        self.ChatModelCard.comboBox.setCurrentText(settings.chat_model)
        self.ChatModelCard.comboBox.currentTextChanged.connect(self._ChatModelChanged)

        self.ChatGroup.addSettingCard(self.ChatTtsCard)
        self.ChatGroup.addSettingCard(self.ChatSttCard)
        self.ChatGroup.addSettingCard(self.ChatModelCard)
        self.ChatGroup.addSettingCard(self.ChatVoiceCard)
        self.expandLayout.addWidget(self.ChatGroup)

        # Day/Night mode (借鉴官方 v0.8.10 昼夜模式) ========================================
        self.DayNightGroup = SettingCardGroup(self.tr('Day/Night Mode'), self.scrollWidget)
        self.DayNightCard = SwitchSettingCard(
            QIcon(os.path.join(basedir, 'res/icons/system/sun.svg')) if os.path.isfile(
                os.path.join(basedir, 'res/icons/system/sun.svg')) else FIF.CALENDAR,
            self.tr('Day/Night Mode'),
            self.tr('Pet sleeps automatically at night and wakes up in the morning'),
            parent=self.DayNightGroup
        )
        self.DayNightCard.setChecked(bool(getattr(settings, 'day_night_on', False)))
        self.DayNightCard.switchButton.checkedChanged.connect(self._DayNightChanged)

        _time_opts = [f'{h:02d}:{m:02d}' for h in range(24) for m in (0, 30)]
        self.DayStartCard = Dyber_ComboBoxSettingCard(
            _time_opts, _time_opts,
            QIcon(os.path.join(basedir, 'res/icons/system/sun.svg')) if os.path.isfile(
                os.path.join(basedir, 'res/icons/system/sun.svg')) else FIF.CALENDAR,
            self.tr('Wake-up Time'),
            self.tr('Day starts at'),
            parent=self.DayNightGroup
        )
        if getattr(settings, 'day_start', '08:00') in _time_opts:
            self.DayStartCard.comboBox.setCurrentText(settings.day_start)
        self.DayStartCard.comboBox.currentTextChanged.connect(self._DayStartChanged)

        self.NightStartCard = Dyber_ComboBoxSettingCard(
            _time_opts, _time_opts,
            QIcon(os.path.join(basedir, 'res/icons/system/moon.svg')) if os.path.isfile(
                os.path.join(basedir, 'res/icons/system/moon.svg')) else FIF.CALENDAR,
            self.tr('Bedtime'),
            self.tr('Night starts at'),
            parent=self.DayNightGroup
        )
        if getattr(settings, 'night_start', '23:00') in _time_opts:
            self.NightStartCard.comboBox.setCurrentText(settings.night_start)
        self.NightStartCard.comboBox.currentTextChanged.connect(self._NightStartChanged)

        self.DayNightGroup.addSettingCard(self.DayNightCard)
        self.DayNightGroup.addSettingCard(self.DayStartCard)
        self.DayNightGroup.addSettingCard(self.NightStartCard)
        self.expandLayout.addWidget(self.DayNightGroup)

        # 确保当前设置值存在于下拉框（即便不在推荐列表里）
        self._populate_combo(self.ChatModelCard, RECOMMENDED_MODELS, settings.chat_model)

        # About ==============================================================================
        self.aboutGroup = SettingCardGroup(self.tr('About'), self.scrollWidget)
        update_needed, update_text = self._checkUpdate()
        settings.UPDATE_NEEDED = update_needed
        self.aboutCard = HyperlinkCard(
            settings.RELEASE_URL,
            self.tr('Release Website'),
            QIcon(os.path.join(basedir, 'res/icons/system/update.svg')),
            self.tr('Check Updates'),
            update_text, #self.tr('Check update and learn more about the project on our GitHub page'),
            self.aboutGroup
        )
        self.helpCard = HyperlinkCard(
            settings.HELP_URL,
            self.tr('Issue Page'),
            FIF.HELP,
            self.tr('Help & Issue'),
            self.tr('Post your issue or question on our GitHub Issue, or contact us on BiliBili'),
            self.aboutGroup
        )
        self.devCard = HyperlinkCard(
            settings.DEVDOC_URL,
            self.tr('Developer Document'),
            QIcon(os.path.join(basedir, 'res/icons/system/document.svg')),
            self.tr('Re-development'),
            self.tr('If you want to develop your own pet/item/actions... Check here'),
            self.aboutGroup
        )


        self.__initWidget()

    def __initWidget(self):
        #self.resize(1000, 800)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setViewportMargins(0, 75, 0, 20)
        self.setWidget(self.scrollWidget)
        #self.scrollWidget.resize(1000, 800)
        self.setWidgetResizable(True)

        # initialize style sheet
        self.__setQss()

        # initialize layout
        self.__initLayout()
        #self.__connectSignalToSlot()

    def __initLayout(self):
        self.settingLabel.move(50, 20)

        # add cards to group
        self.ModeGroup.addSettingCard(self.AlwaysOnTopCard)
        self.ModeGroup.addSettingCard(self.AllowDropCard)
        self.ModeGroup.addSettingCard(self.AutoLockCard)

        self.InteractionGroup.addSettingCard(self.GravityCard)
        self.InteractionGroup.addSettingCard(self.DragCard)

        self.VolumnGroup.addSettingCard(self.VolumnCard)
        self.VolumnGroup.addSettingCard(self.AllowToasterCard)
        self.VolumnGroup.addSettingCard(self.AllowBubbleCard)

        self.PersonalGroup.addSettingCard(self.ScaleCard)
        self.PersonalGroup.addSettingCard(self.DefaultPetCard)
        self.PersonalGroup.addSettingCard(self.languageCard)
        self.PersonalGroup.addSettingCard(self.themeColorCard)

        self.aboutGroup.addSettingCard(self.aboutCard)
        self.aboutGroup.addSettingCard(self.helpCard)
        self.aboutGroup.addSettingCard(self.devCard)

        # add setting card group to layout
        self.expandLayout.setSpacing(28)
        self.expandLayout.setContentsMargins(60, 10, 60, 0)

        self.expandLayout.addWidget(self.ModeGroup)
        self.expandLayout.addWidget(self.InteractionGroup)
        self.expandLayout.addWidget(self.VolumnGroup)
        self.expandLayout.addWidget(self.PersonalGroup)
        self.expandLayout.addWidget(self.aboutGroup)

    def __setQss(self):
        """ set style sheet """
        self.scrollWidget.setObjectName('scrollWidget')
        self.settingLabel.setObjectName('settingLabel')

        theme = 'light' #if isDarkTheme() else 'light'
        with open(os.path.join(basedir, 'res/icons/system/qss/', theme, 'setting_interface.qss'), encoding='utf-8') as f:
            self.setStyleSheet(f.read())

    def _AlwaysOnTopChanged(self, isChecked):
        if isChecked:
            settings.on_top_hint = True
            settings.save_settings()
            self.ontop_changed.emit()
        else:
            settings.on_top_hint = False
            settings.save_settings()
            self.ontop_changed.emit()

    def _AllowDropChanged(self, isChecked):
        if isChecked:
            settings.set_fall = True
        else:
            settings.set_fall = False
        settings.save_settings()

    def _AutoLockChanged(self, isChecked):
        if isChecked:
            settings.auto_lock = True
        else:
            settings.auto_lock = False
        settings.save_settings()

    def _GravityChanged(self, value):
        settings.gravity = value*0.01
        settings.save_settings()

    def _DragChanged(self, value):
        settings.fixdragspeedx, settings.fixdragspeedy = value*0.01, value*0.01
        settings.save_settings()

    def _VolumnChanged(self, value):
        settings.volume = round(value*0.1, 3)
        settings.save_settings()

    def _SoundOnChanged(self, isChecked):
        settings.sound_on = bool(isChecked)
        settings.save_settings()

    def _ScaleChanged(self, value):
        settings.tunable_scale = value*0.1
        settings.scale_dict[settings.petname] = settings.tunable_scale
        settings.save_settings()
        self.scale_changed.emit()

    def _update_scale(self):
        self.ScaleCard.setValue(int(settings.tunable_scale*10))

    def _DefaultPetChanged(self, value):
        settings.default_pet = value
        settings.save_settings()

    def _LanguageChanged(self, value):
        settings.language_code = settings.lang_dict[value]
        settings.save_settings()
        settings.change_translator(settings.lang_dict[value])
        #self.retranslateUi()
        self.__showRestartTooltip()
        self.lang_changed.emit()
    
    def __showRestartTooltip(self):
        """ show restart tooltip """
        InfoBar.warning(
            '',
            self.tr('Configuration takes effect after restart\n此设置在重启后生效'),
            duration=3000,
            position=InfoBarPosition.BOTTOM,
            parent=self.window()
        )

    def _DayNightChanged(self, checked):
        settings.day_night_on = bool(checked)
        settings.save_settings()

    def _DayStartChanged(self, text):
        settings.day_start = text
        settings.save_settings()

    def _NightStartChanged(self, text):
        settings.night_start = text
        settings.save_settings()

    def colorChanged(self, color_str):
        setThemeColor(color_str)
        settings.themeColor = color_str
        settings.save_settings()

    def _checkUpdate(self):
        local_version = settings.VERSION
        # 关闭官方更新检查（避免打包版弹出官方新版本提示）
        return False, local_version + "  " + self.tr("Update check disabled")
        
    def _AllowToasterChanged(self, isChecked):
        if isChecked:
            settings.toaster_on = True
        else:
            settings.toaster_on = False
        settings.save_settings()

    def _AllowBubbleChanged(self, isChecked):
        if isChecked:
            settings.bubble_on = True
        else:
            settings.bubble_on = False
        settings.save_settings()

    def _ChatTtsChanged(self, isChecked):
        settings.chat_tts = bool(isChecked)
        settings.save_settings()

    def _ChatSttChanged(self, isChecked):
        settings.chat_stt = bool(isChecked)
        # 关掉语音输入时，始终聆听没有意义，一并关闭
        if not isChecked and settings.chat_stt_always_listen:
            settings.chat_stt_always_listen = False
            self.ChatSttAlwaysCard.switchButton.blockSignals(True)
            self.ChatSttAlwaysCard.setChecked(False)
            self.ChatSttAlwaysCard.switchButton.blockSignals(False)
        settings.save_settings()

    def _ChatSttAlwaysChanged(self, isChecked):
        if isChecked and not settings.chat_stt:
            # 始终聆听依赖语音输入；未开启时给出提示并回滚
            InfoBar.warning(
                title=self.tr('需要开启语音输入'),
                content=self.tr('「始终聆听」需要先开启「Voice Input」'),
                orient=Qt.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self
            )
            self.ChatSttAlwaysCard.switchButton.blockSignals(True)
            self.ChatSttAlwaysCard.setChecked(False)
            self.ChatSttAlwaysCard.switchButton.blockSignals(False)
            settings.chat_stt_always_listen = False
            settings.save_settings()
            return
        settings.chat_stt_always_listen = bool(isChecked)
        settings.save_settings()

    def _ChatVoiceChanged(self, value):
        settings.chat_voice = value
        settings.save_settings()

    def _ChatModelChanged(self, value):
        settings.chat_model = value
        settings.save_settings()

    # ---- 模型列表动态刷新 ----
    def _populate_combo(self, card, models, current_value):
        combo = card.comboBox
        combo.blockSignals(True)
        combo.clear()
        for m in models:
            combo.addItem(m, userData=m)
        idx = combo.findText(current_value) if current_value else -1
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)


def get_latest_version():
    url = settings.RELEASE_API
    try:
        with urllib.request.urlopen(url) as response:
            data = json.loads(response.read())
            return True, data['tag_name']
    except Exception as e:
        return False, None

def compare_versions(local_version, github_version):
    # Remove 'v' prefix from version strings
    local_version = local_version.lstrip('v')
    github_version = github_version.lstrip('v')

    # Split version strings into their components
    local_parts = local_version.split('.')
    github_parts = github_version.split('.')

    # Convert version components to integers
    local_numbers = [int(part) for part in local_parts]
    github_numbers = [int(part) for part in github_parts]

    # Compare each component
    for local, github in zip(local_numbers, github_numbers):
        if local < github:
            return True  # User should update
        elif local > github:
            return False  # Local version is ahead

    # If all components are equal, check for additional components
    if len(local_numbers) < len(github_numbers):
        return True  # User should update
    else:
        return False  # Local version is up to date or ahead