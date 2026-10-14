"""Compara cnpj_core.py com o JavaScript do index.html.

O index.html e a referencia: suas funcoes sao executadas no Node com stubs de
DOM, e a saida tem de bater com a do Python. Cobre a geracao de SQL em todas as
combinacoes de regras, a normalizacao das tres APIs e o parser da base PESSOAS.

Precisa do Node instalado. Sem Node, os testes sao pulados.
"""

import copy
import json
import os
import shutil
import subprocess
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
sys.path.insert(0, RAIZ)

import cnpj_core as core  # noqa: E402

INDEX = os.path.join(RAIZ, "index.html")
APP_JS = os.path.join(AQUI, "_app.js")

MAPA_TIPO = {"CLIENTE": "cliente", "FORNECEDOR": "fornecedor", "AMBOS": "ambos"}
RENOMEIA = {"api_nome": "api", "fieldSelection": "campos_selecionados"}
CAMPOS_RESULTADO = set(core.Resultado.__dataclass_fields__)

CAMPOS_NORMALIZACAO = [
    "razao_social", "nome_fantasia", "situacao", "ie_raw", "ie", "logradouro",
    "numero", "complemento", "bairro", "cidade", "estado", "cep", "ibge_id",
    "telefone1", "telefone2", "email", "email_original", "tipo", "cnae",
]


def tem_node():
    return shutil.which("node") is not None


