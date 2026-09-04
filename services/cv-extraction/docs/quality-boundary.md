# CV 抽取质量边界

> 文档类型：质量
> 维护状态：active
> 适用范围：`services/cv-extraction/`
> 事实源：模块代码、配置、Schema 与测试
> 责任角色：模块维护者
> 最后复核：2026-07-27

## 质量解释

- 流水线成功只表示 Schema、Evidence 和确定性校验通过，不代表内容准确率或召回率。
- `alignment != exact` 的 Evidence 不允许进入成功结果。
- `unresolved` 归一化项必须进入审查，不会静默映射。
- V2 不读取或改写 V1 输出；下一次真实运行应使用新的 `run-id`。
