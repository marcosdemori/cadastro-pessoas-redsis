"""Testa a camada de rede contra um servidor HTTP local.

Cobre o que nao da para verificar com a API publica sem depender da internet:
Retry-After em segundos e como data HTTP, recuo exponencial, o ritmo adaptativo
por provedor e a interrupcao imediata quando o operador manda parar.
"""

import email.utils
import http.server
import json
import os
import socket
import sys
import threading
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))

import cnpj_core as core  # noqa: E402

ESTADO = {"chamadas": [], "modo": "ok", "saida": "nao-executou"}

CORPO_OK = {
    "razao_social": "EMPRESA FAKE LTDA",
    "estabelecimento": {
        "nome_fantasia": "FAKE", "situacao_cadastral": "Ativa", "logradouro": "RUA A",
        "numero": "1", "bairro": "CENTRO", "cep": "01001000", "ddd1": "11",
        "telefone1": "33334444", "email": "a@b.com", "tipo": "Matriz",
        "cidade": {"nome": "Sao Paulo", "ibge_id": 3550308}, "estado": {"sigla": "SP"},
        "atividade_principal": {"id": "4712100"}, "inscricoes_estaduais": [],
    },
}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        ESTADO["chamadas"].append((time.monotonic(), self.path))
        modo = ESTADO["modo"]

        if modo == "429_data" and len(ESTADO["chamadas"]) == 1:
            # formatdate trunca para o segundo, entao a espera real fica entre
            # 3s e 4s; a verificacao usa margem larga de proposito
            self.send_response(429)
            self.send_header("Retry-After", email.utils.formatdate(time.time() + 4, usegmt=True))
            self.end_headers()
            self.wfile.write(b"{}")
            return
        if modo in ("429_sempre", "500"):
            self.send_response(429 if modo == "429_sempre" else 500)
            self.end_headers()
            self.wfile.write(b"{}")
            return

        corpo = json.dumps(CORPO_OK).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)


class RespostaFake:
    def __init__(self, retry_after):
        self.headers = {"Retry-After": retry_after} if retry_after else {}


def subir_servidor():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        porta = s.getsockname()[1]
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", porta), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{porta}/"


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

    try:
        # Retry-After como data HTTP: antes virava ValueError e falha de rede
        ESTADO.update(chamadas=[], modo="429_data")
        cfg = core.Config(delay=0, tentativas=2, estrategia="cnpjws").normalizar()
        t0 = time.monotonic()
        r = core.consultar("00000000000191", cfg)
        dur = time.monotonic() - t0
        checar("Retry-After em data HTTP é respeitado",
               r.status == "OK" and 2.5 <= dur <= 8.0,
               f"status={r.status} dur={dur:.1f}s erro={r.erro[:60]}")

        checar("Retry-After numérico", core.segundos_retry_after(RespostaFake("7"), 1, 10) == 7.0)
        quando = email.utils.formatdate(time.time() + 30, usegmt=True)
        seg = core.segundos_retry_after(RespostaFake(quando), 1, 10)
        checar("Retry-After como data vira ~30s", 25 <= seg <= 31, f"{seg}")
        checar("Retry-After ilegível cai no recuo", core.segundos_retry_after(RespostaFake("amanha"), 1, 10) == 10.0)

        recuo = [core.segundos_retry_after(RespostaFake(None), n, 10) for n in (1, 2, 3, 4, 5)]
        checar("recuo exponencial 10/20/40/80/120", recuo == [10.0, 20.0, 40.0, 80.0, 120.0], str(recuo))
        checar("intervalo abaixo de 10s usa piso de 10s", core.segundos_retry_after(RespostaFake(None), 1, 2) == 10.0)

        agenda = core.Agenda(1)
        agenda.penalizar("cnpjws", 40)
        checar("429 eleva o intervalo do provedor", agenda.intervalo("cnpjws") == 40.0)
        checar("penalidade não vaza para outro provedor", agenda.intervalo("brasilapi") == 1.0)
        agenda.premiar("cnpjws")
        checar("sucesso reduz o acréscimo pela metade", agenda.intervalo("cnpjws") == 20.0)
        for _ in range(10):
            agenda.premiar("cnpjws")
        checar("acréscimo volta ao intervalo base", agenda.intervalo("cnpjws") == 1.0)

        # Parar durante a espera de um 429
        ESTADO.update(chamadas=[], modo="429_sempre", saida="nao-executou")
        cfg = core.Config(delay=0, tentativas=3, estrategia="cnpjws").normalizar()
        flag = threading.Event()
        t0 = time.monotonic()
        th = threading.Thread(target=lambda: ESTADO.update(
            saida=core.consultar("00000000000191", cfg, parar=flag.is_set)))
        th.start()
        time.sleep(1.0)
        flag.set()
        th.join(timeout=15)
        dur = time.monotonic() - t0
        checar("Parar interrompe a espera de 429",
               not th.is_alive() and dur < 4.0 and ESTADO["saida"] is None,
               f"viva={th.is_alive()} dur={dur:.1f}s saida={ESTADO['saida']}")

        # Fallback nao deve somar o intervalo de um provedor no outro
        ESTADO.update(chamadas=[], modo="500")
        cfg = core.Config(delay=5, tentativas=1, estrategia="auto").normalizar()
        t0 = time.monotonic()
        r = core.consultar("00000000000191", cfg)
        dur = time.monotonic() - t0
        checar("fallback entre 3 APIs não soma 3x o intervalo",
               r.status == "ERRO" and dur < 2.0, f"dur={dur:.1f}s")

        # O mesmo provedor, em sequencia, continua respeitando o intervalo
        ESTADO.update(chamadas=[], modo="ok")
        agenda = core.Agenda(1.5)
        cfg = core.Config(delay=1.5, tentativas=1, estrategia="cnpjws").normalizar()
        t0 = time.monotonic()
        core.consultar("00000000000191", cfg, agenda=agenda)
        core.consultar("33000167000101", cfg, agenda=agenda)
        dur = time.monotonic() - t0
        checar("mesmo provedor respeita o intervalo", 1.4 <= dur <= 3.0, f"dur={dur:.1f}s")
    finally:
        core.API_PROVIDERS = originais
        srv.shutdown()

    print(f"\ncamada de rede: {len(falhas)} falha(s)")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
