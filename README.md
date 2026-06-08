# Email Marketing Django Backend

Standalone microservice for email marketing. Multi-tenant: **Client → many Stores → contacts/campaigns per store**.

Contacts are imported via **CSV upload**. Bulk send runs in background batch loops (Celery-ready).

## Tenant model

```
User (global login — one email, one password)
  └── ClientUser (role per client: admin | member)
        └── Client (organization)
              └── Stores (1..N)
                    ├── Contacts (CSV upload)
                    ├── Campaigns
                    ├── Templates
                    └── Segments
```

One user can belong to **multiple clients** via `ClientUser`.

## Stack

- Django 4.1 + DRF + Token auth
- MySQL
- SendGrid or SMTP per store

## Setup

```bash
cd ~/Documents/email-marketing-django-backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env with MySQL credentials

python manage.py makemigrations Accounts Stores EmailMarketing
python manage.py migrate
python manage.py runserver
```

## Auth (Token)

All authenticated requests:

```
Authorization: Token <token>
```

Client-scoped endpoints (`/stores`, `/auth/users`) require:

```
X-Client-Id: <client_id>
```

Store-scoped endpoints (contacts, campaigns, templates, etc.) require **both**:

```
X-Client-Id: <client_id>
X-Store-Id: <store_id>
```

## Onboarding flow

### 1. Register client + admin user (+ optional first store)

```bash
POST /auth/register
{
  "client_name": "Bata Pakistan",
  "email": "admin@bata.com",
  "password": "securepass123",
  "first_name": "Admin",
  "store_name": "Bata Karachi",
  "shop_url": "bata-karachi.myshopify.com"
}
```

Response includes `token`, `user`, `client`, and `stores`.

### 2. Login

```bash
POST /auth/login
{"email": "admin@bata.com", "password": "securepass123"}
```

Returns `token` + list of **all clients** the user can access (each with role + stores).

### 3. Add more stores (client admin only)

```bash
POST /stores
Authorization: Token ...
X-Client-Id: 1
{
  "name": "Bata Lahore",
  "shop_url": "bata-lahore.myshopify.com",
  "email_provider": "sendgrid",
  "sendgrid_api_key": "SG.xxx",
  "default_from_email": "hello@bata.com"
}
```

### 4. Invite team members (client admin only)

```bash
POST /auth/users
Authorization: Token ...
X-Client-Id: 1
{
  "email": "marketer@bata.com",
  "password": "securepass123",
  "role": "member"
}
```

Roles are **per client** on `ClientUser`: `admin` (manage stores/users), `member` (use stores).

If the email already exists (user works with another client), omit `password` — only a `ClientUser` link is created.

### 5. Upload contacts CSV for a store

```bash
POST /stores/contacts/uploadCsv
Authorization: Token ...
X-Client-Id: 1
X-Store-Id: 1
Form: file=@sample_contacts.csv
```

### 6. Create & send campaign

```bash
POST /emailMarketing/campaigns
Authorization: Token ...
X-Client-Id: 1
X-Store-Id: 1
{"name": "Summer Sale", "subject": "Sale!", "segment": 1, "html_content": "<p>Hi {{first_name}}</p>"}

POST /emailMarketing/campaigns/1/buildAudience
POST /emailMarketing/campaigns/1/send
```

## API endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/auth/register` | None | Onboard client + admin |
| POST | `/auth/login` | None | Login, get token |
| POST | `/auth/logout` | Token | Invalidate token |
| GET | `/auth/me` | Token | Current user + all clients |
| GET/POST | `/auth/users` | Token + X-Client-Id (admin) | Team per client |
| GET/POST | `/stores` | Token + X-Client-Id | List / create stores |
| GET/PATCH | `/stores/{id}` | Token + X-Client-Id | Store settings |
| POST | `/stores/contacts/uploadCsv` | Token + X-Client-Id + X-Store-Id | CSV upload |
| GET/POST | `/emailMarketing/campaigns` | Token + X-Client-Id + X-Store-Id | Campaigns |
| POST | `/emailMarketing/campaigns/{id}/send` | Token + X-Client-Id + X-Store-Id | Start bulk send |

## CSV format

Required: `email`. Optional: `first_name`, `last_name`, `phone`, `city`, `country`, `tags`, `accept_email_marketing`, `total_orders`, `total_spent`.

See `sample_contacts.csv`.

## Celery migration

See `EmailMarketing/tasks.py` — replace `spawn_thread` with Celery task when ready.
