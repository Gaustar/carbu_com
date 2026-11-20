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
BAROMETER_URL = "https://carbu.com/belgique//index.php/cif_detail?p=Y&C=D"
MONTH_CODES = "FGHJKMNQUVXZ"  # codes mois des contrats à terme

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


def _js_array(text, start):
    """Tableau JavaScript à partir du premier « [ » après start."""
    i = text.index("[", start)
    depth = 0
    for end in range(i, len(text)):
        depth += text[end] == "["
        depth -= text[end] == "]"
        if depth == 0:
            return json.loads(re.sub(r",\s*\]", "]", text[i:end + 1]))
    raise ValueError("tableau non fermé")


def parse_barometer(html):
    """Baromètre carbu.com (€/1000 L HTVA) : cotation CIF du jour, moyenne 7 jours, seuils et prix de base."""
    start = html.find("categories:")
    if start < 0:
        raise ValueError("baromètre introuvable")
    end = html.find("categories:", start + 11)  # le second graphique répète les mêmes séries
    block = html[start:end if end > 0 else None]
    days = [datetime.strptime(d, "%d/%m/%Y").date()
            for d in re.findall(r"'(\d\d/\d\d/\d{4})'", block[:block.index("]")])]

    def series(name):
        m = re.search(r"name:\s*['\"]" + name + r"['\"]", block)
        if not m:
            raise ValueError(f"série {name} introuvable")
        return _js_array(block, block.index("data:", m.end()))

    lim, cif, ma, base = series("Limites"), series("CIF"), series("Moyenne mobile"), series("Prix maximum")
    rows = [{"date": days[k], "cif": cif[k], "moyenne": ma[k], "bas": lim[k][0], "haut": lim[k][1], "base": base[k]}
            for k in range(min(len(days), len(cif), len(ma), len(base), len(lim)))
            if lim[k] and None not in (cif[k], ma[k], base[k])]
    if len(rows) < 7:
        raise ValueError("baromètre trop court")
    return rows


def _triggered(cif, moyenne, bas, haut):
    """Règle du contrat-programme : la cotation du jour ET sa moyenne 7 jours sortent du même seuil."""
    if cif < bas and moyenne < bas:
        return "baisse"
    if cif > haut and moyenne > haut:
        return "hausse"
    return None


def _euro(ho, fx):
    fx_by_day = dict(fx)
    return {d: p / fx_by_day[d] for d, p in ho if d in fx_by_day}


def barometer_outlook(rows, ho=None, fx=None):
    """Position de la cotation entre les seuils, et ce que la dernière séance du gasoil annonce pour la prochaine annonce."""
    last = rows[-1]
    out = {"date": last["date"], "cif": last["cif"], "moyenne": last["moyenne"], "bas": last["bas"], "haut": last["haut"],
           "base": last["base"], "position": round(100 * (last["cif"] - last["bas"]) / (last["haut"] - last["bas"])),
           "ecart_baisse": round((last["cif"] - last["bas"]) * CIF_TO_CENTS, 1),
           "ecart_hausse": round((last["haut"] - last["cif"]) * CIF_TO_CENTS, 1),
           "declenche": _triggered(last["cif"], last["moyenne"], last["bas"], last["haut"]), "estimation": None}
    if out["declenche"] or not ho or not fx:
        return out  # changement déjà décidé : le prix officiel et la prévision carbu.com le portent
    euro = _euro(ho, fx)
    announce = add_workdays(last["date"], 1)
    now = [d for d in sorted(euro) if d < announce]
    ref = [d for d in sorted(euro) if d < last["date"]]
    if not now or not ref or now[-1] == ref[-1]:
        return out
    cif = last["cif"] * euro[now[-1]] / euro[ref[-1]]  # la cotation du jour suit la séance de la veille
    moyenne = (sum(r["cif"] for r in rows[-6:]) + cif) / 7
    # shortcut: seuils du dernier jour connu ; ils ne font que se resserrer, l'estimation reste prudente
    sens = _triggered(cif, moyenne, last["bas"], last["haut"])
    out["estimation"] = {"annonce": announce, "changement": announce + timedelta(days=1), "seance_du": now[-1],
                         "marche_pct": round(100 * (euro[now[-1]] / euro[ref[-1]] - 1), 2),
                         "cif": round(cif, 1), "moyenne": round(moyenne, 1), "sens": sens,
                         "variation": round((cif - last["base"]) * CIF_TO_CENTS, 1) if sens else 0.0}
    return out


