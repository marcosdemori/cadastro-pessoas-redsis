"""Gera os dois executaveis.

    python build.py            roda os testes e empacota
    python build.py --sem-teste    empacota direto

Os testes rodam antes do empacotamento de proposito: um build que passou na
bateria e o unico que vale distribuir.
"""

import argparse
import os
import shutil
import subprocess
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
ICONE = os.path.join(AQUI, "icone.ico")

INDEX = os.path.join(AQUI, "index.html")

# (nome do exe, script, modo de console, arquivos extras a embutir)
ALVOS = [
    ("ConsultaCNPJ", "gui.py", "--noconsole", []),
    ("ConsultaCNPJ-CLI", "CNPJ.py", "--console", []),
    ("PonteFirebird", "ponte.py", "--console", [INDEX]),
]


def rodar(cmd, titulo):
    print(f"\n=== {titulo} ===", flush=True)
    r = subprocess.run(cmd, cwd=AQUI)
    return r.returncode == 0


def garantir_icone():
    if os.path.exists(ICONE):
        return True
    try:
        import icone

        icone.main()
        return True
    except Exception as exc:
        print(f"AVISO: não foi possível gerar o ícone ({exc}); os exes sairão com o ícone padrão.")
        return False


def main():
    p = argparse.ArgumentParser(description="Empacota a interface gráfica e a linha de comando.")
    p.add_argument("--sem-teste", action="store_true", help="pula a bateria de testes")
    args = p.parse_args()

    if not args.sem_teste:
        if not rodar([sys.executable, os.path.join(AQUI, "tests", "run.py")], "testes"):
            print("\nABORTADO: os testes falharam. Use --sem-teste para empacotar assim mesmo.")
            return 1

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("ERRO: PyInstaller não instalado. Rode: python -m pip install pyinstaller")
        return 1

    tem_icone = garantir_icone()

    for pasta in ("build", "dist"):
        shutil.rmtree(os.path.join(AQUI, pasta), ignore_errors=True)
    for nome, *_ in ALVOS:
        spec = os.path.join(AQUI, f"{nome}.spec")
        if os.path.exists(spec):
            os.remove(spec)

    for nome, script, modo, extras in ALVOS:
        cmd = [sys.executable, "-m", "PyInstaller", "--onefile", modo,
               "--name", nome, "--clean", "--noconfirm"]
        if tem_icone:
            # --icon define o icone do .exe; --add-data embute o arquivo para a
            # janela do Tk poder usar o mesmo icone
            cmd += ["--icon", ICONE, "--add-data", f"{ICONE}{os.pathsep}."]
        for extra in extras:
            cmd += ["--add-data", f"{extra}{os.pathsep}."]
        cmd.append(os.path.join(AQUI, script))
        if not rodar(cmd, f"empacotando {nome}.exe"):
            print(f"\nFALHOU ao gerar {nome}.exe")
            return 1

    print("\n" + "=" * 60)
    for nome, *_ in ALVOS:
        caminho = os.path.join(AQUI, "dist", f"{nome}.exe")
        tamanho = os.path.getsize(caminho) / (1024 * 1024)
        print(f"{caminho}  ({tamanho:.1f} MB)")

    print(
        "\nOs executáveis não são assinados: o SmartScreen avisa na primeira execução.\n"
        "Com um certificado de code signing, assine com:\n"
        '  signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /f certificado.pfx /p SENHA dist\\*.exe'
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
