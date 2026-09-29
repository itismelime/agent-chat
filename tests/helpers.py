import tempfile
import threading
from pathlib import Path

from bullpen.server import serve
from bullpen.store import Store


def start(wait_seconds=1, models=None):
    """A service on a free port with a fresh data folder. Returns
    (store, server, port, tmp); call stop(server) when done."""
    tmp = Path(tempfile.mkdtemp())
    store = Store(tmp / "data")
    server = serve(port=0, store=store, wait_seconds=wait_seconds, deliver=False, models=models)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return store, server, server.server_address[1], tmp


def stop(server):
    server.shutdown()
    server.server_close()
