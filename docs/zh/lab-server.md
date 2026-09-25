# 智能体自己拉起的实验室服务器

`tools/lab_server.py` 为每个实验拉起一台用完即弃的无头 Fabric 服务器——一个**实验室**——并给智能体一个进得去的控制台。没有 GUI、没有启动器、没有要点来点去的窗口，也不和玩家的活客户端共享任何东西：供给一个目录、后台启动、通过 RCON 敲命令。

```powershell
python tools/lab_server.py provision --name smoke --void --fabric-api --carpet `
    --server-port 27170 --rcon-port 27171 --vantage-port 27172 --bridge-port 27173
python tools/lab_server.py start --name smoke --wait 300     # 后台运行；日志写 labs/smoke/logs/console.log
python tools/lab_server.py exec --name smoke "summon minecraft:tnt 0 100 0 {}"
python tools/lab_server.py exec --name smoke "tick freeze"
python tools/lab_server.py status --name smoke --tail 20
python tools/lab_server.py identity --name smoke --json       # 世界/审计目录、端口、实例 id
python tools/lab_server.py verify --name smoke --require-vantage   # 重新哈希 mod，校验 port.txt
python tools/lab_server.py stop --name smoke
python tools/lab_server.py list
```

`provision` 接受 `--void`（虚空生物群系的纯空气超平坦世界）或 `--world DIR`（把已有存档拷进来），以及 `--fabric-api`、`--carpet`、`--mod-jar PATH`、`--mod-url URL`（每次 provision 重新拉取）、`--test-mod PATH`（安装并标记为必需）、`--require-mod NAME`、`--forget-mod NAME`（显式移除一条必需项）、`--mc VERSION`、`--memory 2G`、`--java PATH`、`--jdk PATH`（用来构建 mod 的 JDK；会被记住并哈希）和四个身份端口 `--server-port`、`--rcon-port`、`--vantage-port`、`--bridge-port`（传 `0` 表示自动挑一个空闲的）。`--server-dir`、`--audit-dir` 覆盖 server-vantage mod 与游戏侧审计输出的位置。Java 依次取 `--java`、`$MC_AGENT_JAVA`、PATH 上的 `java`；provision 解析到的结果会被记住，之后 `start` 不必重复指定。`start --wait N` 会在日志出现 `Done` 时立刻返回。

```
labs/<lab>/       fabric-server-mc.*-launcher.*.jar、server.properties、
                  eula.txt、rcon.json（host/port/password）、lab.json、
                  identity.json、run.json、mods/、world/、audit/、
                  mc-agent-server/port.txt（每次启动由 mod 重写）、
                  logs/console.log、.fabric/server/（服务端 jar）
labs/_cache/      启动器 jar + mod jar，所有实验室共用一份下载
```

`exec` 自己实现了 RCON（length/id/type 头，payload 加两个 NUL，type 3 登录、type 2 执行，回答可能跨包），并把控制台回答的内容打出来——那就是命令的反馈，所以 `list`、`tick freeze` 和报错都会以文本回来。有几个怪癖是**游戏**的，不是工具的：`save-all flush` 会以单个包到达、两条消息粘在一起；`stop` 会在回复完成前就关掉 socket。

## 一个实验一个实验室：身份、目录与端口

实验室不只是一个目录，而是一个有身份的命名实例。`provision` 在首次启动前就解析并记录每个端点，并把同一份记录写进 `labs/<lab>/identity.json`，编排者不必猜哪个世界、哪个端口属于哪个实验：

| 字段 | 含义 |
| --- | --- |
| `instanceId` | 随机生成、随实验室存活；重新 provision 也保持不变 |
| `worldDir` | 关卡目录（`labs/<lab>/world`） |
| `auditDir` | 游戏侧 mod 写样本的地方（`labs/<lab>/audit`） |
| `serverDir` / `portFile` | server-vantage mod 写 `port.txt` 的位置 |
| `serverPort` / `rconPort` | 游戏与 RCON 端口 |
| `serverVantagePort` | server-vantage mod 绑定的基础端口（向上找 20 个） |
| `bridgeApiPort` | 该实验室的 bridge loopback API 端口 |

两个实验室可以同时运行且不共享任何东西：

```powershell
python tools/lab_server.py provision --name lab-a --void --fabric-api --carpet `
    --server-port 27170 --rcon-port 27171 --vantage-port 27172 --bridge-port 27173
python tools/lab_server.py provision --name lab-b --void --fabric-api --carpet `
    --server-port 27174 --rcon-port 27175 --vantage-port 27176 --bridge-port 27177
