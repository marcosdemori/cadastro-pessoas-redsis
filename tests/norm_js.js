const fs = require('fs'), vm = require('vm');
const sandbox = { console, CFG: {}, document: { querySelector: () => null, querySelectorAll: () => [], createElement: () => ({}) },
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} }, alert: () => {}, confirm: () => true,
  setTimeout, clearTimeout, URL: { createObjectURL: () => '', revokeObjectURL: () => {} }, Blob: function () {} };
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(`globalThis.$ = function(id){ return { get value(){return '';}, set value(v){}, get checked(){return false;}, set checked(v){},
  style:{}, textContent:'', innerHTML:'', addEventListener(){}, showModal(){}, close(){} }; };`, sandbox);
let js = fs.readFileSync(process.argv[2], 'utf8').replace(/const \$=id=>document\.getElementById\(id\);?/, '');
vm.runInContext(js, sandbox);
const payloads = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const out = {};
const mapa = { cnpjws: 'CNPJWS', brasilapi: 'BRASILAPI', minhareceita: 'MINHARECEITA' };
for (const [prov, lista] of Object.entries(payloads)) {
  out[prov] = lista.map((d, i) => {
    sandbox.__d = d;
    return vm.runInContext(`normalizarProvider('${mapa[prov]}', __d, '0000000000019${i}')`, sandbox);
  });
}
fs.writeFileSync(process.argv[4], JSON.stringify(out, null, 1));
console.log('norm js ok');
