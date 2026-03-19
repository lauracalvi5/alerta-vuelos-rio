"""
Bot de alertas de vuelos — Sitios argentinos
Ruta: EZE/AEP/ROS → GIG/SDU (Río de Janeiro) — Enero 2027
Precio máximo: USD 300 ida y vuelta
Notificaciones: Telegram + Email + WhatsApp
"""

import os, json, time, re, smtplib, random, urllib.request, urllib.parse
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ══════════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ══════════════════════════════════════════════════════════
CONFIG = {
    "rutas": [
        # ida y vuelta a Río de Janeiro — Enero 2027
        # GIG = Galeão (aeropuerto internacional)
        # SDU = Santos Dumont (más céntrico, vuelos regionales)
        # max_price_usd = total ida y vuelta en dólares
        {"origin": "EZE", "destination": "GIG", "month": "2027-01", "max_price_usd": 250},
        {"origin": "EZE", "destination": "SDU", "month": "2027-01", "max_price_usd": 250},
        {"origin": "AEP", "destination": "GIG", "month": "2027-01", "max_price_usd": 250},
        {"origin": "ROS", "destination": "GIG", "month": "2027-01", "max_price_usd": 250},
    ],

    # Telegram
    "telegram_bot_token": os.environ.get("TELEGRAM_BOT_TOKEN", ""),
    "telegram_chat_id":   os.environ.get("TELEGRAM_CHAT_ID", ""),

    # Email Gmail
    "email_sender":   os.environ.get("EMAIL_SENDER", ""),
    "email_password": os.environ.get("EMAIL_PASSWORD", ""),
    "email_receiver": os.environ.get("EMAIL_RECEIVER", ""),

    # WhatsApp (CallMeBot)
    "whatsapp_phone":  os.environ.get("WHATSAPP_PHONE", ""),
    "whatsapp_apikey": os.environ.get("WHATSAPP_APIKEY", ""),

    # True = sin ventana (GitHub Actions), False = ves el browser (debug local)
    "headless": True,
}

DELAY_MIN = 4
DELAY_MAX = 9


# ══════════════════════════════════════════════════════════
#  UTILIDADES
# ══════════════════════════════════════════════════════════

def limpiar_precio(texto: str):
    nums = re.sub(r"[^\d]", "", (texto or "").strip())
    return int(nums) if nums else None


def pausa():
    t = random.uniform(DELAY_MIN, DELAY_MAX)
    print(f"    ⏳ Esperando {t:.1f}s...")
    time.sleep(t)


