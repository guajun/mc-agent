package dev.mcagent.romlog;

import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerEntityEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;

/** Entry point of the agent-owned Minecart ROM logger. */
public final class RomLogMod implements ModInitializer {
    @Override
    public void onInitialize() {
        ServerLifecycleEvents.SERVER_STARTED.register(RomLog::arm);
        ServerLifecycleEvents.SERVER_STOPPING.register(server -> RomLog.flush("server_stopping"));
        ServerTickEvents.END_SERVER_TICK.register(server -> RomLog.tick());
        ServerEntityEvents.ENTITY_LOAD.register(RomLog::track);
        System.out.println("[romlog] loaded build=" + RomLog.BUILD);
    }
}
