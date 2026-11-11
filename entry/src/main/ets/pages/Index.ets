import { ReferenceCompanion } from '../components/ReferenceCompanion';
import { HomeWelcomeMascot } from '../components/HomeWelcomeMascot';
import { ReadingRadar } from '../components/ReadingRadar';
import { RfidReaderPanel } from '../components/RfidReaderPanel';
import { LengthMetrics } from '@kit.ArkUI';
import { FONT_BODY, FONT_STRONG, FONT_TITLE } from '../theme/Typography';
import { emptyReaderCache, readReaderCache, ReaderCache } from '../services/RfidReader';
import { formatReadingDuration, ReadingSummary, ReadingWeek, summarizeReading } from '../services/ReadingStats';

const INK: string = '#152044';
const LIGHT: string = '#FFFFFF';
const LIME: string = '#21C55D';
const PAGE: string = '#FFFDF7';
const MUTED: string = '#7C8993';

@Entry
@Component
struct Index {
  @State tab: number = 0;
  @State view: string = '';
  @State selected: number = 0;
  @State page: number = 8;
  @State readingSummary: ReadingSummary = summarizeReading(emptyReaderCache(), Date.now());
  @State filter: string = '推荐';
  @State query: string = '';
  @State question: string = '为什么天空是蓝色的？';
  @State answered: boolean = true;
  @State searchOpen: boolean = false;
  @State libraryAge: number = -1;
  @State growthPeriod: string = '本周';
  @State growthSection: string = '阅读数据';
  @State aiDraft: string = '';
  @State aiVoiceHint: boolean = false;
  @State focusMode: boolean = false;
  @State review: string = '';
  @State reducedMotion: boolean = false;
  @State child: string = '小雨';
  @State deviceVolume: number = 65;
  @State dailyMinutes: number = 60;
  @State libraryCardsVisible: boolean = false;
  @State pageContentOpacity: number = 1;
  @State pageContentOffset: number = 0;
  private pageScroller: Scroller = new Scroller();
  private libraryEntranceTimer: number = -1;
  private aiResponseTimer: number = -1;
  private aiThinkingGeneration: number = 0;
  private pageTransitionGeneration: number = 0;
  private titles: string[] = ['小小探险家', '月亮的礼物', '神奇的种子'];

  aboutToAppear(): void {
    const fonts = this.getUIContext().getFont();
    fonts.registerFont({ familyName: FONT_BODY, familySrc: $rawfile('fonts/ResourceHanRoundedCN-Regular.ttf') });
    fonts.registerFont({ familyName: FONT_STRONG, familySrc: $rawfile('fonts/ResourceHanRoundedCN-Bold.ttf') });
    fonts.registerFont({ familyName: FONT_TITLE, familySrc: $rawfile('fonts/ResourceHanRoundedCN-Heavy.ttf') });
    this.loadReadingSummary();
  }

  onPageShow(): void {
    this.loadReadingSummary();
  }

  private loadReadingSummary(): void {
    const context = this.getUIContext().getHostContext();
    if (!context) { return; }
    try {
      this.updateReadingSummary(readReaderCache(context.filesDir + '/rfid-reader.json'));
    } catch (_) {
      // Keep the last successful summary; the reader panel reports storage errors.
    }
  }

  private updateReadingSummary(cache: ReaderCache): void {
    this.readingSummary = summarizeReading(cache, Date.now());
  }

  private selectedWeek(): ReadingWeek {
    return this.growthPeriod === '本周' ? this.readingSummary.currentWeek : this.readingSummary.previousWeek;
  }

  aboutToDisappear(): void {
    clearTimeout(this.libraryEntranceTimer);
    clearTimeout(this.aiResponseTimer);
    this.aiThinkingGeneration++;
    this.pageTransitionGeneration++;
  }

  private transitionPage(change: () => void): void {
    const generation = ++this.pageTransitionGeneration;
    if (this.reducedMotion) {
      change();
      this.pageContentOpacity = 1;
      this.pageContentOffset = 0;
      return;
    }
    this.getUIContext().animateTo({
      duration: 130,
      curve: Curve.EaseIn,
      onFinish: () => {
        if (generation !== this.pageTransitionGeneration) { return; }
        change();
        this.pageContentOffset = 12;
        this.pageContentOpacity = 0;
        this.getUIContext().animateTo({
          duration: 240,
          curve: Curve.EaseOut,
          onFinish: () => {
            if (generation !== this.pageTransitionGeneration) { return; }
            this.pageContentOffset = 0;
          }
        }, () => { this.pageContentOpacity = 1; this.pageContentOffset = 0; });
      }
    }, () => { this.pageContentOpacity = 0; this.pageContentOffset = -8; });
  }

  private switchTab(tab: number): void {
    if (tab === 0 || tab === 2) { this.loadReadingSummary(); }
    if (this.tab === tab && this.view.length === 0) {
      this.pageScroller.scrollTo({ xOffset: 0, yOffset: 0 });
      return;
    }
    this.transitionPage(() => {
      this.tab = tab;
      this.view = '';
      this.pageScroller.scrollTo({ xOffset: 0, yOffset: 0 });
    });
  }

  private setView(view: string): void {
    if (this.view === view) { return; }
    this.transitionPage(() => {
      this.view = view;
      this.pageScroller.scrollTo({ xOffset: 0, yOffset: 0 });
    });
  }

  private pageColor(): string {
    return PAGE;
  }

  private matchingBooks(): number[] {
    let ids: number[] = this.filter === '热门' ? [1, 0, 2] : [0, 1, 2];
    if (this.filter === '经典') { ids = [0, 1]; }
    if (this.libraryAge >= 0) { ids = this.libraryAge === 0 ? [0] : (this.libraryAge === 3 ? [1, 2] : [0, 1, 2]); }
    return ids.filter((id: number) => this.titles[id].includes(this.query));
  }

  private ask(question: string): void {
    if (question.trim().length === 0) { return; }
    clearTimeout(this.aiResponseTimer);
    this.aiThinkingGeneration++;
    const generation = this.aiThinkingGeneration;
    this.question = question.trim();
    this.answered = false;
    this.aiDraft = '';
    this.aiVoiceHint = false;
    this.aiResponseTimer = setTimeout(() => {
      if (generation !== this.aiThinkingGeneration) { return; }
      this.aiThinkingGeneration++;
      this.answered = true;
      }, this.reducedMotion ? 700 : 2600);
  }

  private replayLibraryEntrance(): void {
    clearTimeout(this.libraryEntranceTimer);
    this.libraryCardsVisible = false;
    if (this.reducedMotion) {
      this.libraryCardsVisible = true;
      return;
    }
    this.libraryEntranceTimer = setTimeout(() => { this.libraryCardsVisible = true; }, 50);
  }

  private answerText(): string {
    if (this.question.includes('大海')) { return '大海的颜色和阳光、水的深浅都有关系。水会吸收不同颜色的光，深一些的海水常呈蓝色；浅海也可能因为海底和水中物质呈现绿色或其他颜色。'; }
    if (this.question.includes('天空') || this.question.includes('蓝色')) { return '这是一个很棒的问题！阳光看起来是白色的，其实包含许多颜色。经过空气时，蓝色的光更容易向四周散开，所以我们抬头时，常常看到蓝色的天空。'; }
    if (this.question.includes('下雨')) { return '云里有许多小水滴。它们聚在一起，变得越来越大、越来越重，最后就从云里落下来，变成了雨。下次下雨时，我们一起观察雨滴吧！'; }
    if (this.question.includes('星星')) { return '许多星星像太阳一样，会自己发光。它们离我们很远，看起来就像夜空里的小光点。我们可以在晴朗的夜晚一起找找它们。'; }
    if (this.question.includes('种子')) { return '植物会用阳光、水和空气制造生长需要的养分。可以和孩子一起观察：窗边的小苗和阴影里的小苗，有什么不同？'; }
    if (this.question.includes('鲸鱼')) { return '鲸鱼也需要休息，但它们必须到水面呼吸。不同鲸类的休息方式并不完全一样。我们可以在共读后一起找找更多鲸鱼资料。'; }
    return '这个问题很有意思！我们可以先在故事里找找线索，再和家长一起查一查。你觉得可能是什么原因呢？';
  }

