"""Conseil plein diesel (Belgique) : lecture des sources et décision, sans dépendance à Home Assistant."""
import json
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

PREDICTION_URL = "https://carbu.com/belgie//index.php/voorspellingen?p=M&C=D"
MAXIMUM_URL = "https://carbu.com/belgique/prixmaximum"
ENERGIA_URL = "https://www.energiafed.be/fr/prix-maximums"
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{}?range=1mo&interval=1d"

# €/1000 L hors TVA -> c/L TVA belge 21 % comprise (vérifié sur 69 changements officiels, écart max 0,1 c/L)
CIF_TO_CENTS = 0.121
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
BRUSSELS = ZoneInfo("Europe/Brussels")

try:
    import holidays
    # calculés à l'import (hors boucle d'événements) ; shortcut: au-delà de 5 ans, seuls les week-ends comptent
    _YEAR = date.today().year
    HOLIDAYS = holidays.country_holidays("BE", years=range(_YEAR - 1, _YEAR + 5), expand=False)
except ImportError:  # shortcut: sans la lib holidays, seuls les week-ends sont exclus
    HOLIDAYS = set()


def is_workday(day):
    """Jour où le SPF Économie peut annoncer un nouveau prix maximum."""
    return day.weekday() < 5 and day not in HOLIDAYS


def add_workdays(day, n):
    while n > 0:
        day += timedelta(days=1)
        n -= is_workday(day)
    return day


def next_change_day(today):
    """Premier jour où le prix peut changer : le lendemain d'un jour d'annonce."""
    announce = today if is_workday(today) else add_workdays(today, 1)
    return announce + timedelta(days=1)


def _echarts_options(html):
    options = []
    for script in BeautifulSoup(html, "html.parser").find_all("script"):
        text = script.string or script.get_text()
        match = re.search(r"\b(?:var|let|const)\s+option\s*=", text)
        if "echarts" not in text or not match:
            continue
        start = text.index("{", match.end())
        depth = 0
        for end in range(start, len(text)):
            depth += text[end] == "{"
            depth -= text[end] == "}"
            if depth == 0:
                break
        options.append(json.loads(text[start:end + 1], strict=False))
    return options


def parse_prediction(html):
    """Prix max (€/1000 L HTVA) du dernier jour d'annonce et prévisions datées au jour d'entrée en vigueur."""
    for option in reversed(_echarts_options(html)):  # le dernier graphique a les décimales
        axis = option["xAxis"]
        categories = (axis[0] if isinstance(axis, list) else axis)["data"]
        series = {str(s.get("name", "")).strip(): s.get("data", []) for s in option["series"]}
        actual, forecast = series.get("Maximum prijs"), series.get("Maximum prijs (Voorspellingen)")
        if not actual or not forecast:
            continue
        last = max(i for i, c in enumerate(categories) if "/" in str(c))
        last_date = datetime.strptime(categories[last], "%d/%m/%Y").date()
        steps = []
        for i, c in enumerate(categories[last + 1:], start=last + 1):
            n = int(str(c).strip().lstrip("+"))
            if forecast[i] is not None:
                # le « +n » s'applique le lendemain du n-ième jour d'annonce (le +1 d'un vendredi = samedi)
                steps.append({"date": add_workdays(last_date, n - 1) + timedelta(days=1), "max": float(forecast[i])})
        return {"date": last_date, "max": float(actual[last]), "forecast": steps}
    raise ValueError("graphique de prévision introuvable")


def parse_current_maximum(html, label="Diesel (B7)"):
    """Prix maximum du jour (€/L TVAC) sur carbu.com/belgique/prixmaximum."""
    for tr in BeautifulSoup(html, "html.parser").find_all("tr"):
        cells = [" ".join(td.get_text(" ").split()) for td in tr.find_all(["td", "th"])]
        if len(cells) >= 2 and cells[0] == label:
            return float(cells[1].split()[0].replace(",", "."))
    raise ValueError(f"{label} introuvable")


def parse_energia(html, title="Diesel"):
    """Dernier prix officiel publié et sa date d'entrée en vigueur."""
    for header in BeautifulSoup(html, "html.parser").select("header.samenstelling-chart__header"):
        h = header.select_one(".samenstelling-chart__title")
        if h is None or h.get_text(strip=True) != title:
            continue
        price = header.select_one(".samenstelling-chart__price").get_text(" ", strip=True)
        valid = header.select_one(".samenstelling-chart__date").get_text(" ", strip=True)
        return {
            "price": float(re.search(r"\d+[.,]\d+", price).group().replace(",", ".")),
            "valid_from": date.fromisoformat(re.search(r"\d{4}-\d{2}-\d{2}", valid).group()),
        }
    raise ValueError(f"{title} introuvable chez Energia")


