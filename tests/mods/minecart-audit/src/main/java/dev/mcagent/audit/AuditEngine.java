package dev.mcagent.audit;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.entity.vehicle.minecart.AbstractMinecartContainer;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.block.NoteBlock;
import net.minecraft.world.level.block.state.BlockState;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayDeque;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.UUID;

/**
 * Server-side audit engine. Every entry point here is called from a mixin or a
 * Fabric event and is defensive: it only reads the world, it never cancels or
 * mutates anything, and a failure is recorded as an incomplete audit instead
 * of escaping into the game.
 */
public final class AuditEngine {
    private static volatile AuditEngine instance;

    public static AuditEngine get() {
        return instance;
    }

    public static void install(AuditEngine engine) {
        instance = engine;
    }

    public static void uninstall() {
        instance = null;
    }

    public final AuditConfig config;
    public final AuditLog log;
    private final Path auditDir;
    private final Map<UUID, TrackedCart> carts = new LinkedHashMap<>();
    private final Map<Long, ArrayDeque<InputRecord>> requests = new LinkedHashMap<>();
    private final Map<Long, ArrayDeque<InputRecord>> attempts = new LinkedHashMap<>();
    private final Map<String, HookStats> hooks = new TreeMap<>();
    private final Set<String> incompleteReasons = new LinkedHashSet<>();
    private final long startedNanos = System.nanoTime();
    private int serverTick;
    private String phase = "bootstrap";
    private boolean ended;

    private AuditEngine(AuditConfig config, AuditLog log, Path auditDir) {
        this.config = config;
        this.log = log;
        this.auditDir = auditDir;
    }

    private record InputRecord(
            long seq,
            int tick,
            String kind,
            String path,
            int x,
            int y,
            int z,
            String operatorUuid,
            String operatorName,
            String trigger) {
    }

    /** Load config, open the evidence file and announce readiness. */
    public static AuditEngine start(MinecraftServer server) throws IOException {
        Path gameDir = FabricLoader.getInstance().getGameDir();
        Path configPath = gameDir.resolve("mc-audit").resolve("config.json");
        String override = System.getProperty("mcaudit.config", "");
        if (!override.isBlank()) {
            configPath = Path.of(override);
        }
        if (!configPath.isAbsolute()) {
            configPath = gameDir.resolve(configPath);
        }
        AuditConfig config = AuditConfig.load(configPath);
        Path dir = config.outputDir.isBlank() ? gameDir.resolve("mc-audit") : gameDir.resolve(config.outputDir);
        AuditLog log = new AuditLog(dir, config.runId, config.instanceId, config.resolvedOutputFile(), config.maxBytes);
        AuditEngine engine = new AuditEngine(config, log, dir);
        install(engine);
        engine.ready();
        return engine;
    }

    private void ready() {
        phase = "ready";
        log.setPhase(phase);
        JsonObject session = log.event("session_start");
        session.addProperty("sessionId", log.sessionId());
        session.addProperty("logBytesBefore", log.bytesBeforeSession());
        session.addProperty("previousSeq", log.initialSeq());
        log.append(session);
        JsonObject event = log.event("audit_ready");
        event.add("config", config.evidenceJson());
        event.addProperty("minecraft", modVersion("minecraft"));
        event.addProperty("fabricLoader", modVersion("fabricloader"));
        event.addProperty("fabricApi", modVersion("fabric-api"));
        event.addProperty("auditMod", modVersion("mc-agent-minecart-audit"));
        event.addProperty("readOnly", true);
        event.addProperty("observationOnly", true);
        event.add("inputRegions", regionArray(config.inputRegions));
        event.add("stackRegion", config.stackRegion.toJson());
        event.add("outputRegion", config.outputRegion == null ? JsonNull.INSTANCE : config.outputRegion.toJson());
        event.addProperty("gameDir", FabricLoader.getInstance().getGameDir().toString());
        log.append(event);
        writeStatus(true);
    }

