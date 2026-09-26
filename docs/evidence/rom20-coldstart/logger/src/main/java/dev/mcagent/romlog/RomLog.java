package dev.mcagent.romlog;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.vehicle.minecart.AbstractMinecartContainer;
import net.minecraft.world.item.ItemStack;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Agent-owned observation logger for the Minecart ROM experiment.
 *
 * <p>This is deliberately independent of the evaluator's audit mod: it writes
 * its own JSONL file under {@code <auditDir>/romlogger/romlog.jsonl}, records a
 * {@code logger_armed} event when the server starts, and captures each chest
 * minecart that leaves the stack, including the ordered inventory read before
 * the cart is destroyed. It never mutates the world.
 *
 * <p>Capture timing: the first end-of-tick sample that sees the cart outside
 * the stack region (block x &gt;= 16) only arms the capture; the cart is
 * recorded on the next tick, so the capture is strictly after the audit's
 * cart_exit event and still far before removal. The
 * {@code AbstractMinecartContainer.remove} mixin is a fallback for a cart that
 * disappears before the sampler runs.
 */
public final class RomLog {
    public static final String MOD_ID = "rom20-agent-logger";
    public static final String BUILD = "romlog-20260926-2";

    private static final DateTimeFormatter AT =
            DateTimeFormatter.ofPattern("uuuu-MM-dd'T'HH:mm:ss.SSS'Z'").withZone(ZoneOffset.UTC);
    private static final AtomicLong SEQ = new AtomicLong();
    private static final Object LOCK = new Object();
    private static final Set<String> CAPTURED = ConcurrentHashMap.newKeySet();
    private static final Map<UUID, AbstractMinecartContainer> TRACKED = new ConcurrentHashMap<>();
    private static final Map<UUID, Integer> PENDING_EXIT = new ConcurrentHashMap<>();

    private static volatile Path file;
    private static volatile String instance = "unknown";
    private static volatile String runId = "unknown";
    private static volatile String dimension = "minecraft:overworld";
    private static volatile MinecraftServer server;
    private static volatile boolean armed = false;

    private RomLog() {
    }

    public static synchronized void arm(MinecraftServer started) {
        server = started;
        String auditDir = System.getProperty("mcagent.auditDir", "audit");
        instance = resolveInstance(auditDir);
        runId = resolveRunId(auditDir, System.getProperty("romlog.runId", "rom20-20260926T063100Z"));
        dimension = System.getProperty("romlog.dimension", "minecraft:overworld");
        String configured = System.getProperty("romlog.dir", "");
        Path directory;
        if (configured == null || configured.isBlank()) {
            directory = Path.of(auditDir, "romlogger");
        } else {
            directory = Path.of(configured);
        }
        file = directory.toAbsolutePath().resolve("romlog.jsonl");
        try {
            Files.createDirectories(file.getParent());
        } catch (IOException error) {
            System.err.println("[romlog] cannot create " + file.getParent() + ": " + error);
            return;
        }
        SEQ.set(lastSeq(file));
        CAPTURED.clear();
        TRACKED.clear();
        PENDING_EXIT.clear();
        armed = true;
        JsonObject record = new JsonObject();
        record.addProperty("event", "logger_armed");
        record.addProperty("at", AT.format(Instant.now()));
        record.addProperty("wall", System.currentTimeMillis());
        record.addProperty("tick", started.getTickCount());
        record.addProperty("instance", instance);
        record.addProperty("dimension", dimension);
        record.addProperty("run_id", runId);
        record.addProperty("mod", MOD_ID);
        record.addProperty("build", BUILD);
        record.addProperty("levelName", started.getWorldData().getLevelName());
        record.addProperty("file", file.toString());
        append(record);
        System.out.println("[romlog] armed build=" + BUILD + " instance=" + instance
                + " tick=" + started.getTickCount() + " file=" + file);
    }

    /** Keep the logger's instance/run ids aligned with the evaluator's own config. */
    private static String resolveInstance(String auditDir) {
        JsonObject config = readAuditConfig(auditDir);
        if (config != null && config.has("instanceId")) {
            return config.get("instanceId").getAsString();
        }
        return System.getProperty("romlog.instance", "experiment");
    }

    private static String resolveRunId(String auditDir, String fallback) {
        JsonObject config = readAuditConfig(auditDir);
        if (config != null && config.has("runId")) {
            return config.get("runId").getAsString();
        }
        return fallback;
    }

    /** Continue the file's sequence across server restarts instead of restarting at 1. */
    private static long lastSeq(Path target) {
        try {
            if (!Files.isRegularFile(target)) {
                return 0;
            }
            long last = 0;
            for (String line : Files.readAllLines(target, StandardCharsets.UTF_8)) {
                if (line.isBlank()) {
                    continue;
                }
                try {
                    JsonObject record = JsonParser.parseString(line).getAsJsonObject();
                    if (record.has("seq")) {
                        last = Math.max(last, record.get("seq").getAsLong());
                    }
                } catch (Exception ignored) {
                    // A malformed line must not stop the logger from starting.
                }
            }
            return last;
        } catch (IOException error) {
            System.err.println("[romlog] cannot read existing " + target + ": " + error);
            return 0;
        }
    }

