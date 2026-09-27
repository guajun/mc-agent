package dev.mcagent.audit.mixin;

import dev.mcagent.audit.AuditEngine;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.level.portal.TeleportTransition;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

import java.util.Set;

/**
 * Removal and teleport hooks used for transient-cart capture and for
 * alt-behaviour evidence. {@code remove} runs before the removal reason is
 * applied, so position and motion are still the real final values.
 */
@Mixin(Entity.class)
public abstract class EntityMixin {
    @Inject(
            method = "remove(Lnet/minecraft/world/entity/Entity$RemovalReason;)V",
            at = @At("HEAD"))
    private void mcaudit$remove(Entity.RemovalReason reason, CallbackInfo info) {
        AuditEngine engine = AuditEngine.get();
        if (engine != null) {
            engine.onEntityRemove((Entity) (Object) this, reason);
        }
    }

    @Inject(
            method = "teleport(Lnet/minecraft/world/level/portal/TeleportTransition;)"
                    + "Lnet/minecraft/world/entity/Entity;",
            at = @At("HEAD"))
    private void mcaudit$teleport(TeleportTransition transition, CallbackInfoReturnable<Entity> info) {
        AuditEngine engine = AuditEngine.get();
        if (engine != null && transition != null && transition.position() != null) {
            engine.onEntityTeleport(
                    (Entity) (Object) this,
                    "teleport",
                    transition.position().x,
                    transition.position().y,
                    transition.position().z);
        }
    }

    @Inject(
            method = "teleportTo(Lnet/minecraft/server/level/ServerLevel;DDDLjava/util/Set;FFZ)Z",
            at = @At("HEAD"))
    private void mcaudit$teleportTo(
            ServerLevel level,
            double x,
            double y,
            double z,
            Set<?> relatives,
            float yRot,
            float xRot,
            boolean setCamera,
            CallbackInfoReturnable<Boolean> info) {
        AuditEngine engine = AuditEngine.get();
        if (engine != null) {
            engine.onEntityTeleport((Entity) (Object) this, "teleportTo", x, y, z);
        }
    }

    @Inject(method = "teleportTo(DDD)V", at = @At("HEAD"))
    private void mcaudit$teleportToSimple(double x, double y, double z, CallbackInfo info) {
        AuditEngine engine = AuditEngine.get();
        if (engine != null) {
            engine.onEntityTeleport((Entity) (Object) this, "teleportTo_simple", x, y, z);
        }
    }
}
