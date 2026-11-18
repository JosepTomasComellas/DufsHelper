"""Reinici del contenidor dufs via l'API Docker (socket Unix, només stdlib)."""
import http.client
import re
import socket
import threading

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class _UnixConn(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float = 30):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._path)


def restart(container: str, socket_path: str = "/var/run/docker.sock") -> None:
    """Reinicia *container*. Llança RuntimeError si Docker no retorna 204."""
    if not NAME_RE.match(container):
        raise RuntimeError(f"Nom de contenidor no vàlid: {container!r}")
    conn = _UnixConn(socket_path)
    try:
        conn.request("POST", f"/containers/{container}/restart?t=5")
        resp = conn.getresponse()
        body = resp.read().decode(errors="replace")
    finally:
        conn.close()
    if resp.status != 204:
        raise RuntimeError(f"Docker ha respost {resp.status}: {body[:200]}")


class DebouncedRestarter:
    """Agrupa canvis propers en un sol reinici (evita tallar pujades en curs a cada canvi)."""

    def __init__(self, container: str, delay: float = 5.0, socket_path: str = "/var/run/docker.sock"):
        self.container, self.delay, self.socket_path = container, delay, socket_path
        self.last_error = None
        self._timer = None
        self._lock = threading.Lock()

    def schedule(self) -> None:
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self.delay, self._run)
            self._timer.daemon = True
            self._timer.start()

    def _run(self) -> None:
        try:
            restart(self.container, self.socket_path)
            self.last_error = None
        except Exception as e:  # noqa: BLE001 - es mostra a la pàgina
            self.last_error = str(e)
