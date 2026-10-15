-- MPSTME ONE knowledge-base schema (safe to run many times)

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id      TEXT PRIMARY KEY,
    source_type   TEXT NOT NULL,             -- 'pdf' or 'web'
    source        TEXT NOT NULL,             -- file name or page title
    url           TEXT,
    page          INT,                       -- PDF page number (NULL for web pages)
    section       TEXT,
    method        TEXT,                      -- 'text', 'ocr' or 'web'
    text          TEXT NOT NULL,
    content_hash  TEXT NOT NULL,             -- lets us re-embed only what changed
    embed_model   TEXT NOT NULL,
    embedding     vector(768) NOT NULL,
    -- keyword index for exact terms like 'ATKT', 'PPO', 'Rs. 5000'
    tsv           tsvector GENERATED ALWAYS AS (
                      to_tsvector('english', coalesce(section, '') || ' ' || text)
                  ) STORED,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- fast nearest-neighbour search on embeddings (cosine distance)
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw
    ON chunks USING hnsw (embedding vector_cosine_ops);

-- fast keyword search
CREATE INDEX IF NOT EXISTS chunks_tsv_gin ON chunks USING gin (tsv);