def parse_yahoo(text):
    result = json.loads(text)["chart"]["result"][0]
    closes = result["indicators"]["quote"][0]["close"]
    return [(datetime.fromtimestamp(t, BRUSSELS).date(), c) for t, c in zip(result["timestamp"], closes) if c is not None]


def market_trend(ho, fx):
    """Variation (%) du gasoil en € à la dernière séance et sur 5 séances."""
    fx_by_day = dict(fx)
    euro = [(d, p / fx_by_day[d]) for d, p in ho if d in fx_by_day]
    if len(euro) < 6:
        return None
    pct = lambda a, b: round(100 * (b - a) / a, 2)
    return {"date": euro[-1][0], "jour": pct(euro[-2][1], euro[-1][1]), "cinq_jours": pct(euro[-6][1], euro[-1][1])}


def forecast_from_today(prediction, today):
    """Prévisions encore à venir, en c/L par rapport au palier déjà en vigueur."""
    base = prediction["max"]
    for step in prediction["forecast"]:
        if step["date"] <= today:
            base = step["max"]
    return [{"date": s["date"], "variation": round((s["max"] - base) * CIF_TO_CENTS, 1)}
            for s in prediction["forecast"] if s["date"] > today]


def _cents(v):
    return f"{abs(v):.1f}".replace(".", ",")


def day_name(day, today):
    if day == today + timedelta(days=1):
        return "demain"
    return f"{JOURS[day.weekday()]} {day.day}/{day.month}"


def decide(today, current, energia=None, prediction=None, trend=None):
    """Verdict « quand ». current = prix max du jour (€/L TVAC). Ne dit jamais « attends » sans base solide."""
    if energia and energia["valid_from"] > today and round(energia["price"] - current, 4):
        delta = 100 * (energia["price"] - current)
        when = day_name(energia["valid_from"], today)
        price = f"{energia['price']:.3f}".replace(".", ",")
        return _verdict(delta, when, energia["valid_from"], energia["price"], "officiel",
                        f"Prix maximum officiel annoncé : {price} €/L à partir de {when}.")
    if not prediction:
        return info("Pas de prévision disponible", "inconnue", current, "Prévision carbu.com indisponible.")
    soon = next_change_day(today)
    for step in forecast_from_today(prediction, today):
        delta, when = step["variation"], day_name(step["date"], today)
        if delta == 0:
            continue
        price = round(current + delta / 100, 3)
        if step["date"] <= soon:
            return _verdict(delta, when, step["date"], price, "prévision",
                            f"Prévision carbu.com : {'baisse' if delta < 0 else 'hausse'} de {_cents(delta)} c/L {when} "
                            f"(juste 4 fois sur 5 sur un an de données).")
        if delta > 0:
            return {**info(f"Hausse possible {when}, à confirmer", "prévision lointaine", price,
                            f"carbu.com prévoit +{_cents(delta)} c/L {when}, rien n'est attendu avant."),
                    "variation": delta, "date": step["date"]}
        if trend and trend["cinq_jours"] < 0:
            return {**info(f"Baisse possible {when}, à confirmer", "prévision lointaine", price,
                            f"carbu.com prévoit −{_cents(delta)} c/L {when} et le gasoil baisse "
                            f"({trend['cinq_jours']:+.1f} % sur 5 séances).".replace(".", ",")),
                    "variation": delta, "date": step["date"]}
        return info("Pas de prévision fiable", "incertaine", current,
                     f"carbu.com prévoit une baisse {when}, mais le gasoil ne baisse pas : "
                     "dans ce cas, une baisse annoncée aussi loin finit en hausse une fois sur trois.")
    note = " Un petit ajustement trimestriel (moins de 1 c/L) reste possible." if soon.day == 1 and soon.month in (1, 4, 7, 10) else ""
    return info("Prix stable prévu", "prévision", current, "Aucun changement annoncé ni prévu dans les 5 jours ouvrables." + note)


def info(text, certitude, price, raison):
    return {"verdict": text, "certitude": certitude, "variation": 0.0, "date": None, "prix_prevu": price, "raison": raison}


def _verdict(delta, when, day, price, certitude, raison):
    text = f"Attends {when} : −{_cents(delta)} c/L" if delta < 0 else f"Fais le plein avant {when} : +{_cents(delta)} c/L"
    return {"verdict": text, "certitude": certitude, "variation": round(delta, 1), "date": day,
            "prix_prevu": price, "raison": raison}


def next_visit(visits, interval_days):
    if not visits:
        return None
    return visits[-1] + timedelta(days=int(interval_days))
