"""Demostración sintética; la política no autentica usuarios reales."""
import json
from uuid import uuid4

from provenance_lab import ProvenanceLab, preview


def main():
    actor = "persona-simulada"
    key = "perfil/idioma_preferido"
    lab = ProvenanceLab({actor})
    session_id = uuid4()
    generation = lab.create_session(actor, session_id)
    first = {"generation": generation, "operation_id": uuid4(), "key": key, "value": "italiano"}
    initial = lab.confirm(actor, session_id, first)
    correction = {"generation": generation, "operation_id": uuid4(), "key": key, "value": "español",
                  "supersedes_statement_id": initial["statement_id"]}
    shown = preview(key, correction["value"])
    updated = lab.confirm(actor, session_id, correction)
    repeated = lab.confirm(actor, session_id, correction)
    print(json.dumps({
        "aviso": "Identidad y autorización simuladas; datos sintéticos.",
        "mensaje_previo_a_confirmar": shown,
        "mensaje_guardado": updated["message"]["content"],
        "correccion_referencia_anterior": updated["supersedes_statement_id"] == initial["statement_id"],
        "reintento_conserva_identificador": repeated["statement_id"] == updated["statement_id"],
        "cantidad_de_mensajes": len(lab.history(actor, session_id, generation)),
        "afirmacion_atribuida_no_verificada": True,
    }, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
