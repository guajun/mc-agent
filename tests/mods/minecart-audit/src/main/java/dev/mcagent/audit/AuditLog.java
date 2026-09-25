package dev.mcagent.audit;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.io.BufferedWriter;
import java.io.IOException;
import java.io.RandomAccessFile;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.Map;
import java.util.TreeMap;

/**
 * Append-only JSONL evidence file plus a small status file.
 *
 * <p>Every event is flushed before its hook returns, so a crash or a killed
 * server still leaves a readable, ordered prefix. Nothing here depends on the
 * Bridge ring buffer: this is the test side's own file.
 */
public final class AuditLog {
    private final Gson gson = new GsonBuilder().disableHtmlEscaping().serializeNulls().create();
    private final Path dir;
    private final Path file;
    private final Path statusFile;
    private final Path latestFile;
    private final Writer writer;
    private final long maxBytes;
    private final String runId;
    private final String instanceId;
    private final String sessionId;
    private final long bytesBeforeSession;
    private final long initialSeq;
    private final Map<String, Long> counters = new TreeMap<>();
    private long seq;
    private long bytes;
    private long events;
    private boolean truncated;
    private boolean writeFailed;
    private boolean closed;
    private long lastStatusWall;
    private String phase = "bootstrap";
    private int tick;

    public AuditLog(Path dir, String runId, String instanceId, String fileName, long maxBytes) throws IOException {
        this.dir = dir;
        this.runId = runId;
        this.instanceId = instanceId;
        this.maxBytes = maxBytes;
        Files.createDirectories(dir);
        this.file = dir.resolve(fileName);
        this.sessionId = "s" + System.currentTimeMillis();
        this.initialSeq = lastSeq(file);
        this.bytesBeforeSession = Files.isRegularFile(file) ? Files.size(file) : 0L;
        this.seq = initialSeq;
        // maxBytes caps the whole file: a run restarted several times must not
        // reset the budget on every session.
        this.bytes = bytesBeforeSession;
        this.statusFile = dir.resolve("status-" + AuditConfig.safeName(runId) + ".json");
        this.latestFile = dir.resolve("latest.json");
        this.writer = Files.newBufferedWriter(
                file,
                StandardCharsets.UTF_8,
                StandardOpenOption.CREATE,
                StandardOpenOption.APPEND);
    }

    public Path file() {
        return file;
    }

    public Path statusFile() {
        return statusFile;
    }

    public String runId() {
        return runId;
    }

    public String instanceId() {
        return instanceId;
    }

    public String sessionId() {
        return sessionId;
    }

    public long bytesBeforeSession() {
        return bytesBeforeSession;
    }

    public long initialSeq() {
        return initialSeq;
    }

    /** Last complete sequence number in a log that this session appends to. */
    private static long lastSeq(Path file) {
        if (!Files.isRegularFile(file)) {
            return 0;
        }
        try (RandomAccessFile handle = new RandomAccessFile(file.toFile(), "r")) {
            long length = handle.length();
            int chunk = (int) Math.min(length, 65536);
            byte[] bytes = new byte[chunk];
            handle.seek(length - chunk);
            handle.readFully(bytes);
            String[] lines = new String(bytes, StandardCharsets.UTF_8).split("\n");
            for (int index = lines.length - 1; index >= 0; index--) {
                String line = lines[index].trim();
                if (line.isEmpty()) {
                    continue;
                }
                try {
                    JsonObject object = JsonParser.parseString(line).getAsJsonObject();
                    if (object.has("seq")) {
                        return object.get("seq").getAsLong();
                    }
                } catch (RuntimeException ignored) {
                    // a torn first line of the window: keep looking upwards
                }
            }
        } catch (IOException error) {
            System.err.println("mcaudit: could not read previous seq from " + file + ": " + error);
        }
        return 0;
    }

    public synchronized long seq() {
        return seq;
    }

    public synchronized long bytes() {
        return bytes;
    }

    public synchronized boolean truncated() {
        return truncated;
    }

    public synchronized void setPhase(String phase) {
        this.phase = phase;
    }

    public synchronized void setTick(int tick) {
        this.tick = tick;
    }

