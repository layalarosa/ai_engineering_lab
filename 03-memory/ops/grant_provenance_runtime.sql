-- Solo para el rol sintético provenance_runtime creado por el fixture temporal.
GRANT USAGE ON SCHEMA memory_provenance_lab TO provenance_runtime;
GRANT SELECT ON memory_provenance_lab.schema_version TO provenance_runtime;
GRANT SELECT, INSERT, DELETE ON memory_provenance_lab.sessions, memory_provenance_lab.messages,
    memory_provenance_lab.statements, memory_provenance_lab.current_statements,
    memory_provenance_lab.operations TO provenance_runtime;
-- FOR UPDATE requiere UPDATE en una columna; clear necesita actualizar generation.
GRANT UPDATE (generation) ON memory_provenance_lab.sessions TO provenance_runtime;
GRANT UPDATE (statement_id) ON memory_provenance_lab.current_statements TO provenance_runtime;
