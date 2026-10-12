package app.laputa;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

import org.json.JSONArray;
import org.json.JSONObject;

/** Notification buttons: Restart a service, Mute a node for an hour. Sent through the first watchman that answers. */
public final class ActionReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        final Context c = context.getApplicationContext();
        final String what = intent.getStringExtra("what");
        final int nid = intent.getIntExtra("nid", 0);
        final JSONObject act;
        try {
            act = new JSONObject(intent.getStringExtra("act"));
        } catch (Exception e) {
            return;
        }
        L.init(c);
        final PendingResult pr = goAsync();
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    work(c, what, nid, act);
                } catch (Throwable t) {
                    L.i("notification action: " + t);
                } finally {
                    pr.finish();
                }
            }
        }, "laputa-action").start();
    }

    private static void work(Context c, String what, int nid, JSONObject act) throws Exception {
        Notifier n = new Notifier(c);
        T tr = n.tr();
        String node = act.optString("node"), name = act.optString("name", node), unit = act.optString("unit"), label = act.optString("label", unit);
        boolean restart = "restart".equals(what);
        String path;
        JSONObject body = new JSONObject();
        body.put("node", node);
        if (restart) {
            n.show(nid, Checker.CH_INFO, tr.t("j.act.restarting", "label", label), name, node, true, null);
            path = "/v1/cmd";
            body.put("type", "svc.restart");
            body.put("args", new JSONObject().put("unit", unit));
        } else {
            path = "/v1/mute";
            body.put("minutes", 60);
        }
        JSONArray hubs = new Store(c).hubs();
        String error = tr.t("err.nohub");
        for (int i = 0; i < hubs.length(); i++) {
            JSONObject h = hubs.optJSONObject(i);
            if (h == null) continue;
            try {
                Net.Resp r = Net.request(h.optString("url").replaceAll("/+$", "") + path, h.optString("pin"), h.optString("token"), "POST", body.toString(), restart ? 80000 : 15000);
                if (r.status != 200) {
                    error = tr.t("err.hub_status", "status", r.status);
                    continue;
                }
                JSONObject res = new JSONObject(r.body);
                if (res.optBoolean("ok")) {
                    L.i((restart ? "restart " + unit : "muted node " + node) + " from notification");
                    n.show(nid, Checker.CH_INFO, restart ? tr.t("j.act.restarted", "label", label) : tr.t("j.act.muted", "name", name), restart ? name : "", node, true, null);
                    return;
                }
                error = tr.err(res);
                if (!"hub_no_node".equals(res.optString("code"))) break;   // the node's answer; the other watchman would say the same
            } catch (Exception e) {
                error = tr.exc(e);
            }
        }
        n.show(nid, Checker.CH_WARN, tr.t("j.act.failed", "err", error), name, node, true, null);
    }
}
