BEGIN;

INSERT INTO sports (
    name,
    normalized_name,
    max_players,
    match_duration,
    resolution_methods
)
VALUES
    (
        'Fútbol',
        'futbol',
        11,
        90,
        '[{"code":"penalty","name":"penales"},{"code":"overtime","name":"tiempo extra"}]'::jsonb
    ),
    (
        'Básquet',
        'basquet',
        5,
        40,
        '[{"code":"overtime","name":"tiempo extra"}]'::jsonb
    )
ON CONFLICT (normalized_name) DO UPDATE
SET match_duration = EXCLUDED.match_duration,
    resolution_methods = EXCLUDED.resolution_methods;

COMMIT;
