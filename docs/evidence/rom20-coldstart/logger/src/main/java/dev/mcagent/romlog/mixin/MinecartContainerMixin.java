package dev.mcagent.romlog.mixin;

import dev.mcagent.romlog.RomLog;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.vehicle.minecart.AbstractMinecartContainer;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * Captures a container minecart's ordered inventory at the head of its own
 * {@code remove} override, before the override drops the contents. This is a
 * safety net for a cart that disappears before the end-of-tick sampler sees it.
 */
@Mixin(AbstractMinecartContainer.class)
public abstract class MinecartContainerMixin {
    @Inject(
            method = "remove(Lnet/minecraft/world/entity/Entity$RemovalReason;)V",
            at = @At("HEAD"))
    private void romlog$remove(Entity.RemovalReason reason, CallbackInfo info) {
        RomLog.capture((AbstractMinecartContainer) (Object) this, "container_remove", reason.name());
    }
}
