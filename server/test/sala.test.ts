// node --test "test/*.test.ts"  (Node >= 23.6, que ejecuta TypeScript sin compilar)
import { test } from "node:test";
import assert from "node:assert/strict";
import * as S from "../src/sala.ts";

const COD = "K7P2QX";
const att = (id: string | null, nueva = false): S.Adjunto => ({ id, nueva, codigo: COD, abierta: 0, intentos: 0 });
const j = (o: unknown) => JSON.stringify(o);
const para = (r: S.Resultado, a: string) => r.salidas.filter((s) => s.a === a).map((s) => s.msg);
const tipos = (r: S.Resultado, a: string) => para(r, a).map((m) => m.tipo);

// Sala con anfitrión m1 (Ana) y m2 (Luis) aprobado.
function salaConDos(t = 0) {
  const mem = S.nuevaMemoria();
  const r = S.recibir(null, mem, att(null, true), j({ tipo: "crear", nombre: "Ana" }), t);
  const sala = r.nueva!;
  const u = S.recibir(sala, mem, att(null), j({ tipo: "unirse", codigo: COD.toLowerCase(), nombre: "Luis" }), t);
  assert.equal(u.asignar, "m2");
  assert.deepEqual(para(u, "m1")[0], { tipo: "solicitud", id: "m2", nombre: "Luis" });
  const a = S.recibir(sala, mem, att("m1"), j({ tipo: "aprobar", id: "m2" }), t);
  const bienv = para(a, "m2")[0];
  assert.equal(bienv.tipo, "bienvenida");
  return { sala, mem, token2: bienv.token as string, tokenAna: (para(r, "yo")[0].token as string) };
}

