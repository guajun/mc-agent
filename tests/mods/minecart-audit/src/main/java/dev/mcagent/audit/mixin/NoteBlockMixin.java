package dev.mcagent.audit.mixin;

import dev.mcagent.audit.AuditEngine;
import net.minecraft.core.BlockPos;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.block.NoteBlock;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.BlockHitResult;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/**
 * Input hooks on the vanilla note block, calibrated in 26.2.
 *
 * <p>{@code useItemOn}/{@code useWithoutItem} are the player attempts;
 * {@code playNote} is the request that carries the triggering entity (a
 * player, or null for redstone); {@code triggerEvent} is the server actually
 * processing the note (the sound/particle effect). All four use
 * {@code @Inject} only - no redirects, no overwrites, no mutation.
 */
@Mixin(NoteBlock.class)
public abstract class NoteBlockMixin {
    @Inject(
            method = "useItemOn(Lnet/minecraft/world/item/ItemStack;Lnet/minecraft/world/level/block/state/BlockState;"
                    + "Lnet/minecraft/world/level/Level;Lnet/minecraft/core/BlockPos;"
                    + "Lnet/minecraft/world/entity/player/Player;Lnet/minecraft/world/InteractionHand;"
                    + "Lnet/minecraft/world/phys/BlockHitResult;)Lnet/minecraft/world/InteractionResult;",
            at = @At("RETURN"))
    private void mcaudit$useItemOn(
            ItemStack stack,
            BlockState state,
            Level level,
            BlockPos pos,
            Player player,
            InteractionHand hand,
            BlockHitResult hit,
            CallbackInfoReturnable<InteractionResult> info) {
        AuditEngine engine = AuditEngine.get();
        if (engine != null) {
            engine.onNoteUse("useItemOn", state, level, pos, player, hand, stack, info.getReturnValue());
        }
    }

    @Inject(method = "attack(Lnet/minecraft/world/level/block/state/BlockState;"
            + "Lnet/minecraft/world/level/Level;Lnet/minecraft/core/BlockPos;"
            + "Lnet/minecraft/world/entity/player/Player;)V", at = @At("HEAD"))
    private void mcaudit$attack(BlockState state, Level level, BlockPos pos, Player player, CallbackInfo info) {
        AuditEngine engine = AuditEngine.get();
        if (engine != null) {
            engine.onNoteAttack(state, level, pos, player);
        }
    }

    @Inject(
            method = "useWithoutItem(Lnet/minecraft/world/level/block/state/BlockState;"
                    + "Lnet/minecraft/world/level/Level;Lnet/minecraft/core/BlockPos;"
                    + "Lnet/minecraft/world/entity/player/Player;Lnet/minecraft/world/phys/BlockHitResult;)"
                    + "Lnet/minecraft/world/InteractionResult;",
            at = @At("RETURN"))
    private void mcaudit$useWithoutItem(
            BlockState state,
            Level level,
            BlockPos pos,
            Player player,
            BlockHitResult hit,
            CallbackInfoReturnable<InteractionResult> info) {
        AuditEngine engine = AuditEngine.get();
        if (engine != null) {
            engine.onNoteUse("useWithoutItem", state, level, pos, player, null, null, info.getReturnValue());
        }
    }

    @Inject(
            method = "playNote(Lnet/minecraft/world/entity/Entity;"
                    + "Lnet/minecraft/world/level/block/state/BlockState;Lnet/minecraft/world/level/Level;"
                    + "Lnet/minecraft/core/BlockPos;)V",
            at = @At("HEAD"))
    private void mcaudit$playNote(Entity entity, BlockState state, Level level, BlockPos pos, CallbackInfo info) {
        AuditEngine engine = AuditEngine.get();
        if (engine != null) {
            engine.onPlayNote(entity, state, level, pos);
        }
    }

    @Inject(
            method = "triggerEvent(Lnet/minecraft/world/level/block/state/BlockState;"
                    + "Lnet/minecraft/world/level/Level;Lnet/minecraft/core/BlockPos;II)Z",
            at = @At("RETURN"))
    private void mcaudit$triggerEvent(
            BlockState state,
            Level level,
            BlockPos pos,
            int eventId,
            int eventParam,
            CallbackInfoReturnable<Boolean> info) {
        AuditEngine engine = AuditEngine.get();
        if (engine != null) {
            engine.onTriggerEvent(state, level, pos, eventId, eventParam, info.getReturnValueZ());
        }
    }
}