def obtener_dolar_turista() -> float:
    """
    Cotización dólar turista desde dolarapi.com (API pública argentina, gratis).
    Sirve para convertir precios ARS → USD mostrados por los sitios.
    """
    try:
        url = "https://dolarapi.com/v1/dolares/turista"
        req = urllib.request.Request(url, headers={"User-Agent": "flight-alert-bot/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
            venta = float(data.get("venta", 0))
            if venta > 100:
                print(f"  💵 Dólar turista hoy: ARS {venta:,.0f}")
                return venta
    except Exception:
        pass
    # Fallback: dólar oficial * 1.6
    try:
        url = "https://dolarapi.com/v1/dolares/oficial"
        req = urllib.request.Request(url, headers={"User-Agent": "flight-alert-bot/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
            fallback = float(data.get("venta", 1000)) * 1.6
            print(f"  💵 Dólar (fallback): ARS {fallback:,.0f}")
            return fallback
    except Exception:
        print("  ⚠️  No se pudo obtener dólar. Usando ARS 1400 por defecto.")
        return 1400.0


# ══════════════════════════════════════════════════════════
#  SCRAPERS
# ══════════════════════════════════════════════════════════

def scrape_turismocity(page, origin, dest, month, tipo="ida_vuelta"):
    resultados = []
    año, mes = month.split("-")
    trip = "OW" if tipo == "ida" else "RT"
    # Vuelta aprox 15 días después
    ret = f"{año}-{mes}-16"
    url = (
        f"https://www.turismocity.com.ar/vuelos/resultados"
        f"?from={origin}&to={dest}&depart={año}-{mes}-01"
        f"{'&return=' + ret if trip == 'RT' else ''}"
        f"&adults=1&cabins=Y&type={trip}"
    )
    print(f"    🌐 Turismocity...")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=40_000)
        page.wait_for_selector("[class*='price'],[class*='precio']", timeout=25_000)
        time.sleep(3)
        price_els   = page.query_selector_all("[class*='price'],[class*='precio']")
        airline_els = page.query_selector_all("[class*='airline'],[class*='aerolinea']")
        precios_raw = [el.inner_text() for el in price_els if "$" in (el.inner_text() or "")]
        for i, raw in enumerate(precios_raw[:5]):
            p = limpiar_precio(raw)
            if p and p > 10_000:
                aero = airline_els[i].inner_text() if i < len(airline_els) else "N/D"
                resultados.append({"sitio": "Turismocity", "precio_ars": p,
                                   "aerolinea": aero.strip()[:30], "url": url})
    except PlaywrightTimeout:
        print("    ⚠️  Turismocity: timeout")
    except Exception as e:
        print(f"    ⚠️  Turismocity: {e}")
    return resultados


def scrape_despegar(page, origin, dest, month, tipo="ida_vuelta"):
    resultados = []
    año, mes = month.split("-")
    if tipo == "ida_vuelta":
        url = (
            f"https://www.despegar.com.ar/vuelos/ofertas/{origin}/{dest}"
            f"/{año}-{mes}-01/{año}-{mes}-15/1/0/0/NA/NA"
        )
    else:
        url = (
            f"https://www.despegar.com.ar/vuelos/ofertas/{origin}/{dest}"
            f"/{año}-{mes}-01/1/0/0/NA/NA"
        )
    print(f"    🌐 Despegar...")
    try:
        page.goto(url, wait_until="networkidle", timeout=50_000)
        time.sleep(4)
        page.wait_for_selector("[data-testid='price'],[class*='amount'],[class*='Price']",
                               timeout=30_000)
        prices = page.query_selector_all("[data-testid='price'],[class*='amount']")
        cards  = page.query_selector_all("[data-testid='cluster'],[class*='cluster']")
        for i, p in enumerate(prices[:5]):
            raw = p.inner_text()
            precio = limpiar_precio(raw)
            if precio and precio > 10_000:
                aero = "N/D"
                if i < len(cards):
                    logos = cards[i].query_selector_all("[class*='airline'],[alt]")
                    if logos:
                        aero = logos[0].get_attribute("alt") or "N/D"
                resultados.append({"sitio": "Despegar", "precio_ars": precio,
                                   "aerolinea": aero.strip()[:30], "url": url})
    except PlaywrightTimeout:
        print("    ⚠️  Despegar: timeout")
    except Exception as e:
        print(f"    ⚠️  Despegar: {e}")
    return resultados


def scrape_almundo(page, origin, dest, month, tipo="ida_vuelta"):
    resultados = []
    año, mes = month.split("-")
    trip = "round_trip" if tipo == "ida_vuelta" else "one_way"
    url = (
        f"https://www.almundo.com.ar/vuelos/buscar"
        f"?from={origin}&to={dest}&departure={año}-{mes}-01"
        f"{'&return=' + año + '-' + mes + '-15' if tipo == 'ida_vuelta' else ''}"
        f"&adults=1&cabin=economy&type={trip}"
    )
    print(f"    🌐 Almundo...")
    try:
        page.goto(url, wait_until="networkidle", timeout=50_000)
        time.sleep(4)
        page.wait_for_selector("[class*='price'],[class*='Price']", timeout=25_000)
        prices = page.query_selector_all("[class*='price'],[class*='Price']")
        for p in prices[:5]:
            raw = p.inner_text()
            precio = limpiar_precio(raw)
            if precio and precio > 10_000:
                resultados.append({"sitio": "Almundo", "precio_ars": precio,
                                   "aerolinea": "N/D", "url": url})
    except PlaywrightTimeout:
        print("    ⚠️  Almundo: timeout")
    except Exception as e:
        print(f"    ⚠️  Almundo: {e}")
    return resultados


def scrape_edreams(page, origin, dest, month, tipo="ida_vuelta"):
    resultados = []
    año, mes = month.split("-")
    if tipo == "ida_vuelta":
        url = f"https://www.edreams.com.ar/vuelos/resultado/{origin}-{dest}/{año}{mes}01/{año}{mes}15/1adulto/economica/"
    else:
        url = f"https://www.edreams.com.ar/vuelos/resultado/{origin}-{dest}/{año}{mes}01/1adulto/economica/"
    print(f"    🌐 eDreams...")
    try:
        page.goto(url, wait_until="networkidle", timeout=50_000)
        time.sleep(5)
        page.wait_for_selector("[class*='price'],[class*='Price'],[data-cy*='price']", timeout=30_000)
        prices   = page.query_selector_all("[class*='price'],[data-cy*='price']")
        airlines = page.query_selector_all("[class*='airline'],[class*='Airline']")
        for i, p in enumerate(prices[:5]):
            raw = p.inner_text()
            precio = limpiar_precio(raw)
            if precio and precio > 10_000:
                aero = airlines[i].inner_text() if i < len(airlines) else "N/D"
                resultados.append({"sitio": "eDreams", "precio_ars": precio,
                                   "aerolinea": aero.strip()[:30], "url": url})
    except PlaywrightTimeout:
        print("    ⚠️  eDreams: timeout")
    except Exception as e:
        print(f"    ⚠️  eDreams: {e}")
    return resultados


def scrape_atrapalo(page, origin, dest, month, tipo="ida_vuelta"):
    resultados = []
    año, mes = month.split("-")
    tip = "I" if tipo == "ida_vuelta" else "O"
    url = (
        f"https://www.atrapalo.com.ar/vuelos/busqueda"
        f"?orig={origin}&dest={dest}&dep={año}-{mes}-01"
        f"{'&ret=' + año + '-' + mes + '-15' if tipo == 'ida_vuelta' else ''}"
        f"&pax=1&cabina=Y&tipov={tip}"
    )
    print(f"    🌐 Atrápalo...")
    try:
        page.goto(url, wait_until="networkidle", timeout=50_000)
        time.sleep(4)
        page.wait_for_selector("[class*='price'],[class*='precio'],[class*='tarifa']", timeout=25_000)
        prices = page.query_selector_all("[class*='price'],[class*='precio'],[class*='tarifa']")
        for p in prices[:5]:
            raw = p.inner_text()
            precio = limpiar_precio(raw)
            if precio and precio > 10_000:
                resultados.append({"sitio": "Atrápalo", "precio_ars": precio,
                                   "aerolinea": "N/D", "url": url})
    except PlaywrightTimeout:
        print("    ⚠️  Atrápalo: timeout")
    except Exception as e:
        print(f"    ⚠️  Atrápalo: {e}")
    return resultados


SCRAPERS = [
    scrape_turismocity,
    scrape_despegar,
    scrape_almundo,
    scrape_edreams,
    scrape_atrapalo,
]


# ══════════════════════════════════════════════════════════
#  NOTIFICACIONES
# ══════════════════════════════════════════════════════════

def formatear_alerta(ruta, baratos, dolar):
    origen = ruta["origin"]
    dest   = ruta["destination"]
    limite_usd = ruta["max_price_usd"]
    lineas = [
        f"✈️  VUELO BARATO A RÍO — {origen} → {dest} (ida y vuelta)",
        f"{'─'*36}",
        f"Mes:          Enero 2027",
        f"Tu límite:    USD {limite_usd}",
        f"Dólar turista hoy: ARS {dolar:,.0f}",
        "",
    ]
    for r in baratos:
        precio_usd = round(r["precio_ars"] / dolar)
        precio_ars_fmt = f"ARS {r['precio_ars']:,}".replace(",", ".")
        ahorro = round((1 - precio_usd / limite_usd) * 100)
        lineas += [
            f"🏷️  {r['sitio']}",
            f"   Precio: USD {precio_usd} ({precio_ars_fmt})",
            f"   Ahorro: {ahorro}% bajo tu límite",
            f"   Aerolínea: {r['aerolinea']}",
            f"   🔗 {r['url']}",
            "",
        ]
    lineas.append(f"⏰ {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    return "\n".join(lineas)


def enviar_telegram(mensaje, token, chat_id):
    if not token or not chat_id:
        print("  [SKIP] Telegram no configurado"); return
    texto = urllib.parse.quote(mensaje)
    url = f"https://api.telegram.org/bot{token}/sendMessage?chat_id={chat_id}&text={texto}"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            if json.loads(r.read()).get("ok"):
                print("  [OK] Telegram ✓")
    except Exception as e:
        print(f"  [ERROR] Telegram: {e}")


def enviar_email(mensaje, asunto, cfg):
    if not cfg["email_sender"] or not cfg["email_password"]:
        print("  [SKIP] Email no configurado"); return
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = asunto
        msg["From"]    = cfg["email_sender"]
        msg["To"]      = cfg["email_receiver"]
        msg.attach(MIMEText(mensaje, "plain", "utf-8"))
        html = "<pre style='font-family:sans-serif;font-size:14px'>" + mensaje.replace("\n","<br>") + "</pre>"
        msg.attach(MIMEText(html, "html", "utf-8"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(cfg["email_sender"], cfg["email_password"])
            s.sendmail(cfg["email_sender"], cfg["email_receiver"], msg.as_string())
        print("  [OK] Email ✓")
    except Exception as e:
        print(f"  [ERROR] Email: {e}")


def enviar_whatsapp(mensaje, phone, apikey):
    if not phone or not apikey:
        print("  [SKIP] WhatsApp no configurado"); return
    texto = urllib.parse.quote(mensaje)
    url = f"https://api.callmebot.com/whatsapp.php?phone={phone}&text={texto}&apikey={apikey}"
    try:
        urllib.request.urlopen(url, timeout=10)
        print("  [OK] WhatsApp ✓")
    except Exception as e:
        print(f"  [ERROR] WhatsApp: {e}")


# ══════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════

def main():
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{'═'*56}")
    print(f"  ✈️  Bot Vuelos Río de Janeiro — {ahora}")
    print(f"{'═'*56}")

    # Obtener cotización del dólar turista (para convertir ARS → USD)
    dolar = obtener_dolar_turista()

    alertas_enviadas = 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=CONFIG["headless"],
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="es-AR",
            timezone_id="America/Argentina/Buenos_Aires",
            viewport={"width": 1280, "height": 900},
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )
        page = context.new_page()

        for ruta in CONFIG["rutas"]:
            origen      = ruta["origin"]
            dest        = ruta["destination"]
            mes         = ruta["month"]
            limite_usd  = ruta["max_price_usd"]
            limite_ars  = int(limite_usd * dolar)

            print(f"\n► {origen} → {dest} | {mes} | límite USD {limite_usd} (≈ ARS {limite_ars:,})")

            todos = []
            for scraper_fn in SCRAPERS:
                pausa()
                resultados = scraper_fn(page, origen, dest, mes, tipo="ida_vuelta")
                todos.extend(resultados)
                if resultados:
                    mejor = min(resultados, key=lambda x: x["precio_ars"])
                    usd_est = round(mejor["precio_ars"] / dolar)
                    print(f"    💰 Mejor en {mejor['sitio']}: ARS {mejor['precio_ars']:,} ≈ USD {usd_est}")

            # Filtrar los que están bajo el límite en USD
            baratos = [r for r in todos if round(r["precio_ars"] / dolar) <= limite_usd]
            baratos = sorted(baratos, key=lambda x: x["precio_ars"])

            if baratos:
                print(f"\n  🚨 ¡{len(baratos)} resultado(s) bajo USD {limite_usd}! Enviando alertas...")
                mensaje = formatear_alerta(ruta, baratos[:5], dolar)
                mejor_usd = round(baratos[0]["precio_ars"] / dolar)
                asunto  = f"✈️ Vuelo a Río USD {mejor_usd} i/v — {origen}→{dest} Ene 2027"
                enviar_telegram(mensaje, CONFIG["telegram_bot_token"], CONFIG["telegram_chat_id"])
                enviar_email(mensaje, asunto, CONFIG)
                enviar_whatsapp(mensaje, CONFIG["whatsapp_phone"], CONFIG["whatsapp_apikey"])
                alertas_enviadas += 1
            else:
                if todos:
                    mejor_global = min(todos, key=lambda x: x["precio_ars"])
                    usd_g = round(mejor_global["precio_ars"] / dolar)
                    print(f"  ℹ️  Sin alertas. Mejor precio: ARS {mejor_global['precio_ars']:,} ≈ USD {usd_g} en {mejor_global['sitio']}")
                else:
                    print(f"  ℹ️  Sin resultados para esta ruta.")

        context.close()
        browser.close()

    print(f"\n{'═'*56}")
    print(f"  Finalizado. Alertas enviadas: {alertas_enviadas}")
    print(f"{'═'*56}\n")


if __name__ == "__main__":
    main()