test("códigos de 6 caracteres sin ambiguos", () => {
  for (let i = 0; i < 500; i++) assert.match(S.nuevoCodigo(), /^[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{6}$/);
  assert.equal(S.normalizarCodigo(" k7p2qx "), COD);
  assert.equal(S.normalizarCodigo("K7P2Q0"), null);
  assert.equal(S.normalizarCodigo(123), null);
});

test("crear da bienvenida con token secreto", () => {
  const r = S.recibir(null, S.nuevaMemoria(), att(null, true), j({ tipo: "crear", nombre: " Ana " }), 0);
  const b = para(r, "yo")[0];
  assert.equal(b.tipo, "bienvenida");
  assert.equal(b.anfitrion, "m1");
  assert.match(b.token as string, /^[0-9a-f]{64}$/);
  assert.deepEqual(b.miembros, [{ id: "m1", nombre: "Ana", conectado: true }]);
});

test("crear solo en conexión nueva; unirse no en conexión nueva", () => {
  const { sala, mem } = salaConDos();
  assert.equal(para(S.recibir(sala, mem, att(null), j({ tipo: "crear", nombre: "X" }), 0), "yo")[0].codigo, "tipo-desconocido");
  assert.equal(para(S.recibir(null, mem, att(null, true), j({ tipo: "unirse", codigo: COD, nombre: "X" }), 0), "yo")[0].codigo, "tipo-desconocido");
});

test("errores de formato y validación", () => {
  const { sala, mem } = salaConDos();
  const err = (d: unknown, id = "m1") => para(S.recibir(sala, mem, att(id), d, 0), "yo")[0];
  assert.equal(err("no json").codigo, "formato");
  assert.equal(err("[1]").codigo, "formato");
  assert.equal(err("x".repeat(5000)).codigo, "formato");
  assert.equal(err(j({ tipo: "volar" })).codigo, "tipo-desconocido");
  assert.equal(err(j({ tipo: "chat", texto: "" })).codigo, "campo-invalido");
  assert.equal(err(j({ tipo: "chat", texto: "a".repeat(301) })).codigo, "campo-invalido");
  assert.equal(err(j({ tipo: "chat", texto: "hola‮" })).codigo, "campo-invalido");
  assert.equal(err(j({ tipo: "ping-reloj", t0: "1" })).codigo, "campo-invalido");
  const ok = { tipo: "estado", titulo: "T", artista: "A", album: "", duracion: 200, posicion: 10, reproduciendo: true, semilla: 7, transicion: "viaje" };
  assert.equal(err(j({ ...ok, posicion: -1 })).mensaje, "posicion");
  assert.equal(err(j({ ...ok, semilla: 1.5 })).mensaje, "semilla");
  assert.equal(err(j({ ...ok, reproduciendo: 1 })).mensaje, "reproduciendo");
  assert.equal(err(j({ ...ok, videoId: "corto" })).mensaje, "videoId");
  assert.equal(err(j({ ...ok, titulo: 5 })).mensaje, "titulo");
});

test("solo el anfitrión envía estado; los demás lo reciben con ts", () => {
  const { sala, mem } = salaConDos();
  const est = { tipo: "estado", titulo: "T", artista: "A", album: "B", duracion: 200, posicion: 10, reproduciendo: true, semilla: 7, transicion: "viaje", videoId: "dQw4w9WgXcQ" };
  assert.equal(para(S.recibir(sala, mem, att("m2"), j(est), 5), "yo")[0].codigo, "no-autorizado");
  const r = S.recibir(sala, mem, att("m1"), j(est), 5);
  assert.deepEqual(para(r, "m2")[0], { ...est, ts: 5 });
  assert.equal(para(r, "m1").length, 0);
  // El que entra después lo recibe en la bienvenida.
  S.recibir(sala, mem, att(null), j({ tipo: "unirse", codigo: COD, nombre: "Eva" }), 6);
  const b = para(S.recibir(sala, mem, att("m1"), j({ tipo: "aprobar", id: "m3" }), 6), "m3")[0];
  assert.equal((b.estado as Record<string, unknown>).posicion, 10);
});

test("pendiente no puede chatear; rechazar lo cierra", () => {
  const { sala, mem } = salaConDos();
  S.recibir(sala, mem, att(null), j({ tipo: "unirse", codigo: COD, nombre: "Eva" }), 0);
  assert.equal(para(S.recibir(sala, mem, att("m3"), j({ tipo: "chat", texto: "hola" }), 0), "yo")[0].codigo, "no-autorizado");
  assert.equal(para(S.recibir(sala, mem, att("m2"), j({ tipo: "rechazar", id: "m3" }), 0), "yo")[0].codigo, "no-autorizado");
  const r = S.recibir(sala, mem, att("m1"), j({ tipo: "rechazar", id: "m3" }), 0);
  assert.deepEqual(tipos(r, "m3"), ["rechazado"]);
  assert.deepEqual(r.cerrar, ["m3"]);
  assert.equal(sala.miembros.length, 2);
});

test("chat a todos con límite de frecuencia", () => {
  const { sala, mem } = salaConDos();
  for (let i = 0; i < 5; i++) {
    const r = S.recibir(sala, mem, att("m2"), j({ tipo: "chat", texto: ` hola ${i} ` }), 1000 + i);
    assert.deepEqual(para(r, "m1")[0], { tipo: "chat", id: "m2", nombre: "Luis", texto: `hola ${i}`, ts: 1000 + i });
  }
  assert.equal(para(S.recibir(sala, mem, att("m2"), j({ tipo: "chat", texto: "otra" }), 1006), "yo")[0].codigo, "limite");
  assert.equal(tipos(S.recibir(sala, mem, att("m2"), j({ tipo: "chat", texto: "ya" }), 20_000), "m1")[0], "chat");
});

test("límite de mensajes por miembro", () => {
  const { sala, mem } = salaConDos();
  for (let i = 0; i < 40; i++) assert.equal(tipos(S.recibir(sala, mem, att("m2"), j({ tipo: "latido" }), 0), "yo")[0], "latido");
  assert.equal(para(S.recibir(sala, mem, att("m2"), j({ tipo: "latido" }), 0), "yo")[0].codigo, "limite");
});

test("ping-reloj devuelve t0 y la hora del servidor", () => {
  const { sala, mem } = salaConDos();
  assert.deepEqual(para(S.recibir(sala, mem, att("m2"), j({ tipo: "ping-reloj", t0: 123.5 }), 999), "yo")[0], { tipo: "pong-reloj", t0: 123.5, ts: 999 });
});

test("sugerir y aprobar sugerencia", () => {
  const { sala, mem } = salaConDos();
  const r = S.recibir(sala, mem, att("m2"), j({ tipo: "sugerir", titulo: "Canción", artista: "X" }), 0);
  assert.deepEqual(para(r, "m1")[0], { tipo: "sugerencia", sid: 1, id: "m2", nombre: "Luis", titulo: "Canción", artista: "X" });
  assert.equal(para(S.recibir(sala, mem, att("m2"), j({ tipo: "aprobar-sugerencia", sid: 1 }), 0), "yo")[0].codigo, "no-autorizado");
  const a = S.recibir(sala, mem, att("m1"), j({ tipo: "aprobar-sugerencia", sid: 1 }), 0);
  assert.equal(tipos(a, "m2")[0], "sugerencia-aprobada");
  assert.equal(sala.sugerencias.length, 0);
  assert.equal(para(S.recibir(sala, mem, att("m1"), j({ tipo: "aprobar-sugerencia", sid: 1 }), 0), "yo")[0].codigo, "no-encontrado");
});

test("expulsar y transferir", () => {
  const { sala, mem } = salaConDos();
  const t = S.recibir(sala, mem, att("m1"), j({ tipo: "transferir", id: "m2" }), 0);
  assert.equal(sala.anfitrion, "m2");
  assert.equal(para(t, "m1")[0].anfitrion, "m2");
  const e = S.recibir(sala, mem, att("m2"), j({ tipo: "expulsar", id: "m1" }), 0);
  assert.deepEqual(tipos(e, "m1")[0], "expulsado");
  assert.deepEqual(e.cerrar, ["m1"]);
  assert.deepEqual(sala.miembros.map((m) => m.id), ["m2"]);
});

test("reconectar con token: correcto, incorrecto y cierre de la conexión vieja", () => {
  const { sala, mem, token2 } = salaConDos();
  S.desconectar(sala, "m2", 100);
  const mal = S.recibir(sala, mem, att(null), j({ tipo: "reconectar", codigo: COD, token: "x".repeat(64) }), 200);
  assert.equal(para(mal, "yo")[0].codigo, "token-invalido");
  assert.deepEqual(mal.cerrar, ["yo"]);
  const r = S.recibir(sala, mem, att(null), j({ tipo: "reconectar", codigo: COD, token: token2 }), 200);
  assert.equal(r.asignar, "m2");
  assert.deepEqual(r.cerrar, ["m2"]);
  assert.equal(para(r, "yo")[0].tipo, "bienvenida");
  assert.equal(sala.miembros[1].conectado, true);
});

test("anfitrión caído: 30 s de gracia y luego pasa al más antiguo", () => {
  const { sala, mem } = salaConDos(0);
  S.recibir(sala, mem, att(null), j({ tipo: "unirse", codigo: COD, nombre: "Eva" }), 1);
  S.recibir(sala, mem, att("m1"), j({ tipo: "aprobar", id: "m3" }), 2);
  const d = S.desconectar(sala, "m1", 1000);
  assert.deepEqual(para(d, "m2")[0], { tipo: "anfitrion-ausente", hasta: 1000 + S.GRACIA_MS });
  assert.equal(S.proximaRevision(sala), 1000 + S.GRACIA_MS);
  assert.equal(S.revisar(sala, 1000 + S.GRACIA_MS - 1).salidas.length, 0);
  assert.equal(sala.anfitrion, "m1");
  const r = S.revisar(sala, 1000 + S.GRACIA_MS);
  assert.equal(sala.anfitrion, "m2");
  assert.equal(para(r, "m3")[0].anfitrion, "m2");
  assert.equal(S.proximaRevision(sala), null);
});

test("anfitrión que sale con salir: pasa al momento", () => {
  const { sala, mem } = salaConDos();
  const r = S.recibir(sala, mem, att("m1"), j({ tipo: "salir" }), 0);
  assert.ok(r.cerrar.includes("yo"));
  assert.equal(sala.anfitrion, "m2");
});

test("solicitud pendiente se reenvía al nuevo anfitrión; se cancela si el pendiente se va", () => {
  const { sala, mem } = salaConDos();
  S.recibir(sala, mem, att(null), j({ tipo: "unirse", codigo: COD, nombre: "Eva" }), 0);
  const t = S.recibir(sala, mem, att("m1"), j({ tipo: "transferir", id: "m2" }), 0);
  assert.deepEqual(para(t, "m2").find((m) => m.tipo === "solicitud"), { tipo: "solicitud", id: "m3", nombre: "Eva" });
  assert.deepEqual(para(S.desconectar(sala, "m3", 0), "m2")[0], { tipo: "solicitud-cancelada", id: "m3" });
});

test("sala vacía se borra a los 10 min; sin sala no se puede entrar", () => {
  const { sala, mem } = salaConDos();
  S.desconectar(sala, "m2", 0);
  S.desconectar(sala, "m1", 10);
  assert.equal(S.proximaRevision(sala), 10 + S.VACIA_MS);
  assert.ok(!S.revisar(sala, 10 + S.GRACIA_MS).borrar); // nadie conectado: no se quita a nadie
  assert.equal(sala.miembros.length, 2);
  assert.ok(S.revisar(sala, 10 + S.VACIA_MS).borrar);
  const r = S.recibir(null, mem, att(null), j({ tipo: "unirse", codigo: COD, nombre: "X" }), 0);
  assert.equal(para(r, "yo")[0].codigo, "sala-no-existe");
});

test("máximo de aprobados y límite propio de 5 solicitudes pendientes", () => {
  const { sala, mem } = salaConDos();
  const unirse = (n: string) => S.recibir(sala, mem, att(null), j({ tipo: "unirse", codigo: COD, nombre: n }), 0);
  for (let i = 0; i < S.MAX_PENDIENTES; i++) assert.equal(para(unirse(`p${i}`), "yo")[0].tipo, "pendiente");
  const r = unirse("sobra");
  assert.equal(para(r, "yo")[0].codigo, "limite");
  assert.deepEqual(r.cerrar, ["yo"]);
  // Los pendientes no ocupan plaza: se pueden aprobar hasta MAX_MIEMBROS.
  for (const m of sala.miembros.filter((x) => !x.aprobado)) S.recibir(sala, mem, att("m1"), j({ tipo: "aprobar", id: m.id }), 0);
  assert.equal(sala.miembros.filter((x) => x.aprobado).length, 7);
  for (let i = 0; i < S.MAX_MIEMBROS - 7; i++) {
    const id = para(unirse(`q${i}`), "yo")[0].id;
    S.recibir(sala, mem, att("m1"), j({ tipo: "aprobar", id }), 0);
  }
  assert.equal(para(unirse("once"), "yo")[0].codigo, "sala-llena");
});

test("conexión sin identificar: máx. 5 intentos y caduca a los 10 s", () => {
  const mem = S.nuevaMemoria();
  const a = att(null);
  for (let i = 0; i < S.MAX_INTENTOS; i++) assert.equal(para(S.recibir(null, mem, a, "basura", 0), "yo")[0].codigo, "formato");
  const r = S.recibir(null, mem, a, "basura", 0);
  assert.equal(para(r, "yo")[0].codigo, "limite");
  assert.deepEqual(r.cerrar, ["yo"]);

  const atts = [{ ...att(null), abierta: 0 }, { ...att(null), abierta: 5000 }, { ...att("m1"), abierta: 0 }, null];
  assert.deepEqual(S.sinIdentificar(atts, S.IDENT_MS - 1), { caducadas: [], proxima: S.IDENT_MS });
  assert.deepEqual(S.sinIdentificar(atts, S.IDENT_MS), { caducadas: [0], proxima: 5000 + S.IDENT_MS });
});

test("anfitrión que reconecta recibe en la conexión nueva las solicitudes pendientes; solo se cierra la vieja", () => {
  const { sala, mem, tokenAna } = salaConDos();
  S.desconectar(sala, "m1", 0); // el anfitrión se cae (gracia)
  S.recibir(sala, mem, att(null), j({ tipo: "unirse", codigo: COD, nombre: "Eva" }), 1);
  S.recibir(sala, mem, att("m2"), j({ tipo: "sugerir", titulo: "Canción", artista: "X" }), 1);
  const r = S.recibir(sala, mem, att(null), j({ tipo: "reconectar", codigo: COD, token: tokenAna }), 2);
  const vieja = { id: "m1" }, nueva = { id: null as string | null }, luis = { id: "m2" };
  const { envios, cierres } = S.repartir(r, [vieja, nueva, luis], (s) => s.id, nueva);
  const aNueva = envios.filter(([s]) => s === nueva).map(([, t]) => JSON.parse(t).tipo);
  assert.ok(aNueva.includes("bienvenida") && aNueva.includes("solicitud") && aNueva.includes("sugerencia"), aNueva.join());
  assert.deepEqual(cierres, [vieja]);
});

test("repartir: \"yo\" se cierra aunque tenga id; expulsar cierra a otro", () => {
  const { sala, mem } = salaConDos();
  const ana = { id: "m1" }, luis = { id: "m2" };
  const e = S.recibir(sala, mem, att("m1"), j({ tipo: "expulsar", id: "m2" }), 0);
  const x = S.repartir(e, [ana, luis], (s) => s.id, ana);
  assert.deepEqual(x.cierres, [luis]);
  assert.equal(JSON.parse(x.envios.find(([s]) => s === luis)![1]).tipo, "expulsado");
  const sal = S.recibir(sala, mem, att("m1"), j({ tipo: "salir" }), 0);
  assert.deepEqual(S.repartir(sal, [ana], (s) => s.id, ana).cierres, [ana]);
});
