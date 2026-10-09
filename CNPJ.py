"""Saneamento de Pessoas - Redsis: interface de linha de comando.

Sem argumentos, faz as perguntas uma a uma. Com argumentos, roda direto:

    ConsultaCNPJ-CLI.exe --base BASE_PESSOAS.csv --modo SMART --delay 3 --workers 4

A logica fica em cnpj_core.py, compartilhada com a interface grafica.
"""

import argparse
import os
import sys

from cnpj_core import (
    ARQUIVO_PROGRESSO,
    CAMPOS_PADRAO,
    CAMPOS_POR_KEY,
    Config,
    ESTRATEGIAS,
    FIELD_DEFS,
    MODOS_SQL,
    avaliar_qualidade,
    caminho,
    campos_alterados,
    carregar_base_arquivo,
    carregar_lista,
    carregar_progresso,
    forcar_utf8,
    limpar_progresso,
    origem,
    pasta_trabalho,
    pode_gerar,
    processar,
    salvar_csv,
    salvar_sql,
    situacao_ativa,
)

MAPA_TIPO = {"1": "cliente", "2": "fornecedor", "3": "ambos"}
MAPA_API = {"1": "auto", "2": "cnpjws", "3": "brasilapi", "4": "minhareceita"}
MAPA_MODO = {"1": "SMART", "2": "UPDATE", "3": "UPSERT"}


def perguntar_sim(texto, padrao=False):
    sufixo = "[S/n]" if padrao else "[s/N]"
    resposta = (input(f"{texto} {sufixo}: ") or "").strip().upper()
    if not resposta:
        return padrao
    return resposta == "S"


def perguntar_numero(texto, padrao):
    bruto = (input(f"{texto} [{padrao:g}]: ") or "").strip().replace(",", ".")
    try:
        return float(bruto) if bruto else float(padrao)
    except ValueError:
        return float(padrao)


def config_interativa(tem_base):
    print("\nModo de SQL: 1-Inteligente (UPDATE existentes + INSERT novos) | 2-Somente UPDATE | 3-UPDATE OR INSERT")
    tipo_sql = MAPA_MODO.get((input("Opção [1]: ") or "1").strip(), "SMART")

    print("Cadastro: 1-Cliente | 2-Fornecedor | 3-Cliente e fornecedor")
    tipo = MAPA_TIPO.get((input("Opção [1]: ") or "1").strip(), "cliente")

    preservar = perguntar_sim("Preservar campos existentes quando API vier vazia?", True)
    incluir_inativos = perguntar_sim("Permitir SQL para CNPJ não ativo?", False)
    forcar_ativo = perguntar_sim("Forçar SIT_PESS = 'ATIVO'?", False)
    gravar_situacao = forcar_ativo or perguntar_sim("Gravar SIT_PESS mapeada da Receita (ATIVO/INATIVO)?", False)

    print("API: 1-Automático (CNPJ.ws > BrasilAPI > Minha Receita) | 2-CNPJ.ws | 3-BrasilAPI | 4-Minha Receita")
    estrategia = MAPA_API.get((input("Opção [1]: ") or "1").strip(), "auto")

    delay = perguntar_numero("Intervalo entre consultas em segundos", 10)
    workers = perguntar_numero("Consultas simultâneas", 1)

    campos = list(CAMPOS_PADRAO)
    if gravar_situacao:
        campos.append("situacao_db")

    cod_strategy, cod_inicio = "AUTO", 1
    if tem_base and tipo_sql in {"SMART", "UPSERT"}:
        if tipo_sql == "UPSERT" or not perguntar_sim("COD_PESS dos novos por MAX(COD_PESS)+1 automático?", True):
            cod_strategy = "MANUAL"
            cod_inicio = int(perguntar_numero("COD_PESS inicial", 1))

    return Config(
        tipo_sql=tipo_sql,
        tipo_cadastro=tipo,
        campos=campos,
        preservar_vazios=preservar,
        incluir_inativos=incluir_inativos,
        situacao_sempre_ativo=forcar_ativo,
        estrategia=estrategia,
        delay=delay,
        workers=int(workers),
        cod_strategy=cod_strategy,
        cod_inicio=cod_inicio,
    ).normalizar()


