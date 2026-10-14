"""Executa o SQL gerado em um Firebird de verdade.

Os outros testes conferem o texto do SQL; este confere que o banco aceita.
Usa o Firebird embarcado (conexao sem host), que cria um .fdb temporario sem
precisar de servidor nem de senha. Sem o firebird-driver ou sem fbclient.dll
compativel, a suite se declara pulada em vez de falhar: nem toda maquina que
roda a bateria tem Firebird instalado.
"""

import os
import shutil
import sys
import tempfile

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))

import cnpj_core as core  # noqa: E402
import firebird_db as fb  # noqa: E402

DDL = [
    """CREATE TABLE MUN (
        COD_MUN INTEGER NOT NULL PRIMARY KEY,
        NOM_MUN VARCHAR(60),
        CODIBGE_MUN VARCHAR(10))""",
    """CREATE TABLE PESSOAS (
        COD_PESS INTEGER NOT NULL PRIMARY KEY,
        CNPJCPF_PESS VARCHAR(20),
        NOM_PESS VARCHAR(100),
        FANT_PESS VARCHAR(100),
        IE_PESS VARCHAR(20),
        CONTBICMS_PESS VARCHAR(1),
        END_PESS VARCHAR(100),
        NUM_PESS INTEGER,
        COMPL_PESS VARCHAR(60),
        BAIRRO_PESS VARCHAR(60),
        MUN_PESS VARCHAR(60),
        COD_MUN INTEGER,
        UF_PESS VARCHAR(2),
        CEP_PESS VARCHAR(10),
        FONE_PESS VARCHAR(20),
        FONE2_PESS VARCHAR(20),
        EMAIL_PESS VARCHAR(100),
        SIT_PESS VARCHAR(10),
        TIPO_PESS VARCHAR(20),
        CNAEP_PESS VARCHAR(10),
        COD_PAIS INTEGER,
        NOM_PAIS VARCHAR(40),
        CLIENT_PESS VARCHAR(1),
        FORN_PESS VARCHAR(1))""",
]


def registros():
    um = core.Resultado(
        cnpj="47158914000149", status="OK", razao_social="A ONIX MARMORARIA LTDA",
        nome_fantasia="MARMORARIA ONIX", ie="261970488", logradouro="CAMINHO DOS ACORES",
        numero="6215", bairro="SANTO ANTONIO DE LISBOA", ibge_id="4205407", estado="SC",
        cep="88050300", telefone1="4899433024", email="onix@exemplo.com",
        situacao="Ativa", tipo="Matriz", cnae="2391503")
    dois = core.Resultado(
        cnpj="20202564000114", status="OK", razao_social="AFONSO DUARTE 62916653953",
        ie="257560653", logradouro="JOAO PAULO", numero="53", bairro="JOAO PAULO",
        ibge_id="4204202", estado="SC", cep="88030300",
        situacao="Ativa", tipo="Matriz", cnae="2391503")
    return [um, dois]


def preparar(driver, alvo):
    con = driver.create_database(alvo, user="SYSDBA", password="masterkey", charset="WIN1252")
    cur = con.cursor()
    for ddl in DDL:
        cur.execute(ddl)
    con.commit()
    cur.execute("INSERT INTO MUN VALUES (1, 'Florianopolis', '4205407')")
    cur.execute("INSERT INTO MUN VALUES (2, 'Chapeco', '4204202')")
    con.commit()
    return con