  private go(target: string): void {
    if (target === 'back') { this.setView(''); }
    else if (target === 'read') { this.page = 8; this.setView('read'); }
    else if (target === 'finish') {
      this.transitionPage(() => {
        this.view = 'finish';
        this.pageScroller.scrollTo({ xOffset: 0, yOffset: 0 });
      });
    }
    else {
      this.setView(target);
    }
  }

  @Builder
  Action(label: string, color: string, target: string) {
    Button(label).width('100%').height(52).fontSize(18).fontFamily(FONT_BODY).fontWeight(FontWeight.Bold).fontFamily(FONT_STRONG).fontColor(INK)
      .backgroundColor(color).borderRadius(17).shadow({ radius: 7, color: '#173E3918', offsetY: 3 })
      .onClick(() => { this.go(target); });
  }

  @Builder
  Title(title: string, caption: string) {
    Column({ space: 6 }) {
      Row() {
        Column({ space: 2 }) {
          Text(title).fontSize(28).fontFamily(FONT_BODY).fontWeight(FontWeight.Bold).fontFamily(FONT_TITLE).fontColor(INK);
          Text(caption).fontSize(15).fontFamily(FONT_BODY).fontColor(MUTED);
        }.alignItems(HorizontalAlign.Start).layoutWeight(1);
        Row({ space: 6 }) {
          Text(this.child === '小雨' ? '🌼' : '⭐').fontSize(18).fontFamily(FONT_BODY);
          Text(this.child + '  ⌄').fontSize(15).fontFamily(FONT_BODY).fontWeight(FontWeight.Bold).fontFamily(FONT_STRONG).fontColor(INK);
        }.padding({ left: 10, right: 10, top: 8, bottom: 8 }).backgroundColor('#F2F5EE').borderRadius(18)
          .onClick(() => { this.setView('profile'); });
      }.width('100%');
    }.padding({ top: 10, bottom: 4 }).width('100%');
  }

  @Builder
  Cover(id: number, height: number) {
    if (id === 0) { Image($r('app.media.page_covers_forest')).width('100%').height(height).objectFit(ImageFit.Cover).borderRadius(19); }
    else if (id === 1) { Image($r('app.media.page_covers_moon')).width('100%').height(height).objectFit(ImageFit.Cover).borderRadius(19); }
    else { Image($r('app.media.page_covers_seed')).width('100%').height(height).objectFit(ImageFit.Cover).borderRadius(19); }
  }

  @Builder
  Home() {
    Column({ space: 12 }) {
      Stack() {
        Image($r('app.media.home_garden')).width('100%').height(260).objectFit(ImageFit.Fill);
        Row() {
          Blank().layoutWeight(1);
          HomeWelcomeMascot({ reduced: this.reducedMotion, canvasSize: 250 });
        }.width('100%').position({ x: 8, y: 38 });
        Column({ space: 2 }) {
          Text('早上好，').fontSize(25).fontFamily(FONT_BODY).fontWeight(FontWeight.Bolder).fontFamily(FONT_TITLE).fontColor('#142744');
          Text(this.child + '！').fontSize(25).fontFamily(FONT_BODY).fontWeight(FontWeight.Bolder).fontFamily(FONT_TITLE).fontColor('#142744');
          Text('今天也一起读个\n好故事吧！').fontSize(15).fontFamily(FONT_BODY).fontWeight(FontWeight.Bold).fontFamily(FONT_STRONG).fontColor('#23354C').lineHeight(21);
        }.width('48%').alignItems(HorizontalAlign.Start).position({ x: 8, y: 12 });
        Row() {
          Blank().layoutWeight(1);
          Text(this.readingSummary.streakDays > 0 ? '🌞 连读 ' + this.readingSummary.streakDays + ' 天' : '🌞 共读时光')
            .fontSize(13).fontFamily(FONT_BODY).fontWeight(FontWeight.Bold).fontFamily(FONT_STRONG).fontColor('#A66C21')
            .padding({ left: 8, right: 8, top: 5, bottom: 5 }).backgroundColor('#FFF0B9').borderRadius(13);
        }.width('100%').padding({ right: 7 }).position({ x: 0, y: 12 });
      }.height(260).width('100%').clip(true);
      Column({ space: 8 }) {
        Row({ space: 9 }) {
          Image($r('app.media.nav_book')).width(34).height(34).padding(5).backgroundColor('#DBFADF').borderRadius(10);
          Column({ space: 7 }) {
            Text('今日阅读任务').fontSize(15).fontFamily(FONT_BODY).fontWeight(FontWeight.Bold).fontFamily(FONT_STRONG).fontColor(INK);
            Progress({ value: Math.min(this.readingSummary.todayCount, 3), total: 3, type: ProgressType.Linear })
              .width('100%').height(7).color('#43D52B').backgroundColor('#E5F4D8');
          }.layoutWeight(1).alignItems(HorizontalAlign.Start);
          Text(Math.min(this.readingSummary.todayCount, 3) + '/3').fontSize(16).fontFamily(FONT_BODY).fontWeight(FontWeight.Bold)
            .fontFamily(FONT_STRONG).fontColor(INK);
          Text('›').fontSize(28).fontFamily('HarmonyOS Sans').fontColor('#16BC4C').width(30).height(30).textAlign(TextAlign.Center)
            .backgroundColor('#DDFFE1').borderRadius(15);
        }.width('100%');
        Text(this.readingSummary.todayCount >= 3 ? '今天的共读任务已完成！' : '共读后同步阅读器，更新今日进度。')
          .fontSize(14).fontFamily(FONT_BODY).fontColor('#7E8A9B');
      }.padding(14).width('100%').backgroundColor('#FFFFFF').borderRadius(22).margin({ top: -23 })
        .shadow({ radius: 12, color: '#64984715', offsetY: 4 }).zIndex(2)
        .onClick(() => { this.switchTab(2); });
      Row({ space: 7 }) {
        this.QuickAction('♥', '每日推荐', '#FF7C8A', 'read');
        this.QuickAction('▣', '我的书架', '#38C4F1', 'library');
        this.QuickAction('✓', '阅读打卡', '#FFCA26', 'growth');
        this.QuickAction('☺', 'AI互动', '#38CDEA', 'ai');
      }.width('100%');
      if (this.readingSummary.latestTitle) {
        Column({ space: 5 }) {
          Text('最近阅读器记录').fontFamily(FONT_STRONG).fontSize(15).fontColor('#538167');
          Text(this.readingSummary.latestTitle).fontFamily(FONT_TITLE).fontSize(20).fontColor(INK);
          Text('末段 ' + this.readingSummary.latestSegment + ' · ' + formatReadingDuration(this.readingSummary.latestDurationMs))
            .fontFamily(FONT_BODY).fontSize(14).fontColor(MUTED);
        }.width('100%').padding(14).alignItems(HorizontalAlign.Start).backgroundColor(LIGHT).borderRadius(18)
          .onClick(() => { this.setView('pair'); });
      }
      Stack() {
        Image($r('app.media.home_reading_banner')).width('100%').height(112).objectFit(ImageFit.Cover);
        Row() {
          Blank().layoutWeight(1);
          Column({ space: 8 }) {
            Text('阅读\n让世界更美好').fontSize(20).fontFamily(FONT_BODY).lineHeight(27).fontWeight(FontWeight.Bolder).fontFamily(FONT_TITLE).fontColor('#126D3B');
            Text('▶').fontSize(16).fontFamily(FONT_BODY).fontColor('#36B945').width(25).height(25).textAlign(TextAlign.Center)
              .padding({ top: 4 }).backgroundColor('#FFFFFF').borderRadius(13);
          }.width('49%').alignItems(HorizontalAlign.Start);
        }.width('100%').padding({ left: 12, right: 8 });
      }.width('100%').height(112).borderRadius(18).clip(true).onClick(() => { this.selected = 0; this.go('read'); });
      Row() {
        Text('应用内共读').fontSize(20).fontFamily(FONT_BODY).fontWeight(FontWeight.Bold).fontFamily(FONT_STRONG).fontColor(INK);
        Blank();
        Text('第 ' + this.page + ' / 24 页').fontSize(14).fontFamily(FONT_BODY).fontColor(MUTED);
      }.width('100%');
      Row({ space: 12 }) {
        Image($r('app.media.page_covers_forest')).width(88).height(112).objectFit(ImageFit.Cover).borderRadius(15);
        Column({ space: 7 }) {
          Text('森林里的新朋友').fontSize(14).fontFamily(FONT_BODY).fontColor('#538167');
          Text('小小探险家').fontSize(20).fontFamily(FONT_BODY).fontWeight(FontWeight.Bold).fontFamily(FONT_STRONG).fontColor(INK);
          Text('一起读到第 ' + this.page + ' 页').fontSize(15).fontFamily(FONT_BODY).fontColor(MUTED);
          Progress({ value: this.page, total: 24, type: ProgressType.Linear }).width('100%').height(7).color(LIME).backgroundColor('#E6EFE5');
          Text('继续共读  →').fontSize(16).fontFamily(FONT_BODY).fontWeight(FontWeight.Bold).fontFamily(FONT_STRONG).fontColor('#169B52')
            .padding({ left: 15, right: 15, top: 8, bottom: 8 }).backgroundColor('#D9F5E1').borderRadius(13)
            .onClick(() => { this.selected = 0; this.go('read'); });
        }.alignItems(HorizontalAlign.Start).layoutWeight(1);
      }.padding(12).width('100%').backgroundColor(LIGHT).borderRadius(21)
        .shadow({ radius: 10, color: '#263C3210', offsetY: 3 })
        .onClick(() => { this.selected = 0; this.setView('book'); });
      Row() {
        Text('今天可以聊聊').fontSize(19).fontFamily(FONT_BODY).fontWeight(FontWeight.Bold).fontFamily(FONT_STRONG).fontColor(INK);
        Blank(); Text('家长小纸条  ›').fontSize(14).fontFamily(FONT_BODY).fontColor(MUTED);
      }.width('100%');
      Text('如果你是故事里的小熊，会怎样邀请新朋友一起玩？')
        .fontSize(16).fontFamily(FONT_BODY).fontColor('#536D5C').lineHeight(25).padding(14).width('100%').backgroundColor('#F0F6E7').borderRadius(18);
    }.padding({ left: 18, right: 18, bottom: 24 });
  }

