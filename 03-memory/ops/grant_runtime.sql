-- El rol memory_runtime debe existir antes de ejecutar este archivo.
-- Solo concede permisos; no elimina privilegios heredados ni configura acceso por usuario.
GRANT USAGE ON SCHEMA memory_lab TO memory_runtime;
GRANT SELECT, INSERT, DELETE ON memory_lab.sessions, memory_lab.messages TO memory_runtime;
-- PostgreSQL requiere UPDATE en al menos una columna para SELECT ... FOR UPDATE.
GRANT UPDATE (session_id) ON memory_lab.sessions TO memory_runtime;
