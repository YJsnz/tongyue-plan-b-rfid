import { FONT_BODY } from '../theme/Typography';

@Component
export struct ReadingRadar {
  build() {
    Row() {
      Blank().layoutWeight(1);
      Stack() {
        Image($r('app.media.page_radar')).width(280).height(172);
        Text('理解能力').fontFamily(FONT_BODY).fontSize(13).fontColor('#6D7890').position({ x: 117, y: 1 });
        Text('词汇积累').fontFamily(FONT_BODY).fontSize(13).fontColor('#6D7890').position({ x: 225, y: 56 });
        Text('语言表达').fontFamily(FONT_BODY).fontSize(13).fontColor('#6D7890').position({ x: 191, y: 149 });
        Text('专注力').fontFamily(FONT_BODY).fontSize(13).fontColor('#6D7890').position({ x: 48, y: 149 });
        Text('阅读兴趣').fontFamily(FONT_BODY).fontSize(13).fontColor('#6D7890').position({ x: 0, y: 56 });
      }.width(280).height(172);
      Blank().layoutWeight(1);
    }.width('100%');
  }
}
