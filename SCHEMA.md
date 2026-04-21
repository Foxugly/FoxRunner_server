# JSON DSL

Le projet est pilote par deux fichiers JSON dans `config/`.

## `config/slots.json`

Chaque entree de `slots` decrit un creneau :

```json
{
  "id": "slot_example",
  "days": [0, 1, 2, 3],
  "start": "17:30",
  "end": "18:00",
  "scenario": "browser_scenario"
}
```

- `id`: identifiant stable du slot
- `days`: jours ISO Python, `0=lundi` ... `6=dimanche`
- `start` / `end`: format `HH:MM`
- `scenario`: identifiant d'un scenario de `config/scenarios.json`

## `config/scenarios.json`

Le document contient une section `data` et une map `scenarios`.

```json
{
  "schema_version": 1,
  "data": {
    "default_pushover": "channel_default",
    "default_network": "network_default",
    "pushovers": {
      "channel_default": {
        "token": "...",
        "user_key": "..."
      }
    },
    "networks": {
      "network_default": {
        "office_ipv4_networks": [],
        "office_gateway_networks": [],
        "office_dns_suffixes": [],
        "vpn_interface_keywords": [],
        "vpn_process_names": [],
        "internal_test_hosts": [],
        "internal_test_ports": [],
        "tcp_timeout_seconds": 1.0,
        "home_like_networks": [],
        "allow_private_non_home_heuristic_for_vpn": true
      }
    }
  },
  "scenarios": {
    "browser_scenario": {
      "user_id": "default",
      "description": "Scenario navigateur automatise",
      "before_steps": [],
      "steps": [],
      "on_success": [],
      "on_failure": [],
      "finally_steps": []
    }
  }
}
```

### `data`

- `pushovers`: map cle/valeur de configurations Pushover
- `networks`: map cle/valeur de configurations reseau entreprise
- `default_pushover`: cle Pushover par defaut pour les notifications techniques
- `default_network`: cle reseau par defaut pour le scheduler
- `schema_version`: version du DSL supportee par le moteur

### Association utilisateur

Chaque scenario peut etre associe a un ou plusieurs utilisateurs pour l'API FastAPI :

- `user_id`: utilisateur proprietaire unique
- `owner_user_id`: alias explicite de `user_id`
- `user_ids`: liste d'utilisateurs autorises

Si aucun de ces champs n'est present, le scenario appartient a l'utilisateur API `default`.

Exemples :

```json
{
  "scenarios": {
    "alice_pointer": {
      "user_id": "alice",
      "steps": []
    },
    "shared_pointer": {
      "user_ids": ["alice", "bob"],
      "steps": []
    }
  }
}
```

### Etapes supportees

#### Operations atomiques

- `open_url`
  - `url`
- `click`
  - `by`
  - `locator`
  - `timeout`
- `wait_for_element`
  - `by`
  - `locator`
  - `timeout`
- `input_text`
  - `by`
  - `locator`
  - `text`
  - `timeout`
  - `clear_first`
- `assert_text`
  - `by`
  - `locator`
  - `text`
  - `timeout`
  - `match` = `contains` ou `equals`
- `assert_attribute`
  - `by`
  - `locator`
  - `attribute`
  - `value`
  - `timeout`
  - `match` = `contains` ou `equals`
- `extract_text_to_context`
  - `key`
  - `by`
  - `locator`
  - `timeout`
- `extract_attribute_to_context`
  - `key`
  - `by`
  - `locator`
  - `attribute`
  - `timeout`
- `screenshot`
  - `path`
- `select_option`
  - `by`
  - `locator`
  - `value` ou `visible_text` ou `index`
  - `timeout`
- `wait_until_url_contains`
  - `value`
  - `timeout`
- `wait_until_title_contains`
  - `value`
  - `timeout`
- `close_browser`
  - aucun champ requis
- `sleep`
  - `seconds`
- `sleep_random`
  - `min_seconds`
  - `max_seconds`
- `notify`
  - `message`
  - `pushover_key` optionnel
- `http_request`
  - `method`
  - `url`
  - `headers`
  - `json`
  - `data`
  - `timeout`
  - `expected_status`
- `require_enterprise_network`
  - `network_key` optionnel
- `set_context`
  - `key`
  - `value`
- `format_context`
  - `key`
  - `template`

