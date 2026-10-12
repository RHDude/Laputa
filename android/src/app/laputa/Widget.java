package app.laputa;

import android.app.PendingIntent;
import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.widget.RemoteViews;

import org.json.JSONObject;

/** Home screen widget: one line about the nodes. Uses the last check's data, never goes online itself. */
public final class Widget extends AppWidgetProvider {
    @Override
    public void onUpdate(Context c, AppWidgetManager m, int[] ids) {
        update(c);
    }

    public static void update(Context context) {
        try {
            Context c = context.getApplicationContext();
            AppWidgetManager m = AppWidgetManager.getInstance(c);
            int[] ids = m.getAppWidgetIds(new ComponentName(c, Widget.class));
            if (ids == null || ids.length == 0) return;
            Store store = new Store(c);
            T tr = Notifier.tr(c);
            String title, sub;
            int color;
            JSONObject st = new JSONObject();
            try {
                String s = store.get("chk");
                if (s.length() > 0) st = new JSONObject(s);
            } catch (Exception ignored) {
            }
            long last = st.optLong("last", 0);
            JSONObject sum = st.optJSONObject("sum");
            if (store.hubs().length() == 0) {
                title = tr.t("j.w.nohub");
                sub = "";
                color = 0xFF98A4C3;
            } else if (sum == null) {
                title = tr.t("j.connecting");
                sub = "";
                color = 0xFF98A4C3;
            } else if (!st.optBoolean("linkOk", true)) {
                title = tr.t("j.nolink");
                sub = last > 0 ? tr.t("j.w.old", "at", T.clock(last)) : "";
                color = 0xFFF0B35A;
            } else {
                title = Checker.summaryText(tr, sum);
                sub = tr.t("j.w.at", "at", T.clock(last));
                color = st.optBoolean("bad") ? 0xFFF2647F : 0xFF86C98A;
            }
            RemoteViews v = new RemoteViews(c.getPackageName(), R.layout.widget);
            v.setTextViewText(R.id.w_title, title);
            v.setTextViewText(R.id.w_sub, sub);
            v.setTextColor(R.id.w_dot, color);
            Intent i = new Intent(c, MainActivity.class);
            i.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
            v.setOnClickPendingIntent(R.id.w_root, PendingIntent.getActivity(c, 77, i, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE));
            m.updateAppWidget(ids, v);
        } catch (Throwable t) {
            L.i("widget: " + t);
        }
    }
}
