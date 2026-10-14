# Saneamento de Pessoas - Redsis

Ferramenta local para consultar CNPJs, comparar os dados oficiais com a base atual da tabela `PESSOAS` e gerar SQL de saneamento com revisão antes da execução.

## Versão atual

**v4.1**

Fluxo principal:

1. importar CNPJs;
2. importar a base atual da `PESSOAS`;
3. consultar dados oficiais;
4. comparar Redsis × Receita/API;
5. revisar divergências e campos permitidos;
6. gerar SQL somente do necessário.

## O que a V4.1 implementa

- validação completa dos dígitos verificadores do CNPJ;
- bloqueio de CNPJ não ativo por padrão;
- preservação de dados existentes quando a API vier vazia;
- saneamento de telefone, CEP e e-mail;
- escolha de IE priorizando inscrição ativa da mesma UF;
- importação de CSV/TXT da tabela `PESSOAS`;
- reconhecimento automático de `NOVO`, `EXISTENTE` e `SEM BASE`;
- indicadores de novos, existentes, sem alteração e com divergência;
- comparação campo a campo Redsis × Receita/API;
- seleção global dos campos autorizados para UPDATE;
- seleção individual por CNPJ no modal de comparação;
- UPDATE contendo somente campos selecionados e realmente divergentes;
- proteção para não apagar valor existente quando a API não tiver informação;
- SQL de novos cadastros com `COD_PESS` automático usando `MAX(COD_PESS)+1` dentro de `EXECUTE BLOCK`;
- verificação `NOT EXISTS` antes de cada INSERT novo;
- opção manual de `COD_PESS` como fallback;
- WHERE por CNPJ tolerando CNPJ formatado ou sem formatação na base;
- relatório CSV de comparação e divergências;
- seleção de cliente, fornecedor ou ambos sem zerar a marcação oposta.

## Importando a base Redsis

Execute o arquivo:

`EXPORTAR_BASE_PESSOAS.sql`

no IBExpert/Firebird e exporte o resultado como CSV/TXT **com cabeçalho**.

A única coluna obrigatória para reconhecer um cadastro é:

`CNPJCPF_PESS`

Para a comparação completa, use todas as colunas do SELECT fornecido.

A ferramenta aceita `;`, vírgula ou TAB como separador e reconhece CNPJ formatado ou somente numérico.

Também existe o arquivo `MODELO_BASE_PESSOAS.csv` com o cabeçalho esperado.

### Lendo direto do Firebird

Em vez de exportar o CSV, a base pode vir da própria tabela `PESSOAS`.

Instale um dos drivers Python de Firebird:

```bash
pip install firebird-driver
```

`firebird-driver` atende Firebird 3, 4 e 5; para Firebird 2.5 use `pip install fdb`.

#### A biblioteca cliente (`fbclient.dll`)

Os dois drivers precisam do `fbclient.dll`, e ele tem que ser da **mesma arquitetura do Python** — um Python de 64 bits nao carrega DLL de 32 bits, e o erro que aparece (`WinError 193`) nao explica isso.

O campo *Biblioteca cliente* resolve. Deixando vazio, o aplicativo procura sozinho: ao lado do executavel, nas pastas padrao do Firebird (5.0, 4.0, 3.0, 2.5, inclusive as de 32 bits) e no PATH, descartando as de arquitetura incompativel e ficando com a mais nova. Preenchido, usa aquele caminho.

Duas armadilhas comuns:

- um `fbclient.dll` de 32 bits largado na pasta do projeto, vindo de instalacao antiga, e ignorado se o Python for de 64 bits — antes a mensagem era so `The location of Firebird Client Library could not be determined.`;
- o driver carrega a biblioteca **uma vez por processo**. Trocar de caminho depois disso exige fechar e abrir o aplicativo, e a mensagem diz isso.

Com varios servidores Firebird na mesma maquina, confira a porta: cada instancia tem a sua em `RemoteServicePort`, no `firebird.conf` da instalacao.

**No aplicativo Python (`gui.py`)**: botão *Ler do banco (Firebird)...* na seção "Base atual da PESSOAS". Informe host, porta, caminho do `.fdb`, usuário, senha e charset, use *Testar conexão* e depois *Carregar base*.

**No `index.html`**: o navegador não fala com o Firebird, então existe a ponte local `ponte.py`.

```bash
python ponte.py
```

Ela sobe um servidor em `http://127.0.0.1:8777/`, serve o próprio `index.html` e abre o navegador. O bloco *Ler direto do Firebird* aparece na seção 2 só quando a página é aberta por essa ponte — pelo arquivo direto (`file://`) ele fica escondido, porque não há como conectar. Opções: `--porta`, `--sem-navegador`, `--verboso`.

