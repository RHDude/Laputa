package app.laputa;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * Decides what to tell the owner. Knows nothing about Android: works through three interfaces,
 * so the same logic serves the background job, the persistent service and JVM tests.
 */
public final class Checker {
    public interface Http {
        Net.Resp get(JSONObject hub, String path, int timeoutMs) throws Exception;
    }

    public interface Alerts {
        /** quiet: update silently (an all-clear replaces the alert with the same id). act: what can be done from the notification. */
        void show(int id, String channel, String title, String text, String node, boolean quiet, JSONObject act);

        void status(String text, boolean bad);

        boolean online();

        T tr();
    }

    public interface KV {
        String get(String key);

        void put(String key, String value);
    }

    public static final String CH_ALERT = "alerts";
    public static final String CH_WARN = "warn";
    public static final String CH_INFO = "info";
    public static final int ID_LOST = 7;
    static final long LOST_AFTER_MS = 180000L;
    private static final Object LOCK = new Object();

    private final Http http;
    private final Alerts alerts;
    private final KV kv;

    public Checker(Http http, Alerts alerts, KV kv) {
        this.http = http;
        this.alerts = alerts;
        this.kv = kv;
    }

    static int idFor(String node, String key) {
        int h = (node + "|" + key).hashCode() & 0x7fffffff;
        return 1000 + h % 2000000000;
    }

    /** Event kind, same as in ui/src/fmt.js: the user picks what to be notified about by it. */
    static String kindOf(String type) {
        if (type == null) return "other";
        if (type.startsWith("node_") || type.equals("group_down") || type.equals("link_lost") || type.startsWith("hub_")) return "down";
        if (type.startsWith("svc_") || type.equals("boot_missing")) return "svc";
        if (type.startsWith("disk_")) return "disk";
        if (type.startsWith("backup_")) return "backup";
        if (type.equals("ssh_login")) return "ssh";
        if (type.startsWith("reboot") || type.equals("maint_reboot")) return "reboot";
        return "other";
    }

    /** sound, quiet or off: the node's own setting, else the general one, else sound. */
    String level(JSONObject e) {
        String kind = kindOf(e.optString("type"));
        try {
            String raw = kv.get("cfg");
            JSONObject set = raw == null || raw.length() == 0 ? null : new JSONObject(raw).optJSONObject("settings");
            if (set == null) return "sound";
            JSONObject byNode = set.optJSONObject("notifyNode");
            JSONObject own = byNode == null ? null : byNode.optJSONObject(e.optString("node"));
            if (own != null && own.has(kind)) return own.optString(kind, "sound");
            JSONObject all = set.optJSONObject("notify");
            if (all != null && all.has(kind)) return all.optString(kind, "sound");
        } catch (Exception ignored) {
        }
        return "sound";
    }

    static String channelFor(String sev) {
        if ("crit".equals(sev)) return CH_ALERT;
        if ("warn".equals(sev)) return CH_WARN;
        return CH_INFO;
    }

    private JSONObject load() {
        try {
            String s = kv.get("chk");
            if (s != null && s.length() > 0) return new JSONObject(s);
        } catch (Exception ignored) {
        }
        return new JSONObject();
    }

    public static String summaryText(T tr, JSONObject sum) {
        JSONArray down = sum.optJSONArray("down");
        int nd = down == null ? 0 : down.length();
        int failed = sum.optInt("failed", 0);
        int total = sum.optInt("total", 0);
        if (nd == 1) return tr.t("j.st.down1", "name", down.optJSONObject(0).optString("name"));
        if (nd > 1) return tr.t("j.st.downN", "n", nd);
        if (failed == 1) return tr.t("j.st.svc1");
        if (failed > 1) return tr.t("j.st.svcN", "n", failed);
        if (total == 0) return tr.t("j.st.empty");
        return total == 1 ? tr.t("j.st.ok1") : tr.t("j.st.okN", "n", total);
    }

    static JSONObject act(JSONObject e) {
        JSONObject a = new JSONObject();
        JSONObject p = e.optJSONObject("p");
        try {
            a.put("type", e.optString("type"));
            a.put("sev", e.optString("sev"));
            a.put("node", e.optString("node"));
            if (p != null) {
                a.put("name", p.optString("name", e.optString("node")));
                a.put("unit", p.optString("unit", ""));
                a.put("label", p.optString("label", ""));
            }
        } catch (Exception ignored) {
        }
        return a;
    }

    /**
     * One pass over the watchmen. waitSec: how long the watchman may hold the connection waiting for events.
     * Returns true if any watchman answered.
     */
    public boolean pass(JSONArray hubs, int waitSec) {
        if (hubs == null || hubs.length() == 0) return false;
        JSONObject st = load();
        String lastUrl = st.optString("hub", "");
        int start = 0;
        for (int i = 0; i < hubs.length(); i++) {
            if (hubs.optJSONObject(i) != null && lastUrl.equals(hubs.optJSONObject(i).optString("url"))) start = i;
        }
        String firstError = null;
        for (int k = 0; k < hubs.length(); k++) {
            JSONObject hub = hubs.optJSONObject((start + k) % hubs.length());
            if (hub == null) continue;
            String url = hub.optString("url");
            boolean same = url.equals(lastUrl);
            long after = same ? st.optLong("seq", 0) : 0;
            int wait = same ? waitSec : 0; // a new watchman: sync first, nothing to wait for
            try {
                Net.Resp r = http.get(hub, "/v1/events?after=" + after + "&wait=" + wait, (wait + 25) * 1000);
                if (r.status != 200) throw new Exception(alerts.tr().t("err.hub_status", "status", r.status));
                handle(url, new JSONObject(r.body));
                return true;
            } catch (Exception e) {
                if (firstError == null) firstError = String.valueOf(e.getMessage());
            }
        }
        lost(firstError);
        return false;
    }

