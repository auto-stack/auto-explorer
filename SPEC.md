# SPEC — 027-file-manager（Plan 440 立；PLAN-016 重写）

AutoOS 桌面文件管理器（Finder / Explorer 双栏形态）。桌面事实轨 = VM
（`auto run -r vm` / 桌面 in-process 装载）；vue 轨（`auto run`）为前端调试轨。

> PLAN-016（2026-09）现代化：mock 平行数组退役 → 真实文件系统；emoji 图标 →
> lucide（按扩展名字面量分支）；固定坐标 popover → alert-dialog + 锚定
> popover + toast()；zinc 硬编码 → 语义 token + dark_mode（桌面 SetTheme 回写
> 链即时换肤）。本节取代 Plan 440 原描述。
>
> PLAN-023（2026-09）增量：网格图片缩略图（auto.image.thumb → Plan 547 媒体
> 管线）+ 地址栏可伸缩坍缩（§1.5/§2.5）。

---

## 1. 数据层（PLAN-001 三层分离 + 平行标量数组快照）

- **三层契约**：NavTo 每目录一次建快照（数据层）→ `RefreshView` 唯一
  派生入口（过滤→排序→统计→窗口物化，零 syscall）→ `render_cap` 渐进
  窗口（渲染层，T-04 哨兵扩窗）。排序/隐藏/搜索交互永不重读盘。
- **快照 = 8 平行标量列表**（`d_name/d_path/d_isdir/d_size/d_mtime/
  d_ext/d_type/d_hidden` + `d_total`）——B12 已证形态。**禁记录对象**：
  VM 记录创建在 ~3k-10k 间离散故障（healthy 实证：3k 导航 0ms、10k
  handler 中止）；1 万次标量 push = 0ms。派生串（size_str/date）窗口
  物化期计算（只付可见行）；name 过滤回落 `name.contains`（大小写敏感，
  v0.6 语义）。
- **SNAP_CAP=8000**：超大目录取前 8000 项 + `item_count_str` 诚实标注
  （"N 项（超大目录，已加载前 8000 项）"）；`d_real_total` 存真值。
  8k-10k 离散墙为框架侧债（债册在档，PLAN-001 探针全证据链）。
- **排序**：键预计算（目录恒先 rank int 不随 desc 反转 + pad10 定宽
  size/mtime 键 + name 次级键）+ 三平行列表归并 `sort_indices`
  （fs_util）；mtime<0 沉底；左元稳定。date 列 = mtime int 序。
- 主目录：`Env.get("USERPROFILE")` → 回落 `HOME`；快捷访问 = 主目录 +
  Desktop/Documents/Downloads/Pictures/Music（exists 门控）。
- 列表物化：`fs.read_dir` + for-in 拷入真 List（F-8：parse 结果禁
  len/索引）→ 逐条 native 三件套 + 8 标量 push。**禁 metadata JSON
  字段级读取**（D-3 实证不变）。
- 隐藏项：dot-prefix（`hidden_name`）；Windows 隐藏属性 stdlib 不可达。
- 错误态：canonical/is_dir 门控 + toast；read_dir 失败中止 handler
  （状态变更置于列目录后，失败保留原视图）。
- **性能口径**：验收一律应用内计时（`last_snapshot_ms/last_derive_ms`，
  handler 内 `time.now_ms` 差值）——MCP fixture 墙钟在大状态为仪器噪声
  （~3s 级）；实测 2000-6000 项快照+派生均 0ms（perf_check ALL PASS）。

## 1.5 地址栏与面包屑（PLAN-023）

- 胶囊占满：面包屑容器 `flex-1 min-w-0`，平时占满导航钮簇与右侧操作区
  （搜索框起）之间全部可用宽；`overflow-hidden` 兜底裁。**顶栏禁用
  `justify-between`**——iced Row SpaceBetween 会把 Fill 子件降级为内容宽
  （实证胶囊恒 ~495px），伸缩一律由 `flex-1` + 对侧 `shrink-0` 承担。
- 深路径坍缩：全链段数 > 5 且未展开 → 首 1 段 + `...` + 末 2 段（视图三面
  crumbs_head / crumb_gap / crumbs_tail——规避循环内条件节点纵向堆叠债，
  R4-2 实证形态）；`...` 点击 = `CrumbsExpand` 就地展开全链（不导航），
  任何导航（NavTo 入口）重置回坍缩态。全链恒存 `crumbs`。
- 段名截断：crumb 按钮 `max-w-[10rem] truncate`——vue 轨真省略号；VM 轨
  裁切无 "…" 字形（renderer truncate = 单行 + clip，框架既有口径）。

