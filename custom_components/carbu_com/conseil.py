"""Entrée « Conseil plein diesel » : coordinateur, capteurs et réglages."""
import logging
from datetime import date, timedelta
from functools import partial

import requests
from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.components.sensor import SensorEntity
from homeassistant.core import callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from . import advisor
from .const import DOMAIN

_LOGGER = logging.getLogger(DOMAIN)
USER_AGENT = "HomeAssistant-carbu_com/13.0 (+https://github.com/Gaustar/carbu_com)"
# Le SPF Économie publie entre 10 h et 12 h les jours ouvrables ; 0 h 05 pour le prix du jour.
REFRESH_HOURS = (0, 11, 13, 16)


def _get(url):
    headers = {"User-Agent": USER_AGENT}
    if "carbu.com" in url:
        headers["Referer"] = "https://carbu.com/"
    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()
    return r.text


def _fetch_all(belgium):
    """Télécharge et lit chaque source (dans l'exécuteur) ; une source en panne n'empêche pas les autres."""
    jobs = {
        "ho": (advisor.YAHOO_URL.format("HO%3DF"), advisor.parse_yahoo),
        "fx": (advisor.YAHOO_URL.format("EURUSD%3DX"), advisor.parse_yahoo),
    }
    if belgium:
        jobs.update(prediction=(advisor.PREDICTION_URL, advisor.parse_prediction),
                    maximum=(advisor.MAXIMUM_URL, advisor.parse_current_maximum),
                    energia=(advisor.ENERGIA_URL, advisor.parse_energia))
    out = {}
    for key, (url, parse) in jobs.items():
        try:
            out[key] = parse(_get(url))
        except Exception as err:
            _LOGGER.warning("Conseil plein : source %s indisponible (%s)", key, err)
            out[key] = None
    return out


class ConseilCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        super().__init__(hass, _LOGGER, config_entry=entry, name="Conseil plein diesel", update_interval=None)
        self.entry = entry
        self.conf = entry.data
        self.settings = {"litres": 50.0, "jours": 10.0}
        self.store = Store(hass, 1, f"{DOMAIN}.conseil_{entry.entry_id}")
        self.visits = []

    async def async_load(self):
        saved = await self.store.async_load() or {}
        self.visits = [date.fromisoformat(d) for d in saved.get("visits", [])]
        await self._seed_visits()
        tracker = self.conf.get("tracker")
        if tracker and self.conf.get("zone"):
            self.entry.async_on_unload(async_track_state_change_event(self.hass, [tracker], self._on_move))
        self.entry.async_on_unload(async_track_time_change(self.hass, self._scheduled, hour=REFRESH_HOURS, minute=[5, 15], second=0))

    async def _scheduled(self, now):
        local = dt_util.as_local(now)
        if (local.hour == 0 and local.minute == 5) or (local.hour > 0 and local.minute == 15 and advisor.is_workday(local.date())):
            await self.async_refresh()

    def _zone_name(self):
        zone = self.hass.states.get(self.conf.get("zone") or "")
        return zone.name if zone else None

    def _add_visit(self, day):
        if day in self.visits:
            return False
        self.visits = sorted({*self.visits, day})[-50:]
        self.store.async_delay_save(lambda: {"visits": [d.isoformat() for d in self.visits]}, 5)
        return True

    async def _seed_visits(self):
        """Retrouve les passages dans la zone dans l'historique du traceur (1 an max)."""
        name, tracker = self._zone_name(), self.conf.get("tracker")
        if not name or not tracker:
            return
        try:
            from homeassistant.components.recorder import get_instance, history
            end = dt_util.utcnow()
            states = await get_instance(self.hass).async_add_executor_job(partial(
                history.state_changes_during_period, self.hass, end - timedelta(days=365), end, tracker, no_attributes=True))
        except Exception as err:  # recorder absent ou indisponible : on apprendra au fil de l'eau
            _LOGGER.warning("Conseil plein : historique du traceur illisible (%s)", err)
            return
        for s in states.get(tracker, []):
            if s.state == name:
                self._add_visit(dt_util.as_local(s.last_changed).date())

    @callback
    def _on_move(self, event):
        new = event.data.get("new_state")
        if new and new.state == self._zone_name() and self._add_visit(dt_util.as_local(new.last_changed).date()):
            self.async_update_listeners()

    async def _async_update_data(self):
        today = dt_util.now().date()
        belgium = self.conf.get("prevision_be", True)
        src = await self.hass.async_add_executor_job(_fetch_all, belgium)
        trend = advisor.market_trend(src["ho"], src["fx"]) if src["ho"] and src["fx"] else None
        if not belgium:
            verdict = advisor.info("Pas de prévision possible", "non prise en charge", None,
                                    "La prévision du « quand » n'existe que pour les prix maximum belges.")
            return {"today": today, "current": None, "energia": None, "prediction": None, "trend": trend, "verdict": verdict}
        energia, current = src["energia"], src["maximum"]
        if energia and energia["valid_from"] <= today:
            current = energia["price"]  # prix officiel en vigueur, même si carbu.com n'est pas encore à jour
        if current is None:
            verdict = advisor.info("Pas de prévision disponible", "inconnue", None, "Prix maximum du jour introuvable.")
        else:
            verdict = advisor.decide(today, current, energia, src["prediction"], trend)
        return {"today": today, "current": current, "energia": energia, "prediction": src["prediction"],
                "trend": trend, "verdict": verdict}

    def stations(self):
        rows, seen = [], set()
        for entity_id in self.conf.get("stations", []):
            s = self.hass.states.get(entity_id)
            try:
                price = float(s.state)
            except (AttributeError, TypeError, ValueError):
                continue
            # une même station sort dans plusieurs capteurs (commune, 5 km, 10 km)
            key = (s.attributes.get("supplier"), s.attributes.get("address")) if s.attributes.get("address") else entity_id
            if key in seen:
                continue
            seen.add(key)
            rows.append({"entity_id": entity_id, "station": s.attributes.get("supplier") or s.name,
                         "ville": s.attributes.get("city"), "adresse": s.attributes.get("address"), "prix": price})
        return sorted(rows, key=lambda r: r["prix"])

    def passage_station(self):
        s = self.hass.states.get(self.conf.get("station_passage") or "")
        try:
            return {"station": s.attributes.get("supplier") or s.name, "ville": s.attributes.get("city"), "prix": float(s.state)}
        except (AttributeError, TypeError, ValueError):
            return None


