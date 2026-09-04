"""统一爬虫后端 API 启动入口。

启动前必须安装::

    pip install -e ../../packages/contracts
    pip install -e .

"""
if __name__ == '__main__':
    import uvicorn
    uvicorn.run(
        'unified_api.main:app',
        host='0.0.0.0',
        port=8800,
        reload=True,
    )
