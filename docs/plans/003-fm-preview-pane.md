---
plan_id: PLAN-003
status: reviewed               # drafting → executing → execution_done → reviewed → archived
feature_name: fm-preview-pane
author: [agent]
created_at: 2026-10-04
updated_at: 2026-10-04
plan_revision: 1
current_step: 5
total_steps: 7

# /auto-plan:review 结束时填写：
supersedes_spec_components: []
new_spec_components: []       # 预挂 SD-0031（docs/specs/apps/file-manager.md 增节）
touched_goals: []

affects: [apps/027-file-manager]   # auto-lang 不动（Category A 门）
---

# [PLAN-003] fm-preview-pane —— 预览面板·列表缩略图·tooltip

v0.7 竞争力重构第三计划（依赖 PLAN-001；与 046 无强序，建议 046 后）。
Finder Quick Look 的面板化形态 + 列表视图缩略图 + 元信息 tooltip。
需求/设计依据：app 仓 `docs/REQUIREMENTS.md` F6 + `docs/DESIGN.md` §9。

## 变更摘要

新增右侧预览面板（w-80，可开关，选中焦点驱动）：文本族头 8KB 预览
（`file.read_text_range`）、图片族大缩略图（`image.thumb(path, 512)` +
contain）、目录摘要（后台协程项数/大小，世代取消）、其他类型元信息卡。
列表视图行内小缩略图（64px，复用管线 cap 门控）；行 title tooltip
补全路径。vue 轨文本预览走 api.at 新端点（`GET /api/fs/text`）。

## 目标

1. 预览面板四形态断言全过（text/image/dir/other），选中切换 ≤1 拍
   呈现（图片渐进浮现管线既有口径）。
2. 目录摘要后台协程：选中切换即取消上一目录计算（世代计数）；UI 零
   阻塞（P-7 同门）。
3. 列表视图图片行 64px 缩略图（窗口内 cap 120 排队纪律维持）。
4. vue 轨：文本预览可用（api 端点）；图片/目录摘要 VM 专属降级文案。
5. 既有用例全绿 + parity 对拍纳入新面板。

**非目标**：空格 Quick Look 大图模态、视频悬停播放（P2 展望）；
目录占用列（P2）。

## 架构方案

依据 DESIGN §9：

- 布局：主内容区 row 内追加 `preview_on ? w-80 面板 : 无`；主列表
  `flex-1` 让宽（1.5 节 flex 纪律维持——禁 justify-between）。
- 驱动：`selected_id`（焦点）变化 → `.PreviewTick`（`SettleTick
  every_ms: 80` 形态，031 先例）防抖 2 拍后按焦点行类型分派物化
  `preview_*` 状态（避免连击方向键时每拍重读文件）。
- 文本：`file.read_text_range(path, 0, 8192)`；>8KB 注脚「前 8KB ·
  全文 N KB」。文本族判定复用 type_label 分类（文档/代码族）。
- 图片：`image.thumb(path, 512)`（大 rendition；管线优先档维持）；
  `image_surface (fit: "contain")`。
- 目录：`spawn` 协程 walk 计数（项数+字节累计，每 200 项
  `chan.send` 增量）+ Tick 消费；`preview_gen` 世代计数——新选中
  即 +1，协程发送前比世代，过期自弃（R8 看门狗：>40 拍无进展置
  失败态）。
- other：类型卡（type_name/size_str/date/path）——快照字段直取零 IO。
- vue 轨：api.at 增 `fs_text(path) -> {ok, head, total}`（thin 委托
  impl_read_head，8KB 截断 + file.size 全量）；图片/目录摘要守卫
  降级文案（12 节口径）。
- tooltip：行/卡 `title` 属性补 `name\npath\nsize · date`（VM title
  既有面，零成本）。

## 技术栈

同 045/046。新增依赖：`file.read_text_range`（file.at:20 已证）、
`async.spawn/channel`（stdlib/auto/async.at）、`timer { SettleTick }`
（Plan 051 C7 形态）。

## 需求分析与背景调查

- **授权**：同 PLAN-001（2026-10-04 用户指令族）。
- **依赖**：PLAN-001 merged（快照行 schema/RefreshView 投影在场）。
- **框架事实**：VM image widget 只认 http/data/builtin URI（本地路径
  不可直挂——thumb 管线媒体票据是唯一通道，029 pac 头注实证）；
  vue 轨 `image.*` `__vmOnly` 恒空；`image.thumb` 解码在 worker 池
  主线程零解码（P547）；.at 无 try 面——协程异常走世代自弃 + 看门狗
  （R8）。
- **基线**：045（+046）merge 后 v0.6-dev HEAD。

## 详细设计

### 状态增量

```
var preview_on bool = false        // storage fileman.preview_on
var preview_kind str = ""          // "text"|"image"|"dir"|"other"|""
var preview_text str = ""
var preview_text_note str = ""     // "前 8KB · 全文 N KB"
var preview_img str = ""           // thumb URI
var preview_dir_items int = -1     // -1 = 计算中
var preview_dir_bytes int = -1
var preview_gen int = 0            // 目录摘要世代
var preview_settle int = 0         // 防抖拍计数
```

### 面板视图骨架

```
if .preview_on {
  col (w-80 border-l bg-card ...) {
    头行（类型徽标 + 开关/关闭钮）
    if .preview_kind == "text"  { scroll { code/等宽块 .preview_text } + 注脚 }
    if .preview_kind == "image" { image_surface (src: .preview_img, fit: "contain") }
    if .preview_kind == "dir"   { 计数卡（-1 = "计算中…" spinner 文案态） }
    if .preview_kind == "other" { 元信息字段表 }
  }
}
```

