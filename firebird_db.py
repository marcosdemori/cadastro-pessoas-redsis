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
import sys
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
    biblioteca: str = ""     # caminho do fbclient.dll; vazio = procurar sozinho

    def normalizar(self):
        self.host = (self.host or "localhost").strip()
        self.banco = (self.banco or "").strip()
        self.biblioteca = (self.biblioteca or "").strip().strip('"')
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


# ------------------------------------------- biblioteca cliente (fbclient.dll)

# Pastas onde o instalador do Firebird e o IBExpert costumam deixar o cliente.
# A ordem e da versao mais nova para a mais velha: um cliente novo fala com
# servidor antigo com mais frequencia que o contrario.
_PASTAS_CLIENTE = [
    r"C:\Program Files\Firebird\Firebird_5_0",
    r"C:\Program Files\Firebird\Firebird_4_0",
    r"C:\Program Files\Firebird\Firebird_3_0",
    r"C:\Program Files\Firebird\Firebird_2_5\bin",
    r"C:\Program Files (x86)\Firebird\Firebird_3_0",
    r"C:\Program Files (x86)\Firebird\Firebird_2_5\bin",
]

_biblioteca_carregada = None

# Maquinas do cabecalho PE que interessam aqui.
_PE_MAQUINA = {0x014C: 32, 0x8664: 64, 0xAA64: 64}


def bits_do_python():
    return 64 if sys.maxsize > 2 ** 32 else 32


def bits_da_dll(caminho):
    """Le o cabecalho PE e devolve 32, 64 ou None se nao der para saber.

    Carregar uma DLL de arquitetura diferente da do Python falha com
    'WinError 193: nao e um aplicativo Win32 valido'. Conferir antes evita
    essa mensagem, que nao ajuda ninguem a entender o problema.
    """
    try:
        with open(caminho, "rb") as f:
            if f.read(2) != b"MZ":
                return None
            f.seek(0x3C)
            inicio = int.from_bytes(f.read(4), "little")
            f.seek(inicio)
            if f.read(4) != b"PE\0\0":
                return None
            return _PE_MAQUINA.get(int.from_bytes(f.read(2), "little"))
    except OSError:
        return None


def localizar_clientes(so_compativeis=True):
    """Lista os fbclient.dll encontrados, do mais novo para o mais velho.

    Procura ao lado do aplicativo, nas pastas padrao de instalacao e no PATH.
    Por padrao descarta as DLLs de arquitetura diferente da do Python, que
    nunca carregariam. Fora do Windows devolve lista vazia: ali o driver acha
    a libfbclient sozinho pelo caminho do sistema.
    """
    if os.name != "nt":
        return []

    candidatos = [
        os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "fbclient.dll"),
        core.recurso("fbclient.dll"),
    ]
    candidatos += [os.path.join(p, "fbclient.dll") for p in _PASTAS_CLIENTE]
    candidatos += [
        os.path.join(p, "fbclient.dll")
        for p in os.environ.get("PATH", "").split(os.pathsep) if p.strip()
    ]

    achados = []
    vistos = set()
    for c in candidatos:
        try:
            real = os.path.normcase(os.path.abspath(c))
        except (OSError, ValueError):
            continue
        if real in vistos:
            continue
        vistos.add(real)
        if not os.path.isfile(c):
            continue
        if so_compativeis and bits_da_dll(c) not in (None, bits_do_python()):
            continue
        achados.append(os.path.abspath(c))
    return achados


