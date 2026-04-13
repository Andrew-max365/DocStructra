# DocStructra 维护边界说明

本文档用于约束子目录之间的依赖方向，目标是：**可以按子文件夹独立维护，减少牵一发而动全身**。

## 1) 子域划分（`agent/subagents`）

- `ingest_parse`：文档读取、段落遍历、结构化 block 构建
- `format_act`：规则判定与排版执行
- `validate_review`：LLM 审阅、模式路由、视觉审查
- `intent_route`：用户意图识别、模板路由、增量指令解析
- `orchestrator`：工作流编排、服务入口、图执行

## 2) 跨子域依赖规则

跨子域调用统一通过各子域 `api.py`：

- `agent/subagents/ingest_parse/api.py`
- `agent/subagents/format_act/api.py`
- `agent/subagents/validate_review/api.py`
- `agent/subagents/intent_route/api.py`
- `agent/subagents/orchestrator/api.py`

建议：

1. **跨子域不要直接 import 对方内部文件**（例如 `xxx/some_impl.py`）。
2. **同子域内部**可直接引用本子域模块，避免不必要的循环依赖。
3. 旧兼容路径（`agent/*`, `core/*`, `service/*`）仅作为兼容层，不承载新逻辑。

## 3) 变更建议

当你只维护某一子域时，优先修改该子域的：

- `api.py`（对外协议）
- 本子域内部实现文件（内部逻辑）

尽量避免修改其他子域文件；若必须变更跨域调用，优先在调用方与被调用方 `api.py` 调整接口，再局部更新调用点。

