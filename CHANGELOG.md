## v4.4 - 14/10/2026

- A base da PESSOAS pode vir direto do Firebird, alem do CSV/TXT: modulo `firebird_db.py`, botao na interface grafica e ponte local `ponte.py` para o `index.html`.
- Campo *Biblioteca cliente*: o `fbclient.dll` e procurado sozinho nas pastas padrao e no PATH, com a arquitetura conferida antes de carregar e as versoes incompativeis descartadas.
- Os INSERTs dos novos cadastros passam a sair como `INSERT ... SELECT` independentes, um comando por cadastro, em vez de um `EXECUTE BLOCK`: o FireDAC abre o bloco como consulta e para em `-308. Cannot open / define command, which does not return result sets`. O `EXECUTE BLOCK` continua disponivel no seletor *INSERT dos novos* (`--formato-insert BLOCO`).
- Os INSERTs dos novos cadastros passam a sair **sem** `SET TERM`, que nao e SQL e fazia FireDAC, JDBC e o SQL Editor do IBExpert responderem `Token unknown - line 1, column 5 TERM`. A opcao *Incluir SET TERM nos INSERTs* (`--set-term`) liga de volta para o isql e o Script Executive.
- Nova bateria `tests/test_sql_firebird.py`, que executa o SQL gerado em um Firebird embarcado de verdade.

## v4.2.1 - 28/09/2026

- Corrige erro JavaScript `temValor is not defined` durante a leitura da resposta da API.
- Consulta em lote passa a trabalhar por CNPJ pendente e não repete CNPJs já concluídos com sucesso.
- Resultados são atualizados por CNPJ, evitando linhas duplicadas ao retomar ou reprocessar.
- Progresso de consultas é salvo no `localStorage` do navegador e restaurado ao reabrir a página.
- Botão **Consultar válidos** funciona também como retomada: processa apenas os CNPJs ainda sem sucesso.
- HTTP 429 mantém backoff/`Retry-After`, aumenta o intervalo adaptativo entre consultas e preserva o progresso mesmo quando o limite persiste.
- Pausar e reprocessar erros preserva todos os sucessos já obtidos.
- SQL gerado passa a identificar a versão v4.2.1.

# Changelog

## v4.1 - 2026-09-28

### Comparação Redsis × Receita
- Importação da base atual da tabela `PESSOAS` via CSV/TXT.
- Identificação automática de registros novos, existentes e sem base de comparação.
- Comparação campo a campo entre Redsis e dados oficiais.
- Indicadores de novos, existentes, sem alteração e registros divergentes.
- Normalização para evitar diferenças apenas por acento, caixa, espaços e zeros à esquerda na IE.

### Controle de atualização
- Seleção global dos campos que podem ser atualizados.
- Perfil seguro de campos.
- Seleção individual por CNPJ no modal de comparação.
- UPDATE passa a conter somente campos autorizados e realmente divergentes.
- `CLIENT_PESS` / `FORN_PESS` só são marcados quando necessário em cadastros existentes.

### COD_PESS e novos cadastros
- Novo modo inteligente: UPDATE existentes + INSERT novos.
- `COD_PESS` automático para novos com `MAX(COD_PESS)+1` dentro de `EXECUTE BLOCK`.
- Proteção `NOT EXISTS` antes do INSERT de cada CNPJ.
- Mantida estratégia manual como fallback.
- WHERE por CNPJ tolera dados formatados e não formatados.

### Arquivos auxiliares
- Adicionado `EXPORTAR_BASE_PESSOAS.sql`.
- Adicionado `MODELO_BASE_PESSOAS.csv`.
- README atualizado para o fluxo V4.1.

## v4.0 - 2026-09-28

- Validação completa de CNPJ.
- Preservação de campos existentes quando a API vier vazia.
- Situação cadastral e bloqueio de CNPJ não ativo por padrão.
- Saneamento de telefone, CEP e e-mail.
- Seleção de IE por situação e UF.
- Revisão por registro antes da geração do SQL.

## v4.2 - 28/09/2026
- Tratamento automático de HTTP 429 com retentativas e backoff progressivo.
- Respeita o cabeçalho Retry-After quando informado pela API.
- Intervalos de consulta ampliados para 5, 10, 15, 30 e 60 segundos; padrão 10 s.
- Reprocessamento em lote dos erros e retentativa individual por CNPJ.
- Motivo, número de tentativas e horário da última tentativa exibidos na revisão.
- Aviso explícito quando a base PESSOAS não foi carregada.
- Comparação desabilitada para registros sem base Redsis.
- Resumo operacional com prontos, revisão, erros, sem alteração e sem base.
- Filtros renomeados para Base e Status; botão Gerar SQL mostra a quantidade elegível.
- CNPJ não ativo inicia fora da geração quando a permissão correspondente está desligada.

## v4.3 - Fallback de APIs

- Mantida **CNPJ.ws** como fonte principal e padrão, preservando a consulta de Inscrição Estadual quando disponível.
- Adicionado modo **Automático** com fallback: CNPJ.ws → BrasilAPI → Minha Receita.
- Adicionadas opções para consultar exclusivamente CNPJ.ws, BrasilAPI ou Minha Receita.
- Fallback acionado quando a fonte anterior retorna erro HTTP, 429, 404/400, falha de rede ou resposta sem dados cadastrais suficientes.
- Resultado passa a registrar qual API respondeu e se houve fallback.
- Nova coluna **API** na revisão e campos `api` / `api_fallback` no CSV.
- Fontes alternativas sem IE não apagam IE já existente quando **Preservar dados existentes** estiver ativo.
- Alertas de revisão indicam quando a fonte alternativa não fornece Inscrição Estadual e quais fontes falharam antes do fallback.
- CLI Python atualizado com a mesma estratégia de fallback.
