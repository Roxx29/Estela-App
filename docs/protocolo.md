# Protocolo de Escuchar juntos (v1)

Servidor: Cloudflare Worker + Durable Objects (`server/`). Un Durable Object por sala.
Todos los mensajes son JSON de texto (máx. 4096 bytes) con un campo `tipo`. Los campos
desconocidos se ignoran. Tiempos `ts`/`hasta` en milisegundos del reloj del servidor
(epoch); `duracion`/`posicion` en segundos.

## Conexión

| URL | Uso | Primer mensaje |
|---|---|---|
| `wss://<host>/ws` | Crear sala nueva (el servidor elige el código) | `crear` |
| `wss://<host>/ws/<CODIGO>` | Entrar o volver a una sala | `unirse` o `reconectar` |

- Código: 6 caracteres de `ABCDEFGHJKMNPQRSTUVWXYZ23456789` (sin `I L O 0 1`). Se acepta en minúsculas.
- Límite por IP: 30 conexiones/min y 5 salas nuevas/min. Si se supera, HTTP 429 sin WebSocket.
- Tras un error de entrada (`sala-no-existe`, `sala-llena`, `token-invalido`) el servidor cierra.

## Límites

| Qué | Valor |
|---|---|
| Miembros por sala (incluye anfitrión y pendientes) | 10 |
| Mensajes por miembro | 40 cada 10 s |
| Chat | 300 caracteres, 5 cada 10 s |
| Sugerencias | 3 cada 30 s, 20 pendientes por sala (se descarta la más antigua) |
| Gracia por desconexión | 30 s (luego se le quita; si era anfitrión pasa al miembro conectado más antiguo) |
| Sala sin nadie conectado | se borra a los 10 min |

Textos: se recortan espacios; no se admiten caracteres de control ni de dirección (bidi).

## Cliente → servidor

| `tipo` | Campos | Quién | Respuesta |
|---|---|---|---|
| `crear` | `nombre` (1–32) | conexión nueva | `bienvenida` |
| `unirse` | `codigo`, `nombre` (1–32) | conexión a `/ws/<CODIGO>` | `pendiente`; al anfitrión `solicitud` |
| `reconectar` | `codigo`, `token` | conexión a `/ws/<CODIGO>` | `bienvenida` (cierra la conexión anterior del mismo miembro) |
| `salir` | — | cualquiera | cierre; a los demás `miembros` |
| `aprobar` | `id` | anfitrión | al aprobado `bienvenida`; a todos `miembros` |
| `rechazar` | `id` | anfitrión | al rechazado `rechazado` y cierre |
| `expulsar` | `id` | anfitrión | al expulsado `expulsado` y cierre; a todos `miembros` |
| `transferir` | `id` (miembro conectado) | anfitrión | a todos `miembros` |
| `estado` | ver abajo | **solo anfitrión** | a los demás `estado` con `ts` |
| `latido` | — | cualquiera (cada 15 s) | `latido` |
| `ping-reloj` | `t0` (número del cliente) | cualquiera | `pong-reloj` |
| `chat` | `texto` (1–300) | aprobado | a todos `chat` |
| `sugerir` | `titulo` (1–200), `artista` (0–200), `videoId`? | aprobado | al anfitrión `sugerencia` |
| `aprobar-sugerencia` | `sid` (entero) | anfitrión | a todos `sugerencia-aprobada` |

Un miembro pendiente solo puede enviar `salir`, `latido` y `ping-reloj`.

### `estado`

```json
{"tipo":"estado","titulo":"Song","artista":"Artist","album":"Album",
 "duracion":215.4,"posicion":42.1,"reproduciendo":true,
 "semilla":12345,"transicion":"viaje","videoId":"dQw4w9WgXcQ"}
```

| Campo | Tipo y rango |
|---|---|
| `titulo`, `artista`, `album` | texto 0–200 |
| `duracion`, `posicion` | número 0–86400 |
| `reproduciendo` | booleano |
| `semilla` | entero 0–2147483647 |
| `transicion` | texto 0–32 |
| `videoId` | opcional, 11 caracteres `[A-Za-z0-9_-]` |

El servidor añade `ts` (cuándo lo recibió) y guarda el último para enviarlo en `bienvenida`.
El invitado estima su desfase con `ping-reloj`: `desfase = ts − (t0 + t1) / 2`, donde `t1`
es su hora al recibir `pong-reloj`.

## Servidor → cliente

| `tipo` | Campos |
|---|---|
| `bienvenida` | `codigo`, `id`, `token`, `anfitrion`, `miembros`, `estado` (o `null`) |
| `pendiente` | `id` |
| `solicitud` | `id`, `nombre` (solo al anfitrión; se reenvía al nuevo anfitrión) |
| `solicitud-cancelada` | `id` (el pendiente se fue) |
| `rechazado` | — |
| `expulsado` | — |
| `miembros` | `anfitrion`, `miembros` |
| `anfitrion-ausente` | `hasta` (fin de los 30 s de gracia) |
| `estado` | campos de `estado` + `ts` |
| `latido` | — |
| `pong-reloj` | `t0` (el del cliente), `ts` |
| `chat` | `id`, `nombre`, `texto`, `ts` |
| `sugerencia` | `sid`, `id`, `nombre`, `titulo`, `artista`, `videoId`? (solo al anfitrión) |
| `sugerencia-aprobada` | `sid`, `titulo`, `artista`, `videoId`? |
| `error` | `codigo`, `mensaje`, `ref` (el `tipo` que lo causó, si se conoce) |

`miembros` es una lista `[{ "id": "m1", "nombre": "Ana", "conectado": true }]` solo con
miembros aprobados. `token` es secreto: guárdalo para `reconectar` y no lo muestres.

### Códigos de error

| `codigo` | Cuándo |
|---|---|
| `formato` | No es JSON, no es objeto, falta `tipo` o pasa de 4096 bytes |
| `tipo-desconocido` | `tipo` no existe o no vale en ese momento (p. ej. `crear` en `/ws/<CODIGO>`) |
| `campo-invalido` | Un campo falta, tiene otro tipo o se sale de rango (`mensaje` lo nombra) |
| `no-autorizado` | Acción de anfitrión hecha por otro, o miembro aún pendiente |
| `no-encontrado` | El `id`/`sid` no existe o no está en el estado correcto |
| `sala-no-existe` | Código sin sala activa |
| `sala-llena` | Ya hay 10 miembros |
| `token-invalido` | `reconectar` con token incorrecto o caducado |
| `limite` | Demasiados mensajes, chats o sugerencias seguidos |

## Ejemplo

```
A → /ws            {"tipo":"crear","nombre":"Ana"}
A ←                {"tipo":"bienvenida","codigo":"K7P2QX","id":"m1","token":"…","anfitrion":"m1","miembros":[…],"estado":null}
B → /ws/K7P2QX     {"tipo":"unirse","codigo":"K7P2QX","nombre":"Luis"}
B ←                {"tipo":"pendiente","id":"m2"}
A ←                {"tipo":"solicitud","id":"m2","nombre":"Luis"}
A →                {"tipo":"aprobar","id":"m2"}
B ←                {"tipo":"bienvenida",…}
A,B ←              {"tipo":"miembros","anfitrion":"m1","miembros":[…]}
A →                {"tipo":"estado",…}
B ←                {"tipo":"estado",…,"ts":1762400000000}
```
