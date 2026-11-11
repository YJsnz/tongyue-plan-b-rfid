import { http } from '@kit.NetworkKit';
import { fileIo } from '@kit.CoreFileKit';
import { asset } from '@kit.AssetStoreKit';
import { util } from '@kit.ArkTS';

export interface ReaderInfo {
  deviceId: string;
  firmwareVersion: string;
  protocolVersion: number;
}

interface Scan {
  scanId: string;
}

export interface ScanResult {
  status: string;
  tagUid?: string;
}

export interface ReaderBook {
  bookId: string;
  title: string;
  version?: string;
}

export interface ImportedStoryPack extends ReaderBook {
  alreadyInstalled: boolean;
}

const MAX_STORYPACK_SIZE: number = 24 * 1024 * 1024;

export async function readStoryPack(uri: string): Promise<ArrayBuffer> {
  const file = await fileIo.open(uri, fileIo.OpenMode.READ_ONLY);
  try {
    const size = (await fileIo.stat(file.fd)).size;
    if (size <= 0 || size > MAX_STORYPACK_SIZE) { throw new Error('请选择不超过 24 MiB 的 StoryPack 文件'); }
    const bytes = new Uint8Array(size);
    let offset = 0;
    while (offset < size) {
      const chunk = new ArrayBuffer(Math.min(262144, size - offset));
      const count = await fileIo.read(file.fd, chunk);
      if (count <= 0) { throw new Error('文件读取不完整，请重新选择'); }
      bytes.set(new Uint8Array(chunk, 0, count), offset);
      offset += count;
    }
    return bytes.buffer;
  } finally {
    await fileIo.close(file);
  }
}

interface BookList {
  items: ReaderBook[];
}

interface Binding {
  scanId: string;
  bookId: string;
}

interface ConfirmedBinding {
  tagUid: string;
  bookId: string;
}

export interface ReadingSession {
  sessionId: string;
  bookId: string;
  packVersion: string;
  endedAtMs?: number | null;
  syncedAtMs?: number;
  durationMs: number;
  lastSegmentId: string;
  positionSource: string;
  answers?: AnswerEvidence[];
  trajectory?: PositionTrace[];
  trajectoryDropped?: number;
  traceStats?: TraceStats;
  lastWordIndex?: number;
  positionUncertain?: boolean;
}

export interface PositionTrace {
  eventId: string;
  offsetMs: number;
  segmentId: string;
  source: string;
  status: string;
  segmentNumber?: number;
  segmentText?: string;
  wordIndex?: number;
  textStart?: number;
  textEnd?: number;
  text?: string;
}

export interface TraceStats {
  repeated: number;
  relocated: number;
  uncertain: number;
}

export interface PositionReview {
  deviceId: string;
  sessionId: string;
  eventId: string;
  status: string;
  note: string;
  updatedAtMs: number;
}

interface PositionReviewBatch {
  items: PositionReview[];
}

const POSITION_REVIEW_STATUSES: string[] = ['needs_review', 'position_mismatch', 'confirmed'];
const MAX_POSITION_REVIEWS: number = 512;

function validCount(value: number | undefined): boolean {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
}

