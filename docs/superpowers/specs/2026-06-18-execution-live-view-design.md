# Design — Vue d'exécution live, étape par étape

> Zone 1 du refactoring UX du frontend FoxRunner. Les zones suivantes
> (slots imbriqués, dashboard « vivant », navigation) auront chacune leur
> propre spec. Repos concernés : `FoxRunner_server` (backend + moteur),
> `FoxRunner_frontend` (Angular 21), `FoxRunner_frontend_node20` (Angular 19).

## Problème

La vue d'exécution actuelle (`job-detail`) est une timeline d'événements
quasi vide : le moteur (`scenarios/runner.py`) ne rapporte que le résultat
final, et le job Celery (`ops/tasks.run_scenario_job`) n'émet que 3
événements grossiers (`running`, `success|failed`, `error`). On ne voit
ni le détail des étapes, ni — en cas d'échec — *pourquoi* ça plante.

## Objectif

Depuis l'UI, **lancer un scénario (dry-run ou réel) et regarder ses étapes
se dérouler en direct**, comprendre chaque étape en français clair, et à
l'échec voir l'étape fautive + l'explication + la capture d'écran.

Pas de WebSocket : les événements transitent par la DB (`JobEvent`) et le
frontend **poll** un instantané (~1,5 s pendant qu'un job tourne). Le
frontend connaît les étapes planifiées (définition du scénario) et, à
chaque poll, reçoit l'état de toutes les étapes déjà jouées → il avance la
checklist (pending → running → ok/échec/sauté).

## Périmètre (v1)

Inclus : lancement depuis l'UI, exécution **inline-en-thread si aucun
worker Celery n'est détecté, sinon via Celery** (auto), événements
par-étape de premier niveau, traceback à l'échec, vue checklist + en-tête
de progression + carte d'échec avec capture inline + boutons relancer,
libellés français lisibles, sur les **deux** frontends.

Hors périmètre v1 : granularité fine des blocs imbriqués (`group`,
`parallel`, `repeat`, `try`) — affichés comme **une** ligne ; WebSocket/SSE ;
exécution inline en production (le chemin prod reste le scheduleur/Celery).

## Architecture

### 1. Identité des étapes (`step_id`)

Le moteur attribue à chaque étape un identifiant **déterministe et stable**
basé sur sa collection + son index, identique entre la liste statique
(définition, côté frontend) et les événements live :

- `before_steps[0]`, `steps[0]`, `steps[1]`, `on_success[0]`,
  `on_failure[0]`, `finally_steps[0]`…
- Un bloc imbriqué garde l'id de sa ligne de premier niveau (ex.
  `steps[2]`) ; son intérieur n'est pas tracé en v1.

Le frontend construit la checklist depuis la définition du scénario (déjà
disponible via `GET /users/{id}/scenarios/{id}` → `definition`, et/ou
`step-collections`) en générant les mêmes `step_id`, puis superpose le
statut live en mappant `JobEvent.step → step_id`.

### 2. Moteur — sink d'événements (`scenarios/runner.py`, `scenarios/engine.py`)

`run_task(...)` reçoit un paramètre optionnel
`on_event: Callable[[StepEvent], None] | None` (framework-agnostic, aucune
dépendance Django). Le moteur l'appelle :

- `step_started`  — au début de chaque étape (porte `step_id`, `type`,
  libellé, index).
- `step_succeeded` — succès (porte la durée en ms).
- `step_failed`   — échec : porte `message` (str(exc)) **et `traceback`**
  (via `traceback.format_exc()`), + `step_id`, `type`.
- `step_skipped`  — `when` faux ou `continue_on_error` après erreur.
- `step_retrying` — avant une nouvelle tentative (porte `attempt`).

`StepEvent` est un `dataclass` simple (`step_id`, `event_type`, `step_type`,
`label`, `level`, `message`, `traceback`, `payload`, `duration_ms`). Le
sink par défaut est `None` (le scheduleur CLI et les tests existants ne
changent pas de comportement). La capture screenshot/page_source existante
est inchangée ; leurs chemins relatifs sont joints au payload de
`step_failed`.

### 3. Dispatch du job (auto inline/Celery) — `ops/services.py`, `ops/tasks.py`

`enqueue_scenario_job` détecte la présence d'un worker Celery :

- **Worker détecté** (`celery_app.control.inspect().ping()` non vide, en
  best-effort avec court timeout) → comportement actuel : `run_scenario_job.delay(...)`.
- **Aucun worker** → exécution **inline dans un thread démon** du process
  Django : le job passe `running`, le sink écrit un `JobEvent` par étape au
  fil de l'eau, puis `success|failed` + `Job.error`=traceback à l'échec.
  Le POST répond **202 immédiatement** (le thread tourne derrière), donc
  l'UI peut poller la timeline en live. Gardé derrière une détection
  automatique ; un flag `RUN_JOBS_INLINE` (true/false/auto, défaut `auto`)
  permet de forcer un mode.