  @Builder
  QuickAction(icon: string, label: string, color: string, target: string) {
    Column({ space: 6 }) {
      if (target === 'read') { Image($r('app.media.home_recommend')).width(44).height(44); }
      else if (target === 'library') { Image($r('app.media.home_books')).width(44).height(44); }
      else if (target === 'growth') { Image($r('app.media.home_checkin')).width(44).height(44); }
      else { Image($r('app.media.home_chat')).width(44).height(44); }
      Text(label).fontSize(13).fontFamily(FONT_BODY).fontWeight(FontWeight.Medium).fontColor('#50616A').maxLines(1);
    }.layoutWeight(1).padding({ top: 10, bottom: 9 }).backgroundColor(LIGHT).borderRadius(17)
      .onClick(() => {
        if (target === 'read') { this.selected = 0; this.go('read'); }
        else if (target === 'library') { this.switchTab(1); }
        else if (target === 'growth') { this.switchTab(2); }
        else { this.switchTab(3); }
      });
  }

  @Builder
  Node(icon: string, label: string, color: string, offset: number) {
    Column({ space: 10 }) {
      Text(icon).fontSize(28).fontFamily(FONT_BODY).fontWeight(FontWeight.Bold).fontFamily(FONT_TITLE).fontColor(INK).width(62).height(58)
        .textAlign(TextAlign.Center).backgroundColor(color).borderRadius(23)
        .shadow({ radius: 0, color: '#0A2E2A', offsetY: 4 }).onClick(() => { this.go('read'); });
      Text(label).fontSize(14).fontFamily(FONT_BODY).fontWeight(FontWeight.Bold).fontFamily(FONT_STRONG).fontColor(MUTED);
    }.layoutWeight(1).margin({ top: offset });
  }

  @Builder
  PageHeading(title: string, action: string, target: string) {
    Row() {
      Text(title).fontSize(27).fontFamily(FONT_TITLE).fontColor(INK).layoutWeight(1);
      if (action.length > 0) {
        Text(action).fontSize(14).fontFamily(FONT_BODY).fontColor('#6E7890')
          .padding({ left: 12, right: 12, top: 8, bottom: 8 }).backgroundColor(LIGHT).borderRadius(17)
          .onClick(() => {
            if (target === 'search') { this.searchOpen = !this.searchOpen; }
            else if (target === 'period') { this.growthPeriod = this.growthPeriod === '本周' ? '上周' : '本周'; }
            else { this.setView(target); }
          });
      }
    }.width('100%').padding({ top: 13, bottom: 5 });
  }

  @Builder
  SectionHeading(title: string, link: string, target: string) {
    Row() {
      Text(title).fontSize(18).fontFamily(FONT_STRONG).fontColor(INK);
      Blank();
      if (link.length > 0) {
        Text(link + ' ›').fontSize(13).fontFamily(FONT_BODY).fontColor(MUTED).onClick(() => {
          if (target === 'books') {
            this.libraryAge = -1;
            this.query = '';
            this.filter = '推荐';
            this.replayLibraryEntrance();
          }
          else if (target === 'themes') { this.filter = '主题'; }
          else if (target === 'growth') { this.switchTab(2); }
          else { this.setView(target); }
        });
      }
    }.width('100%').height(31);
  }

