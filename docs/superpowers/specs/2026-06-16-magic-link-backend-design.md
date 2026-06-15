# FoxRunner_server — magic-link (passwordless) login backend

**Date:** 2026-06-16
**Statut:** design approuvé, prêt pour le plan d'implémentation
**Sous-projet:** A (backend). Le frontend (FoxRunner_frontend + fork node20) est un sous-projet B séparé.

## Problème

Ajouter une connexion **par lien magique** (passwordless) à FoxRunner, alignée sur quizonline/tm
(OPERATIONS.md : « magic-link login keyed on email, single-use + short TTL »). L'utilisateur saisit
son email → reçoit un lien → clique → est connecté (JWT), sans mot de passe.

FoxRunner a **déjà** toute l'infra nécessaire : envoi email via Graph (+ SMTP fallback) dans
`app/mail.py` / `ops/graph.py`, et un pattern token `TimestampSigner` éprouvé (reset-password). Le
magic-link **mirror le reset-password existant** — pas de copie verbatim de quizonline, pas de
nouveaux secrets Graph (ceux du reset-password sont déjà en SSM).

## Contrat (figé ici, consommé par le frontend)

Deux endpoints Ninja sous le router auth existant (`accounts/api.py`, `tags=["auth"]`), préfixe
`/api/v1` :

### `POST /api/v1/auth/magic-link/request`  (auth=None)
- Body : `{ "email": "<str>" }` (`MagicLinkRequestIn`).
- Cherche l'utilisateur **actif ET vérifié** (`is_active=True, is_verified=True`, email
  case-insensitive). Si trouvé : génère un token et envoie l'email magic-link. Sinon : rien.
- Réponse : **toujours `202 {"status": "queued"}`** (silencieux, anti-énumération — identique à
  `forgot-password`).

### `POST /api/v1/auth/magic-link/exchange`  (auth=None)
- Body : `{ "token": "<str>" }` (`MagicLinkExchangeIn`).
- Valide le token (TTL 900 s), récupère l'utilisateur, re-vérifie **actif ET vérifié**, émet le JWT
  **via la même mécanique que le login** (`RefreshToken.for_user(user)`).
- Réponse 200 : `{ "access_token": "<jwt>", "token_type": "bearer" }` (**même forme que
  `/auth/jwt/login`** — un seul access_token, pas de refresh exposé).
- Erreurs : **410** « Lien expiré » (`SignatureExpired`), **400** « Lien invalide »
  (`BadSignature`, utilisateur introuvable, ou non éligible).

## Architecture / fichiers

### `accounts/magic_link.py` (nouveau)
Isole la logique token (mirroir de `PASSWORD_RESET_SALT`/`unsign` mais en module dédié) :
```text
MAGIC_LINK_SALT = "accounts.magic_link"
MAGIC_LINK_MAX_AGE_SECONDS = 900            # 15 min

make_magic_link_token(user_id) -> str       # TimestampSigner(salt).sign(str(user_id))
parse_magic_link_token(token) -> str         # unsign(token, max_age); lève SignatureExpired / BadSignature
```
Single-use = par TTL (comme reset-password ; pas de tracking DB). Salt distinct du reset → un token
de reset ne peut pas servir de magic-link et inversement.

### `foxrunner/serializers.py` (modifié)
Ajouter, à côté de `ForgotPasswordIn`/`ResetPasswordIn` :
```text
MagicLinkRequestIn  { email: str }
MagicLinkExchangeIn { token: str }
```
(La réponse de l'exchange est un `dict[str, str]` inline comme `jwt_login`, pas un schéma nommé.)

### `accounts/api.py` (modifié)
Deux vues mirror de `forgot_password`/`reset_password` :
- `request` : `User.objects.filter(email__iexact=…, is_active=True, is_verified=True).first()` → si
  trouvé, `token = make_magic_link_token(user.id)` puis `send_magic_link_email(user.email, token)` →
  `202 {"status": "queued"}`.
- `exchange` : `parse_magic_link_token` (mappe `SignatureExpired`→410, `BadSignature`→400) →
  `User.objects.get(id=user_id)` (DoesNotExist/ValueError→400) → si `not (is_active and is_verified)`
  →400 → `RefreshToken.for_user(user)` → `{access_token, token_type}`.

### `app/mail.py` (modifié)
Ajouter `send_magic_link_email(email, token)`, mirror de `send_password_reset_email` mais avec un
**lien cliquable** :
```text
magic_url = os.getenv("APP_MAGIC_LINK_URL", "http://localhost:4200/auth/magic")
link = f"{magic_url}/{token}"
subject = "Votre lien de connexion FoxRunner"
body = "Cliquez sur ce lien pour vous connecter (valable 15 minutes) :\n\n{link}"
```
Branche Graph (si `GRAPH_MAIL_ENABLED`) puis fallback SMTP, **identiques** à reset-password
(réutilise `send_graph_mail` et la même config). Nouveau réglage `APP_MAGIC_LINK_URL`
(défaut `http://localhost:4200/auth/magic`) ; en prod il pointera sur le frontend (mirror de
`APP_PASSWORD_RESET_URL`).

### Throttle (léger, nouveau)
FoxRunner n'a aucun throttle aujourd'hui. Ajouter une protection **simple par IP** sur
`magic-link/request` (et `exchange`) via le cache Django : un helper `throttle(request, scope, limit,
window)` qui compte par `(scope, ip)` et lève `HttpError(429, "Trop de tentatives.")` au-delà
(ex. 5/heure pour request, 20/heure pour exchange). Minimal, sans dépendance DRF.

## Tests (Django, suite existante du serveur)

- `request` : utilisateur actif+vérifié → 202 **et** email envoyé (mock `send_magic_link_email`) ;
  email inconnu / inactif / non vérifié → 202 **sans** envoi (silencieux).
- `exchange` : token frais d'un user éligible → 200 + `access_token` valide ; token expiré (forger
  via `TimestampSigner` avec timestamp ancien) → 410 ; token altéré → 400 ; user désactivé/non
  vérifié après émission → 400 ; user supprimé → 400.
- `make/parse_magic_link_token` : round-trip ; `max_age` dépassé → `SignatureExpired`.
- throttle : au-delà de la limite → 429.

## Sécurité / vigilance

- **Anti-énumération** : request renvoie 202 quel que soit le cas (jamais « email inconnu »).
- **Éligibilité** re-checkée à l'exchange (un compte désactivé entre l'envoi et le clic ne passe pas).
- **Token en clair dans l'URL** : TTL court (15 min) + salt dédié ; ne pas logguer l'URL complète.
- **Graph déjà configuré** (reset-password l'utilise) → aucun nouveau secret à poser ; au plus
  définir `APP_MAGIC_LINK_URL` en prod.

## Hors périmètre

- Le frontend (sous-projet B).
- Refresh tokens (FoxRunner n'en expose pas ; le magic-link suit le même modèle access-only).
- i18n des emails (FoxRunner envoie en FR en dur, comme reset-password).
- Connection-log/audit du « login method » (quizonline l'a ; optionnel, non requis ici).
