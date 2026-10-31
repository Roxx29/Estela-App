// Lleva lo que lee la pestaña de YouTube Music a Estela (solo en este PC) y devuelve sus órdenes.
const APP = "http://127.0.0.1:38917/state";

chrome.runtime.onMessage.addListener((state, sender, reply) => {
  fetch(APP, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(state) })
    .then((r) => r.json())
    .then(reply)
    .catch(() => reply({ commands: [] })); // la app está cerrada: no pasa nada
  return true; // respuesta asíncrona
});
