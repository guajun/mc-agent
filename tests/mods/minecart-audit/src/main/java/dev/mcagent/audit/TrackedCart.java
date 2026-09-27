package dev.mcagent.audit;

import net.minecraft.world.entity.Entity;

import java.util.UUID;

/** Bookkeeping for one minecart uuid that ever loaded inside the fixture. */
public final class TrackedCart {
    public final UUID uuid;
    public final String type;
    public final int trackIndex;
    public Entity entity;
    public int epoch = 1;
    public int epochTick;
    public boolean exited;
    public int exitTick = -1;
    public long exitSeq = -1;
    public String exitVia;
    public boolean removeLogged;
    public boolean permanent;
    public int removeTick = -1;
    public long removeSeq = -1;
    public String removalReason;
    public String lastDigest = "none";
    public int lastSampleTick = -1;
    public int samples;
    public double lastX;
    public double lastY;
    public double lastZ;

    public TrackedCart(UUID uuid, String type, Entity entity, int trackIndex) {
        this.uuid = uuid;
        this.type = type;
        this.entity = entity;
        this.trackIndex = trackIndex;
    }
}