function validatedTrajectory(session: ReadingSession): PositionTrace[] | undefined {
  if (session.trajectory === undefined) { return undefined; }
  const entries = session.trajectory;
  const stats = session.traceStats;
  if (!Array.isArray(entries) || entries.length > 128 || !validCount(session.trajectoryDropped) || !stats ||
    !validCount(stats.repeated) || !validCount(stats.relocated) || !validCount(stats.uncertain)) {
    throw new Error('阅读器返回的阅读轨迹格式不正确');
  }
  let previous = 0;
  let repeated = 0;
  let relocated = 0;
  let uncertain = 0;
  const seen = new Set<string>();
  const result = entries.map((entry: PositionTrace): PositionTrace => {
    if (!entry || typeof entry.eventId !== 'string' || !/^[a-f0-9]{32}$/.test(entry.eventId) || seen.has(entry.eventId) ||
      typeof entry.segmentId !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(entry.segmentId) ||
      !validCount(entry.offsetMs) || entry.offsetMs < previous || entry.offsetMs > session.durationMs ||
      !['manual', 'mfcc', 'ctc'].includes(entry.source) ||
      !(entry.source === 'manual' ? entry.status === 'selected' :
        ['matched', 'repeated', 'relocated', 'uncertain'].includes(entry.status))) {
      throw new Error('阅读器返回了一条无效定位记录');
    }
    if ((entry.segmentNumber !== undefined && (!validCount(entry.segmentNumber) || entry.segmentNumber < 1)) ||
      (entry.segmentText !== undefined && (typeof entry.segmentText !== 'string' || entry.segmentText.length > 128))) {
      throw new Error('阅读器返回的段落信息无效');
    }
    const hasWord = entry.wordIndex !== undefined || entry.textStart !== undefined ||
      entry.textEnd !== undefined || entry.text !== undefined;
    if (entry.source === 'ctc' && entry.status !== 'uncertain' && !hasWord) {
      throw new Error('阅读器的 CTC 定位记录缺少词位');
    }
    if (hasWord && (entry.source !== 'ctc' || entry.status === 'uncertain' || !validCount(entry.wordIndex) ||
      !validCount(entry.textStart) || !validCount(entry.textEnd) || entry.textEnd! <= entry.textStart! ||
      typeof entry.text !== 'string' || !entry.text || entry.text.length > 128)) {
      throw new Error('阅读器返回的词位信息无效');
    }
    seen.add(entry.eventId);
    previous = entry.offsetMs;
    if (entry.status === 'repeated') { repeated++; }
    if (entry.status === 'relocated') { relocated++; }
    if (entry.status === 'uncertain') { uncertain++; }
    return { eventId: entry.eventId, offsetMs: entry.offsetMs, segmentId: entry.segmentId,
      source: entry.source, status: entry.status, segmentNumber: entry.segmentNumber, segmentText: entry.segmentText,
      wordIndex: entry.wordIndex, textStart: entry.textStart, textEnd: entry.textEnd, text: entry.text };
  });
  if (stats.repeated < repeated || stats.relocated < relocated || stats.uncertain < uncertain) {
    throw new Error('阅读器的轨迹统计与定位记录不一致');
  }
  return result;
}

export interface AnswerEvidence {
  segmentId: string;
  question: string;
  answer: string;
  evidence: string;
  source: string;
  offsetMs: number;
}

function validEvidenceText(value: string): boolean {
  return typeof value === 'string' && value.length > 0 && value.length <= 2048;
}

function validatedAnswers(session: ReadingSession): AnswerEvidence[] | undefined {
  const answers = session.answers;
  if (answers === undefined) { return undefined; }
  if (!Array.isArray(answers) || answers.length > 20) { throw new Error('阅读器返回的问答记录格式不正确'); }
  return answers.map((entry: AnswerEvidence): AnswerEvidence => {
    if (!entry || !validEvidenceText(entry.segmentId) || !validEvidenceText(entry.question) ||
      !validEvidenceText(entry.answer) || !validEvidenceText(entry.evidence) ||
      (entry.source !== 'voice' && entry.source !== 'text') || !Number.isSafeInteger(entry.offsetMs) ||
      entry.offsetMs < 0 || entry.offsetMs > session.durationMs) {
      throw new Error('阅读器返回了一条无效问答记录');
    }
    return { segmentId: entry.segmentId, question: entry.question, answer: entry.answer,
      evidence: entry.evidence, source: entry.source, offsetMs: entry.offsetMs };
  });
}

interface SessionBatch {
  items: ReadingSession[];
  nextCursor: string;
  hasMore?: boolean;
}

interface ReaderErrorBody {
  code?: string;
}

export interface ReaderHistory {
  deviceId: string;
  cursor: string;
  sessions: ReadingSession[];
}

export interface ReaderCache {
  address: string;
  currentDevice: string;
  histories: ReaderHistory[];
  caCertificate?: string;
  books?: ReaderBook[];
  positionReviews?: PositionReview[];
}

