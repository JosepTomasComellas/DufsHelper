# DufsHelper

Gestió web de credencials per a [dufs](https://github.com/sigoden/dufs), en un únic contenidor que conviu amb el `compose.yaml` existent. Substitueix el fitxer `.env` + les línies `--auth` del compose.

- **Admin** (`HELPER_ADMINS`, per defecte `admin`): crea/esborra usuaris, canvia contrasenyes i edita permisos per carpeta (`/Carpeta:rw` / `:ro`), inclòs l'accés anònim. Les carpetes noves es creen soles.
- **Qualsevol usuari**: es valida amb el seu mateix usuari/contrasenya de dufs i pot canviar la seva contrasenya.
- Sense base de dades ni JavaScript: `users.json` (contrasenyes **hashejades** SHA-512) → es genera `dufs.yaml` → dufs el llegeix amb `--config`.
- Dufs no recarrega les credencials en calent, així que el helper el **reinicia** via el socket Docker (5 s després del darrer canvi; els canvis propers s'agrupen).

## Com funciona

```
navegador ─► Traefik / nginx ─┬─ /          ─► dufs       (lector de ./dufshelper/dufs.yaml)
                              └─ /helper/   ─► dufshelper (escriu ./dufshelper/{users.json,dufs.yaml}
                                                           i reinicia dufs pel socket Docker)
```

## Instal·lació nova

```sh
mkdir -p dufshelper
docker run --rm -v "$PWD/dufshelper:/state" ghcr.io/joseptomascomellas/dufshelper:latest init
#  -> imprimeix la contrasenya de l'usuari «admin» (anota-la)
```

Copia [`deploy/compose.tute.yaml`](deploy/compose.tute.yaml) (Traefik) o [`deploy/compose.examen.yaml`](deploy/compose.examen.yaml) (nginx) com a `compose.yaml`, ajusta-hi el domini i fes `docker compose up -d`. A la variant nginx, afegeix també [`deploy/nginx/helper.conf.snippet`](deploy/nginx/helper.conf.snippet). Entra a `https://<el-teu-portal>/helper/`.

## Migrar un desplegament existent (`.env` + `--auth`)

Des de `/docker/dufs`:

```sh
cp compose.yaml compose.yaml.pre-helper && cp .env .env.pre-helper     # còpia de seguretat
docker run --rm -v "$PWD:/project:ro" -v "$PWD/dufshelper:/state" \
  ghcr.io/joseptomascomellas/dufshelper:latest import
#  llegeix /project/compose.yaml + /project/.env i hasheja les contrasenyes
```

Mostra els usuaris i regles importats: **comprova'ls**. Després substitueix el `compose.yaml` per la plantilla (conservant el teu domini/xarxes), `docker compose up -d` i, quan tot funcioni, elimina el `.env` (conté contrasenyes en clar).

## Enllaç «Gestió d'accessos» al portal

Només es mostra si hi ha un usuari loginat a dufs. A la barra superior (`.top`) afegeix:

```html
<a class="helper-link" href="/helper/" style="display:none">Gestió d'accessos</a>
```

i incorpora [`deploy/portal-link/helper-link.css`](deploy/portal-link/helper-link.css) (estil) i [`deploy/portal-link/link-login.js`](deploy/portal-link/link-login.js) (el mostra quan `DATA.user` existeix) als teus `custom.css` / `custom.js` (tute) o a `css.css` / `js.js` d'nginx (examen). Si canvies el CSS/JS, afegeix `?v=N` a la URL perquè el navegador no usi la caché. El `/helper/` ja exigeix login per si mateix; això només n'amaga l'enllaç.

## Variables del contenidor `dufshelper`

| Variable | Per defecte | |
|---|---|---|
| `DUFS_CONTAINER` | `dufs` | nom del contenidor que es reinicia |
| `HELPER_ADMINS` | `admin` | usuaris administradors, separats per coma |
| `BASE_PATH` | `/helper` | prefix de la URL |
| `RESTART_DELAY` | `5` | segons d'espera abans de reiniciar dufs |

## Seguretat (llegeix-ho)

- **El socket Docker equival a root al servidor.** El helper només l'usa per reiniciar un contenidor, però cal protegir-lo: no el publiquis a cap port; només `expose`. A srv-tute queda darrere de Traefik + CrowdSec.
- Contrasenyes hashejades; límit de 10 intents fallits/5 min per IP; verificació d'`Origin` (CSRF) a tots els POST; mai esborra fitxers (esborrar un usuari només n'elimina les credencials).
- Cada canvi reinicia dufs: les pujades en curs es tallen. Evita fer canvis massius en ple examen.
- Servir-lo sempre per HTTPS (Basic auth).

## Desenvolupament

```sh
cd helper && pip install -r requirements.txt pytest && python -m pytest tests
docker build -t dufshelper helper
```

La imatge es publica a `ghcr.io/joseptomascomellas/dufshelper` amb GitHub Actions a cada push a `main`.
