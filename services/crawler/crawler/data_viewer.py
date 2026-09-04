import os
import sys
import tkinter as tk
from tkinter import ttk, messagebox
import pymysql
import pandas as pd
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.pyplot as plt
import seaborn as sns
from datetime import datetime
import matplotlib

# Ensure the parent directory is on the path so crawler scripts can
# import the shared config module.
from unified_api.config import DB_CONFIG

# 设置matplotlib使用中文字体
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']  # 用来正常显示中文标签
plt.rcParams['axes.unicode_minus'] = False  # 用来正常显示负号

class DataViewer:
    def __init__(self, parent):
        self.parent = parent
        self.db_connection = None
        self.connect_database()

    def connect_database(self):
        """连接数据库"""
        try:
            self.db_connection = pymysql.connect(**DB_CONFIG)
            return True
        except Exception as e:
            messagebox.showerror("错误", f"数据库连接失败: {str(e)}")
            return False

    def show_data_viewer(self):
        """显示数据查看窗口"""
        if not self.connect_database():
            return

        viewer_window = tk.Toplevel(self.parent)
        viewer_window.title("数据查看与分析")
        viewer_window.geometry("1000x700")
        viewer_window.transient(self.parent)

        # 创建标签页
        notebook = ttk.Notebook(viewer_window)
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 数据表格标签页
        table_frame = ttk.Frame(notebook)
        notebook.add(table_frame, text="数据表格")

        # 数据分析标签页
        analysis_frame = ttk.Frame(notebook)
        notebook.add(analysis_frame, text="数据分析")

        # 数据清洗标签页
        cleaning_frame = ttk.Frame(notebook)
        notebook.add(cleaning_frame, text="数据清洗")

        # 设置表格标签页
        self.setup_table_tab(table_frame)

        # 设置分析标签页
        self.setup_analysis_tab(analysis_frame)

        # 设置数据清洗标签页
        self.setup_cleaning_tab(cleaning_frame)

    def setup_cleaning_tab(self, parent):
        """设置数据清洗标签页"""
        # 清洗选项框架
        options_frame = ttk.LabelFrame(parent, text="清洗选项", padding="10")
        options_frame.pack(fill=tk.X, padx=5, pady=5)

        # 重复数据清洗
        ttk.Label(options_frame, text="重复数据清洗:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.dup_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options_frame, text="删除完全重复的数据", variable=self.dup_var).grid(row=0, column=1,
                                                                                              sticky=tk.W, pady=2)

        # 空值数据清洗
        ttk.Label(options_frame, text="空值数据清洗:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.null_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options_frame, text="删除关键字段为空的数据", variable=self.null_var).grid(row=1, column=1,
                                                                                                   sticky=tk.W, pady=2)

        # 无效数据清洗
        ttk.Label(options_frame, text="无效数据清洗:").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.invalid_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options_frame, text="删除明显无效的数据", variable=self.invalid_var).grid(row=2, column=1,
                                                                                                  sticky=tk.W, pady=2)

        # 清洗按钮
        ttk.Button(options_frame, text="执行数据清洗", command=self.execute_advanced_cleaning).grid(row=3, column=0,
                                                                                                    columnspan=2,
                                                                                                    pady=10)

        # 清洗结果框架
        result_frame = ttk.LabelFrame(parent, text="清洗结果", padding="10")
        result_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.cleaning_result = tk.Text(result_frame, height=15, width=80)
        scrollbar = ttk.Scrollbar(result_frame, orient=tk.VERTICAL, command=self.cleaning_result.yview)
        self.cleaning_result.configure(yscrollcommand=scrollbar.set)

        self.cleaning_result.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    def execute_advanced_cleaning(self):
        """执行高级数据清洗"""
        try:
            cursor = self.db_connection.cursor()

            # 统计清洗前数据量
            cursor.execute("SELECT COUNT(*) FROM bosszp")
            before_count = cursor.fetchone()[0]

            total_removed = 0
            cleaning_log = []

            # 1. 删除完全重复的数据
            if self.dup_var.get():
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
                dup_removed = cursor.rowcount
                cleaning_log.append(f"删除重复数据: {dup_removed} 条")
                total_removed += dup_removed

            # 2. 删除关键字段为空的数据
            if self.null_var.get():
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
                null_removed = cursor.rowcount
                cleaning_log.append(f"删除空值数据: {null_removed} 条")
                total_removed += null_removed

            # 3. 删除明显无效的数据
            if self.invalid_var.get():
                cursor.execute("""
                    DELETE FROM bosszp
                    WHERE
                        job_title LIKE '%未知%' OR
                        job_company LIKE '%未知%' OR
                        job_title = '职位' OR
                        job_company = '公司' OR
                        LENGTH(job_title) < 2 OR
                        LENGTH(job_company) < 2
                """)
                invalid_removed = cursor.rowcount
                cleaning_log.append(f"删除无效数据: {invalid_removed} 条")
                total_removed += invalid_removed

            # 统计清洗后数据量
            cursor.execute("SELECT COUNT(*) FROM bosszp")
            after_count = cursor.fetchone()[0]

            self.db_connection.commit()
            cursor.close()

            # 显示清洗结果
            result_text = f"数据清洗完成！\n\n"
            result_text += f"清洗前数据量: {before_count} 条\n"
            result_text += f"清洗后数据量: {after_count} 条\n"
            result_text += f"总共删除: {total_removed} 条数据\n\n"
            result_text += "详细清洗记录:\n"
            for log in cleaning_log:
                result_text += f"  - {log}\n"

            self.cleaning_result.delete(1.0, tk.END)
            self.cleaning_result.insert(1.0, result_text)

        except Exception as e:
            messagebox.showerror("错误", f"数据清洗失败: {str(e)}")

    def setup_table_tab(self, parent):
        """设置数据表格标签页"""
        # 查询控制框架
        control_frame = ttk.Frame(parent)
        control_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(control_frame, text="关键词:").grid(row=0, column=0, padx=(0, 5))
        self.search_keyword = ttk.Entry(control_frame, width=15)
        self.search_keyword.grid(row=0, column=1, padx=(0, 10))

        ttk.Label(control_frame, text="城市:").grid(row=0, column=2, padx=(0, 5))
        self.search_city = ttk.Entry(control_frame, width=15)
        self.search_city.grid(row=0, column=3, padx=(0, 10))

        ttk.Button(control_frame, text="查询", command=self.load_data).grid(row=0, column=4, padx=(0, 10))
        ttk.Button(control_frame, text="导出CSV", command=self.export_csv).grid(row=0, column=5)

        # 数据表格
        table_container = ttk.Frame(parent)
        table_container.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 创建表格
        columns = ("ID", "职位", "薪资", "公司", "城市", "关键词", "时间")
        self.data_tree = ttk.Treeview(table_container, columns=columns, show="headings", height=20)

        # 设置列标题
        for col in columns:
            self.data_tree.heading(col, text=col)
            self.data_tree.column(col, width=100)

        # 滚动条
        scrollbar = ttk.Scrollbar(table_container, orient=tk.VERTICAL, command=self.data_tree.yview)
        self.data_tree.configure(yscrollcommand=scrollbar.set)

        self.data_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 初始加载数据
        self.load_data()

    def setup_analysis_tab(self, parent):
        """设置数据分析标签页"""
        # 分析控制框架
        control_frame = ttk.Frame(parent)
        control_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Button(control_frame, text="薪资分析", command=self.analyze_salary).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(control_frame, text="城市分布", command=self.analyze_city).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(control_frame, text="关键词分析", command=self.analyze_keyword).pack(side=tk.LEFT)

        # 图表框架
        self.chart_frame = ttk.Frame(parent)
        self.chart_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def load_data(self):
        """加载数据到表格"""
        try:
            cursor = self.db_connection.cursor()

            # 构建查询条件
            where_conditions = []
            params = []

            keyword = self.search_keyword.get().strip()
            city = self.search_city.get().strip()

            if keyword:
                where_conditions.append("keyword LIKE %s")
                params.append(f"%{keyword}%")

            if city:
                where_conditions.append("company_city LIKE %s")
                params.append(f"%{city}%")

            where_clause = "WHERE " + " AND ".join(where_conditions) if where_conditions else ""

            query = f"""
            SELECT id, job_title, job_salary, job_company, company_city, keyword, create_time
            FROM bosszp
            {where_clause}
            ORDER BY create_time DESC
            LIMIT 500
            """

            cursor.execute(query, params)
            rows = cursor.fetchall()
            cursor.close()

            # 清空表格
            for item in self.data_tree.get_children():
                self.data_tree.delete(item)

            # 填充数据
            for row in rows:
                self.data_tree.insert("", tk.END, values=row)

        except Exception as e:
            messagebox.showerror("错误", f"加载数据失败: {str(e)}")

    def export_csv(self):
        """导出数据到CSV"""
        try:
            cursor = self.db_connection.cursor()
            cursor.execute("""
                SELECT job_title, job_salary, job_company, company_city, keyword, job_skill, create_time
                FROM bosszp
            """)
            rows = cursor.fetchall()
            cursor.close()

            if not rows:
                messagebox.showwarning("警告", "没有数据可导出")
                return

            # 创建DataFrame
            df = pd.DataFrame(rows, columns=['职位', '薪资', '公司', '城市', '关键词', '技能', '创建时间'])

            # 生成文件名
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"bosszp_data_{timestamp}.csv"

            # 导出CSV
            df.to_csv(filename, index=False, encoding='utf-8-sig')
            messagebox.showinfo("成功", f"数据已导出到: {filename}")

        except Exception as e:
            messagebox.showerror("错误", f"导出失败: {str(e)}")

    def analyze_salary(self):
        """薪资分析"""
        try:
            cursor = self.db_connection.cursor()
            cursor.execute("""
                SELECT job_salary, COUNT(*) as count
                FROM bosszp
                WHERE job_salary IS NOT NULL AND job_salary != ''
                GROUP BY job_salary
                ORDER BY count DESC
                LIMIT 10
            """)
            salary_data = cursor.fetchall()
            cursor.close()

            if not salary_data:
                messagebox.showwarning("警告", "没有薪资数据可分析")
                return

            # 准备数据
            salaries = [str(item[0]) for item in salary_data]
            counts = [item[1] for item in salary_data]

            # 创建图表
            self.create_bar_chart(salaries, counts, "薪资分布分析", "薪资范围", "职位数量")

        except Exception as e:
            messagebox.showerror("错误", f"薪资分析失败: {str(e)}")

    def analyze_city(self):
        """城市分布分析"""
        try:
            cursor = self.db_connection.cursor()
            cursor.execute("""
                SELECT company_city, COUNT(*) as count
                FROM bosszp
                WHERE company_city IS NOT NULL AND company_city != ''
                GROUP BY company_city
                ORDER BY count DESC
                LIMIT 10
            """)
            city_data = cursor.fetchall()
            cursor.close()

            if not city_data:
                messagebox.showwarning("警告", "没有城市数据可分析")
                return

            # 准备数据
            cities = [str(item[0]) for item in city_data]
            counts = [item[1] for item in city_data]

            # 创建图表
            self.create_bar_chart(cities, counts, "城市分布分析", "城市", "职位数量")

        except Exception as e:
            messagebox.showerror("错误", f"城市分析失败: {str(e)}")

    def analyze_keyword(self):
        """关键词分析"""
        try:
            cursor = self.db_connection.cursor()
            cursor.execute("""
                SELECT keyword, COUNT(*) as count
                FROM bosszp
                WHERE keyword IS NOT NULL AND keyword != ''
                GROUP BY keyword
                ORDER BY count DESC
                LIMIT 10
            """)
            keyword_data = cursor.fetchall()
            cursor.close()

            if not keyword_data:
                messagebox.showwarning("警告", "没有关键词数据可分析")
                return

            # 准备数据
            keywords = [str(item[0]) for item in keyword_data]
            counts = [item[1] for item in keyword_data]

            # 创建图表
            self.create_bar_chart(keywords, counts, "关键词分析", "关键词", "职位数量")

        except Exception as e:
            messagebox.showerror("错误", f"关键词分析失败: {str(e)}")

    def create_bar_chart(self, x_data, y_data, title, xlabel, ylabel):
        """创建柱状图"""
        # 清除之前的图表
        for widget in self.chart_frame.winfo_children():
            widget.destroy()

        # 创建图形
        plt.style.use('seaborn-v0_8')
        fig, ax = plt.subplots(figsize=(10, 6))
        # 设置中文字体
        plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei']
        plt.rcParams['axes.unicode_minus'] = False

        # 创建柱状图
        bars = ax.bar(x_data, y_data, color='skyblue', alpha=0.7)

        # 设置标题和标签
        ax.set_title(title, fontsize=14, fontweight='bold', fontfamily='SimHei')
        ax.set_xlabel(xlabel, fontsize=12, fontfamily='SimHei')
        ax.set_ylabel(ylabel, fontsize=12, fontfamily='SimHei')

        # 在柱子上显示数值
        for bar in bars:
            height = bar.get_height()
            ax.text(bar.get_x() + bar.get_width() / 2., height,
                    f'{int(height)}', ha='center', va='bottom', fontfamily='SimHei')

        # 旋转x轴标签
        plt.xticks(rotation=45, ha='right', fontfamily='SimHei')

        # 调整布局
        plt.tight_layout()

        # 嵌入到Tkinter
        canvas = FigureCanvasTkAgg(fig, self.chart_frame)
        canvas.draw()
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)


if __name__ == "__main__":
    root = tk.Tk()
    viewer = DataViewer(root)
    root.mainloop()