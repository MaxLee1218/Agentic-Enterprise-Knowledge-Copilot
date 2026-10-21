-- Add price history without modifying v1 metadata, attempt bytes, or cumulative spending.
BEGIN IMMEDIATE;
CREATE TABLE pricing_cards (hash TEXT PRIMARY KEY, record TEXT NOT NULL);
PRAGMA user_version = 2;
COMMIT;
