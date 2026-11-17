# Google OAuth Authentication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a secure server-side Google OAuth authorization-code flow for local ClassSim VR login without changing existing session or persona APIs.

**Architecture:** FastAPI exposes login, callback, current-user, and logout routes. The backend exchanges the authorization code with Google, verifies the returned ID token, upserts a `Trainee` by verified email, and stores a signed short-lived application session in an HttpOnly cookie. OAuth state is stored in a short-lived SameSite cookie to prevent callback forgery.

**Tech Stack:** FastAPI, HTTPX, Google Auth, itsdangerous signed cookies, SQLAlchemy, pytest.

## Global Constraints

- Google client secrets remain backend-only.
- Existing HTTP session, persona, recording, and assessment routes remain unchanged.
- Only `openid`, `email`, and `profile` scopes are requested.
- Unverified Google email identities are rejected.
- Missing OAuth configuration returns a clear configuration error rather than exposing secrets.
- OAuth callback redirect URIs must exactly match Google Cloud configuration.

## Review Focus

- Missing configuration: login must fail safely without revealing secret values.
- Invalid state: callback must reject mismatched or missing state.
- Google error response: callback must not create a trainee.
- Unverified email or invalid ID token: callback must reject the identity.
- Existing and new email: callback must reuse or create exactly one trainee.
- Session cookie: cookie must be HttpOnly, SameSite=Lax, path `/`, and time-limited.

### Task 1: Add OAuth service and contracts

**Files:**
- Create: `src/classsimvr/services/auth.py`
- Create: `src/classsimvr/services/auth_contracts.py`
- Modify: `pyproject.toml`
- Modify: `requirements.txt`

- [ ] Add environment-backed OAuth configuration and safe configuration checks.
- [ ] Add authorization URL creation, code exchange, ID-token verification, trainee upsert, and signed session helpers.

### Task 2: Add authentication routes

**Files:**
- Create: `src/classsimvr/routes/auth.py`
- Modify: `src/classsimvr/app.py`

- [ ] Add `/api/v1/auth/google/login`.
- [ ] Add `/api/v1/auth/google/callback`.
- [ ] Add `/api/v1/auth/me` and `/api/v1/auth/logout`.
- [ ] Preserve existing route registration and error behavior.

### Task 3: Add focused tests and documentation

**Files:**
- Create: `tests/test_auth_routes.py`
- Modify: `README.md`

- [ ] Test redirect parameters, state validation, existing/new trainee handling, current-user, logout, and configuration failure.
- [ ] Document required environment variables and the exact local redirect URI.
- [ ] Run syntax checks and the focused/full test suite when dependencies are available.
