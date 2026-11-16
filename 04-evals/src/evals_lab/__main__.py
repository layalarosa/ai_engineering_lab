"""Ejecuta una evaluación offline y emite un único informe JSON."""
import argparse
import json
import os
import sys

from .cases import InputError, load_cases, load_outputs
from .evaluate import evaluate


class _Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise InputError("Argumentos inválidos; usa --cases y --outputs.")


def main(argv=None):
    parser = _Parser(prog="evals_lab", description="Evalúa respuestas registradas por coincidencia exacta.",
                     allow_abbrev=False)
    parser.add_argument("--cases", required=True, help="archivo JSONL de casos")
    parser.add_argument("--outputs", required=True, help="archivo JSONL de respuestas")
    try:
        args = parser.parse_args(argv)
        cases, dataset_hash = load_cases(args.cases)
        outputs, outputs_hash = load_outputs(args.outputs)
        report = evaluate(cases, outputs, dataset_sha256=dataset_hash, outputs_sha256=outputs_hash)
        encoded = (json.dumps(report, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
                              allow_nan=False) + "\n").encode("ascii")
        written = sys.stdout.buffer.write(encoded)
        if written != len(encoded):
            raise OSError("incomplete output")
        sys.stdout.buffer.flush()
        return 0 if report["failed"] == 0 and report["errors"] == 0 else 1
    except BrokenPipeError:
        # Evita un segundo fallo al vaciar stdout durante el cierre de Python.
        try:
            null_fd = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(null_fd, sys.stdout.fileno())
            finally:
                os.close(null_fd)
        except OSError:
            pass
        print("Error de evaluación: no se pudo escribir el informe.", file=sys.stderr)
        return 2
    except InputError as exc:
        print(f"Error de evaluación: {exc}", file=sys.stderr)
        return 2
    except Exception:
        print("Error de evaluación: no se pudo completar la evaluación.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
