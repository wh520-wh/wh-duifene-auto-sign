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
        self.active_coord = "1"
        self.log_line_count = 0
        self.max_log_lines = 150
        self.log_mode = "simple"
        self._last_update_text = ""
        self._live_log_tag = "live_log_line"

        self.check_interval_min = 1.0
        self.check_interval_max = 3.0

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
        self.init()
        self.schedule_memory_cleanup()
        self.check_schedule_timer()
        self.after(100, self.update_timeline_display)

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
        ctk.CTkLabel(interval_frame, text="模式:", text_color="#6B6560",
                     width=60, anchor="w").pack(side="left")
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
        ctk.CTkLabel(custom_interval_frame, text="间隔:", text_color="#6B6560",
                     width=60, anchor="w").pack(side="left")
        self.interval_min_entry = ctk.CTkEntry(custom_interval_frame, width=45, height=26,
                                               fg_color="#FFFFFF", border_color="#E8E5E0",
                                               corner_radius=6, text_color="#1A1A1A")
        self.interval_min_entry.pack(side="left", padx=2)
        self.interval_min_entry.insert(0, "1.0")
        self.interval_min_entry.bind("<KeyRelease>", lambda _event: self.refresh_overview())
        ctk.CTkLabel(custom_interval_frame, text="-", text_color="#6B6560").pack(side="left", padx=2)
        self.interval_max_entry = ctk.CTkEntry(custom_interval_frame, width=45, height=26,
                                               fg_color="#FFFFFF", border_color="#E8E5E0",
                                               corner_radius=6, text_color="#1A1A1A")
        self.interval_max_entry.pack(side="left", padx=2)
        self.interval_max_entry.insert(0, "3.0")
        self.interval_max_entry.bind("<KeyRelease>", lambda _event: self.refresh_overview())
        ctk.CTkLabel(custom_interval_frame, text="秒", text_color="#6B6560").pack(side="left", padx=(2, 0))

        # 定位签到坐标输入（两组坐标可选）
        loc_frame1 = ctk.CTkFrame(basic_card, fg_color="transparent")
        loc_frame1.pack(fill="x", padx=12, pady=(0, 4))
        self.coord_radio_var = ctk.StringVar(value="1")
        ctk.CTkRadioButton(loc_frame1, text="坐标1", variable=self.coord_radio_var,
                           value="1", width=60, height=20,
                           fg_color="#D97706", hover_color="#B45309",
                           text_color="#1A1A1A").pack(side="left")
        self.lon_entry_1 = ctk.CTkEntry(loc_frame1, width=85, placeholder_text="经度",
                                        fg_color="#FFFFFF", border_color="#E8E5E0",
                                        text_color="#1A1A1A", placeholder_text_color="#9CA3AF")
        self.lon_entry_1.pack(side="left", padx=(5, 3))
        self.lon_entry_1.insert(0, self.preset_lon_1)
        self.lat_entry_1 = ctk.CTkEntry(loc_frame1, width=85, placeholder_text="纬度",
                                        fg_color="#FFFFFF", border_color="#E8E5E0",
                                        text_color="#1A1A1A", placeholder_text_color="#9CA3AF")
        self.lat_entry_1.pack(side="left", padx=(3, 0))
        self.lat_entry_1.insert(0, self.preset_lat_1)

        loc_frame2 = ctk.CTkFrame(basic_card, fg_color="transparent")
        loc_frame2.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkRadioButton(loc_frame2, text="坐标2", variable=self.coord_radio_var,
                           value="2", width=60, height=20,
                           fg_color="#D97706", hover_color="#B45309",
                           text_color="#1A1A1A").pack(side="left")
        self.lon_entry_2 = ctk.CTkEntry(loc_frame2, width=85, placeholder_text="经度",
                                        fg_color="#FFFFFF", border_color="#E8E5E0",
                                        text_color="#1A1A1A", placeholder_text_color="#9CA3AF")
        self.lon_entry_2.pack(side="left", padx=(5, 3))
        self.lon_entry_2.insert(0, self.preset_lon_2)
        self.lat_entry_2 = ctk.CTkEntry(loc_frame2, width=85, placeholder_text="纬度",
                                        fg_color="#FFFFFF", border_color="#E8E5E0",
                                        text_color="#1A1A1A", placeholder_text_color="#9CA3AF")
        self.lat_entry_2.pack(side="left", padx=(3, 0))
        self.lat_entry_2.insert(0, self.preset_lat_2)

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

        self.text_box._textbox.tag_config("success", foreground="#059669")
        self.text_box._textbox.tag_config("error", foreground="#DC2626")
        self.text_box._textbox.tag_config("warning", foreground="#D97706")
        self.text_box._textbox.tag_config("info", foreground="#2563EB")
        self.text_box._textbox.tag_config("schedule", foreground="#7C3AED")
        self.text_box._textbox.tag_config("highlight", foreground="#D97706",
                                          font=("Consolas", 13, "bold"))
        self.text_box._textbox.tag_config("debug", foreground="#9CA3AF",
                                          font=("Consolas", 11))
        self.text_box._textbox.tag_config("detail", foreground="#6B6560")

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
        interval_min = self.interval_min_entry.get().strip() if hasattr(self, "interval_min_entry") else "1.0"
        interval_max = self.interval_max_entry.get().strip() if hasattr(self, "interval_max_entry") else "3.0"
        interval_text = f"{interval_min or '1.0'} - {interval_max or '3.0'} 秒"
        start_text = f"{self.start_hour.get()}:{self.start_minute.get()}" if hasattr(self, "start_hour") else "--:--"
        end_text = f"{self.end_hour.get()}:{self.end_minute.get()}" if hasattr(self, "end_hour") else "--:--"
        schedule_text = f"{start_text} - {end_text}" if self.time_schedule_enabled else "未启用"
        coord = self.coord_radio_var.get() if hasattr(self, "coord_radio_var") else "1"
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
            text=f"{state_text} | 坐标{coord} | 轮询 {interval_text}",
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
        self._last_update_text = ""
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
        self.text_box._textbox.tag_remove(self._live_log_tag, "1.0", "end")
        self._last_update_text = ""
        if self.log_line_count >= self.max_log_lines:
            self.text_box._textbox.delete("1.0", "50.0")
            self.log_line_count -= 50
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.text_box.insert("end", f"[{timestamp}] {msg}\n")
        self.log_line_count += 1
        if level in ["success", "error", "warning", "info", "schedule", "highlight", "debug", "detail"]:
            line_count = int(self.text_box.index('end-1c').split('.')[0])
            self.text_box._textbox.tag_add(level, f"{line_count - 1}.0", f"{line_count}.0")
        self.text_box.see("end")

    def update_last_line(self, text, level="info"):
        if self._last_update_text == text:
            return
        self._last_update_text = text
        try:
            timestamp = datetime.now().strftime("%H:%M:%S")
            line_text = f"[{timestamp}] {text}\n"
            ranges = self.text_box._textbox.tag_ranges(self._live_log_tag)
            replacing = bool(ranges)
            if replacing:
                insert_at = str(ranges[0])
                self.text_box._textbox.delete(ranges[0], ranges[-1])
            else:
                if self.log_line_count >= self.max_log_lines:
                    self.text_box._textbox.delete("1.0", "50.0")
                    self.log_line_count -= 50
                insert_at = self.text_box.index("end")
            start_index = self.text_box.index(insert_at)
            self.text_box.insert(start_index, line_text)
            end_index = self.text_box.index(f"{start_index}+{len(line_text)}c")
            self.text_box._textbox.tag_add(self._live_log_tag, start_index, end_index)
            if level:
                self.text_box._textbox.tag_add(level, start_index, end_index)
            if not replacing:
                self.log_line_count += 1
            self.text_box.see("end")
        except:
            self._render_log(level, text)

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
        if manual and self.time_schedule_enabled and self.check_in_schedule_time():
            self._manual_schedule_pause = True
        if sys.platform == "win32":
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        self._last_update_text = ""
        self.set_main_button_idle()
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
                'active_coord': self.coord_radio_var.get(),
                'lon_1': self.lon_entry_1.get().strip(),
                'lat_1': self.lat_entry_1.get().strip(),
                'lon_2': self.lon_entry_2.get().strip(),
                'lat_2': self.lat_entry_2.get().strip(),
                'selected_course_id': str(Course.id or ""),
                'selected_course_name': self.combo.get().strip() if self.combo.get() else ""
            }
            self.write_config_file()
            self.log("success", "✅ 所有配置参数已全量保存")
            self.save_btn.configure(text="✓ 已保存", fg_color="#059669")
            self.after(1500, lambda: self.save_btn.configure(text="💾 保存配置", fg_color="#F5F4F0"))
        except Exception as e:
            self.log("error", f"❌ 保存配置失败: {e}")

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
                    self.check_interval_min = float(self.interval_min_entry.get())
                    self.check_interval_max = float(self.interval_max_entry.get())
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
                    self.coord_radio_var.set(
                        self.config.get('SETTINGS', 'active_coord', fallback='1'))
                    lon1 = self.config.get('SETTINGS', 'lon_1', fallback='')
                    lat1 = self.config.get('SETTINGS', 'lat_1', fallback='')
                    lon2 = self.config.get('SETTINGS', 'lon_2', fallback='')
                    lat2 = self.config.get('SETTINGS', 'lat_2', fallback='')
                    if lon1:
                        self.lon_entry_1.delete(0, "end")
                        self.lon_entry_1.insert(0, lon1)
                    if lat1:
                        self.lat_entry_1.delete(0, "end")
                        self.lat_entry_1.insert(0, lat1)
                    if lon2:
                        self.lon_entry_2.delete(0, "end")
                        self.lon_entry_2.insert(0, lon2)
                    if lat2:
                        self.lat_entry_2.delete(0, "end")
                        self.lat_entry_2.insert(0, lat2)
                    self._saved_course_id = self.config.get('SETTINGS', 'selected_course_id', fallback='')
                self.refresh_overview()
        except:
            pass

    def copy_wx_link(self):
        self.clipboard_clear()
        self.clipboard_append(self.wx_guide)
        self.log("info", "📋 已复制微信提取链接")

    def login_link(self):
        link = self.link_entry.get()
        code = self.extract_wechat_code(link)
        if code:
            self.x.cookies.clear()
            try:
                self.wx_login_btn.configure(state="disabled", text="登录中...")
                with self._session_lock:
                    self.x.get(url=self.host + f"/P.aspx?authtype=1&code={code}&state=1",
                               timeout=self.req_timeout)
                self.get_class_list()
                self.config['INFO'] = {'cookie': self.get_cookie_string()}
                self.write_config_file()
                self.log("success", "✅ 微信链接登录成功")
                self.refresh_overview()
            except Exception as e:
                self.log("error", f"❌ 网络请求异常: {e}")
            finally:
                self.wx_login_btn.configure(state="normal", text="微信登录")
        else:
            messagebox.showerror("error", "链接有误")

    def login(self):
        headers = {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                   "Referer": f"{self.host}/AppGate.aspx"}
        params = (f'action=loginmb&loginname={self.username_entry.get()}'
                  f'&password={self.password_entry.get()}')
        self.x.cookies.clear()
        try:
            self.pwd_login_btn.configure(state="disabled", text="登录中...")
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
                self.refresh_overview()
            else:
                self.log("error", f"❌ 登录失败: {_r.json().get('msgbox', '未知错误')}")
        except Exception as e:
            self.log("error", f"❌ 网络请求异常: {e}")
        finally:
            self.pwd_login_btn.configure(state="normal", text="账号登录")

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
        self.log("error", "🔒 登录状态已失效，监控已自动停止，请重新登录账号")
        self.x.cookies.clear()
        Course.flag = False
        self.is_monitoring = False
        self._last_update_text = ""
        self.set_main_button_idle()
        self.update_status("error", "登录已失效")

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
        except:
            pass
        return None

    def _process_watch_result(self, data):
        if not self.is_monitoring:
            return

        self.ui_call(
            self.update_last_line,
            f"👀 持续异步监控中：{datetime.now().strftime('%H:%M:%S')}", "detail")

        if self.log_mode == "debug":
            self.log("debug", f"API原始响应: msg={data.get('msg')}({type(data.get('msg')).__name__}), rows数量={len(data.get('rows', []))}")

        rows = data.get("rows", [])
        has_data = str(data.get("msg", "")) == "1" and bool(rows)

        pending = []
        if has_data:
            pending = [r for r in rows if str(r.get("StatusID", "")) == "2" and r.get("ID") not in Course.check_list]

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

                if self.log_mode == "debug":
                    self.log("debug", f"检测到缺勤: ID={HFC_ID}, type={HFC_type}, code={check_code}")

                self.log("info", f"📢 检测到{sign_type}签到，执行签到")
                try:
                    status = self._do_sign(HFC_type, check_code, HFC_ID)
                    if status == True:
                        Course.check_list.append(HFC_ID)
                        if HFC_type == '2':
                            for other in pending:
                                if str(other.get("CheckInType", "")) == "2":
                                    oid = other.get("ID", "")
                                    if oid and oid not in Course.check_list:
                                        Course.check_list.append(oid)
                            break
                    elif status == "expired":
                        Course.check_list.append(HFC_ID)
                        self.log("detail", f"⏹️ {sign_type}签到已结束，跳过")
                    elif status == "ratelimit":
                        self.ui_call(self.update_last_line, "⏳ 频率限制，6秒后重试", "detail")
                        self.ui_after(6000, self.watching_sign)
                        return
                except Exception as e:
                    if self.log_mode == "debug":
                        self.log("debug", f"⚠️ 签到处理异常: {e}")

        if not has_qr_pending and not self._qr_probe_cooldown():
            threading.Thread(target=self._probe_qr_sign, daemon=True).start()
            return

        self._schedule_next_watch()

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
        """主动探测是否有活跃的二维码签到（通过 getcodeimage）"""
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
            if _r.status_code == 200:
                data = _r.json()
                if data.get("msg") == 1 and data.get("data"):
                    self._handle_qr_probe_hit()
                    return
        except Exception as e:
            if self.log_mode == "debug":
                self.log("debug", f"QR探测异常: {e}")
        self._schedule_next_watch()

    def _handle_qr_probe_hit(self):
        """getcodeimage 探测到活跃二维码签到"""
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
        """二维码签到：通过 getcodeimage 获取 QR 图片，解码提取 state"""
        self.log("info", "🔲 检测到二维码签到，尝试获取二维码...")
        state = self._get_qr_state()
        if state:
            self.log("info", f"🔑 获取到 state={state}，执行签到...")
            result = self.sign(state, is_qr=True)
            if result == False:
                self.log("warning", "⚠️ 二维码签到需要微信链接登录，账号登录不支持")
            return result
        self.log("warning", "⚠️ 二维码签到：无法获取二维码数据")
        return False

    def _get_qr_state(self):
        """通过 getcodeimage 获取 QR 图片并解码出 state"""
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
            if data.get("msg") != 1 or not data.get("data"):
                return None
            import io
            from PIL import Image
            from pyzbar.pyzbar import decode as qr_decode
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

    def _do_location_sign(self):
        """定位签到：使用预设坐标"""
        lon, lat = self._fetch_room_location()
        if lon and lat:
            return self.sign_location(lon, lat)
        self.log("warning", "⚠️ 定位签到需要预设经纬度")
        return False

    def _fetch_room_location(self):
        """获取教室坐标：根据选中的坐标组"""
        if self.coord_radio_var.get() == "1":
            lon = self.lon_entry_1.get().strip()
            lat = self.lat_entry_1.get().strip()
        else:
            lon = self.lon_entry_2.get().strip()
            lat = self.lat_entry_2.get().strip()
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
        except:
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
                        self.log("highlight", "🎉🎉🎉 签到成功！🎉🎉🎉")
                        return True
                    elif "已结束" in msg or "没有正在进行" in msg:
                        self.log("detail", "⏹️ 签到已结束，跳过")
                        return "expired"
                    elif "频繁" in msg or "等待" in msg:
                        return "ratelimit"
                    else:
                        self.log("error", f"❌ 签到码签到失败: {msg}")
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
                        self.log("highlight", "🎉🎉🎉 二维码签到成功！🎉🎉🎉")
                        return True
                    else:
                        self.log("error", "❌ 二维码签到失败：未找到成功标记")
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
                    self.log("highlight", "🎉🎉🎉 定位签到成功！🎉🎉🎉")
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
        self.log("success", f"🎯 进程锁已绑定：【{course_name}】")
        Course.flag = True
        self.is_monitoring = True
        self.waiting_for_schedule = False
        self._manual_schedule_pause = False
        self.stop_schedule_countdown()
        self.set_main_button_running()
        self.update_status("running", "监控中")
        self.refresh_overview()
        self._cached_uid = uid
        if not self._cached_uid:
            self.log("warning", "⚠️ 无法获取UID，监控可能异常")
        Course.check_list.clear()
        if sys.platform == "win32":
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000003)
        self.watching_sign()

    def _finish_monitoring_error(self, message):
        self.log("error", message)
        self.set_main_button_idle()
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
                    self.combo.configure(values=class_name_list)
                    if class_name_list:
                        selected = _json[0]
                        saved_course_id = str(self._saved_course_id or "")
                        if saved_course_id:
                            for item in _json:
                                if str(item.get("CourseID", "")) == saved_course_id:
                                    selected = item
                                    break
                        self.combo.set(selected["CourseName"])
                        Course.id = selected['CourseID']
                        Course.class_id = selected["TClassID"]
                        Course.class_list = _json
                    self.log("info", f"📚 拉取到 {len(class_name_list)} 门活跃课程")
                    self.refresh_overview()
        except:
            pass

    def on_combo_change(self, choice):
        for i in Course.class_list:
            if i["CourseName"] == choice:
                Course.id = i["CourseID"]
                Course.class_id = i["TClassID"]
                self._saved_course_id = str(Course.id or "")
        self.refresh_overview()

    def init(self):
        try:
            if not os.path.exists(self.filename):
                self.config['INFO'] = {'cookie': '1=1'}
                self.write_config_file()
                with self._session_lock:
                    self.x.get(self.host, timeout=self.req_timeout)
            else:
                self.read_config_file()
                cookie = self.config.get('INFO', 'cookie', fallback='')
                if cookie and cookie != '1=1':
                    self.x.cookies.update(
                        {k: v for pair in cookie.split('; ')
                        if '=' in pair for k, v in [pair.split('=', 1)]})
                self.get_class_list()
                self.refresh_overview()
        except:
            pass


if __name__ == '__main__':
    if not check_single_instance():
        if sys.platform == "win32":
            ctypes.windll.user32.MessageBoxW(
                0, "检测到后台已存在运行的对分易签到实例！\n为了防止接口互抢和封号，请勿多开。",
                "运行限制", 0x30)
        sys.exit(0)
    app = DuifenyiApp()
    app.mainloop()