  @Builder
  Library() {
    Column({ space: 10 }) {
      this.PageHeading('绘本馆', '⌕', 'search');
      if (this.searchOpen) {
        TextInput({ placeholder: '找一本喜欢的绘本', text: this.query }).fontFamily(FONT_BODY).fontSize(16).height(48)
          .backgroundColor(LIGHT).fontColor(INK).placeholderColor('#9BA3B2').borderRadius(18)
          .onChange((value: string) => { this.query = value; });
      }
      Row({ space: 7 }) {
        ForEach(['推荐', '热门', '经典', '分级', '主题'], (name: string) => {
          Text(name).fontFamily(FONT_STRONG).fontSize(15).fontColor(this.filter === name ? LIGHT : '#7B8394')
            .layoutWeight(1).textAlign(TextAlign.Center).padding({ top: 7, bottom: 7 })
            .backgroundColor(this.filter === name ? '#14CA43' : '#F1F1EB').borderRadius(16)
            .onClick(() => {
              this.filter = name;
              this.libraryAge = -1;
              this.replayLibraryEntrance();
            });
        });
      }.width('100%');
      Stack() {
        Row() {
          Blank().layoutWeight(1);
          Stack() { ReferenceCompanion({ kind: 'reading', canvasSize: 148, reduced: this.reducedMotion }); }
            .width(162).height(140).translate({ x: 0, y: this.libraryCardsVisible ? 0 : 16 })
            .scale({ x: this.libraryCardsVisible ? 1 : 0.92, y: this.libraryCardsVisible ? 1 : 0.92 })
            .opacity(this.libraryCardsVisible ? 1 : 0)
            .animation({ duration: this.reducedMotion ? 0 : 480, curve: Curve.EaseOut });
        }.width('100%');
        Column({ space: 2 }) {
          Text('和好故事').fontFamily(FONT_TITLE).fontSize(24).fontColor(INK);
          Text('交个朋友').fontFamily(FONT_TITLE).fontSize(24).fontColor(INK);
          Text('一起发现阅读的快乐').fontFamily(FONT_BODY).fontSize(13).fontColor('#347176');
        }.alignItems(HorizontalAlign.Start).position({ x: 16, y: 20 });
      }.width('100%').height(132).backgroundColor('#DBF6E8').borderRadius(19).clip(true)
        .onClick(() => { this.selected = 0; this.setView('book'); });
      Row({ space: 8 }) {
        ForEach(['0–3岁', '3–6岁', '6–8岁', '8岁+'], (age: string, i: number) => {
          Column({ space: 4 }) {
            if (i === 1) { Image($r('app.media.home_chat')).width(28).height(28); }
            else { Text(['🐤', '', '★', '🎓'][i]).fontSize(28).fontColor('#FFCE23').height(28); }
            Text(age).fontFamily(FONT_BODY).fontSize(13).fontColor(INK);
          }.layoutWeight(1).padding({ top: 7, bottom: 7 }).backgroundColor(this.libraryAge === i ? '#DBF8DE' : LIGHT)
            .borderRadius(14).onClick(() => {
              this.libraryAge = this.libraryAge === i ? -1 : i;
              this.filter = '分级';
              this.replayLibraryEntrance();
            });
        });
      }.width('100%');
      this.SectionHeading(this.filter === '分级' ? '适龄绘本' : '精选绘本', '查看全部', 'books');
      if (this.matchingBooks().length > 0) {
        Row({ space: 8 }) {
          ForEach(this.matchingBooks(), (id: number, index: number) => {
            Column({ space: 5 }) {
              this.Cover(id, 126);
              Text(this.titles[id]).fontFamily(FONT_STRONG).fontSize(14).fontColor(INK).maxLines(1);
              Text('✓ ' + ['3–6岁', '4–8岁', '5–8岁'][id]).fontFamily(FONT_BODY).fontSize(13).fontColor('#24B856');
            }.layoutWeight(1).padding(6).backgroundColor(LIGHT).borderRadius(15)
              .translate({ x: 0, y: this.libraryCardsVisible ? 0 : 14 })
              .scale({ x: this.libraryCardsVisible ? 1 : 0.96, y: this.libraryCardsVisible ? 1 : 0.96 })
              .opacity(this.libraryCardsVisible ? 1 : 0)
              .animation({ duration: 420, delay: index * 90, curve: Curve.EaseOut })
              .onClick(() => { this.selected = id; this.setView('book'); });
          });
        }.width('100%');
      } else {
        Text('还没找到这本书，换个名字试试吧').fontFamily(FONT_BODY).fontSize(16).fontColor(MUTED)
          .padding(20).width('100%').backgroundColor(LIGHT).borderRadius(16);
      }
      this.SectionHeading('主题活动', '查看更多', 'themes');
      Row({ space: 8 }) {
        this.ThemeCard('春天的故事', '共12本', 0);
        this.ThemeCard('情绪小课堂', '共8本', 1);
        this.ThemeCard('奇妙的自然', '共10本', 2);
      }.width('100%');
      this.Action('＋ 为阅读器准备新绘本', '#DDF7DC', 'pair');
    }.padding({ left: 16, right: 16, bottom: 22 }).onAppear(() => { this.replayLibraryEntrance(); });
  }

  @Builder
  ThemeCard(title: string, sub: string, id: number) {
    Stack() {
      if (id === 0) { Image($r('app.media.page_themes_spring')).width('100%').height(118).objectFit(ImageFit.Cover); }
      else if (id === 1) { Image($r('app.media.page_themes_feelings')).width('100%').height(118).objectFit(ImageFit.Cover); }
      else { Image($r('app.media.page_themes_nature')).width('100%').height(118).objectFit(ImageFit.Cover); }
      Column({ space: 4 }) {
        Text(title).fontFamily(FONT_STRONG).fontSize(14).fontColor(id === 1 ? '#AF3B58' : '#1D6444').maxLines(1);
        Text(sub).fontFamily(FONT_BODY).fontSize(12).fontColor('#668B73');
      }.alignItems(HorizontalAlign.Start).position({ x: 8, y: 10 });
    }.layoutWeight(1).height(118).borderRadius(14).clip(true)
      .onClick(() => { this.selected = id; this.setView('book'); });
  }

  @Builder
  TrendBars(values: number[], maximum: number) {
    Row({ space: 9 }) {
      Column() {
        Text(maximum.toString()).fontFamily(FONT_BODY).fontSize(11).fontColor('#A2AAB7');
        Blank(); Text((maximum / 2).toString()).fontFamily(FONT_BODY).fontSize(11).fontColor('#A2AAB7');
        Blank(); Text('0').fontFamily(FONT_BODY).fontSize(11).fontColor('#A2AAB7');
      }.width(20).height(94).margin({ bottom: 17 });
      Row({ space: 7 }) {
        ForEach(values, (value: number, i: number) => {
          Column({ space: 6 }) {
            Blank().width(18).height(value > 0 ? Math.max(2, value / maximum * 90) : 0).backgroundColor('#31D966')
              .linearGradient({ angle: 0, colors: [['#97F0A2', 0], ['#14CC50', 1]] }).borderRadius({ topLeft: 6, topRight: 6 });
            Text(['周一', '周二', '周三', '周四', '周五', '周六', '周日'][i])
              .fontFamily(FONT_BODY).fontSize(11).fontColor('#9A9FB0');
          }.layoutWeight(1).justifyContent(FlexAlign.End);
        }, (value: number, i: number) => i.toString() + ':' + value.toString());
      }.layoutWeight(1).height(112).alignItems(VerticalAlign.Bottom);
    }.width('100%').height(114).alignItems(VerticalAlign.Bottom);
  }

