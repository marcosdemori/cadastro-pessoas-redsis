const fs = require('fs'), vm = require('vm');
const casos = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const CFG = { baseInput: '' };
const sandbox = { console, CFG, document: { querySelector: () => null, querySelectorAll: () => [], createElement: () => ({}) },
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  alert: (m) => { sandbox.__alert = m; }, confirm: () => true,
  setTimeout, clearTimeout, URL: { createObjectURL: () => '', revokeObjectURL: () => {} }, Blob: function () {} };
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(`globalThis.$ = function(id){ return {
  get value(){ return String(CFG[id] === undefined ? '' : CFG[id]); }, set value(v){ CFG[id] = v; },
  get checked(){ return !!CFG[id]; }, set checked(v){ CFG[id] = v; },
  style:{}, textContent:'', innerHTML:'', addEventListener(){}, showModal(){}, close(){} }; };`, sandbox);
let js = fs.readFileSync(process.argv[2], 'utf8').replace(/const \$=id=>document\.getElementById\(id\);?/, '');
vm.runInContext(js, sandbox);

const saida = casos.map(texto => {
  CFG.baseInput = texto;
  sandbox.__alert = null;
  vm.runInContext('baseMap = new Map(); carregarBase();', sandbox);
  const erro = sandbox.__alert;
  const mapa = vm.runInContext('Object.fromEntries(baseMap)', sandbox);
  return { erro, mapa };
});
fs.writeFileSync(process.argv[4], JSON.stringify(saida, null, 1));
console.log('base js ok');