python tools/lab_server.py start --name lab-a --wait 300
python tools/lab_server.py start --name lab-b --wait 300
python tools/lab_server.py list
python tools/lab_server.py identity --name lab-a --json

# 每个实验室一个 bridge：只读它自己的 port.txt，服务它自己的 API 端口
.venv/Scripts/mc-bridge run --server-dir labs/lab-a --api-port 27173
.venv/Scripts/mc-bridge --api-port 27173 call status
```

`list` 会把每个实验室的 RCON、VANTAGE、BRIDGE 端口并排显示。`start` 在拉起进程前先清掉陈旧的 `port.txt`，mod 一绑定就重写它，所以跟着文件走的 bridge 总会落到正在运行的服务端上——重启也一样，即使 lab-a/lab-b 同时在跑。`provision` 拒绝复用已记录给别的实验室的端口。

`identity` 以文本或 `--json` 打印这份记录；`verify --require-vantage` 重新哈希已部署 jar，并在要求时校验 `port.txt` 仍指向记录的 server-vantage 端口。两者都只读，适合在测量实验前对着运行中的实验室跑一遍。

## 部署 jar：看内容哈希，不看大小

`provision` 判断已安装 jar 是否需要替换时比较的是 **SHA-256**，不是字节大小。重建后大小恰好相同的 mod 会被替换而不是跳过（旧的大小比较可能让运行中的服务端继续跑旧代码，而实验室记录却写着新构建）。拷贝先落到临时文件名再原子改名到位，拷完还会重新哈希目标文件；不一致会明确报错。

`mods/` 里的每个 jar 都会记进 `lab.json`，带文件名、SHA-256、大小、来源，以及已知时的版本。`run.json` 在启动时再次记录同一批哈希（`modsAtStart`），所以任何一次运行都能追溯到磁盘上确切的字节。

```powershell
# 构建一个新 logger，停止后部署，再启动
python tools/build_mod.py --source my-mod --lab lab-a --out labs/build/logger.jar --version 1.1
python tools/lab_server.py stop --name lab-a
python tools/lab_server.py provision --name lab-a --mod-jar labs/build/logger.jar
#   mods: logger.jar (replaced, 4b3c...9f)   <- 即使大小完全一样
python tools/lab_server.py start --name lab-a --wait 300
```

工具强制执行的规则：

* **不支持热加载。** Fabric 只在服务端启动时加载 mod。`provision` 拒绝对运行中的实验室部署 mod；先停掉。只改属性文件可以在运行中做，并会标记 `restartPending` 直到下次启动。
* **必需的 mod 不可能悄悄消失。** `--test-mod PATH` 安装并标记为必需；`--require-mod NAME` 只标记。缺任何一个必需 jar 时 `start` 都以非零退出并列出名字，而不会把一个无法再审计的实例跑起来。必需项在 jar 缺席时也能挺过后续的 `provision`；只有 `--forget-mod NAME` 才会显式移除。
* **漂移可见。** 如果已部署 jar 的字节与 `lab.json` 记录不符，`start` 会列出两个哈希并拒绝启动。`--allow-mod-drift` 可以照常启动，真实字节会以 `drift: true` 记进 `run.json`。
* **未记录的 jar 不会被默默信任。** provision 之后手工拷进 `mods/` 的 jar 仍会被 Fabric 加载，所以 `start` 和 `verify` 会把它列为问题并报出文件名。`--allow-unrecorded-mod` 可以照常启动，`run.json` 仍保留该 jar 的 `recorded: false`。
* **会变化的 URL 每次重拉。** `--mod-url` 在每次 provision 时重新下载，所以同名 URL 背后的新字节会按哈希比较并以 `replaced` 部署，而不是被缓存当作 `unchanged`。离线供给请用 `--mod-jar` 配本地文件（或预先填好 `labs/_cache/mods`）。

Fabric 仍在启动时加载 mod，所以实验的安全顺序是：**停止时部署 -> 启动 -> 恢复原始内存实体并校验顺序 -> 做实验。** 不要相信存档重载产生的实体顺序；那是恢复前置项要解决的事（[分叉一个活的世界](protocol-snapshot.md)）。

## 为实验室构建 mod

Minecraft 26.2 的类文件没有混淆，可以直接对着实验室已经下载好的 jar 编译——不需要 Gradle，也不需要额外下载。`tools/build_mod.py` 用 javac 编译源码树，写出确定性的 jar 和一份 `<out>.build.json` 旁文件，记录 JDK、classpath 与源码哈希：

```powershell
python tools/build_mod.py --source examples/smoke-mod --lab lab-a `
    --out labs/build/smoke-mod.jar --version 0.1.0 --compression store
```

