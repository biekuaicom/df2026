package com.odoo.machinerepair;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.location.Location;
import android.location.LocationListener;
import android.location.LocationManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.util.Log;
import android.widget.Toast;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * 后台上报位置的前台服务。
 *
 * 为什么需要它：WebView 里的 JS 在 App 切到后台/锁屏后会被系统暂停，
 * 只有原生前台服务（带常驻通知）才能继续拿 GPS 并上传。
 *
 * 认证：复用 WebView 的 session_id Cookie（MainActivity 在页面加载完成后写入
 * SharedPreferences），服务直接带 Cookie 调 /machine_repair/mobile/location/native。
 * 这个接口 csrf=False，但要求自定义头 X-MR-Native，跨站表单伪造不了。
 */
public class LocationService extends Service implements LocationListener {

    private static final String TAG = "MR-LocationService";
    public static final String CHANNEL_ID = "mr_location_status";
    public static final int NOTIFICATION_ID = 4201;
    public static final String API_URL =
            "https://df2026.213579.com/machine_repair/mobile/location/native";
    public static final String PREFS = "mr_location";
    public static final String PREF_COOKIE = "cookie";

    private LocationManager locationManager;
    private ExecutorService executor;
    private Handler mainHandler;
    private volatile long intervalMs = 60_000L;
    private volatile float minDistance = 30f;
    private volatile long lastSentAt = 0L;
    private volatile double lastLat = 0d;
    private volatile double lastLng = 0d;
    private volatile boolean hasLast = false;
    private volatile boolean sending = false;

    @Override
    public void onCreate() {
        super.onCreate();
        mainHandler = new Handler(Looper.getMainLooper());
        executor = Executors.newSingleThreadExecutor();
        createChannel();
        if (!hasLocationPermission()) {
            Log.w(TAG, "no location permission, stop");
            stopSelf();
            return;
        }
        try {
            startForegroundCompat(buildNotification("位置上报中", "正在获取定位…"));
        } catch (Exception e) {
            Log.e(TAG, "startForeground failed", e);
            stopSelf();
            return;
        }
        locationManager = (LocationManager) getSystemService(Context.LOCATION_SERVICE);
        startUpdates();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (!hasLocationPermission()) {
            stopSelf();
            return START_NOT_STICKY;
        }
        // 页面加载完成后 MainActivity 会再次 startService，让服务读到新 Cookie
        startUpdates();
        return START_STICKY;
    }

