import csv
import re
import time
from dataclasses import dataclass, asdict
from typing import Optional

import requests

API_PROVIDERS = {
    "cnpjws": ("CNPJ.ws", lambda c: f"https://publica.cnpj.ws/cnpj/{c}"),
    "brasilapi": ("BrasilAPI", lambda c: f"https://brasilapi.com.br/api/cnpj/v1/{c}"),
    "minhareceita": ("Minha Receita", lambda c: f"https://minhareceita.org/{c}"),
}
DELAY_PADRAO = 10

IE_TAMANHO = {
    "AC": 13, "AL": 9, "AM": 9, "AP": 9, "BA": 8,
    "CE": 9, "DF": 13, "ES": 9, "GO": 9, "MA": 9,
    "MG": 13, "MS": 9, "MT": 11, "PA": 9, "PB": 9,
    "PE": 9, "PI": 9, "PR": 10, "RJ": 8, "RN": 10,
    "RO": 13, "RR": 9, "RS": 10, "SC": 9, "SE": 9,
    "SP": 12, "TO": 9,
}


@dataclass
class Resultado:
    status: str
    cnpj: str
    razao_social: Optional[str] = None
    nome_fantasia: Optional[str] = None
    situacao: Optional[str] = None
    ie_raw: Optional[str] = None
    ie: Optional[str] = None
    logradouro: Optional[str] = None
    numero: Optional[str] = None
    complemento: Optional[str] = None
    bairro: Optional[str] = None
    cidade: Optional[str] = None
    estado: Optional[str] = None
    cep: Optional[str] = None
    ibge_id: Optional[str] = None
    telefone1: Optional[str] = None
    telefone2: Optional[str] = None
    email: Optional[str] = None
    tipo: Optional[str] = None
    cnae: Optional[str] = None
    erro: str = ""
    api: Optional[str] = None
    api_fallback: bool = False


def somente_numero(valor):
    return re.sub(r"\D", "", str(valor or ""))


def limpar_texto(valor):
    if valor is None:
        return None
    valor = str(valor).strip()
    return valor or None


def escape_sql(valor):
    valor = limpar_texto(valor)
    return valor.replace("'", "''") if valor else None


def sql_texto(valor):
    valor = escape_sql(valor)
    return f"'{valor}'" if valor else "NULL"


def sql_numero(valor):
    valor = somente_numero(valor)
    return valor if valor else "NULL"


