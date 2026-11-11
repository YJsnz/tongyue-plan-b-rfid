// Exercise the actual ArkTS service code with desktop file, network and asset adapters.
// Pass the TypeScript package bundled with DevEco Studio; no npm installation is needed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');

const compilerPath = process.argv[2] || process.env.TONGYUE_TYPESCRIPT;
if (!compilerPath) {
  throw new Error('Usage: node tools/test_parent_services.cjs <DevEco TypeScript package path>');
}
const ts = require(path.resolve(compilerPath));
const root = path.resolve(__dirname, '..');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'tongyue-parent-test-'));
const network = { replies: [], calls: [], destroyed: 0 };
const assets = new Map();
const faults = { writeLimit: Infinity, readLimit: Infinity, sync: false, asset: null };
const http = {
  RequestMethod: { GET: 'GET', POST: 'POST', PUT: 'PUT' }, HttpDataType: { STRING: 'STRING' },
  createHttp() {
    return {
      async request(url, options) {
        network.calls.push({ url, options });
        if (!network.replies.length) { throw new Error('Unexpected HTTP request'); }
        const next = network.replies.shift();
        if (next instanceof Error) { throw next; }
        return next;
      },
      destroy() { network.destroyed++; }
    };
  }
};
const bytes = value => typeof value === 'string' ? Buffer.from(value, 'utf8') : Buffer.from(value);
const fileIo = {
  OpenMode: { READ_ONLY: 0, WRITE_ONLY: 1, CREATE: 2, TRUNC: 4 },
  accessSync: fs.existsSync, readTextSync: name => fs.readFileSync(name, 'utf8'),
  openSync: (name, mode) => ({ fd: fs.openSync(name, mode ? 'w' : 'r') }),
  closeSync: file => fs.closeSync(file.fd),
  writeSync(fd, data) {
    const value = bytes(data);
    return fs.writeSync(fd, value, 0, Math.min(value.length, faults.writeLimit));
  },
  fsyncSync(fd) { if (faults.sync) { throw new Error('Injected fsync failure'); } fs.fsyncSync(fd); },
  renameSync: fs.renameSync,
  async open(name) { return { fd: fs.openSync(name, 'r') }; },
  async stat(fd) { return fs.fstatSync(fd); },
  async read(fd, buffer) {
    return fs.readSync(fd, new Uint8Array(buffer), 0, Math.min(buffer.byteLength, faults.readLimit));
  },
  async close(file) { fs.closeSync(file.fd); }
};
const asset = {
  Tag: { ALIAS: 'alias', SECRET: 'secret', RETURN_TYPE: 'return', CONFLICT_RESOLUTION: 'conflict' },
  ReturnType: { ALL: 'all' }, ConflictResolution: { OVERWRITE: 'overwrite' },
  async query(query) {
    if (faults.asset) { throw faults.asset; }
    const key = Buffer.from(query.get('alias')).toString();
    if (!assets.has(key)) { throw { code: 24000002 }; }
    return [new Map([['secret', assets.get(key)]])];
  },
  async add(item) {
    if (faults.asset) { throw faults.asset; }
    assets.set(Buffer.from(item.get('alias')).toString(), item.get('secret'));
  }
};
const util = {
  TextEncoder: class { encodeInto(value) { return new TextEncoder().encode(value); } },
  TextDecoder: { create: () => ({ decodeToString: value => new TextDecoder().decode(value) }) }
};
const modules = {
  '@kit.NetworkKit': { http }, '@kit.CoreFileKit': { fileIo },
  '@kit.AssetStoreKit': { asset }, '@kit.ArkTS': { util }
};
function loadService(name) {
  const filename = path.join(root, 'entry/src/main/ets/services', name + '.ets');
  const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    fileName: name + '.ts', reportDiagnostics: true,
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS }
  });
  assert.equal(compiled.diagnostics.filter(item => item.category === ts.DiagnosticCategory.Error).length, 0);
  const module = { exports: {} };
  vm.runInNewContext(compiled.outputText, {
    module, exports: module.exports, require(id) {
      assert.ok(modules[id], 'Unexpected service dependency ' + id);
      return modules[id];
    }, ArrayBuffer, Uint8Array, Map, Set, Date, Error
  }, { filename });
  return module.exports;
}
const reader = loadService('RfidReader');
modules['./RfidReader'] = reader;
const stats = loadService('ReadingStats');
const tests = [];
const test = (name, run) => tests.push({ name, run });
const json = value => JSON.parse(JSON.stringify(value));
const reply = (value, responseCode = 200) => ({ responseCode, result: JSON.stringify(value) });
const client = () => new reader.RfidReader('https://127.0.0.1:8765', 'test-token', 'test-ca');
const session = (id, extra = {}) => ({ sessionId: id, bookId: 'book-1', packVersion: 'v1', endedAtMs: null,
  durationMs: 1000, lastSegmentId: 's1', positionSource: 'manual', ...extra });
