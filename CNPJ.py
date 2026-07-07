import requests
import time
import re

def somente_numero(valor):
    return re.sub(r'\D', '', str(valor))

def limpar_cep(cep):
    return re.sub(r'\D', '', str(cep)) if cep else None

def limpar_numero(texto):
    if not texto:
        return None
    nums = re.findall(r'\d+', str(texto))
    return nums[0] if nums else None

def limpar_texto(texto):
    if not texto:
        return None
    return str(texto).replace("'", "").strip()

def sql_texto(valor):
    valor = limpar_texto(valor)
    return f"'{valor}'" if valor else "NULL"

def sql_numero(valor):
    valor = somente_numero(valor) if valor else None
    return valor if valor else "NULL"

def normalizar_tipo_cadastro(valor):
    valor = (valor or 'cliente').strip().lower()
    aliases = {
        '1': 'cliente',
        'c': 'cliente',
        'cliente': 'cliente',
        '2': 'fornecedor',
        'f': 'fornecedor',
        'fornecedor': 'fornecedor',
        '3': 'ambos',
        'a': 'ambos',
        'ambos': 'ambos',
        'cliente_fornecedor': 'ambos',
        'cliente e fornecedor': 'ambos',
    }
    return aliases.get(valor, 'cliente')

def campos_tipo_cadastro(tipo_cadastro):
    tipo_cadastro = normalizar_tipo_cadastro(tipo_cadastro)
    if tipo_cadastro == 'cliente':
        return ['CLIENT_PESS']
    if tipo_cadastro == 'fornecedor':
        return ['FORN_PESS']
    return ['CLIENT_PESS', 'FORN_PESS']

def set_tipo_cadastro_sql(tipo_cadastro, indent='  '):
    return ',\n'.join(f"{indent}{campo} = 'S'" for campo in campos_tipo_cadastro(tipo_cadastro))

# Tamanho total dos dígitos da IE (somente números) por UF
IE_TAMANHO = {
    'AC': 13, 'AL':  9, 'AM':  9, 'AP':  9, 'BA':  8,
    'CE':  9, 'DF': 13, 'ES':  9, 'GO':  9, 'MA':  9,
    'MG': 13, 'MS':  9, 'MT': 11, 'PA':  9, 'PB':  9,
    'PE':  9, 'PI':  9, 'PR': 10, 'RJ':  8, 'RN': 10,
    'RO': 13, 'RR':  9, 'RS': 10, 'SC':  9, 'SE':  9,
    'SP': 12, 'TO':  9,
}

def normalizar_situacao():
    return "'ATIVO'"

def tem_ie(ie):
    return bool(somente_numero(ie or ''))

def contbicms(ie):
    return "'S'" if tem_ie(ie) else "'N'"

def tratar_ie(ie, uf=None):
    ie = limpar_texto(ie)
    if not ie:
        return "'ISENTO'"
    ie_num = somente_numero(ie)
    if not ie_num:
        return "'ISENTO'"
    if uf:
        uf = str(uf).strip().upper()
        tamanho = IE_TAMANHO.get(uf)
        if tamanho and len(ie_num) < tamanho:
            ie_num = ie_num.zfill(tamanho)
    return f"'{ie_num}'"

def sql_mun_nome(ibge_id):
    if not ibge_id:
        return 'NULL'
    return f"(SELECT UPPER(NOM_MUN) FROM MUN WHERE CODIBGE_MUN = '{ibge_id}')"

def sql_mun_cod(ibge_id):
    if not ibge_id:
        return 'NULL'
    return f"(SELECT COD_MUN FROM MUN WHERE CODIBGE_MUN = '{ibge_id}')"

def montar_fone(ddd, fone):
    d = somente_numero(ddd or '')
    f = somente_numero(fone or '')
    resultado = d + f
    return resultado if resultado else None

