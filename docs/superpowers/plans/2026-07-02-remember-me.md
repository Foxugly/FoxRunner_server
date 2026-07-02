# « Se souvenir de moi » — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persister la session FoxRunner via un flux refresh-token (rotation + blacklist) pour que l'utilisateur ne se reconnecte plus à chaque rechargement, avec une case « Se souvenir de moi » au login.

**Architecture:** Le backend (djoser + simple-jwt) renvoie désormais un `refresh_token` au login/magic-link, active la rotation + blacklist, et révoque le refresh au logout. Les 2 frontends Angular gardent l'access token (1h) en mémoire, persistent le refresh (localStorage si « remember » sinon sessionStorage), réhydratent la session au démarrage via `/auth/jwt/refresh`, et rafraîchissent l'access sur 401 (avec file d'attente).

**Tech Stack:** Django-ninja, djoser, `rest_framework_simplejwt` (+ `token_blacklist`), PostgreSQL ; Angular 21 (`FoxRunner_frontend`) & Angular 19 (`FoxRunner_frontend_node20`), PrimeNG, signals.

## Global Constraints

- Access token lifetime inchangé : 1h (`AUTH_JWT_LIFETIME_SECONDS`).
- Refresh token : 30 jours (`AUTH_JWT_REFRESH_LIFETIME_SECONDS`, défaut `2592000`).
- `ROTATE_REFRESH_TOKENS=True`, `BLACKLIST_AFTER_ROTATION=True`.
- Contrat login/magic-link : `{access_token, refresh_token, token_type: "bearer"}`.
- Endpoint refresh (djoser) : `POST /api/v1/auth/jwt/refresh` body `{refresh}` → `{access, refresh}` (roté).
- Le client DOIT persister le refresh **roté** retourné (sinon éjection après le 1er refresh).
- Les 2 frontends sont des ports miroir : tout changement front est appliqué à l'identique dans les 2 repos (`FoxRunner_frontend` = A21, `FoxRunner_frontend_node20` = A19).
- `schema.ts` est généré (gitignoré) : après tout changement du contrat OpenAPI, régénérer `openapi.json` backend + `npm run gen:api:offline` dans les 2 fronts.
- Permissions/déploiement : Phase A (backend) déployée AVANT Phase B (front).

---

# PHASE A — Backend (`FoxRunner_server`)

Repo : `D:\Projects\PycharmProjects\FoxRunner_server`. Tests : `python manage.py test accounts` (SQLite par défaut en test). Chaque tâche = 1 commit ; la phase = 1 PR.

### Task A1 : Activer refresh long + rotation + blacklist

**Files:**
- Modify: `foxrunner/settings.py:198-206` (bloc `SIMPLE_JWT` et `INSTALLED_APPS`)
- Test: `accounts/tests/test_auth.py`

**Interfaces:**
- Produces: réglages `SIMPLE_JWT` (`REFRESH_TOKEN_LIFETIME`, `ROTATE_REFRESH_TOKENS`, `BLACKLIST_AFTER_ROTATION`) + app `token_blacklist` migrée (tables `token_blacklist_outstandingtoken` / `blacklistedtoken`).

- [ ] **Step 1 : Écrire le test qui échoue** (rotation invalide l'ancien refresh)

Ajouter à `accounts/tests/test_auth.py` :

```python
class RefreshRotationTests(TestCase):
    def setUp(self):
        from accounts.models import User
        self.user = User.objects.create_user(email="rot@x.io", password="pw12345678")
        self.user.is_active = True
        self.user.is_verified = True
        self.user.save()

    def _login(self):
        r = self.client.post(
            "/api/v1/auth/jwt/login",
            data="username=rot@x.io&password=pw12345678",
            content_type="application/x-www-form-urlencoded",
        )
        return r.json()

    def test_refresh_rotates_and_blacklists_old(self):
        body = self._login()
        old_refresh = body["refresh_token"]
        # 1er refresh : renvoie un nouvel access ET un nouveau refresh
        r1 = self.client.post(
            "/api/v1/auth/jwt/refresh",
            data={"refresh": old_refresh},
            content_type="application/json",
        )
        self.assertEqual(r1.status_code, 200, r1.content)
        self.assertIn("access", r1.json())
        self.assertIn("refresh", r1.json())
        # Réutiliser l'ANCIEN refresh doit échouer (blacklisté après rotation)
        r2 = self.client.post(
            "/api/v1/auth/jwt/refresh",
            data={"refresh": old_refresh},
            content_type="application/json",
        )
        self.assertEqual(r2.status_code, 401, r2.content)
```

- [ ] **Step 2 : Lancer le test, vérifier l'échec**

Run: `python manage.py test accounts.tests.test_auth.RefreshRotationTests -v2`
Expected: FAIL — `refresh_token` absent de la réponse de login (KeyError) et/ou l'ancien refresh reste valide (r2 == 200).

- [ ] **Step 3 : Config settings**

Dans `foxrunner/settings.py`, ajouter l'app blacklist à `INSTALLED_APPS` (près des autres apps `rest_framework*`) :

```python
    "rest_framework_simplejwt.token_blacklist",
```

Remplacer le bloc `SIMPLE_JWT` (l.203-206) par :

```python
def _parse_refresh_lifetime():
    from datetime import timedelta

    return timedelta(seconds=int(os.getenv("AUTH_JWT_REFRESH_LIFETIME_SECONDS", "2592000")))


SIMPLE_JWT = {
    "AUTH_HEADER_TYPES": ("Bearer",),
    "ACCESS_TOKEN_LIFETIME": _parse_token_lifetime(),
    "REFRESH_TOKEN_LIFETIME": _parse_refresh_lifetime(),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
}
```

(Note : la Task A2 fait renvoyer `refresh_token` par le login ; ce test dépend des deux — implémenter A2 avant de relancer le test complet, ou committer A1+A2 ensemble si le reviewer préfère. Ici on garde le test « rotation » dans A1 et le test « login renvoie refresh » dans A2.)

- [ ] **Step 4 : Migration blacklist**

Run: `python manage.py migrate token_blacklist`
Expected: applique les migrations `token_blacklist` (crée les tables). Aucune migration custom à écrire.

- [ ] **Step 5 : Commit**

```bash
git add foxrunner/settings.py accounts/tests/test_auth.py
git commit -m "feat(auth): 30d refresh + rotation + blacklist (SIMPLE_JWT + token_blacklist)"
```

---

### Task A2 : login + magic-link renvoient `refresh_token`

**Files:**
- Modify: `accounts/api.py:61-62` (`jwt_login`) et `accounts/api.py:139-140` (`magic_link_exchange`)
- Test: `accounts/tests/test_auth.py`

**Interfaces:**
- Produces: réponses `{access_token, refresh_token, token_type}` sur `POST /auth/jwt/login` et `POST /auth/magic-link/exchange`.

- [ ] **Step 1 : Écrire le test qui échoue**

Ajouter à `accounts/tests/test_auth.py` (classe login existante) :

```python
    def test_form_login_returns_refresh_token(self):
        response = self.client.post(
            "/api/v1/auth/jwt/login",
            data="username=rot@x.io&password=pw12345678",
            content_type="application/x-www-form-urlencoded",
        )
        body = response.json()
        self.assertIn("refresh_token", body)
        self.assertTrue(body["refresh_token"])
```

(Réutilise l'utilisateur créé dans la classe concernée ; adapter l'email au `setUp` de la classe.)

- [ ] **Step 2 : Lancer, vérifier l'échec**

Run: `python manage.py test accounts.tests.test_auth -v2`
Expected: FAIL — `KeyError: 'refresh_token'`.

- [ ] **Step 3 : Renvoyer le refresh**

Dans `accounts/api.py`, remplacer la ligne de retour de `jwt_login` (l.61-62) par :

```python
    refresh = RefreshToken.for_user(user)
    return {
        "access_token": str(refresh.access_token),
        "refresh_token": str(refresh),
        "token_type": "bearer",
    }
```

Idem pour `magic_link_exchange` (l.139-140) :

```python
    refresh = RefreshToken.for_user(user)
    return {
        "access_token": str(refresh.access_token),
        "refresh_token": str(refresh),
        "token_type": "bearer",
    }
```

- [ ] **Step 4 : Lancer, vérifier le succès** (login + rotation A1)

Run: `python manage.py test accounts.tests.test_auth -v2`
Expected: PASS (y compris `RefreshRotationTests`).

- [ ] **Step 5 : Commit**

```bash
git add accounts/api.py accounts/tests/test_auth.py
git commit -m "feat(auth): return refresh_token from login and magic-link exchange"
```

---

### Task A3 : logout blackliste le refresh

**Files:**
- Modify: `accounts/api.py:65-68` (`jwt_logout`)
- Test: `accounts/tests/test_auth.py`

**Interfaces:**
- Consumes: `refresh_token` d'un login (Task A2).
- Produces: `POST /auth/jwt/logout` body `{refresh}` → 200 ; le refresh fourni est blacklisté (refresh ultérieur → 401). Tolérant si `refresh` absent/déjà invalide (200 quand même).

- [ ] **Step 1 : Écrire le test qui échoue**

```python
    def test_logout_blacklists_refresh(self):
        body = self._login()  # helper renvoyant le JSON de login
        refresh = body["refresh_token"]
        out = self.client.post(
            "/api/v1/auth/jwt/logout",
            data={"refresh": refresh},
            content_type="application/json",
        )
        self.assertEqual(out.status_code, 200, out.content)
        # le refresh est révoqué : un refresh ensuite → 401
        r = self.client.post(
            "/api/v1/auth/jwt/refresh",
            data={"refresh": refresh},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 401, r.content)
```

- [ ] **Step 2 : Lancer, vérifier l'échec**

Run: `python manage.py test accounts.tests.test_auth -v2`
Expected: FAIL — logout est un no-op, le refresh reste valide (r == 200).

- [ ] **Step 3 : Blacklister au logout**

Remplacer `jwt_logout` (l.65-68) par :

```python
@router.post("/auth/jwt/logout", auth=None, summary="Logout (revokes the refresh token)")
def jwt_logout(request) -> dict[str, str]:
    """Blacklist the supplied refresh token (best-effort). Tolerant if the
    body has no/invalid refresh so a stale client can still 'log out'."""
    import json

    from rest_framework_simplejwt.exceptions import TokenError

    raw = request.body.decode("utf-8") if request.body else ""
    token = ""
    if raw:
        try:
            token = (json.loads(raw) or {}).get("refresh", "")
        except json.JSONDecodeError:
            token = ""
    if token:
        try:
            RefreshToken(token).blacklist()
        except TokenError:
            pass
    return {"status": "ok"}
```

- [ ] **Step 4 : Lancer, vérifier le succès**

Run: `python manage.py test accounts.tests.test_auth -v2`
Expected: PASS.

- [ ] **Step 5 : Régénérer l'OpenAPI + commit**

Le contrat login/logout/magic-link a changé (nouveau champ `refresh_token`). Régénérer le schéma versionné (cf. mémoire drift-guard) :

```bash
./.venv/Scripts/python.exe scripts/export_openapi.py
git add accounts/api.py accounts/tests/test_auth.py openapi.json
git commit -m "feat(auth): revoke refresh token on logout; regen openapi"
```

- [ ] **Step 6 : PR backend**

```bash
git checkout -b feat/remember-me-backend
git push -u origin feat/remember-me-backend
gh pr create --base main --title "feat(auth): remember-me — refresh tokens (30d, rotation, blacklist, logout revoke)" --body "Voir docs/superpowers/specs/2026-07-02-remember-me-design.md"
```

Attendre CI verte + merge. **Déployer** avant la Phase B (nouvelle var d'env optionnelle `AUTH_JWT_REFRESH_LIFETIME_SECONDS` ; défaut 30j si absente — pas de seed SSM obligatoire).

---

# PHASE B — Frontends (`FoxRunner_frontend` + `FoxRunner_frontend_node20`)

Appliquer chaque changement à l'IDENTIQUE dans les 2 repos. Chemins relatifs à `src/app`. Vérif par repo : `npm run gen:api:offline && npm run lint && npx ng build --configuration production`. `FoxRunner_frontend` utilise `ng test` ; `FoxRunner_frontend_node20` utilise `vitest`.

### Task B1 : `AuthService` — persistance du refresh + refresh()

**Files:**
- Modify: `core/auth/auth.service.ts`
- Test: `core/auth/auth.service.spec.ts` (créer si absent)

**Interfaces:**
- Consumes: contrat backend Phase A (`{access_token, refresh_token}` ; `POST /auth/jwt/refresh` → `{access, refresh}` ; `POST /auth/jwt/logout` body `{refresh}`).
- Produces: `login(email, password, remember: boolean)`, `loginWithToken(access, refresh, remember)`, `refresh(): Promise<string>`, `logout()`, `hasStoredRefresh(): boolean`, storage clé `fox.refresh` + `fox.remember`.

- [ ] **Step 1 : Remplacer `auth.service.ts`**

Remplacer intégralement `core/auth/auth.service.ts` par :

```ts
import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, computed, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../../environments/environment';

export interface CurrentUser {
  id: string;
  email: string;
  is_active: boolean;
  is_superuser: boolean;
  is_verified: boolean;
  timezone_name: string;
}

interface LoginResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

interface RefreshResponse {
  access: string;
  refresh: string;
}

const REFRESH_KEY = 'fox.refresh';
const REMEMBER_KEY = 'fox.remember';

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly http = inject(HttpClient);
  private readonly router = inject(Router);

  private readonly _token = signal<string | null>(null);
  private readonly _user = signal<CurrentUser | null>(null);

  readonly token = this._token.asReadonly();
  readonly currentUser = this._user.asReadonly();
  readonly isLoggedIn = computed(() => this._token() !== null && this._user() !== null);
  readonly isSuperuser = computed(() => this._user()?.is_superuser ?? false);

  /** localStorage when "remember" was chosen, else sessionStorage. */
  private store(): Storage {
    return localStorage.getItem(REMEMBER_KEY) === '1' ? localStorage : sessionStorage;
  }

  private setRemember(remember: boolean): void {
    localStorage.setItem(REMEMBER_KEY, remember ? '1' : '0');
  }

  private persistRefresh(refresh: string): void {
    // Write to the chosen store; clear the other so only one copy exists.
    const remember = localStorage.getItem(REMEMBER_KEY) === '1';
    (remember ? localStorage : sessionStorage).setItem(REFRESH_KEY, refresh);
    (remember ? sessionStorage : localStorage).removeItem(REFRESH_KEY);
  }

  private readRefresh(): string | null {
    return localStorage.getItem(REFRESH_KEY) ?? sessionStorage.getItem(REFRESH_KEY);
  }

  hasStoredRefresh(): boolean {
    return this.readRefresh() !== null;
  }

  async login(email: string, password: string, remember: boolean): Promise<void> {
    const body = new HttpParams({ fromObject: { username: email, password } });
    const res = await firstValueFrom(
      this.http.post<LoginResponse>(`${environment.apiBaseUrl}/auth/jwt/login`, body.toString(), {
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      }),
    );
    this.setRemember(remember);
    this._token.set(res.access_token);
    this.persistRefresh(res.refresh_token);
    await this.refreshCurrentUser();
  }

  async loginWithToken(accessToken: string, refreshToken: string, remember = true): Promise<void> {
    this.setRemember(remember);
    this._token.set(accessToken);
    this.persistRefresh(refreshToken);
    await this.refreshCurrentUser();
  }

  /** Exchange the stored refresh for a new access; persist the rotated refresh. */
  async refresh(): Promise<string> {
    const refresh = this.readRefresh();
    if (!refresh) throw new Error('no refresh token');
    const res = await firstValueFrom(
      this.http.post<RefreshResponse>(`${environment.apiBaseUrl}/auth/jwt/refresh`, { refresh }),
    );
    this._token.set(res.access);
    this.persistRefresh(res.refresh);
    return res.access;
  }

  async refreshCurrentUser(): Promise<void> {
    const user = await firstValueFrom(
      this.http.get<CurrentUser>(`${environment.apiBaseUrl}/users/me`),
    );
    this._user.set(user);
  }

  async updateTimezone(timezoneName: string): Promise<void> {
    const user = await firstValueFrom(
      this.http.patch<CurrentUser>(`${environment.apiBaseUrl}/users/me`, {
        timezone_name: timezoneName,
      }),
    );
    this._user.set(user);
  }

  async logout(): Promise<void> {
    const refresh = this.readRefresh();
    try {
      await firstValueFrom(
        this.http.post(`${environment.apiBaseUrl}/auth/jwt/logout`, { refresh }),
      );
    } catch {
      // Backend might reject an expired token; we still clear locally.
    }
    this.clear();
    this.router.navigate(['/login']);
  }

  clear(): void {
    this._token.set(null);
    this._user.set(null);
    localStorage.removeItem(REFRESH_KEY);
    sessionStorage.removeItem(REFRESH_KEY);
  }
}
```

- [ ] **Step 2 : Test unitaire (persistance)**

Créer/compléter `core/auth/auth.service.spec.ts` avec `HttpClientTestingModule` + `HttpTestingController` :

```ts
import { TestBed } from '@angular/core/testing';
import { HttpClientTestingModule, HttpTestingController } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { AuthService } from './auth.service';
import { environment } from '../../../environments/environment';

describe('AuthService remember-me', () => {
  let svc: AuthService;
  let http: HttpTestingController;

  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    TestBed.configureTestingModule({
      imports: [HttpClientTestingModule],
      providers: [provideRouter([])],
    });
    svc = TestBed.inject(AuthService);
    http = TestBed.inject(HttpTestingController);
  });

  it('persists refresh in localStorage when remember=true', async () => {
    const p = svc.login('a@b.io', 'pw', true);
    http.expectOne(`${environment.apiBaseUrl}/auth/jwt/login`).flush({
      access_token: 'acc', refresh_token: 'ref', token_type: 'bearer',
    });
    http.expectOne(`${environment.apiBaseUrl}/users/me`).flush({
      id: '1', email: 'a@b.io', is_active: true, is_superuser: false,
      is_verified: true, timezone_name: 'Europe/Brussels',
    });
    await p;
    expect(localStorage.getItem('fox.refresh')).toBe('ref');
    expect(sessionStorage.getItem('fox.refresh')).toBeNull();
  });

  it('uses sessionStorage when remember=false', async () => {
    const p = svc.login('a@b.io', 'pw', false);
    http.expectOne(`${environment.apiBaseUrl}/auth/jwt/login`).flush({
      access_token: 'acc', refresh_token: 'ref', token_type: 'bearer',
    });
    http.expectOne(`${environment.apiBaseUrl}/users/me`).flush({
      id: '1', email: 'a@b.io', is_active: true, is_superuser: false,
      is_verified: true, timezone_name: 'Europe/Brussels',
    });
    await p;
    expect(sessionStorage.getItem('fox.refresh')).toBe('ref');
    expect(localStorage.getItem('fox.refresh')).toBeNull();
  });
});
```

- [ ] **Step 3 : Lancer les tests**

Run (A21): `npx ng test --watch=false --include='**/auth.service.spec.ts'`
Run (node20): `npx vitest run src/app/core/auth/auth.service.spec.ts`
Expected: PASS (2 tests).

- [ ] **Step 4 : Commit (par repo)**

```bash
git add src/app/core/auth/auth.service.ts src/app/core/auth/auth.service.spec.ts
git commit -m "feat(auth): persist refresh token (remember-me) + refresh()"
```

---

### Task B2 : Réhydratation au démarrage (`APP_INITIALIZER`)

**Files:**
- Modify: `app.config.ts`

**Interfaces:**
- Consumes: `AuthService.hasStoredRefresh()`, `refresh()`, `refreshCurrentUser()`, `clear()`.
- Produces: session restaurée avant le premier rendu si un refresh valide est stocké.

- [ ] **Step 1 : Ajouter l'initializer**

Dans `app.config.ts`, ajouter les imports :

```ts
import { APP_INITIALIZER } from '@angular/core';
import { AuthService } from './core/auth/auth.service';
```

Ajouter dans le tableau `providers` (après `provideHttpClient(...)`) :

```ts
    {
      provide: APP_INITIALIZER,
      multi: true,
      deps: [AuthService],
      useFactory: (auth: AuthService) => async () => {
        if (!auth.hasStoredRefresh()) return;
        try {
          await auth.refresh();
          await auth.refreshCurrentUser();
        } catch {
          auth.clear();
        }
      },
    },
```

- [ ] **Step 2 : Vérifier build**

Run: `npx ng build --configuration production`
Expected: succès (le seul warning admis = budget de bundle préexistant).

- [ ] **Step 3 : Commit**

```bash
git add src/app/app.config.ts
git commit -m "feat(auth): rehydrate session on startup via stored refresh"
```

---

### Task B3 : Intercepteur — refresh sur 401 (file d'attente)

**Files:**
- Modify: `core/http/error.interceptor.ts`

**Interfaces:**
- Consumes: `AuthService.refresh()`, `token()`, `clear()`.
- Produces: sur 401 (hors `/auth/jwt/login` et `/auth/jwt/refresh`), un seul refresh en vol ; les requêtes 401 concurrentes attendent puis sont rejouées avec le nouvel access ; échec → `clear()` + redirect `/login`.

- [ ] **Step 1 : Réécrire la branche 401 de `error.interceptor.ts`**

Ajouter en tête du fichier un singleton de refresh partagé (au niveau module) :

```ts
import { from, Observable, switchMap, throwError, catchError, tap } from 'rxjs';
```

Remplacer la branche `if (err.status === 401 && !isLoginAttempt) { ... }` par une tentative de refresh. Structure attendue (le fichier garde le reste : health, toast, logging) :

```ts
// Module-level: one in-flight refresh shared by all 401s.
let refreshInFlight: Promise<string> | null = null;

function sharedRefresh(auth: AuthService): Promise<string> {
  if (!refreshInFlight) {
    refreshInFlight = auth.refresh().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}
```

Dans `catchError`, avant le toast, gérer le 401 :

```ts
      const isAuthEndpoint =
        req.url.includes('/auth/jwt/login') || req.url.includes('/auth/jwt/refresh');

      if (err.status === 401 && !isAuthEndpoint && auth.hasStoredRefresh()) {
        return from(sharedRefresh(auth)).pipe(
          switchMap((access) =>
            next(req.clone({ setHeaders: { Authorization: `Bearer ${access}` } })),
          ),
          catchError(() => {
            auth.clear();
            router.navigate(['/login']);
            return throwError(() => err);
          }),
        );
      }

      if (err.status === 401 && !isAuthEndpoint) {
        auth.clear();
        router.navigate(['/login']);
      }
```

(Conserver ensuite le bloc toast existant `if (!isLoginAttempt) { messages.add(...) }` et le `console.error` + `return throwError(() => err)` final. `health.reportSuccess/Failure` restent inchangés.)

- [ ] **Step 2 : Vérifier lint + build**

Run: `npm run lint && npx ng build --configuration production`
Expected: succès.

- [ ] **Step 3 : Commit**

```bash
git add src/app/core/http/error.interceptor.ts
git commit -m "feat(auth): refresh access token on 401 with a shared in-flight queue"
```

---

### Task B4 : Case « Se souvenir de moi » au login

**Files:**
- Modify: `features/auth/login/login.component.ts`

**Interfaces:**
- Consumes: `AuthService.login(email, password, remember)`.
- Produces: contrôle `remember` (défaut coché) passé à `login`.

- [ ] **Step 1 : Ajouter le contrôle + la case**

Ajouter l'import PrimeNG Checkbox :

```ts
import { CheckboxModule } from 'primeng/checkbox';
```
…et `CheckboxModule` dans `imports: [...]`.

Ajouter `remember` au form group :

```ts
  readonly form = this.fb.nonNullable.group({
    email: ['', [Validators.required, Validators.email]],
    password: ['', [Validators.required]],
    remember: [true],
  });
```

Dans le template, juste avant le `<p-button type="submit" ...>` :

```html
              <div class="flex align-items-center gap-2">
                <p-checkbox inputId="remember" formControlName="remember" [binary]="true" />
                <label for="remember" class="text-sm">Se souvenir de moi</label>
              </div>
```

Passer `remember` à `login` dans `onSubmit` :

```ts
      const { email, password, remember } = this.form.getRawValue();
      await this.auth.login(email, password, remember);
```

- [ ] **Step 2 : Câbler le magic-link exchange** (composant `magic-link-exchange`)

Ce composant appelle `auth.loginWithToken(...)` après échange. Vérifier `features/auth/magic-link-exchange/magic-link-exchange.component.ts` : il doit récupérer `refresh_token` de la réponse d'échange et appeler `loginWithToken(access, refresh)`. Adapter la signature de l'appel au nouveau `loginWithToken(accessToken, refreshToken, remember=true)`. (Le service d'échange `auth-magic.service.ts` doit renvoyer `refresh_token` — vérifier son type et le champ.)

- [ ] **Step 3 : Vérifier lint + build**

Run: `npm run lint && npx ng build --configuration production`
Expected: succès.

- [ ] **Step 4 : Commit**

```bash
git add src/app/features/auth/login/login.component.ts src/app/features/auth/magic-link-exchange/magic-link-exchange.component.ts
git commit -m "feat(auth): 'Se souvenir de moi' checkbox on login"
```

---

### Task B5 : Régénérer le client + miroir node20 + PR

**Files:**
- Modify: `openapi.json` (les 2 fronts), `src/app/core/api/*` (généré)

- [ ] **Step 1 : Recopier l'OpenAPI backend + régénérer**

Dans CHAQUE repo front :

```bash
cp D:/Projects/PycharmProjects/FoxRunner_server/openapi.json ./openapi.json
npm run gen:api:offline
```

- [ ] **Step 2 : Vérification finale par repo**

Run: `npm run lint && npx ng build --configuration production`
Run (tests): A21 `npx ng test --watch=false` ; node20 `npx vitest run`
Expected: succès.

- [ ] **Step 3 : PR par repo**

```bash
git checkout -b feat/remember-me
git add -A
git commit -m "chore(api): regen client for remember-me contract"
git push -u origin feat/remember-me
gh pr create --base main --title "feat(auth): remember-me (persist session, refresh-on-401, checkbox)" --body "Voir docs/superpowers/specs/2026-07-02-remember-me-design.md ; nécessite le backend Phase A déployé."
```

Attendre CI verte (rappel : `gh pr checks` + `mergeStateStatus=CLEAN` avant merge). Merger les 2.

---

## Vérification manuelle (après déploiement)

- [ ] Login avec « Se souvenir de moi » coché → recharger la page → toujours connecté.
- [ ] Login sans cocher → recharger → toujours connecté (sessionStorage) ; fermer/rouvrir l'onglet → déconnecté.
- [ ] Laisser l'access expirer (>1h) ou forcer un 401 → une requête déclenche un refresh transparent, pas de redirection login.
- [ ] Logout → refresh révoqué côté serveur (un vieux refresh rejoué → 401).