const history = () => ({ deviceId: 'reader-1', cursor: '', sessions: [] });
const trace = (eventId = '0123456789abcdef0123456789abcdef', overrides = {}) => ({
  eventId, offsetMs: 500, segmentId: 's1', source: 'ctc', status: 'matched',
  segmentNumber: 1, segmentText: '小熊借伞。', wordIndex: 0, textStart: 0, textEnd: 2, text: '小熊', ...overrides
});

test('reader address preserves HTTPS and restricts HTTP to local debugging', () => {
  assert.equal(reader.readerAddress(' https://192.168.1.10:8765/ '), 'https://192.168.1.10:8765');
  assert.equal(reader.readerAddress('http://127.0.0.1:8765'), 'http://127.0.0.1:8765');
  for (const address of ['http://192.168.1.10', 'https://user:pass@reader', 'https://reader/path', 'reader:8765']) {
    assert.throws(() => reader.readerAddress(address));
  }
});

test('device request supplies pairing settings and closes the HTTP client', async () => {
  network.replies.push(reply({ deviceId: 'reader-1', firmwareVersion: 'test', protocolVersion: 1 }));
  assert.equal((await client().device()).deviceId, 'reader-1');
  const request = network.calls[0];
  assert.equal(request.options.header.Authorization, 'Bearer test-token');
  assert.equal(request.options.caData, 'test-ca');
  assert.equal(request.options.maxRedirects, 0);
  assert.equal(request.options.usingProxy, false);
});

test('pairing failure becomes a user-readable error', async () => {
  network.replies.push(reply({ code: 'PAIRING_REQUIRED' }, 401));
  await assert.rejects(client().device(), /配对密钥/);
});

test('unsupported device protocol and mismatched binding are rejected', async () => {
  network.replies.push(reply({ deviceId: 'reader-1', protocolVersion: 9 }));
  await assert.rejects(client().device(), /不受支持/);
  network.replies.push(reply({ tagUid: 'AABBCCDD', bookId: 'book-1' }));
  await assert.rejects(client().bind('0004A12B', 'scan-1', 'book-1'), /绑定结果不一致/);
});

test('binary StoryPack upload uses the file body and a longer timeout', async () => {
  const archive = new Uint8Array([31, 139, 8]).buffer;
  network.replies.push(reply({ bookId: 'book-1', title: '原创绘本', version: 'v1', alreadyInstalled: false }));
  assert.equal((await client().importStoryPack(archive)).bookId, 'book-1');
  assert.equal(network.calls[0].options.extraData, archive);
  assert.equal(network.calls[0].options.header['Content-Type'], 'application/gzip');
  assert.equal(network.calls[0].options.readTimeout, 90000);
});

test('paged sync deduplicates sessions and preserves an existing sync date', async () => {
  const old = { deviceId: 'reader-1', cursor: 's0', sessions: [session('s0', { syncedAtMs: 10 })] };
  network.replies.push(reply({ items: [session('s0'), session('s1')], nextCursor: 's1', hasMore: true }),
    reply({ items: [session('s2')], nextCursor: 's2', hasMore: false }));
  const result = await client().sync(old);
  assert.equal(result.cursor, 's2');
  assert.deepEqual(json(result.sessions.map(item => item.sessionId)), ['s0', 's1', 's2']);
  assert.equal(result.sessions[0].syncedAtMs, 10);
  assert.ok(network.calls[1].url.endsWith('after=s1&limit=8'));
  assert.equal(old.cursor, 's0');
  assert.equal(old.sessions.length, 1);
});