def validar_cnpj(cnpj):
    n = somente_numero(cnpj)
    if len(n) != 14 or len(set(n)) == 1:
        return False

    def digito(base, pesos):
        soma = sum(int(base[i]) * peso for i, peso in enumerate(pesos))
        resto = soma % 11
        return 0 if resto < 2 else 11 - resto

    d1 = digito(n, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    d2 = digito(n, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return int(n[12]) == d1 and int(n[13]) == d2


def sanitizar_cep(valor):
    n = somente_numero(valor)
    return n if len(n) == 8 and set(n) != {"0"} else None


def sanitizar_telefone(valor):
    n = somente_numero(valor)
    if not n or set(n) == {"0"} or len(n) < 10 or len(n) > 13:
        return None
    return n


def montar_fone(ddd, numero):
    return sanitizar_telefone(somente_numero(ddd) + somente_numero(numero))


def situacao_ativa(valor):
    return str(valor or "").strip().upper() in {"ATIVA", "ATIVO"}


def tratar_ie_valor(ie, uf=None):
    num = somente_numero(ie)
    if not num:
        return "ISENTO"
    tam = IE_TAMANHO.get(str(uf or "").upper())
    return num.zfill(tam) if tam and len(num) < tam else num


def contbicms_valor(ie_raw):
    return "S" if somente_numero(ie_raw) else "N"


def escolher_ie(ies, uf):
    candidatos = [x for x in (ies or []) if isinstance(x, dict) and x.get("inscricao_estadual")]
    if not candidatos:
        return None
    uf = str(uf or "").upper()

    def mesma_uf(x):
        xuf = str((x.get("estado") or {}).get("sigla") or x.get("uf") or "").upper()
        return not uf or not xuf or xuf == uf

    for x in candidatos:
        st = str(x.get("situacao") or "").upper()
        if mesma_uf(x) and (x.get("ativo") is True or st in {"ATIVA", "ATIVO"}):
            return x
    for x in candidatos:
        if mesma_uf(x):
            return x
    return candidatos[0]


def sql_mun_nome(ibge_id):
    return f"(SELECT UPPER(NOM_MUN) FROM MUN WHERE CODIBGE_MUN = '{escape_sql(ibge_id)}')" if ibge_id else "NULL"


def sql_mun_cod(ibge_id):
    return f"(SELECT COD_MUN FROM MUN WHERE CODIBGE_MUN = '{escape_sql(ibge_id)}')" if ibge_id else "NULL"


def normalizar_tipo_cadastro(valor):
    valor = (valor or "1").strip().lower()
    return {
        "1": "cliente", "c": "cliente", "cliente": "cliente",
        "2": "fornecedor", "f": "fornecedor", "fornecedor": "fornecedor",
        "3": "ambos", "a": "ambos", "ambos": "ambos",
    }.get(valor, "cliente")


def campos_tipo_cadastro(tipo):
    if tipo == "cliente":
        return ["CLIENT_PESS"]
    if tipo == "fornecedor":
        return ["FORN_PESS"]
    return ["CLIENT_PESS", "FORN_PESS"]


def _normalizar_api(provider, data, cnpj):
    if provider == "cnpjws":
        est = data.get("estabelecimento") or {}
        cidade = est.get("cidade") or {}
        estado = est.get("estado") or {}
        uf = estado.get("sigla")
        ie_obj = escolher_ie(est.get("inscricoes_estaduais") or [], uf)
        ie_raw = ie_obj.get("inscricao_estadual") if ie_obj else None
        cnae = est.get("atividade_principal") or {}
        email = limpar_texto(est.get("email"))
        return Resultado("OK", cnpj, data.get("razao_social"), est.get("nome_fantasia"), est.get("situacao_cadastral"), ie_raw, tratar_ie_valor(ie_raw, uf), est.get("logradouro"), est.get("numero"), est.get("complemento"), est.get("bairro"), cidade.get("nome"), uf, sanitizar_cep(est.get("cep")), str(cidade.get("ibge_id")) if cidade.get("ibge_id") else None, montar_fone(est.get("ddd1"), est.get("telefone1")), montar_fone(est.get("ddd2"), est.get("telefone2")), email if email and re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email) else None, est.get("tipo"), cnae.get("id") or cnae.get("codigo"), api="CNPJ.ws")
    if provider in {"brasilapi", "minhareceita"}:
        nome_api = API_PROVIDERS[provider][0]
        razao = data.get("razao_social") or data.get("nome")
        fantasia = data.get("nome_fantasia") or data.get("fantasia")
        situacao = data.get("descricao_situacao_cadastral") or data.get("situacao_cadastral")
        uf = data.get("uf")
        email = limpar_texto(data.get("email"))
        return Resultado("OK", cnpj, razao, fantasia, situacao, None, None, data.get("logradouro"), data.get("numero"), data.get("complemento"), data.get("bairro"), data.get("municipio"), uf, sanitizar_cep(data.get("cep")), somente_numero(data.get("codigo_municipio_ibge") or data.get("codigo_municipio")) or None, sanitizar_telefone(data.get("ddd_telefone_1") or data.get("telefone")), sanitizar_telefone(data.get("ddd_telefone_2")), email if email and re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email) else None, data.get("descricao_identificador_matriz_filial") or data.get("tipo"), somente_numero(data.get("cnae_fiscal")) or None, api=nome_api)
    raise ValueError("API desconhecida")