    private static String modVersion(String id) {
        return FabricLoader.getInstance().getModContainer(id)
                .map(container -> container.getMetadata().getVersion().getFriendlyString())
                .orElse("absent");
    }

    private static JsonArray regionArray(Iterable<Region> regions) {
        JsonArray array = new JsonArray();
        for (Region region : regions) {
            array.add(region.toJson());
        }
        return array;
    }

    // ------------------------------------------------------------------ hooks

    /** Called at RETURN of NoteBlock.useItemOn / useWithoutItem. */
    public void onNoteUse(
            String path,
            BlockState state,
            Level level,
            BlockPos pos,
            Player player,
            InteractionHand hand,
            ItemStack stack,
            InteractionResult result) {
        if (ended) {
            return;
        }
        long started = System.nanoTime();
        try {
            if (!(level instanceof ServerLevel)) {
                return;
            }
            Region region = config.inputRegionFor(pos.getX(), pos.getY(), pos.getZ());
            InputRecord request = latest(requests, pos, serverTick);
            JsonObject event = log.event("input_attempt");
            event.add("pos", JsonViews.position(pos.getX(), pos.getY(), pos.getZ()));
            event.addProperty("block", BuiltInRegistries.BLOCK.getKey(state.getBlock()).toString());
            event.addProperty("region", region == null ? null : region.name);
            event.addProperty("targetInput", region != null);
            addOperator(event, player);
            event.add("item", itemJson(stack));
            event.addProperty("path", path);
            event.addProperty("hand", hand == null ? null : hand.name());
            event.addProperty("result", resultName(result));
            event.addProperty("resultConsumesAction", result != null && result.consumesAction());
            event.addProperty("requestSeq", request == null ? null : request.seq());
            log.append(event);
            record(attempts, new InputRecord(
                    log.seq(),
                    serverTick,
                    "attempt",
                    path,
                    pos.getX(),
                    pos.getY(),
                    pos.getZ(),
                    player instanceof ServerPlayer serverPlayer ? serverPlayer.getUUID().toString() : null,
                    player instanceof ServerPlayer serverPlayer ? serverPlayer.getScoreboardName() : null,
                    "player"));
        } catch (Throwable error) {
            hookError("note_use", error);
        } finally {
            hook("note_use").record(System.nanoTime() - started);
        }
    }

    /** Called at HEAD of NoteBlock.playNote: the actual request carrying the trigger. */
    public void onPlayNote(Entity entity, BlockState state, Level level, BlockPos pos) {
        if (ended) {
            return;
        }
        long started = System.nanoTime();
        try {
            if (!(level instanceof ServerLevel)) {
                return;
            }
            Region region = config.inputRegionFor(pos.getX(), pos.getY(), pos.getZ());
            String trigger = entity == null
                    ? "redstone_or_environment"
                    : entity instanceof ServerPlayer ? "player" : "entity";
            JsonObject event = log.event("input_request");
            event.add("pos", JsonViews.position(pos.getX(), pos.getY(), pos.getZ()));
            event.addProperty("block", BuiltInRegistries.BLOCK.getKey(state.getBlock()).toString());
            event.addProperty("region", region == null ? null : region.name);
            event.addProperty("targetInput", region != null);
            event.addProperty("trigger", trigger);
            addOperator(event, entity instanceof Player player ? player : null);
            if (state.getBlock() instanceof NoteBlock) {
                event.addProperty("note", state.getValue(NoteBlock.NOTE));
                event.addProperty("instrument", state.getValue(NoteBlock.INSTRUMENT).name());
            }
            log.append(event);
            record(requests, new InputRecord(
                    log.seq(),
                    serverTick,
                    "request",
                    "playNote",
                    pos.getX(),
                    pos.getY(),
                    pos.getZ(),
                    entity instanceof ServerPlayer serverPlayer ? serverPlayer.getUUID().toString() : null,
                    entity instanceof ServerPlayer serverPlayer ? serverPlayer.getScoreboardName() : null,
                    trigger));
        } catch (Throwable error) {
            hookError("play_note", error);
        } finally {
            hook("play_note").record(System.nanoTime() - started);
        }
    }

