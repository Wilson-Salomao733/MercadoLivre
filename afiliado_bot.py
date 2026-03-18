#!/usr/bin/env python3
"""
Bot de Afiliados - Mercado Livre + Shopee → Telegram
Busca automaticamente produtos tech/setup com desconto e envia links de afiliado
"""
import requests
import json
import time
import re
import os
import hashlib
import schedule
from datetime import datetime
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# Carrega .env do mesmo diretório do script
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# ─── CONFIGURAÇÕES ────────────────────────────────────────────────────────────

def _require(var: str) -> str:
    value = os.getenv(var)
    if not value:
        raise EnvironmentError(f"Variável de ambiente obrigatória não definida: {var}")
    return value

ML_CLIENT_ID     = _require("ML_CLIENT_ID")
ML_CLIENT_SECRET = _require("ML_CLIENT_SECRET")
ML_TOKENS_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tokens.json")

TELEGRAM_TOKEN   = _require("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = _require("TELEGRAM_CHAT_ID")

# WhatsApp Cloud API
WA_TOKEN    = _require("WA_TOKEN")
WA_PHONE_ID = _require("WA_PHONE_ID")
WA_TO       = _require("WA_TO")

# ID de afiliado do Mercado Livre
MATT_TOOL = _require("MATT_TOOL")
MATT_WORD = _require("MATT_WORD")

# Credenciais Shopee Afiliados
SHOPEE_APP_ID = _require("SHOPEE_APP_ID")
SHOPEE_SECRET = _require("SHOPEE_SECRET")
SHOPEE_GQL    = "https://open-api.affiliate.shopee.com.br/graphql"

# Intervalo entre rodadas (em horas)
INTERVALO_HORAS = 2

# Desconto mínimo para postar (%)
DESCONTO_MINIMO = 10

# Máximo de produtos por rodada
MAX_PRODUTOS = 8

# IDs dos produtos já enviados (evita repetição)
ENVIADOS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "enviados.json")

# ─── KEYWORDS DE TECH/SETUP ───────────────────────────────────────────────────

TECH_KEYWORDS = [
    "teclado mecanico",
    "mouse gamer",
    "headset gamer",
    "monitor 27",
    "webcam full hd",
    "hub usb-c",
    "suporte monitor",
    "cadeira gamer",
    "microfone condensador",
    "placa de video",
    "ssd nvme",
    "memoria ram",
    "notebook",
    "mousepad gamer",
    "cooler processador",
    "fonte modular",
    "gabinete gamer",
    "teclado setup",
]

TECH_PALAVRAS = {
    "teclado", "mouse", "headset", "monitor", "webcam", "hub", "usb",
    "ssd", "hd", "ram", "notebook", "setup", "gamer", "cooler", "fonte",
    "gabinete", "placa", "microfone", "mousepad", "processador", "cpu",
    "gpu", "rtx", "rx", "intel", "amd", "ryzen", "i5", "i7", "i9",
    "cadeira", "suporte", "led", "rgb", "wireless", "bluetooth",
}

HASHTAGS_TECH = "#setup #tech #programador #gamer #tecnologia"

# ─── TOKEN ML ─────────────────────────────────────────────────────────────────

def carregar_tokens():
    try:
        with open(ML_TOKENS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def salvar_tokens(tokens):
    with open(ML_TOKENS_FILE, "w") as f:
        json.dump(tokens, f, indent=2)

def renovar_token():
    tokens = carregar_tokens()
    refresh = tokens.get("refresh_token")
    if not refresh:
        print("❌ Sem refresh_token disponível.")
        return None

    resp = requests.post(
        "https://api.mercadolibre.com/oauth/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type":    "refresh_token",
            "client_id":     ML_CLIENT_ID,
            "client_secret": ML_CLIENT_SECRET,
            "refresh_token": refresh,
        },
        timeout=15,
    )
    if resp.status_code == 200:
        novos = resp.json()
        novos["expires_at"]  = time.time() + novos.get("expires_in", 21600)
        novos["created_at"]  = time.time()
        salvar_tokens(novos)
        print(f"✅ Token renovado. Expira em {novos['expires_in']//3600}h.")
        return novos["access_token"]
    else:
        print(f"❌ Erro ao renovar token: {resp.text}")
        return None

