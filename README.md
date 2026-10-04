# auto-explorer

文件与资源浏览器，使用 AutoLang / AutoUI 开发的独立应用。

本仓是首批产品源码基线；当前能力以导入版本为准，仓库描述中的产品方向不表示全部已实现。

## 运行

安装对应版本的 `auto` CLI 后，从本仓根执行：

```sh
auto run
auto run -r vm
```

前端端口：`17840`。后端端口：`17841`。

共享 StyleKit 已固定在 `vendor/stylekit`，无需相邻 auto-lang 示例目录。

## v0.7 功能全景（PLAN-001..005 已实施，待统一复审）

- 性能基座：三层分离 + 平行标量数组快照 + 渐进扩窗 + SNAP_CAP=8000
  （应用内实测 2000-6000 项快照+派生 <1ms）
- 键盘与多选：全键盘面（导航/操作/范围选择）+ 路径键多选 + 批量
  复制/剪切/粘贴/递归删除 + 冲突三选（覆盖/跳过/保留两者）
- 预览面板：文本头/图片/目录摘要（后台 BFS）/元信息卡
- 递归搜索（子树即搜即显，结果全功能可操作）+ 收藏夹 + 自然排序
- 交互响应优化（PLAN-005）：自适应内联派发——普通目录（≤4000 项）
  目录切换零 Tick 延迟（超大目录保留预算拆链保险）；首屏窗口 300→120；
  右键菜单 content 条件挂载（闭合行空挂载，消闭合态控件爆炸）
- 验收：desktop_mcp 78 用例 + verify_p2/p3/p4/p5 短探针（46 断言）
  + perf_check 应用内计时门——全部绿

## v0.7 性能基座（PLAN-001）

快照/派生/渲染三层分离：目录切换建一次平行标量数组快照，排序/过滤/
隐藏交互零重读盘（应用内实测 2000-6000 项派生 <1ms）；渐进渲染窗口
（扩窗哨兵）+ 超大目录 SNAP_CAP=8000 分层标注；手写归并排序（原生
sort_by 在 app VM 会话不可链，探针留证）。验收：`tests/desktop_mcp.py`
（73 用例）+ `tests/perf_check.py`（应用内计时门）。详见
`docs/REQUIREMENTS.md` / `docs/DESIGN.md` / `docs/plans/001-*`。

## 来源与组合

来源提交、路径与文件 hash 见 `SOURCE-IMPORT.json`。首次导入提交保留在 `source-sync` 分支；完整 v0.5 恢复后从该基线导入差异，再与产品开发线合并。

AutoOS 通过 [`apps/027-file-manager`](https://github.com/auto-stack/auto-os/tree/v0.6-dev/apps/027-file-manager) submodule 固定本仓版本；教学 Demo 保留在来源仓。

已有测试随源导入；端口与平台相关测试需要按本仓配置准备运行环境。安装/启动与双端完整功能验收是不同检查项。
