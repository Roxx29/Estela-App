// Lógica pura de una sala (sin APIs de Cloudflare) para poder probarla con node --test.
// Protocolo: docs/protocolo.md

export const ALFABETO = "ABCDEFGHJKMNPQRSTUVWXYZ23456789";
export const MAX_MIEMBROS = 10; // aprobados
export const MAX_PENDIENTES = 5;
export const IDENT_MS = 10_000; // una conexión tiene 10 s para crear/unirse/reconectar
export const MAX_INTENTOS = 5; // mensajes de entrada por conexión sin identificar
export const MAX_BYTES = 4096;
export const GRACIA_MS = 30_000;
export const VACIA_MS = 600_000;
export const CHAT_MAX = 300;
const MAX_SUGERENCIAS = 20;

export type Miembro = {
  id: string; nombre: string; token: string;
  aprobado: boolean; conectado: boolean; alta: number; caido: number;
};
export type Sugerencia = { sid: number; id: string; nombre: string; titulo: string; artista: string; videoId?: string };
export type Sala = {
  codigo: string; anfitrion: string; miembros: Miembro[]; sugerencias: Sugerencia[];
  vaciaDesde: number; sigId: number; sigSid: number;
};
// Lo que no merece una escritura en disco: se pierde si el objeto hiberna.
export type Memoria = { estado: Record<string, unknown> | null; limites: Map<string, number[]> };
export type Adjunto = { id: string | null; nueva: boolean; codigo: string; abierta: number; intentos: number };
// `a` es un id de miembro o "yo" (la conexión que envió el mensaje).
export type Resultado = {
  salidas: { a: string; msg: Record<string, unknown> }[];
  cerrar: string[]; asignar?: string; nueva?: Sala; borrar?: boolean; guardar: boolean;
};

export const nuevaMemoria = (): Memoria => ({ estado: null, limites: new Map() });

export function nuevoCodigo(): string {
  let s = "";
  const b = new Uint8Array(1);
  while (s.length < 6) {
    crypto.getRandomValues(b);
    if (b[0] < 248) s += ALFABETO[b[0] % 31]; // 248 = 31·8, sin sesgo
  }
  return s;
}

export function normalizarCodigo(v: unknown): string | null {
  if (typeof v !== "string") return null;
  const c = v.trim().toUpperCase();
  return c.length === 6 && [...c].every((ch) => ALFABETO.includes(ch)) ? c : null;
}

const nuevoToken = () =>
  Array.from(crypto.getRandomValues(new Uint8Array(32)), (b) => b.toString(16).padStart(2, "0")).join("");

function igual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

const PROHIBIDOS = /[\u0000-\u001f\u007f-\u009f؜‎‏‪-‮⁦-⁩]/;

function texto(v: unknown, min: number, max: number): string | null {
  if (typeof v !== "string") return null;
  const t = v.trim();
  const n = [...t].length;
  return n >= min && n <= max && !PROHIBIDOS.test(t) ? t : null;
}

const numero = (v: unknown, min: number, max: number) =>
  typeof v === "number" && Number.isFinite(v) && v >= min && v <= max;

const videoIdValido = (v: unknown) => typeof v === "string" && /^[A-Za-z0-9_-]{11}$/.test(v);

function limitar(mem: Memoria, clave: string, ahora: number, max: number, ventana: number): boolean {
  const ts = (mem.limites.get(clave) ?? []).filter((t) => t > ahora - ventana);
  const ok = ts.length < max;
  if (ok) ts.push(ahora);
  mem.limites.set(clave, ts);
  return ok;
}

const vacio = (): Resultado => ({ salidas: [], cerrar: [], guardar: false });

function error(r: Resultado, codigo: string, mensaje: string, ref?: unknown, cerrar = false): Resultado {
  r.salidas.push({ a: "yo", msg: { tipo: "error", codigo, mensaje, ref: typeof ref === "string" ? ref : null } });
  if (cerrar) r.cerrar.push("yo");
  return r;
}

