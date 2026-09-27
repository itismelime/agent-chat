"""Local models: the GPU, per-model context and thinking, Hugging Face
search, and the import/pull/benchmark jobs behind the Models panel."""
import hashlib
import json
import re
import shutil
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from . import rating
from .ollama import Ollama, OllamaError
from .store import StoreError, data_dir, write_json

CTX_MIN, CTX_MAX = 512, 131072
LEVEL_ARCHS = ("gptoss",)   # models that take a thinking level instead of on/off
LEVELS = ["low", "medium", "high"]
THINK_DEFAULT = {"talk": "off", "agent": "on"}


def gpu():
    """{"name", "total", "used"} in bytes over all NVIDIA GPUs, or None."""
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used",
                            "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    rows = [line.split(",") for line in r.stdout.strip().splitlines() if line.count(",") == 2]
    if r.returncode or not rows:
        return None
    mib = 1024 * 1024
    return {"name": " + ".join(x[0].strip() for x in rows),
            "total": sum(int(x[1]) for x in rows) * mib,
            "used": sum(int(x[2]) for x in rows) * mib}


def recommend_ctx(gpu_total, model_limit=None):
    """local-ai-chat's rule: 32K from 12 GiB, 16K from 8 GiB, else 8K;
    never above the model's own limit."""
    ctx = 32768 if gpu_total >= 12 * rating.GIB else 16384 if gpu_total >= 8 * rating.GIB else 8192
    if model_limit and model_limit > 0:
        ctx = min(ctx, model_limit)
    return max(CTX_MIN, ctx // 64 * 64)


def model_facts(show):
    info = show.get("model_info") or {}
    arch = info.get("general.architecture") or ""
    can = "thinking" in (show.get("capabilities") or [])
    return {"limit": info.get("%s.context_length" % arch), "can_think": can,
            "levels": LEVELS if can and arch.startswith(LEVEL_ARCHS) else None}


class Tuning:
    """Per-model context and thinking settings in <data>/models.json."""

    def __init__(self, root):
        self.path = Path(root) / "models.json"
        self.lock = threading.RLock()

    def all(self):
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def _save(self, model, entry):
        with self.lock:
            data = self.all()
            data[model] = entry
            self.path.parent.mkdir(parents=True, exist_ok=True)
            write_json(self.path, data)

    def get(self, model, ollama, gpu_total):
        """The model's entry, filled in from Ollama the first time."""
        with self.lock:
            entry = self.all().get(model)
            if entry is None:
                facts = model_facts(ollama.show(model))
                entry = {"num_ctx": recommend_ctx(gpu_total, facts["limit"]), "override": None,
                         "think": None, "can_think": facts["can_think"], "levels": facts["levels"]}
                self._save(model, entry)
            return entry

    def set(self, model, ollama, gpu_total, **fields):
        entry = dict(self.get(model, ollama, gpu_total))
        if "num_ctx" in fields:
            v = fields["num_ctx"]
            if v is not None and not (isinstance(v, int) and not isinstance(v, bool)
                                      and CTX_MIN <= v <= CTX_MAX):
                raise StoreError(400, "context must be %d-%d tokens" % (CTX_MIN, CTX_MAX))
            entry["override"] = v
        if "think" in fields:
            v = fields["think"]
            allowed = ["off"] + ((entry["levels"] or ["on"]) if entry["can_think"] else [])
            if v is not None and v not in allowed:
                raise StoreError(400, "thinking for %s can be: %s" % (model, ", ".join(allowed)))
            entry["think"] = v
        self._save(model, entry)
        return entry

    def forget(self, model):
        with self.lock:
            data = self.all()
            if data.pop(model, None) is not None:
                write_json(self.path, data)

    def request(self, model, ollama, gpu_total, use):
        """(num_ctx, think) to send with a request; use is "talk" or "agent"."""
        e = self.get(model, ollama, gpu_total)
        setting = e["think"] or THINK_DEFAULT[use]
        if not e["can_think"] or setting == "off":
            think = False
        elif setting == "on":
            think = "medium" if e["levels"] else True
        else:
            think = setting
        return e["override"] or e["num_ctx"], think


def installed(ollama, tuning, gpu_info):
    """The Installed tab: every model Ollama has, with its settings."""
    total = (gpu_info or {}).get("total", 0)
    loaded = set(ollama.loaded())
    rows = []
    for m in ollama.tags():
        name, size = m["name"], m.get("size", 0)
        try:
            e = tuning.get(name, ollama, total)
        except OllamaError:  # e.g. deleted meanwhile
            e = {"num_ctx": None, "override": None, "think": None, "can_think": False, "levels": None}
        rows.append({"name": name, "size": size, "loaded": name in loaded,
                     "verdict": rating.fit(size, total)[1], "num_ctx": e["num_ctx"],
                     "override": e["override"], "think": e["think"],
                     "can_think": e["can_think"], "levels": e["levels"]})
    return rows