  @Builder
  Growth() {
    Column({ space: 12 }) {
      this.PageHeading('我的阅读成长', this.growthPeriod + ' ⌄', 'period');
      Row({ space: 10 }) {
        Image($r('app.media.page_boy')).width(108).height(96).objectFit(ImageFit.Contain);
        Column({ space: 5 }) {
          Text('共读记录').fontFamily(FONT_TITLE).fontSize(21).fontColor('#175A37');
          Text('来自当前阅读器 · ' + this.growthPeriod)
            .fontFamily(FONT_BODY).fontSize(15).fontColor('#3B6950');
          Text(this.selectedWeek().sessionCount > 0 ? '共读 ' + this.selectedWeek().sessionCount + ' 次' : '暂无该周的阅读记录')
            .fontFamily(FONT_STRONG).fontSize(18).fontColor('#126B35');
        }.alignItems(HorizontalAlign.Start).layoutWeight(1);
      }.width('100%').height(96).backgroundColor('#E0FFDD').borderRadius(20).padding({ left: 4, right: 10 });
      Row({ space: 6 }) {
        ForEach(['阅读数据', '能力雷达', '成长勋章'], (name: string) => {
          Text(name).fontFamily(FONT_STRONG).fontSize(14).fontColor(this.growthSection === name ? LIGHT : '#7D8596')
            .layoutWeight(1).textAlign(TextAlign.Center).padding({ top: 9, bottom: 9 })
            .backgroundColor(this.growthSection === name ? '#14C83F' : '#F0F1EA').borderRadius(18)
            .onClick(() => { this.growthSection = name; });
        });
      }.width('100%');
      if (this.growthSection === '成长勋章') {
        Row({ space: 9 }) {
          this.BadgeCard('★', '故事探索家', '#FFF1C6');
          this.BadgeCard('♧', '好奇提问家', '#E4F6FF');
          this.BadgeCard('♥', '共读好搭档', '#FFE7EC');
        }.width('100%');
        Image($r('app.media.home_reading_banner')).width('100%').height(135).objectFit(ImageFit.Cover).borderRadius(18);
        Text('勋章样式预览。当前阅读器已连续共读 ' + this.readingSummary.streakDays + ' 天。')
          .fontFamily(FONT_BODY).fontSize(16).fontColor('#487156').lineHeight(27).padding(16)
          .backgroundColor(LIGHT).borderRadius(18);
      } else {
        if (this.growthSection === '阅读数据') {
          Row({ space: 8 }) {
            this.GrowthMetric('◷', '阅读时长', this.selectedWeek().durationMs > 0 && this.selectedWeek().durationMs < 60000 ?
              '<1' : Math.round(this.selectedWeek().durationMs / 60000).toString(), '分钟', '#18C94E');
            this.GrowthMetric('▣', '阅读本数', this.selectedWeek().bookCount.toString(), '本', '#45A0F6');
            this.GrowthMetric('♨', '有共读记录', this.selectedWeek().activeDays.toString(), '天', '#FF9E35');
          }.width('100%');
          Column({ space: 8 }) {
            Row() {
              Text('阅读趋势').fontFamily(FONT_STRONG).fontSize(17).fontColor(INK).layoutWeight(1);
              Text(formatReadingDuration(this.selectedWeek().durationMs)).fontFamily(FONT_STRONG).fontSize(13).fontColor(LIGHT)
                .padding({ left: 9, right: 9, top: 5, bottom: 5 }).backgroundColor('#13CB43').borderRadius(13);
            }.width('100%');
            this.TrendBars(this.selectedWeek().dailyMinutes, this.selectedWeek().chartMaximum);
          }.padding(14).width('100%').backgroundColor(LIGHT).borderRadius(19);
          Column({ space: 8 }) {
            Row() {
              Text('位置复核').fontFamily(FONT_STRONG).fontSize(17).fontColor(INK).layoutWeight(1);
              Text('按共读周统计').fontFamily(FONT_BODY).fontSize(12).fontColor(MUTED);
            }.width('100%');
            Row({ space: 6 }) {
              this.GrowthMetric('!', '标记不符', this.selectedWeek().mismatchCount.toString(), '处', '#E08A29');
              this.GrowthMetric('✓', '确认无误', this.selectedWeek().confirmedCount.toString(), '处', '#18C94E');
              this.GrowthMetric('·', '待核对', this.selectedWeek().needsReviewCount.toString(), '处', '#45A0F6');
            }.width('100%');
            Text(this.selectedWeek().reviewCount > 0 ?
              this.growthPeriod + '复核 ' + this.selectedWeek().reviewCount + ' 个定位点；复核状态独立于原始阅读轨迹。' :
              '该周暂无位置复核记录。')
              .fontFamily(FONT_BODY).fontSize(13).fontColor(MUTED).lineHeight(20);
          }.padding(13).width('100%').backgroundColor(LIGHT).borderRadius(19);
          if (this.readingSummary.undatedCount > 0) {
            Text(this.readingSummary.undatedCount + ' 条记录的阅读日期未知，可到设备中心查看。')
              .fontFamily(FONT_BODY).fontSize(14).fontColor(MUTED);
          }
          this.Action('查看和同步阅读器记录', '#D8F3DF', 'pair');
        } else {
          Column({ space: 5 }) {
            Text('阅读能力雷达 · 样式预览').fontFamily(FONT_STRONG).fontSize(17).fontColor(INK).width('100%');
            ReadingRadar();
          }.padding({ left: 13, right: 13, top: 13, bottom: 6 }).width('100%').backgroundColor(LIGHT).borderRadius(19);
          Column({ space: 14 }) {
            this.SkillProgress('理解能力', 82, '#21CC5C');
            this.SkillProgress('语言表达', 72, '#5EBAEA');
            this.SkillProgress('阅读兴趣', 90, '#E8B94C');
            Text('每天聊一个故事里的小问题，让表达和好奇心一起成长。')
              .fontFamily(FONT_BODY).fontSize(15).fontColor('#788490').lineHeight(24);
          }.padding(15).width('100%').backgroundColor('#ECF9E9').borderRadius(18);
        }
      }
    }.padding({ left: 16, right: 16, bottom: 22 });
  }

  @Builder
  GrowthMetric(icon: string, title: string, value: string, unit: string, color: string) {
    Column({ space: 7 }) {
      Row({ space: 4 }) {
        Text(icon).fontFamily(FONT_BODY).fontSize(16).fontColor(color);
        Text(title).fontFamily(FONT_BODY).fontSize(12).fontColor('#8B92A2');
      }
      Row({ space: 3 }) {
        Text(value).fontFamily(FONT_TITLE).fontSize(27).fontColor(INK);
        Text(unit).fontFamily(FONT_STRONG).fontSize(13).fontColor(INK).margin({ top: 8 });
      }
    }.layoutWeight(1).padding({ top: 12, bottom: 12 }).backgroundColor(LIGHT).borderRadius(16);
  }

  @Builder
  Metric(value: string, label: string, color: string) {
    Column({ space: 6 }) {
      Text(value).fontFamily(FONT_TITLE).fontSize(27).fontColor(INK);
      Text(label).fontFamily(FONT_BODY).fontSize(13).fontColor('#6D7C87');
    }.layoutWeight(1).padding(13).backgroundColor(color).borderRadius(17);
  }

  @Builder
  SkillProgress(label: string, value: number, color: string) {
    Row({ space: 8 }) {
      Text(label).fontFamily(FONT_BODY).fontSize(14).fontColor('#63727D').width(58);
      Progress({ value: value, total: 100, type: ProgressType.Linear }).layoutWeight(1).height(7).color(color).backgroundColor('#E0EBDD');
      Text(value + '%').fontFamily(FONT_STRONG).fontSize(13).fontColor(INK).width(30);
    }.width('100%');
  }

  @Builder
  BadgeCard(icon: string, label: string, color: string) {
    Column({ space: 9 }) {
      Text(icon).fontSize(29).fontColor('#E7B51F');
      Text(label).fontFamily(FONT_STRONG).fontSize(13).fontColor(INK).maxLines(1);
    }.layoutWeight(1).padding({ top: 17, bottom: 17 }).backgroundColor(color).borderRadius(18);
  }

  @Builder
  CelebrationCharacter() {
    Stack() { ReferenceCompanion({ kind: 'reward', canvasSize: 220, reduced: this.reducedMotion }); }
      .width(220).height(250);
  }

  @Builder
  HappyCharacter() {
    Stack() { ReferenceCompanion({ kind: 'happy', canvasSize: 178, reduced: this.reducedMotion }); }
      .width(178).height(205);
  }

