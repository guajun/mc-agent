package dev.mcagent.audit;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/**
 * Immutable, validated view of {@code mc-audit/config.json}.
 *
 * <p>A malformed config is a hard start error: the audit would otherwise
 * collect useless evidence. The error is reported on the console and in
 * {@code mc-audit/status-unconfigured.json}; it never stops the game.
 */
public final class AuditConfig {
    public final Path configPath;
    public final String sha256;
    public final JsonObject raw;
    public final String runId;
    public final String instanceId;
    public final String dimension;
    public final String outputDir;
    public final String outputFile;
    public final long maxBytes;
    public final int sampleIntervalTicks;
    public final int correlationWindowTicks;
    public final String provenanceKind;
    public final String provenanceReference;
    public final String snapshotId;
    public final String snapshotHash;
    public final List<Region> inputRegions;
    public final Region stackRegion;
    public final Region outputRegion;
    public final Set<String> cartTypes;
    public final Set<String> agentUuids;

    private AuditConfig(
            Path configPath,
            String sha256,
            JsonObject raw,
            String runId,
            String instanceId,
            String dimension,
            String outputDir,
            String outputFile,
            long maxBytes,
            int sampleIntervalTicks,
            int correlationWindowTicks,
            String provenanceKind,
            String provenanceReference,
            String snapshotId,
            String snapshotHash,
            List<Region> inputRegions,
            Region stackRegion,
            Region outputRegion,
            Set<String> cartTypes,
            Set<String> agentUuids) {
        this.configPath = configPath;
        this.sha256 = sha256;
        this.raw = raw;
        this.runId = runId;
        this.instanceId = instanceId;
        this.dimension = dimension;
        this.outputDir = outputDir;
        this.outputFile = outputFile;
        this.maxBytes = maxBytes;
        this.sampleIntervalTicks = sampleIntervalTicks;
        this.correlationWindowTicks = correlationWindowTicks;
        this.provenanceKind = provenanceKind;
        this.provenanceReference = provenanceReference;
        this.snapshotId = snapshotId;
        this.snapshotHash = snapshotHash;
        this.inputRegions = inputRegions;
        this.stackRegion = stackRegion;
        this.outputRegion = outputRegion;
        this.cartTypes = cartTypes;
        this.agentUuids = agentUuids;
    }

    public static AuditConfig load(Path path) throws IOException {
        String text = Files.readString(path, StandardCharsets.UTF_8);
        JsonElement parsed = JsonParser.parseString(text);
        if (!parsed.isJsonObject()) {
            throw new IllegalArgumentException("config root must be a JSON object");
        }
        JsonObject object = parsed.getAsJsonObject();
        String runId = requiredString(object, "runId");
        String instanceId = requiredString(object, "instanceId");
        String dimension = object.has("dimension")
                ? object.get("dimension").getAsString()
                : "minecraft:overworld";
        String outputDir = object.has("outputDir") ? object.get("outputDir").getAsString() : "mc-audit";
        String outputFile = object.has("outputFile") ? object.get("outputFile").getAsString() : "";
        long maxBytes = object.has("maxBytes") ? object.get("maxBytes").getAsLong() : 64L * 1024 * 1024;
        int sampleIntervalTicks = object.has("sampleIntervalTicks")
                ? Math.max(1, object.get("sampleIntervalTicks").getAsInt())
                : 1;
        int correlationWindowTicks = object.has("correlationWindowTicks")
                ? Math.max(1, object.get("correlationWindowTicks").getAsInt())
                : 2;

        JsonObject provenance = object.has("provenance") && object.get("provenance").isJsonObject()
                ? object.getAsJsonObject("provenance")
                : new JsonObject();
        String provenanceKind = provenance.has("kind") ? provenance.get("kind").getAsString() : "unspecified";
        String provenanceReference = provenance.has("reference")
                ? provenance.get("reference").getAsString()
                : "";
        String snapshotId = provenance.has("snapshotId") ? provenance.get("snapshotId").getAsString() : "";
        String snapshotHash = provenance.has("snapshotHash") ? provenance.get("snapshotHash").getAsString() : "";

        if (!object.has("inputRegions") || !object.get("inputRegions").isJsonArray()) {
            throw new IllegalArgumentException("config needs an inputRegions array");
        }
        List<Region> inputRegions = new ArrayList<>();
        for (JsonElement element : object.getAsJsonArray("inputRegions")) {
            JsonObject region = element.getAsJsonObject();
            String name = requiredString(region, "name");
            inputRegions.add(Region.fromJson(name, region));
        }
        if (inputRegions.isEmpty()) {
            throw new IllegalArgumentException("inputRegions must name at least one machine input");
        }
        Region stackRegion = Region.fromJson("stack", requiredObject(object, "stackRegion"));
        Region outputRegion = object.has("outputRegion") && object.get("outputRegion").isJsonObject()
                ? Region.fromJson("output", object.getAsJsonObject("outputRegion"))
                : null;

        Set<String> cartTypes = new LinkedHashSet<>();
        if (object.has("cartTypes") && object.get("cartTypes").isJsonArray()) {
            for (JsonElement element : object.getAsJsonArray("cartTypes")) {
                cartTypes.add(element.getAsString());
            }
        }
        if (cartTypes.isEmpty()) {
            cartTypes.add("minecraft:chest_minecart");
        }
        Set<String> agentUuids = new LinkedHashSet<>();
        if (object.has("agentUuids") && object.get("agentUuids").isJsonArray()) {
            for (JsonElement element : object.getAsJsonArray("agentUuids")) {
                agentUuids.add(element.getAsString().toLowerCase(Locale.ROOT));
            }
        }
        return new AuditConfig(
                path,
                JsonViews.sha256(text),
                object,
                runId,
                instanceId,
                dimension,
                outputDir,
                outputFile,
                maxBytes,
                sampleIntervalTicks,
                correlationWindowTicks,
                provenanceKind,
                provenanceReference,
                snapshotId,
                snapshotHash,
                inputRegions,
                stackRegion,
                outputRegion,
                cartTypes,
                agentUuids);
    }

