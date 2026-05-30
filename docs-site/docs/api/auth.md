---
id: auth
title: Auth
sidebar_position: 1
---

# Auth — `/api/auth`

**File:** [`backend/app/api/auth.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/auth.py)

---

## Endpoints

| Method | Path | Line | Description |
|---|---|---|---|
| `POST` | `/api/auth/signup/send-otp` | [123](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/auth.py#L123) | Send OTP to email + WhatsApp for new signup |
| `POST` | `/api/auth/signup/verify-otp` | [160](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/auth.py#L160) | Verify 6-digit OTP, mark email verified |
| `POST` | `/api/auth/signup/complete` | [170](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/auth.py#L170) | Complete registration + broker linking |
| `POST` | `/api/auth/login` | [228](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/auth.py#L228) | Email or phone + password → JWT |
| `GET` | `/api/auth/me` | [268](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/auth.py#L268) | Current user from JWT |
| `GET` | `/api/auth/profile` | [280](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/auth.py#L280) | Extended profile with broker linkage |
| `POST` | `/api/auth/logout` | [368](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/auth.py#L368) | Invalidate session |
| `GET` | `/api/auth/groww/status` | [375](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/auth.py#L375) | Groww token validity + expiry |
| `POST` | `/api/auth/groww/refresh` | [395](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/auth.py#L395) | Force Groww OAuth token refresh |
| `PUT` | `/api/auth/groww/credentials` | [412](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/auth.py#L412) | Update Groww API key + secret |

---

## Signup Flow

```
POST /signup/send-otp
  Body: { first_name, last_name, email, phone, password, confirm_password }
  → OTP stored in Redis otp:{email}  (TTL 5 min)
  → Pending signup data stored in Redis signup:{email}
  → OTP sent to email + WhatsApp

POST /signup/verify-otp
  Body: { email, otp }
  → Marks email as verified in Redis

POST /signup/complete
  Body: { email, broker, api_key, api_secret }
  → Creates row in PostgreSQL users table
  → Clears Redis signup state
  → Returns JWT
```

## Login Flow

```
POST /login
  Body: { identifier, password }   ← identifier = email OR phone
  → Looks up user in PostgreSQL by email, then phone
  → bcrypt password check
  → Returns JWT (expires in JWT_EXPIRE_HOURS)
```

## Request / Response Schemas

### `POST /signup/send-otp`

```json title="Request"
{
  "first_name": "Abhinav",
  "last_name": "Shah",
  "email": "user@example.com",
  "phone": "9876543210",
  "password": "Min8Chars!",
  "confirm_password": "Min8Chars!"
}
```

```json title="Response 200"
{ "status": "success", "message": "Verification code sent to user@example.com and WhatsApp" }
```

### `POST /login`

```json title="Request"
{ "identifier": "user@example.com", "password": "Min8Chars!" }
```

```json title="Response 200"
{
  "status": "success",
  "data": {
    "token": "<JWT>",
    "broker": "groww",
    "expires_at": "2026-05-31T06:52:02Z",
    "user_id": 1,
    "name": "Abhinav Shah",
    "email": "user@example.com"
  }
}
```

---

## Auth Guard

All protected endpoints use `get_current_user` dependency ([line 61](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/auth.py#L61)):

```python
Authorization: Bearer <JWT>
```
