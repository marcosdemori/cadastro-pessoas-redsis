"""Motor de saneamento de pessoas: consulta de CNPJ, comparacao com a base
Redsis e geracao de SQL.

Portado de index.html v4.3, com as mesmas regras: importacao da base PESSOAS,
classificacao NOVO/EXISTENTE, diferenca campo a campo, selecao de campos,
alertas de qualidade e os tres modos de SQL (SMART, UPDATE, UPSERT).

Compartilhado pela interface grafica (gui.py) e pela linha de comando (CNPJ.py).
"""

import csv
import json
import os
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Optional

import requests
import sys


def forcar_utf8():
    """Faz o console do Windows aceitar acentos em vez de imprimir lixo."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass


def pasta_trabalho():
    """Pasta do executavel (ou do script) onde ficam CNPJ.txt e os arquivos gerados."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def caminho(nome):
    return os.path.join(pasta_trabalho(), nome)


def recurso(nome):
    """Arquivo embutido no executavel (ou ao lado do script, em desenvolvimento)."""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, nome)


# ------------------------------------------------------------------ provedores


@dataclass(frozen=True)
class Provider:
    id: str
    nome: str
    tem_ie: bool
    url: object


API_PROVIDERS = {
    "cnpjws": Provider("cnpjws", "CNPJ.ws", True, lambda c: f"https://publica.cnpj.ws/cnpj/{c}"),
    "brasilapi": Provider("brasilapi", "BrasilAPI", False, lambda c: f"https://brasilapi.com.br/api/cnpj/v1/{c}"),
    "minhareceita": Provider("minhareceita", "Minha Receita", False, lambda c: f"https://minhareceita.org/{c}"),
}

ORDEM_AUTO = ["cnpjws", "brasilapi", "minhareceita"]

ESTRATEGIAS = {
    "auto": "Automático (CNPJ.ws > BrasilAPI > Minha Receita)",
    "cnpjws": "Somente CNPJ.ws (com IE)",
    "brasilapi": "Somente BrasilAPI",
    "minhareceita": "Somente Minha Receita",
}

MODOS_SQL = {
    "SMART": "Inteligente: UPDATE existentes + INSERT novos",
    "UPDATE": "Somente UPDATE",
    "UPSERT": "UPDATE OR INSERT",
}

# Acompanha a versao do index.html: o cabecalho do .sql sai identico nos dois.
VERSAO = "v4.3"

DELAY_PADRAO = 10

ARQUIVO_PROGRESSO = "progresso.json"
ARQUIVO_SQL = "updates.sql"
ARQUIVO_CSV = "relatorio_cnpj.csv"

IE_TAMANHO = {
    "AC": 13, "AL": 9, "AM": 9, "AP": 9, "BA": 8,
    "CE": 9, "DF": 13, "ES": 9, "GO": 9, "MA": 9,
    "MG": 13, "MS": 9, "MT": 11, "PA": 9, "PB": 9,
    "PE": 9, "PI": 9, "PR": 10, "RJ": 8, "RN": 10,
    "RO": 13, "RR": 9, "RS": 10, "SC": 9, "SE": 9,
    "SP": 12, "TO": 9,
}


# --------------------------------------------------------------------- helpers


def somente_numero(valor):
    return re.sub(r"\D", "", str(valor if valor is not None else ""))


def limpar_texto(valor):
    if valor is None:
        return None
    texto = re.sub(r"[\x00-\x1f]+", " ", str(valor))
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto or None


def tem_valor(valor):
    if valor is None:
        return False
    if isinstance(valor, str):
        return valor.strip() != ""
    if isinstance(valor, (list, tuple, dict, set)):
        return len(valor) > 0
    return True


def normal_text(valor):
    texto = unicodedata.normalize("NFD", str(valor if valor is not None else ""))
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", texto.strip()).upper()


def normal(valor, kind="text"):
    """Normaliza um valor para comparar Redsis x Receita sem falso positivo."""
    if valor is None:
        return ""
    if kind == "ie":
        return re.sub(r"^0+(?=\d)", "", somente_numero(valor))
    if kind in ("digits", "number"):
        return somente_numero(valor)
    if kind == "lower":
        return str(valor).strip().lower()
    if kind == "upper":
        return str(valor).strip().upper()
    return normal_text(valor)


