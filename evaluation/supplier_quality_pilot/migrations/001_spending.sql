-- Evaluation-only spending authority; never applied to a Task or business database.
BEGIN IMMEDIATE;
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE attempts (id TEXT PRIMARY KEY, record TEXT NOT NULL);
PRAGMA user_version = 1;
COMMIT;
