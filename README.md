# Estela

**Tu música, en la luz de tu pantalla.**

Estela ilumina los bordes de tu pantalla con los colores de la portada de lo que suena en YouTube Music y muestra la letra sincronizada como una constelación: cada palabra es una estrella que se une a la siguiente mientras se canta.

## Qué hace

- **Brillo en los bordes**: un halo con los colores de la portada que reacciona al ritmo. Efectos: Estático, Respirar, Al ritmo, Giratorio, Aurora, Destello, Fuego, Ondas y Arcoíris, o **Automático**, que cambia según el ambiente de la canción (tranquila, normal, intensa, subidas fuertes).
- **Letra en constelación**: letras sincronizadas de LRCLIB, mejoradas con letra por palabra, coros y dúos cuando otro proveedor la tiene. Cada estrofa es una constelación nueva con su transición: Viaje, Remolino, Explosión, Lluvia de estrellas, Polvo, Reconstelar u Onda. También puede ser **Automática** (según la música) o **Aleatoria**.
- **Portada de fondo**: difuminada, en movimiento, Ken Burns, al ritmo o **Automático**, con portadas HD de iTunes y Deezer.
- **Carátula**: tarjeta en la esquina con título, artista y progreso, arriba a la izquierda, como marca de agua u oculta. Si la canción no tiene letra, o hay un instrumental largo, la carátula viaja al centro en grande.
- Se oculta sola durante juegos a pantalla completa y consume poca CPU.

## Requisitos

- Windows 10 u 11.
- YouTube Music en un navegador basado en Chromium (Chrome, Edge, Opera, Brave…).

## Descarga

- **Versión publicada**: en *Releases* del repositorio, descarga `Estela-vX.Y.Z.zip`.
- **Última compilación**: en *Actions → Build*, abre la ejecución más reciente y descarga el artefacto `Estela-windows` (se guarda 30 días; hace falta haber iniciado sesión en GitHub).

## Instalación

1. Descomprime la carpeta `Estela`. Mantén la carpeta entera: el `.exe` necesita su carpeta `_internal`.
2. Abre `Estela.exe`. Aparece una estrella en la bandeja del sistema. Si no la ves, pulsa la flecha **^** junto al reloj.

**Aviso de Windows SmartScreen.** El `.exe` no está firmado (un certificado de firma cuesta dinero), así que la primera vez Windows puede decir *"Windows protegió tu PC"*. Pulsa **Más información → Ejecutar de todas formas**. Descárgalo solo desde este repositorio; si prefieres no fiarte, genera tú el `.exe` con `build.bat` (ver abajo).

## Uso

En la estrella de la bandeja, el doble clic abre la letra y el clic derecho abre el menú, con efecto del borde, transición entre estrofas, fondo de la letra, carátula, sincronizar letra y atajos.

Atajos en la ventana de la letra:

| Tecla | Acción |
|---|---|
| **B** | Cambiar fondo |
| **C** | Cambiar posición de la carátula |
| **T** | Cambiar transición |
| **← / →** | Retrasar / adelantar la letra 0,1 s (se recuerda por canción) |
| **J** | Escuchar juntos |
| **H** | Ver los atajos |

## Escuchar juntos

Escucha la misma canción a la vez que tus amigos, cada uno en su ordenador y con su propia cuenta de YouTube Music. El anfitrión manda: cuando cambia de canción, pausa o salta, los demás le siguen. Hay chat y se pueden sugerir canciones.

**Prepararlo (una vez, solo el anfitrión).** Estela no usa un servidor central: cada anfitrión publica el suyo, gratis, en Cloudflare.
1. Instala [Node.js](https://nodejs.org) 20 o superior y crea una cuenta gratuita de Cloudflare.
2. Descarga el código del repositorio y ejecuta `deploy_servidor.bat`. Abre el navegador para iniciar sesión en Cloudflare y al final muestra la dirección del servidor (`wss://…/ws`).
3. En Estela pulsa **J** y pega esa dirección.

**Invitar.** Pulsa **J → Crear sala → Copiar invitación** y mándala por donde quieras. Tu amigo pulsa **J**, pega la invitación y tú apruebas su entrada. Máximo 10 personas por sala.

**Limitaciones.**
- Cada uno necesita YouTube Music (con Premium: los anuncios rompen la sincronía) y Estela abierta.
- El invitado abre la canción con la primera coincidencia de búsqueda: puede salir otra versión, o ninguna si no está en su país.
- La sincronía ronda el segundo, no es exacta: los desfases pequeños se corrigen poco a poco y los grandes con un salto.
- Si el navegador no deja saltar desde los controles multimedia de Windows, Estela avisa y hay que ajustar a mano.
- El servidor gratuito tiene los límites del plan gratuito de Cloudflare.

**Privacidad.** El servidor es tuyo: solo pasan por él el título, el artista y la posición de la canción, los nombres que elijáis y el chat. La sala se borra 10 minutos después de quedarse vacía y no se guarda historial. Nadie entra sin tu aprobación.

## Privacidad

Estela lee la canción desde los controles multimedia de Windows. Para buscar la letra y la portada HD envía **solo el título y el artista** a LRCLIB, iTunes y Deezer. La portada HD se puede desactivar en *Carátula → Portada HD*. No guarda ni envía nada más. La caché vive en `%LOCALAPPDATA%\Estela` (si venías de BrilloMusical, tus ajustes y tu caché se pasan solos).

## Problemas comunes

- **No detecta la canción**: comprueba que suena en YouTube Music en el navegador y que Windows muestra sus controles multimedia (las teclas de volumen enseñan la canción).
- **La letra va adelantada o atrasada**: usa ← / → en la ventana de la letra o *Sincronizar letra* en la bandeja. Para investigarlo, abre la app con la variable `ESTELA_DEBUG=1` y revisa `app.log`.
- **El .exe no arranca**: abre el de `dist\Estela\`, no el de `build\`.

## Generar el ejecutable

Cada cambio en `main` se compila solo en GitHub Actions y cada etiqueta `v*` publica una Release con el `.zip`. Para hacerlo en tu PC: con Python 3.12 instalado, haz doble clic en `build.bat`. Instala las dependencias, pasa los tests y deja el programa en `dist\Estela\Estela.exe`.

## Capturas sugeridas

Borde en Fuego y en Aurora; constelación a mitad de estrofa; carátula grande sin letra; las cuatro posiciones de la carátula; un GIF de cada transición.

## Créditos

Letras: [LRCLIB](https://lrclib.net), BetterLyrics, BiniLyrics, LrcRed y YouLy+. Portadas: iTunes Search API y Deezer API. Hecho con Python y PySide6.
