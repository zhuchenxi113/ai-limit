# ai-limit

[English](README.md)

查看 Claude Code 和 CodeX 的实时剩余额度与 token 消耗情况。

## 效果

```
────────────────────────────────────────────────────
                    Claude Code                     

  统计范围: 05-19 15:24 CST  (7 天内)
  总输出: 3.2M  |  净输入(非缓存): 13.9M

  输出占比
  sonnet-4-6  ███████████████░░░░░  76%
  opus-4-7    █████░░░░░░░░░░░░░░░  24%

  实时额度  (与 --days 统计范围无关)
  数据来源: claude.ai usage API  (浏览器登录态)

  5小时滚动窗  ██████████████░░░░░░  剩余 68%  (已用 32%)
  重置时间: 05-26 16:20 CST

  7天滚动窗   ██████████████████░░  剩余 89%  (已用 11%)
  重置时间: 05-31 13:00 CST

  📊 按当前速率 (0.3%/小时)，剩余 89% 约可用 344 小时

────────────────────────────────────────────────────
                CodeX (OpenAI GPT-5)                

  数据时间: 05-26 15:24 CST  (实时(网页))
  数据来源: chatgpt.com usage API  (浏览器登录态)
  套餐: PLUS

  5小时滚动窗  ████████░░░░░░░░░░░░  剩余 39%  (已用 61%)
  重置时间: 05-26 17:22 CST

  7天滚动窗   ██████████████████░░  剩余 89%  (已用 11%)
  重置时间: 06-01 18:26 CST

  📊 按当前速率 (0.5%/小时)，剩余 89% 约可用 170 小时

────────────────────────────────────────────────────
```

## 环境要求

- macOS
- Python 3.8+
- Chrome 或 Firefox 已登录 [claude.ai](https://claude.ai)（用于读取 Claude 额度）
- Chrome 或 Firefox 已登录 [chatgpt.com](https://chatgpt.com)（用于读取 CodeX 额度，推荐路径）
- 可选：[CodeX CLI](https://developers.openai.com/codex/cli) 已安装并登录（作为浏览器 cookie 失效时的兜底路径）

## 安装

**1. 克隆项目**

```bash
git clone https://gitee.com/zhuchenxi113/ai-limit.git ~/Developer/ai-limit
```

**2. 安装依赖**

```bash
pip install -r requirements.txt
```

**3. 配置 alias**

在 `~/.zshrc` 中添加：

```bash
alias ai-limit="python3 ~/Developer/ai-limit/usage.py"
```

然后执行：

```bash
source ~/.zshrc
```

## 用法

```bash
ai-limit              # 最近 7 天（默认）
ai-limit --days 1     # 今天
ai-limit --all        # 全部历史
ai-limit --offline    # 不启动 CodeX app-server，只读本地快照
ai-limit --detail     # 展示每个模型的详细 token 统计
```

输出语言自动识别系统 locale（中文系统输出中文，其他系统输出英文）。可用 `AI_LIMIT_LANG` 环境变量手动指定：

```bash
AI_LIMIT_LANG=en ai-limit   # 强制英文
AI_LIMIT_LANG=zh ai-limit   # 强制中文
```

## 数据来源

### Claude Code

| 数据 | 来源 |
|------|------|
| token 消耗明细 | `~/.claude/projects/**/*.jsonl` |
| 实时剩余额度 | 浏览器 Cookie → `claude.ai/api/organizations/{orgId}/usage` |

额度获取依赖 Chrome/Firefox 的 claude.ai 登录态。Cookie 失效时自动回退，显示失败原因和网页链接。

### CodeX

数据源按优先级依次尝试：

| 优先级 | 数据 | 来源 | 是否触发 5h 窗口 |
|------|------|------|------|
| 1 | 实时剩余额度 | 浏览器 Cookie → `chatgpt.com/backend-api/codex/usage` | ❌ 不触发 |
| 2 | 实时剩余额度 | `codex app-server` WebSocket → `account/rateLimits/read` | ⚠️ **会触发** |
| 3 | 本地回退 | `~/.codex/sessions/**/*.jsonl` | ❌ 不触发 |

浏览器路径（路径 1）复用 chatgpt.com 网页分析端点，与 dashboard 同一通道，覆盖 **Cloud + CLI 合并用量**，只读不触发窗口。这是默认推荐路径。

> **⚠️ 副作用警告（CodeX 协议限制）：** 当路径 1 失败（未登录 chatgpt.com / cookie 过期 / 网络异常），ai-limit 会自动 fallback 到 `codex app-server`。这条路径需要发送 `initialize` 调用，OpenAI 会将其计为一次会话开始——若当前 5 小时窗口已到期，**会触发新的 5 小时冷却窗口计时**。这是 CodeX CLI 数据接口的固有机制，工具层面无法规避。
>
> 如果只想查看本地数据而完全不联网，请使用 `--offline` 参数。

## 说明

- 浏览器 Cookie 读取仅支持 macOS（依赖系统 Keychain 解密 Chrome Cookie）
- Claude 额度使用的是 claude.ai 内部接口，**非官方 API**，可能随版本变化失效
- `<synthetic>` 模型记录是 Claude Code 遇到 API 错误时写入的占位，不计入统计
- 各模型输出占比仅 Claude Code 提供；CodeX 不区分模型，无此数据

## 维护说明

个人工具，按自己的使用需求维护，不保证及时处理 issue 或 PR，也不承诺长期支持。

## License

本项目代码使用 [Apache License 2.0](LICENSE)。

第三方依赖：`browser-cookie3` 使用 LGPL 协议。
