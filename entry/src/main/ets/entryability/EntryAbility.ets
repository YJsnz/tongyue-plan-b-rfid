import { AbilityConstant, UIAbility, Want } from '@kit.AbilityKit';
import { window } from '@kit.ArkUI';

export default class EntryAbility extends UIAbility {
  onCreate(_want: Want, _launchParam: AbilityConstant.LaunchParam): void {}

  onWindowStageCreate(windowStage: window.WindowStage): void {
    windowStage.getMainWindowSync().setWindowSystemBarProperties({
      statusBarColor: '#FFFDF7', statusBarContentColor: '#173E39',
      navigationBarColor: '#FFFDF7', navigationBarContentColor: '#173E39'
    }).catch(() => {});
    windowStage.loadContent('pages/Index');
  }
}
