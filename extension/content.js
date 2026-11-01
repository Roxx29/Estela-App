// Cada segundo lee la cola de YouTube Music y ejecuta lo que pida Estela (reproducir una de la cola).
// Los selectores son los de la página de YouTube Music: si la web cambia, hay que ajustarlos aquí.
const text = (el, sel) => ((el.querySelector(sel) || {}).textContent || "").trim();
const items = () => [...document.querySelectorAll("ytmusic-player-queue-item")];

// Letra de la pestaña "Letra" de YouTube Music (con licencia de Google), una vez por canción. Si la pestaña
// no está abierta y se puede (ventana propia o pestaña en segundo plano), la abre, la lee y vuelve a "A continuación".
function estelaLyrics(key, canClick) {
  const old = window.__estelaLyr;
  const s = window.__estelaLyr = old && old.key === key ? old : {key, text: "", wait: 0, seen: 0, clicked: false, done: false};
  if (s.done || !key) return s.text;
  const tabs = document.querySelectorAll("ytmusic-player-page tp-yt-paper-tab");
  const tab = tabs[1];
  if (!tab || tab.hasAttribute("disabled") || tab.getAttribute("aria-disabled") === "true") {
    if (++s.wait > 8) s.done = true;  // canción sin letra en YouTube Music
    return "";
  }
  if (tab.getAttribute("aria-selected") !== "true" && !tab.classList.contains("iron-selected")) {
    if (canClick && !s.clicked) { s.clicked = true; tab.click(); }
    return "";
  }
  if (++s.seen < 3) return "";  // un momento: la de la canción anterior aún puede estar
  const d = document.querySelector("ytmusic-player-page ytmusic-description-shelf-renderer .description");
  s.text = d ? d.textContent.trim().slice(0, 20000) : "";
  if (s.text || s.seen > 10) {
    s.done = true;
    if (s.clicked && tabs[0]) tabs[0].click();
  }
  return s.text;
}

function read() {
  const m = navigator.mediaSession && navigator.mediaSession.metadata;
  return {
    now: { title: m ? m.title : "", artist: m ? m.artist : "" },
    // en una pestaña a la vista no se cambia de pestaña interna: solo se lee si el usuario abrió "Letra"
    lyrics: m ? estelaLyrics(m.title + "|" + m.artist, document.hidden) : "",
    queue: items().map((q) => ({
      title: text(q, ".song-title"),
      artist: text(q, ".byline"),
      thumb: (q.querySelector(".thumbnail img") || {}).src || "",
      selected: q.hasAttribute("selected") || ["playing", "paused"].includes(q.getAttribute("play-button-state")),
    })),
  };
}

function run(cmd) {
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