const conectados = (s: Sala) => s.miembros.filter((m) => m.aprobado && m.conectado);

const aprobados = (s: Sala) => s.miembros.filter((m) => m.aprobado).length;

const lista = (s: Sala) =>
  s.miembros.filter((m) => m.aprobado).map((m) => ({ id: m.id, nombre: m.nombre, conectado: m.conectado }));

function todos(r: Resultado, s: Sala, msg: Record<string, unknown>, excepto?: string) {
  for (const m of conectados(s)) if (m.id !== excepto) r.salidas.push({ a: m.id, msg });
}

const avisarMiembros = (r: Resultado, s: Sala) => todos(r, s, { tipo: "miembros", anfitrion: s.anfitrion, miembros: lista(s) });

// Solicitudes y sugerencias pendientes, para un anfitrión recién llegado o que vuelve.
function ponerAlDia(r: Resultado, s: Sala) {
  for (const m of s.miembros) if (!m.aprobado) r.salidas.push({ a: s.anfitrion, msg: { tipo: "solicitud", id: m.id, nombre: m.nombre } });
  for (const g of s.sugerencias) r.salidas.push({ a: s.anfitrion, msg: { tipo: "sugerencia", ...g } });
}

const bienvenida = (s: Sala, m: Miembro, mem: Memoria) => ({
  tipo: "bienvenida", codigo: s.codigo, id: m.id, token: m.token,
  anfitrion: s.anfitrion, miembros: lista(s), estado: mem.estado,
});

function nuevoMiembro(s: Sala | null, nombre: string, aprobado: boolean, ahora: number): Miembro {
  const id = `m${s ? s.sigId++ : 1}`;
  return { id, nombre, token: aprobado ? nuevoToken() : "", aprobado, conectado: true, alta: ahora, caido: 0 };
}

// Quita un miembro aprobado; si era el anfitrión, pasa al conectado más antiguo.
function quitar(r: Resultado, s: Sala, id: string, ahora: number) {
  s.miembros = s.miembros.filter((m) => m.id !== id);
  if (s.anfitrion === id) {
    const sig = conectados(s).sort((a, b) => a.alta - b.alta)[0];
    if (sig) { s.anfitrion = sig.id; ponerAlDia(r, s); }
  }
  if (!conectados(s).length && !s.vaciaDesde) s.vaciaDesde = ahora;
  avisarMiembros(r, s);
  r.guardar = true;
}