export function updatePositionReview(cache: ReaderCache, deviceId: string, sessionId: string, eventId: string,
  status: string, note: string, now: number): ReaderCache {
  const history = cache.histories.find((item: ReaderHistory) => item.deviceId === deviceId);
  const session = history?.sessions.find((item: ReadingSession) => item.sessionId === sessionId);
  if (!session?.trajectory?.some((entry: PositionTrace) => entry.eventId === eventId) ||
    !['needs_review', 'position_mismatch', 'confirmed'].includes(status) ||
    typeof note !== 'string' || note.trim().length > 256 || !Number.isSafeInteger(now) || now < 0) {
    throw new Error('无法保存这条位置复核，请重新选择定位点');
  }
  const reviews = (cache.positionReviews ?? []).filter((entry: PositionReview) =>
    entry.deviceId !== deviceId || entry.sessionId !== sessionId || entry.eventId !== eventId);
  if (reviews.length >= 512) { throw new Error('本机复核记录已达上限，请先整理已有记录'); }
  reviews.push({ deviceId: deviceId, sessionId: sessionId, eventId: eventId, status: status,
    note: note.trim(), updatedAtMs: now });
  return { address: cache.address, currentDevice: cache.currentDevice, histories: cache.histories,
    caCertificate: cache.caCertificate, books: cache.books, positionReviews: reviews };
}

export function emptyReaderCache(): ReaderCache {
  return { address: '', currentDevice: '', histories: [] };
}

function tokenAlias(deviceId: string): Uint8Array {
  return new util.TextEncoder().encodeInto('tongyue.reader.' + deviceId);
}

export async function readReaderToken(deviceId: string): Promise<string> {
  const query: asset.AssetMap = new Map();
  query.set(asset.Tag.ALIAS, tokenAlias(deviceId));
  query.set(asset.Tag.RETURN_TYPE, asset.ReturnType.ALL);
  try {
    const rows = await asset.query(query);
    const value = rows[0]?.get(asset.Tag.SECRET);
    return value instanceof Uint8Array ? util.TextDecoder.create().decodeToString(value) : '';
  } catch (error) {
    if ((error as ReaderAssetError).code === 24000002) { return ''; }
    throw new Error('无法读取已保存的配对密钥');
  }
}

interface ReaderAssetError {
  code: number;
}

export async function saveReaderToken(deviceId: string, token: string): Promise<void> {
  const item: asset.AssetMap = new Map();
  item.set(asset.Tag.ALIAS, tokenAlias(deviceId));
  item.set(asset.Tag.SECRET, new util.TextEncoder().encodeInto(token));
  item.set(asset.Tag.CONFLICT_RESOLUTION, asset.ConflictResolution.OVERWRITE);
  try {
    await asset.add(item);
  } catch (_) {
    throw new Error('无法保存配对密钥');
  }
}

export function readReaderCache(path: string): ReaderCache {
  if (!fileIo.accessSync(path)) {
    return emptyReaderCache();
  }
  return JSON.parse(fileIo.readTextSync(path)) as ReaderCache;
}

export function saveReaderCache(path: string, cache: ReaderCache): void {
  const temp = path + '.tmp';
  const file = fileIo.openSync(temp, fileIo.OpenMode.WRITE_ONLY | fileIo.OpenMode.CREATE | fileIo.OpenMode.TRUNC);
  try {
    const bytes = new util.TextEncoder().encodeInto(JSON.stringify(cache));
    let offset = 0;
    while (offset < bytes.byteLength) {
      const written = fileIo.writeSync(file.fd, bytes.buffer.slice(bytes.byteOffset + offset,
        bytes.byteOffset + bytes.byteLength));
      if (written <= 0) { throw new Error('阅读记录未能完整保存，请检查存储后重试'); }
      offset += written;
    }
    fileIo.fsyncSync(file.fd);
  } finally {
    fileIo.closeSync(file);
  }
  fileIo.renameSync(temp, path);
}

export function readerAddress(input: string): string {
  const address = input.trim().replace(/\/+$/, '');
  if (!/^https?:\/\/([A-Za-z0-9.-]+|\[[0-9A-Fa-f:]+\])(:[0-9]{1,5})?$/.test(address)) {
    throw new Error('请输入完整地址，例如 https://192.168.1.10:8765');
  }
  if (address.startsWith('http://') && !/^http:\/\/127\.0\.0\.1(:[0-9]{1,5})?$/.test(address)) {
    throw new Error('局域网阅读器需要 HTTPS 地址');
  }
  return address;
}

