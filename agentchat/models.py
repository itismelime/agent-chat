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


HF = rating.HF
SEARCH_TTL = 600
DISK_FACTOR = 3.1   # Ollama copies an imported file into its own layers
MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*(?::[A-Za-z0-9._-]+)?$")
GGUF_NAME = re.compile(r"^[^/\\\s][^/\\]*\.gguf$", re.I)
BENCH_PROMPT = "Reply with exactly: benchmark ok"
THINK_PROMPT = "What is 17 × 23? Answer with the number only."
LOAD_HINT = (" (if the model did not load: free video memory, another program may have a"
             " model loaded, or pick a smaller file)")


def fetch_json(url):
    with urlopen(Request(url, headers={"User-Agent": "agent-chat"}), timeout=20) as r:
        return json.loads(r.read())


class Hub:
    """Hugging Face GGUF search, rated for this GPU; cached for SEARCH_TTL."""

    def __init__(self, fetch=None):
        self.fetch, self.cache = fetch or fetch_json, {}

    def search(self, q, gpu_total):
        key = (q or "").strip().lower()
        hit = self.cache.get(key)
        if hit and time.time() - hit[0] < SEARCH_TTL:
            found = hit[1]
        else:
            params = [("filter", "gguf"), ("sort", "trendingScore"), ("direction", "-1"),
                      ("limit", "30")] + ([("search", q.strip())] if key else [])
            params += [("expand[]", f) for f in ("downloads", "likes", "tags", "trendingScore",
                                                  "lastModified", "siblings")]
            try:
                listing = self.fetch(HF + "/api/models?" + urlencode(params))
                with ThreadPoolExecutor(8) as pool:
                    found = list(pool.map(self._with_sizes, listing))
            except (OSError, ValueError) as e:
                if not hit:
                    raise StoreError(502, "Hugging Face search failed: %s" % e) from None
                found = hit[1]  # keep the last results
            else:
                if not any(m.get("_no_sizes") for m in found):  # a failed lookup: ask again next time
                    self.cache[key] = (time.time(), found)
        return sorted((rating.rate(m, gpu_total) for m in found), key=lambda r: -r["score"])

    def _with_sizes(self, m):
        try:
            detail = self.fetch("%s/api/models/%s?blobs=true" % (HF, m["id"]))
        except (OSError, ValueError):
            return dict(m, _no_sizes=True)
        return dict(m, siblings=detail.get("siblings") or m.get("siblings") or [])


class Cancelled(Exception):
    pass


class Job:
    def __init__(self, kind, model):
        self.id, self.kind, self.model = uuid.uuid4().hex[:12], kind, model
        self.state, self.completed, self.total, self.message, self.result = "running", 0, 0, "", None
        self.cancel = threading.Event()

    def progress(self, completed=None, total=None, message=None):
        """Report progress; raises Cancelled once the job was cancelled."""
        if self.cancel.is_set():
            raise Cancelled()
        if completed is not None:
            self.completed = completed
        if total is not None:
            self.total = total
        if message is not None:
            self.message = message

    def view(self):
        return {k: getattr(self, k) for k in
                ("id", "kind", "model", "state", "completed", "total", "message", "result")}


class Jobs:
    KEEP = 50

    def __init__(self):
        self.jobs, self.lock = [], threading.Lock()

    def start(self, kind, model, work):
        with self.lock:
            if any(j.kind == kind and j.model == model and j.state == "running" for j in self.jobs):
                raise StoreError(409, "a %s of %s is already running" % (kind, model))
            job = Job(kind, model)
            self.jobs.insert(0, job)
            del self.jobs[self.KEEP:]

        def run():
            try:
                job.result = work(job)
                job.state = "done"
            except Cancelled:
                job.state, job.message = "cancelled", "cancelled"
            except (OllamaError, StoreError, OSError, ValueError) as e:
                job.state, job.message = "failed", str(e)
            except Exception as e:  # e.g. http.client.IncompleteRead: a job must always end
                job.state, job.message = "failed", "%s: %s" % (type(e).__name__, e)
        threading.Thread(target=run, daemon=True).start()
        return job.view()

    def list(self):
        return [j.view() for j in self.jobs]

    def cancel(self, job_id):
        for j in self.jobs:
            if j.id == job_id:
                if j.kind not in ("import", "pull"):
                    raise StoreError(400, "only imports and pulls can be cancelled")
                j.cancel.set()
                return j.view()
        raise StoreError(404, "no job %s" % job_id)


def check_model_name(model):
    if not isinstance(model, str) or not MODEL_NAME.match(model):
        raise StoreError(400, "model must be an Ollama model name without spaces, "
                              "not starting with '.'")


