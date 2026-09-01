BEGIN;

INSERT INTO sports (
    name,
    normalized_name,
    max_players,
    max_players_in_game
)
VALUES
    ('Fútbol', 'futbol', 22, 11),
    ('Básquet', 'basquet', 15, 5)
ON CONFLICT (normalized_name) DO NOTHING;

COMMIT;