  @Builder
  ChatAvatar(child: boolean) {
    Stack() {
      if (child) {
        Image($r('app.media.home_wave_body')).width(76).height(76).objectFit(ImageFit.Contain).position({ x: -22, y: -14 });
      } else {
        Image($r('app.media.page_boy')).width(75).height(75).objectFit(ImageFit.Contain).position({ x: -16, y: -11 });
      }
    }.width(42).height(42).backgroundColor(child ? '#E0FAD6' : '#D9EFFF').borderRadius(21).clip(true);
  }

  @Builder
  AI() {
    Column({ space: 17 }) {
      this.PageHeading('AI阅读伙伴', '◷ 对话历史', 'history');
      Row({ space: 10 }) {
        this.ChatAvatar(false);
        Column({ space: 6 }) {
          Text('你好呀，' + this.child + '！').fontFamily(FONT_STRONG).fontSize(16).fontColor('#335579');
          Text('我可以和你一起读故事，\n也可以回答你的好奇问题。').fontFamily(FONT_BODY).fontSize(16)
            .fontColor('#526681').lineHeight(26).width('100%');
        }.padding(15).alignItems(HorizontalAlign.Start).backgroundColor('#E5F2FF').borderRadius(20).layoutWeight(1);
        Blank().width(8);
      }.width('100%').alignItems(VerticalAlign.Top);
      Row({ space: 9 }) {
        Blank().layoutWeight(1);
        Text(this.question).fontFamily(FONT_STRONG).fontSize(16).fontColor('#205C38').padding(14)
          .constraintSize({ maxWidth: 263 }).backgroundColor('#D5F9DC').borderRadius(19);
        this.ChatAvatar(true);
      }.width('100%').alignItems(VerticalAlign.Top);
      if (this.answered) {
        Row({ space: 10 }) {
          this.ChatAvatar(false);
          Column({ space: 12 }) {
            Text('一起想一想').fontFamily(FONT_STRONG).fontSize(16).fontColor(INK).width('100%');
            Text(this.answerText()).fontFamily(FONT_BODY).fontSize(16).fontColor('#4D5D78').lineHeight(27).width('100%');
            Image($r('app.media.page_sky')).width('100%').height(112).objectFit(ImageFit.Cover).borderRadius(14)
              .onClick(() => { this.setView('listen'); });
            Row() {
              Text('看图，讲给孩子听').fontFamily(FONT_BODY).fontSize(13).fontColor('#38986A').layoutWeight(1);
              Text('↗').fontFamily('HarmonyOS Sans').fontSize(20).fontColor('#1BBF53');
            }.width('100%').onClick(() => { this.setView('listen'); });
          }.padding(16).alignItems(HorizontalAlign.Start).backgroundColor(LIGHT).borderRadius(21).layoutWeight(1);
        }.width('100%').alignItems(VerticalAlign.Top);
      } else {
        Row({ space: 10 }) {
          ReferenceCompanion({ kind: 'thinking', canvasSize: 112, reduced: this.reducedMotion });
          Text('让我想一想…').fontFamily(FONT_STRONG).fontSize(16).fontColor('#526681')
            .padding({ left: 16, right: 20, top: 15, bottom: 15 })
            .backgroundColor(LIGHT).borderRadius(19);
        }.width('100%').alignItems(VerticalAlign.Center);
      }
      Text('你可能还想问').fontFamily(FONT_STRONG).fontSize(15).fontColor('#7B879A').width('100%');
      Flex({ direction: FlexDirection.Row, wrap: FlexWrap.Wrap, space: { main: LengthMetrics.vp(8), cross: LengthMetrics.vp(8) } }) {
        ForEach(['为什么会下雨？', '星星为什么发光？', '大海为什么是蓝色的？'], (question: string) => {
          Text(question).fontFamily(FONT_BODY).fontSize(14).fontColor('#62708A').padding({ left: 13, right: 13, top: 11, bottom: 11 })
            .backgroundColor(LIGHT).borderRadius(18).onClick(() => { this.ask(question); });
        });
      }.width('100%');
    }.padding({ left: 16, right: 16, bottom: 20 });
  }

  @Builder
  AIComposer() {
    Column({ space: 7 }) {
      if (this.aiVoiceHint) {
        Text('试着问：“为什么天空是蓝色的？”').fontFamily(FONT_BODY).fontSize(14).fontColor('#4C86BA')
          .padding(11).width('100%').backgroundColor('#EAF4FF').borderRadius(14);
      }
      Row({ space: 8 }) {
        Image($r('app.media.icon_voice')).width(44).height(44).padding(9).backgroundColor('#EAF2FF').borderRadius(22)
          .onClick(() => { this.aiVoiceHint = !this.aiVoiceHint; });
        TextInput({ placeholder: '你还想知道什么？', text: this.aiDraft }).fontFamily(FONT_BODY).fontSize(16)
          .layoutWeight(1).height(48).backgroundColor(LIGHT).fontColor(INK).placeholderColor('#9BA8C0')
          .onChange((value: string) => { this.aiDraft = value; }).onSubmit(() => { this.ask(this.aiDraft); });
        Text('➤').fontFamily('HarmonyOS Sans').fontSize(24).fontColor(LIGHT).width(46).height(46).textAlign(TextAlign.Center)
          .padding({ top: 8 }).backgroundColor(this.aiDraft.trim().length > 0 ? '#14C843' : '#80DDA0').borderRadius(23)
          .onClick(() => { this.ask(this.aiDraft); });
      }.padding(7).width('100%').backgroundColor(LIGHT).borderRadius(30)
        .shadow({ radius: 9, color: '#386A3110', offsetY: 1 });
    }.padding({ left: 14, right: 14, top: 8, bottom: 8 }).width('100%').backgroundColor(PAGE)
      .opacity(this.pageContentOpacity).translate({ x: 0, y: this.pageContentOffset });
  }

  @Builder
  Device() {
    Column({ space: 14 }) {
      Row() {
        Column({ space: 5 }) {
          Text('设备中心').fontFamily(FONT_TITLE).fontSize(27).fontColor(INK);
          Text('好故事，随时陪在身边').fontFamily(FONT_BODY).fontSize(14).fontColor('#8994A2');
        }.layoutWeight(1).alignItems(HorizontalAlign.Start);
        HomeWelcomeMascot({ reduced: true, canvasSize: 92 });
      }.height(88).width('100%').clip(true);
      Row({ space: 12 }) {
        Image($r('app.media.page_reader')).width(126).height(148).objectFit(ImageFit.Contain)
          .onClick(() => { this.setView('pair'); });
        Column({ space: 9 }) {
          Row({ space: 5 }) {
            Text('童阅阅读器').fontFamily(FONT_TITLE).fontSize(20).fontColor(INK).layoutWeight(1);
            Image($r('app.media.page_settings')).width(26).height(26).onClick(() => { this.setView('device-settings'); });
          }.width('100%');
          Text('RFID 故事伙伴').fontFamily(FONT_BODY).fontSize(13).fontColor('#9AA3B3');
          Text('连接阅读器、绑定绘本并同步阅读记录').fontFamily(FONT_BODY).fontSize(14).fontColor('#7C8B9D');
        }.layoutWeight(1).alignItems(HorizontalAlign.Start);
      }.padding(12).width('100%').backgroundColor(LIGHT).borderRadius(23)
        .shadow({ radius: 10, color: '#42617A0A', offsetY: 3 });
      Row({ space: 7 }) {
        this.DeviceShortcut('▣', '内容管理', '阅读器绘本', '#DCF8F4', 'packs');
        this.DeviceShortcut('♥', '使用管控', this.dailyMinutes + '分钟/日', '#FFE7EB', 'usage');
        this.DeviceShortcut('◷', '专注模式', this.focusMode ? '已开启' : '点击开启', '#EEE7FF', 'focus');
        this.DeviceShortcut('⚙', '设备设置', '声音/提醒', '#E7F2FF', 'device-settings');
      }.width('100%');
      Row({ space: 4 }) {
        Column({ space: 7 }) {
          Text('一起养成好习惯').fontFamily(FONT_TITLE).fontSize(20).fontColor('#126332');
          Text('每天读一点，成长看得见').fontFamily(FONT_BODY).fontSize(14).fontColor('#528564').lineHeight(22);
        }.layoutWeight(1).alignItems(HorizontalAlign.Start);
        Image($r('app.media.page_boy')).width(104).height(100).objectFit(ImageFit.Contain);
      }.width('100%').constraintSize({ minHeight: 106 }).padding({ left: 15, right: 4, top: 4, bottom: 4 })
        .backgroundColor('#DDF9D1').borderRadius(20).onClick(() => { this.switchTab(2); });
      this.Setting('◷', '阅读器记录', '查看并同步阅读记录', 'pair');
      this.Setting('♧', '孩子档案', this.child + ' · 点击切换', 'profile');
      this.Setting('◇', '隐私与数据', '由家长管理每一次分享', 'privacy');
      this.Action('管理阅读器与绘本标签', '#D8F3DF', 'pair');
    }.padding({ left: 16, right: 16, bottom: 24 });
  }

