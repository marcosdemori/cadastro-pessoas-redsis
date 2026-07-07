# Dashboard CNPJ - Redsis

Dashboard HTML para consultar CNPJs na API pública `cnpj.ws`, gerar SQL para cadastro/atualização na tabela `PESSOAS` e acompanhar o progresso das consultas.

## Objetivo

Este projeto foi criado para apoiar a migração e atualização de cadastros entre bases Edari/Redsis, permitindo:

- consultar dados cadastrais por CNPJ;
- gerar SQL no padrão do banco;
- tratar Inscrição Estadual;
- marcar contribuinte de ICMS;
- gerar `UPDATE OR INSERT`;
- escolher se o cadastro será gerado como cliente, fornecedor ou ambos;
- controlar o `COD_PESS` inicial;
- baixar SQL e relatório CSV.

## Arquivos

```text
index.html
README.md
CHANGELOG.md
.gitignore
```

## Como usar

1. Abra o arquivo `index.html` no navegador.
2. Cole uma lista de CNPJs ou importe um arquivo `.txt`/`.csv`.
3. Escolha o **Tipo de SQL**: `UPDATE` ou `INSERT OR UPDATE`.
4. Escolha em **Gerar cadastro como**: `Cliente`, `Fornecedor` ou `Cliente e Fornecedor`.
5. Se escolher `INSERT OR UPDATE`, informe o `COD_PESS inicial`.
6. Clique em **Carregar lista**.
7. Clique em **Iniciar consultas**.
8. Ao final, baixe:
   - arquivo `.sql` com o nome conforme o tipo escolhido;
   - `relatorio_cnpj.csv`

## SQL gerado

O dashboard gera SQL no formato:

```sql
UPDATE OR INSERT INTO PESSOAS (...)
VALUES (...)
MATCHING (CNPJCPF_PESS);
```

A chave usada para atualização/inserção é:

```sql
MATCHING (CNPJCPF_PESS)
```

## Campos tratados no SQL

O SQL gerado contempla:

- `COD_PESS`
- `CNPJCPF_PESS`
- `NOM_PESS`
- `FANT_PESS`
- `IE_PESS`
- `CONTBICMS_PESS`
- `END_PESS`
- `NUM_PESS`
- `COMPL_PESS`
- `BAIRRO_PESS`
- `MUN_PESS`
- `COD_MUN`
- `UF_PESS`
- `CEP_PESS`
- `FONE_PESS`
- `FONE2_PESS`
- `EMAIL_PESS`
- `SIT_PESS`
- `TIPO_PESS`
- `CNAEP_PESS`
- `COD_PAIS`
- `NOM_PAIS`
- `CLIENT_PESS`, quando escolhido Cliente ou Cliente e Fornecedor
- `FORN_PESS`, quando escolhido Fornecedor ou Cliente e Fornecedor

## Regras aplicadas

### CNPJ

- Aceita somente documentos com 14 dígitos.
- Remove caracteres não numéricos.
- Ignora CPF automaticamente.

### Inscrição Estadual

- Se existir IE, aplica zeros à esquerda conforme UF.
- Se não existir IE, grava:

```sql
IE_PESS = 'ISENTO'
```

### Contribuinte de ICMS

- Se tiver IE:

```sql
CONTBICMS_PESS = 'S'
```

- Se não tiver IE:

```sql
CONTBICMS_PESS = 'N'
```

### Situação cadastral

Sempre grava:

```sql
SIT_PESS = 'ATIVO'
```

### País

Sempre grava:

```sql
COD_PAIS = 1058,
NOM_PAIS = 'BRASIL'
```

### Município

O município é buscado pelo código IBGE:

```sql
MUN_PESS = (SELECT UPPER(NOM_MUN) FROM MUN WHERE CODIBGE_MUN = '...')
COD_MUN  = (SELECT COD_MUN FROM MUN WHERE CODIBGE_MUN = '...')
```

## API utilizada

Endpoint público:

```text
https://publica.cnpj.ws/cnpj/{CNPJ}
```

## Atenção sobre limite da API

A API pública pode retornar erro `429` quando muitas consultas são feitas em sequência.

Recomendação:

- usar delay de 20 segundos ou mais;
- se houver erro 429, rodar novamente depois;
- baixar o relatório CSV para revisar falhas.

## Como subir para o GitHub

### 1. Criar repositório privado

No GitHub:

1. Clique em **New repository**
2. Nome sugerido:

```text
dashboard-cnpj-redsis
```

3. Marque como **Private**
4. Crie o repositório

### 2. Subir os arquivos

No terminal, dentro da pasta do projeto:

```bash
git init
git add .
git commit -m "feat: adiciona dashboard CNPJ Redsis"
git branch -M main
git remote add origin https://github.com/marcosdemori/cadastro-pessoas-redsis.git
git push -u origin main
```

## Segurança

Este projeto roda localmente no navegador. Ele não contém credenciais, senha ou conexão direta com banco de dados.

Mesmo assim, recomenda-se manter o repositório privado por conter regras internas de cadastro e SQL da empresa.


### Cliente / Fornecedor

A opção **Gerar cadastro como** define quais campos serão incluídos no SQL:

```sql
-- Cliente
CLIENT_PESS = 'S'

-- Fornecedor
FORN_PESS = 'S'

-- Cliente e Fornecedor
CLIENT_PESS = 'S'
FORN_PESS = 'S'
```

O sistema não força o campo não selecionado para `N`, evitando remover uma marcação existente quando o CNPJ já estiver cadastrado.