export function recibir(sala: Sala | null, mem: Memoria, att: Adjunto, data: unknown, ahora: number): Resultado {
  const r = vacio();
  if (!att.id && ++att.intentos > MAX_INTENTOS) return error(r, "limite", "Demasiados intentos", null, true);
  if (typeof data !== "string" || data.length > MAX_BYTES) return error(r, "formato", "Mensaje no válido");
  let m: Record<string, unknown>;
  try { m = JSON.parse(data); } catch { return error(r, "formato", "No es JSON"); }
  if (!m || typeof m !== "object" || Array.isArray(m) || typeof m.tipo !== "string") return error(r, "formato", "Falta tipo");
  const tipo = m.tipo;

  if (!att.id) return entrar(r, sala, mem, att, m, ahora);
  const yo = sala?.miembros.find((x) => x.id === att.id);
  if (!sala || !yo) { r.cerrar.push("yo"); return r; }
  if (!limitar(mem, `${yo.id}:msg`, ahora, 40, 10_000)) return error(r, "limite", "Demasiados mensajes", tipo);

  if (tipo === "latido") { r.salidas.push({ a: "yo", msg: { tipo: "latido" } }); return r; }
  if (tipo === "ping-reloj") {
    if (!numero(m.t0, -1e15, 1e15)) return error(r, "campo-invalido", "t0", tipo);
    r.salidas.push({ a: "yo", msg: { tipo: "pong-reloj", t0: m.t0, ts: ahora } });
    return r;
  }
  if (tipo === "salir") {
    const res = yo.aprobado ? (quitar(r, sala, yo.id, ahora), r) : desconectar(sala, yo.id, ahora);
    res.cerrar.push("yo");
    return res;
  }
  if (!yo.aprobado) return error(r, "no-autorizado", "Esperando aprobación", tipo);

  const esAnfitrion = sala.anfitrion === yo.id;
  const soloAnfitrion = ["aprobar", "rechazar", "expulsar", "transferir", "estado", "aprobar-sugerencia"];
  if (soloAnfitrion.includes(tipo) && !esAnfitrion) return error(r, "no-autorizado", "Solo el anfitrión", tipo);

  if (["aprobar", "rechazar", "expulsar", "transferir"].includes(tipo)) {
    const obj = typeof m.id === "string" ? sala.miembros.find((x) => x.id === m.id && x.id !== yo.id) : undefined;
    if (!obj) return error(r, "no-encontrado", "Miembro no encontrado", tipo);
    if ((tipo === "aprobar" || tipo === "rechazar") === obj.aprobado) return error(r, "no-encontrado", "Miembro en otro estado", tipo);
    r.guardar = true;
    if (tipo === "aprobar") {
      if (aprobados(sala) >= MAX_MIEMBROS) return error(r, "sala-llena", "La sala está llena", tipo);
      obj.aprobado = true; obj.token = nuevoToken(); obj.alta = ahora;
      r.salidas.push({ a: obj.id, msg: bienvenida(sala, obj, mem) });
      avisarMiembros(r, sala);
    } else if (tipo === "rechazar" || tipo === "expulsar") {
      r.salidas.push({ a: obj.id, msg: { tipo: tipo === "rechazar" ? "rechazado" : "expulsado" } });
      r.cerrar.push(obj.id);
      if (obj.aprobado) quitar(r, sala, obj.id, ahora);
      else sala.miembros = sala.miembros.filter((x) => x !== obj);
    } else {
      if (!obj.conectado) return error(r, "no-encontrado", "Miembro desconectado", tipo);
      sala.anfitrion = obj.id;
      avisarMiembros(r, sala);
      ponerAlDia(r, sala);
    }
    return r;
  }

  switch (tipo) {
    case "estado": {
      const t = texto(m.titulo, 0, 200), ar = texto(m.artista, 0, 200), al = texto(m.album, 0, 200), tr = texto(m.transicion, 0, 32);
      const malo =
        t === null ? "titulo" : ar === null ? "artista" : al === null ? "album" : tr === null ? "transicion"
        : !numero(m.duracion, 0, 86400) ? "duracion" : !numero(m.posicion, 0, 86400) ? "posicion"
        : typeof m.reproduciendo !== "boolean" ? "reproduciendo"
        : !(Number.isInteger(m.semilla) && numero(m.semilla, 0, 2147483647)) ? "semilla"
        : m.videoId !== undefined && !videoIdValido(m.videoId) ? "videoId" : null;
      if (malo) return error(r, "campo-invalido", malo, tipo);
      mem.estado = {
        tipo: "estado", titulo: t, artista: ar, album: al, duracion: m.duracion, posicion: m.posicion,
        reproduciendo: m.reproduciendo, semilla: m.semilla, transicion: tr, ts: ahora,
        ...(m.videoId !== undefined ? { videoId: m.videoId } : {}),
      };
      todos(r, sala, mem.estado, yo.id);
      return r;
    }
    case "chat": {
      const t = texto(m.texto, 1, CHAT_MAX);
      if (t === null) return error(r, "campo-invalido", "texto", tipo);
      if (!limitar(mem, `${yo.id}:chat`, ahora, 5, 10_000)) return error(r, "limite", "Espera un poco para escribir", tipo);
      todos(r, sala, { tipo: "chat", id: yo.id, nombre: yo.nombre, texto: t, ts: ahora });
      return r;
    }
    case "sugerir": {
      const t = texto(m.titulo, 1, 200), ar = texto(m.artista, 0, 200);
      const malo = t === null ? "titulo" : ar === null ? "artista" : m.videoId !== undefined && !videoIdValido(m.videoId) ? "videoId" : null;
      if (malo) return error(r, "campo-invalido", malo, tipo);
      if (!limitar(mem, `${yo.id}:sug`, ahora, 3, 30_000)) return error(r, "limite", "Demasiadas sugerencias", tipo);
      const g: Sugerencia = { sid: sala.sigSid++, id: yo.id, nombre: yo.nombre, titulo: t!, artista: ar!, ...(m.videoId !== undefined ? { videoId: m.videoId as string } : {}) };
      sala.sugerencias.push(g);
      if (sala.sugerencias.length > MAX_SUGERENCIAS) sala.sugerencias.shift();
      r.salidas.push({ a: sala.anfitrion, msg: { tipo: "sugerencia", ...g } });
      r.guardar = true;
      return r;
    }
    case "aprobar-sugerencia": {
      const g = sala.sugerencias.find((x) => x.sid === m.sid);
      if (!g) return error(r, "no-encontrado", "Sugerencia no encontrada", tipo);
      sala.sugerencias = sala.sugerencias.filter((x) => x !== g);
      todos(r, sala, { tipo: "sugerencia-aprobada", sid: g.sid, titulo: g.titulo, artista: g.artista, ...(g.videoId ? { videoId: g.videoId } : {}) });
      r.guardar = true;
      return r;
    }
  }
  return error(r, "tipo-desconocido", "Tipo desconocido", tipo);
}

