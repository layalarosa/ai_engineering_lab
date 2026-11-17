"""Convierte un informe RAG en respuestas y fragmentos para revisar sustento."""
import argparse
import json
import os
import sys

from .cases import MAX_FILE_BYTES, MAX_LINE_BYTES, MAX_RECORDS, InputError, _read_jsonl
from .grounding import MAX_ITEMS, _id, _text, _validate_case, _validate_output
from .rag_adapter import _load_report


def convert(report, cases):
    """Valida el informe y conserva cada fuente y su texto, en orden."""
    if not isinstance(report, dict):
        raise InputError("Informe RAG inválido.")
    limit, records = report.get("limit"), report.get("cases")
    if (type(limit) is not int or not 1 <= limit <= MAX_ITEMS or
            not isinstance(records, list) or len(records) > MAX_RECORDS):
        raise InputError("Informe RAG o límite inválido.")
    known = {case["id"]: case for case in cases}
    converted = {}
    for record in records:
        if not isinstance(record, dict):
            raise InputError("Registro RAG inválido.")
        case_id = record.get("id")
        try:
            _id(case_id)
        except ValueError:
            raise InputError("ID de caso RAG inválido.") from None
        if case_id not in known or case_id in converted:
            raise InputError("ID de caso RAG duplicado o desconocido.")
        if record.get("question") != known[case_id]["input"]:
            raise InputError("Pregunta RAG incompatible.")
        status, answer, sources = record.get("status"), record.get("answer"), record.get("sources")
        if (status not in ("answered", "no_context") or not isinstance(sources, list) or
                len(sources) > limit or (status == "no_context") != (len(sources) == 0)):
            raise InputError("Respuesta RAG inválida.")
        try:
            _text(answer, nonblank=True)
            result_sources = []
            for index, source in enumerate(sources, 1):
                if not isinstance(source, dict) or source.get("label") != f"[{index}]":
                    raise ValueError
                result_sources.append({"source_id": f"s{index}", "document_id": source.get("document_id"),
                                       "chunk_index": source.get("chunk_index"), "text": source.get("text")})
            result = {"id": case_id, "answer": answer, "sources": result_sources}
            _validate_output(result)
        except (ValueError, UnicodeError):
            raise InputError("Respuesta o fuente RAG inválida.") from None
        converted[case_id] = result
    return [converted[case["id"]] for case in cases if case["id"] in converted]


def serialize(records):
    """Genera JSONL acotado antes de publicar cualquier contenido."""
    output = bytearray()
    for record in records:
        line = json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode("ascii")
        if len(line) > MAX_LINE_BYTES or len(output) + len(line) + 1 > MAX_FILE_BYTES:
            raise InputError("La salida supera los límites permitidos.")
        output.extend(line)
        output.append(10)
    return bytes(output)


class _Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise InputError("Argumentos inválidos; usa --report y --cases.")


def main(argv=None):
    parser = _Parser(prog="evals_lab.rag_grounding_adapter",
                     description="Convierte fuentes RAG para revisión de sustento.", allow_abbrev=False)
    parser.add_argument("--report", required=True)
    parser.add_argument("--cases", required=True)
    try:
        args = parser.parse_args(argv)
        cases, _ = _read_jsonl(args.cases, kind="casos", keys={"id", "input"},
                               allow_empty=False, validate=_validate_case)
        encoded = serialize(convert(_load_report(args.report), cases))
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