def check_import(url, filename, model):
    u = urlparse(url or "")
    if u.scheme != "https" or u.hostname != "huggingface.co" or "/resolve/" not in u.path:
        raise StoreError(400, "url must be an https://huggingface.co/<repo>/resolve/<rev>/<file> link")
    if not isinstance(filename, str) or not GGUF_NAME.match(filename) or filename.startswith("."):
        raise StoreError(400, "unsafe GGUF file name: %r" % (filename,))
    if u.path.rsplit("/", 1)[-1] not in (filename, filename.replace(" ", "%20")):
        raise StoreError(400, "the url must point at %s" % filename)
    check_model_name(model)


def import_gguf(job, ollama, url, filename, model, work_dir,
                free=lambda p: shutil.disk_usage(p).free):
    """Download a GGUF file (hashing it), hand it to Ollama, create the model."""
    ollama.version()  # fail in a second, not after a 20 GB download, if Ollama is down
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    tmp = work_dir / (uuid.uuid4().hex + ".gguf.part")
    try:
        with urlopen(Request(url, headers={"User-Agent": "agent-chat"}), timeout=60) as r:
            total = int(r.headers.get("Content-Length") or 0)
            if total and free(work_dir) < total * DISK_FACTOR:
                raise StoreError(507, "not enough disk space: importing %s needs %.1f GB free"
                                 % (filename, total * DISK_FACTOR / rating.GIB))
            job.progress(0, total, "downloading")
            h, done = hashlib.sha256(), 0
            with open(tmp, "wb") as out:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    job.progress(done)
        digest = "sha256:" + h.hexdigest()
        job.progress(message="adding to Ollama")
        if not ollama.has_blob(digest):
            ollama.push_blob(digest, tmp, done)
        for event in ollama.stream("/api/create", {"model": model, "files": {filename: digest}}):
            job.progress(message=event.get("status", ""))
        return {"model": model}
    finally:
        tmp.unlink(missing_ok=True)


def pull(job, ollama, model):
    for event in ollama.stream("/api/pull", {"model": model}):
        job.progress(event.get("completed"), event.get("total"), event.get("status"))
    return {"model": model}


def _tokens_per_second(r):
    d = r.get("eval_duration") or 0
    return round(r.get("eval_count", 0) / (d / 1e9), 1) if d else None


def _thinking_tokens(r):
    """An estimate: Ollama counts thinking and answer tokens together."""
    msg = r.get("message") or {}
    thought, answer = len(msg.get("thinking") or ""), len(msg.get("content") or "")
    return round(r.get("eval_count", 0) * thought / (thought + answer)) if thought else 0


def benchmark(job, ollama, model, tuning, gpu_fn=gpu):
    info = gpu_fn() or {}
    entry = tuning.get(model, ollama, info.get("total", 0))
    try:
        job.progress(message="loading and generating")
        start = time.monotonic()
        r = ollama.chat(model, [{"role": "user", "content": BENCH_PROMPT}], think=False)
        seconds = time.monotonic() - start
        after = (gpu_fn() or {}).get("used")
        result = {"seconds": round(seconds, 2), "tokens_per_second": _tokens_per_second(r),
                  "vram_used": after - info["used"] if after is not None and "used" in info else None,
                  "reply": (r.get("message") or {}).get("content", "")}
        if entry["can_think"]:
            result["thinking"] = {}
            for label, think in (("off", False), ("on", "low" if entry["levels"] else True)):
                job.progress(message="thinking " + label)
                start = time.monotonic()
                r = ollama.chat(model, [{"role": "user", "content": THINK_PROMPT}], think=think)
                result["thinking"][label] = {
                    "seconds": round(time.monotonic() - start, 2), "tokens": r.get("eval_count", 0),
                    "thinking_tokens": _thinking_tokens(r),
                    "reply": (r.get("message") or {}).get("content", "")}
        return result
    except OllamaError as e:
        raise OllamaError(str(e) + LOAD_HINT) from None


class Models:
    """Everything the server's model routes need."""

    def __init__(self, root=None, ollama_url=None, hub=None):
        from . import config
        self.own = config.OWN_OLLAMA
        self.ollama = Ollama(ollama_url or config.ollama_url())
        self.root = Path(root) if root else data_dir()
        self.tuning, self.hub, self.jobs = Tuning(self.root), hub or Hub(), Jobs()

    def gpu_total(self):
        return (gpu() or {}).get("total", 0)

    def status(self):
        try:
            version, reachable, error = self.ollama.version(), True, None
        except OllamaError as e:
            version, reachable, error = None, False, str(e)
        return {"url": self.ollama.url, "own": self.ollama.url == self.own,
                "reachable": reachable, "version": version, "error": error, "gpu": gpu()}
