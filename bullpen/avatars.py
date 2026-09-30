"""Avatar pictures: yours (for every project) and each agent's (per project).
Only PNG, JPEG and WebP, told apart by their first bytes, never SVG: the page
shows them next to its own scripts."""
import base64
import binascii

from .store import StoreError

MAX_BYTES = 1_000_000


def kind(data):
    """The image type of data, or None when it is not a PNG, JPEG or WebP."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _file(store, pid, name):
    d = store.root / "avatars"
    d.mkdir(exist_ok=True)
    return d / ("user" if name == "user" else "%s--%s" % (pid, name))


def save(store, pid, name, data):
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_BYTES:
        raise StoreError(413, "a picture is at most %d KB" % (MAX_BYTES // 1000))
    if not kind(data):
        raise StoreError(415, "a picture must be PNG, JPEG or WebP")
    if name != "user" and name not in store.agents(pid):
        raise StoreError(404, "no agent %s in this project" % name)
    f = _file(store, pid, name)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(f)
    return versions(store, pid)


def save_base64(store, pid, name, text):
    """A picture sent by the page: base64, or a data: URL."""
    if not isinstance(text, str):
        raise StoreError(400, "data must be the picture in base64")
    try:
        data = base64.b64decode(text.split(",", 1)[-1], validate=True)
    except (binascii.Error, ValueError):
        raise StoreError(400, "data must be the picture in base64") from None
    return save(store, pid, name, data)


def load(store, pid, name):
    f = _file(store, pid, name)
    if not f.is_file():
        raise StoreError(404, "no picture for %s" % name)
    data = f.read_bytes()
    return data, kind(data) or "application/octet-stream"


def remove(store, pid, name):
    _file(store, pid, name).unlink(missing_ok=True)
    return versions(store, pid)


def rename(store, pid, old, new):
    """An agent renamed: its picture follows."""
    f = _file(store, pid, old)
    if f.is_file():
        f.replace(_file(store, pid, new))


def versions(store, pid):
    """{name: version} of the pictures the page shows in this project (you included)."""
    out = {}
    for name in ["user", *store.agents(pid)]:
        f = _file(store, pid, name)
        if f.is_file():
            out[name] = int(f.stat().st_mtime)
    return out
