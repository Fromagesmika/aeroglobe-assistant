# Jeu de données — AeroGlobe Airways
### Projet de fin de cours — Advanced BI avec Power BI Desktop

Ce jeu de données synthétique couvre la période **du 01/01/2022 au 25/09/2026** (près de 5 ans),
afin de permettre des analyses de tendance, de saisonnalité et de comparaison annuelle (YoY, YTD…).

## 1. Contenu du dossier

| Fichier | Rôle | Nb de lignes environ |
|---|---|---|
| `Dim_Date.csv` | Dimension calendrier (1 ligne / jour) | 1 729 |
| `Dim_Client.csv` | Clients de la compagnie | 6 000 |
| `Dim_Aeroport.csv` | Aéroports du réseau (+ Latitude/Longitude pour cartographie) | 20 |
| `Dim_Avion.csv` | Flotte d'appareils | 15 |
| `Dim_Vol.csv` | Numéros de vol / routes | 50 |
| `Dim_Classe.csv` | Classes de voyage | 3 |
| `Dim_Canal.csv` | Canaux de vente | 4 |
| `Fait_Ventes_Billets.csv` | 1 ligne = 1 billet vendu | **450 000** |
| `Fait_Operations_Vol.csv` | 1 ligne = 1 vol réellement opéré (numéro de vol × date) | **46 930** |
| `Fait_Satisfaction_Client.csv` | 1 ligne = 1 réponse d'enquête ou réclamation | **33 057** |

**Total : plus de 537 000 lignes**, volontairement plus volumineux que l'indication de départ du
sujet, pour que les étudiants disposent d'une vraie profondeur historique (5 années) et puissent
travailler des mesures de Time Intelligence sur des données qui varient réellement d'une année sur
l'autre (croissance, saisonnalité, ponctualité, satisfaction).

## 2. Ce qu'il faut savoir avant de modéliser

- **Format des dates** : `AAAA-MM-JJ` (ISO). Les champs `DateVenteID`, `DateVolID`, `DateEnqueteID`
  doivent être mis en relation avec `Dim_Date.DateID`.
- **Dim_Date est une dimension à rôles multiples** : elle doit être reliée trois fois à
  `Fait_Ventes_Billets` (date de vente **et** date de vol) et une fois à `Fait_Operations_Vol`, une
  fois à `Fait_Satisfaction_Client`. Une seule relation peut être active par table de faits dans
  Power BI : les autres doivent être mobilisées en DAX avec `USERELATIONSHIP` — c'est un exigence du
  sujet (voir section 4.4 du document de cadrage).
- **Dim_Aeroport est également à rôles multiples** dans `Dim_Vol` (`AeroportDepartID` et
  `AeroportArriveeID`) : pensez à dupliquer la dimension (ou à utiliser des relations inactives) pour
  pouvoir afficher correctement les aéroports de départ ET d'arrivée sur un même visuel.
- **`Fait_Operations_Vol.VolID` + `DateVolID`** correspond à un vol réellement opéré, avec son statut
  (`À l'heure` / `Retardé` / `Annulé` / `Dérouté`), son remplissage et ses horaires réels.
- **`Fait_Ventes_Billets`** référence toujours un `VolID` + `DateVolID` qui existe bien dans
  `Fait_Operations_Vol` (intégrité référentielle garantie) — cela permet de croiser les deux tables.
  ⚠️ **Point pédagogique volontaire** : le nombre de billets vendus sur un vol donné n'est **pas**
  censé se réconcilier exactement avec `NombrePassagers` dans `Fait_Operations_Vol`. C'est une
  situation très fréquente en entreprise (système de réservation commercial vs. système
  opérationnel de suivi des vols, alimentés séparément). Les étudiants ne doivent pas chercher à les
  faire correspondre ligne à ligne, mais à les analyser chacune à son grain, et à les croiser via
  `VolID` / `ClientID` pour les analyses transverses (ex. satisfaction vs retard).
- **`Fait_Satisfaction_Client.VolID`** est vide pour les réclamations générales (hors vol précis,
  ex. réclamations sur le programme de fidélité). Pensez à gérer ces valeurs manquantes en Power
  Query.
- **Corrélation volontaire** : la note de ponctualité et la note globale de satisfaction sont
  corrélées avec le retard réel du vol concerné. C'est ce qui doit permettre aux étudiants de
  démontrer, chiffres à l'appui, l'un des insights croisés attendus dans le sujet (impact du retard
  sur la satisfaction).
- **Tendance & saisonnalité intégrées** : croissance du trafic et du chiffre d'affaires d'année en
  année (2022 → 2025), forte saisonnalité estivale (juillet-août) et un pic fin d'année. **2026 est
  une année incomplète** (arrêtée au 25/09/2026) : les totaux annuels 2026 apparaîtront donc plus
  bas en cumul — c'est normal et constitue même un bon point de vigilance à relever par les étudiants
  (ne pas comparer un total 2026 incomplet à un total 2025 complet sans le signaler).

## 3. Import dans Power BI Desktop

1. `Obtenir les données` → `Texte/CSV`, importer les 10 fichiers.
2. Vérifier le typage de chaque colonne dans Power Query (dates, décimaux, texte) — certaines
   colonnes comme `HeureDepartPrevue` sont au format texte `HH:MM`, à retyper en heure si besoin.
3. Construire les colonnes calculées demandées par le sujet directement en Power Query si vous le
   souhaitez (ex. `Age` du client à partir de `DateNaissance`, `RetardDepartMinutes` à partir des
   heures prévues/réelles) : elles ne sont **pas** fournies pré-calculées, c'est un exercice du
   sujet.
4. Marquer `Dim_Date` comme table de dates (`Modélisation` → `Marquer comme table de dates`).
5. Créer les relations du schéma en étoile (voir section 3 du document de cadrage du projet).

## 4. Reproductibilité

Ces données sont générées de façon procédurale (graine aléatoire fixe) à partir d'un script Python.
Elles sont fictives : toute ressemblance avec une compagnie aérienne réelle serait fortuite.