def consulta_com_retry(cnpj, tipo_cadastro):
    url = f'https://publica.cnpj.ws/cnpj/{cnpj}'

    for tentativa in range(5):
        try:
            r = requests.get(url, timeout=15)

            if r.status_code == 200:
                data = r.json()
                est = data.get('estabelecimento', {})

                razao_social  = data.get('razao_social')
                nome_fantasia = est.get('nome_fantasia')

                ies = est.get('inscricoes_estaduais', [])
                ie  = ies[0].get('inscricao_estadual') if ies else None

                logradouro  = est.get('logradouro')
                numero      = est.get('numero')
                complemento = est.get('complemento')
                bairro      = est.get('bairro')
                cep         = limpar_cep(est.get('cep'))

                cidade  = None
                estado  = None
                ibge_id = None

                cidade_obj = est.get('cidade')
                estado_obj = est.get('estado')

                if isinstance(cidade_obj, dict):
                    cidade  = cidade_obj.get('nome')
                    ibge_id = str(cidade_obj.get('ibge_id')) if cidade_obj.get('ibge_id') else None

                if isinstance(estado_obj, dict):
                    estado = estado_obj.get('sigla')

                telefone1 = montar_fone(est.get('ddd1'), est.get('telefone1'))
                telefone2 = montar_fone(est.get('ddd2'), est.get('telefone2'))

                email    = est.get('email')
                situacao = est.get('situacao_cadastral')
                tipo     = est.get('tipo')

                cnae     = None
                cnae_obj = est.get('atividade_principal')
                if isinstance(cnae_obj, dict):
                    cnae = cnae_obj.get('id') or cnae_obj.get('descricao')

                print(f'OK {cnpj} | {razao_social} | {cidade}/{estado} | IBGE: {ibge_id}')

                return f"""UPDATE PESSOAS
SET
  NOM_PESS     = {sql_texto(razao_social)},
  FANT_PESS    = {sql_texto(nome_fantasia)},
  IE_PESS      = {tratar_ie(ie, estado)},
  CONTBICMS_PESS = {contbicms(ie)},
  END_PESS     = {sql_texto(logradouro)},
  NUM_PESS     = {sql_numero(numero)},
  COMPL_PESS   = {sql_texto(complemento)},
  BAIRRO_PESS  = {sql_texto(bairro)},
  MUN_PESS     = {sql_mun_nome(ibge_id)},
  COD_MUN      = {sql_mun_cod(ibge_id)},
  UF_PESS      = {sql_texto(estado)},
  CEP_PESS     = {sql_texto(cep)},
  FONE_PESS    = {sql_texto(telefone1)},
  FONE2_PESS   = {sql_texto(telefone2)},
  EMAIL_PESS   = {sql_texto(email)},
  SIT_PESS     = {normalizar_situacao()},
  TIPO_PESS    = {sql_texto(tipo)},
  CNAEP_PESS   = {sql_texto(cnae)},
  COD_PAIS     = 1058,
  NOM_PAIS     = 'BRASIL',
{set_tipo_cadastro_sql(tipo_cadastro, '  ')}
WHERE CNPJCPF_PESS = '{cnpj}';"""

            elif r.status_code == 429:
                print(f'AGUARDANDO (429) {cnpj}...')
                time.sleep(20)

            else:
                print(f'ERRO {cnpj} -> {r.status_code}')
                return None

        except Exception as e:
            print(f'EXCEPTION {cnpj} -> {e}')
            time.sleep(20)

    print(f'FALHOU {cnpj}')
    return None


# ── Main ────────────────────────────────────────────────────────────────────
print('Gerar cadastro como:')
print('1 - Cliente')
print('2 - Fornecedor')
print('3 - Cliente e Fornecedor')
tipo_cadastro = normalizar_tipo_cadastro(input('Opção [1]: ') or '1')
print(f'Tipo selecionado: {tipo_cadastro}')

with open('CNPJ.txt', encoding='utf-8') as f:
    lista = [somente_numero(l.strip()) for l in f if l.strip()]

f_out = open('updates.sql', 'a', encoding='utf-8')

total    = len(lista)
contador = 0

for cnpj in lista:
    if len(cnpj) != 14:
        continue

    contador += 1
    print(f'[{contador}/{total}] Consultando {cnpj}...')

    sql = consulta_com_retry(cnpj, tipo_cadastro)

    if sql:
        f_out.write(sql + "\n\n")
        f_out.flush()
        print(f'GRAVADO {cnpj}')

    time.sleep(20)

f_out.close()
print("FINALIZADO 100% ✅")
