package dev.mcagent.smoke;

import com.mojang.brigadier.arguments.StringArgumentType;
import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.command.v2.CommandRegistrationCallback;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.Commands;
import net.minecraft.network.chat.Component;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.phys.Vec3;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.time.Instant;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.atomic.AtomicLong;

/**
 * A deliberately generic observation smoke mod: it proves that a self-built jar
 * was deployed, loaded at server start, and can leave readable evidence. It has
 * no ROM, minecart, or any other use-case logic - the point is the deployment
 * path, not what is being observed.
 *
 * <p>It reads the lab identity the server was started with (system properties
 * {@code mcagent.auditDir}, {@code mcagent.labName}, {@code mcagent.labInstance}
 * and {@code mcagent.worldDir}), logs a line at load time, writes {@code
 * start.json} plus a {@code smoke.log} line when the server is ready, and adds
 * two console commands:
 *
 * <pre>
 *   mcagent-smoke status                 - build, lab and tick, as console text
 *   mcagent-smoke sample &lt;label&gt;         - write audit/sample-&lt;label&gt;.json
 * </pre>
 *
 * <p>The {@link #BUILD} constant is the marker the deployment test changes
 * between builds; logging and every sample carry it, so a log can never claim
 * one build while the running code is another.
 */
public final class SmokeMod implements ModInitializer {
    public static final String MOD_ID = "mc-agent-lab-smoke";
    /** Build marker. The lab deployment test edits this string between builds. */
    public static final String BUILD = "smoke-dev";

    private static final AtomicLong SAMPLE_SEQUENCE = new AtomicLong();
    private static volatile MinecraftServer server;
    private static volatile int lastTick;
    private static volatile Path auditDir = Path.of("audit").toAbsolutePath();
    private static volatile String lab = "unknown";
    private static volatile String instance = "unknown";
    private static volatile String version = "unknown";

    @Override
    public void onInitialize() {
        auditDir = Path.of(System.getProperty("mcagent.auditDir", "audit")).toAbsolutePath();
        lab = System.getProperty("mcagent.labName", "unknown");
        instance = System.getProperty("mcagent.labInstance", "unknown");
        String worldDir = System.getProperty("mcagent.worldDir", "unknown");
        version = FabricLoader.getInstance()
                .getModContainer(MOD_ID)
                .map(container -> container.getMetadata().getVersion().getFriendlyString())
                .orElse("unknown");

        System.out.println("[mc-agent-smoke] loaded build=" + BUILD + " version=" + version
                + " lab=" + lab + " instance=" + instance
                + " worldDir=" + worldDir + " audit=" + auditDir);

        ServerLifecycleEvents.SERVER_STARTED.register(started -> {
            server = started;
            writeFile("start.json", startJson(started));
            appendLine("smoke.log", "started build=" + BUILD + " version=" + version
                    + " lab=" + lab + " instance=" + instance + " tick=" + started.getTickCount());
        });
        ServerLifecycleEvents.SERVER_STOPPING.register(stopping -> {
            appendLine("smoke.log", "stopping build=" + BUILD + " tick=" + stopping.getTickCount());
            server = null;
        });
        ServerTickEvents.END_SERVER_TICK.register(ticking -> lastTick = ticking.getTickCount());

        CommandRegistrationCallback.EVENT.register((dispatcher, registryAccess, environment) ->
                dispatcher.register(Commands.literal("mcagent-smoke")
                        .then(Commands.literal("status").executes(context -> status(context.getSource())))
                        .then(Commands.literal("sample")
                                .then(Commands.argument("label", StringArgumentType.word())
                                        .executes(context -> sample(
                                                context.getSource(),
                                                StringArgumentType.getString(context, "label")))))));
    }

    private static int status(CommandSourceStack source) {
        String line = "[mc-agent-smoke] build=" + BUILD + " version=" + version
                + " lab=" + lab + " instance=" + instance + " tick=" + sampleTick()
                + " audit=" + auditDir;
        source.sendSuccess(() -> Component.literal(line), false);
        return 1;
    }

    private static int sample(CommandSourceStack source, String label) {
        MinecraftServer current = server;
        int tick = sampleTick();
        List<ServerPlayer> players = current == null ? List.of() : current.getPlayerList().getPlayers();
        Vec3 position = source.getPosition();
        String dimension = source.getLevel().dimension().identifier().toString();
        long sequence = SAMPLE_SEQUENCE.incrementAndGet();
        String name = "sample-" + label + ".json";
        writeFile(name, sampleJson(label, sequence, tick, source.getTextName(), dimension, position, players));
        Path written = auditDir.resolve(name);
        source.sendSuccess(() -> Component.literal("[mc-agent-smoke] wrote " + written
                + " build=" + BUILD + " tick=" + tick), false);
        return 1;
    }