async def async_setup_conseil(hass, entry):
    coordinator = ConseilCoordinator(hass, entry)
    await coordinator.async_load()
    await coordinator.async_config_entry_first_refresh()
    return coordinator


class _Base(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, key, name, icon):
        super().__init__(coordinator)
        entry = coordinator.entry
        self._attr_unique_id = f"{entry.entry_id}_conseil_{key}"
        self._attr_name = name
        self._attr_icon = icon
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, f"conseil_{entry.entry_id}")},
                                            name="Conseil plein diesel", manufacturer="Carbu.com")


class ConseilSensor(_Base, SensorEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator, "diesel", "Conseil", "mdi:gas-station")

    @property
    def native_value(self):
        return self.coordinator.data["verdict"]["verdict"]

    @property
    def extra_state_attributes(self):
        d = self.coordinator.data
        v, p = d["verdict"], d["prediction"]
        attrs = {
            "raison": v["raison"], "certitude": v["certitude"], "prix_max_actuel": d["current"],
            "prix_max_prevu": v["prix_prevu"], "variation_c_l": v["variation"],
            "date_changement": v["date"].isoformat() if v["date"] else None,
            "prevision_du": p["date"].isoformat() if p else None,
            "previsions": [{"date": s["date"].isoformat(), "variation_c_l": s["variation"]}
                           for s in advisor.forecast_from_today(p, d["today"])] if p else [],
        }
        if d["energia"]:
            attrs["officiel_prix"] = d["energia"]["price"]
            attrs["officiel_a_partir_du"] = d["energia"]["valid_from"].isoformat()
        return attrs


class EndroitSensor(_Base, SensorEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator, "endroit", "Meilleur endroit", "mdi:map-marker-star")

    @property
    def native_value(self):
        best = (self.coordinator.stations() or [None])[0]
        if not best:
            return None
        where = f" ({best['ville']})" if best["ville"] else ""
        return f"{best['station']}{where} {best['prix']:.3f} €/L".replace(".", ",")[:255]

    @property
    def extra_state_attributes(self):
        c = self.coordinator
        rows, other, visits = c.stations(), c.passage_station(), c.visits
        attrs = {"classement": rows}
        if rows:
            attrs.update(prix=rows[0]["prix"], adresse=rows[0]["adresse"], entity_id=rows[0]["entity_id"])
        if other:
            attrs.update(passage_prix=other["prix"], passage_station=other["station"], passage_ville=other["ville"])
            if rows:
                ecart = rows[0]["prix"] - other["prix"]
                attrs["passage_ecart_c_l"] = round(100 * ecart, 1)
                attrs["passage_economie_plein"] = round(ecart * c.settings["litres"], 2)
        if visits:
            attrs["dernier_passage"] = visits[-1].isoformat()
            attrs["prochain_passage_estime"] = advisor.next_visit(visits, c.settings["jours"]).isoformat()
            attrs["passages"] = [v.isoformat() for v in visits[-10:]]
        return attrs


class TendanceSensor(_Base, SensorEntity):
    _attr_native_unit_of_measurement = "%"

    def __init__(self, coordinator):
        super().__init__(coordinator, "tendance", "Tendance gasoil", "mdi:chart-line")

    @property
    def native_value(self):
        t = self.coordinator.data["trend"]
        return t["jour"] if t else None

    @property
    def extra_state_attributes(self):
        t = self.coordinator.data["trend"]
        return {"cinq_jours": t["cinq_jours"], "seance_du": t["date"].isoformat(),
                "source": "ULSD NYMEX en €, indicatif"} if t else {}


class ReglageNumber(_Base, RestoreNumber):
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator, key, name, icon, unit, maxi):
        super().__init__(coordinator, key, name, icon)
        self._key = key
        self._attr_native_min_value, self._attr_native_max_value, self._attr_native_step = 1, maxi, 1
        self._attr_native_unit_of_measurement = unit

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last and last.native_value is not None:
            self.coordinator.settings[self._key] = last.native_value
            self.coordinator.async_update_listeners()

    @property
    def native_value(self):
        return self.coordinator.settings[self._key]

    async def async_set_native_value(self, value):
        self.coordinator.settings[self._key] = value
        self.coordinator.async_update_listeners()


async def async_setup_sensors(hass, entry, async_add_entities):
    c = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ConseilSensor(c), EndroitSensor(c), TendanceSensor(c)])


async def async_setup_numbers(hass, entry, async_add_entities):
    c = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        ReglageNumber(c, "litres", "Litres par plein", "mdi:fuel", "L", 120),
        ReglageNumber(c, "jours", "Jours entre deux passages", "mdi:calendar-sync", "j", 60),
    ])