test('later-page network failure leaves the original history unchanged', async () => {
  const old = { deviceId: 'reader-1', cursor: 's0', sessions: [session('s0')] };
  const before = JSON.stringify(old);
  network.replies.push(reply({ items: [session('s1')], nextCursor: 's1', hasMore: true }), new Error('offline'));
  await assert.rejects(client().sync(old), /offline/);
  assert.equal(JSON.stringify(old), before);
});

test('repeated sync cursor fails instead of looping', async () => {
  network.replies.push(reply({ items: [session('s1')], nextCursor: '', hasMore: true }));
  await assert.rejects(client().sync(history()), /重复的同步位置/);
});

test('answers are preserved and invalid evidence cannot enter history', async () => {
  const answer = { segmentId: 's1', question: '为什么？', answer: '原文答案', evidence: '原文', source: 'voice', offsetMs: 500 };
  network.replies.push(reply({ items: [session('s1', { answers: [answer] })], nextCursor: 's1' }));
  const result = await client().sync(history());
  assert.deepEqual(json(result.sessions[0].answers), [answer]);
  network.replies.push(reply({ items: [session('s2', { answers: [{ ...answer, offsetMs: 2000 }] })], nextCursor: 's2' }));
  await assert.rejects(client().sync(history()), /无效问答记录/);
});

test('valid word trajectory is retained and a repeated sync does not lose it', async () => {
  const sessionWithTrace = session('trace-1', { trajectory: [trace()], trajectoryDropped: 0,
    traceStats: { repeated: 0, relocated: 0, uncertain: 0 }, lastWordIndex: 0, positionUncertain: false });
  network.replies.push(reply({ items: [sessionWithTrace], nextCursor: 'trace-1', hasMore: false }));
  const first = await client().sync(history());
  assert.deepEqual(json(first.sessions[0].trajectory), [trace()]);
  assert.equal(first.sessions[0].lastWordIndex, 0);
  assert.equal(first.sessions[0].positionUncertain, false);
  network.replies.push(reply({ items: [], nextCursor: 'trace-1', hasMore: false }));
  const second = await client().sync(first);
  assert.deepEqual(json(second.sessions[0].trajectory), [trace()]);
});

test('bad CTC words, unknown statuses, inconsistent statistics and oversized traces are rejected', async () => {
  const base = { trajectory: [trace()], trajectoryDropped: 0, traceStats: { repeated: 0, relocated: 0, uncertain: 0 } };
  const malformed = [
    { ...trace(), eventId: 'bad' }, { ...trace(), status: 'other' },
    { ...trace(), offsetMs: 1001 }, { ...trace(), textEnd: 0 },
    { ...trace(), source: 'mfcc' }, { ...trace(), segmentText: 'x'.repeat(129) }
  ];
  for (const bad of malformed) {
    network.replies.push(reply({ items: [session('bad-trace', { ...base, trajectory: [bad] })],
      nextCursor: 'bad-trace', hasMore: false }));
    await assert.rejects(client().sync(history()));
  }
  network.replies.push(reply({ items: [session('bad-count', { ...base, traceStats: { repeated: 0, relocated: 0, uncertain: 0 },
    trajectory: [trace('11111111111111111111111111111111', { status: 'repeated' })] })],
    nextCursor: 'bad-count', hasMore: false }));
  await assert.rejects(client().sync(history()));
  network.replies.push(reply({ items: [session('too-many', { ...base, trajectory: Array(129).fill(trace()).map((item, i) =>
    trace(i.toString(16).padStart(32, '0'))) })], nextCursor: 'too-many', hasMore: false }));
  await assert.rejects(client().sync(history()));
});