  @Builder
  DeviceShortcut(icon: string, title: string, detail: string, color: string, target: string) {
    Column({ space: 7 }) {
      if (target === 'packs') { Image($r('app.media.page_content')).width(38).height(38); }
      else if (target === 'usage') { Image($r('app.media.home_recommend')).width(38).height(38); }
      else if (target === 'focus') { Image($r('app.media.page_focus')).width(38).height(38); }
      else { Image($r('app.media.page_settings')).width(38).height(38); }
      Text(title).fontFamily(FONT_STRONG).fontSize(13).fontColor(INK).maxLines(1);
      Text(detail).fontFamily(FONT_BODY).fontSize(11).fontColor('#8A96A7').maxLines(1);
    }.layoutWeight(1).padding({ top: 13, bottom: 13 }).backgroundColor(target === 'focus' && this.focusMode ? '#F0EBFF' : LIGHT)
      .border({ width: target === 'focus' && this.focusMode ? 1 : 0, color: '#D5C6FA' }).borderRadius(17)
      .onClick(() => { if (target === 'focus') { this.focusMode = !this.focusMode; } else { this.setView(target); } });
  }

  @Builder
  Setting(icon: string, title: string, detail: string, target: string) {
    Row({ space: 11 }) {
      Text(icon).fontSize(21).fontFamily(FONT_BODY).fontColor('#329C68').width(36).height(36).textAlign(TextAlign.Center)
        .padding({ top: 5 }).backgroundColor('#E9F7EC').borderRadius(12);
      Column({ space: 3 }) {
        Text(title).fontSize(16).fontFamily(FONT_BODY).fontWeight(FontWeight.Bold).fontFamily(FONT_STRONG).fontColor(INK);
        Text(detail).fontSize(13).fontFamily(FONT_BODY).fontColor(MUTED);
      }.layoutWeight(1).alignItems(HorizontalAlign.Start);
      Text('›').fontSize(24).fontFamily(FONT_BODY).fontColor('#98A5A7');
    }.width('100%').padding(11).backgroundColor(LIGHT).borderRadius(16).onClick(() => { this.setView(target); });
  }