export class RfidReader {
  private address: string;
  private token: string;
  private caCertificate: string;

  constructor(address: string, token: string, caCertificate: string) {
    this.address = address;
    this.token = token;
    this.caCertificate = caCertificate;
  }

  private async request<T>(path: string, method: http.RequestMethod, body?: string | ArrayBuffer): Promise<T> {
    const client = http.createHttp();
    try {
      const options: http.HttpRequestOptions = {
        method: method, expectDataType: http.HttpDataType.STRING, usingCache: false,
        connectTimeout: 3500, readTimeout: body instanceof ArrayBuffer ? 90000 : 3500, usingProxy: false, maxRedirects: 0
      };
      const headers: Record<string, string> = {};
      if (this.token) { headers['Authorization'] = 'Bearer ' + this.token; }
      if (this.caCertificate) { options.caData = this.caCertificate; }
      if (body !== undefined) {
        options.extraData = body;
        headers['Content-Type'] = typeof body === 'string' ? 'application/json' : 'application/gzip';
      }
      options.header = headers;
      const response = await client.request(this.address + path, options);
      if (typeof response.result !== 'string') {
        throw new Error('阅读器返回了无法识别的数据');
      }
      if (response.responseCode !== 200) {
        let message = `阅读器返回错误 (${response.responseCode})`;
        try {
          const failure = JSON.parse(response.result) as ReaderErrorBody;
          if (failure.code === 'SCAN_EXPIRED') { message = '扫描已超时，请重新扫描'; }
          if (failure.code === 'TAG_ALREADY_BOUND') { message = '这个标签已绑定到另一本绘本'; }
          if (failure.code === 'PACK_NOT_FOUND') { message = '阅读器上还没有这本绘本的故事包'; }
          if (failure.code === 'PACK_INVALID') { message = '故事包不可用，请检查阅读器上的文件'; }
          if (failure.code === 'SCAN_TAG_MISMATCH') { message = '标签与扫描结果不一致，请重新扫描'; }
          if (failure.code === 'RFID_UNAVAILABLE') { message = '阅读器的读卡模块暂时不可用'; }
          if (failure.code === 'PAIRING_REQUIRED') { message = '请输入阅读器的正确配对密钥'; }
          if (failure.code === 'STORAGE_UNAVAILABLE') { message = '阅读器暂时无法保存数据，请检查存储后重试'; }
          if (failure.code === 'STORAGE_RESTART_REQUIRED') { message = '阅读器存储异常，请检查存储并重启阅读器'; }
          if (failure.code === 'PACK_ALREADY_EXISTS') { message = '阅读器已安装同一编号的其他版本，请联系维护人员'; }
          if (failure.code === 'PACK_TOO_LARGE') { message = '绘本包超过 24 MiB'; }
          if (failure.code === 'PACK_IMPORT_UNAVAILABLE') { message = '该阅读器未开启绘本导入'; }
          if (failure.code === 'PACK_IMPORT_BUSY') { message = '阅读器正在导入其他绘本，请稍后重试'; }
          if (failure.code === 'PACK_UPLOAD_TIMEOUT' || failure.code === 'PACK_UPLOAD_INCOMPLETE') {
            message = '绘本包未传输完成，请检查网络后重试';
          }
          if (failure.code === 'INVALID_PACK_UPLOAD') { message = '请选择正确的 .tar.gz 绘本包'; }
        } catch (_) {
          // Some development servers return a non-JSON error page.
        }
        throw new Error(message);
      }
      return JSON.parse(response.result) as T;
    } finally {
      client.destroy();
    }
  }

  async device(): Promise<ReaderInfo> {
    const result = await this.request<ReaderInfo>('/v1/device', http.RequestMethod.GET);
    if (result.protocolVersion !== 1 || typeof result.deviceId !== 'string' ||
      !/^[A-Za-z0-9_-]{1,96}$/.test(result.deviceId)) {
      throw new Error('这个阅读器的接口版本不受支持');
    }
    return result;
  }

