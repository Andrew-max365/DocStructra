# 变更记录

## [未发布] — LLM 标注超时问题修复

### 问题描述

执行 `python -m cli.format_docx tests/samples/test_in.docx tests/samples/locx --label-mode llm`
时，`service/format_service.py` 会在警告中输出：

```
LLM labeling failed, falling back to rule-based: LLM 审阅调用超时: Request timed out.
```

根本原因：
1. 超时时间固定（默认 60 秒），对大文档或网络较慢的环境不够充裕。
2. 超时后直接失败，没有重试，任何一次瞬时抖动都会导致回退。
3. 错误信息笼统，无法区分"连接超时"与"读取超时"，难以诊断。
4. 超时、重试、连接等参数均不可配置，难以根据部署环境调整。

---

### 修改内容

#### 1. `config/__init__.py` — 新增四个可配置环境变量

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `LLM_CONNECT_TIMEOUT_S` | `10` | 建立 TCP 连接的超时秒数；连接失败时更快暴露问题 |
| `LLM_MAX_TIMEOUT_S` | `120` | 动态读取超时的上限；防止文档过大时超时设置过长 |
| `LLM_RETRY_ATTEMPTS` | `3` | 超时/网络错误的最大重试次数（含首次） |
| `LLM_RETRY_BACKOFF_S` | `1` | 重试指数退避基础等待秒数（实际等待 = base × 2ⁿ⁻¹） |

**原有 `LLM_TIMEOUT_S`（默认 60 秒）保持不变**，作为动态超时的基准值。

---

#### 2. `agent/llm_client.py` — 三项核心改进

**2a. `compute_dynamic_timeout(n_paragraphs: int) -> int`（新增函数）**

根据送入 LLM 的段落数量，自动拉长读取超时：

```
timeout = min(LLM_TIMEOUT_S + n_paragraphs × 0.5, LLM_MAX_TIMEOUT_S)
```

例：60 段落 → 90 秒；200 段落 → 上限 120 秒。文档越大，允许的响应时间越长。

**2b. `LLMClient._execute_chat_completion(messages, timeout)`（新增方法）**

将所有 API 调用统一收口到此方法，具备：

- **自动重试**：对 `APITimeoutError` 和 `APIConnectionError` 自动重试，最多 `LLM_RETRY_ATTEMPTS` 次，每次等待 `LLM_RETRY_BACKOFF_S × 2ⁿ⁻¹` 秒（指数退避）。
- **不重试鉴权错误**：`AuthenticationError` 立即抛出，避免无效重试。
- **错误类型细化**：通过检查底层 httpx 异常的 `__cause__` 名称，将超时细分为：
  - `connect_timeout`：建立连接时超时
  - `read_timeout`：等待响应时超时
  - `timeout`：其他超时
  - `connect_error`：网络连接失败

**2c. `LLMClient.__init__` — 使用 `openai.Timeout` 分别设置连接/读取超时**

```python
timeout=openai.Timeout(LLM_TIMEOUT_S, connect=LLM_CONNECT_TIMEOUT_S)
```

`call_raw` 和 `call_review` 均已重构为调用 `_execute_chat_completion`，并自动传入
`compute_dynamic_timeout` 计算出的动态超时值。

---

#### 3. `service/format_service.py` — 更清晰的回退警告

回退警告现在包含模式名称、块数量和错误类型，方便定位问题：

```
[format_service] LLM labeling failed (llm mode, 47 blocks, error_type=read_timeout),
falling back to rule-based: LLM 读取超时 (尝试 3/3): ...
```

---

#### 4. `tests/test_llm_timeout.py` — 新增 16 个测试

覆盖以下场景：

- 动态超时公式正确性（含上限截断）
- 重试次数（超时/网络错误触发重试；鉴权失败不重试）
- 指数退避时间验证（`time.sleep` 调用参数）
- 首次失败后第二次成功即返回
- `openai.Timeout` 被正确传递到 `create` 调用
- 回退后返回规则标签，警告中含模式名/块数/错误类型

---

#### 5. `README.md` — 环境变量文档更新

`### 环境变量配置` 一节更新，列出全部 9 个环境变量（含新增 4 个），并增加动态超时与重试机制的说明段落。`### agent/llm_client.py` 模块说明同步更新。

---

### 验收标准确认

| 要求 | 结论 |
|---|---|
| 运行 llm 模式不再因超时太短立即失败（默认重试 3 次 + 动态超时） | ✅ |
| 超时警告更清晰（含模式/块数/错误类型） | ✅ |
| 现有规则兜底仍正常工作，输出/报告不变 | ✅ |
| 全部测试通过（159 个，含新增 16 个） | ✅ |
