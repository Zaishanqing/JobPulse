# 模块扩展指南

> 文档类型：指南（guide）
> 维护状态：生效（active）
> 适用范围：主后端与各独立服务
> 责任人：架构维护者
> 最后复核：2026-07-18

本指南只说明如何在现有整洁架构中扩展模块。新增业务能力需要另行确认需求；不得借扩展过程引入新的算法、Provider 或基础设施。

## 1. 先确定上下文

先在 `backend-architecture.md` 的上下文描述中确定所有者。若能力属于现有上下文，扩展其同名文件族；只有业务语言、生命周期和事务边界均独立时才新增上下文。跨上下文需求先定义稳定 DTO 与窄 Port，禁止直接调用另一个上下文的用例。

## 2. 按向内依赖实现

建议顺序：

1. 在 Domain 增加领域类型和规则，不导入框架、配置、数据库或 Provider。
2. 在 Application 增加命令/结果 DTO、Port、用例和事务编排，不生成 HTTP JSON。
3. 在 Infrastructure 实现 Repository、UoW 或 Provider Port，并在边界完成 ORM/JSON 转换。
4. 在 API 增加 Pydantic 请求模型、认证信息映射、异常翻译和响应映射。
5. 仅在该后端唯一组合根注册实现和用例。

核心对象不得使用 `dict[str, Any]`。展示型 JSON 可以保留；一旦数据参与计算、状态变更、发布判断或跨上下文传输，就必须定义明确类型。

## 3. API 与装配限制

API 中禁止：

- 创建 Session、UoW、Repository、Provider 或 Use Case；
- 调用 ORM 查询或控制事务；
- 判断角色集合、发布门禁或状态流转；
- 导入 Infrastructure、ORM 模型或旧 `app.services`。

API 依赖函数只能从应用状态或容器取出组合根已注册的对象。权限规则由 Domain/Application 执行，API 将拒绝异常映射为既有状态码和响应结构。

## 4. 验证清单

每次扩展至少验证：

- 上下文单元测试和既有 API 契约测试；
- 各后端架构测试与静态检查；
- 受影响的 Alembic 升级、降级和再次升级；
- 前端构建；
- `docker compose config`、镜像构建、迁移启动、健康检查。

若 Docker Registry 网络不可达，应记录为环境阻塞，不能把 Compose 配置解析成功当作镜像闭环成功。

## 5. 禁止回退

不新增 Service 层或兼容实现，不复制另一上下文的领域规则，不让 Application 返回 API 序列化字典。旧兼容文件只有在外部测试仍需导入时保留；生产引用归零后，应记录删除条件并最终删除。