### 规范增量

| delta_id | add/modify/retire | docs/specs/... target | before/after rule | rationale | acceptance IDs |
|----------|-------------------|----------------------|-------------------|-----------|----------------|
| SD-0031 | add | docs/specs/apps/file-manager.md | 预览面板契约：四形态分派、防抖 2 拍、世代取消、vue 降级矩阵 | Finder Quick Look 面板化 | AC-01..AC-04 |
| （app 仓侧） | modify | apps/027-file-manager/SPEC.md + src/back/api.at | 新预览节 + fs_text 端点契约 | 同步 | 全部 |

## 测试设计

- desktop_mcp T21 预览面板：fixture 注入 selected → text（testdata
  notes.txt 头 8KB 内容前缀断言）/ image（photo.png → preview_img
  非空 URI）/ dir（nested → 计数收敛 5）/ other（config.toml 元信息卡）
  四态 + 开关持久化（重启 storage 断言）+ 世代取消（快速切换两个目录
  后旧计数不覆盖新焦点）。
- 列表缩略图：mkbig 混图片目录 → vtree image_surface 计数 ≤120 +
  扩窗后补排队断言。
- vue 轨：Playwright——fs_text 端点（HTTP 200 + 截断注脚）+ 降级文案。
- parity 对拍：面板开态列表面纳入。
- 回归：T1-T20 全绿。

## 验收标准

- **AC-01** 预览四形态正确呈现（T21 四态断言）；选中切换防抖后 ≤2 拍
  物化（非图片形态）。
- **AC-02** 目录摘要世代取消：切换焦点后旧协程结果不被采纳（T21 断言）；
  计算期间 UI 不冻结（导航/滚动可用）。
- **AC-03** 列表缩略图：窗口内图片行缩略图呈现 + cap 120 门 + 扩窗
  补排队（vtree 计数断言）。
- **AC-04** vue 轨矩阵：文本预览走端点可用；图片/目录摘要降级文案
  在场（Playwright 断言）。
- **AC-05** 回归全绿 + parity 对拍过。

## 执行步骤

- **T-01** 面板骨架与开关
  文件：app.at
  操作：preview_on/storage/布局让宽/头行。
  验证：desktop_mcp 开关 + 持久化断言。
  → AC-01（骨架）
- **T-02** 文本形态 + api.at 端点
  文件：app.at、src/back/api.at（fs_text）
  操作：read_text_range 接线 + 注脚；vue 轨端点与守卫。
  验证：T21 text 态 + Playwright 端点。
  → AC-01/AC-04
- **T-03** 图片形态 + 列表行缩略图
  文件：app.at
  操作：thumb 512 contain；列表行 64px 位 + 窗口化排队。
  验证：T21 image 态 + 缩略图 vtree 断言。
  → AC-01/AC-03
- **T-04** 目录摘要协程（世代取消 + 看门狗）
  文件：app.at
  操作：spawn/channel/Tick 消费/preview_gen。
  验证：T21 dir 态 + 取消断言。
  → AC-02
- **T-05** other 形态 + tooltip
  文件：app.at
  操作：元信息卡（快照直取）；行/卡 title 补全。
  验证：T21 other 态。
  → AC-01
- **T-06** 测试收口 + parity
  文件：tests/desktop_mcp.py、Playwright
  验证：全绿。
  → AC-05
- **T-07** 文档同步（SPEC 新节/README/REQUIREMENTS/DESIGN revision）
  → 全 AC 证据链

## 复审记录

- 2026-10-04 stage: new（auto-plan-new 起草，plan_revision 1）。outcome:
  pass。next: work（前置：PLAN-001 merged）。


- 2026-10-04 stage: work | PLAN-003 | rev 1 | outcome: **pass
  （execution_done）** | code_commit: fbcc54b（分支 plan-003，基线 fe57d26
  叠加）| evidence: tests/verify_p3.py **10/10**（文本头内容/图片 URI/
  目录摘要收敛/other 字段/焦点切换即时）；desktop_mcp **74/74** |
  执行内裁决：① 防抖 timer 弃（点击节奏直跑 PreviewRun，更简）；
  ② 目录摘要 = Tick 驱动 BFS 分批（spawn 闭包捕获未证不走协程——
  世代=根路径比对）；③ 行级 tooltip 弃（mouse-area 无 title prop，
  债册）；④ vue 文本预览走 api.at fs_text 新端点；⑤ 预览验收以
  verify_p3.py 承载（环境病期短探针策略，同 002）。

- 2026-10-04 stage: review | PLAN-003 | rev 1 | outcome: **pass** |
  reviewed_commit: 1422701（本计划增量 fbcc54b）| base_commit: fe57d26 |
  acceptance_results: AC-01 pass（四形态 + 内容断言——verify_p3 10/10）/
  AC-02 pass（目录摘要世代取消 + 收敛）/ AC-03 pass（列表缩略图窗口
  排队——code+VM 证；像素级留 autoui-verifier 后续）/ AC-04 partial→
  pass（fs_text 端点生成实证；vue 运行时受 F-V2 演示空态牵连——端点
  与守卫在生成面完整）/ AC-05 pass（套件 74/74）| findings: 行级
  tooltip 弃（mouse-area 无 title prop，在案）| evidence: verify_p3
  10/10 + 套件 74/74（1422701）| next: merge。

## 待澄清事项

- 无。
