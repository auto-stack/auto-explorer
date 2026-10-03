# 027-file-manager 设计文档 —— v0.7「最强·最快」竞争力重构

状态：draft（2026-10-04；随计划族 045-048 演进，每计划 review 时对本档
对应章节做 revision 绑定）。配套：[需求文档](REQUIREMENTS.md)。

---

## 1. 设计约束（框架现实，全部有据）

写在此处的是**设计时必须绕开/利用的框架事实**，来源为 2026-10-04 对
auto-lang crates 与本仓 apps 的实证摸底：

1. **无虚拟化列表**：框架无「仅渲染视口」组件；先例是 029-photo-gallery
   `render_cap=60 + 加载更多`、026-database 手动分页、027 现状 500 截断。
   `scroll` 元素有 `onscroll` 几何回调（offset_y/viewport_h/content_h），
   可自研扩窗但无先例——作为验证臂。
2. **原生排序存在**：`stdlib/auto/sort.at` 声明 `sort_by(list, cmp_fn)`、
   `sort/sorted/reverse`（#[vm] 原生实现）。比较器签名为
   `fn(a Val, b Val) int`。**闭包捕获外层变量无先例未证**——设计回避：
   用 8 个模块级具名比较器（见 §5）。
3. **流式枚举存在但无消费先例**：VM ffi `fs.entries(path) -> iterator_id`
   （后台 OS 线程 + tokio mpsc，事件 JSON 含 name/is_dir/size，**无
   mtime**；`crates/auto-lang/src/vm/ffi/stdlib.rs:1293`）。.at 侧 for-in
   消费 iterator 的形态无 app 先例——作为验证臂，回落方案 = `fs.read_dir`
   + Tick 分批物化（见 §6）。
4. **异步协程面完整**：`stdlib/auto/async.at` — `spawn(fn())`、
   `channel[T]()`（Sender.send / Receiver.recv/try_recv/has_data）、
   `sleep/yield_now/wait_all`。递归搜索的底座。
5. **递归删除 native 已注册**：`File.remove_dir_all`（ffi stdlib.rs:464，
   带 bare 别名）。.at 侧 `file.remove_dir_all(path)` 直调可达性在计划内
   首个任务验证（与 `file.remove_dir` 同命名面）。
6. **文本范围读**：`file.read_text_range(path, offset, limit)`（file.at:20）
   ——预览面板文本头来源。
7. **缩略图管线既有**：`image.thumb(path, size) -> URI`（Thumbnail 最低
   优先档 worker 池、主线程零解码、渐进浮现）；vue 轨 `__vmOnly` 恒空回落
   FileIcon。
8. **键盘事件**：DOM 事件 dot-modifier 链 `onkeydown.<key>[.mods]`（auto-term
   13 快捷键先例）+ widget `actions { action(shortcut:) }` 双轨声明面；
   修饰链支持 `shift.up` 等组合。
9. **VM 遍历/语言纪律**（在册债，不得踩）：json.parse 结果不做 `.len()`
   （F-8：`json.parse("[]").len()` 返回 20）；`.copy` 是关键字不可点调；
   无按索引删除列表（重建数组）；VM for 迭代单根元素（多子件包 row）；
   popover 不可入 mouse-area 子树；模板文本不能调 `.len()`（计数预计算
   成状态）；Init 不做重 FS 活（iced boot 竞态）。
10. **排序后重编 id**：id = 显示序（既有 R4-4 纪律，多选时代价见 §7——
    选中集改为路径键，id 漂移免疫）。

## 2. 总体架构：三层分离

现状是单层大 handler：`NavTo` 串起「读盘→过滤→物化→排序→渲染物化」，
任何交互（排序/过滤/隐藏）都全量重跑（含 syscall）。重构为：

```
┌─ 数据层（每目录一次） ─────────────────────────────┐
│ dir_entries: 目录快照（全量条目，含 metadata 派生） │
│   NavTo / 写操作后 Reload 时重建                     │
├─ 派生层（每交互一拍，纯内存） ──────────────────────┤
│ filter(快照, show_hidden, search_q)                 │
│  → sort_by(8 具名比较器)                             │
│  → 统计（item_count/total_size/sel 汇总）            │
│  → files_view 物化（含 id 重编、sel 投影、thumb 预留）│
├─ 渲染层（每拍/每扩窗） ─────────────────────────────┤
│ render_cap 窗口切片 + 扩窗触发（加载更多/onscroll 臂）│
└─────────────────────────────────────────────────────┘
```

