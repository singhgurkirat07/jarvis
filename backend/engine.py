"""Model lifecycle and inference: load a local GGUF file with llama.cpp, generate, unload."""
from __future__ import annotations

import gc
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterator

from .config import Settings
from .context import PromptTooLong, default_token_counter
from .gpu import GpuInfo, detect_gpu, plan_gpu_layers, register_cuda_dlls
from .model_file import ModelInfo, validate_model

log = logging.getLogger("jarvis.engine")


class EngineNotReady(RuntimeError):
    pass


class EngineBusy(RuntimeError):
    pass


def llama_loader(settings: Settings, n_gpu_layers: int):
    """Default loader: llama-cpp-python, reading the GGUF file straight from disk."""
    register_cuda_dlls()
    from llama_cpp import Llama  # imported lazily so tests can run without it

    return Llama(
        model_path=settings.model_path,
        n_gpu_layers=n_gpu_layers,
        offload_kqv=settings.offload_kqv,
        flash_attn=settings.flash_attn,
        n_ctx=settings.n_ctx,
        n_batch=settings.n_batch,
        n_threads=settings.n_threads,
        use_mmap=settings.use_mmap,
        verbose=False,
    )


@dataclass
class EngineStatus:
    state: str = "unloaded"          # unloaded | loading | ready | error | closed
    error: str | None = None
    model: dict | None = None
    gpu: dict | None = None
    n_gpu_layers: int | None = None
    gpu_plan: str | None = None
    load_seconds: float | None = None
    offload_kqv: bool | None = None
    n_ctx: int | None = None
    last_generation: dict | None = None
    load_attempts: list = field(default_factory=list)


