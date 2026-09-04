# crawler/simple_spider.py
import pymysql
import time
import json
from DrissionPage import ChromiumPage

from unified_api.config import DB_CONFIG


def run_crawl(keyword, city, pages, user_id=1, task_id=None):
    """简化版爬虫，供Flask调用"""
    try:
        # 数据库连接 — 复用统一安全配置
        db_connection = pymysql.connect(**DB_CONFIG)

        count = 0
        dp = ChromiumPage()

        try:
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

            # 监听接口
            dp.listen.start('zpgeek/search/joblist.json')
            url = f'https://www.zhipin.com/web/geek/jobs?query={keyword}&city={city_code}'
            dp.get(url)
            time.sleep(5)

            for page in range(1, pages + 1):
                print(f'正在采集第{page}页数据')

                # 滚动页面
                dp.run_js("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(3)

                # 获取数据
                resp = dp.listen.wait(timeout=15)
                if not resp:
                    continue

                # 解析数据
                body = resp.response.body
                json_data = body if isinstance(body, dict) else json.loads(body)
                jobList = json_data.get('zpData', {}).get('jobList', [])

                for job_data in jobList:
                    if save_job_data(db_connection, job_data, keyword, city, user_id, task_id):
                        count += 1

                print(f"第{page}页获取{len(jobList)}条数据")
                time.sleep(2)

        finally:
            dp.quit()
            db_connection.close()

        return count

    except Exception as e:
        print(f"爬虫执行失败: {str(e)}")
        return 0


def save_job_data(db_connection, job_data, keyword, city, user_id, task_id):
    """保存职位数据到数据库"""
    try:
        cursor = db_connection.cursor()

        job_title = job_data.get('jobName', '')
        job_salary = job_data.get('salaryDesc', '')
        job_company = job_data.get('brandName', '')
        company_city = job_data.get('cityName', '')

        # 数据验证
        if not job_title or not job_company:
            return False

        insert_sql = """
        INSERT IGNORE INTO bosszp (job_title, job_salary, job_company, company_city, keyword, user_id, task_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        cursor.execute(insert_sql, (
            job_title, job_salary, job_company, company_city, keyword, user_id, task_id
        ))

        affected_rows = cursor.rowcount
        db_connection.commit()
        cursor.close()

        return affected_rows > 0

    except Exception as e:
        print(f"保存数据失败: {str(e)}")
        return False