def futures_symbols(today, count=7):
    """Contrats ULSD NYMEX des prochains mois (le contrat du mois M expire fin M-1)."""
    out = []
    for k in range(1, count + 1):
        m = today.month - 1 + k
        year, month = today.year + m // 12, m % 12 + 1
        out.append((f"{year}-{month:02d}", f"HO{MONTH_CODES[month - 1]}{year % 100:02d}.NYM"))
    return out


def futures_curve(points):
    """points = [(mois, prix $/gal)] : ce que le marché paie aujourd'hui pour livrer chaque mois."""
    points = [(m, p) for m, p in points if p]
    if len(points) < 2:
        return None
    first, last = points[0], points[-1]
    var = round(100 * (last[1] - first[1]) / first[1], 1)
    sens = "des prix à peu près stables" if abs(var) < 2 else ("une baisse" if var < 0 else "une hausse")
    return {"variation_pct": var, "mois": [{"mois": m, "prix_usd_gal": round(p, 4)} for m, p in points],
            "lecture": f"Contrats à terme {var:+.1f} % entre {first[0]} et {last[0]} : le marché s'attend à {sens}."
                       .replace(".", ",", 1)}


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


def _estimate(barometer, today, after_noon):
    """Estimation du baromètre encore valable : son jour d'annonce n'est pas passé sans annonce."""
    est = (barometer or {}).get("estimation")
    if not est or not est["sens"] or est["annonce"] < today or (est["annonce"] == today and after_noon):
        return None
    return est


def decide(today, current, energia=None, prediction=None, trend=None, barometer=None, after_noon=False):
    """Verdict « quand ». current = prix max du jour (€/L TVAC). Ne dit jamais « attends » sans base solide."""
    if energia and energia["valid_from"] > today and round(energia["price"] - current, 4):
        delta = 100 * (energia["price"] - current)
        when = day_name(energia["valid_from"], today)
        price = f"{energia['price']:.3f}".replace(".", ",")
        return _verdict(delta, when, energia["valid_from"], energia["price"], "officiel",
                        f"Prix maximum officiel annoncé : {price} €/L à partir de {when}.")
    est = _estimate(barometer, today, after_noon)
    if not prediction and not est:
        return info("Pas de prévision disponible", "inconnue", current, "Prévision carbu.com indisponible.")
    soon = next_change_day(today)
    steps = [s for s in forecast_from_today(prediction, today) if s["variation"]] if prediction else []
    step = steps[0] if steps else None
    if step and step["date"] <= soon:
        delta, when = step["variation"], day_name(step["date"], today)
        return _verdict(delta, when, step["date"], round(current + delta / 100, 3), "prévision",
                        f"Prévision carbu.com : {'baisse' if delta < 0 else 'hausse'} de {_cents(delta)} c/L {when} "
                        f"(juste 4 fois sur 5 sur un an de données).")
    if est:
        delta, when = est["variation"], day_name(est["changement"], today)
        return {**info(f"{est['sens'].capitalize()} probable {when}, à confirmer", "probable (marché)",
                       round(current + delta / 100, 3),
                       f"Cotation estimée d'après la séance du gasoil du {est['seance_du'].day}/{est['seance_du'].month} "
                       f"({est['marche_pct']:+.1f} %) : avec la moyenne sur 7 jours, le seuil de {est['sens']} serait "
                       f"franchi et le prix changerait {when} (environ {'−' if delta < 0 else '+'}{_cents(delta)} c/L). "
                       f"Sur un an : juste 7 fois sur 10, jamais dans le mauvais sens.".replace(".", ",", 1)),
                "variation": delta, "date": est["changement"]}
    if step:
        delta, when = step["variation"], day_name(step["date"], today)
        price = round(current + delta / 100, 3)
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
    if barometer and barometer["ecart_baisse"] > 0 and barometer["ecart_hausse"] > 0:
        note += (f" La cotation doit encore baisser de {_cents(barometer['ecart_baisse'])} c/L pour déclencher une baisse,"
                 f" ou monter de {_cents(barometer['ecart_hausse'])} c/L pour une hausse.")
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
