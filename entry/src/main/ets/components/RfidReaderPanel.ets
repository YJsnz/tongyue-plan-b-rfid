import { picker } from '@kit.CoreFileKit';
import { FONT_BODY, FONT_STRONG, FONT_TITLE } from '../theme/Typography';
import { AnswerEvidence, emptyReaderCache, readReaderCache, readReaderToken, ReaderBook, ReaderCache, readerAddress, ReaderHistory,
  ReadingSession, readStoryPack, RfidReader, saveReaderCache, saveReaderToken, PositionTrace, PositionReview,
  updatePositionReview } from '../services/RfidReader';
import { confirmedEndTime } from '../services/ReadingStats';

@Component
export struct RfidReaderPanel {
  onCacheChanged: (cache: ReaderCache) => void = (_: ReaderCache): void => {};
  @Prop managePacks: boolean = false;
  @State address: string = '';
  @State pairingToken: string = '';
  @State caCertificate: string = '';
  @State books: ReaderBook[] = [];
  @State deviceId: string = '';
  @State busy: boolean = false;
  @State message: string = '';
  @State book: number = 0;
  @State tag: string = '';
  @State sessions: ReadingSession[] = [];
  @State expandedSession: string = '';
  @State expandedTrace: string = '';
  @State reviewTarget: string = '';
  @State reviewNote: string = '';
  @State reviews: PositionReview[] = [];
  private cachePath: string = '';
  private cache: ReaderCache = emptyReaderCache();
  private reader?: RfidReader;
  private scanId: string = '';
  private visible: boolean = false;

  aboutToAppear(): void {
    this.visible = true;
    const context = this.getUIContext().getHostContext();
    if (!context) {
      this.message = '无法访问应用存储';
      return;
    }
    this.cachePath = context.filesDir + '/rfid-reader.json';
    try {
      this.cache = readReaderCache(this.cachePath);
      this.address = this.cache.address;
      this.caCertificate = this.cache.caCertificate ?? '';
      this.books = this.cache.books ?? [];
      this.reviews = this.cache.positionReviews ?? [];
      this.showHistory(this.cache.currentDevice);
    } catch (_) {
      this.cachePath = '';
      this.message = '无法读取本机的阅读记录';
    }
    if (this.address) { this.connect(); }
  }

  aboutToDisappear(): void {
    this.visible = false;
  }

  private showHistory(deviceId: string): void {
    const history = this.cache.histories.find((entry: ReaderHistory) => entry.deviceId === deviceId);
    this.sessions = history ? history.sessions.slice().reverse() : [];
  }

  private save(cache: ReaderCache): void {
    saveReaderCache(this.cachePath, cache);
    this.cache = cache;
    this.reviews = cache.positionReviews ?? [];
    this.onCacheChanged(cache);
  }

  private invalidateConnection(): void {
    this.reader = undefined;
    this.deviceId = '';
    this.tag = '';
    this.message = '';
  }

  private async connect(): Promise<void> {
    if (this.busy || !this.cachePath) { return; }
    this.busy = true;
    this.deviceId = '';
    this.reader = undefined;
    this.tag = '';
    this.scanId = '';
    this.message = '正在连接阅读器…';
    try {
      const address = readerAddress(this.address);
      let token = this.pairingToken.trim();
      const secure = address.startsWith('https://');
      const caCertificate = secure ? this.caCertificate.trim() : '';
      if (token && (!secure || !/^[A-Za-z0-9_-]{32,128}$/.test(token))) {
        throw new Error('配对密钥格式不正确，请从阅读器重新复制');
      }
      if (caCertificate && (caCertificate.length > 8000 || !caCertificate.startsWith('-----BEGIN CERTIFICATE-----') ||
        !caCertificate.endsWith('-----END CERTIFICATE-----'))) {
        throw new Error('请粘贴完整的 PEM 格式 CA 证书');
      }
      const reuseToken = secure && !token && address === this.cache.address &&
        caCertificate === (this.cache.caCertificate ?? '') && this.cache.currentDevice.length > 0;
      if (reuseToken) { token = await readReaderToken(this.cache.currentDevice); }
      if (!this.visible) { return; }
      const reader = new RfidReader(address, token, caCertificate);
      const info = await reader.device();
      if (reuseToken && info.deviceId !== this.cache.currentDevice) {
        throw new Error('此地址的阅读器已更换，请输入新阅读器的配对密钥');
      }
      const books = await reader.books();
      if (!this.visible) { return; }
      if (token && !reuseToken) { await saveReaderToken(info.deviceId, token); }
      if (!this.visible) { return; }
      this.save({ address: address, currentDevice: info.deviceId, histories: this.cache.histories,
        caCertificate: caCertificate, books: books, positionReviews: this.cache.positionReviews });
      this.reader = reader;
      this.deviceId = info.deviceId;
      this.expandedSession = '';
      this.expandedTrace = '';
      this.reviewTarget = '';
      this.books = books;
      this.book = 0;
      this.pairingToken = '';
      this.showHistory(info.deviceId);
      this.message = '阅读器已连接';
    } catch (error) {
      if (this.visible) { this.message = (error as Error).message || '无法连接，请检查阅读器地址和网络'; }
    } finally {
      if (this.visible) { this.busy = false; }
    }
  }

