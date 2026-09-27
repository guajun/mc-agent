package dev.mcagent.rom21.mixin;

import dev.mcagent.rom21.RomLog;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.vehicle.minecart.AbstractMinecartContainer;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * Captures the ordered live inventory at the head of the cart's own
 * {@code remove} override, before the override drops the contents, and
 * records the real removal reason.  This is the natural void capture: the
 * game removes a cart that fell below the world with
 * {@code RemovalReason.DISCARDED}.
 */
@Mixin(AbstractMinecartContainer.class)
public abstract class MinecartContainerMixin {
    @Inject(
            method = "remove(Lnet/minecraft/world/entity/Entity$RemovalReason;)V",
            at = @At("HEAD"))
    private void romlog$remove(Entity.RemovalReason reason, CallbackInfo info) {
        RomLog.captureVoid((AbstractMinecartContainer) (Object) this, reason.name());
    }
}
