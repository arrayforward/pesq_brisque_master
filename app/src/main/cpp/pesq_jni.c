#include <jni.h>
#include <string.h>

#include "pesqio.h"
#include "pesqmain.h"

JNIEXPORT jfloat JNICALL
Java_com_example_pesq_Pesq_measure(JNIEnv *env, jclass clazz,
                                   jint sampleRate,
                                   jfloatArray refArr,
                                   jfloatArray degArr,
                                   jboolean wideband) {
    long errorFlag = 0;
    char *errorType = "unknown";

    select_rate((long) sampleRate, &errorFlag, &errorType);
    if (errorFlag != 0) {
        return (jfloat) errorFlag;
    }

    jsize refLen = (*env)->GetArrayLength(env, refArr);
    jsize degLen = (*env)->GetArrayLength(env, degArr);
    jfloat *refData = (*env)->GetFloatArrayElements(env, refArr, NULL);
    jfloat *degData = (*env)->GetFloatArrayElements(env, degArr, NULL);
    if (refData == NULL || degData == NULL) {
        if (refData != NULL) (*env)->ReleaseFloatArrayElements(env, refArr, refData, JNI_ABORT);
        if (degData != NULL) (*env)->ReleaseFloatArrayElements(env, degArr, degData, JNI_ABORT);
        return -1.0f;
    }

    SIGNAL_INFO ref_info;
    SIGNAL_INFO deg_info;
    memset(&ref_info, 0, sizeof(ref_info));
    memset(&deg_info, 0, sizeof(deg_info));

    strcpy(ref_info.path_name, "reference-signal");
    strcpy(ref_info.file_name, "reference-signal");
    strcpy(deg_info.path_name, "degraded-signal");
    strcpy(deg_info.file_name, "degraded-signal");

    ref_info.Nsamples = (long) refLen;
    ref_info.apply_swap = 0;
    ref_info.input_filter = wideband ? 2 : 1;
    ref_info.data = refData;

    deg_info.Nsamples = (long) degLen;
    deg_info.apply_swap = 0;
    deg_info.input_filter = wideband ? 2 : 1;
    deg_info.data = degData;

    ERROR_INFO err_info;
    memset(&err_info, 0, sizeof(err_info));
    err_info.mode = wideband ? WB_MODE : NB_MODE;

    pesq_measure(&ref_info, &deg_info, &err_info, &errorFlag, &errorType);

    (*env)->ReleaseFloatArrayElements(env, refArr, refData, JNI_ABORT);
    (*env)->ReleaseFloatArrayElements(env, degArr, degData, JNI_ABORT);

    if (errorFlag != 0) {
        return (jfloat) errorFlag;
    }
    return (jfloat) err_info.mapped_mos;
}
