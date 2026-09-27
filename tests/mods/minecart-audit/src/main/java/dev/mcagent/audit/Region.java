package dev.mcagent.audit;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

/**
 * An inclusive block-aligned box.
 *
 * <p>The coordinates are block coordinates. A point belongs to the region when
 * the block containing it (floor of each axis) is inside {@code from..to}; a
 * cart at {@code x=3.5} is inside a region ending at {@code x=3}, a cart at
 * {@code x=4.0} is not. This keeps the config readable in exactly the numbers
 * an operator types into a command.
 */
public final class Region {
    public final String name;
    public final int minX;
    public final int minY;
    public final int minZ;
    public final int maxX;
    public final int maxY;
    public final int maxZ;

    private Region(String name, int minX, int minY, int minZ, int maxX, int maxY, int maxZ) {
        this.name = name;
        this.minX = minX;
        this.minY = minY;
        this.minZ = minZ;
        this.maxX = maxX;
        this.maxY = maxY;
        this.maxZ = maxZ;
    }

    public static Region fromJson(String name, JsonElement element) {
        if (element == null || !element.isJsonObject()) {
            throw new IllegalArgumentException("region " + name + " must be an object with from/to");
        }
        JsonObject object = element.getAsJsonObject();
        int[] from = vector(name, object.get("from"));
        int[] to = vector(name, object.get("to"));
        for (int axis = 0; axis < 3; axis++) {
            if (from[axis] > to[axis]) {
                throw new IllegalArgumentException("region " + name + " has from > to on axis " + axis);
            }
        }
        return new Region(name, from[0], from[1], from[2], to[0], to[1], to[2]);
    }

    private static int[] vector(String name, JsonElement element) {
        if (element == null || !element.isJsonArray() || element.getAsJsonArray().size() != 3) {
            throw new IllegalArgumentException("region " + name + " needs a [x, y, z] vector");
        }
        JsonArray array = element.getAsJsonArray();
        int[] vector = new int[3];
        for (int index = 0; index < 3; index++) {
            vector[index] = array.get(index).getAsInt();
        }
        return vector;
    }

    public boolean contains(double x, double y, double z) {
        int blockX = floor(x);
        int blockY = floor(y);
        int blockZ = floor(z);
        return blockX >= minX && blockX <= maxX
                && blockY >= minY && blockY <= maxY
                && blockZ >= minZ && blockZ <= maxZ;
    }

    public boolean contains(int x, int y, int z) {
        return x >= minX && x <= maxX && y >= minY && y <= maxY && z >= minZ && z <= maxZ;
    }

    public static int floor(double value) {
        int truncated = (int) value;
        return value < truncated ? truncated - 1 : truncated;
    }

    public JsonObject toJson() {
        JsonObject object = new JsonObject();
        object.addProperty("name", name);
        object.add("from", vector(minX, minY, minZ));
        object.add("to", vector(maxX, maxY, maxZ));
        return object;
    }

    public static JsonArray vector(int x, int y, int z) {
        JsonArray array = new JsonArray();
        array.add(x);
        array.add(y);
        array.add(z);
        return array;
    }

    @Override
    public String toString() {
        return name + "[" + minX + "," + minY + "," + minZ + ".." + maxX + "," + maxY + "," + maxZ + "]";
    }
}