    /** Called at RETURN of NoteBlock.triggerEvent: the server actually processed the note. */
    public void onTriggerEvent(
            BlockState state, Level level, BlockPos pos, int eventId, int eventParam, boolean played) {
        if (ended) {
            return;
        }
        long started = System.nanoTime();
        try {
            if (!(level instanceof ServerLevel)) {
                return;
            }
            Region region = config.inputRegionFor(pos.getX(), pos.getY(), pos.getZ());
            boolean target = region != null;
            InputRecord request = latest(requests, pos, serverTick);
            InputRecord attempt = latest(attempts, pos, serverTick);
            String operatorUuid = request != null ? request.operatorUuid() : null;
            JsonObject event = log.event("input_processed");
            event.add("pos", JsonViews.position(pos.getX(), pos.getY(), pos.getZ()));
            event.addProperty("block", BuiltInRegistries.BLOCK.getKey(state.getBlock()).toString());
            event.addProperty("region", region == null ? null : region.name);
            event.addProperty("targetInput", target);
            event.addProperty("eventId", eventId);
            event.addProperty("eventParam", eventParam);
            event.addProperty("played", played);
            if (state.getBlock() instanceof NoteBlock) {
                event.addProperty("note", state.getValue(NoteBlock.NOTE));
                event.addProperty("instrument", state.getValue(NoteBlock.INSTRUMENT).name());
            }
            event.addProperty("requestSeq", request == null ? null : request.seq());
            event.addProperty("requestTrigger", request == null ? null : request.trigger());
            event.addProperty("attemptSeq", attempt == null ? null : attempt.seq());
            JsonObject operator = new JsonObject();
            if (request != null && request.operatorUuid() != null) {
                operator.addProperty("uuid", request.operatorUuid());
                operator.addProperty("name", request.operatorName());
            }
            event.add("operator", request != null && request.operatorUuid() != null ? operator : JsonNull.INSTANCE);
            event.addProperty("agentOp", operatorUuid != null && config.isAgentOperator(operatorUuid));
            event.addProperty("orderingEvidence", request != null);
            if (target && request == null) {
                markIncomplete(
                        "target_input_without_request",
                        "triggerEvent at " + pos + " had no playNote request within "
                                + config.correlationWindowTicks + " ticks");
            }
            log.append(event);
        } catch (Throwable error) {
            hookError("trigger_event", error);
        } finally {
            hook("trigger_event").record(System.nanoTime() - started);
        }
    }

    /** Called at HEAD of Entity.remove. */
    public void onEntityRemove(Entity entity, Entity.RemovalReason reason) {
        if (ended) {
            return;
        }
        long started = System.nanoTime();
        try {
            if (!(entity.level() instanceof ServerLevel)) {
                return;
            }
            TrackedCart cart = carts.get(entity.getUUID());
            if (cart == null || cart.removeLogged) {
                return;
            }
            handleRemoval(cart, entity, reason, "entity_remove");
        } catch (Throwable error) {
            hookError("entity_remove", error);
        } finally {
            hook("entity_remove").record(System.nanoTime() - started);
        }
    }

    /**
     * Called at HEAD of AbstractMinecartContainer.remove, before the container
     * drops its contents. This is what preserves the ordered inventory of a
     * chest minecart that is about to fall into the void.
     */
    public void onMinecartContainerRemove(AbstractMinecartContainer cart, Entity.RemovalReason reason) {
        if (ended) {
            return;
        }
        long started = System.nanoTime();
        try {
            if (!(cart.level() instanceof ServerLevel)) {
                return;
            }
            TrackedCart tracked = carts.get(cart.getUUID());
            if (tracked == null || tracked.removeLogged) {
                return;
            }
            handleRemoval(tracked, cart, reason, "minecart_container_remove_before_drop");
        } catch (Throwable error) {
            hookError("minecart_container_remove", error);
        } finally {
            hook("minecart_container_remove").record(System.nanoTime() - started);
        }
    }

