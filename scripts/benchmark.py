"""Benchmark local inference configurations, each in an isolated subprocess.

    py -3.12 scripts/benchmark.py                 # CPU vs partial GPU vs full GPU (offload_kqv=False)
    py -3.12 scripts/benchmark.py --include-unsafe  # also try offload_kqv=True (expected to crash on sm_120)

Results: data/benchmark_results.json and a markdown table on stdout.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PROMPT = "Write a short paragraph explaining how a refrigerator keeps food cold."
GEN_TOKENS = 128

CONFIGS = {
    "cpu":           dict(n_gpu_layers=0,  offload_kqv=False, flash_attn=False),
    "gpu_half":      dict(n_gpu_layers=14, offload_kqv=False, flash_attn=False),
    "gpu_full":      dict(n_gpu_layers=-1, offload_kqv=False, flash_attn=False),
}
UNSAFE = {
    "gpu_full_kqv":    dict(n_gpu_layers=-1, offload_kqv=True, flash_attn=False),
    "gpu_full_kqv_fa": dict(n_gpu_layers=-1, offload_kqv=True, flash_attn=True),
}


def vram_used_mb():
    from backend.gpu import detect_gpu
    g = detect_gpu()
    return g.used_mb if g.available else None


def run_one(name: str, cfg: dict) -> dict:
    """Executed inside the child process."""
    import psutil
    from backend.config import load_settings
    from backend.engine import llama_loader
    from backend import offline

    offline.enable()  # benchmark also proves no network access is needed
    s = load_settings(**{k: v for k, v in cfg.items()}, n_ctx=2048)
    before = vram_used_mb()
    t0 = time.perf_counter()
    llm = llama_loader(s, cfg["n_gpu_layers"])
    load_s = time.perf_counter() - t0
    after_load = vram_used_mb()

    msgs = [{"role": "user", "content": PROMPT}]
    llm.create_chat_completion(messages=msgs, max_tokens=8, temperature=0)  # warm-up
    t0 = time.perf_counter()
    first = None
    n = 0
    for chunk in llm.create_chat_completion(messages=msgs, max_tokens=GEN_TOKENS, temperature=0,
                                            stream=True):
        if chunk["choices"][0].get("delta", {}).get("content"):
            n += 1
            first = first or time.perf_counter()
    end = time.perf_counter()
    peak = vram_used_mb()
    return {
        "config": name, **cfg, "ok": True,
        "load_seconds": round(load_s, 2),
        "vram_before_mb": before, "vram_after_load_mb": after_load, "vram_after_generate_mb": peak,
        "vram_model_mb": (peak - before) if before is not None and peak is not None else None,
        "rss_mb": round(psutil.Process().memory_info().rss / 2**20),
        "tokens": n,
        "time_to_first_token_s": round(first - t0, 3) if first else None,
        "decode_tokens_per_second": round((n - 1) / (end - first), 2) if first and n > 1 else None,
        "end_to_end_tokens_per_second": round(n / (end - t0), 2),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--child")
    ap.add_argument("--include-unsafe", action="store_true")
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()

    allc = {**CONFIGS, **UNSAFE}
    if args.child:
        print("RESULT " + json.dumps(run_one(args.child, allc[args.child])), flush=True)
        return

    names = args.only or list(CONFIGS) + (list(UNSAFE) if args.include_unsafe else [])
    results = []
    for name in names:
        print(f"running {name} ...", file=sys.stderr, flush=True)
        p = subprocess.run([sys.executable, __file__, "--child", name], capture_output=True,
                           text=True, cwd=ROOT, timeout=900)
        line = next((l for l in p.stdout.splitlines() if l.startswith("RESULT ")), None)
        if line:
            results.append(json.loads(line[7:]))
        else:
            err = [l for l in (p.stderr or "").splitlines() if "error" in l.lower()][-3:]
            results.append({"config": name, **allc[name], "ok": False, "exit_code": p.returncode,
                            "error": " | ".join(err) or "no output"})
        time.sleep(2)  # let the driver release VRAM

    out = ROOT / "data" / "benchmark_results.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                               "prompt": PROMPT, "max_tokens": GEN_TOKENS, "results": results}, indent=2))

    print("| config | n_gpu_layers | offload_kqv | flash_attn | load s | VRAM used by model MiB | RSS MiB | TTFT s | decode tok/s |")
    print("|---|---|---|---|---|---|---|---|---|")
    for r in results:
        if r["ok"]:
            print(f"| {r['config']} | {r['n_gpu_layers']} | {r['offload_kqv']} | {r['flash_attn']} | {r['load_seconds']} | "
                  f"{r['vram_model_mb']} | {r['rss_mb']} | {r['time_to_first_token_s']} | {r['decode_tokens_per_second']} |")
        else:
            print(f"| {r['config']} | {r['n_gpu_layers']} | {r['offload_kqv']} | {r['flash_attn']} | FAILED (exit {r['exit_code']}): {r['error']} |||||")
    print(f"\nSaved {out}")


if __name__ == "__main__":
    main()
