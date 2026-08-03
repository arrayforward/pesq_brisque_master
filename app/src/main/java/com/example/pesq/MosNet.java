package com.example.pesq;

import android.content.Context;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;

public final class MosNet {

    private static boolean ready = false;

    private MosNet() {
    }

    static {
        System.loadLibrary("pesqjni");
    }

    private static native boolean nativeInit(byte[] weights);

    private static native float nativeMeasure(short[] pcm);

    public static synchronized boolean init(Context ctx) {
        if (ready) return true;
        try {
            InputStream in = ctx.getAssets().open("mosnet_weights.bin");
            ByteArrayOutputStream bos = new ByteArrayOutputStream();
            byte[] buf = new byte[65536];
            int n;
            while ((n = in.read(buf)) != -1) bos.write(buf, 0, n);
            in.close();
            ready = nativeInit(bos.toByteArray());
        } catch (Exception e) {
            ready = false;
        }
        return ready;
    }

    public static float measure(short[] pcm16k) {
        if (!ready) return -1f;
        return nativeMeasure(pcm16k);
    }

    public static boolean isReady() {
        return ready;
    }
}