def is_email(valor):
    if not valor:
        return True
    return bool(re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", str(valor).strip()))


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
    return n if len(n) == 8 else None


def sanitizar_telefone(valor):
    n = somente_numero(valor)
    if not n or set(n) == {"0"} or len(n) < 10 or len(n) > 13:
        return None
    return n


def montar_fone(ddd, numero):
    t = somente_numero(numero)
    if not t or set(t) == {"0"}:
        return None
    return sanitizar_telefone(somente_numero(ddd) + t)


def situacao_ativa(valor):
    return normal_text(valor) in {"ATIVA", "ATIVO"}


def tratar_ie_valor(ie, uf=None):
    num = somente_numero(ie)
    if not num:
        return "ISENTO"
    tam = IE_TAMANHO.get(str(uf or "").upper())
    return num.zfill(tam) if tam and len(num) < tam else num


def contbicms_valor(ie):
    """Recebe a IE ja tratada: 'ISENTO' ou vazio significa contribuinte 'N'."""
    return "S" if ie and ie != "ISENTO" else "N"


def escolher_ie(ies, uf):
    candidatos = [x for x in (ies or []) if isinstance(x, dict) and x.get("inscricao_estadual")]
    if not candidatos:
        return None
    uf = str(uf or "").upper()

    def mesma_uf(x):
        xuf = str((x.get("estado") or {}).get("sigla") or x.get("uf") or "").upper()
        return not uf or not xuf or xuf == uf

    for x in candidatos:
        bruto = x.get("ativo")
        if bruto is None:
            bruto = x.get("situacao")
        st = str(bruto if bruto is not None else "").upper()
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


def formatar_cnpj(cnpj):
    """00000000000191 -> 00.000.000/0001-91, so para exibicao."""
    n = somente_numero(cnpj)
    if len(n) != 14:
        return str(cnpj or "")
    return f"{n[:2]}.{n[2:5]}.{n[5:8]}/{n[8:12]}-{n[12:]}"


def cnpj_where(cnpj):
    return f"REPLACE(REPLACE(REPLACE(CNPJCPF_PESS, '.', ''), '/', ''), '-', '') = '{cnpj}'"


# ---------------------------------------------------------------------- campos


@dataclass(frozen=True)
class Campo:
    key: str
    db: str
    label: str
    kind: str


FIELD_DEFS = [
    Campo("razao_social", "NOM_PESS", "Razão social", "text"),
    Campo("nome_fantasia", "FANT_PESS", "Fantasia", "text"),
    Campo("ie", "IE_PESS", "Inscrição estadual", "ie"),
    Campo("logradouro", "END_PESS", "Endereço", "text"),
    Campo("numero", "NUM_PESS", "Número", "number"),
    Campo("complemento", "COMPL_PESS", "Complemento", "text"),
    Campo("bairro", "BAIRRO_PESS", "Bairro", "text"),
    Campo("cidade", "MUN_PESS", "Município", "text"),
    Campo("estado", "UF_PESS", "UF", "text"),
    Campo("cep", "CEP_PESS", "CEP", "digits"),
    Campo("telefone1", "FONE_PESS", "Telefone 1", "digits"),
    Campo("telefone2", "FONE2_PESS", "Telefone 2", "digits"),
    Campo("email", "EMAIL_PESS", "E-mail", "lower"),
    Campo("situacao_db", "SIT_PESS", "Situação", "upper"),
    Campo("tipo", "TIPO_PESS", "Tipo", "text"),
    Campo("cnae", "CNAEP_PESS", "CNAE", "digits"),
]

CAMPOS_POR_KEY = {c.key: c for c in FIELD_DEFS}

# SIT_PESS fica de fora por padrao: mexer na situacao e decisao do operador.
CAMPOS_PADRAO = [
    "razao_social", "nome_fantasia", "ie", "logradouro", "numero", "complemento",
    "bairro", "cidade", "estado", "cep", "telefone1", "telefone2", "email", "cnae",
]


# ------------------------------------------------------------------- resultado


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
    email_original: Optional[str] = None
    tipo: Optional[str] = None
    cnae: Optional[str] = None
    erro: str = ""
    api: Optional[str] = None
    api_provider: Optional[str] = None
    api_tem_ie: Optional[bool] = None
    api_fallback: bool = False
    api_falhas_anteriores: list = field(default_factory=list)
    tentativas: int = 0
    ultima_tentativa: Optional[str] = None
    incluir: bool = True
    campos_selecionados: dict = field(default_factory=dict)


@dataclass
class Config:
    tipo_sql: str = "SMART"
    tipo_cadastro: str = "cliente"
    campos: list = field(default_factory=lambda: list(CAMPOS_PADRAO))
    preservar_vazios: bool = True
    incluir_inativos: bool = False
    situacao_sempre_ativo: bool = False
    estrategia: str = "auto"
    delay: float = DELAY_PADRAO
    workers: int = 1
    tentativas: int = 2
    cod_strategy: str = "AUTO"
    cod_inicio: int = 1

    def normalizar(self):
        self.tipo_sql = str(self.tipo_sql or "SMART").upper()
        if self.tipo_sql not in MODOS_SQL:
            self.tipo_sql = "SMART"
        self.tipo_cadastro = normalizar_tipo_cadastro(self.tipo_cadastro)
        self.campos = [k for k in (self.campos or []) if k in CAMPOS_POR_KEY]
        if self.estrategia not in ESTRATEGIAS:
            self.estrategia = "auto"
        self.cod_strategy = str(self.cod_strategy or "AUTO").upper()
        if self.cod_strategy not in {"AUTO", "MANUAL"}:
            self.cod_strategy = "AUTO"
        self.delay = max(0.0, float(self.delay))
        self.workers = max(1, min(8, int(self.workers)))
        self.tentativas = max(1, min(5, int(self.tentativas)))
        self.cod_inicio = max(1, int(self.cod_inicio or 1))
        return self


def normalizar_tipo_cadastro(valor):
    valor = str(valor or "1").strip().lower()
    return {
        "1": "cliente", "c": "cliente", "cliente": "cliente",
        "2": "fornecedor", "f": "fornecedor", "fornecedor": "fornecedor",
        "3": "ambos", "a": "ambos", "ambos": "ambos",
    }.get(valor, "cliente")


def campos_tipo_cadastro(tipo):
    tipo = normalizar_tipo_cadastro(tipo)
    if tipo == "cliente":
        return ["CLIENT_PESS"]
    if tipo == "fornecedor":
        return ["FORN_PESS"]
    return ["CLIENT_PESS", "FORN_PESS"]


# --------------------------------------------------------------- normalizacao


def _normalizar_api(provider, data, cnpj):
    p = API_PROVIDERS[provider]
    if provider == "cnpjws":
        est = data.get("estabelecimento") or {}
        cidade = est.get("cidade") or {}
        estado = est.get("estado") or {}
        uf = estado.get("sigla")
        ie_obj = escolher_ie(est.get("inscricoes_estaduais") or [], uf)
        ie_raw = ie_obj.get("inscricao_estadual") if ie_obj else None
        cnae = est.get("atividade_principal") or {}
        email = str(est.get("email")).strip() if tem_valor(est.get("email")) else None
        return Resultado(
            status="OK",
            cnpj=cnpj,
            razao_social=data.get("razao_social") or None,
            nome_fantasia=est.get("nome_fantasia") or None,
            situacao=est.get("situacao_cadastral") or None,
            ie_raw=ie_raw,
            ie=tratar_ie_valor(ie_raw, uf),
            logradouro=est.get("logradouro") or None,
            numero=est.get("numero") or None,
            complemento=est.get("complemento") or None,
            bairro=est.get("bairro") or None,
            cidade=cidade.get("nome") or None,
            estado=uf,
            cep=sanitizar_cep(est.get("cep")),
            ibge_id=str(cidade.get("ibge_id")) if cidade.get("ibge_id") else None,
            telefone1=montar_fone(est.get("ddd1"), est.get("telefone1")),
            telefone2=montar_fone(est.get("ddd2"), est.get("telefone2")),
            email=email if is_email(email) else None,
            email_original=email,
            tipo=est.get("tipo") or None,
            cnae=cnae.get("id") or cnae.get("codigo") or None,
            api=p.nome,
            api_provider=p.id,
            api_tem_ie=p.tem_ie,
        )

    if provider in {"brasilapi", "minhareceita"}:
        email = str(data.get("email")).strip() if tem_valor(data.get("email")) else None
        telefone1 = data.get("ddd_telefone_1")
        if provider == "brasilapi":
            telefone1 = telefone1 or data.get("telefone")
        return Resultado(
            status="OK",
            cnpj=cnpj,
            razao_social=data.get("razao_social") or (data.get("nome") if provider == "brasilapi" else None) or None,
            nome_fantasia=data.get("nome_fantasia") or (data.get("fantasia") if provider == "brasilapi" else None) or None,
            situacao=data.get("descricao_situacao_cadastral") or data.get("situacao_cadastral") or None,
            ie_raw=None,
            ie=None,
            logradouro=data.get("logradouro") or None,
            numero=data.get("numero") or None,
            complemento=data.get("complemento") or None,
            bairro=data.get("bairro") or None,
            cidade=data.get("municipio") or None,
            estado=data.get("uf") or None,
            cep=sanitizar_cep(data.get("cep")),
            ibge_id=somente_numero(data.get("codigo_municipio_ibge") or data.get("codigo_municipio")) or None,
            telefone1=sanitizar_telefone(telefone1),
            telefone2=sanitizar_telefone(data.get("ddd_telefone_2")),
            email=email if is_email(email) else None,
            email_original=email,
            tipo=data.get("descricao_identificador_matriz_filial") or (data.get("tipo") if provider == "brasilapi" else None) or None,
            cnae=somente_numero(data.get("cnae_fiscal")) or None,
            api=p.nome,
            api_provider=p.id,
            api_tem_ie=p.tem_ie,
        )

    raise ValueError("API desconhecida")


def dormir(segundos, parar=None):
    """Espera em fatias curtas para que Parar tenha efeito imediato.

    Devolve False quando a espera foi interrompida.
    """
    fim = time.monotonic() + max(0.0, float(segundos))
    while True:
        restante = fim - time.monotonic()
        if restante <= 0:
            return True
        if parar and parar():
            return False
        time.sleep(min(restante, 0.25))


def segundos_retry_after(resp, tentativa, delay_base):
    """Quanto esperar depois de um HTTP 429, igual ao retryAfterMs do index.html.

    Aceita Retry-After em segundos ou como data HTTP; sem o cabecalho, aplica
    recuo exponencial a partir de `delay_base` (minimo de 10s), limitado a 120s.
    """
    bruto = resp.headers.get("Retry-After") if resp is not None else None
    if bruto:
        try:
            return max(1.0, float(str(bruto).strip()))
        except ValueError:
            pass
        try:
            alvo = parsedate_to_datetime(str(bruto))
            agora = datetime.now(alvo.tzinfo) if alvo.tzinfo else datetime.now()
            return max(1.0, (alvo - agora).total_seconds())
        except (TypeError, ValueError):
            pass
    base = max(float(delay_base or 0), 10.0)
    return min(120.0, base * (2 ** max(0, tentativa - 1)))


class Agenda:
    """Controla o ritmo das requisicoes, separadamente para cada API.

    O limite das APIs publicas e por provedor, entao uma chamada a BrasilAPI
    nao deve consumir o intervalo do CNPJ.ws. Alem do intervalo fixo, mantem
    um acrescimo adaptativo por provedor: sobe quando vem um 429 e cai pela
    metade a cada sucesso, como o adaptiveDelayMs do index.html.
    """

    def __init__(self, intervalo_base):
        self.base = max(0.0, float(intervalo_base))
        self._lock = threading.Lock()
        self._proximo = {}
        self._extra = {}

    def intervalo(self, provider):
        with self._lock:
            return max(self.base, self._extra.get(provider, 0.0))

    def aguardar(self, provider, parar=None):
        while True:
            with self._lock:
                agora = time.monotonic()
                alvo = self._proximo.get(provider, 0.0)
                if agora >= alvo:
                    self._proximo[provider] = agora + max(self.base, self._extra.get(provider, 0.0))
                    return True
                espera = alvo - agora
            if not dormir(espera, parar):
                return False

    def penalizar(self, provider, segundos):
        with self._lock:
            self._extra[provider] = max(self._extra.get(provider, 0.0), float(segundos))

    def premiar(self, provider):
        with self._lock:
            self._extra[provider] = max(0.0, self._extra.get(provider, 0.0) / 2)


def consultar(cnpj, cfg=None, agenda=None, parar=None, log=None):
    """Consulta um CNPJ, com fallback entre as APIs.

    Devolve None quando o lote foi interrompido por `parar`.
    """
    cfg = (cfg or Config()).normalizar()
    agenda = agenda if agenda is not None else Agenda(cfg.delay)
    ordem = ORDEM_AUTO if cfg.estrategia == "auto" else [cfg.estrategia]
    falhas = []
    total = 0

    for pos, provider in enumerate(ordem):
        p = API_PROVIDERS[provider]
        if log and pos > 0:
            log(f"Fallback → {p.nome} para {cnpj}")
        for tentativa in range(1, cfg.tentativas + 1):
            if parar and parar():
                return None
            if not agenda.aguardar(provider, parar):
                return None
            total += 1
            try:
                resp = requests.get(p.url(cnpj), timeout=25, headers={"Accept": "application/json"})
                if resp.status_code in (400, 404):
                    falhas.append(f"{p.nome}: CNPJ não encontrado/aceito")
                    break
                if resp.status_code == 429:
                    espera = segundos_retry_after(resp, tentativa, cfg.delay)
                    agenda.penalizar(provider, espera)
                    if tentativa < cfg.tentativas:
                        if log:
                            log(f"429 em {p.nome} para {cnpj}; aguardando {espera:.0f}s...")
                        if not dormir(espera, parar):
                            return None
                        continue
                    falhas.append(f"{p.nome}: HTTP 429")
                    break
                if not resp.ok:
                    if tentativa < cfg.tentativas:
                        if not dormir(min(30.0, max(cfg.delay, 3.0) * tentativa), parar):
                            return None
                        continue
                    falhas.append(f"{p.nome}: HTTP {resp.status_code}")
                    break

                data = resp.json()
                if data.get("status") == "ERROR" or data.get("erro") is True:
                    falhas.append(f"{p.nome}: {data.get('message') or data.get('mensagem') or 'resposta sem dados válidos'}")
                    break

                r = _normalizar_api(provider, data, cnpj)
                if not limpar_texto(r.razao_social) and not limpar_texto(r.nome_fantasia):
                    falhas.append(f"{p.nome}: resposta sem dados cadastrais")
                    break

                agenda.premiar(provider)
                r.api_fallback = pos > 0
                r.api_falhas_anteriores = list(falhas)
                r.tentativas = total
                r.ultima_tentativa = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
                return r
            except Exception as exc:
                if tentativa < cfg.tentativas:
                    if not dormir(min(30.0, max(cfg.delay, 3.0) * tentativa), parar):
                        return None
                    continue
                falhas.append(f"{p.nome}: {exc}")

    return Resultado(
        status="ERRO",
        cnpj=cnpj,
        erro="Todas as APIs falharam: " + " | ".join(falhas),
        api_falhas_anteriores=falhas,
        tentativas=total,
        ultima_tentativa=datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        incluir=False,
    )


# ------------------------------------------------------------- lista e base


def parse_lista(texto):
    """Separa um texto solto em CNPJs validos e invalidos, sem repetir."""
    validos, invalidos, vistos = [], [], set()
    for item in re.split(r"[\s;,]+", texto or ""):
        doc = somente_numero(item)
        if not doc or doc in vistos:
            continue
        vistos.add(doc)
        if validar_cnpj(doc):
            validos.append(doc)
        else:
            invalidos.append(doc)
    return validos, invalidos


def carregar_lista(arquivo=None):
    # utf-8-sig descarta o BOM; errors="replace" evita abortar num TXT salvo em
    # ANSI, ja que so interessam os digitos.
    with open(arquivo or caminho("CNPJ.txt"), encoding="utf-8-sig", errors="replace") as f:
        return parse_lista(f.read())


def detectar_delimitador(linha):
    return max([";", "\t", ","], key=lambda d: linha.count(d))


def parse_linha(linha, delim):
    """CSV simples com aspas duplas escapadas, igual ao parser do index.html."""
    saida, atual, entre_aspas = [], "", False
    i = 0
    while i < len(linha):
        c = linha[i]
        if c == '"':
            if entre_aspas and i + 1 < len(linha) and linha[i + 1] == '"':
                atual += '"'
                i += 1
            else:
                entre_aspas = not entre_aspas
        elif c == delim and not entre_aspas:
            saida.append(atual.strip())
            atual = ""
        else:
            atual += c
        i += 1
    saida.append(atual.strip())
    return saida


def normalizar_cabecalho(h):
    return re.sub(r"[^A-Z0-9_]", "", normal_text(h))


COLUNAS_CNPJ = {"CNPJCPF_PESS", "CNPJ", "CNPJCPF"}


def carregar_base(texto):
    """Le o CSV exportado da PESSOAS e devolve {cnpj: {COLUNA: valor}}.

    Levanta ValueError quando falta cabecalho ou a coluna de CNPJ.
    """
    texto = (texto or "").lstrip("﻿").strip()
    if not texto:
        return {}

    linhas = [l for l in re.split(r"\r?\n", texto) if l.strip()]
    if len(linhas) < 2:
        raise ValueError("A base precisa ter cabeçalho e pelo menos uma linha.")

    delim = detectar_delimitador(linhas[0])
    cabecalho = [normalizar_cabecalho(h) for h in parse_linha(linhas[0], delim)]
    indice_cnpj = next((i for i, h in enumerate(cabecalho) if h in COLUNAS_CNPJ), -1)
    if indice_cnpj < 0:
        raise ValueError("Não encontrei a coluna CNPJCPF_PESS/CNPJ no cabeçalho.")

    base = {}
    for linha in linhas[1:]:
        valores = parse_linha(linha, delim)
        registro = {h: (valores[i] if i < len(valores) else "") for i, h in enumerate(cabecalho)}
        cnpj = somente_numero(valores[indice_cnpj] if indice_cnpj < len(valores) else "")
        if len(cnpj) != 14:
            continue
        registro["CNPJCPF_PESS"] = cnpj
        base[cnpj] = registro
    return base


def carregar_base_arquivo(arquivo):
    with open(arquivo, encoding="utf-8-sig", errors="replace") as f:
        return carregar_base(f.read())


def base_val(base, db):
    if not base:
        return None
    if db in base:
        return base[db]
    return base.get(normalizar_cabecalho(db))


def origem(r, base_map):
    if base_map and r.cnpj in base_map:
        return "EXISTENTE"
    return "NOVO" if base_map else "DESCONHECIDO"


# ------------------------------------------------------- comparacao e alertas


def situacao_db(r, cfg):
    if cfg.situacao_sempre_ativo:
        return "ATIVO"
    return "ATIVO" if situacao_ativa(r.situacao) else "INATIVO"


def field_api_value(r, campo, cfg):
    if campo.key == "situacao_db":
        return situacao_db(r, cfg)
    return getattr(r, campo.key, None)


@dataclass
class Diff:
    campo: Campo
    api: object
    base: object
    same: bool
    state: str


def diff_field(r, campo, base, cfg):
    a = field_api_value(r, campo, cfg)
    b = base_val(base, campo.db)
    if not base:
        return Diff(campo, a, None, False, "new")
    if cfg.preservar_vazios and not tem_valor(a):
        return Diff(campo, a, b, True, "preserved")
    igual = normal(a, campo.kind) == normal(b, campo.kind)
    return Diff(campo, a, b, igual, "same" if igual else "changed")


def campo_habilitado(r, key, cfg):
    if key not in cfg.campos:
        return False
    selecao = r.campos_selecionados or {}
    if key in selecao:
        return bool(selecao[key])
    return True


def diffs(r, base, cfg):
    return [diff_field(r, c, base, cfg) for c in FIELD_DEFS]


def campos_alterados(r, base, cfg):
    """Divergencias que de fato serao gravadas (campo habilitado e diferente)."""
    return [d for d in diffs(r, base, cfg) if not d.same and campo_habilitado(r, d.campo.key, cfg)]


def avaliar_qualidade(r):
    """Alertas que colocam o registro em REVISAO, iguais aos da tela."""
    if r.status != "OK":
        return [("erro", r.erro or "Falha na consulta")]

    issues = []
    if not situacao_ativa(r.situacao):
        issues.append(("erro", f"Situação cadastral: {r.situacao or 'não informada'}"))
    if not r.ibge_id:
        issues.append(("erro", "Código IBGE do município ausente"))
    if not r.razao_social:
        issues.append(("erro", "Razão social ausente"))
    if not r.cep:
        issues.append(("warn", "CEP ausente ou inválido"))
    if not r.telefone1 and not r.email:
        issues.append(("warn", "Sem telefone/e-mail válido"))
    if r.email_original and not is_email(r.email_original):
        issues.append(("warn", "E-mail inválido"))
    if r.ie_raw and not somente_numero(r.ie_raw):
        issues.append(("warn", "IE suspeita"))
    if r.api_tem_ie is False:
        issues.append(("warn", f"Dados obtidos por {r.api or 'API alternativa'}; essa fonte pode não fornecer Inscrição Estadual."))
    if r.api_fallback and r.api_falhas_anteriores:
        issues.append(("warn", "Fallback utilizado após falha: " + " | ".join(r.api_falhas_anteriores)))
    return issues


def status_registro(r, base_map, cfg):
    if r.status != "OK":
        return "ERRO"
    if r.incluir is False:
        return "EXCLUIDO"
    base = (base_map or {}).get(r.cnpj)
    if origem(r, base_map) == "EXISTENTE" and not campos_alterados(r, base, cfg):
        return "SEM_ALTERACAO"
    if avaliar_qualidade(r):
        return "REVISAO"
    return "PRONTO"


def pode_gerar(r, base_map, cfg):
    if r.status != "OK" or r.incluir is False:
        return False
    if not situacao_ativa(r.situacao) and not cfg.incluir_inativos:
        return False
    base = (base_map or {}).get(r.cnpj)
    orig = origem(r, base_map)
    if orig == "EXISTENTE" and not campos_alterados(r, base, cfg):
        return False
    if cfg.tipo_sql == "UPDATE" and orig == "NOVO":
        return False
    return True


# ------------------------------------------------------------------- geracao


def sql_value_for_field(r, campo, cfg):
    valor = field_api_value(r, campo, cfg)
    if campo.key == "cidade":
        return sql_mun_nome(r.ibge_id)
    if campo.key == "numero":
        return sql_numero(valor)
    return sql_texto(valor)


def update_assignments(r, base, cfg):
    saida = []
    for campo in FIELD_DEFS:
        if not campo_habilitado(r, campo.key, cfg):
            continue
        d = diff_field(r, campo, base, cfg)
        if base and d.same:
            continue
        if cfg.preservar_vazios and not tem_valor(field_api_value(r, campo, cfg)):
            continue
        if campo.key == "cidade":
            saida.append(f"MUN_PESS = {sql_mun_nome(r.ibge_id)}")
            saida.append(f"COD_MUN = {sql_mun_cod(r.ibge_id)}")
            continue
        saida.append(f"{campo.db} = {sql_value_for_field(r, campo, cfg)}")
        if campo.key == "ie":
            saida.append(f"CONTBICMS_PESS = '{contbicms_valor(r.ie)}'")

    for coluna in campos_tipo_cadastro(cfg.tipo_cadastro):
        if not base or normal_text(base_val(base, coluna)) != "S":
            saida.append(f"{coluna} = 'S'")
    return saida


def montar_update(r, base, cfg):
    atribuicoes = update_assignments(r, base, cfg)
    if not atribuicoes:
        return f"-- {r.cnpj}: sem alteração selecionada."
    corpo = ",\n  ".join(atribuicoes)
    return f"UPDATE PESSOAS\nSET\n  {corpo}\nWHERE {cnpj_where(r.cnpj)};"


def insert_data(r, cod_expr, cfg):
    colunas_tipo = campos_tipo_cadastro(cfg.tipo_cadastro)
    campos = [
        "COD_PESS", "CNPJCPF_PESS", "NOM_PESS", "FANT_PESS", "IE_PESS", "CONTBICMS_PESS",
        "END_PESS", "NUM_PESS", "COMPL_PESS", "BAIRRO_PESS", "MUN_PESS", "COD_MUN",
        "UF_PESS", "CEP_PESS", "FONE_PESS", "FONE2_PESS", "EMAIL_PESS", "SIT_PESS",
        "TIPO_PESS", "CNAEP_PESS", "COD_PAIS", "NOM_PAIS",
    ] + colunas_tipo
    valores = [
        cod_expr,
        sql_texto(r.cnpj),
        sql_texto(r.razao_social),
        sql_texto(r.nome_fantasia),
        sql_texto(r.ie or "ISENTO"),
        f"'{contbicms_valor(r.ie)}'",
        sql_texto(r.logradouro),
        sql_numero(r.numero),
        sql_texto(r.complemento),
        sql_texto(r.bairro),
        sql_mun_nome(r.ibge_id),
        sql_mun_cod(r.ibge_id),
        sql_texto(r.estado),
        sql_texto(r.cep),
        sql_texto(r.telefone1),
        sql_texto(r.telefone2),
        sql_texto(r.email),
        sql_texto(situacao_db(r, cfg)),
        sql_texto(r.tipo),
        sql_texto(r.cnae),
        "1058",
        "'BRASIL'",
    ] + ["'S'"] * len(colunas_tipo)
    return campos, valores


def montar_insert(r, cod_expr, cfg):
    campos, valores = insert_data(r, cod_expr, cfg)
    return (
        "INSERT INTO PESSOAS (\n    "
        + ",\n    ".join(campos)
        + "\n  ) VALUES (\n    "
        + ",\n    ".join(valores)
        + "\n  );"
    )


def montar_upsert(r, cod_expr, cfg):
    campos, valores = insert_data(r, cod_expr, cfg)
    return (
        "UPDATE OR INSERT INTO PESSOAS (\n  "
        + ",\n  ".join(campos)
        + "\n) VALUES (\n  "
        + ",\n  ".join(valores)
        + "\n) MATCHING (CNPJCPF_PESS);"
    )


def gerar_execute_block_novos(novos, cfg):
    """INSERTs dos novos cadastros.

    No modo AUTO sai um EXECUTE BLOCK que calcula MAX(COD_PESS)+1 na hora da
    execucao e ainda confere se o CNPJ ja existe.
    """
    if not novos:
        return ""

    if cfg.cod_strategy == "MANUAL":
        inicio = int(cfg.cod_inicio or 0)
        if inicio < 1:
            return "-- INFORME UM COD_PESS INICIAL VÁLIDO PARA OS NOVOS REGISTROS."
        return "\n\n".join(montar_insert(r, str(inicio + i), cfg) for i, r in enumerate(novos))

    corpos = []
    for r in novos:
        insert = montar_insert(r, ":V_COD", cfg).replace("\n", "\n    ")
        corpos.append(
            f"  IF (NOT EXISTS(SELECT 1 FROM PESSOAS WHERE {cnpj_where(r.cnpj)})) THEN\n"
            f"  BEGIN\n    {insert}\n    V_COD = V_COD + 1;\n  END"
        )
    return (
        "SET TERM ^ ;\nEXECUTE BLOCK AS\n  DECLARE VARIABLE V_COD INTEGER;\nBEGIN\n"
        "  SELECT COALESCE(MAX(COD_PESS), 0) + 1 FROM PESSOAS INTO :V_COD;\n\n"
        + "\n\n".join(corpos)
        + "\nEND^\nSET TERM ; ^"
    )


def gerar_sql_completo(resultados, base_map, cfg):
    cfg = cfg.normalizar()
    elegiveis = [r for r in resultados if pode_gerar(r, base_map, cfg)]
    if not elegiveis:
        return ""

    cabecalho = (
        f"-- Saneamento de Pessoas Redsis {VERSAO}\n"
        f"-- Gerado em: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n"
        "-- Revise e faça backup antes de executar.\n\n"
    )

    def base_de(r):
        return (base_map or {}).get(r.cnpj)

    if cfg.tipo_sql == "UPDATE":
        itens = [r for r in elegiveis if origem(r, base_map) != "NOVO"]
        return cabecalho + "\n\n".join(montar_update(r, base_de(r), cfg) for r in itens)

    if cfg.tipo_sql == "UPSERT":
        inicio = int(cfg.cod_inicio or 1)
        return cabecalho + "\n\n".join(
            montar_upsert(r, str(inicio + i), cfg) for i, r in enumerate(elegiveis)
        )

    existentes = [r for r in elegiveis if origem(r, base_map) != "NOVO"]
    novos = [r for r in elegiveis if origem(r, base_map) == "NOVO"]
    partes = []
    if existentes:
        partes.append(
            "-- ATUALIZAÇÕES DE CADASTROS EXISTENTES\n"
            + "\n\n".join(montar_update(r, base_de(r), cfg) for r in existentes)
        )
    if novos:
        partes.append("-- INSERÇÕES DE NOVOS CADASTROS\n" + gerar_execute_block_novos(novos, cfg))
    return cabecalho + "\n\n".join(partes)


# ------------------------------------------------------------------- arquivos


COLUNAS_RELATORIO = [
    "origem", "status", "cnpj", "api", "api_fallback", "cod_pess_redsis",
    "razao_redsis", "razao_receita", "situacao_receita", "divergencias",
    "campos_divergentes", "incluir_sql", "alertas",
]


def salvar_sql(resultados, cfg, base_map=None, destino=None):
    cfg = cfg.normalizar()
    sql = gerar_sql_completo(resultados, base_map, cfg)
    total = sum(1 for r in resultados if pode_gerar(r, base_map, cfg))
    alvo = destino or caminho(ARQUIVO_SQL)
    with open(alvo, "w", encoding="utf-8") as f:
        f.write(sql)
    return alvo, total


def salvar_csv(resultados, cfg, base_map=None, destino=None):
    cfg = cfg.normalizar()
    alvo = destino or caminho(ARQUIVO_CSV)
    with open(alvo, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=COLUNAS_RELATORIO, delimiter=";", quoting=csv.QUOTE_ALL)
        writer.writeheader()
        for r in resultados:
            base = (base_map or {}).get(r.cnpj)
            alterados = campos_alterados(r, base, cfg) if r.status == "OK" else []
            writer.writerow({
                "origem": origem(r, base_map),
                "status": status_registro(r, base_map, cfg),
                "cnpj": r.cnpj,
                "api": r.api or "",
                "api_fallback": "SIM" if r.api_fallback else "NAO",
                "cod_pess_redsis": base_val(base, "COD_PESS") or "",
                "razao_redsis": base_val(base, "NOM_PESS") or "",
                "razao_receita": r.razao_social or "",
                "situacao_receita": r.situacao or "",
                "divergencias": len(alterados),
                "campos_divergentes": " | ".join(d.campo.label for d in alterados),
                "incluir_sql": "SIM" if pode_gerar(r, base_map, cfg) else "NAO",
                "alertas": " | ".join(texto for _, texto in avaliar_qualidade(r)),
            })
    return alvo


# ------------------------------------------------------------------- progresso


def salvar_progresso(resultados, cfg, destino=None):
    dados = {
        "versao": 2,
        "salvo_em": time.time(),
        "config": asdict(cfg),
        "resultados": [asdict(r) for r in resultados],
    }
    alvo = destino or caminho(ARQUIVO_PROGRESSO)
    temporario = alvo + ".tmp"
    with open(temporario, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False)
    os.replace(temporario, alvo)


def _filtrar_campos(classe, dados):
    validos = {f for f in classe.__dataclass_fields__}
    return {k: v for k, v in (dados or {}).items() if k in validos}


def carregar_progresso(origem_arquivo=None):
    """Devolve (resultados, config) do ultimo lote, ou ([], None) se nao houver."""
    alvo = origem_arquivo or caminho(ARQUIVO_PROGRESSO)
    if not os.path.exists(alvo):
        return [], None
    try:
        with open(alvo, encoding="utf-8") as f:
            dados = json.load(f)
        resultados = [Resultado(**_filtrar_campos(Resultado, r)) for r in dados.get("resultados", [])]
        bruto = dados.get("config")
        cfg = Config(**_filtrar_campos(Config, bruto)).normalizar() if bruto else None
        return resultados, cfg
    except Exception:
        return [], None


def limpar_progresso(origem_arquivo=None):
    alvo = origem_arquivo or caminho(ARQUIVO_PROGRESSO)
    try:
        os.remove(alvo)
    except OSError:
        pass


# -------------------------------------------------------------------- execucao


def processar(cnpjs, cfg, on_resultado=None, log=None, parar=None, anteriores=None):
    """Consulta os CNPJs pendentes e devolve a lista completa de resultados.

    `anteriores` sao resultados ja obtidos: os que terminaram em OK nao sao
    consultados de novo. `parar` e uma funcao que, ao devolver True, interrompe
    o lote. O progresso e gravado em disco a cada resposta.
    """
    cfg = cfg.normalizar()
    resultados = list(anteriores or [])
    concluidos = {r.cnpj for r in resultados if r.status == "OK"}
    pendentes = [c for c in cnpjs if c not in concluidos]

    if not pendentes:
        return resultados

    # Um CNPJ reconsultado substitui o resultado de erro anterior.
    resultados = [r for r in resultados if r.cnpj not in set(pendentes)]

    # Uma agenda por lote: o ritmo e controlado separadamente para cada API.
    agenda = Agenda(cfg.delay)
    trava = threading.Lock()
    total = len(pendentes)
    feitos = 0

    def tarefa(cnpj):
        if parar and parar():
            return None
        return consultar(cnpj, cfg, agenda=agenda, parar=parar, log=log)

    def registrar(r):
        nonlocal feitos
        with trava:
            resultados.append(r)
            feitos += 1
            indice = feitos
            try:
                salvar_progresso(resultados, cfg)
            except Exception as exc:
                if log:
                    log(f"Aviso: nao foi possivel salvar o progresso ({exc}).")
        if on_resultado:
            on_resultado(r, indice, total)

    if cfg.workers == 1:
        for cnpj in pendentes:
            r = tarefa(cnpj)
            if r is None:
                break
            registrar(r)
    else:
        with ThreadPoolExecutor(max_workers=cfg.workers) as pool:
            for r in pool.map(tarefa, pendentes):
                if r is None:
                    continue
                registrar(r)

    return resultados