    private void handleRemoval(TrackedCart cart, Entity entity, Entity.RemovalReason reason, String path) {
        JsonElement inventory = JsonViews.inventory(entity);
        String digest = JsonViews.inventoryDigest(inventory);
        if (!cart.exited) {
            cart.exited = true;
            cart.exitTick = serverTick;
            cart.exitVia = "removal";
            appendCartExit(cart, entity, inventory, digest, "removal");
        }
        JsonObject event = log.event("cart_remove");
        event.addProperty("uuid", cart.uuid.toString());
        event.addProperty("cartType", cart.type);
        event.addProperty("epoch", cart.epoch);
        event.addProperty("reason", reason.name());
        event.addProperty("shouldDestroy", reason.shouldDestroy());
        event.addProperty("shouldSave", reason.shouldSave());
        event.add("pos", JsonViews.position(entity));
        event.add("motion", JsonViews.motion(entity.getDeltaMovement()));
        event.addProperty("level", JsonViews.dimension(entity));
        event.addProperty("exited", cart.exited);
        event.addProperty("exitVia", cart.exitVia);
        event.add("inventory", inventory);
        event.addProperty("inventoryDigest", digest);
        event.addProperty("capturedPath", path);
        event.addProperty("permanent", reason.shouldDestroy());
        log.append(event);
        cart.removeLogged = true;
        cart.permanent = reason.shouldDestroy();
        cart.removeTick = serverTick;
        cart.removeSeq = log.seq();
        cart.removalReason = reason.name();
        cart.lastDigest = digest;
        if (!cart.permanent) {
            // UNLOADED_*: the entity may come back when the chunk reloads, so
            // keep the record and let cart_reload clear removeLogged.
            log.count("cart_unload");
        }
    }

    private void appendCartExit(TrackedCart cart, Entity entity, JsonElement inventory, String digest, String via) {
        JsonObject event = log.event("cart_exit");
        event.addProperty("uuid", cart.uuid.toString());
        event.addProperty("cartType", cart.type);
        event.addProperty("epoch", cart.epoch);
        event.add("pos", JsonViews.position(entity));
        event.add("motion", JsonViews.motion(entity.getDeltaMovement()));
        event.addProperty("level", JsonViews.dimension(entity));
        event.addProperty("via", via);
        event.addProperty("stackRegion", config.stackRegion.name);
        event.addProperty("outputRegion", config.outputRegion == null ? null : config.outputRegion.name);
        event.add("inventory", inventory);
        event.addProperty("inventoryDigest", digest);
        log.append(event);
        cart.exitSeq = log.seq();
        cart.lastDigest = digest;
    }

    /** Called on Fabric ENTITY_LOAD. */
    public void onEntityLoad(Entity entity, ServerLevel world) {
        if (ended) {
            return;
        }
        long started = System.nanoTime();
        try {
            trackCart(entity, world);
        } catch (Throwable error) {
            hookError("entity_load", error);
        } finally {
            hook("entity_load").record(System.nanoTime() - started);
        }
    }

