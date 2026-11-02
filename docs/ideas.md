# Ideas pendientes

Ideas del usuario guardadas para hacer. Cuando una se haga, se pasa a `docs/decisiones.md`.

## 2026-10-29

Las ideas 1 a 4 se hicieron en la v8 (ver `docs/decisiones.md`). Queda la 5.

1. **Controles de YouTube Music desde la app**: un panel que aparece en el lado **izquierdo** de la ventana de la letra al acercar el ratón (anterior, play/pausa, siguiente, avanzar en la canción). Vía controles multimedia de Windows (SMTC); funciona con YouTube Music en el navegador.
2. **Carrusel vertical a la derecha**: aparece en el lado **derecho**. Al pasar el ratón por una canción, se hace grande. Cada estilo la muestra a su manera:
   - **Cinta**: un disco de vinilo retro / tocadiscos.
   - **Constelación** (el modo principal): una galaxia.
   - **Latido**: por definir.
   - Ojo: Windows no da la cola de YouTube Music; el carrusel sería el historial de lo que ha sonado (ver la respuesta del 2026-10-29).
3. **Estilo automático por género**: un modo que detecta el género de la canción y elige el estilo de letra (Constelación / Latido / Cinta). El género puede venir de iTunes (`primaryGenreName`), que la app ya consulta para la portada HD.
4. **Mejorar Cinta**: además de la cinta 3D, añadir más animaciones a ese estilo (por definir con Animaciones y el agente de vídeo).
5. Del Gestor de ideas (v7), sin hacer: captura de la estrofa para redes, aviso "Ahora suena", luces Philips Hue, partículas según palabras de la letra, "Tu semana en colores".

## 2026-10-30

Las ideas 6, 7 y 8 (opción A) se hicieron en la v9, como prototipo.

6. **Carrusel centrado en la canción actual**: la que suena en el medio, lo ya escuchado arriba y lo que viene (cola/playlist) abajo.
7. **Ver la playlist / cola**: Windows (SMTC) no la da. Hace falta YouTube Music integrado en la app o una extensión del navegador.
8. **No tener YouTube Music abierto aparte, buscar canciones desde la app**. Opciones: A) YouTube Music dentro de la app con un navegador integrado (QtWebEngine: +150–200 MB; riesgo de que Google bloquee el inicio de sesión, hay que probarlo); B) extensión de Chrome/Edge que habla con la app (pestaña de fondo); C) descargar el audio: descartada (condiciones de YouTube).

## 2026-10-30 (Latido distinto y modo nuevo)

Hechas en la v10: Latido como Pulso + Onda (9) y el estilo Nueva York (10).

9. **Latido distinto de Constelación** (propuestas de los agentes; sin decidir). Base común: quitar a Latido las estrellas, la nebulosa y la portada difuminada.
   - **Pulso** (recomendada): fondo de color liso de la portada que se aclara con cada golpe; letra blanca gigante, gruesa y en mayúsculas, abajo a la izquierda; la palabra que suena crece con el golpe.
   - **Onda**: fondo negro mate y anillos del color de la portada que salen del centro con cada golpe (varios en drops y estribillos).
   - **Pulso + Onda**: la mezcla de las dos (recomendada por el Capitán).
   - **Ecualizador**: barras del color de la portada abajo y la letra saltando encima.
   - **Escenario**: rejilla en perspectiva y láseres que destellan con los golpes.
   - **Póster**: se aparta para un modo propio (idea 10).
10. **Modo nuevo "Nueva York"** (del usuario, a partir de Póster): blanco y negro; las palabras se van juntando unas con otras en distintas direcciones (horizontal y vertical girada 90°, de distintos tamaños, como un crucigrama tipográfico), con placas negras de esquinas redondeadas detrás como en la foto de referencia (MAYAMI / CORRER / GUSTAR); cuando se juntan varias, zoom a la última, que pasa a ser el inicio del siguiente bloque. Fondo que combine con las letras en negro.

## 2026-10-31 (Cinta: los dos aros de altavoz)

Hecha en la v12 (todas las ideas). También la opción B de la idea 8 (extensión de Chrome).