  private async scan(): Promise<void> {
    const reader = this.reader;
    if (!reader || this.busy) { return; }
    this.busy = true;
    this.tag = '';
    this.scanId = '';
    this.message = '请将绘本标签贴近阅读器';
    try {
      const id = await reader.startScan();
      const deadline = Date.now() + 35000;
      while (this.visible && Date.now() < deadline) {
        const result = await reader.checkScan(id);
        if (!this.visible) { return; }
        if (result.status === 'detected' && typeof result.tagUid === 'string' && result.tagUid) {
          this.scanId = id;
          this.tag = result.tagUid;
          this.message = '已读到标签，请确认要绑定的绘本';
          return;
        }
        if (result.status === 'expired') { break; }
        await new Promise<void>((resolve: () => void) => { setTimeout(resolve, 700); });
      }
      if (this.visible) { this.message = '扫描已超时，请重新扫描'; }
    } catch (error) {
      if (this.visible) { this.message = (error as Error).message || '扫描失败，请重试'; }
    } finally {
      if (this.visible) { this.busy = false; }
    }
  }

  private async bind(): Promise<void> {
    const reader = this.reader;
    const book = this.books[this.book];
    if (!reader || !book || this.busy || !this.tag || !this.scanId) { return; }
    this.busy = true;
    try {
      await reader.bind(this.tag, this.scanId, book.bookId);
      if (!this.visible) { return; }
      this.message = '已将标签绑定到《' + book.title + '》';
      this.tag = '';
      this.scanId = '';
    } catch (error) {
      if (this.visible) { this.message = (error as Error).message || '绑定失败，请重试'; }
    } finally {
      if (this.visible) { this.busy = false; }
    }
  }

  private async sync(): Promise<void> {
    const reader = this.reader;
    if (!reader || this.busy) { return; }
    this.busy = true;
    const deviceId = this.deviceId;
    try {
      const info = await reader.device();
      if (info.deviceId !== deviceId) { throw new Error('阅读器已更换，请重新连接'); }
      const history = this.cache.histories.find((entry: ReaderHistory) => entry.deviceId === deviceId) ??
        { deviceId: deviceId, cursor: '', sessions: [] } as ReaderHistory;
      const next = await reader.sync(history);
      if (!this.visible) { return; }
      const histories = this.cache.histories.filter((entry: ReaderHistory) => entry.deviceId !== deviceId);
      histories.push(next);
      this.save({ address: this.cache.address, currentDevice: deviceId, histories: histories,
        caCertificate: this.cache.caCertificate, books: this.books, positionReviews: this.cache.positionReviews });
      this.showHistory(deviceId);
      this.message = next.sessions.length > history.sessions.length ? '阅读记录已同步' : '没有新的阅读记录';
    } catch (error) {
      if (this.visible) { this.message = (error as Error).message || '同步失败，本机记录仍可查看'; }
    } finally {
      if (this.visible) { this.busy = false; }
    }
  }

