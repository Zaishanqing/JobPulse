# 访问控制与账号安全

> 文档类型：安全
> 维护状态：active
> 适用范围：`apps/api/`
> 事实源：认证授权代码、迁移与安全测试
> 责任角色：安全维护者
> 最后复核：2026-08-09

## 1. 系统角色

| 角色 | 可见性 | 创建方式 |
|---|---|---|
| `personal_user` | 公开 | 自助注册 |
| `enterprise_user` | 公开 | 自助注册 |
| `reviewer` | 内部 | 仅管理员 |
| `admin` | 内部 | 仅管理员 |
| `developer` | 内部 | 仅管理员 |

权威角色目录是 `app.domain.accounts.ACCOUNT_ROLES`（不可变 `frozenset`）。ORM
`USER_ROLES`、领域 `ACCOUNT_ROLES` 与测试工厂 `ALL_ROLES` 由
`test_role_directory_drift` 保持同步；该检查是编译/加载期不变量，不是运行时检查。

## 2. 账号管理权限主体

账号管理授权使用 `require_permission` 与中心化的 `ROLE_PERMISSIONS` 映射。其他业务
上下文（人才招聘、JD 生命周期、KG 审核等）可以有独立且不受该映射约束的领域授权策略。

完整权限集合为 `app.domain.permissions.ALL_PERMISSIONS`（不可变 `frozenset`）。

## 3. `account.manage`

- 定义于 `app.domain.permissions.ACCOUNT_MANAGE`。
- **只有 `admin`** 通过 `ROLE_PERMISSIONS["admin"] = _ALL_PERMISSIONS` 获得
  `account.manage`。
- `developer`、`reviewer`、`personal_user` 和 `enterprise_user` **不拥有**
  `account.manage`。
- 未知角色获得空权限集合，不能管理账号。
- 既有公开读权限（`catalog.read_published`、`emerging.read_published`、
  `evidence.read_public`）保持不变。

## 4. 账号管理授权

`ManageAccount`（位于 `app.contexts.access._applications.account_management`）在每次
操作（`list_roles`、`list_permissions`、`change_role`、`change_active`）上强制执行：

```python
require_permission(actor.role, "account.manage")
```

账号管理不存在替代的角色集合门禁。旧 `INTERNAL_ROLES` 常量与
`AccountActor.can_administer_accounts()` 已移除。

## 5. 管理员自身操作限制

限制在 Application 层执行，而不只在 API 边界执行：

- 管理员**不能修改自己的角色**，返回
  `InvalidAccountChange("Cannot change your own administrative role")`，HTTP 422。
- 管理员**不能禁用自己**，返回
  `InvalidAccountChange("Cannot disable your own account")`，HTTP 422。
- 管理员仍可修改自己的密码。

## 6. 最后一位活跃管理员保护

账号管理应用的设计不变量是：任何完成的降级或禁用操作后，仍至少保留一个
`role=admin` 的活跃账号。

### 锁策略

- **PostgreSQL 运行时**：`acquire_account_administration_lock()` 不发出数据库级写锁。
  行级锁由 `active_account_ids_by_role_for_update` 通过带稳定 `ORDER BY User.id` 的
  `SELECT ... FOR UPDATE` 实现。
- **SQLite 测试夹具**：隔离的 pytest 适配器保留 `BEGIN IMMEDIATE` 覆盖，用于确定性
  单元测试。非测试 Settings 拒绝 SQLite，因此这不是运行时回退。

### 临界区顺序

```text
进入 UoW
→ acquire_account_administration_lock()
→ 读取活跃管理员（行锁方言使用 FOR UPDATE）
→ 读取目标账号
→ 校验不变量
→ 变更
→ 提交
```

### 保护规则

- **降级保护**：当 `change_role` 会把活跃管理员改为非管理员角色时，会锁定并统计活跃
  管理员数量；若数量 ≤ 1，操作被拒绝，错误为
  `"Cannot demote the last active administrator"`。
- **禁用保护**：`change_active(False)` 执行同样的检查，错误为
  `"Cannot disable the last active administrator"`。

始终允许的操作：

- 将非管理员提升为管理员。
- 重新启用此前被禁用的管理员。
- 再次禁用已不活跃的账号（语义兼容）。
- 存在两个或更多活跃管理员时，降级或禁用其中一个管理员。

两个保护都在状态变更所在的同一个工作单元事务内执行。

## 7. Token 状态一致性

- 访问 JWT 在 `tv` 声明中携带账号的整数 `token_version`；每个已认证请求要求
  `jwt.tv == account.token_version`。
- 修改密码和 `POST /auth/logout-all` 会使 `token_version` 递增，从而在 API 层使所有
  此前签发的访问 JWT 失效。密码修改成功后返回携带新版本的新访问 JWT。
- `POST /auth/logout` 只是当前客户端的登出确认；客户端删除本地保存的 token，不撤销
  其他客户端。
- `POST /auth/refresh` 是**访问会话续期**：它接受当前有效的访问 JWT，并签发另一个
  同版本访问 JWT。没有 refresh-token 轮换、重用检测、Redis 黑名单或按设备会话存储，
  因此不能描述为完整的双 token 会话系统。
- 每个已认证请求都会通过 `AuthenticateAccount.resolve()` 从数据库重新读取账号。
- 如果账号在 token 签发后被**禁用**，旧 token 会立即返回 **401**。
- 如果账号在 token 签发后**角色发生变化**，旧 token 会在 `/auth/me` 中立即反映**新角色
  与新权限**（无需重新登录）。

## 8. 企业资料授权

企业资料管理（`ManageEnterprise`）保持不变：

- 只有 `enterprise_user` 能创建企业并管理自己的资料。
- `admin` 与 `developer` 通过 `app.domain.accounts` 中的 `ENTERPRISE_READ_ROLES` 常量
  保留对非自有企业的只读访问。
- 该常量在语义上只用于企业数据读取授权，**不**用于账号管理。

## 9. 账号与 Token 端口变更

账号管理锁端口继续保留；轻量会话撤销增加一次账号变更与版本感知 token 操作：

```python
# AccountUnitOfWork — 新增方法
def acquire_account_administration_lock(self) -> None: ...

# AccountRepository — 新增方法
def active_account_ids_by_role_for_update(self, role: str) -> tuple[str, ...]: ...

# AccountRepository — 版本感知会话撤销
def increment_token_version(self, account_id: str) -> int: ...

# TokenPort — 版本化访问 JWT
def issue(self, subject: str, token_version: int) -> str: ...
def identity(self, token: str) -> tuple[str, int]: ...
```

- `acquire_account_administration_lock`：PostgreSQL 运行时把行级锁延迟到 Repository
  方法；SQLite-only 测试适配器会在当前连接上发出 `BEGIN IMMEDIATE`。
- `active_account_ids_by_role_for_update`：PostgreSQL 中使用带稳定 `ORDER BY User.id`
  的 `SELECT ... FOR UPDATE`。
- 密码/会话用例使用的 Fake/stub Repository 也必须实现 `increment_token_version`。