    private static JsonObject readAuditConfig(String auditDir) {
        try {
            Path config = Path.of(auditDir).resolveSibling("mc-audit").resolve("config.json");
            if (Files.isRegularFile(config)) {
                return JsonParser.parseString(Files.readString(config, StandardCharsets.UTF_8))
                        .getAsJsonObject();
            }
        } catch (Exception error) {
            System.err.println("[romlog] cannot read mc-audit config: " + error);
        }
        return null;
    }

    public static void flush(String why) {
        if (!armed) {
            return;
        }
        JsonObject record = new JsonObject();
        record.addProperty("event", "logger_flushed");
        record.addProperty("at", AT.format(Instant.now()));
        record.addProperty("wall", System.currentTimeMillis());
        record.addProperty("tick", server == null ? -1 : server.getTickCount());
        record.addProperty("instance", instance);
        record.addProperty("dimension", dimension);
        record.addProperty("run_id", runId);
        record.addProperty("why", why);
        append(record);
    }

    /** Fabric ENTITY_LOAD: remember container minecarts in the overworld. */
    public static void track(Entity entity, ServerLevel world) {
        if (!armed || !(entity instanceof AbstractMinecartContainer cart)) {
            return;
        }
        String level = world.dimension().identifier().toString();
        if (!dimension.equals(level)) {
            return;
        }
        TRACKED.put(entity.getUUID(), cart);
    }

    /** Fabric END_SERVER_TICK: capture a cart one tick after it leaves the stack region. */
    public static void tick() {
        if (!armed || TRACKED.isEmpty()) {
            return;
        }
        for (Map.Entry<UUID, AbstractMinecartContainer> entry : TRACKED.entrySet()) {
            AbstractMinecartContainer cart = entry.getValue();
            if (cart == null || cart.isRemoved()) {
                continue;
            }
            // The audit's stack region ends at block x=15; a cart at x >= 16 has
            // left it. Capture one tick later so the capture is strictly after
            // the evaluator's cart_exit event without racing it in the same tick.
            if (cart.getX() >= 16.0d) {
                Integer seen = PENDING_EXIT.putIfAbsent(entry.getKey(), currentTick());
                if (seen != null && currentTick() > seen) {
                    capture(cart, "left_stack_region", null);
                }
            }
        }
    }

    private static int currentTick() {
        MinecraftServer current = server;
        return current == null ? -1 : current.getTickCount();
    }

    /**
     * Capture one cart and its ordered inventory. Safe to call from
     * {@code AbstractMinecartContainer.remove} HEAD (contents still intact) and
     * from the end-of-tick sampler; the first call per UUID wins.
     */
    public static void capture(AbstractMinecartContainer cart, String via, String removalReason) {
        if (!armed) {
            return;
        }
        String uuid = cart.getUUID().toString();
        if (!CAPTURED.add(uuid)) {
            return;
        }
        JsonObject record = new JsonObject();
        record.addProperty("event", "cart_observed");
        record.addProperty("at", AT.format(Instant.now()));
        record.addProperty("wall", System.currentTimeMillis());
        record.addProperty("tick", currentTick());
        record.addProperty("instance", instance);
        record.addProperty("dimension", cart.level().dimension().identifier().toString());
        record.addProperty("run_id", runId);
        record.addProperty("uuid", uuid);
        record.addProperty("cartType", BuiltInRegistries.ENTITY_TYPE.getKey(cart.getType()).toString());
        record.addProperty("via", via);
        if (removalReason != null) {
            record.addProperty("removalReason", removalReason);
        }
        record.addProperty("x", cart.getX());
        record.addProperty("y", cart.getY());
        record.addProperty("z", cart.getZ());
        record.addProperty("motionX", cart.getDeltaMovement().x);
        record.addProperty("motionY", cart.getDeltaMovement().y);
        record.addProperty("motionZ", cart.getDeltaMovement().z);

        JsonArray items = new JsonArray();
        for (int slot = 0; slot < cart.getContainerSize(); slot++) {
            ItemStack stack = cart.getItem(slot);
            if (stack == null || stack.isEmpty()) {
                continue;
            }
            JsonObject item = new JsonObject();
            item.addProperty("slot", slot);
            item.addProperty("id", BuiltInRegistries.ITEM.getKey(stack.getItem()).toString());
            item.addProperty("count", stack.getCount());
            items.add(item);
        }
        record.add("items", items);
        append(record);
        TRACKED.remove(cart.getUUID());
        PENDING_EXIT.remove(cart.getUUID());
    }

    private static void append(JsonObject record) {
        Path target = file;
        if (target == null) {
            return;
        }
        record.addProperty("seq", SEQ.incrementAndGet());
        String line = record + "\n";
        synchronized (LOCK) {
            try {
                Files.writeString(
                        target,
                        line,
                        StandardCharsets.UTF_8,
                        StandardOpenOption.CREATE,
                        StandardOpenOption.APPEND,
                        StandardOpenOption.WRITE);
            } catch (IOException error) {
                System.err.println("[romlog] cannot append " + target + ": " + error);
            }
        }
    }
}