  private async importPack(): Promise<void> {
    const reader = this.reader;
    const context = this.getUIContext().getHostContext();
    if (!reader || !context || this.busy) { return; }
    this.busy = true;
    try {
      const options = new picker.DocumentSelectOptions();
      options.maxSelectNumber = 1;
      options.fileSuffixFilters = ['StoryPack (.tar.gz, .tgz)|.gz,.tgz'];
      const selected = await new picker.DocumentViewPicker(context).select(options);
      if (!this.visible || selected.length === 0) { return; }
      this.message = '正在读取并导入绘本包…';
      const archive = await readStoryPack(selected[0]);
      if (!this.visible) { return; }
      const imported = await reader.importStoryPack(archive);
      if (!this.visible) { return; }
      const updated = this.books.filter((entry: ReaderBook) => entry.bookId !== imported.bookId);
      updated.push({ bookId: imported.bookId, title: imported.title, version: imported.version });
      this.books = updated;
      this.save({ address: this.cache.address, currentDevice: this.cache.currentDevice, histories: this.cache.histories,
        caCertificate: this.cache.caCertificate, books: updated, positionReviews: this.cache.positionReviews });
      this.message = '《' + imported.title + '》' + (imported.alreadyInstalled ? '已在阅读器上' : '已导入阅读器');
    } catch (error) {
      if (this.visible) { this.message = (error as Error).message || '导入失败，请重试'; }
    } finally {
      if (this.visible) { this.busy = false; }
    }
  }

  private bookTitle(id: string): string {
    return this.books.find((book: ReaderBook) => book.bookId === id)?.title ?? id;
  }

  private bookOptions(): SelectOption[] {
    return this.books.map((book: ReaderBook): SelectOption => ({ value: book.title }));
  }

  private sessionDate(session: ReadingSession): string {
    const end = confirmedEndTime(session, Date.now());
    if (end !== undefined) { return new Date(end).toLocaleDateString(); }
    if (session.syncedAtMs) { return '日期未知 · 同步于 ' + new Date(session.syncedAtMs).toLocaleDateString(); }
    return '阅读日期未知';
  }

  private traceLabel(entry: PositionTrace): string {
    if (entry.status === 'selected') { return '选择段落'; }
    if (entry.status === 'repeated') { return '重读片段'; }
    if (entry.status === 'relocated') { return '重新定位'; }
    if (entry.status === 'uncertain') { return '位置待确认'; }
    return entry.source === 'ctc' ? '定位到词语' : '定位到段落';
  }

  private traceContext(entry: PositionTrace): string {
    if (entry.text) { return '「' + entry.text + '」'; }
    if (entry.segmentText) { return '「' + entry.segmentText + '」'; }
    return entry.segmentNumber ? '第 ' + entry.segmentNumber + ' 段' : '阅读位置';
  }

  private positionReview(session: ReadingSession, entry: PositionTrace): PositionReview | undefined {
    return this.reviews.find((review: PositionReview) => review.deviceId === this.cache.currentDevice &&
      review.sessionId === session.sessionId && review.eventId === entry.eventId);
  }

  private reviewLabel(session: ReadingSession, entry: PositionTrace): string {
    const review = this.positionReview(session, entry);
    if (!review) { return '复核此处位置'; }
    if (review.status === 'confirmed') { return '家长确认记录无误'; }
    if (review.status === 'position_mismatch') { return '已标记位置不符'; }
    return '已标记待核对';
  }

  private saveReview(session: ReadingSession, entry: PositionTrace, status: string): void {
    if (this.busy || !this.cachePath) { return; }
    try {
      this.save(updatePositionReview(this.cache, this.cache.currentDevice, session.sessionId,
        entry.eventId, status, this.reviewNote, Date.now()));
      this.reviewTarget = '';
      this.reviewNote = '';
      this.message = '位置复核已保存在本机';
    } catch (error) {
      this.message = (error as Error).message || '复核未保存，请重试';
    }
  }