    private void trackCart(Entity entity, ServerLevel world) {
        if (!world.dimension().identifier().toString().equals(config.dimension)) {
            return;
        }
        String type = BuiltInRegistries.ENTITY_TYPE.getKey(entity.getType()).toString();
        if (!config.tracksCartType(type)) {
            return;
        }
        if (!config.stackRegion.contains(entity.getX(), entity.getY(), entity.getZ())) {
            return;
        }
        UUID uuid = entity.getUUID();
        JsonElement inventory = JsonViews.inventory(entity);
        String digest = JsonViews.inventoryDigest(inventory);
        TrackedCart existing = carts.get(uuid);
        if (existing != null) {
            existing.entity = entity;
            if (existing.removeLogged && existing.permanent) {
                existing.epoch++;
                existing.epochTick = serverTick;
                existing.exited = false;
                existing.exitTick = -1;
                existing.exitSeq = -1;
                existing.exitVia = null;
                existing.removeLogged = false;
                existing.permanent = false;
                existing.removeTick = -1;
                existing.removeSeq = -1;
                existing.removalReason = null;
                existing.lastSampleTick = -1;
                existing.samples = 0;
                JsonObject event = log.event("cart_reappeared");
                event.addProperty("uuid", uuid.toString());
                event.addProperty("cartType", type);
                event.addProperty("epoch", existing.epoch);
                event.add("pos", JsonViews.position(entity));
                event.add("inventory", inventory);
                event.addProperty("inventoryDigest", digest);
                log.append(event);
                markIncomplete("uuid_reuse", "cart " + uuid + " loaded again after a permanent removal");
            } else {
                existing.removeLogged = false;
                JsonObject event = log.event("cart_reload");
                event.addProperty("uuid", uuid.toString());
                event.addProperty("cartType", type);
                event.addProperty("epoch", existing.epoch);
                event.add("pos", JsonViews.position(entity));
                event.add("inventory", inventory);
                event.addProperty("inventoryDigest", digest);
                log.append(event);
            }
            existing.lastDigest = digest;
            return;
        }
        TrackedCart cart = new TrackedCart(uuid, type, entity, carts.size());
        cart.epochTick = serverTick;
        cart.lastDigest = digest;
        carts.put(uuid, cart);
        JsonObject event = log.event("cart_tracked");
        event.addProperty("uuid", uuid.toString());
        event.addProperty("cartType", type);
        event.addProperty("trackIndex", cart.trackIndex);
        event.addProperty("epoch", cart.epoch);
        event.addProperty("origin", phase.equals("experiment") || phase.equals("post") ? "experiment" : "pre_experiment");
        event.addProperty("dimension", config.dimension);
        event.add("pos", JsonViews.position(entity));
        event.add("motion", JsonViews.motion(entity.getDeltaMovement()));
        event.add("inventory", inventory);
        event.addProperty("inventoryDigest", digest);
        log.append(event);
    }

    /** Called at HEAD of the Entity teleport paths we care about. */
    public void onEntityTeleport(Entity entity, String method, double x, double y, double z) {
        if (ended) {
            return;
        }
        long started = System.nanoTime();
        try {
            if (!(entity.level() instanceof ServerLevel)) {
                return;
            }
            TrackedCart cart = carts.get(entity.getUUID());
            if (cart == null) {
                return;
            }
            JsonObject event = log.event("cart_teleport");
            event.addProperty("uuid", cart.uuid.toString());
            event.addProperty("cartType", cart.type);
            event.addProperty("epoch", cart.epoch);
            event.addProperty("method", method);
            event.add("from", JsonViews.position(entity));
            event.add("to", JsonViews.position(x, y, z));
            event.addProperty("level", JsonViews.dimension(entity));
            log.append(event);
        } catch (Throwable error) {
            hookError("entity_teleport", error);
        } finally {
            hook("entity_teleport").record(System.nanoTime() - started);
        }
    }

    // ------------------------------------------------------------------ ticks

    public void onTickStart(MinecraftServer server) {
        serverTick = server.getTickCount();
        log.setTick(serverTick);
    }

