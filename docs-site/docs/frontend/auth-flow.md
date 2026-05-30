---
id: auth-flow
title: Auth Flow (Login & Signup)
sidebar_label: Auth Flow
---

# Auth Flow

## Login Page (`/login`)

**File:** `frontend/src/pages/Login.tsx`

### Step-by-step flow

```
User fills: identifier (email or phone) + password
    │
    ▼
POST /api/auth/login
    │  body: { identifier, password }
    │  success → { access_token, token_type, broker, expires_at, user_id, email }
    ▼
authStore.setAuth(token, broker, expires_at, user_id, email)
    │
    ▼
GET /api/auth/me
    │  header: Authorization: Bearer <token>
    │  returns: UserProfile { name, email, phone, broker, groww_connected }
    ▼
authStore.setProfile(profile)
    │
    ▼
navigate(originalPage || '/')
```

### Error handling

| HTTP Status | Cause | UI Message |
|---|---|---|
| 401 | Wrong password / user not found | `extractApiError()` → "Invalid credentials..." |
| 422 | Missing field | Pydantic detail array parsed by `extractApiError()` |

---

## Signup Page (`/signup`)

**File:** `frontend/src/pages/Signup.tsx`

3-step multi-stage form:

### Step 1 — Personal Info

```
User fills: name, email, phone, password, confirm_password
    │
    ▼
POST /api/auth/signup/send-otp
    │  body: { name, email, phone, password }
    │  success → { message: "OTP sent" }
    ▼
Advance to Step 2
```

### Step 2 — OTP Verification

```
User fills: 6-digit OTP (received via email/SMS)
    │
    ▼
POST /api/auth/signup/verify-otp
    │  body: { email, otp }
    │  success → { message: "OTP verified" }
    ▼
Advance to Step 3
```

### Step 3 — Broker Credentials

```
User selects: broker (GROWW / ZERODHA / UPSTOX / ANGEL / NONE)
User fills:   api_key, api_secret (optional for NONE)
    │
    ▼
POST /api/auth/signup/complete
    │  body: { email, broker, api_key?, api_secret? }
    │  success → { access_token, broker, expires_at, user_id, email }
    ▼
authStore.setAuth(...)
    │
    ▼
GET /api/auth/me  → authStore.setProfile(...)
    │
    ▼
navigate('/')
```

### Error handling

All catch blocks use `extractApiError(err, fallback)` which:
- Returns string `detail` as-is
- Parses Pydantic array `[{type, loc, msg}]` — strips "Value error," prefix
- Falls back to the provided message

```typescript
// frontend/src/pages/Signup.tsx
function extractApiError(err: unknown, fallback: string): string {
  const detail = (err as any)?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const msg = (detail[0] as any)?.msg || fallback;
    return msg.replace(/^Value error,\s*/i, '');
  }
  return fallback;
}
```

---

## Protected Route (`frontend/src/components/ProtectedRoute.tsx`)

Wraps all authenticated pages. On every navigation:

```
ProtectedRoute mounts
    │
    ▼
GET /api/auth/me
    │  401 → clearAuth() → <Navigate to="/login" />
    │  200 → setProfile(data) → render children
    ▼
Children rendered
```
