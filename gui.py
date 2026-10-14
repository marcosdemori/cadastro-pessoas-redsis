"""Saneamento de Pessoas - Redsis: interface grafica (Tkinter).

Mesmas etapas do index.html v4.3: lista de CNPJs, base atual da PESSOAS,
regras de geracao, consulta com progresso, revisao dos resultados e SQL.

O lote roda em uma thread separada para a janela nao congelar; o progresso e
salvo a cada consulta e pode ser retomado.
"""

import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import cnpj_core as core
import firebird_db as fb

TITULO = "Saneamento de Pessoas - Redsis"

TIPOS = [("Cliente", "cliente"), ("Fornecedor", "fornecedor"), ("Cliente e fornecedor", "ambos")]
APIS = [(rotulo, chave) for chave, rotulo in core.ESTRATEGIAS.items()]
MODOS = [(rotulo, chave) for chave, rotulo in core.MODOS_SQL.items()]

COLUNAS = [
    ("cnpj", "CNPJ", 130),
    ("origem", "Origem", 90),
    ("situacao_reg", "Status", 110),
    ("razao", "Razão social", 280),
    ("api", "Fonte", 110),
    ("divergencias", "Diverg.", 60),
    ("campos", "Campos divergentes", 320),
    ("alertas", "Alertas", 320),
]


