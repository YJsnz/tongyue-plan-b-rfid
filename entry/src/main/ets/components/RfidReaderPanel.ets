import { picker } from '@kit.CoreFileKit';
import { media } from '@kit.MediaKit';
import { FONT_BODY, FONT_STRONG, FONT_TITLE } from '../theme/Typography';
import { AnswerEvidence, emptyReaderCache, readReaderCache, readReaderToken, ReaderBook, ReaderCache, readerAddress, ReaderHistory,
  ReadingSession, readStoryPack, RfidReader, saveReaderCache, saveReaderToken, PositionTrace, PositionReview,
  updatePositionReview, ReaderQuestionResult, ReaderReadingState } from '../services/RfidReader';
import { confirmedEndTime } from '../services/ReadingStats';

@Component
export struct RfidReaderPanel {
  onCacheChanged: (cache: ReaderCache) => void = (_: ReaderCache): void => {};
  onExperienceBack: () => void = (): void => {};
  onOpenDevice: () => void = (): void => {};
  onReadingStarted: (reading: ReaderReadingState) => void = (_: ReaderReadingState): void => {};
  @Prop managePacks: boolean = false;
  @Prop readingExperience: boolean = false;
  @Prop experienceTitle: string = '小小探险家';
  @Prop experienceCover: number = 0;
  @State address: string = '';
  @State pairingToken: string = '';
  @State caCertificate: string = '';
  @State scanId: string = '';
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
  @State simulatorAvailable: boolean = false;
  @State simTagUid: string = '0004A12B';
  @State simTapBusy: boolean = false;
  @State reading: ReaderReadingState = { active: false };
  @State question: string = '';
  @State answer: ReaderQuestionResult = { status: 'idle' };
  @State showQuestionControls: boolean = false;
  @State audioActive: boolean = false;
  @State audioMessage: string = '';
  private cachePath: string = '';
  private cache: ReaderCache = emptyReaderCache();
  private reader?: RfidReader;
  private player?: media.AVPlayer;
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
    this.releaseAudio();
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
    this.releaseAudio();
    this.reader = undefined;
    this.deviceId = '';
    this.tag = '';
    this.scanId = '';
    this.simulatorAvailable = false;
    this.reading = { active: false };
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
      const isSimulator = info.firmwareVersion === 'pc-simulator';
      let currentReading: ReaderReadingState = { active: false };
      if (isSimulator) { currentReading = await reader.readingState(); }
      if (token && !reuseToken) { await saveReaderToken(info.deviceId, token); }
      if (!this.visible) { return; }
      this.save({ address: address, currentDevice: info.deviceId, histories: this.cache.histories,
        caCertificate: caCertificate, books: books, positionReviews: this.cache.positionReviews });
      this.reader = reader;
      this.deviceId = info.deviceId;
      this.simulatorAvailable = isSimulator;
      this.reading = currentReading;
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
    const selectedBook = this.books[this.book];
    if (!reader || !selectedBook || this.busy) { return; }
    this.busy = true;
    this.tag = '';
    this.scanId = '';
    this.message = this.simulatorAvailable ? '正在读取本地演示标签…' : '请将绘本标签贴近阅读器';
    try {
      const id = await reader.startScan();
      this.scanId = id;
      if (this.simulatorAvailable) {
        const status = await reader.simulateTap(this.simTagUid);
        if (status !== 'scanned') { throw new Error('本地模拟标签未进入扫描状态，请重试'); }
      }
      const deadline = Date.now() + 35000;
      while (this.visible && Date.now() < deadline) {
        const result = await reader.checkScan(id);
        if (!this.visible) { return; }
        if (result.status === 'detected' && typeof result.tagUid === 'string' && result.tagUid) {
          this.scanId = id;
          this.tag = result.tagUid;
          this.simTagUid = result.tagUid;
          if (this.simulatorAvailable) {
            await reader.bind(result.tagUid, id, selectedBook.bookId);
            this.tag = '';
            this.scanId = '';
            const status = await reader.simulateTap(result.tagUid);
            if (status !== 'started' && status !== 'alreadyReading') {
              throw new Error('已读到标签，但无法启动对应绘本');
            }
            const currentReading = await reader.readingState();
            if (!currentReading.active) { throw new Error('阅读器没有返回活动绘本，请重试'); }
            this.reading = currentReading;
            this.message = '已识别绘本，正在打开阅读页面';
            this.onReadingStarted(currentReading);
            return;
          }
          this.message = '已读到标签，请确认要绑定的绘本';
          return;
        }
        if (result.status === 'expired') { break; }
        await new Promise<void>((resolve: () => void) => { setTimeout(resolve, 700); });
      }
      if (this.visible) { this.message = '扫描已超时，请重新扫描'; }
      this.scanId = '';
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
      this.simTagUid = this.tag;
      this.tag = '';
      this.scanId = '';
    } catch (error) {
      if (this.visible) { this.message = (error as Error).message || '绑定失败，请重试'; }
    } finally {
      if (this.visible) { this.busy = false; }
    }
  }

  private async simulateReadingTap(): Promise<void> {
    const reader = this.reader;
    const uid = this.simTagUid.trim().toUpperCase();
    if (!reader || !this.simulatorAvailable || this.busy || this.simTapBusy ||
      !/^[0-9A-F]{4,32}$/.test(uid)) { return; }
    this.simTapBusy = true;
    this.message = '正在向电脑阅读器呈送模拟标签…';
    try {
      const status = await reader.simulateTap(uid);
      if (status === 'started' || status === 'alreadyReading') {
        this.reading = await reader.readingState();
        if (status === 'started') { this.showQuestionControls = false; }
        this.answer = { status: 'idle' };
        this.message = this.reading.active ? '已连接到《' + (this.reading.bookTitle ?? '') + '》的当前阅读' :
          '模拟标签已呈送，但当前没有活动阅读';
      } else if (status === 'unbound') {
        this.message = '这个 UID 尚未绑定绘本。请先到设备中心扫描并绑定绘本标签。';
      } else {
        this.message = '标签已被当前配对扫描接收，请先完成扫描和绑定';
      }
    } catch (error) {
      if (this.visible) { this.message = (error as Error).message || '无法开始模拟阅读'; }
    } finally {
      if (this.visible) { this.simTapBusy = false; }
    }
  }

  private async moveSegment(delta: number): Promise<void> {
    const reader = this.reader;
    const state = this.reading;
    if (!reader || !state.active || this.busy || !state.segmentIds) { return; }
    const index = (state.segmentNumber ?? 1) - 1 + delta;
    if (index < 0 || index >= state.segmentIds.length) { return; }
    this.busy = true;
    this.answer = { status: 'idle' };
    await this.releaseAudio();
    try {
      this.reading = await reader.selectSegment(state.segmentIds[index]);
      this.message = '已切换到第 ' + this.reading.segmentNumber + ' 段';
    } catch (error) {
      if (this.visible) { this.message = (error as Error).message || '切换段落失败'; }
    } finally {
      if (this.visible) { this.busy = false; }
    }
  }

  private async submitQuestion(): Promise<void> {
    const reader = this.reader;
    const text = this.question.trim();
    if (!reader || !this.reading.active || this.busy || !text) { return; }
    this.busy = true;
    await this.releaseAudio();
    try {
      this.answer = await reader.askQuestion(text);
      if (this.answer.status === 'answered') {
        this.message = this.answer.speechUnavailable ? '已回答并保留原文证据；本机语音合成暂不可用' :
          '回答已关联当前段落，可播放答案音频';
      } else if (this.answer.status === 'needsPositionConfirmation') {
        this.message = '当前位置不确定，请先手动确认段落后再提问';
      } else {
        this.message = '当前绘本没有足够原文证据回答这个问题';
      }
    } catch (error) {
      if (this.visible) { this.message = (error as Error).message || '提问失败，请重试'; }
    } finally {
      if (this.visible) { this.busy = false; }
    }
  }

  private async startExperienceReading(): Promise<void> {
    if (!this.simulatorAvailable) {
      this.message = '请先在设备中心连接电脑阅读器，再回到这里开始共读';
      this.onOpenDevice();
      return;
    }
    await this.simulateReadingTap();
  }

  private async toggleExperienceAudio(): Promise<void> {
    if (this.audioActive) {
      await this.releaseAudio();
      this.audioMessage = '已停止正文播放';
      return;
    }
    if (!this.reading.active) { await this.startExperienceReading(); }
    if (this.reading.active) { await this.playStoryAudio(); }
  }

  private async openExperienceQuestion(): Promise<void> {
    if (!this.reading.active) {
      await this.startExperienceReading();
    }
    if (this.reading.active) { this.showQuestionControls = true; }
  }

  private async playAudio(path: string, label: string): Promise<void> {
    const reader = this.reader;
    if (!reader) { return; }
    await this.releaseAudio();
    try {
      const player = await media.createAVPlayer();
      this.player = player;
      player.on('stateChange', (state: string) => {
        this.audioActive = state === 'playing';
        if (state === 'completed') { this.audioMessage = label + '播放完成'; }
        if (state === 'error') { this.audioMessage = label + '播放失败'; }
      });
      player.url = reader.audioUrl(path);
      this.audioMessage = '正在准备' + label + '…';
      await player.prepare();
      await player.play();
      this.audioMessage = '正在播放' + label;
    } catch (error) {
      await this.releaseAudio();
      this.audioMessage = (error as Error).message || label + '播放失败';
    }
  }

  private async playStoryAudio(): Promise<void> {
    await this.playAudio('/sim/audio/current', '正文音频');
  }

  private async playAnswerAudio(): Promise<void> {
    const path = this.answer.audioUrl;
    if (path) { await this.playAudio(path, '答案音频'); }
  }

  private async releaseAudio(): Promise<void> {
    const player = this.player;
    this.player = undefined;
    this.audioActive = false;
    if (player) {
      try { await player.release(); } catch (_) { }
    }
  }

  private async finishReading(): Promise<void> {
    const reader = this.reader;
    if (!reader || !this.reading.active || this.busy) { return; }
    const segmentId = this.reading.segmentId ?? '';
    this.busy = true;
    await this.releaseAudio();
    try {
      await reader.finishReading(segmentId);
      this.reading = { active: false };
      this.answer = { status: 'idle' };
      this.showQuestionControls = false;
      this.message = '本次阅读已结束，正在同步问答证据和阅读记录…';
    } catch (error) {
      if (this.visible) { this.message = (error as Error).message || '结束阅读失败'; }
    } finally {
      if (this.visible) { this.busy = false; }
    }
    if (!this.reading.active && this.visible) { await this.sync(); }
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
      try {
        const reviews = await reader.syncPositionReviews(next, this.cache.positionReviews ?? []);
        if (!this.visible) { return; }
        this.save({ address: this.cache.address, currentDevice: deviceId, histories: histories,
          caCertificate: this.cache.caCertificate, books: this.books, positionReviews: reviews });
        this.message = '阅读记录与位置复核已同步';
      } catch (error) {
        if (this.visible) {
          this.message = '阅读记录已同步；位置复核同步失败：' + ((error as Error).message || '请重试');
        }
      }
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
      this.message = '位置复核已保存在本机；同步阅读记录后可供其他家长端查看';
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
            Text('复核先保存在本机。点击“同步阅读记录”后会同步到当前阅读器，供其他已配对家长端查看。')
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

  @Builder
  ReadingControls() {
    if (this.reading.active) {
      Column({ space: 10 }) {
        Text(this.reading.bookTitle ?? this.reading.bookId ?? '正在阅读')
          .fontFamily(FONT_STRONG).fontSize(18).fontColor('#152044');
        Text('第 ' + (this.reading.segmentNumber ?? 0) + ' / ' + (this.reading.segmentCount ?? 0) +
          ' 段 · ' + (this.reading.segmentId ?? ''))
          .fontFamily(FONT_BODY).fontSize(13).fontColor('#53665E');
        Text(this.reading.text ?? '此阅读器未提供段落文字')
          .fontFamily(FONT_BODY).fontSize(16).fontColor('#152044').lineHeight(25);
        if (this.reading.positionUncertain === true) {
          Text('阅读位置待确认。请手动选择段落后再提问。')
            .fontFamily(FONT_BODY).fontSize(13).fontColor('#815828');
        }
        Row({ space: 8 }) {
          Button('上一段').fontFamily(FONT_STRONG).layoutWeight(1).backgroundColor('#E8F6EC').fontColor('#188C4A')
            .enabled(!this.busy && (this.reading.segmentNumber ?? 1) > 1)
            .onClick(() => { this.moveSegment(-1); });
          Button(this.audioActive ? '正文播放中' : '播放正文').fontFamily(FONT_STRONG).layoutWeight(1)
            .backgroundColor('#188C4A').onClick(() => { this.playStoryAudio(); });
          Button('下一段').fontFamily(FONT_STRONG).layoutWeight(1).backgroundColor('#E8F6EC').fontColor('#188C4A')
            .enabled(!this.busy && (this.reading.segmentNumber ?? 0) < (this.reading.segmentCount ?? 0))
            .onClick(() => { this.moveSegment(1); });
        }
        if (!this.readingExperience || this.showQuestionControls || this.answer.status === 'answered') {
          TextInput({ placeholder: '就这一段内容提问，例如：小熊带了什么？', text: this.question })
            .fontFamily(FONT_BODY).maxLength(120).enabled(!this.busy)
            .onChange((value: string) => { this.question = value; });
          Button(this.busy ? '正在处理…' : '提问并查找原文证据').fontFamily(FONT_STRONG).width('100%')
            .enabled(!this.busy && this.question.trim().length > 0).backgroundColor('#E8F6EC').fontColor('#188C4A')
            .onClick(() => { this.submitQuestion(); });
          if (this.answer.status === 'answered') {
            Column({ space: 5 }) {
              Text('回答' + (this.answer.provider === 'deepseek' ? ' · DeepSeek 联网' : ' · 本地问答'))
                .fontFamily(FONT_STRONG).fontSize(14).fontColor('#188C4A');
              Text(this.answer.answer ?? '').fontFamily(FONT_BODY).fontSize(16).fontColor('#152044');
              Text('原文证据：「' + (this.answer.evidence ?? '') + '」')
                .fontFamily(FONT_BODY).fontSize(14).fontColor('#53665E');
              if (this.answer.audioUrl) {
                Button(this.audioActive ? '答案播放中' : '播放答案音频').fontFamily(FONT_STRONG)
                  .backgroundColor('#FFF1DF').fontColor('#815828').onClick(() => { this.playAnswerAudio(); });
              }
            }.width('100%').alignItems(HorizontalAlign.Start).padding(10).backgroundColor('#F3F8F3').borderRadius(10);
          }
        }
        if (this.audioMessage) {
          Text(this.audioMessage).fontFamily(FONT_BODY).fontSize(12).fontColor('#53665E');
        }
        if (this.audioActive) {
          Button('停止播放').fontFamily(FONT_BODY).backgroundColor('#FFFFFF').fontColor('#53665E')
            .onClick(() => { this.releaseAudio(); });
        }
        Button(this.busy ? '正在结束…' : '结束阅读并同步记录').fontFamily(FONT_STRONG).width('100%')
          .enabled(!this.busy).backgroundColor('#F2F3EF').fontColor('#53665E')
          .onClick(() => { this.finishReading(); });
      }.width('100%').alignItems(HorizontalAlign.Start).padding(12).backgroundColor('#FFFFFF').borderRadius(14);
    }
  }

  @Builder
  ReadingExperience() {
    Column({ space: 12 }) {
      Row({ space: 9 }) {
        Text('‹ 返回').fontFamily(FONT_STRONG).fontSize(16).fontColor('#188C4A')
          .onClick(() => { this.onExperienceBack(); });
        Blank();
        Button(this.audioActive ? 'Ⅱ' : '▶').width(38).height(38).fontSize(16).fontColor('#188C4A')
          .backgroundColor('#E8F6EC').borderRadius(19).enabled(!this.busy)
          .onClick(() => { this.toggleExperienceAudio(); });
        Button('问').width(38).height(38).fontFamily(FONT_STRONG).fontSize(15).fontColor('#188C4A')
          .backgroundColor('#E8F6EC').borderRadius(19).enabled(!this.busy)
          .onClick(() => { this.openExperienceQuestion(); });
      }.width('100%').height(40);
      if (this.simulatorAvailable) {
        Text('电脑阅读器已连接 · 本地演示').fontFamily(FONT_BODY).fontSize(12).fontColor('#538167');
      } else {
        Text(this.busy ? '正在连接阅读器…' : '连接电脑阅读器后，可从这里操作共读')
          .fontFamily(FONT_BODY).fontSize(12).fontColor('#7C8B9D');
      }
      if (this.experienceCover === 1) {
        Image($r('app.media.page_covers_moon')).width('100%').height(250).objectFit(ImageFit.Cover).borderRadius(19);
      } else if (this.experienceCover === 2) {
        Image($r('app.media.page_covers_seed')).width('100%').height(250).objectFit(ImageFit.Cover).borderRadius(19);
      } else {
        Image($r('app.media.page_covers_forest')).width('100%').height(250).objectFit(ImageFit.Cover).borderRadius(19);
      }
      Text(this.reading.active ? (this.reading.bookTitle ?? this.experienceTitle) : this.experienceTitle)
        .fontFamily(FONT_TITLE).fontSize(27).fontColor('#152044').width('100%');
      Text('3–6 岁  ·  约 15 分钟  ·  亲子共读')
        .fontFamily(FONT_BODY).fontSize(14).fontColor('#7C8B9D').width('100%');
      Text('在故事里认识新朋友，也听听孩子心里的小世界。读完后，可以聊聊：“你最喜欢谁？为什么？”')
        .fontFamily(FONT_BODY).fontSize(16).fontColor('#52655D').lineHeight(27).width('100%');
      Button(this.simTapBusy ? '正在连接阅读…' : (this.reading.active ? '继续和孩子一起读  ▶' : '和孩子一起读  ▶'))
        .fontFamily(FONT_STRONG).fontSize(17).width('100%').height(52).fontColor('#102E22')
        .backgroundColor('#20C85B').borderRadius(16).enabled(!this.busy && !this.simTapBusy)
        .onClick(() => { this.startExperienceReading(); });
      if (this.reading.active) { this.ReadingControls(); }
      if (this.audioMessage) {
        Text(this.audioMessage).fontFamily(FONT_BODY).fontSize(13).fontColor('#53665E');
      }
      if (this.message) {
        Text(this.message).fontFamily(FONT_BODY).fontSize(14).fontColor('#53665E').width('100%');
      }
      if (!this.simulatorAvailable) {
        Button('前往设备中心连接阅读器').fontFamily(FONT_BODY).fontSize(13).fontColor('#188C4A')
          .backgroundColor('#FFFFFF').width('100%').onClick(() => { this.onOpenDevice(); });
      }
    }.width('100%').padding({ left: 20, right: 20, top: 10, bottom: 22 })
      .alignItems(HorizontalAlign.Start).backgroundColor('#FFFDF7');
  }

  @Builder
  DevicePanel() {
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

  build() {
    if (this.readingExperience) {
      this.ReadingExperience();
    } else {
      this.DevicePanel();
    }
  }
}