  @Builder
  Trajectory(session: ReadingSession, entries: PositionTrace[]) {
    Button(this.expandedTrace === session.sessionId ? '收起阅读轨迹' : '查看阅读轨迹（' + entries.length + ' 处）')
      .fontFamily(FONT_STRONG).fontColor('#188C4A').backgroundColor('#E8F6EC')
      .onClick(() => {
        this.expandedTrace = this.expandedTrace === session.sessionId ? '' : session.sessionId;
        this.reviewTarget = '';
      });
    if (this.expandedTrace === session.sessionId) {
      Text('重读片段 ' + (session.traceStats?.repeated ?? 0) + ' 次 · 位置待确认 ' +
        (session.traceStats?.uncertain ?? 0) + ' 次').fontFamily(FONT_BODY).fontSize(13).fontColor('#53665E');
      if ((session.trajectoryDropped ?? 0) > 0) {
        Text('显示最近的定位点，另有 ' + session.trajectoryDropped + ' 处早期定位点未保留')
          .fontFamily(FONT_BODY).fontSize(12).fontColor('#53665E');
      }
      ForEach(entries, (entry: PositionTrace) => {
        Column({ space: 6 }) {
          Text(Math.floor(entry.offsetMs / 1000) + ' 秒 · ' + this.traceLabel(entry))
            .fontFamily(FONT_STRONG).fontSize(14).fontColor('#152044');
          Text(this.traceContext(entry)).fontFamily(FONT_BODY).fontSize(14).fontColor('#53665E');
          Button(this.reviewLabel(session, entry)).fontFamily(FONT_BODY).fontSize(13)
            .backgroundColor('#FFFFFF').fontColor('#188C4A').enabled(!this.busy)
            .onClick(() => {
              this.reviewTarget = this.reviewTarget === entry.eventId ? '' : entry.eventId;
              this.reviewNote = this.positionReview(session, entry)?.note ?? '';
            });
          if (this.reviewTarget === entry.eventId) {
            Text('结合当次共读情境核对阅读位置，复核结果保存在本机。')
              .fontFamily(FONT_BODY).fontSize(12).fontColor('#53665E');
            TextInput({ placeholder: '可填写当时读到的内容（选填）', text: this.reviewNote }).maxLength(256)
              .fontFamily(FONT_BODY).onChange((value: string) => { this.reviewNote = value; });
            Row({ space: 5 }) {
              Button('待核对').fontSize(12).backgroundColor('#E8F6EC').fontColor('#188C4A')
                .enabled(!this.busy).onClick(() => { this.saveReview(session, entry, 'needs_review'); });
              Button('位置不符').fontSize(12).backgroundColor('#FFF1DF').fontColor('#815828')
                .enabled(!this.busy).onClick(() => { this.saveReview(session, entry, 'position_mismatch'); });
              Button('记录无误').fontSize(12).backgroundColor('#E8F6EC').fontColor('#188C4A')
                .enabled(!this.busy).onClick(() => { this.saveReview(session, entry, 'confirmed'); });
            }
          }
        }.width('100%').alignItems(HorizontalAlign.Start).padding(10).backgroundColor('#F3F8F3').borderRadius(10);
      }, (entry: PositionTrace) => entry.eventId);
    }
  }

  @Builder
  Evidence(session: ReadingSession, answers: AnswerEvidence[]) {
    Button(this.expandedSession === session.sessionId ? '收起问答证据' : '查看问答证据（' + answers.length + ' 条）')
      .fontFamily(FONT_STRONG).fontColor('#188C4A').backgroundColor('#E8F6EC')
      .onClick(() => { this.expandedSession = this.expandedSession === session.sessionId ? '' : session.sessionId; });
    if (this.expandedSession === session.sessionId) {
      ForEach(answers, (entry: AnswerEvidence) => {
        Column({ space: 5 }) {
          Text((entry.source === 'voice' ? '语音提问' : '文字提问') + ' · 段落 ' + entry.segmentId)
            .fontFamily(FONT_BODY).fontSize(12).fontColor('#53665E');
          Text(entry.question + '？').fontFamily(FONT_STRONG).fontSize(16).fontColor('#152044');
          Text(entry.answer).fontFamily(FONT_BODY).fontSize(15).fontColor('#152044');
          Text('原文证据：「' + entry.evidence + '」').fontFamily(FONT_BODY).fontSize(14).fontColor('#53665E');
        }.width('100%').alignItems(HorizontalAlign.Start).padding(10).backgroundColor('#F3F8F3').borderRadius(10);
      }, (entry: AnswerEvidence, i: number) => i.toString() + ':' + entry.offsetMs.toString());
    }
  }

