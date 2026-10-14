"""Leitura da tabela PESSOAS direto do Firebird.

Complementa a importacao por CSV/TXT: em vez de exportar pelo IBExpert, o
aplicativo abre uma conexao somente leitura e monta o mesmo dicionario
{cnpj: {COLUNA: valor}} que ``cnpj_core.carregar_base`` devolve.

Suporta os dois drivers Python de Firebird:

* ``firebird-driver`` (Firebird 3, 4 e 5) - ``pip install firebird-driver``
* ``fdb``            (Firebird 2.5 e 3)   - ``pip install fdb``

Ambos precisam da biblioteca cliente do Firebird (fbclient.dll no Windows)
disponivel no PATH ou ao lado do executavel.
"""

import json
import os
import re
from dataclasses import dataclass, asdict, field

import cnpj_core as core


ARQUIVO_CONEXAO = "conexao_firebird.json"

# Colunas lidas da PESSOAS. Mesma lista do EXPORTAR_BASE_PESSOAS.sql.
COLUNAS_PESSOAS = [
    "COD_PESS",
    "CNPJCPF_PESS",
    "NOM_PESS",
    "FANT_PESS",
    "IE_PESS",
    "END_PESS",
    "NUM_PESS",
    "COMPL_PESS",
    "BAIRRO_PESS",
    "MUN_PESS",
    "COD_MUN",
    "UF_PESS",
    "CEP_PESS",
    "FONE_PESS",
    "FONE2_PESS",
    "EMAIL_PESS",
    "SIT_PESS",
    "TIPO_PESS",
    "CNAEP_PESS",
    "CLIENT_PESS",
    "FORN_PESS",
]

CHARSETS = ["WIN1252", "ISO8859_1", "UTF8", "NONE"]

# Only SELECT is ever sent to the database; nothing here writes to PESSOAS.
_IDENTIFICADOR = re.compile(r"^[A-Za-z][A-Za-z0-9_$]*$")


class ErroFirebird(Exception):
    """Falha de conexao, de driver ausente ou de leitura da tabela."""


@dataclass
class Conexao:
    host: str = "localhost"
    porta: int = 3050
    banco: str = ""
    usuario: str = "SYSDBA"
    senha: str = ""
    charset: str = "WIN1252"
    tabela: str = "PESSOAS"
    coluna_cnpj: str = "CNPJCPF_PESS"
    limite: int = 0          # 0 = sem limite
    salvar_senha: bool = False

    def normalizar(self):
        self.host = (self.host or "localhost").strip()
        self.banco = (self.banco or "").strip()
        self.usuario = (self.usuario or "SYSDBA").strip()
        self.charset = (self.charset or "WIN1252").strip().upper()
        self.tabela = (self.tabela or "PESSOAS").strip().upper()
        self.coluna_cnpj = (self.coluna_cnpj or "CNPJCPF_PESS").strip().upper()
        try:
            self.porta = int(self.porta or 3050)
        except (TypeError, ValueError):
            self.porta = 3050
        try:
            self.limite = max(0, int(self.limite or 0))
        except (TypeError, ValueError):
            self.limite = 0
        if not _IDENTIFICADOR.match(self.tabela):
            raise ErroFirebird(f"Nome de tabela invalido: {self.tabela}")
        if not _IDENTIFICADOR.match(self.coluna_cnpj):
            raise ErroFirebird(f"Nome de coluna invalido: {self.coluna_cnpj}")
        return self

    def descricao(self):
        local = self.banco or "(banco nao informado)"
        return f"{self.usuario}@{self.host}:{self.porta} - {os.path.basename(local) or local}"


# ------------------------------------------------------------------- drivers


def driver_disponivel():
    """Devolve o nome do driver instalado, ou None."""
    try:
        import firebird.driver  # noqa: F401
        return "firebird-driver"
    except ImportError:
        pass
    try:
        import fdb  # noqa: F401
        return "fdb"
    except ImportError:
        pass
    return None


def _abrir(cfg):
    """Abre a conexao com o driver que estiver instalado."""
    cfg.normalizar()
    if not cfg.banco:
        raise ErroFirebird("Informe o caminho (ou alias) do banco .fdb.")

    nome = driver_disponivel()
    if nome == "firebird-driver":
        from firebird.driver import connect
        dsn = f"{cfg.host}/{cfg.porta}:{cfg.banco}" if cfg.host else cfg.banco
        try:
            return connect(dsn, user=cfg.usuario, password=cfg.senha, charset=cfg.charset)
        except Exception as exc:
            raise ErroFirebird(_mensagem(exc)) from exc

    if nome == "fdb":
        import fdb
        try:
            return fdb.connect(
                host=cfg.host or None,
                port=cfg.porta,
                database=cfg.banco,
                user=cfg.usuario,
                password=cfg.senha,
                charset=cfg.charset,
            )
        except Exception as exc:
            raise ErroFirebird(_mensagem(exc)) from exc

    raise ErroFirebird(
        "Nenhum driver Firebird instalado.\n"
        "Instale um dos dois:\n"
        "  pip install firebird-driver   (Firebird 3, 4 e 5)\n"
        "  pip install fdb               (Firebird 2.5 e 3)\n"
        "Os dois exigem o fbclient.dll da mesma arquitetura do Python (32 ou 64 bits)."
    )