  @Builder
  Detail() {
    Column({ space: 15 }) {
      Row() {
        Text('‹  返回').fontSize(17).fontFamily(FONT_BODY).fontWeight(FontWeight.Bold).fontFamily(FONT_STRONG).fontColor('#188C4A').onClick(() => { this.setView(''); });
        Blank();
        Text(this.child).fontSize(14).fontFamily(FONT_BODY).fontColor(MUTED);
      }.width('100%');
      if (this.view === 'device-settings') {
        Text('阅读器设置').fontFamily(FONT_TITLE).fontSize(27).fontColor(INK).width('100%');
        Text('故事音量  ' + this.deviceVolume + '%').fontFamily(FONT_STRONG).fontSize(18).fontColor(INK).width('100%');
        Slider({ value: this.deviceVolume, min: 0, max: 100, step: 5 }).width('100%').selectedColor('#20C85B')
          .onChange((value: number) => { this.deviceVolume = value; });
        Row() {
          Text('专注阅读模式').fontFamily(FONT_STRONG).fontSize(18).fontColor(INK).layoutWeight(1);
          Toggle({ type: ToggleType.Switch, isOn: this.focusMode }).selectedColor('#21C85B')
            .onChange((value: boolean) => { this.focusMode = value; });
        }.width('100%').padding(15).backgroundColor(LIGHT).borderRadius(18);
        this.Setting('◷', '每日阅读时长', this.dailyMinutes + '分钟', 'usage');
        this.Setting('◇', '隐私与数据', '由家长管理阅读记录', 'privacy');
        this.Action('保存并返回', LIME, 'back');
      } else if (this.view === 'usage') {
        Text('每天的阅读约定').fontFamily(FONT_TITLE).fontSize(27).fontColor(INK).width('100%');
        Text('为孩子选择每天的共读时长').fontFamily(FONT_BODY).fontSize(17).fontColor('#78869B').width('100%');
        ForEach([15, 30, 45, 60], (minutes: number) => {
          Button(minutes + ' 分钟' + (this.dailyMinutes === minutes ? '  ✓' : '')).fontFamily(FONT_STRONG).fontSize(20)
            .height(60).width('100%').backgroundColor(this.dailyMinutes === minutes ? '#DDF8E3' : LIGHT)
            .fontColor(INK).borderRadius(18).onClick(() => { this.dailyMinutes = minutes; });
        });
        Text('每天读一点，也给眼睛留出休息时间。').fontFamily(FONT_BODY).fontSize(16).fontColor('#70867B');
        this.Action('保存阅读约定', LIME, 'back');
      } else if (this.view === 'packs') {
        RfidReaderPanel({ managePacks: true, onCacheChanged: (cache: ReaderCache) => { this.updateReadingSummary(cache); } });
        this.Action('绑定绘本标签', '#DFF5DA', 'pair');
      } else if (this.view === 'history') {
        Text('对话历史').fontFamily(FONT_TITLE).fontSize(27).fontColor(INK).width('100%');
        ForEach(['为什么天空是蓝色的？', '为什么会下雨？', '星星为什么会发光？'], (question: string) => {
          Text(question + '  ›').fontFamily(FONT_BODY).fontSize(17).fontColor(INK).padding(18)
            .width('100%').backgroundColor(LIGHT).borderRadius(18)
            .onClick(() => { this.ask(question); this.switchTab(3); });
        });
      } else if (this.view === 'listen') {
        Text('和孩子一起读').fontFamily(FONT_TITLE).fontSize(27).fontColor(INK).width('100%');
        Image($r('app.media.page_sky')).width('100%').height(200).objectFit(ImageFit.Cover).borderRadius(20);
        Text(this.question).fontFamily(FONT_STRONG).fontSize(22).fontColor(INK).width('100%');
        Text(this.answerText()).fontFamily(FONT_BODY).fontSize(19).fontColor('#53647E').lineHeight(34);
        this.Action('回到对话', LIME, 'back');
      } else if (this.view === 'book') {
        this.Cover(this.selected, 300);
        Text(this.titles[this.selected]).fontSize(29).fontFamily(FONT_BODY).fontWeight(FontWeight.Bold).fontFamily(FONT_STRONG).fontColor(INK).width('100%');
        Text('3–6 岁  ·  约 15 分钟  ·  亲子共读').fontSize(15).fontFamily(FONT_BODY).fontColor(MUTED).width('100%');
        Text('在故事里认识新朋友，也听听孩子心里的小世界。读完后，可以聊聊：“你最喜欢谁？为什么？”')
          .fontSize(17).fontFamily(FONT_BODY).fontColor('#52655D').lineHeight(30);
        this.Action('和孩子一起读  ▶', LIME, 'read');
      } else if (this.view === 'read') {
        Row() {
          Text(this.titles[this.selected]).fontSize(18).fontFamily(FONT_BODY).fontWeight(FontWeight.Bold).fontFamily(FONT_STRONG).fontColor(INK).layoutWeight(1);
          Text(this.page + ' / 24').fontSize(14).fontFamily(FONT_BODY).fontColor(MUTED);
        }.width('100%');
        Progress({ value: this.page, total: 24, type: ProgressType.Linear }).width('100%').height(8).color(LIME).backgroundColor('#E6EEE6');
        this.Cover(this.selected, 340);
        Text(this.page < 16 ? '小雨来到森林里，想找一个可以一起读故事的朋友。' : '“你好，我们能一起玩吗？”小雨终于勇敢地说出了心里的话。')
          .fontSize(22).fontFamily(FONT_BODY).fontWeight(FontWeight.Bold).fontFamily(FONT_STRONG).fontColor(INK).lineHeight(35).width('100%');
        Text('演示共读 · 可手动翻页体验').fontSize(14).fontFamily(FONT_BODY).fontColor(MUTED).width('100%');
        Row({ space: 10 }) {
          Button('上一页').height(49).layoutWeight(1).backgroundColor('#EDF3EF').fontColor(INK).borderRadius(15)
            .onClick(() => { this.page = Math.max(1, this.page - 1); });
          Button(this.page >= 24 ? '完成共读' : '下一页 →').height(49).layoutWeight(2).backgroundColor(LIME).fontColor(LIGHT)
            .fontWeight(FontWeight.Bold).fontFamily(FONT_STRONG).borderRadius(15)
            .onClick(() => { if (this.page >= 24) { this.go('finish'); } else { this.page++; } });
        }.width('100%');
      } else if (this.view === 'finish') {
        Column({ space: 12 }) {
          this.CelebrationCharacter();
          Text('又收获一个好故事！').fontSize(28).fontFamily(FONT_BODY).fontWeight(FontWeight.Bold).fontFamily(FONT_STRONG).fontColor(INK);
          Text('今天的共读旅程完成啦。\n给彼此一个大大的拥抱吧。').fontSize(16).fontFamily(FONT_BODY).fontColor('#5D6C65')
            .textAlign(TextAlign.Center).lineHeight(27);
          Row({ space: 10 }) {
            this.Metric('+20', '阅读星星', '#FFF0C8');
            this.Metric(this.readingSummary.streakDays + ' 天', '阅读器连续共读', '#E1F5E5');
          }.width('100%');
          this.Action('回到故事岛', LIME, 'back');
        }.padding(18).width('100%').backgroundColor('#FFF8E2').borderRadius(24);
      } else if (this.view === 'pair') {
        RfidReaderPanel({ onCacheChanged: (cache: ReaderCache) => { this.updateReadingSummary(cache); } });
      } else if (this.view === 'profile') {
        Text('谁来打开今天的故事？').fontSize(25).fontFamily(FONT_BODY).fontWeight(FontWeight.Bold).fontFamily(FONT_STRONG).fontColor(INK).width('100%');
        ForEach(['小雨', '小星'], (name: string, i: number) => {
          Button(name + (this.child === name ? '  ✓' : '')).width('100%').height(62)
            .backgroundColor(this.child === name ? '#DFF6E5' : LIGHT)
            .fontColor(INK).fontSize(20).fontFamily(FONT_BODY).fontWeight(FontWeight.Bold).fontFamily(FONT_STRONG).borderRadius(18)
            .onClick(() => { this.child = name; this.setView(''); });
        });
      } else if (this.view === 'privacy') {
        Text('把选择留给家长').fontSize(25).fontFamily(FONT_BODY).fontWeight(FontWeight.Bold).fontFamily(FONT_STRONG).fontColor(INK).width('100%');
        Text('原始录音默认不保存。复核以故事文字为参考，你可以随时撤回复核记录。')
          .fontSize(17).fontFamily(FONT_BODY).fontColor('#53665E').lineHeight(30);
        Button(this.reducedMotion ? '✓ 已开启：减少动态效果' : '开启减少动态效果').height(50).width('100%')
          .backgroundColor('#DFF6E5').fontColor(INK).borderRadius(16)
          .onClick(() => { this.reducedMotion = !this.reducedMotion; });
      } else {
        HomeWelcomeMascot({ reduced: true, canvasSize: 160 });
        Text(this.view === 'schedule' ? '约好每天的共读时光' : '让阅读器听清这一句')
          .fontSize(25).fontFamily(FONT_BODY).fontWeight(FontWeight.Bold).fontFamily(FONT_STRONG).fontColor(INK).textAlign(TextAlign.Center);
        Text(this.view === 'schedule' ? '今天 19:30，用 15 分钟陪孩子进入故事。' : '安静地读一句绘本正文，阅读器会尝试找到当前句段。')
          .fontSize(16).fontFamily(FONT_BODY).fontColor('#586B63').lineHeight(28).textAlign(TextAlign.Center);
        this.Action('知道了', '#DFF6E5', 'back');
      }
    }.padding(20).width('100%').alignItems(HorizontalAlign.Center);
  }

  @Builder
  Navigation() {
    Row() {
      ForEach([0, 1, 2, 3, 4], (id: number) => {
        Column({ space: 3 }) {
          if (id === 0) { Image($r('app.media.nav_home')).width(24).height(24); }
          else if (id === 1) { Image($r('app.media.nav_book')).width(24).height(24); }
          else if (id === 2) { Image($r('app.media.nav_growth')).width(24).height(24); }
          else if (id === 3) { Image($r('app.media.nav_ai')).width(24).height(24); }
          else { Image($r('app.media.nav_device')).width(24).height(24); }
          Text(['首页', '绘本馆', '成长', 'AI互动', '设备'][id]).fontSize(13).fontFamily(FONT_BODY)
            .fontWeight(this.tab === id ? FontWeight.Bold : FontWeight.Regular)
            .fontFamily(this.tab === id ? FONT_STRONG : FONT_BODY)
            .fontColor(this.tab === id ? '#15984C' : '#89949A').height(22);
        }.layoutWeight(1).height(64).justifyContent(FlexAlign.Center)
          .backgroundColor(this.tab === id ? '#EAF7ED' : '#FFFDF7').borderRadius(16)
          .animation({ duration: 210, curve: Curve.EaseInOut })
          .onClick(() => { this.switchTab(id); });
      });
    }.padding({ left: 8, right: 8, top: 6, bottom: 7 }).width('100%').backgroundColor('#FFFDF7');
  }

  build() {
    Column() {
      Scroll(this.pageScroller) {
        if (this.view.length > 0) { this.Detail(); }
        else if (this.tab === 0) { this.Home(); }
        else if (this.tab === 1) { this.Library(); }
        else if (this.tab === 2) { this.Growth(); }
        else if (this.tab === 3) { this.AI(); }
        else { this.Device(); }
      }.layoutWeight(1).scrollBar(BarState.Off).width('100%')
        .opacity(this.pageContentOpacity).translate({ x: 0, y: this.pageContentOffset });
      if (this.view.length === 0 && this.tab === 3) { this.AIComposer(); }
      if (this.view.length === 0) { this.Navigation(); }
    }.width('100%').height('100%').backgroundColor(this.pageColor());
  }
}