11. **Que los aros de las esquinas de Cinta hagan algo** (propuesta de Animaciones, sin decidir):
    - **Cono que bombea**: cúpula interior que se hunde con el golpe (60 ms) y rebota (220 ms); más fuerte en canciones intensas.
    - **Polvo que salta**: motas de tinta sobre el aro que saltan con cada golpe y caen; en los drops saltan todas y se tiñen del acento.
    - **Chladni en el estribillo**: dibujo de líneas dentro del aro que cambia cada 4 golpes.
    - **Tocadiscos**: el aro de arriba gira como un vinilo (33⅓ o 45 rpm según la intensidad) con una aguja que marca la posición de la canción.
    - **Diafragma de cristal**: el aro se deforma en ondas al ritmo.
    - Combinación recomendada: cono en los dos, polvo en el de abajo, Chladni en estribillos y el de arriba como tocadiscos.

## 2026-10-31 (modo Loco)

Hecha en la v13, con 10 transiciones.

12. **Modo Loco**: un estilo más que va saltando entre Constelación, Latido, Cinta y Nueva York según la música (drops, inicio de estribillo, cambios de estrofa; más a menudo en canciones intensas), con transiciones muy fluidas de formas: iris que se transforma (círculo → estrella → hexágono), cristal roto, líquido que crece con la energía, remolino que se hunde y persiana o barrido inclinado al ritmo. Se dibujan los dos temas a la vez durante la transición (< 1 s) y se mezclan con la forma animada; en Ligera, transiciones más simples. No es viable que cada elemento se transforme en el del otro tema (dibujan cosas distintas): la transformación es de la imagen entera.

## 2026-10-31 (animación de inicio de Estela)

Hecha la B en la v14. A y C quedan como ideas.

13. **Intro al abrir el .exe** (propuesta de Animaciones, Lógica y Marketing):
    - **B. Estela que dibuja la estrella** (favorita, 2,2 s): un punto de luz entra en diagonal dejando cola, traza las 4 puntas de la estrella de un solo trazo, cierra el aro azul → cian, destello + onda y el logo encoge hacia la bandeja mientras se encienden los bordes.
    - **A. Meteoro que estalla** (2 s): cruza la pantalla en arco, frena en el centro, estalla en chispas y de ahí sale el logo.
    - **C. Lluvia que converge** (2,4 s): 60 estrellas entran desde los lados y forman el logo (la más vistosa, marea un poco).
    - Técnica: ventana transparente que no recibe clics (como los bordes), solo del tamaño de la animación; la app carga por detrás; clic o Esc la saltan; casilla «Animación de inicio» en la bandeja; no sale con un juego a pantalla completa, en Escritorio remoto ni con las animaciones de Windows desactivadas. Ligera: 1,2 s sin partículas.
    - Marketing: el nombre «Estela» escrito solo la primera vez (con «Lo que suena, brilla.»); después versión corta de ~0,8 s solo con el símbolo.

## 2026-11-02 (controlar Estela desde el móvil)

14. **Mando en el móvil** (Gestor de ideas, Marketing). Hecha la A el 2026-11-02, con volumen de YouTube Music y todos los ajustes:
    - **A. Página web servida por Estela en el Wi‑Fi de casa** (recomendada): bandeja → «Controlar desde el móvil…» (apagado de serie) muestra un QR; el móvil abre una página sin instalar nada (se puede añadir a la pantalla de inicio). Play/pausa, anterior/siguiente, ±10 s, portada con el halo de su color, letra en 3 líneas, estilo de letra y de brillo, apagar brillo, ocultar letra, desfase de la letra, cola. Servidor aparte del puente de la extensión, con código secreto en el QR; solo red local. Windows pide permiso al firewall la primera vez (marcar «Redes privadas»); no va en redes de invitados, de universidad ni con VPN. Esfuerzo medio (~200 líneas + una página; `segno` para el QR).
    - **B. KDE Connect o Unified Remote** (sin código): app en el móvil y en el PC; solo play/pausa, siguiente y volumen de Windows, nada propio de Estela.
    - **C. PWA con HTTPS**: descartada (certificado autofirmado = avisos en el móvil).
    - **D. Bluetooth / Enlace Móvil**: descartadas (Enlace Móvil funciona al revés; Bluetooth es frágil).
    - Por decidir: ¿letra en el móvil o solo botones? ¿Volumen de YouTube Music o de Windows (añade pycaw)?
15. **Instalador** (Optimización): Inno Setup con LZMA, descarga de ~130–150 MB, con acceso directo y desinstalador.
16. **WebView2 en vez de QtWebEngine** (Optimización): −190 MB, pero hay que rehacer YouTube Music integrado y el medidor de audio. Riesgo alto.
