package app.laputa;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** Restores the checks and the persistent service after boot and after an app update. */
public final class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context c, Intent intent) {
        L.init(c);
        L.i("start after " + (intent == null ? "?" : intent.getAction()));
        WatchJob.schedule(c);
        LiveService.apply(c);
    }
}
