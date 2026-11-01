# Carbu.com pour Home Assistant

Prix des carburants et du mazout (Belgique, France, Luxembourg, etc.) lus sur [carbu.com](https://carbu.com), avec la prédiction de prix.

Reprise de l'intégration `myTselection/Carbu_com` (v12.0.2), dont le dépôt d'origine a été supprimé avec le compte GitHub de son auteur. Le code est celui de la v12.0.2 ; seuls le manifeste et les liens changent. Tout le mérite revient à l'auteur d'origine.

## Installation (HACS)

HACS → ⋮ → Dépôts personnalisés → `https://github.com/Gaustar/carbu_com`, type **Intégration** → installer « Carbu.com » → redémarrer Home Assistant.

Les entrées et capteurs déjà créés avec la version d'origine sont conservés (même domaine `carbu_com`).

## Conseil plein diesel (v13)

Ajouter l'intégration Carbu.com → **Conseil plein diesel**. Elle crée :

- **Conseil** : « Attends demain : −3,4 c/L », « Fais le plein avant mardi 3/11 : +1,9 c/L », « Hausse possible… », « Pas de prévision fiable » ou « Prix stable prévu », avec la certitude (officiel, prévision, prévision lointaine, incertaine), la raison et les prévisions jour par jour.
- **Meilleur endroit** : la station la moins chère parmi les capteurs choisis. En option, l'écart avec une station où vous passez régulièrement (autre pays, trajet domicile-travail…), l'économie sur un plein et le prochain passage estimé (zone + traceur GPS).
- **Tendance gasoil** : variation du gasoil (ULSD NYMEX converti en €) sur la dernière séance et sur 5 séances, à titre indicatif.
- Réglages : litres par plein, jours entre deux passages.

**Le « quand » ne fonctionne que pour la Belgique** (prix maximum officiels). Ailleurs, le conseil affiche « Pas de prévision possible » et garde le meilleur endroit et la tendance.

Comment il décide : l'annonce officielle du lendemain (Energia) prime ; sinon la prévision de carbu.com au prochain jour où le prix peut changer (juste 4 fois sur 5 sur un an de données) ; au-delà, jamais « attends », et une baisse lointaine n'est signalée que si le gasoil baisse aussi. Les petits ajustements trimestriels (1er janvier, avril, juillet, octobre, moins de 1 c/L) ne sont pas prévisibles. Rafraîchissement : 0 h 05, puis 11 h 15, 13 h 15 et 16 h 15 les jours ouvrables belges.

Détails et chiffres de vérification : `docs/superpowers/specs/2026-11-01-conseil-plein-design.md`. Tests : `python3 tests/test_advisor.py`.