def get_token():
    tokens = carregar_tokens()
    expira = tokens.get("expires_at", 0)
    # Renova se faltam menos de 30 minutos
    if time.time() >= expira - 1800:
        print("🔄 Token prestes a expirar, renovando...")
        return renovar_token()
    return tokens.get("access_token")

# ─── PRODUTOS JÁ ENVIADOS ─────────────────────────────────────────────────────

def carregar_enviados():
    try:
        with open(ENVIADOS_FILE, "r") as f:
            return set(json.load(f))
    except Exception:
        return set()

def salvar_enviados(ids: set):
    # Mantém só os últimos 500 para não crescer infinitamente
    lista = list(ids)[-500:]
    with open(ENVIADOS_FILE, "w") as f:
        json.dump(lista, f)

# ─── BUSCA NO MERCADO LIVRE ───────────────────────────────────────────────────

SCRAPE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

def buscar_produtos_por_keyword(keyword: str, token: str, limit: int = 10) -> list:
    """Scraping direto da página de listagem do ML — extrai dados completos do HTML."""
    produtos = []
    try:
        url = f"https://lista.mercadolivre.com.br/{keyword.replace(' ', '-')}"
        resp = requests.get(url, headers=SCRAPE_HEADERS, timeout=20)
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        cards = soup.find_all("li", class_=re.compile(r"ui-search-layout__item"))

        for card in cards[:limit]:
            try:
                # Título e link
                titulo_el = card.find("a", class_=re.compile(r"poly-component__title"))
                if not titulo_el:
                    continue
                titulo = titulo_el.get_text(strip=True)
                link   = titulo_el.get("href", "").split("?")[0]
                if not link:
                    continue

                # ID do produto (MLB...)
                item_id_match = re.search(r"MLB\d{7,12}", link)
                item_id = item_id_match.group() if item_id_match else link

                price_box = card.find("div", class_=re.compile(r"poly-component__price"))
                if not price_box:
                    continue

                # Preço original (dentro de <s>)
                orig_s = price_box.find("s", class_=re.compile(r"andes-money-amount--previous"))
                preco_orig = 0.0
                if orig_s:
                    orig_frac = orig_s.find("span", class_=re.compile(r"andes-money-amount__fraction"))
                    if orig_frac:
                        preco_orig = float(re.sub(r"\D", "", orig_frac.get_text()) or 0)

                # Preço atual: primeira fraction que NÃO está dentro de <s> e NÃO em parcelamento
                preco = 0.0
                preco_cents = 0.0
                for span in price_box.find_all("span", class_=re.compile(r"andes-money-amount__fraction")):
                    # Ignora se está dentro do <s> (preço original riscado)
                    if span.find_parent("s"):
                        continue
                    # Ignora se está dentro de container de parcelamento
                    if span.find_parent(class_=re.compile(r"installment|poly-price__installments|andes-money-amount--previous")):
                        continue
                    preco = float(re.sub(r"\D", "", span.get_text()) or 0)
                    # Pega os centavos do mesmo container pai
                    pai = span.find_parent()
                    if pai:
                        cents_el = pai.find("span", class_=re.compile(r"andes-money-amount__cents"))
                        if cents_el:
                            preco_cents = float(re.sub(r"\D", "", cents_el.get_text()) or 0) / 100
                    break

                preco += preco_cents

                if preco == 0:
                    continue

                # Desconto
                desc_el = price_box.find("span", class_=re.compile(r"andes-money-amount__discount|poly-price__discount"))
                desconto = 0
                if desc_el:
                    d_match = re.search(r"(\d+)", desc_el.get_text())
                    if d_match:
                        desconto = int(d_match.group(1))
                elif preco_orig > preco:
                    desconto = round((1 - preco / preco_orig) * 100)

                # Frete grátis
                frete_el = card.find(string=re.compile(r"[Ff]rete\s+gr", re.I))
                frete_gratis = bool(frete_el)

                if preco_orig == 0:
                    preco_orig = preco

                produtos.append({
                    "id":           item_id,
                    "titulo":       titulo,
                    "preco":        preco,
                    "preco_orig":   preco_orig,
                    "desconto":     desconto,
                    "vendidos":     0,
                    "permalink":    link,
                    "categoria":    "",
                    "condicao":     "new",
                    "frete_gratis": frete_gratis,
                })
            except Exception:
                continue

    except Exception as e:
        print(f"  ⚠️  Scraping falhou para '{keyword}': {e}")

    return produtos

