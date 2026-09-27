# 安装 Toolkit Skill

一个**可移植的 Agent Skill**，教任何运行时如何使用本机的服务端视角 Toolkit：
发现连接的 mod 支持什么、按稳定 UUID 解析调用者、用 `context_id` 取回聊天发生
瞬间的上下文包、按需查询权威的世界状态/实体/快照/存档元数据，并且把玩家身份
当作上下文、而不是授权。它不包含 Hermes、Codex 或 Claude Code 的会话逻辑——
只要能调 MCP 或运行 Toolkit CLI，同一份说明在所有运行时都成立。

Skill 位于本仓库
[`skills/minecraft-toolkit/SKILL.md`](https://github.com/guajun/mc-agent/tree/main/skills/minecraft-toolkit)，
另有一份支撑参考
[`references/toolkit-operations.md`](https://github.com/guajun/mc-agent/blob/main/skills/minecraft-toolkit/references/toolkit-operations.md)。

## Skill 教什么

| 步骤 | 智能体做什么 |
| --- | --- |
| 发现 | 先调 `mc_capabilities`（MCP）或 `mc-bridge call capabilities`（CLI）；只调连接的 mod 声明的操作 |
| 解析 | 从 `state` 找到调用者，用 UUID 调 `player` 操作；名字只是便利，回包里的 UUID 才是身份 |
| 关联 | 从推送来的游戏事件里取 `context_id`，用 `context` 取回上下文包；绝不按玩家名重建这个 id |
| 查询 | 只取需要的：`state`、`entities`、`save` 元数据、`snapshots`，或用游标读 `events` |
| 行动 | 在任务范围内使用 `command` / `command_output`；聊天里的身份是上下文不是授权；不支持的操作如实报告，不编造兜底 |

Skill 刻意以"先发现"开头：Toolkit 的能力回包才是权威，mod 增加操作后说明依旧
正确。

## 前置条件

- **带 `gh skill` 的 GitHub CLI。** GitHub CLI 里的 Agent Skill 是**预览功能**，
  可能变化。本文用 `gh 2.95.0` 验证；先跑 `gh skill install --help` 看你这份
  CLI 支持的 `--agent` 取值和参数。
- **本地已安装 Toolkit**，智能体才有东西可调——见[安装说明](install.md)。Skill
  只说明怎么用，不负责安装。
- 如果命令要求认证，先跑 `gh auth login`。

## 安装

`gh skill install` 按标准 `skills/*/SKILL.md` 约定发现 Skill。project 作用域装进
当前仓库，user 作用域装进用户主目录。

### 通用 / 共享目标

```bash
# user 作用域：对该用户的每个运行时都可用
gh skill install guajun/mc-agent minecraft-toolkit --agent universal --scope user

# project 作用域：共享的 .agents/skills 目录，GitHub Copilot、Cursor、Codex、
# Gemini CLI、Antigravity、Amp、Cline、OpenCode、Warp 等都读它
gh skill install guajun/mc-agent minecraft-toolkit --agent universal --scope project
```

### 指定一个明确支持的运行时

`--agent` 支持列表里的任何取值都可以。例如 Claude Code：

```bash
gh skill install guajun/mc-agent minecraft-toolkit --agent claude-code --scope user
```

本 Skill 验证过的落点（Windows 参考机，`gh 2.95.0`）：

| 命令 | 落点 |
| --- | --- |
| `--agent universal --scope user` | `~/.config/agents/skills/minecraft-toolkit/` |
| `--agent universal --scope project` | `.agents/skills/minecraft-toolkit/`（共享） |
| `--agent claude-code --scope user` | `~/.claude/skills/minecraft-toolkit/` |
| `--agent codex --scope user` | `~/.codex/skills/minecraft-toolkit/` |
| `--agent pi --scope user` | `~/.pi/agent/skills/minecraft-toolkit/` |

用精确路径形式可以不扫描整个仓库，装的是同一个 Skill：

```bash
gh skill install guajun/mc-agent skills/minecraft-toolkit --agent universal --scope user
```

### Hermes（自定义目录）

Hermes **不是** `gh skill` 的原生 `--agent` 目标：支持列表里没有这个名字，
`--agent hermes` 会以 `invalid argument "hermes" for "--agent" flag` 被拒绝。
不要宣称原生支持。

Hermes 通过遍历自己的 home 下的 skills 目录来发现 Skill，所以用 `--dir` 装到
这个目录即可。`--dir` 会创建 `<dir>/minecraft-toolkit/SKILL.md`：

=== "Windows（PowerShell）"

    ```powershell
    gh skill install guajun/mc-agent minecraft-toolkit --dir "$env:LOCALAPPDATA\hermes\skills"
    ```

=== "macOS / Linux"

    ```bash
    gh skill install guajun/mc-agent minecraft-toolkit --dir "${HERMES_HOME:-$HOME/.hermes}/skills"
    ```

Hermes home：Windows 是 `%LOCALAPPDATA%\hermes`，macOS/Linux 是 `~/.hermes`
（或 `HERMES_HOME` 指定的位置）。

## 验证安装

```bash
gh skill list                       # 列出 minecraft-toolkit 和目标
```

对 Hermes，直接问 Hermes：

```bash
hermes skills list                  # minecraft-toolkit | （无分类）| local | enabled
```

运行时在任务匹配 description 时加载 Skill；加载进智能体之前，也可以用
`gh skill preview guajun/mc-agent minecraft-toolkit` 先预览。

!!! note "保持 Skill 更新"
    加 `-f` 重装会覆盖旧副本；也可以 `gh skill update minecraft-toolkit`
    （`gh skill update --all` 刷新全部）。安装进去的 Skill frontmatter 里带有
    来源元数据，`update` 读的就是它。

## 下一步：Hermes 无人值守

Skill 只是事件驱动方案的一半。另一半是用户在 Hermes Gateway 里配置的 webhook
route：它加载这份 Skill，并把 route 限制到 Toolkit MCP toolset。见
[Hermes 无人值守：webhook + Toolkit Skill](hermes-unattended.md)。