class Engine:
    def __init__(self, settings: Settings, loader: Callable = llama_loader):
        self.settings = settings
        self._loader = loader
        self._llm = None
        self._gen_lock = threading.Lock()     # llama.cpp contexts are not thread-safe
        self._state_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.status = EngineStatus(offload_kqv=settings.offload_kqv, n_ctx=settings.n_ctx)

    # ---- lifecycle -------------------------------------------------------
    @property
    def ready(self) -> bool:
        return self.status.state == "ready"

    def count_tokens(self, text: str) -> int:
        llm = self._llm
        if llm is not None and hasattr(llm, "tokenize"):
            return len(llm.tokenize(text.encode("utf-8"), add_bos=False, special=False))
        return default_token_counter(text)

    def load(self) -> None:
        """Validate the model file, plan GPU offload and load. Blocking."""
        with self._state_lock:
            if self.status.state in ("loading", "ready"):
                return
            self.status.state, self.status.error = "loading", None
            self.status.offload_kqv = self.settings.offload_kqv
            self.status.n_ctx = self.settings.n_ctx
        try:
            info: ModelInfo = validate_model(self.settings.model_path)
            self.status.model = {
                "path": info.path, "name": info.name, "architecture": info.architecture,
                "size_mb": round(info.size_bytes / 2**20), "layers": info.block_count,
                "gguf_version": info.gguf_version,
            }
            gpu: GpuInfo = detect_gpu()
            self.status.gpu = gpu.as_dict()
            layers, reason = plan_gpu_layers(self.settings.n_gpu_layers, info.size_bytes,
                                             info.block_count, gpu, self.settings.vram_reserve_mb)
            log.info("gpu plan", extra={"n_gpu_layers": layers, "reason": reason})

            # Fallback chain protects against GPU out-of-memory at load time.
            candidates = [layers]
            if layers != 0:
                half = (info.block_count or 32) // 2 if layers < 0 else layers // 2
                candidates += [half, 0] if half > 0 else [0]
            last_err = None
            for n in candidates:
                t0 = time.perf_counter()
                try:
                    llm = self._loader(self.settings, n)
                except Exception as e:  # llama-cpp raises ValueError on load failure
                    last_err = e
                    self.status.load_attempts.append({"n_gpu_layers": n, "error": str(e)})
                    log.warning("model load failed", extra={"n_gpu_layers": n, "error": str(e)})
                    gc.collect()
                    continue
                with self._state_lock:
                    if self.status.state != "loading":   # closed while we were loading
                        del llm
                        gc.collect()
                        return
                    self._llm = llm
                    self.status.load_seconds = round(time.perf_counter() - t0, 2)
                    self.status.load_attempts.append({"n_gpu_layers": n, "ok": True})
                    self.status.n_gpu_layers = n
                    self.status.gpu_plan = reason if n == layers else f"fallback after load failure ({reason})"
                    self.status.state = "ready"
                after = detect_gpu()
                if after.available and self.status.gpu:
                    self.status.gpu["used_mb_after_load"] = after.used_mb
                log.info("model ready", extra={"load_seconds": self.status.load_seconds, "n_gpu_layers": n})
                return
            raise RuntimeError(f"Model failed to load with every GPU layer setting tried: {last_err}")
        except Exception as e:  # validation, planning or final load failure
            self.status.state, self.status.error = "error", str(e)
            log.error("model load error", extra={"error": str(e)})

    def reload(self, new_settings: Settings) -> None:
        """Close the current model and load with new_settings (only on explicit user confirmation)."""
        self.close()
        with self._state_lock:
            self.settings = new_settings
            self.status = EngineStatus(offload_kqv=new_settings.offload_kqv, n_ctx=new_settings.n_ctx)
        self.load()

    def load_in_background(self) -> threading.Thread:
        self._thread = threading.Thread(target=self.load, name="jarvis-model-loader", daemon=True)
        self._thread.start()
        return self._thread

    def close(self, timeout: float = 30) -> None:
        """Release the model (and its VRAM). Waits for any in-flight generation to stop."""
        with self._state_lock:
            prev = self.status.state
            self.status.state = "closed"
        acquired = self._gen_lock.acquire(timeout=timeout)
        if not acquired:
            # Freeing the model under a running generation could crash native code;
            # leave it to process exit instead.
            log.warning("engine close timed out waiting for generation", extra={"previous_state": prev})
            return
        try:
            llm, self._llm = self._llm, None
            if llm is not None:
                if hasattr(llm, "close"):
                    llm.close()
                del llm
                gc.collect()
            log.info("engine closed", extra={"previous_state": prev})
        finally:
            self._gen_lock.release()

    # ---- inference -------------------------------------------------------
    def _params(self, max_tokens, temperature, top_p, repeat_penalty=None):
        s = self.settings
        return dict(
            max_tokens=max_tokens or s.max_tokens,
            temperature=s.temperature if temperature is None else temperature,
            top_p=s.top_p if top_p is None else top_p,
            repeat_penalty=s.repeat_penalty if repeat_penalty is None else repeat_penalty,
        )

    def _prepare(self, messages: list[dict], max_tokens: int) -> list[dict]:
        """Add the system prompt and drop the oldest turns until the prompt fits n_ctx."""
        if not messages or messages[0]["role"] != "system":
            messages = [{"role": "system", "content": self.settings.system_prompt}] + messages
        budget = self.settings.n_ctx - max_tokens

        def cost(m):  # content tokens + chat-template overhead per message
            return len(self._llm.tokenize(m["content"].encode(), add_bos=False, special=False)) + 8

        costs = [cost(m) for m in messages]
        while sum(costs) > budget and len(messages) > 2:
            del messages[1], costs[1]           # oldest non-system turn
        if sum(costs) > budget:
            raise PromptTooLong(
                f"Message is too long: about {sum(costs)} tokens, limit is {budget} "
                f"(context {self.settings.n_ctx} minus {max_tokens} reply tokens)"
            )
        return messages

    def _acquire(self):
        if not self.ready:
            raise EngineNotReady(self.status.error or f"Model is {self.status.state}")
        if not self._gen_lock.acquire(timeout=self.settings.queue_timeout_s):
            raise EngineBusy("Model is busy with another request")
        if not self.ready or self._llm is None:
            self._gen_lock.release()
            raise EngineNotReady(f"Model is {self.status.state}")

    def generate(self, messages, max_tokens=None, temperature=None, top_p=None, repeat_penalty=None) -> dict:
        params = self._params(max_tokens, temperature, top_p, repeat_penalty)
        self._acquire()
        try:
            msgs = self._prepare(list(messages), params["max_tokens"])
            t0 = time.perf_counter()
            out = self._llm.create_chat_completion(messages=msgs, **params)
            stats = self._record(out["usage"]["completion_tokens"], out["usage"]["prompt_tokens"],
                                 time.perf_counter() - t0, out["choices"][0].get("finish_reason"))
            return {"content": out["choices"][0]["message"]["content"], **stats}
        finally:
            self._gen_lock.release()

    def stream(self, messages, max_tokens=None, temperature=None, top_p=None, repeat_penalty=None) -> Iterator[dict]:
        """Generator: yields {"delta": str} events, then one {"done": True, ...stats} event.

        Errors (not ready / busy / too long) are raised on the first next() call, so callers
        should pull the first event before committing to a streaming HTTP response. Closing
        the generator early (client disconnect) stops generation and releases the model.
        """
        params = self._params(max_tokens, temperature, top_p, repeat_penalty)
        self._acquire()
        try:
            msgs = self._prepare(list(messages), params["max_tokens"])
            t0 = time.perf_counter()
            first = None
            n = 0
            finish = None
            for chunk in self._llm.create_chat_completion(messages=msgs, stream=True, **params):
                if self.status.state == "closed":
                    finish = "shutdown"
                    break
                choice = chunk["choices"][0]
                text = choice.get("delta", {}).get("content")
                finish = choice.get("finish_reason") or finish
                if text:
                    n += 1  # llama-cpp-python streams one chunk per generated token
                    first = first or time.perf_counter()
                    yield {"delta": text}
            end = time.perf_counter()
            stats = self._record(n, None, end - t0, finish, ttft=(first - t0) if first else None)
            if first and n > 1 and end > first:
                # generation speed excluding prompt processing (time to first token)
                stats["decode_tokens_per_second"] = round((n - 1) / (end - first), 2)
            yield {"done": True, **stats}
        finally:
            self._gen_lock.release()

    def _record(self, completion_tokens, prompt_tokens, seconds, finish, ttft=None) -> dict:
        stats = {
            "completion_tokens": completion_tokens,
            "prompt_tokens": prompt_tokens,
            "seconds": round(seconds, 3),
            "tokens_per_second": round(completion_tokens / seconds, 2) if seconds > 0 else None,
            "time_to_first_token": round(ttft, 3) if ttft else None,
            "finish_reason": finish,
        }
        self.status.last_generation = stats
        log.info("generation", extra=stats)
        return stats
