package dev.mcagent.audit.mixin;

import dev.mcagent.audit.AuditEngine;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.vehicle.minecart.AbstractMinecartContainer;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * Container minecarts override {@code remove} and drop their contents before
 * calling super. Hooking the override at HEAD is the only place where the
 * ordered inventory of a cart that is about to fall into the void is still
 * intact.
 */
@Mixin(AbstractMinecartContainer.class)
public abstract class MinecartContainerMixin {
    @Inject(
            method = "remove(Lnet/minecraft/world/entity/Entity$RemovalReason;)V",
            at = @At("HEAD"))
    private void mcaudit$remove(Entity.RemovalReason reason, CallbackInfo info) {
        AuditEngine engine = AuditEngine.get();
        if (engine != null) {
            engine.onMinecartContainerRemove((AbstractMinecartContainer) (Object) this, reason);
        }
    }
}
