# llm-wiki-lite

基于 [llm-wiki](https://github.com/crylearner/llm-wiki) 的知识库技能，优化了**多人协作提交冲突**、**知识库质量度量**两大痛点。AI 负责编写与维护 `wiki/`，人只维护 `docs/`（AI 只读），每个事实可双链回溯、可验证。

> 依赖 [obsidian-mcp-pro](https://github.com/crylearner/obsidian-mcp-pro)：检索面（语义检索 / 别名解析 / 反链 / 断链检查）与**零 LLM 调用遥测采集**均由其提供。本仓库的度量与覆盖度能力建立在该工具之上。

---

## 特色说明

- **冲突友好（多人协作）**：人只写 `docs/`、AI 只写 `wiki/`，读写区严格分离，从源头避免提交冲突；Ingest 走"diff 而非全文"的最小补丁，不整页重写。
- **零 LLM 质量度量（需求侧）**：消费 obsidian-mcp-pro 的工具调用遥测（需 `OBSIDIAN_TELEMETRY` 开启），算**回源依赖度 / 失败率 / 检索命中率 / 路径跳数 / refine 触发率**，并输出**可执行的"知识缺口"清单**（哪些常用知识没录入 `MISSING`、哪些在库但检索词没接住 `STALE_SEARCH`）。
- **零 LLM 静态覆盖度（供给侧）**：冷启动或尚无查询行为时，比对源文档章节树与 wiki 吸收文本，算出"源文档有多少被 wiki 吸收"，定位结构性未纳管缺口。
- **确定性坐标 `raw`**：每个 `wiki/` 页面用 frontmatter `raw` 声明其证据源文档坐标，让度量、Lint、覆盖度都能机械地"滚回"源文档，无需 LLM 猜测。
- **双链 + 回溯**：所有事实可经 `[[wiki 页]]` / `raw` 坐标回溯到 `docs/` 原文，可验证、可审计。
- **MIT License**，与所依赖的 obsidian-mcp-pro 一致。

---

## 快速开始

### 前置

1. 安装并运行 [obsidian-mcp-pro](https://github.com/crylearner/obsidian-mcp-pro)（提供 MCP 检索面与遥测采集）。
2. 本 skill 目录（`SKILL.md` + `scripts/`）可整体拷贝独立部署。
3. 项目布局约定：
   ```
   <project-root>/
   ├── docs/          # 源文档（人维护，AI 只读）
   └── wiki/          # 知识库（AI 唯一可写区）
       ├── src-*.md   # 主题页（frontmatter 含 raw 坐标）
       ├── con-*.md   # 概念/聚合页
       ├── index.md   # 可选索引
       └── log.md     # 操作日志
   ```

### 1. 录入（Ingest）

四阶段流水线（变更检测 → 影响面 → 增量编译 → 确定性收尾），零 LLM 的变更检测先行：

```bash
python scripts/wiki_changed.py <project-root>     # 阶段A：零 LLM 变更检测，产出待处理清单
# 阶段 C/D 由 AI 按 SKILL.md 规程执行（读 diff、最小补丁、刷新 raw/version）
```

### 2. 查询（Query）

经 obsidian-mcp-pro 检索面查询（`search_notes` / `resolve_alias` / `get_backlinks`），无果再回源 `docs/` 补充。**查询不写文件**。

### 3. 检查（Lint，统一入口）

```bash
# 事实校验 + 覆盖度（冷启动即可）；有遥测时一并出需求侧度量与知识缺口
python scripts/wiki_lint.py <project-root> [--events telemetry.jsonl]
# 报告含：事实硬错误 + 覆盖度 +（有遥测）回源/失败/命中/跳数/refine 率 + 知识缺口清单
# 知识缺口：[INGEST] 待录入 / [FIX-INDEX] 待修索引 → 直接作为 Ingest 待办
```

### 4. 质量度量（需 obsidian-mcp-pro 遥测，由 wiki_lint 内部调度）

```bash
# 终端启动 obsidian-mcp-pro 时打开遥测（事件落盘），之后跑 wiki_lint 即含度量
OBSIDIAN_TELEMETRY=/abs/path/telemetry.jsonl node dist/index.js
python scripts/wiki_lint.py <project-root> --events telemetry.jsonl
# 底层脚本 wiki_check / wiki_coverage / wiki_metrics 也可单独调用
```

---

## 脚本一览

| 脚本 | 用途 | 是否零 LLM |
|------|------|-----------|
| `wiki_changed.py` | Ingest 阶段 A 变更检测 | 是 |
| `wiki_lint.py` | **统一 Lint 入口**：编排 wiki_check + wiki_coverage +（有遥测时）wiki_metrics，合并成一份报告 | 是 |
| `wiki_check.py` | Lint 事实校验（字面量保真/版本漂移/证据错误），被 wiki_lint 调用 | 是 |
| `wiki_coverage.py` | 供给侧静态覆盖度（章节/术语吸收），被 wiki_lint 调用 | 是 |
| `wiki_metrics.py` | 需求侧度量 + 知识缺口清单（消费遥测），被 wiki_lint 调用 | 是 |
| `rebuild_index.py` | 重建可选 index | 是 |
| `wiki_log.py` | 出错定位日志：脚本异常时把堆栈写临时目录（按项目隔离），正常执行不产生文件 | 是 |

---

## License

[MIT](./LICENSE) —— 与所依赖的 obsidian-mcp-pro 一致。