完整流程、classpath 来源和会看到的报错在[为实验室构建 mod](mod-building.md)。`examples/smoke-mod/` 是一个通用观测 mod（加载日志、`start.json`、`mcagent-smoke sample`），用来验证构建、部署、重启和结果读取；它不含任何用例逻辑。

## 26.2 的无头服务器会逼你做的事

* **启动器其实是个安装器。** `meta.fabricmc.net/.../server/jar` 背后那个 jar 带着 `install.properties`（`game-version=26.2`），会把 vanilla 服务端下载到 `.fabric/server/26.2-server.jar`，把它的 bundle 解到 `versions/26.2/server-26.2.jar`，并安装依赖库。所以第一次 `start` 约 40 秒、约 140 MB；`labs/_cache/` 只帮你省掉启动器和 mod。
* **要 Java 25，不是 21。** 服务端自己的 mod 列表里写着 `java 25` 配 `minecraft 26.2`，用更老的 JVM 会在世界加载前就死掉：`UnsupportedClassVersionError: net/minecraft/bundler/Main has been compiled by a more recent version of the Java Runtime (class file version 69.0), this version of the Java Runtime only recognizes class file versions up to 65.0`。PATH 上是 Java 21 的机器必须传 `--java`（或设 `MC_AGENT_JAVA`）。
* **没人登录，世界可能不 tick。** 26.2 有 `pause-when-empty-seconds`（默认 60）；工具把它设成 `0`，否则一个只通过 RCON 说话的实验室会把自己暂停，"跑 N tick"的实验会悄悄什么都不做。
* **什么都没加载。** 没有玩家的实验室没有区块被加载，所以在你 `forceload add 0 0` 之前，`if block` / `data get block` 会回 *That position is not loaded*。虚空实验室的检查要先做这一步。
* **时间现在是一条时间轴。** `time query daytime` 没有了；26.2 对 `time query day` 回 `Timeline minecraft:day is at 362 tick(s)`，对 `time set day` 回 `Set minecraft:overworld to time marker minecraft:day`。
* **server.properties 会在首次启动时被重写**：`:` 会被转义（`level-type=minecraft\:flat`），默认项被追加，26.2 还会加一整块 `management-server-*`——第二个默认关闭的管理监听器，自带生成的密钥，和 RCON 无关。
* **能用的虚空写法**就是工具写下去的那份：`level-type=minecraft:flat` 配 `generator-settings={"layers":[{"block":"minecraft:air","height":1}],"biome":"minecraft:the_void"}`。已验证：区块 (0,0) 在 y=-64、y=0、y=200 都是空气，而同样的检测在 y=-64 的基岩上会失败，`locate biome minecraft:the_void` 报的也是原点。
* **26.2 的关卡布局搬过家。** region/entity/poi 文件在 `world/dimensions/minecraft/<维度>/`，世界级状态在 `world/data/minecraft/*.dat`（游戏规则、天气、计分板、世界时钟），玩家数据在 `world/players/` 而不是 `world/playerdata/`。`--world` 会跳过 `session.lock`、`playerdata/`、`players/`、`stats/`、`advancements/`、`logs/`；注意 [protocol-snapshot.md](protocol-snapshot.md) 里 `mc_fork` 的拷贝清单描述的仍是 26.2 之前的形状，应该对着真实的 26.2 存档再核对一遍。
