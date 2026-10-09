"""Testa o motor sem rede: leitura da lista, progresso, retomada e geracao.

Usa um servidor HTTP local no lugar das APIs publicas para que o resultado nao
dependa de internet nem de cota.
"""

import http.server
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))

import cnpj_core as core  # noqa: E402

ESTADO = {"falhar": set()}

CORPO = {
    "razao_social": "EMPRESA FAKE LTDA",
    "estabelecimento": {
        "nome_fantasia": "FAKE", "situacao_cadastral": "Ativa", "logradouro": "RUA A",
        "numero": "1", "bairro": "CENTRO", "cep": "01001000", "ddd1": "11",
        "telefone1": "33334444", "email": "a@b.com", "tipo": "Matriz",
        "cidade": {"nome": "Sao Paulo", "ibge_id": 3550308}, "estado": {"sigla": "SP"},
        "atividade_principal": {"id": "4712100"},
        "inscricoes_estaduais": [{"inscricao_estadual": "123456789012", "ativo": True, "estado": {"sigla": "SP"}}],
    },
}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        cnpj = self.path.rsplit("/", 1)[-1]
        if cnpj in ESTADO["falhar"]:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"{}")
            return
        corpo = json.dumps(CORPO).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)


def subir_servidor():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        porta = s.getsockname()[1]
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", porta), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{porta}/"


VALIDOS = ["00000000000191", "33000167000101", "60701190000104"]


