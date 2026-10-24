CREATE TABLE ptcg_v2.matches (
    id text PRIMARY KEY REFERENCES ptcg_v2.initializations(match_id),
    room_id text NOT NULL UNIQUE REFERENCES ptcg_v2.rooms,
    rule_release_id text NOT NULL, match_seq bigint NOT NULL DEFAULT 0,
    state_version bigint NOT NULL, status text NOT NULL CHECK(status IN ('playing','finished','aborted')),
    recovery jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE ptcg_v2.match_members (
    match_id text NOT NULL REFERENCES ptcg_v2.matches, user_id text NOT NULL REFERENCES ptcg_v2.users,
    seat text NOT NULL CHECK(seat IN ('PLAYER1','PLAYER2')),
    controller_epoch bigint NOT NULL DEFAULT 0, controller_id text,
    controller_session text REFERENCES ptcg_v2.sessions,
    stream_seq bigint NOT NULL DEFAULT 0, stream_cursor text,
    PRIMARY KEY(match_id,user_id), UNIQUE(match_id,seat)
);
CREATE TABLE ptcg_v2.match_frames (
    match_id text NOT NULL, user_id text NOT NULL, match_seq bigint NOT NULL,
    cursor text NOT NULL UNIQUE, snapshot jsonb NOT NULL,
    PRIMARY KEY(match_id,user_id,match_seq),
    FOREIGN KEY(match_id,user_id) REFERENCES ptcg_v2.match_members
);
CREATE TABLE ptcg_v2.match_receipts (
    match_id text NOT NULL, user_id text NOT NULL, command_id text NOT NULL,
    digest text NOT NULL, receipt jsonb NOT NULL,
    PRIMARY KEY(match_id,user_id,command_id),
    FOREIGN KEY(match_id,user_id) REFERENCES ptcg_v2.match_members
);
CREATE TABLE ptcg_v2.control_receipts (
    match_id text NOT NULL, user_id text NOT NULL, request_id text NOT NULL,
    digest text NOT NULL, receipt jsonb NOT NULL,
    PRIMARY KEY(match_id,user_id,request_id),
    FOREIGN KEY(match_id,user_id) REFERENCES ptcg_v2.match_members
);
CREATE TABLE ptcg_v2.match_outbox (
    match_id text NOT NULL, user_id text NOT NULL, stream_seq bigint NOT NULL,
    cursor text NOT NULL UNIQUE, kind text NOT NULL CHECK(kind IN ('snapshot','update','control')),
    snapshot jsonb NOT NULL,
    PRIMARY KEY(match_id,user_id,stream_seq),
    FOREIGN KEY(match_id,user_id) REFERENCES ptcg_v2.match_members
);
