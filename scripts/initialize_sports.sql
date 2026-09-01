BEGIN;

INSERT INTO sports (name, normalized_name, max_players)
VALUES
    ('Fútbol', 'futbol', 11),
    ('Básquet', 'basquet', 5)
ON CONFLICT (normalized_name) DO NOTHING;

COMMIT;
