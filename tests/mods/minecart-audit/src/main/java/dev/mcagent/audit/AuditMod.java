package dev.mcagent.audit;

import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.command.v2.CommandRegistrationCallback;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerEntityEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;

/**
 * Entry point of the independent audit mod.
 *
 * <p>The mod is intentionally a test-side instrument: it loads on both
 * dedicated and integrated servers, writes only files, registers only
 * read-only hooks and never talks to the agent. Missing or malformed
 * configuration is loud (console + status-unconfigured.json) but never stops
 * the game.
 */
public final class AuditMod implements ModInitializer {
    @Override
    public void onInitialize() {
        ServerLifecycleEvents.SERVER_STARTED.register(server -> {
            try {
                AuditEngine.start(server);
                System.err.println("mcaudit: audit ready: " + AuditEngine.get().describe());
            } catch (Throwable error) {
                AuditEngine.reportConfigError(server, error);
            }
        });
        ServerLifecycleEvents.SERVER_STOPPING.register(server -> {
            AuditEngine engine = AuditEngine.get();
            if (engine != null) {
                engine.endFromServer("server_stopping");
                AuditEngine.uninstall();
            }
        });
        ServerTickEvents.START_SERVER_TICK.register(server -> {
            AuditEngine engine = AuditEngine.get();
            if (engine != null) {
                engine.onTickStart(server);
            }
        });
        ServerTickEvents.END_SERVER_TICK.register(server -> {
            AuditEngine engine = AuditEngine.get();
            if (engine != null) {
                engine.onTickEnd(server);
            }
        });
        ServerEntityEvents.ENTITY_LOAD.register((entity, world) -> {
            AuditEngine engine = AuditEngine.get();
            if (engine != null) {
                engine.onEntityLoad(entity, world);
            }
        });
        CommandRegistrationCallback.EVENT.register(
                (dispatcher, registryAccess, environment) -> AuditCommand.register(dispatcher));
    }
}
