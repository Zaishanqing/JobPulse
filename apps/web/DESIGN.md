---
name: JobPulse
description: 面向岗位与能力图谱治理的可追溯聚焦数据平台
colors:
  terracotta: "#B94D2E"
  terracotta-soft: "#F3E3D9"
  emerald: "#1F883D"
  success-soft: "#E6ECE5"
  success-border: "#B6C3B5"
  copper: "#9A6335"
  sand: "#B58A62"
  canvas: "#F6F2EA"
  surface: "#FFFDFC"
  surface-muted: "#F2EEE7"
  charcoal: "#252827"
  charcoal-soft: "#343735"
  border: "#DED7CC"
  border-soft: "#ECE6DC"
  surface-hover: "#F4EEE7"
  text-muted: "#696861"
  text-soft: "#514F49"
  error: "#B94A3B"
  terracotta-hover: "#A94429"
  on-dark: "#FFFAF4"
  on-dark-soft: "#C9C3B9"
  on-dark-muted: "#AAA89F"
  on-dark-accent: "#D7B18D"
  charcoal-line: "#393C39"
  charcoal-raised: "#3C3F3D"
  charcoal-selected: "#3A302B"
  charcoal-deep: "#282B29"
  charcoal-deep-line: "#414440"
typography:
  display:
    fontFamily: "Noto Sans SC, Microsoft YaHei, PingFang SC, system-ui, sans-serif"
    fontSize: "clamp(28px, 4vw, 48px)"
    fontWeight: 700
    lineHeight: 1.18
  headline:
    fontFamily: "Noto Sans SC, Microsoft YaHei, PingFang SC, system-ui, sans-serif"
    fontSize: "26px"
    fontWeight: 700
    lineHeight: 1.25
    letterSpacing: "-0.025em"
  body:
    fontFamily: "Noto Sans SC, Microsoft YaHei, PingFang SC, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.7
  label:
    fontFamily: "Noto Sans SC, Microsoft YaHei, PingFang SC, system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 700
    lineHeight: 1.4
rounded:
  tag: "6px"
  control: "10px"
  compact-surface: "12px"
  surface: "14px"
  feature: "16px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "20px"
  xxl: "24px"
  page: "28px"
components:
  button-primary:
    backgroundColor: "{colors.terracotta}"
    textColor: "{colors.surface}"
    rounded: "{rounded.control}"
    padding: "10px 22px"
    height: "44px"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.charcoal}"
    rounded: "{rounded.control}"
    padding: "8px 16px"
  card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.charcoal}"
    rounded: "{rounded.surface}"
    padding: "{spacing.xxl}"
  tag-primary:
    backgroundColor: "{colors.terracotta-soft}"
    textColor: "{colors.copper}"
    rounded: "{rounded.tag}"
    padding: "3px 9px"
---

# JobPulse 设计系统

## 总览

**Creative North Star: "可追溯工作台"**

JobPulse 采用克制、专业、可审计的聚焦数据平台语言。每个视口由一个明确任务主导，紧凑壳层负责角色与路由定位，主工作画布承载数据操作，证据在需要时进入上下文面板。

视觉材料以暖象牙、石墨灰和陶土色为基础，翠绿色表达稳定状态，沙铜色提示证据与关系。界面保持中等信息密度，拒绝白蓝企业模板、像素风和指标堆叠。

**Key Characteristics:**

- 单一主任务占据视口，减少竞争面板。
- 暖色中性底与低反光平面，强调长期分析与审阅。
- 角色、证据和不可变版本在交互链路中持续可见。
- 中文信息层级清晰，桌面与移动端保持同一品牌语气。

## 色彩

色彩通过暖中性背景与少量功能性色建立秩序，强调色保持稀缺。

### 主色

- **治理陶土色**：用于主操作、活动状态、焦点与关键图谱节点。

### 辅色

- **稳定翠绿色**：用于稳定、已验证或低风险状态。

### 三级色

- **证据沙铜色**：用于证据关联、关系提示与辅助品牌细节。

### 中性色

