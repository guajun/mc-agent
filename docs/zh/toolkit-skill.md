# 安装 Toolkit Skill

一个**可移植 Agent Skill** 教任何 Harness 使用 CLI-only 的服务端视角 Toolkit：
发现连接与能力面、按稳定 UUID 解析调用者、按 `context_id` 取回聊天时刻上下文、
处理事件游标与缺口、处理未知写结果，以及做受控的原位实验。它不包含 Hermes、
Codex 或 Claude Code 的会话逻辑——同样的说明适用于任何能执行命令的 Harness。

Skill 位于本仓库
[`skills/minecraft-toolkit/SKILL.md`](https://github.com/guajun/mc-agent/tree/main/skills/minecraft-toolkit)，
带一个参考文件
[`references/toolkit-operations.md`](https://github.com/guajun/mc-agent/blob/main/skills/minecraft-toolkit/references/toolkit-operations.md)。
本仓库是**唯一权威来源**：发行包从 bridge 发布中 `skill-pin.json` 记录的固定提交
打包，绝不使用手工维护的副本。

## Skill 教什么

| 步骤 | 智能体做什么 |
| --- | --- |
| 发现 | 运行 `mc-agent version`/`doctor`，再运行 `capabilities` 和 `schema`；只调用连接 mod 声明的操作 |
| 连接 | 启动或复用 daemon；按错误码区分 daemon 未运行、mod 未连接、操作不支持与未知写结果 |
| 解析 | 从 `state` 找到调用者，按 UUID 调用 `player`；名字只是便利，回复中的 UUID 才是身份 |
| 关联 | 从推送的事件取 `context_id` 并及时取回 bundle；绝不按玩家名重建 id |
| 查询 | 只取所需：`state`、`entities`、`save` 元数据、`snapshots`，或按游标读 `events` |
| 行动 | 在任务范围内使用 `command`/`command-output`，把聊天身份当上下文而非授权，不支持的操作如实报告 |
| 恢复 | 如实处理 `event_gap`、`game_restarted` 与 `truncated`；先查 `request-status` 而不是重放非幂等写 |
| 实验 | 在同一维度与完整 X/Y/Z 重复试次；恢复并核对基线，只改变声明的变量，记录观察结果 |

Skill 采用发现优先：capability 回复是权威，因此随着 mod 增加操作，说明仍然正确。

实验时，同一 X/Z、不同 Y 不是原位重复。只有把高度/位置显式作为自变量时才允许变化；
如果无法验证基线恢复，智能体必须报告限制，而不是把挪位后的测试说成受控比较。

这些是 Harness 侧行为说明。Toolkit 不会把它们实现为命令守卫。更新已安装副本后，要
验证实际运行确实加载了 `minecraft-toolkit` 并遵循连接与实验指引；技能列表里显示
启用并不能证明这一点。

## 从发行包安装（固定版本）

bridge 安装器会按发布校验和验证 Skill 包，并且除非指定 `--update-skill`，不会覆盖
已有目录：

```bash
# 显式指定 harness
sh install.sh --version 0.5.0 --skill-harness codex
# 或自定义父目录；会安装到 <DIR>/minecraft-toolkit/
sh install.sh --version 0.5.0 --skill-dir /path/to/skills
```

| `--skill-harness` | 目标目录 |
| --- | --- |
| `codex` | `$CODEX_HOME/skills` 或 `~/.codex/skills` |
| `claude-code`（`claude`） | `$CLAUDE_CONFIG_DIR/skills` 或 `~/.claude/skills` |
| `universal` | `$XDG_CONFIG_HOME/agents/skills` 或 `~/.config/agents/skills` |
| `hermes` | `$HERMES_HOME/skills`（安装器要求设置 `HERMES_HOME`；Hermes 默认目录随版本变化，不猜测） |

Windows 使用 `./install.ps1 -Version 0.5.0 -SkillHarness codex`（或 `-SkillDir DIR`）。

## 用 `gh skill` 安装

支持原生 Skill 的 Harness 可以用自己的流程。GitHub CLI skills 处于 preview；
本文以 `gh 2.95.0` 验证：

```bash
gh skill install guajun/mc-agent minecraft-toolkit --agent codex --scope user
# 或显式目标目录
gh skill install guajun/mc-agent minecraft-toolkit --dir "$HOME/.hermes/skills"
```

Hermes 没有原生 `gh skill` agent 目标，用
`--dir "$HERMES_HOME/skills"`，或用发行安装器的 `--skill-harness hermes` 并设置
`HERMES_HOME`。`HERMES_HOME` 以你安装的 Hermes 配置为准；发行安装器有意不猜测默认目录。

`gh skill` 读取仓库分支；发行安装器才是绑定到具体 toolkit 版本的路径。更新已安装
副本后，请验证新内容确实被加载。

## 前提

- 已安装二进制并配置目标——见[安装与首次运行](getting-started.md)。Skill 只解释
  Toolkit，不安装它。
- 使用 `gh skill` 时需要已认证的 GitHub CLI（`gh auth login`）。

## 下一步：无人值守

Skill 是事件驱动方案的一半。另一半是用户配置的路由（例如 Hermes），它加载本 skill
并订阅 daemon 的签名 webhook：见
[无人值守 Hermes：webhook + Skill](hermes-unattended.md)。
