// POC 最小插件：验证 dsh 能挂载我们自己写的插件。
// Cordis 插件形态：导出 name + apply(ctx, config)，经 ctx 扩展点接入。
// 教训：ctx 上的注入属性（如 command/tools/logger）必须显式声明到 `inject`，
//       不可在 apply 里随意访问，否则 Cordis 抛 "cannot get property without inject"。
export const name = 'my-hello-plugin'

export function apply() {
  // console.log 必现到 stdout，作为"被成功加载"的硬证据
  console.log('[POC-MOUNT] my-hello-plugin apply() executed => 插件已被 dsh 挂载')
}