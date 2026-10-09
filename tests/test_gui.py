"""Testa a logica da interface grafica sem depender de cliques.

A janela e criada de verdade, mas fica oculta: o que se verifica e o estado dos
controles, a tabela, a previa de SQL e a selecao de campo por registro.
"""

import json
import os
import shutil
import sys
import tempfile
import tkinter as tk
from tkinter import messagebox

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))

import cnpj_core as core  # noqa: E402

RENOMEIA = {"api_nome": "api", "fieldSelection": "campos_selecionados"}
CAMPOS_RESULTADO = set(core.Resultado.__dataclass_fields__)


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


def main():
    falhas = []

    def checar(nome, cond, detalhe=""):
        print(("OK    " if cond else "FALHA ") + nome + ("" if cond else f"\n      {detalhe}"))
        if not cond:
            falhas.append(nome)

    pasta = tempfile.mkdtemp(prefix="saneamento-gui-")
    core.pasta_trabalho = lambda: pasta
    core.limpar_progresso()

    messagebox.showinfo = lambda *a, **k: None
    messagebox.showwarning = lambda *a, **k: None
    messagebox.showerror = lambda *a, **k: print("      dialogo de erro:", a)
    messagebox.askyesno = lambda *a, **k: True

    import gui  # noqa: E402  (depois de redirecionar pasta_trabalho)

    with open(os.path.join(AQUI, "cenario.json"), encoding="utf-8") as f:
        cen = json.load(f)
    registros = [resultado_de(r) for r in cen["registros"]]

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"PULADO: sem display para o Tk ({exc}).")
        shutil.rmtree(pasta, ignore_errors=True)
        return 0

    try:
        app = gui.App(root)
        root.withdraw()
        app.base_map = cen["base"]
        app.resultados = registros
        app.recalcular()
        root.update()

        checar("tabela lista todos os registros", len(app.tabela.get_children()) == len(registros))
        checar("métricas somam sucesso, erro e novos",
               app.lbl_metricas.cget("text") == "Sucesso: 5 | Erros: 1 | Novos: 2 | No SQL: 3",
               app.lbl_metricas.cget("text"))

        primeiro = app.tabela.item(app.tabela.get_children()[0])["values"]
        checar("CNPJ aparece mascarado, sem perder zeros à esquerda",
               str(primeiro[0]) == "00.000.000/0001-91", str(primeiro[0]))

        app.atualizar_previa()
        root.update()
        cfg = app._config_atual()
        esperado = core.gerar_sql_completo(registros, app.base_map, cfg).strip()
        checar("prévia de SQL confere com o motor",
               app.texto_sql.get("1.0", "end").strip() == esperado)

        # excluir um registro tira ele do SQL
        app.tabela.selection_set("0")
        app.alternar_inclusao()
        root.update()
        checar("excluir registro reduz a contagem do SQL",
               "No SQL: 2" in app.lbl_metricas.cget("text"), app.lbl_metricas.cget("text"))
        checar("a linha selecionada sobrevive ao recálculo",
               app.tabela.selection() == ("0",), str(app.tabela.selection()))
        app.alternar_inclusao()
        root.update()
        checar("incluir de volta restaura a contagem",
               "No SQL: 3" in app.lbl_metricas.cget("text"), app.lbl_metricas.cget("text"))

        # selecao de campo so para um registro
        alvo = registros[0]
        app._marcar_campo_registro(alvo, "nome_fantasia", False, None)
        root.update()
        cfg = app._config_atual()
        sql = core.gerar_sql_completo(registros, app.base_map, cfg)
        trecho = sql.split(core.cnpj_where(alvo.cnpj))[0]
        checar("campo desligado no registro sai do UPDATE dele",
               "FANT_PESS" not in trecho and "NOM_PESS" in trecho,
               f"seleção={alvo.campos_selecionados} trecho={trecho[-220:]!r}")
        checar("outros registros seguem a regra global",
               alvo.campos_selecionados == {"nome_fantasia": False}, str(alvo.campos_selecionados))

        salvos, _ = core.carregar_progresso()
        marcado = next((r for r in salvos if r.cnpj == alvo.cnpj), None)
        checar("escolha por registro é gravada no progresso",
               marcado is not None and marcado.campos_selecionados.get("nome_fantasia") is False)

        alvo.campos_selecionados.clear()
        app.recalcular()
        root.update()
        sql = core.gerar_sql_completo(registros, app.base_map, app._config_atual())
        trecho = sql.split(core.cnpj_where(alvo.cnpj))[0]
        checar("restaurar seleção global devolve o campo", "FANT_PESS" in trecho)

        # marcar todos os campos liga SIT_PESS
        app._marcar_campos("todos")
        root.update()
        cfg = app._config_atual()
        checar("marcar todos inclui SIT_PESS", "situacao_db" in cfg.campos)
        checar("SIT_PESS aparece no SQL", "SIT_PESS" in core.gerar_sql_completo(registros, app.base_map, cfg))
        app._marcar_campos("seguros")
        root.update()

        # modos de SQL
        app._modo_mudou("UPSERT")
        app.var_cod_inicio.set("900")
        root.update()
        cfg = app._config_atual()
        checar("modo UPSERT força COD_PESS manual",
               cfg.tipo_sql == "UPSERT" and cfg.cod_strategy == "MANUAL" and cfg.cod_inicio == 900,
               f"{cfg.tipo_sql} {cfg.cod_strategy} {cfg.cod_inicio}")
        checar("UPSERT gera UPDATE OR INSERT",
               "UPDATE OR INSERT" in core.gerar_sql_completo(registros, app.base_map, cfg))

        app._modo_mudou("UPDATE")
        root.update()
        sql = core.gerar_sql_completo(registros, app.base_map, app._config_atual())
        checar("modo UPDATE não gera INSERT", "INSERT INTO PESSOAS" not in sql)

        app._modo_mudou("SMART")
        root.update()
        app.salvar_saidas()
        checar("salva updates.sql e relatorio_cnpj.csv",
               os.path.exists(os.path.join(pasta, core.ARQUIVO_SQL))
               and os.path.exists(os.path.join(pasta, core.ARQUIVO_CSV)))
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass
        shutil.rmtree(pasta, ignore_errors=True)

    print(f"\ninterface gráfica: {len(falhas)} falha(s)")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
