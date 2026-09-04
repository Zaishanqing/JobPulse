import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading
import time
import os
import sys
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
import pymysql
import random
import re
import json
import csv
import logging
from DrissionPage import ChromiumPage  # 新增：导入DrissionPage

# Ensure the parent directory is on the path so crawler scripts can
# import the shared config module.
from unified_api.config import DB_CONFIG

# 导入数据分析模块
from crawler.data_viewer import DataViewer


logger = logging.getLogger(__name__)


class BossZPSpiderGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Boss直聘数据爬取系统 - 增强版")
        self.root.geometry("900x700")
        self.root.resizable(True, True)

        # 爬虫状态
        self.is_running = False
        self.spider_thread = None

        # 数据库连接
        self.db_connection = None

        # DrissionPage浏览器实例
        self.dp = None
        self.is_logged_in = False

        # 数据清洗统计
        self.cleaning_stats = {
            'total_found': 0,
            'duplicates_removed': 0,
            'invalid_removed': 0
        }

        # 创建界面
        self.create_widgets()

        # 连接数据库
        self.connect_database()

    def create_widgets(self):
        # 创建主框架
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # 配置网格权重
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)

        # 爬取设置区域
        settings_frame = ttk.LabelFrame(main_frame, text="爬取设置", padding="10")
        settings_frame.grid(row=0, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))
        settings_frame.columnconfigure(1, weight=1)

        # 爬取模式选择
        ttk.Label(settings_frame, text="爬取模式:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.mode_var = tk.StringVar(value="drissionpage")  # 默认使用DrissionPage模式
        mode_frame = ttk.Frame(settings_frame)
        mode_frame.grid(row=0, column=1, sticky=(tk.W, tk.E), pady=5)

        ttk.Radiobutton(mode_frame, text="DrissionPage(需登录,稳定)",
                        variable=self.mode_var, value="drissionpage").grid(row=0, column=0, sticky=tk.W)
        ttk.Radiobutton(mode_frame, text="Selenium(免登录,可能被反爬)",
                        variable=self.mode_var, value="selenium").grid(row=0, column=1, sticky=tk.W, padx=(20, 0))

        # 关键词设置
        ttk.Label(settings_frame, text="职位关键词:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.keyword_var = tk.StringVar(value="Java")
        keyword_frame = ttk.Frame(settings_frame)
        keyword_frame.grid(row=1, column=1, sticky=(tk.W, tk.E), pady=5)

        self.keyword_entry = ttk.Entry(keyword_frame, textvariable=self.keyword_var, width=30)
        self.keyword_entry.grid(row=0, column=0, sticky=(tk.W, tk.E))
        keyword_frame.columnconfigure(0, weight=1)

        ttk.Button(keyword_frame, text="常用关键词", command=self.show_keyword_selector).grid(row=0, column=1,
                                                                                              padx=(5, 0))

        # 城市选择
        ttk.Label(settings_frame, text="目标城市:").grid(row=2, column=0, sticky=tk.W, pady=5)
        self.city_var = tk.StringVar(value="北京")
        city_frame = ttk.Frame(settings_frame)
        city_frame.grid(row=2, column=1, sticky=(tk.W, tk.E), pady=5)

        self.city_combo = ttk.Combobox(city_frame, textvariable=self.city_var, width=28)
        self.city_combo['values'] = [
            '北京', '上海', '广州', '深圳', '杭州', '天津', '西安', '苏州',
            '武汉', '厦门', '长沙', '成都', '郑州', '重庆', '佛山', '合肥',
            '济南', '青岛', '南京', '东莞', '福州'
        ]
        self.city_combo.grid(row=0, column=0, sticky=(tk.W, tk.E))
        city_frame.columnconfigure(0, weight=1)

        ttk.Button(city_frame, text="多城市选择", command=self.show_city_selector).grid(row=0, column=1, padx=(5, 0))

        # 爬取页数
        ttk.Label(settings_frame, text="爬取页数:").grid(row=3, column=0, sticky=tk.W, pady=5)
        self.page_var = tk.IntVar(value=5)
        page_frame = ttk.Frame(settings_frame)
        page_frame.grid(row=3, column=1, sticky=tk.W, pady=5)

        ttk.Scale(page_frame, from_=1, to=15, variable=self.page_var, orient=tk.HORIZONTAL, length=200).grid(row=0,
                                                                                                             column=0)
        ttk.Label(page_frame, textvariable=self.page_var).grid(row=0, column=1, padx=(5, 0))

        # 爬取选项
        ttk.Label(settings_frame, text="爬取选项:").grid(row=4, column=0, sticky=tk.W, pady=5)
        options_frame = ttk.Frame(settings_frame)
        options_frame.grid(row=4, column=1, sticky=tk.W, pady=5)

        self.include_salary_var = tk.BooleanVar(value=True)
        self.include_company_var = tk.BooleanVar(value=True)
        self.include_skill_var = tk.BooleanVar(value=True)

        ttk.Checkbutton(options_frame, text="包含薪资信息", variable=self.include_salary_var).grid(row=0, column=0,
                                                                                                   sticky=tk.W)
        ttk.Checkbutton(options_frame, text="包含公司信息", variable=self.include_company_var).grid(row=0, column=1,
                                                                                                    sticky=tk.W,
                                                                                                    padx=(20, 0))
        ttk.Checkbutton(options_frame, text="包含技能要求", variable=self.include_skill_var).grid(row=1, column=0,
                                                                                                  sticky=tk.W,
                                                                                                  pady=(5, 0))

        # 控制按钮区域
        control_frame = ttk.Frame(main_frame)
        control_frame.grid(row=1, column=0, columnspan=2, pady=10)

        self.start_button = ttk.Button(control_frame, text="开始爬取", command=self.start_spider)
        self.start_button.grid(row=0, column=0, padx=(0, 10))

        self.stop_button = ttk.Button(control_frame, text="停止爬取", command=self.stop_spider, state=tk.DISABLED)
        self.stop_button.grid(row=0, column=1, padx=(0, 10))

        ttk.Button(control_frame, text="登录Boss直聘", command=self.login_boss).grid(row=0, column=2, padx=(0, 10))
        ttk.Button(control_frame, text="清空数据", command=self.clear_data).grid(row=0, column=3, padx=(0, 10))
        ttk.Button(control_frame, text="查看数据", command=self.view_data).grid(row=0, column=4, padx=(0, 10))
        ttk.Button(control_frame, text="数据分析", command=self.analyze_data).grid(row=0, column=5)
        ttk.Button(control_frame, text="数据清洗", command=self.data_cleaning).grid(row=0, column=6, padx=(10, 0))

        # 状态显示区域
        status_frame = ttk.LabelFrame(main_frame, text="状态信息", padding="5")
        status_frame.grid(row=2, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(10, 0))

        self.status_var = tk.StringVar(value="准备就绪")
        status_label = ttk.Label(status_frame, textvariable=self.status_var)
        status_label.grid(row=0, column=0, sticky=tk.W)

        self.login_status_var = tk.StringVar(value="未登录")
        login_status_label = ttk.Label(status_frame, textvariable=self.login_status_var, foreground="red")
        login_status_label.grid(row=0, column=1, sticky=tk.E)

        # 日志区域
        log_frame = ttk.LabelFrame(main_frame, text="爬取日志", padding="5")
        log_frame.grid(row=3, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(10, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        main_frame.rowconfigure(3, weight=1)

        self.log_text = scrolledtext.ScrolledText(log_frame, height=15, width=80)
        self.log_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # 进度条
        self.progress = ttk.Progressbar(main_frame, orient=tk.HORIZONTAL, mode='determinate')
        self.progress.grid(row=4, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(10, 0))

    def login_boss(self):
        """登录Boss直聘"""
        if self.dp is not None:
            messagebox.showinfo("提示", "浏览器已打开，请手动登录Boss直聘")
            return

        try:
            self.log("正在启动浏览器，请手动登录Boss直聘...")
            self.status_var.set("正在启动浏览器")

            # 启动DrissionPage浏览器
            self.dp = ChromiumPage()
            self.dp.get('https://www.zhipin.com/web/geek/jobs?query=java&city=101010100')

            self.log("浏览器已启动，请在弹出的浏览器窗口中登录Boss直聘")
            self.log("登录完成后，回到程序点击确定继续")

            # 等待用户登录
            result = messagebox.askokcancel("登录提示",
                                            "浏览器已打开，请手动登录Boss直聘\n"
                                            "登录完成后点击确定继续")

            if result:
                self.is_logged_in = True
                self.login_status_var.set("已登录")
                self.log("登录状态已确认，可以开始爬取")
                self.status_var.set("已登录，准备爬取")
            else:
                self.log("登录取消")
                if self.dp:
                    self.dp.quit()
                self.dp = None

        except Exception as e:
            self.log(f"登录失败: {str(e)}")
            messagebox.showerror("错误", f"登录失败: {str(e)}")

    def data_cleaning(self):
        """数据清洗功能"""
        if messagebox.askyesno("确认", "确定要执行数据清洗吗？这将删除重复数据和无效数据"):
            try:
                self.log("开始数据清洗...")

                cursor = self.db_connection.cursor()

                # 统计清洗前数据量
                cursor.execute("SELECT COUNT(*) FROM bosszp")
                before_count = cursor.fetchone()[0]

                # 1. 删除完全重复的数据（基于职位、公司、城市、关键词）
                cursor.execute("""
                    DELETE t1 FROM bosszp t1
                    INNER JOIN bosszp t2
                    WHERE
                        t1.id < t2.id AND
                        t1.job_title = t2.job_title AND
                        t1.job_company = t2.job_company AND
                        t1.company_city = t2.company_city AND
                        t1.keyword = t2.keyword
                """)
                duplicates_removed = cursor.rowcount

                # 2. 删除关键字段为空的数据
                cursor.execute("""
                    DELETE FROM bosszp
                    WHERE
                        job_title IS NULL OR
                        job_title = '' OR
                        job_company IS NULL OR
                        job_company = '' OR
                        company_city IS NULL OR
                        company_city = ''
                """)
                invalid_removed = cursor.rowcount

                # 3. 删除明显无效的数据（如"未知职位"、"未知公司"等）
                cursor.execute("""
                    DELETE FROM bosszp
                    WHERE
                        job_title LIKE '%未知%' OR
                        job_company LIKE '%未知%' OR
                        job_title = '职位' OR
                        job_company = '公司'
                """)
                obvious_invalid_removed = cursor.rowcount

                # 统计清洗后数据量
                cursor.execute("SELECT COUNT(*) FROM bosszp")
                after_count = cursor.fetchone()[0]

                self.db_connection.commit()
                cursor.close()

                # 记录清洗结果
                self.log(f"数据清洗完成！")
                self.log(f"清洗前数据量: {before_count}")
                self.log(f"清洗后数据量: {after_count}")
                self.log(f"删除重复数据: {duplicates_removed} 条")
                self.log(f"删除无效数据: {invalid_removed + obvious_invalid_removed} 条")
                self.log(f"总共删除: {before_count - after_count} 条数据")

                messagebox.showinfo("数据清洗完成",
                                    f"数据清洗完成！\n"
                                    f"清洗前: {before_count} 条\n"
                                    f"清洗后: {after_count} 条\n"
                                    f"删除重复: {duplicates_removed} 条\n"
                                    f"删除无效: {invalid_removed + obvious_invalid_removed} 条")

            except Exception as e:
                self.log(f"数据清洗失败: {str(e)}")
                messagebox.showerror("错误", f"数据清洗失败: {str(e)}")

    def analyze_data(self):
        """打开数据分析窗口"""
        data_viewer = DataViewer(self.root)
        data_viewer.show_data_viewer()

    def show_keyword_selector(self):
        """显示关键词选择窗口"""
        keyword_window = tk.Toplevel(self.root)
        keyword_window.title("选择职位关键词")
        keyword_window.geometry("300x400")
        keyword_window.transient(self.root)
        keyword_window.grab_set()

        # 常用关键词列表
        common_keywords = [
            "Java", "Python", "前端", "后端", "算法", "测试", "运维",
            "Android", "iOS", "PHP", "C++", "C#", ".NET", "Go",
            "大数据", "人工智能", "机器学习", "深度学习", "数据挖掘",
            "架构师", "全栈", "Node.js", "Vue", "React", "Angular"
        ]

        selected_keywords = []

        def toggle_keyword(keyword, var):
            if var.get():
                if keyword not in selected_keywords:
                    selected_keywords.append(keyword)
            else:
                if keyword in selected_keywords:
                    selected_keywords.remove(keyword)

        def confirm_selection():
            if selected_keywords:
                self.keyword_var.set(" ".join(selected_keywords))
            keyword_window.destroy()

        # 创建复选框
        frame = ttk.Frame(keyword_window)
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        keyword_vars = {}
        for i, keyword in enumerate(common_keywords):
            var = tk.BooleanVar()
            cb = ttk.Checkbutton(frame, text=keyword, variable=var,
                                 command=lambda k=keyword, v=var: toggle_keyword(k, v))
            cb.grid(row=i // 3, column=i % 3, sticky=tk.W, padx=5, pady=2)
            keyword_vars[keyword] = var

        # 确认按钮
        ttk.Button(keyword_window, text="确认选择", command=confirm_selection).pack(pady=10)

    def show_city_selector(self):
        """显示城市选择窗口"""
        city_window = tk.Toplevel(self.root)
        city_window.title("选择目标城市")
        city_window.geometry("300x400")
        city_window.transient(self.root)
        city_window.grab_set()

        # 城市列表
        cities = [
            '北京', '上海', '广州', '深圳', '杭州', '天津', '西安', '苏州',
            '武汉', '厦门', '长沙', '成都', '郑州', '重庆', '佛山', '合肥',
            '济南', '青岛', '南京', '东莞', '福州'
        ]

        selected_cities = []

        def toggle_city(city, var):
            if var.get():
                if city not in selected_cities:
                    selected_cities.append(city)
            else:
                if city in selected_cities:
                    selected_cities.remove(city)

        def confirm_selection():
            if selected_cities:
                self.city_var.set(" ".join(selected_cities))
            city_window.destroy()

        # 创建复选框
        frame = ttk.Frame(city_window)
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        city_vars = {}
        for i, city in enumerate(cities):
            var = tk.BooleanVar()
            cb = ttk.Checkbutton(frame, text=city, variable=var,
                                 command=lambda c=city, v=var: toggle_city(c, v))
            cb.grid(row=i // 3, column=i % 3, sticky=tk.W, padx=5, pady=2)
            city_vars[city] = var

        # 确认按钮
        ttk.Button(city_window, text="确认选择", command=confirm_selection).pack(pady=10)

    def connect_database(self):
        """连接数据库"""
        try:
            self.db_connection = pymysql.connect(**DB_CONFIG)
            self.log("数据库连接成功")
        except Exception as e:
            self.log(f"数据库连接失败: {str(e)}")
            messagebox.showerror("错误", f"数据库连接失败: {str(e)}")

    def log(self, message):
        """添加日志"""
        self.log_text.insert(tk.END, f"{time.strftime('%H:%M:%S')} - {message}\n")
        self.log_text.see(tk.END)
        self.root.update()

    def start_spider(self):
        """开始爬虫"""
        if self.is_running:
            messagebox.showwarning("警告", "爬虫正在运行中")
            return

        # 检查爬取模式
        mode = self.mode_var.get()
        if mode == "drissionpage" and not self.is_logged_in:
            result = messagebox.askyesno("登录提示",
                                         "DrissionPage模式需要先登录Boss直聘\n"
                                         "是否现在登录？")
            if result:
                self.login_boss()
                if not self.is_logged_in:
                    return
            else:
                # 切换到Selenium模式
                self.mode_var.set("selenium")
                self.log("已切换到Selenium模式")

        keyword = self.keyword_var.get().strip()
        city = self.city_var.get().strip()
        pages = self.page_var.get()

        if not keyword:
            messagebox.showerror("错误", "请输入关键词")
            return

        if not city:
            messagebox.showerror("错误", "请选择城市")
            return

        self.is_running = True
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self.progress['value'] = 0

        # 在新线程中运行爬虫
        self.spider_thread = threading.Thread(
            target=self.run_spider,
            args=(keyword, city, pages, mode),
            daemon=True
        )
        self.spider_thread.start()

    def stop_spider(self):
        """停止爬虫"""
        self.is_running = False
        self.start_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.DISABLED)
        self.log("爬虫停止中...")
        self.status_var.set("爬虫已停止")

    def run_spider(self, keyword, city, pages, mode):
        """运行爬虫"""
        try:
            self.status_var.set(f"开始爬取: {keyword} - {city}")

            if mode == "drissionpage":
                self.log(f"使用DrissionPage模式爬取")
                count = self.run_drissionpage_spider(keyword, city, pages)
            else:
                self.log(f"使用Selenium模式爬取")
                count = self.run_selenium_spider(keyword, city, pages)

            self.log(f"爬取完成，共获取 {count} 条数据")
            self.status_var.set("爬取完成")

        except Exception as e:
            self.log(f"爬虫异常: {str(e)}")
            messagebox.showerror("错误", f"爬虫异常: {str(e)}")
            self.status_var.set("爬取出错")

        finally:
            self.is_running = False
            self.start_button.config(state=tk.NORMAL)
            self.stop_button.config(state=tk.DISABLED)
            self.progress['value'] = 100

    def run_drissionpage_spider(self, keyword, city, pages):
        """使用DrissionPage爬取数据 - 修复多城市支持"""
        if not self.dp or not self.is_logged_in:
            self.log("DrissionPage浏览器未就绪，请先登录")
            return 0

        city_codes = {
            '北京': '101010100', '上海': '101020100', '广州': '101280100',
            '深圳': '101280600', '杭州': '101210100', '天津': '101030100',
            '西安': '101110100', '苏州': '101190400', '武汉': '101200100',
            '厦门': '101230200', '长沙': '101250100', '成都': '101270100',
            '郑州': '101180100', '重庆': '101040100', '佛山': '101280800',
            '合肥': '101220100', '济南': '101120100', '青岛': '101120200',
            '南京': '101190100', '东莞': '101281600', '福州': '101230100'
        }

        # 处理多城市
        cities = city.split()
        keywords = keyword.split()

        total_tasks = len(keywords) * len(cities) * pages
        completed_tasks = 0
        total_count = 0

        try:
            for kw in keywords:
                for ct in cities:
                    if not self.is_running:
                        break

                    city_code = city_codes.get(ct, '101010100')
                    self.log(f"开始爬取: {kw} - {ct}")

                    # 开始监听数据
                    self.dp.listen.start('zpgeek/search/joblist.json')

                    # 构建URL并访问
                    url = f'https://www.zhipin.com/web/geek/jobs?query={kw}&city={city_code}'
                    self.log(f"访问页面: {url}")
                    self.dp.get(url)
                    time.sleep(5)  # 等待页面加载

                    city_count = 0
                    for page in range(1, pages + 1):
                        if not self.is_running:
                            break

                        self.log(f'正在采集 {ct} 第{page}页的数据内容')

                        # 模拟滚动加载
                        try:
                            # 定位最后一个职位元素
                            job_cards = self.dp.eles('css:.job-card-wrapper')
                            if job_cards:
                                last_job = job_cards[-1]
                                last_job.scroll.to_view(align='bottom')
                                time.sleep(3)

                                # 再小幅度滑一下
                                self.dp.run_js("window.scrollBy(0, 200)")
                                time.sleep(1)
                            else:
                                self.log(f'{ct} 第{page}页未找到职位元素，降级滑页面底部')
                                self.dp.run_js("window.scrollTo(0, document.body.scrollHeight)")
                                time.sleep(3)
                        except Exception as e:
                            self.log(f"滚动页面出错: {str(e)}")
                            self.dp.run_js("window.scrollTo(0, document.body.scrollHeight)")
                            time.sleep(3)

                        # 监听接口数据
                        resp = self.dp.listen.wait(timeout=15)
                        if not resp:
                            self.log(f'{ct} 第{page}页数据加载超时，重试1次...')
                            time.sleep(2)
                            resp = self.dp.listen.wait(timeout=10)
                            if not resp:
                                self.log(f'{ct} 第{page}页重试后仍未获取数据，跳过该页')
                                continue

                        # 处理JSON解析
                        body = resp.response.body
                        json_data = body if isinstance(body, dict) else json.loads(body)
                        jobList = json_data.get('zpData', {}).get('jobList', [])

                        if not jobList:
                            self.log(f'{ct} 第{page}页无数据，停止采集该城市')
                            break

                        page_count = 0
                        # 遍历处理数据
                        for job_data in jobList:
                            if not self.is_running:
                                break

                            # 解析数据并保存到数据库
                            saved = self.parse_drissionpage_data(job_data, kw, ct)
                            if saved:
                                page_count += 1
                                city_count += 1
                                total_count += 1

                        self.log(f"{ct} 第 {page} 页获取 {page_count} 条数据")

                        completed_tasks += 1
                        progress = (completed_tasks / total_tasks) * 100
                        self.progress['value'] = progress

                    self.log(f"完成爬取 {ct}: 共获取 {city_count} 条数据")

            return total_count

        except Exception as e:
            self.log(f"DrissionPage爬取失败: {str(e)}")
            return total_count

    def parse_drissionpage_data(self, job_data, keyword, city):
        """解析DrissionPage获取的数据"""
        try:
            # 提取字段
            job_title = job_data.get('jobName', '')
            job_salary = job_data.get('salaryDesc', '')
            job_city = job_data.get('cityName', '')
            area_district = job_data.get('areaDistrict', '')
            business_district = job_data.get('businessDistrict', '')
            job_company = job_data.get('brandName', '')

            # 经验要求
            job_experience = job_data.get('jobExperience', '')
            # 学历要求
            job_education = job_data.get('jobDegree', '')
            # 公司信息
            company_industry = job_data.get('brandIndustry', '')
            company_stage = job_data.get('brandStageName', '')
            company_scale = job_data.get('brandScaleName', '')

            # 技能标签
            skills = job_data.get('skills', [])
            job_skill = ' '.join(skills) if skills else ''

            # 福利标签
            welfare_list = job_data.get('welfareList', [])
            job_welfare = ' '.join(welfare_list) if welfare_list else ''

            # 合并地区信息
            job_area = f"{area_district} {business_district}".strip()

            # 合并公司信息
            company_info = f"{company_industry} {company_stage} {company_scale}".strip()

            # 合并职位要求
            job_requirement = f"{job_experience} {job_education}".strip()

        # 数据验证
            if not self.is_valid_data(job_title, job_company):

                 self.log(f"跳过无效数据: {job_title} - {job_company}")
                 return False

        # 保存到数据库
            return self.save_to_database(

                job_title, job_salary, job_area, job_company,
                company_info, job_skill, keyword, city,
                job_education, job_experience, "", job_welfare
        )

        except Exception as e:
             self.log(f"解析DrissionPage数据失败: {str(e)}")
             return False

    def run_selenium_spider(self, keyword, city, pages):
            """使用Selenium爬取数据（原逻辑）"""
            try:
                # 初始化浏览器
                driver = self.init_browser()
                if not driver:
                    return 0

                # 处理多关键词和多城市
                keywords = keyword.split()
                cities = city.split()

                total_tasks = len(keywords) * len(cities) * pages
                completed_tasks = 0
                total_count = 0

                for kw in keywords:
                    for ct in cities:
                        if not self.is_running:
                            break

                        self.log(f"开始爬取: {kw} - {ct}")

                        try:
                            # 爬取该城市的关键词数据
                            count = self.crawl_city_data_selenium(driver, kw, ct, pages)
                            total_count += count
                            self.log(f"完成爬取: {kw} - {ct}, 获取 {count} 条数据")

                        except Exception as e:
                            self.log(f"爬取失败 {kw} - {ct}: {str(e)}")

                        completed_tasks += pages
                        progress = (completed_tasks / total_tasks) * 100
                        self.progress['value'] = progress

                driver.quit()
                return total_count

            except Exception as e:
                self.log(f"Selenium爬取失败: {str(e)}")
                return 0

    def crawl_city_data_selenium(self, driver, keyword, city, pages):
        city_codes = {
            '北京': '101010100', '上海': '101020100', '广州': '101280100',
            '深圳': '101280600', '杭州': '101210100', '天津': '101030100',
            '西安': '101110100', '苏州': '101190400', '武汉': '101200100',
            '厦门': '101230200', '长沙': '101250100', '成都': '101270100',
            '郑州': '101180100', '重庆': '101040100', '佛山': '101280800',
            '合肥': '101220100', '济南': '101120100', '青岛': '101120200',
            '南京': '101190100', '东莞': '101281600', '福州': '101230100'
        }

        city_code = city_codes.get(city, '101010100')
        count = 0

        for page in range(1, pages + 1):
            if not self.is_running:
                break

            try:
                url = f'https://www.zhipin.com/c{city_code}/?query={keyword}&page={page}&ka=page-{page}'
                self.log(f"访问页面: {url}")

                driver.get(url)
                time.sleep(random.uniform(3, 5))

                html = driver.page_source
                soup = BeautifulSoup(html, 'html.parser')

                # === 新增：IP检测逻辑 ===
                home_inner = soup.find_all('div', {'class': 'inner home-inner'})
                job_list = soup.find_all('div', {"class": "job-primary"})

                # IP检测 - 如果被反爬就等待用户处理
                retry_count = 0
                max_retries = 3

                while not home_inner and retry_count < max_retries:
                    self.log(">>>当前IP可能被限制，等待用户处理...")

                    result = messagebox.askretrycancel("IP限制",
                                                       "当前IP可能被限制，请检查：\n"
                                                       "1. 是否需要手动验证\n"
                                                       "2. 是否需要更换网络\n"
                                                       "3. 点击重试继续尝试\n"
                                                       "点击取消停止爬取")

                    if result:  # 用户点击重试
                        retry_count += 1
                        self.log(f">>>第{retry_count}次重试...")

                        # 重新访问页面
                        driver.get(url)
                        time.sleep(random.uniform(5, 8))

                        html = driver.page_source
                        soup = BeautifulSoup(html, 'html.parser')
                        home_inner = soup.find_all('div', {'class': 'inner home-inner'})
                        job_list = soup.find_all('div', {"class": "job-primary"})

                        if home_inner:
                            self.log(">>>IP限制已解除，继续爬取")
                            break
                    else:  # 用户点击取消
                        self.log(">>>用户选择停止爬取")
                        self.is_running = False
                        return count

                # 如果重试后仍然被限制，跳过这个页面
                if not home_inner:
                    self.log(">>>IP限制仍未解除，跳过此页面")
                    continue
                # === 结束新增 ===

                # === 新增：随机滚动页面 ===
                self.__random_scroll(driver)

                page_count = self.parse_page_selenium(html, keyword, city)
                count += page_count

                self.log(f"第 {page} 页获取 {page_count} 条数据")
                time.sleep(random.uniform(4, 7))

            except Exception as e:
                self.log(f"第 {page} 页爬取失败: {str(e)}")
                time.sleep(5)
                continue

        return count

    def __random_scroll(self, driver):
        """随机滚动页面 - 模拟人类行为"""
        try:
            total_height = driver.execute_script("return document.body.scrollHeight")
            for i in range(3):
                target_height = random.randint(0, total_height)
                driver.execute_script(f"window.scrollTo(0, {target_height});")
                time.sleep(random.uniform(0.5, 1.5))
        except Exception as e:
            self.log(f"滚动页面失败: {str(e)}")

    def init_browser(self):
            """初始化Selenium浏览器"""
            try:
                chrome_options = Options()
                chrome_options.add_argument('--no-sandbox')
                chrome_options.add_argument('--disable-dev-shm-usage')
                chrome_options.add_argument(
                    'user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')
                chrome_options.add_argument('--disable-blink-features=AutomationControlled')
                chrome_options.add_experimental_option('excludeSwitches', ['enable-automation'])
                chrome_options.add_experimental_option('useAutomationExtension', False)

                driver_paths = [
                    'chromedriver.exe',
                    './chromedriver.exe',
                    'C:/Windows/chromedriver.exe'
                ]

                driver = None
                for path in driver_paths:
                    try:
                        service = Service(path)
                        driver = webdriver.Chrome(service=service, options=chrome_options)
                        break
                    except Exception as e:
                        continue

                if not driver:
                    self.log("无法找到ChromeDriver，请确保chromedriver在正确路径")
                    return None

                driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                    'source': 'Object.defineProperty(navigator, "webdriver", {get: () => undefined})'
                })

                driver.implicitly_wait(20)
                self.log("Selenium浏览器初始化成功")
                return driver

            except Exception as e:
                self.log(f"Selenium浏览器初始化失败: {str(e)}")
                return None

    def parse_page_selenium(self, html, keyword, city):
        count = 0
        try:
            soup = BeautifulSoup(html, 'html.parser')
            job_list = soup.find_all('div', {"class": "job-primary"})

            self.log(f"找到 {len(job_list)} 个职位元素")

            for job in job_list:
                if not self.is_running:
                    break

                try:
                    # 工作名称
                    job_name_elem = job.find('span', {"class": "job-name"})
                    job_name = job_name_elem.get_text().strip() if job_name_elem else "未知职位"

                    # 工作地点
                    job_area_elem = job.find('span', {'class': "job-area"})
                    job_area = job_area_elem.get_text().strip() if job_area_elem else "未知地区"

                    # 工作公司
                    company_elem = job.find('div', {'class': 'company-text'})
                    if company_elem:
                        company_name_elem = company_elem.find('h3', {'class': "name"})
                        job_company = company_name_elem.get_text().strip() if company_name_elem else "未知公司"

                        company_scale_elem = company_elem.find('p')
                        company_scale = company_scale_elem.get_text().strip() if company_scale_elem else ""
                    else:
                        job_company = "未知公司"
                        company_scale = ""

                    # 工作薪资
                    salary_elem = job.find('span', {'class': 'red'})
                    job_salary = salary_elem.get_text().strip() if salary_elem else "面议"

                    # 工作学历和经验
                    job_limit_elem = job.find('div', {'class': 'job-limit'})
                    if job_limit_elem:
                        limit_text = job_limit_elem.find('p').get_text().strip() if job_limit_elem.find('p') else ""
                        job_education = limit_text[-2:] if len(limit_text) >= 2 else ""
                        job_experience = limit_text
                    else:
                        job_education = ""
                        job_experience = ""

                    # 工作标签
                    job_label_elem = job.find('a', {'class': 'false-link'})
                    job_label = job_label_elem.get_text().strip() if job_label_elem else ""

                    # 技能要求
                    tags_elem = job.find('div', {'class': 'tags'})
                    job_skill = tags_elem.get_text().replace("\n", " ").strip() if tags_elem else ""

                    # 福利
                    welfare_elem = job.find('div', {'class': 'info-desc'})
                    job_welfare = welfare_elem.get_text().replace("，", " ").strip() if welfare_elem else ""

                    # === 修改：增强日志输出 ===
                    if self.is_valid_data(job_name, job_company):
                        saved = self.save_to_database(
                            job_name, job_salary, job_area, job_company,
                            company_scale, job_skill, keyword, city,
                            job_education, job_experience, job_label, job_welfare
                        )
                        if saved:
                            count += 1
                            self.log(f"成功解析: {job_name} - {job_company}")  # 新增成功日志
                        else:
                            self.log(f"跳过重复数据: {job_name} - {job_company}")
                    else:
                        self.log(f"跳过无效数据: {job_name} - {job_company}")

                except Exception as e:
                    # 减少错误日志输出，避免干扰
                    continue

        except Exception as e:
            self.log(f"解析页面失败: {str(e)}")

        return count

    def is_valid_data(self, job_title, job_company):
            """检查数据是否有效"""
            invalid_titles = ['未知职位', '职位', '', None]
            invalid_companies = ['未知公司', '公司', '', None]

            if job_title in invalid_titles or job_company in invalid_companies:
                return False

            if len(job_title) < 2 or len(job_company) < 2:
                return False

            return True

    def save_to_database(self, name, salary, area, company, scale, skills, keyword, city,
                             education="", experience="", label="", welfare=""):
            """保存数据到数据库"""
            try:
                if not self.db_connection:
                    self.connect_database()
                    if not self.db_connection:
                        return False

                cursor = self.db_connection.cursor()

                # 使用 INSERT IGNORE 来避免重复数据
                insert_sql = """
                INSERT IGNORE INTO bosszp (job_title, job_salary, job_lable, job_company,
                                  job_company_tag, job_acquire, company_city, job_skill, keyword)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """

                cursor.execute(insert_sql, (
                    name, salary, area, company,
                    scale, skills, city, skills, keyword
                ))

                affected_rows = cursor.rowcount
                self.db_connection.commit()
                cursor.close()

                return affected_rows > 0

            except Exception as e:
                self.log(f"保存数据失败: {str(e)}")
                if self.db_connection:
                    self.db_connection.rollback()
                return False

    def clear_data(self):
            """清空数据"""
            if messagebox.askyesno("确认", "确定要清空所有爬取数据吗？"):
                try:
                    cursor = self.db_connection.cursor()
                    cursor.execute("DELETE FROM bosszp")
                    self.db_connection.commit()
                    cursor.close()
                    self.log("数据已清空")
                    messagebox.showinfo("成功", "数据清空完成")
                except Exception as e:
                    messagebox.showerror("错误", f"清空数据失败: {str(e)}")

    def view_data(self):
            """查看数据"""
            try:
                cursor = self.db_connection.cursor()
                cursor.execute("SELECT COUNT(*) FROM bosszp")
                count = cursor.fetchone()[0]
                cursor.close()

                messagebox.showinfo("数据统计", f"当前数据库中共有 {count} 条招聘数据")

            except Exception as e:
                messagebox.showerror("错误", f"查看数据失败: {str(e)}")

    def __del__(self):
            """析构函数，确保资源被正确释放"""
            if hasattr(self, 'dp') and self.dp:
                try:
                    self.dp.quit()
                except Exception as exc:
                    # Destructors cannot surface errors to the GUI, but the
                    # cleanup failure must remain observable in application logs.
                    logger.warning("Failed to close DrissionPage: %s", exc)


@staticmethod
def run_crawl_task(keyword, city, pages, user_id=1, task_id=None, headless=True):
    """供Flask调用的爬虫任务函数"""
    try:
        # 创建临时实例（不显示GUI）
        class TempSpider:
            def __init__(self):
                self.db_connection = None
                self.is_running = True
                self.connect_database()

            def connect_database(self):
                """连接数据库"""
                try:
                    self.db_connection = pymysql.connect(**DB_CONFIG)
                    return True
                except Exception as e:
                    print(f"数据库连接失败: {str(e)}")
                    return False

            def save_to_database(self, name, salary, area, company, scale, skills, keyword, city,
                                 education="", experience="", label="", welfare=""):
                """保存数据到数据库"""
                try:
                    if not self.db_connection:
                        return False

                    cursor = self.db_connection.cursor()
                    insert_sql = """
                    INSERT IGNORE INTO bosszp (job_title, job_salary, job_lable, job_company,
                                      job_company_tag, company_city, job_skill, keyword, user_id, task_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """
                    cursor.execute(insert_sql, (
                        name, salary, area, company, scale, city, skills, keyword, user_id, task_id
                    ))
                    affected_rows = cursor.rowcount
                    self.db_connection.commit()
                    cursor.close()
                    return affected_rows > 0
                except Exception as e:
                    print(f"保存数据失败: {str(e)}")
                    return False

            def run_drissionpage_spider(self, keyword, city, pages, user_id, task_id):
                """简化版的DrissionPage爬虫"""
                try:
                    from DrissionPage import ChromiumPage

                    # 初始化浏览器
                    dp = ChromiumPage()
                    dp.get('https://www.zhipin.com/web/geek/jobs?query=java&city=101010100')

                    city_codes = {
                        '北京': '101010100', '上海': '101020100', '广州': '101280100',
                        '深圳': '101280600', '杭州': '101210100', '天津': '101030100',
                        '西安': '101110100', '苏州': '101190400', '武汉': '101200100',
                        '厦门': '101230200', '长沙': '101250100', '成都': '101270100',
                        '郑州': '101180100', '重庆': '101040100', '佛山': '101280800',
                        '合肥': '101220100', '济南': '101120100', '青岛': '101120200',
                        '南京': '101190100', '东莞': '101281600', '福州': '101230100'
                    }

                    city_code = city_codes.get(city, '101010100')
                    count = 0

                    # 监听接口
                    dp.listen.start('zpgeek/search/joblist.json')

                    url = f'https://www.zhipin.com/web/geek/jobs?query={keyword}&city={city_code}'
                    dp.get(url)
                    time.sleep(5)

                    for page in range(1, pages + 1):
                        if not self.is_running:
                            break

                        print(f'正在采集第{page}页数据')

                        # 滚动页面
                        try:
                            job_cards = dp.eles('css:.job-card-wrapper')
                            if job_cards:
                                last_job = job_cards[-1]
                                last_job.scroll.to_view(align='bottom')
                                time.sleep(3)
                            else:
                                dp.run_js("window.scrollTo(0, document.body.scrollHeight)")
                                time.sleep(3)
                        except Exception as e:
                            dp.run_js("window.scrollTo(0, document.body.scrollHeight)")
                            time.sleep(3)

                        # 获取数据
                        resp = dp.listen.wait(timeout=15)
                        if not resp:
                            time.sleep(2)
                            resp = dp.listen.wait(timeout=10)
                            if not resp:
                                continue

                        # 解析数据
                        body = resp.response.body
                        json_data = body if isinstance(body, dict) else json.loads(body)
                        jobList = json_data.get('zpData', {}).get('jobList', [])

                        for job_data in jobList:
                            if not self.is_running:
                                break

                            # 解析并保存数据
                            if self.parse_and_save_job_data(job_data, keyword, city, user_id, task_id):
                                count += 1

                        print(f"第{page}页获取{len(jobList)}条数据")
                        time.sleep(3)

                    dp.quit()
                    return count

                except Exception as e:
                    print(f"DrissionPage爬取失败: {str(e)}")
                    return 0

            def parse_and_save_job_data(self, job_data, keyword, city, user_id, task_id):
                """解析并保存职位数据"""
                try:
                    job_title = job_data.get('jobName', '')
                    job_salary = job_data.get('salaryDesc', '')
                    job_company = job_data.get('brandName', '')
                    company_city = job_data.get('cityName', '')

                    # 数据验证
                    if not job_title or not job_company or len(job_title) < 2 or len(job_company) < 2:
                        return False

                    # 保存到数据库
                    return self.save_to_database(
                        job_title, job_salary, "", job_company,
                        "", "", keyword, city, "", "", "", "",
                        user_id, task_id
                    )

                except Exception as e:
                    print(f"解析数据失败: {str(e)}")
                    return False

        # 执行爬取
        spider = TempSpider()
        if spider.db_connection:
            count = spider.run_drissionpage_spider(keyword, city, pages, user_id, task_id)
            spider.db_connection.close()
            return count
        else:
            return 0

    except Exception as e:
        print(f"爬虫任务执行失败: {str(e)}")
        return 0


def run_crawl(self,keyword, city, pages, user_id=1, task_id=None, mode='drissionpage', headless=True):
    """供外部调用的爬取函数 - 修复参数接收问题"""
    try:
        print(f"开始爬取: {keyword} - {city}, 页数: {pages}, 模式: {mode}")

        # 这里需要根据您的爬虫程序结构进行调整
        # 如果是基于GUI的，可能需要创建无头版本
        if mode == 'drissionpage':
            # 调用drissionpage模式
            count = self.run_drissionpage_spider(keyword, city, pages)
        else:
            # 调用selenium模式
            count = self.run_selenium_spider(keyword, city, pages)

        print(f"爬取完成，获取 {count} 条数据")
        return count

    except Exception as e:
        print(f"爬取过程错误: {str(e)}")
        import traceback
        traceback.print_exc()
        return 0


def main():
    """主函数，支持命令行参数"""
    """供Flask调用的主函数"""
    import argparse
    parser = argparse.ArgumentParser(description='Boss直聘爬虫')
    parser.add_argument('--keyword', type=str, required=True, help='搜索关键词')
    parser.add_argument('--city', type=str, required=True, help='城市')
    parser.add_argument('--pages', type=int, default=5, help='爬取页数')
    parser.add_argument('--user_id', type=int, default=1, help='用户ID')
    parser.add_argument('--task_id', type=int, help='任务ID')
    parser.add_argument('--mode', type=str, default='drissionpage', help='爬取模式')

    args = parser.parse_args()

    # 直接调用爬虫函数
    count = BossZPSpiderGUI.run_crawl_task(
        keyword=args.keyword,
        city=args.city,
        pages=args.pages,
        user_id=args.user_id,
        task_id=args.task_id,
        headless=True
    )

    print(f"爬取完成，共获取{count}条数据")
    return count


# 添加直接调用的函数
def run_from_api(keyword, city, pages, user_id=1, task_id=None, mode='drissionpage', headless=True):
    """API调用的入口函数"""
    return run_crawl(keyword, city, pages, user_id, task_id, mode, headless)

if __name__ == "__main__":
    # 如果是命令行直接运行，启动GUI
    root = tk.Tk()
    app = BossZPSpiderGUI(root)
    root.mainloop()