    public synchronized JsonObject event(String type) {
        JsonObject object = new JsonObject();
        object.addProperty("type", type);
        return object;
    }

    public synchronized void count(String key) {
        counters.merge(key, 1L, Long::sum);
    }

    public synchronized long countOf(String key) {
        return counters.getOrDefault(key, 0L);
    }

    public synchronized JsonObject countersJson() {
        JsonObject object = new JsonObject();
        for (Map.Entry<String, Long> entry : counters.entrySet()) {
            object.addProperty(entry.getKey(), entry.getValue());
        }
        return object;
    }

    /** Append one event. Never throws into a game hook: a full disk drops lines. */
    public synchronized void append(JsonObject object) {
        if (closed || writeFailed) {
            return;
        }
        String type = object.has("type") ? object.get("type").getAsString() : "unknown";
        if (maxBytes > 0 && bytes >= maxBytes) {
            truncated = true;
            count("audit_truncated");
            return;
        }
        seq++;
        object.addProperty("seq", seq);
        if (!object.has("tick")) {
            object.addProperty("tick", tick);
        }
        if (!object.has("wall")) {
            object.addProperty("wall", System.currentTimeMillis());
        }
        if (!object.has("run")) {
            object.addProperty("run", runId);
        }
        if (!object.has("inst")) {
            object.addProperty("inst", instanceId);
        }
        if (!object.has("session")) {
            object.addProperty("session", sessionId);
        }
        if (!object.has("phase")) {
            object.addProperty("phase", phase);
        }
        String line = gson.toJson(object) + System.lineSeparator();
        try {
            writer.write(line);
            writer.flush();
        } catch (IOException error) {
            writeFailed = true;
            System.err.println("mcaudit: writing " + file + " failed: " + error);
            return;
        }
        bytes += line.getBytes(StandardCharsets.UTF_8).length;
        events++;
        count(type);
    }

    public synchronized JsonObject statusJson(JsonObject extra, boolean ended, String[] incomplete) {
        JsonObject object = new JsonObject();
        object.addProperty("run", runId);
        object.addProperty("inst", instanceId);
        object.addProperty("session", sessionId);
        object.addProperty("phase", phase);
        object.addProperty("seq", seq);
        object.addProperty("events", events);
        object.addProperty("bytes", bytes);
        object.addProperty("truncated", truncated);
        object.addProperty("writeFailed", writeFailed);
        object.addProperty("ended", ended);
        object.addProperty("updatedAtWall", System.currentTimeMillis());
        object.addProperty("updatedAtTick", tick);
        object.addProperty("log", file.toString());
        JsonArray reasons = new JsonArray();
        for (String reason : incomplete) {
            reasons.add(reason);
        }
        object.add("incomplete", reasons);
        object.add("counters", countersJson());
        if (extra != null) {
            for (Map.Entry<String, com.google.gson.JsonElement> entry : extra.entrySet()) {
                object.add(entry.getKey(), entry.getValue());
            }
        }
        return object;
    }

    /**
     * Write the status file, throttled unless forced. The status file is what
     * lets the test program notice a server that died without writing
     * {@code audit_end}.
     */
    public synchronized void writeStatus(JsonObject snapshot, boolean force) {
        long now = System.currentTimeMillis();
        if (!force && now - lastStatusWall < 1000) {
            return;
        }
        lastStatusWall = now;
        try {
            Files.writeString(statusFile, gson.toJson(snapshot), StandardCharsets.UTF_8);
            JsonObject latest = new JsonObject();
            latest.addProperty("run", runId);
            latest.addProperty("inst", instanceId);
            latest.addProperty("session", sessionId);
            latest.addProperty("log", file.toString());
            latest.addProperty("status", statusFile.toString());
            latest.addProperty("updatedAtWall", now);
            Files.writeString(latestFile, gson.toJson(latest), StandardCharsets.UTF_8);
        } catch (IOException error) {
            System.err.println("mcaudit: writing status " + statusFile + " failed: " + error);
        }
    }

    public synchronized void close() {
        if (closed) {
            return;
        }
        closed = true;
        try {
            writer.flush();
            writer.close();
        } catch (IOException error) {
            System.err.println("mcaudit: closing " + file + " failed: " + error);
        }
    }
}