def main():
    falhas = []

    def checar(nome, cond, detalhe=""):
        print(("OK    " if cond else "FALHA ") + nome + ("" if cond else f"\n      {detalhe}"))
        if not cond:
            falhas.append(nome)

    srv, url = subir_servidor()
    originais = core.API_PROVIDERS
    core.API_PROVIDERS = {
        "cnpjws": core.Provider("cnpjws", "CNPJ.ws", True, lambda c: url + "ws/" + c),
        "brasilapi": core.Provider("brasilapi", "BrasilAPI", False, lambda c: url + "br/" + c),
        "minhareceita": core.Provider("minhareceita", "Minha Receita", False, lambda c: url + "mr/" + c),
    }
    pasta = tempfile.mkdtemp(prefix="saneamento-teste-")
    core.pasta_trabalho = lambda: pasta

    try:
        # --- leitura da lista de CNPJs
        lista = os.path.join(pasta, "CNPJ.txt")
        with open(lista, "wb") as f:
            f.write("﻿00.000.000/0001-91\n33000167000101;60701190000104\n11111111111111\n".encode("utf-8"))
        validos, invalidos = core.carregar_lista(lista)
        checar("lista com BOM, máscara e separadores variados",
               validos == VALIDOS and invalidos == ["11111111111111"], f"{validos} {invalidos}")

        # TXT em ANSI com acento nao pode derrubar o programa
        ansi = os.path.join(pasta, "ansi.txt")
        with open(ansi, "wb") as f:
            f.write("CNPJs da filial de S\xe3o Paulo\n00000000000191\n".encode("latin-1"))
        try:
            validos, _ = core.carregar_lista(ansi)
            ok, detalhe = validos == ["00000000000191"], str(validos)
        except UnicodeDecodeError as exc:
            ok, detalhe = False, f"UnicodeDecodeError: {exc}"
        checar("TXT salvo em ANSI não derruba a leitura", ok, detalhe)

        # --- lote completo
        ESTADO["falhar"] = set()
        core.limpar_progresso()
        cfg = core.Config(delay=0, tentativas=1, estrategia="cnpjws").normalizar()
        vistos = []
        resultados = core.processar(VALIDOS, cfg, on_resultado=lambda r, i, t: vistos.append(r.cnpj))
        checar("lote consulta todos os pendentes", len(resultados) == 3 and len(vistos) == 3, f"{len(resultados)}/{len(vistos)}")
        checar("progresso gravado em disco", os.path.exists(os.path.join(pasta, core.ARQUIVO_PROGRESSO)))

        # --- retomada: nada de reconsultar quem ja deu certo
        salvos, cfg_salva = core.carregar_progresso()
        checar("progresso restaura resultados e config", len(salvos) == 3 and cfg_salva is not None)
        vistos = []
        core.processar(VALIDOS, cfg, on_resultado=lambda r, i, t: vistos.append(r.cnpj), anteriores=salvos)
        checar("retomada não reconsulta os concluídos", vistos == [], str(vistos))

        # --- so o que falhou volta na proxima rodada
        core.limpar_progresso()
        ESTADO["falhar"] = {"33000167000101"}
        resultados = core.processar(VALIDOS, cfg)
        status = {r.cnpj: r.status for r in resultados}
        checar("registro com falha fica marcado como ERRO", status.get("33000167000101") == "ERRO", str(status))

        ESTADO["falhar"] = set()
        salvos, _ = core.carregar_progresso()
        vistos = []
        resultados = core.processar(VALIDOS, cfg, on_resultado=lambda r, i, t: vistos.append(r.cnpj), anteriores=salvos)
        checar("reconsulta apenas o que falhou", vistos == ["33000167000101"], str(vistos))
        checar("resultado reconsultado substitui o anterior, sem duplicar",
               len(resultados) == 3 and all(r.status == "OK" for r in resultados),
               str([(r.cnpj, r.status) for r in resultados]))

        # --- parar no meio do lote
        core.limpar_progresso()
        cfg_lenta = core.Config(delay=2, tentativas=1, estrategia="cnpjws").normalizar()
        flag = threading.Event()
        feitos = []
        th = threading.Thread(target=lambda: core.processar(
            VALIDOS, cfg_lenta, on_resultado=lambda r, i, t: feitos.append(r.cnpj), parar=flag.is_set))
        th.start()
        time.sleep(2.5)
        flag.set()
        th.join(timeout=20)
        checar("Parar encerra o lote antes do fim", not th.is_alive() and 0 < len(feitos) < 3, f"feitos={feitos}")
        salvos, _ = core.carregar_progresso()
        checar("o que já foi consultado fica salvo", len(salvos) == len(feitos), f"{len(salvos)} vs {len(feitos)}")

        # --- selecao de campo por registro
        core.limpar_progresso()
        ESTADO["falhar"] = set()
        cfg = core.Config(delay=0, tentativas=1, estrategia="cnpjws", tipo_sql="UPDATE").normalizar()
        resultados = core.processar(["00000000000191"], cfg)
        base_map = {"00000000000191": {"CNPJCPF_PESS": "00000000000191", "NOM_PESS": "OUTRO NOME", "FANT_PESS": "OUTRA FANTASIA"}}
        sql_antes = core.gerar_sql_completo(resultados, base_map, cfg)
        checar("UPDATE inclui os dois campos divergentes",
               "NOM_PESS" in sql_antes and "FANT_PESS" in sql_antes)

        resultados[0].campos_selecionados["nome_fantasia"] = False
        sql_depois = core.gerar_sql_completo(resultados, base_map, cfg)
        checar("campo desligado só neste registro sai do UPDATE",
               "NOM_PESS" in sql_depois and "FANT_PESS" not in sql_depois)

        core.salvar_progresso(resultados, cfg)
        salvos, _ = core.carregar_progresso()
        checar("a escolha por registro sobrevive ao progresso",
               salvos[0].campos_selecionados.get("nome_fantasia") is False,
               str(salvos[0].campos_selecionados))

        # --- arquivos de saida
        destino_sql, total = core.salvar_sql(resultados, cfg, base_map)
        destino_csv = core.salvar_csv(resultados, cfg, base_map)
        checar("grava updates.sql e relatorio_cnpj.csv",
               os.path.exists(destino_sql) and os.path.exists(destino_csv) and total == 1,
               f"total={total}")
    finally:
        core.API_PROVIDERS = originais
        srv.shutdown()
        shutil.rmtree(pasta, ignore_errors=True)

    print(f"\nmotor: {len(falhas)} falha(s)")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
