"""Demostración sin red ni dependencias: historial y ventana reciente."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from memory_lab.memory import ConversationMemory


def main():
    memory = ConversationMemory()
    memory.append("user", "Uso Python")
    memory.append("assistant", "Entendido.")
    memory.append("user", "Uso C#")
    print(json.dumps(memory.context(max_chars=16, max_messages=2), ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
