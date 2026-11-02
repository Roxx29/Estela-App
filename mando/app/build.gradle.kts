plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.estela.mando"
    compileSdk = 35
    defaultConfig {
        applicationId = "com.estela.mando"
        minSdk = 26
        targetSdk = 35
        // CI numera cada build para que Android acepte la actualización encima
        versionCode = System.getenv("GITHUB_RUN_NUMBER")?.toInt() ?: 1
        versionName = "1.0.${System.getenv("GITHUB_RUN_NUMBER") ?: "0"}"
    }
    // shortcut: clave en el repo para que cada versión se instale encima de la anterior; pasarla a GitHub Secrets si se publica en Play
    signingConfigs {
        create("mando") {
            storeFile = file("mando.keystore")
            storePassword = "estelamando"
            keyAlias = "mando"
            keyPassword = "estelamando"
        }
    }
    buildTypes {
        getByName("release") {
            signingConfig = signingConfigs.getByName("mando")
            isMinifyEnabled = false
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    // lector de QR de Google Play Services: sin permiso de cámara y sin librería de escaneo propia
    implementation("com.google.android.gms:play-services-code-scanner:16.1.0")
    testImplementation("junit:junit:4.13.2")
}
