package com.example.pesq;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ContentValues;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.net.Uri;
import android.os.Bundle;
import android.provider.MediaStore;
import android.widget.Button;
import android.widget.ImageView;
import android.widget.TextView;
import android.widget.Toast;

import java.io.File;
import java.io.FileInputStream;
import java.io.OutputStream;
import java.util.Locale;

public class FrameViewActivity extends Activity {

    public static final String EXTRA_PATH = "path";
    public static final String EXTRA_IDX = "idx";
    public static final String EXTRA_SCORE = "score";

    private String path;
    private int idx;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_frame_view);

        path = getIntent().getStringExtra(EXTRA_PATH);
        idx = getIntent().getIntExtra(EXTRA_IDX, 0);
        double score = getIntent().getDoubleExtra(EXTRA_SCORE, 0);

        TextView tvTitle = findViewById(R.id.tvFrameTitle);
        tvTitle.setText(String.format(Locale.US, "第 %d 帧   BRISQUE = %.2f (%s)",
                idx, score, MainActivity.brisqueQuality(score)));

        ImageView iv = findViewById(R.id.ivFrame);
        File f = new File(path);
        if (f.exists()) {
            Bitmap bmp = BitmapFactory.decodeFile(path);
            iv.setImageBitmap(bmp);
        } else {
            tvTitle.setText("第 " + idx + " 帧   图片不存在");
        }

        Button btnSave = findViewById(R.id.btnSaveFrame);
        btnSave.setOnClickListener(v -> saveToGallery());
        Button btnBack = findViewById(R.id.btnBack);
        btnBack.setOnClickListener(v -> finish());
    }

    private void saveToGallery() {
        File src = new File(path);
        if (!src.exists()) {
            Toast.makeText(this, "图片不存在", Toast.LENGTH_SHORT).show();
            return;
        }
        try {
            String name = String.format(Locale.US, "pesq_frame_%03d_%d.jpg", idx,
                    System.currentTimeMillis());
            ContentValues values = new ContentValues();
            values.put(MediaStore.Images.Media.DISPLAY_NAME, name);
            values.put(MediaStore.Images.Media.MIME_TYPE, "image/jpeg");
            values.put(MediaStore.Images.Media.RELATIVE_PATH, "Pictures/PesqMeter");
            Uri uri = getContentResolver().insert(
                    MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values);
            if (uri == null) throw new Exception("insert failed");
            OutputStream out = getContentResolver().openOutputStream(uri);
            FileInputStream in = new FileInputStream(src);
            byte[] buf = new byte[8192];
            int n;
            while ((n = in.read(buf)) != -1) out.write(buf, 0, n);
            in.close();
            out.close();
            new AlertDialog.Builder(this)
                    .setMessage("已保存到相册: Pictures/PesqMeter/" + name)
                    .setPositiveButton("好", null)
                    .show();
        } catch (Exception e) {
            Toast.makeText(this, "保存失败: " + e.getMessage(), Toast.LENGTH_LONG).show();
        }
    }
}
