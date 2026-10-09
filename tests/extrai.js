const fs = require('fs');
const html = fs.readFileSync(process.argv[3], 'utf8');
const ini = html.indexOf('<script>') + '<script>'.length;
const fim = html.lastIndexOf('</script>');
let js = html.slice(ini, fim);
// corta a linha de bootstrap que precisa de DOM real
js = js.replace(/initFields\(\);renderImportSummary\(\);[^\n]*\n?/, '');
fs.writeFileSync(process.argv[2], js);
console.log('script extraido:', js.length, 'bytes');
