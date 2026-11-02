package com.estela.mando

import android.app.Activity
import android.content.SharedPreferences
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.webkit.JavascriptInterface
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.codescanner.GmsBarcodeScannerOptions
import com.google.mlkit.vision.codescanner.GmsBarcodeScanning

/** Mando de Estela: escanea el QR del PC y abre su página del mando a pantalla completa, sin navegador. */
class MainActivity : Activity() {
    private lateinit var prefs: SharedPreferences
    private var web: WebView? = null

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        prefs = getSharedPreferences("mando", MODE_PRIVATE)
        val url = prefs.getString("url", null)
        if (url == null) welcome(null, null) else remote(url)
    }

    override fun onDestroy() {
        web?.destroy()
        super.onDestroy()
    }

    /** Contenido dentro de las barras del sistema (Android 15 dibuja de borde a borde). */
    @Suppress("DEPRECATION")
    private fun show(content: View) {
        if (web !== content) dropWeb()
        val frame = FrameLayout(this)
        frame.setBackgroundColor(NIGHT)
        frame.addView(content, FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT))
        frame.setOnApplyWindowInsetsListener { v, insets ->
            v.setPadding(insets.systemWindowInsetLeft, insets.systemWindowInsetTop, insets.systemWindowInsetRight,
                insets.systemWindowInsetBottom)
            insets
        }
        setContentView(frame)
    }

    /** El WebView se quita de la ventana antes de destruirlo (destruirlo puesto puede cerrar la app). */
    private fun dropWeb() {
        web?.let { (it.parent as? ViewGroup)?.removeView(it); it.destroy() }
        web = null
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    private fun button(text: String, primary: Boolean, onClick: () -> Unit) = Button(this).apply {
        this.text = text
        isAllCaps = false
        textSize = 17f
        setTextColor(if (primary) NIGHT else BONE)
        background = GradientDrawable().apply {
            cornerRadius = dp(28).toFloat()
            if (primary) colors = intArrayOf(STAR, CYAN) else setStroke(dp(1), Color.argb(70, 230, 225, 215))
            orientation = GradientDrawable.Orientation.TL_BR
        }
        setPadding(dp(28), dp(14), dp(28), dp(14))
        setOnClickListener { onClick() }
    }

    /** Pantalla de inicio o de error: qué hacer y el botón de escanear. */
    private fun welcome(error: String?, retry: String?) {
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setPadding(dp(32), dp(24), dp(32), dp(24))
            background = GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM,
                intArrayOf(Color.rgb(0x16, 0x26, 0x58), NIGHT, NIGHT))
        }
        fun text(t: String, size: Float, color: Int, bold: Boolean = false) = TextView(this).apply {
            text = t
            textSize = size
            setTextColor(color)
            gravity = Gravity.CENTER
            if (bold) typeface = Typeface.create("sans-serif-black", Typeface.NORMAL)
            setLineSpacing(0f, 1.2f)
        }
        fun add(v: View, top: Int) = col.addView(v, LinearLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT).apply {
            topMargin = dp(top)
            gravity = Gravity.CENTER_HORIZONTAL
        })
        col.addView(ImageView(this).apply { setImageResource(R.drawable.ic_star) },
            LinearLayout.LayoutParams(dp(150), dp(150)).apply { gravity = Gravity.CENTER_HORIZONTAL })
        add(text(if (error == null) "Tu móvil, el mando." else "Algo no va", 30f, Color.WHITE, true), 8)
        add(text(error ?: "Controla la música, la letra y todos los ajustes de Estela desde aquí.", 16f,
            if (error == null) BONE else Color.rgb(0xFF, 0x9C, 0x96)), 12)
        if (error == null) {
            add(text("1. En el PC: bandeja de Estela → Controlar desde el móvil…\n2. Pulsa «Escanear el QR» y apunta a la pantalla.\n\n" +
                "El móvil y el PC tienen que estar en el mismo Wi‑Fi.", 14f, DIM), 28)
        }
        if (retry != null) add(button("Reintentar", true) { remote(retry) }, 36)
        add(button("Escanear el QR", retry == null) { scan() }, if (retry != null) 14 else 36)
        show(col)
    }

    private fun scan() {
        val options = GmsBarcodeScannerOptions.Builder().setBarcodeFormats(Barcode.FORMAT_QR_CODE).build()
        GmsBarcodeScanning.getClient(this, options).startScan()
            .addOnSuccessListener { code ->
                val url = Pairing.parse(code.rawValue)
                if (url == null) {
                    welcome("Ese QR no es el de Estela. Ábrelo desde la bandeja del PC: Controlar desde el móvil…", null)
                } else {
                    prefs.edit().putString("url", url).apply()
                    remote(url)
                }
            }
            .addOnCanceledListener { if (web == null && prefs.getString("url", null) == null) welcome(null, null) }
            .addOnFailureListener {
                welcome("No se pudo abrir el lector de QR. Actualiza Google Play Services e inténtalo otra vez.", null)
            }
    }

    private fun remote(url: String) {
        val target = Uri.parse(url)
        dropWeb()
        val w = WebView(this)
        web = w
        w.setBackgroundColor(NIGHT)
        w.settings.javaScriptEnabled = true
        w.settings.domStorageEnabled = true
        w.addJavascriptInterface(AppBridge(), "EstelaApp")
        w.webViewClient = object : WebViewClient() {
            // solo la página de Estela: cualquier otro enlace se queda fuera de la app
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest) =
                request.url.host != target.host || request.url.port != target.port

            override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                if (request.isForMainFrame) view.post {  // fuera del aviso del propio WebView, que se va a destruir
                    welcome("Estela no responde. ¿Sigue abierta en el PC y el mando encendido? Revisa que estéis en el mismo Wi‑Fi.", url)
                }
            }
        }
        show(w)
        w.loadUrl(url)
    }

    /** Lo que la página puede pedir a la app: solo volver a escanear (código caducado). */
    inner class AppBridge {
        @JavascriptInterface
        fun rescan() = runOnUiThread {
            prefs.edit().remove("url").apply()
            scan()
        }
    }

    companion object {
        val NIGHT = Color.rgb(0x0A, 0x14, 0x30)
        val STAR = Color.rgb(0x4D, 0xA3, 0xFF)
        val CYAN = Color.rgb(0x2A, 0x7F, 0xA8)
        val BONE = Color.rgb(0xE6, 0xE1, 0xD7)
        val DIM = Color.argb(150, 230, 225, 215)
    }
}