function entrar(r: Resultado, sala: Sala | null, mem: Memoria, att: Adjunto, m: Record<string, unknown>, ahora: number): Resultado {
  const tipo = m.tipo as string;
  if (tipo === "crear" && att.nueva) {
    const nombre = texto(m.nombre, 1, 32);
    if (!nombre) return error(r, "campo-invalido", "nombre", tipo);
    const yo = nuevoMiembro(null, nombre, true, ahora);
    r.nueva = { codigo: att.codigo, anfitrion: yo.id, miembros: [yo], sugerencias: [], vaciaDesde: 0, sigId: 2, sigSid: 1 };
    r.asignar = yo.id;
    r.salidas.push({ a: "yo", msg: bienvenida(r.nueva, yo, mem) });
    r.guardar = true;
    return r;
  }
  if ((tipo !== "unirse" && tipo !== "reconectar") || att.nueva) return error(r, "tipo-desconocido", "Primero crear, unirse o reconectar", tipo);
  if (normalizarCodigo(m.codigo) !== att.codigo) return error(r, "campo-invalido", "codigo", tipo);
  if (!sala || !sala.miembros.some((x) => x.aprobado)) return error(r, "sala-no-existe", "La sala no existe", tipo, true);

  if (tipo === "reconectar") {
    const tk = m.token;
    const yo = typeof tk === "string" && tk.length <= 64 ? sala.miembros.find((x) => x.aprobado && igual(x.token, tk)) : undefined;
    if (!yo) return error(r, "token-invalido", "No se puede volver a la sala", tipo, true);
    r.cerrar.push(yo.id); // la conexión anterior, si sigue abierta
    yo.conectado = true; yo.caido = 0; sala.vaciaDesde = 0;
    r.asignar = yo.id;
    r.salidas.push({ a: "yo", msg: bienvenida(sala, yo, mem) });
    avisarMiembros(r, sala);
    if (sala.anfitrion === yo.id) ponerAlDia(r, sala);
    r.guardar = true;
    return r;
  }

  const nombre = texto(m.nombre, 1, 32);
  if (!nombre) return error(r, "campo-invalido", "nombre", tipo);
  if (aprobados(sala) >= MAX_MIEMBROS) return error(r, "sala-llena", "La sala está llena", tipo, true);
  if (sala.miembros.length - aprobados(sala) >= MAX_PENDIENTES) return error(r, "limite", "Hay demasiadas solicitudes; prueba en un rato", tipo, true);
  const yo = nuevoMiembro(sala, nombre, false, ahora);
  sala.miembros.push(yo);
  r.asignar = yo.id;
  r.salidas.push({ a: "yo", msg: { tipo: "pendiente", id: yo.id } });
  r.salidas.push({ a: sala.anfitrion, msg: { tipo: "solicitud", id: yo.id, nombre } });
  r.guardar = true;
  return r;
}

