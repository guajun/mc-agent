# 玩家身份与游戏上下文

[English](https://guajun.github.io/mc-agent/player-identity/)

Toolkit 没有一个永久绑定的玩家身体。默认服务端视角可以解析任意在线玩家并
返回该玩家的服务端已知上下文。哪个玩家与任务相关，由用户请求、Harness
身份映射或游戏内事件决定。

## 当前调用者上下文

Harness 知道稳定 UUID 时应优先使用；名字只是便捷输入：

~~~powershell
mc-bridge call player '{"player":"<uuid-or-name>"}'
~~~

结果包含身份、维度、位置、速度、旋转、生命、游戏模式、眼睛位置和服务端
视线射线。这是权威服务端状态，不是客户端准星或渲染插值。

游戏聊天触发的任务中，事件可以包含 **context_id**。对应上下文包冻结服务端
收到消息时发送者的精简上下文：

~~~powershell
mc-bridge call context '{"id":"<context_id>"}'
~~~

缓存容量和有效期有限，应尽早读取。Agent 执行期间需要的新信息再通过
**state**、**entities** 或其它 Toolkit 调用获取。

身份不是授权。匹配的 UUID/名字或上下文包不能证明发送者有权执行命令、修改
文件或控制另一位玩家。该策略属于 Harness/操作者。

## 给 Agent 一个身体

上下文回答“谁的请求和视角与任务相关”，但不会创建由 Agent 控制的玩家。
实验确实需要可见身体时，Carpet 假人通常成本最低：

~~~powershell
python tools/fake_player.py spawn agent 103 95 52
python tools/fake_player.py action agent look north
python tools/fake_player.py action agent jump
python tools/fake_player.py status agent
python tools/fake_player.py say "hello" --as agent
python tools/fake_player.py kill agent
~~~

假人是服务端真实玩家实体，压力板、生物仇恨和区块加载都会考虑它。它没有
客户端屏幕或相机，并且需要 Carpet 与命令权限。Toolkit 可通过命令驱动它，
按名字/UUID 读取权威状态。

## 用户主动型 Harness

Codex、Claude Code 和其它用户主动型 Harness 不需要游戏聊天触发词或专用
backend。用户启动 Harness，必要时指出相关玩家，Harness 再从 Toolkit 拉取
上下文。

回复需要出现在 Minecraft 时，Harness 必须显式选择投递方式，例如服务端
**tellraw** 命令。不要只根据不可信显示名推断回复目标。

## 无人值守事件

Hermes 等接收方可以由单独配置的签名事件 webhook 唤醒。事件提供发送者元数据
以及可能存在的 **context_id**；Hermes 仍使用同一套 Toolkit 读写游戏。
webhook 路由、授权和回复投递属于 Harness 配置，不是玩家身份功能。

route 与投递配置见 [Hermes 无人值守运行](hermes-unattended.md)。在请求头与
delivery-ID 互操作问题
[mc-agent-bridge#7](https://github.com/guajun/mc-agent-bridge/issues/7)
落地前，签名端到端投递仍会 fail closed。

## 旧客户端身份

只有 Agent 确实需要屏幕状态、客户端相机、打开存档或加入服务器等客户端专属
能力时，才使用客户端视角：

~~~powershell
mc-bridge run --vantage client --port-file "C:/path/to/mc-agent/port.txt"
~~~

第二个 Minecraft 客户端可给 Agent 独立账号、背包、屏幕和相机，但要付出一个
完整客户端的资源，并且认证服务器还需要第二个账号。它的 Toolkit daemon 必须使用
不同 API 端口和端口文件。

可选兼容 **mc-agent-loop** 仍可响应聊天，默认触发词是 **@agent**。这套
loop/client 设计不是默认 Toolkit 架构，不能只为获得服务端已知玩家上下文而使用。
