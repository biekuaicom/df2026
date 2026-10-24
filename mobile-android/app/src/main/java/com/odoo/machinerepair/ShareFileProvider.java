package com.odoo.machinerepair;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;

import java.io.File;
import java.io.FileNotFoundException;

/**
 * 把 App 缓存目录里的文件以 content:// 暴露给别的应用（微信 / QQ 转发视频用）。
 *
 * 为什么不直接用 Uri.fromFile：Android 7（API 24）起会抛 FileUriExposedException；
 * 项目又刻意不依赖 androidx（没有 FileProvider），所以自己写一个最小实现：
 *   content://com.odoo.machinerepair.files/share/<文件名>  ->  cache/share/<文件名>
 *
 * 只有拿到临时授权的接收方（Intent.FLAG_GRANT_READ_URI_PERMISSION）才能读，
 * provider 本身 exported=false。
 */
public class ShareFileProvider extends ContentProvider {

    public static final String AUTHORITY = "com.odoo.machinerepair.files";
    private static final String ROOT = "share";

    public static Uri uriFor(File file) {
        return new Uri.Builder()
                .scheme("content")
                .authority(AUTHORITY)
                .appendPath(ROOT)
                .appendPath(file.getName())
                .build();
    }

    @Override
    public boolean onCreate() {
        return true;
    }

    private File resolve(Uri uri) throws FileNotFoundException {
        if (uri == null || uri.getPathSegments().size() < 2
                || !ROOT.equals(uri.getPathSegments().get(0))) {
            throw new FileNotFoundException(String.valueOf(uri));
        }
        File dir = new File(getContext().getCacheDir(), ROOT);
        File file = new File(dir, uri.getPathSegments().get(1));
        if (!file.exists()) {
            throw new FileNotFoundException(file.getAbsolutePath());
        }
        return file;
    }

    @Override
    public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        return ParcelFileDescriptor.open(resolve(uri), ParcelFileDescriptor.MODE_READ_ONLY);
    }

    @Override
    public Cursor query(Uri uri, String[] projection, String selection,
                        String[] selectionArgs, String sortOrder) {
        File file;
        try {
            file = resolve(uri);
        } catch (FileNotFoundException e) {
            return null;
        }
        String[] columns = projection != null && projection.length > 0
                ? projection
                : new String[]{OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE};
        MatrixCursor cursor = new MatrixCursor(columns, 1);
        Object[] row = new Object[columns.length];
        for (int i = 0; i < columns.length; i++) {
            if (OpenableColumns.DISPLAY_NAME.equals(columns[i])) {
                row[i] = file.getName();
            } else if (OpenableColumns.SIZE.equals(columns[i])) {
                row[i] = file.length();
            } else {
                row[i] = null;
            }
        }
        cursor.addRow(row);
        return cursor;
    }

    @Override
    public String getType(Uri uri) {
        return "video/mp4";
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) {
        return null;
    }

    @Override
    public int delete(Uri uri, String selection, String[] selectionArgs) {
        return 0;
    }

    @Override
    public int update(Uri uri, ContentValues values, String selection, String[] selectionArgs) {
        return 0;
    }
}