def montar_parser():
    p = argparse.ArgumentParser(
        prog="ConsultaCNPJ-CLI",
        description="Consulta CNPJs na Receita, compara com a base PESSOAS e gera o SQL.",
        epilog="Sem nenhum argumento, o programa pergunta cada opção.",
    )
    p.add_argument("--arquivo", default=None, help="lista de CNPJs (padrão: CNPJ.txt ao lado do programa)")
    p.add_argument("--base", default=None, metavar="ARQ", help="CSV da PESSOAS exportado do Firebird; sem ele tudo vira UPDATE cego")
    p.add_argument("--modo", choices=list(MODOS_SQL), default="SMART", help="modo de SQL")
    p.add_argument("--tipo", choices=["cliente", "fornecedor", "ambos"], default="cliente", help="tipo de cadastro")
    p.add_argument("--api", choices=list(ESTRATEGIAS), default="auto", help="estratégia de consulta")
    p.add_argument("--delay", type=float, default=10.0, metavar="SEG", help="intervalo mínimo entre consultas")
    p.add_argument("--workers", type=int, default=1, metavar="N", help="consultas simultâneas (1 a 8)")
    p.add_argument("--tentativas", type=int, default=2, metavar="N", help="tentativas por API (1 a 5)")
    p.add_argument("--campos", default=None, metavar="LISTA", help="campos a gravar, separados por vírgula; 'todos' libera tudo")
    p.add_argument("--listar-campos", action="store_true", help="mostra os campos disponíveis e sai")
    p.add_argument("--nao-preservar", action="store_true", help="deixa a API apagar campos já cadastrados")
    p.add_argument("--incluir-inativos", action="store_true", help="gera SQL também para CNPJ não ativo")
    p.add_argument("--forcar-ativo", action="store_true", help="grava SIT_PESS = 'ATIVO' em todos")
    p.add_argument("--situacao-receita", action="store_true", help="grava SIT_PESS mapeada da Receita (ATIVO/INATIVO)")
    p.add_argument("--cod-inicio", type=int, default=None, metavar="N", help="COD_PESS inicial manual para os novos cadastros")
    p.add_argument("--recomecar", action="store_true", help="ignora o progresso salvo e consulta tudo de novo")
    p.add_argument("--sql", default=None, metavar="ARQ", help="caminho do SQL gerado")
    p.add_argument("--csv", default=None, metavar="ARQ", help="caminho do relatório gerado")
    return p


def resolver_campos(args):
    if not args.campos:
        campos = list(CAMPOS_PADRAO)
    elif args.campos.strip().lower() == "todos":
        campos = [c.key for c in FIELD_DEFS]
    else:
        campos, desconhecidos = [], []
        for item in args.campos.split(","):
            chave = item.strip()
            if not chave:
                continue
            if chave in CAMPOS_POR_KEY:
                campos.append(chave)
            else:
                desconhecidos.append(chave)
        if desconhecidos:
            print(f"AVISO: campo(s) ignorado(s): {', '.join(desconhecidos)}. Use --listar-campos.")

    if (args.forcar_ativo or args.situacao_receita) and "situacao_db" not in campos:
        campos.append("situacao_db")
    return campos


def config_de_args(args):
    return Config(
        tipo_sql=args.modo,
        tipo_cadastro=args.tipo,
        campos=resolver_campos(args),
        preservar_vazios=not args.nao_preservar,
        incluir_inativos=args.incluir_inativos,
        situacao_sempre_ativo=args.forcar_ativo,
        estrategia=args.api,
        delay=args.delay,
        workers=args.workers,
        tentativas=args.tentativas,
        cod_strategy="MANUAL" if args.cod_inicio else "AUTO",
        cod_inicio=args.cod_inicio or 1,
    ).normalizar()


def listar_campos():
    print("Campos disponíveis (--campos):")
    for c in FIELD_DEFS:
        marca = "padrão" if c.key in CAMPOS_PADRAO else "opcional"
        print(f"  {c.key.ljust(14)} {c.db.ljust(16)} {c.label} [{marca}]")