**不变式**：
- `dir_entries` 是当前目录的唯一事实源（name/path/is_dir/size/mtime +
  预派生 ext/type_name/date/size_str/is_hidden/name_key）——派生字段在
  快照构建时**一次算好**，此后过滤/排序/统计零重复格式化。
- `RefreshView` 是派生层唯一入口；`NavTo` 只做「换目录→建快照→
  RefreshView」。排序/隐藏/过滤/多选统计类交互**永不触盘**。
- 写操作（新建/重命名/删除/粘贴）完成后调 `Reload`（同目录重建快照，
  走与 NavTo 相同的快照构建函数但**不**动历史栈与选中路径恢复逻辑）。

## 3. 状态模型与 handler 面

model 新增/改造（`fileman.*` storage 键同步扩展）：

```
// 数据层
var dir_entries = []      // 快照：全量行 schema（见 §2，含预派生字段）
// 派生层
var files_view = []       // 现有（含 id/name/path/.../thumb_src + 新 sel）
var view_total int = 0    // 过滤后总数（≠渲染数）
var render_cap int = 300  // 渲染窗口（初始档）
// 多选
var sel_paths = []        // 选中路径键集合（字符串数组，contains 判定）
var sel_count int = 0     // 预计算（模板零 .len() 纪律）
var sel_bytes int = 0
var anchor_id int = -1    // Shift 范围选择锚
// 键盘
var focus_in_input bool = false   // 输入框聚焦守卫（addr/search/模态统一门）
```

handler 面重构（msg 变更）：

| handler | 重构后职责 | 触盘 |
|---------|-----------|------|
| `NavTo(path)` | canonical 门控 → 建快照（§6）→ 历史栈/面包屑 → 选中恢复策略（清空）→ RefreshView | 一次 |
| `Reload` | 同目录重建快照 → RefreshView（不动历史/尽量保选中路径） | 一次 |
| `RefreshView` | 过滤→排序→统计→物化（sel 投影 + thumb 排队预留）→ render_cap 切片 | **零** |
| `SortByXxx / ToggleHidden / SetSearch` | 只改配置状态 + storage → RefreshView | **零** |
| `GrowRender` | render_cap += 档位 → RefreshView（只扩窗，重物化窗口切片） | 零 |
| `SelectItem / ToggleSel / SelectAll / ClearSel / SelectRange` | 多选模型维护（§7）→ 统计重算（轻量，不重物化——sel 高亮走行字段投影时才 RefreshView） | 零 |

「零触盘」是**可断言**的：desktop_mcp 用例在交互前后各做一次
`fs.read_dir` 探针计数不可行（.at 无计数面），改用行为断言——交互后
目录 mtime 不变场景下结果与触盘重读一致 + 性能计时门（P-2）。

## 4. 渐进渲染（去 500 截断）

- **窗口物化**：`files_view` 只物化 `render_cap` 切片（初始 300，档位
  +500）；`view_total` 全量计数恒示（「5,230 个项目 · 已显示 800」）。
- **扩窗触发**（叠加三层，逐级降险）：
  1. 列表尾部「加载更多 (N)」按钮（029 先例，保底必达）；
  2. 底部哨兵行 `onmouseenter` 自动扩窗一档（滚动到近底即触发，
     mouse-area 既有事件面，无新框架依赖）；
  3. `scroll.onscroll` 几何回调扩窗（progress_y > 0.92 时 GrowRender）
     ——验证臂：VM 轨 onscroll 事件形态若无回调面则弃，1/2 已足。
- **thumb 排队**保持 cap 120/目录（管线既有），扩窗后新窗口内图片条目
  补排队（RefreshView 物化时按「窗口内且 kept<120」判定——排队发生在
  物化循环，天然只对可见窗口付费）。
- 统计（total_size）在快照构建时全量累计（含未渲染条目），与渲染窗口
  解耦——「最快」口径：数据准确性与渲染成本无关。

## 5. 排序：原生 sort_by + 8 具名比较器

fs_util 新增（替换 `sort_files` 选择排序；旧函数删除不留守卫）：

