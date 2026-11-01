# Conseil plein diesel — conception (2026-11-01)

## Pourquoi
Beaucoup d'utilisateurs regardent leurs prix Carbu.com chaque jour pour décider **quand** et **où** faire le plein.
L'intégration donnait des prix et une tendance en %, la décision restait à faire. Le conseil donne une réponse :
« fais le plein aujourd'hui » ou « attends demain, ça baisse de X c », et où.

## Objectifs
- Un verdict clair, avec son niveau de certitude affiché.
- Le meilleur endroit parmi les stations choisies ; en option, l'écart avec une station de passage régulier
  (autre pays, trajet domicile-travail…) et le prochain passage estimé.
- Les entrées et capteurs existants ne changent pas (même domaine `carbu_com`).

## Anti-objectifs
- Dire « attends » quand le prix monte. Mieux vaut « pas de prévision fiable » que deviner.
- Seuils inventés : les réglages (litres par plein, jours entre deux passages) sont des entités `number`.
- Prévoir des pays à prix libres (France, Pays-Bas…) : aucun mécanisme, ce serait de la spéculation.

## Sources
| Donnée | Source | Certitude |
|---|---|---|
| Prix max diesel du jour | carbu.com/belgique/prixmaximum ; Energia s'il est déjà en vigueur | officiel |
| Prix max annoncé pour demain | energiafed.be/fr/prix-maximums (« à partir du » date future) | officiel |
| Prévision (+1…+5) | carbu.com voorspellingen?p=M&C=D (graphique : CIF, moyenne 7 j, bande de seuil, prix max en €/1000 L) | prévision |
| Tendance marché | Yahoo HO=F (ULSD NYMEX) + EURUSD=X | indicatif |
| Prix stations | capteurs carbu_com existants | — |
| Passages | historique de l'état d'un traceur (dans la zone choisie) | mesuré |

## Résultats de la vérification (historique officiel Energia 2025-2026, 69 changements)
- Δ prix pompe (c/L TVAC) = Δ prix max (€/1000 L HTVA) × 0,121 : écart moyen 0,004 c/L, max 0,095.
- Le « +n » de carbu.com entre en vigueur le lendemain du n-ième jour d'annonce : le « +1 » d'un vendredi
  s'applique le samedi. Aucun changement un lundi. Le SPF publie entre 10 h et 12 h les jours ouvrables (hors fériés).
- Fiabilité du sens (règle reconstruite, cours figé) : +1 81 % (contre 75 % pour « rien ne change »), +2 77 %,
  +3 69 %, +5 65 %. Baisse annoncée finissant en hausse : 0 à +1, 17 sur 252 à +5.
- Filtre gasoil : une baisse prévue à +3/+5 se réalise 79 % du temps si le gasoil a baissé sur 5 séances
  (7 % finissent en hausse), contre 46 % sinon (35 % en hausse).
- Petits ajustements trimestriels (1/1, 1/4, 1/7, 1/10 ; +0,2 à +0,9 c/L) imprévisibles.

## Règles de décision
1. Prix officiel annoncé (Energia, date future) ≠ prix du jour → « Attends {jour} » / « Fais le plein avant {jour} » (officiel).
2. Prévision carbu.com : base = dernier palier déjà en vigueur. Changement au prochain jour possible
   (lendemain du prochain jour d'annonce) → même verdict, certitude « prévision ».
3. Plus loin : jamais « attends ». Hausse → « Hausse possible {jour}, à confirmer ». Baisse → « Baisse possible »
   seulement si le gasoil baisse sur 5 séances, sinon « Pas de prévision fiable ».
4. Sinon « Prix stable prévu » (avec la note trimestrielle le cas échéant). Hors Belgique : « Pas de prévision possible ».

## Rafraîchissement
0 h 05 tous les jours ; 11 h 15, 13 h 15 et 16 h 15 les jours ouvrables belges. User-Agent identifiant l'intégration.
