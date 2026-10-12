package app.laputa;

import android.app.job.JobInfo;
import android.app.job.JobParameters;
import android.app.job.JobScheduler;
import android.app.job.JobService;
import android.content.ComponentName;
import android.content.Context;

/**
 * Scheduled check every 15 minutes. In 15-minute mode it is the only source of alerts;
 * in instant mode it is a safety net in case the system kills the persistent service.
 */
public final class WatchJob extends JobService {
    private static final int JOB_PERIODIC = 1;
    private static final int JOB_RETRY = 2;

    public static void schedule(Context c) {
        try {
            JobScheduler js = (JobScheduler) c.getSystemService(Context.JOB_SCHEDULER_SERVICE);
            boolean have = false;
            for (JobInfo j : js.getAllPendingJobs()) {
                if (j.getId() == JOB_PERIODIC) have = true;
            }
            if (have) return;
            JobInfo job = new JobInfo.Builder(JOB_PERIODIC, new ComponentName(c, WatchJob.class))
                    .setPeriodic(15 * 60 * 1000L)
                    .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
                    .setPersisted(true)
                    .build();
            js.schedule(job);
        } catch (Throwable t) {
            L.i("check schedule: " + t);
        }
    }

    private static void retrySoon(Context c) {
        try {
            JobScheduler js = (JobScheduler) c.getSystemService(Context.JOB_SCHEDULER_SERVICE);
            js.schedule(new JobInfo.Builder(JOB_RETRY, new ComponentName(c, WatchJob.class))
                    .setMinimumLatency(3 * 60 * 1000L)
                    .setOverrideDeadline(6 * 60 * 1000L)
                    .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
                    .build());
        } catch (Throwable ignored) {
        }
    }

    @Override
    public boolean onStartJob(final JobParameters params) {
        L.init(this);
        final Context c = getApplicationContext();
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    Store store = new Store(c);
                    if (store.hubs().length() > 0) {
                        boolean liveFresh = store.live() && LiveService.running && System.currentTimeMillis() - LiveService.lastOk < 6 * 60 * 1000L;
                        if (!liveFresh) {
                            boolean ok = new Checker(Notifier.HTTP, new Notifier(c), store).pass(store.hubs(), 0);
                            if (!ok && params.getJobId() == JOB_PERIODIC) retrySoon(c);
                            if (store.live() && !LiveService.running) LiveService.apply(c);
                        }
                    }
                } catch (Throwable t) {
                    L.i("scheduled check: " + t);
                }
                jobFinished(params, false);
            }
        }, "laputa-job").start();
        return true;
    }

    @Override
    public boolean onStopJob(JobParameters params) {
        return false;
    }
}
