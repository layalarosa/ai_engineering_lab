-- Ejecutar con psql y un rol administrador, únicamente en una base dedicada.
-- Requiere que rag_runtime ya exista como LOGIN sin privilegios ni membresías.
\set ON_ERROR_STOP on
BEGIN;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rag_runtime' AND rolcanlogin
        AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls) THEN
        RAISE EXCEPTION 'rag_runtime debe ser LOGIN sin privilegios administrativos';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_auth_members WHERE member = 'rag_runtime'::regrole) THEN
        RAISE EXCEPTION 'rag_runtime no debe tener membresías';
    END IF;
    IF has_schema_privilege('rag_runtime', 'public', 'CREATE') OR
       has_database_privilege('rag_runtime', current_database(), 'CREATE') OR
       (SELECT pg_get_userbyid(datdba) = 'rag_runtime' FROM pg_database WHERE datname = current_database()) OR
       EXISTS (SELECT 1 FROM pg_class WHERE oid IN ('public.rag_models'::regclass, 'public.rag_chunks'::regclass)
               AND pg_get_userbyid(relowner) = 'rag_runtime') THEN
        RAISE EXCEPTION 'rag_runtime no debe ser propietario ni poder crear en public';
    END IF;
END $$;
REVOKE ALL ON TABLE public.rag_models, public.rag_chunks FROM rag_runtime;
GRANT USAGE ON SCHEMA public TO rag_runtime;
GRANT SELECT, INSERT ON TABLE public.rag_models TO rag_runtime;
GRANT SELECT, INSERT, DELETE ON TABLE public.rag_chunks TO rag_runtime;
DO $$
BEGIN
    IF has_table_privilege('rag_runtime', 'public.rag_models', 'UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER') OR
       has_table_privilege('rag_runtime', 'public.rag_chunks', 'UPDATE, TRUNCATE, REFERENCES, TRIGGER') THEN
        RAISE EXCEPTION 'rag_runtime tiene permisos adicionales efectivos; revisa PUBLIC y otras concesiones';
    END IF;
END $$;
COMMIT;
