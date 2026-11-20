"""Tests du conseiller (pages réelles du 2026-11-01). Lancer : python3 tests/test_advisor.py"""
import importlib.util
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
spec = importlib.util.spec_from_file_location("advisor", ROOT.parent / "custom_components/carbu_com/advisor.py")
advisor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(advisor)
advisor.HOLIDAYS = {date(2026, 11, 1), date(2026, 11, 11), date(2026, 12, 25), date(2027, 1, 1)}
fixture = lambda name: (ROOT / "fixtures" / name).read_text()

SAT, SUN = date(2026, 10, 31), date(2026, 11, 1)
PRED = advisor.parse_prediction(fixture("prediction_diesel.html"))
ENERGIA = advisor.parse_energia(fixture("energia.html"))


def test_parse_prediction_dates_weekend_change_on_saturday():
    assert PRED["date"] == date(2026, 10, 30) and PRED["max"] == 1192.527
    # +1 d'un vendredi = samedi ; +2 = mardi (aucun changement un lundi)
    assert [s["date"] for s in PRED["forecast"]] == [
        date(2026, 10, 31), date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5), date(2026, 11, 6)]


def test_parse_current_and_energia():
    assert advisor.parse_current_maximum(fixture("prixmaximum.html")) == 2.452
    assert ENERGIA == {"price": 2.452, "valid_from": date(2026, 10, 31)}


def test_weekend_drop_already_applied_is_not_counted_twice():
    # La baisse de −3,4 c/L du samedi 31/10 est déjà en vigueur : rien d'autre n'est prévu.
    for today in (SAT, SUN):
        v = advisor.decide(today, 2.452, ENERGIA, PRED)
        assert v["verdict"] == "Prix stable prévu" and v["prix_prevu"] == 2.452, (today, v)


def test_friday_forecast_points_to_saturday():
    v = advisor.decide(date(2026, 10, 30), 2.486, None, PRED)
    assert v["verdict"] == "Attends demain : −3,4 c/L" and v["certitude"] == "prévision", v


def test_official_announcement_wins():
    v = advisor.decide(SUN, 2.452, {"price": 2.471, "valid_from": date(2026, 11, 3)}, PRED)
    assert v["certitude"] == "officiel" and v["verdict"] == "Fais le plein avant mardi 3/11 : +1,9 c/L", v
    v = advisor.decide(date(2026, 11, 2), 2.452, {"price": 2.433, "valid_from": date(2026, 11, 3)}, PRED)
    assert v["verdict"] == "Attends demain : −1,9 c/L", v


def _pred(*maxes, last=date(2026, 11, 2)):
    return {"date": last, "max": 1000.0,
            "forecast": [{"date": advisor.add_workdays(last, i) + timedelta(days=1), "max": m} for i, m in enumerate(maxes)]}


def test_near_change_and_far_changes():
    mon = date(2026, 11, 2)
    assert advisor.decide(mon, 2.0, None, _pred(1020.0))["verdict"] == "Fais le plein avant demain : +2,4 c/L"
    far_up = advisor.decide(mon, 2.0, None, _pred(1000.0, 1000.0, 1020.0))
    assert far_up["verdict"] == "Hausse possible jeudi 5/11, à confirmer", far_up
    far_down = _pred(1000.0, 1000.0, 980.0)
    # baisse lointaine : jamais « attends » ; affichée seulement si le gasoil baisse aussi
    assert advisor.decide(mon, 2.0, None, far_down, {"cinq_jours": 2.0})["verdict"] == "Pas de prévision fiable"
    assert advisor.decide(mon, 2.0, None, far_down, None)["verdict"] == "Pas de prévision fiable"
    v = advisor.decide(mon, 2.0, None, far_down, {"cinq_jours": -3.1})
    assert v["verdict"] == "Baisse possible jeudi 5/11, à confirmer" and v["raison"].startswith("carbu.com prévoit") and v["raison"].endswith(").") and "−3,1 %" not in v["raison"], v
    assert advisor.decide(mon, 2.0)["verdict"] == "Pas de prévision disponible"


def test_stale_prediction_uses_current_plateau():
    # Prévision du lundi 26/10 : baisse jeudi 29/10 déjà appliquée, consultée le vendredi 30/10.
    stale = _pred(1000.0, 1000.0, 970.0, 970.0, 970.0, last=date(2026, 10, 26))
    assert advisor.decide(date(2026, 10, 30), 1.9, None, stale)["verdict"] == "Prix stable prévu"