def _carregar_biblioteca(caminho):
    """Aponta o driver para um fbclient.dll especifico.

    O driver so carrega a biblioteca uma vez por processo. Trocar de caminho
    depois disso exige reiniciar o aplicativo, e e isso que a mensagem diz.
    """
    global _biblioteca_carregada
    if not caminho:
        return
    if not os.path.isfile(caminho):
        raise ErroFirebird(f"Biblioteca cliente nao encontrada em: {caminho}")

    bits = bits_da_dll(caminho)
    meus = bits_do_python()
    if bits and bits != meus:
        outras = [c for c in localizar_clientes() if os.path.normcase(c) != os.path.normcase(caminho)]
        extra = ("\nCompativeis encontradas:\n  " + "\n  ".join(outras[:6])) if outras else ""
        raise ErroFirebird(
            f"{caminho} e de {bits} bits e este Python e de {meus} bits; "
            f"ela nunca vai carregar aqui.{extra}"
        )

    caminho = os.path.abspath(caminho)
    if _biblioteca_carregada:
        if os.path.normcase(_biblioteca_carregada) != os.path.normcase(caminho):
            raise ErroFirebird(
                f"A biblioteca {_biblioteca_carregada} ja foi carregada neste processo.\n"
                f"Para usar {caminho}, feche e abra o aplicativo de novo."
            )
        return

    nome = driver_disponivel()
    try:
        if nome == "firebird-driver":
            import firebird.driver as fdriver
            fdriver.load_api(caminho)
        elif nome == "fdb":
            import fdb
            carregar = getattr(fdb, "load_api", None)
            if carregar is None:
                raise ErroFirebird(
                    "Esta versao do fdb nao deixa escolher o fbclient.dll. "
                    "Deixe o campo da biblioteca vazio e coloque o fbclient.dll no PATH, "
                    "ou use o firebird-driver."
                )
            carregar(caminho)
    except ErroFirebird:
        raise
    except AttributeError as exc:
        # A API orientada a objetos que o firebird-driver usa so existe a partir
        # do fbclient 3.0; com o do Firebird 2.5 falta fb_get_master_interface.
        if "fb_get_master_interface" in str(exc):
            raise ErroFirebird(
                f"{caminho} e do Firebird 2.5. O firebird-driver precisa de um "
                f"fbclient.dll 3.0 ou mais novo.\nUse a biblioteca de uma instalacao "
                f"3.0, 4.0 ou 5.0, ou instale o fdb (pip install fdb) para falar com 2.5."
            ) from exc
        raise ErroFirebird(f"Nao deu para carregar {caminho}: {_mensagem(exc)}") from exc
    except Exception as exc:
        raise ErroFirebird(f"Nao deu para carregar {caminho}: {_mensagem(exc)}") from exc

    _biblioteca_carregada = caminho


def _preparar_biblioteca(cfg):
    """Garante uma biblioteca cliente antes de conectar.

    Com o campo preenchido usa aquele caminho, e o erro dele vai direto para a
    tela. Vazio, tenta as encontradas em ordem ate uma carregar: assim um
    fbclient de 2.5 na frente da fila nao impede de chegar na de 5.0.
    """
    if cfg.biblioteca:
        _carregar_biblioteca(cfg.biblioteca)
        return
    if _biblioteca_carregada:
        return

    achados = localizar_clientes()
    if not achados:
        return
    erros = []
    for caminho in achados:
        try:
            _carregar_biblioteca(caminho)
            return
        except ErroFirebird as exc:
            erros.append(f"{caminho}: {str(exc).splitlines()[0]}")
    raise ErroFirebird(
        "Nenhuma das bibliotecas encontradas carregou:\n  " + "\n  ".join(erros)
    )


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

    _preparar_biblioteca(cfg)

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
        achados = localizar_clientes()
        if achados:
            dica = ("\nA biblioteca cliente nao foi carregada. Escolha uma destas no campo "
                    "'Biblioteca cliente':\n  " + "\n  ".join(achados[:6]))
        else:
            dica = ("\nA biblioteca cliente nao foi encontrada. Copie o fbclient.dll "
                    "(mesma arquitetura do Python: 32 ou 64 bits) para a pasta do aplicativo "
                    "ou para o PATH, ou informe o caminho no campo 'Biblioteca cliente'.")
    elif "password" in baixo or "user name" in baixo:
        dica = "\nUsuario ou senha recusados pelo servidor."
    elif "unavailable" in baixo or "connection refused" in baixo or "network" in baixo:
        dica = ("\nO servidor nao respondeu. Confira host, porta e firewall. "
                "Com varias instalacoes do Firebird na mesma maquina, cada instancia tem "
                "a sua porta em RemoteServicePort, no firebird.conf.")
        if _biblioteca_carregada:
            dica += (f"\nBiblioteca em uso: {_biblioteca_carregada} — uma biblioteca mais "
                     f"velha que o servidor tambem da esta mesma mensagem.")
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
        "biblioteca": _biblioteca_carregada or "",
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
