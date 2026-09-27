"""Model Hub ratings for GGUF chat models, ported from local-ai-chat's
extensions/model-hub/recommendations.mjs (chat models only)."""
import math
import re
import time
from datetime import datetime
from urllib.parse import quote

GIB = 1024 ** 3
MARGIN = 1.2   # a GGUF file's size × MARGIN ≈ the video memory it needs
LIMIT = 0.9    # a preferred file needs at most this share of the GPU's memory
HF = "https://huggingface.co"
SUPPORT_FILE = re.compile(
    r"(?:^|[/_.-])(?:ae|autoencoder|mmproj|imatrix|tokenizer|text[_-]?encoder|clip|vae|lora|"
    r"controlnet|refiner|optimizer|encoder|decoder)(?:[/_.-]|$)", re.I)
UNSUPPORTED = re.compile(r"(?:mtp|\.part|\.split|-\d{5}-of-\d{5}\.gguf$)", re.I)
QUANTS = [(re.compile(p, re.I), q) for p, q in (
    (r"F(?:16|32)", 100), (r"Q8(?:_0)?", 92), (r"Q6_K", 86), (r"Q5_K_M", 82), (r"Q5_K_S", 79),
    (r"Q5(?:_\d)?", 76), (r"Q4_K_M", 73), (r"Q4_K_S", 70), (r"(?:IQ4|Q4)(?:_\w+)?", 68),
    (r"(?:IQ3|Q3)(?:_\w+)?", 57), (r"(?:IQ2|Q2|PQ2|PTQ1)(?:_\w+)?", 46))]
QUANT_TAG = re.compile(r"IQ\d_[A-Z0-9]+(?:_[A-Z0-9]+)*|Q\d_K_[SML]|Q\d_K|Q\d_\d|BF16|F16|F32", re.I)


def file_size(f):
    return int(f.get("size") or (f.get("lfs") or {}).get("size") or 0)


def quant_quality(name):
    return next((q for pattern, q in QUANTS if pattern.search(name)), 60)


def usable(f):
    """A single, plain GGUF chat-model file (the import refuses paths)."""
    name = f.get("rfilename") or ""
    return (name.lower().endswith(".gguf") and "/" not in name
            and not SUPPORT_FILE.search(name) and not UNSUPPORTED.search(name))


def best_file(model, gpu_bytes=0):
    files = [f for f in model.get("siblings") or [] if usable(f)]
    if not files:
        return None
    fitting = [f for f in files if not gpu_bytes or not file_size(f)
               or file_size(f) * MARGIN <= gpu_bytes * LIMIT]
    return sorted(fitting or files, key=lambda f: (-quant_quality(f["rfilename"]), -file_size(f)))[0]


def fit(size, gpu_bytes):
    """(points, verdict) for a file of `size` bytes on a GPU of `gpu_bytes`."""
    if not size or not gpu_bytes:
        return 10, "Unknown fit"
    ratio = size * MARGIN / gpu_bytes
    for limit, points, verdict in ((0.55, 45, "Plenty of room"), (0.75, 40, "Comfortable"),
                                   (0.9, 33, "Good fit"), (1.0, 20, "Tight fit")):
        if ratio <= limit + 1e-9:
            return points, verdict
    return 0, "Won't fit GPU memory"


def community(model):
    downloads = math.log10(max(1, model.get("downloads") or 0)) / 6 * 11
    likes = math.log10(max(1, model.get("likes") or 0)) / 4.5 * 5
    trending = math.log10(max(1, model.get("trendingScore") or 0)) / 4 * 4
    return min(20, max(0, downloads + likes + trending))


def freshness(last_modified, now=None):
    try:
        ts = datetime.fromisoformat(last_modified.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return 2
    days = max(0, ((now or time.time()) - ts) / 86400)
    for limit, points in ((30, 10), (180, 8), (365, 6), (730, 3)):
        if days <= limit:
            return points
    return 1


def suggest_name(repo_id, filename):
    base = re.sub(r"[-_.]?gguf$", "", repo_id.split("/")[-1].lower())
    base = re.sub(r"[^a-z0-9._-]+", "-", base).strip("-.") or "model"
    tag = QUANT_TAG.search(filename)
    return base + (":" + tag.group(0).lower() if tag else "")


def rate(model, gpu_bytes, now=None):
    f = best_file(model, gpu_bytes)
    size = file_size(f) if f else 0
    fit_points, verdict = fit(size, gpu_bytes)
    score = fit_points + (25 if f else 0) + community(model) + freshness(model.get("lastModified"), now)
    score = int(min(100, max(0, score)) + 0.5)
    label = "Excellent" if score >= 85 else "Good" if score >= 70 else "Fair" if score >= 50 else "Poor"
    repo = model.get("id") or ""
    return {"id": repo, "score": score, "label": label, "size": size, "verdict": verdict,
            "downloads": model.get("downloads") or 0, "likes": model.get("likes") or 0,
            "file": f["rfilename"] if f else None,
            "name": suggest_name(repo, f["rfilename"]) if f else None,
            "url": "%s/%s/resolve/main/%s" % (HF, repo, quote(f["rfilename"])) if f else None}