test('parent position reviews are device-scoped, editable and stay separate from original readings', () => {
  const recorded = session('trace-review', { trajectory: [trace()] });
  const before = { ...history(), currentDevice: 'reader-1', positionReviews: [],
    histories: [{ ...history(), sessions: [recorded] },
      { deviceId: 'reader-2', cursor: '', sessions: [session('trace-review')] }] };
  const first = reader.updatePositionReview(before, 'reader-1', 'trace-review', trace().eventId,
    'needs_review', '再和孩子核对', 1000);
  assert.equal(before.positionReviews.length, 0);
  assert.deepEqual(json(first.positionReviews[0]), { deviceId: 'reader-1', sessionId: 'trace-review',
    eventId: trace().eventId, status: 'needs_review', note: '再和孩子核对', updatedAtMs: 1000 });
  const edited = reader.updatePositionReview(first, 'reader-1', 'trace-review', trace().eventId,
    'position_mismatch', '', 2000);
  assert.equal(edited.positionReviews.length, 1);
  assert.equal(edited.positionReviews[0].status, 'position_mismatch');
  assert.equal(edited.histories[0].sessions[0].trajectory[0].status, 'matched');
  assert.equal(edited.positionReviews[0].deviceId, 'reader-1');
  assert.throws(() => reader.updatePositionReview(edited, 'reader-2', 'trace-review', trace().eventId,
    'confirmed', '', 3000), /无法保存/);
  assert.throws(() => reader.updatePositionReview(edited, 'reader-1', 'trace-review', 'not-a-trace',
    'confirmed', '', 3000), /无法保存/);
  assert.throws(() => reader.updatePositionReview(edited, 'reader-1', 'trace-review', trace().eventId,
    'diagnosed', '', 3000), /无法保存/);
  assert.throws(() => reader.updatePositionReview(edited, 'reader-1', 'trace-review', trace().eventId,
    'confirmed', '注'.repeat(257), 3000), /无法保存/);
});

test('position review sync uploads local edits and imports the device canonical list', async () => {
  const recorded = session('review-sync', { trajectory: [trace()], trajectoryDropped: 0,
    traceStats: { repeated: 0, relocated: 0, uncertain: 0 } });
  const local = { deviceId: 'reader-1', sessionId: 'review-sync', eventId: trace().eventId,
    status: 'position_mismatch', note: '再和孩子核对', updatedAtMs: 2000 };
  const anotherReader = { ...local, deviceId: 'reader-2', sessionId: 'other-session' };
  network.replies.push(reply({ items: [] }), reply(local), reply({ items: [local] }));
  const result = await client().syncPositionReviews({ ...history(), sessions: [recorded] }, [local, anotherReader]);
  assert.deepEqual(json(result), [anotherReader, local]);
  assert.deepEqual(network.calls.map(item => [item.options.method, new URL(item.url).pathname]), [
    ['GET', '/v1/position-reviews'],
    ['PUT', '/v1/position-reviews/review-sync/' + trace().eventId],
    ['GET', '/v1/position-reviews']
  ]);
  assert.deepEqual(JSON.parse(network.calls[1].options.extraData), {
    status: local.status, note: local.note, updatedAtMs: local.updatedAtMs
  });
});

test('position review sync keeps a newer device version and rejects unknown trace references', async () => {
  const recorded = session('review-sync', { trajectory: [trace()], trajectoryDropped: 0,
    traceStats: { repeated: 0, relocated: 0, uncertain: 0 } });
  const local = { deviceId: 'reader-1', sessionId: 'review-sync', eventId: trace().eventId,
    status: 'needs_review', note: '手机的旧状态', updatedAtMs: 1000 };
  const remote = { ...local, status: 'confirmed', note: '另一台家长设备已确认', updatedAtMs: 2000 };
  network.replies.push(reply({ items: [remote] }), reply({ items: [remote] }));
  const result = await client().syncPositionReviews({ ...history(), sessions: [recorded] }, [local]);
  assert.deepEqual(json(result), [remote]);
  assert.deepEqual(network.calls.map(item => item.options.method), ['GET', 'GET']);

  network.replies.push(reply({ items: [{ ...remote, sessionId: 'missing-session' }] }));
  await assert.rejects(client().syncPositionReviews({ ...history(), sessions: [recorded] }, []), /无效的位置复核/);
  assert.equal(network.replies.length, 0);
});

test('position review labels survive cache save and restart', () => {
  const filename = path.join(temporary, 'position-reviews.json');
  const previous = { ...reader.emptyReaderCache(), currentDevice: 'reader-1', histories: [history()] };
  const entry = { eventId: '0123456789abcdef0123456789abcdef', offsetMs: 10, segmentId: 's1',
    source: 'ctc', status: 'matched', wordIndex: 0, textStart: 0, textEnd: 2, text: '小熊' };
  previous.histories[0].sessions.push(session('trace-2', { trajectory: [entry] }));
  const updated = reader.updatePositionReview(previous, 'reader-1', 'trace-2', entry.eventId,
    'confirmed', '', 500);
  reader.saveReaderCache(filename, updated);
  const restored = reader.readReaderCache(filename);
  assert.deepEqual(json(restored.positionReviews), json(updated.positionReviews));
  assert.deepEqual(json(restored.histories[0].sessions[0].trajectory), [entry]);
});

