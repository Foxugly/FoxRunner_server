# Design — « Se souvenir de moi » (persistance de session FoxRunner)

**Date:** 2026-07-02
**Repos:** `FoxRunner_server` (backend), `FoxRunner_frontend` (A21), `FoxRunner_frontend_node20`

## Problème

`AuthService` (les 2 frontends) garde l'access token **en mémoire seule** (signals,
aucune persistance / réhydratation). Chaque rechargement de page ou réouverture de
l'onglet déconnecte l'utilisateur → il doit sans cesse ressaisir ses identifiants.

Backend actuel (`accounts/api.py`) : `jwt_login` crée un `RefreshToken.for_user(user)`
mais **ne renvoie que l'access token** (le refresh est jeté). Access token = 1h
(`AUTH_JWT_LIFETIME_SECONDS`). `jwt_logout` est un no-op. Pas de rotation/blacklist.

## Décisions

- **Flux refresh token** (pas de simple persistance d'un access long).
- Refresh **30 jours** (env `AUTH_JWT_REFRESH_LIFETIME_SECONDS`, défaut 2592000).
- **Rotation + blacklist** : à chaque refresh, nouveau refresh émis et l'ancien
  blacklisté ; le logout blackliste le refresh côté serveur.
- Access token inchangé (1h, court).
- **Case « Se souvenir de moi »** sur le login (cochée par défaut) :
  - cochée → refresh en `localStorage` (survit à la fermeture du navigateur) ;
  - décochée → refresh en `sessionStorage` (survit au reload, effacé à la
    fermeture de l'onglet).

## Backend (`FoxRunner_server`, 1 PR)

### `foxrunner/settings.py`
- `INSTALLED_APPS += "rest_framework_simplejwt.token_blacklist"`.
- `SIMPLE_JWT` :
  - `REFRESH_TOKEN_LIFETIME = timedelta(seconds=int(os.getenv("AUTH_JWT_REFRESH_LIFETIME_SECONDS", "2592000")))`
  - `ROTATE_REFRESH_TOKENS = True`
  - `BLACKLIST_AFTER_ROTATION = True`
- `migrate` (l'app token_blacklist crée ses tables `outstanding`/`blacklisted`).

### `accounts/api.py`
- `jwt_login` (form) : renvoyer `{access_token, refresh_token, token_type: "bearer"}`
  (le refresh est déjà créé, il suffit de le renvoyer : `str(refresh)`).
- Le chemin **magic-link** (même fichier, ~l.139) : idem, ajouter `refresh_token`.
- `jwt_logout` : accepter le refresh dans le body et le blacklister
  (`RefreshToken(token).blacklist()`), tolérant si absent/déjà invalide (no-op).
- `/auth/jwt/refresh` (djoser/simple-jwt, déjà exposé) renvoie `{access, refresh}`
  (refresh roté grâce à `ROTATE_REFRESH_TOKENS`). Aucun code à ajouter, juste
  vérifier qu'il est bien routé et documenté dans l'OpenAPI si nécessaire.

### Contrat API (nouveau)
```
POST /auth/jwt/login   → { access_token, refresh_token, token_type }
POST /auth/jwt/refresh → { access, refresh }            # refresh roté
POST /auth/jwt/logout  → body { refresh } ; 200 ; blackliste le refresh
```

### Tests (`accounts/tests/test_auth.py`)
- login renvoie un `refresh_token` non vide.
- refresh renvoie un nouvel access **et** un nouveau refresh ; l'ancien refresh
  est refusé après rotation (blacklist).
- logout blackliste le refresh : un refresh ultérieur avec ce token → 401.

## Frontend (les 2 repos — code identique, 1 PR chacun)

### `core/auth/auth.service.ts`
- `access` en mémoire (signal, comme aujourd'hui) ; **refresh persisté** via un
  petit helper de stockage (clé `fox.refresh`) : lit/écrit `localStorage` OU
  `sessionStorage` selon la préférence « remember », mémorisée dans une clé
  `fox.remember` (`'1'`/`'0'`).
- `login(email, password, remember: boolean)` : POST login → stocke access
  (mémoire) + refresh (storage choisi) ; `remember` fixe le storage cible.
- `loginWithToken` (magic-link) : idem, avec `remember = true` par défaut.
- `refresh()` : POST `/auth/jwt/refresh` avec le refresh stocké → met à jour
  access (mémoire) **et** persiste le refresh **roté** retourné (sinon éjection —
  cf. mémoire flotte rotation JWT). Renvoie le nouvel access ou throw.
- `logout()` : POST refresh à `/auth/jwt/logout` (best-effort) puis `clear()` +
  purge des deux storages.
- `clear()` : vide signals + storages.

### Réhydratation au démarrage (`app.config.ts`, `APP_INITIALIZER`)
- Au boot : si un refresh est stocké → tenter `refresh()` puis `refreshCurrentUser()`
  (`/users/me`) ; échec → `clear()` (rester déconnecté, pas de blocage).
- Ne bloque pas le rendu plus que nécessaire (résout la promesse même en cas d'échec).

### Intercepteur (`core/http/auth.interceptor.ts` + `error.interceptor.ts`)
- Sur **401** (hors `/auth/jwt/login` et `/auth/jwt/refresh`) : tenter **un** refresh,
  avec une **file d'attente** (un seul refresh en vol ; les requêtes 401 concurrentes
  attendent le même refresh puis sont rejouées). Succès → rejouer la requête avec le
  nouvel access. Échec → `clear()` + redirection `/login` (comportement actuel).

### UI (`features/auth/login/login.component.ts`)
- Ajouter une case `p-checkbox` « Se souvenir de moi » (cochée par défaut), passée
  à `auth.login(...)`.

## Sécurité

- Le refresh en `localStorage` est exposé au XSS (standard SPA sans cookie
  httpOnly ; FoxRunner est hors-flotte, pas de CSP). Atténuations : access court
  (1h) + rotation + blacklist ; le logout révoque côté serveur.
- Pas de cookie httpOnly (hors périmètre ; nécessiterait CORS credentials + CSRF).

## Portée & séquencement

1. **PR backend** d'abord (contrat + migration + tests). Déployé.
2. Puis **PR frontend** sur A21 et node20 (miroir), qui consomment le nouveau contrat.
   - ⚠️ régénérer `openapi.json` backend + recopier dans les 2 fronts si le contrat
     de login/refresh/logout apparaît dans le schéma (client typé).

## Hors périmètre

- Cookies httpOnly / CSRF.
- « Se souvenir de moi » côté app mobile (non concernée).
- Idle-timeout / expiration d'inactivité.