# ─── FILTROS ──────────────────────────────────────────────────────────────────

def e_tech(produto: dict) -> bool:
    titulo_lower = produto["titulo"].lower()
    # Verifica se alguma palavra-chave tech está no título
    for palavra in TECH_PALAVRAS:
        if palavra in titulo_lower:
            return True
    # Verifica se categoria começa com MLB1648 (Tecnologia) ou similares
    cat = produto.get("categoria", "")
    categorias_tech = {"MLB1648", "MLB1574", "MLB1430", "MLB1500", "MLB218519"}
    if any(cat.startswith(c) for c in categorias_tech):
        return True
    return False

def tem_desconto_suficiente(produto: dict) -> bool:
    # Filtra por desconto mínimo e exclui descontos suspeitos (>95% geralmente são fraudes)
    return DESCONTO_MINIMO <= produto["desconto"] <= 95

def filtrar_produtos(produtos: list, enviados: set) -> list:
    resultado = []
    for p in produtos:
        if p["id"] in enviados:
            continue
        if not e_tech(p):
            continue
        if not tem_desconto_suficiente(p):
            continue
        resultado.append(p)
    return resultado

# ─── LINK DE AFILIADO ─────────────────────────────────────────────────────────

def gerar_link_afiliado(produto: dict) -> str:
    base = produto["permalink"]
    return (
        f"{base}?matt_tool={MATT_TOOL}"
        f"&matt_word={MATT_WORD}"
        f"&matt_source=telegram"
        f"&matt_campaign=afiliado_tech"
        f"&matt_medium=cpm"
    )

# ─── MENSAGEM TELEGRAM ────────────────────────────────────────────────────────

def formatar_mensagem(produto: dict) -> str:
    link    = gerar_link_afiliado(produto)
    titulo  = produto["titulo"]
    preco   = produto["preco"]
    orig    = produto["preco_orig"]
    desc    = produto["desconto"]
    vendas  = produto["vendidos"]
    frete   = "✅ Frete Grátis" if produto["frete_gratis"] else "🚚 Ver frete"
    cond    = "Novo" if produto["condicao"] == "new" else "Usado"

    msg = (
        f"🔥 <b>OFERTA TECH</b> | Setup & Programador\n\n"
        f"📦 <b>{titulo}</b>\n\n"
    )
    if desc > 0 and orig != preco:
        msg += f"💸 <s>R$ {orig:,.0f}</s>  →  "
    msg += f"💰 <b>R$ {preco:,.0f}</b>"
    if desc > 0:
        msg += f"  <b>(-{desc}%)</b>"
    msg += f"\n\n{frete}\n\n"
    msg += f'👉 <a href="{link}">PEGAR OFERTA AGORA</a>\n\n'
    msg += HASHTAGS_TECH

    return msg

# ─── TELEGRAM ─────────────────────────────────────────────────────────────────

def enviar_telegram(mensagem: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        json={
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       mensagem,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        },
        timeout=15,
    )
    return resp.status_code == 200

def enviar_cabecalho():
    msg = (
        "🤖 <b>Bot de Afiliados Iniciado</b>\n\n"
        f"🕐 {datetime.now().strftime('%d/%m/%Y %H:%M')}\n"
        f"🔎 Buscando melhores ofertas tech...\n\n"
        f"<i>Próxima atualização em {INTERVALO_HORAS}h</i>"
    )
    enviar_telegram(msg)

def enviar_whatsapp(texto: str) -> bool:
    """Envia mensagem de texto simples via WhatsApp Cloud API."""
    url = f"https://graph.facebook.com/v22.0/{WA_PHONE_ID}/messages"
    try:
        r = requests.post(url,
            headers={
                "Authorization": f"Bearer {WA_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "messaging_product": "whatsapp",
                "to": WA_TO,
                "type": "text",
                "text": {"body": texto},
            },
            timeout=15,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"  ⚠️  WhatsApp erro: {e}")
        return False

def enviar_resumo(total: int):
    msg = (
        f"✅ <b>Rodada Finalizada</b>\n\n"
        f"📊 {total} produto(s) enviado(s)\n"
        f"⏰ Próxima busca em {INTERVALO_HORAS}h"
    )
    enviar_telegram(msg)