test('selected archive is read completely through partial reads', async () => {
  const filename = path.join(temporary, 'selected.tar.gz');
  const data = Buffer.from('中文测试包'.repeat(1000));
  fs.writeFileSync(filename, data);
  faults.readLimit = 73;
  assert.deepEqual(Buffer.from(await reader.readStoryPack(filename)), data);
});

test('an oversized selected archive is rejected before upload', async () => {
  const filename = path.join(temporary, 'large.tar.gz');
  const fd = fs.openSync(filename, 'w');
  fs.ftruncateSync(fd, 24 * 1024 * 1024 + 1);
  fs.closeSync(fd);
  await assert.rejects(reader.readStoryPack(filename), /24 MiB/);
  assert.equal(network.calls.length, 0);
});

test('parent cache survives restart with non-ASCII data', () => {
  const filename = path.join(temporary, 'cache.json');
  const cache = { address: 'https://reader', currentDevice: 'reader-1', histories: [],
    books: [{ bookId: 'book-1', title: '小熊借伞 🐻' }] };
  reader.saveReaderCache(filename, cache);
  assert.deepEqual(json(reader.readReaderCache(filename)), cache);
});

test('partial cache writes still produce a complete readable history', () => {
  const filename = path.join(temporary, 'partial-cache.json');
  const cache = { address: 'https://reader', currentDevice: 'reader-1', histories: [], books: [{ title: '小熊 🐻' }] };
  faults.writeLimit = 7;
  reader.saveReaderCache(filename, cache);
  assert.deepEqual(json(reader.readReaderCache(filename)), cache);
});

test('zero-byte cache write cannot replace previously saved history', () => {
  const filename = path.join(temporary, 'zero-cache.json');
  fs.writeFileSync(filename, '{"previous":true}');
  faults.writeLimit = 0;
  assert.throws(() => reader.saveReaderCache(filename, reader.emptyReaderCache()));
  assert.equal(fs.readFileSync(filename, 'utf8'), '{"previous":true}');
});

test('fsync failure retains the previously published cache', () => {
  const filename = path.join(temporary, 'fsync-cache.json');
  fs.writeFileSync(filename, '{"previous":true}');
  faults.sync = true;
  assert.throws(() => reader.saveReaderCache(filename, reader.emptyReaderCache()), /fsync/);
  assert.equal(fs.readFileSync(filename, 'utf8'), '{"previous":true}');
});

test('pairing tokens are isolated by device and missing-token lookup is recoverable', async () => {
  assert.equal(await reader.readReaderToken('new-reader'), '');
  await reader.saveReaderToken('reader-1', 'token-一');
  await reader.saveReaderToken('reader-2', 'token-二');
  assert.equal(await reader.readReaderToken('reader-1'), 'token-一');
  assert.equal(await reader.readReaderToken('reader-2'), 'token-二');
  faults.asset = { code: 999 };
  await assert.rejects(reader.readReaderToken('reader-1'), /无法读取/);
});

test('reading statistics isolate the current device and exclude unknown dates', () => {
  const now = new Date(2026, 10, 7, 12).getTime();
  const sessions = [session('1', { endedAtMs: new Date(2026, 10, 5, 10).getTime(), durationMs: 60000 }),
    session('2', { endedAtMs: new Date(2026, 10, 6, 10).getTime(), durationMs: 120000 }),
    session('3', { endedAtMs: new Date(2026, 10, 7, 10).getTime(), durationMs: 180000 }),
    session('4', { endedAtMs: new Date(2026, 10, 1, 10).getTime() }), session('5')];
  const cache = { currentDevice: 'reader-1', histories: [{ deviceId: 'reader-1', sessions },
    { deviceId: 'reader-2', sessions: [] }], books: [{ bookId: 'book-1', title: '小熊借伞' }] };
  const summary = stats.summarizeReading(cache, now);
  assert.equal(summary.todayCount, 1);
  assert.equal(summary.streakDays, 3);
  assert.equal(summary.undatedCount, 1);
  assert.equal(summary.currentWeek.sessionCount, 3);
  assert.equal(summary.currentWeek.durationMs, 360000);
  assert.equal(summary.previousWeek.sessionCount, 1);
  assert.equal(summary.latestTitle, '小熊借伞');
  assert.equal(stats.summarizeReading({ ...cache, currentDevice: 'reader-2' }, now).currentWeek.sessionCount, 0);
  assert.equal(stats.confirmedEndTime(session('future', { endedAtMs: now + 600000 }), now), undefined);
});

