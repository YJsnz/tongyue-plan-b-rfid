// Two resident atlas cells. Moving parts never swap textures between frames.
@Component
export struct ReferenceCompanion {
  @Prop kind: string = 'reading';
  @Prop canvasSize: number = 180;
  @Prop reduced: boolean = false;
  @State private angle: number = 0;
  @State private otherAngle: number = 0;
  @State private lift: number = 0;
  @State private stretch: number = 1;
  private timer: number = -1;
  private alive: boolean = false;
  private playing: boolean = false;
  private generation: number = 0;

  aboutToAppear(): void {
    this.alive = true;
    this.timer = setTimeout(() => { this.play(); }, 400);
  }

  aboutToDisappear(): void {
    this.alive = false;
    this.generation++;
    clearTimeout(this.timer);
  }

  private texture(): Resource {
    if (this.kind === 'reward') { return $r('app.media.rig_reward'); }
    if (this.kind === 'thinking') { return $r('app.media.rig_thinking'); }
    if (this.kind === 'happy') { return $r('app.media.rig_happy'); }
    return $r('app.media.rig_reading');
  }

  private play(): void {
    if (!this.alive || this.reduced || this.playing) { return; }
    clearTimeout(this.timer);
    this.playing = true;
    this.step(0, ++this.generation);
  }

  private stillTexture(): Resource {
    if (this.kind === 'reward') { return $r('app.media.motion_reward_boy'); }
    if (this.kind === 'thinking') { return $r('app.media.motion_thinking_child'); }
    if (this.kind === 'happy') { return $r('app.media.motion_happy_child'); }
    return $r('app.media.motion_reading_boy');
  }

  private step(index: number, generation: number): void {
    if (!this.alive || generation !== this.generation) { return; }
    if (this.reduced) {
      this.angle = 0; this.otherAngle = 0; this.lift = 0; this.stretch = 1;
      this.playing = false;
      return;
    }
    let angles: number[] = [4, 7, 3, -2, 0];
    let other: number[] = [0, 0, 0, 0, 0];
    let lifts: number[] = [0, -0.01, 0, 0, 0];
    let scales: number[] = [1, 1, 1, 1, 1];
    let durations: number[] = [480, 520, 480, 420, 440];
    if (this.kind === 'reward') {
      angles = [-4, 4, -2, 1, 0];
      lifts = [-0.02, -0.035, -0.01, 0, 0];
    } else if (this.kind === 'thinking') {
      angles = [-5, -9, -9, 3, 0];
      durations = [420, 480, 460, 420, 420];
    } else if (this.kind === 'happy') {
      angles = [-8, -13, -8, 3, 0];
      other = [8, 13, 8, -3, 0];
      lifts = [0.015, -0.09, -0.14, 0.012, 0];
      scales = [0.97, 1.02, 1.02, 0.96, 1];
      durations = [300, 420, 360, 320, 400];
    }
    this.getUIContext().animateTo({
      duration: durations[index], curve: Curve.EaseInOut,
      onFinish: () => {
        if (!this.alive || generation !== this.generation) { return; }
        if (index < angles.length - 1) { this.step(index + 1, generation); }
        else {
          this.playing = false;
          this.timer = setTimeout(() => { this.play(); }, 10000);
        }
      }
    }, () => {
      this.angle = angles[index]; this.otherAngle = other[index];
      this.lift = lifts[index] * this.canvasSize; this.stretch = scales[index];
    });
  }

  @Builder
  Cell(cell: number) {
    Stack({ alignContent: Alignment.TopStart }) {
      Image(this.texture()).width(this.canvasSize * 2).height(this.canvasSize)
        .objectFit(ImageFit.Fill).syncLoad(true).position({ x: -cell * this.canvasSize, y: 0 });
    }.width(this.kind === 'thinking' && cell === 0 ? this.canvasSize * 0.96 : this.canvasSize)
      .height(this.canvasSize).clip(true);
  }

  @Builder
  MovingPart() {
    Stack({ alignContent: Alignment.TopStart }) { this.Cell(1); }
      .width(this.canvasSize).height(this.canvasSize)
      .scale({ x: this.kind === 'reading' ? 0.82 : (this.kind === 'reward' ? 0.82 : 0.90),
        y: this.kind === 'reading' ? 0.82 : (this.kind === 'reward' ? 0.82 : 0.90),
        centerX: '0%', centerY: '0%' })
      .translate({ x: this.canvasSize * (this.kind === 'reading' ? 0.01 : (this.kind === 'reward' ? 0.25 : -0.03)),
        y: this.canvasSize * (this.kind === 'reading' ? -0.03 : (this.kind === 'reward' ? 0.12 : 0.03)) })
      .rotate({ angle: this.angle, centerX: this.kind === 'reward' ? '50%' : '52%',
        centerY: this.kind === 'reward' ? '70%' : '78%' });
  }

  @Builder
  Arm(right: boolean) {
    Stack({ alignContent: Alignment.TopStart }) {
      Image(this.texture()).width(this.canvasSize * 2).height(this.canvasSize).objectFit(ImageFit.Fill)
        .syncLoad(true).position({ x: -this.canvasSize * (right ? 1.5 : 1.05), y: 0 });
    }.width(this.canvasSize * (right ? 0.5 : 0.45)).height(this.canvasSize).clip(true)
      .rotate({ angle: right ? this.otherAngle : this.angle,
        centerX: right ? '20%' : '64%', centerY: right ? '59%' : '54%' })
      .position({ x: this.canvasSize * (right ? 0.5 : 0.05), y: right ? -this.canvasSize * 0.01 : -this.canvasSize * 0.05 });
  }

  build() {
    Stack({ alignContent: Alignment.TopStart }) {
      if (this.reduced) {
        Image(this.stillTexture()).width(this.canvasSize).height(this.canvasSize).objectFit(ImageFit.Contain).syncLoad(true);
      } else {
        this.Cell(0);
        if (this.kind === 'thinking') {
          Ellipse().width(this.canvasSize * 0.104).height(this.canvasSize * 0.056).fill('#FFD0A8')
            .position({ x: this.canvasSize * 0.758, y: this.canvasSize * 0.812 });
        }
        if (this.kind === 'happy') { this.Arm(false); this.Arm(true); }
        else { this.MovingPart(); }
      }
    }.width(this.canvasSize).height(this.canvasSize)
      .translate({ x: 0, y: this.kind === 'happy' ? this.lift * 0.84 + this.canvasSize * 0.04 : this.lift })
      .scale({ x: this.kind === 'happy' ? 0.84 : 1, y: (this.kind === 'happy' ? 0.84 : 1) * this.stretch })
      .onClick(() => { this.play(); });
  }
}

