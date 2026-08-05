const http = require('http');
const fs = require('fs');
const path = require('path');
const STATIC = path.join(__dirname, 'static');

http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) {
    const options = { hostname: '127.0.0.1', port: 5000, path: req.url, method: req.method, headers: req.headers };
    const proxy = http.request(options, pr => { res.writeHead(pr.statusCode, pr.headers); pr.pipe(res); });
    proxy.on('error', () => { res.writeHead(502); res.end('API unavailable'); });
    req.pipe(proxy);
    return;
  }
  let fp = path.join(STATIC, req.url === '/' ? 'index.html' : req.url.split('?')[0]);
  fs.readFile(fp, (err, data) => {
    if (err) { res.writeHead(404); res.end('Not Found'); return; }
    const types = {'.html':'text/html','.js':'application/javascript','.css':'text/css','.png':'image/png','.ico':'image/x-icon','.svg':'image/svg+xml'};
    res.writeHead(200, {'Content-Type': types[path.extname(fp)] || 'text/plain', 'Access-Control-Allow-Origin': '*'});
    res.end(data);
  });
}).listen(process.env.PORT || 8080, () => console.log('Server running'));