  build() {
    Column({ space: 14 }) {
      Text(this.managePacks ? '阅读器里的故事' : '阅读器与绘本标签').fontFamily(FONT_TITLE).fontSize(26).fontColor('#152044');
      TextInput({ placeholder: 'https://阅读器地址:8765', text: this.address }).fontFamily(FONT_BODY)
        .width('100%').enabled(!this.busy).onChange((value: string) => {
          this.address = value;
          this.pairingToken = '';
          this.caCertificate = value.trim() === this.cache.address ? this.cache.caCertificate ?? '' : '';
          this.invalidateConnection();
        });
      if (this.address.trim().startsWith('https://')) {
        TextInput({ placeholder: '阅读器配对密钥', text: this.pairingToken }).type(InputType.Password).maxLength(128)
          .width('100%').enabled(!this.busy).onChange((value: string) => {
            this.pairingToken = value;
            this.invalidateConnection();
          });
        TextArea({ placeholder: '局域网 CA 证书（PEM）', text: this.caCertificate }).maxLength(8000).height(90)
          .width('100%').enabled(!this.busy).onChange((value: string) => {
            this.caCertificate = value;
            this.invalidateConnection();
          });
      }
      Button(this.deviceId ? '重新连接' : '连接阅读器').fontFamily(FONT_STRONG).width('100%')
        .enabled(!this.busy).backgroundColor('#188C4A').onClick(() => { this.connect(); });
      if (this.managePacks) {
        Text(this.deviceId ? '已安装 ' + this.books.length + ' 本绘本' : '上次读取的绘本清单')
          .fontFamily(FONT_STRONG).fontColor('#152044');
        ForEach(this.books, (item: ReaderBook) => {
          Text(item.title + (item.version ? ' · ' + item.version : '')).fontFamily(FONT_BODY)
            .width('100%').padding(12).backgroundColor('#FFFFFF').borderRadius(14);
        }, (item: ReaderBook) => item.bookId + ':' + (item.version ?? ''));
        if (this.books.length === 0) {
          Text('暂时没有绘本包').fontFamily(FONT_BODY).fontColor('#53665E');
        }
        Button('从文件导入绘本包').fontFamily(FONT_STRONG).width('100%')
          .enabled(!this.busy && this.deviceId.length > 0).backgroundColor('#188C4A')
          .onClick(() => { this.importPack(); });
      } else if (this.deviceId) {
        if (this.books.length > 0) {
          Select(this.bookOptions()).selected(this.book).value(this.books[this.book].title)
            .enabled(!this.busy).onSelect((index: number) => { this.book = index; });
          Button('扫描绘本标签').fontFamily(FONT_STRONG).width('100%')
            .enabled(!this.busy).backgroundColor('#188C4A').onClick(() => { this.scan(); });
          if (this.tag) {
            Text('标签 ' + this.tag).fontFamily(FONT_BODY).fontColor('#53665E');
            Button('绑定到《' + this.books[this.book].title + '》').fontFamily(FONT_STRONG).width('100%')
              .enabled(!this.busy).backgroundColor('#188C4A').onClick(() => { this.bind(); });
          }
        } else {
          Text('阅读器还没有可配对的绘本').fontFamily(FONT_BODY).fontColor('#53665E');
        }
        Button('同步阅读记录').fontFamily(FONT_STRONG).width('100%')
          .enabled(!this.busy).backgroundColor('#188C4A').onClick(() => { this.sync(); });
      }
      if (this.message) {
        Text(this.message).fontFamily(FONT_BODY).fontSize(15).fontColor('#53665E');
      }
      if (!this.managePacks) {
        Text('本机阅读记录').fontFamily(FONT_TITLE).fontSize(20).fontColor('#152044');
        if (this.sessions.length === 0) {
          Text('还没有从阅读器同步的记录').fontFamily(FONT_BODY).fontColor('#53665E');
        }
        ForEach(this.sessions, (session: ReadingSession) => {
          Column({ space: 5 }) {
            Text(this.bookTitle(session.bookId)).fontFamily(FONT_STRONG).fontSize(17).fontColor('#152044');
            Text(Math.round(session.durationMs / 1000) + ' 秒 · 末段 ' + session.lastSegmentId)
              .fontFamily(FONT_BODY).fontColor('#53665E');
            Text(this.sessionDate(session)).fontFamily(FONT_BODY).fontSize(13).fontColor('#53665E');
            if (session.positionUncertain === true) {
              Text('结束时的位置仍需确认').fontFamily(FONT_BODY).fontSize(13).fontColor('#815828');
            }
            if (session.trajectory && session.trajectory.length > 0) {
              this.Trajectory(session, session.trajectory);
            }
            if (session.answers && session.answers.length > 0) {
              this.Evidence(session, session.answers);
            }
          }.width('100%').alignItems(HorizontalAlign.Start).padding(12).backgroundColor('#FFFFFF').borderRadius(14);
        }, (session: ReadingSession) => session.sessionId);
      }
    }.width('100%').alignItems(HorizontalAlign.Start);
  }
}
