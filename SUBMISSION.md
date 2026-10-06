# Submission

## Repo
Private GitHub repo — `pratham-saraf` added as collaborator.

## How to run
```bash
cp .env.example .env   # add your OPENAI_API_KEY
docker compose up      # starts API on :8000 + PaySwift sandbox on :8080
python eval.py         # run evals (separate terminal)
```

## Stack
- **Python 3.11** + **FastAPI** + **aiosqlite** (SQLite)
- **OpenAI SDK** (works with Groq free tier — set `OPENAI_BASE_URL` in `.env`)
- **httpx** for PaySwift calls
- **Docker Compose** for one-command startup
- **GitHub Actions** for CI on every push

## AI logs
Raw Kiro AI session exports are in `ai-logs/`.
