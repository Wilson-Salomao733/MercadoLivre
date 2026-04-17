#!/usr/bin/env python3
"""
video_afiliado.py — Gerador de vídeos promocionais de produtos tech

Pipeline:
  1. Recebe produto (ou busca TOP Shopee com imageUrl)
  2. Groq gera roteiro de vendas em PT-BR
  3. gTTS converte roteiro em áudio
  4. PIL monta frame: imagem do produto + overlay (título, preço, desconto, CTA)
  5. MoviePy compõe: frame animado + narração + música de fundo aleatória do v2/audios
  6. Telegram envia vídeo ao canal com caption + link de afiliado
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import random
import tempfile
import time
import traceback

import numpy as np
import PIL.Image
import PIL.ImageDraw
import PIL.ImageFont

if not hasattr(PIL.Image, "ANTIALIAS"):
    PIL.Image.ANTIALIAS = getattr(PIL.Image, "LANCZOS", 1)

import requests
from dotenv import load_dotenv
import asyncio
from moviepy.editor import (
    AudioFileClip,
    CompositeAudioClip,
    CompositeVideoClip,
    ImageClip,
)
from moviepy.audio.AudioClip import concatenate_audioclips

# ── Caminhos base ──────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

# ── Credenciais ────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
SHOPEE_APP_ID    = os.getenv("SHOPEE_APP_ID", "")
SHOPEE_SECRET    = os.getenv("SHOPEE_SECRET", "")
GROQ_API_KEY     = os.getenv("GROQ_API_KEY", "")

# ── Constantes ─────────────────────────────────────────────────────────────────
SHOPEE_GQL_URL = "https://open-api.affiliate.shopee.com.br/graphql"
GROQ_URL       = "https://api.groq.com/openai/v1/chat/completions"
AUDIOS_DIR     = os.path.join(BASE_DIR, "v2", "CONTENT_STUDIO", "audios")
OUTPUT_DIR     = os.path.join(BASE_DIR, "output_videos")
os.makedirs(OUTPUT_DIR, exist_ok=True)

VIDEO_W   = 1080
VIDEO_H   = 1920
VIDEO_FPS = 30

# Voz Edge TTS — configurável via .env
# Vozes PT-BR disponíveis:
#   pt-BR-AntonioNeural   (masculina, jovem — energético para tech/vendas)
#   pt-BR-FranciscaNeural (feminina, jovem)
#   pt-BR-ThalitaNeural   (feminina, adulta)
#   pt-BR-FabioNeural     (masculina, adulta)
EDGE_TTS_VOICE = os.getenv("EDGE_TTS_VOICE", "pt-BR-AntonioNeural")
EDGE_TTS_RATE  = os.getenv("EDGE_TTS_RATE",  "+15%")

# Keywords para buscar produto top na Shopee quando chamado standalone
TECH_KEYWORDS_VIDEO = [
    "teclado mecanico", "mouse gamer", "headset gamer",
    "placa de video", "ssd nvme", "cadeira gamer",
    "monitor 27", "webcam full hd", "notebook gamer",
    "hub usb-c", "mousepad gamer",
]

# ── Shopee GQL com imageUrl ────────────────────────────────────────────────────
SHOPEE_QUERY_VIDEO = """
query($keyword: String, $limit: Int, $page: Int) {
  productOfferV2(keyword: $keyword, limit: $limit, page: $page, sortType: 2) {
    nodes {
      itemId
      shopId
      productName
      price
      priceMin
      priceDiscountRate
      imageUrl
      offerLink
    }
  }
}
"""


def _shopee_gql(query: str, variables: dict | None = None) -> dict:
    ts      = int(time.time())
    payload = json.dumps({"query": query, "variables": variables or {}}, separators=(",", ":"))
    sig     = hashlib.sha256(f"{SHOPEE_APP_ID}{ts}{payload}{SHOPEE_SECRET}".encode()).hexdigest()
    headers = {
        "Content-Type":  "application/json",
        "Authorization": f"SHA256 Credential={SHOPEE_APP_ID}, Timestamp={ts}, Signature={sig}",
    }
    try:
        r = requests.post(SHOPEE_GQL_URL, headers=headers, data=payload, timeout=15)
        return r.json()
    except Exception as e:
        print(f"  ⚠️  Shopee GQL: {e}")
        return {}


def buscar_produto_top_video() -> dict | None:
    """Busca produto Shopee com maior desconto que tenha imageUrl."""
    candidatos: list[dict] = []
    for kw in TECH_KEYWORDS_VIDEO:
        data  = _shopee_gql(SHOPEE_QUERY_VIDEO, {"keyword": kw, "limit": 5, "page": 1})
        nodes = data.get("data", {}).get("productOfferV2", {}).get("nodes") or []
        for p in nodes:
            try:
                preco    = float(p.get("priceMin") or p.get("price") or 0)
                desconto = int(p.get("priceDiscountRate") or 0)
                img_url  = p.get("imageUrl", "")
                link     = p.get("offerLink", "")
                titulo   = p.get("productName", "")
                if not titulo or not link or preco == 0 or not img_url or desconto < 10:
                    continue
                candidatos.append({
                    "titulo":   titulo,
                    "preco":    preco,
                    "desconto": desconto,
                    "img_url":  img_url,
                    "link":     link,
                })
            except Exception:
                continue
        time.sleep(0.4)

    if not candidatos:
        return None
    candidatos.sort(key=lambda x: x["desconto"], reverse=True)
    return candidatos[0]


# ── Groq — Roteiro de vendas ───────────────────────────────────────────────────
def gerar_roteiro_groq(produto: dict) -> str:
    """Pede para o Groq criar um roteiro de venda curto e empolgante."""
    prompt = (
        f"Você é um vendedor expert em tecnologia e gadgets para jovens brasileiros. "
        f"Crie um roteiro CURTO (máximo 30 segundos de fala, ~70 palavras) e MUITO CONVINCENTE "
        f"para vender via vídeo de redes sociais.\n\n"
        f"Produto: {produto['titulo']}\n"
        f"Preço: R$ {produto['preco']:.2f} com {produto['desconto']}% de desconto\n\n"
        f"Regras:\n"
        f"- Português brasileiro informal e super energético\n"
        f"- Destaque muito o desconto e o valor do produto\n"
        f"- Fale para quem curte tech, setup gamer e programação\n"
        f"- Termine com chamada para entrar no grupo do Telegram para mais ofertas exclusivas\n"
        f"- NAO mencione o nome da loja nem do site\n"
        f"- Responda APENAS com o roteiro, sem titulo nem comentario extra\n"
    )
    try:
        r = requests.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 220,
                "temperature": 0.85,
            },
            timeout=25,
        )
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"  ⚠️  Groq: {e}. Usando roteiro fallback.")
        return (
            f"Olha essa oferta incrível! {produto['titulo']} com {produto['desconto']} porcento "
            f"de desconto, por apenas R$ {produto['preco']:.2f}! Muito pelo dinheiro. "
            f"Se você curte tech e setup não pode perder. "
            f"Entra no nosso grupo do Telegram, link tá na bio, mais ofertas toda hora!"
        )


# ── TTS — Edge TTS (Microsoft, natural) ──────────────────────────────────────
def narrar_edge(texto: str, output_path: str) -> str:
    """
    Gera narração com Microsoft Edge TTS.
    Muito mais natural que gTTS — sem sotaque robótico.
    Voz e velocidade configuráveis via EDGE_TTS_VOICE / EDGE_TTS_RATE no .env.
    """
    try:
        import edge_tts
    except ImportError:
        raise ImportError("edge-tts nao instalado. Execute: pip install edge-tts")

    async def _gen():
        communicate = edge_tts.Communicate(
            text=texto,
            voice=EDGE_TTS_VOICE,
            rate=EDGE_TTS_RATE,
        )
        await communicate.save(output_path)

    # asyncio.run() cria loop próprio — funciona em qualquer thread (main ou secundária)
    asyncio.run(_gen())
    return output_path


# ── Download de imagem ────────────────────────────────────────────────────────
def baixar_imagem(url: str, output_path: str) -> str | None:
    try:
        r = requests.get(url, timeout=20)
        if r.status_code == 200:
            with open(output_path, "wb") as f:
                f.write(r.content)
            return output_path
    except Exception as e:
        print(f"  ⚠️  Download imagem: {e}")
    return None


# ── PIL — Frame com overlay de produto ───────────────────────────────────────
def _get_font(size: int):
    """Tenta carregar uma fonte bold do sistema; fallback para fonte default."""
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return PIL.ImageFont.truetype(path, size)
    return PIL.ImageFont.load_default()


def _segmentar_legendas(texto: str, duracao: float) -> list[tuple[float, float, str]]:
    """
    Divide o roteiro em segmentos curtos e distribui o tempo proporcionalmente
    ao número de palavras de cada segmento.

    Retorna lista de (t_inicio, t_fim, texto_segmento).
    """
    import re
    # Quebra em sentenças por pontuação ou a cada MAX_WORDS palavras
    MAX_WORDS = 6
    tokens = re.split(r'(?<=[.!?,;])\s+|\s+', texto.strip())
    tokens = [t for t in tokens if t]

    segmentos: list[str] = []
    buf: list[str] = []
    for tok in tokens:
        buf.append(tok)
        # Força quebra se buffer atingiu max ou terminou sentença
        ends = tok.endswith((".", "!", "?", ",", ";"))
        if len(buf) >= MAX_WORDS or (ends and len(buf) >= 3):
            segmentos.append(" ".join(buf))
            buf = []
    if buf:
        segmentos.append(" ".join(buf))

    if not segmentos:
        return [(0.0, duracao, texto)]

    total_words  = sum(len(s.split()) for s in segmentos)
    resultado: list[tuple[float, float, str]] = []
    t = 0.0
    for seg in segmentos:
        frac  = len(seg.split()) / total_words
        dt    = duracao * frac
        resultado.append((round(t, 3), round(t + dt, 3), seg))
        t += dt
    return resultado


def _criar_legenda_clip(
    texto: str,
    duracao: float,
    vid_w: int,
    vid_h: int,
    fontsize: int = 52,
    y_pos_ratio: float = 0.55,
) -> ImageClip:
    """
    Cria um ImageClip com legenda estilo TikTok:
    - Fundo preto semi-transparente arredondado
    - Texto branco com sombra escura para máxima legibilidade
    - Posicionado no centro vertical do frame (y_pos_ratio)
    Não altera nenhum parâmetro de encoding — zero perda de qualidade.
    """
    MAX_CHARS = 38
    words = texto.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        test = f"{cur} {w}".strip()
        if len(test) > MAX_CHARS and cur:
            lines.append(cur)
            cur = w
        else:
            cur = test
    if cur:
        lines.append(cur)

    font = _get_font(fontsize)

    # Dimensões do bloco de texto
    line_h  = fontsize + 12
    pad_x   = 36
    pad_y   = 22
    # Mede a largura máxima real das linhas
    tmp_img = PIL.Image.new("RGBA", (1, 1))
    tmp_drw = PIL.ImageDraw.Draw(tmp_img)
    max_lw  = max(tmp_drw.textbbox((0, 0), l, font=font)[2] for l in lines)
    box_w   = min(max_lw + pad_x * 2, vid_w - 80)
    box_h   = len(lines) * line_h + pad_y * 2

    img = PIL.Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    drw = PIL.ImageDraw.Draw(img)

    # Fundo arredondado semi-transparente
    drw.rounded_rectangle([0, 0, box_w - 1, box_h - 1], radius=18, fill=(0, 0, 0, 185))

    # Texto linha a linha
    for i, line in enumerate(lines):
        lb  = drw.textbbox((0, 0), line, font=font)
        lw  = lb[2] - lb[0]
        lx  = (box_w - lw) // 2
        ly  = pad_y + i * line_h
        # Sombra (4 deslocamentos)
        for dx, dy in ((-2, -2), (2, -2), (-2, 2), (2, 2)):
            drw.text((lx + dx, ly + dy), line, font=font, fill=(0, 0, 0, 255))
        drw.text((lx, ly), line, font=font, fill=(255, 255, 255, 255))

    arr = np.array(img)
    x   = (vid_w - box_w) // 2
    y   = int(vid_h * y_pos_ratio) - box_h // 2
    y   = max(10, min(y, vid_h - box_h - 10))

    return ImageClip(arr).set_duration(duracao).set_position((x, y))


def _wrap_text(draw: PIL.ImageDraw.Draw, text: str, font, max_width: int) -> list[str]:
    """Quebra texto em linhas que cabem em max_width pixels."""
    words = text.split()
    lines, cur = [], ""
    for word in words:
        test = f"{cur} {word}".strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] > max_width and cur:
            lines.append(cur)
            cur = word
        else:
            cur = test
    if cur:
        lines.append(cur)
    return lines


def criar_frame_produto(img_path: str, produto: dict) -> str:
    """
    Cria imagem 1080x1920 com:
    - imagem do produto como fundo (cover + gradiente escuro)
    - badge laranja "OFERTA TECH" no topo
    - nome do produto em branco
    - preco em amarelo + badge verde de desconto
    - CTA branco no rodape
    Retorna o path da imagem gerada.
    """
    W, H = VIDEO_W, VIDEO_H

    # Abre e faz cover-resize
    img = PIL.Image.open(img_path).convert("RGBA")
    scale = max(W / img.width, H / img.height)
    img = img.resize((int(img.width * scale), int(img.height * scale)), PIL.Image.LANCZOS)
    left = (img.width - W) // 2
    top  = (img.height - H) // 2
    img  = img.crop((left, top, left + W, top + H))

    # Overlay gradiente para legibilidade
    overlay = PIL.Image.new("RGBA", (W, H), (0, 0, 0, 0))
    drw = PIL.ImageDraw.Draw(overlay)
    # Gradiente topo (180px)
    for y in range(180):
        a = int(170 * (1 - y / 180))
        drw.line([(0, y), (W, y)], fill=(0, 0, 0, a))
    # Gradiente base (700px)
    grad_start = H - 700
    for y in range(grad_start, H):
        a = int(210 * (y - grad_start) / 700)
        drw.line([(0, y), (W, y)], fill=(0, 0, 0, a))
    img = PIL.Image.alpha_composite(img, overlay)
    drw = PIL.ImageDraw.Draw(img)

    # ── Fonte ──────────────────────────────────────────────────────────────────
    f_badge  = _get_font(50)
    f_titulo = _get_font(54)
    f_preco  = _get_font(90)
    f_desc   = _get_font(58)
    f_cta    = _get_font(46)

    pad = 60  # margem lateral

    # ── Badge topo ─────────────────────────────────────────────────────────────
    badge_txt = "OFERTA TECH"
    bb = drw.textbbox((0, 0), badge_txt, font=f_badge)
    bw = bb[2] - bb[0] + 60
    bh = bb[3] - bb[1] + 24
    bx = (W - bw) // 2
    by = 55
    drw.rounded_rectangle([bx, by, bx + bw, by + bh], radius=18, fill=(230, 60, 0, 230))
    drw.text((bx + 30, by + 12), badge_txt, font=f_badge, fill=(255, 255, 255, 255))

    # ── Titulo ─────────────────────────────────────────────────────────────────
    lines = _wrap_text(drw, produto["titulo"], f_titulo, W - pad * 2)[:3]
    ty    = H - 660
    lh    = 68
    for i, line in enumerate(lines):
        lb = drw.textbbox((0, 0), line, font=f_titulo)
        lx = (W - (lb[2] - lb[0])) // 2
        drw.text((lx + 2, ty + i * lh + 2), line, font=f_titulo, fill=(0, 0, 0, 160))
        drw.text((lx, ty + i * lh),          line, font=f_titulo, fill=(255, 255, 255, 255))

    # ── Preco ──────────────────────────────────────────────────────────────────
    preco_txt = f"R$ {produto['preco']:.2f}"
    pb = drw.textbbox((0, 0), preco_txt, font=f_preco)
    pw = pb[2] - pb[0]
    px = (W - pw) // 2 - 80
    py = ty + len(lines) * lh + 20
    drw.text((px + 2, py + 2), preco_txt, font=f_preco, fill=(0, 0, 0, 160))
    drw.text((px, py),         preco_txt, font=f_preco, fill=(255, 215, 0, 255))

    # ── Badge desconto ─────────────────────────────────────────────────────────
    desc_txt = f"-{produto['desconto']}%"
    db = drw.textbbox((0, 0), desc_txt, font=f_desc)
    dw = db[2] - db[0] + 30
    dh = db[3] - db[1] + 22
    dx = px + pw + 20
    dy = py + (pb[3] - pb[1] - dh) // 2 + 8
    drw.rounded_rectangle([dx, dy, dx + dw, dy + dh], radius=14, fill=(20, 190, 80, 235))
    drw.text((dx + 15, dy + 11), desc_txt, font=f_desc, fill=(255, 255, 255, 255))

    # ── CTA rodape ─────────────────────────────────────────────────────────────
    cta_txt = "Entre no grupo do Telegram!"
    cb  = drw.textbbox((0, 0), cta_txt, font=f_cta)
    cx  = (W - (cb[2] - cb[0])) // 2
    cy  = H - 110
    drw.text((cx + 2, cy + 2), cta_txt, font=f_cta, fill=(0, 0, 0, 160))
    drw.text((cx, cy),         cta_txt, font=f_cta, fill=(255, 255, 255, 255))

    out = img_path.rsplit(".", 1)[0] + "_card.png"
    img.convert("RGB").save(out, "PNG")
    return out


# ── Música de fundo ───────────────────────────────────────────────────────────
def _find_music(music_dir: str) -> str | None:
    if not os.path.exists(music_dir):
        return None
    files: list[str] = []
    for ext in ("*.mp3", "*.wav", "*.m4a"):
        files.extend(glob.glob(os.path.join(music_dir, "**", ext), recursive=True))
        files.extend(glob.glob(os.path.join(music_dir, ext)))
    return random.choice(files) if files else None


# ── MoviePy — monta vídeo ─────────────────────────────────────────────────────
def montar_video(
    frame_path: str,
    audio_path: str,
    output_path: str,
    music_path: str | None = None,
    music_volume: float = 0.15,
    legendas: list[tuple[float, float, str]] | None = None,
) -> str:
    """
    Monta o vídeo final:
    - frame_path  : imagem do produto com overlay PIL já aplicado
    - audio_path  : narração MP3 (gTTS)
    - legendas    : lista de (t_inicio, t_fim, texto) — cada frase sincronizada
    Qualidade: CRF 22 inalterado. As legendas são composited antes do encode,
    sem passo extra de compressão.
    """
    narration = AudioFileClip(audio_path)
    duration  = float(narration.duration)

    # ── Música de fundo ────────────────────────────────────────────────────────
    if music_path and os.path.exists(music_path):
        try:
            music = AudioFileClip(music_path)
            if music.duration < duration:
                loops = int(duration / music.duration) + 1
                music = concatenate_audioclips([music] * loops)
            music = music.subclip(0, duration).volumex(music_volume).set_duration(duration)
            final_audio = CompositeAudioClip([
                narration.set_start(0),
                music.set_start(0),
            ]).set_duration(duration)
        except Exception as e:
            print(f"  ⚠️  Musica de fundo: {e}")
            final_audio = narration.set_duration(duration)
    else:
        final_audio = narration.set_duration(duration)

    # ── Imagem base (cover-resize exato) ──────────────────────────────────────
    img_clip = ImageClip(frame_path)
    scale    = max(VIDEO_W / img_clip.w, VIDEO_H / img_clip.h)
    img_clip = img_clip.resize(scale)
    img_clip = img_clip.crop(
        x_center=img_clip.w / 2,
        y_center=img_clip.h / 2,
        width=VIDEO_W,
        height=VIDEO_H,
    )
    img_clip = img_clip.set_duration(duration).set_audio(final_audio)

    # ── Legendas sincronizadas ─────────────────────────────────────────────────
    if legendas:
        sub_clips = []
        for t_ini, t_fim, txt in legendas:
            seg_dur = max(0.1, t_fim - t_ini)
            clip = (
                _criar_legenda_clip(txt, seg_dur, VIDEO_W, VIDEO_H)
                .set_start(t_ini)
            )
            sub_clips.append(clip)
        video = CompositeVideoClip([img_clip] + sub_clips, size=(VIDEO_W, VIDEO_H))
        video = video.set_duration(duration).set_audio(final_audio)
    else:
        video = img_clip

    video.write_videofile(
        output_path,
        fps=VIDEO_FPS,
        codec="libx264",
        audio_codec="aac",
        temp_audiofile="temp-audio-afiliado.m4a",
        remove_temp=True,
        threads=4,
        # CRF 22 = mesma qualidade de antes; legendas não alteram esse valor
        ffmpeg_params=["-preset", "fast", "-crf", "22", "-maxrate", "2000k", "-bufsize", "4000k"],
        logger=None,
    )

    img_clip.close()
    video.close()
    narration.close()
    return output_path


# ── Telegram ──────────────────────────────────────────────────────────────────
def enviar_video_telegram(video_path: str, caption: str) -> bool:
    size_mb = os.path.getsize(video_path) / 1024 / 1024
    print(f"  📤 Enviando video ({size_mb:.1f} MB) ao Telegram...")
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendVideo"
    try:
        with open(video_path, "rb") as f:
            r = requests.post(
                url,
                files={"video": f},
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption[:1024]},
                timeout=300,
            )
        if r.status_code == 200 and r.json().get("ok"):
            print("  ✅ Video enviado ao Telegram!")
            return True
        # Fallback: sendDocument (sem preview nativo)
        if size_mb > 45:
            print("  🔄 Tentando como documento...")
            with open(video_path, "rb") as f:
                r2 = requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument",
                    files={"document": f},
                    data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption[:1024]},
                    timeout=300,
                )
            if r2.status_code == 200 and r2.json().get("ok"):
                print("  ✅ Enviado como documento!")
                return True
        print(f"  ❌ Telegram: {r.text[:300]}")
        return False
    except Exception as e:
        print(f"  ❌ Erro Telegram: {e}")
        return False


# ── Pipeline principal ────────────────────────────────────────────────────────
def gerar_e_enviar_video_produto(produto_externo: dict | None = None) -> bool:
    """
    Gera e envia 1 vídeo de produto ao Telegram.

    Args:
        produto_externo: dict com chaves:
            titulo, preco (float), desconto (int), img_url (str), link (str)
            Se None, busca o produto TOP da Shopee.

    Returns:
        True se o vídeo foi enviado com sucesso.
    """
    print("\n🎬 Iniciando geracao de video de produto...")

    produto = produto_externo or buscar_produto_top_video()
    if not produto:
        print("  ❌ Nenhum produto encontrado.")
        return False

    print(f"  🛍️  {produto['titulo'][:70]}")
    print(f"  💰 R$ {produto['preco']:.2f} | -{produto['desconto']}%")

    from datetime import datetime
    ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmp_dir = tempfile.mkdtemp(prefix="video_afiliado_")

    try:
        # 1. Imagem
        img_raw  = os.path.join(tmp_dir, "produto.jpg")
        img_path = baixar_imagem(produto["img_url"], img_raw)
        if not img_path:
            print("  ❌ Falha ao baixar imagem.")
            return False
        print("  ✅ Imagem baixada")

        # 2. Frame com overlay
        frame_path = criar_frame_produto(img_path, produto)
        print("  ✅ Frame criado")

        # 3. Roteiro Groq
        roteiro = gerar_roteiro_groq(produto)
        print(f"  ✅ Roteiro: {roteiro[:80]}...")

        # 4. TTS (Edge — voz natural)
        audio_path = os.path.join(tmp_dir, "narration.mp3")
        narrar_edge(roteiro, audio_path)
        print(f"  ✅ Narracao gerada  [{EDGE_TTS_VOICE} | rate {EDGE_TTS_RATE}]")

        # 5. Música de fundo
        music_path = _find_music(AUDIOS_DIR)
        if music_path:
            print(f"  🎵 Musica: {os.path.basename(music_path)}")
        else:
            print("  ℹ️  Sem musica de fundo (audios/ nao encontrado)")

        # 6. Gera legendas sincronizadas com a duração do áudio
        from moviepy.editor import AudioFileClip as _AClip
        _dur = float(_AClip(audio_path).duration)
        legendas = _segmentar_legendas(roteiro, _dur)
        print(f"  ✅ Legendas: {len(legendas)} segmento(s)")

        # 7. Monta vídeo
        output_path = os.path.join(OUTPUT_DIR, f"oferta_{ts}.mp4")
        print("  ⚙️  Montando video com legendas (aguarde)...")
        montar_video(frame_path, audio_path, output_path, music_path, legendas=legendas)
        print("  ✅ Video montado")

        # 7. Caption
        caption = (
            f"🔥 OFERTA TECH\n\n"
            f"📦 {produto['titulo'][:80]}\n\n"
            f"💰 R$ {produto['preco']:.2f} (-{produto['desconto']}%)\n\n"
            f"👉 {produto['link']}\n\n"
            f"🔔 Entre no grupo para mais ofertas exclusivas!\n\n"
            f"#tech #setup #gamer #oferta #desconto"
        )

        # 8. Envia
        ok = enviar_video_telegram(output_path, caption)
        return ok

    except Exception as e:
        print(f"  ❌ Erro no pipeline de video: {e}")
        traceback.print_exc()
        return False


# ── Entrypoint standalone ─────────────────────────────────────────────────────
if __name__ == "__main__":
    if not GROQ_API_KEY:
        raise EnvironmentError("GROQ_API_KEY nao definido no .env")
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        raise EnvironmentError("TELEGRAM_TOKEN / TELEGRAM_CHAT_ID nao definidos no .env")
    gerar_e_enviar_video_produto()
