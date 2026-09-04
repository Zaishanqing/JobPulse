# 大厂招聘JD爬虫

从中国知名大厂的官方招聘网站爬取正在招聘的职位JD数据，输出为结构化的Excel文件。

## 快速开始

```bash
pip install -r requirements.txt
playwright install chromium
```

### 列出所有配置的公司

```bash
python main.py --list
```

### 爬取单家公司

```bash
python main.py --company 字节跳动
```

### 爬取所有可爬取的公司

```bash
python main.py --output 招聘JD数据.xlsx
```

### 批量JSON输出

```bash
python batch_scrape_json.py
```

逐公司输出独立JSON文件，并合并为 `output/all_companies.json`。

## 当前可爬取的公司

以下公司已通过Playwright浏览器自动化验证，可成功爬取职位数据：

| 公司 | 职位数 | JD完整度 | 备注 |
|------|--------|----------|------|
| 字节跳动 | ~200 | 大部分有JD | API + 详情页补充 |
| 京东 | ~200 | JD完整 | API直出 |
| 快手 | ~200 | JD完整 | API直出 |
| 小红书 | 200 | JD完整 | API直出 |
| 美团 | 200 | JD完整 | API直出 |
| B站 | 200 | JD完整 | API直出 |
| 米哈游 | 200 | JD完整 | API列表 + 详情API补充 |
| vivo | 168 | JD完整 | API直出 |
| 网易 | 100 | JD完整 | API直出 |
| 小米 | 110 | 无JD文本 | 仅列表API，无详情接口 |
| 大疆 | 60 | 部分有JD | DOM解析 |
| 海康威视 | 37 | 无JD文本 | DOM解析，需详情API |
| Keep | 36 | JD完整 | 北森(Beisen)平台API |
| 虎牙 | 4 | JD完整 | DOM解析 |
| 比亚迪 | 2 | 无JD文本 | DOM解析，API未检测到 |
| 贝壳找房 | 2 | 无JD文本 | DOM解析，API未检测到 |

> 共 **16** 家公司可爬取，单次全量约 1,900+ 条职位。每家公司上限200条。
>
> 标注"JD完整"的职位有完整的岗位职责和任职要求文本；缺少JD的职位仅包含职位名称、城市、薪资等基本元信息。

## 不可用的平台/公司

| 平台 | 覆盖公司 | 原因 |
|------|---------|------|
| Moka (摩卡) | SHEIN、滴滴、搜狐、新浪微博、知乎 等 | API返回加密数据，需逆向客户端JS |
| 飞书招聘 | 小鹏汽车、蔚来、理想汽车、得物 等 | API需 `_signature` 签名，由混淆JS生成 |
| 百度招聘 | 百度 | API端点疑似变更，返回空数据 |
| 腾讯招聘 | 腾讯 | API端点未确认，需进一步调研 |
| 智联ATS | 科大讯飞 | 子域名各异，尚未逐一适配 |

## 输出字段

爬取结果输出为Excel文件（`.xlsx`），包含两个工作表：

### "全部职位" 工作表

| 字段 | 说明 |
|------|------|
| 公司名称 | 来源公司 |
| 职位名称 | 招聘岗位名称 |
| 职位ID | 平台内部唯一标识 |
| 所属部门 | 招聘部门 |
| 工作城市 | 工作所在城市 |
| 区/县 | 区或县级行政区划 |
| 工作类型 | 全职/实习/兼职等 |
| 经验要求 | 工作年限要求（规范化为：不限/1年以下/1-3年/3-5年/5-10年/10年以上） |
| 学历要求 | 学历要求（规范化为：不限/大专/本科/硕士/博士） |
| 最低月薪(K) | 月薪下限（千元），面议为0 |
| 最高月薪(K) | 月薪上限（千元），面议为0 |
| 薪资原文 | 原始薪资描述文本 |
| JD全文 | 完整职位描述 |
| 岗位职责 | 拆分后的职责部分 |
| 任职要求 | 拆分后的要求部分 |
| 技能标签 | 技能关键词 |
| 福利待遇 | 福利信息 |
| 发布时间 | 职位发布日期 |
| 原始链接 | 职位详情页URL |
| 来源平台 | 爬取平台标识 |
| 爬取时间 | 数据采集时间戳 |

### "统计" 工作表

汇总各公司的职位数量。

## 项目结构

```
multi_company_scraper/
  main.py                  # CLI入口
  batch_scrape_json.py     # 批量JSON输出脚本
  collector.py             # 数据收集器
  excel_writer.py          # Excel输出
  normalizer.py            # 数据规范化（薪资/经验/学历）
  http_client.py           # 限速HTTP客户端
  config/
    companies.yaml         # 公司配置
  models/
    job_data.py            # JobData数据类
    company_config.py      # CompanyConfig数据类
  scrapers/
    base.py                # 爬虫抽象基类
    dispatcher.py          # 爬虫调度器
    playwright_scraper.py  # Playwright通用爬虫（主力）
    moka_scraper.py        # Moka平台爬虫（暂不可用）
    feishu_scraper.py      # 飞书招聘平台爬虫（暂不可用）
    baidu_scraper.py       # 百度招聘爬虫（暂不可用）
    tencent_scraper.py     # 腾讯招聘爬虫（暂不可用）
    netease_scraper.py     # 网易HTML解析爬虫（备用）
    zhiye_scraper.py       # 智联ATS爬虫（暂不可用）
  output/                  # 爬取结果输出
```

## 工作原理

PlaywrightScraper 使用双路径策略：

1. **API拦截（主路径）**：启动无头浏览器访问招聘页面，拦截XHR/JSON响应，自动检测包含职位数据的API。检测到后直接调用API分页获取结构化数据并丰富JD详情。

2. **DOM解析（回退）**：当未检测到API时，使用CSS选择器从渲染后的页面DOM中提取职位信息并翻页。

## 注意事项

- 全量爬取16家公司约需30-50分钟，具体取决于网络状况
- Playwright爬虫启动无头Chromium，请确保已执行 `playwright install chromium`
- 部分公司可能因反爬措施或网络波动临时失败，自动重试机制会延长等待时间重新检测API
- 请遵守各公司网站的 robots.txt 和服务条款，合理控制请求频率
- 本工具仅供学习和研究使用
