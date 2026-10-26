-- 0002_installation_install_id: the installation identifier used by the
-- events genesis hash (TRD §4.3). Additive; the writer fills it on insert.
ALTER TABLE installation ADD COLUMN install_id TEXT NOT NULL DEFAULT '';
