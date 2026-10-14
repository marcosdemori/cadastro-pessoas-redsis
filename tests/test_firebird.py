"""Testa a leitura da PESSOAS pelo Firebird e a ponte HTTP local.

Nao ha banco Firebird aqui: um driver falso e injetado no lugar de
firebird_db._abrir, para exercitar o mapeamento de colunas, o descarte de
CNPJ invalido e as rotas da ponte sem depender de servidor nem de fbclient.dll.
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))

import cnpj_core as core  # noqa: E402
import firebird_db as fb  # noqa: E402
import ponte  # noqa: E402


COLUNAS_FAKE = [
    "COD_PESS", "CNPJCPF_PESS", "NOM_PESS", "FANT_PESS", "IE_PESS",
    "END_PESS", "NUM_PESS", "UF_PESS", "CEP_PESS", "SIT_PESS",
]

LINHAS_FAKE = [
    ("1", "11.222.333/0001-81", "EMPRESA UM LTDA", "UM", "123456789012",
     "RUA A", "10", "SP", "01001-000", "A"),
    ("2", "44555666000177", "EMPRESA DOIS LTDA", None, "",
     "RUA B", "20", "RJ", "20000000", "A"),
    ("3", "12345678901", "PESSOA FISICA", "", "", "RUA C", "30", "MG", "30000000", "A"),
    ("4", "", "SEM CNPJ", "", "", "", "", "", "", ""),
]


class CursorFake:
    def __init__(self, dono):
        self.dono = dono
        self.linhas = []

    def execute(self, sql, params=None):
        self.dono.sqls.append(sql)
        if "RDB$RELATION_FIELDS" in sql:
            self.linhas = [(c,) for c in COLUNAS_FAKE]
            return
        colunas = [c.strip() for c in sql.split("SELECT", 1)[1].split("FROM", 1)[0].split(",")]
        if colunas and colunas[0].upper().startswith("FIRST"):
            colunas[0] = colunas[0].split(None, 2)[2]
        indices = [COLUNAS_FAKE.index(c) for c in colunas]
        self.linhas = [tuple(linha[i] for i in indices) for linha in LINHAS_FAKE]

    def fetchall(self):
        linhas, self.linhas = self.linhas, []
        return linhas

    def fetchmany(self, n):
        lote, self.linhas = self.linhas[:n], self.linhas[n:]
        return lote


class ConexaoFake:
    def __init__(self):
        self.sqls = []
        self.fechada = False

    def cursor(self):
        return CursorFake(self)

    def close(self):
        self.fechada = True


def main():
    falhas = []
    abertas = []

    def checar(nome, cond, detalhe=""):
        print(("OK    " if cond else "FALHA ") + nome + ("" if cond else f"\n      {detalhe}"))
        if not cond:
            falhas.append(nome)

    original = fb._abrir

    def abrir_fake(cfg):
        cfg.normalizar()
        if not cfg.banco:
            raise fb.ErroFirebird("Informe o caminho (ou alias) do banco .fdb.")
        if cfg.senha == "errada":
            raise fb.ErroFirebird("Your user name and password are not defined.")
        c = ConexaoFake()
        abertas.append(c)
        return c

    fb._abrir = abrir_fake
    pasta = tempfile.mkdtemp(prefix="firebird-teste-")
    trabalho = core.pasta_trabalho
    core.pasta_trabalho = lambda: pasta

    try:
        cfg = fb.Conexao(banco="C:/dados/TESTE.FDB", senha="x")

        # --- SQL montado
        sql = fb.montar_select(cfg.normalizar(), ["COD_PESS", "CNPJCPF_PESS"])
        checar("o SELECT filtra CNPJ nulo",
               sql == "SELECT COD_PESS, CNPJCPF_PESS FROM PESSOAS WHERE CNPJCPF_PESS IS NOT NULL", sql)

        com_limite = fb.Conexao(banco="x.fdb", limite=5).normalizar()
        checar("limite vira FIRST n", "FIRST 5" in fb.montar_select(com_limite, ["COD_PESS"]))

        # --- nomes de tabela e coluna so aceitam identificador
        for valor, rotulo in (("PESSOAS; DROP TABLE X", "tabela"), ("A B", "tabela")):
            try:
                fb.Conexao(banco="x.fdb", tabela=valor).normalizar()
                ok = False
            except fb.ErroFirebird:
                ok = True
            checar(f"recusa {rotulo} fora do padrao ({valor!r})", ok)

        try:
            fb.Conexao(banco="x.fdb", coluna_cnpj="X)--").normalizar()
            ok = False
        except fb.ErroFirebird:
            ok = True
        checar("recusa coluna de CNPJ fora do padrao", ok)

        # --- leitura
        base, info = fb.ler_base(fb.Conexao(banco="x.fdb", senha="x"))
        checar("le so os CNPJ validos de 14 digitos", len(base) == 2, str(sorted(base)))
        checar("conta linhas lidas e ignoradas",
               info["lidas"] == 4 and info["ignoradas"] == 2, str(info))
        checar("CNPJ formatado vira so numero", "11222333000181" in base, str(sorted(base)))
        checar("CPF de 11 digitos fica de fora", "12345678901" not in base)
        checar("valor nulo vira string vazia",
               base["44555666000177"]["FANT_PESS"] == "", repr(base["44555666000177"]["FANT_PESS"]))
        checar("campos chegam sem espaco sobrando",
               base["11222333000181"]["NOM_PESS"] == "EMPRESA UM LTDA")
        checar("colunas ausentes na tabela sao ignoradas",
               "EMAIL_PESS" not in info["colunas"] and "COD_MUN" not in info["colunas"],
               str(info["colunas"]))
        checar("a conexao e sempre fechada", all(c.fechada for c in abertas))

        # --- mesmo formato do CSV
        do_csv = core.carregar_base(
            "COD_PESS;CNPJCPF_PESS;NOM_PESS\n1;11.222.333/0001-81;EMPRESA UM LTDA")
        checar("banco e CSV produzem a mesma chave e o mesmo CNPJ",
               set(do_csv) <= set(base)
               and do_csv["11222333000181"]["CNPJCPF_PESS"] == base["11222333000181"]["CNPJCPF_PESS"])

        # --- teste de conexao
        info = fb.testar(fb.Conexao(banco="x.fdb", senha="x"))
        checar("testar lista as colunas que faltam",
               "EMAIL_PESS" in info["colunas_faltando"] and "NOM_PESS" in info["colunas_usadas"],
               str(info))

        try:
            fb.testar(fb.Conexao(banco="x.fdb", senha="x", coluna_cnpj="NAO_EXISTE"))
            ok = False
        except fb.ErroFirebird:
            ok = True
        checar("avisa quando a coluna do CNPJ nao existe", ok)

        # --- biblioteca cliente: arquitetura conferida antes de carregar
        def pe(bits):
            maquina = {32: 0x014C, 64: 0x8664}[bits]
            cab = bytearray(0x50)
            cab[0:2] = b"MZ"
            cab[0x3C:0x40] = (0x40).to_bytes(4, "little")
            cab[0x40:0x44] = b"PE\x00\x00"
            cab[0x44:0x46] = maquina.to_bytes(2, "little")
            return bytes(cab)

        meus = fb.bits_do_python()
        outros = 32 if meus == 64 else 64
        certa = os.path.join(pasta, "fbclient.dll")
        errada = os.path.join(pasta, "errada", "fbclient.dll")
        os.makedirs(os.path.dirname(errada), exist_ok=True)
        open(certa, "wb").write(pe(meus))
        open(errada, "wb").write(pe(outros))

        checar("le a arquitetura do cabecalho PE",
               fb.bits_da_dll(certa) == meus and fb.bits_da_dll(errada) == outros,
               f"{fb.bits_da_dll(certa)} / {fb.bits_da_dll(errada)}")
        checar("arquivo que nao e DLL nao quebra a leitura",
               fb.bits_da_dll(os.path.join(pasta, "nao-existe.dll")) is None)

        try:
            fb._carregar_biblioteca(errada)
            ok, msg = False, "carregou a DLL de arquitetura errada"
        except fb.ErroFirebird as exc:
            msg = str(exc)
            ok = f"{outros} bits" in msg and f"{meus} bits" in msg
        checar("recusa fbclient.dll da arquitetura errada antes de carregar", ok, msg)

        try:
            fb._carregar_biblioteca(os.path.join(pasta, "nao-existe.dll"))
            ok = False
        except fb.ErroFirebird:
            ok = True
        checar("avisa quando a biblioteca informada nao existe", ok)

        checar("a lista de clientes descarta a arquitetura errada",
               all(fb.bits_da_dll(c) in (None, meus) for c in fb.localizar_clientes()))

        # --- config: a senha so e gravada quando o usuario pede
        alvo = fb.salvar_conexao(fb.Conexao(banco="x.fdb", senha="segredo"))
        gravado = json.load(open(alvo, encoding="utf-8"))
        checar("senha nao e gravada por padrao", gravado["senha"] == "", str(gravado))
        fb.salvar_conexao(fb.Conexao(banco="x.fdb", senha="segredo", salvar_senha=True))
        checar("senha e gravada quando salvar_senha esta ligado",
               json.load(open(alvo, encoding="utf-8"))["senha"] == "segredo")
        checar("conexao gravada volta igual", fb.carregar_conexao().banco == "x.fdb")

        # --- ponte HTTP
        srv = ThreadingHTTPServer(("127.0.0.1", 0), ponte.Handler)
        srv.verboso = False
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        time.sleep(0.2)
        raiz = f"http://127.0.0.1:{srv.server_address[1]}"

        def req(rota, corpo=None, token=ponte.TOKEN):
            pedido = urllib.request.Request(
                raiz + rota,
                data=json.dumps(corpo).encode() if corpo is not None else None,
                headers={"Content-Type": "application/json", "X-Ponte-Token": token},
            )
            try:
                with urllib.request.urlopen(pedido, timeout=5) as resp:
                    return resp.status, json.loads(resp.read() or b"{}")
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read() or b"{}")

        try:
            checar("so escuta em 127.0.0.1", srv.server_address[0] == "127.0.0.1")

            with urllib.request.urlopen(raiz + "/", timeout=5) as resp:
                html = resp.read().decode("utf-8")
            checar("serve o index.html com o token injetado",
                   "PONTE_TOKEN" in html and ponte.TOKEN in html and "carregarBaseDoBanco" in html)

            codigo, corpo = req("/api/status", token="token-errado")
            checar("recusa /api sem o token certo", codigo == 403, f"{codigo} {corpo}")

            codigo, corpo = req("/api/pessoas", {"banco": "x.fdb"}, token="token-errado")
            checar("recusa POST sem o token certo", codigo == 403, f"{codigo} {corpo}")

            codigo, corpo = req("/api/status")
            checar("status responde sem devolver a senha",
                   codigo == 200 and "senha" not in corpo["conexao"], f"{codigo} {corpo}")

            codigo, corpo = req("/api/pessoas", {"banco": "x.fdb", "senha": "x"})
            checar("a ponte devolve a base lida",
                   codigo == 200 and len(corpo["base"]) == 2 and corpo["info"]["lidas"] == 4,
                   f"{codigo} {str(corpo)[:200]}")

            codigo, corpo = req("/api/testar", {"banco": "x.fdb", "tabela": "A;DROP TABLE X"})
            checar("a ponte barra tabela fora do padrao", codigo == 400, f"{codigo} {corpo}")

            codigo, corpo = req("/api/pessoas", {"banco": "x.fdb", "senha": "errada"})
            checar("erro do banco vira 502 com a mensagem",
                   codigo == 502 and "password" in corpo["erro"], f"{codigo} {corpo}")

            codigo, _ = req("/api/qualquer", {})
            checar("rota desconhecida devolve 404", codigo == 404, str(codigo))
        finally:
            srv.shutdown()
            srv.server_close()
    finally:
        fb._abrir = original
        core.pasta_trabalho = trabalho
        shutil.rmtree(pasta, ignore_errors=True)

    print(f"\nfirebird e ponte: {len(falhas)} falha(s)")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
