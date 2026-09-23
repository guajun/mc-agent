# 智能体自己拉起的实验室服务器

`tools/lab_server.py` 为每个实验拉起一台用完即弃的无头 Fabric 服务器——一个**实验室**——并给智能体一个进得去的控制台。没有 GUI、没有启动器、没有要点来点去的窗口，也不和玩家的活客户端共享任何东西：供给一个目录、后台启动、通过 RCON 敲命令。

```powershell
python tools/lab_server.py provision --name smoke --void --fabric-api --carpet
python tools/lab_server.py start --name smoke --wait 300     # 后台运行；日志写 labs/smoke/logs/console.log
python tools/lab_server.py exec --name smoke "summon minecraft:tnt 0 100 0 {}"
python tools/lab_server.py exec --name smoke "tick freeze"
python tools/lab_server.py status --name smoke --tail 20
python tools/lab_server.py stop --name smoke
python tools/lab_server.py list
```

`provision` 接受 `--void`（虚空生物群系的纯空气超平坦世界）或 `--world DIR`（把已有存档拷进来），以及 `--fabric-api`、`--carpet`、`--mod-jar PATH`、`--mod-url URL`、`--mc VERSION`、`--memory 2G`、`--java PATH`。Java 依次取 `--java`、`$MC_AGENT_JAVA`、PATH 上的 `java`；provision 解析到的结果会被记住，之后 `start` 不必重复指定。`start --wait N` 会在日志出现 `Done` 时立刻返回。

```
labs/<lab>/       fabric-server-mc.*-launcher.*.jar、server.properties、
                  eula.txt、rcon.json（host/port/password）、lab.json、
                  run.json、mods/、world/、logs/console.log、
                  .fabric/server/（启动器下载下来的服务端 jar）
labs/_cache/      启动器 jar + mod jar，所有实验室共用一份下载
```

`exec` 自己实现了 RCON（length/id/type 头，payload 加两个 NUL，type 3 登录、type 2 执行，回答可能跨包），并把控制台回答的内容打出来——那就是命令的反馈，所以 `list`、`tick freeze` 和报错都会以文本回来。有几个怪癖是**游戏**的，不是工具的：`save-all flush` 会以单个包到达、两条消息粘在一起；`stop` 会在回复完成前就关掉 socket。

## 26.2 的无头服务器会逼你做的事

* **启动器其实是个安装器。** `meta.fabricmc.net/.../server/jar` 背后那个 jar 带着 `install.properties`（`game-version=26.2`），会把 vanilla 服务端下载到 `.fabric/server/26.2-server.jar`，把它的 bundle 解到 `versions/26.2/server-26.2.jar`，并安装依赖库。所以第一次 `start` 约 40 秒、约 140 MB；`labs/_cache/` 只帮你省掉启动器和 mod。
* **要 Java 25，不是 21。** 服务端自己的 mod 列表里写着 `java 25` 配 `minecraft 26.2`，用更老的 JVM 会在世界加载前就死掉：`UnsupportedClassVersionError: net/minecraft/bundler/Main has been compiled by a more recent version of the Java Runtime (class file version 69.0), this version of the Java Runtime only recognizes class file versions up to 65.0`。PATH 上是 Java 21 的机器必须传 `--java`（或设 `MC_AGENT_JAVA`）。
* **没人登录，世界可能不 tick。** 26.2 有 `pause-when-empty-seconds`（默认 60）；工具把它设成 `0`，否则一个只通过 RCON 说话的实验室会把自己暂停，"跑 N tick"的实验会悄悄什么都不做。
* **什么都没加载。** 没有玩家的实验室没有区块被加载，所以在你 `forceload add 0 0` 之前，`if block` / `data get block` 会回 *That position is not loaded*。虚空实验室的检查要先做这一步。
* **时间现在是一条时间轴。** `time query daytime` 没有了；26.2 对 `time query day` 回 `Timeline minecraft:day is at 362 tick(s)`，对 `time set day` 回 `Set minecraft:overworld to time marker minecraft:day`。
* **server.properties 会在首次启动时被重写**：`:` 会被转义（`level-type=minecraft\:flat`），默认项被追加，26.2 还会加一整块 `management-server-*`——第二个默认关闭的管理监听器，自带生成的密钥，和 RCON 无关。
* **能用的虚空写法**就是工具写下去的那份：`level-type=minecraft:flat` 配 `generator-settings={"layers":[{"block":"minecraft:air","height":1}],"biome":"minecraft:the_void"}`。已验证：区块 (0,0) 在 y=-64、y=0、y=200 都是空气，而同样的检测在 y=-64 的基岩上会失败，`locate biome minecraft:the_void` 报的也是原点。
* **26.2 的关卡布局搬过家。** region/entity/poi 文件在 `world/dimensions/minecraft/<维度>/`，世界级状态在 `world/data/minecraft/*.dat`（游戏规则、天气、计分板、世界时钟），玩家数据在 `world/players/` 而不是 `world/playerdata/`。`--world` 会跳过 `session.lock`、`playerdata/`、`players/`、`stats/`、`advancements/`、`logs/`；注意 [protocol-snapshot.md](protocol-snapshot.md) 里 `mc_fork` 的拷贝清单描述的仍是 26.2 之前的形状，应该对着真实的 26.2 存档再核对一遍。
