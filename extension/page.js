// Corre en el mundo de la página: el reproductor de YouTube Music (#movie_player) solo se ve desde aquí.
// Publica el volumen para content.js y aplica el que pide Estela (mando del móvil). No lee nada más.
const player = () => document.getElementById("movie_player");

setInterval(() => {
  const p = player();
  if (p && p.getVolume) document.documentElement.dataset.estelaVolume = String(p.getVolume());
}, 1000);

document.addEventListener("estela-volume", (e) => {
  const v = Number(e.detail), p = player();
  if (!p || !p.setVolume || !Number.isInteger(v) || v < 0 || v > 100) return;
  p.setVolume(v);
  if (v > 0 && p.isMuted && p.isMuted()) p.unMute();
});
