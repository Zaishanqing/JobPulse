# 统一招聘爬虫后端 API

> 文档类型：模块入口
> 维护状态：active
> 适用范围：`JobPulse/services/crawler/`
> 事实源：模块代码、配置与测试
> 责任角色：爬虫维护者
> 最后复核：2026-08-21

本模块提供招聘站点采集、任务 API、本地存储和离线 JD Bundle 导出。采集数据不直接
写入主后端数据库。

## 主要功能

- 管理站点登录、采集任务、进度与失败信息。
- 将招聘页面整理为统一 JD 记录并保存在爬虫侧数据库。
- 导出带 manifest、校验信息和来源记录的离线 JD Bundle。
- 提供多公司批量采集工具；站点补丁放在 `patches/`。

## 最短启动

前置条件：安装依赖并按[配置说明](docs/configuration.md)准备数据库。执行目录：
`<repo-root>/JobPulse/services/crawler`。

```powershell
pip install -e ../../packages/contracts
pip install -e .
python run.py
```

成功信号：HTTP API 可访问。实际抓取还需要目标站点账号、Cookie、网络和授权。

## 最短验证

```powershell
python -m pytest
```

## 当前限制

站点登录、验证码、页面结构和访问限制可能变化。robots.txt 和使用条款提示不构成法律
结论；数据授权、个人信息处理和适用法律需人工合规审查。

## 文档

- [模块文档索引](docs/index.md)
- [JD 离线 Bundle 导入](../../docs/user-guide/data-import.md)
