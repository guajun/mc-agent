package dev.mcagent.audit;

import com.mojang.brigadier.CommandDispatcher;
import com.mojang.brigadier.arguments.StringArgumentType;
import com.mojang.brigadier.context.CommandContext;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.Commands;
import net.minecraft.network.chat.Component;
import net.minecraft.server.permissions.Permissions;

/**
 * {@code /mcaudit} control surface for the test side, spoken over RCON.
 *
 * <p>Commands are not command blocks, and none of them operate the machine:
 * they only open/close phases, write markers, flush and end the audit. A
 * marker or a phase change can never be mistaken for a machine operation
 * because only {@code input_processed} events carry an operator.
 */
public final class AuditCommand {
    private AuditCommand() {
    }

    public static void register(CommandDispatcher<CommandSourceStack> dispatcher) {
        dispatcher.register(Commands.literal("mcaudit")
                .requires(source -> source.permissions().hasPermission(Permissions.COMMANDS_GAMEMASTER))
                .then(Commands.literal("status").executes(AuditCommand::status))
                .then(Commands.literal("flush").executes(context -> {
                    AuditEngine engine = AuditEngine.get();
                    if (engine == null) {
                        reply(context, "mcaudit: not configured");
                        return 0;
                    }
                    engine.flush();
                    reply(context, "mcaudit: flushed " + engine.log.file());
                    return 1;
                }))
                .then(Commands.literal("mark")
                        .then(Commands.argument("label", StringArgumentType.greedyString())
                                .executes(context -> {
                                    AuditEngine engine = AuditEngine.get();
                                    if (engine == null) {
                                        reply(context, "mcaudit: not configured");
                                        return 0;
                                    }
                                    engine.mark(StringArgumentType.getString(context, "label"), actor(context));
                                    reply(context, "mcaudit: marked");
                                    return 1;
                                })))
                .then(Commands.literal("phase")
                        .then(Commands.argument("name", StringArgumentType.word())
                                .executes(context -> phase(context, ""))
                                .then(Commands.argument("reason", StringArgumentType.greedyString())
                                        .executes(context -> phase(
                                                context, StringArgumentType.getString(context, "reason"))))))
                .then(Commands.literal("end").executes(context -> {
                    AuditEngine engine = AuditEngine.get();
                    if (engine == null) {
                        reply(context, "mcaudit: not configured");
                        return 0;
                    }
                    engine.endFromCommand(actor(context), "command");
                    reply(context, "mcaudit: ended");
                    return 1;
                })));
    }

    private static int status(CommandContext<CommandSourceStack> context) {
        AuditEngine engine = AuditEngine.get();
        if (engine == null) {
            reply(context, "mcaudit: not configured (see mc-audit/status-unconfigured.json)");
            return 0;
        }
        reply(context, "mcaudit: " + engine.describe());
        return 1;
    }

    private static int phase(CommandContext<CommandSourceStack> context, String reason) {
        AuditEngine engine = AuditEngine.get();
        if (engine == null) {
            reply(context, "mcaudit: not configured");
            return 0;
        }
        String requested = StringArgumentType.getString(context, "name");
        String error = engine.transition(requested, actor(context), reason);
        if (error != null) {
            reply(context, "mcaudit: " + error);
            return 0;
        }
        reply(context, "mcaudit: phase " + requested);
        return 1;
    }

    private static String actor(CommandContext<CommandSourceStack> context) {
        try {
            return context.getSource().getTextName();
        } catch (RuntimeException error) {
            return "unknown";
        }
    }

    private static void reply(CommandContext<CommandSourceStack> context, String message) {
        context.getSource().sendSuccess(() -> Component.literal(message), false);
    }
}
