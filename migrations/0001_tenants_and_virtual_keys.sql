-- Phase 2 — tenancy: tenants and their virtual keys.
--
-- The first real migration. Three properties in here are load-bearing and are argued
-- in docs/DECISIONS.md H-017 (the key format) and H-022 (this schema):
--
--   1. A key's plaintext is NEVER stored. `key_hash` holds the SHA-256 of the whole
--      `hk_...` string and is the only thing authentication ever looks up by;
--      `key_prefix` holds a deliberately short, non-secret slice for display.
--   2. Revocation is a timestamp, not a boolean. `revoked_at IS NULL` *is* the active
--      state, so "is it live" and "when did it die" can never disagree — which they
--      can, and eventually do, when a boolean and a timestamp are maintained apart.
--   3. Keys and tenants are never deleted. Phase 3's cost ledger attributes every
--      request to a key id and a tenant id forever; a row that vanishes turns a
--      historical invoice into an orphan. The admin API revokes and deactivates.

CREATE TABLE tenants (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name       TEXT        NOT NULL,
    active     BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One tenant per name: the admin API answers a duplicate with 409 rather than
-- silently creating a second "acme" that a later operator will attribute spend to.
CREATE UNIQUE INDEX tenants_name_uniq ON tenants (name);

CREATE TABLE virtual_keys (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    -- RESTRICT rather than CASCADE: deleting a tenant out from under its keys would
    -- silently delete the identities Phase 3's ledger rows point at. Tenants are
    -- deactivated, never deleted, and this constraint is what enforces that.
    tenant_id         UUID        NOT NULL REFERENCES tenants (id) ON DELETE RESTRICT,
    name              TEXT        NOT NULL,
    -- SHA-256 hex of the full `hk_...` string (H-017). UNIQUE both because a
    -- collision would be a catastrophe and because the uniqueness gives the index
    -- that every authenticated request uses.
    key_hash          TEXT        NOT NULL UNIQUE,
    -- The first 11 characters of the key — `hk_` plus 8 of its 43 secret characters.
    -- Enough to recognise a key in a list, ~48 bits short of enough to be one.
    key_prefix        TEXT        NOT NULL,
    -- Scope. An EMPTY array means unrestricted, which is the only encoding that makes
    -- "no restriction" and "restricted to nothing" different values instead of the
    -- same NULL. An entry matches a model/provider exactly, or as a prefix when it
    -- ends in `*`.
    allowed_models    TEXT[]      NOT NULL DEFAULT '{}',
    allowed_providers TEXT[]      NOT NULL DEFAULT '{}',
    revoked_at        TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The admin API's list-by-tenant, and Phase 7's Tenants & Keys view.
CREATE INDEX virtual_keys_tenant_id_idx ON virtual_keys (tenant_id);