    private static int sampleTick() {
        MinecraftServer current = server;
        return current == null ? lastTick : current.getTickCount();
    }

    private static String startJson(MinecraftServer started) {
        String worldDir = System.getProperty("mcagent.worldDir", "unknown");
        return "{\n"
                + "  \"mod\": \"" + json(MOD_ID) + "\",\n"
                + "  \"build\": \"" + json(BUILD) + "\",\n"
                + "  \"version\": \"" + json(version) + "\",\n"
                + "  \"lab\": \"" + json(lab) + "\",\n"
                + "  \"instance\": \"" + json(instance) + "\",\n"
                + "  \"worldDirProperty\": \"" + json(worldDir) + "\",\n"
                + "  \"levelName\": \"" + json(started.getWorldData().getLevelName()) + "\",\n"
                + "  \"tick\": " + started.getTickCount() + ",\n"
                + "  \"startedAt\": \"" + json(Instant.now().toString()) + "\"\n"
                + "}\n";
    }

    private static String sampleJson(
            String label,
            long sequence,
            int tick,
            String sourceName,
            String dimension,
            Vec3 position,
            List<ServerPlayer> players) {
        StringBuilder json = new StringBuilder();
        json.append("{\n");
        json.append("  \"mod\": \"").append(json(MOD_ID)).append("\",\n");
        json.append("  \"build\": \"").append(json(BUILD)).append("\",\n");
        json.append("  \"version\": \"").append(json(version)).append("\",\n");
        json.append("  \"lab\": \"").append(json(lab)).append("\",\n");
        json.append("  \"instance\": \"").append(json(instance)).append("\",\n");
        json.append("  \"label\": \"").append(json(label)).append("\",\n");
        json.append("  \"sequence\": ").append(sequence).append(",\n");
        json.append("  \"tick\": ").append(tick).append(",\n");
        json.append("  \"sampledAt\": \"").append(json(Instant.now().toString())).append("\",\n");
        json.append("  \"source\": {\"name\": \"").append(json(sourceName))
                .append("\", \"dimension\": \"").append(json(dimension))
                .append("\", \"x\": ").append(fmt(position.x))
                .append(", \"y\": ").append(fmt(position.y))
                .append(", \"z\": ").append(fmt(position.z)).append("},\n");
        json.append("  \"players\": [");
        for (int index = 0; index < players.size(); index++) {
            ServerPlayer player = players.get(index);
            Vec3 at = player.position();
            if (index > 0) {
                json.append(", ");
            }
            json.append("{\"name\": \"").append(json(player.getName().getString()))
                    .append("\", \"dimension\": \"").append(json(player.level().dimension().identifier().toString()))
                    .append("\", \"x\": ").append(fmt(at.x))
                    .append(", \"y\": ").append(fmt(at.y))
                    .append(", \"z\": ").append(fmt(at.z)).append("}");
        }
        json.append("]\n");
        json.append("}\n");
        return json.toString();
    }

    private static void writeFile(String name, String text) {
        try {
            Files.createDirectories(auditDir);
            Files.writeString(
                    auditDir.resolve(name),
                    text,
                    StandardCharsets.UTF_8,
                    StandardOpenOption.CREATE,
                    StandardOpenOption.TRUNCATE_EXISTING,
                    StandardOpenOption.WRITE);
        } catch (IOException exception) {
            System.err.println("[mc-agent-smoke] cannot write " + auditDir.resolve(name) + ": " + exception);
        }
    }

    private static void appendLine(String name, String line) {
        try {
            Files.createDirectories(auditDir);
            Files.writeString(
                    auditDir.resolve(name),
                    line + System.lineSeparator(),
                    StandardCharsets.UTF_8,
                    StandardOpenOption.CREATE,
                    StandardOpenOption.APPEND,
                    StandardOpenOption.WRITE);
        } catch (IOException exception) {
            System.err.println("[mc-agent-smoke] cannot append " + auditDir.resolve(name) + ": " + exception);
        }
    }

    private static String fmt(double value) {
        return String.format(Locale.ROOT, "%.3f", value);
    }

    private static String json(String value) {
        StringBuilder escaped = new StringBuilder();
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            switch (character) {
                case '\\' -> escaped.append("\\\\");
                case '"' -> escaped.append("\\\"");
                case '\n' -> escaped.append("\\n");
                case '\r' -> escaped.append("\\r");
                case '\t' -> escaped.append("\\t");
                default -> {
                    if (character < 0x20) {
                        escaped.append(String.format("\\u%04x", (int) character));
                    } else {
                        escaped.append(character);
                    }
                }
            }
        }
        return escaped.toString();
    }
}