    private static String requiredString(JsonObject object, String key) {
        if (!object.has(key) || !object.get(key).isJsonPrimitive()) {
            throw new IllegalArgumentException("config needs a string " + key);
        }
        String value = object.get(key).getAsString();
        if (value.isBlank()) {
            throw new IllegalArgumentException("config " + key + " must not be blank");
        }
        return value;
    }

    private static JsonObject requiredObject(JsonObject object, String key) {
        if (!object.has(key) || !object.get(key).isJsonObject()) {
            throw new IllegalArgumentException("config needs an object " + key);
        }
        return object.getAsJsonObject(key);
    }

    public Region inputRegionFor(int x, int y, int z) {
        for (Region region : inputRegions) {
            if (region.contains(x, y, z)) {
                return region;
            }
        }
        return null;
    }

    public boolean tracksCartType(String type) {
        return cartTypes.contains(type);
    }

    public boolean isAgentOperator(String uuid) {
        if (uuid == null) {
            return false;
        }
        if (agentUuids.isEmpty()) {
            return true;
        }
        return agentUuids.contains(uuid.toLowerCase(Locale.ROOT));
    }

    public String resolvedOutputFile() {
        return outputFile.isBlank() ? "audit-" + safeName(runId) + ".jsonl" : outputFile;
    }

    public static String safeName(String value) {
        StringBuilder builder = new StringBuilder(value.length());
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            builder.append(Character.isLetterOrDigit(character) || character == '-' || character == '_' || character == '.'
                    ? character
                    : '_');
        }
        return builder.toString();
    }

    /** The config without the test-side private fields that are not evidence. */
    public JsonObject evidenceJson() {
        JsonObject object = new JsonObject();
        object.addProperty("runId", runId);
        object.addProperty("instanceId", instanceId);
        object.addProperty("dimension", dimension);
        object.addProperty("outputDir", outputDir);
        object.addProperty("outputFile", resolvedOutputFile());
        object.addProperty("maxBytes", maxBytes);
        object.addProperty("sampleIntervalTicks", sampleIntervalTicks);
        object.addProperty("correlationWindowTicks", correlationWindowTicks);
        object.addProperty("provenanceKind", provenanceKind);
        object.addProperty("provenanceReference", provenanceReference);
        object.addProperty("snapshotId", snapshotId);
        object.addProperty("snapshotHash", snapshotHash);
        object.addProperty("cartTypes", String.join(",", cartTypes));
        object.addProperty("agentUuids", String.join(",", agentUuids));
        object.addProperty("sha256", sha256);
        object.addProperty("configPath", configPath.toString());
        return object;
    }
}
