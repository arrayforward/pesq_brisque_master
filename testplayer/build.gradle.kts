plugins {
    id("com.android.application")
}

android {
    namespace = "com.example.testplayer"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.example.testplayer"
        minSdk = 29
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
        encoding = "UTF-8"
    }
}

dependencies {
}
