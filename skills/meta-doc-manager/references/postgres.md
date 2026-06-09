# Using Postgres as the backend

`cm.py` and `tm.py` both auto-detect the backend from the form of `--db`:

- starts with `postgresql://` or `postgres://` → Postgres (via `psycopg` v3)
- anything else → SQLite path

The schema is identical in either backend (UUID string PKs, an `idx`
integer handle, app-written ISO-8601 timestamps, no `CHECK` constraints, no
recursive CTEs). See `schema.md` for the full table definitions.

## Prereq for Postgres

```
pip install 'psycopg[binary]>=3'
```

Only needed when actually pointing `--db` at a Postgres URI. SQLite usage
remains stdlib-only.

## Local Postgres

The simplest way to develop against a local Postgres is the official Docker
image:

```
docker run -d --name md-pg \
  -e POSTGRES_PASSWORD=local \
  -e POSTGRES_DB=metadocs \
  -p 5432:5432 \
  postgres:16
```

Then point the CLI at it:

```
export META_DOC_MANAGER_DB=postgresql://postgres:local@localhost:5432/metadocs
cm.py init
cm.py topic add --name "Auth"
```

`init` is idempotent (all `CREATE TABLE` statements use `IF NOT EXISTS`), so
re-running it against an existing database is safe.

## AWS RDS

Connection-only support: any reachable RDS instance works. The CLI does not
provision infrastructure.

Minimum viable setup:

1. **Create the instance.** Engine: PostgreSQL 16 (or 15). Smallest burstable
   instance class is fine. Storage: 20 GB gp3 is plenty — the index is small.
2. **Security group.** Allow inbound TCP 5432 from your developer machine
   (or VPN CIDR). Do not open it to `0.0.0.0/0`.
3. **Database.** Either let RDS create the default `postgres` database and
   use that, or create a dedicated database (`CREATE DATABASE metadocs;`).
4. **Credentials.** Use the master password RDS gives you, or create a
   dedicated role with `CREATE`, `SELECT`, `INSERT`, `UPDATE`, `DELETE` on
   the target database.
5. **SSL.** RDS accepts SSL by default. Append `?sslmode=require` to the URI:

   ```
   postgresql://metadocs_user:PW@your-instance.xyz.us-east-1.rds.amazonaws.com:5432/metadocs?sslmode=require
   ```

6. **Initialize.** Run `cm.py init --db <URI>` (and `tm.py init` if using
   todos). That's it.

### Things explicitly out of scope (for now)

- No Terraform / CDK templates.
- No IAM database authentication (use a password, store it in your shell
  config or a secrets manager).
- No connection pooling. Each CLI invocation opens one connection and exits.
- No multi-tenancy / schema-per-project. One database, one project, same as
  the SQLite mode.

## Backups and care of remote data

Two things to know:

- The CLI does not maintain migrations — schema lives in
  `META_DOC_SCHEMA_SQL` and is recreated via `IF NOT EXISTS`. If you change
  the schema, plan a manual migration with `pg_dump` / `pg_restore`.
- For local SQLite users moving to Postgres, see
  `_one_time_migrate_local.py` — it reads an old SQLite (integer-PK or new
  UUID-PK) and copies it into the target DB.