  async books(): Promise<ReaderBook[]> {
    const list = await this.request<BookList>('/v1/books', http.RequestMethod.GET);
    if (!Array.isArray(list.items) || list.items.some((book: ReaderBook) =>
      typeof book.title !== 'string' || !book.title || typeof book.bookId !== 'string' ||
      !/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(book.bookId))) {
      throw new Error('阅读器返回的绘本列表格式不正确');
    }
    return list.items;
  }

  async importStoryPack(archive: ArrayBuffer): Promise<ImportedStoryPack> {
    const result = await this.request<ImportedStoryPack>('/v1/storypacks', http.RequestMethod.POST, archive);
    if (!result || typeof result.bookId !== 'string' || typeof result.title !== 'string' ||
      typeof result.version !== 'string' || typeof result.alreadyInstalled !== 'boolean') {
      throw new Error('阅读器返回了无法识别的导入结果');
    }
    return result;
  }

  async startScan(): Promise<string> {
    const result = await this.request<Scan>('/v1/scans', http.RequestMethod.POST);
    if (typeof result.scanId !== 'string' || !result.scanId) { throw new Error('阅读器未返回扫描编号'); }
    return result.scanId;
  }

  async checkScan(scanId: string): Promise<ScanResult> {
    return this.request<ScanResult>('/v1/scans/' + encodeURIComponent(scanId), http.RequestMethod.GET);
  }

  async bind(uid: string, scanId: string, bookId: string): Promise<void> {
    const binding: Binding = { scanId: scanId, bookId: bookId };
    const result = await this.request<ConfirmedBinding>('/v1/book-bindings/' + encodeURIComponent(uid),
      http.RequestMethod.PUT, JSON.stringify(binding));
    if (result.tagUid !== uid || result.bookId !== bookId) { throw new Error('阅读器返回的绑定结果不一致'); }
  }

  async sync(history: ReaderHistory): Promise<ReaderHistory> {
    const sessions = new Map<string, ReadingSession>();
    history.sessions.forEach((session: ReadingSession) => { sessions.set(session.sessionId, session); });
    const syncTime = Date.now();
    let cursor = history.cursor;
    const cursors = new Set<string>();
    do {
      cursors.add(cursor);
      const batch = await this.request<SessionBatch>('/v1/reading-sessions?after=' + encodeURIComponent(cursor) + '&limit=8',
        http.RequestMethod.GET);
      if (!Array.isArray(batch.items) || typeof batch.nextCursor !== 'string') {
        throw new Error('阅读器返回的记录格式不正确');
      }
      batch.items.forEach((session: ReadingSession) => {
        if (!session || typeof session.sessionId !== 'string' || !session.sessionId || typeof session.bookId !== 'string' ||
          !Number.isFinite(session.durationMs) || session.durationMs < 0 || typeof session.lastSegmentId !== 'string') {
          throw new Error('阅读器返回了一条无效记录');
        }
        const previous = sessions.get(session.sessionId);
        const trajectory = validatedTrajectory(session);
        if ((session.lastWordIndex !== undefined && !validCount(session.lastWordIndex)) ||
          (session.positionUncertain !== undefined && typeof session.positionUncertain !== 'boolean')) {
          throw new Error('阅读器返回的末次位置格式不正确');
        }
        const saved: ReadingSession = {
          sessionId: session.sessionId, bookId: session.bookId, packVersion: session.packVersion,
          endedAtMs: session.endedAtMs, durationMs: session.durationMs, lastSegmentId: session.lastSegmentId,
          positionSource: session.positionSource, syncedAtMs: previous?.syncedAtMs ?? syncTime,
          answers: validatedAnswers(session) ?? previous?.answers,
          trajectory: trajectory ?? previous?.trajectory,
          trajectoryDropped: trajectory !== undefined ? session.trajectoryDropped : previous?.trajectoryDropped,
          traceStats: trajectory !== undefined ? session.traceStats : previous?.traceStats,
          lastWordIndex: session.lastWordIndex, positionUncertain: session.positionUncertain
        };
        sessions.set(session.sessionId, saved);
      });
      cursor = batch.nextCursor;
      if (batch.hasMore !== true) { break; }
      if (!cursor || cursors.has(cursor)) { throw new Error('阅读器返回了重复的同步位置'); }
    } while (true);
    return { deviceId: history.deviceId, cursor: cursor, sessions: Array.from(sessions.values()) };
  }

