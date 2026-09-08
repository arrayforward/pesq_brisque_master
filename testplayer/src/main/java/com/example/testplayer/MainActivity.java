package com.example.testplayer;

import android.app.Activity;
import android.content.res.AssetFileDescriptor;
import android.graphics.Color;
import android.graphics.Typeface;
import android.media.AudioAttributes;
import android.media.MediaPlayer;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.WindowManager;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.io.IOException;
import java.util.Locale;

/**
 * 音质评价测试播放器。
 * 内置 chirp 导频测试音频（tools/musicq 生成），开屏自动播放、列表循环。
 * 播放链路：MediaPlayer → AudioFlinger → HAL，即被测的完整播放通路。
 * PC 端用 scrcpy 采集系统播放输出后，由 tools/musicq 对齐并评分。
 */
public class MainActivity extends Activity {

    /** assets 文件名 + 展示名。顺序即播放顺序。 */
    private static final String[][] SONGS = {
            {"song1.wav", "夜曲 - 周杰伦"},
            {"song2.wav", "海阔天空 - Beyond"},
    };

    private MediaPlayer player;
    private int index = 0;
    private TextView songView;
    private TextView posView;
    private final Handler handler = new Handler(Looper.getMainLooper());

    private final Runnable posTicker = new Runnable() {
        @Override
        public void run() {
            if (player != null && player.isPlaying()) {
                int pos = player.getCurrentPosition() / 1000;
                int dur = player.getDuration() / 1000;
                posView.setText(String.format(Locale.US, "%d:%02d / %d:%02d",
                        pos / 60, pos % 60, dur / 60, dur % 60));
            }
            handler.postDelayed(this, 500);
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        buildUi();
        playCurrent();
        handler.post(posTicker);
    }

    private void buildUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setGravity(Gravity.CENTER);
        root.setBackgroundColor(Color.WHITE);
        int pad = (int) (32 * getResources().getDisplayMetrics().density);
        root.setPadding(pad, pad, pad, pad);

        TextView title = new TextView(this);
        title.setText("音质评价测试播放中");
        title.setTextSize(22);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        title.setGravity(Gravity.CENTER);

        songView = new TextView(this);
        songView.setTextSize(18);
        songView.setGravity(Gravity.CENTER);
        songView.setPadding(0, pad / 2, 0, 0);

        posView = new TextView(this);
        posView.setTextSize(48);
        posView.setTypeface(Typeface.MONOSPACE);
        posView.setGravity(Gravity.CENTER);
        posView.setPadding(0, pad / 2, 0, 0);

        TextView hint = new TextView(this);
        hint.setText("循环播放中，请勿切后台。PC 端执行 mq capture 开始采集。");
        hint.setTextSize(13);
        hint.setTextColor(Color.GRAY);
        hint.setGravity(Gravity.CENTER);
        hint.setPadding(0, pad, 0, 0);

        root.addView(title);
        root.addView(songView);
        root.addView(posView);
        root.addView(hint);
        setContentView(root);
    }

    private void playCurrent() {
        releasePlayer();
        String asset = SONGS[index][0];
        String name = SONGS[index][1];
        songView.setText(String.format(Locale.US, "第 %d/%d 首：%s", index + 1, SONGS.length, name));
        try {
            AssetFileDescriptor afd = getAssets().openFd(asset);
            player = new MediaPlayer();
            player.setAudioAttributes(new AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                    .build());
            player.setDataSource(afd.getFileDescriptor(), afd.getStartOffset(), afd.getLength());
            afd.close();
            player.setOnCompletionListener(mp -> {
                index = (index + 1) % SONGS.length;
                playCurrent();
            });
            player.setOnErrorListener((mp, what, extra) -> {
                songView.setText("播放错误: what=" + what + " extra=" + extra);
                return true;
            });
            player.prepare();
            player.start();
        } catch (IOException e) {
            songView.setText("无法播放 " + asset + ": " + e.getMessage());
        }
    }

    private void releasePlayer() {
        if (player != null) {
            try {
                player.stop();
            } catch (IllegalStateException ignored) {
            }
            player.release();
            player = null;
        }
    }

    @Override
    protected void onDestroy() {
        handler.removeCallbacks(posTicker);
        releasePlayer();
        super.onDestroy();
    }
}
