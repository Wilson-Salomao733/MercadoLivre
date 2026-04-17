FROM python:3.11-slim

WORKDIR /app

# ffmpeg obrigatório para moviepy montar vídeos
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY afiliado_bot.py .
COPY video_afiliado.py .

CMD ["python", "-u", "afiliado_bot.py"]
