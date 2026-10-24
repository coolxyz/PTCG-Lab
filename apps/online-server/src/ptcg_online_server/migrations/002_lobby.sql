CREATE TABLE ptcg_v2.deck_revisions (
    id text PRIMARY KEY, user_id text NOT NULL REFERENCES ptcg_v2.users,
    rule_release_id text NOT NULL, as_of text NOT NULL,
    entries jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX deck_owner ON ptcg_v2.deck_revisions(user_id);
CREATE TABLE ptcg_v2.rooms (
    id text PRIMARY KEY, invite_code text NOT NULL UNIQUE,
    rule_release_id text NOT NULL, as_of text NOT NULL,
    version bigint NOT NULL DEFAULT 1 CHECK(version > 0),
    status text NOT NULL CHECK(status IN ('waiting','initializing','initialized','technical_abort','closed')),
    match_id text UNIQUE, created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL
);
CREATE TABLE ptcg_v2.room_members (
    room_id text NOT NULL REFERENCES ptcg_v2.rooms, user_id text NOT NULL REFERENCES ptcg_v2.users,
    seat text NOT NULL CHECK(seat IN ('PLAYER1','PLAYER2')),
    deck_revision text NOT NULL REFERENCES ptcg_v2.deck_revisions,
    ready boolean NOT NULL DEFAULT false, left_at timestamptz,
    PRIMARY KEY(room_id,user_id), UNIQUE(room_id,seat)
);
CREATE INDEX member_user ON ptcg_v2.room_members(user_id);
CREATE TABLE ptcg_v2.lobby_receipts (
    user_id text NOT NULL REFERENCES ptcg_v2.users, request_id text NOT NULL,
    digest text NOT NULL, response jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(user_id,request_id)
);
CREATE TABLE ptcg_v2.initializations (
    match_id text PRIMARY KEY, room_id text NOT NULL UNIQUE REFERENCES ptcg_v2.rooms,
    seed bigint NOT NULL CHECK(seed >= 0 AND seed <= 9007199254740991),
    rule_release_id text NOT NULL, as_of text NOT NULL,
    player1 text NOT NULL REFERENCES ptcg_v2.users, player2 text NOT NULL REFERENCES ptcg_v2.users,
    revision1 text NOT NULL REFERENCES ptcg_v2.deck_revisions,
    revision2 text NOT NULL REFERENCES ptcg_v2.deck_revisions,
    deck1 jsonb NOT NULL, deck2 jsonb NOT NULL,
    status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','initialized','technical_abort')),
    attempts integer NOT NULL DEFAULT 0, lease_token text, lease_until timestamptz,
    retry_at timestamptz NOT NULL DEFAULT now(), recovery jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK(player1 <> player2),
    CHECK((status = 'initialized') = (recovery IS NOT NULL))
);
CREATE INDEX initialization_queue ON ptcg_v2.initializations(status,retry_at,lease_until);