# ─── SHOPEE ───────────────────────────────────────────────────────────────────

def shopee_gql(query: str, variables: dict = None) -> dict:
    ts      = int(time.time())
    payload = json.dumps({"query": query, "variables": variables or {}}, separators=(",", ":"))
    sig     = hashlib.sha256(f"{SHOPEE_APP_ID}{ts}{payload}{SHOPEE_SECRET}".encode()).hexdigest()
    headers = {
        "Content-Type":  "application/json",
        "Authorization": f"SHA256 Credential={SHOPEE_APP_ID}, Timestamp={ts}, Signature={sig}",
    }
    try:
        r = requests.post(SHOPEE_GQL, headers=headers, data=payload, timeout=15)
        return r.json()
    except Exception as e:
        print(f"  ⚠️  Shopee GQL erro: {e}")
        return {}

SHOPEE_QUERY = """
query($keyword: String, $limit: Int, $page: Int) {
  productOfferV2(keyword: $keyword, limit: $limit, page: $page, sortType: 2) {
    nodes {
      itemId
      shopId
      productName
      price
      priceMin
      priceDiscountRate
      commissionRate
      sales
      ratingStar
      offerLink
    }
  }
}
"""

def buscar_shopee(keyword: str, limit: int = 8) -> list:
    data    = shopee_gql(SHOPEE_QUERY, {"keyword": keyword, "limit": limit, "page": 1})
    nodes   = data.get("data", {}).get("productOfferV2", {}).get("nodes") or []
    produtos = []
    for p in nodes:
        try:
            preco    = float(p.get("priceMin") or p.get("price") or 0)
            desconto = int(p.get("priceDiscountRate") or 0)
            link     = p.get("offerLink", "")
            titulo   = p.get("productName", "")
            if not titulo or not link or preco == 0:
                continue
            item_id = f"shopee_{p.get('itemId')}_{p.get('shopId')}"
            produtos.append({
                "id":           item_id,
                "titulo":       titulo,
                "preco":        preco,
                "preco_orig":   round(preco / (1 - desconto / 100), 2) if desconto > 0 else preco,
                "desconto":     desconto,
                "vendidos":     int(p.get("sales") or 0),
                "permalink":    link,
                "frete_gratis": False,
                "fonte":        "shopee",
            })
        except Exception:
            continue
    return produtos

def formatar_mensagem_shopee(produto: dict) -> str:
    titulo  = produto["titulo"]
    preco   = produto["preco"]
    orig    = produto["preco_orig"]
    desc    = produto["desconto"]
    link    = produto["permalink"]

    msg = (
        f"🛍️ <b>OFERTA SHOPEE</b> | Tech & Setup\n\n"
        f"📦 <b>{titulo}</b>\n\n"
    )
    if desc > 0 and orig != preco:
        msg += f"💸 <s>R$ {orig:,.2f}</s>  →  "
    msg += f"💰 <b>R$ {preco:,.2f}</b>"
    if desc > 0:
        msg += f"  <b>(-{desc}%)</b>"
    msg += f"\n\n👉 <a href=\"{link}\">PEGAR OFERTA AGORA</a>\n\n"
    msg += "#shopee #setup #tech #programador #gamer"
    return msg

# ─── FLUXO PRINCIPAL ──────────────────────────────────────────────────────────

