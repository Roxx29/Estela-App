// Cada segundo lee la cola de YouTube Music y ejecuta lo que pida Estela (reproducir una de la cola).
// Los selectores son los de la página de YouTube Music: si la web cambia, hay que ajustarlos aquí.
const text = (el, sel) => ((el.querySelector(sel) || {}).textContent || "").trim();
const items = () => [...document.querySelectorAll("ytmusic-player-queue-item")];

function read() {
  const m = navigator.mediaSession && navigator.mediaSession.metadata;
  return {
    now: { title: m ? m.title : "", artist: m ? m.artist : "" },
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
