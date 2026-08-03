#include <jni.h>

#define main p563_cli_main
#include "p563/p563.c"
#undef main

JNIEXPORT jfloat JNICALL
Java_com_example_pesq_P563_nativeMeasure(JNIEnv *env, jclass clazz, jshortArray pcm) {
    jsize len = (*env)->GetArrayLength(env, pcm);
    if (len < 8000) return -1.0f;
    jshort *data = (*env)->GetShortArrayElements(env, pcm, NULL);
    if (data == NULL) return -1.0f;

    p563Results_struct tResults = {-1};
    module1((short int *) data, (INT32) len, &tResults);
    module2((short int *) data, (INT32) len, &tResults);
    module3((short int *) data, (INT32) len, &tResults);

    int partitionNumber = 0;
    FLOAT mos = 0;
    PostProcessMovs(&partitionNumber, &mos, &tResults);

    (*env)->ReleaseShortArrayElements(env, pcm, data, JNI_ABORT);
    return (jfloat) mos;
}
