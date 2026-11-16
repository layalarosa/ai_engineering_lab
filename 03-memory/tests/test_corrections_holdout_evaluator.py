import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("holdout_evaluator", ROOT / "experiments/corrections/evaluate_holdout.py")
evaluator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluator)


def row(name, relevant, stale, quality, resoluble, actual, expected):
    return {"id": name, "relevantes": relevant, "desactualizados": stale,
            "calidad_metadatos": quality, "resoluble": resoluble,
            "metodos": {"estructurada": {"estado": actual[0], "posiciones": actual[1]}},
            "resultado_esperado": {"estado": expected[0], "posiciones": expected[1]}}


class HoldoutEvaluatorTests(unittest.TestCase):
    def test_criteria_detect_stale_selection_and_error_in_no_answer(self):
        rows = [
            row("stale", [1], [2], "correctos", True, ("seleccionado", [2]), ("seleccionado", [1])),
            row("error", [], [], "correctos", False, ("entrada_invalida", []), ("sin_evidencia_registrada", [])),
            row("abstain", [1], [], "correctos", False, ("evidencia_ambigua", []), ("evidencia_ambigua", [])),
            row("invalid", [1], [], "invalidos", False, ("entrada_invalida", []), ("entrada_invalida", [])),
        ]
        criteria = evaluator.evaluate_criteria(rows, True, True)
        self.assertEqual(criteria["evidencia_vigente_con_metadatos_correctos"]["incumplen"], ["stale"])
        self.assertEqual(criteria["sin_salida_en_casos_sin_respuesta"]["incumplen"], ["error"])
        self.assertTrue(criteria["estados_de_abstencion_y_errores"]["cumple"])

    def test_empty_groups_are_not_reported_as_passing(self):
        criteria = evaluator.evaluate_criteria([], False, True)
        self.assertFalse(criteria["evidencia_vigente_con_metadatos_correctos"]["cumple"])
        self.assertFalse(criteria["sin_salida_en_casos_sin_respuesta"]["cumple"])
        self.assertFalse(criteria["limites_y_ausencia_de_mutacion"]["cumple"])

    def test_evaluator_runs_on_existing_development_data_without_changes(self):
        snapshots, queries, judgments, digest = evaluator.frozen.load_development()
        report = evaluator.evaluate(snapshots, queries, judgments, digest)
        self.assertEqual(report["casos"], 24)
        self.assertEqual(report["metodos"]["estructurada"]["seleccion_util"]["numerador"], 11)
        self.assertTrue(report["criterios"]["limites_y_ausencia_de_mutacion"]["cumple"])


if __name__ == "__main__":
    unittest.main()