def _mensagem(exc):
    texto = str(exc).strip() or exc.__class__.__name__
    dica = ""
    baixo = texto.lower()
    if "fbclient" in baixo or "client library" in baixo or "shared library" in baixo:
        dica = ("\nA biblioteca cliente nao foi encontrada. Copie o fbclient.dll "
                "(mesma arquitetura do Python) para a pasta do aplicativo ou para o PATH.")
    elif "password" in baixo or "user name" in baixo:
        dica = "\nUsuario ou senha recusados pelo servidor."
    elif "unavailable" in baixo or "connection refused" in baixo or "network" in baixo:
        dica = f"\nO servidor nao respondeu. Confira host, porta e firewall."
    elif "no permission" in baixo or "file is not a valid database" in baixo:
        dica = "\nCaminho do .fdb incorreto ou sem permissao de leitura."
    return texto + dica


# ------------------------------------------------------------------- leitura


def _valor_texto(valor):
    if valor is None:
        return ""
    if isinstance(valor, bytes):
        return valor.decode("latin-1", errors="replace").strip()
    if isinstance(valor, str):
        return valor.strip()
    return str(valor).strip()


def colunas_da_tabela(cfg):
    """Lista as colunas que a tabela realmente tem, em maiusculas."""
    conexao = _abrir(cfg)
    try:
        cursor = conexao.cursor()
        cursor.execute(
            "SELECT TRIM(RDB$FIELD_NAME) FROM RDB$RELATION_FIELDS "
            "WHERE RDB$RELATION_NAME = ? ORDER BY RDB$FIELD_POSITION",
            (cfg.tabela,),
        )
        return [_valor_texto(linha[0]).upper() for linha in cursor.fetchall()]
    except Exception as exc:
        raise ErroFirebird(_mensagem(exc)) from exc
    finally:
        _fechar(conexao)


def _fechar(conexao):
    try:
        conexao.close()
    except Exception:
        pass


def testar(cfg):
    """Confere a conexao e devolve um resumo legivel. Nao le a PESSOAS inteira."""
    cfg.normalizar()
    colunas = colunas_da_tabela(cfg)
    if not colunas:
        raise ErroFirebird(f"A tabela {cfg.tabela} nao existe nesse banco.")
    if cfg.coluna_cnpj not in colunas:
        raise ErroFirebird(
            f"A tabela {cfg.tabela} nao tem a coluna {cfg.coluna_cnpj}. "
            f"Colunas encontradas: {', '.join(colunas[:15])}..."
        )
    usadas = [c for c in COLUNAS_PESSOAS if c in colunas]
    faltando = [c for c in COLUNAS_PESSOAS if c not in colunas]
    return {
        "driver": driver_disponivel(),
        "tabela": cfg.tabela,
        "colunas_total": len(colunas),
        "colunas_usadas": usadas,
        "colunas_faltando": faltando,
    }


def montar_select(cfg, colunas):
    primeiro = f"FIRST {cfg.limite} " if cfg.limite else ""
    campos = ", ".join(colunas)
    return (
        f"SELECT {primeiro}{campos} FROM {cfg.tabela} "
        f"WHERE {cfg.coluna_cnpj} IS NOT NULL"
    )


def ler_base(cfg, progresso=None):
    """Le a PESSOAS e devolve {cnpj: {COLUNA: valor}}, igual ao CSV.

    ``progresso`` recebe a contagem de linhas lidas a cada 500 registros.
    Somente SELECT e enviado ao banco.
    """
    cfg.normalizar()
    colunas = colunas_da_tabela(cfg)
    if not colunas:
        raise ErroFirebird(f"A tabela {cfg.tabela} nao existe nesse banco.")
    if cfg.coluna_cnpj not in colunas:
        raise ErroFirebird(f"A tabela {cfg.tabela} nao tem a coluna {cfg.coluna_cnpj}.")

    usadas = [c for c in COLUNAS_PESSOAS if c in colunas]
    if cfg.coluna_cnpj not in usadas:
        usadas.insert(0, cfg.coluna_cnpj)

    conexao = _abrir(cfg)
    base = {}
    lidas = 0
    ignoradas = 0
    try:
        cursor = conexao.cursor()
        cursor.execute(montar_select(cfg, usadas))
        while True:
            lote = cursor.fetchmany(1000)
            if not lote:
                break
            for linha in lote:
                lidas += 1
                registro = {col: _valor_texto(linha[i]) for i, col in enumerate(usadas)}
                cnpj = core.somente_numero(registro.get(cfg.coluna_cnpj, ""))
                if len(cnpj) != 14:
                    ignoradas += 1
                    continue
                registro["CNPJCPF_PESS"] = cnpj
                base[cnpj] = registro
            if progresso:
                progresso(lidas)
    except ErroFirebird:
        raise
    except Exception as exc:
        raise ErroFirebird(_mensagem(exc)) from exc
    finally:
        _fechar(conexao)

    return base, {"lidas": lidas, "validas": len(base), "ignoradas": ignoradas, "colunas": usadas}


# ------------------------------------------------------------------- config


def salvar_conexao(cfg, destino=None):
    """Grava os dados de conexao. A senha so e gravada se salvar_senha for True."""
    dados = asdict(cfg)
    if not cfg.salvar_senha:
        dados["senha"] = ""
    alvo = destino or core.caminho(ARQUIVO_CONEXAO)
    temporario = alvo + ".tmp"
    with open(temporario, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
    os.replace(temporario, alvo)
    try:
        os.chmod(alvo, 0o600)
    except OSError:
        pass
    return alvo


def carregar_conexao(origem=None):
    alvo = origem or core.caminho(ARQUIVO_CONEXAO)
    if not os.path.exists(alvo):
        return Conexao()
    try:
        with open(alvo, encoding="utf-8") as f:
            dados = json.load(f)
        validos = {c for c in Conexao.__dataclass_fields__}
        return Conexao(**{k: v for k, v in (dados or {}).items() if k in validos})
    except Exception:
        return Conexao()