    public void onTickEnd(MinecraftServer server) {
        if (ended) {
            return;
        }
        long started = System.nanoTime();
        try {
            serverTick = server.getTickCount();
            log.setTick(serverTick);
            for (TrackedCart cart : carts.values()) {
                Entity entity = cart.entity;
                if (entity == null || entity.isRemoved()) {
                    continue;
                }
                boolean outsideStack = !config.stackRegion.contains(entity.getX(), entity.getY(), entity.getZ());
                boolean inOutput = config.outputRegion != null
                        && config.outputRegion.contains(entity.getX(), entity.getY(), entity.getZ());
                if (!cart.exited && (outsideStack || inOutput)) {
                    cart.exited = true;
                    cart.exitTick = serverTick;
                    cart.exitVia = inOutput ? "output_region" : "left_stack_region";
                    JsonElement inventory = JsonViews.inventory(entity);
                    appendCartExit(
                            cart, entity, inventory, JsonViews.inventoryDigest(inventory), cart.exitVia);
                }
                if (cart.lastSampleTick < 0 || serverTick - cart.lastSampleTick >= config.sampleIntervalTicks) {
                    sampleCart(cart, entity, outsideStack, inOutput);
                    cart.lastSampleTick = serverTick;
                    cart.samples++;
                }
            }
            writeStatus(false);
        } catch (Throwable error) {
            hookError("tick_end", error);
        } finally {
            hook("tick_end").record(System.nanoTime() - started);
        }
    }

    private void sampleCart(TrackedCart cart, Entity entity, boolean outsideStack, boolean inOutput) {
        JsonElement inventory = JsonViews.inventory(entity);
        String digest = JsonViews.inventoryDigest(inventory);
        JsonObject event = log.event("cart_sample");
        event.addProperty("uuid", cart.uuid.toString());
        event.addProperty("cartType", cart.type);
        event.addProperty("epoch", cart.epoch);
        event.addProperty("sampleIndex", cart.samples);
        event.add("pos", JsonViews.position(entity));
        event.add("motion", JsonViews.motion(entity.getDeltaMovement()));
        event.addProperty("level", JsonViews.dimension(entity));
        event.addProperty("exited", cart.exited);
        event.addProperty("outsideStackRegion", outsideStack);
        event.addProperty("insideOutputRegion", inOutput);
        event.add("inventory", inventory);
        event.addProperty("inventoryDigest", digest);
        log.append(event);
        if (!digest.equals(cart.lastDigest)) {
            JsonObject change = log.event("cart_inventory_change");
            change.addProperty("uuid", cart.uuid.toString());
            change.addProperty("cartType", cart.type);
            change.addProperty("epoch", cart.epoch);
            change.addProperty("previousDigest", cart.lastDigest);
            change.addProperty("inventoryDigest", digest);
            change.add("inventory", inventory);
            log.append(change);
            cart.lastDigest = digest;
        }
    }

    // ------------------------------------------------------------------ phase

    public synchronized String transition(String requested, String actor, String reason) {
        String target = switch (requested) {
            case "ready" -> "ready";
            case "init" -> "init";
            case "restore" -> "restore";
            case "experiment_start" -> "experiment";
            case "experiment_end" -> "post";
            case "end" -> "end";
            default -> null;
        };
        if (target == null) {
            return "unknown phase " + requested;
        }
        boolean allowed = switch (target) {
            case "ready" -> phase.equals("bootstrap") || phase.equals("ready");
            case "init", "restore" -> !phase.equals("experiment") && !phase.equals("post") && !phase.equals("end");
            case "experiment" -> phase.equals("ready") || phase.equals("init") || phase.equals("restore");
            case "post" -> phase.equals("experiment");
            case "end" -> true;
            default -> false;
        };
        if (!allowed) {
            return "cannot move from " + phase + " to " + requested;
        }
        String previous = phase;
        phase = target;
        log.setPhase(target);
        JsonObject event = log.event("phase");
        event.addProperty("from", previous);
        event.addProperty("to", target);
        event.addProperty("requested", requested);
        event.addProperty("actor", actor == null || actor.isBlank() ? "unknown" : actor);
        event.addProperty("reason", reason == null ? "" : reason);
        log.append(event);
        if (target.equals("end")) {
            endInternal(reason);
            return null;
        }
        writeStatus(true);
        return null;
    }

    public synchronized void mark(String label, String actor) {
        if (ended) {
            return;
        }
        JsonObject event = log.event("mark");
        event.addProperty("label", label == null ? "" : label);
        event.addProperty("actor", actor == null || actor.isBlank() ? "unknown" : actor);
        log.append(event);
        writeStatus(false);
    }