    private void handle(String url, JSONObject r) throws Exception {
        synchronized (LOCK) {
            JSONObject st = load(); // re-read: a parallel pass may have handled the same events
            T tr = alerts.tr();
            long now = System.currentTimeMillis();
            boolean same = url.equals(st.optString("hub", ""));
            long seen = same ? st.optLong("seq", 0) : -1;
            long seq = r.optLong("seq", 0);
            JSONObject sum = r.optJSONObject("summary");
            if (sum == null) sum = new JSONObject();
            String hubName = r.optJSONObject("hub") == null ? "" : r.optJSONObject("hub").optString("name");

            JSONArray probs = r.optJSONArray("problems");
            if (probs == null) probs = new JSONArray();
            if (seen >= 0 && seq >= seen) {
                // Normal pass: show new events. If several piled up for one subject
                // (the phone was off), only the last matters: an alert that is already cleared must not ring.
                JSONArray ev = r.optJSONArray("events");
                java.util.LinkedHashMap<Integer, JSONObject> last = new java.util.LinkedHashMap<Integer, JSONObject>();
                for (int i = 0; ev != null && i < ev.length(); i++) {
                    JSONObject e = ev.optJSONObject(i);
                    if (e == null || e.optLong("seq", 0) <= seen || "info".equals(e.optString("sev"))) continue;
                    int id = idFor(e.optString("node"), e.optString("key", "node"));
                    last.remove(id);
                    last.put(id, e);
                }
                for (java.util.Map.Entry<Integer, JSONObject> en : last.entrySet()) {
                    JSONObject e = en.getValue();
                    String sev = e.optString("sev");
                    String lvl = level(e);
                    if ("off".equals(lvl)) continue;   // only in the Events tab
                    String[] x = tr.ev(e);
                    boolean quiet = "ok".equals(sev) || "quiet".equals(lvl);
                    alerts.show(en.getKey(), quiet && !"ok".equals(sev) ? CH_INFO : channelFor(sev), x[0], x[1], e.optString("node"), quiet, act(e));
                }
            } else {
                // First contact with this watchman, or switching to it: don't replay history,
                // only report current problems we have not reported yet.
                JSONArray known = st.optJSONArray("known");
                for (int i = 0; i < probs.length(); i++) {
                    JSONObject p = probs.optJSONObject(i);
                    if (p == null) continue;
                    String pid = p.optString("node") + "|" + p.optString("key", "node");
                    boolean was = false;
                    for (int j = 0; known != null && j < known.length(); j++) {
                        if (pid.equals(known.optString(j))) was = true;
                    }
                    String lvl = level(p);
                    if (!was && !"off".equals(lvl)) {
                        String[] x = tr.ev(p);
                        String from = tr.t("j.from_hub", "hub", hubName);
                        boolean quiet = "quiet".equals(lvl);
                        alerts.show(idFor(p.optString("node"), p.optString("key", "node")), quiet ? CH_INFO : channelFor(p.optString("sev")), x[0],
                                x[1].length() > 0 ? x[1] + " " + from : from, p.optString("node"), quiet, act(p));
                    }
                }
            }
            JSONArray ids = new JSONArray();
            for (int i = 0; i < probs.length(); i++) {
                JSONObject p = probs.optJSONObject(i);
                if (p != null) ids.put(p.optString("node") + "|" + p.optString("key", "node"));
            }
            JSONArray down = sum.optJSONArray("down");
            if (st.optBoolean("lostNotified", false)) {
                alerts.show(ID_LOST, CH_INFO, tr.t("j.hubs_back"), tr.t("j.hubs_back.x", "d", tr.dur((now - st.optLong("lostSince", now)) / 1000, false)), null, true, null);
            }
            st.put("hub", url);
            st.put("seq", seq);
            st.put("known", ids);
            st.put("lostSince", 0);
            st.put("lostNotified", false);
            st.put("last", now);
            boolean bad = (down != null && down.length() > 0) || sum.optInt("failed", 0) > 0;
            st.put("text", summaryText(tr, sum));
            st.put("sum", sum);
            st.put("bad", bad);
            st.put("linkOk", true);
            kv.put("chk", st.toString());
            alerts.status(summaryText(tr, sum), bad);
        }
    }

    private void lost(String error) {
        synchronized (LOCK) {
            try {
                JSONObject st = load();
                T tr = alerts.tr();
                long now = System.currentTimeMillis();
                long since = st.optLong("lostSince", 0);
                if (since == 0) {
                    since = now;
                    st.put("lostSince", now);
                }
                if (!alerts.online()) {
                    // the phone is offline: not the watchmen's fault, start counting once the network is back
                    st.put("lostSince", 0);
                    alerts.status(tr.t("j.offline"), false);
                } else {
                    if (!st.optBoolean("lostNotified", false) && now - since >= LOST_AFTER_MS) {
                        alerts.show(ID_LOST, CH_WARN, tr.t("j.hubs_lost"), tr.t("j.hubs_lost.x", "d", tr.dur((now - since) / 1000, false)), null, false, null);
                        st.put("lostNotified", true);
                    }
                    alerts.status(tr.t("j.nolink"), true);
                }
                if (error != null) st.put("error", error);
                st.put("linkOk", false);
                kv.put("chk", st.toString());
            } catch (Exception ignored) {
            }
        }
    }
}
