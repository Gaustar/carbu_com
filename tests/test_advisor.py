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
    assert v["verdict"] == "Baisse possible jeudi 5/11, à confirmer" and "−3,1 %" not in v["raison"], v
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


def test_next_visit():
    assert advisor.next_visit([date(2026, 10, 24)], 10) == date(2026, 11, 3)
    assert advisor.next_visit([], 10) is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