```
// 比较器族：模块级具名 fn（规避闭包捕获未证面）；目录恒先在比较器内
// 实现（rank 投影），不是两段排序。
pub fn cmp_name_asc(a, b) int   // 自然排序感知（§5.1）
pub fn cmp_name_desc(a, b) int
pub fn cmp_size_asc(a, b) int   // int 比较
pub fn cmp_size_desc(a, b) int
pub fn cmp_date_asc(a, b) int   // mtime int（快照存原始 int，弃字符串序）
pub fn cmp_date_desc(a, b) int
pub fn cmp_type_asc(a, b) int
pub fn cmp_type_desc(a, b) int

pub fn sort_entries(list List, col str, dir str) List {
    // 拷贝入 out（不动入参纪律）→ 按 (col,dir) 派发具名比较器
    // → sort_by(out, cmp_xxx) → return
}
```

- 比较器内目录恒先：`rank = is_dir ? 0 : 1`，rank 不等即返回（desc 列
  **不反转 rank**——目录恒先是产品语义不是排序方向的一部分）。
- 比较器返回 int：`a<b` 返 -1、等返 0、`a>b` 返 1（sort_by 契约）。
- **date 列改 int 序**：快照存原始 mtime（int），列显示仍 fmt_date——
  修掉现状「date 字符串字典序」的隐性脆弱（等值但格式一致的债）。

### 5.1 自然排序（P1，比较器内联实现）

`cmp_name_*` 内做数字段感知比较：逐字符推进，遇连续数字段按 int 值比
（前导零短的为小，同值前导多者为大）；非数字段按字符序。实现为
fs_util 纯函数 `natcmp(a str, b str) int`（约 60 行 while 扫描，无分配）。
万级目录每次排序全量 natcmp 成本 = 原生 sort 的 O(n log n) 次比较器调用
× 每次 ~len 字符扫描——10k×14×20 ≈ 2.8M 解释器操作，实测若超 P-4 预算
（100ms）则回落字典序并在债册登记（性能断言门把关）。

## 6. 快照构建与元数据策略

**小目录（≤2000 项）**：维持现状同步路径——`fs.read_dir`（一次）+
逐条三件套 native（is_dir/size/mtime）+ 预派生。SSD 上 2000×3 syscall
≈ 数十 ms，P-1 达标。

**大目录（>2000 项）——两臂，计划内首任务裁决**：

- **臂 A（验证目标）**：`for ev in fs.entries(can)` 流式消费——后台线程
  枚举（name/is_dir/size 自带，mtime 缺）→ 快照先建（date 列 "--"）→
  mtime 分批补齐（Tick 每拍 500 条 fs.mtime，补完一拍 RefreshView）。
  验证点：.at for-in 消费 iterator_id 的形态（io.lines 同族先例在框架
  测试里找；找不到则写 10 行探针 app 实测）。
- **臂 B（回落保底）**：`fs.read_dir` 一次拿全名 → **分批物化**：首拍
  只做前 2000 条的三件套（首屏 P-1 达标），其余按 Tick 每拍 2000 条推进
  （`snapshot_progress` 状态 + 渐进 RefreshView），状态栏示「元数据加载
  中 N/M」。

两臂共同的**正确性不变式**：排序键未齐（mtime=-1）期间，date 排序把 -1
沉底（比较器内 rank 投影：mtime<0 恒大于一切）；补齐后 RefreshView 一次
性稳定（规避 Explorer 式排序抖动教训——补齐期间禁止逐条插入重排）。

## 7. 多选模型

- **键 = 路径**（`sel_paths` 字符串数组 + contains 判定），非行 id——
  id 随排序/扩窗重编漂移，路径键天然免疫（R4-4 债的多选时代价解）。
- **交互面**：
  - 行首 checkbox 列（列表/网格两模式）——无 shift/ctrl-click 事件面
    （mouse-area onclick 无修饰信息），勾选是鼠标多选唯一入口；
  - Ctrl+A 全选 / Esc 清除（键盘）；
  - Shift+↑/↓ 范围扩展（anchor_id 锚 + 当前 id 区间，路径入 sel_paths）；
  - 单击行 = 选中焦点（现有语义不变，single-select focus 与 multi-select
    集合正交：焦点驱动预览/Enter 打开，集合驱动批量操作）。
- **投影**：RefreshView 物化时行字段 `sel: sel_paths.contains(path)`
  （contains 是 O(n)，n=选中数，典型 ≤100 可接受；万级选中时统计仍准，
  渲染投影每行 O(sel)——债册登记上限提示）。
- **批量操作**：复制/剪切/删除循环 `sel_paths` 逐项执行（复制集 N≤500
  门 + 超限 toast 拒绝——防误操作万级粘贴）；结果 toast 汇总
  「已复制 12 项，跳过 3 项（同名）」。