## 1.7 键盘面与多选（PLAN-002/004）

- 键盘双声明面：actions（Backspace/Alt+方向/F2/Delete/Ctrl+C/X/V/A/
  Esc/Enter）+ 内容容器 onkeydown 导航族（↑↓/Home/End/PgUp/PgDn +
  Shift↑↓ 范围选择）；输入态守卫内联各 handler 首行；窗口边缘自动
  扩窗（键盘可达全表）。Ctrl+F 聚焦延后（ui.focus 接线面未证）。
- 多选：路径键集合（sel_paths）免疫 id 重编；行勾选列 + 表头全选⇄清空
  + 网格卡勾选；增量统计（sel_count/sel_bytes）；SelectAll(≤5000)/
  清除/反向/锚范围；批量复制/剪切/粘贴/删除（≤500 上限 + 汇总 toast）。
- 粘贴冲突三选模态：覆盖/跳过/保留两者（序号插扩展名前——a (2).txt）
  + 「应用到剩余」记忆；批量删除含递归警告文案。
- 递归删除：File.remove_dir_all（分级确认带项数；批量逐项 + 失败计数）。

## 1.8 预览面板（PLAN-003）

- 右侧 w-80 面板（book-open 开关，storage fileman.preview_on）；
  选中焦点驱动（SelectItem → PreviewRun，点击节奏免防抖）。
- 四形态：文本（read_text_range 头 8KB + 全文注脚；vue 轨走
  api.at fs_text 端点）/ 图片（image.thumb 512 → image_surface
  contain；vue 降级提示）/ 目录摘要（Tick BFS 每拍 ≤40 目录，世代=
  根路径比对，看门狗拍数）/ other（元信息卡，handler 期预取）。
- 列表行图片缩略图（16px，窗口内排队）。

## 1.9 递归搜索与收藏夹（PLAN-004）

- 搜索范围 chip（本目录 ↔ 子树）；子树搜索 = 同步 fs.walk（native
  快）+ .at 过滤 + **结果即快照**（写入 d_* 平行数组——排序/扩窗/
  多选/预览对结果免费生效）；逐词重跑；cap 2000 诚实标注；结果行
  导航（目录直达/文件跳父）；Esc 退出回搜索根；真实导航退出结果态。
- 收藏夹：工具栏星标 + 右键「收藏此目录」；侧栏收藏组；storage
  0x1E/0x1F 编码持久化 + Init 解码。
- 自然排序：natural_key（数字段 6 位定宽）**缓存在快照**（d_nkey）。

## 1.10 VM 指令预算（PLAN-004 发现，架构级）

- **每 handler 调用链 10M 指令硬上限**——超限中止且回滚（事务性）。
  AddrGo→NavTo→快照+派发在 9k 目录实测超限（"3k-10k 离散墙"的最终
  解释——PLAN-001 记录的记录对象墙实为本预算墙）。
- 对策：**预算拆链**——NavTo/RunTreeSearch 只建快照并置
  pending_refresh；Tick 下一拍独立预算执行 RefreshView；重计算
  （natural_key 等）缓存在快照数组。

## 2. 主题与图标（T-01/T-02）

- 全视图语义 token（bg-background/bg-card/border-border/text-foreground/
  text-muted-foreground/bg-accent/bg-primary/bg-destructive 族）。
- `var dark_mode bool`：宿主 SetTheme 执行臂对声明该变量的 app 回写
  （renderer execute_set_theme），vue 轨生成器据此绑根 dark class。
- 图标：`icon (name:)` 元素 + FileIcon 组件（components/file_icon.at，按
  扩展名字面量分支——vue 轨 icon 动态名 Circle 占位规避，TreeIcon 范式）。

## 2.5 网格缩略图（PLAN-023）

- `auto.image.thumb(path, size) -> str`（stdlib 新原语）：图片文件排队
  方形 rendition，返回媒体 URI（"" = 不支持/失败）；解码走 Plan 547 媒体
  管线 worker 池 **Thumbnail 优先档**（最低优先、`MediaPin::None` 可驱逐），
  主线程零解码。
- 接线：物化循环内 `is_image_ext(ext)`（jpg/jpeg/png/webp，与管线解码器
  白名单同款——gif/bmp/svg/ico 管线不收，照旧 FileIcon）且 `kept < 120`
  （THUMB_CAP）时排队，URI 存行字段 `thumb_src`。
