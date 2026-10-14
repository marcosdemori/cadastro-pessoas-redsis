"""Ponte local entre o index.html e o Firebird.

O navegador nao fala com o Firebird. Este servidor resolve isso: ele serve o
proprio index.html em http://127.0.0.1:8777/ e expoe quatro rotas JSON que leem
a tabela PESSOAS pelo modulo firebird_db.

Uso:

    python ponte.py                 # abre o navegador em 127.0.0.1:8777
    python ponte.py --porta 9000
    python ponte.py --sem-navegador

Limites de seguranca:

* escuta somente em 127.0.0.1, nunca na rede;
* cada execucao gera um token novo, injetado no HTML servido; as rotas /api
  recusam chamadas sem o token, entao outra pagina aberta no navegador nao
  consegue usar a ponte;
* somente SELECT chega ao banco (ver firebird_db.montar_select).
"""

import argparse
import json
import os
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cnpj_core as core
import firebird_db as fb


TOKEN = secrets.token_urlsafe(24)
LIMITE_CORPO = 64 * 1024


def _index_html():
    caminho = core.recurso("index.html")
    if not os.path.exists(caminho):
        raise FileNotFoundError(f"index.html nao encontrado em {caminho}")
    with open(caminho, encoding="utf-8") as f:
        html = f.read()
    injecao = f'<script>window.PONTE_TOKEN="{TOKEN}";</script>'
    if "</head>" in html:
        return html.replace("</head>", injecao + "</head>", 1)
    return injecao + html


class Handler(BaseHTTPRequestHandler):
    server_version = "PonteRedsis/1.0"

    # ---------------------------------------------------------- utilitarios

    def log_message(self, formato, *args):
        if self.server.verboso:
            sys.stderr.write("%s - %s\n" % (self.address_string(), formato % args))

    def _responder(self, codigo, corpo, tipo="application/json; charset=utf-8"):
        dados = corpo if isinstance(corpo, bytes) else corpo.encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(dados)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(dados)

    def _json(self, codigo, objeto):
        self._responder(codigo, json.dumps(objeto, ensure_ascii=False))

    def _erro(self, codigo, mensagem):
        self._json(codigo, {"erro": mensagem})

    def _autorizado(self):
        return secrets.compare_digest(self.headers.get("X-Ponte-Token", ""), TOKEN)

    def _ler_corpo(self):
        """Consome o corpo da requisicao. Sempre chamado antes de responder a um
        POST: deixar bytes no socket faz o cliente levar connection reset."""
        try:
            tamanho = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return b"", "Content-Length invalido."
        if tamanho <= 0:
            return b"", None
        if tamanho > LIMITE_CORPO:
            # le e descarta para nao travar o cliente, mas recusa o conteudo
            restante = tamanho
            while restante > 0:
                pedaco = self.rfile.read(min(restante, 8192))
                if not pedaco:
                    break
                restante -= len(pedaco)
            return b"", "Corpo da requisicao grande demais."
        return self.rfile.read(tamanho), None

    def _corpo_json(self, bruto):
        if not bruto:
            return {}
        try:
            dados = json.loads(bruto.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"JSON invalido: {exc}") from exc
        if not isinstance(dados, dict):
            raise ValueError("Esperava um objeto JSON.")
        return dados

    def _conexao(self, dados):
        validos = {c for c in fb.Conexao.__dataclass_fields__}
        return fb.Conexao(**{k: v for k, v in dados.items() if k in validos}).normalizar()

    # ---------------------------------------------------------------- rotas

    def do_GET(self):
        rota = self.path.split("?", 1)[0]
        if rota in ("/", "/index.html"):
            try:
                self._responder(200, _index_html(), "text/html; charset=utf-8")
            except OSError as exc:
                self._erro(500, str(exc))
            return
        if rota == "/api/status":
            if not self._autorizado():
                self._erro(403, "Token ausente ou invalido. Recarregue a pagina pela ponte.")
                return
            salvo = fb.carregar_conexao()
            salvo.senha = ""
            self._json(200, {
                "driver": fb.driver_disponivel(),
                "conexao": {k: v for k, v in vars(salvo).items() if k != "senha"},
                "colunas": fb.COLUNAS_PESSOAS,
                "charsets": fb.CHARSETS,
                "bibliotecas": fb.localizar_clientes(),
                "bits": fb.bits_do_python(),
            })
            return
        self._erro(404, "Rota desconhecida.")

    def do_POST(self):
        rota = self.path.split("?", 1)[0]
        bruto, falha = self._ler_corpo()
        if rota not in ("/api/testar", "/api/pessoas", "/api/cnpjs"):
            self._erro(404, "Rota desconhecida.")
            return
        if not self._autorizado():
            self._erro(403, "Token ausente ou invalido. Recarregue a pagina pela ponte.")
            return
        if falha:
            self._erro(400, falha)
            return
        try:
            dados = self._corpo_json(bruto)
            conexao = self._conexao(dados)
        except (ValueError, TypeError, fb.ErroFirebird) as exc:
            self._erro(400, str(exc))
            return

        try:
            if rota == "/api/testar":
                self._json(200, fb.testar(conexao))
                return
            if rota == "/api/cnpjs":
                cnpjs, info = fb.ler_cnpjs(conexao)
                if dados.get("lembrar"):
                    try:
                        fb.salvar_conexao(conexao)
                    except OSError:
                        pass
                self._json(200, {"cnpjs": cnpjs, "info": info})
                return
            base, info = fb.ler_base(conexao)
            if dados.get("lembrar"):
                try:
                    fb.salvar_conexao(conexao)
                except OSError:
                    pass
            self._json(200, {"base": base, "info": info})
        except fb.ErroFirebird as exc:
            self._erro(502, str(exc))
        except Exception as exc:  # pragma: no cover - rede/driver
            self._erro(500, f"{exc.__class__.__name__}: {exc}")


def servir(porta=8777, abrir=True, verboso=False):
    servidor = ThreadingHTTPServer(("127.0.0.1", porta), Handler)
    servidor.verboso = verboso
    url = f"http://127.0.0.1:{servidor.server_address[1]}/"
    print(f"Ponte Firebird no ar: {url}")
    print(f"Driver Firebird: {fb.driver_disponivel() or 'NENHUM INSTALADO'}")
    print("Ctrl+C encerra.")
    if abrir:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrando.")
    finally:
        servidor.server_close()


def main(argv=None):
    core.forcar_utf8()
    p = argparse.ArgumentParser(description="Ponte local entre o index.html e o Firebird.")
    p.add_argument("--porta", type=int, default=8777, help="porta local (padrao 8777)")
    p.add_argument("--sem-navegador", action="store_true", help="nao abrir o navegador")
    p.add_argument("--verboso", action="store_true", help="registrar cada requisicao")
    args = p.parse_args(argv)
    servir(porta=args.porta, abrir=not args.sem_navegador, verboso=args.verboso)


if __name__ == "__main__":
    main()
