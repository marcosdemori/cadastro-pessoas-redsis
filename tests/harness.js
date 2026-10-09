// Executa as funcoes de index.html fora do navegador, com stubs de DOM,
// e grava o SQL + relatorio para comparar com o Python.
const fs = require('fs');
const vm = require('vm');

const cenario = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const CFG = cenario.config;
const CAMPOS = new Set(cenario.campos);

const sandbox = {
  console,
  CFG,
  __base: cenario.base,
  __registros: cenario.registros,
  document: {
    querySelector(sel) {
      const m = /\[data-field="([^"]+)"\]/.exec(sel);
      if (m) return { checked: CAMPOS.has(m[1]), dataset: { field: m[1] } };
      return null;
    },
    querySelectorAll() { return []; },
    createElement() { return {}; },
  },
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  alert: () => {}, confirm: () => true,
  setTimeout, clearTimeout,
  URL: { createObjectURL: () => '', revokeObjectURL: () => {} },
  Blob: function () {},
};
sandbox.window = sandbox;
vm.createContext(sandbox);

// `$` do app resolve ids dos controles pelo objeto CFG
vm.runInContext(`
  globalThis.$ = function(id){ return {
    get value(){ return String(CFG[id] === undefined || CFG[id] === null ? '' : CFG[id]); },
    set value(v){ CFG[id] = v; },
    get checked(){ return !!CFG[id]; },
    set checked(v){ CFG[id] = v; },
    style: {}, textContent: '', innerHTML: '',
    addEventListener(){}, showModal(){}, close(){} }; };
`, sandbox);

let js = fs.readFileSync(process.argv[3], 'utf8');
// o app declara seu proprio `$`; removemos para usar o stub
js = js.replace(/const \$=id=>document\.getElementById\(id\);?/, '');
js = js.replace(/function \$\(id\)\{[^}]*\}/, '');
vm.runInContext(js, sandbox);

vm.runInContext(`
  baseMap = new Map();
  for (const [cnpj, linha] of Object.entries(__base)) baseMap.set(cnpj, linha);
  resultados = __registros.map(x => Object.assign({}, x));
  associarBase();
`, sandbox);

const sql = vm.runInContext('gerarSqlCompleto()', sandbox);
const linhas = vm.runInContext(`
  resultados.map(r => [r.origem, statusRegistro(r), r.cnpj,
    String(r.changed ? r.changed.length : 0),
    (r.changed||[]).map(x=>x.f.label).join(' | '),
    podeGerar(r) ? 'SIM' : 'NAO',
    (r.issues||[]).map(x=>x.texto).join(' | ')].join(';'))
`, sandbox);

fs.writeFileSync(process.argv[4], sql.split('\n').filter(l => !l.startsWith('-- Gerado em:')).join('\n'));
fs.writeFileSync(process.argv[5], linhas.join('\n'));
console.log('js ok');