`run_scenario_job` (Celery) et le runner inline partagent **le même** code
de pilotage : une fonction `execute_scenario_job(job_id, scenario_id,
dry_run)` qui construit le service, branche le sink → `append_job_event`,
et met à jour la ligne `Job`. Le traceback de l'étape fautive est propagé
dans `Job.error` et dans le payload de l'event `step_failed`.

### 4. API

- Réutilise l'existant : `POST /users/{id}/scenarios/{id}/jobs` (lancer),
  `GET /jobs/{id}` (état), `GET /jobs/{id}/events` (instantané des
  événements — c'est le canal « attends / voici le résultat » de chaque
  étape).
- `JobEventOut` expose déjà `event_type`, `level`, `message`, `step`,
  `payload`, `created_at`. On ajoute, via le `payload`, `traceback`,
  `duration_ms`, et les références d'artefacts (screenshot/page_source).
- **Artefacts à l'échec** : une route accessible au propriétaire du job
  pour servir la capture/HTML (ex. `GET /jobs/{id}/artifacts/{kind}` →
  fichier), scopée à `request.auth`. (Évite de réutiliser la route admin
  artifacts.)

### 5. Frontend — vue exécution (les deux repos)

Refonte de `features/jobs/detail/job-detail.component.ts` en vue
« Exécution » :

- **En-tête de statut** : nom du scénario, badge `dry-run`/réel, statut
  coloré (queued/running/success/failed/cancelled), compteur `n/N étapes`
  + barre de progression, chrono (temps écoulé, fige à la fin).
- **Checklist** groupée par collection (`before_steps`, `steps`,
  `on_success`, `on_failure`, `finally_steps`) ; `before_steps`/
  `finally_steps`/`on_*` repliées par défaut, focus sur `steps`. États par
  étape : ○ pending · ◐ running · ✓ ok · ✗ failed · ⊘ skipped · ↻ retry,
  + durée. **Libellé français lisible** par étape (voir §6).
- **Carte d'échec** (si échec) : étape fautive en libellé clair +
  explication courte + **vignette de la capture d'écran** (cliquable →
  plein écran) + sections repliables « traceback technique » et « HTML de
  la page ».
- **Boutons** : « Relancer » et « Relancer en dry-run » (réutilisent
  `JobsService.trigger`/`retry`). Lancement initial : boutons « Lancer
  (dry-run) / (réel) » sur la page **détail du scénario** → crée le job →
  navigue vers la vue exécution.
- **Polling** : conserve l'auto-refresh existant mais à ~1,5 s tant que
  `queued|running`, stop en état terminal. Le journal d'événements brut
  reste accessible en section repliable « Journal détaillé ».

Le `schema.ts` est régénéré (nouveaux champs payload / route artefacts).
Les types passent par `types.ts`.

### 6. Libellés français lisibles

Une table de correspondance `step_type → modèle FR` côté frontend (pas de
DSL brut) :

| type | libellé |
|---|---|
| `open_url` | Ouvrir la page « {url} » |
| `click` | Cliquer sur « {locator} » |
| `input_text` | Saisir du texte dans « {locator} » |
| `wait_for_element` | Attendre l'élément « {locator} » |
| `assert_text` | Vérifier le texte de « {locator} » |
| `notify` | Envoyer une notification |
| `sleep` | Attendre {seconds} s |
| … | (un libellé par type supporté ; repli = le type brut) |

Helper pur `stepLabel(step)` + table, testable en isolation (vitest).

## Gestion des erreurs

- Sink qui lève → ne doit jamais casser le run (best-effort, log).
- Détection worker Celery indisponible/lente → repli inline (timeout court
  sur le `ping`).
- Thread inline : exceptions capturées → job `failed` + event `step_failed`
  avec traceback ; jamais de thread qui meurt en silence.
- Frontend : artefact manquant → on masque la vignette ; job introuvable →
  état vide existant.

## Tests

Backend :
- Moteur : le sink émet les bons événements dans l'ordre (succès, échec
  avec traceback non vide, skip via `when`, retry) — suite CLI `tests/`.
- `step_id` déterministe et stable.
- Dispatch : mode inline écrit bien les `JobEvent` par étape + `Job.error`
  à l'échec ; détection worker → choisit le bon chemin (mocké) — suite
  Django.
- Route artefacts scopée au propriétaire (404 pour un autre user).

Frontend (vitest) :
- `stepLabel()` : libellés FR par type + repli.
- Mapping `events → checklist` : statut par `step_id`, progression `n/N`.

Gates CI inchangées : ruff/format, check_env_example (flag `RUN_JOBS_INLINE`
ajouté à `.env.example`), check_openapi (régénérer), check_docs, couverture
CLI ≥75 / Django ≥84 ; front lint + vitest + build sur les deux repos.

## Décisions

- **Pas de WebSocket** : DB (`JobEvent`) + polling court. Justifié par le
  stack WSGI+sync et le fait que le run tourne dans un autre process.
- **Auto inline/Celery** : marche sur Windows local sans Celery, tout en
  gardant Celery quand un worker tourne. Prod = scheduleur/Celery.
- **v1 = étapes de premier niveau** ; blocs imbriqués détaillés plus tard
  si besoin.