  async syncPositionReviews(history: ReaderHistory, cached: PositionReview[]): Promise<PositionReview[]> {
    const traces = new Map<string, Set<string>>();
    history.sessions.forEach((session: ReadingSession) => {
      const trajectory = validatedTrajectory(session);
      traces.set(session.sessionId, new Set((trajectory ?? []).map((entry: PositionTrace) => entry.eventId)));
    });

    const validate = (value: PositionReview): PositionReview => {
      if (!value || value.deviceId !== history.deviceId || typeof value.sessionId !== 'string' ||
        !/^[A-Za-z0-9_-]{1,96}$/.test(value.sessionId) || typeof value.eventId !== 'string' ||
        !/^[a-f0-9]{32}$/.test(value.eventId) || !POSITION_REVIEW_STATUSES.includes(value.status) ||
        typeof value.note !== 'string' || value.note.length > 256 || !Number.isSafeInteger(value.updatedAtMs) ||
        value.updatedAtMs < 0 || !traces.get(value.sessionId)?.has(value.eventId)) {
        throw new Error('阅读器返回了一条无效的位置复核记录');
      }
      return { deviceId: value.deviceId, sessionId: value.sessionId, eventId: value.eventId,
        status: value.status, note: value.note, updatedAtMs: value.updatedAtMs };
    };

    const readRemote = async (): Promise<Map<string, PositionReview>> => {
      const batch = await this.request<PositionReviewBatch>('/v1/position-reviews', http.RequestMethod.GET);
      if (!batch || !Array.isArray(batch.items) || batch.items.length > MAX_POSITION_REVIEWS) {
        throw new Error('阅读器返回的位置复核列表格式不正确');
      }
      const result = new Map<string, PositionReview>();
      batch.items.forEach((item: PositionReview) => {
        const review = validate(item);
        const key = review.sessionId + '/' + review.eventId;
        if (result.has(key)) { throw new Error('阅读器返回了重复的位置复核记录'); }
        result.set(key, review);
      });
      return result;
    };

    const localReviews = cached.filter((review: PositionReview) => review.deviceId === history.deviceId);
    if (cached.length > MAX_POSITION_REVIEWS || localReviews.length > MAX_POSITION_REVIEWS) {
      throw new Error('本机位置复核记录已达同步上限');
    }
    const local = new Map<string, PositionReview>();
    localReviews.forEach((item: PositionReview) => {
      const review = validate(item);
      const key = review.sessionId + '/' + review.eventId;
      if (local.has(key)) { throw new Error('本机存在重复的位置复核记录'); }
      local.set(key, review);
    });

    let remote = await readRemote();
    const localKeys = Array.from(local.keys());
    for (let index = 0; index < localKeys.length; index++) {
      const key = localKeys[index];
      const review = local.get(key);
      if (!review) { continue; }
      const current = remote.get(key);
      if (!current || review.updatedAtMs > current.updatedAtMs) {
        const path = '/v1/position-reviews/' + encodeURIComponent(review.sessionId) + '/' +
          encodeURIComponent(review.eventId);
        const saved = await this.request<PositionReview>(path, http.RequestMethod.PUT,
          JSON.stringify({ status: review.status, note: review.note, updatedAtMs: review.updatedAtMs }));
        remote.set(key, validate(saved));
      }
    }
    remote = await readRemote();
    const otherDevices = cached.filter((review: PositionReview) => review.deviceId !== history.deviceId);
    const merged = [...otherDevices, ...Array.from(remote.values())];
    if (merged.length > MAX_POSITION_REVIEWS) {
      throw new Error('本机复核记录已达上限，请先整理其他阅读器的复核记录');
    }
    return merged;
  }
}
