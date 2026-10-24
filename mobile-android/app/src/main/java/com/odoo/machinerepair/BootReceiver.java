package com.odoo.machinerepair;

import android.Manifest;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.os.Build;
import android.util.Log;

/**
 * 开机后尝试把后台上报服务拉起来。
 *
 * 注意：Android 12+ 对「后台启动前台服务」有限制，部分系统/厂商会直接拒绝；
 * 这里 try/catch，失败时用户下次打开 App 仍会自动启动，不影响已有数据。
 * 如果用户「强行停止」过 App，系统不会发 BOOT_COMPLETED 给该 App，必须手动打开一次。
 */
public class BootReceiver extends BroadcastReceiver {

    private static final String TAG = "MR-BootReceiver";

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null) {
            return;
        }
        String action = intent.getAction();
        if (!Intent.ACTION_BOOT_COMPLETED.equals(action)
                && !Intent.ACTION_MY_PACKAGE_REPLACED.equals(action)) {
            return;
        }
        SharedPreferences prefs = context.getSharedPreferences(
                LocationService.PREFS, Context.MODE_PRIVATE);
        if (prefs.getString(LocationService.PREF_COOKIE, "").isEmpty()) {
            return; // 从未登录过
        }
        boolean permitted =
                context.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)
                        == PackageManager.PERMISSION_GRANTED
                || context.checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION)
                        == PackageManager.PERMISSION_GRANTED;
        if (!permitted) {
            return;
        }
        try {
            Intent service = new Intent(context, LocationService.class);
            if (Build.VERSION.SDK_INT >= 26) {
                context.startForegroundService(service);
            } else {
                context.startService(service);
            }
        } catch (Exception e) {
            // Android 12+ 的 ForegroundServiceStartNotAllowedException 等
            Log.w(TAG, "start location service on boot failed", e);
        }
    }
}