def main():
    if fb.driver_disponivel() != "firebird-driver":
        print("PULADO: firebird-driver nao instalado (pip install firebird-driver).")
        return 0
    bibliotecas = fb.localizar_clientes()
    if not bibliotecas:
        print(f"PULADO: nenhum fbclient.dll de {fb.bits_do_python()} bits encontrado.")
        return 0

    import firebird.driver as driver
    try:
        fb._carregar_biblioteca(bibliotecas[0])
    except fb.ErroFirebird as exc:
        print(f"PULADO: {str(exc).splitlines()[0]}")
        return 0

    falhas = []

    def checar(nome, cond, detalhe=""):
        print(("OK    " if cond else "FALHA ") + nome + ("" if cond else f"\n      {detalhe}"))
        if not cond:
            falhas.append(nome)

    pasta = tempfile.mkdtemp(prefix="sql-firebird-")
    try:
        novos = registros()

        def executar(sql):
            """Manda o SQL como um statement so, igual ao FireDAC e ao JDBC."""
            alvo = os.path.join(pasta, f"t{len(os.listdir(pasta))}.fdb").replace("\\", "/")
            con = preparar(driver, alvo)
            try:
                cur = con.cursor()
                cur.execute(sql)
                con.commit()
                cur.execute("SELECT COUNT(*), MIN(COD_PESS), MAX(COD_PESS) FROM PESSOAS")
                return cur.fetchone(), None
            except Exception as exc:
                return None, str(exc).replace("\n", " ")[:160]
            finally:
                con.close()

        cfg = core.Config()

        # --- padrao: INSERT ... SELECT independentes, que e o que roda em
        #     qualquer cliente, inclusive os que so sabem ExecSQL
        padrao = core.Config().normalizar()
        checar("o formato padrao e INDEPENDENTE", padrao.formato_insert == "INDEPENDENTE",
               padrao.formato_insert)
        soltos = core.gerar_execute_block_novos(novos, padrao)
        checar("o padrao nao usa EXECUTE BLOCK nem SET TERM",
               "EXECUTE BLOCK" not in soltos and "SET TERM" not in soltos,
               soltos.splitlines()[0])

        def executar_varios(comandos):
            alvo = os.path.join(pasta, f"v{len(os.listdir(pasta))}.fdb").replace("\\", "/")
            con = preparar(driver, alvo)
            try:
                cur = con.cursor()
                for comando in comandos:
                    cur.execute(comando)
                con.commit()
                # roda tudo de novo: o WHERE NOT EXISTS tem que segurar
                for comando in comandos:
                    cur.execute(comando)
                con.commit()
                cur.execute("SELECT COUNT(*), MIN(COD_PESS), MAX(COD_PESS) FROM PESSOAS")
                return cur.fetchone(), None
            except Exception as exc:
                return None, str(exc).replace("\n", " ")[:160]
            finally:
                con.close()

        comandos = [c.strip().rstrip(";") for c in soltos.split(";\n\n") if c.strip()]
        checar("sai um comando por cadastro", len(comandos) == len(novos), str(len(comandos)))
        contagem, erro = executar_varios(comandos)
        checar("cada INSERT ... SELECT roda sozinho e nao duplica na 2a passada",
               erro is None and contagem == (2, 1, 2), erro or str(contagem))

        # --- o EXECUTE BLOCK continua disponivel para quem executa por script
        cfg.formato_insert = "BLOCO"

        # --- sem SET TERM, que e o que roda em quem manda um statement
        cfg.set_term = False
        sql = core.gerar_execute_block_novos(novos, cfg.normalizar())
        checar("o padrao nao emite SET TERM", "SET TERM" not in sql, sql.splitlines()[0])
        contagem, erro = executar(sql)
        checar("o Firebird aceita o EXECUTE BLOCK sem SET TERM", erro is None, erro or "")
        checar("os dois novos entram com COD_PESS 1 e 2", contagem == (2, 1, 2), str(contagem))

        # --- com SET TERM: quebra exatamente como na ferramenta do usuario
        cfg.set_term = True
        com = core.gerar_execute_block_novos(novos, cfg.normalizar())
        checar("ligado, o SET TERM envolve o bloco",
               com.startswith("SET TERM ^ ;") and com.rstrip().endswith("SET TERM ; ^"),
               com.splitlines()[0])
        _, erro = executar(com)
        checar("com SET TERM o banco recusa num statement unico",
               erro is not None and "Dynamic SQL Error" in erro, erro or "nao recusou")

        # --- o bloco roda duas vezes sem duplicar: o IF NOT EXISTS segura
        cfg.set_term = False
        sql = core.gerar_execute_block_novos(novos, cfg.normalizar())
        alvo = os.path.join(pasta, "repeticao.fdb").replace("\\", "/")
        con = preparar(driver, alvo)
        try:
            cur = con.cursor()
            cur.execute(sql)
            con.commit()
            cur.execute(sql)
            con.commit()
            cur.execute("SELECT COUNT(*) FROM PESSOAS")
            total = cur.fetchone()[0]
        finally:
            con.close()
        checar("rodar o bloco duas vezes nao duplica cadastro", total == 2, f"total={total}")

        # --- COD_PESS manual continua saindo como INSERTs soltos
        cfg.cod_strategy = "MANUAL"
        cfg.cod_inicio = 500
        soltos = core.gerar_execute_block_novos(novos, cfg.normalizar())
        checar("no modo MANUAL nao ha EXECUTE BLOCK nem SET TERM",
               "EXECUTE BLOCK" not in soltos and "SET TERM" not in soltos,
               soltos.splitlines()[0])
        alvo = os.path.join(pasta, "manual.fdb").replace("\\", "/")
        con = preparar(driver, alvo)
        erro = None
        try:
            cur = con.cursor()
            for comando in [c for c in soltos.split(";\n\n") if c.strip()]:
                cur.execute(comando.rstrip().rstrip(";"))
            con.commit()
            cur.execute("SELECT MIN(COD_PESS), MAX(COD_PESS) FROM PESSOAS")
            faixa = cur.fetchone()
        except Exception as exc:
            erro, faixa = str(exc)[:160], None
        finally:
            con.close()
        checar("os INSERTs manuais rodam e respeitam o COD_PESS inicial",
               erro is None and faixa == (500, 501), erro or str(faixa))
    finally:
        shutil.rmtree(pasta, ignore_errors=True)

    print(f"\nSQL em Firebird real: {len(falhas)} falha(s)")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