def test_holidays_and_next_change_day():
    assert advisor.next_change_day(SUN) == date(2026, 11, 3)            # dimanche férié -> annonce lundi
    assert advisor.next_change_day(date(2026, 11, 10)) == date(2026, 11, 11)
    assert advisor.next_change_day(date(2026, 11, 11)) == date(2026, 11, 13)  # 11/11 férié : pas d'annonce
    assert advisor.add_workdays(date(2026, 12, 24), 1) == date(2026, 12, 28)
    v = advisor.decide(date(2026, 12, 29), 2.0, None, _pred(1000.0, last=date(2026, 12, 29)))
    assert "trimestriel" not in v["raison"]
    v = advisor.decide(date(2026, 9, 30), 2.0, None, _pred(1000.0, last=date(2026, 9, 30)))
    assert "trimestriel" in v["raison"], v


def test_market_trend_and_bad_pages():
    t = advisor.market_trend(advisor.parse_yahoo(fixture("ho.json")), advisor.parse_yahoo(fixture("eurusd.json")))
    assert t["date"] == date(2026, 10, 30) and -5 < t["jour"] < 0, t
    for fn in (advisor.parse_prediction, advisor.parse_current_maximum, advisor.parse_energia):
        try:
            fn("<html></html>")
        except ValueError:
            continue
        raise AssertionError(f"{fn.__name__} aurait dû échouer")


BARO = advisor.parse_barometer(fixture("barometre_diesel.html"))
HO, FX = advisor.parse_yahoo(fixture("ho.json")), advisor.parse_yahoo(fixture("eurusd.json"))


def _outlook(day):
    return advisor.barometer_outlook([r for r in BARO if r["date"] <= day], HO, FX)


def test_parse_barometer():
    assert len(BARO) == 263 and BARO[-1]["date"] == date(2026, 11, 19)
    assert BARO[-1] == {"date": date(2026, 11, 19), "cif": 1017, "moyenne": 1054, "bas": 1057, "haut": 1075, "base": 1066}
    o = advisor.barometer_outlook(BARO)  # 19/11 : cotation et moyenne sous le seuil -> baisse décidée
    assert o["declenche"] == "baisse" and o["estimation"] is None and o["ecart_baisse"] == -4.8
    try:
        advisor.parse_barometer("<html></html>")
    except ValueError:
        return
    raise AssertionError("parse_barometer aurait dû échouer")


def test_barometer_estimates_next_announcement():
    # 24/08 : le gasoil perd 5 % ; la baisse s'est bien déclenchée le 25/08 (nouveau prix le 26/08)
    o = _outlook(date(2026, 8, 24))
    e = o["estimation"]
    assert o["declenche"] is None and e["annonce"] == date(2026, 8, 25) and e["sens"] == "baisse", e
    assert e["changement"] == date(2026, 8, 26) and -9 < e["variation"] < -6
    v = advisor.decide(date(2026, 8, 24), 2.0, barometer=o)
    assert v["verdict"] == "Baisse probable mercredi 26/8, à confirmer" and v["certitude"] == "probable (marché)", v
    assert "Attends" not in v["verdict"] and v["date"] == date(2026, 8, 26)
    # le jour de l'annonce, passé 13 h sans annonce officielle, l'estimation ne vaut plus rien
    assert advisor.decide(date(2026, 8, 25), 2.0, barometer=o, after_noon=True)["certitude"] == "inconnue"


def test_official_and_carbu_forecast_beat_barometer():
    o = _outlook(date(2026, 8, 24))
    near = {"date": date(2026, 8, 24), "max": 1000.0, "forecast": [{"date": date(2026, 8, 25), "max": 1020.0}]}
    assert advisor.decide(date(2026, 8, 24), 2.0, prediction=near, barometer=o)["certitude"] == "prévision"


def test_stable_says_how_far_thresholds_are():
    o = _outlook(date(2026, 10, 20))
    assert o["estimation"]["sens"] is None and o["ecart_baisse"] > 0 and o["ecart_hausse"] > 0
    flat = {"date": date(2026, 10, 20), "max": 1000.0, "forecast": []}
    v = advisor.decide(date(2026, 10, 20), 2.0, prediction=flat, barometer=o)
    assert v["verdict"] == "Prix stable prévu" and "pour déclencher une baisse" in v["raison"], v


def test_futures_curve():
    assert advisor.futures_symbols(date(2026, 11, 20))[0] == ("2026-12", "HOZ26.NYM")
    assert advisor.futures_symbols(date(2026, 11, 20))[-1] == ("2027-06", "HOM27.NYM")
    assert advisor.futures_symbols(date(2026, 12, 15))[0] == ("2027-01", "HOF27.NYM")
    c = advisor.futures_curve([("2026-12", 4.0), ("2027-01", None), ("2027-06", 3.48)])
    assert c["variation_pct"] == -13.0 and "une baisse" in c["lecture"] and "-13,0 %" in c["lecture"], c
    assert advisor.futures_curve([("2026-12", 4.0)]) is None


def test_next_visit():
    assert advisor.next_visit([date(2026, 10, 24)], 10) == date(2026, 11, 3)
    assert advisor.next_visit([], 10) is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
