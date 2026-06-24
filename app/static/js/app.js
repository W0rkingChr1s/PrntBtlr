// PrntBtlr — tiny vanilla helpers. No framework, works fully offline.
window.PrntBtlr = (function () {
  // Periodically fetch an HTML fragment and swap it into a container.
  function poll(url, targetId, intervalMs) {
    const el = document.getElementById(targetId);
    if (!el) return;
    let timer = null;

    async function tick() {
      try {
        const res = await fetch(url, { headers: { "X-Requested-With": "fetch" } });
        if (res.ok) el.innerHTML = await res.text();
      } catch (_) {
        /* transient network/host hiccup — keep the last good render */
      }
    }

    function start() { timer = setInterval(tick, intervalMs); }
    function stop() { if (timer) clearInterval(timer); timer = null; }

    // Pause polling while the tab is hidden to spare the Pi.
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) stop(); else { tick(); start(); }
    });
    start();
  }

  // Auto-dismiss success flashes after a few seconds.
  document.addEventListener("DOMContentLoaded", () => {
    const flash = document.querySelector(".flash-success");
    if (flash) setTimeout(() => flash.remove(), 5000);
  });

  return { poll };
})();
