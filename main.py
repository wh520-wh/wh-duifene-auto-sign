#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
对分易自动签到 - 异步高刷性能版
版本: 6.5.1-dark-ultimate-async
基于 6.5 原版，修复：密码登录 session 静默过期、空值崩溃、
布尔配置丢失、线程安全
"""
import configparser
import os
import sys
import re
import time
import random
import math
import threading
import queue
import base64
import gc
import ctypes
import socket
from datetime import datetime, time as datetime_time, timedelta
from urllib.parse import parse_qs, urlparse

import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox
import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("dark-blue")


def resource_path(relative_path):
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


# ==================== 系统级防多开 (双重保险) ====================
def check_single_instance():
    global _app_socket_lock
    try:
        _app_socket_lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _app_socket_lock.bind(('127.0.0.1', 48625))
    except socket.error:
        return False
    if sys.platform == "win32":
        mutex_name = "Duifenyi_AutoSign_Mutex_v6_Ultimate"
        mutex = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
        if ctypes.windll.kernel32.GetLastError() == 183:
            return False
        global _app_mutex
        _app_mutex = mutex
    return True


# ==================== 数据结构 ====================
class Course:
    id = '0'
    class_id = '0'
    flag = True
    check_list = []
    class_list = []


# ==================== 核心应用 ====================
class DuifenyiApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.host = "https://www.duifene.com"
        self.UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36')
        self.desktop_ua = self.UA  # 桌面端 UA，用于签到页请求
        self.mobile_ua = ('Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 '
                          '(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.40(0x1800282a) '
                          'NetType/WIFI Language/zh_CN ')
        self.filename = 'duifenyi.ini'
        self.config = configparser.ConfigParser()

        self.x = requests.Session()
        self.x.headers['User-Agent'] = self.mobile_ua
        self.x.verify = False
        self.x.headers['Accept'] = 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
        self.x.headers['Accept-Language'] = 'zh-CN,zh;q=0.9'
        self.req_timeout = 8
        # ━━━ 【新增】线程安全锁 ━━━
        self._session_lock = threading.Lock()
        self._ui_thread_id = threading.get_ident()
        self._ui_queue = queue.Queue()
        self._is_closing = False
        self._watch_task_running = False
        self._manual_schedule_pause = False
        self._saved_course_id = ""

        self.wx_guide = (
            "https://open.weixin.qq.com/connect/oauth2/authorize?appid=wx1b5650884f657981&redirect_uri="
            "https://www.duifene.com/_FileManage/PdfView.aspx?file=https%3A%2F%2Ffs.duifene.com%2Fres%2Fr2%2Fu6106199%2F"
            "%E5%AF%B9%E5%88%86%E6%98%93%E7%99%BB%E5%BD%95_876c9d439ca68ead389c.pdf&response_type=code&scope=snsapi_userinfo"
            "&connect_redirect=1#wechat_redirect")

        self.is_monitoring = False
        self._cached_uid = ""
        self.preset_lon_1 = "113.123456"
        self.preset_lat_1 = "23.654321"
        self.preset_lon_2 = ""
        self.preset_lat_2 = ""
        self.preset_lon_3 = ""
        self.preset_lat_3 = ""
        self.active_coord = "1"
        # ━━━ 坐标快照：在 UI 线程预存，供后台签到子线程安全读取（不再让子线程直接碰 Tk 控件）━━━
        self._active_coord_label = "1"
        self._active_lon = self.preset_lon_1
        self._active_lat = self.preset_lat_1
        self._coord_jitter_enabled = False  # 定位坐标随机抖动（默认关，≤5米）
        self.log_line_count = 0
        self.max_log_lines = 150
        self.log_mode = "simple"
        self._live_log_tag = "live_log_line"

        self.check_interval_min = 1.0
        self.check_interval_max = 3.0
        # 注:本字段为历史占位,运行期不再读取。真实延迟阈值以输入框/配置为准(空或0=立即),
        #     启动监听时定格到 _active_trigger_seconds;此初值 30 不代表默认行为。
        self.sign_trigger_seconds = 30  # (占位,未参与实际签到逻辑)
        self._countdown_logged = set()  # 避免同一秒数重复刷屏,只在剩余秒数变化时记录一次
        # 注:当前仅在启动时记录,尚未用于历史签到过滤(预留字段),不影响现有逻辑
        self._monitor_start_time = None  # 启动监听时的时间戳(预留:历史签到过滤)
        self._scheduled_signs = {}  # API无结束时间时的回退机制:{HFC_ID: (HFC_type, check_code, sign_type, target_ts)}
        self._qr_first_seen = {}  # 二维码签到条目首次进入 pending 的时间戳,用于延迟签到判断
        self._expired_signs = set()  # 已识别的过期/无效签到 ID 指纹,避免每轮轮询重复报错

        self.time_schedule_enabled = False
        self.waiting_for_schedule = False
        self.schedule_countdown_job = None

        self.clean_counter = 0

        self.title("对分易自动签到 v6.6")
        self.set_window_icon()
        self.geometry("1024x660")
        self.minsize(840, 560)
        self.resizable(True, True)
        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.configure(fg_color="#F7F5EF")
        self.setup_ui()
        self.after(40, self._drain_ui_queue)
        self.load_config()
        self._snapshot_coords()
        self.init()
        self.schedule_memory_cleanup()
        self.check_schedule_timer()
        self.after(100, self.update_timeline_display)
        self.after(400, self._check_optional_deps)  # 启动自检二维码依赖,缺失则显式告警

    def set_window_icon(self):
        icon_path = resource_path("logo.ico")
        if os.path.exists(icon_path):
            try:
                self.iconbitmap(icon_path)
            except Exception:
                pass

    def ui_call(self, callback, *args, **kwargs):
        if self._is_closing:
            return None
        if threading.get_ident() == self._ui_thread_id:
            return callback(*args, **kwargs)
        self._ui_queue.put((callback, args, kwargs))
        return None

    def ui_after(self, delay_ms, callback, *args):
        def schedule_after():
            if not self._is_closing:
                self.after(delay_ms, callback, *args)
        return self.ui_call(schedule_after)

    def _drain_ui_queue(self):
        if self._is_closing:
            return
        for _ in range(80):
            try:
                callback, args, kwargs = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                callback(*args, **kwargs)
            except Exception:
                pass
        self.after(40, self._drain_ui_queue)

    def on_closing(self):
        self._is_closing = True
        Course.flag = False
        self.is_monitoring = False
        if sys.platform == "win32":
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        try:
            self.x.close()
        except:
            pass
        self.destroy()
        os._exit(0)

    def setup_ui(self):
        # ========== 顶部栏 ==========
        top_bar = ctk.CTkFrame(self, height=58, fg_color="#1F1D1A", corner_radius=0)
        top_bar.pack(fill="x")
        top_bar.pack_propagate(False)

        title_frame = ctk.CTkFrame(top_bar, fg_color="transparent")
        title_frame.pack(side="left", padx=24, pady=9)
        title_row = ctk.CTkFrame(title_frame, fg_color="transparent")
        title_row.pack(anchor="w")
        ctk.CTkLabel(title_row, text="对分易自动签到",
                     font=ctk.CTkFont(family="Microsoft YaHei UI", size=18, weight="bold"),
                     text_color="#FFF7ED").pack(side="left", padx=(0, 10))
        ctk.CTkLabel(title_row, text="v6.6",
                     font=ctk.CTkFont(family="Consolas", size=11),
                     text_color="#F59E0B").pack(side="left", pady=(3, 0))
        status_frame = ctk.CTkFrame(top_bar, fg_color="#292524", corner_radius=18)
        status_frame.pack(side="right", padx=24, pady=12)
        self.status_frame = status_frame
        self.status_dot = ctk.CTkLabel(status_frame, text="●",
                                       font=ctk.CTkFont(size=14), text_color="#9CA3AF")
        self.status_dot.pack(side="left", padx=(12, 6), pady=4)
        self.status_text = ctk.CTkLabel(status_frame, text="未运行",
                                        font=ctk.CTkFont(family="Microsoft YaHei UI", size=12),
                                        text_color="#FAFAF9")
        self.status_text.pack(side="left", padx=(0, 12), pady=4)

        # 顶栏底部分隔线
        ctk.CTkFrame(self, height=1, fg_color="#E7E1D5", corner_radius=0).pack(fill="x")

        # ========== 主内容区 ==========
        main_container = ctk.CTkFrame(self, fg_color="#F7F5EF")
        main_container.pack(fill="both", expand=True, padx=12, pady=(10, 8))

        left_panel = ctk.CTkScrollableFrame(main_container, width=330, fg_color="#EFECE3",
                                            corner_radius=8,
                                            scrollbar_button_color="#E8E5E0",
                                            scrollbar_button_hover_color="#D4D0C8")
        left_panel.pack(side="left", fill="y", padx=(0, 6))

        card_kwargs = {"fg_color": "#FFFFFF", "corner_radius": 8, "border_width": 1, "border_color": "#E7E1D5"}
        label_font = ctk.CTkFont(family="Microsoft YaHei UI", size=13, weight="bold")

        # --- 登录模块 ---
        login_card = ctk.CTkFrame(left_panel, **card_kwargs)
        login_card.pack(fill="x", padx=6, pady=(4, 6))
        self.login_mode = ctk.CTkSegmentedButton(
            login_card, values=["微信登录", "账号登录"], command=self.switch_login_mode,
            fg_color="#E8E5E0", selected_color="#D97706", unselected_color="#8B7355",
            selected_hover_color="#B45309", unselected_hover_color="#7A6548",
            text_color="#FFFFFF")
        self.login_mode.pack(fill="x", padx=12, pady=12)
        self.login_mode.set("微信登录")
        self.login_frame = ctk.CTkFrame(login_card, fg_color="transparent")
        self.login_frame.pack(fill="x", padx=12, pady=(0, 12))

        self.wx_frame = ctk.CTkFrame(self.login_frame, fg_color="transparent")
        ctk.CTkButton(self.wx_frame, text="⧉ 复制链接", command=self.copy_wx_link,
                      height=34, fg_color="#F5F4F0", hover_color="#E8E5E0",
                      text_color="#1A1A1A", corner_radius=8).pack(fill="x", pady=(0, 6))
        self.link_entry = ctk.CTkEntry(self.wx_frame, placeholder_text="粘贴微信链接...",
                                       height=32, fg_color="#FFFFFF",
                                       border_color="#E8E5E0", corner_radius=6,
                                       text_color="#1A1A1A", placeholder_text_color="#9CA3AF")
        self.link_entry.pack(fill="x", pady=6)
        self.wx_login_btn = ctk.CTkButton(self.wx_frame, text="微信登录", command=self.login_link,
                      height=36, fg_color="#D97706", hover_color="#B45309",
                      corner_radius=8, text_color="#FFFFFF")
        self.wx_login_btn.pack(fill="x", pady=(6, 0))
        self.wx_frame.pack(fill="x")

        self.pwd_frame = ctk.CTkFrame(self.login_frame, fg_color="transparent")
        self.username_entry = ctk.CTkEntry(self.pwd_frame, placeholder_text="输入账号",
                                           height=32, fg_color="#FFFFFF",
                                           border_color="#E8E5E0", corner_radius=6,
                                           text_color="#1A1A1A", placeholder_text_color="#9CA3AF")
        self.username_entry.pack(fill="x", pady=4)
        self.password_entry = ctk.CTkEntry(self.pwd_frame, placeholder_text="输入密码", show="●",
                                           height=32, fg_color="#FFFFFF",
                                           border_color="#E8E5E0", corner_radius=6,
                                           text_color="#1A1A1A", placeholder_text_color="#9CA3AF")
        self.password_entry.pack(fill="x", pady=4)
        self.pwd_login_btn = ctk.CTkButton(self.pwd_frame, text="账号登录", command=self.login,
                      height=36, fg_color="#D97706", hover_color="#B45309",
                      corner_radius=8, text_color="#FFFFFF")
        self.pwd_login_btn.pack(fill="x", pady=(6, 0))

        # --- 课程选择 ---
        course_card = ctk.CTkFrame(left_panel, **card_kwargs)
        course_card.pack(fill="x", padx=6, pady=6)
        ctk.CTkLabel(course_card, text="课程选择", font=label_font,
                     text_color="#1A1A1A").pack(anchor="w", padx=12, pady=(12, 4))
        self.combo = ctk.CTkComboBox(course_card, values=["请先登录"],
                                     command=self.on_combo_change, height=34,
                                     fg_color="#FFFFFF", button_color="#E8E5E0",
                                     border_color="#E8E5E0", corner_radius=6, state="readonly",
                                     text_color="#1A1A1A", button_hover_color="#D4D0C8")
        self.combo.pack(fill="x", padx=12, pady=(0, 12))
        # --- 基础设置 ---
        basic_card = ctk.CTkFrame(left_panel, **card_kwargs)
        basic_card.pack(fill="x", padx=6, pady=6)
        ctk.CTkLabel(basic_card, text="基础设置", font=label_font,
                     text_color="#1A1A1A").pack(anchor="w", padx=12, pady=(12, 8))

        interval_frame = ctk.CTkFrame(basic_card, fg_color="transparent")
        interval_frame.pack(fill="x", padx=12, pady=(8, 4))
        ctk.CTkLabel(interval_frame, text="轮询模式:", text_color="#6B6560",
                     width=70, anchor="w").pack(side="left")
        self.interval_preset = ctk.CTkSegmentedButton(
            interval_frame, values=["快速", "标准", "省电"], command=self.set_interval_preset,
            height=26, corner_radius=6, fg_color="#E8E5E0", selected_color="#D97706",
            unselected_color="#8B7355", selected_hover_color="#B45309",
            unselected_hover_color="#7A6548", text_color="#FFFFFF",
            font=ctk.CTkFont(size=11))
        self.interval_preset.pack(fill="x", expand=True)
        self.interval_preset.set("标准")

        custom_interval_frame = ctk.CTkFrame(basic_card, fg_color="transparent")
        custom_interval_frame.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(custom_interval_frame, text="轮询间隔:", text_color="#6B6560",
                     width=70, anchor="w").pack(side="left")
        self.interval_min_entry = ctk.CTkEntry(custom_interval_frame, width=45, height=26,
                                               fg_color="#FFFFFF", border_color="#E8E5E0",
                                               corner_radius=6, text_color="#1A1A1A")
        self.interval_min_entry.pack(side="left", padx=2)
        self.interval_min_entry.insert(0, "1.0")
        self.interval_min_entry.bind("<KeyRelease>", lambda _event: self.refresh_overview())
        ctk.CTkLabel(custom_interval_frame, text="~", text_color="#6B6560").pack(side="left", padx=2)
        self.interval_max_entry = ctk.CTkEntry(custom_interval_frame, width=45, height=26,
                                               fg_color="#FFFFFF", border_color="#E8E5E0",
                                               corner_radius=6, text_color="#1A1A1A")
        self.interval_max_entry.pack(side="left", padx=2)
        self.interval_max_entry.insert(0, "3.0")
        self.interval_max_entry.bind("<KeyRelease>", lambda _event: self.refresh_overview())
        ctk.CTkLabel(custom_interval_frame, text="秒",
                     text_color="#9CA3AF", font=ctk.CTkFont(size=10)).pack(side="left", padx=(4, 0))

        # 延迟签到：检测到签到后延迟多少秒再触发
        trigger_frame = ctk.CTkFrame(basic_card, fg_color="transparent")
        trigger_frame.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(trigger_frame, text="延迟签到:", text_color="#6B6560",
                     width=70, anchor="w").pack(side="left")
        self.trigger_entry = ctk.CTkEntry(trigger_frame, width=45, height=26,
                                          fg_color="#FFFFFF", border_color="#E8E5E0",
                                          corner_radius=6, text_color="#1A1A1A",
                                          placeholder_text="0")
        self.trigger_entry.pack(side="left", padx=2)
        ctk.CTkLabel(trigger_frame, text="秒  检测到后延迟再签,空或0=立即",
                     text_color="#9CA3AF",
                     font=ctk.CTkFont(size=10)).pack(side="left", padx=(4, 0))

        # 定位签到坐标（支持自定义新增 / 命名 / 改名 / 删除，全部持久化保存）
        coord_header = ctk.CTkFrame(basic_card, fg_color="transparent")
        coord_header.pack(fill="x", padx=12, pady=(10, 0))
        ctk.CTkLabel(coord_header, text="定位坐标", text_color="#1A1A1A",
                     anchor="w", font=ctk.CTkFont(family="Microsoft YaHei UI",
                                                  size=12, weight="bold")).pack(side="left")
        ctk.CTkLabel(coord_header, text="选中圆点为生效坐标", text_color="#9CA3AF",
                     font=ctk.CTkFont(size=10)).pack(side="right")

        # 坐标数据权威来源:[{"name","lon","lat"}];UI 行控件镜像存于 coord_row_widgets
        self.coord_radio_var = ctk.StringVar(value="0")
        self.coords = [
            {"name": "坐标1", "lon": self.preset_lon_1, "lat": self.preset_lat_1},
            {"name": "坐标2", "lon": self.preset_lon_2, "lat": self.preset_lat_2},
            {"name": "坐标3", "lon": self.preset_lon_3, "lat": self.preset_lat_3},
        ]
        self.coord_row_widgets = []
        self.coord_list_frame = ctk.CTkFrame(basic_card, fg_color="transparent")
        self.coord_list_frame.pack(fill="x", padx=12, pady=(4, 2))

        self.add_coord_btn = ctk.CTkButton(
            basic_card, text="＋ 新增坐标", command=self.add_coord,
            height=28, fg_color="#F5F4F0", hover_color="#E8E5E0",
            text_color="#1A1A1A", corner_radius=6, font=ctk.CTkFont(size=11))
        self.add_coord_btn.pack(fill="x", padx=12, pady=(2, 6))

        jitter_frame = ctk.CTkFrame(basic_card, fg_color="transparent")
        jitter_frame.pack(fill="x", padx=12, pady=(2, 12))
        self.coord_jitter_switch = ctk.CTkSwitch(
            jitter_frame, text="定位坐标随机抖动（≤5米 · 默认关）",
            command=self._snapshot_coords,
            button_color="#D97706", progress_color="#B45309",
            text_color="#6B6560", font=ctk.CTkFont(size=11))
        self.coord_jitter_switch.pack(side="left")
        self.render_coord_list()

        self.advanced_card = ctk.CTkFrame(left_panel, **card_kwargs)
        self.advanced_card.pack(fill="x", padx=6, pady=6)

        advanced_header = ctk.CTkFrame(self.advanced_card, fg_color="transparent")
        advanced_header.pack(fill="x", padx=12, pady=10)
        self.advanced_toggle = ctk.CTkButton(advanced_header, text="定时设置 ▼",
                                             command=self.toggle_advanced,
                                             fg_color="transparent", hover_color="#F5F4F0",
                                             corner_radius=6, font=label_font,
                                             text_color="#1A1A1A", anchor="w")
        self.advanced_toggle.pack(fill="x")
        self.advanced_content = ctk.CTkFrame(self.advanced_card, fg_color="transparent")

        self.schedule_switch = ctk.CTkSwitch(self.advanced_content, text="启用定时监听",
                                             command=self.toggle_schedule,
                                             button_color="#D97706", progress_color="#B45309",
                                             text_color="#6B6560",
                                             font=ctk.CTkFont(size=11))
        self.schedule_switch.pack(anchor="w", padx=12, pady=(4, 8))

        time_frame = ctk.CTkFrame(self.advanced_content, fg_color="transparent")
        time_frame.pack(fill="x", padx=12, pady=(0, 4))

        start_frame = ctk.CTkFrame(time_frame, fg_color="transparent")
        start_frame.pack(fill="x", pady=4)
        ctk.CTkLabel(start_frame, text="开始:", text_color="#6B6560", width=40,
                     anchor="w").pack(side="left")
        self.start_hour = ctk.CTkComboBox(start_frame,
                                          values=[f"{i:02d}" for i in range(24)],
                                          width=55, height=26, fg_color="#FFFFFF",
                                          button_color="#E8E5E0", border_color="#E8E5E0",
                                          corner_radius=6, state="readonly",
                                          text_color="#1A1A1A",
                                          command=self.on_time_change)
        self.start_hour.set("08")
        self.start_hour.pack(side="left", padx=2)
        ctk.CTkLabel(start_frame, text=":", text_color="#6B6560").pack(side="left")
        self.start_minute = ctk.CTkComboBox(start_frame,
                                            values=["00", "15", "30", "45"],
                                            width=55, height=26, fg_color="#FFFFFF",
                                            button_color="#E8E5E0", border_color="#E8E5E0",
                                            corner_radius=6, state="readonly",
                                            text_color="#1A1A1A",
                                            command=self.on_time_change)
        self.start_minute.set("00")
        self.start_minute.pack(side="left", padx=(2, 0))

        end_frame = ctk.CTkFrame(time_frame, fg_color="transparent")
        end_frame.pack(fill="x", pady=4)
        ctk.CTkLabel(end_frame, text="结束:", text_color="#6B6560", width=40,
                     anchor="w").pack(side="left")
        self.end_hour = ctk.CTkComboBox(end_frame,
                                        values=[f"{i:02d}" for i in range(24)],
                                        width=55, height=26, fg_color="#FFFFFF",
                                        button_color="#E8E5E0", border_color="#E8E5E0",
                                        corner_radius=6, state="readonly",
                                        text_color="#1A1A1A",
                                        command=self.on_time_change)
        self.end_hour.set("18")
        self.end_hour.pack(side="left", padx=2)
        ctk.CTkLabel(end_frame, text=":", text_color="#6B6560").pack(side="left")
        self.end_minute = ctk.CTkComboBox(end_frame,
                                          values=["00", "15", "30", "45"],
                                          width=55, height=26, fg_color="#FFFFFF",
                                          button_color="#E8E5E0", border_color="#E8E5E0",
                                          corner_radius=6, state="readonly",
                                          text_color="#1A1A1A",
                                          command=self.on_time_change)
        self.end_minute.set("00")
        self.end_minute.pack(side="left", padx=(2, 0))

        self.timeline_container = ctk.CTkFrame(self.advanced_content, fg_color="#F5F4F0",
                                               corner_radius=8)
        self.timeline_container.pack(fill="x", padx=12, pady=(8, 12))
        self.timeline_canvas = tk.Canvas(self.timeline_container, height=28, bg="#F5F4F0",
                                         highlightthickness=0)
        self.timeline_canvas.pack(fill="x", padx=10, pady=8)
        self.timeline_canvas.bind("<Configure>", lambda e: self.draw_timeline())
        self.advanced_expanded = False

        # === 右侧日志面板 ===
        right_panel = ctk.CTkFrame(main_container, fg_color="#FFFFFF", corner_radius=8,
                                   border_width=1, border_color="#E7E1D5")
        right_panel.pack(side="right", fill="both", expand=True, padx=(8, 0))

        overview_card = ctk.CTkFrame(right_panel, fg_color="#F5F2EA", corner_radius=8,
                                     border_width=1, border_color="#E7E1D5")
        overview_card.pack(fill="x", padx=12, pady=(10, 6))

        overview_head = ctk.CTkFrame(overview_card, fg_color="transparent")
        overview_head.pack(fill="x", padx=12, pady=(8, 4))
        ctk.CTkLabel(overview_head, text="当前概览", font=label_font,
                     text_color="#1A1A1A").pack(side="left")
        overview_row_1 = ctk.CTkFrame(overview_card, fg_color="transparent")
        overview_row_1.pack(fill="x", padx=12, pady=(0, 4))
        self.course_value_label = self.create_overview_item(overview_row_1, "当前课程", "#D97706")
        self.mode_value_label = self.create_overview_item(overview_row_1, "登录方式", "#059669")

        overview_row_2 = ctk.CTkFrame(overview_card, fg_color="transparent")
        overview_row_2.pack(fill="x", padx=12, pady=(4, 0))
        self.interval_value_label = self.create_overview_item(overview_row_2, "监听区间", "#B45309")
        self.schedule_value_label = self.create_overview_item(overview_row_2, "定时窗口", "#7C3AED")

        self.summary_hint_label = ctk.CTkLabel(
            overview_card, text="", anchor="w", justify="left",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=11),
            fg_color="#F3F4F6", corner_radius=6,
            text_color="#374151")
        self.summary_hint_label.pack(fill="x", padx=12, pady=(8, 8))

        log_header = ctk.CTkFrame(right_panel, fg_color="transparent", height=44)
        log_header.pack(fill="x", padx=12, pady=(2, 2))
        header_left = ctk.CTkFrame(log_header, fg_color="transparent")
        header_left.pack(side="left", fill="y")
        ctk.CTkLabel(header_left, text="运行日志", font=label_font,
                     text_color="#1A1A1A").pack(side="left", padx=(0, 16))
        self.log_mode_btn = ctk.CTkSegmentedButton(
            header_left, values=["精简", "详细", "调试"], command=self.switch_log_mode,
            height=26, corner_radius=6, fg_color="#E8E5E0", selected_color="#D97706",
            unselected_color="#8B7355", text_color="#FFFFFF",
            font=ctk.CTkFont(size=11))
        self.log_mode_btn.pack(side="left")
        self.log_mode_btn.set("精简")
        ctk.CTkButton(log_header, text="⌦ 清空日志", command=self.clear_log,
                      width=60, height=26, fg_color="#F5F4F0", hover_color="#E8E5E0",
                      text_color="#1A1A1A", corner_radius=6,
                      font=ctk.CTkFont(size=11)).pack(side="right")

        self.text_box = ctk.CTkTextbox(right_panel, fg_color="#FBFAF7", text_color="#1A1A1A",
                                       font=("Consolas", 12), corner_radius=8,
                                       border_width=1, border_color="#E7E1D5",
                                       scrollbar_button_color="#E8E5E0",
                                       scrollbar_button_hover_color="#D4D0C8")
        self.text_box._textbox.configure(undo=False, maxundo=0)
        self.text_box.pack(fill="both", expand=True, padx=12, pady=(0, 10))

        self.text_box._textbox.tag_config("success", foreground="#059669",
                                          font=("Consolas", 12, "bold"))
        self.text_box._textbox.tag_config("error", foreground="#DC2626",
                                          font=("Consolas", 12, "bold"))
        self.text_box._textbox.tag_config("warning", foreground="#D97706",
                                          font=("Consolas", 12, "bold"))
        self.text_box._textbox.tag_config("info", foreground="#2563EB")
        self.text_box._textbox.tag_config("schedule", foreground="#7C3AED")
        self.text_box._textbox.tag_config("highlight", foreground="#D97706",
                                          font=("Consolas", 13, "bold"))
        self.text_box._textbox.tag_config("debug", foreground="#9CA3AF",
                                          font=("Consolas", 11))
        self.text_box._textbox.tag_config("detail", foreground="#6B6560")
        # 签到成功专属样式：大号粗体 + 醒目的红色 + 居中填充
        self.text_box._textbox.tag_config("celebration",
                                          foreground="#DC2626",
                                          font=("Consolas", 15, "bold"))
        self.text_box._textbox.tag_config("celebration_bar",
                                          foreground="#DC2626",
                                          font=("Consolas", 12, "bold"))
        # 倒计时数字专用：红色加粗
        self.text_box._textbox.tag_config("countdown_num",
                                          foreground="#DC2626",
                                          font=("Consolas", 13, "bold"))
        # 时间戳列与消息体的视觉分隔
        self.text_box._textbox.tag_config("ts_sep", foreground="#C4BFB6")

        # ========== 底部操作栏 ==========
        action_bar = ctk.CTkFrame(self, height=70, fg_color="#F7F5EF", corner_radius=0)
        action_bar.pack(fill="x", side="bottom")
        action_bar.pack_propagate(False)
        self.fab_frame = ctk.CTkFrame(action_bar, fg_color="#FFFFFF", corner_radius=8,
                                      border_width=1, border_color="#E7E1D5")
        self.fab_frame.pack(side="right", padx=24, pady=10)
        self.main_btn = ctk.CTkButton(self.fab_frame, text="▶ 开始监听", command=self.toggle_monitoring,
                                      width=180, height=46, corner_radius=8,
                                      fg_color="#D97706", hover_color="#B45309",
                                      text_color="#FFFFFF",
                                      font=ctk.CTkFont(family="Microsoft YaHei UI", size=15,
                                                       weight="bold"))
        self.main_btn.pack(side="left", padx=(6, 6), pady=6)
        self.save_btn = ctk.CTkButton(self.fab_frame, text="💾 保存配置", command=self.save_config,
                                      width=110, height=46, corner_radius=8,
                                      fg_color="#F5F4F0", hover_color="#E8E5E0",
                                      text_color="#1A1A1A",
                                      font=ctk.CTkFont(family="Microsoft YaHei UI", size=13))
        self.save_btn.pack(side="left", padx=(0, 6), pady=6)

        # 坐标行的 KeyRelease 同步在 render_coord_list() 内逐行绑定

        self.show_welcome()
        self.refresh_overview()

    # --- 全局24小时时间轴绘制 ---
    def on_time_change(self, _=None):
        self.draw_timeline()
        self.refresh_overview()

    def update_timeline_display(self):
        self.draw_timeline()
        self.after(30000, self.update_timeline_display)

    def draw_timeline(self):
        self.timeline_canvas.delete("all")
        width = self.timeline_canvas.winfo_width()
        if width <= 10:
            return
        y_center = 14
        self.timeline_canvas.create_line(0, y_center, width, y_center,
                                         fill="#E8E5E0", width=6, capstyle=tk.ROUND)
        st_h, st_m = int(self.start_hour.get()), int(self.start_minute.get())
        ed_h, ed_m = int(self.end_hour.get()), int(self.end_minute.get())
        st_frac = (st_h + st_m / 60.0) / 24.0
        ed_frac = (ed_h + ed_m / 60.0) / 24.0
        color = "#D97706" if self.schedule_switch.get() else "#9CA3AF"
        if st_frac <= ed_frac:
            x1 = st_frac * width
            x2 = ed_frac * width
            self.timeline_canvas.create_line(x1, y_center, x2, y_center,
                                             fill=color, width=6, capstyle=tk.ROUND)
        else:
            x1 = st_frac * width
            self.timeline_canvas.create_line(x1, y_center, width, y_center,
                                             fill=color, width=6, capstyle=tk.ROUND)
            x2 = ed_frac * width
            self.timeline_canvas.create_line(0, y_center, x2, y_center,
                                             fill=color, width=6, capstyle=tk.ROUND)
        now = datetime.now()
        now_frac = (now.hour + now.minute / 60.0 + now.second / 3600.0) / 24.0
        now_x = now_frac * width
        self.timeline_canvas.create_oval(now_x - 5, y_center - 5, now_x + 5, y_center + 5,
                                         fill="#F5F4F0", outline="#D97706", width=1.5)
        self.timeline_canvas.create_oval(now_x - 2, y_center - 2, now_x + 2, y_center + 2,
                                         fill="#D97706", outline="")
        time_text = now.strftime("%H:%M")
        txt_y = y_center - 12 if now_frac > 0.1 else y_center + 12
        self.timeline_canvas.create_text(now_x, txt_y, text=time_text,
                                         fill="#D97706", font=("Consolas", 9, "bold"))

    # --- UI 辅助方法 ---
    def create_overview_item(self, parent, title, accent_color):
        card = ctk.CTkFrame(parent, fg_color="#FFFFFF", corner_radius=8,
                            border_width=1, border_color="#EEECE8")
        card.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ctk.CTkLabel(card, text=title, anchor="w", text_color="#6B6560",
                     font=ctk.CTkFont(size=11)).pack(fill="x", padx=8, pady=(4, 1))
        value = ctk.CTkLabel(card, text="--", anchor="w", justify="left",
                             wraplength=170, text_color=accent_color,
                             font=ctk.CTkFont(family="Microsoft YaHei UI", size=12,
                                              weight="bold"))
        value.pack(fill="x", padx=8, pady=(0, 4))
        return value

    def refresh_overview(self, *_args):
        if not hasattr(self, "summary_hint_label"):
            return
        course_text = self.combo.get().strip() if hasattr(self, "combo") and self.combo.get() else "等待登录"
        if course_text == "请先登录":
            course_text = "等待登录"
        login_mode = self.login_mode.get().strip() if hasattr(self, "login_mode") and self.login_mode.get() else "微信登录"
        login_mode = self.normalize_login_mode(login_mode)
        if self.is_monitoring:
            # 监听中显示启动时锁定生效的轮询值,而非可能已被改动的输入框值
            interval_text = f"{self.check_interval_min} - {self.check_interval_max} 秒（已锁定）"
        else:
            interval_min = self.interval_min_entry.get().strip() if hasattr(self, "interval_min_entry") else "1.0"
            interval_max = self.interval_max_entry.get().strip() if hasattr(self, "interval_max_entry") else "3.0"
            interval_text = f"{interval_min or '1.0'} - {interval_max or '3.0'} 秒"
        start_text = f"{self.start_hour.get()}:{self.start_minute.get()}" if hasattr(self, "start_hour") else "--:--"
        end_text = f"{self.end_hour.get()}:{self.end_minute.get()}" if hasattr(self, "end_hour") else "--:--"
        schedule_text = f"{start_text} - {end_text}" if self.time_schedule_enabled else "未启用"
        # 概览显示生效坐标名称(而非内部索引)
        coord_name = "坐标1"
        try:
            if getattr(self, "coords", None):
                ci = int(self.coord_radio_var.get())
                if 0 <= ci < len(self.coords):
                    coord_name = self.coords[ci].get("name", f"坐标{ci + 1}")
        except (ValueError, TypeError):
            pass
        if self.is_monitoring:
            state_text = "正在监控当前课程"
        elif self.waiting_for_schedule:
            state_text = "等待进入定时窗口"
        elif self.time_schedule_enabled:
            state_text = "定时待命中"
        else:
            state_text = "可随时开始监听"
        self.course_value_label.configure(text=course_text)
        self.mode_value_label.configure(text=login_mode)
        self.interval_value_label.configure(text=interval_text)
        self.schedule_value_label.configure(text=schedule_text)
        styles = {
            "正在监控当前课程": ("#DCFCE7", "#166534"),
            "等待进入定时窗口": ("#FEF3C7", "#92400E"),
            "定时待命中": ("#E0E7FF", "#1D4ED8"),
            "本窗口已暂停": ("#FEE2E2", "#991B1B"),
            "可随时开始监听": ("#F3F4F6", "#374151"),
        }
        badge_bg, badge_fg = styles.get(state_text, ("#F3F4F6", "#374151"))
        self.summary_hint_label.configure(
            text=f"{state_text} ｜ {coord_name} ｜ 轮询 {interval_text}",
            fg_color=badge_bg, text_color=badge_fg)

    def switch_login_mode(self, value):
        value = self.normalize_login_mode(value)
        if value == "微信登录":
            self.pwd_frame.pack_forget()
            self.wx_frame.pack(fill="x")
        else:
            self.wx_frame.pack_forget()
            self.pwd_frame.pack(fill="x")
        self.refresh_overview()

    def toggle_advanced(self):
        if self.advanced_expanded:
            self.advanced_content.pack_forget()
            self.advanced_toggle.configure(text="定时设置 ▼")
            self.advanced_expanded = False
        else:
            self.advanced_content.pack(fill="x")
            self.advanced_toggle.configure(text="定时设置 ▲")
            self.advanced_expanded = True
            self.draw_timeline()
            self.refresh_overview()

    def set_interval_preset(self, value):
        presets = {"快速": (0.5, 1.0), "标准": (1.0, 3.0), "省电": (3.0, 5.0)}
        if value in presets:
            min_val, max_val = presets[value]
            self.interval_min_entry.delete(0, "end")
            self.interval_min_entry.insert(0, str(min_val))
            self.interval_max_entry.delete(0, "end")
            self.interval_max_entry.insert(0, str(max_val))
            self.check_interval_min, self.check_interval_max = min_val, max_val
            self.refresh_overview()

    def switch_log_mode(self, value):
        mode_map = {"精简": "simple", "详细": "detail", "调试": "debug"}
        self.log_mode = mode_map.get(value, "simple")

    def update_status(self, status, text=""):
        colors = {"idle": "#374151", "running": "#14532D", "waiting": "#92400E",
                  "scheduled": "#1E3A8A", "error": "#7F1D1D"}
        text_colors = {"idle": "#F9FAFB", "running": "#F0FDF4", "waiting": "#FFFBEB",
                       "scheduled": "#EFF6FF", "error": "#FEF2F2"}
        if status in colors:
            if hasattr(self, "status_frame"):
                self.status_frame.configure(fg_color=colors[status])
            self.status_dot.configure(text_color=text_colors[status])
            self.status_text.configure(text=text if text else status, text_color=text_colors[status])
        self.refresh_overview()

    def set_main_button_idle(self):
        self.main_btn.configure(text="▶ 开始监听", state="normal",
                                fg_color="#D97706", hover_color="#B45309")

    def set_main_button_starting(self):
        self.main_btn.configure(text="正在启动...", state="disabled",
                                fg_color="#A16207", hover_color="#854D0E")

    def set_main_button_running(self):
        self.main_btn.configure(text="■ 停止监控", state="normal",
                                fg_color="#DC2626", hover_color="#B91C1C")

    def show_welcome(self):
        self.log("info", "=" * 48)
        self.log("highlight", "对分易自动签到 v6.6")
        self.log("info", "=" * 48)

    def validate_number(self, value):
        return value == "" or value.isdigit()

    def normalize_login_mode(self, value):
        return value if value in ("微信登录", "账号登录") else "微信登录"

    def decode_saved_text(self, section, option):
        raw = self.config.get(section, option, fallback="")
        if not raw:
            return ""
        try:
            return base64.b64decode(raw.encode()).decode()
        except Exception:
            return raw

    def read_config_file(self):
        if not os.path.exists(self.filename):
            return False
        for encoding in ("utf-8", "gbk", None):
            try:
                self.config.clear()
                if encoding:
                    with open(self.filename, "r", encoding=encoding) as f:
                        self.config.read_file(f)
                else:
                    self.config.read(self.filename)
                return True
            except (UnicodeDecodeError, configparser.Error):
                continue
        self.config.clear()
        return False

    def write_config_file(self):
        with open(self.filename, "w", encoding="utf-8") as f:
            self.config.write(f)

    def extract_wechat_code(self, link):
        link = (link or "").strip()
        if not link:
            return None
        try:
            code = parse_qs(urlparse(link).query).get("code", [None])[0]
            if code:
                return code
        except Exception:
            pass
        match = re.search(r"[?&]code=([^&#]+)", link)
        return match.group(1) if match else None

    def get_cookie_string(self):
        cookie_dict = self.x.cookies.get_dict()
        if not cookie_dict:
            return "1=1"
        return "; ".join(f"{k}={v}" for k, v in cookie_dict.items())

    def start_schedule_countdown(self):
        if self._manual_schedule_pause and self.check_in_schedule_time():
            self.waiting_for_schedule = False
            self.update_status("scheduled", "本窗口已暂停")
            return
        self.waiting_for_schedule = True
        self.stop_schedule_countdown()
        self.update_schedule_countdown()

    def stop_schedule_countdown(self):
        if self.schedule_countdown_job is not None:
            try:
                self.after_cancel(self.schedule_countdown_job)
            except Exception:
                pass
            self.schedule_countdown_job = None

    # --- 日志渲染（与 6.5 完全一致） ---
    def clear_log(self):
        self.text_box.delete("1.0", "end")
        self.text_box._textbox.tag_remove(self._live_log_tag, "1.0", "end")
        self.log_line_count = 0
        self.log("info", "🗑️ 日志已清空")

    def log(self, level, msg):
        if self.log_mode == "simple":
            if level in ["debug", "detail"]:
                return
            # ━━━ 【修复】过滤串改为 "监控中"，匹配 "持续监控中" 和 "持续异步监控中" ━━━
            if "监控中" in msg or "检测到非本班" in msg:
                self.ui_call(self.update_last_line, msg, level)
                return
        elif self.log_mode == "detail":
            if level == "debug":
                return
        self.ui_call(self._render_log, level, msg)

    def _render_log(self, level, msg):
        # 先抹掉当前活动行(心跳/倒计时等),让普通日志成为最底部的历史行
        self._drop_live_line()
        if self.log_line_count >= self.max_log_lines:
            self.text_box._textbox.delete("1.0", "50.0")
            self.log_line_count -= 50
        timestamp = datetime.now().strftime("%H:%M:%S")
        # 时间戳列与消息体之间用浅色 │ 分隔，等宽对齐更整齐
        line = f"{timestamp}  │ {msg}\n"
        self.text_box.insert("end", line)
        self.log_line_count += 1
        if level in ["success", "error", "warning", "info", "schedule", "highlight", "debug", "detail"]:
            line_count = int(self.text_box.index('end-1c').split('.')[0])
            # 分隔符 │ 单独染色，时间戳列保持原色
            sep_start = f"{line_count - 1}.9"
            sep_end = f"{line_count - 1}.12"
            self.text_box._textbox.tag_add("ts_sep", sep_start, sep_end)
            self.text_box._textbox.tag_add(level, f"{line_count - 1}.0", f"{line_count}.0")
        self.text_box.see("end")

    def _drop_live_line(self):
        """删除当前"活动行"。心跳/排期等待/限流/倒计时统一用 _live_log_tag 标记,
        只占文本框最后一行、不计入 log_line_count。删除时按 tag 范围精确定位,
        因此绝不会误伤历史日志及其颜色 tag。"""
        try:
            ranges = self.text_box._textbox.tag_ranges(self._live_log_tag)
            if ranges:
                self.text_box._textbox.delete(ranges[0], ranges[-1])
                self.text_box._textbox.tag_remove(self._live_log_tag, "1.0", "end")
        except Exception:
            pass

    def _render_live_line(self, level, text):
        """渲染"活动行":原地替换最后一行,不累积、不计入历史行数。
        历史日志的颜色 tag 完全不受影响(被替换的只是 _live_log_tag 标记的那一行)。"""
        self._drop_live_line()
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"{timestamp}  │ {text}\n"
        self.text_box.insert("end", line)
        line_no = int(self.text_box.index('end-1c').split('.')[0]) - 1
        # 整行打 _live_log_tag 供下次定位删除;颜色用 level,分隔符 │ 单独染色
        self.text_box._textbox.tag_add(self._live_log_tag, f"{line_no}.0", f"{line_no + 1}.0")
        self.text_box._textbox.tag_add("ts_sep", f"{line_no}.9", f"{line_no}.12")
        if level in ("success", "error", "warning", "info", "schedule", "highlight", "debug", "detail"):
            self.text_box._textbox.tag_add(level, f"{line_no}.0", f"{line_no + 1}.0")
        self.text_box.see("end")

    def log_celebration(self, title, subtitle=""):
        """签到成功专用：上分隔线 + 居中标题 + 副标题 + 下分隔线"""
        # 后台签到线程会直接调用本方法,统一转发回 UI 线程执行,避免跨线程操作 Tk 崩溃
        if threading.get_ident() != self._ui_thread_id:
            self.ui_call(self.log_celebration, title, subtitle)
            return
        bar = "━" * 38
        self._drop_live_line()
        if self.log_line_count >= self.max_log_lines:
            self.text_box._textbox.delete("1.0", "50.0")
            self.log_line_count -= 50
        self.text_box.insert("end", f"  {bar}\n")
        self.text_box.insert("end", f"  ✨ {title} ✨\n")
        if subtitle:
            self.text_box.insert("end", f"     {subtitle}\n")
        self.text_box.insert("end", f"  {bar}\n")
        self.log_line_count += 4 if subtitle else 3
        end_idx = int(self.text_box.index('end-1c').split('.')[0])
        start = end_idx - (4 if subtitle else 3) + 1
        for ln in range(start, end_idx + 1):
            if ln == start + 1:
                self.text_box._textbox.tag_add("celebration", f"{ln}.0", f"{ln}.0 lineend")
            else:
                self.text_box._textbox.tag_add("celebration_bar", f"{ln}.0", f"{ln}.0 lineend")
        self.text_box.see("end")

    def update_last_line(self, text, level="info"):
        """心跳/排期等待/限流提示统一入口:作为"活动行"原地刷新,只占最后一行。
        不再全量重建文本框,因此历史日志的颜色 tag 不会被抹掉。"""
        try:
            self._render_live_line(level, text)
        except Exception:
            self._render_log(level, text)

    def log_countdown(self, seconds_left, sign_type):
        """倒计时活动行:⏰ 距离触发还剩 XX 秒,数字红色加粗,原地刷新只占一行。
        后台线程调用时统一转发回 UI 线程,避免跨线程操作 Tk。"""
        if threading.get_ident() != self._ui_thread_id:
            self.ui_call(self.log_countdown, seconds_left, sign_type)
            return
        try:
            self._drop_live_line()
            timestamp = datetime.now().strftime("%H:%M:%S")
            num_str = str(int(seconds_left))
            prefix = f"{timestamp}  │ ⏰ 距离触发还剩 "
            full_line = f"{prefix}{num_str} 秒 [{sign_type}]\n"
            self.text_box.insert("end", full_line)
            line_no = int(self.text_box.index('end-1c').split('.')[0]) - 1
            num_col = len(prefix)
            # 活动行整行打 _live_log_tag(供下次定位删除),不计入 log_line_count
            self.text_box._textbox.tag_add(self._live_log_tag, f"{line_no}.0", f"{line_no + 1}.0")
            self.text_box._textbox.tag_add("ts_sep", f"{line_no}.9", f"{line_no}.12")
            self.text_box._textbox.tag_add("info", f"{line_no}.0", f"{line_no + 1}.0")
            self.text_box._textbox.tag_add("countdown_num",
                                           f"{line_no}.{num_col}",
                                           f"{line_no}.{num_col + len(num_str)}")
            self.text_box.see("end")
        except Exception:
            self.log("info", f"⏰ 距离触发还剩 {int(seconds_left)} 秒 [{sign_type}]")

    # --- 调度器 ---
    def toggle_schedule(self):
        self.time_schedule_enabled = self.schedule_switch.get()
        self.draw_timeline()
        if self.time_schedule_enabled:
            self._manual_schedule_pause = False
            self.log("schedule", "📅 定时监听已开启")
            if self.check_in_schedule_time():
                if not self.is_monitoring and self.combo.get() != "请先登录":
                    self.stop_schedule_countdown()
                    self.go_sign()
                elif self.is_monitoring:
                    self.update_status("running", "监控中")
                else:
                    self.start_schedule_countdown()
            else:
                self.start_schedule_countdown()
                self.update_status("scheduled", "等待定时")
        else:
            self.log("warning", "⏸ 定时监听已关闭")
            self._manual_schedule_pause = False
            self.waiting_for_schedule = False
            self.stop_schedule_countdown()
            if not self.is_monitoring:
                self.update_status("idle", "未运行")
            self.refresh_overview()

    def check_in_schedule_time(self):
        if not self.time_schedule_enabled:
            return True
        now = datetime.now().time()
        start = datetime_time(int(self.start_hour.get()), int(self.start_minute.get()))
        end = datetime_time(int(self.end_hour.get()), int(self.end_minute.get()))
        if start <= end:
            return start <= now <= end
        return now >= start or now <= end

    def update_schedule_countdown(self):
        self.schedule_countdown_job = None
        if not self.waiting_for_schedule or not self.time_schedule_enabled:
            return
        now = datetime.now()
        next_start = now.replace(hour=int(self.start_hour.get()),
                                 minute=int(self.start_minute.get()),
                                 second=0, microsecond=0)
        if next_start <= now:
            next_start += timedelta(days=1)
        diff = next_start - now
        hours, remainder = divmod(diff.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        self.update_last_line(f"⏳ 距离下次监听还有 {hours:02d}:{minutes:02d}:{seconds:02d}", "schedule")
        self.update_status("scheduled", "等待定时")
        self.refresh_overview()
        self.schedule_countdown_job = self.after(1000, self.update_schedule_countdown)

    def check_schedule_timer(self):
        if self.time_schedule_enabled:
            in_schedule = self.check_in_schedule_time()
            if in_schedule and not self.is_monitoring:
                if self._manual_schedule_pause:
                    self.update_status("scheduled", "本窗口已暂停")
                elif self.waiting_for_schedule and self.combo.get() != "请先登录":
                    self.log("schedule", "📅 定时任务触发：自动开始监听")
                    self.stop_schedule_countdown()
                    self.waiting_for_schedule = False
                    self.go_sign()
            elif not in_schedule and self.is_monitoring:
                self.log("schedule", "📅 定时任务触发：已到结束时间")
                self.stop_monitoring()
                self.start_schedule_countdown()
            elif not in_schedule and self._manual_schedule_pause:
                self._manual_schedule_pause = False
                self.start_schedule_countdown()
        self.after(30000, self.check_schedule_timer)

    def schedule_memory_cleanup(self):
        self.clean_counter += 1
        if self.clean_counter % 30 == 0:
            gc.collect()
            if self.log_mode == "debug":
                self.log("debug", "🧹 [GC] 内存回收执行完毕")
            if len(Course.check_list) > 100:
                Course.check_list = Course.check_list[-50:]
        self.after(60000, self.schedule_memory_cleanup)

    # ==================== 核心网络与控制逻辑 ====================
    def toggle_monitoring(self):
        if not self.is_monitoring:
            try:
                self.check_interval_min = float(self.interval_min_entry.get())
                self.check_interval_max = float(self.interval_max_entry.get())
                if self.check_interval_min <= 0 or self.check_interval_max <= 0:
                    raise ValueError("间隔必须大于0")
                if self.check_interval_min > self.check_interval_max:
                    raise ValueError("最小值不能大于最大值")
            except ValueError as e:
                messagebox.showerror("错误", f"监听间隔设置错误: {e}")
                return
            self.go_sign()
        else:
            self.stop_monitoring(manual=True)

    def stop_monitoring(self, manual=False):
        Course.flag = False
        self.is_monitoring = False
        self._scheduled_signs.clear()
        self._countdown_logged.clear()
        self._expired_signs.clear()
        if manual and self.time_schedule_enabled and self.check_in_schedule_time():
            self._manual_schedule_pause = True
        if sys.platform == "win32":
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        self.set_main_button_idle()
        self.combo.configure(state="readonly")  # 解锁课程下拉框
        self.log("warning", "⏹ 监控已停止")
        if self.time_schedule_enabled:
            if self._manual_schedule_pause:
                self.stop_schedule_countdown()
                self.update_status("scheduled", "本窗口已暂停")
                self.log("schedule", "⏸ 已暂停本次定时窗口，下个窗口再自动监听")
            else:
                self.start_schedule_countdown()
        else:
            self.stop_schedule_countdown()
            self.update_status("idle", "未运行")
        self.refresh_overview()

    def save_config(self):
        try:
            self._sync_coords_from_widgets()  # 先把坐标行控件值写回 self.coords(权威数据)
            self.config['ACCOUNT'] = {
                'login_mode': self.login_mode.get(),
                'username': base64.b64encode(self.username_entry.get().encode()).decode(),
                'password': base64.b64encode(self.password_entry.get().encode()).decode(),
                'wx_link': base64.b64encode(self.link_entry.get().encode()).decode()
            }
            self.config['SETTINGS'] = {
                'interval_preset': self.interval_preset.get(),
                'interval_min': self.interval_min_entry.get(),
                'interval_max': self.interval_max_entry.get(),
                'time_schedule': "1" if self.schedule_switch.get() else "0",
                'start_time': f"{self.start_hour.get()}:{self.start_minute.get()}",
                'end_time': f"{self.end_hour.get()}:{self.end_minute.get()}",
                'log_mode': self.log_mode_btn.get(),
                # 旧键双写:前三个坐标 + 生效项写回旧字段,保证回退到旧版程序配置仍可用
                'active_coord': self._legacy_active_coord(),
                'coord_jitter': "1" if self.coord_jitter_switch.get() else "0",
                'lon_1': self._coord_get(0, "lon"),
                'lat_1': self._coord_get(0, "lat"),
                'lon_2': self._coord_get(1, "lon"),
                'lat_2': self._coord_get(1, "lat"),
                'lon_3': self._coord_get(2, "lon"),
                'lat_3': self._coord_get(2, "lat"),
                'selected_course_id': str(Course.id or ""),
                'selected_course_name': self.combo.get().strip() if self.combo.get() else "",
                'sign_trigger_seconds': self._read_trigger_seconds()
            }
            self._write_coords_section()  # 新的多坐标段(权威来源)
            self.write_config_file()
            self.log("success", "✅ 所有配置参数已全量保存")
            self.save_btn.configure(text="✓ 已保存", fg_color="#059669")
            self.after(1500, lambda: self.save_btn.configure(text="💾 保存配置", fg_color="#F5F4F0"))
        except Exception as e:
            self.log("error", f"❌ 保存配置失败: {e}")

    def _coord_get(self, i, key):
        return self.coords[i].get(key, "") if 0 <= i < len(self.coords) else ""

    def _legacy_active_coord(self):
        """旧版 active_coord 语义为 "1"/"2"/"3";新 active_index<3 时映射,否则回退 "1"。"""
        try:
            idx = int(self.coord_radio_var.get())
        except (ValueError, TypeError):
            idx = 0
        return str(idx + 1) if 0 <= idx < 3 else "1"

    def _write_coords_section(self):
        """把全部坐标写入 [COORDS] 段。名称用 base64(避免中文/特殊字符干扰 ini 取值)。"""
        try:
            idx = int(self.coord_radio_var.get())
        except (ValueError, TypeError):
            idx = 0
        section = {"count": str(len(self.coords)), "active_index": str(max(0, idx))}
        for i, c in enumerate(self.coords):
            section[f"name_{i}"] = base64.b64encode(c.get("name", "").encode()).decode()
            section[f"lon_{i}"] = c.get("lon", "")
            section[f"lat_{i}"] = c.get("lat", "")
        self.config['COORDS'] = section

    def _read_trigger_seconds(self):
        return str(self._get_live_trigger_seconds())

    def _get_live_trigger_seconds(self):
        """实时读取阈值:空输入或非数字都按 0 处理(直接签到)"""
        try:
            raw = self.trigger_entry.get().strip()
        except (AttributeError, Exception):
            return 0
        if not raw:
            return 0
        try:
            v = int(raw)
        except (ValueError, TypeError):
            return 0
        return max(0, min(v, 3600))

    def load_config(self):
        try:
            if self.read_config_file():
                if 'ACCOUNT' in self.config:
                    l_mode = self.normalize_login_mode(
                        self.config.get('ACCOUNT', 'login_mode', fallback='微信登录'))
                    self.login_mode.set(l_mode)
                    self.switch_login_mode(l_mode)
                    self.username_entry.insert(0, self.decode_saved_text('ACCOUNT', 'username'))
                    self.password_entry.insert(0, self.decode_saved_text('ACCOUNT', 'password'))
                    self.link_entry.insert(0, self.decode_saved_text('ACCOUNT', 'wx_link'))
                if 'SETTINGS' in self.config:
                    self.interval_preset.set(
                        self.config.get('SETTINGS', 'interval_preset', fallback='标准'))
                    self.interval_min_entry.delete(0, "end")
                    self.interval_min_entry.insert(
                        0, self.config.get('SETTINGS', 'interval_min', fallback='1.0'))
                    self.interval_max_entry.delete(0, "end")
                    self.interval_max_entry.insert(
                        0, self.config.get('SETTINGS', 'interval_max', fallback='3.0'))
                    try:
                        self.check_interval_min = float(self.interval_min_entry.get())
                        self.check_interval_max = float(self.interval_max_entry.get())
                    except (ValueError, TypeError):
                        # 坏值不中断后续配置加载(坐标/课程/延迟阈值仍能正常读入)
                        self.check_interval_min, self.check_interval_max = 1.0, 3.0
                    if self.config.get('SETTINGS', 'time_schedule', fallback='0') in ('1', 'True', 'true'):
                        self.schedule_switch.select()
                        self.time_schedule_enabled = True
                    try:
                        st_h, st_m = self.config.get(
                            'SETTINGS', 'start_time', fallback='08:00').split(':')
                        ed_h, ed_m = self.config.get(
                            'SETTINGS', 'end_time', fallback='18:00').split(':')
                        self.start_hour.set(st_h.zfill(2))
                        self.start_minute.set(st_m.zfill(2))
                        self.end_hour.set(ed_h.zfill(2))
                        self.end_minute.set(ed_m.zfill(2))
                    except:
                        pass
                    log_m = self.config.get('SETTINGS', 'log_mode', fallback='精简')
                    self.log_mode_btn.set(log_m)
                    self.switch_log_mode(log_m)
                    if self.config.get('SETTINGS', 'coord_jitter', fallback='0') in ('1', 'True', 'true'):
                        self.coord_jitter_switch.select()
                    # 坐标列表的加载/迁移统一放到方法末尾的 _load_coords_from_config()
                    self._saved_course_id = self.config.get('SETTINGS', 'selected_course_id', fallback='')
                    try:
                        loaded_trigger = int(self.config.get('SETTINGS', 'sign_trigger_seconds', fallback='0'))
                    except (ValueError, TypeError):
                        loaded_trigger = 0
                    loaded_trigger = max(0, min(loaded_trigger, 3600))
                    self.sign_trigger_seconds = loaded_trigger
                    if hasattr(self, 'trigger_entry') and loaded_trigger > 0:
                        # 0 或空都保持输入框为空,代表"立即签到"
                        self.trigger_entry.delete(0, "end")
                        self.trigger_entry.insert(0, str(loaded_trigger))
                # 坐标:优先读新 [COORDS] 段,否则从旧 lon_1~lat_3/active_coord 迁移
                try:
                    self._load_coords_from_config()
                except Exception:
                    pass
                self.refresh_overview()
        except Exception as e:
            if self.log_mode == "debug":
                self.log("debug", f"load_config 异常: {type(e).__name__}: {e}")

    def _load_coords_from_config(self):
        """加载坐标:优先 [COORDS] 段;若无则从旧 SETTINGS(lon_1~lat_3/active_coord)迁移。
        迁移时空值回退到内置预设,保持与旧版默认一致。"""
        coords = []
        active_index = 0
        if self.config.has_section('COORDS'):
            try:
                count = int(self.config.get('COORDS', 'count', fallback='0'))
            except (ValueError, TypeError):
                count = 0
            for i in range(max(0, count)):
                name_raw = self.config.get('COORDS', f'name_{i}', fallback='')
                try:
                    name = base64.b64decode(name_raw.encode()).decode() if name_raw else ''
                except Exception:
                    name = name_raw
                coords.append({
                    "name": name or f"坐标{i + 1}",
                    "lon": self.config.get('COORDS', f'lon_{i}', fallback=''),
                    "lat": self.config.get('COORDS', f'lat_{i}', fallback=''),
                })
            try:
                active_index = int(self.config.get('COORDS', 'active_index', fallback='0'))
            except (ValueError, TypeError):
                active_index = 0
        if not coords:
            # 迁移旧三坐标
            lon1 = self.config.get('SETTINGS', 'lon_1', fallback='') or self.preset_lon_1
            lat1 = self.config.get('SETTINGS', 'lat_1', fallback='') or self.preset_lat_1
            lon2 = self.config.get('SETTINGS', 'lon_2', fallback='') or self.preset_lon_2
            lat2 = self.config.get('SETTINGS', 'lat_2', fallback='') or self.preset_lat_2
            lon3 = self.config.get('SETTINGS', 'lon_3', fallback='') or self.preset_lon_3
            lat3 = self.config.get('SETTINGS', 'lat_3', fallback='') or self.preset_lat_3
            coords = [
                {"name": "坐标1", "lon": lon1, "lat": lat1},
                {"name": "坐标2", "lon": lon2, "lat": lat2},
                {"name": "坐标3", "lon": lon3, "lat": lat3},
            ]
            ac = self.config.get('SETTINGS', 'active_coord', fallback='1')
            try:
                active_index = max(0, min(int(ac) - 1, 2))
            except (ValueError, TypeError):
                active_index = 0
        if not coords:
            return
        if active_index < 0 or active_index >= len(coords):
            active_index = 0
        self.coords = coords
        self.coord_radio_var.set(str(active_index))
        self.render_coord_list()

    def copy_wx_link(self):
        self.clipboard_clear()
        self.clipboard_append(self.wx_guide)
        self.log("info", "📋 已复制微信提取链接")

    def login_link(self):
        link = self.link_entry.get()
        code = self.extract_wechat_code(link)
        if not code:
            messagebox.showerror("错误", "微信链接有误，请重新复制粘贴")
            return
        self.wx_login_btn.configure(state="disabled", text="登录中...")
        threading.Thread(target=self._login_link_task, args=(code,), daemon=True).start()

    def _login_link_task(self, code):
        """微信链接登录的网络部分，在后台线程执行，避免卡死 UI"""
        self.x.cookies.clear()
        try:
            with self._session_lock:
                self.x.get(url=self.host + f"/P.aspx?authtype=1&code={code}&state=1",
                           timeout=self.req_timeout)
            self.get_class_list()
            self.config['INFO'] = {'cookie': self.get_cookie_string()}
            self.write_config_file()
            self.log("success", "✅ 微信链接登录成功")
            self.ui_call(self.refresh_overview)
        except Exception as e:
            self.log("error", f"❌ 网络请求异常: {e}")
        finally:
            self.ui_call(self.wx_login_btn.configure, state="normal", text="微信登录")

    def login(self):
        username = self.username_entry.get()
        password = self.password_entry.get()
        self.pwd_login_btn.configure(state="disabled", text="登录中...")
        threading.Thread(target=self._login_task, args=(username, password), daemon=True).start()

    def _login_task(self, username, password):
        """账号密码登录的网络部分，在后台线程执行，避免卡死 UI"""
        headers = {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                   "Referer": f"{self.host}/AppGate.aspx"}
        params = f'action=loginmb&loginname={username}&password={password}'
        self.x.cookies.clear()
        try:
            with self._session_lock:
                self.x.get(self.host, timeout=self.req_timeout)
                _r = self.x.post(url=self.host + "/AppCode/LoginInfo.ashx",
                                 data=params, headers=headers, timeout=self.req_timeout)
            if _r.status_code == 200 and _r.json().get("msgbox") == "登录成功":
                self.log("success", "✅ 账号登录成功")
                # 模拟微信OAuth重定向链，建立完整ASP.NET session上下文
                with self._session_lock:
                    self.x.get(url=self.host + "/_UserCenter/MB/index.aspx",
                               headers={"Referer": self.host + "/AppGate.aspx"},
                               timeout=self.req_timeout)
                self.get_class_list()
                self.config['INFO'] = {'cookie': self.get_cookie_string()}
                self.write_config_file()
                self.ui_call(self.refresh_overview)
            else:
                self.log("error", f"❌ 登录失败: {_r.json().get('msgbox', '未知错误')}")
        except Exception as e:
            self.log("error", f"❌ 网络请求异常: {e}")
        finally:
            self.ui_call(self.pwd_login_btn.configure, state="normal", text="账号登录")

    # ==================== 心跳验证（与 build.py 一致） ====================
    def is_login(self):
        """与 build.py 的 is_login() 逐行对齐，用 POST + data 传参"""
        headers = {
            "Referer": f"{self.host}/_UserCenter/MB/index.aspx",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
        }
        try:
            with self._session_lock:
                _r = self.x.post(url=f"{self.host}/AppCode/LoginInfo.ashx",
                                 data="Action=checklogin",
                                 headers=headers,
                                 timeout=self.req_timeout)
            if _r.status_code == 200:
                data = _r.json()
                if data.get("msg") == "1":
                    return True
                else:
                    self.log("error", f"🔒 登录状态已失效，session验证未通过")
                    return False
            else:
                self.log("error", f"⚠️ 心跳请求异常 HTTP {_r.status_code}")
                return None
        except requests.exceptions.Timeout:
            # 超时不判定为失效，可能是网络抖动
            return None
        except Exception as e:
            if self.log_mode == "debug":
                self.log("debug", f"心跳底层异常: {type(e).__name__}: {e}")
            return None

    # ==================== 核心异步化抓包系统 ====================
    def _async_watch_task(self):
        try:
            if not Course.flag or not self.is_monitoring:
                return

            login_ok = self.is_login()
            if self.log_mode == "debug":
                self.log("debug", f"心跳结果: {login_ok}")
            if login_ok is False:
                self.ui_call(self._handle_session_expired)
                return

            try:
                if not self._cached_uid:
                    self._cached_uid = self.get_user_id() or ""
                if not self._cached_uid:
                    self._schedule_next_watch()
                    return
                with self._session_lock:
                    _r = self.x.post(
                        url=f"{self.host}/_CheckIn/MBCount.ashx",
                        data=f"action=getstudentinlogbyday&classid={Course.class_id}&studentid={self._cached_uid}",
                        headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
                        timeout=self.req_timeout)
                if _r.status_code == 200:
                    self._process_watch_result(_r.json())
                else:
                    self._schedule_next_watch()
            except requests.exceptions.Timeout:
                self.log("warning", "⚠️ 服务器握手超时，进入重试")
                self._schedule_next_watch()
            except Exception as e:
                self.log("warning", f"⚠️ 监控请求异常: {e}")
                self._schedule_next_watch()
        finally:
            self._watch_task_running = False

    def _handle_session_expired(self):
        """session 失效时自动停止监控并提示"""
        self.x.cookies.clear()
        self.stop_monitoring()
        self.log("error", "🔒 登录状态已失效，监控已自动停止，请重新登录账号")
        self.update_status("error", "登录已失效")

    def _extract_remaining_seconds(self, item):
        """从签到条目中解析结束时间,返回剩余秒数(>=0);解析不到返回 None(走原签到逻辑)"""
        candidates = [
            item.get("EndTime"), item.get("endtime"),
            item.get("SignEndTime"), item.get("SignEnd"),
            item.get("CheckEndTime"), item.get("CheckEnd"),
            item.get("EndDate"), item.get("EndDateTime"),
            item.get("EndDateStr"), item.get("SignEndDate"),
            item.get("LastSignTime"), item.get("SignEndDateTime"),
            item.get("ApplyLimitDate"),
        ]
        for raw in candidates:
            if not raw:
                continue
            end_dt = self._parse_end_time(raw)
            if end_dt:
                return max(0, (end_dt - datetime.now()).total_seconds())
        return None

    def _parse_end_time(self, raw):
        """尝试把各种格式的结束时间字符串/datetime 解析为 datetime"""
        if isinstance(raw, datetime):
            return raw
        s = str(raw).strip()
        if not s:
            return None
        m = re.match(r"^/Date\((-?\d+)([+-]\d+)?\)/?$", s)
        if m:
            try:
                return datetime.fromtimestamp(int(m.group(1)) / 1000.0)
            except (ValueError, OSError):
                return None
        if s.isdigit():
            try:
                return datetime.fromtimestamp(int(s))
            except (ValueError, OSError):
                return None
        for fmt in (
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y/%m/%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d %H:%M",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        return None

    def get_checkin_progress(self, ciid):
        """返回 (已签人数, 总人数, 缺勤, 迟到, 请假)，失败返回 None"""
        try:
            with self._session_lock:
                _r = self.x.post(
                    url=f"{self.host}/_CheckIn/MBCount.ashx",
                    data=f"action=getcheckintotalbyciid&ciid={ciid}&t=cking",
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                        "Referer": f"{self.host}/_CheckIn/MB/TeachCheckIn.aspx",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    timeout=self.req_timeout)
            if _r.status_code == 200:
                data = _r.json()
                return (int(data.get("OutNumber", 0)), int(data.get("TotalNumber", 0)),
                        int(data.get("AbsenceNumber", 0)), int(data.get("LateNumber", 0)),
                        int(data.get("LeaveNumber", 0)))
        except Exception as e:
            if self.log_mode == "debug":
                self.log("debug", f"get_checkin_progress 异常: {type(e).__name__}: {e}")
        return None

    def _process_watch_result(self, data):
        if not self.is_monitoring:
            return

        now_str = datetime.now().strftime('%H:%M:%S')
        if self._scheduled_signs:
            heartbeat = f"⏳ 准备签到中…  【{now_str}】"
        else:
            heartbeat = f"👀 持续异步监控中  【{now_str}】"
        self.ui_call(self.update_last_line, heartbeat, "detail")

        if self.log_mode == "debug":
            self.log("debug", f"API原始响应: msg={data.get('msg')}({type(data.get('msg')).__name__}), rows数量={len(data.get('rows', []))}")

        # 0) 先处理上一轮已排期的签到(到点了就真正发起)
        self._flush_scheduled_signs()

        rows = data.get("rows", [])
        has_data = str(data.get("msg", "")) == "1" and bool(rows)

        pending = []
        if has_data:
            candidates = []
            for r in rows:
                if str(r.get("StatusID", "")) != "2":
                    continue
                hid = r.get("ID", "")
                if not hid or hid in Course.check_list or hid in self._expired_signs:
                    continue
                candidates.append(r)
            # 业务铁律:同时只有一个签到活跃,其余检测到的都是过期残留。
            # 取 CreaterDate 最新的一条为活跃签到,其余静默指纹掉,不进倒计时。
            if len(candidates) > 1:
                def _created_ts(row):
                    dt = self._parse_end_time(row.get("CreaterDate"))
                    return dt.timestamp() if dt else 0
                candidates.sort(key=_created_ts, reverse=True)
                for stale in candidates[1:]:
                    sid = stale.get("ID", "")
                    if sid:
                        self._expired_signs.add(sid)
                        if sid not in Course.check_list:
                            Course.check_list.append(sid)
                    if self.log_mode == "debug":
                        self.log("debug", f"过期残留静默跳过: ID={sid} CreaterDate={stale.get('CreaterDate')}")
                candidates = candidates[:1]
            pending = candidates

        # 记录/清理二维码签到的"首次被检测"时间,供探针侧阈值判断使用
        pending_ids = {item.get("ID", "") for item in pending}
        now_ts = datetime.now().timestamp()
        for item in pending:
            hid = item.get("ID", "")
            if str(item.get("CheckInType", "")) == "2" and hid and hid not in self._qr_first_seen:
                self._qr_first_seen[hid] = now_ts
        if self._qr_first_seen:
            self._qr_first_seen = {hid: ts for hid, ts in self._qr_first_seen.items() if hid in pending_ids}

        has_qr_pending = any(str(r.get("CheckInType", "")) == "2" for r in pending)

        if pending:
            type_priority = {'1': 0, '2': 1, '3': 2}
            pending.sort(key=lambda r: type_priority.get(str(r.get("CheckInType", "")), 9))

            for item in pending:
                HFC_ID = item.get("ID", "")
                HFC_type = str(item.get("CheckInType", ""))
                check_code = item.get("CheckInCode", "")
                sign_type = {'1': '签到码', '2': '二维码', '3': '定位'}.get(HFC_type, '未知')

                if HFC_ID in Course.check_list:
                    continue

                # 阈值是点击开始监听时锁定的快照,运行期不重新读取输入框
                threshold = getattr(self, '_active_trigger_seconds', 0)
                remaining = self._extract_remaining_seconds(item)

                if self.log_mode == "debug":
                    self.log("debug", f"缺勤: ID={HFC_ID} type={HFC_type} code={check_code!r} "
                                      f"CreaterDate={item.get('CreaterDate')!r} "
                                      f"ApplyLimitDate={item.get('ApplyLimitDate')!r} "
                                      f"remaining={remaining}")

                if remaining is not None and remaining <= 0:
                    Course.check_list.append(HFC_ID)
                    self._expired_signs.add(HFC_ID)
                    self.log("detail", f"⏹️ {sign_type}签到已结束，跳过")
                    continue

                # 二维码预检:发签到请求前先确认 QR 码确实存在(不发签到动作)
                # 没数据 → 视为已过期,完全静默指纹掉,不打印 📢 / 🔲
                if HFC_type == '2' and not self._has_active_qr_data():
                    Course.check_list.append(HFC_ID)
                    self._expired_signs.add(HFC_ID)
                    continue

                if threshold > 0:
                    # 阈值语义:检测到签到后延迟 N 秒再签(模拟人工操作延迟)
                    self._schedule_sign_later(HFC_ID, HFC_type, check_code, sign_type, threshold)
                    continue

                # 走到这里 = 真的要签了
                self._scheduled_signs.pop(HFC_ID, None)
                self._countdown_logged.clear()
                self._do_sign_with_log(HFC_ID, HFC_type, check_code, sign_type, pending)

        if not has_qr_pending and not self._qr_probe_cooldown():
            threading.Thread(target=self._probe_qr_sign, daemon=True).start()
            return

        self._schedule_next_watch()

    def _schedule_sign_later(self, HFC_ID, HFC_type, check_code, sign_type, threshold):
        """检测到签到后延迟 N 秒再触发,模拟人工操作延迟。
        target_ts 一旦锁定就不再重置,避免轮询间隔把它无限推迟。"""
        prev = self._scheduled_signs.get(HFC_ID)
        if prev is None:
            target_ts = datetime.now().timestamp() + threshold
            self._scheduled_signs[HFC_ID] = (HFC_type, check_code, sign_type, target_ts)
            self.log("schedule", f"🕒 检测到{sign_type}签到,将在 {threshold} 秒后触发")
        else:
            _, _, _, target_ts = prev
        # 实时显示剩余秒数,让用户看到当前还在等待
        wait_left = max(0, int(round(target_ts - datetime.now().timestamp())))
        bucket = wait_left
        if bucket not in self._countdown_logged:
            self._countdown_logged.add(bucket)
            self.log_countdown(wait_left, f"{sign_type}")

    def _flush_scheduled_signs(self):
        """到点的排期签到真正执行。签到成功后清掉剩余排期;失败的静默指纹后继续尝试下一个。"""
        if not self._scheduled_signs:
            return
        now_ts = datetime.now().timestamp()
        if self.log_mode == "debug":
            for hid, (_, _, _, tt) in self._scheduled_signs.items():
                self.log("debug", f"flush检查: ID={hid} now={now_ts:.0f} target={tt:.0f} diff={now_ts-tt:.1f}s")
        ready_ids = [hid for hid, (_, _, _, tt) in self._scheduled_signs.items() if now_ts >= tt]
        if not ready_ids:
            return
        for hid in ready_ids:
            entry = self._scheduled_signs.pop(hid, None)
            if not entry:
                continue
            HFC_type, check_code, sign_type, _tt = entry
            if hid in Course.check_list:
                continue
            if self.log_mode == "debug":
                self.log("debug", f"⏰ 排期到期,执行{sign_type}签到")
            result = self._do_sign_with_log(hid, HFC_type, check_code, sign_type, pending=None)
            if result == "ratelimit":
                # 限流:保留原 entry(含原 target_ts)回插排期,延迟倒计时不从头重置,
                #       下一轮到点继续尝试。其余限流退避流程保持现状。
                self._scheduled_signs[hid] = entry
                continue
            if hid in Course.check_list and hid not in self._expired_signs:
                # 签到成功,清掉剩余排期与倒计时去重缓存
                self._scheduled_signs.clear()
                self._countdown_logged.clear()
                return
        # 所有 ready 的都失败/被指纹了,继续正常轮询

    def _do_sign_with_log(self, HFC_ID, HFC_type, check_code, sign_type, pending):
        """统一的签到入口,负责日志、成功/失败/限流的分支处理。
        设计原则:成功才庆祝,失败(过期/无效码)完全静默指纹掉,用户日志里只看到成功。
        返回 "success"/"ratelimit"/"expired"/"error",供排期路径决定是否回插。"""
        try:
            status = self._do_sign(HFC_type, check_code, HFC_ID)
            if status == True:
                Course.check_list.append(HFC_ID)
                if HFC_type == '2' and pending is not None:
                    for other in pending:
                        if str(other.get("CheckInType", "")) == "2":
                            oid = other.get("ID", "")
                            if oid and oid not in Course.check_list:
                                Course.check_list.append(oid)
                return "success"
            elif status == "ratelimit":
                self.ui_call(self.update_last_line, "⏳ 频率限制，6秒后重试", "detail")
                self.ui_after(6000, self.watching_sign)
                return "ratelimit"
            else:
                # False / "expired" / 其它:静默指纹,不打日志,下一轮不再处理
                Course.check_list.append(HFC_ID)
                self._expired_signs.add(HFC_ID)
                return "expired"
        except Exception as e:
            if self.log_mode == "debug":
                self.log("debug", f"⚠️ 签到处理异常: {e}")
            Course.check_list.append(HFC_ID)
            self._expired_signs.add(HFC_ID)
            return "error"

    def _qr_probe_cooldown(self):
        """每5次轮询探测一次二维码，避免频繁请求"""
        if not hasattr(self, '_qr_probe_counter'):
            self._qr_probe_counter = 0
        self._qr_probe_counter += 1
        if self._qr_probe_counter >= 5:
            self._qr_probe_counter = 0
            return False
        return True

    def _probe_qr_sign(self):
        """主动探测：先确认有活跃 QR 签到实例，再调 getcodeimage 获取 state"""
        # 停止监控后(手动停止/定时结束)在途探测线程不得再发起签到
        if not (self.is_monitoring and Course.flag):
            return
        try:
            # 第一步：用轮询接口确认当前确实有活跃的 QR 签到（type=2, StatusID=2）
            # 注:此 gate 要求 rows 里能看到 QR 条目;而能进入探测分支的前提恰是主轮询 rows 中
            #     没有 QR,故该 gate 会让"纯靠图片接口兜底"的场景几乎失效。在缺乏 2026 机制的
            #     真实抓包验证前保守保留现状(改动会触及签到探测的请求行为)。
            with self._session_lock:
                _r = self.x.post(
                    url=f"{self.host}/_CheckIn/MBCount.ashx",
                    data=f"action=getstudentinlogbyday&classid={Course.class_id}&studentid={self._cached_uid}",
                    headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
                    timeout=self.req_timeout)
            if _r.status_code != 200:
                self._schedule_next_watch()
                return
            rows = _r.json().get("rows", [])
            has_active_qr = any(
                str(r.get("StatusID", "")) == "2"
                and str(r.get("CheckInType", "")) == "2"
                and r.get("ID") not in Course.check_list
                for r in rows
            )
            if not has_active_qr:
                self._schedule_next_watch()
                return

            # 第二步：确认有活跃 QR 后，再调 getcodeimage 获取 state
            with self._session_lock:
                _r = self.x.post(
                    url=f"{self.host}/_CheckIn/CheckIn.ashx",
                    data=f"action=getcodeimage&cid={Course.id}",
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                        "Referer": f"{self.host}/_CheckIn/MB/TeachCheckIn.aspx",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    timeout=self.req_timeout)
            if _r.status_code == 200:
                data = _r.json()
                if str(data.get("msg")) == "1" and data.get("data"):
                    self._handle_qr_probe_hit()
                    return
        except Exception as e:
            if self.log_mode == "debug":
                self.log("debug", f"QR探测异常: {e}")
        self._schedule_next_watch()

    def _handle_qr_probe_hit(self):
        """getcodeimage 探测到活跃二维码签到"""
        # 停止监控后不得补发签到
        if not (self.is_monitoring and Course.flag):
            return
        # 阈值判断:首次见到二维码签到条目后,未到阈值则推迟本次签到
        threshold = getattr(self, '_active_trigger_seconds', 0)
        if threshold > 0 and self._qr_first_seen:
            earliest_ts = min(self._qr_first_seen.values())
            elapsed = datetime.now().timestamp() - earliest_ts
            if elapsed < threshold:
                wait_left = int(threshold - elapsed)
                self.log("detail", f"🕒 二维码签到延迟中,还需 {wait_left} 秒")
                self._schedule_next_watch()
                return
        self.log("info", "📢 探测到二维码签到（通过QR图片接口），执行签到")
        try:
            status = self._do_qr_sign("")
            if status == "ratelimit":
                self.ui_call(self.update_last_line, "⏳ 频率限制，6秒后重试", "detail")
                self.ui_after(6000, self.watching_sign)
                return
        except Exception as e:
            self.log("warning", f"⚠️ QR探测签到异常: {e}")
        self._schedule_next_watch()

    def _do_sign(self, check_type, check_code, check_id):
        """统一签到执行入口"""
        check_type = str(check_type)
        if check_type == '1' and check_code:
            return self.sign(check_code)
        elif check_type == '2':
            return self._do_qr_sign(check_id)
        elif check_type == '3':
            return self._do_location_sign()
        return False

    def _do_qr_sign(self, check_id):
        """二维码签到：通过 getcodeimage 获取 QR 图片，解码提取 state。
        设计原则:不打印「🔲 尝试获取」这种事前预告,只在拿到 state 或
        确认无数据后再说话,过期/无效场景下完全静默。"""
        state = self._get_qr_state()
        if state:
            # state 级去重：同一个 state 不重复签到
            if not hasattr(self, '_signed_states'):
                self._signed_states = set()
            if state in self._signed_states:
                self.log("detail", f"⏭️ state={state} 已签过，跳过")
                return True
            self.log("info", f"🔑 获取到 state={state}，执行签到...")
            result = self.sign(state, is_qr=True)
            if result == True:
                self._signed_states.add(state)
            return result
        # 没有 QR 数据 → 视为已过期/签到已结束,让上层把 ID 指纹掉
        return "expired"

    def _has_active_qr_data(self):
        """轻量级预检:只问 getcodeimage 当前有没有可签的二维码数据,不解码不发签到请求。
        True=有可签 QR,False=已过期/未发布/接口异常。"""
        try:
            with self._session_lock:
                _r = self.x.post(
                    url=f"{self.host}/_CheckIn/CheckIn.ashx",
                    data=f"action=getcodeimage&cid={Course.id}",
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                        "Referer": f"{self.host}/_CheckIn/MB/TeachCheckIn.aspx",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    timeout=self.req_timeout)
            if _r.status_code != 200:
                return False
            data = _r.json()
            return str(data.get("msg")) == "1" and bool(data.get("data"))
        except Exception:
            return False

    def _get_qr_state(self):
        """通过 getcodeimage 获取 QR 图片并解码出 state"""
        # 依赖自检:Pillow/pyzbar(及其 libzbar DLL)缺失时显式告警(仅一次),
        #          不再把 ImportError 静默当作"已过期"指纹掉,导致二维码签到无声失效。
        try:
            import io
            from PIL import Image
            from pyzbar.pyzbar import decode as qr_decode
        except Exception as e:
            if not getattr(self, "_qr_dep_warned", False):
                self._qr_dep_warned = True
                self.log("error", f"⚠️ 二维码依赖缺失({type(e).__name__})，无法解码二维码签到，"
                                  f"请安装 Pillow / pyzbar 后重启程序")
            return None
        try:
            with self._session_lock:
                _r = self.x.post(
                    url=f"{self.host}/_CheckIn/CheckIn.ashx",
                    data=f"action=getcodeimage&cid={Course.id}",
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                        "Referer": f"{self.host}/_CheckIn/MB/TeachCheckIn.aspx",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    timeout=self.req_timeout)
            if _r.status_code != 200:
                return None
            data = _r.json()
            if str(data.get("msg")) != "1" or not data.get("data"):
                return None
            qr_bytes = base64.b64decode(data["data"])
            img = Image.open(io.BytesIO(qr_bytes))
            results = qr_decode(img)
            for result in results:
                url = result.data.decode()
                if "state=" in url:
                    from urllib.parse import urlparse, parse_qs
                    parsed = urlparse(url)
                    qs = parse_qs(parsed.query)
                    return qs.get("state", [None])[0]
                if "OnlyId=" in url or "G.aspx" in url:
                    with self._session_lock:
                        r2 = self.x.get(url, allow_redirects=False, timeout=self.req_timeout)
                    loc = r2.headers.get("Location", "")
                    if "state=" in loc:
                        from urllib.parse import urlparse, parse_qs
                        parsed = urlparse(loc)
                        qs = parse_qs(parsed.query)
                        return qs.get("state", [None])[0]
        except Exception as e:
            if self.log_mode == "debug":
                self.log("debug", f"QR解码异常: {e}")
        return None

    # ==================== 坐标管理（自定义新增/命名/删除/选择，持久化） ====================
    def render_coord_list(self):
        """根据 self.coords 重建坐标行 UI。每行:○选中 + 名称框 + 经度 + 纬度 + 删除。
        名称/经纬度编辑只写回数据并刷新快照,不触发重建(避免输入时失焦);
        仅新增/删除/切换选中会重建或刷新。"""
        for child in self.coord_list_frame.winfo_children():
            child.destroy()
        self.coord_row_widgets = []
        entry_kw = {"fg_color": "#FFFFFF", "border_color": "#E8E5E0", "corner_radius": 6,
                    "text_color": "#1A1A1A", "placeholder_text_color": "#9CA3AF", "height": 26}
        for i, c in enumerate(self.coords):
            row = ctk.CTkFrame(self.coord_list_frame, fg_color="#F9F8F5", corner_radius=6,
                               border_width=1, border_color="#EEECE8")
            row.pack(fill="x", pady=3)
            top = ctk.CTkFrame(row, fg_color="transparent")
            top.pack(fill="x", padx=6, pady=(5, 1))
            ctk.CTkRadioButton(top, text="", variable=self.coord_radio_var, value=str(i),
                               width=22, radiobutton_width=18, radiobutton_height=18,
                               fg_color="#D97706", hover_color="#B45309",
                               command=self._snapshot_coords).pack(side="left")
            name_entry = ctk.CTkEntry(top, placeholder_text="坐标名称", **entry_kw)
            name_entry.pack(side="left", fill="x", expand=True, padx=(2, 4))
            name_entry.insert(0, c.get("name", ""))
            name_entry.bind("<KeyRelease>", self._snapshot_coords)
            del_btn = ctk.CTkButton(top, text="🗑", width=30, height=26,
                                    fg_color="#F5F4F0", hover_color="#FCA5A5",
                                    text_color="#B91C1C", corner_radius=6,
                                    command=lambda idx=i: self.delete_coord(idx))
            del_btn.pack(side="left")
            bottom = ctk.CTkFrame(row, fg_color="transparent")
            bottom.pack(fill="x", padx=6, pady=(0, 5))
            ctk.CTkLabel(bottom, text="经度", text_color="#9CA3AF",
                         font=ctk.CTkFont(size=10), width=28, anchor="w").pack(side="left")
            lon_entry = ctk.CTkEntry(bottom, width=92, placeholder_text="经度", **entry_kw)
            lon_entry.pack(side="left", padx=(2, 8))
            lon_entry.insert(0, c.get("lon", ""))
            lon_entry.bind("<KeyRelease>", self._snapshot_coords)
            ctk.CTkLabel(bottom, text="纬度", text_color="#9CA3AF",
                         font=ctk.CTkFont(size=10), width=28, anchor="w").pack(side="left")
            lat_entry = ctk.CTkEntry(bottom, width=92, placeholder_text="纬度", **entry_kw)
            lat_entry.pack(side="left", padx=(2, 0))
            lat_entry.insert(0, c.get("lat", ""))
            lat_entry.bind("<KeyRelease>", self._snapshot_coords)
            self.coord_row_widgets.append({"name": name_entry, "lon": lon_entry, "lat": lat_entry})
        # 选中项越界则归位到第一个
        if self.coords:
            try:
                idx = int(self.coord_radio_var.get())
            except (ValueError, TypeError):
                idx = -1
            if idx < 0 or idx >= len(self.coords):
                self.coord_radio_var.set("0")
        self._snapshot_coords()

    def _sync_coords_from_widgets(self):
        """把 UI 行控件里的当前值写回 self.coords(权威数据)。"""
        for i, w in enumerate(self.coord_row_widgets):
            if i < len(self.coords):
                try:
                    name = w["name"].get().strip()
                    self.coords[i]["name"] = name if name else f"坐标{i + 1}"
                    self.coords[i]["lon"] = w["lon"].get().strip()
                    self.coords[i]["lat"] = w["lat"].get().strip()
                except Exception:
                    continue

    def add_coord(self):
        self._sync_coords_from_widgets()
        self.coords.append({"name": f"坐标{len(self.coords) + 1}", "lon": "", "lat": ""})
        self.coord_radio_var.set(str(len(self.coords) - 1))  # 选中新建项
        self.render_coord_list()
        self.log("info", "➕ 已新增一个坐标")

    def delete_coord(self, idx):
        self._sync_coords_from_widgets()
        if idx < 0 or idx >= len(self.coords):
            return
        if len(self.coords) <= 1:
            self.log("warning", "⚠️ 至少需保留一个坐标，无法删除")
            return
        removed = self.coords.pop(idx)
        try:
            cur = int(self.coord_radio_var.get())
        except (ValueError, TypeError):
            cur = 0
        if cur == idx:
            cur = max(0, idx - 1)
        elif cur > idx:
            cur -= 1
        self.coord_radio_var.set(str(cur))
        self.render_coord_list()
        self.log("info", f"🗑 已删除坐标「{removed.get('name', '')}」")

    def _snapshot_coords(self, *_):
        """在 UI 线程把当前选中的坐标读进普通变量(并先把控件值同步回 self.coords)。
        后台签到线程只读这份快照，绝不直接调用 Tk 控件的 .get()，避免跨线程崩溃。"""
        try:
            self._sync_coords_from_widgets()
            try:
                idx = int(self.coord_radio_var.get())
            except (ValueError, TypeError):
                idx = 0
            if not self.coords:
                self._active_coord_label, self._active_lon, self._active_lat = "", "", ""
            else:
                if idx < 0 or idx >= len(self.coords):
                    idx = 0
                    self.coord_radio_var.set("0")
                c = self.coords[idx]
                self._active_coord_label = c.get("name", f"坐标{idx + 1}")
                self._active_lon = c.get("lon", "")
                self._active_lat = c.get("lat", "")
            if hasattr(self, "coord_jitter_switch"):
                self._coord_jitter_enabled = bool(self.coord_jitter_switch.get())
        except Exception:
            pass

    def _apply_coord_jitter(self, lon, lat):
        """在 ≤5 米半径的圆内对坐标做随机偏移（圆内均匀分布）。
        经度偏移按纬度做 cos 修正，保证东西/南北方向都是真实米数。解析失败则原样返回。"""
        try:
            lon_f, lat_f = float(lon), float(lat)
        except (TypeError, ValueError):
            return lon, lat
        radius_m = 5.0                              # 硬上限 5 米
        meters_per_deg = 111320.0                   # 1° 纬度 ≈ 111320 米
        theta = random.uniform(0, 2 * math.pi)      # 随机方向
        r = radius_m * math.sqrt(random.random())   # 随机距离，sqrt 保证圆内均匀
        d_lat = (r * math.cos(theta)) / meters_per_deg
        cos_lat = math.cos(math.radians(lat_f))
        d_lon = 0.0 if abs(cos_lat) < 1e-9 else (r * math.sin(theta)) / (meters_per_deg * cos_lat)
        return f"{lon_f + d_lon:.6f}", f"{lat_f + d_lat:.6f}"

    def _do_location_sign(self):
        """定位签到：使用预设坐标（可选 ≤5 米随机抖动，默认关）"""
        lon, lat = self._fetch_room_location()
        if not (lon and lat):
            self.log("warning", "⚠️ 定位签到需要预设经纬度（当前选中坐标为空，请在左侧填好经纬度）")
            return False
        label = getattr(self, "_active_coord_label", "") or "坐标"
        if getattr(self, "_coord_jitter_enabled", False):
            j_lon, j_lat = self._apply_coord_jitter(lon, lat)
            self.log("info", f"📍 使用【{label}】基准 经度={lon} 纬度={lat}；"
                             f"抖动后 经度={j_lon} 纬度={j_lat}（≤5米）提交定位签到")
            lon, lat = j_lon, j_lat
        else:
            self.log("info", f"📍 使用【{label}】经度={lon} 纬度={lat} 提交定位签到")
        return self.sign_location(lon, lat)

    def _fetch_room_location(self):
        """获取教室坐标：读取 UI 线程预存的坐标快照（线程安全，不碰 Tk 控件）"""
        lon = (getattr(self, "_active_lon", "") or "").strip()
        lat = (getattr(self, "_active_lat", "") or "").strip()
        if lon and lat:
            return lon, lat
        return None, None

    def _schedule_next_watch(self):
        if Course.flag and self.is_monitoring:
            delay_ms = int(random.uniform(self.check_interval_min,
                                          self.check_interval_max) * 1000)
            self.ui_after(delay_ms, self.watching_sign)

    def watching_sign(self):
        if not Course.flag:
            return
        if self.time_schedule_enabled and not self.check_in_schedule_time():
            self.stop_monitoring()
            return
        if self._watch_task_running:
            return
        self._watch_task_running = True
        threading.Thread(target=self._async_watch_task, daemon=True).start()

    # ==================== 签到执行（与 6.5 一致的日志风格）====================
    def get_user_id(self):
        try:
            with self._session_lock:
                _r = self.x.get(url=self.host + "/_UserCenter/MB/index.aspx",
                                timeout=self.req_timeout)
            return BeautifulSoup(_r.text, "lxml").find(id="hidUID").get("value") \
                if _r.status_code == 200 else None
        except Exception as e:
            if self.log_mode == "debug":
                self.log("debug", f"get_user_id 异常: {type(e).__name__}: {e}")
            return None

    def sign(self, sign_code, is_qr=False):
        try:
            with self._session_lock:
                self.x.get(
                    url=f"{self.host}/_CheckIn/MB/CheckInStudent.aspx?moduleid=16&pasd=",
                    headers={"Referer": f"{self.host}/_UserCenter/MB/Module.aspx?data={Course.id}"},
                    timeout=self.req_timeout)
            if not is_qr and len(sign_code) <= 6:
                headers = {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                           "Referer": f"{self.host}/_CheckIn/MB/CheckInStudent.aspx?moduleid=16&pasd="}
                params = f"action=studentcheckin&studentid={self._cached_uid or self.get_user_id()}&checkincode={sign_code}"
                with self._session_lock:
                    _r = self.x.post(url=self.host + "/_CheckIn/CheckIn.ashx",
                                     data=params, headers=headers,
                                     timeout=self.req_timeout)
                if _r.status_code == 200:
                    msg = _r.json().get("msgbox", "")
                    if "签到成功" in msg:
                        self.log_celebration("签到成功", "签到码模式")
                        return True
                    elif "已结束" in msg or "没有正在进行" in msg or "过期" in msg:
                        return "expired"
                    elif "频繁" in msg or "等待" in msg:
                        return "ratelimit"
                    else:
                        if self.log_mode == "debug":
                            self.log("debug", f"签到码被拒: {msg}")
                        return False
            else:
                headers = {"Referer": f"{self.host}/_CheckIn/MB/CheckInStudent.aspx?moduleid=16&pasd="}
                with self._session_lock:
                    _r = self.x.get(
                        url=self.host + "/_CheckIn/MB/QrCodeCheckOK.aspx?state=" + sign_code,
                        headers=headers,
                        timeout=self.req_timeout)
                if _r.status_code == 200:
                    div_ok = BeautifulSoup(_r.text, "lxml").find(id="DivOK")
                    if div_ok and "签到成功" in div_ok.get_text():
                        self.log_celebration("二维码签到成功", "扫码模式")
                        return True
                    else:
                        if self.log_mode == "debug":
                            self.log("debug", "二维码签到被拒：未找到成功标记")
                    return False
        except Exception as e:
            self.log("error", f"❌ 签到异常: {e}")
        return False

    def sign_location(self, longitude, latitude):
        lon = str(longitude)
        lat = str(latitude)
        headers = {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                   "Referer": f"{self.host}/_CheckIn/MB/CheckInStudent.aspx?moduleid=16&pasd="}
        params = f"action=signin&sid={self._cached_uid or self.get_user_id()}&longitude={lon}&latitude={lat}"
        try:
            with self._session_lock:
                self.x.get(
                    url=f"{self.host}/_CheckIn/MB/CheckInStudent.aspx?moduleid=16&pasd=",
                    headers={"Referer": f"{self.host}/_UserCenter/MB/Module.aspx?data={Course.id}"},
                    timeout=self.req_timeout)
            with self._session_lock:
                _r = self.x.post(url=self.host + "/_CheckIn/CheckInRoomHandler.ashx",
                                 data=params, headers=headers,
                                 timeout=self.req_timeout)
            if _r.status_code == 200:
                msg = _r.json().get("msgbox", "")
                if "签到成功" in msg:
                    self.log_celebration("定位签到成功", "GPS 定位模式")
                    return True
                elif "已结束" in msg or "没有正在进行" in msg:
                    self.log("detail", f"⏹️ 签到已结束，跳过")
                    return "expired"
                elif "300秒" in msg or "频繁" in msg or "等待" in msg:
                    return "ratelimit"
                else:
                    self.log("error", f"❌ 定位签到失败: {msg}")
        except Exception as e:
            self.log("error", f"❌ 定位签到异常: {e}")
        return False

    def go_sign(self):
        if not self.combo.get() or self.combo.get() == '请先登录':
            messagebox.showerror("错误", "请先提取并登录账号")
            return
        # 延迟签到:本次启动监听前从输入框定格一次,运行期不再读取
        self._active_trigger_seconds = self._get_live_trigger_seconds()
        course_id = str(Course.id or "")
        course_name = self.combo.get()
        self.set_main_button_starting()
        threading.Thread(target=self._start_monitoring_task,
                         args=(course_id, course_name), daemon=True).start()

    def _start_monitoring_task(self, course_id, course_name):
        try:
            with self._session_lock:
                _r = self.x.get(
                    url=self.host + "/_UserCenter/MB/Module.aspx?data=" + course_id,
                    headers={"Referer": f"{self.host}/_UserCenter/MB/index.aspx"},
                    timeout=self.req_timeout)
            if _r.status_code == 200 and course_id in _r.text:
                course_name_tag = BeautifulSoup(_r.text, "lxml").find(id="CourseName")
                course_name = course_name_tag.text if course_name_tag else course_name
                uid = self.get_user_id() or ""
                self.ui_call(self._finish_monitoring_start, course_name, uid)
            else:
                self.ui_call(
                    self._finish_monitoring_error,
                    f"❌ 启动监控失败：HTTP {_r.status_code}，课程ID不在响应中")
        except Exception as e:
            self.ui_call(self._finish_monitoring_error, f"❌ 启动监控异常: {e}")

    def _finish_monitoring_start(self, course_name, uid):
        self.clear_log()
        self._snapshot_coords()  # 启动监听时锁定当前坐标快照
        self.log("success", f"🎯 进程锁已绑定：【{course_name}】")
        Course.flag = True
        self.is_monitoring = True
        self.waiting_for_schedule = False
        self._manual_schedule_pause = False
        self.stop_schedule_countdown()
        self.set_main_button_running()
        self.combo.configure(state="disabled")  # 监听中锁定课程，防止中途切课导致签错/漏签
        self.update_status("running", "监控中")
        self.refresh_overview()
        self._cached_uid = uid
        if not self._cached_uid:
            self.log("warning", "⚠️ 无法获取UID，监控可能异常")
        Course.check_list.clear()
        self._signed_states = set()  # 清空 QR state 去重集合
        self._scheduled_signs.clear()
        self._countdown_logged.clear()
        self._expired_signs.clear()
        self._monitor_start_time = datetime.now()  # 记录启动时间,过滤此前的历史签到
        # 阈值在 go_sign() 进入瞬间已定格,这里只负责展示
        trigger_snapshot = getattr(self, '_active_trigger_seconds', 0)
        if trigger_snapshot > 0:
            self.log("schedule", f"🎚 延迟签到已锁定: 检测到签到后 {trigger_snapshot} 秒触发")
        else:
            self.log("schedule", "🎚 延迟签到未设置,检测到签到后立即触发")
        if sys.platform == "win32":
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000003)
        self.watching_sign()

    def _finish_monitoring_error(self, message):
        self.log("error", message)
        self.set_main_button_idle()
        self.combo.configure(state="readonly")
        self.update_status("error", "启动失败")
        self.refresh_overview()

    def get_class_list(self):
        headers = {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                   "Referer": f"{self.host}/_UserCenter/PC/CenterStudent.aspx"}
        try:
            with self._session_lock:
                _r = self.x.post(
                    url=self.host + "/_UserCenter/CourseInfo.ashx",
                    data="action=getstudentcourse&classtypeid=2",
                    headers=headers, timeout=self.req_timeout)
            if _r.status_code == 200:
                _json = _r.json()
                if isinstance(_json, dict) and "msgbox" in _json:
                    self.log("warning", f"⚠️ {_json['msgbox']} 请重新登录。")
                    self.x.cookies.clear()
                else:
                    class_name_list = [i["CourseName"] for i in _json]
                    # 控件更新统一走 ui_call，支持从后台登录线程安全调用
                    self.ui_call(self.combo.configure, values=class_name_list)
                    if class_name_list:
                        selected = _json[0]
                        saved_course_id = str(self._saved_course_id or "")
                        if saved_course_id:
                            for item in _json:
                                if str(item.get("CourseID", "")) == saved_course_id:
                                    selected = item
                                    break
                        self.ui_call(self.combo.set, selected["CourseName"])
                        Course.id = selected['CourseID']
                        Course.class_id = selected["TClassID"]
                        Course.class_list = _json
                    self.log("info", f"📚 拉取到 {len(class_name_list)} 门活跃课程")
                    self.ui_call(self.refresh_overview)
        except Exception as e:
            self.log("warning", f"⚠️ 拉取课程列表失败: {type(e).__name__}: {e}（请检查网络或重新登录）")

    def on_combo_change(self, choice):
        for i in Course.class_list:
            if i["CourseName"] == choice:
                Course.id = i["CourseID"]
                Course.class_id = i["TClassID"]
                self._saved_course_id = str(Course.id or "")
        self.refresh_overview()

    def _check_optional_deps(self):
        """启动自检二维码签到所需的可选依赖,缺失则显式告警(不阻断程序运行)。
        pyzbar 在 Windows 还依赖 libzbar DLL,打包时极易漏带,故单独提示。"""
        missing = []
        try:
            import PIL  # noqa: F401
        except Exception:
            missing.append("Pillow")
        try:
            import pyzbar.pyzbar  # noqa: F401
        except Exception:
            missing.append("pyzbar")
        try:
            import lxml  # noqa: F401
        except Exception:
            missing.append("lxml")
        if missing:
            self.log("error", f"⚠️ 缺少依赖 {', '.join(missing)}：二维码签到可能无法使用，"
                              f"请执行 pip install {' '.join(missing)} 后重启程序")

    def init(self):
        # 本地部分（建文件/读 cookie）在主线程快速完成，网络部分丢到后台线程，避免开机卡死窗口
        try:
            if not os.path.exists(self.filename):
                self.config['INFO'] = {'cookie': '1=1'}
                self.write_config_file()
                threading.Thread(target=self._init_new_session, daemon=True).start()
            else:
                self.read_config_file()
                cookie = self.config.get('INFO', 'cookie', fallback='')
                if cookie and cookie != '1=1':
                    self.x.cookies.update(
                        {k: v for pair in cookie.split('; ')
                        if '=' in pair for k, v in [pair.split('=', 1)]})
                threading.Thread(target=self._init_load_courses, daemon=True).start()
        except Exception as e:
            if self.log_mode == "debug":
                self.log("debug", f"init 异常: {type(e).__name__}: {e}")

    def _init_new_session(self):
        """首次运行：后台建立初始会话"""
        try:
            with self._session_lock:
                self.x.get(self.host, timeout=self.req_timeout)
        except Exception as e:
            if self.log_mode == "debug":
                self.log("debug", f"init 新会话异常: {type(e).__name__}: {e}")

    def _init_load_courses(self):
        """已有配置：后台拉取课程列表（控件更新由 get_class_list 内部走 ui_call）"""
        try:
            self.get_class_list()
            self.ui_call(self.refresh_overview)
        except Exception as e:
            if self.log_mode == "debug":
                self.log("debug", f"init 拉课程异常: {type(e).__name__}: {e}")


if __name__ == '__main__':
    if not check_single_instance():
        if sys.platform == "win32":
            ctypes.windll.user32.MessageBoxW(
                0, "检测到后台已存在运行的对分易签到实例！\n为了防止接口互抢和封号，请勿多开。",
                "运行限制", 0x30)
        sys.exit(0)
    app = DuifenyiApp()
    app.mainloop()