- **暖象牙画布**：全局背景，降低长时间阅读的视觉刺激。
- **纸白表面**：表格、卡片、抽屉与输入容器。
- **石墨工作面**：导航与高聚焦区域。登录面为全浅色面，石墨在此完全退出，仅作文字 token（字标、标题）与品牌点（brand-mark 第四点）。
- **柔灰边界**：通过边框和色块分层，不依赖强阴影。分隔线分两档：标准 `--border` 用于表面外缘与表头，`--border-soft` 用于列表行、描述项等内部细分。
- **悬停暖灰** `--surface-hover`：列表行、可点条目的唯一悬停底色，禁止游离的浅灰值。
- **错误红** `#A73F32`：独立于品牌色，仅表达错误、删除与高风险。

### 深色表面层级

石墨工作面上的颜色只允许来自这一族变量，禁止在样式中写游离色值：

- **on-dark**：石墨面上的主文字与高亮标记（暖白）。
- **on-dark-soft / on-dark-muted**：石墨面上的次级与弱化文字。
- **on-dark-accent**：石墨面上的沙铜点缀（品牌标记、选中图标）。
- **charcoal-line / charcoal-raised / charcoal-selected**：石墨面的边界、分层与选中块。
- **charcoal-deep / charcoal-deep-line**：比导航更深的内嵌面板（血缘条、信任条）及其分隔线。

### 命名规则

**The One Accent Rule.** 同一操作组只允许一个陶土色主操作；其余动作使用中性或文字样式。

**The Semantic Warmth Rule.** 翠绿色只表达稳定状态，沙铜色只表达证据或关系，禁止用作随机装饰。

## 字体排印

**Display Font:** Noto Sans SC（回退至 Microsoft YaHei、PingFang SC 与系统无衬线）

**Body Font:** Noto Sans SC（同一回退栈）

**Label/Mono Font:** Cascadia Mono（回退至 SFMono-Regular、Consolas，仅用于版本快照与原始数据）

**Character:** 中文无衬线保持直接、理性；标题通过字重和紧凑字距建立权威，不使用装饰字体。

### 层级

- **Display**（700，流体 28–48px，1.18）：登录门和单一关键任务。
- **Headline**（700，26px，1.25，紧凑字距）：页面级标题。
- **Title**（600，17–20px）：表面与数据对象标题。
- **Body**（400，14–15px，1.7）：说明、证据与业务文本。
- **Label**（700，11–12px）：导航分区、状态与表头。

字重只取真实字体档位的两档语义 token：`--weight-title/strong`（600）与 `--weight-label`（700），禁止 620/650/680 等合成字重。次级文字除 `--text-muted` 外增加中间档 `--text-soft`，用于说明性文字与描述列表。

### 高密度数据字号

高密度数据位使用更小的字号 token，全部经 `--font-*` 变量落地：

- **Caption**（11px）：表内辅助信息、技能计数。
- **Meta**（10px）：时间戳、来源、次级元信息。
- **Micro**（9px）：徽记、角标等一次性标记。
- **KPI**（44px）：匹配度等单一关键数字，配合 `font-variant-numeric: tabular-nums`。

以下一次性尺寸不进入 token 体系，属于有意例外：空态/菜单图标（17–42px 的 `.anticon`）、响应式断点覆盖（768px 以下的 13/15/22px）与少量组件级标题（16/18/20px）。新增字号前先复用上述 ramp。

### 命名规则

**The Chinese-First Rule.** 界面文案优先使用简明中文；缩写首次出现时需给出中文含义或上下文。

## 布局

桌面端采用 256px 左侧导航、72px 顶栏与自适应主内容区，内容最大宽度 1680px，页面边距 28px。构图 A 规定图谱或核心表格占据主工作区，证据通过右侧抽屉进入上下文，避免永久并列信息墙。

未登录时不渲染壳层：整页登录面以单张居中纸白卡片为主体（`--radius-feature` 圆角 + 柔和暖色投影，见 Elevation 例外），品牌、三联角色入口、图谱几何母题、账号表单与可追溯注记合并在同一张卡片内；画布周围使用克制的暖色晕染，以及本子、签字笔、笔记本电脑、简历纸张线稿点缀，保持整体留白。768px 以下收窄卡片内边距并隐藏角色职责辅助文案与装饰线稿。

1080px 以下隐藏次要权限标签与路由描述；768px 以下收起侧栏、顶栏降至 62px、内容边距降至 12–16px，表格允许横向滚动，证据抽屉宽度不超过 92vw。

## 层级与纵深

系统默认无投影。深度由石墨工作面、纸白表面、柔灰边界和轻微色调差建立；焦点态使用低透明陶土色光圈，抽屉和模态框由其原生层级承担空间关系。唯一例外：登录面表单卡片是聚焦表面，允许一次柔和暖色投影（0 18px 48px rgba(53,43,34,.12)），此例外仅限登录面。