- 渲染：grid 卡 `thumb_src` 非空 → `image_surface (fit: "cover")` 入
  h-20 圆角容器；空 → FileIcon 原样。URI 未就绪本帧渲染为空，随 250ms
  Tick 渐进浮现；解码后 per-asset Handle 缓存稳定不闪（P547）。
- vue 轨：`image.*` 走 ts_adapter VM-only 白名单 `__vmOnly` 降级（Plan 444
  形态），thumb_src 恒空 → FileIcon 回落（与既有 fs.* 桩同口径）。
- 驱逐卫生：thumb 原语 queue 后立即 release 配平引用——条目 30s 宽限后
  可驱逐，decoded LRU 保 URI 渲染直至预算压力（重导航自愈）。
- Windows Shell 缩略图（IShellItemImageFactory/ thumbcache 复用）为后续
  可选优化臂，不在本计划（PLAN-023 §0 裁决记录）。

## 3. 弹层（T-03/T-04）

- 新建/重命名/删除确认 = alert-dialog（025-sys-monitor 形态，state 驱动
  open）。
- 操作反馈 = toast()/toast.success()/toast.error()（Plan 412 __toast 管线，
  renderer 窗口级堆叠悬浮层）。
- 右键菜单 = 逐行锚定 popover（shell dock 菜单范式：open 按 `ctx_id ==
  item.id` 匹配，placement bottom-end/bottom-start；oncontextmenu.prevent
  触发）。

## 4. 文件操作（T-06；D-4 口径）

新建文件夹 `file.create_dir`；新建文件 `file.write_text(p,"")`；重命名
`fs.rename`（同目录）；复制 `fs.copy_recursive`（`copy` 为 .at 关键字不可
点调）；剪切 = `fs.rename` 跨目录；删除：文件 `file.delete` / 空目录
`file.remove_dir`，非空目录拒绝（toast；递归删除待 stdlib 提案）。
写操作前 `file.exists` 重名门控；所有路径来自真实解析（canonical 后）。

## 5. 桌面互操作（T-07/T-08/T-09；协议 v1.7）

- `__desktop_cmd` 总线（shell 同款状态面）写 `open_with	<app-id>	<path>`；
  v1.7 起 pump 对全部注册表窗联合排空（原仅特权四窗）。
- 关联解析：fs_util.app_for_ext（静态默认表，与 041/031 pac `opens:` 声明
  同源）；无关联 toast.error（"打开方式"选择器待注册表 opens 投影面）。
- 宿主执行臂：未知 app / opens 未声明扩展名拒绝（toast）；未运行 launch 后
  write_state `auto_open_path`、已运行聚焦 + 写入；目标 App Tick 消费
  （041 ConsumeOpen、031 SettleTick 臂）。

## 5.5 vue 轨降级（PLAN-680 Q1）

- 桌面事实轨 = VM；vue 轨为前端调试轨——无 FS 能力，**降级不报错**。
- 轨别信号：`var is_vm str`，Init 读 `Env.track()`（VM 运行时返 "vm"，
  vue 轨生成期折叠为 "vue" 字面量；`Env.get("字面量")` 同机制折叠为 gen 机
  env 快照——home 主目录解析在 vue 轨取 gen 机值）。
- 四点 FS 守卫：Tick bootstrap（快捷访问/盘符/首目录引导）、NavTo 单点
  （导航/排序/搜索/隐藏切换/地址跳转全汇聚）、GoUp（fs.parent 先于 NavTo）、
  CommitNew（空列表下新建仍可达）。守卫分支内 fs.*/file.*/image.* 调用
  vue 轨不执行。
- 空态文案按轨别分派：VM = "此目录为空"；vue = "vue 调试轨无文件系统
  访问 — 完整功能请使用 auto run -r vm"（列表/网格双模式）。
- 不门控：view_mode/sort_col/show_hidden 及其 storage 持久化（localStorage
  构建在 vue 轨可用）——保住样式调试价值。
- 布局注记：sidebar_provider 必须 `w-auto`（上游 SidebarProvider 默认
  `w-full`，在 row 内与 flex-1 兄弟互斥→内容区 0 宽；018 先例）。

## 6. 测试

- tests/desktop_mcp.py（VM 模式）：真实 FS 断言套件 + tests/testdata 副本
  （破坏性操作只对副本；地址栏 submit 跳转）。
- open_with 端到端 = 桌面宿主级（acceptance bus 注入），证据
  auto-os docs/plans/evidence/016/t07-open-with-e2e.png。
- 已知残留：MCP 服务器线程偶发静默失联（框架级，mock 时代同机制）；
  Tick 延迟引导避开 iced boot 临界区竞态（原 Init 内同步 fs 调用偶发挂起）。