def node(script, *args):
    r = subprocess.run(["node", os.path.join(AQUI, script), *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{script} falhou:\n{r.stderr[-1200:]}")
    return r.stdout


def preparar():
    node("extrai.js", APP_JS, INDEX)


def carregar(nome):
    with open(os.path.join(AQUI, nome), encoding="utf-8") as f:
        return json.load(f)


def resultado_de(reg):
    dados = {}
    for k, v in reg.items():
        k = RENOMEIA.get(k, k)
        if k == "api_provider" and v:
            v = v.lower()
        if k in CAMPOS_RESULTADO:
            dados[k] = v
    dados.setdefault("erro", "")
    return core.Resultado(**dados)


def config_de(cen):
    c = cen["config"]
    return core.Config(
        tipo_sql=c["tipoSql"],
        tipo_cadastro=MAPA_TIPO[c["tipoCadastro"]],
        campos=cen["campos"],
        preservar_vazios=c["preservarVazios"],
        incluir_inativos=c["incluirNaoAtivos"],
        situacao_sempre_ativo=c["situacaoSempreAtivo"],
        cod_strategy=c["codStrategy"],
        cod_inicio=c["codInicio"],
        set_term=c.get("usarSetTerm", False),
        formato_insert=c.get("formatoInsert", "INDEPENDENTE"),
    ).normalizar()


def py_saida(cen):
    cfg = config_de(cen)
    base_map = cen["base"]
    resultados = [resultado_de(r) for r in cen["registros"]]
    sql = core.gerar_sql_completo(resultados, base_map, cfg)
    linhas = []
    for r in resultados:
        base = base_map.get(r.cnpj)
        alt = core.campos_alterados(r, base, cfg) if r.status == "OK" else []
        linhas.append(";".join([
            core.origem(r, base_map),
            core.status_registro(r, base_map, cfg),
            r.cnpj,
            str(len(alt)),
            " | ".join(d.campo.label for d in alt),
            "SIM" if core.pode_gerar(r, base_map, cfg) else "NAO",
        ]))
    return sql, "\n".join(linhas)


def js_saida(cen):
    entrada = os.path.join(AQUI, "_cen.json")
    with open(entrada, "w", encoding="utf-8") as f:
        json.dump(cen, f, ensure_ascii=False)
    sql_out, csv_out = os.path.join(AQUI, "_js.sql"), os.path.join(AQUI, "_js.csv")
    node("harness.js", entrada, APP_JS, sql_out, csv_out)
    with open(sql_out, encoding="utf-8", newline="") as f:
        sql = f.read()
    with open(csv_out, encoding="utf-8", newline="") as f:
        csv = f.read()
    # o relatorio do JS traz os alertas na ultima coluna; comparamos as 6 primeiras
    csv = "\n".join(";".join(l.split(";")[:6]) for l in csv.split("\n") if l)
    return sql, csv


def limpar(texto):
    linhas = [l.rstrip("\r") for l in texto.split("\n")]
    return "\n".join(l for l in linhas if not l.startswith("-- Gerado em:")).strip()


def primeira_diferenca(a, b):
    la, lb = limpar(a).split("\n"), limpar(b).split("\n")
    for i in range(max(len(la), len(lb))):
        x = la[i] if i < len(la) else "<ausente>"
        y = lb[i] if i < len(lb) else "<ausente>"
        if x != y:
            return f"linha {i + 1}:\n      js: {x}\n      py: {y}"
    return ""


def variantes():
    todos = [c.key for c in core.FIELD_DEFS]
    seguros = list(core.CAMPOS_PADRAO)
    return [
        ("SMART padrao", {}, seguros),
        ("SMART ambos", {"tipoCadastro": "AMBOS"}, seguros),
        ("SMART cod manual", {"codStrategy": "MANUAL", "codInicio": 5000}, seguros),
        ("SMART sem preservar", {"preservarVazios": False}, seguros),
        ("SMART inativos", {"incluirNaoAtivos": True}, seguros),
        ("SMART forca ativo", {"situacaoSempreAtivo": True}, todos),
        ("SMART bloco", {"formatoInsert": "BLOCO"}, todos),
        ("SMART bloco com SET TERM", {"formatoInsert": "BLOCO", "usarSetTerm": True}, todos),
        ("SMART todos campos", {}, todos),
        ("SMART so razao", {}, ["razao_social"]),
        ("UPDATE padrao", {"tipoSql": "UPDATE"}, seguros),
        ("UPDATE todos campos", {"tipoSql": "UPDATE"}, todos),
        ("UPSERT padrao", {"tipoSql": "UPSERT", "codInicio": 900}, seguros),
        ("UPSERT fornecedor", {"tipoSql": "UPSERT", "codInicio": 1, "tipoCadastro": "FORNECEDOR"}, todos),
        ("SMART sem base", {"__sem_base": True}, seguros),
    ]


def testar_sql(relatar):
    falhas = 0
    base_cen = carregar("cenario.json")
    for nome, override, campos in variantes():
        cen = copy.deepcopy(base_cen)
        cen["campos"] = campos
        override = dict(override)
        if override.pop("__sem_base", False):
            cen["base"] = {}
        cen["config"].update(override)

        js_sql, js_csv = js_saida(cen)
        py_sql, py_csv = py_saida(cen)

        ok = True
        if limpar(js_sql) != limpar(py_sql):
            ok = False
            relatar(f"SQL {nome}", False, primeira_diferenca(js_sql, py_sql))
        if limpar(js_csv) != limpar(py_csv):
            ok = False
            relatar(f"classificação {nome}", False, primeira_diferenca(js_csv, py_csv))
        if ok:
            relatar(f"SQL e classificação: {nome}", True)
        else:
            falhas += 1
    return falhas


def testar_normalizacao(relatar):
    payloads = carregar("payloads.json")
    saida = os.path.join(AQUI, "_norm.json")
    node("norm_js.js", APP_JS, os.path.join(AQUI, "payloads.json"), saida)
    with open(saida, encoding="utf-8") as f:
        esperado = json.load(f)

    falhas = 0
    for prov, lista in payloads.items():
        for i, data in enumerate(lista):
            r = core._normalizar_api(prov, data, f"0000000000019{i}")
            js = esperado[prov][i]
            divergentes = []
            for campo in CAMPOS_NORMALIZACAO:
                a, b = js.get(campo), getattr(r, campo)
                a = None if a == "" else a
                b = None if b == "" else b
                a = None if a is None else str(a)
                b = None if b is None else str(b)
                if a != b:
                    divergentes.append(f"{campo}: js={a!r} py={b!r}")
            if divergentes:
                falhas += 1
                relatar(f"normalização {prov}[{i}]", False, "; ".join(divergentes))
            else:
                relatar(f"normalização {prov}[{i}]", True)
    return falhas


def testar_base(relatar):
    casos = carregar("casos_base.json")
    saida = os.path.join(AQUI, "_base.json")
    node("base_js.js", APP_JS, os.path.join(AQUI, "casos_base.json"), saida)
    with open(saida, encoding="utf-8") as f:
        esperado = json.load(f)

    falhas = 0
    for i, texto in enumerate(casos):
        alvo = esperado[i]
        try:
            obtido, erro_py = core.carregar_base(texto), None
        except ValueError as exc:
            obtido, erro_py = {}, str(exc)
        erro_js = alvo["erro"]

        problema = ""
        if bool(erro_js) != bool(erro_py):
            problema = f"erro js={erro_js!r} py={erro_py!r}"
        elif erro_js and erro_js.strip() != (erro_py or "").strip():
            problema = f"texto do erro js={erro_js!r} py={erro_py!r}"
        elif set(obtido) != set(alvo["mapa"]):
            problema = f"chaves js={sorted(alvo['mapa'])} py={sorted(obtido)}"
        else:
            for cnpj, linha_js in alvo["mapa"].items():
                if linha_js != obtido[cnpj]:
                    problema = f"linha {cnpj}: js={linha_js} py={obtido[cnpj]}"
                    break

        if problema:
            falhas += 1
            relatar(f"base CSV caso {i}", False, problema)
        else:
            relatar(f"base CSV caso {i}", True)
    return falhas


def limpar_temporarios():
    for nome in ("_app.js", "_cen.json", "_js.sql", "_js.csv", "_norm.json", "_base.json"):
        try:
            os.remove(os.path.join(AQUI, nome))
        except OSError:
            pass


def main():
    def relatar(nome, ok, detalhe=""):
        print(("OK    " if ok else "FALHA ") + nome + (f"\n      {detalhe}" if detalhe else ""))

    if not tem_node():
        print("PULADO: Node não encontrado; a paridade com index.html não foi verificada.")
        return 0

    preparar()
    try:
        falhas = testar_sql(relatar) + testar_normalizacao(relatar) + testar_base(relatar)
    finally:
        limpar_temporarios()

    print(f"\nparidade com index.html: {falhas} falha(s)")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