### 命名规则

**The Flat-by-Default Rule.** 静态表面不使用装饰性阴影；只有焦点、覆盖层和明确状态可获得视觉抬升。

## 形状

表面采用 12–16px 的柔和圆角，控件采用 10px，标签采用 6px。圆角以 `--radius-tag/control/compact/surface/feature` 变量落地，禁止游离数值。边框保持 1px 与低对比度；圆形只用于品牌节点、头像和状态点。形状服务于分组与操作识别，不制造拟物装饰。

## 组件

### 按钮

- **Shape:** 紧凑、稳定的控制圆角（10px）。
- **Primary:** 陶土底、纸白文字，标准高度 44px。
- **Hover / Focus:** 悬停加深陶土色；键盘焦点使用可见的陶土色外圈。
- **Secondary / Ghost:** 纸白或透明背景，石墨文字，悬停转为陶土边界。

### 标签组件

- **Style:** 柔和陶土底与深铜文字；权限标签使用柔翠绿中性色。
- **State:** 只承载状态、权限和轻量分类，不承担主操作。

### 卡片与容器

- **Corner Style:** 主表面 14px；登录面角色门用 12px compact，登录表单卡片用 16px feature。
- **Background:** 纸白表面或石墨工作面。
- **Shadow Strategy:** 静态无阴影；登录表单卡片为唯一登记的投影例外。
- **Border:** 1px 柔灰边界。
- **Internal Padding:** 16–28px，随信息层级调整。

信息提示默认使用标题下的简短说明或空状态，不在岗位全景等浏览页面额外堆叠整行提示框；`Alert` 仅用于需要用户立即处理的真实错误或风险。

### 输入与字段

- **Style:** 纸白背景、柔灰边框、10px 圆角。
- **Focus:** 陶土边框与低透明焦点环。
- **Error / Disabled:** 沿用语义状态，禁止用品牌强调色代替错误色。

### 导航

桌面导航使用石墨底、暖灰文字与 44px 行高；选中项使用深暖色块和沙铜内侧标记。移动端保留 JobPulse 字标、当前路由与身份入口。

### 登录角色门（Role Doors）

角色门即演示入口：点选角色一键填充账号。三项入口横向排布在登录卡片顶部，以紧凑标签承担登录角色切换。

- **Shape:** 三列横向文字标签（统一尺寸图标 / 角色名），选中项使用陶土色文字和短底线；职责和账号通过悬浮提示与一键填充保留在交互行为中。
- **Hover / Focus:** 悬停切悬停暖灰 `--surface-hover`；键盘焦点为 2px 陶土外圈。
- **Selected:** 不使用角色外框；图标反转为陶土底纸白图标，并仅用短底线确认当前角色。
- **Type:** 账号行使用等宽字体与沙铜色；角色名为标题字重（600）。

### 图谱画布

图谱画布为主工作面，使用纸白底与 14px 圆角。陶土色表示主要对象或选中关系，翠绿与沙铜承担稳定与证据语义；证据详情进入右侧抽屉。

## 动效

动效保持克制，只服务状态反馈，不制造签名动效。统一使用两个 token：`--motion-fast`（.16s）与 `--ease-out`（cubic-bezier(.16,1,.3,1)）。

- 只过渡 `color`、`background-color`、`border-color` 等具体属性，禁止 `transition: all`。
- 悬停与选中反馈在 160ms 内完成，不使用弹跳或延迟编排。
- `prefers-reduced-motion` 下全局关闭过渡与动画（styles.css 已有通配兜底）。

## 使用准则

### 应做

- **Do** 让一个治理任务主导当前视口，并将证据放入上下文抽屉。
- **Do** 保持角色权限控制（RBAC）、证据链和版本不可变机制在流程中可见。
- **Do** 用边界、留白和色调层次控制中等信息密度。
- **Do** 在移动端保留品牌、当前任务与登录身份。

### 不应做

- **Don't** 回退到通用白蓝数据平台配色。
- **Don't** 使用像素风、霓虹科技装饰或大面积渐变。
- **Don't** 堆叠大量指标卡、竞争图表或永久并列侧栏。
- **Don't** 将陶土、翠绿和沙铜当作无语义的装饰色。
