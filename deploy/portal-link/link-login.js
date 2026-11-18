
// Mostra l'enllaç "Gestió d'accessos" (DufsHelper) només si hi ha un usuari loginat a dufs.
(function () {
  var link = document.querySelector('.helper-link');
  if (!link) return;
  try {
    var tpl = document.getElementById('index-data');
    var raw = atob(tpl.innerHTML.trim());
    var bytes = Uint8Array.from(raw, function (c) { return c.charCodeAt(0); });
    var data = JSON.parse(new TextDecoder().decode(bytes));
    if (data.user) link.style.display = '';
  } catch (e) { /* sense dades: l'enllaç es queda amagat */ }
})();
