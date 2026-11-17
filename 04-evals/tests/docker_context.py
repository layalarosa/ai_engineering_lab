"""Verifica en un contexto sintético que no se empaqueten archivos ajenos."""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
FILES = ("__init__.py", "__main__.py", "cases.py", "completeness.py", "evaluate.py", "grounding.py", "live_metrics.py", "rag_adapter.py",
         "rag_grounding_adapter.py", "retrieval.py", "security.py", "security_capture.py", "security_controls.py", "security_expanded_simulation.py",
         "security_orchestration_sim.py", "security_simulation.py")


def check():
    with tempfile.TemporaryDirectory(prefix="evals-context-test-") as raw:
        folder = Path(raw).resolve()
        if folder.parent != Path(tempfile.gettempdir()).resolve() or not folder.name.startswith("evals-context-test-"):
            raise RuntimeError("La ruta temporal de pruebas no es válida.")
        shutil.copy2(ROOT / "Dockerfile", folder / "Dockerfile")
        shutil.copy2(ROOT / ".dockerignore", folder / ".dockerignore")
        package = folder / "src" / "evals_lab"
        package.mkdir(parents=True)
        for name in FILES:
            shutil.copy2(ROOT / "src" / "evals_lab" / name, package / name)
        (package / ".env.synthetic").write_text("synthetic", encoding="utf-8")
        (package / "private.synthetic").write_text("synthetic", encoding="utf-8")
        (folder / "src" / "unrelated.py").write_text("synthetic", encoding="utf-8")
        (package / "__pycache__").mkdir()
        (package / "__pycache__" / "synthetic.pyc").write_bytes(b"synthetic")
        image = "ai-engineering-lab-evals:context-check-" + uuid4().hex
        built = False
        try:
            subprocess.run(["docker", "build", "--pull=false", "-t", image, str(folder)],
                           capture_output=True, check=True, timeout=120)
            built = True
            code = ("import json,os; "
                    "print(json.dumps(sorted(os.path.relpath(os.path.join(d,f),'/app') "
                    "for d,_,fs in os.walk('/app') for f in fs)))")
            result = subprocess.run(["docker", "run", "--rm", "--network", "none", "--read-only",
                                     "--entrypoint", "python", image, "-S", "-c", code],
                                    capture_output=True, check=True, timeout=30)
            expected = ["src/evals_lab/" + name for name in FILES]
            if json.loads(result.stdout) != expected:
                raise RuntimeError("La imagen incluye archivos ajenos a los módulos previstos.")
            print("Contexto verificado: archivos ajenos y cachés excluidos.")
        finally:
            if built:
                subprocess.run(["docker", "image", "rm", image], capture_output=True, check=True, timeout=30)


if __name__ == "__main__":
    try:
        check()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        print("No se completó la comprobación del contexto Docker.", file=sys.stderr)
        raise SystemExit(1)