test('weekly position review metrics follow the selected reader and reading week', () => {
  const now = new Date(2026, 10, 11, 12).getTime();
  const currentId = '11111111111111111111111111111111';
  const currentConfirmedId = '22222222222222222222222222222222';
  const previousId = '33333333333333333333333333333333';
  const orphanedId = '44444444444444444444444444444444';
  const current = session('review-current-week', { endedAtMs: new Date(2026, 10, 10, 10).getTime(),
    trajectory: [trace(currentId), trace(currentConfirmedId, { offsetMs: 800 })], trajectoryDropped: 0,
    traceStats: { repeated: 0, relocated: 0, uncertain: 0 } });
  const previous = session('review-previous-week', { endedAtMs: new Date(2026, 10, 6, 10).getTime(),
    trajectory: [trace(previousId)], trajectoryDropped: 0,
    traceStats: { repeated: 0, relocated: 0, uncertain: 0 } });
  const reviews = [
    { deviceId: 'reader-1', sessionId: current.sessionId, eventId: currentId,
      status: 'position_mismatch', note: '', updatedAtMs: now },
    { deviceId: 'reader-1', sessionId: current.sessionId, eventId: currentConfirmedId,
      status: 'confirmed', note: '', updatedAtMs: now },
    { deviceId: 'reader-1', sessionId: previous.sessionId, eventId: previousId,
      status: 'needs_review', note: '', updatedAtMs: now },
    { deviceId: 'reader-1', sessionId: current.sessionId, eventId: orphanedId,
      status: 'confirmed', note: '', updatedAtMs: now },
    { deviceId: 'reader-2', sessionId: current.sessionId, eventId: currentId,
      status: 'position_mismatch', note: '', updatedAtMs: now }
  ];
  const cache = { currentDevice: 'reader-1', histories: [
    { deviceId: 'reader-1', cursor: '', sessions: [current, previous] },
    { deviceId: 'reader-2', cursor: '', sessions: [current] }
  ], positionReviews: reviews };

  const summary = stats.summarizeReading(cache, now);

  assert.deepEqual(json([summary.currentWeek.reviewCount, summary.currentWeek.mismatchCount,
    summary.currentWeek.confirmedCount, summary.currentWeek.needsReviewCount]), [2, 1, 1, 0]);
  assert.deepEqual(json([summary.previousWeek.reviewCount, summary.previousWeek.mismatchCount,
    summary.previousWeek.confirmedCount, summary.previousWeek.needsReviewCount]), [1, 0, 0, 1]);
  assert.equal(cache.histories[0].sessions[0].trajectory[0].status, 'matched');
});

(async () => {
  const results = [];
  try {
    for (const { name, run } of tests) {
      network.replies = []; network.calls = []; network.destroyed = 0;
      faults.writeLimit = faults.readLimit = Infinity; faults.sync = false; faults.asset = null;
      try {
        await run();
        assert.equal(network.destroyed, network.calls.length, 'HTTP clients must always be destroyed');
        results.push({ name, passed: true });
      } catch (error) {
        results.push({ name, passed: false, error: String(error.stack || error) });
      }
    }
    console.log(JSON.stringify({ scope: 'ArkTS service logic with desktop API adapters; no HarmonyOS UI/device execution',
      compilerVersion: ts.version, passed: results.filter(item => item.passed).length, total: results.length, results }, null, 2));
    if (results.some(item => !item.passed)) { process.exitCode = 1; }
  } finally {
    assert.ok(path.dirname(temporary) === os.tmpdir() && path.basename(temporary).startsWith('tongyue-parent-test-'));
    fs.rmSync(temporary, { recursive: true });
  }
})();
