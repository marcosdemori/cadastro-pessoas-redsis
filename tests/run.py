"""Roda toda a bateria de testes.

    python tests/run.py

Sai com codigo diferente de zero se qualquer teste falhar, para servir de porta
antes de gerar os executaveis.
"""

import os
import subprocess
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))

SUITES = [
    ("motor (lista, progresso, retomada, geração)", "test_motor.py"),
    ("camada de rede (429, recuo, agenda, parar)", "test_rede.py"),
    ("paridade com index.html", "test_paridade.py"),
    ("interface gráfica", "test_gui.py"),
    ("Firebird e ponte local", "test_firebird.py"),
]


def main():
    falhas = []
    for titulo, arquivo in SUITES:
        # flush antes de cada subprocesso, senao a saida sai fora de ordem
        print(f"\n=== {titulo} ===", flush=True)
        r = subprocess.run([sys.executable, "-I", os.path.join(AQUI, arquivo)])
        if r.returncode != 0:
            falhas.append(titulo)

    print("\n" + "=" * 60, flush=True)
    if falhas:
        print("FALHOU: " + " | ".join(falhas), flush=True)
        return 1
    print(f"Tudo passou ({len(SUITES)} suítes).", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
