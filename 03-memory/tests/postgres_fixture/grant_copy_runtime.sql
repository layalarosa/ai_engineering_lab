-- Concesiones exclusivas para el rol de copia del fixture efímero.
GRANT USAGE ON SCHEMA memory_test_fixture, memory_lab, memory_provenance_lab TO provenance_copy_runtime;
GRANT SELECT ON memory_test_fixture.identity, memory_test_fixture.synthetic_sessions TO provenance_copy_runtime;
GRANT SELECT ON memory_lab.sessions, memory_lab.messages TO provenance_copy_runtime;
-- FOR UPDATE requiere UPDATE en alguna columna; no garantiza acceso de solo lectura.
GRANT UPDATE (session_id) ON memory_lab.sessions TO provenance_copy_runtime;
GRANT SELECT ON memory_provenance_lab.schema_version TO provenance_copy_runtime;
GRANT INSERT ON memory_provenance_lab.sessions, memory_provenance_lab.messages TO provenance_copy_runtime;
