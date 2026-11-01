# Models Directory

Jarvis loads GGUF model files directly from disk without copying or duplicating them.

- Default configured model path: `C:\Users\Gurkirat Singh\models\qwen2.5-7b-instruct.gguf`
- You may also place `.gguf` files in this `models/` directory and update `model_path` in `config/settings.json` (or set `JARVIS_MODEL_PATH`).
- All `.gguf`, `.bin`, and model weight files are excluded from Git via `.gitignore`.