class App:
    def __init__(self, root):
        self.root = root
        self.eventos = queue.Queue()
        self.thread = None
        self.parar_flag = threading.Event()
        self.resultados = []
        self.validos = []
        self.base_map = {}

        root.title(TITULO)
        root.geometry("1180x820")
        root.minsize(980, 680)

        self._montar_widgets()
        self._carregar_arquivos_padrao()
        self._verificar_progresso_salvo()
        self.root.after(100, self._drenar_eventos)
        self.root.protocol("WM_DELETE_WINDOW", self._ao_fechar)

    # ------------------------------------------------------------- construcao

    def _montar_widgets(self):
        topo = ttk.Frame(self.root, padding=(10, 8))
        topo.pack(fill="x")
        ttk.Label(topo, text=TITULO, font=("Segoe UI", 14, "bold")).pack(side="left")
        ttk.Label(topo, text=core.pasta_trabalho(), foreground="#555").pack(side="right")

        abas = ttk.Notebook(self.root)
        abas.pack(fill="both", expand=True, padx=10, pady=(0, 4))
        self.abas = abas

        self.aba_entrada = ttk.Frame(abas, padding=10)
        self.aba_regras = ttk.Frame(abas, padding=10)
        self.aba_resultados = ttk.Frame(abas, padding=10)
        self.aba_sql = ttk.Frame(abas, padding=10)
        abas.add(self.aba_entrada, text="1. Entrada")
        abas.add(self.aba_regras, text="2. Regras")
        abas.add(self.aba_resultados, text="3. Resultados")
        abas.add(self.aba_sql, text="4. SQL")

        self._montar_entrada(self.aba_entrada)
        self._montar_regras(self.aba_regras)
        self._montar_resultados(self.aba_resultados)
        self._montar_sql(self.aba_sql)
        self._montar_rodape()

    def _montar_entrada(self, pai):
        cnpjs = ttk.Labelframe(pai, text="CNPJs", padding=8)
        cnpjs.pack(fill="both", expand=True)
        corpo = ttk.Frame(cnpjs)
        corpo.pack(fill="both", expand=True)
        self.texto_cnpj = tk.Text(corpo, height=8, wrap="word", font=("Consolas", 10))
        self.texto_cnpj.pack(side="left", fill="both", expand=True)
        rol = ttk.Scrollbar(corpo, command=self.texto_cnpj.yview)
        rol.pack(side="left", fill="y")
        self.texto_cnpj.configure(yscrollcommand=rol.set)

        lado = ttk.Frame(corpo)
        lado.pack(side="left", fill="y", padx=(8, 0))
        ttk.Button(lado, text="Importar arquivo...", command=self.importar_cnpjs).pack(fill="x", pady=2)
        ttk.Button(lado, text="Validar lista", command=self.validar).pack(fill="x", pady=2)
        ttk.Button(lado, text="Limpar tudo", command=self.limpar).pack(fill="x", pady=2)
        self.lbl_lista = ttk.Label(lado, text="Nenhum CNPJ carregado.", wraplength=160, foreground="#555")
        self.lbl_lista.pack(fill="x", pady=(8, 0))

        base = ttk.Labelframe(pai, text="Base atual da PESSOAS", padding=8)
        base.pack(fill="x", pady=(10, 0))
        ttk.Label(
            base,
            text="CSV exportado do Firebird/IBExpert ou leitura direta da tabela PESSOAS no Firebird. "
                 "No CSV, o cabeçalho precisa conter CNPJCPF_PESS. "
                 "Sem a base não há comparação: todo CNPJ vira UPDATE cego e nenhum INSERT é gerado.",
            wraplength=1050,
            foreground="#555",
        ).pack(fill="x", pady=(0, 6))
        linha = ttk.Frame(base)
        linha.pack(fill="x")
        ttk.Button(linha, text="Importar base (CSV/TXT)...", command=self.importar_base).pack(side="left")
        ttk.Button(linha, text="Ler do banco (Firebird)...", command=self.abrir_firebird).pack(side="left", padx=6)
        ttk.Button(linha, text="Ver SELECT de exportação", command=self.mostrar_select).pack(side="left", padx=6)
        ttk.Button(linha, text="Remover base", command=self.remover_base).pack(side="left")
        self.lbl_base = ttk.Label(linha, text="Nenhuma base carregada.", foreground="#8a6d00")
        self.lbl_base.pack(side="left", padx=12)

    def _montar_regras(self, pai):
        geracao = ttk.Labelframe(pai, text="Geração de SQL", padding=8)
        geracao.pack(fill="x")

        l1 = ttk.Frame(geracao)
        l1.pack(fill="x", pady=2)
        self.var_modo = tk.StringVar(value="SMART")
        ttk.Label(l1, text="Modo:").pack(side="left")
        cb_modo = ttk.Combobox(l1, state="readonly", width=44, values=[r for r, _ in MODOS])
        cb_modo.current(0)
        cb_modo.pack(side="left", padx=(4, 14))
        cb_modo.bind("<<ComboboxSelected>>", lambda e: self._modo_mudou(MODOS[cb_modo.current()][1]))

        self.var_tipo = tk.StringVar(value="cliente")
        ttk.Label(l1, text="Cadastro:").pack(side="left")
        cb_tipo = ttk.Combobox(l1, state="readonly", width=20, values=[r for r, _ in TIPOS])
        cb_tipo.current(0)
        cb_tipo.pack(side="left", padx=(4, 0))
        cb_tipo.bind("<<ComboboxSelected>>", lambda e: self._config_mudou(TIPOS[cb_tipo.current()][1], self.var_tipo))

        l2 = ttk.Frame(geracao)
        l2.pack(fill="x", pady=2)
        self.var_cod_auto = tk.BooleanVar(value=True)
        self.chk_cod = ttk.Checkbutton(
            l2,
            text="COD_PESS dos novos por MAX(COD_PESS)+1 no momento da execução",
            variable=self.var_cod_auto,
            command=self._atualizar_cod,
        )
        self.chk_cod.pack(side="left")
        ttk.Label(l2, text="COD_PESS inicial:").pack(side="left", padx=(14, 4))
        self.var_cod_inicio = tk.StringVar(value="1")
        self.spin_cod = ttk.Spinbox(l2, from_=1, to=99999999, width=12, textvariable=self.var_cod_inicio, state="disabled")
        self.spin_cod.pack(side="left")

        l3 = ttk.Frame(geracao)
        l3.pack(fill="x", pady=(6, 0))
        self.var_preservar = tk.BooleanVar(value=True)
        self.var_inativos = tk.BooleanVar(value=False)
        self.var_forcar = tk.BooleanVar(value=False)
        ttk.Checkbutton(l3, text="Preservar dados existentes quando a API vier vazia", variable=self.var_preservar, command=self.recalcular).pack(anchor="w")
        ttk.Checkbutton(l3, text="Permitir SQL para CNPJ não ativo (BAIXADO, INAPTO, SUSPENSO)", variable=self.var_inativos, command=self.recalcular).pack(anchor="w")
        ttk.Checkbutton(l3, text="Forçar SIT_PESS = 'ATIVO' (sem isso a situação vem mapeada da Receita)", variable=self.var_forcar, command=self.recalcular).pack(anchor="w")

        campos = ttk.Labelframe(pai, text="Campos que podem ser gravados", padding=8)
        campos.pack(fill="both", expand=True, pady=(10, 0))
        acoes = ttk.Frame(campos)
        acoes.pack(fill="x", pady=(0, 6))
        ttk.Button(acoes, text="Marcar todos", command=lambda: self._marcar_campos("todos")).pack(side="left")
        ttk.Button(acoes, text="Só os seguros", command=lambda: self._marcar_campos("seguros")).pack(side="left", padx=6)
        ttk.Button(acoes, text="Desmarcar todos", command=lambda: self._marcar_campos("nenhum")).pack(side="left")

        grade = ttk.Frame(campos)
        grade.pack(fill="both", expand=True)
        self.vars_campos = {}
        for i, c in enumerate(core.FIELD_DEFS):
            var = tk.BooleanVar(value=c.key in core.CAMPOS_PADRAO)
            self.vars_campos[c.key] = var
            ttk.Checkbutton(grade, text=f"{c.label}  ({c.db})", variable=var, command=self.recalcular).grid(
                row=i // 4, column=i % 4, sticky="w", padx=4, pady=2
            )
        for col in range(4):
            grade.columnconfigure(col, weight=1)

        consulta = ttk.Labelframe(pai, text="Consulta", padding=8)
        consulta.pack(fill="x", pady=(10, 0))
        l4 = ttk.Frame(consulta)
        l4.pack(fill="x")
        self.var_api = tk.StringVar(value="auto")
        ttk.Label(l4, text="API:").pack(side="left")
        cb_api = ttk.Combobox(l4, state="readonly", width=42, values=[r for r, _ in APIS])
        cb_api.current(0)
        cb_api.pack(side="left", padx=(4, 14))
        cb_api.bind("<<ComboboxSelected>>", lambda e: self.var_api.set(APIS[cb_api.current()][1]))

        self.var_delay = tk.StringVar(value="10")
        ttk.Label(l4, text="Intervalo (s):").pack(side="left")
        ttk.Spinbox(l4, from_=0, to=120, width=6, textvariable=self.var_delay).pack(side="left", padx=(4, 14))
        self.var_workers = tk.StringVar(value="1")
        ttk.Label(l4, text="Simultâneas:").pack(side="left")
        ttk.Spinbox(l4, from_=1, to=8, width=4, textvariable=self.var_workers).pack(side="left", padx=(4, 14))
        self.var_tentativas = tk.StringVar(value="2")
        ttk.Label(l4, text="Tentativas por API:").pack(side="left")
        ttk.Spinbox(l4, from_=1, to=5, width=4, textvariable=self.var_tentativas).pack(side="left", padx=(4, 0))

        ttk.Label(
            consulta,
            text="Intervalo baixo com muitas consultas simultâneas provoca bloqueio HTTP 429 nas APIs públicas.",
            foreground="#8a6d00",
            wraplength=1050,
        ).pack(fill="x", pady=(6, 0))

    def _montar_resultados(self, pai):
        acoes = ttk.Frame(pai)
        acoes.pack(fill="x", pady=(0, 6))
        ttk.Button(acoes, text="Incluir/excluir selecionado", command=self.alternar_inclusao).pack(side="left")
        ttk.Button(acoes, text="Reconsultar com erro", command=self.reconsultar_erros).pack(side="left", padx=6)
        self.lbl_metricas = ttk.Label(acoes, text="", foreground="#555")
        self.lbl_metricas.pack(side="right")

        quadro = ttk.Frame(pai)
        quadro.pack(fill="both", expand=True)
        self.tabela = ttk.Treeview(quadro, columns=[c[0] for c in COLUNAS], show="headings", selectmode="browse")
        for chave, titulo, largura in COLUNAS:
            self.tabela.heading(chave, text=titulo)
            self.tabela.column(chave, width=largura, anchor="w", stretch=(chave in ("razao", "campos", "alertas")))
        self.tabela.pack(side="left", fill="both", expand=True)
        rol = ttk.Scrollbar(quadro, command=self.tabela.yview)
        rol.pack(side="left", fill="y")
        self.tabela.configure(yscrollcommand=rol.set)
        self.tabela.tag_configure("erro", foreground="#b00020")
        self.tabela.tag_configure("revisao", foreground="#8a6d00")
        self.tabela.tag_configure("excluido", foreground="#888")
        self.tabela.tag_configure("semalt", foreground="#0a7d25")
        self.tabela.bind("<Double-1>", lambda e: self.ver_detalhe())

    def _montar_sql(self, pai):
        acoes = ttk.Frame(pai)
        acoes.pack(fill="x", pady=(0, 6))
        ttk.Button(acoes, text="Atualizar prévia", command=self.atualizar_previa).pack(side="left")
        ttk.Button(acoes, text="Salvar SQL e CSV", command=self.salvar_saidas).pack(side="left", padx=6)
        ttk.Label(acoes, text="Revise e faça backup antes de executar em produção.", foreground="#8a6d00").pack(side="left", padx=12)

        self.texto_sql = tk.Text(pai, wrap="none", font=("Consolas", 9))
        self.texto_sql.pack(side="left", fill="both", expand=True)
        rol = ttk.Scrollbar(pai, command=self.texto_sql.yview)
        rol.pack(side="left", fill="y")
        self.texto_sql.configure(yscrollcommand=rol.set)

    def _montar_rodape(self):
        rodape = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        rodape.pack(fill="x")

        acoes = ttk.Frame(rodape)
        acoes.pack(fill="x")
        self.btn_iniciar = ttk.Button(acoes, text="Consultar", command=self.iniciar)
        self.btn_iniciar.pack(side="left")
        self.btn_parar = ttk.Button(acoes, text="Parar", command=self.parar, state="disabled")
        self.btn_parar.pack(side="left", padx=6)
        self.btn_descartar = ttk.Button(acoes, text="Descartar progresso", command=self.descartar_progresso)
        self.btn_descartar.pack(side="left")

        self.progresso = ttk.Progressbar(rodape, mode="determinate")
        self.progresso.pack(fill="x", pady=(6, 2))
        self.lbl_status = ttk.Label(rodape, text="Pronto.")
        self.lbl_status.pack(fill="x")

        self.log = tk.Text(rodape, height=7, wrap="word", font=("Consolas", 9), state="disabled")
        self.log.pack(side="left", fill="both", expand=True, pady=(6, 0))
        rol = ttk.Scrollbar(rodape, command=self.log.yview)
        rol.pack(side="left", fill="y", pady=(6, 0))
        self.log.configure(yscrollcommand=rol.set)
        self.log.tag_configure("ok", foreground="#0a7d25")
        self.log.tag_configure("erro", foreground="#b00020")
        self.log.tag_configure("aviso", foreground="#8a6d00")

    # ------------------------------------------------------------- utilitarios

    def _escrever(self, texto, tag=None):
        self.log.configure(state="normal")
        self.log.insert("end", texto + "\n", tag or "")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _config_mudou(self, valor, var):
        var.set(valor)
        self.recalcular()

    def _modo_mudou(self, modo):
        self.var_modo.set(modo)
        self._atualizar_cod()
        self.recalcular()

    def _atualizar_cod(self):
        manual = (self.var_modo.get() == "UPSERT") or not self.var_cod_auto.get()
        self.spin_cod.configure(state="normal" if manual else "disabled")
        self.chk_cod.configure(state="disabled" if self.var_modo.get() == "UPSERT" else "normal")

    def _marcar_campos(self, alvo):
        for key, var in self.vars_campos.items():
            if alvo == "todos":
                var.set(True)
            elif alvo == "nenhum":
                var.set(False)
            else:
                var.set(key in core.CAMPOS_PADRAO)
        self.recalcular()

    def _carregar_arquivos_padrao(self):
        lista = core.caminho("CNPJ.txt")
        if os.path.exists(lista):
            try:
                with open(lista, encoding="utf-8") as f:
                    self.texto_cnpj.insert("1.0", f.read())
                self.validar(silencioso=True)
                self._escrever(f"CNPJ.txt carregado de {lista}")
            except Exception as exc:
                self._escrever(f"Não foi possível ler CNPJ.txt: {exc}", "aviso")

        base = core.caminho("BASE_PESSOAS.csv")
        if os.path.exists(base):
            self._ler_base(base, silencioso=True)

    def _verificar_progresso_salvo(self):
        salvos, cfg = core.carregar_progresso()
        if not salvos:
            return
        ok = sum(1 for r in salvos if r.status == "OK")
        if messagebox.askyesno(
            TITULO,
            f"Há um lote anterior salvo com {ok} consulta(s) concluída(s) de {len(salvos)}.\n\n"
            "Deseja retomar esse lote? Responder Não descarta o progresso salvo.",
        ):
            self.resultados = salvos
            if cfg:
                self._aplicar_config(cfg)
            self._escrever(f"Progresso restaurado: {ok} concluído(s), {len(salvos) - ok} com erro.", "aviso")
            self.recalcular()
        else:
            core.limpar_progresso()

    def _aplicar_config(self, cfg):
        self.var_modo.set(cfg.tipo_sql)
        self.var_tipo.set(cfg.tipo_cadastro)
        self.var_api.set(cfg.estrategia)
        self.var_delay.set(f"{cfg.delay:g}")
        self.var_workers.set(str(cfg.workers))
        self.var_tentativas.set(str(cfg.tentativas))
        self.var_preservar.set(cfg.preservar_vazios)
        self.var_inativos.set(cfg.incluir_inativos)
        self.var_forcar.set(cfg.situacao_sempre_ativo)
        self.var_cod_auto.set(cfg.cod_strategy == "AUTO")
        self.var_cod_inicio.set(str(cfg.cod_inicio))
        for key, var in self.vars_campos.items():
            var.set(key in cfg.campos)
        self._atualizar_cod()

    def _config_atual(self):
        def numero(var, padrao):
            try:
                return float(str(var.get()).replace(",", "."))
            except ValueError:
                return padrao

        manual = (self.var_modo.get() == "UPSERT") or not self.var_cod_auto.get()
        return core.Config(
            tipo_sql=self.var_modo.get(),
            tipo_cadastro=self.var_tipo.get(),
            campos=[k for k, v in self.vars_campos.items() if v.get()],
            preservar_vazios=self.var_preservar.get(),
            incluir_inativos=self.var_inativos.get(),
            situacao_sempre_ativo=self.var_forcar.get(),
            estrategia=self.var_api.get(),
            delay=numero(self.var_delay, 10.0),
            workers=int(numero(self.var_workers, 1)),
            tentativas=int(numero(self.var_tentativas, 2)),
            cod_strategy="MANUAL" if manual else "AUTO",
            cod_inicio=int(numero(self.var_cod_inicio, 1)),
        ).normalizar()

    # -------------------------------------------------------------- entrada

    def importar_cnpjs(self):
        arquivo = filedialog.askopenfilename(
            title="Selecione a lista de CNPJs",
            filetypes=[("Texto ou CSV", "*.txt *.csv"), ("Todos os arquivos", "*.*")],
        )
        if not arquivo:
            return
        try:
            with open(arquivo, encoding="utf-8", errors="replace") as f:
                conteudo = f.read()
        except Exception as exc:
            messagebox.showerror(TITULO, f"Não foi possível ler o arquivo:\n{exc}")
            return
        self.texto_cnpj.delete("1.0", "end")
        self.texto_cnpj.insert("1.0", conteudo)
        self.validar()

    def importar_base(self):
        arquivo = filedialog.askopenfilename(
            title="Selecione o CSV da base PESSOAS",
            filetypes=[("CSV ou texto", "*.csv *.txt"), ("Todos os arquivos", "*.*")],
        )
        if arquivo:
            self._ler_base(arquivo)

    def _ler_base(self, arquivo, silencioso=False):
        try:
            self.base_map = core.carregar_base_arquivo(arquivo)
        except (OSError, ValueError) as exc:
            self.base_map = {}
            if not silencioso:
                messagebox.showerror(TITULO, f"Não foi possível ler a base:\n{exc}")
            self._escrever(f"Falha ao ler a base: {exc}", "erro")
            self.lbl_base.configure(text="Nenhuma base carregada.", foreground="#8a6d00")
            return
        self.lbl_base.configure(text=f"{len(self.base_map)} registro(s) · {os.path.basename(arquivo)}", foreground="#0a7d25")
        self._escrever(f"Base Redsis carregada: {len(self.base_map)} registro(s) de {arquivo}", "ok")
        self.recalcular()

    def remover_base(self):
        self.base_map = {}
        self.lbl_base.configure(text="Nenhuma base carregada.", foreground="#8a6d00")
        self._escrever("Base removida; sem comparação com o cadastro atual.", "aviso")
        self.recalcular()

    # ------------------------------------------------------------ Firebird

    def abrir_firebird(self):
        """Janela de conexao para ler a PESSOAS direto do Firebird."""
        cfg = fb.carregar_conexao()
        janela = tk.Toplevel(self.root)
        janela.title("Ler base PESSOAS do Firebird")
        janela.transient(self.root)
        janela.resizable(False, False)

        corpo = ttk.Frame(janela, padding=12)
        corpo.pack(fill="both", expand=True)

        driver = fb.driver_disponivel()
        if driver:
            aviso = f"Driver detectado: {driver}"
            cor = "#0a7d25"
        else:
            aviso = ("Nenhum driver Firebird instalado. Rode:  pip install firebird-driver  "
                     "(Firebird 3/4/5) ou  pip install fdb  (Firebird 2.5/3).")
            cor = "#b00020"
        ttk.Label(corpo, text=aviso, foreground=cor, wraplength=520).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))

        campos = {}

        def campo(rotulo, chave, valor, largura=42, senha=False, linha=0):
            ttk.Label(corpo, text=rotulo).grid(row=linha, column=0, sticky="e", padx=(0, 6), pady=3)
            var = tk.StringVar(value=str(valor))
            entrada = ttk.Entry(corpo, textvariable=var, width=largura,
                                show="*" if senha else "")
            entrada.grid(row=linha, column=1, sticky="w", pady=3)
            campos[chave] = var
            return entrada

        campo("Servidor (host):", "host", cfg.host, linha=1)
        campo("Porta:", "porta", cfg.porta, largura=10, linha=2)
        entrada_banco = campo("Banco (.fdb ou alias):", "banco", cfg.banco, linha=3)
        ttk.Button(corpo, text="Procurar...",
                   command=lambda: self._escolher_fdb(campos["banco"])).grid(
            row=3, column=2, sticky="w", padx=6)
        campo("Usuário:", "usuario", cfg.usuario, largura=20, linha=4)
        campo("Senha:", "senha", cfg.senha, largura=20, senha=True, linha=5)

        ttk.Label(corpo, text="Charset:").grid(row=6, column=0, sticky="e", padx=(0, 6), pady=3)
        var_charset = tk.StringVar(value=cfg.charset)
        cb = ttk.Combobox(corpo, textvariable=var_charset, values=fb.CHARSETS,
                          state="readonly", width=18)
        cb.grid(row=6, column=1, sticky="w", pady=3)

        campo("Tabela:", "tabela", cfg.tabela, largura=20, linha=7)
        campo("Coluna do CNPJ:", "coluna_cnpj", cfg.coluna_cnpj, largura=20, linha=8)
        campo("Limite de linhas (0 = tudo):", "limite", cfg.limite, largura=10, linha=9)

        var_salvar = tk.BooleanVar(value=cfg.salvar_senha)
        ttk.Checkbutton(
            corpo,
            text="Gravar a senha em conexao_firebird.json (texto puro, sem criptografia)",
            variable=var_salvar,
        ).grid(row=10, column=0, columnspan=3, sticky="w", pady=(8, 0))

        lbl_status = ttk.Label(corpo, text="", wraplength=520, foreground="#555")
        lbl_status.grid(row=11, column=0, columnspan=3, sticky="w", pady=(10, 0))

        def montar_cfg():
            atual = fb.Conexao(
                host=campos["host"].get(),
                porta=campos["porta"].get(),
                banco=campos["banco"].get(),
                usuario=campos["usuario"].get(),
                senha=campos["senha"].get(),
                charset=var_charset.get(),
                tabela=campos["tabela"].get(),
                coluna_cnpj=campos["coluna_cnpj"].get(),
                limite=campos["limite"].get(),
                salvar_senha=var_salvar.get(),
            )
            return atual.normalizar()

        def testar():
            try:
                atual = montar_cfg()
                lbl_status.configure(text="Conectando...", foreground="#555")
                janela.update_idletasks()
                info = fb.testar(atual)
            except fb.ErroFirebird as exc:
                lbl_status.configure(text=str(exc), foreground="#b00020")
                return
            faltando = info["colunas_faltando"]
            texto = (f"Conexão OK via {info['driver']}. Tabela {info['tabela']} com "
                     f"{info['colunas_total']} coluna(s); {len(info['colunas_usadas'])} serão lidas.")
            if faltando:
                texto += " Ausentes (serão ignoradas): " + ", ".join(faltando)
            lbl_status.configure(text=texto, foreground="#0a7d25")

        def carregar():
            try:
                atual = montar_cfg()
                lbl_status.configure(text="Lendo a tabela...", foreground="#555")
                janela.update_idletasks()
                base, info = fb.ler_base(atual)
            except fb.ErroFirebird as exc:
                lbl_status.configure(text=str(exc), foreground="#b00020")
                self._escrever(f"Falha ao ler o Firebird: {exc}", "erro")
                return
            try:
                fb.salvar_conexao(atual)
            except OSError as exc:
                self._escrever(f"Conexão lida, mas não deu para gravar as preferências: {exc}", "aviso")

            self.base_map = base
            self.lbl_base.configure(
                text=f"{len(base)} registro(s) · Firebird {atual.host}:{atual.porta}",
                foreground="#0a7d25",
            )
            self._escrever(
                f"Base lida do Firebird: {info['validas']} CNPJ(s) válidos de {info['lidas']} linha(s); "
                f"{info['ignoradas']} ignorada(s) por CNPJ ausente, CPF ou tamanho diferente de 14.",
                "ok",
            )
            self.recalcular()
            janela.destroy()

        botoes = ttk.Frame(corpo)
        botoes.grid(row=12, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(botoes, text="Testar conexão", command=testar).pack(side="left", padx=4)
        ttk.Button(botoes, text="Carregar base", command=carregar).pack(side="left", padx=4)
        ttk.Button(botoes, text="Fechar", command=janela.destroy).pack(side="left", padx=4)

        entrada_banco.focus_set()

    def _escolher_fdb(self, var):
        arquivo = filedialog.askopenfilename(
            title="Selecione o arquivo do banco",
            filetypes=[("Banco Firebird", "*.fdb *.gdb"), ("Todos os arquivos", "*.*")],
        )
        if arquivo:
            var.set(arquivo)

    def mostrar_select(self):
        sql = (
            "SELECT\n  " + ",\n  ".join([
                "COD_PESS", "CNPJCPF_PESS", "NOM_PESS", "FANT_PESS", "IE_PESS", "END_PESS",
                "NUM_PESS", "COMPL_PESS", "BAIRRO_PESS", "MUN_PESS", "COD_MUN", "UF_PESS",
                "CEP_PESS", "FONE_PESS", "FONE2_PESS", "EMAIL_PESS", "SIT_PESS", "TIPO_PESS",
                "CNAEP_PESS", "CLIENT_PESS", "FORN_PESS",
            ])
            + "\nFROM PESSOAS\nWHERE CNPJCPF_PESS IS NOT NULL;"
        )
        janela = tk.Toplevel(self.root)
        janela.title("SELECT para exportar a base")
        janela.geometry("560x520")
        caixa = tk.Text(janela, wrap="none", font=("Consolas", 10))
        caixa.pack(fill="both", expand=True, padx=8, pady=8)
        caixa.insert("1.0", sql)
        ttk.Button(janela, text="Copiar", command=lambda: (self.root.clipboard_clear(), self.root.clipboard_append(sql))).pack(pady=(0, 8))

    def validar(self, silencioso=False):
        self.validos, invalidos = core.parse_lista(self.texto_cnpj.get("1.0", "end"))
        self.lbl_lista.configure(text=f"{len(self.validos)} válido(s)\n{len(invalidos)} inválido(s) ignorado(s)")
        if not silencioso:
            self._escrever(f"Lista validada: {len(self.validos)} válido(s), {len(invalidos)} inválido(s).")
            if invalidos:
                amostra = ", ".join(invalidos[:20]) + ("..." if len(invalidos) > 20 else "")
                self._escrever(f"Inválidos: {amostra}", "aviso")
        return self.validos

    def limpar(self):
        if not messagebox.askyesno(TITULO, "Limpar CNPJs, base, resultados e progresso salvo?"):
            return
        self.texto_cnpj.delete("1.0", "end")
        self.validos = []
        self.resultados = []
        self.base_map = {}
        core.limpar_progresso()
        self.progresso.configure(value=0)
        self.lbl_lista.configure(text="Nenhum CNPJ carregado.")
        self.lbl_base.configure(text="Nenhuma base carregada.", foreground="#8a6d00")
        self.recalcular()
        self._escrever("Tudo limpo.")

    def descartar_progresso(self):
        if not messagebox.askyesno(TITULO, "Descartar o progresso salvo? As consultas já feitas serão refeitas."):
            return
        core.limpar_progresso()
        self.resultados = []
        self.recalcular()
        self._escrever("Progresso descartado.", "aviso")

    # ------------------------------------------------------------ resultados

    def recalcular(self):
        cfg = self._config_atual()
        self._renderizar_tabela(cfg)
        self._atualizar_metricas(cfg)

    def _renderizar_tabela(self, cfg):
        # a tabela e recriada a cada recalculo; guarda a linha selecionada pelo
        # CNPJ para o operador nao perder o registro em que estava trabalhando
        selecionado = self._selecionado()
        cnpj_selecionado = selecionado.cnpj if selecionado else None
        posicao = self.tabela.yview()[0] if self.tabela.get_children() else 0.0

        self.tabela.delete(*self.tabela.get_children())
        for i, r in enumerate(self.resultados):
            base = self.base_map.get(r.cnpj)
            situacao = core.status_registro(r, self.base_map, cfg)
            alterados = core.campos_alterados(r, base, cfg) if r.status == "OK" else []
            alertas = core.avaliar_qualidade(r)
            tag = {"ERRO": "erro", "REVISAO": "revisao", "EXCLUIDO": "excluido", "SEM_ALTERACAO": "semalt"}.get(situacao, "")
            self.tabela.insert(
                "", "end", iid=str(i), tags=(tag,),
                values=(
                    core.formatar_cnpj(r.cnpj),
                    core.origem(r, self.base_map),
                    situacao,
                    r.razao_social or (r.erro[:60] if r.erro else ""),
                    r.api or "",
                    len(alterados),
                    " | ".join(d.campo.label for d in alterados),
                    " | ".join(texto for _, texto in alertas),
                ),
            )

        if cnpj_selecionado is not None:
            for i, r in enumerate(self.resultados):
                if r.cnpj == cnpj_selecionado:
                    self.tabela.selection_set(str(i))
                    self.tabela.focus(str(i))
                    break
        if posicao:
            self.tabela.yview_moveto(posicao)

    def _atualizar_metricas(self, cfg):
        ok = sum(1 for r in self.resultados if r.status == "OK")
        erros = sum(1 for r in self.resultados if r.status != "OK")
        novos = sum(1 for r in self.resultados if r.status == "OK" and core.origem(r, self.base_map) == "NOVO")
        no_sql = sum(1 for r in self.resultados if core.pode_gerar(r, self.base_map, cfg))
        self.lbl_metricas.configure(text=f"Sucesso: {ok} | Erros: {erros} | Novos: {novos} | No SQL: {no_sql}")

    def _selecionado(self):
        sel = self.tabela.selection()
        if not sel:
            return None
        indice = int(sel[0])
        return self.resultados[indice] if indice < len(self.resultados) else None

    def alternar_inclusao(self):
        r = self._selecionado()
        if not r:
            messagebox.showinfo(TITULO, "Selecione uma linha na tabela.")
            return
        r.incluir = not bool(r.incluir)
        self._escrever(f"{core.formatar_cnpj(r.cnpj)}: {'incluído' if r.incluir else 'excluído'} da geração.", "aviso")
        self.recalcular()
        self._salvar_progresso_silencioso()

    ROTULOS_DIFF = {"same": "Igual", "preserved": "Preservado", "new": "Novo cadastro", "changed": "Alterado"}

    def ver_detalhe(self):
        """Comparação Redsis × Receita, com escolha de campo só para este CNPJ."""
        r = self._selecionado()
        if not r:
            return

        janela = tk.Toplevel(self.root)
        janela.title(f"Redsis × Receita • {core.formatar_cnpj(r.cnpj)}")
        janela.geometry("960x640")
        janela.transient(self.root)

        if r.status != "OK":
            ttk.Label(janela, text=r.erro, wraplength=900, foreground="#b00020", padding=12).pack(fill="x")
            return

        alertas = core.avaliar_qualidade(r)
        if alertas:
            ttk.Label(
                janela,
                text="Alertas: " + " • ".join(t for _, t in alertas),
                wraplength=900, foreground="#8a6d00", padding=(12, 8),
            ).pack(fill="x")

        ttk.Label(
            janela,
            text="A marcação abaixo vale apenas para este CNPJ. Campos desligados na aba Regras ficam bloqueados aqui.",
            wraplength=900, foreground="#555", padding=(12, 4),
        ).pack(fill="x")

        area = ttk.Frame(janela, padding=(10, 6))
        area.pack(fill="both", expand=True)

        canvas = tk.Canvas(area, highlightthickness=0)
        canvas.pack(side="left", fill="both", expand=True)
        rol = ttk.Scrollbar(area, orient="vertical", command=canvas.yview)
        rol.pack(side="left", fill="y")
        canvas.configure(yscrollcommand=rol.set)
        grade = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=grade, anchor="nw")
        grade.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        for col, (titulo, largura) in enumerate(zip(
            ("Grava", "Campo", "Redsis atual", "Receita/API", "Resultado"), (60, 170, 260, 260, 120)
        )):
            ttk.Label(grade, text=titulo, font=("Segoe UI", 9, "bold"), width=largura // 8).grid(
                row=0, column=col, sticky="w", padx=4, pady=(0, 4)
            )

        self._montar_linhas_detalhe(grade, r, janela)

        rodape = ttk.Frame(janela, padding=(10, 0, 10, 10))
        rodape.pack(fill="x")
        ttk.Button(rodape, text="Restaurar seleção global", command=lambda: self._limpar_selecao_registro(r, janela)).pack(side="left")
        ttk.Button(rodape, text="Fechar", command=janela.destroy).pack(side="right")

    def _montar_linhas_detalhe(self, grade, r, janela):
        cfg = self._config_atual()
        base = self.base_map.get(r.cnpj)
        for linha, d in enumerate(core.diffs(r, base, cfg), start=1):
            key = d.campo.key
            global_ligado = key in cfg.campos
            var = tk.BooleanVar(value=core.campo_habilitado(r, key, cfg))
            chk = ttk.Checkbutton(
                grade, variable=var,
                command=lambda k=key, v=var, j=janela: self._marcar_campo_registro(r, k, v.get(), j),
            )
            if not global_ligado:
                chk.state(["disabled"])
            chk.grid(row=linha, column=0, sticky="w", padx=4)

            cor = "#555" if not global_ligado else ("#8a6d00" if d.state == "changed" else "#000")
            ttk.Label(grade, text=d.campo.label, foreground=cor).grid(row=linha, column=1, sticky="w", padx=4)
            ttk.Label(grade, text=self._curto(d.base)).grid(row=linha, column=2, sticky="w", padx=4)
            ttk.Label(grade, text=self._curto(d.api)).grid(row=linha, column=3, sticky="w", padx=4)
            ttk.Label(grade, text=self.ROTULOS_DIFF.get(d.state, d.state)).grid(row=linha, column=4, sticky="w", padx=4)

    @staticmethod
    def _curto(valor):
        texto = str(valor) if core.tem_valor(valor) else "—"
        return texto if len(texto) <= 40 else texto[:37] + "..."

    def _marcar_campo_registro(self, r, key, ligado, janela):
        cfg = self._config_atual()
        # Marcar igual à regra global não precisa virar exceção do registro.
        if ligado == (key in cfg.campos):
            r.campos_selecionados.pop(key, None)
        else:
            r.campos_selecionados[key] = bool(ligado)
        self._escrever(
            f"{core.formatar_cnpj(r.cnpj)}: campo {core.CAMPOS_POR_KEY[key].label} "
            f"{'ligado' if ligado else 'desligado'} só neste registro.",
            "aviso",
        )
        self.recalcular()
        self._salvar_progresso_silencioso()

    def _limpar_selecao_registro(self, r, janela):
        r.campos_selecionados.clear()
        self._escrever(f"{core.formatar_cnpj(r.cnpj)}: voltou a seguir a seleção global de campos.", "aviso")
        self.recalcular()
        self._salvar_progresso_silencioso()
        janela.destroy()
        self.ver_detalhe()

    def _salvar_progresso_silencioso(self):
        """Grava as escolhas por registro para que sobrevivam a um fechamento."""
        if not self.resultados:
            return
        try:
            core.salvar_progresso(self.resultados, self._config_atual())
        except Exception as exc:
            self._escrever(f"Aviso: não foi possível salvar o progresso ({exc}).", "aviso")

    def reconsultar_erros(self):
        com_erro = [r.cnpj for r in self.resultados if r.status != "OK"]
        if not com_erro:
            messagebox.showinfo(TITULO, "Nenhum registro com erro.")
            return
        self.resultados = [r for r in self.resultados if r.status == "OK"]
        self._escrever(f"Reconsultando {len(com_erro)} registro(s) com erro.", "aviso")
        self.iniciar(somente=com_erro)

    # -------------------------------------------------------------- execucao

    def iniciar(self, somente=None):
        if self.thread and self.thread.is_alive():
            return
        alvo = somente or self.validar(silencioso=True)
        if not alvo:
            messagebox.showwarning(TITULO, "Nenhum CNPJ válido na lista.")
            return

        cfg = self._config_atual()
        if not cfg.campos:
            messagebox.showwarning(TITULO, "Nenhum campo marcado na aba Regras: o SQL sairia vazio.")
            return
        if cfg.tipo_sql != "UPDATE" and not self.base_map:
            if not messagebox.askyesno(
                TITULO,
                f"O modo {cfg.tipo_sql} precisa da base da PESSOAS para saber o que é novo.\n\n"
                "Sem base, todo CNPJ é tratado como existente e nenhum INSERT é gerado. Continuar assim?",
            ):
                return

        concluidos = {r.cnpj for r in self.resultados if r.status == "OK"}
        pendentes = [c for c in alvo if c not in concluidos]
        if not pendentes:
            messagebox.showinfo(TITULO, "Todos os CNPJs já foram consultados com sucesso.")
            return

        self.parar_flag.clear()
        self.btn_iniciar.configure(state="disabled")
        self.btn_parar.configure(state="normal")
        self.progresso.configure(value=0, maximum=len(pendentes))
        self.abas.select(self.aba_resultados)
        self._escrever(
            f"Iniciando {len(pendentes)} consulta(s) | modo {cfg.tipo_sql} | intervalo {cfg.delay:g}s | "
            f"{cfg.workers} simultânea(s) | API {core.ESTRATEGIAS[cfg.estrategia]}"
        )

        def trabalho():
            try:
                resultados = core.processar(
                    alvo,
                    cfg,
                    on_resultado=lambda r, i, t: self.eventos.put(("resultado", (r, i, t))),
                    log=lambda m: self.eventos.put(("log", m)),
                    parar=self.parar_flag.is_set,
                    anteriores=self.resultados,
                )
                self.eventos.put(("fim", resultados))
            except Exception as exc:
                self.eventos.put(("falha", str(exc)))

        self.thread = threading.Thread(target=trabalho, daemon=True)
        self.thread.start()

    def parar(self):
        self.parar_flag.set()
        self.btn_parar.configure(state="disabled")
        self.lbl_status.configure(text="Parando após a consulta em andamento...")
        self._escrever("Parada solicitada. O progresso já feito fica salvo.", "aviso")

    # ------------------------------------------------------------------ saida

    def atualizar_previa(self):
        cfg = self._config_atual()
        sql = core.gerar_sql_completo(self.resultados, self.base_map, cfg)
        self.texto_sql.delete("1.0", "end")
        self.texto_sql.insert("1.0", sql or "-- Nada a gerar com as regras atuais.")
        self.abas.select(self.aba_sql)

    def salvar_saidas(self):
        if not self.resultados:
            messagebox.showinfo(TITULO, "Nenhum resultado para gravar.")
            return
        cfg = self._config_atual()
        try:
            destino_sql, total = core.salvar_sql(self.resultados, cfg, self.base_map)
            destino_csv = core.salvar_csv(self.resultados, cfg, self.base_map)
        except Exception as exc:
            messagebox.showerror(TITULO, f"Falha ao gravar os arquivos:\n{exc}")
            return
        self._escrever(f"SQL gravado ({total} registro(s)): {destino_sql}", "ok")
        self._escrever(f"Relatório gravado: {destino_csv}", "ok")
        messagebox.showinfo(
            TITULO,
            f"{total} registro(s) no SQL.\n\n{destino_sql}\n{destino_csv}\n\nRevise o SQL antes de executar em produção.",
        )

    # ------------------------------------------------------------------ eventos

    def _drenar_eventos(self):
        try:
            while True:
                tipo, dado = self.eventos.get_nowait()
                if tipo == "log":
                    self._escrever(str(dado), "aviso")
                elif tipo == "resultado":
                    self._ao_resultado(*dado)
                elif tipo == "fim":
                    self._ao_fim(dado)
                elif tipo == "falha":
                    self._ao_falha(dado)
        except queue.Empty:
            pass
        self.root.after(100, self._drenar_eventos)

    def _ao_resultado(self, r, indice, total):
        self.progresso.configure(value=indice, maximum=total)
        self.lbl_status.configure(text=f"Consultando {indice} de {total}...")
        self.resultados = [x for x in self.resultados if x.cnpj != r.cnpj] + [r]
        if r.status != "OK":
            self._escrever(f"[{indice}/{total}] {r.cnpj} ERRO: {r.erro}", "erro")
        else:
            marca = " (fallback)" if r.api_fallback else ""
            orig = core.origem(r, self.base_map)
            self._escrever(
                f"[{indice}/{total}] {r.cnpj} {r.razao_social} | {r.api or 'N/I'}{marca} | {orig}",
                "ok" if core.situacao_ativa(r.situacao) else "aviso",
            )
        self.recalcular()

    def _ao_fim(self, resultados):
        self.resultados = resultados
        self.thread = None
        self.btn_iniciar.configure(state="normal")
        self.btn_parar.configure(state="disabled")
        self.recalcular()
        interrompido = self.parar_flag.is_set()
        self.lbl_status.configure(text="Interrompido." if interrompido else "Lote concluído.")
        self._escrever("Lote interrompido." if interrompido else "Lote concluído.", "aviso" if interrompido else "ok")
        if interrompido:
            return
        self.atualizar_previa()
        self.salvar_saidas()
        # Sem erros nao ha o que retomar: o progresso salvo so atrapalharia a proxima abertura.
        if all(r.status == "OK" for r in self.resultados):
            core.limpar_progresso()
            self._escrever("Nenhum erro pendente; progresso salvo removido.")

    def _ao_falha(self, mensagem):
        self.thread = None
        self.btn_iniciar.configure(state="normal")
        self.btn_parar.configure(state="disabled")
        self.lbl_status.configure(text="Falha na execução.")
        self._escrever(f"FALHA: {mensagem}", "erro")
        messagebox.showerror(TITULO, f"Falha na execução:\n{mensagem}")

    def _ao_fechar(self):
        if self.thread and self.thread.is_alive():
            if not messagebox.askyesno(TITULO, "Há um lote em andamento. Fechar mesmo assim?\nO progresso já feito fica salvo."):
                return
            self.parar_flag.set()
        self.root.destroy()


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    icone = core.recurso("icone.ico")
    if os.path.exists(icone):
        try:
            root.iconbitmap(icone)
        except tk.TclError:
            pass
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    main()