def consultar(cnpj, tentativas=2, estrategia="auto"):
    ordem = ["cnpjws", "brasilapi", "minhareceita"] if estrategia == "auto" else [estrategia]
    falhas = []
    for pos, provider in enumerate(ordem):
        nome, url_fn = API_PROVIDERS[provider]
        for tentativa in range(1, tentativas + 1):
            try:
                resp = requests.get(url_fn(cnpj), timeout=25, headers={"Accept": "application/json"})
                if resp.status_code in (400, 404):
                    falhas.append(f"{nome}: CNPJ não encontrado/aceito")
                    break
                if resp.status_code == 429:
                    if tentativa < tentativas:
                        espera = int(resp.headers.get("Retry-After") or DELAY_PADRAO)
                        print(f"429 em {nome} para {cnpj}; aguardando {espera}s...")
                        time.sleep(espera)
                        continue
                    falhas.append(f"{nome}: HTTP 429")
                    break
                if not resp.ok:
                    if tentativa < tentativas:
                        time.sleep(5 * tentativa)
                        continue
                    falhas.append(f"{nome}: HTTP {resp.status_code}")
                    break
                data = resp.json()
                r = _normalizar_api(provider, data, cnpj)
                if not limpar_texto(r.razao_social) and not limpar_texto(r.nome_fantasia):
                    falhas.append(f"{nome}: resposta sem dados cadastrais")
                    break
                r.api_fallback = pos > 0
                return r
            except Exception as exc:
                if tentativa < tentativas:
                    time.sleep(5 * tentativa)
                    continue
                falhas.append(f"{nome}: {exc}")
    return Resultado("ERRO", cnpj, erro="Todas as APIs falharam: " + " | ".join(falhas))


def expr_update(campo, valor_sql, preservar=True):
    return campo if preservar and valor_sql == "NULL" else valor_sql


def montar_update(r, tipo_cadastro, preservar=True, forcar_ativo=False):
    ie_expr = sql_texto(r.ie) if r.ie_raw else ("IE_PESS" if preservar else "'ISENTO'")
    icms_expr = f"'{contbicms_valor(r.ie_raw)}'" if r.ie_raw else ("CONTBICMS_PESS" if preservar else "'N'")
    mun_nome = sql_mun_nome(r.ibge_id) if r.ibge_id else ("MUN_PESS" if preservar else "NULL")
    mun_cod = sql_mun_cod(r.ibge_id) if r.ibge_id else ("COD_MUN" if preservar else "NULL")
    sit = "'ATIVO'" if forcar_ativo else "SIT_PESS"

    campos = [
        ("NOM_PESS", expr_update("NOM_PESS", sql_texto(r.razao_social), preservar)),
        ("FANT_PESS", expr_update("FANT_PESS", sql_texto(r.nome_fantasia), preservar)),
        ("IE_PESS", ie_expr),
        ("CONTBICMS_PESS", icms_expr),
        ("END_PESS", expr_update("END_PESS", sql_texto(r.logradouro), preservar)),
        ("NUM_PESS", expr_update("NUM_PESS", sql_numero(r.numero), preservar)),
        ("COMPL_PESS", expr_update("COMPL_PESS", sql_texto(r.complemento), preservar)),
        ("BAIRRO_PESS", expr_update("BAIRRO_PESS", sql_texto(r.bairro), preservar)),
        ("MUN_PESS", mun_nome),
        ("COD_MUN", mun_cod),
        ("UF_PESS", expr_update("UF_PESS", sql_texto(r.estado), preservar)),
        ("CEP_PESS", expr_update("CEP_PESS", sql_texto(r.cep), preservar)),
        ("FONE_PESS", expr_update("FONE_PESS", sql_texto(r.telefone1), preservar)),
        ("FONE2_PESS", expr_update("FONE2_PESS", sql_texto(r.telefone2), preservar)),
        ("EMAIL_PESS", expr_update("EMAIL_PESS", sql_texto(r.email), preservar)),
        ("SIT_PESS", sit),
        ("TIPO_PESS", expr_update("TIPO_PESS", sql_texto(r.tipo), preservar)),
        ("CNAEP_PESS", expr_update("CNAEP_PESS", sql_texto(r.cnae), preservar)),
        ("COD_PAIS", "1058"),
        ("NOM_PAIS", "'BRASIL'"),
    ]
    campos += [(campo, "'S'") for campo in campos_tipo_cadastro(tipo_cadastro)]
    sets = ",\n  ".join(f"{campo.ljust(14)} = {valor}" for campo, valor in campos)
    return f"UPDATE PESSOAS\nSET\n  {sets}\nWHERE REPLACE(REPLACE(REPLACE(CNPJCPF_PESS, '.', ''), '/', ''), '-', '') = '{r.cnpj}';"