def main(argv=None):
    forcar_utf8()
    argv = sys.argv[1:] if argv is None else argv
    args = montar_parser().parse_args(argv)

    if args.listar_campos:
        listar_campos()
        return 0

    interativo = not argv
    print("Saneamento de Pessoas - Redsis CLI")
    print(f"Pasta de trabalho: {pasta_trabalho()}")

    arquivo = args.arquivo or caminho("CNPJ.txt")
    if not os.path.exists(arquivo):
        print(f"ERRO: {os.path.basename(arquivo)} não encontrado. Crie o arquivo com um CNPJ por linha.")
        return 1

    # Base da PESSOAS: sem ela nao da para separar novo de existente nem
    # comparar campo a campo, entao todo registro vira UPDATE cego.
    base_map = {}
    caminho_base = args.base
    if interativo and not caminho_base:
        padrao = caminho("BASE_PESSOAS.csv")
        if os.path.exists(padrao):
            caminho_base = padrao
        else:
            resposta = (input("Caminho do CSV da base PESSOAS (ENTER para seguir sem base): ") or "").strip().strip('"')
            caminho_base = resposta or None

    if caminho_base:
        try:
            base_map = carregar_base_arquivo(caminho_base)
        except (OSError, ValueError) as exc:
            print(f"ERRO ao ler a base: {exc}")
            return 1
        print(f"Base Redsis: {len(base_map)} registro(s) de {os.path.basename(caminho_base)}")
    else:
        print("Sem base Redsis: nenhuma comparação; todos os campos marcados serão gravados.")

    cfg = config_interativa(bool(base_map)) if interativo else config_de_args(args)

    if cfg.tipo_sql != "UPDATE" and not base_map:
        print(f"AVISO: modo {cfg.tipo_sql} sem base carregada trata todo CNPJ como existente; nenhum INSERT será gerado.")

    validos, invalidos = carregar_lista(arquivo)
    if not validos:
        print("ERRO: nenhum CNPJ válido na lista.")
        return 1
    print(f"Válidos: {len(validos)} | Inválidos ignorados: {len(invalidos)}")

    anteriores = []
    if args.recomecar:
        limpar_progresso()
    else:
        salvos, _ = carregar_progresso()
        concluidos = [r for r in salvos if r.status == "OK" and r.cnpj in set(validos)]
        if concluidos:
            pendentes = len(validos) - len(concluidos)
            if interativo:
                print(f"Progresso encontrado: {len(concluidos)} consulta(s) concluída(s), {pendentes} pendente(s).")
                retomar = perguntar_sim("Retomar de onde parou?", True)
            else:
                print(f"Retomando: {len(concluidos)} já concluído(s), {pendentes} pendente(s).")
                retomar = True
            if retomar:
                anteriores = salvos
            else:
                limpar_progresso()

    def ao_concluir(r, indice, total):
        prefixo = f"[{indice}/{total}] {r.cnpj}"
        if r.status != "OK":
            print(f"{prefixo} ERRO: {r.erro}")
            return
        marca = " (fallback)" if r.api_fallback else ""
        orig = origem(r, base_map)
        base = base_map.get(r.cnpj)
        alterados = campos_alterados(r, base, cfg)
        detalhe = f"{r.razao_social} | {r.api or 'N/I'}{marca} | {orig}"
        if base:
            detalhe += f" | {len(alterados)} divergência(s)"
        if pode_gerar(r, base_map, cfg):
            print(f"{prefixo} OK: {detalhe}")
        else:
            motivo = "sem alteração" if base and not alterados else f"situação {r.situacao}"
            print(f"{prefixo} SEM SQL: {detalhe} ({motivo})")

    print(f"Consultando com intervalo de {cfg.delay:g}s e {cfg.workers} consulta(s) simultânea(s). Modo {cfg.tipo_sql}.")
    resultados = processar(validos, cfg, on_resultado=ao_concluir, log=print, anteriores=anteriores)

    destino_sql, total_sql = salvar_sql(resultados, cfg, base_map, args.sql)
    destino_csv = salvar_csv(resultados, cfg, base_map, args.csv)

    erros = sum(1 for r in resultados if r.status != "OK")
    revisao = sum(1 for r in resultados if r.status == "OK" and avaliar_qualidade(r))
    novos = sum(1 for r in resultados if r.status == "OK" and origem(r, base_map) == "NOVO")

    print(f"Finalizado. Registros no SQL: {total_sql} | Novos: {novos} | Em revisão: {revisao} | Erros: {erros}")
    print(f"SQL: {destino_sql}")
    print(f"Relatório: {destino_csv}")
    if erros:
        print(f"Progresso mantido em {ARQUIVO_PROGRESSO}: rode de novo para tentar só os que falharam.")
    else:
        limpar_progresso()
    return 0


if __name__ == "__main__":
    codigo = 0
    try:
        codigo = main()
    except KeyboardInterrupt:
        print("\nInterrompido. O progresso foi salvo; rode de novo para continuar.")
        codigo = 130
    except Exception as exc:
        print(f"ERRO INESPERADO: {exc}")
        codigo = 1
    # Pausa so no duplo clique: com argumentos o usuario ja esta num terminal.
    if getattr(sys, "frozen", False) and not sys.argv[1:]:
        try:
            input("Pressione ENTER para sair...")
        except EOFError:
            pass
    sys.exit(codigo)
