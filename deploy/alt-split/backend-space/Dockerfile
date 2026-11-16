# Naadi API — Hugging Face Docker Space (requires PRO/Team plan for Docker hardware)
# requirements.txt = config/requirements.api.txt (copy it into apps/api/)
FROM python:3.13-slim

# HF Spaces run containers as uid 1000
RUN useradd -m -u 1000 user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH PYTHONUNBUFFERED=1
WORKDIR /home/user/app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r requirements.txt

COPY --chown=user . .
USER user
EXPOSE 7860
# SSE needs no proxy buffering; keep one worker per vCPU on cpu-basic (2 vCPU)
CMD ["uvicorn", "naadi_api.main:app", "--host", "0.0.0.0", "--port", "7860", "--workers", "2", "--proxy-headers"]
