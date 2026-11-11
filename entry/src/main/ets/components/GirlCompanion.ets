@Component
export struct GirlCompanion {
  @Prop mode: string = 'sequence';
  @Prop canvasSize: number = 190;
  @Prop reduced: boolean = false;

  private frame(src: Resource, duration: number = 120): ImageFrameInfo {
    return { src: src, width: this.canvasSize, height: this.canvasSize * 1.25, duration: duration };
  }

  private restFrame(): ImageFrameInfo {
    return this.frame($r('app.media.girl_wave_00'), 10000);
  }

  private stillFrame(): Resource {
    if (this.mode === 'reading') { return $r('app.media.girl_reading_06'); }
    if (this.mode === 'think') { return $r('app.media.girl_think_07'); }
    if (this.mode === 'celebrate') { return $r('app.media.girl_celebrate_07'); }
    if (this.mode === 'encourage') { return $r('app.media.girl_think_06'); }
    return $r('app.media.girl_wave_00');
  }

  @Builder
  Animator(frames: Array<ImageFrameInfo>, repeatCount: number) {
    ImageAnimator()
      .images(frames)
      .iterations(repeatCount)
      .fillMode(FillMode.Forwards)
      .state(AnimationStatus.Running)
      .fixedSize(true)
      .width(this.canvasSize)
      .height(this.canvasSize * 1.25);
  }

  build() {
    if (this.reduced) {
      Image(this.stillFrame())
        .width(this.canvasSize)
        .height(this.canvasSize * 1.25)
        .objectFit(ImageFit.Contain);
    } else if (this.mode === 'sequence') {
      this.Animator([
        this.frame($r('app.media.girl_wave_00')),
        this.frame($r('app.media.girl_wave_01')),
        this.frame($r('app.media.girl_wave_02')),
        this.frame($r('app.media.girl_wave_03')),
        this.frame($r('app.media.girl_wave_04')),
        this.frame($r('app.media.girl_wave_05')),
        this.frame($r('app.media.girl_wave_06')),
        this.frame($r('app.media.girl_wave_07')),
        this.restFrame(),
        this.frame($r('app.media.girl_reading_00')),
        this.frame($r('app.media.girl_reading_01')),
        this.frame($r('app.media.girl_reading_02')),
        this.frame($r('app.media.girl_reading_03')),
        this.frame($r('app.media.girl_reading_04')),
        this.frame($r('app.media.girl_reading_05')),
        this.frame($r('app.media.girl_reading_06')),
        this.frame($r('app.media.girl_reading_07')),
        this.restFrame(),
        this.frame($r('app.media.girl_think_00')),
        this.frame($r('app.media.girl_think_01')),
        this.frame($r('app.media.girl_think_02')),
        this.frame($r('app.media.girl_think_03')),
        this.frame($r('app.media.girl_think_04')),
        this.frame($r('app.media.girl_think_05')),
        this.frame($r('app.media.girl_think_06')),
        this.frame($r('app.media.girl_think_07')),
        this.restFrame(),
        this.frame($r('app.media.girl_celebrate_00')),
        this.frame($r('app.media.girl_celebrate_01')),
        this.frame($r('app.media.girl_celebrate_02')),
        this.frame($r('app.media.girl_celebrate_03')),
        this.frame($r('app.media.girl_celebrate_04')),
        this.frame($r('app.media.girl_celebrate_05')),
        this.frame($r('app.media.girl_celebrate_06')),
        this.frame($r('app.media.girl_celebrate_07')),
        this.restFrame()
      ], -1);
    } else if (this.mode === 'reading') {
      this.Animator([
        this.frame($r('app.media.girl_reading_00')),
        this.frame($r('app.media.girl_reading_01')),
        this.frame($r('app.media.girl_reading_02')),
        this.frame($r('app.media.girl_reading_03')),
        this.frame($r('app.media.girl_reading_04')),
        this.frame($r('app.media.girl_reading_05')),
        this.frame($r('app.media.girl_reading_06')),
        this.frame($r('app.media.girl_reading_07')),
        this.restFrame()
      ], -1);
    } else if (this.mode === 'encourage') {
      this.Animator([
        this.frame($r('app.media.girl_think_05')),
        this.frame($r('app.media.girl_think_06')),
        this.frame($r('app.media.girl_think_07')),
        this.restFrame()
      ], -1);
    } else if (this.mode === 'think') {
      this.Animator([
        this.frame($r('app.media.girl_think_00')),
        this.frame($r('app.media.girl_think_01')),
        this.frame($r('app.media.girl_think_02')),
        this.frame($r('app.media.girl_think_03')),
        this.frame($r('app.media.girl_think_04')),
        this.frame($r('app.media.girl_think_05')),
        this.frame($r('app.media.girl_think_06')),
        this.frame($r('app.media.girl_think_07')),
        this.restFrame()
      ], -1);
    } else if (this.mode === 'celebrate') {
      this.Animator([
        this.frame($r('app.media.girl_celebrate_00')),
        this.frame($r('app.media.girl_celebrate_01')),
        this.frame($r('app.media.girl_celebrate_02')),
        this.frame($r('app.media.girl_celebrate_03')),
        this.frame($r('app.media.girl_celebrate_04')),
        this.frame($r('app.media.girl_celebrate_05')),
        this.frame($r('app.media.girl_celebrate_06')),
        this.frame($r('app.media.girl_celebrate_07')),
        this.restFrame()
      ], -1);
    } else {
      this.Animator([
        this.frame($r('app.media.girl_wave_00')),
        this.frame($r('app.media.girl_wave_01')),
        this.frame($r('app.media.girl_wave_02')),
        this.frame($r('app.media.girl_wave_03')),
        this.frame($r('app.media.girl_wave_04')),
        this.frame($r('app.media.girl_wave_05')),
        this.frame($r('app.media.girl_wave_06')),
        this.frame($r('app.media.girl_wave_07')),
        this.restFrame()
      ], -1);
    }
  }
}
