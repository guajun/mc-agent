package dev.mcagent.audit;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.Container;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.phys.Vec3;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.UUID;

/** How the audit turns live game objects into stable JSON evidence. */
public final class JsonViews {
    private JsonViews() {
    }

    /** The ordered slot list of a container entity, or JSON null for a plain entity. */
    public static JsonElement inventory(Entity entity) {
        if (!(entity instanceof Container container)) {
            return JsonNull.INSTANCE;
        }
        JsonArray slots = new JsonArray();
        for (int slot = 0; slot < container.getContainerSize(); slot++) {
            ItemStack stack = container.getItem(slot);
            if (stack == null || stack.isEmpty()) {
                continue;
            }
            JsonObject entry = new JsonObject();
            entry.addProperty("slot", slot);
            entry.addProperty("item", BuiltInRegistries.ITEM.getKey(stack.getItem()).toString());
            entry.addProperty("count", stack.getCount());
            if (stack.isDamageableItem()) {
                entry.addProperty("damage", stack.getDamageValue());
            }
            slots.add(entry);
        }
        return slots;
    }

    /** Stable digest of the ordered inventory evidence; never used as the answer itself. */
    public static String inventoryDigest(JsonElement inventory) {
        if (inventory == null || inventory.isJsonNull()) {
            return "none";
        }
        return sha256(inventory.toString()).substring(0, 16);
    }

    public static String sha256(String text) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] hashed = digest.digest(text.getBytes(StandardCharsets.UTF_8));
            StringBuilder builder = new StringBuilder(hashed.length * 2);
            for (byte value : hashed) {
                builder.append(Character.forDigit((value >> 4) & 0xF, 16));
                builder.append(Character.forDigit(value & 0xF, 16));
            }
            return builder.toString();
        } catch (Exception error) {
            throw new IllegalStateException("SHA-256 unavailable", error);
        }
    }

    public static JsonObject position(double x, double y, double z) {
        JsonObject object = new JsonObject();
        object.addProperty("x", round(x));
        object.addProperty("y", round(y));
        object.addProperty("z", round(z));
        return object;
    }

    public static JsonObject position(Entity entity) {
        return position(entity.getX(), entity.getY(), entity.getZ());
    }

    public static JsonObject motion(Vec3 motion) {
        JsonObject object = new JsonObject();
        object.addProperty("x", round(motion.x));
        object.addProperty("y", round(motion.y));
        object.addProperty("z", round(motion.z));
        return object;
    }

    public static JsonObject operator(Player player) {
        if (player == null) {
            return null;
        }
        JsonObject object = new JsonObject();
        UUID uuid = player.getUUID();
        object.addProperty("uuid", uuid.toString());
        object.addProperty("name", player.getScoreboardName());
        return object;
    }

    public static String dimension(Entity entity) {
        return entity.level().dimension().identifier().toString();
    }

    private static double round(double value) {
        return Math.round(value * 1000.0) / 1000.0;
    }
}