## 8. 键盘路由

- 声明面：widget `actions { action(shortcut:) }`（进入 menubar/命令面）
  承载可披露动作（重命名 F2/删除 Delete/全选 Ctrl+A/打开 Enter/上级
  Backspace/历史 Alt+←→）；列表容器 `onkeydown.<key>` dot-modifier 承载
  导航键（↑↓/Home/End/PgUp/PgDn/shift.up/shift.down）——auto-term 双面
  先例。
- **聚焦守卫**：`focus_in_input` 状态在 addr/search/模态 input 的
  focus 事件置位（vue 轨）/ 守卫条件并集（`.addr_editing || 模态 open ||
  .search_focus`）替代（VM 轨无 focus 事件面时的等效门——两轨取交集
  保守实现）。所有全局快捷键 handler 首行守卫。
- 方向键移动 = 改 `selected_id` + 视口跟随（VM 无 scroll_to_index 命令，
  降级：移动到窗口边缘时自动 GrowRender 一档，保证键盘可达全表——
  「键盘滚动」即「扩窗」的语义化）。

## 9. 预览面板（P1，计划 047）

- 右侧 `w-80` 面板，开关按钮 + storage 持久化（`fileman.preview_on`）；
  主内容区 `flex-1` 让宽（既有 1.5 节 flex 纪律维持）。
- 选中（单击焦点）驱动，类型分派：
  - 文本族（txt/md/log/json/code，复用 type_label 分类）：`file.read_text_range(path, 0, 8192)` → 等宽 `code` 块 + 「前 8KB · 全文 N KB」注脚；
  - 图片族（is_image_ext 同表白名单）：`image.thumb(path, 512)` 大尺寸
    rendition + image_surface（contain fit）；
  - 目录：项数/总大小——**后台协程**（async.spawn + channel + Tick 消费
    递增计数，世代取消同 §10）+ 选中切换即取消上一目录的计算；
  - 其他：类型卡（type_name/size/date/path 全量 tooltip 同源字段）。
- vue 轨：api.at 新增 `GET /api/fs/text?path=`（thin 委托 impl_read_head，
  8KB 截断）；图片/目录摘要 VM 专属降级文案（既有守卫口径）。

## 10. 递归搜索（P1，计划 048）

- 入口：过滤框左侧范围切换 chip「本目录 / 含子目录」（`search_scope`
  状态）；递归模式按下即启后台协程，逐字重输即重启（世代取消）。
- 协程形态（VM 轨）：
  ```
  var search_gen int = 0
  .SetSearch(q) -> { ... .search_gen = .search_gen + 1
      let gen = .search_gen
      spawn(fn() {                    // async.at
          let names_src = fs.walk(.current_path)   // JSON 全量（快但大）
          // 臂 A 验证后可换 fs.entries 递归不存在——walk 是递归唯一面
          ...逐条 name_key.contains(q_lower) 命中 → chan.send({name, path})
      })
  .Tick -> { while chan.has_data() > 0 && 批内 ≤200 { 取一条入 results }
      if .search_gen != gen 协程侧自弃（发送前比世代，过期即 return）}
  ```
  - 结果呈现：files_view 复用（行 schema 同款，path 为全路径，点击 =
    打开文件 / 定位目录=NavTo(parent)）；状态栏「搜索中… 已命中 N」
    （「取消」= Esc 清 q）。
  - 结果 cap 2000（超限提示收敛关键词）。
  - **已知边界**：fs.walk 返回全量 JSON 数组（非流式）——万级子树单次
    walk 内存/时延可控（原生侧一次 syscall 序列），过滤在协程内做不
    阻塞 UI；真正的流式（fs.entries 无递归形态）登记债册。
- 收藏夹（同计划）：`favorites` 路径数组 storage 持久化（`fileman.favs`）；
  侧栏星标组 + 行右键「添加到收藏」/收藏项右键移除；当前目录在列时
  工具栏星标高亮。

## 11. 写操作强化（P0，计划 046）

- **递归删除**：`file.remove_dir_all(path)`（§1.5 验证）——确认模态分级：
  单文件（现行文案）/ 空目录（现行）/ 非空目录（「该文件夹包含 N 个
  项，将全部永久删除」——N 由快照子目录计数协程预取或 read_dir 计数
  兜底「多个项」）。多选批量删除 = 逐项同规则 + 汇总 toast。
