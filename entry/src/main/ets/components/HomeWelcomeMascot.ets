// Two resident textures; only the arm transform changes during a greeting.
@Component
export struct HomeWelcomeMascot {
  @Prop reduced: boolean = false;
  @Prop canvasSize: number = 250;
  @State private armAngle: number = 0;
  private timer: number = -1;
  private alive: boolean = false;
  private playing: boolean = false;
  private generation: number = 0;
  private angles: number[] = [-10, 12, -12, 10, -6, 0];
  private durations: number[] = [320, 440, 460, 440, 360, 320];

  aboutToAppear(): void {
    this.alive = true;
    this.timer = setTimeout(() => { this.play(); }, 650);
  }

  aboutToDisappear(): void {
    this.alive = false;
    this.generation++;
    clearTimeout(this.timer);
  }

  private play(): void {
    // A tap during a wave does not interrupt the current continuous motion.
    if (!this.alive || this.reduced || this.playing) { return; }
    clearTimeout(this.timer);
    this.playing = true;
    this.generation++;
    this.leg(0, this.generation);
  }

  private leg(index: number, generation: number): void {
    if (!this.alive || generation !== this.generation) { return; }
    this.getUIContext().animateTo({
      duration: this.durations[index],
      curve: Curve.EaseInOut,
      onFinish: () => {
        if (!this.alive || generation !== this.generation) { return; }
        if (index < this.angles.length - 1) {
          this.leg(index + 1, generation);
        } else {
          this.playing = false;
          this.timer = setTimeout(() => { this.play(); }, 10000);
        }
      }
    }, () => { this.armAngle = this.angles[index]; });
  }

  build() {
    Stack() {
      Image($r('app.media.home_wave_body')).width(this.canvasSize).height(this.canvasSize)
        .objectFit(ImageFit.Contain).syncLoad(true);
      Image($r('app.media.home_wave_arm')).width(this.canvasSize).height(this.canvasSize)
        .objectFit(ImageFit.Contain).syncLoad(true)
        .rotate({ x: 0, y: 0, z: 1, angle: this.armAngle, centerX: '31%', centerY: '64%' });
    }.width(this.canvasSize).height(this.canvasSize).onClick(() => { this.play(); });
  }
}
