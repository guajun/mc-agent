package dev.mcagent.audit;

import com.google.gson.JsonObject;

/** Per-hook counters proving the audit stays cheap enough to keep installed. */
public final class HookStats {
    public final String hook;
    public long calls;
    public long totalNanos;
    public long maxNanos;

    public HookStats(String hook) {
        this.hook = hook;
    }

    public void record(long nanos) {
        calls++;
        totalNanos += nanos;
        if (nanos > maxNanos) {
            maxNanos = nanos;
        }
    }

    public JsonObject toJson() {
        JsonObject object = new JsonObject();
        object.addProperty("calls", calls);
        object.addProperty("totalNanos", totalNanos);
        object.addProperty("maxNanos", maxNanos);
        object.addProperty("avgNanos", calls == 0 ? 0.0 : (double) totalNanos / calls);
        return object;
    }
}