O resultado é idêntico ao do CSV: o mesmo mapa `{CNPJ: registro}`, com o CNPJ reduzido a 14 dígitos e os registros sem CNPJ válido (CPF, campo vazio, tamanho diferente) descartados. Colunas do `EXPORTAR_BASE_PESSOAS.sql` que não existirem na sua tabela são simplesmente ignoradas; o *Testar conexão* lista quais faltam.

Limites de segurança da leitura:

- apenas `SELECT` chega ao banco — nada é gravado na `PESSOAS`;
- nomes de tabela e de coluna passam por validação de identificador, então não dá para injetar SQL pelos campos da tela;
- a ponte escuta somente em `127.0.0.1` e exige um token gerado a cada execução, injetado no HTML que ela mesma serve; outra aba do navegador não consegue usá-la;
- a conexão é gravada em `conexao_firebird.json`, **sem a senha**, a menos que você marque a opção de gravá-la (aí fica em texto puro no arquivo).

## Comparação

Para cada campo, a V4.1 mostra:

- valor atual da Redsis;
- valor retornado pela Receita/API;
- se está igual ou alterado;
- checkbox indicando se aquele campo pode ser atualizado.

A comparação ignora diferenças meramente visuais de maiúsculas/minúsculas, acentos e espaços. Na IE, zeros à esquerda não geram falsa divergência.

## Campos controláveis

A tela permite controlar:

- Razão social;
- Fantasia;
- Inscrição estadual;
- Endereço;
- Número;
- Complemento;
- Bairro;
- Município;
- UF;
- CEP;
- Telefone 1;
- Telefone 2;
- E-mail;
- Situação;
- Tipo;
- CNAE.

Existe um **Perfil seguro** como padrão. `Situação` e `Tipo` ficam fora desse perfil para evitar alterações automáticas desnecessárias.

## Geração inteligente de SQL

### Existentes

Quando a base foi importada, um cadastro existente recebe UPDATE apenas nos campos que:

1. estão autorizados globalmente;
2. não foram desmarcados especificamente naquele CNPJ;
3. estão realmente diferentes;
4. possuem valor utilizável, quando a opção de preservar vazios estiver ligada.

### Novos

No modo recomendado, os novos entram em um `EXECUTE BLOCK`.

O bloco calcula o primeiro código no momento da execução:

```sql
SELECT COALESCE(MAX(COD_PESS), 0) + 1
FROM PESSOAS
INTO :V_COD;
```

Antes de cada INSERT há uma verificação de existência pelo CNPJ.

> Importante: `MAX(COD_PESS)+1` é mais seguro do que digitar uma faixa manualmente, mas não substitui um generator/sequence real. Execute o lote sem outro processo cadastrando pessoas simultaneamente. Se a sua base possuir um generator oficial da `PESSOAS`, ele pode ser incorporado em uma evolução futura.

## Modos de SQL

- **Inteligente**: recomendado. UPDATE para existentes e INSERT para novos.
- **Somente UPDATE**: ignora registros classificados como novos.
- **UPDATE OR INSERT**: mantido para compatibilidade; prefira o modo inteligente quando houver base importada.

### `SET TERM` nos INSERTs

Os novos cadastros saem em um `EXECUTE BLOCK`, que calcula `MAX(COD_PESS)+1` na hora de executar. Por padrao o bloco sai **sem** `SET TERM`.

`SET TERM` nao e SQL: e comando do programa cliente. Quem manda um comando por vez — FireDAC (o app Delphi), JDBC, ADO, o driver Python, o SQL Editor do IBExpert — recebe o `SET TERM` como se fosse SQL e devolve:

```
Dynamic SQL Error
SQL error code = -104
Token unknown - line 1, column 5
TERM
```

Marque *Incluir SET TERM nos INSERTs* (`--set-term` na linha de comando) so para o **isql** e o **Script Executive** do IBExpert, que leem o arquivo como script e precisam saber onde um comando termina.

Nos dois casos o bloco em si e o mesmo, e `tests/test_sql_firebird.py` executa os dois contra um Firebird embarcado de verdade: sem `SET TERM` o banco aceita, com `SET TERM` ele recusa com o erro acima.

## Situação cadastral

Por padrão, CNPJ que não estiver ativo fica fora do SQL. É possível liberar manualmente quando necessário.

`SIT_PESS` também pode ser autorizado na seleção de campos. O perfil seguro não o marca por padrão.

## Aplicativos Python

Mesmas regras do `index.html`, em dois programas que dispensam navegador. O motor fica em `cnpj_core.py` e é compartilhado pelos dois.

- `gui.py` — janela com as etapas Entrada, Regras, Resultados e SQL. Empacotado como `ConsultaCNPJ.exe`.
- `CNPJ.py` — linha de comando. Sem argumentos pergunta cada opção; com argumentos roda direto. Empacotado como `ConsultaCNPJ-CLI.exe`.
- `ponte.py` — ponte local que liga o `index.html` ao Firebird (veja *Lendo direto do Firebird*). Empacotada como `PonteFirebird.exe`, com o `index.html` embutido.