    public synchronized void endFromServer(String reason) {
        if (!ended) {
            endInternal(reason);
        }
    }

    private void endInternal(String reason) {
        if (ended) {
            return;
        }
        ended = true;
        phase = "end";
        log.setPhase(phase);
        JsonObject event = log.event("audit_end");
        boolean complete = incompleteReasons.isEmpty() && !log.truncated();
        event.addProperty("sessionId", log.sessionId());
        event.addProperty("status", complete ? "complete" : "incomplete");
        event.addProperty("reason", reason == null ? "" : reason);
        event.addProperty("truncated", log.truncated());
        event.addProperty("bytes", log.bytes());
        event.addProperty("seq", log.seq());
        event.addProperty("uptimeMillis", (System.nanoTime() - startedNanos) / 1_000_000L);
        JsonArray reasons = new JsonArray();
        for (String incomplete : incompleteReasons) {
            reasons.add(incomplete);
        }
        event.add("incompleteReasons", reasons);
        event.add("counts", log.countersJson());
        event.add("carts", cartsSummary());
        event.add("hooks", hooksJson());
        log.append(event);
        writeStatus(true);
        log.close();
    }

    private JsonObject cartsSummary() {
        JsonObject object = new JsonObject();
        int tracked = 0;
        int exited = 0;
        int permanentRemoved = 0;
        int unloaded = 0;
        int present = 0;
        for (TrackedCart cart : carts.values()) {
            tracked++;
            if (cart.exited) {
                exited++;
            }
            if (cart.removeLogged) {
                if (cart.permanent) {
                    permanentRemoved++;
                } else {
                    unloaded++;
                }
            } else if (!cart.entity.isRemoved()) {
                present++;
            }
        }
        object.addProperty("tracked", tracked);
        object.addProperty("exited", exited);
        object.addProperty("permanentRemoved", permanentRemoved);
        object.addProperty("unloaded", unloaded);
        object.addProperty("presentAtEnd", present);
        return object;
    }

    private JsonObject hooksJson() {
        JsonObject object = new JsonObject();
        for (Map.Entry<String, HookStats> entry : hooks.entrySet()) {
            object.add(entry.getKey(), entry.getValue().toJson());
        }
        return object;
    }

    public JsonObject statusExtra() {
        JsonObject extra = new JsonObject();
        extra.addProperty("mod", "mc-agent-minecart-audit");
        extra.addProperty("sessionId", log.sessionId());
        extra.add("carts", cartsSummary());
        extra.add("hooks", hooksJson());
        extra.addProperty("log", log.file().toString());
        return extra;
    }

    private void writeStatus(boolean force) {
        JsonArray reasons = new JsonArray();
        for (String reason : incompleteReasons) {
            reasons.add(reason);
        }
        String[] array = new String[reasons.size()];
        for (int index = 0; index < reasons.size(); index++) {
            array[index] = reasons.get(index).getAsString();
        }
        log.writeStatus(log.statusJson(statusExtra(), ended, array), force);
    }

    public synchronized void markIncomplete(String reason, String detail) {
        if (ended || !incompleteReasons.add(reason)) {
            return;
        }
        JsonObject event = log.event("audit_incomplete");
        event.addProperty("reason", reason);
        event.addProperty("detail", detail == null ? "" : detail);
        log.append(event);
        report("mcaudit: incomplete audit: " + reason + " (" + detail + ")");
    }

    private void hookError(String hookName, Throwable error) {
        String message = error.getClass().getSimpleName() + ": " + error.getMessage();
        markIncomplete("hook_error:" + hookName, message);
        error.printStackTrace(System.err);
    }

    private void report(String message) {
        System.err.println(message);
    }

    public Path auditDir() {
        return auditDir;
    }

    // ------------------------------------------------------------------ helpers

    private HookStats hook(String name) {
        return hooks.computeIfAbsent(name, HookStats::new);
    }

