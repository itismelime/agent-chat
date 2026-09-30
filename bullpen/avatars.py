"""Avatar pictures: yours (for every project) and each agent's (per project).
Only PNG, JPEG and WebP, told apart by their first bytes, never SVG: the page
shows them next to its own scripts."""
import base64
import binascii
import json

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
    _crop_file(f).unlink(missing_ok=True)  # a new picture starts uncropped (centered)
    return versions(store, pid)


def _crop_file(f):
    return f.with_name(f.name + ".crop")


def set_crop(store, pid, name, crop):
    """Which part of the picture shows: x, y, w, h as fractions of its width
    and height (the page makes w and h the same square on the picture)."""
    f = _file(store, pid, name)
    if not f.is_file():
        raise StoreError(404, "no picture for %s" % name)
    try:
        c = {k: float(crop[k]) for k in ("x", "y", "w", "h")}
    except (TypeError, KeyError, ValueError):
        raise StoreError(400, "crop must have x, y, w and h") from None
    if not (0 < c["w"] <= 1 and 0 < c["h"] <= 1 and 0 <= c["x"] <= 1 - c["w"] + 1e-9
            and 0 <= c["y"] <= 1 - c["h"] + 1e-9):
        raise StoreError(400, "the crop must lie inside the picture")
    _crop_file(f).write_text(json.dumps(c))
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
    f = _file(store, pid, name)
    f.unlink(missing_ok=True)
    _crop_file(f).unlink(missing_ok=True)
    return versions(store, pid)


def rename(store, pid, old, new):
    """An agent renamed: its picture follows."""
    f = _file(store, pid, old)
    if f.is_file():
        new_f = _file(store, pid, new)
        f.replace(new_f)
        if _crop_file(f).is_file():
            _crop_file(f).replace(_crop_file(new_f))


def versions(store, pid):
    """{name: {"v": version, "crop": crop or None}} of the pictures the page shows
    in this project (you included)."""
    return {name: v for name in ["user", *(store.agents(pid) if pid else [])] if (v := version(store, pid, name))}


def version(store, pid, name):
    """{"v": version, "crop": crop or None} of one picture, or None."""
    f = _file(store, pid, name)
    if not f.is_file():
        return None
    c = _crop_file(f)
    return {"v": int(f.stat().st_mtime), "crop": json.loads(c.read_text()) if c.is_file() else None}
