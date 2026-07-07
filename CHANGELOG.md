# Changelog

## Versão atual


## v3

### Adicionado

- Nova opção **Gerar cadastro como** no dashboard.
- Permite gerar SQL marcando a pessoa como:
  - Cliente: `CLIENT_PESS = 'S'`;
  - Fornecedor: `FORN_PESS = 'S'`;
  - Cliente e Fornecedor: ambos os campos com `'S'`.
- Nome do arquivo SQL baixado passa a incluir o tipo selecionado.
- Script `CNPJ.py` também pergunta se deve gerar como Cliente, Fornecedor ou ambos.

### Segurança do cadastro

- O campo não selecionado não é forçado para `N`, evitando remover uma classificação existente em cadastros já gravados.


### Adicionado

- Dashboard HTML único para consulta de CNPJ.
- Entrada manual de CNPJs.
- Importação de arquivo `.txt` ou `.csv`.
- Remoção automática de duplicados.
- Filtro para aceitar somente CNPJ com 14 dígitos.
- Consulta na API pública `cnpj.ws`.
- Tabela de resultados com barra de rolagem.
- Geração de relatório CSV.
- Download do SQL gerado.
- Campo para informar `COD_PESS inicial`.
- Incremento automático do `COD_PESS`.
- Se `COD_PESS inicial` não for informado, começa em `1`.

### SQL

- Alterado de `UPDATE PESSOAS` para:

```sql
UPDATE OR INSERT INTO PESSOAS
...
MATCHING (CNPJCPF_PESS)
```

### Inscrição Estadual

- IE vazia passa a ser tratada como:

```sql
'ISENTO'
```

- IE existente recebe zeros à esquerda conforme UF.

### ICMS

- Adicionado tratamento do campo:

```sql
CONTBICMS_PESS
```

Regras:

```sql
'S' quando houver IE
'N' quando não houver IE
```

### Município

- `MUN_PESS` passa a ser preenchido via subselect pelo código IBGE.
- `COD_MUN` passa a ser preenchido via subselect pelo código IBGE.

### País

- Adicionado preenchimento fixo:

```sql
COD_PAIS = 1058
NOM_PAIS = 'BRASIL'
```

### Situação

- `SIT_PESS` passa a ser preenchido sempre como:

```sql
'ATIVO'
```

### Telefone

- DDD e telefone são concatenados.