    private void record(Map<Long, ArrayDeque<InputRecord>> map, InputRecord record) {
        map.computeIfAbsent(BlockPos.asLong(record.x(), record.y(), record.z()), key -> new ArrayDeque<>())
                .addLast(record);
    }

    /**
     * The most recent record at {@code pos} inside the correlation window.
     * Order is insertion order (actual occurrence), never a sorted query.
     */
    private InputRecord latest(Map<Long, ArrayDeque<InputRecord>> map, BlockPos pos, int tick) {
        ArrayDeque<InputRecord> queue = map.get(BlockPos.asLong(pos.getX(), pos.getY(), pos.getZ()));
        if (queue == null) {
            return null;
        }
        while (!queue.isEmpty() && tick - queue.peekFirst().tick() > config.correlationWindowTicks) {
            queue.pollFirst();
        }
        return queue.peekLast();
    }

    private static void addOperator(JsonObject event, Player player) {
        JsonObject operator = JsonViews.operator(player);
        event.add("operator", operator == null ? JsonNull.INSTANCE : operator);
    }

    private static JsonElement itemJson(ItemStack stack) {
        if (stack == null || stack.isEmpty()) {
            return JsonNull.INSTANCE;
        }
        JsonObject object = new JsonObject();
        object.addProperty("item", BuiltInRegistries.ITEM.getKey(stack.getItem()).toString());
        object.addProperty("count", stack.getCount());
        return object;
    }

    private static String resultName(InteractionResult result) {
        if (result == null) {
            return null;
        }
        if (result == InteractionResult.SUCCESS) {
            return "SUCCESS";
        }
        if (result == InteractionResult.SUCCESS_SERVER) {
            return "SUCCESS_SERVER";
        }
        if (result == InteractionResult.CONSUME) {
            return "CONSUME";
        }
        if (result == InteractionResult.FAIL) {
            return "FAIL";
        }
        if (result == InteractionResult.PASS) {
            return "PASS";
        }
        if (result == InteractionResult.TRY_WITH_EMPTY_HAND) {
            return "TRY_WITH_EMPTY_HAND";
        }
        return result.getClass().getSimpleName();
    }

    /** Called by AuditMod when no valid config can be loaded. */
    public static void reportConfigError(MinecraftServer server, Throwable error) {
        try {
            Path gameDir = FabricLoader.getInstance().getGameDir();
            Path dir = gameDir.resolve("mc-audit");
            Files.createDirectories(dir);
            JsonObject object = new JsonObject();
            object.addProperty("mod", "mc-agent-minecart-audit");
            object.addProperty("configured", false);
            object.addProperty("error", error.getClass().getSimpleName() + ": " + error.getMessage());
            object.addProperty("configPath", dir.resolve("config.json").toString());
            object.addProperty("updatedAtWall", System.currentTimeMillis());
            Files.writeString(dir.resolve("status-unconfigured.json"), object.toString(), StandardCharsets.UTF_8);
        } catch (IOException writeError) {
            System.err.println("mcaudit: could not write status-unconfigured.json: " + writeError);
        }
        System.err.println("mcaudit: audit disabled: " + error);
        error.printStackTrace(System.err);
    }

    public void endFromCommand(String actor, String reason) {
        if (!ended) {
            JsonObject event = log.event("phase");
            String previous = phase;
            phase = "end";
            log.setPhase(phase);
            event.addProperty("from", previous);
            event.addProperty("to", "end");
            event.addProperty("requested", "end");
            event.addProperty("actor", actor == null || actor.isBlank() ? "unknown" : actor);
            event.addProperty("reason", reason == null ? "" : reason);
            log.append(event);
            endInternal(reason);
        }
    }

    public void flush() {
        writeStatus(true);
    }

    public String describe() {
        return "run=" + config.runId + " inst=" + config.instanceId + " phase=" + phase
                + " seq=" + log.seq() + " tracked=" + carts.size() + " incomplete=" + incompleteReasons;
    }
}
