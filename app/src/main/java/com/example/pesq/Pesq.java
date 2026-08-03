package com.example.pesq;

public final class Pesq {

    static {
        System.loadLibrary("pesqjni");
    }

    public static final int ERR_UNKNOWN = -1;
    public static final int ERR_INVALID_RATE = -2;
    public static final int ERR_OOM_REF = -3;
    public static final int ERR_OOM_DEG = -4;
    public static final int ERR_OOM_TMP = -5;
    public static final int ERR_BUFFER_SHORT = -6;
    public static final int ERR_NO_UTTERANCES = -7;

    private Pesq() {
    }

    public static native float measure(int sampleRate, float[] ref, float[] deg, boolean wideband);

    public static String errorMessage(int code) {
        switch (code) {
            case ERR_INVALID_RATE:
                return "无效的采样率 (仅支持 8000/16000)";
            case ERR_OOM_REF:
            case ERR_OOM_DEG:
            case ERR_OOM_TMP:
                return "内存不足";
            case ERR_BUFFER_SHORT:
                return "音频太短 (至少 1/4 秒)";
            case ERR_NO_UTTERANCES:
                return "未检测到语音段 (录音中没有参考语音?)";
            default:
                return "未知错误 (" + code + ")";
        }
    }
}
