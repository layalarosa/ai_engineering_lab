"""Convierte informes offline de RAG en listas de documentos para Evals."""
import argparse
import json
import math
import os
import sys
from pathlib import Path

from .cases import MAX_FILE_BYTES, MAX_RECORDS, InputError, _object_without_duplicates, _read_jsonl
from .retrieval import MAX_DOCUMENTS, _case_id, _document_id, _validate_case, _validate_corpus


def _finite_float(value):
    if len(value) > 128:
        raise ValueError("number too long")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite number")
    return number


def _bounded_int(value):
    if len(value) > 128:
        raise ValueError("number too long")
    return int(value)


def _reject_constant(_value):
    raise ValueError("non-finite number")


def _load_report(path):
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES or raw.startswith(b"\xef\xbb\xbf"):
            raise ValueError
        report = json.loads(raw.decode("utf-8"), object_pairs_hook=_object_without_duplicates,
                            parse_int=_bounded_int, parse_float=_finite_float, parse_constant=_reject_constant)
        if not isinstance(report, dict):
            raise ValueError
        return report
    except (OSError, ValueError, UnicodeError, RecursionError, OverflowError):
        raise InputError("El informe RAG no es válido o no se pudo leer.") from None


def convert(report, corpus, cases, *, k):
    """Convierte datos ya leídos y valida todas las referencias antes de devolver resultados."""
    if type(k) is not int or not 1 <= k <= MAX_DOCUMENTS:
        raise InputError("El valor de k debe ser un entero entre 1 y 20.")
    if not isinstance(report, dict):
        raise InputError("Informe RAG inválido.")
    limit, records = report.get("limit"), report.get("cases")
    if (type(limit) is not int or not 1 <= limit <= MAX_DOCUMENTS or k > limit or
            not isinstance(records, list) or len(records) > MAX_RECORDS):
        raise InputError("Informe RAG o límite incompatible.")
    known_documents = {row["document_id"] for row in corpus}
    cases_by_id = {row["id"]: row for row in cases}
    if any(not set(row["relevant_document_ids"]) <= known_documents for row in cases):
        raise InputError("Los casos contienen documentos ajenos al corpus.")
    converted = {}
    for row in records:
        if not isinstance(row, dict):
            raise InputError("Registro RAG inválido.")
        case_id = row.get("id")
        try:
            _case_id(case_id)
        except ValueError:
            raise InputError("ID de caso RAG inválido.") from None
        if case_id in converted or case_id not in cases_by_id:
            raise InputError("ID de caso RAG duplicado o desconocido.")
        case = cases_by_id[case_id]
        relevant = case["relevant_document_ids"]
        if (row.get("question") != case["input"] or "expected_document_id" not in row or
                (row["expected_document_id"] is None and relevant) or
                (row["expected_document_id"] is not None and
                 (not isinstance(row["expected_document_id"], str) or row["expected_document_id"] not in relevant))):
            raise InputError("Pregunta o etiqueta RAG incompatible.")
        answer, status, sources = row.get("answer"), row.get("status"), row.get("sources")
        if (not isinstance(answer, str) or not answer.strip() or
                status not in ("answered", "no_context") or not isinstance(sources, list) or
                len(sources) > limit or (status == "no_context") != (len(sources) == 0)):
            raise InputError("Respuesta RAG inválida.")
        ordered, seen = [], set()
        for source in sources:
            if not isinstance(source, dict):
                raise InputError("Fuente RAG inválida.")
            document_id = source.get("document_id")
            try:
                _document_id(document_id)
            except ValueError:
                raise InputError("Fuente RAG inválida.") from None
            if document_id not in known_documents:
                raise InputError("La fuente RAG no existe en el corpus.")
            if document_id not in seen:
                ordered.append(document_id)
                seen.add(document_id)
        converted[case_id] = {"id": case_id, "retrieved_document_ids": ordered}
    return [converted[case["id"]] for case in cases if case["id"] in converted]


class _Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise InputError("Argumentos inválidos; usa --report, --corpus, --cases y --k.")


def main(argv=None):
    parser = _Parser(prog="evals_lab.rag_adapter", description="Convierte un informe RAG ya registrado.",
                     allow_abbrev=False)
    parser.add_argument("--report", required=True)
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--k", type=int, default=3)
    try:
        args = parser.parse_args(argv)
        corpus, _ = _read_jsonl(args.corpus, kind="corpus", keys={"document_id"},
                                allow_empty=False, id_field="document_id", validate=_validate_corpus)
        cases, _ = _read_jsonl(args.cases, kind="casos", keys={"id", "input", "relevant_document_ids"},
                               allow_empty=False, validate=_validate_case)
        converted = convert(_load_report(args.report), corpus, cases, k=args.k)
        encoded = b"".join((json.dumps(row, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False) + "\n").encode("ascii") for row in converted)
        if sys.stdout.buffer.write(encoded) != len(encoded):
            raise OSError("incomplete output")
        sys.stdout.buffer.flush()
        return 0
    except BrokenPipeError:
        try:
            null_fd = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(null_fd, sys.stdout.fileno())
            finally:
                os.close(null_fd)
        except OSError:
            pass
        print("Error de conversión: no se pudo escribir la salida.", file=sys.stderr)
        return 2
    except InputError as exc:
        print(f"Error de conversión: {exc}", file=sys.stderr)
        return 2
    except Exception:
        print("Error de conversión: no se pudo completar la conversión.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