    private void startForegroundCompat(Notification notification) {
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIFICATION_ID, notification,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION);
        } else {
            startForeground(NOTIFICATION_ID, notification);
        }
    }

    private boolean hasLocationPermission() {
        return checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)
                == PackageManager.PERMISSION_GRANTED
                || checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION)
                == PackageManager.PERMISSION_GRANTED;
    }

    private boolean hasBackgroundLocation() {
        if (Build.VERSION.SDK_INT < 29) {
            return true;
        }
        return checkSelfPermission(Manifest.permission.ACCESS_BACKGROUND_LOCATION)
                == PackageManager.PERMISSION_GRANTED;
    }

    private void startUpdates() {
        if (locationManager == null || !hasLocationPermission()) {
            return;
        }
        try {
            locationManager.removeUpdates(this);
            boolean any = false;
            if (locationManager.isProviderEnabled(LocationManager.GPS_PROVIDER)) {
                locationManager.requestLocationUpdates(LocationManager.GPS_PROVIDER,
                        intervalMs, minDistance, this, Looper.getMainLooper());
                any = true;
            }
            if (locationManager.isProviderEnabled(LocationManager.NETWORK_PROVIDER)) {
                locationManager.requestLocationUpdates(LocationManager.NETWORK_PROVIDER,
                        intervalMs, minDistance, this, Looper.getMainLooper());
                any = true;
            }
            if (!any) {
                updateNotification("手机定位未开启", "请打开 GPS/定位服务");
            } else {
                updateNotification(notificationTitle(),
                        hasBackgroundLocation()
                                ? "后台定位已开启，关闭 App 仍会继续上报"
                                : "后台权限未开启：关闭 App 后可能停止（请在设置选「始终允许」）");
            }
        } catch (SecurityException e) {
            Log.e(TAG, "requestLocationUpdates denied", e);
            updateNotification("定位权限不足", "请在设置中允许定位（建议「始终允许」）");
        } catch (Exception e) {
            Log.e(TAG, "requestLocationUpdates error", e);
        }
    }

    private String notificationTitle() {
        return hasBackgroundLocation() ? "位置上报中" : "位置上报（前台）";
    }

    @Override
    public void onLocationChanged(Location location) {
        if (location == null) {
            return;
        }
        long now = System.currentTimeMillis();
        float[] result = new float[1];
        boolean moved = true;
        if (hasLast) {
            Location.distanceBetween(lastLat, lastLng, location.getLatitude(),
                    location.getLongitude(), result);
            moved = result[0] >= minDistance;
        }
        if (hasLast && !moved && now - lastSentAt < intervalMs) {
            return;
        }
        if (sending) {
            return;
        }
        sendLocation(location);
    }

    private void sendLocation(final Location location) {
        sending = true;
        final String cookie = getSharedPreferences(PREFS, MODE_PRIVATE)
                .getString(PREF_COOKIE, "");
        executor.execute(new Runnable() {
            @Override
            public void run() {
                HttpURLConnection conn = null;
                try {
                    if (cookie == null || cookie.isEmpty()) {
                        updateNotification("尚未登录", "请打开 App 登录一次");
                        return;
                    }
                    String body = "lat=" + URLEncoder.encode(String.valueOf(location.getLatitude()), "UTF-8")
                            + "&lng=" + URLEncoder.encode(String.valueOf(location.getLongitude()), "UTF-8")
                            + "&accuracy=" + URLEncoder.encode(String.valueOf(location.getAccuracy()), "UTF-8")
                            + "&src=android";
                    URL url = new URL(API_URL);
                    conn = (HttpURLConnection) url.openConnection();
                    conn.setRequestMethod("POST");
                    conn.setConnectTimeout(15000);
                    conn.setReadTimeout(15000);
                    conn.setDoOutput(true);
                    conn.setUseCaches(false);
                    conn.setInstanceFollowRedirects(false);
                    conn.setRequestProperty("Content-Type",
                            "application/x-www-form-urlencoded; charset=UTF-8");
                    conn.setRequestProperty("Accept", "application/json");
                    conn.setRequestProperty("Accept-Language", "zh-CN,zh;q=0.9");
                    conn.setRequestProperty("User-Agent",
                            "Mozilla/5.0 (Linux; Android) MachineRepairNative/1.12");
                    conn.setRequestProperty("Cookie", cookie);
                    conn.setRequestProperty("X-MR-Native", "1");
                    OutputStream os = conn.getOutputStream();
                    os.write(body.getBytes(StandardCharsets.UTF_8));
                    os.close();

                    int code = conn.getResponseCode();
                    String text = readAll(code >= 200 && code < 300
                            ? conn.getInputStream() : conn.getErrorStream());
                    if (code == 401 || code == 403) {
                        updateNotification("需要重新登录", "登录已过期，请打开 App 登录一次");
                        return;
                    }
                    if (code != 200) {
                        updateNotification("位置上报失败", "服务器 HTTP " + code + "，稍后重试");
                        return;
                    }
                    JSONObject obj = new JSONObject(text);
                    if (!obj.optBoolean("ok", false)) {
                        updateNotification("位置上报失败", obj.optString("error", "服务器返回错误"));
                        return;
                    }
                    updateCookieFromResponse(conn);

                    long newInterval = Math.max(15, obj.optInt("interval", 60)) * 1000L;
                    float newDistance = (float) Math.max(5.0, obj.optDouble("min_distance", 30.0));
                    boolean changed = newInterval != intervalMs
                            || Math.abs(newDistance - minDistance) > 0.5f;
                    intervalMs = newInterval;
                    minDistance = newDistance;

                    lastSentAt = System.currentTimeMillis();
                    lastLat = location.getLatitude();
                    lastLng = location.getLongitude();
                    hasLast = true;
                    String time = new SimpleDateFormat("HH:mm:ss", Locale.getDefault())
                            .format(new Date(lastSentAt));
                    String state = obj.optString("state", "");
                    if ("inside".equals(state)) {
                        state = "在工作位置";
                    } else if ("outside".equals(state)) {
                        state = "离开工作位置";
                    } else if ("unassigned".equals(state)) {
                        state = "未分配工作位置";
                    } else {
                        state = "已上报";
                    }
                    updateNotification(notificationTitle(), "最后上报 " + time + " · " + state);
                    if (changed) {
                        mainHandler.post(new Runnable() {
                            @Override
                            public void run() {
                                startUpdates();
                            }
                        });
                    }
                } catch (Exception e) {
                    Log.w(TAG, "send failed", e);
                    updateNotification("位置上报失败", "网络异常，稍后自动重试");
                } finally {
                    if (conn != null) {
                        conn.disconnect();
                    }
                    sending = false;
                }
            }
        });
    }

    private void updateCookieFromResponse(HttpURLConnection conn) {
        try {
            List<String> setCookies = conn.getHeaderFields().get("Set-Cookie");
            if (setCookies == null) {
                return;
            }
            for (String value : setCookies) {
                if (value == null || !value.contains("session_id=")) {
                    continue;
                }
                String sid = value.substring(value.indexOf("session_id="));
                int end = sid.indexOf(';');
                if (end > 0) {
                    sid = sid.substring(0, end);
                }
                SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
                String old = prefs.getString(PREF_COOKIE, "");
                if (!old.contains(sid)) {
                    prefs.edit().putString(PREF_COOKIE, sid).apply();
                }
            }
        } catch (Exception e) {
            Log.w(TAG, "parse Set-Cookie failed", e);
        }
    }

    private static String readAll(InputStream input) throws Exception {
        if (input == null) {
            return "";
        }
        BufferedReader reader = new BufferedReader(
                new InputStreamReader(input, StandardCharsets.UTF_8));
        StringBuilder builder = new StringBuilder();
        String line;
        while ((line = reader.readLine()) != null) {
            builder.append(line);
        }
        reader.close();
        return builder.toString();
    }

    private void createChannel() {
        if (Build.VERSION.SDK_INT < 26) {
            return;
        }
        NotificationManager manager =
                (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        if (manager == null) {
            return;
        }
        NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID, "位置上报", NotificationManager.IMPORTANCE_LOW);
        channel.setDescription("后台上报位置，用于确认是否在工作位置");
        channel.setShowBadge(false);
        manager.createNotificationChannel(channel);
    }

    private Notification buildNotification(String title, String text) {
        Intent open = new Intent(this, MainActivity.class);
        open.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= 23) {
            flags |= PendingIntent.FLAG_IMMUTABLE;
        }
        PendingIntent pending = PendingIntent.getActivity(this, 0, open, flags);
        Notification.Builder builder;
        if (Build.VERSION.SDK_INT >= 26) {
            builder = new Notification.Builder(this, CHANNEL_ID);
        } else {
            builder = new Notification.Builder(this);
        }
        builder.setContentTitle(title)
                .setContentText(text)
                .setSmallIcon(android.R.drawable.ic_menu_mylocation)
                .setContentIntent(pending)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setShowWhen(false)
                .setCategory(Notification.CATEGORY_SERVICE);
        if (Build.VERSION.SDK_INT >= 31) {
            builder.setForegroundServiceBehavior(Notification.FOREGROUND_SERVICE_IMMEDIATE);
        }
        return builder.build();
    }

    private void updateNotification(String title, String text) {
        try {
            NotificationManager manager =
                    (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
            if (manager != null) {
                manager.notify(NOTIFICATION_ID, buildNotification(title, text));
            }
        } catch (Exception e) {
            Log.w(TAG, "updateNotification failed", e);
        }
    }

    @Override
    public void onProviderEnabled(String provider) {
        startUpdates();
    }

    @Override
    public void onProviderDisabled(String provider) {
        updateNotification("手机定位未开启", "请打开 GPS/定位服务");
    }

    @Override
    public void onStatusChanged(String provider, int status, Bundle extras) {
        // no-op
    }

    @Override
    public void onDestroy() {
        try {
            if (locationManager != null) {
                locationManager.removeUpdates(this);
            }
        } catch (Exception ignored) {
        }
        if (executor != null) {
            executor.shutdownNow();
        }
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
