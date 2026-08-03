package com.example.pesq;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;

public final class WavReader {

    public final int sampleRate;
    public final short[] samples;

    private WavReader(int sampleRate, short[] samples) {
        this.sampleRate = sampleRate;
        this.samples = samples;
    }

    public static WavReader read(InputStream in) throws IOException {
        byte[] all = readAll(in);
        if (all.length < 44) throw new IOException("文件太小");
        if (tag(all, 0) != 0x46464952 || tag(all, 8) != 0x45564157) {
            throw new IOException("不是 WAV 文件");
        }
        int pos = 12;
        int audioFormat = -1, channels = -1, rate = -1, bits = -1;
        byte[] data = null;
        while (pos + 8 <= all.length) {
            int id = tag(all, pos);
            int size = leInt(all, pos + 4);
            int body = pos + 8;
            if (body + size > all.length) size = all.length - body;
            if (id == 0x20746D66) {
                audioFormat = leShort(all, body);
                channels = leShort(all, body + 2);
                rate = leInt(all, body + 4);
                bits = leShort(all, body + 14);
            } else if (id == 0x61746164) {
                data = new byte[size];
                System.arraycopy(all, body, data, 0, size);
            }
            pos = body + size + (size & 1);
        }
        if (audioFormat != 1 || bits != 16 || data == null) {
            throw new IOException("仅支持 16-bit PCM WAV");
        }
        if (rate != 8000 && rate != 16000) {
            throw new IOException("仅支持 8000 或 16000 Hz 采样率, 当前: " + rate);
        }
        int frames = data.length / 2 / channels;
        short[] out = new short[frames];
        for (int i = 0; i < frames; i++) {
            int off = (i * channels) * 2;
            out[i] = (short) ((data[off] & 0xff) | (data[off + 1] << 8));
        }
        return new WavReader(rate, out);
    }

    public static float[] toFloat(short[] s) {
        float[] f = new float[s.length];
        for (int i = 0; i < s.length; i++) f[i] = s[i];
        return f;
    }

    private static byte[] readAll(InputStream in) throws IOException {
        ByteArrayOutputStream bos = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        while ((n = in.read(buf)) != -1) bos.write(buf, 0, n);
        in.close();
        return bos.toByteArray();
    }

    private static int tag(byte[] b, int off) {
        return (b[off] & 0xff) << 24 | (b[off + 1] & 0xff) << 16
                | (b[off + 2] & 0xff) << 8 | (b[off + 3] & 0xff);
    }

    private static int leInt(byte[] b, int off) {
        return (b[off] & 0xff) | (b[off + 1] & 0xff) << 8
                | (b[off + 2] & 0xff) << 16 | (b[off + 3] & 0xff) << 24;
    }

    private static int leShort(byte[] b, int off) {
        return (b[off] & 0xff) | (b[off + 1] & 0xff) << 8;
    }
}
