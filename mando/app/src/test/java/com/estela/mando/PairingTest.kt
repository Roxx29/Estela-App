package com.estela.mando

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class PairingTest {
    private val key = "abcdefghijklmnop_-12"

    @Test fun aceptaElQrDeEstela() {
        assertEquals("http://192.168.1.20:38918/#k=$key", Pairing.parse(" http://192.168.1.20:38918/#k=$key "))
        assertEquals("http://10.0.0.5:38918/#k=$key", Pairing.parse("http://10.0.0.5:38918/#k=$key"))
    }

    @Test fun rechazaLoQueNoEsEstela() {
        for (bad in listOf(null, "", "hola", "https://192.168.1.20:38918/#k=$key", "http://8.8.8.8:38918/#k=$key",
                "http://evil.com:38918/#k=$key", "http://192.168.1.20:80/#k=$key", "http://192.168.1.20:38918/#k=corto",
                "http://192.168.1.20:38918/", "http://172.32.0.1:38918/#k=$key", "http://192.168.1.300:38918/#k=$key")) {
            assertNull(bad, Pairing.parse(bad))
        }
    }
}