Ambos leem `CNPJ.txt` e `BASE_PESSOAS.csv` da própria pasta, e gravam `updates.sql` e `relatorio_cnpj.csv` ali mesmo. O progresso é salvo em `progresso.json` a cada consulta e pode ser retomado.

```bash
ConsultaCNPJ-CLI.exe --base BASE_PESSOAS.csv --modo SMART --delay 3 --workers 4
```

Principais opções: `--modo SMART|UPDATE|UPSERT`, `--tipo cliente|fornecedor|ambos`, `--api`, `--delay`, `--workers`, `--campos` (veja `--listar-campos`), `--incluir-inativos`, `--forcar-ativo`, `--situacao-receita`, `--cod-inicio`, `--recomecar`.

Sem o CSV da base não há comparação: todo CNPJ é tratado como existente, nenhum INSERT é gerado e todos os campos marcados são gravados.

### Ritmo das consultas

O intervalo é controlado **por API**, não globalmente: uma chamada de fallback à BrasilAPI não consome o intervalo do CNPJ.ws. Um HTTP 429 eleva o intervalo daquela API — respeitando `Retry-After` em segundos ou em data, ou aplicando recuo exponencial de até 120 s quando o cabeçalho não vem — e cada sucesso reduz esse acréscimo pela metade. Parar interrompe na hora, mesmo durante uma espera longa.

### Testes

```bash
python tests/run.py
```

Quatro suítes: motor (lista, progresso, retomada, geração), camada de rede contra um servidor HTTP local, paridade com o `index.html` e interface gráfica. A paridade executa o JavaScript do próprio `index.html` no Node e exige que a saída do Python seja idêntica — se a interface mudar e o Python não acompanhar, o teste acusa. Sem o Node instalado essa suíte é pulada; as outras três não dependem de rede.

### Gerar os executáveis

```bash
python build.py
```

Roda a bateria de testes e só empacota se tudo passar. Use `--sem-teste` para pular. O ícone é gerado por `icone.py` (precisa do Pillow).

Os executáveis não são assinados, então o SmartScreen avisa na primeira execução. Com um certificado de code signing:

```bash
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /f certificado.pfx /p SENHA dist\*.exe
```

## Segurança operacional

Antes de executar em produção:

- faça backup da base;
- revise os itens marcados como revisão;
- confira os INSERTs novos;
- execute preferencialmente primeiro em uma base de teste;
- no modo `MAX(COD_PESS)+1`, não execute simultaneamente com outro processo que esteja cadastrando pessoas.

### Controle de limite da API (v4.3)
A aplicação trata respostas HTTP 429 automaticamente. Quando a API informar `Retry-After`, esse tempo é respeitado; caso contrário é usado backoff progressivo a partir do intervalo configurado. O padrão passou a 10 segundos e pode ser alterado para 5, 15, 30 ou 60 segundos. Registros que ainda falharem podem ser reprocessados individualmente ou pelo botão **Reprocessar erros**, sem refazer as consultas já concluídas.

### Retomada e preservação de progresso (v4.3)

A aplicação salva automaticamente o andamento das consultas no navegador. Ao fechar/reabrir a página, os resultados já obtidos são restaurados e **Consultar válidos** processa apenas CNPJs ainda sem sucesso. CNPJs concluídos não são consultados novamente.

Em caso de HTTP 429, a aplicação respeita `Retry-After` quando disponível, aplica espera progressiva e aumenta temporariamente o intervalo entre as próximas consultas. Se o limite persistir, o registro fica como erro para reprocessamento posterior, sem apagar os demais resultados.

## Fontes de consulta - v4.3

A fonte principal continua sendo **CNPJ.ws**, pois é a integração já utilizada pelo projeto e pode retornar Inscrição Estadual.

Na interface, em **Estratégia de consulta**, existem quatro modos:

- **Automático: CNPJ.ws → BrasilAPI → Minha Receita**: recomendado. Sempre tenta CNPJ.ws primeiro e usa as demais somente em caso de falha.
- **Somente CNPJ.ws (com IE)**: não usa fallback.
- **Somente BrasilAPI**: útil para contingência.
- **Somente Minha Receita**: útil para contingência.

O fallback ocorre em erros de rede, HTTP 429, HTTP 4xx/5xx relevantes ou quando a resposta não contém dados cadastrais suficientes. A tela de revisão mostra qual API respondeu para cada CNPJ.

### Inscrição Estadual em fallback

BrasilAPI e Minha Receita são tratadas como fontes alternativas cadastrais e podem não fornecer IE. Para registros já existentes, com **Preservar dados existentes quando API vier vazia** ativo, a IE atual da Redsis não é apagada. Registros obtidos por fallback ficam sinalizados para revisão.
