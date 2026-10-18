-- The admin handles technical follow-ups, such as which reference-link sites deserve a
-- reader. The first person seeded starts as the admin.
ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0;
UPDATE users SET is_admin = 1 WHERE id = (SELECT MIN(id) FROM users);

-- Reader reminders now go in every admin email, so nothing tracks which were sent.
DELETE FROM settings WHERE key = 'reminded_domains';