export function desconectar(sala: Sala, id: string, ahora: number): Resultado {
  const r = vacio();
  const yo = sala.miembros.find((x) => x.id === id);
  if (!yo) return r;
  r.guardar = true;
  if (!yo.aprobado) {
    sala.miembros = sala.miembros.filter((x) => x !== yo);
    r.salidas.push({ a: sala.anfitrion, msg: { tipo: "solicitud-cancelada", id } });
    return r;
  }
  yo.conectado = false; yo.caido = ahora;
  if (!conectados(sala).length) sala.vaciaDesde = ahora;
  if (sala.anfitrion === id) todos(r, sala, { tipo: "anfitrion-ausente", hasta: ahora + GRACIA_MS });
  else avisarMiembros(r, sala);
  return r;
}

// Se llama desde la alarma: aplica los 30 s de gracia y borra la sala vacía a los 10 min.
export function revisar(sala: Sala, ahora: number): Resultado {
  const r = vacio();
  if (!conectados(sala).length) {
    if (sala.vaciaDesde && ahora - sala.vaciaDesde >= VACIA_MS) r.borrar = true;
    return r;
  }
  for (const m of sala.miembros.filter((x) => x.aprobado && !x.conectado && ahora - x.caido >= GRACIA_MS)) quitar(r, sala, m.id, ahora);
  return r;
}

export function proximaRevision(sala: Sala): number | null {
  if (!conectados(sala).length) return sala.vaciaDesde ? sala.vaciaDesde + VACIA_MS : null;
  const caidos = sala.miembros.filter((m) => m.aprobado && !m.conectado).map((m) => m.caido + GRACIA_MS);
  return caidos.length ? Math.min(...caidos) : null;
}

// Conexiones que no se identificaron a tiempo (índices) y cuándo caduca la siguiente.
export function sinIdentificar(atts: (Adjunto | null)[], ahora: number): { caducadas: number[]; proxima: number | null } {
  const caducadas: number[] = [];
  let proxima: number | null = null;
  atts.forEach((a, i) => {
    if (!a || a.id) return;
    const fin = a.abierta + IDENT_MS;
    if (fin <= ahora) caducadas.push(i);
    else proxima = Math.min(proxima ?? fin, fin);
  });
  return { caducadas, proxima };
}

// A qué conexiones enviar y cuáles cerrar. `yo` ya cuenta con el id de `r.asignar`, para que
// una reconexión reciba lo suyo y solo se cierre la conexión vieja.
export function repartir<T>(r: Resultado, socks: T[], idDe: (s: T) => string | null | undefined, yo: T | null) {
  const id = (s: T) => (s === yo && r.asignar ? r.asignar : idDe(s));
  const destino = (a: string, cerrando: boolean) =>
    a === "yo" ? (yo ? [yo] : []) : socks.filter((s) => id(s) === a && !(cerrando && s === yo));
  return {
    envios: r.salidas.flatMap(({ a, msg }) => destino(a, false).map((s) => [s, JSON.stringify(msg)] as [T, string])),
    cierres: r.cerrar.flatMap((a) => destino(a, true)),
  };
}