def rodar_busca():
    print(f"\n{'='*50}")
    print(f"🚀 Iniciando busca — {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print(f"{'='*50}")

    token = get_token()
    if not token:
        print("❌ Sem token válido. Abortando.")
        return

    enviados = carregar_enviados()
    todos_produtos = []

    # Busca em cada keyword (scraping, não usa token)
    for kw in TECH_KEYWORDS:
        print(f"🔍 Buscando: {kw}")
        produtos = buscar_produtos_por_keyword(kw, token, limit=8)
        print(f"   → {len(produtos)} produto(s)")
        todos_produtos.extend(produtos)
        time.sleep(1.5)

    # Remove duplicatas por ID
    vistos = set()
    sem_dup = []
    for p in todos_produtos:
        if p["id"] not in vistos:
            vistos.add(p["id"])
            sem_dup.append(p)

    # Filtra por tech + desconto + não enviados
    filtrados = filtrar_produtos(sem_dup, enviados)

    # Ordena por maior desconto
    filtrados.sort(key=lambda x: x["desconto"], reverse=True)

    print(f"\n📦 {len(sem_dup)} produtos encontrados | {len(filtrados)} passaram no filtro")

    if not filtrados:
        print("😴 Nenhum produto novo com desconto suficiente.")
        return

    # Envia até MAX_PRODUTOS
    enviados_agora = 0
    for produto in filtrados[:MAX_PRODUTOS]:
        mensagem = formatar_mensagem(produto)
        ok_tg = enviar_telegram(mensagem)
        # WhatsApp não suporta HTML — envia versão texto puro
        texto_wa = (
            f"🔥 OFERTA TECH | Setup & Programador\n\n"
            f"📦 {produto['titulo']}\n\n"
            f"💰 R$ {produto['preco']:,.0f} (-{produto['desconto']}%)\n\n"
            f"{'✅ Frete Grátis' if produto['frete_gratis'] else '🚚 Ver frete'}\n\n"
            f"👉 {gerar_link_afiliado(produto)}\n\n"
            f"#setup #tech #programador #gamer"
        )
        ok_wa = enviar_whatsapp(texto_wa)
        if ok_tg:
            enviados.add(produto["id"])
            enviados_agora += 1
            wa_status = "✅WA" if ok_wa else "❌WA"
            print(f"  ✅TG {wa_status} {produto['titulo'][:45]} | -{produto['desconto']}%")
        else:
            print(f"  ❌ Falha: {produto['titulo'][:50]}")
        time.sleep(2)

    salvar_enviados(enviados)
    print(f"\n📤 {enviados_agora} produto(s) ML enviado(s) ao Telegram.")

    # ── Shopee ──────────────────────────────────────────────────────────────
    print(f"\n🛍️  Buscando produtos Shopee...")
    shopee_produtos = []
    for kw in TECH_KEYWORDS[:10]:
        prods = buscar_shopee(kw, limit=5)
        print(f"   {kw}: {len(prods)} produto(s)")
        shopee_produtos.extend(prods)
        time.sleep(0.5)

    # Remove duplicatas e já enviados
    vistos_shopee = set()
    shopee_filtrados = []
    for p in shopee_produtos:
        if p["id"] in enviados or p["id"] in vistos_shopee:
            continue
        if not (DESCONTO_MINIMO <= p["desconto"] <= 95):
            continue
        vistos_shopee.add(p["id"])
        shopee_filtrados.append(p)

    shopee_filtrados.sort(key=lambda x: x["desconto"], reverse=True)
    print(f"   → {len(shopee_filtrados)} passaram no filtro")

    enviados_shopee = 0
    for produto in shopee_filtrados[:MAX_PRODUTOS]:
        mensagem = formatar_mensagem_shopee(produto)
        ok = enviar_telegram(mensagem)
        if ok:
            enviados.add(produto["id"])
            enviados_shopee += 1
            print(f"  ✅ [Shopee] {produto['titulo'][:50]} | -{produto['desconto']}%")
        else:
            print(f"  ❌ [Shopee] Falha: {produto['titulo'][:50]}")
        time.sleep(2)

    salvar_enviados(enviados)
    total_enviados = enviados_agora + enviados_shopee
    print(f"\n📤 Total enviado: {enviados_agora} ML + {enviados_shopee} Shopee = {total_enviados} ofertas")
    enviar_resumo(total_enviados)

def main():
    print("🤖 Bot de Afiliados ML → Telegram")
    print(f"📡 Canal: {TELEGRAM_CHAT_ID}")
    print(f"⏰ Intervalo: a cada {INTERVALO_HORAS}h\n")

    # Testa conexão Telegram
    ok = enviar_telegram("🤖 <b>Bot Afiliado Conectado!</b>\n\nAguarde as ofertas tech chegarem aqui... 🛒")
    if not ok:
        print("❌ Falha na conexão com Telegram. Verifique o token.")
        return

    print("✅ Telegram conectado!\n")

    # Primeira rodada imediata
    rodar_busca()

    # Agenda próximas rodadas
    schedule.every(INTERVALO_HORAS).hours.do(rodar_busca)

    print(f"\n⏳ Agendado para rodar a cada {INTERVALO_HORAS}h. Pressione Ctrl+C para parar.")
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    main()
