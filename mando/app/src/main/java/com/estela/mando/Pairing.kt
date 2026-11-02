package com.estela.mando

import java.net.URI

/** Valida el QR de Estela: http a una IP de la red local, puerto del mando y código secreto tras el #. */
object Pairing {
    const val PORT = 38918
    private val KEY = Regex("k=[A-Za-z0-9_-]{16,64}")

    fun parse(raw: String?): String? {
        val uri = try { URI(raw?.trim() ?: return null) } catch (e: Exception) { return null }
        val host = uri.host ?: return null
        if (uri.scheme != "http" || uri.port != PORT || !localIp(host) || !KEY.matches(uri.rawFragment ?: "")) return null
        return "http://$host:$PORT/#${uri.rawFragment}"
    }

    /** Solo la red de casa: así un QR ajeno no abre una web cualquiera dentro de la app. */
    fun localIp(host: String): Boolean {
        val p = host.split(".").map { it.toIntOrNull() ?: return false }
        if (p.size != 4 || p.any { it !in 0..255 }) return false
        return p[0] == 10 || p[0] == 127 || (p[0] == 192 && p[1] == 168) || (p[0] == 172 && p[1] in 16..31) ||
            (p[0] == 169 && p[1] == 254)
    }
}
