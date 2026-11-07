import { DurableObject } from "cloudflare:workers";
import * as S from "./sala.ts";

type Limitador = { limit(o: { key: string }): Promise<{ success: boolean }> };
type Env = { SALAS: DurableObjectNamespace<SalaDO>; LIMITE_IP: Limitador; LIMITE_CREAR: Limitador };

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);
    const ruta = url.pathname.match(/^\/ws(?:\/([^/]+))?\/?$/);
    if (!ruta) return new Response(url.pathname === "/" ? "Estela: servidor de Escuchar juntos" : "No encontrado", { status: url.pathname === "/" ? 200 : 404 });
    if (req.headers.get("Upgrade")?.toLowerCase() !== "websocket") return new Response("Se espera WebSocket", { status: 426 });

    const ip = req.headers.get("CF-Connecting-IP") ?? "local";
    if (!(await env.LIMITE_IP.limit({ key: ip })).success) return new Response("Demasiadas conexiones", { status: 429 });

    const reenviar = (codigo: string, nueva: boolean) => {
      const h = new Headers(req.headers);
      h.set("X-Estela-Codigo", codigo);
      h.set("X-Estela-Nueva", nueva ? "1" : "0");
      return env.SALAS.get(env.SALAS.idFromName(codigo)).fetch(new Request(req.url, { headers: h }));
    };

    if (ruta[1] !== undefined) {
      const codigo = S.normalizarCodigo(ruta[1]);
      return codigo ? reenviar(codigo, false) : new Response("Código no válido", { status: 400 });
    }
    if (!(await env.LIMITE_CREAR.limit({ key: ip })).success) return new Response("Demasiadas salas nuevas", { status: 429 });
    for (let i = 0; i < 5; i++) {
      const res = await reenviar(S.nuevoCodigo(), true);
      if (res.status !== 409) return res; // 409: código ya en uso, probar otro
    }
    return new Response("Inténtalo de nuevo", { status: 503 });
  },
};

export class SalaDO extends DurableObject<Env> {
  sala: S.Sala | null = null;
  mem = S.nuevaMemoria();

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    ctx.blockConcurrencyWhile(async () => { this.sala = (await ctx.storage.get<S.Sala>("sala")) ?? null; });
  }

  async fetch(req: Request): Promise<Response> {
    const nueva = req.headers.get("X-Estela-Nueva") === "1";
    if (nueva && (this.sala || this.ctx.getWebSockets().length)) return new Response(null, { status: 409 });
    const { 0: cliente, 1: servidor } = new WebSocketPair();
    this.ctx.acceptWebSocket(servidor);
    const att: S.Adjunto = { id: null, nueva, codigo: req.headers.get("X-Estela-Codigo") ?? "" };
    servidor.serializeAttachment(att);
    return new Response(null, { status: 101, webSocket: cliente });
  }

  async webSocketMessage(ws: WebSocket, data: string | ArrayBuffer) {
    const att = ws.deserializeAttachment() as S.Adjunto;
    await this.aplicar(ws, S.recibir(this.sala, this.mem, att, data, Date.now()));
  }

  async webSocketClose(ws: WebSocket) {
    try { ws.close(); } catch { /* ya cerrado */ }
    const att = ws.deserializeAttachment() as S.Adjunto | null;
    if (att?.id && this.sala) await this.aplicar(ws, S.desconectar(this.sala, att.id, Date.now()));
  }

  async webSocketError(ws: WebSocket) { await this.webSocketClose(ws); }

  async alarm() {
    if (this.sala) await this.aplicar(null, S.revisar(this.sala, Date.now()));
  }

  async aplicar(ws: WebSocket | null, r: S.Resultado) {
    if (r.nueva) this.sala = r.nueva;
    const socks = this.ctx.getWebSockets();
    const idDe = (s: WebSocket) => (s.deserializeAttachment() as S.Adjunto | null)?.id;
    const destino = (a: string) => (a === "yo" ? (ws ? [ws] : []) : socks.filter((s) => idDe(s) === a));

    for (const { a, msg } of r.salidas) {
      const txt = JSON.stringify(msg);
      for (const s of destino(a)) try { s.send(txt); } catch { /* conexión caída */ }
    }
    // Antes de asignar: así una reconexión cierra solo la conexión vieja.
    for (const a of r.cerrar) for (const s of destino(a)) {
      const att = s.deserializeAttachment() as S.Adjunto;
      s.serializeAttachment({ ...att, id: null }); // que su cierre no cuente como desconexión
      try { s.close(4000, "fin"); } catch { /* ya cerrado */ }
    }
    if (r.asignar && ws) ws.serializeAttachment({ ...(ws.deserializeAttachment() as S.Adjunto), id: r.asignar });

    if (r.borrar) {
      this.sala = null;
      for (const s of this.ctx.getWebSockets()) try { s.close(4000, "sala cerrada"); } catch { /* ya cerrado */ }
      await this.ctx.storage.deleteAlarm();
      await this.ctx.storage.deleteAll();
      return;
    }
    if (!r.guardar || !this.sala) return;
    await this.ctx.storage.put("sala", this.sala);
    const t = S.proximaRevision(this.sala);
    if (t) await this.ctx.storage.setAlarm(t);
    else await this.ctx.storage.deleteAlarm();
  }
}
