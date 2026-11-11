// Drive the actual ArkTS reader service against the live desktop reader HTTP API.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const vm = require('node:vm');

const [compilerPath, address, phase] = process.argv.slice(2);
if (!compilerPath || !address || !['parent-a', 'parent-b'].includes(phase)) {
  throw new Error('Usage: node test_parent_review_e2e.cjs <TypeScript package> <reader URL> <parent-a|parent-b>');
}
const ts = require(path.resolve(compilerPath));
const root = path.resolve(__dirname, '..');
const deviceId = 'reader-e2e';
const sessionId = 'session-e2e';
const eventId = '0123456789abcdef0123456789abcdef';

const httpKit = {
  RequestMethod: { GET: 'GET', POST: 'POST', PUT: 'PUT' },
  HttpDataType: { STRING: 'STRING' },
  createHttp() {
    return {
      request(url, options) {
        return new Promise((resolve, reject) => {
          const body = options.extraData;
          const headers = { ...options.header };
          if (body !== undefined) { headers['Content-Length'] = Buffer.byteLength(body); }
          const request = http.request(url, {
            method: options.method,
            headers,
            agent: false
          }, response => {
            const chunks = [];
            response.on('data', chunk => chunks.push(chunk));
            response.on('end', () => resolve({
              responseCode: response.statusCode,
              result: Buffer.concat(chunks).toString('utf8')
            }));
          });
          request.on('error', reject);
          if (body !== undefined) { request.write(body); }
          request.end();
        });
      },
      destroy() {}
    };
  }
};

const modules = {
  '@kit.NetworkKit': { http: httpKit },
  '@kit.CoreFileKit': { fileIo: {} },
  '@kit.AssetStoreKit': { asset: {} },
  '@kit.ArkTS': { util: {} }
};
const filename = path.join(root, 'entry/src/main/ets/services/RfidReader.ets');
const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  fileName: 'RfidReader.ts', reportDiagnostics: true,
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS }
});
const errors = compiled.diagnostics.filter(item => item.category === ts.DiagnosticCategory.Error);
assert.equal(errors.length, 0, errors.map(item => ts.flattenDiagnosticMessageText(item.messageText, '\n')).join('\n'));
const serviceModule = { exports: {} };
vm.runInNewContext(compiled.outputText, {
  module: serviceModule,
  exports: serviceModule.exports,
  require(id) {
    assert.ok(modules[id], 'Unexpected service dependency ' + id);
    return modules[id];
  },
  ArrayBuffer, Uint8Array, Map, Set, Date, Error
}, { filename });
const reader = serviceModule.exports;
const client = () => new reader.RfidReader(address, '', '');
const emptyHistory = () => ({ deviceId, cursor: '', sessions: [] });

async function main() {
  if (phase === 'parent-a') {
    const history = await client().sync(emptyHistory());
    assert.equal(history.sessions.length, 1);
    const cache = reader.updatePositionReview({ currentDevice: deviceId, histories: [history], positionReviews: [] },
      deviceId, sessionId, eventId, 'position_mismatch', '读到了下一页的内容', 1000);
    const reviews = await client().syncPositionReviews(history, cache.positionReviews);
    assert.equal(reviews.length, 1);
    assert.equal(reviews[0].status, 'position_mismatch');
    console.log(JSON.stringify({ reviews }));
    return;
  }

  const history = await client().sync(emptyHistory());
  const parentBRead = await client().syncPositionReviews(history, []);
  assert.equal(parentBRead.length, 1);
  assert.equal(parentBRead[0].note, '读到了下一页的内容');

  const updated = reader.updatePositionReview({ currentDevice: deviceId, histories: [history],
    positionReviews: parentBRead }, deviceId, sessionId, eventId,
  'confirmed', '已与孩子核对，读者位置有误', 2000);
  const parentBPublished = await client().syncPositionReviews(history, updated.positionReviews);
  const parentARefresh = await client().syncPositionReviews(history, parentBPublished);
  assert.equal(parentARefresh[0].status, 'confirmed');
  assert.equal(parentARefresh[0].note, '已与孩子核对，读者位置有误');
  assert.equal(history.sessions[0].trajectory[0].status, 'matched');
  console.log(JSON.stringify({ parentBRead, parentARefresh,
    originalTraceStatus: history.sessions[0].trajectory[0].status }));
}

main().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
