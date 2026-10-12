package app.laputa;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** После включения телефона и после обновления приложения возвращает проверки и постоянную службу. */
public final class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context c, Intent intent) {
        L.init(c);
        L.i("старт после " + (intent == null ? "?" : intent.getAction()));
        WatchJob.schedule(c);
        LiveService.apply(c);
    }
}