#### Blocs DSL

- `group`
  - `steps`
- `parallel`
  - `steps`
  - reserve aux operations non Selenium partageant le driver
- `repeat`
  - `times`
  - `steps`
- `try`
  - `try_steps`
  - `catch_steps` optionnel
  - `finally_steps` optionnel

### Champs DSL transverses

Chaque etape peut aussi declarer :

- `when`
  - `context_exists:variable`
  - `context_not_exists:variable`
  - `context_equals:variable=valeur`
  - `context_in:variable=a,b,c`
  - `context_matches:variable=regex`
- `retry`
  - nombre de tentatives supplementaires
- `retry_delay_seconds`
  - delai entre deux tentatives
- `retry_backoff_seconds`
  - multiplicateur applique entre deux tentatives
- `timeout_seconds`
  - timeout de l'etape
- `continue_on_error`
  - journalise l'erreur puis continue le scenario
- `ref`
  - ex: `{ "pushover": "channel_default" }`
  - ex: `{ "network": "network_default" }`

### Variables disponibles dans les messages

- `{slot_key}`
- `{slot_id}`
- `{scenario_id}`
- `{scheduled_for}`
- `{executed_at}`
- `{execution_id}`
- `{current_step}`
- `{error_message}`

### Semantique des hooks

- `before_steps`: executes avant `steps`
- `steps`: scenario principal
- `on_success`: executes seulement si `steps` reussit
- `on_failure`: executes seulement si `steps` echoue
- `finally_steps`: executes dans tous les cas

### Detection du besoin reseau

Le scheduler deduit si un scenario exige le reseau entreprise/VPN en detectant l'operation
`require_enterprise_network` dans ses hooks ou ses `steps`. Cette regle ne se configure plus
dans `config/slots.json`.

### Dry-run

Le mode `--dry-run` traverse `before_steps`, `steps`, `on_success`, `on_failure` et `finally_steps`
sans lancer Selenium ni effectuer d'effets externes sur les operations `notify`, `http_request`
ou `sleep`.

## Runtime files

### `next.json`

Contient la prochaine execution planifiee avec :

- `slot_key`
- `slot_id`
- `scenario_id`
- `execution_id`
- `scheduled_for`
- `status`
- `updated_at`
- `details` si applicable

### `last_run.json`

Contient le dernier resultat d'execution avec :

- `slot_key`
- `slot_id`
- `scenario_id`
- `execution_id`
- `executed_at`
- `status`
- `step`
- `message`
- `updated_at`

En cas d'echec Selenium, `message` peut aussi inclure :

- `screenshot=<chemin>`
- `page_source=<chemin>`

Les fichiers associes sont ecrits sous `.runtime/artifacts/screenshots/` et `.runtime/artifacts/pages/` quand la capture est possible.

## Validation

La commande `python main.py --validate-config` valide `config/slots.json` et `config/scenarios.json`
sans lancer le scheduler.

La commande `python main.py --plan` affiche la prochaine execution theorique, le scenario cible
et les metadonnees principales sans lancer la tache.

La commande `python main.py --dump-runtime` affiche la configuration runtime resolue.

La commande `python main.py --list-slots` liste les slots configures.

La commande `python main.py --list-scenarios` liste les scenarios configures.

La commande `python main.py --history` affiche l'historique JSON filtre.

Filtres disponibles :

- `--history-limit`
- `--history-status`
- `--history-slot-id`
- `--history-scenario-id`
- `--history-execution-id`

La commande `python main.py --prune-history-days <N>` purge l'historique plus ancien que `N` jours.

La commande `python main.py --run-slot <slot_id>` execute immediatement un slot.

La commande `python main.py --run-scenario <scenario_id>` execute immediatement un scenario.

La commande `python main.py --run-next` execute immediatement le prochain slot calcule.

La commande `python main.py --export-plan <fichier.json>` ecrit le plan courant dans un fichier JSON.

Les fichiers de schema documentaires sont :

- `schemas/slots.schema.json`
- `schemas/scenarios.schema.json`

## Historique

La commande `python -m cli --limit 10 --status failed` permet d'explorer `history.jsonl`.

## Exemples

Des exemples complets sont fournis dans :

- `examples/scenario_browser.json`
- `examples/scenario_rest_notify.json`
- `examples/scenario_flow_blocks.json`