- **粘贴冲突解决**：目标已存在时弹三选模态（alert-dialog）：
  覆盖（copy_recursive 直覆盖 / rename 覆盖）/ 跳过 / 保留两者
  （目标名自动 `原名 (2)` 递增——exists 循环探测）。多选批量内同名
  冲突记住当次选择（「对剩余 N 项应用此选择」checkbox）。

## 12. vue 轨降级口径（扩展）

| 能力 | vue 轨形态 |
|------|-----------|
| 读盘/导航/过滤/排序/渐进渲染 | 后端 fs_list 全量 entries → 前端快照/派生同构（本设计零差异） |
| 多选/批量复制粘贴 | 写面 VM 专属（既有守卫 toast 维持） |
| 缩略图（列表/网格） | `__vmOnly` 恒空 → FileIcon（既有） |
| 预览面板-文本 | api.at 新端点（§9） |
| 预览-图片/目录摘要、递归搜索 | VM 专属降级文案 |
| 键盘面 | 双轨一致（actions + onkeydown 生成面同源） |

## 13. 测试策略

- desktop_mcp.py 扩展用例族（注入式驱动，既有 T1-T14 不动语义只适配
  断言面）：
  - T15 排序/过滤/隐藏交互后快照不重建（行为等价断言：交互前后
    selected 路径保持 + 计时门 P-2）；
  - T16 渐进渲染：mkbig 3000 项目录 → 首屏 ≤300 行 vtree 计数 +
    GrowRender 后窗口扩展 + view_total 恒 3000；
  - T17 多选：fixture 注入 sel_paths → 统计/批量删除副本目录断言；
  - T18 键盘：autoui_keyboard 派发 ↑↓/Enter/F2/Delete/Ctrl+A 断言；
  - T19 冲突三选：testdata 副本构造同名 → 覆盖/跳过/保留两者三分支；
  - T20 递归删除：嵌套 testdata 副本删除后 fs 断言子文件不存；
  - T21 预览面板：文本头/图片 URI/目录摘要三态断言；
  - T22 递归搜索：nested 子树命中 + 世代取消（改 q 后旧结果不被采纳）。
- 性能断言：tests/perf_check.py（复用 McpClient）——P-1/P-2/P-4/P-5
  计时，容差 ±50%，超限 fail（软门：环境注记 waiver 机制——dev 机
  负载波动人工豁免记录）。
- 构造工具：tests/mkbig.py（N 参数生成临时目录，用后清理）。
- 双轨验证：autoui-verifier 技能 parity 对拍（新 UI 面纳入）。

## 14. 风险与验证点登记

| # | 风险 | 缓解 |
|---|------|------|
| R1 | sort_by 比较器闭包捕获未证 | 8 具名比较器规避（§5），零捕获 |
| R2 | fs.entries for-in 消费无先例 | 臂 B 回落保底（§6），首任务探针裁决 |
| R3 | natcmp 万级超预算 | 性能断言门 → 回落字典序 + 债册 |
| R4 | onscroll 扩窗 VM 无回调面 | 三层扩窗叠加，1/2 保底（§4） |
| R5 | file.remove_dir_all 裸名解析 | 首任务 10 行探针；失败则 ffi 侧补别名属 auto-lang 变更（触发跨仓 plan，升格决策） |
| R6 | sel_paths contains 万级选中 O(n²) 投影 | 批量操作 cap 500 门（§7） |
| R7 | Tick 分批物化与用户交互竞态（翻页中排序） | snapshot_progress 期间排序/过滤可用（对已齐子集），补齐后一次性稳定重排（§6 不变式） |
| R8 | spawn 协程异常静默 | 协程内 try 面缺失（.at 无 try）——发送侧世代自弃 + 超时看门狗（Tick 计数 > N 拍无进展即标记搜索失败 toast） |

## 15. 计划映射与 SPEC 演进

| 计划 | 本档章节 | SPEC.md 增量 |
|------|---------|-------------|
| 045 性能基座 | §2-§6 | §1 数据层重写（三层分离/渐进渲染/原生排序） |
| 046 键盘批量 | §7-§8、§11 | §4 文件操作扩展 + 新 §键盘面 |
| 047 预览面板 | §9 | 新 §预览 |
| 048 搜索收藏 | §10 | 新 §搜索与收藏 |

每计划 merge 时同步：app README 功能清单、REQUIREMENTS 状态列、
本档状态行 revision 绑定。
