package dev.mcagent.rom21;

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
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.security.MessageDigest;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.Map;
import java.util.Properties;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Agent-owned observation logger for the Minecart ROM regression (issue #21).
 *
 * <p>Derived from the frozen issue-#20 logger
 * ({@code docs/evidence/rom20-coldstart/logger}; sha256 provenance in
 * {@code examples/minecart-rom/regression/logger/PROVENANCE.md}).  The
 * regression keeps the original behaviour - a transient capture one
 * configured delay after the cart leaves the stack region, with the ordered
 * live inventory read before destruction - and adds:
 *
 * <ul>
 *   <li>config-driven scene: the pop plane axis/threshold, dimension, output
 *       directory, run id, instance id and expected cart count all come from
 *       system properties or the evaluator's {@code mc-audit/config.json};</li>
 *   <li>a natural void capture ({@code cart_void_capture}) written at the head
 *       of the cart's own {@code remove} override, with the live inventory and
 *       the real {@code RemovalReason}; the transient capture is labelled
 *       {@code cart_observed} with {@code via=left_stack_region};</li>
 *   <li>an inventory digest over the slots actually read, so a capture can be
 *       compared with its removal record without trusting order;</li>
 *   <li>a build string substituted at build time from the bundled
 *       {@code romlog.properties} ({@code ${version}}), which lets the
 *       same-size iteration proof build two jars that differ in bytes while
 *       keeping the same size.</li>
 * </ul>
 *
 * <p>The logger never mutates the world and never reads the evaluator's audit
 * log; it writes its own JSONL file separately.
 */
public final class RomLog {
    public static final String MOD_ID = "rom21-regression-logger";

    private static final DateTimeFormatter AT =
            DateTimeFormatter.ofPattern("uuuu-MM-dd'T'HH:mm:ss.SSS'Z'").withZone(ZoneOffset.UTC);
    private static final AtomicLong SEQ = new AtomicLong();
    private static final Object LOCK = new Object();
    private static final Map<UUID, AbstractMinecartContainer> TRACKED = new ConcurrentHashMap<>();
    private static final Map<UUID, Integer> OUTSIDE_SINCE = new ConcurrentHashMap<>();
    private static final Map<UUID, Integer> OBSERVED = new ConcurrentHashMap<>();
    private static final Set<UUID> VOID_CAPTURED = ConcurrentHashMap.newKeySet();

    private static volatile Path file;
    private static volatile String instance = "unknown";
    private static volatile String runId = "unknown";
    private static volatile String dimension = "minecraft:overworld";
    private static volatile String exitAxis = "x";
    private static volatile double exitGreaterThan = 15.0d;
    private static volatile int captureDelayTicks = 1;
    private static volatile int expectedCarts = 0;
    private static volatile String build = "romlog-rom21-dev";
    private static volatile MinecraftServer server;
    private static volatile boolean armed = false;

    private RomLog() {
    }

    public static synchronized void arm(MinecraftServer started) {
        server = started;
        loadBuild();
        String auditDir = System.getProperty("mcagent.auditDir", "audit");
        instance = resolveInstance(auditDir);
        runId = resolveRunId(auditDir, System.getProperty("romlog.runId", "rom21-regression"));
        dimension = System.getProperty("romlog.dimension", "minecraft:overworld");
        exitAxis = normalizeAxis(System.getProperty("romlog.exitAxis", "x"));
        exitGreaterThan = parseDouble(System.getProperty("romlog.exitGreaterThan"), 15.0d);
        captureDelayTicks = Math.max(0, parseInt(System.getProperty("romlog.captureDelayTicks"), 1));
        expectedCarts = Math.max(0, parseInt(System.getProperty("romlog.expectedCarts"), 0));
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
        TRACKED.clear();
        OUTSIDE_SINCE.clear();
        OBSERVED.clear();
        VOID_CAPTURED.clear();
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
        record.addProperty("build", build);
        record.addProperty("levelName", started.getWorldData().getLevelName());
        record.addProperty("file", file.toString());
        record.addProperty("exitAxis", exitAxis);
        record.addProperty("exitGreaterThan", exitGreaterThan);
        record.addProperty("captureDelayTicks", captureDelayTicks);
        record.addProperty("expectedCarts", expectedCarts);
        append(record);
        System.out.println("[romlog] armed build=" + build + " instance=" + instance
                + " axis=" + exitAxis + ">" + exitGreaterThan + " delay=" + captureDelayTicks
                + " tick=" + started.getTickCount() + " file=" + file);
    }

    private static void loadBuild() {
        try (InputStream stream = RomLog.class.getResourceAsStream("/romlog.properties")) {
            if (stream == null) {
                return;
            }
            Properties properties = new Properties();
            properties.load(stream);
            String value = properties.getProperty("build", "").trim();
            if (!value.isEmpty()) {
                build = value;
            }
        } catch (IOException error) {
            System.err.println("[romlog] cannot read build properties: " + error);
        }
    }

    private static String normalizeAxis(String value) {
        String axis = value == null ? "x" : value.trim().toLowerCase();
        return switch (axis) {
            case "x", "y", "z" -> axis;
            default -> "x";
        };
    }

    private static double parseDouble(String value, double fallback) {
        if (value == null || value.isBlank()) {
            return fallback;
        }
        try {
            return Double.parseDouble(value.trim());
        } catch (NumberFormatException error) {
            return fallback;
        }
    }

    private static int parseInt(String value, int fallback) {
        if (value == null || value.isBlank()) {
            return fallback;
        }
        try {
            return Integer.parseInt(value.trim());
        } catch (NumberFormatException error) {
            return fallback;
        }
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
        record.addProperty("build", build);
        record.addProperty("why", why);
        record.addProperty("observed", OBSERVED.size());
        record.addProperty("voidCaptured", VOID_CAPTURED.size());
        record.addProperty("expectedCarts", expectedCarts);
        append(record);
    }

    /** Fabric ENTITY_LOAD: remember container minecarts in the configured dimension. */
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

    private static double axisValue(AbstractMinecartContainer cart) {
        return switch (exitAxis) {
            case "y" -> cart.getY();
            case "z" -> cart.getZ();
            default -> cart.getX();
        };
    }

    /** Fabric END_SERVER_TICK: transient capture after the cart leaves the stack region. */
    public static void tick() {
        if (!armed || TRACKED.isEmpty()) {
            return;
        }
        int now = currentTick();
        for (Map.Entry<UUID, AbstractMinecartContainer> entry : TRACKED.entrySet()) {
            AbstractMinecartContainer cart = entry.getValue();
            if (cart == null || cart.isRemoved() || OBSERVED.containsKey(entry.getKey())) {
                continue;
            }
            if (axisValue(cart) > exitGreaterThan) {
                Integer seen = OUTSIDE_SINCE.putIfAbsent(entry.getKey(), now);
                int since = seen == null ? now : seen;
                if (now - since >= captureDelayTicks) {
                    captureObserved(cart, "left_stack_region");
                }
            }
        }
    }

    private static int currentTick() {
        MinecraftServer current = server;
        return current == null ? -1 : current.getTickCount();
    }

    /**
     * Transient capture with the ordered live inventory; first capture per
     * UUID per server session wins.  The cart stays tracked for the natural
     * void capture.
     */
    private static void captureObserved(AbstractMinecartContainer cart, String via) {
        UUID uuid = cart.getUUID();
        if (OBSERVED.putIfAbsent(uuid, currentTick()) != null) {
            return;
        }
        JsonObject record = baseCartRecord("cart_observed", cart);
        record.addProperty("via", via);
        record.add("items", inventory(cart));
        record.addProperty("inventoryDigest", inventoryDigest(cart));
        record.addProperty("observedTick", OBSERVED.get(uuid));
        append(record);
        OUTSIDE_SINCE.remove(uuid);
    }

    /**
     * Natural capture at the head of the cart's own {@code remove} override:
     * the contents are still intact, so the live inventory and the real
     * removal reason are recorded.  Always written, even when a transient
     * capture already exists.
     */
    public static void captureVoid(AbstractMinecartContainer cart, String removalReason) {
        if (!armed) {
            return;
        }
        UUID uuid = cart.getUUID();
        JsonObject record = baseCartRecord("cart_void_capture", cart);
        record.addProperty("via", "container_remove");
        record.addProperty("removalReason", removalReason == null ? "UNKNOWN" : removalReason);
        record.add("items", inventory(cart));
        record.addProperty("inventoryDigest", inventoryDigest(cart));
        Integer observedTick = OBSERVED.get(uuid);
        if (observedTick != null) {
            record.addProperty("observedTick", observedTick);
        }
        append(record);
        VOID_CAPTURED.add(uuid);
        TRACKED.remove(uuid);
        OUTSIDE_SINCE.remove(uuid);
    }

    private static JsonObject baseCartRecord(String event, AbstractMinecartContainer cart) {
        JsonObject record = new JsonObject();
        record.addProperty("event", event);
        record.addProperty("at", AT.format(Instant.now()));
        record.addProperty("wall", System.currentTimeMillis());
        record.addProperty("tick", currentTick());
        record.addProperty("instance", instance);
        record.addProperty("dimension", cart.level().dimension().identifier().toString());
        record.addProperty("run_id", runId);
        record.addProperty("build", build);
        record.addProperty("uuid", cart.getUUID().toString());
        record.addProperty("cartType", BuiltInRegistries.ENTITY_TYPE.getKey(cart.getType()).toString());
        record.addProperty("x", cart.getX());
        record.addProperty("y", cart.getY());
        record.addProperty("z", cart.getZ());
        record.addProperty("motionX", cart.getDeltaMovement().x);
        record.addProperty("motionY", cart.getDeltaMovement().y);
        record.addProperty("motionZ", cart.getDeltaMovement().z);
        return record;
    }

    private static JsonArray inventory(AbstractMinecartContainer cart) {
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
        return items;
    }

    private static String inventoryDigest(AbstractMinecartContainer cart) {
        StringBuilder text = new StringBuilder();
        for (int slot = 0; slot < cart.getContainerSize(); slot++) {
            ItemStack stack = cart.getItem(slot);
            if (stack == null || stack.isEmpty()) {
                continue;
            }
            text.append(slot).append('|')
                .append(BuiltInRegistries.ITEM.getKey(stack.getItem()))
                .append('|').append(stack.getCount()).append(';');
        }
        return digest(text.toString());
    }

    private static String digest(String text) {
        try {
            MessageDigest sha = MessageDigest.getInstance("SHA-256");
            byte[] bytes = sha.digest(text.getBytes(StandardCharsets.UTF_8));
            StringBuilder hex = new StringBuilder();
            for (int index = 0; index < 8; index++) {
                hex.append(String.format("%02x", bytes[index]));
            }
            return hex.toString();
        } catch (Exception error) {
            return Integer.toHexString(text.hashCode());
        }
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