def carregar_lista(caminho="CNPJ.txt"):
    validos, invalidos, vistos = [], [], set()
    with open(caminho, encoding="utf-8") as f:
        bruto = re.split(r"[\s;,]+", f.read())
    for item in bruto:
        doc = somente_numero(item)
        if not doc or doc in vistos:
            continue
        vistos.add(doc)
        if validar_cnpj(doc):
            validos.append(doc)
        else:
            invalidos.append(doc)
    return validos, invalidos


def main():
    print("Saneamento de Pessoas - Redsis v4.3 CLI")
    print("1 - Cliente\n2 - Fornecedor\n3 - Cliente e Fornecedor")
    tipo = normalizar_tipo_cadastro(input("Opção [1]: ") or "1")
    preservar = (input("Preservar campos existentes quando API vier vazia? [S/n]: ") or "S").strip().upper() != "N"
    incluir_inativos = (input("Permitir SQL para CNPJ não ativo? [s/N]: ") or "N").strip().upper() == "S"
    forcar_ativo = (input("Forçar SIT_PESS = 'ATIVO'? [s/N]: ") or "N").strip().upper() == "S"
    print("API: 1-Automático (CNPJ.ws > BrasilAPI > Minha Receita) | 2-CNPJ.ws | 3-BrasilAPI | 4-Minha Receita")
    api_opt = (input("Opção [1]: ") or "1").strip()
    estrategia = {"1":"auto","2":"cnpjws","3":"brasilapi","4":"minhareceita"}.get(api_opt, "auto")

    validos, invalidos = carregar_lista()
    print(f"Válidos: {len(validos)} | Inválidos ignorados: {len(invalidos)}")

    resultados = []
    sqls = []
    for idx, cnpj in enumerate(validos, 1):
        print(f"[{idx}/{len(validos)}] Consultando {cnpj}...")
        r = consultar(cnpj, estrategia=estrategia)
        resultados.append(r)
        if r.status == "OK":
            if situacao_ativa(r.situacao) or incluir_inativos:
                sqls.append(montar_update(r, tipo, preservar, forcar_ativo))
                print(f"OK: {r.razao_social} | Fonte: {r.api or 'N/I'}{' (fallback)' if r.api_fallback else ''} | Situação: {r.situacao or 'N/I'}")
            else:
                print(f"REVISÃO: {r.razao_social} | situação {r.situacao}; SQL não gerado")
        else:
            print(f"ERRO: {r.erro}")
        if idx < len(validos):
            time.sleep(DELAY_PADRAO)

    with open("updates.sql", "w", encoding="utf-8") as f:
        f.write("-- Gerado pelo Saneamento de Pessoas Redsis v4.3 CLI\n")
        f.write("-- Revise antes de executar em produção.\n\n")
        f.write("\n\n".join(sqls))

    with open("relatorio_cnpj.csv", "w", newline="", encoding="utf-8-sig") as f:
        campos = list(asdict(Resultado("", "")).keys())
        writer = csv.DictWriter(f, fieldnames=campos, delimiter=";")
        writer.writeheader()
        for r in resultados:
            writer.writerow(asdict(r))

    print(f"Finalizado. SQLs gerados: {len(sqls)} | Relatório: relatorio_cnpj.csv")


if __name__ == "__main__":
    main()
