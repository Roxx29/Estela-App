// Cada segundo lee la cola de YouTube Music y ejecuta lo que pida Estela (reproducir una de la cola).
// Los selectores son los de la página de YouTube Music: si la web cambia, hay que ajustarlos aquí.
const text = (el, sel) => ((el.querySelector(sel) || {}).textContent || "").trim();
const items = () => [...document.querySelectorAll("ytmusic-player-queue-item")];

// Letra de la pestaña "Letra" de YouTube Music (con licencia de Google), una vez por canción. Si la pestaña
// no está abierta y se puede (ventana propia o pestaña en segundo plano), la abre, la lee y vuelve a "A continuación".
function estelaLyrics(key, canClick) {
  const old = window.__estelaLyr;
  let s = old;
  if (!old || old.key !== key) {
    // si cambió la canción a mitad de una lectura, se hereda el clic para volver luego a la pestaña del usuario
    const back = old && !old.done && old.clicked ? old.back : -1;
    s = window.__estelaLyr = {key, text: "", wait: 0, seen: 0, clicked: back >= 0, back, done: false,
                              prev: old ? old.text || old.prev : ""};
  }
  if (s.done || !key) return s.text;
  const tabs = [...document.querySelectorAll("ytmusic-player-page tp-yt-paper-tab")];
  const tab = tabs[1];
  const finish = () => { s.done = true; if (s.clicked && tabs[s.back]) tabs[s.back].click(); };
  if (!tab || tab.hasAttribute("disabled") || tab.getAttribute("aria-disabled") === "true") {
    if (++s.wait > 8) finish();  // canción sin letra en YouTube Music
    return "";
  }
  if (tab.getAttribute("aria-selected") !== "true" && !tab.classList.contains("iron-selected")) {
    if (canClick && !s.clicked) {
      s.back = Math.max(0, tabs.findIndex((t) => t.getAttribute("aria-selected") === "true"));
      s.clicked = true;
      tab.click();
    }
    return "";
  }
  if (++s.seen < 3) return "";
  const d = document.querySelector("ytmusic-player-page ytmusic-description-shelf-renderer .description");
  const t = d ? d.textContent.trim().slice(0, 20000) : "";
  if (t && t === s.prev && s.seen <= 10) return "";  // aún es la letra de la canción anterior
  s.text = t === s.prev ? "" : t;
  if (s.text || s.seen > 10) finish();
  return s.text;
}

function read() {
  const m = navigator.mediaSession && navigator.mediaSession.metadata;
  return {
    now: { title: m ? m.title : "", artist: m ? m.artist : "" },
    // en una pestaña a la vista no se cambia de pestaña interna: solo se lee si el usuario abrió "Letra"
    lyrics: m ? estelaLyrics(m.title + "|" + m.artist, document.hidden) : "",
    // lo publica page.js (el reproductor solo se ve desde el mundo de la página)
    volume: document.documentElement.dataset.estelaVolume ? Number(document.documentElement.dataset.estelaVolume) : null,
    queue: items().map((q) => ({
      title: text(q, ".song-title"),
      artist: text(q, ".byline"),
      thumb: (q.querySelector(".thumbnail img") || {}).src || "",
      selected: q.hasAttribute("selected") || ["playing", "paused"].includes(q.getAttribute("play-button-state")),
    })),
  };
}

function run(cmd) {
  if (cmd && cmd.name === "volume" && Number.isInteger(cmd.index)) {
    document.dispatchEvent(new CustomEvent("estela-volume", { detail: cmd.index }));  // lo aplica page.js
  }
  if (cmd && cmd.name === "queue" && Number.isInteger(cmd.index)) {
    // por título: si la cola cambió en ese segundo, el índice ya no es el de esa canción
    const all = items();
    const q = all.find((it) => cmd.title && text(it, ".song-title") === cmd.title) || (!cmd.title && all[cmd.index]);
    if (q) (q.querySelector("ytmusic-play-button-renderer") || q).click();
  }
}

setInterval(() => {
  try {
    chrome.runtime.sendMessage(read(), (res) => {
      if (chrome.runtime.lastError || !res) return;
      (res.commands || []).forEach(run);
    });
  } catch (e) {
    // la extensión se recargó: esta pestaña deja de enviar hasta recargarla
  }
}, 1000);
