# Backend (FastAPI)

This folder contains the **MEME MIND AI** API and static mounts for the parent frontend.

**Full project documentation** (architecture, API reference, troubleshooting): see the repository README at [`../../README.md`](../../README.md) (workspace root).

## Quick start

1. Copy `.env.example` to `.env` and set `GEMINI_API_KEY`.
2. Create a virtual environment named `venv` in this directory (required for `run.bat` on Windows).
3. `pip install -r requirements.txt`
4. `python -m uvicorn main:app --host 127.0.0.1 --port 8000`

Do **not** put your API key in `main.py`; configuration is loaded from `.env` via `python-dotenv`.
