CREATE TABLE ptcg_v2.users (
    id text PRIMARY KEY, name text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE ptcg_v2.devices (
    id text PRIMARY KEY, user_id text NOT NULL REFERENCES ptcg_v2.users,
    name text NOT NULL, UNIQUE(id, user_id)
);
CREATE TABLE ptcg_v2.sessions (
    id text PRIMARY KEY, user_id text NOT NULL REFERENCES ptcg_v2.users,
    device_id text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL, revoked_at timestamptz,
    FOREIGN KEY(device_id, user_id) REFERENCES ptcg_v2.devices(id, user_id)
);
CREATE TABLE ptcg_v2.access_tokens (
    digest text PRIMARY KEY CHECK(length(digest) = 64),
    session_id text NOT NULL UNIQUE REFERENCES ptcg_v2.sessions,
    expires_at timestamptz NOT NULL
);
CREATE TABLE ptcg_v2.refresh_tokens (
    digest text PRIMARY KEY CHECK(length(digest) = 64),
    session_id text NOT NULL REFERENCES ptcg_v2.sessions,
    expires_at timestamptz NOT NULL, used_at timestamptz
);
CREATE UNIQUE INDEX one_unused_refresh ON ptcg_v2.refresh_tokens(session_id) WHERE used_at IS NULL;
CREATE INDEX session_expiry ON ptcg_v2.sessions(expires_at);
CREATE INDEX refresh_session ON ptcg_v2.refresh_tokens(session_id);
CREATE TABLE ptcg_v2.rate_buckets (
    key text PRIMARY KEY, count integer NOT NULL CHECK(count > 0), reset_at timestamptz NOT NULL
);
